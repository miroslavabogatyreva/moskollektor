#!/usr/bin/env python3
"""Права ролей в git против ref.role_permission в базе: строка НФ-43, НФ-44.

Задача MOS-119 (Q1.12), нашла 58 на проверке Q4.12 18.09.2026. Сиды из
db/seed/ накатывались руками, и на стенде это выстрелило: в db/seed/rbac.sql
появились settings.read и settings.write, база их не получила, и admin1
отвечал 403 на GET /api/settings, пока файл не накатали руками. Расхождение
«права в git против прав в базе» ловилось только чьим-то 403 на живом прогоне.

Что проверяем: набор пар (роль, право) в git обязан в точности совпадать с
ref.role_permission в базе — в обе стороны. Недостающая пара означает, что
права в базе отстали от git и метод закрыт тому, кому открыт; лишняя — что
сид урезали и не накатали. Обе стороны красные: и 403 там, где его быть не
должно, и открытый метод там, где он закрыт, — одинаково дыра в разграничении
доступа.

**Ожидаемый набор берётся из двух слоёв git, а не из одного сида.**
ref.role_permission пишут и db/seed/rbac.sql, и миграции — правило «ПРАВА —
МИГРАЦИЕЙ, А НЕ СИДОМ» из db/migrations/045_notification_ack.sql. Сверка с
одним сидом дала бы ложное красное на исправной базе: сид держит 19 пар,
миграции добавляют к ним ещё 12, и в базе их 31. Поэтому ожидаемый набор —
это пары сида плюс пары из INSERT миграций минус пары, которые миграции
удаляют (044_roles_scope.sql убирает старые роли analyst и engineer).

Каждая строка вывода называет знаменатель: «совпало 31 пара из 31 в базе»
вместо «совпало». Ноль расхождений на пустой выборке — не доказательство.

Код возврата: 1, если хоть одна строка красная.

Запуск:  DATABASE_URL=postgresql://... python3 code/check_seed_rbac.py
Самопроверка без базы:  python3 code/check_seed_rbac.py --demo
"""

import asyncio
import os
import re
import sys
from pathlib import Path

try:
    import asyncpg
except ImportError:  # локально пакет живёт в .venv, в образе — в системном питоне
    asyncpg = None

КОРЕНЬ = Path(__file__).resolve().parent.parent
СИД = КОРЕНЬ / "db" / "seed" / "rbac.sql"
МИГРАЦИИ = КОРЕНЬ / "db" / "migrations"

# Вставки и удаления ловим только у ref.role_permission: в миграциях есть и
# другие таблицы, а нам нужна одна. Перенос строки внутри конструкции допустим
# (в 044 и 045 он есть), поэтому у шаблонов re.S и \s*, а не пробелы.
ВСТАВКА = re.compile(
    r"INSERT\s+INTO\s+ref\.role_permission\s*\(([^)]*)\)\s*VALUES\s*(.*?)(?:ON\s+CONFLICT|;)",
    re.DOTALL | re.IGNORECASE,
)
УДАЛЕНИЕ = re.compile(
    r"DELETE\s+FROM\s+ref\.role_permission\s+WHERE\s+role_code\s+IN\s*\(([^)]*)\)",
    re.DOTALL | re.IGNORECASE,
)
ПАРА = re.compile(r"\(\s*'([^']*)'\s*,\s*'([^']*)'\s*\)")
РОЛЬ = re.compile(r"'([^']*)'")
# Имена колонок в списке INSERT: их порядок решает, какая часть пары — роль.
ЗАГОЛОВОК = re.compile(r"\b(role_code|permission_code)\b", re.IGNORECASE)


def пар(число):
    """«1 пара», «2 пары», «5 пар» — число в отчёте обязано читаться по-русски."""
    if число % 10 == 1 and число % 100 != 11:
        return f"{число} пара"
    if число % 10 in (2, 3, 4) and число % 100 not in (12, 13, 14):
        return f"{число} пары"
    return f"{число} пар"


