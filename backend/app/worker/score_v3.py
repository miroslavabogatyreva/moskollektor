"""Validate score.v3 and immutable collector warning snapshots.

Product keys are level-2 object-tree collector IDs. The legacy prefix adapter
below is retained for offline historical checks; run_v3 rejects prefix scores.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import math
from pathlib import Path
import json

СХЕМА = "score.v3"
СХЕМА_ПРИЗНАКОВ = "feat.v3"


class ФайлНеГодится(Exception):
    """Файл есть, но им нельзя считать прогноз: не та схема, пусто, битые числа."""


def прочитать(путь: str | Path) -> dict:
    """Разобрать score.json и проверить всё, от чего зависит прогноз.

    Падаем на кривом файле, а не берём что получится: расчёт по молча укоротившемуся
    списку признаков дал бы прогноз без единой ошибки и с чужими числами.
    """
    путь = Path(путь)
    if not путь.exists():
        raise ФайлНеГодится(f"{путь}: файла нет — расчёт ml-score не отработал")
    try:
        д = json.loads(путь.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ФайлНеГодится(f"{путь}: не разбирается как JSON — {e}") from e

    if д.get("schema_version") != СХЕМА:
        raise ФайлНеГодится(
            f"{путь}: schema_version={д.get('schema_version')!r}, ждём {СХЕМА!r}"
        )
    if д.get("feature_schema") != СХЕМА_ПРИЗНАКОВ:
        raise ФайлНеГодится(
            f"{путь}: feature_schema={д.get('feature_schema')!r}, ждём {СХЕМА_ПРИЗНАКОВ!r}"
        )

    if not isinstance(д.get("horizon_h"), int) or isinstance(д["horizon_h"], bool) or д["horizon_h"] <= 0:
        raise ФайлНеГодится("horizon_h must be a positive integer")
    имена = д.get("feature_names") or []
    коллекторы = д.get("collectors") or []
    if not имена or not коллекторы:
        raise ФайлНеГодится(
            f"{путь}: признаков {len(имена)}, коллекторов {len(коллекторы)} — считать нечем"
        )

    for к in коллекторы:
        if len(к.get("features") or []) != len(имена):
            raise ФайлНеГодится(
                f"{путь}: у коллектора {к.get('pfx')} значений {len(к.get('features') or [])}, "
                f"а имён признаков {len(имена)} — порядок признаков не сойдётся"
            )
        if any(v is not None and (not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v)) for v in к["features"]):
            raise ФайлНеГодится("features must be finite numbers or null")
        p = к.get("p")
        if not isinstance(p, (int, float)) or isinstance(p, bool) or not 0.0 <= p <= 1.0:
            raise ФайлНеГодится(
                f"{путь}: у коллектора {к.get('pfx')} вероятность {p!r}"
            )
    if д.get("object_level") == "collector":
        ids = [к.get("collector_id") for к in коллекторы]
        if any(not isinstance(i, int) or isinstance(i, bool) for i in ids) or len(set(ids)) != len(ids):
            raise ФайлНеГодится("collector_id must be unique integer object-tree keys")
        if "warnings" not in д:
            raise ФайлНеГодится("immutable warnings history is required")
        warning_keys = set()
        for w in д["warnings"]:
            opened, expires = момент(w["opened_at"]), момент(w["expires_at"])
            key = (w["collector_id"], opened)
            if key in warning_keys:
                raise ФайлНеГодится("duplicate warning issuance")
            warning_keys.add(key)
            if any(v is not None and (not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v)) for v in w["features"]):
                raise ФайлНеГодится("warning features must be finite numbers or null")
            if w["collector_id"] not in ids or not 0 <= w["probability"] <= 1:
                raise ФайлНеГодится("invalid warning object or probability")
            if abs((expires - opened).total_seconds() / 3600 - д["horizon_h"]) > 1e-6:
                raise ФайлНеГодится("warning expiry differs from model horizon")
            if opened > момент(д["as_of"]) or len(w["features"]) != len(имена):
                raise ФайлНеГодится("invalid warning onset or opening features")
    return д


def момент(value: str) -> datetime:
    """Naive archive times are Moscow time, as in SMVU ingest."""
    result = datetime.fromisoformat(value)
    return result.astimezone(ZoneInfo("Europe/Moscow")) if result.tzinfo else result.replace(tzinfo=ZoneInfo("Europe/Moscow"))


def горизонт(данные: dict, модель: dict, requested: int | None = None) -> int:
    h = данные["horizon_h"]
    if модель.get("horizon_h") != h:
        raise ФайлНеГодится(f"score/model horizon mismatch: {h} / {модель.get('horizon_h')}")
    if модель.get("model_version") != данные["model_version"]:
        raise ФайлНеГодится("score/model version mismatch")
    if requested is not None and requested != h:
        raise ФайлНеГодится(f"requested horizon {requested} differs from trained horizon {h}")
    return h


def горизонт_продукта(данные: dict, модель: dict, requested: int | None = None) -> int:
    h = горизонт(данные, модель, requested)
    if h != 24:
        raise ФайлНеГодится("MOS-219: product requires a trained 24-hour model; candidate publication is blocked")
    return h


def возраст_часов(данные: dict, сейчас: datetime | None = None) -> float:
    """Сколько часов прошло от среза данных файла до `сейчас`.

    Срез, а не время записи файла: `as_of` — это момент, на который посчитаны
    признаки, и именно он должен совпадать с `as_of` нашего прогона. Время файла
    на диске отвечает на другой вопрос — когда расчёт закончился.
    """
    срез = момент(данные["as_of"])
    сейчас = сейчас or datetime.now(timezone.utc)
    return (сейчас - срез).total_seconds() / 3600


def по_коллекторам(данные: dict, мост: dict[str, int]) -> dict[int, dict]:
    """`{collector_id: {p, features, pfx, порог}}` — по одной записи на коллектор.

    `мост` — `{pfx: collector_id}` из `pred.pfx_collector`. Префикс, которого в мосте
    нет, пропускаем и называем в диагностике: молча выброшенный коллектор выглядит
    как коллектор без риска, а это разные вещи.
    """
    if данные.get("object_level") == "collector":
        return {к["collector_id"]: dict(к) for к in данные["collectors"]}
    итог: dict[int, dict] = {}
    чужие: list[str] = []
    for к in данные["collectors"]:
        pfx = str(к["pfx"])
        коллектор = мост.get(pfx)
        if коллектор is None:
            чужие.append(pfx)
            continue
        открыто = bool(к.get("warning_open"))
        прежний = итог.get(коллектор)
        if прежний is None or к["p"] > прежний["p"]:
            итог[коллектор] = {
                "p": float(к["p"]),
                "features": к["features"],
                "pfx": pfx,
                # Флаг складываем по ВСЕМ префиксам коллектора, а не берём у
                # победителя по вероятности: это разные величины. Замер на живом
                # score.json 22.09.2026 — у коллектора 12 предупреждение открыто
                # у префикса 889, а по вероятности побеждает 890 (0,5643) без
                # предупреждения. Взяли бы флаг у победителя — коллекторов под
                # предупреждением стало бы 8 вместо 9, и одно живое предупреждение
                # исчезло бы молча, вместе с проигравшим префиксом.
                "warning_open": открыто or (прежний or {}).get("warning_open", False),
                "moment_t": к.get("moment_t"),
            }
        elif открыто:
            прежний["warning_open"] = True
    if чужие:
        итог["_чужие"] = sorted(чужие)  # читает диагностика прогона, не расчёт
    return итог


def предупреждения(данные: dict) -> tuple[list[dict], list[str]]:
    """Открытые предупреждения модели ПО ПРЕФИКСАМ: `[{pfx, opened_at, expires_at, p}]`.

    По префиксам, а не по коллекторам дерева, потому что модель открывает
    предупреждение на префикс (MOS-180): свести их к коллектору значит склеить
    два разных предупреждения одного коллектора в одно и потерять второе.
    Время в файле наивное московское — так же, как `as_of` (run.py).

    Второй элемент — префиксы, у которых предупреждение открыто, а момента
    открытия нет: ключ заявки собрать не из чего, прогон называет их вслух.
    """
    открытые, без_момента = [], []
    for к in данные["collectors"]:
        if not к.get("warning_open"):
            continue
        if not к.get("warning_opened_at"):
            без_момента.append(str(к["pfx"]))
            continue
        открытые.append({
            "pfx": str(к["pfx"]),
            "opened_at": к["warning_opened_at"],
            "expires_at": к.get("warning_expires_at"),
            "p": float(к["p"]),
        })
    return открытые, sorted(без_момента)


def _selfcheck():
    """Проверки на образцах: контракт, максимум по префиксам, отказ на кривом файле."""
    import tempfile

    имена = ["a", "b"]
    хороший = {
        "schema_version": СХЕМА,
        "feature_schema": СХЕМА_ПРИЗНАКОВ,
        "as_of": "2026-06-30T23:59:59",
        "model_version": "lgbm-v3-bag-2026.09.21",
        "horizon_h": 720,
        "alert_threshold": 0.63,
        "feature_names": имена,
        "collectors": [
            {"pfx": "798", "p": 0.2, "features": [1.0, 2.0], "warning_open": False},
            {"pfx": "797", "p": 0.8, "features": [3.0, 4.0], "warning_open": True},
            {"pfx": "999", "p": 0.5, "features": [5.0, 6.0]},
        ],
    }
    with tempfile.TemporaryDirectory() as д:
        п = Path(д) / "score.json"
        п.write_text(json.dumps(хороший), encoding="utf-8")
        данные = прочитать(п)
        assert len(данные["collectors"]) == 3

        # Два префикса одного коллектора: побеждает больший, и признаки едут его.
        по = по_коллекторам(данные, {"798": 12, "797": 12, "999": 7})
        assert по[12]["p"] == 0.8 and по[12]["pfx"] == "797", по[12]
        assert по[12]["features"] == [3.0, 4.0], по[12]
        assert по[7]["p"] == 0.5 and "_чужие" not in по, по

        # Предупреждение у проигравшего префикса не теряется. Красный случай
        # с живого стенда: коллектор 12, предупреждение у 889, побеждает 890.
        двое = {
            **хороший,
            "collectors": [
                {"pfx": "889", "p": 0.30, "features": [1.0, 2.0], "warning_open": True},
                {
                    "pfx": "890",
                    "p": 0.56,
                    "features": [3.0, 4.0],
                    "warning_open": False,
                },
            ],
        }
        п.write_text(json.dumps(двое), encoding="utf-8")
        по = по_коллекторам(прочитать(п), {"889": 12, "890": 12})
        assert по[12]["p"] == 0.56 and по[12]["pfx"] == "890", по[12]
        assert по[12]["warning_open"] is True, "предупреждение проигравшего потерялось"
        # И в обратном порядке строк — флаг не должен зависеть от того, кто первым.
        двое["collectors"].reverse()
        п.write_text(json.dumps(двое), encoding="utf-8")
        по = по_коллекторам(прочитать(п), {"889": 12, "890": 12})
        assert по[12]["p"] == 0.56 and по[12]["warning_open"] is True, по[12]

        п.write_text(json.dumps(хороший), encoding="utf-8")
        # Префикса нет в мосте — коллектор не выдумывается, префикс назван.
        по = по_коллекторам(данные, {"798": 12})
        assert по["_чужие"] == ["797", "999"], по
        assert set(по) == {12, "_чужие"}, по

        # Возраст считается от среза данных, а не от времени файла.
        часов = возраст_часов(
            данные, datetime(2026, 7, 1, 11, 59, 59, tzinfo=timezone.utc)
        )
        assert abs(часов - 15.0) < 0.01, часов

        # Четыре вида порчи, каждый обязан быть виден.
        for порча, что in (
            ({"schema_version": "score.v2"}, "не та схема файла"),
            ({"feature_schema": "feat.v1"}, "не та схема признаков"),
            ({"collectors": []}, "пусто"),
            (
                {"collectors": [{"pfx": "1", "p": 0.5, "features": [1.0]}]},
                "признак потерялся",
            ),
            (
                {"collectors": [{"pfx": "1", "p": 1.5, "features": [1.0, 2.0]}]},
                "вероятность вне 0..1",
            ),
        ):
            п.write_text(json.dumps({**хороший, **порча}), encoding="utf-8")
            try:
                прочитать(п)
            except ФайлНеГодится:
                pass
            else:
                raise AssertionError(f"не замечено: {что}")

        # Файла нет — это тоже отказ, а не пустой прогноз.
        п.unlink()
        try:
            прочитать(п)
        except ФайлНеГодится:
            pass
        else:
            raise AssertionError("не замечено: файла нет")

    print(
        "самопроверка ok: контракт score.v3, максимум по префиксам, шесть видов порчи"
    )


if __name__ == "__main__":
    _selfcheck()
