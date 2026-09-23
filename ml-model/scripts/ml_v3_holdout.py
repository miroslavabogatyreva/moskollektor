#!/usr/bin/env python3
"""Отложенная проверка протокола v3: порог выбран заранее, блок оценки смотрится один раз.

В цикле рабочая точка фолда выбирается по самому фолду (огибающая) — это оптимистично.
Здесь порог переносится с более ранних блоков, и на блоке оценки ничего не подбирается.

    python3 scripts/ml_v3_holdout.py rehearsal          # репетиция внутри запечатанного набора
    python3 scripts/ml_v3_holdout.py rehearsal --dir <каталог с вариантом train.py>
    python3 scripts/ml_v3_holdout.py rehearsal --seeds 5     # быстрый прогон
    python3 scripts/ml_v3_holdout.py final --only-look  # test брифа, один раз
    python3 scripts/ml_v3_holdout.py verify             # пересчёт и сверка с готовым отчётом
    python3 scripts/ml_v3_holdout.py nf03               # порог под окно НФ-03, только доноры
    python3 scripts/ml_v3_holdout.py nf03 --h 168       # то же для модели горизонта 7 суток

`rehearsal`: для k = 2, 3, 4 порог берётся с блоков 1 … k−1, судится блок k. Test брифа
не читается.

`final`: собирает незапечатанный набор (журнал до 2026-06-30), берёт порог с четырёх
блоков, учит модель на всём до 2026-04-01 минус зазор и судит блок с 2026-04-01.
Отчёт пишется в `docs/research/alert_policy_20260920/holdout_<дата>.md`; если отчёт уже
есть, запуск отказывает: второй взгляд на test превращает его в ещё один фолд.

`verify`: независимая сверка. Пересчитывает ту же таблицу и сравнивает с отчётом. Это не
второй взгляд: новых чисел о test он не открывает, потому что работает только на том коде,
которым сделан первый, — по хешам `LOOK_CODE`. Другой код — отказ. На другой машине
LightGBM вправе разойтись в третьем знаке; сверка печатает обе величины и разницу.

Геометрия блока (README протокола, раздел 11). Блок — тело и хвост. В теле
(`[start, end_body]`) открытые предупреждения судятся: окно каждого целиком лежит
в данных, Precision считается по ним. Хвост — `H` часов после тела: система продолжает
решать каждый день, но его предупреждения в Precision не идут (их окна обрезаны концом
данных). Recall — по всем инцидентам тела и хвоста и по всем предупреждениям. Геометрия
`now` — прежняя, без решений в хвосте: после последнего дня тела новое предупреждение
открыть нечем, и из инцидентов хвоста засчитывается не больше одного на коллектор;
Recall там мерит долю хвоста, а не модель. `now` печатается для сравнения.

Правила переноса порога (общий счёт TP, FP, FN блоков-доноров на сетке `ABS_GRID`):

* `margin` — порог, самый глубокий внутри цели: максимум меньшего из запасов
  `Precision − 0,7` и `Recall − 0,5`. Цель — прямоугольник, и промах по любой стороне
  одинаково проваливает приёмку. Основное правило;
* `pooled` — лучшая общая Precision при общем Recall не ниже `POOLED_RECALL`.

Мешок здесь — 25 сидов, а не 5 цикла: среднее то же, разброс Precision по наборам
сидов втрое меньше (замер разбора 2). Подбора по фолдам в этом нет.
"""

from __future__ import annotations

import hashlib
import os
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AR = ROOT / "autoresearch_v3"
FINAL_DATA = Path("data/03_processed/v3_final_20260920")
REPORT_DIR = ROOT / "docs" / "research" / "alert_policy_20260920"

HORIZONS_H = (168, 336, 504, 720)   # горизонты цикла плюс 30 суток — месяц планирования ТО
PRIMARY_H = 720                     # основной горизонт; выбран по репетиции, до final
PRIMARY_RULE = "margin"             # основное правило переноса порога; выбрано до final
PRIMARY_GEOMETRY = "tail"           # решения продолжаются в хвосте; выбрано до final
RULES = ("margin", "pooled")
GEOMETRIES = ("tail", "now")
POOLED_RECALL = 0.55
ABS_GRID = [round(0.05 + 0.01 * i, 2) for i in range(91)]        # 0,05 … 0,95
SEEDS = tuple(range(42, 67))        # 25 сидов
TEST_FROM, TEST_END = "2026-04-01", "2026-06-30"
SEAL_DAY, LOOP_SCORE_END = "2026-03-31", "2026-03-10"

