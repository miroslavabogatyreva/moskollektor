#!/usr/bin/env python3
"""Воспроизводимость backend/requirements.txt без пересборки образа: MOS-140.

Обычная проверка лицензий (check_licenses.py) сравнивает ДВА снимка
установленного — .venv и образ — с docs/libraries.md. Оба снимка честные, но
оба про СЕГОДНЯ: если пакет в requirements.txt не закреплён точной версией,
снимки могут случайно совпасть, а завтрашняя пересборка — нет (находка
проверяющей 58 и 28, 21.09.2026: idna разошлась между .venv и образом именно
так, молча, и жила незамеченной весь день).

Эта проверка отвечает на другой вопрос: если пересобрать образ ПРЯМО СЕЙЧАС
из сегодняшнего requirements.txt, получим ли мы то же самое, что уже стоит
в образе? Ответ ищем не пересборкой (минуты), а прогоном pip'а ВНУТРИ уже
собранного образа: `pip install --dry-run --ignore-installed --report ...`
использует тот же интерпретатор, тот же pip и тот же индекс PyPI, но не
трогает файловую систему контейнера — только план установки.

Флаг --ignore-installed обязателен. Без него пакеты уже стоят в образе, pip
отвечает «ставить нечего», план пуст, сравнивать не с чем — проверка тогда
СВЕТИТСЯ ЗЕЛЁНЫМ ВСЕГДА, что бы ни случилось с requirements.txt (нашла 28,
проверяя проверку: без флага ноль расхождений печатался и на подменённой
версии). Пустой план и здесь ловится отдельно, а не через диф: если pip
ничего не поставил бы, сравнивать план не с чем, и это СБОЙ проверки, а не
«ноль расхождений».

Запуск:
    DOCKER_HOST=ssh://root@135.106.216.101 \\
      .venv/bin/python code/check_dependency_pins.py --image moskollektor-api:latest
"""

import argparse
import json
import os
import subprocess
import sys

ЗДЕСЬ = os.path.dirname(os.path.abspath(__file__))
КОРЕНЬ = os.path.join(ЗДЕСЬ, "..")
sys.path.insert(0, ЗДЕСЬ)

from check_licenses import НЕ_В_ОБРАЗЕ, собрать_backend_из_образа  # noqa: E402

_КОМАНДА_В_КОНТЕЙНЕРЕ = (
    "cat > /tmp/req.txt && "
    "pip install --dry-run --ignore-installed --no-cache-dir "
    "--report /tmp/report.json -r /tmp/req.txt >/tmp/pip.log 2>&1 "
    "|| { cat /tmp/pip.log >&2; exit 1; }; "
    "cat /tmp/report.json"
)


def свежий_резолв(образ, requirements_путь):
    """{имя: версия}, которые поставил бы pip внутри `образ`, разрешая
    requirements_путь с нуля — не то, что там стоит сейчас. Файл берём
    с диска (дерево), а не изнутри образа: внутри лежит копия, с которой
    его когда-то собрали, а нас интересует СЕГОДНЯШНИЙ requirements.txt."""
    with open(requirements_путь, encoding="utf-8") as f:
        текст = f.read()
    вывод = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-i",
            "--entrypoint",
            "sh",
            образ,
            "-c",
            _КОМАНДА_В_КОНТЕЙНЕРЕ,
        ],
        input=текст,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if вывод.returncode != 0:
        raise RuntimeError(
            f"pip install --dry-run внутри {образ} упал:\n{вывод.stderr.strip()}"
        )
    отчёт = json.loads(вывод.stdout)
    результат = {}
    for запись in отчёт["install"]:
        имя = запись["metadata"]["name"]
        if имя.lower() in НЕ_В_ОБРАЗЕ:
            continue
        результат[имя] = запись["metadata"]["version"]
    return результат


def main():
    р = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    р.add_argument(
        "--image",
        required=True,
        help="docker run --entrypoint ... <образ>, тот же, что в check_licenses.py --image",
    )
    р.add_argument(
        "--requirements",
        default=os.path.join(КОРЕНЬ, "backend", "requirements.txt"),
    )
    а = р.parse_args()

    сейчас = {
        имя: версия
        for имя, версия, _лицензия, _набор in собрать_backend_из_образа(а.image)
    }
    свежее = свежий_резолв(а.image, а.requirements)

    if not свежее:
        print(
            "СБОЙ: pip внутри образа не поставил бы ничего — без --ignore-installed "
            "план всегда пуст, а проверка тогда красит любую подмену как «ноль расхождений»"
        )
        return 1

    имена = sorted(set(сейчас) | set(свежее))
    разошлись = [
        (имя, сейчас.get(имя), свежее.get(имя))
        for имя in имена
        if сейчас.get(имя) != свежее.get(имя)
    ]

    print(f"в образе сейчас: {len(сейчас)}, новая сборка поставила бы: {len(свежее)}")
    if разошлись:
        print(f"СБОЙ: пересборка сегодня дала бы другую версию — {len(разошлись)}")
        for имя, было, стало in разошлись:
            print(f"  - {имя}: в образе {было}, новая сборка взяла бы {стало}")
        return 1
    print("OK    воспроизводимость: пересборка сегодня дала бы те же версии")
    return 0


if __name__ == "__main__":
    sys.exit(main())
