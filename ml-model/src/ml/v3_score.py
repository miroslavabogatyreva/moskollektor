"""Расчёт модели v3 на момент `as_of` — то, что worker забирает каждый час (MOS-145).

Зачем отдельный модуль. Модель v3 — не только 42 признака: системе надо знать отказы D5,
инциденты коллектора и правило выдачи «одно открытое предупреждение». Повторить это
второй раз в worker значило бы держать две реализации, и малейшая разница молча портила
бы прогноз. Здесь всё считает тот же код, что обучал модель:

* суточные признаки — сборщики `src/ml` (`daily`, `grid`, `features_pfx`) по журналу
  `ts <= as_of`, как в проверке причинности (`scripts/ml_autoresearch_v3_causality.py`);
* моменты решения — правило `ml.moments.build_moments`: конец суток и час после старта
  инцидента;
* событийные признаки — `autoresearch_v3/train.py::feature_engineering` без изменений;
* вероятность — `ml.serving.v3_bag.Bag`, та же, что у сервера инференса;
* выдача — `autoresearch_v3/prepare.py::simulate`.

Отказы D5 и инциденты собираются по журналу `ts <= as_of` (`ml.v3_events`) теми же
функциями, что собрали набор, и обрезаются по `as_of`: инцидент виден с `t_start +
CONFIRM_H`, конец отказа — с `t_end`. Готовый набор (`EVENTS`) остался для сверки:
`events="frozen"` берёт события из него, как до MOS-145 (14503). На новой выгрузке
готовый набор устаревает, пересборка — нет.

Значения журнала, которых нет в словаре `ml.journal_vals`, модель не знает. Расчёт их
не отбрасывает молча: пишет в лог и в `unknown_values` выхода и считает дальше.

Правило выдачи помнит своё прошлое: открыто ли сейчас предупреждение, зависит от того,
когда открылось прошлое. У коллектора, который неделями держит высокий риск без инцидентов,
момент открытия задаётся днём начала счёта, и никакой «достаточный разогрев» этого не
снимает (сверка 2024-04-09: коллектор 884, 60 суток разогрева не хватило). Поэтому начало
счёта — фиксированная дата `POLICY_FROM`, первый день test брифа: с неё предупреждения
стенда — ровно те, что судила отложенная проверка. Для моментов раньше неё счёт идёт
за `FALLBACK_DAYS` суток, и состояние предупреждений там зависит от этого выбора.

Журнал читается не весь: самое длинное окно суточных признаков — год, поэтому хватает
`LOOKBACK_DAYS` до начала счёта. Совпадение признаков с набором, собранным по всему
журналу, проверяет `scripts/ml_v3_score.py --check`.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

import duckdb
import pandas as pd

from . import config as C
from . import daily, features_pfx, grid
from . import failure_defs as F
from . import moments as M
from . import v3_events as E
from . import collector_identity as I

ROOT = Path(__file__).resolve().parents[2]
AR = ROOT / "autoresearch_v3"
EVENTS = Path("data/03_processed/v3_final_20260920")   # incidents, failures набора v3
EVENTS_SOURCE = "journal"   # journal — пересборка; frozen — набор EVENTS; иначе каталог
MODEL_DIR = Path("models/v3_collector_20260922")
POLICY_FROM = pd.Timestamp("2026-04-01")    # начало счёта выдачи: первый день test брифа
FALLBACK_DAYS = 90      # начало счёта для моментов раньше POLICY_FROM
LOOKBACK_DAYS = 366     # год окна суточных признаков плюс сутки запаса


def load_code():
    """`prepare` и `train` цикла v3 — ровно те файлы, что обучали модель.

    Имена `prepare` и `train` есть и у цикла v2 (`autoresearch/`). Если в процессе уже
    загружен чужой `prepare`, `import` молча вернул бы его, поэтому сверяется путь файла.
    """
    for name in ("prepare", "prepare_data", "train"):
        mod = sys.modules.get(name)
        if mod is not None and Path(getattr(mod, "__file__", "")).parent != AR:
            del sys.modules[name]
    if str(AR) in sys.path:
        sys.path.remove(str(AR))
    sys.path.insert(0, str(AR))
    import prepare                                                    # noqa: PLC0415
    import train                                                      # noqa: PLC0415
    return prepare, train


def use_data(prepare, train, data_dir: Path) -> None:
    """Каталог набора для `prepare._all_rows` и `load_*`: он читается на каждом вызове."""
    sys.modules["prepare_data"].DATA = Path(data_dir)
    train._EVENTS = None                                              # кэш событий — заново


def connect(tmp: Path):
    con = duckdb.connect()
    con.execute(f"SET memory_limit='6GB'; SET threads=6; SET temp_directory='{tmp}'")
    con.execute("SET preserve_insertion_order=false; SET enable_progress_bar=false")
    return con


def cut_events(con, events: Path, as_of: pd.Timestamp) -> None:
    """Таблицы `incidents` и `failures` готового набора `events`, какими их знала
    система в `as_of`."""
    E.cut(con, f"'{events / 'incidents.parquet'}'", f"'{events / 'failures.parquet'}'",
          as_of)


def build_events(con, source: str | Path, as_of: pd.Timestamp, log=print) -> str:
    """Таблицы `incidents` и `failures` на `as_of`; возвращает, откуда они взяты."""
    if str(source) == "journal":
        E.rebuild(con, as_of, log=log)
        E.cut(con, "incidents_all", "failures_all", as_of)
        return "journal"
    events = EVENTS if str(source) == "frozen" else Path(source)
    cut_events(con, events, as_of)
    return str(events)


def check_values(con, as_of: pd.Timestamp, d_from: pd.Timestamp, log=print) -> list | None:
    """Незнакомые словарю значения журнала в окне суточных признаков.

    Проверка не должна ронять расчёт: ошибка чтения — предупреждение и `None`.
    """
    ts_from = (d_from - pd.Timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d 00:00:00")
    try:
        found = E.unknown_values(con, ts_from, f"{as_of:%Y-%m-%d %H:%M:%S}")
    except Exception as exc:                                          # noqa: BLE001
        log(f"ВНИМАНИЕ: словарь значений журнала не проверен: {exc}")
        return None
    if found:
        log("ВНИМАНИЕ: значения журнала вне словаря ml.journal_vals: " + ", ".join(
            f"{u['value']!r} ({u['rows']} строк)" for u in found))
    return found


def daily_features(con, as_of: pd.Timestamp, d_from: pd.Timestamp) -> None:
    """Таблица `features_pfx` по журналу `[d_from − LOOKBACK_DAYS, as_of]`.

    Окна признаков смотрят назад не дальше года (`n_rows_365d`, «жив» канал, умирающие
    каналы), поэтому для дней `d >= d_from` результат тот же, что по всему журналу.
    """
    ts_from = (d_from - pd.Timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d 00:00:00")
    daily.build_chanday(con, log=lambda _m: None, ts_to=f"{as_of:%Y-%m-%d %H:%M:%S}",
                        ts_from=ts_from)
    daily.build_pfxday(con)
    grid.build_grid_ch(con, as_of)
    grid.build_grid_pfx(con, as_of)
    features_pfx.build(con)


def outage_days(con) -> None:
    """Таблица `calendar`: день-провал — строк меньше 10 % медианы своего года.

    Правило `daily.build_calendar`, но медиана — по дням года, известным к `as_of`:
    остаток года система ещё не видела. Дни-провалы тиков не дают (`build_moments`).
    """
    con.execute(f"""CREATE OR REPLACE TABLE calendar AS
        WITH tot AS (SELECT d, sum(n) AS n_rows_total FROM chanday GROUP BY 1),
        med AS (SELECT year(d) AS y, median(n_rows_total) AS m FROM tot GROUP BY 1)
        SELECT tot.d, tot.n_rows_total, tot.n_rows_total < {C.OUTAGE_SHARE} * med.m AS is_outage
        FROM tot JOIN med ON med.y = year(tot.d)""")


def build_moments(con, as_of: pd.Timestamp, d_from: pd.Timestamp) -> None:
    """Таблица `moments`: решения с `d_from` по `as_of`, правилом `ml.moments`.

    Сам `build_moments` читает таблицы `features`, `calendar`, `incidents`; моменты
    позже `as_of` отрезаются: тик сегодняшних суток наступит только в 23:59:59.
    """
    con.execute("CREATE OR REPLACE VIEW features AS SELECT * FROM features_pfx")
    M.build_moments(con, f"{as_of:%Y-%m-%d}", f"{as_of:%Y-%m-%d %H:%M:%S}")
    con.execute(f"""CREATE OR REPLACE TABLE moments_cut AS SELECT * FROM moments
        WHERE t <= TIMESTAMP '{as_of:%Y-%m-%d %H:%M:%S}' AND d >= DATE '{d_from:%Y-%m-%d}'""")


def write_set(con, dst: Path, d_from: pd.Timestamp) -> None:
    """Набор в формате `prepare_data`: `_all_rows` и `load_*` читают его как обучение."""
    feat_from = d_from - pd.Timedelta(days=1)                         # d_feat у rearm — вчера
    for name, sql in (
            ("moments", "SELECT * FROM moments_cut ORDER BY t, pfx, kind"),
            ("features", f"SELECT * FROM features_pfx WHERE d >= DATE '{feat_from:%Y-%m-%d}' "
                         "ORDER BY d, pfx"),
            ("incidents", "SELECT * FROM incidents ORDER BY t_start, pfx"),
            ("failures", "SELECT * FROM failures ORDER BY t_start, ch")):
        con.execute(f"COPY ({sql}) TO '{dst / (name + '.parquet')}' (FORMAT PARQUET)")


def feature_rows(prepare, train, data_dir: Path, horizon_h: int) -> pd.DataFrame:
    """Моменты с 42 признаками — тем же путём, что `ml_v3_export.check`."""
    use_data(prepare, train, data_dir)
    con = prepare.connect()
    try:
        rows = prepare._all_rows(con)
    finally:
        con.close()
    _, va = train.feature_engineering(rows.iloc[:0], rows, horizon_h)
    return va


def open_warnings(alerts: pd.DataFrame, incidents: pd.DataFrame, as_of: pd.Timestamp,
                  horizon_h: int) -> dict:
    """Открытое к `as_of` предупреждение по коллектору — по правилам `simulate`.

    Открыто, если срок не истёк и ни один инцидент, стартовавший после открытия,
    ещё не подтверждён к `as_of`.
    """
    win, confirm = pd.Timedelta(hours=horizon_h), pd.Timedelta(hours=M.CONFIRM_H)
    out = {}
    for pfx, g in alerts.groupby("pfx"):
        t_open = pd.Timestamp(g["t"].max())
        if as_of - t_open > win:
            continue
        s = pd.to_datetime(incidents.loc[incidents["pfx"] == pfx, "t_start"])
        if ((s > t_open) & (s + confirm <= as_of)).any():
            continue
        out[pfx] = t_open
    return out


def policy_start(as_of: pd.Timestamp) -> pd.Timestamp:
    """День, с которого правило выдачи считает решения для момента `as_of`."""
    if as_of >= POLICY_FROM:
        return POLICY_FROM
    return as_of.normalize() - pd.Timedelta(days=FALLBACK_DAYS)


def moments_start(as_of: pd.Timestamp) -> pd.Timestamp:
    """С какого дня нужны моменты: с начала счёта выдачи, но не позже вчера — иначе в
    первые часы после `POLICY_FROM` у коллекторов не было бы последнего момента решения."""
    return min(policy_start(as_of), as_of.normalize() - pd.Timedelta(days=1))


def score(as_of, events: str | Path | None = None, model_dir: Path | None = None,
          log=print) -> dict:
    """Score with the exact trained channel identity, validated before inference."""
    from .serving.model_store import load_model, ModelLoadError
    as_of = pd.Timestamp(as_of)
    if pd.isna(as_of) or as_of.tzinfo is not None:
        raise ValueError("as_of must be a finite archive-local timestamp without a timezone")
    if as_of < pd.Timestamp(C.DATE_START):
        raise ValueError(f"as_of precedes supported feature history {C.DATE_START}")
    with duckdb.connect() as coverage:
        last = None
        for year in reversed(C.JOURNAL_YEARS):
            path = C.MK / "parquet" / f"j{year}.parquet"
            if path.exists():
                last = coverage.execute(f"SELECT max(ts) FROM '{path}'").fetchone()[0]
                if last is not None:
                    break
    if last is None or as_of > pd.Timestamp(last):
        raise ValueError(f"as_of {as_of} exceeds latest journal observation {last}; fresh data is required")
    model_dir = Path(model_dir or MODEL_DIR)
    model = load_model(model_dir)
    registry = None
    if model.meta.get("object_level") == "collector":
        contract = model.meta.get("channel_registry", {})
        registry = model_dir / contract.get("file", "channels.parquet")
        if not registry.is_file() or I.sha256(registry) != contract.get("sha256"):
            raise ModelLoadError("Collector model channel registry is absent or has changed")
    with I.registry_context(registry):
        return _score(as_of, events=events, model_dir=model_dir, log=log)


def warning_history(alerts, rows, incidents, as_of, meta):
    """Original warning facts and their status as known at as_of, including closed ones.

    The immutable ID is bound to model version, real object ID and opening time.
    Original probability/features are selected at issuance, never copied from the
    newest low/high score. Status may evolve, but these original facts cannot.
    """
    feats = meta["feature_names"]
    horizon = pd.Timedelta(hours=int(meta["horizon_h"]))
    confirm = pd.Timedelta(hours=M.CONFIRM_H)
    result = []
    for alert in alerts.itertuples(index=False):
        opened = pd.Timestamp(alert.t)
        expires = opened + horizon
        source = rows[(rows.pfx == alert.pfx) & (rows.t == opened) &
                      (rows.p >= float(meta["alert_threshold"]))]
        if source.empty:
            raise ValueError(f"No original score for warning {alert.pfx} {opened}")
        original = source.iloc[0]
        starts = pd.to_datetime(incidents.loc[incidents.pfx == alert.pfx, "t_start"])
        confirmed = starts[(starts > opened) & (starts + confirm <= as_of)] + confirm
        closed = confirmed.min() if len(confirmed) else pd.NaT
        if not pd.isna(closed) and closed <= expires:
            status = "closed"
        elif as_of > expires:
            status, closed = "expired", expires
        else:
            status, closed = "open", pd.NaT
        opened_s = f"{opened:%Y-%m-%dT%H:%M:%S}"
        key = f"{meta['model_version']}:{alert.pfx}:{opened_s}"
        result.append(dict(warning_id="warn:" + hashlib.sha256(key.encode()).hexdigest(),
                           collector_id=int(alert.pfx), opened_at=opened_s,
                           expires_at=f"{expires:%Y-%m-%dT%H:%M:%S}",
                           probability=float(original.p),
                           features=[None if pd.isna(original[f]) else float(original[f]) for f in feats],
                           closed_at=None if pd.isna(closed) else f"{closed:%Y-%m-%dT%H:%M:%S}",
                           status=status))
    return result


def _score(as_of, events: str | Path | None = None, model_dir: Path | None = None,
          log=print) -> dict:
    """Всё, что worker показывает на момент `as_of`: по коллектору — последний момент
    решения, его 42 признака и вероятность, открытое предупреждение.

    `events` — откуда отказы и инциденты: `journal` (по умолчанию, `EVENTS_SOURCE`),
    `frozen` — готовый набор `EVENTS`, или каталог с `incidents.parquet`, `failures.parquet`.
    """
    from ml.serving import v3_bag                                     # noqa: PLC0415
    as_of = pd.Timestamp(as_of)
    events = events or EVENTS_SOURCE
    model_dir = Path(model_dir or MODEL_DIR)
    meta = json.loads((Path(model_dir) / "model_meta.json").read_text())
    if meta.get("model_format") != v3_bag.FORMAT:
        raise ValueError(f"{model_dir}: модель не v3-bag")
    if meta.get("object_level") == "collector" and str(events) == "frozen":
        events = Path(meta["reference_dataset"])
    horizon_h, thr = int(meta["horizon_h"]), float(meta["alert_threshold"])
    feats = meta["feature_names"]
    d_from, m_from = policy_start(as_of), moments_start(as_of)
    prepare, train = load_code()
    with tempfile.TemporaryDirectory(prefix="v3_score_") as tmp_s:
        tmp = Path(tmp_s)
        con = connect(tmp)
        try:
            unknown = check_values(con, as_of, m_from, log=log)
            source = build_events(con, events, as_of, log=log)
            daily_features(con, as_of, m_from)
            outage_days(con)
            build_moments(con, as_of, m_from)
            write_set(con, tmp, m_from)
            incidents = con.execute("SELECT pfx, t_start FROM incidents").df()
        finally:
            con.close()
        rows = feature_rows(prepare, train, tmp, horizon_h)
    if rows.empty:
        raise ValueError(f"No decision moments available for {as_of}; cannot issue a forecast")
    bag = v3_bag.Bag.load(Path(model_dir), meta)
    rows["p"] = bag.predict_proba(rows[feats].to_numpy(dtype=float))
    in_policy = pd.to_datetime(rows["t"]).dt.normalize() >= d_from
    alerts = prepare.simulate(rows.loc[in_policy, ["pfx", "t", "p"]], incidents, thr, horizon_h)
    opened = open_warnings(alerts, incidents, as_of, horizon_h)
    last = rows.sort_values(["t", "kind"]).groupby("pfx").tail(1).sort_values("pfx")
    collectors = []
    for r in last.itertuples(index=False):
        t_open = opened.get(r.pfx)
        collectors.append({
            "pfx": r.pfx, "moment_t": f"{pd.Timestamp(r.t):%Y-%m-%dT%H:%M:%S}",
            "moment_kind": r.kind, "p": round(float(r.p), 6),
            "warning_open": t_open is not None,
            "warning_opened_at": None if t_open is None else f"{t_open:%Y-%m-%dT%H:%M:%S}",
            "warning_expires_at": None if t_open is None else
            f"{t_open + pd.Timedelta(hours=horizon_h):%Y-%m-%dT%H:%M:%S}",
            "features": [None if pd.isna(v) else float(v) for v in
                         (getattr(r, f) for f in feats)]})
    if meta.get("object_level") == "collector":
        for collector in collectors:
            collector["collector_id"] = int(collector.pop("pfx"))
    warnings = (warning_history(alerts, rows, incidents, as_of, meta)
                if meta.get("object_level") == "collector" else [])
    log(f"v3_score {as_of}: моментов {len(rows)}, коллекторов {len(collectors)}, "
        f"открыто предупреждений {len(opened)}")
    return {"schema_version": "score.v3", "as_of": f"{as_of:%Y-%m-%dT%H:%M:%S}",
            "model_version": meta["model_version"], "model_sha256": meta["sha256"],
            "horizon_h": horizon_h, "alert_threshold": thr, "feature_schema": "feat.v3",
            "object_level": meta["object_level"],
            "explanation_scope": "mean_tree_logit", "explains_probability": False,
            "warnings": warnings,
            "policy_from": f"{d_from:%Y-%m-%d}",
            "events_source": source, "failure_values": list(F.D5_VALUES),
            "unknown_values": unknown,
            "feature_names": feats, "collectors": collectors,
            "alerts": [{("collector_id" if meta["object_level"] == "collector" else "pfx"):
                        (int(a.pfx) if meta["object_level"] == "collector" else a.pfx),
                        "t": f"{pd.Timestamp(a.t):%Y-%m-%dT%H:%M:%S}"}
                       for a in alerts.itertuples(index=False)],
            "_rows": rows}