# Код, которым сделан единственный взгляд (коммит 665485c, 2026-09-20): SHA-256 файлов.
# `verify` работает только на нём: пересчёт другим кодом был бы вторым взглядом на test.
LOOK_CODE = {
    "train.py": "3ec380d78ae9713ab40f766877020c17bb9266dec9875b02bc88c97972a01369",
    "prepare.py": "f642f0b280ae0d1f2038a5ffb5bb999d6515954e2836167b2c547c19df3b36ee",
    "prepare_data.py": "1a5ab5e4e7eae539716a1faad4415a2327707d755503d1d19e1e889fa0ced1d2",
}
VERIFY_COLS = ("threshold", "per_day", "tp", "fp", "fn", "P", "R", "rule_P", "rule_R")
VERIFY_KEY = ("H", "геометрия", "правило")


def score_end(last_day: str, window_h: int, cap: str | None = None) -> str:
    """Последний день тела блока: окно метки обязано целиком лежать в данных."""
    import pandas as pd                                               # noqa: PLC0415
    end = pd.Timestamp(last_day) - pd.Timedelta(hours=window_h)
    if cap is not None:
        end = min(end, pd.Timestamp(cap))
    return str(end.date())


def _imports(data_dir: Path | None, code_dir: Path = AR):
    """`prepare` читает каталог набора из окружения один раз, при импорте."""
    if data_dir is not None:
        os.environ["AUTORESEARCH_V3_DATA"] = str(data_dir)
    sys.path.insert(0, str(code_dir))
    sys.path.insert(0, str(ROOT / "src"))
    import prepare                                                    # noqa: PLC0415
    import train                                                      # noqa: PLC0415
    return prepare, train


def make_block(prepare, rows, inc_all, start: str, end_body: str, window_h: int) -> dict:
    """Блок оценки: train до зазора, моменты тела и хвоста, инциденты тела и хвоста."""
    import pandas as pd                                               # noqa: PLC0415
    day, t = pd.to_datetime(rows["d"]), pd.to_datetime(rows["t"])
    lo = pd.Timestamp(start)
    body_hi = pd.Timestamp(end_body) + pd.Timedelta(days=1)
    hi = body_hi + pd.Timedelta(hours=window_h)
    gap = pd.Timedelta(days=prepare.gap_days(window_h) + 1)
    t_inc = pd.to_datetime(inc_all["t_start"])
    return {"train": rows[day <= lo - gap].reset_index(drop=True),
            "moments": rows[(day >= lo) & (t < hi)].reset_index(drop=True),
            "incidents": inc_all[(t_inc > lo) & (t_inc < hi)].reset_index(drop=True),
            "body_hi": body_hi, "window_h": window_h}


def score_block(train, block: dict):
    """Скоры моментов тела и хвоста. Метки моментов блока обучению не видны."""
    scores, _inc, _n, _trees = train.run_fold(
        (block["train"], block["moments"], block["incidents"]), time.time(), block["window_h"])
    return scores.assign(kind=block["moments"]["kind"].to_numpy())


def counts(prepare, scores, block: dict, thr: float, geometry: str, lead_h: int = 0,
           max_lead_h: int | None = None) -> dict:
    """TP и FP по предупреждениям тела, TP и FN по всем инцидентам блока.

    Окно зачёта `[lead_h, max_lead_h]`; по умолчанию верхняя граница — горизонт. Срок
    жизни предупреждения в `simulate` — всегда горизонт: окно зачёта меняет оценку,
    а не выдачу.
    """
    pm = prepare.load_predictive_metrics()
    inc, window_h = block["incidents"], block["window_h"]
    max_lead_h = max_lead_h or window_h
    in_body = scores["t"] < block["body_hi"]
    al = prepare.simulate(scores if geometry == "tail" else scores[in_body], inc, thr, window_h)
    fails = prepare._pairs(inc["pfx"], inc["t_start"])
    body = al[al["t"] < block["body_hi"]]
    m_body = pm.evaluate_alerts(prepare._pairs(body["pfx"], body["t"]), fails,
                                horizon_hours=lead_h, max_lead_hours=max_lead_h)
    m_all = pm.evaluate_alerts(prepare._pairs(al["pfx"], al["t"]), fails,
                               horizon_hours=lead_h, max_lead_hours=max_lead_h)
    n_days = max(1, scores.loc[in_body, "t"].dt.normalize().nunique())
    return {"tp_p": m_body["tp"], "fp": m_body["fp"], "tp_r": m_all["tp"], "fn": m_all["fn"],
            "per_day": round(len(body) / n_days, 2), "median_lead_h": m_all["median_lead_hours"],
            "under_24h": m_all["lead_under_24h_share"]}


