#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Жизненный цикл заявки (сообщение ТОРО) и наряда-задания (заказ ТОРО).

Схема снята с КСУ ТОиР «Арктик СПГ 2» (SAP S/4HANA): статусные схемы ZPM_WR и
ZPM_WO из Протокола выполненных настроек v.2.4 плюс правила доработок
SAP.PM.003 / SAP.PM.004 / SAP.PM.005 из проектных решений PM.03 и PM.04.

Что здесь воспроизведено:
  * два параллельных набора статусов — системный (его ставит система при
    выполнении функции) и пользовательский (его ставит человек);
  * жёсткая связка: системный статус тянет за собой пользовательский
    (СОРА -> APPR, СОТЛ -> MIRQ, МТКУ -> CNCL, ДЕБЛ -> APPR);
  * флаги, которые живут отдельно от основной цепочки (SDRE, WPOK, EXEC);
  * сроки по приоритету: приоритет 1 -> устранить за 2 дня и так далее;
  * запрет создать наряд-задание, пока заявка не утверждена.

Запуск: python3 toir_state_machine.py — прогоняет самопроверку переходов.
Только стандартная библиотека.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta

# --------------------------------------------------------------------------
# Справочники (дубль ключевых кусков toir_dictionaries.json, чтобы файл был
# самодостаточным)
# --------------------------------------------------------------------------

# Приоритет -> (название, сдвиг начала в днях, срок устранения)
# Источник: ФПР PM.03, Таблица 11 «Приоритеты внеплановых работ»
PRIORITY_WR = {
    1: ("Наивысший", 0, timedelta(days=2)),
    2: ("Высокий", 0, timedelta(days=7)),
    3: ("Средний", 7, timedelta(days=30)),   # «1 месяц»
    4: ("Низкий", 14, timedelta(days=365)),  # «1 год»
}

# Вид сообщения -> какие виды заказа из него можно создать (SAP.PM.005)
NOTIF_TO_ORDER = {
    "WR": {"CORR", "WCOR", "PRDM", "REFB", "IMOW"},
    "MD": set(),
    "AR": {"PREV", "WPRV"},
    "IR": {"PRDM"},
}

# --------------------------------------------------------------------------
# Заявка = сообщение ТОРО вида WR
# --------------------------------------------------------------------------

# Системные статусы сообщения и что за ними следует ставить автоматически
NOTIF_SYS_TRANSITIONS = {
    # из         : {функция: (в системный, автоматический пользовательский)}
    "СООТ": {
        "отдать_в_обработку": ("СОРА", "APPR"),
        "отсрочить": ("СОТЛ", "MIRQ"),
        "пометить_на_удаление": ("МТКУ", "CNCL"),
    },
    "СОТЛ": {
        "отдать_в_обработку": ("СОРА", "APPR"),
        "пометить_на_удаление": ("МТКУ", "CNCL"),
    },
    "СОРА": {
        "закрыть": ("СОЗА", None),
        "отсрочить": ("СОТЛ", "MIRQ"),
        "пометить_на_удаление": ("МТКУ", "CNCL"),
    },
    "СОЗА": {},
    "МТКУ": {},
}

# Пользовательские статусы, которые инициатор/утверждающий ставит руками
NOTIF_USER_ORDER = ["INIT", "AWAP", "MIRQ", "CNCL", "APPR", "TBCO"]
NOTIF_USER_MANUAL = {
    # из   : куда можно руками
    "INIT": {"AWAP"},
    "AWAP": {"APPR", "MIRQ", "CNCL"},
    "MIRQ": {"AWAP"},
    "APPR": {"TBCO", "CNCL"},
    "TBCO": set(),
    "CNCL": set(),
}


