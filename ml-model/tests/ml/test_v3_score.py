"""Расчёт модели v3 на момент (`ml.v3_score`, MOS-145).

Главный тест — сверка с набором `v3_final_20260920`, по которому судили test брифа:
расчёт читает журнал окном и пересобирает по нему события на момент, набор собран по
всему журналу. Совпасть обязаны моменты, 42 признака, вероятности и предупреждения. Данные
настоящие; нет журнала, набора или модели — тест пропускается.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from ml import failure_defs as F  # noqa: E402
from ml import v3_score as S  # noqa: E402

MODEL = ROOT / "models" / "v3_20260921"
REF = ROOT / "data" / "03_processed" / "v3_final_20260920"
JOURNAL = ROOT / "data" / "02_interim" / "mk" / "parquet" / "j2026.parquet"

need_data = pytest.mark.skipif(
    not (MODEL.exists() and REF.exists() and JOURNAL.exists()),
    reason="нет модели v3, набора v3_final или журнала")


def test_policy_start_is_fixed_after_policy_from():
    """С первого дня test счёт выдачи один и тот же — как у отложенной проверки."""
    for as_of in ("2026-04-01 00:00:00", "2026-05-15 12:00:00", "2026-06-30 23:59:59"):
        assert S.policy_start(pd.Timestamp(as_of)) == S.POLICY_FROM


def test_policy_start_before_policy_from_falls_back():
    as_of = pd.Timestamp("2024-04-09 12:00:00")
    assert S.policy_start(as_of) == pd.Timestamp("2024-01-10")


def test_moments_start_never_leaves_collectors_empty():
    """В первый час после POLICY_FROM у коллектора должен быть вчерашний тик."""
    as_of = pd.Timestamp("2026-04-01 00:00:00")
    assert S.moments_start(as_of) == pd.Timestamp("2026-03-31")


@need_data
@pytest.mark.parametrize("as_of", ["2026-04-01 05:00:00", "2026-06-30 23:59:59",
                                   "2024-04-09 12:00:00"])
def test_score_matches_training_set(monkeypatch, as_of):
    import ml_v3_score as cli
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(S, "MODEL_DIR", MODEL)
    assert cli.check([as_of]) == 0


@need_data
def test_frozen_events_still_match_training_set(monkeypatch):
    """Путь через готовый набор оставлен для сравнения — и он сходится, как раньше."""
    import ml_v3_score as cli
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(S, "MODEL_DIR", MODEL)
    monkeypatch.setattr(S, "EVENTS_SOURCE", "frozen")
    assert cli.check(["2026-06-30 23:59:59"]) == 0


def test_value_check_never_fails_the_run(monkeypatch):
    """Проверка словаря не роняет расчёт: ошибка чтения — предупреждение и `None`."""
    def unreadable_journal(*_args):
        raise OSError("journal unavailable")

    monkeypatch.setattr(S.E, "unknown_values", unreadable_journal)
    said = []
    got = S.check_values(None, pd.Timestamp("2026-06-30 23:59:59"),
                         pd.Timestamp("2026-06-29"), log=said.append)
    assert got is None and "не проверен" in said[0]
    assert "journal unavailable" in said[0]


@need_data
def test_score_output_contract(monkeypatch):
    """Что забирает worker: 42 признака на коллектор, срок предупреждения = горизонт."""
    monkeypatch.chdir(ROOT)
    out = S.score("2026-06-30 23:59:59", model_dir=MODEL, log=lambda _m: None)
    assert out["schema_version"] == "score.v3" and out["horizon_h"] == 720
    assert out["policy_from"] == "2026-04-01"
    assert out["events_source"] == "journal"
    assert out["failure_values"] == list(F.D5_VALUES)
    assert out["unknown_values"] == []
    alerts = {(a["pfx"], a["t"]) for a in out["alerts"]}
    for c in out["collectors"]:
        assert len(c["features"]) == len(out["feature_names"]) == 42
        assert 0.0 < c["p"] < 1.0
        assert pd.Timestamp(c["moment_t"]) <= pd.Timestamp(out["as_of"])
        if c["warning_open"]:
            opened = pd.Timestamp(c["warning_opened_at"])
            assert (c["pfx"], c["warning_opened_at"]) in alerts
            assert pd.Timestamp(c["warning_expires_at"]) - opened == pd.Timedelta(hours=720)


def test_check_requires_dates():
    import ml_v3_score as cli
    with pytest.raises(ValueError, match="at least one date"):
        cli.check([])
    with pytest.raises(SystemExit) as exc:
        cli.main(["--check"])
    assert exc.value.code == 2


@need_data
def test_cli_reports_bad_model_without_keyerror(capsys):
    import ml_v3_score as cli
    with pytest.raises(SystemExit) as exc:
        cli.main(["--model", str(ROOT / "models/current"), "--check", "2026-06-30"])
    assert exc.value.code == 2
    assert "not a v3-bag model" in capsys.readouterr().err


@pytest.mark.skipif(not (ROOT / "models/v3_collector_20260922/model_meta.json").exists(),
                    reason="collector release not built")
def test_cli_default_is_valid_v3_model():
    import ml_v3_score as cli
    assert cli.validated_model(ROOT / cli.DEFAULT_MODEL).bag is not None


@pytest.mark.parametrize("date", ["not-a-date", "NaT", "2026-06-30T00:00:00Z"])
def test_cli_rejects_invalid_archive_dates(date, capsys):
    import ml_v3_score as cli
    with pytest.raises(SystemExit) as exc:
        cli.main(["--check", date])
    assert exc.value.code == 2
    assert "date" in capsys.readouterr().err.lower()