def _pr(c: dict) -> tuple[float, float]:
    p = c["tp_p"] / (c["tp_p"] + c["fp"]) if c["tp_p"] + c["fp"] else 0.0
    r = c["tp_r"] / (c["tp_r"] + c["fn"]) if c["tp_r"] + c["fn"] else 0.0
    return p, r


def donor_grid(prepare, scores, block: dict, geometry: str, lead_h: int = 0,
               max_lead_h: int | None = None) -> list[dict]:
    return [counts(prepare, scores, block, thr, geometry, lead_h, max_lead_h)
            for thr in ABS_GRID]


def pick_threshold(rule: str, donors: list[list[dict]], target_p: float,
                   target_r: float) -> float:
    """Порог по общему счёту блоков-доноров. На судимом блоке ничего не подбирается."""
    best, best_v = ABS_GRID[0], -9.0
    for i, thr in enumerate(ABS_GRID):
        pooled = {k: sum(d[i][k] for d in donors) for k in ("tp_p", "fp", "tp_r", "fn")}
        if not pooled["tp_p"] or not pooled["tp_r"]:
            continue
        p, r = _pr(pooled)
        value = min(p - target_p, r - target_r) if rule == "margin" else (
            p if r >= POOLED_RECALL else -9.0)
        if value > best_v:
            best, best_v = thr, value
    return best


def judge(prepare, scores, block: dict, thr: float, geometry: str) -> dict:
    """Метрики судимого блока при заранее выбранном пороге."""
    c = counts(prepare, scores, block, thr, geometry)
    p, r = _pr(c)
    out = {"threshold": thr, "per_day": c["per_day"], "tp": c["tp_p"], "fp": c["fp"],
           "fn": c["fn"], "P": round(p, 3), "R": round(r, 3),
           "median_lead_h": c["median_lead_h"]}
    for lead in (1, 24):
        pl, rl = _pr(counts(prepare, scores, block, thr, geometry, lead))
        out.update({f"P_lead{lead}h": round(pl, 3), f"R_lead{lead}h": round(rl, 3)})
    rule = scores.assign(p=(scores["kind"].to_numpy() == "rearm").astype(float))
    pr, rr = _pr(counts(prepare, rule, block, 0.5, geometry))
    out.update({"rule_P": round(pr, 3), "rule_R": round(rr, 3)})
    return out


def validation_blocks(prepare, rows, inc_all, window_h: int) -> list[dict]:
    """Четыре блока на фолдах цикла; тело кончается не позже печати минус горизонт."""
    cap = score_end(SEAL_DAY, window_h, LOOP_SCORE_END)
    out = []
    for start, end in prepare.FOLDS:
        end_body = min(str(end), cap)
        out.append(make_block(prepare, rows, inc_all, str(start), end_body, window_h))
    return out


def _load_rows(prepare, window_h: int):
    con = prepare.connect()
    try:
        rows = prepare._all_rows(con)
    finally:
        con.close()
    return prepare.label(rows.drop(columns=["y", "n_inc_in_window"]), window_h)


def _setup(argv: list[str], data_dir: Path | None):
    code_dir = Path(argv[argv.index("--dir") + 1]).resolve() if "--dir" in argv else AR
    prepare, train = _imports(data_dir, code_dir)
    n = int(argv[argv.index("--seeds") + 1]) if "--seeds" in argv else len(SEEDS)
    train.SEEDS = SEEDS[:n]
    return prepare, train


