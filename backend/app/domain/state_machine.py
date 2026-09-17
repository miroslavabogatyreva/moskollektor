"""Автомат состояний заявки и заказа ТОиР. Задача Q6.4 (MOS-59), приёмка М-09.

Источник истины — колонка `status` с ограничением CHECK в db/migrations/001_assets.sql,
а не код. Самопроверка ниже читает CHECK из миграции и сверяет с константой TRANSITIONS
здесь: поменяют миграцию — самопроверка покраснеет, а не разойдётся тихо.

Первая версия (MOS-59, 17.09.2026) переносила SAP-автомат из code/toir_state_machine.py
буквально: два статуса на объект (системный и пользовательский), значения на кириллице.
Проверка на приёмке показала, что в базе статус один и значения совсем другие — SAP-схема
в неё не попала, а code/toir_state_machine.py остаётся историей прототипа и дальше не
меняется. Смысл переходов (создан -> в работе -> выполнен -> закрыт, отмена до выполнения,
из терминальных статусов выхода нет) взят из того же SAP-автомата, но значения — из CHECK.

Соответствие SAP-кода нашему статусу, чтобы происхождение было видно:

  Заявка (maint.notification.status, SAP-прототип — Notification.sys_status):
    СООТ -> OPEN         создана
    СОРА -> IN_PROCESS   отдана в обработку
    СОЗА -> COMPLETED    закрыта (функция «закрыть»)
    МТКУ -> CANCELLED    помечена на удаление
    СОТЛ -> —            отдельного статуса «отложена» в базе нет, состояние не сохранилось

  Заказ (maint.work_order.status, SAP-прототип — Order.sys_status):
    ОТКР -> CREATED
    ДЕБЛ -> RELEASED
    ПДТВ -> CONFIRMED
    ТЗКР -> TECH_COMPLETE
    ЗАКР -> CLOSED
    —    -> CANCELLED    нового значения в SAP-автомате заказа не было

Самопроверка: python -m app.domain.state_machine — перечисляет все переходы, падает
на запрещённом, проходит на разрешённом и сверяет статусы с CHECK в миграции.
"""

import re
from pathlib import Path

NOTIFICATION_TABLE = "maint.notification"
WORK_ORDER_TABLE = "maint.work_order"

TRANSITIONS = {
    "notification": {
        "OPEN": {"IN_PROCESS", "CANCELLED"},
        "IN_PROCESS": {"COMPLETED", "CANCELLED"},
        "COMPLETED": set(),
        "CANCELLED": set(),
    },
    "work_order": {
        "CREATED": {"RELEASED", "CANCELLED"},
        "RELEASED": {"CONFIRMED", "TECH_COMPLETE", "CANCELLED"},
        "CONFIRMED": {"TECH_COMPLETE"},
        "TECH_COMPLETE": {"CLOSED"},
        "CLOSED": set(),
        "CANCELLED": set(),
    },
}


def transition(kind: str, old: str, new: str) -> None:
    """Проверить переход old -> new для kind ('notification' или 'work_order')."""
    if new not in TRANSITIONS[kind].get(old, set()):
        raise ValueError(f"{kind}: переход {old} -> {new} запрещён")


# --------------------------------------------------------------------------
# Самопроверка
# --------------------------------------------------------------------------


def _expect_error(fn, fragment: str) -> None:
    try:
        fn()
    except ValueError as exc:
        assert fragment in str(exc), f"ждали «{fragment}», получили «{exc}»"
    else:
        raise AssertionError(f"ждали ошибку со словами «{fragment}», её не было")


def _db_statuses(table: str) -> set[str]:
    """Допустимые статусы таблицы прямо из CHECK в 001_assets.sql."""
    migration = (
        Path(__file__).resolve().parents[3] / "db" / "migrations" / "001_assets.sql"
    )
    sql = migration.read_text()
    block = re.search(rf"CREATE TABLE {re.escape(table)} \(.*?\n\);", sql, re.S)
    if not block:
        raise ValueError(f"таблица {table} не найдена в {migration}")
    check = re.search(r"CHECK \(status IN \(([^)]*)\)\)", block.group(0))
    if not check:
        raise ValueError(f"в {table} нет CHECK на status")
    return {value.strip().strip("'") for value in check.group(1).split(",")}


def _all_transitions() -> list[tuple[str, str, str]]:
    return [
        (kind, src, dst)
        for kind, transitions in TRANSITIONS.items()
        for src, dsts in transitions.items()
        for dst in sorted(dsts)
    ]


def _selfcheck() -> None:
    rows = _all_transitions()
    for kind, src, dst in rows:
        print(f"  {kind}: {src} -> {dst}")
    print(f"переходов: {len(rows)}")

    # --- разрешённые переходы проходят --------------------------------
    transition("notification", "OPEN", "IN_PROCESS")
    transition("notification", "IN_PROCESS", "COMPLETED")
    transition("notification", "OPEN", "CANCELLED")
    transition("work_order", "CREATED", "RELEASED")
    transition("work_order", "RELEASED", "CONFIRMED")
    transition("work_order", "RELEASED", "TECH_COMPLETE")
    transition("work_order", "CONFIRMED", "TECH_COMPLETE")
    transition("work_order", "TECH_COMPLETE", "CLOSED")
    transition("work_order", "CREATED", "CANCELLED")

    # --- запрещённые падают --------------------------------------------
    _expect_error(lambda: transition("notification", "COMPLETED", "OPEN"), "запрещён")
    _expect_error(
        lambda: transition("notification", "CANCELLED", "IN_PROCESS"), "запрещён"
    )
    _expect_error(
        lambda: transition("work_order", "CONFIRMED", "CANCELLED"), "запрещён"
    )
    _expect_error(lambda: transition("work_order", "CLOSED", "CREATED"), "запрещён")

    # --- статусы автомата обязаны совпасть с CHECK в миграции ----------
    db_notification = _db_statuses(NOTIFICATION_TABLE)
    db_work_order = _db_statuses(WORK_ORDER_TABLE)
    for kind, db_statuses in (
        ("notification", db_notification),
        ("work_order", db_work_order),
    ):
        used = set(TRANSITIONS[kind]) | {
            dst for dsts in TRANSITIONS[kind].values() for dst in dsts
        }
        assert used == db_statuses, (kind, sorted(used ^ db_statuses))
    assert db_notification == set(TRANSITIONS["notification"]), (
        db_notification,
        set(TRANSITIONS["notification"]),
    )
    assert db_work_order == set(TRANSITIONS["work_order"]), (
        db_work_order,
        set(TRANSITIONS["work_order"]),
    )
    print(
        f"статусов в базе {len(db_notification)} и {len(db_work_order)}, "
        f"в автомате {len(TRANSITIONS['notification'])} и {len(TRANSITIONS['work_order'])}"
    )
    print("selfcheck ok")


if __name__ == "__main__":
    _selfcheck()