@dataclass
class Notification:
    """Заявка на работы. В SAP это сообщение ТОРО вида WR."""

    number: str
    equipment: str
    priority: int
    created: date
    kind: str = "WR"
    sys_status: str = "СООТ"
    user_status: str = "INIT"
    shutdown_required: bool = False  # флаг SDRE, живёт параллельно
    breakdown: bool = False          # признак «Простой»
    fault_end: date | None = None
    cause_code: str | None = None
    orders: list = field(default_factory=list)

    # -- сроки: их считает система от приоритета (доработка SAP.PM.003) ----

    @property
    def required_start(self) -> date:
        return self.created + timedelta(days=PRIORITY_WR[self.priority][1])

    @property
    def required_finish(self) -> date:
        return self.created + PRIORITY_WR[self.priority][2]

    @property
    def fields_locked(self) -> bool:
        """После утверждения приоритет и требуемые даты больше не правятся."""
        return self.user_status == "APPR"

    # -- переходы ----------------------------------------------------------

    def run(self, function: str) -> None:
        """Выполнить системную функцию (кнопку). Система сама тянет статусы."""
        allowed = NOTIF_SYS_TRANSITIONS[self.sys_status]
        if function not in allowed:
            raise ValueError(
                f"{self.number}: функция «{function}» недоступна "
                f"из системного статуса {self.sys_status}"
            )
        if function == "закрыть":
            self._check_closable()
        if function == "пометить_на_удаление" and self.orders:
            raise ValueError(
                f"{self.number}: нельзя пометить на удаление — есть заказы "
                f"{self.orders}; удалять надо заказ и сообщение вместе"
            )
        new_sys, forced_user = allowed[function]
        self.sys_status = new_sys
        if forced_user:
            self.user_status = forced_user

    def set_user_status(self, status: str) -> None:
        """Поставить пользовательский статус руками."""
        if status not in NOTIF_USER_MANUAL[self.user_status]:
            raise ValueError(
                f"{self.number}: переход {self.user_status} -> {status} запрещён"
            )
        self.user_status = status

    def set_priority(self, priority: int) -> None:
        if self.fields_locked:
            raise ValueError(
                f"{self.number}: приоритет закрыт для изменения после APPR"
            )
        self.priority = priority

    def _check_closable(self) -> None:
        """Проверки при закрытии сообщения (ОИ_Ведение сообщений + SAP.PM.003)."""
        if self.fault_end is None or self.cause_code is None:
            raise ValueError(
                f"{self.number}: заполните вкладку «Данные неисправности» — "
                f"нужны конец неисправности и причина"
            )


# --------------------------------------------------------------------------
# Наряд-задание = заказ ТОРО
# --------------------------------------------------------------------------

ORDER_SYS_TRANSITIONS = {
    "ОТКР": {"деблокировать": ("ДЕБЛ", "APPR")},
    "ДЕБЛ": {
        "подтвердить": ("ПДТВ", None),
        "техническое_закрытие": ("ТЗКР", None),  # но требует ПДТВ, см. ниже
    },
    "ПДТВ": {"техническое_закрытие": ("ТЗКР", None)},
    "ТЗКР": {"закрыть": ("ЗАКР", None)},
    "ЗАКР": {},
}

ORDER_USER_MANUAL = {
    "INIT": {"AWRE"},
    "AWRE": {"INIT"},   # отклонение утверждения возвращает в INIT
    "APPR": {"NCMP"},
    "NCMP": set(),
}