def stage_rehearsal(argv: list[str]) -> int:
    import pandas as pd                                               # noqa: PLC0415
    prepare, train = _setup(argv, None)
    inc_all, rows_out = prepare.load_incidents(), []
    for window_h in HORIZONS_H:
        blocks = validation_blocks(prepare, _load_rows(prepare, window_h), inc_all, window_h)
        scored = [score_block(train, b) for b in blocks]
        for geometry in GEOMETRIES:
            grids = [donor_grid(prepare, s, b, geometry) for s, b in zip(scored, blocks)]
            for k in range(1, len(blocks)):
                for rule in RULES:
                    thr = pick_threshold(rule, grids[:k], prepare.TARGET_P, prepare.TARGET_R)
                    rows_out.append({"H": window_h, "геометрия": geometry, "правило": rule,
                                     "судим блок": k + 1,
                                     **judge(prepare, scored[k], blocks[k], thr, geometry)})
    df = pd.DataFrame(rows_out)
    pd.set_option("display.width", 250)
    main_rows = df[(df["правило"] == PRIMARY_RULE)]
    print(main_rows.to_string(index=False))
    print()
    print(df.groupby(["H", "геометрия", "правило"])[["P", "R", "rule_P", "rule_R"]]
            .agg(["mean", "min"]).round(3).to_string())
    return 0


NF03_LEAD_H, NF03_MAX_LEAD_H = 24, 168     # окно зачёта НФ-03 Мирославы (MOS-74, 14471)


def stage_nf03(argv: list[str]) -> int:
    """Порог основной строки под окно зачёта НФ-03. Только блоки-доноры, test не судится.

    Модель та же (горизонт 720 ч, выдача та же), меняется только окно зачёта при подборе
    порога: `[24, 168]` ч вместо `[0, 720]`. Контроль: при прежнем окне порог обязан
    выйти тем же, что в отчёте взгляда (0,63). Судить test новым порогом — решение
    отдельное: `final` здесь не вызывается.
    """
    import pandas as pd                                               # noqa: PLC0415
    data = FINAL_DATA if (FINAL_DATA / "moments.parquet").exists() else None
    prepare, train = _setup(argv, data)
    window_h = int(argv[argv.index("--h") + 1]) if "--h" in argv else PRIMARY_H
    geometry = PRIMARY_GEOMETRY
    blocks = validation_blocks(prepare, _load_rows(prepare, window_h), prepare.load_incidents(),
                               window_h)
    scored = [score_block(train, b) for b in blocks]
    out = []
    for name, lead, hi in ((f"0…{window_h}, контроль", 0, None),
                           ("24…168, НФ-03", NF03_LEAD_H, NF03_MAX_LEAD_H)):
        grids = [donor_grid(prepare, s, b, geometry, lead, hi) for s, b in zip(scored, blocks)]
        thr = pick_threshold(PRIMARY_RULE, grids, prepare.TARGET_P, prepare.TARGET_R)
        i = ABS_GRID.index(thr)
        for k, g in enumerate(grids, 1):
            p, r = _pr(g[i])
            out.append({"окно": name, "порог": thr, "блок": k, "tp_p": g[i]["tp_p"],
                        "fp": g[i]["fp"], "tp_r": g[i]["tp_r"], "fn": g[i]["fn"],
                        "P": round(p, 3), "R": round(r, 3)})
        pooled = {c: sum(g[i][c] for g in grids) for c in ("tp_p", "fp", "tp_r", "fn")}
        p, r = _pr(pooled)
        out.append({"окно": name, "порог": thr, "блок": "все", **pooled,
                    "P": round(p, 3), "R": round(r, 3)})
        # кривая общего счёта: видно, есть ли на ней точка внутри цели вообще
        curve = [_pr({c: sum(g[j][c] for g in grids) for c in ("tp_p", "fp", "tp_r", "fn")})
                 for j in range(0, len(ABS_GRID), 5)]
        print(f"{name}: общий счёт доноров, порог → P / R: " + "; ".join(
            f"{ABS_GRID[j]:.2f} → {cp:.3f} / {cr:.3f}"
            for j, (cp, cr) in zip(range(0, len(ABS_GRID), 5), curve)))
    # М-20 при новом пороге: упреждение без нижней границы, верхняя — 168 ч
    thr = out[-1]["порог"]
    lead = [counts(prepare, s, b, thr, geometry, 0, NF03_MAX_LEAD_H)
            for s, b in zip(scored, blocks)]
    df = pd.DataFrame(out)
    print(f"набор: {data or prepare.DATA}; горизонт {window_h} ч, геометрия {geometry}, "
          f"правило {PRIMARY_RULE}, сидов {len(train.SEEDS)}")
    print(df.to_string(index=False))
    print("М-20 при пороге", thr, "(окно 0…168): медиана упреждения по блокам",
          [c["median_lead_h"] for c in lead], "доля позже суток", [c["under_24h"] for c in lead])
    return 0


