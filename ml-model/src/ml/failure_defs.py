"""Кандидаты определений отказа D0…D9: разметка по параметрам, а не по копиям кода.

Вход — `docs/research/failure_definition_20260915/definitions.md` разд. C.
Все девять кандидатов — это значения пяти параметров над полями `val`, `alarm`,
`ts` и справочником `chan` (разд. D.III п. 1 того же документа):

  * `bad`      — SQL-предикат «плохого» значения;
  * `min_hours`— порог длительности эпизода;
  * `close_in` / `close_not_in` — фильтр по значению, которым эпизод закрылся;
  * `sys_hours`— порог по подсистеме (кандидат D8);
  * `kind`     — «эпизод» или «повторяемость коротких эпизодов» (кандидат D7).

Эпизоды пересобираются по алгоритму `data/02_interim/mk/episodes.py` дословно:
меняется только предикат. Порядок внутри секунды — вариант V0 `(ts, ev)`,
единственный, который воспроизводит контрольные числа (`sensitivity.md`, табл. 1).

Один проход по журналу считает эпизоды сразу для всех предикатов: окно
`PARTITION BY ch ORDER BY ts, ev` строится один раз на срез каналов, дальше
каждый предикат только группирует свои строки. Иначе пять предикатов —
это пять чтений 200 млн строк.

Инциденты и метки не переписываются: `labels.build_incidents` и
`labels.build_labels_pfx` вызываются как есть, поэтому окно метки
`[d_end + 24 ч, d_end + 168 ч]` совпадает с `evaluate_alerts(24, 168)`
по построению, а не по договорённости.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import config as C
from . import grid, labels
from . import journal_vals as JV

OUT = Path("data/03_processed/faildef_20260915")
TMP = OUT / "dtmp"

# Журнал читается с 2022: эпизод, начавшийся до окна сборки, в разметку всё равно
# не попадает (`t_start >= DATE_START`), а лишние три файла — это 110 млн строк.
SRC_YEARS = (2022, 2023, 2024, 2025, 2026)

CHUNKS = 16          # каналы режем на части по ch % CHUNKS, как в episodes.py
ORDER = "ts, ev"     # вариант V0

# Сроки устранения из Регламента по подсистемам. Каждое число — пункт документа
# заказчика, а не наша подгонка: ОПС категория I — 3 ч (табл. 10.2 и табл. 10.1,
# резерв питания); АКМ — 24 ч (п. 11.3.3); СДУК категория I — 24 ч (п. 12.5.11–13);
# ИБП — 3 ч (резерв питания ОПС, табл. 10.1). Для температурной подсистемы
# и каналов вне справочника норматива нет — остаётся договорной час.
SYS_HOURS = {
    "Пожарная охрана": 3.0,
    "Охранная подсистема": 3.0,
    "Газовая охрана": 24.0,
    "Диспетчерский контроль": 24.0,
    "Диагностическая подсистема": 3.0,
    "Температурная подсистема": 1.0,
}
SYS_HOURS_DEFAULT = 1.0

# Словарь отказа D5 — определение модели v3. Кортеж, а не только SQL: его же отдаёт
# расчёт на момент (`failure_values` в `score.v3`), чтобы продукт знал, что модель
# считает отказом. Строки — из `journal_vals`; текст предикатов тот же, что был.
D5_VALUES = (JV.FAULT, JV.BATTERY_FAULT, JV.MANY_FAULTS, JV.NOT_DEFINED)

NEISPR = f"val = '{JV.FAULT}'"
EXT = f"val IN ({JV.sql_list(D5_VALUES)})"
LOSS = f"val IN ({JV.sql_list((JV.FAULT, JV.UNDEF))})"
DEEN = f"val = '{JV.DEENERGIZED}' AND alarm"
UPS = f"val = '{JV.ON_BATTERY}'"


@dataclass(frozen=True)
class Part:
    """Одна ветка определения: предикат плюс свои порог и фильтры."""

    bad: str
    min_hours: float = 1.0
    close_in: tuple[str, ...] | None = None      # закрытие только этими значениями
    close_not_in: tuple[str, ...] = ()
    keep_open: bool = True                       # незакрытые эпизоды — отказы
    stype_in: tuple[str, ...] | None = None      # применимость по типу точки
    sys_hours: bool = False                      # порог берётся из SYS_HOURS


@dataclass(frozen=True)
class FailureDef:
    """Кандидат определения отказа целиком."""

    def_id: str
    rule: str                                     # одна строка для отчёта
    parts: tuple[Part, ...] = ()
    kind: str = "episode"                         # episode | burst
    burst_k: int = 3                              # D7: сколько коротких за сутки
    burst_max_h: float = 1.0                      # D7: что считается коротким
    note: str = ""
    extra: dict = field(default_factory=dict)


DEFS: dict[str, FailureDef] = {
    "D0": FailureDef(
        "D0", "«Неисправен» дольше 1 ч, незакрытые считаются отказом",
        (Part(NEISPR, 1.0),)),
    "D1": FailureDef(
        "D1", "«Неисправен» дольше 1 ч и закрыт возвратом в «Норма»",
        (Part(NEISPR, 1.0, close_in=(JV.NORMAL,), keep_open=False),)),
    "D2": FailureDef(
        "D2", "«Неисправен» дольше 1 ч, кроме закрытых «Неопределен»",
        (Part(NEISPR, 1.0, close_not_in=(JV.UNDEF,)),)),
    "D3": FailureDef(
        "D3", "«Неисправен» дольше 3 ч (срок ОПС категории I)",
        (Part(NEISPR, 3.0),)),
    "D4": FailureDef(
        "D4", "«Неисправен» дольше 24 ч (срок восстановления АКМ и СДУК)",
        (Part(NEISPR, 24.0),)),
    "D5": FailureDef(
        "D5", "расширенный словарь: + «Батарея неисправна», «Много неисправных "
              "устройств», «Не определено», порог 1 ч",
        (Part(EXT, 1.0),)),
    "D6": FailureDef(
        "D6", "потеря работоспособности: «Неисправен» ∪ «Неопределен», порог 1 ч",
        (Part(LOSS, 1.0),)),
    "D6s": FailureDef(
        "D6s", "то же объединение при пороге 24 ч",
        (Part(LOSS, 24.0),)),
    "D7": FailureDef(
        "D7", "перемежающийся отказ: 3 и более коротких эпизода канала за сутки, "
              "момент отказа — старт третьего",
        kind="burst", burst_k=3, burst_max_h=1.0),
    "D8": FailureDef(
        "D8", "порог по подсистеме: ОПС 3 ч, АКМ и СДУК 24 ч, прочее 1 ч",
        (Part(NEISPR, 1.0, sys_hours=True),)),
    "D9": FailureDef(
        "D9", "отказ питания: «Обесточен» с тревогой у насосов и вентиляторов "
              "дольше 1 ч ∪ «Питание от батарей» у ИБП дольше 3 ч",
        (Part(DEEN, 1.0, stype_in=("Состояние насоса", "Состояние вентилятора")),
         Part(UPS, 3.0, stype_in=("ИБП",)))),
}

ORDER_IDS = ("D0", "D1", "D2", "D3", "D4", "D5", "D6", "D6s", "D7", "D8", "D9")

EP_COLS = ("ch INT, t_start TIMESTAMP, t_last TIMESTAMP, n_bad INT, "
           "t_end TIMESTAMP, close_val VARCHAR, dur_h DOUBLE")


# ------------------------------------------------------------------ предикаты


def predicates(def_ids=ORDER_IDS) -> list[str]:
    """Различные предикаты `bad`, нужные перечисленным определениям.

    D7 работает над теми же эпизодами «Неисправен», что и D0, поэтому свой
    предикат ему не нужен.
    """
    seen: list[str] = []
    for did in def_ids:
        d = DEFS[did]
        parts = d.parts if d.kind == "episode" else (Part(NEISPR),)
        for p in parts:
            if p.bad not in seen:
                seen.append(p.bad)
    return seen


def ep_table(bad: str, preds: list[str]) -> str:
    return f"ep_{preds.index(bad)}"


# -------------------------------------------------------------- сборка эпизодов


def _src_sql(k: int, chunks: int, ch_where: str = "", ts_to: str | None = None) -> str:
    """Срез журнала по остатку `ch % chunks`, как в episodes.py.

    `DISTINCT` обязателен. В выгрузке 563 940 строк — точные копии (те же `ch`,
    `ts`, `ev`, `val`): 1 491 за 2019-07-05 и 562 449 за 2023-11-24 … 2023-11-29.
    У копий ключ сортировки `(ts, ev)` совпадает, порядок между ними DuckDB
    выбирает произвольно, и счётчик `run` приклеивал копию к давно закрытому
    эпизоду: 22-секундный эпизод 2022-07-27 становился «отказом» на 11 700 часов,
    причём от прогона к прогону — у разных каналов.

    `ts_to` — журнал только до этой метки включительно (расчёт на момент, `v3_events`).
    """
    extra = f" AND ({ch_where})" if ch_where else ""
    if ts_to is not None:
        extra += f" AND ts <= TIMESTAMP '{ts_to}'"
    parts = []
    for y in SRC_YEARS:
        f = C.MK / "parquet" / f"j{y}.parquet"
        if ts_to is not None and y > int(ts_to[:4]):
            continue
        if f.exists():
            parts.append(f"SELECT DISTINCT ch, ts, ev, val, alarm FROM '{f}' "
                         f"WHERE ch % {chunks} = {k}{extra}")
    return " UNION ALL ".join(parts)


def build_episodes(con, preds: list[str], chunks: int = CHUNKS, log=print,
                   ch_where: str = "", ts_to: str | None = None) -> None:
    """Пересобирает эпизоды сразу для всех предикатов за один проход по журналу.

    Окно `PARTITION BY ch ORDER BY ts, ev` считается один раз на срез: из него
    получаются `next_ts`, `next_val` и флаги начала эпизода для каждого
    предиката. Дальше в таблице `s` остаются только строки, «плохие» хоть
    по одному предикату, — это на порядок меньше журнала.

    `ts_to` — эпизоды по журналу, каким он был в `ts_to`. Эпизод, у последней строки
    которого нет следующей записи канала, к `ts_to` ещё не закрыт: `t_end`, `close_val`
    и `dur_h` у него NULL. Без `ts_to` конец — `max(next_ts)`, как при сборке набора:
    на всём журнале незакрытых эпизодов единицы, а в середине журнала незакрыт каждый
    идущий отказ, и `max(next_ts)` закрыл бы его последней «плохой» строкой.
    """
    for i, _bad in enumerate(preds):
        con.execute(f"DROP TABLE IF EXISTS ep_{i}")
        con.execute(f"CREATE TABLE ep_{i} ({EP_COLS})")

    flags = ", ".join(f"({b}) AS b{i}" for i, b in enumerate(preds))
    wins = ", ".join(
        f"b{i}, b{i} AND NOT coalesce(lag(b{i}) OVER w, false) AS st{i}"
        for i in range(len(preds)))
    any_bad = " OR ".join(f"b{i}" for i in range(len(preds)))
    # Строка без следующей записи бывает в эпизоде только последней: count(next_ts)
    # меньше count(*) ровно у незакрытых.
    end, close = "max(next_ts)", "max(next_val)"
    if ts_to is not None:
        end, close = (f"CASE WHEN count(next_ts) = count(*) THEN {x} END"
                      for x in (end, close))

    for k in range(chunks):
        con.execute("DROP TABLE IF EXISTS s")
        con.execute(f"""
        CREATE TEMP TABLE s AS
        SELECT * FROM (
            SELECT ch, ts, ev, lead(ts) OVER w AS next_ts, lead(val) OVER w AS next_val,
                   {wins}
            FROM (SELECT ch, ts, ev, val, {flags}
                  FROM ({_src_sql(k, chunks, ch_where, ts_to)}))
            WINDOW w AS (PARTITION BY ch ORDER BY {ORDER}))
        WHERE {any_bad}""")
        for i in range(len(preds)):
            con.execute(f"""
            INSERT INTO ep_{i}
            WITH g AS (
                SELECT ch, ts, next_ts, next_val,
                       sum(CASE WHEN st{i} THEN 1 ELSE 0 END)
                           OVER (PARTITION BY ch ORDER BY {ORDER}
                                 ROWS UNBOUNDED PRECEDING) AS run
                FROM s WHERE b{i})
            SELECT ch, min(ts), max(ts), count(*), {end}, {close},
                   date_diff('second', min(ts), {end}) / 3600.0
            FROM g GROUP BY ch, run""")
        con.execute("DROP TABLE IF EXISTS s")
        log(f"  часть {k + 1}/{chunks}: " + ", ".join(
            f"ep_{i}={con.execute(f'SELECT count(*) FROM ep_{i}').fetchone()[0]:,}"
            for i in range(len(preds))))


# ---------------------------------------------------------------------- отказы


def _dur_expr(max_ts) -> str:
    """Длительность незакрытого эпизода — до конца выгрузки (вариант «638»)."""
    return (f"date_diff('second', e.t_start, "
            f"TIMESTAMP '{max_ts:%Y-%m-%d %H:%M:%S}') / 3600.0")


def _sys_case() -> str:
    whens = " ".join(f"WHEN c.sys = '{s}' THEN {h}" for s, h in SYS_HOURS.items())
    return f"CASE {whens} ELSE {SYS_HOURS_DEFAULT} END"


def _part_where(p: Part, max_ts) -> str:
    """Условие отбора отказов одной ветки определения."""
    dur = f"coalesce(e.dur_h, {_dur_expr(max_ts)})"
    cond = [f"e.t_start >= DATE '{C.DATE_START}'"]
    cond.append(f"{dur} > {_sys_case()}" if p.sys_hours else f"{dur} > {p.min_hours}")
    if not p.keep_open:
        cond.append("e.t_end IS NOT NULL")
    if p.close_in is not None:
        vals = ", ".join(f"'{v}'" for v in p.close_in)
        cond.append(f"e.close_val IN ({vals})")
    if p.close_not_in:
        vals = ", ".join(f"'{v}'" for v in p.close_not_in)
        cond.append(f"(e.close_val IS NULL OR e.close_val NOT IN ({vals}))")
    if p.stype_in is not None:
        vals = ", ".join(f"'{v}'" for v in p.stype_in)
        cond.append(f"c.stype IN ({vals})")
    return " AND ".join(cond)


def _select_part(p: Part, preds: list[str], max_ts) -> str:
    """SELECT одной ветки в схеме таблицы `failures` из labels.py."""
    return f"""
    SELECT e.ch, c.pfx, c.stype, e.t_start,
           CASE WHEN e.t_end IS NULL THEN {_dur_expr(max_ts)} ELSE e.dur_h END AS dur_h,
           e.t_end IS NOT NULL AS closed
    FROM {ep_table(p.bad, preds)} e LEFT JOIN '{C.CHAN}' c USING (ch)
    WHERE {_part_where(p, max_ts)}"""


def _select_burst(d: FailureDef, preds: list[str], max_ts) -> str:
    """D7: отказ — момент старта K-го короткого эпизода канала за сутки.

    Короткий эпизод — `dur_h <= burst_max_h`; незакрытый короткий невозможен
    (у него длительность считается до конца выгрузки), поэтому отдельного
    условия на `t_end` не нужно.
    """
    dur = f"coalesce(e.dur_h, {_dur_expr(max_ts)})"
    return f"""
    WITH sh AS (
        SELECT e.ch, e.t_start, e.dur_h
        FROM {ep_table(NEISPR, preds)} e
        WHERE {dur} <= {d.burst_max_h}),
    n AS (
        SELECT ch, t_start, dur_h,
               row_number() OVER (PARTITION BY ch, t_start::DATE ORDER BY t_start) AS rn
        FROM sh)
    SELECT n.ch, c.pfx, c.stype, n.t_start, n.dur_h, true AS closed
    FROM n LEFT JOIN '{C.CHAN}' c USING (ch)
    WHERE n.rn = {d.burst_k} AND n.t_start >= DATE '{C.DATE_START}'"""


def build_failures(con, d: FailureDef, preds: list[str], max_ts) -> None:
    """Таблица `failures` для одного определения. Имя то же, что в labels.py,
    поэтому `labels.build_incidents` работает без единой правки."""
    if d.kind == "burst":
        body = _select_burst(d, preds, max_ts)
    else:
        body = "\nUNION ALL\n".join(_select_part(p, preds, max_ts) for p in d.parts)
    con.execute("DROP TABLE IF EXISTS failures")
    con.execute(f"CREATE TABLE failures AS SELECT * FROM ({body}) ORDER BY t_start, ch")


# ------------------------------------------------------------- сетка и разметка


def build_grid(con) -> None:
    """Сетка префикс-день из готового `pfxday.parquet` основного датасета.

    Сетка от определения отказа не зависит: это календарь наблюдений, а не метка.
    Пересобирать её по журналу ради каждого кандидата незачем.
    """
    src = Path(C.OUT) / "pfxday.parquet"
    if not src.exists():
        raise FileNotFoundError(f"нет {src}: сначала scripts/ml_build_dataset.py")
    con.execute("DROP TABLE IF EXISTS pfxday")
    con.execute(f"CREATE TABLE pfxday AS SELECT * FROM '{src}'")


def _build_incidents(con) -> None:
    """`labels.build_incidents` как есть, плюс случай пустой разметки.

    `executemany` в DuckDB не принимает пустой список. На полном парке этого
    не бывает, но тесты гоняют определения на срезе одного префикса, и там
    узкое определение легко даёт ноль отказов с префиксом.
    """
    n = con.execute("SELECT count(*) FROM failures WHERE pfx IS NOT NULL").fetchone()[0]
    if n:
        labels.build_incidents(con)
        return
    con.execute("DROP TABLE IF EXISTS incidents")
    con.execute("CREATE TABLE incidents (pfx VARCHAR, t_start TIMESTAMP)")


def build(def_id: str, con, preds: list[str], max_ts, save: bool = True) -> dict:
    """Отказы, инциденты и метки префикс-дня для одного определения.

    Возвращает счётчики. Файлы кладутся в `faildef_20260915/D{k}/`.
    """
    d = DEFS[def_id]
    build_failures(con, d, preds, max_ts)
    _build_incidents(con)
    labels.build_labels_pfx(con, max_ts)
    if save:
        dst = OUT / def_id
        dst.mkdir(parents=True, exist_ok=True)
        for tbl, order in (("failures", "t_start, ch"), ("incidents", "t_start, pfx"),
                           ("labels_pfx", "pfx, d")):
            con.execute(f"COPY (SELECT * FROM {tbl} ORDER BY {order}) "
                        f"TO '{dst / (tbl + '.parquet')}' (FORMAT PARQUET)")
    return counts(con)


def counts(con) -> dict:
    """Счётчики разметки: отказы, инциденты по разрезам, base rate префиксо-дня."""
    one = lambda sql: con.execute(sql).fetchone()                      # noqa: E731
    res = {"failures": one("SELECT count(*) FROM failures")[0],
           "failures_no_pfx": one(
               "SELECT count(*) FROM failures WHERE pfx IS NULL")[0],
           "incidents": one("SELECT count(*) FROM incidents")[0]}
    for name, lo, hi in C.SPLITS:
        res[f"fail_{name}"] = one(
            f"SELECT count(*) FROM failures WHERE t_start >= DATE '{lo}' "
            f"AND t_start < DATE '{hi}' + INTERVAL 1 DAY")[0]
        res[f"inc_{name}"] = one(
            f"SELECT count(*) FROM incidents WHERE t_start >= DATE '{lo}' "
            f"AND t_start < DATE '{hi}' + INTERVAL 1 DAY")[0]
    rate = one("""
        SELECT count(*), sum(y) FROM labels_pfx l JOIN calendar c USING (d)
        WHERE c.split IS NOT NULL""")
    res["pfx_days"], res["pfx_pos"] = rate[0], rate[1] or 0
    res["base_rate"] = round(res["pfx_pos"] / res["pfx_days"], 4) if res["pfx_days"] else None
    return res


def build_calendar_from_main(con) -> None:
    """Календарь берётся готовым: дни-провалы и разрез от разметки не зависят."""
    src = Path(C.OUT) / "calendar.parquet"
    con.execute("DROP TABLE IF EXISTS calendar")
    con.execute(f"CREATE TABLE calendar AS SELECT * FROM '{src}'")


def prepare(con, chunks: int = CHUNKS, log=print, def_ids=ORDER_IDS,
            ch_where: str = "") -> tuple[list[str], object]:
    """Общая часть: календарь, сетка, эпизоды по всем предикатам."""
    build_calendar_from_main(con)
    build_grid(con)
    max_ts = C.max_ts(con)
    grid.build_grid_pfx(con, max_ts)
    preds = predicates(def_ids)
    build_episodes(con, preds, chunks=chunks, log=log, ch_where=ch_where)
    return preds, max_ts


def connect(memory: str = "16GB", threads: int = 8):
    """DuckDB с лимитами задания и своей временной папкой."""
    import duckdb

    TMP.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET memory_limit='{memory}'")
    con.execute(f"SET threads={threads}")
    con.execute(f"SET temp_directory='{TMP}'")
    con.execute("SET preserve_insertion_order=false")
    con.execute("SET enable_progress_bar=false")
    return con