@dataclass
class Order:
    """Наряд-задание. В SAP это заказ ТОРО."""

    number: str
    order_type: str
    notification: Notification
    sys_status: str = "ОТКР"
    user_status: str = "INIT"
    work_pack_ready: bool = False   # флаг WPOK
    ready_to_execute: bool = False  # флаг EXEC
    control_keys: list = field(default_factory=list)
    agreed_cost: float | None = None
    cost: float = 0.0
    quality_lot_checked: bool = True  # результаты QM введены
    confirmed_ops: int = 0
    total_ops: int = 1

    def __post_init__(self):
        n = self.notification
        # SAP.PM.005: заказ создаётся только из утверждённого сообщения
        # разрешённого вида
        if self.order_type not in NOTIF_TO_ORDER[n.kind]:
            raise ValueError(
                f"{self.number}: не соответствуют виды сообщений и заказов "
                f"({n.kind} -> {self.order_type})"
            )
        if self.order_type in ("CORR", "WCOR") and n.user_status != "APPR":
            raise ValueError(
                f"{self.number}: заказ {self.order_type} можно создать только "
                f"из сообщения со статусом APPR, а у {n.number} — {n.user_status}"
            )
        n.orders.append(self.number)

    @property
    def latest_allowed_finish(self) -> date:
        """LAFD — крайний срок, считается от срочности сообщения (SAP.PM.004)."""
        return self.notification.required_finish

    def run(self, function: str) -> None:
        allowed = ORDER_SYS_TRANSITIONS[self.sys_status]
        if function not in allowed:
            raise ValueError(
                f"{self.number}: функция «{function}» недоступна "
                f"из системного статуса {self.sys_status}"
            )
        if function == "деблокировать":
            self._check_releasable()
        if function == "подтвердить":
            self._check_confirmable()
        if function == "техническое_закрытие":
            self._check_teco()
        new_sys, forced_user = allowed[function]
        self.sys_status = new_sys
        if forced_user:
            self.user_status = forced_user
        if function == "деблокировать":
            self.agreed_cost = self.cost  # фиксируем согласованную стоимость

    def set_user_status(self, status: str) -> None:
        if status not in ORDER_USER_MANUAL[self.user_status]:
            raise ValueError(
                f"{self.number}: переход {self.user_status} -> {status} запрещён"
            )
        self.user_status = status

    def change_cost(self, new_cost: float) -> None:
        """Рост стоимости после деблокирования ограничен 10 процентами."""
        if self.agreed_cost is not None and new_cost > self.agreed_cost * 1.1:
            raise ValueError(
                f"{self.number}: стоимость {new_cost:.0f} превышает "
                f"согласованную {self.agreed_cost:.0f} более чем на 10% — "
                f"нужен подзаказ"
            )
        self.cost = new_cost

    def confirm_operation(self) -> None:
        if not self.quality_lot_checked:
            raise ValueError(
                f"{self.number}: подтверждение запрещено до ввода результатов "
                f"контроля качества"
            )
        self.confirmed_ops += 1
        if self.confirmed_ops >= self.total_ops:
            self.run("подтвердить")

    # -- проверки ---------------------------------------------------------

    def _check_releasable(self) -> None:
        if self.user_status != "AWRE":
            raise ValueError(
                f"{self.number}: деблокировать можно только заказ со статусом "
                f"AWRE, а сейчас {self.user_status}"
            )
        if "PMEX" in self.control_keys:
            raise ValueError(
                f"{self.number}: есть операции с управляющим ключом PMEX — "
                f"решите, PMIN это или PMSV, либо удалите операцию"
            )

    def _check_confirmable(self) -> None:
        if not self.quality_lot_checked:
            raise ValueError(f"{self.number}: сначала результаты контроля качества")

    def _check_teco(self) -> None:
        if self.sys_status != "ПДТВ":
            raise ValueError(
                f"{self.number}: техническое закрытие требует статуса ПДТВ, "
                f"а сейчас {self.sys_status}"
            )
        n = self.notification
        if n.fault_end is None or n.cause_code is None:
            raise ValueError(
                f"{self.number}: до закрытия заполните в сообщении {n.number} "
                f"конец неисправности и причину отказа"
            )


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


