#!/usr/bin/env python3
"""Проверка перечня библиотек и лицензий: приёмка НФ-82 (docs/plan.md, Q1.5, MOS-21).

Строка приёмки — два требования, не одно (docs/acceptance-test.md:495): (1) в
`docs/libraries.md` есть КАЖДАЯ зависимость сборки, у каждой названы версия
и лицензия; (2) под GPL, LGPL и AGPL в `api`, `worker` и `nginx` нет ничего.
Проверка закрывает оба — сверяет живое окружение с документом (пара чисел
«в сборке N, в перечне M» ловит расхождение, как и везде в проекте, где считают
дважды) и красит запрещённые лицензии в самом окружении.

Перечень не пишем руками, а читаем из окружения: для фронта — `package.json`
каждого пакета в `frontend/node_modules` (дерево, которое даёт `npm ls --all`),
для `nginx` — образ `nginx:1.31-alpine` из `docs/HLD.md` разд. 7.1, лицензия
сверена на nginx.org/LICENSE. Пакеты самого Alpine (busybox, musl и т.д.)
не разбираем — MOS-21 просит перечень библиотек, а не аудит базового образа.

Для `api`/`worker` — ДВА разных источника, и это не одно и то же. Без `--image`
скрипт читает метаданные `.venv` разработчика (`importlib.metadata`) — быстро,
годится на каждый прогон, но `.venv` не обязан совпадать с тем, что реально
легло в образ (17.09.2026, находка проверяющей 58: у ruff, который в `.venv`
стоит для форматирования, в образе быть не должно, а pip внутри образа мог
обновиться отдельно от локального; опаснее обратный случай — транзитивная
зависимость сборки, которой нет в `.venv`, проверкой без `--image` попросту
не видна). С `--image <образ>` скрипт заходит в контейнер (`docker run --rm
--entrypoint python <образ> -c ...`) и читает метаданные ТАМ — это и есть
«сборка» из текста строки НФ-82, а не приближение к ней. Печатает явно,
какой источник использован.

Ловушка из HLD 7.3: у `scikit-survival` поле лицензии на PyPI —
`GPL-3.0-or-later`, а список классификаторов пуст. Проверяем в первую очередь
ПОЛЕ лицензии, а не классификаторы.

Образ `ml` — не наш: перечень присылает Николай, здесь не проверяется.

Запуск:
    .venv/bin/python code/check_licenses.py                          # .venv, все наборы
    .venv/bin/python code/check_licenses.py --only backend           # api/worker/nginx из .venv
    .venv/bin/python code/check_licenses.py --only backend --image moskollektor-api  # из настоящего образа
    .venv/bin/python code/check_licenses.py --only frontend          # только фронт
    .venv/bin/python code/check_licenses.py --selfcheck               # только самопроверка
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from importlib import metadata as ilm

# HLD 7.1.2: инструмент разработки, в образ api/worker не попадает (как prettier
# для фронта). pip/setuptools/wheel — сам пакетный менеджер, не наша зависимость.
НЕ_В_ОБРАЗЕ = {"ruff", "pip", "setuptools", "wheel"}

ЗДЕСЬ = os.path.dirname(os.path.abspath(__file__))
КОРЕНЬ = os.path.join(ЗДЕСЬ, "..")


def нарушает(лицензия):
    """Причина отказа для одной лицензии, либо None, если она чистая."""
    if not лицензия or лицензия.strip().upper() == "UNKNOWN":
        return "лицензия не определена (UNKNOWN)"
    if "gpl" in лицензия.lower():  # ловит GPL, LGPL и AGPL одной подстрокой
        return (
            f"запрещённая лицензия ({лицензия}) — HLD 7.3: GPL/LGPL/AGPL вне поставки"
        )
    return None


def _лицензия_пакета(dist):
    # Метаданные пакетов сейчас на переходе (Metadata-Version 2.4, PEP 639):
    # часть пакетов уже пишет SPDX-поле License-Expression (asyncpg, fastapi,
    # pydantic — проверено 17.09.2026), часть ещё держит старое поле License
    # (h11, APScheduler). Смотрим оба, иначе половина venv уходит в UNKNOWN
    # не по лицензии, а по формату метаданных (реальная находка первого прогона).
    для_поля_expr = (dist.metadata.get("License-Expression") or "").strip()
    if для_поля_expr:
        return для_поля_expr
    поле = (dist.metadata.get("License") or "").strip()
    if поле and поле.upper() != "UNKNOWN":
        return поле
    # Оба поля пустые — смотрим классификаторы (ловушка HLD 7.3: у scikit-survival
    # наоборот, поле заполнено, а классификаторы пусты — до этой ветки очередь
    # не доходит, и подставная запись в самопроверке краснеет уже на поле).
    for classifier in dist.metadata.get_all("Classifier") or []:
        if classifier.startswith("License :: OSI Approved ::"):
            return classifier.rsplit("::", 1)[-1].strip()
    return "UNKNOWN"


class _ФальшМетаданные:
    """Стенд под email.message.Message — get()/get_all(). Не только для тестов:
    собрать_backend_из_образа() строит такой же стенд из JSON, который отдаёт
    контейнер, и прогоняет его через _лицензия_пакета() — то же самое правило
    чтения метаданных для .venv и для образа, а не два расходящихся куска кода."""

    def __init__(self, поля, классификаторы=()):
        self._поля = поля
        self._классификаторы = list(классификаторы)

    def get(self, ключ, по_умолчанию=None):
        return self._поля.get(ключ, по_умолчанию)

    def get_all(self, ключ):
        return self._классификаторы if ключ == "Classifier" else None


class _ФальшДистрибутив:
    def __init__(self, поля, классификаторы=()):
        self.metadata = _ФальшМетаданные(поля, классификаторы)


def собрать_backend(path=None):
    """path — список каталогов site-packages; по умолчанию текущий интерпретатор.

    Параметр существует ради проверки на живом пакете (см. docs/libraries.md
    и разбор в чате с проверяющей 58): подсовываем сюда site-packages отдельного
    venv с реально установленным GPL-пакетом, без риска для .venv проекта.
    """
    строки, без_имени = [], 0
    for dist in ilm.distributions(path=path) if path else ilm.distributions():
        имя = dist.metadata["Name"]
        if not имя:
            # Реальный случай, 18.09.2026: на системном Python (Homebrew, не наш
            # .venv) dist-info пакетов pycparser и cffi содержит только каталог
            # licenses/ без файла METADATA вовсе — dist.metadata["Name"] отдаёт
            # None, а .lower() на None падал AttributeError на первом же пакете.
            # Показать нечего — пропускаем и говорим об этом, а не молчим
            # о неполном перечне.
            без_имени += 1
            continue
        if имя.lower() in НЕ_В_ОБРАЗЕ:
            continue
        строки.append((имя, dist.version, _лицензия_пакета(dist), "api, worker"))
    if без_имени:
        print(
            f"ПРЕДУПРЕЖДЕНИЕ: {без_имени} дистрибутив(ов) без поля Name в метаданных "
            "пропущено — сломанное окружение (не .venv/backend/requirements.txt?)",
            file=sys.stderr,
        )
    return sorted(строки)


# Снимок метаданных внутри контейнера — только чтение сырых полей, никакой
# логики: правило «как читать лицензию» одно, в _лицензия_пакета(), а не
# разъезжается между .venv-веткой и образом (находка проверяющей 58, 17.09.2026:
# .venv разработчика — это не то же самое, что сборка, строка приёмки говорит
# «сборка», а сборка — это образ).
_СНИМОК_ОБРАЗА = (
    "import json, importlib.metadata as m; "
    "print(json.dumps([{'name': d.metadata['Name'], 'version': d.version, "
    "'license_expr': d.metadata.get('License-Expression'), "
    "'license': d.metadata.get('License'), "
    "'classifiers': d.metadata.get_all('Classifier') or []} "
    "for d in m.distributions()]))"
)


def собрать_backend_из_образа(образ):
    """То же самое, что собрать_backend(), но метаданные читает интерпретатор
    ВНУТРИ готового образа, а не .venv разработчика. `docker run --entrypoint
    python <образ> -c ...` — тот же способ, каким проверяющая 58 сверяла руками
    (18 в .venv против 17 в образе, разница — ruff и версия pip)."""
    вывод = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "python",
            образ,
            "-c",
            _СНИМОК_ОБРАЗА,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if вывод.returncode != 0:
        raise RuntimeError(
            f"docker run для образа {образ} упал: {вывод.stderr.strip()}"
        )
    строки, без_имени = [], 0
    for запись in json.loads(вывод.stdout):
        имя = запись["name"]
        if not имя:  # та же защита, что в собрать_backend() — см. её комментарий
            без_имени += 1
            continue
        if имя.lower() in НЕ_В_ОБРАЗЕ:
            continue
        поля = {}
        if запись["license_expr"]:
            поля["License-Expression"] = запись["license_expr"]
        if запись["license"]:
            поля["License"] = запись["license"]
        dist = _ФальшДистрибутив(поля, запись["classifiers"])
        строки.append((имя, запись["version"], _лицензия_пакета(dist), "api, worker"))
    if без_имени:
        print(
            f"ПРЕДУПРЕЖДЕНИЕ: {без_имени} дистрибутив(ов) без поля Name в образе "
            f"{образ} пропущено — сломанные метаданные",
            file=sys.stderr,
        )
    return sorted(строки)


def _пакеты_node_modules(node_modules):
    имена = []
    for запись in sorted(os.listdir(node_modules)):
        путь = os.path.join(node_modules, запись)
        if запись.startswith("."):
            continue
        if запись.startswith("@") and os.path.isdir(путь):
            имена += [f"{запись}/{под}" for под in sorted(os.listdir(путь))]
        elif os.path.isdir(путь):
            имена.append(запись)
    return имена


def _лицензия_из_package_json(pj):
    лицензия = pj.get("license", pj.get("licenses", "UNKNOWN"))
    if isinstance(лицензия, dict):
        return лицензия.get("type", "UNKNOWN")
    if isinstance(лицензия, list):
        return "/".join(л.get("type", "?") for л in лицензия) or "UNKNOWN"
    return str(лицензия) if лицензия else "UNKNOWN"


def собрать_frontend(node_modules):
    if not os.path.isdir(node_modules):
        return []
    строки = []
    for имя in _пакеты_node_modules(node_modules):
        путь = os.path.join(node_modules, имя, "package.json")
        if not os.path.exists(путь):
            continue
        with open(путь, encoding="utf-8") as f:
            pj = json.load(f)
        строки.append(
            (имя, pj.get("version", "?"), _лицензия_из_package_json(pj), "фронт")
        )
    return sorted(строки)


def собрать_nginx():
    return [("nginx", "1.31", "BSD-2-Clause", "nginx")]


def нарушения(строки):
    return [
        (имя, версия, лицензия, набор, нарушает(лицензия))
        for имя, версия, лицензия, набор in строки
        if нарушает(лицензия)
    ]


# ------------------------------------------------------ сверка с docs/libraries.md


РАЗДЕЛЫ_ПЕРЕЧНЯ = {
    "## `api`, `worker`": "api, worker",
    "## `nginx`": "nginx",
    "## Фронт": "фронт",
}


def прочитать_перечень(путь):
    """Читает готовые markdown-таблицы docs/libraries.md, а не парсит их заново
    отдельным форматом — второй источник правды разошёлся бы с первым молча."""
    if not os.path.exists(путь):
        return []
    with open(путь, encoding="utf-8") as f:
        текст = f.read()
    строки = []
    for заголовок, набор in РАЗДЕЛЫ_ПЕРЕЧНЯ.items():
        начало = текст.find(заголовок)
        if начало == -1:
            continue
        кусок = текст[начало:]
        шапка = кусок.find("| Пакет | Версия | Лицензия |")
        if шапка == -1:
            continue
        for строка in кусок[шапка:].splitlines()[2:]:  # пропуск шапки и разделителя
            строка = строка.strip()
            if not строка.startswith("|"):
                break
            ячейки = [c.strip() for c in строка.strip("|").split("|")]
            if len(ячейки) != 3:
                break
            имя, версия, лицензия = ячейки
            строки.append((имя.strip("`"), версия, лицензия, набор))
    return sorted(строки)


def сверить_с_перечнем(сборка, перечень):
    """(нет_в_перечне, лишние_в_перечне, разошлись) — три списка несовпадений.

    Пара чисел «в сборке N, в перечне M» сама по себе не ловит расхождение,
    если одно добавили и одно убрали в ту же правку — счёт сойдётся, а состав
    нет. Сравниваем по составу (имя, набор), числа — только в отчёте.
    """
    инд_сборка = {
        (имя, набор): (версия, лицензия) for имя, версия, лицензия, набор in сборка
    }
    инд_перечень = {
        (имя, набор): (версия, лицензия) for имя, версия, лицензия, набор in перечень
    }
    нет_в_перечне = sorted(инд_сборка.keys() - инд_перечень.keys())
    лишние_в_перечне = sorted(инд_перечень.keys() - инд_сборка.keys())
    разошлись = sorted(
        к
        for к in (инд_сборка.keys() & инд_перечень.keys())
        if инд_сборка[к] != инд_перечень[к]
    )
    return нет_в_перечне, лишние_в_перечне, разошлись


# ---------------------------------------------------------------- самопроверка


def _selfcheck():
    # PEP 639 / Metadata-Version 2.4: часть venv 17.09.2026 пишет License-Expression
    # (asyncpg, fastapi), часть ещё старое поле License (h11, APScheduler) — обе
    # ветки обязаны читаться, иначе половина пакетов красит UNKNOWN не по лицензии,
    # а по формату метаданных (реальная находка при первом прогоне этого скрипта).
    assert _лицензия_пакета(_ФальшДистрибутив({"License-Expression": "MIT"})) == "MIT"
    assert (
        _лицензия_пакета(_ФальшДистрибутив({"License": "Apache-2.0"})) == "Apache-2.0"
    )
    assert (
        _лицензия_пакета(
            _ФальшДистрибутив({"License-Expression": "MIT", "License": "не то"})
        )
        == "MIT"
    )
    assert (
        _лицензия_пакета(
            _ФальшДистрибутив({}, ["License :: OSI Approved :: BSD License"])
        )
        == "BSD License"
    )
    assert _лицензия_пакета(_ФальшДистрибутив({})) == "UNKNOWN"

    assert нарушает("GPL-3.0-or-later") is not None
    assert нарушает("LGPL-3.0") is not None
    assert нарушает("AGPL-3.0") is not None
    assert нарушает("UNKNOWN") is not None
    assert нарушает("") is not None
    assert нарушает(None) is not None
    assert нарушает("MIT") is None
    assert нарушает("BSD-3-Clause") is None
    assert нарушает("Apache-2.0") is None
    assert (
        нарушает("MPL-2.0") is None
    )  # копилефт, но HLD 7.3 запрещает только GPL/LGPL/AGPL

    подставные = [
        ("scikit-survival", "0.24.1", "GPL-3.0-or-later", "api, worker"),
        ("почта-без-лицензии", "1.0", "UNKNOWN", "фронт"),
        ("fastapi", "0.141.1", "MIT", "api, worker"),
    ]
    плохие = {п[0] for п in нарушения(подставные)}
    assert плохие == {"scikit-survival", "почта-без-лицензии"}, плохие

    # Сверка с перечнем: пакет есть в сборке, но отсутствует в документе —
    # СБОЙ по первой половине НФ-82, даже если лицензия у него чистая.
    сборка = [("новый-пакет", "1.0", "MIT", "api, worker")]
    перечень = [("старый-пакет", "1.0", "MIT", "api, worker")]
    нет, лишние, разошлись = сверить_с_перечнем(сборка, перечень)
    assert нет == [("новый-пакет", "api, worker")], нет
    assert лишние == [("старый-пакет", "api, worker")], лишние
    assert разошлись == [], разошлись

    # Версия обновилась в сборке, а документ не тронули — тоже расхождение.
    сборка2 = [("pkg", "2.0", "MIT", "фронт")]
    перечень2 = [("pkg", "1.0", "MIT", "фронт")]
    _, _, разошлись2 = сверить_с_перечнем(сборка2, перечень2)
    assert разошлись2 == [("pkg", "фронт")], разошлись2

    # Регрессия 18.09.2026 (нашла и воспроизвела 76/57): системный Python держит
    # dist-info pycparser и cffi БЕЗ файла METADATA вовсе (только licenses/) —
    # dist.metadata["Name"] отдаёт None, и .lower() на None ронял всю проверку
    # AttributeError'ом на первом же пакете. Собираем такой каталог по-настоящему
    # (не мокаем ilm.distributions) — тот же путь, что настоящий баг прошёл.
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "broken-1.0.dist-info", "licenses"))
        good = os.path.join(tmp, "good_pkg-1.0.dist-info")
        os.makedirs(good)
        with open(os.path.join(good, "METADATA"), "w", encoding="utf-8") as f:
            f.write(
                "Metadata-Version: 2.1\nName: good-pkg\nVersion: 1.0\nLicense: MIT\n"
            )
        строки_рег = собрать_backend(path=[tmp])
    assert [и for и, *_ in строки_рег] == ["good-pkg"], строки_рег

    print(
        "самопроверка ok: ловушка scikit-survival (поле GPL) красит, UNKNOWN красит, "
        "сверка с перечнем ловит пропажу/лишнее/расхождение версии, дистрибутив "
        "без METADATA не роняет сборку"
    )


# ----------------------------------------------------------------------- отчёт


def _печать_с_лимитом(заголовок, элементы, лимит=3):
    print(f"{заголовок}: {len(элементы)}")
    for имя, набор in элементы[:лимит]:
        print(f"  - {набор}: {имя}")
    if len(элементы) > лимит:
        print(f"  ...ещё {len(элементы) - лимит}")


def main():
    р = argparse.ArgumentParser(description="Проверка лицензий: приёмка НФ-82")
    р.add_argument(
        "--frontend-dir",
        default=os.path.join(КОРЕНЬ, "frontend", "node_modules"),
    )
    р.add_argument(
        "--libraries-doc",
        default=os.path.join(КОРЕНЬ, "docs", "libraries.md"),
    )
    р.add_argument(
        "--only",
        choices=["backend", "frontend", "all"],
        default="all",
        help="backend — api/worker/nginx без node_modules; frontend — только фронт",
    )
    р.add_argument(
        "--image",
        help="читать api/worker не из .venv, а из готового образа (docker run "
        "--entrypoint python <образ>) — так проверяется настоящая сборка, "
        "а не окружение разработчика (см. НФ-82: строка требует «сборку»)",
    )
    р.add_argument("--selfcheck", action="store_true", help="только самопроверка")
    а = р.parse_args()

    _selfcheck()
    if а.selfcheck:
        return 0

    сборка = []
    источник_backend = "образ " + а.image if а.image else ".venv (не сборка!)"
    if а.only in ("backend", "all"):
        backend = собрать_backend_из_образа(а.image) if а.image else собрать_backend()
        сборка += backend + собрать_nginx()
        print(f"api/worker: источник — {источник_backend}")
    if а.only in ("frontend", "all"):
        сборка += собрать_frontend(а.frontend_dir)

    if not сборка:
        print(
            "СБОЙ: сборка пуста — нет .venv или frontend/node_modules, проверять нечего"
        )
        return 1

    наборы_в_области = {набор for *_, набор in сборка}
    перечень = [
        строка
        for строка in прочитать_перечень(а.libraries_doc)
        if строка[3] in наборы_в_области
    ]

    по_наборам = {}
    for _, _, _, набор in сборка:
        по_наборам[набор] = по_наборам.get(набор, 0) + 1
    print(
        f"в сборке: {len(сборка)} ({', '.join(f'{k}: {v}' for k, v in sorted(по_наборам.items()))}), "
        f"в перечне: {len(перечень)}"
    )

    плохие_лицензии = нарушения(сборка)
    нет_в_перечне, лишние_в_перечне, разошлись = сверить_с_перечнем(сборка, перечень)

    ок = True
    if плохие_лицензии:
        ок = False
        for имя, версия, лицензия, набор, причина in плохие_лицензии[:3]:
            print(f"СБОЙ  {набор:<12} {имя} {версия}: {причина}")
        if len(плохие_лицензии) > 3:
            print(
                f"  ...ещё {len(плохие_лицензии) - 3} с запрещённой/неизвестной лицензией"
            )

    if нет_в_перечне:
        ок = False
        _печать_с_лимитом(
            "СБОЙ  НФ-82: в сборке есть, в docs/libraries.md нет", нет_в_перечне
        )
    if лишние_в_перечне:
        ок = False
        _печать_с_лимитом(
            "СБОЙ  НФ-82: в docs/libraries.md есть, в сборке нет (устарел)",
            лишние_в_перечне,
        )
    if разошлись:
        ок = False
        _печать_с_лимитом(
            "СБОЙ  НФ-82: версия или лицензия разошлись с документом", разошлись
        )

    if not ок:
        print(f"ИТОГО СБОЙ: сборка {len(сборка)}, перечень {len(перечень)}")
        return 1

    print(
        f"OK    НФ-82: сборка {len(сборка)} = перечень {len(перечень)}, GPL/LGPL/AGPL/UNKNOWN нет"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