def build_final_data() -> None:
    """Незапечатанный набор v3: те же функции сборки, журнал до конца выгрузки."""
    import duckdb                                                     # noqa: PLC0415

    from ml import config as C                                        # noqa: PLC0415
    from ml import failure_defs as F                                  # noqa: PLC0415
    from ml import labels                                             # noqa: PLC0415
    from ml import moments as M                                       # noqa: PLC0415

    src = Path(C.OUT)                                                 # основной датасет
    episodes = F.OUT / "D5" / "episodes_ext.parquet"
    FINAL_DATA.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("SET memory_limit='8GB'; SET threads=4")
    try:
        max_ts = C.max_ts(con)
        end_ts = f"{max_ts:%Y-%m-%d %H:%M:%S}"
        con.execute(f"CREATE TABLE features AS SELECT * FROM '{src / 'features_pfx.parquet'}'")
        con.execute(f"CREATE TABLE calendar AS SELECT d, n_rows_total, is_outage "
                    f"FROM '{src / 'calendar.parquet'}'")
        con.execute(f"CREATE TABLE ep_0 AS SELECT * FROM '{episodes}'")
        F.build_failures(con, F.DEFS["D5"], [F.EXT], max_ts)
        labels.build_incidents(con, window_minutes=M.MERGE_MIN)
        M.build_moments(con, f"{max_ts:%Y-%m-%d}", end_ts)
        M.seal_failures(con, end_ts)
        M.seal_episodes(con, f"'{episodes}'", C.CHAN, C.DATE_START, end_ts)
        for table, name, order in (("features", "features", "d, pfx"),
                                   ("calendar", "calendar", "d"),
                                   ("incidents", "incidents", "t_start, pfx"),
                                   ("moments", "moments", "t, pfx, kind"),
                                   ("failures_sealed", "failures", "t_start, ch"),
                                   ("episodes_sealed", "episodes", "t_start, ch")):
            con.execute(f"COPY (SELECT * FROM {table} ORDER BY {order}) "
                        f"TO '{FINAL_DATA / (name + '.parquet')}' (FORMAT PARQUET)")
        for name in ("features_ch", "chanday", "pfxday"):
            dst = FINAL_DATA / f"{name}.parquet"
            if not (dst.exists() or dst.is_symlink()):
                dst.symlink_to((src / f"{name}.parquet").resolve())
    finally:
        con.close()


def final_table(prepare, train):
    """Таблица блока test по всем горизонтам, геометриям и правилам."""
    import pandas as pd                                               # noqa: PLC0415
    inc_all, rows_out = prepare.load_incidents(), []
    for window_h in HORIZONS_H:
        rows = _load_rows(prepare, window_h)
        donors = validation_blocks(prepare, rows, inc_all, window_h)
        donor_scores = [score_block(train, b) for b in donors]
        test = make_block(prepare, rows, inc_all, TEST_FROM, score_end(TEST_END, window_h),
                          window_h)
        test_scores = score_block(train, test)
        for geometry in GEOMETRIES:
            grids = [donor_grid(prepare, s, b, geometry) for s, b in zip(donor_scores, donors)]
            for rule in RULES:
                thr = pick_threshold(rule, grids, prepare.TARGET_P, prepare.TARGET_R)
                main = (window_h, rule, geometry) == (PRIMARY_H, PRIMARY_RULE, PRIMARY_GEOMETRY)
                rows_out.append({"H": window_h, "геометрия": geometry, "правило": rule,
                                 "основная строка": "да" if main else "",
                                 "тело до": score_end(TEST_END, window_h),
                                 "инцидентов": len(test["incidents"]),
                                 **judge(prepare, test_scores, test, thr, geometry)})
    return pd.DataFrame(rows_out)