def demo() -> None:
    d0 = date(2026, 3, 2)

    # --- счёт сроков по приоритету ------------------------------------
    n1 = Notification("110000001", "НАС-ЦТП-12", priority=1, created=d0)
    assert n1.required_start == d0, n1.required_start
    assert n1.required_finish == date(2026, 3, 4), n1.required_finish
    n3 = Notification("110000002", "ВШ-КОЛ-07", priority=3, created=d0)
    assert n3.required_start == date(2026, 3, 9)
    assert n3.required_finish == date(2026, 4, 1)

    # --- заявку нельзя утвердить, минуя «ожидает утверждения» ----------
    _expect_error(lambda: n1.set_user_status("APPR"), "INIT -> APPR запрещён")
    n1.set_user_status("AWAP")

    # --- отсрочка: система ставит СОТЛ, за ней автоматом MIRQ ----------
    n1.run("отсрочить")
    assert (n1.sys_status, n1.user_status) == ("СОТЛ", "MIRQ")

    # --- доработали, снова на утверждение, утвердили -------------------
    n1.set_user_status("AWAP")
    n1.run("отдать_в_обработку")
    assert (n1.sys_status, n1.user_status) == ("СОРА", "APPR")

    # --- после APPR приоритет заморожен --------------------------------
    _expect_error(lambda: n1.set_priority(4), "закрыт для изменения после APPR")

    # --- вид заказа должен соответствовать виду сообщения --------------
    _expect_error(
        lambda: Order("400000001", "PREV", n1),
        "не соответствуют виды сообщений и заказов",
    )

    # --- CORR из неутверждённой заявки не создаётся --------------------
    n_raw = Notification("110000003", "ЛЮК-КОЛ-91", priority=2, created=d0)
    _expect_error(
        lambda: Order("400000002", "CORR", n_raw),
        "только из сообщения со статусом APPR",
    )

    # --- нормальный заказ ---------------------------------------------
    o = Order("400000003", "CORR", n1, control_keys=["PMIN", "PMEX"], total_ops=2)
    o.cost = 100_000
    assert o.latest_allowed_finish == date(2026, 3, 4)

    # деблокировать из INIT нельзя
    _expect_error(lambda: o.run("деблокировать"), "только заказ со статусом AWRE")
    o.set_user_status("AWRE")
    # PMEX блокирует деблокирование
    _expect_error(lambda: o.run("деблокировать"), "PMEX")
    o.control_keys = ["PMIN", "PMSV"]
    o.run("деблокировать")
    assert (o.sys_status, o.user_status) == ("ДЕБЛ", "APPR")
    assert o.agreed_cost == 100_000

    # --- рост стоимости больше 10% запрещён ----------------------------
    _expect_error(lambda: o.change_cost(120_000), "более чем на 10%")
    o.change_cost(108_000)  # +8% проходит

    # --- подтверждение: сначала контроль качества ----------------------
    o.quality_lot_checked = False
    _expect_error(o.confirm_operation, "результатов контроля качества")
    o.quality_lot_checked = True
    o.confirm_operation()
    assert o.sys_status == "ДЕБЛ", "одна операция из двух — заказ ещё не ПДТВ"
    o.confirm_operation()
    assert o.sys_status == "ПДТВ", o.sys_status

    # --- техзакрытие требует данных неисправности в сообщении ----------
    _expect_error(lambda: o.run("техническое_закрытие"), "конец неисправности")
    n1.fault_end = date(2026, 3, 3)
    n1.cause_code = "T-042"
    o.run("техническое_закрытие")
    assert o.sys_status == "ТЗКР"
    o.run("закрыть")
    assert o.sys_status == "ЗАКР"

    # --- сообщение с открытым заказом на удаление не пометить ----------
    _expect_error(lambda: n1.run("пометить_на_удаление"), "есть заказы")

    # --- закрытие сообщения ------------------------------------------
    n1.run("закрыть")
    assert n1.sys_status == "СОЗА"

    # --- отмена заявки на этапе утверждения ---------------------------
    n4 = Notification("110000004", "ДАТ-ДЫМ-331", priority=4, created=d0)
    n4.set_user_status("AWAP")
    n4.set_user_status("CNCL")
    n4.run("пометить_на_удаление")
    assert (n4.sys_status, n4.user_status) == ("МТКУ", "CNCL")

    print("Все проверки переходов прошли.")
    print(f"  заявка {n1.number}: приоритет 1, создана {d0}, "
          f"устранить до {n1.required_finish} — это 2 дня")
    print(f"  заявка {n3.number}: приоритет 3, начать с {n3.required_start} "
          f"(+7 дней), устранить до {n3.required_finish} (+1 месяц)")
    print(f"  заказ {o.number}: {o.sys_status}, согласовано "
          f"{o.agreed_cost:.0f} руб., по факту {o.cost:.0f} руб.")


if __name__ == "__main__":
    demo()