def разобрать(текст, имя):
    """(добавлено, удалено, жалобы) по одному SQL-файлу.

    Разбор намеренно узкий и падает жалобой на всякой конструкции, которую он
    не понял: молча пропущенная вставка сделала бы проверку зелёной на базе,
    которую она обязана краснеть.
    """
    добавлено, удалено, жалобы = set(), set(), []

    for шапка, тело in ВСТАВКА.findall(текст):
        порядок = ЗАГОЛОВОК.findall(шапка)
        # Порядок колонок любой: половинки пары переставляем по шапке ниже.
        # А вот состав — строго две колонки, другого разбор не понимает.
        if sorted(к.lower() for к in порядок) != ["permission_code", "role_code"]:
            жалобы.append(
                f"{имя}: вставка в ref.role_permission с колонками "
                f"[{', '.join(порядок) or 'пусто'}] — разбор ждёт role_code, permission_code"
            )
            continue
        if порядок[0].lower() == "role_code":
            пары = set(ПАРА.findall(тело))
        else:
            # Порядок колонок обратный: половинки пары меняем местами.
            пары = {(право, роль) for роль, право in ПАРА.findall(тело)}
        if not пары:
            жалобы.append(f"{имя}: вставка в ref.role_permission без ни одной пары")
        добавлено |= пары

    for список in УДАЛЕНИЕ.findall(текст):
        удалено |= set(РОЛЬ.findall(список))
    # Удаление в миграциях описано только списком ролей в WHERE role_code IN (…).
    # Любую другую форму разбор не понимает и обязан сказать об этом красным.
    for кусок in re.findall(
        r"DELETE\s+FROM\s+ref\.role_permission[^;]*", текст, re.IGNORECASE | re.DOTALL
    ):
        if not УДАЛЕНИЕ.search(кусок):
            жалобы.append(
                f"{имя}: удаление из ref.role_permission в форме, которой разбор не знает: "
                f"{кусок.strip()[:60]}"
            )

    return добавлено, удалено, жалобы


def собрать_из_git():
    """Ожидаемый набор пар: сид плюс миграции минус удалённое миграциями."""
    добавлено, жалобы = set(), []
    роли_к_удалению = set()

    if not СИД.exists():
        жалобы.append(f"файла нет: {СИД}")
    else:
        п, у, ж = разобрать(СИД.read_text(encoding="utf-8"), "db/seed/rbac.sql")
        добавлено |= п
        роли_к_удалению |= у
        жалобы += ж

    файлы = sorted(МИГРАЦИИ.glob("*.sql")) if МИГРАЦИИ.is_dir() else []
    if not файлы:
        жалобы.append(f"в {МИГРАЦИИ} нет ни одного .sql")
    for путь in файлы:
        п, у, ж = разобрать(
            путь.read_text(encoding="utf-8"), f"db/migrations/{путь.name}"
        )
        добавлено |= п
        роли_к_удалению |= у
        жалобы += ж

    # Удалённое миграциями вычитаем в конце: 044 убирает роли до того, как их
    # могла бы добавить более поздняя вставка, а порядок файлов у нас — по имени.
    уж_нет = {(роль, право) for роль, право in добавлено if роль in роли_к_удалению}
    return добавлено - уж_нет, жалобы


def сверить(из_git, жалобы, из_базы):
    """Печатает строки отчёта, возвращает True, если всё зелёное."""
    зелено = True

    if жалобы:
        print(f"RB-01 СБОЙ форма файлов: {len(жалобы)} нарушений, первые три:")
        for жалоба in жалобы[:3]:
            print(f"       {жалоба}")
        зелено = False
    else:
        print(
            f"RB-01 OK   форма: {пар(len(из_git))} в git "
            f"(сид плюс миграции), нарушений разбора нет"
        )

    лишние = из_базы - из_git
    недостающие = из_git - из_базы
    if лишние or недостающие:
        print(
            f"RB-02 СБОЙ права в базе разошлись с git: в базе {пар(len(из_базы))}, "
            f"в git {пар(len(из_git))}; лишних в базе {len(лишние)}, "
            f"недостающих в базе {len(недостающие)}"
        )
        for роль, право in sorted(лишние)[:3]:
            print(f"       лишняя в базе: {роль} → {право}")
        for роль, право in sorted(недостающие)[:3]:
            print(
                f"       недостающая в базе: {роль} → {право} (на этом праве метод отвечает 403)"
            )
        зелено = False
    else:
        print(
            f"RB-02 OK   пар в базе {пар(len(из_базы))}, в git {пар(len(из_git))}, "
            f"совпадают все — и роль, и право"
        )

    return зелено