def stage_final(argv: list[str]) -> int:
    report = REPORT_DIR / f"holdout_{date.today():%Y%m%d}.md"
    done = sorted(REPORT_DIR.glob("holdout_*.md"))
    if done:
        print(f"отчёт уже есть: {done[0]}. Второй взгляд на test запрещён.")
        return 1
    if "--only-look" not in argv:
        print("final смотрит test брифа. Запуск один: добавьте --only-look.")
        return 1
    sys.path.insert(0, str(ROOT / "src"))
    build_final_data()
    prepare, train = _setup(argv, FINAL_DATA)
    df = final_table(prepare, train)
    text = (f"# Отложенная проверка протокола v3, {date.today():%Y-%m-%d}\n\n"
            f"Блок оценки — test брифа: тело с {TEST_FROM} до «{TEST_END} минус горизонт», "
            f"хвост до {TEST_END}. Порог перенесён с четырёх блоков 2025-04 … 2026-03. "
            f"Основная строка выбрана до запуска: горизонт {PRIMARY_H} ч, правило "
            f"`{PRIMARY_RULE}`, геометрия `{PRIMARY_GEOMETRY}`. Мешок {len(train.SEEDS)} "
            f"сидов. Взгляд один.\n\n" + df.to_markdown(index=False) + "\n")
    report.write_text(text)
    print(text)
    return 0


def code_is_the_look_code(code_dir: Path = AR) -> list[str]:
    """Файлы, чей SHA-256 не совпал с кодом единственного взгляда. Пусто — код тот же."""
    return [name for name, want in LOOK_CODE.items()
            if hashlib.sha256((code_dir / name).read_bytes()).hexdigest() != want]


def parse_report(path: Path):
    """Таблица отчёта обратно в DataFrame: строки Markdown между шапкой и концом файла."""
    import pandas as pd                                               # noqa: PLC0415
    lines = [ln for ln in path.read_text().splitlines() if ln.startswith("|")]
    head = [c.strip() for c in lines[0].strip("|").split("|")]
    body = [[c.strip() for c in ln.strip("|").split("|")] for ln in lines[2:]]
    df = pd.DataFrame(body, columns=head)
    for col in df.columns:
        if col not in ("геометрия", "правило", "основная строка", "тело до"):
            df[col] = pd.to_numeric(df[col])
    return df


def stage_verify(argv: list[str]) -> int:
    """Пересчёт таблицы тем же кодом и сверка с отчётом. Новых чисел не открывает."""
    done = sorted(REPORT_DIR.glob("holdout_*.md"))
    if not done:
        print("отчёта ещё нет: сверять не с чем. Первый взгляд делает только `final`.")
        return 1
    changed = code_is_the_look_code()
    if changed:
        print(f"код не тот, которым сделан взгляд: {', '.join(changed)}. Пересчёт был бы "
              f"вторым взглядом на test — отказ. Верните коммит 665485c для каталога "
              f"autoresearch_v3/.")
        return 1
    sys.path.insert(0, str(ROOT / "src"))
    if not (FINAL_DATA / "moments.parquet").exists():
        build_final_data()
    prepare, train = _setup([a for a in argv if a != "--dir"], FINAL_DATA)
    want = parse_report(done[0]).set_index(list(VERIFY_KEY))
    got = final_table(prepare, train).set_index(list(VERIFY_KEY))
    worst = 0.0
    for key in want.index:
        diffs = {c: float(got.loc[key, c]) - float(want.loc[key, c]) for c in VERIFY_COLS}
        worst = max(worst, max(abs(diffs[c]) for c in ("P", "R")))
        mark = "совпало" if all(abs(v) < 5e-4 for v in diffs.values()) else "разошлось"
        print(f"H={key[0]:>3} {key[1]:4s} {key[2]:6s}: отчёт P {want.loc[key, 'P']:.3f} "
              f"R {want.loc[key, 'R']:.3f} | пересчёт P {got.loc[key, 'P']:.3f} "
              f"R {got.loc[key, 'R']:.3f} — {mark}")
    print(f"наибольшее расхождение по Precision и Recall: {worst:.3f}")
    print("СВЕРКА СОШЛАСЬ" if worst < 5e-4 else
          "СВЕРКА РАЗОШЛАСЬ: до 0,02 — разница сборок LightGBM, больше — разбираться")
    return 0 if worst < 0.02 else 1


def main(argv: list[str]) -> int:
    stage = argv[1] if len(argv) > 1 else ""
    if stage == "rehearsal":
        return stage_rehearsal(argv)
    if stage == "nf03":
        return stage_nf03(argv)
    if stage == "final":
        return stage_final(argv)
    if stage == "verify":
        return stage_verify(argv)
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