async def из_базы():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        ряды = await conn.fetch(
            "SELECT role_code, permission_code FROM ref.role_permission"
        )
    finally:
        await conn.close()
    return {(р["role_code"], р["permission_code"]) for р in ряды}


def демо():
    """Самопроверка: сверка обязана краснеть на трёх видах порчи."""
    сид = """
INSERT INTO ref.role_permission (role_code, permission_code) VALUES
    ('dispatcher', 'risks.read'),
    ('admin',      'settings.write')
ON CONFLICT (role_code, permission_code) DO NOTHING;
"""
    миграция = """
INSERT INTO ref.role_permission (role_code, permission_code) VALUES
    ('admin', 'notifications.read')
ON CONFLICT (role_code, permission_code) DO NOTHING;
"""
    пары_сида, удалено_в_сиде, ж = разобрать(сид, "сид")
    assert пары_сида == {("dispatcher", "risks.read"), ("admin", "settings.write")}, (
        пары_сида
    )
    assert not удалено_в_сиде and not ж, ж

    # Второй слой git: пары, которых в сиде нет, добавляют миграции. Разбор
    # обязан их увидеть — иначе сверка краснеет на исправной базе, где 31 пара
    # против 19 в сиде (045_notification_ack.sql).
    пары_миграций, _, ж = разобрать(миграция, "миграция")
    assert пары_миграций == {("admin", "notifications.read")}, пары_миграций
    assert not ж, ж

    # Ожидаемый набор — оба слоя вместе, и дальше все случаи меряем им.
    git = пары_сида | пары_миграций

    # Обратный порядок колонок разбор обязан переставить, а не потерять.
    перепутанные, _, _ = разобрать(
        "INSERT INTO ref.role_permission (permission_code, role_code) VALUES ('risks.read', 'tech');",
        "обратный",
    )
    assert перепутанные == {("tech", "risks.read")}, перепутанные

    # Непонятная вставка — жалоба, а не молчаливый пропуск.
    _, _, ж = разобрать(
        "INSERT INTO ref.role_permission (role_code) VALUES ('admin');", "узкая"
    )
    assert ж, "должен пожаловаться на колонки, которые не умеет"

    # Удаление миграцией вычитает пары старой роли.
    добавлено, удалено, ж = разобрать(
        "DELETE FROM ref.role_permission WHERE role_code IN ('analyst', 'engineer');",
        "удаление",
    )
    assert удалено == {"analyst", "engineer"} and not добавлено and not ж, (
        добавлено,
        удалено,
        ж,
    )
    _, _, ж = разобрать(
        "DELETE FROM ref.role_permission WHERE 1 = 1;", "чужое удаление"
    )
    assert ж, "должен пожаловаться на форму удаления, которой не знает"

    # Добрый случай.
    assert сверить(git, [], set(git)) is True

    # Недостающая пара в базе — то самое «права отстали от git» и 403 на методе.
    assert сверить(git, [], set(git) - {("admin", "settings.write")}) is False

    # Лишняя пара в базе.
    assert сверить(git, [], set(git) | {("admin", "audit.read")}) is False

    # Приёмочный случай MOS-119: пару убрали из сида и не накатали — в базе она
    # осталась, и это обязано краснеть, а не считаться нормой.
    оскудевший = set(git) - {("dispatcher", "risks.read")}
    assert сверить(оскудевший, [], set(git)) is False

    print("демо: сверка краснеет на трёх видах порчи, зелёная на добром наборе")


def main():
    if "--demo" in sys.argv:
        демо()
        return 0

    из_git, жалобы = собрать_из_git()

    if not os.environ.get("DATABASE_URL") or asyncpg is None:
        print(
            f"ПРОПУСК сверка с базой: задайте DATABASE_URL "
            f"(в git {пар(len(из_git))}, нарушений разбора {len(жалобы)})"
        )
        return 1 if жалобы else 0

    # База названа, но не отвечает — это СБОЙ, а не пропуск: DATABASE_URL задали
    # нарочно, значит сверку ждали. Строкой, а не трассировкой стека: скрипт
    # едет в образ и на приёмке его читает человек, а не разработчик.
    try:
        из_базы_пары = asyncio.run(из_базы())
    except OSError as ошибка:
        print(f"RB-02 СБОЙ база не ответила: {ошибка}")
        return 1

    return 0 if сверить(из_git, жалобы, из_базы_пары) else 1


if __name__ == "__main__":
    sys.exit(main())
