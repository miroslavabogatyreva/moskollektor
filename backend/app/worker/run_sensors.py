"""Прогноз участков по правилам датчиков — источник всех экранов с 28.09.2026.

Решение Славы 28.09.2026: «переводим всё на датчики». Схема, AlertBar, дашборд,
карточка, журнал прогнозов и заявки читают pred.forecast и pred.forecast_current;
сюда их число теперь кладёт не модель коллектора (score.json), а правила датчика
`app.domain.sensor_risk.rule_split()` — те же, что считает тик
`app.worker.sensor_scores` для таблицы датчиков.

Как участок получает число. Берём все датчики участка на срезе прогона, выбираем
самый рискованный: сначала уровень (high, watch, normal), потом балл, потом
channel_id. Уровень у правил из балла не следует (отказ 12–24 ч назад — high
с баллом 0,019, 24–48 ч — watch с 0,075), поэтому сортировать по баллу нельзя.
Вероятность участка — балл этого датчика в режиме «с синтетикой»
(score_real + score_synth), класс риска — high, если уровень датчика high, иначе
normal. Объяснение — главная причина этого датчика и до двух следующих датчиков
с риском, как в блоке «Почему такой риск» карточки (frontend/src/lib/sensorRisk.ts).

Пример: на участке три датчика, у одного отказ 30 ч назад — watch с баллом 0,0747
(корзина 24–48 ч в sensor_rules.json), два других в норме. Вероятность участка
0,0747, класс normal, текст «<имя датчика>: правило давности (наблюдать): последний
отказ канала 30 ч назад (…)».
"""

import json

from app.domain import sensor_risk
from app.worker import sensor_scores

ВЕРСИЯ = "sensor-rules-24h"
УРОВЕНЬ = {"high": 0, "watch": 1, "normal": 2}
ПОКАЗАТЬ = 3

# Все участки справочника: участок без датчиков тоже получает строку прогноза
# с нулём, иначе схема покажет его «без прогноза», хотя правило просто не сработало.
УЧАСТКИ = "SELECT section_id FROM ref.object_xref ORDER BY section_id"
КАНАЛЫ = """
SELECT channel_id, section_id, name FROM smvu.channel
 WHERE is_active AND section_id IS NOT NULL
"""


def главная_причина(reasons: list[dict]) -> dict | None:
    """Та же, что главнаяПричина() фронта: самая весомая, окно ППР — только если нет других."""
    весомые = [r for r in reasons if r["kind"] != "plan"]
    if not весомые:
        return reasons[0] if reasons else None
    return max(весомые, key=lambda r: r["weight"])


def причина_уровня(reasons: list[dict], level: str) -> dict | None:
    """Причина, которая дала уровень датчика, а не самая весомая.

    Датчик high по давности (вес 0,019) и с предвестником watch (вес 0,49): самая
    весомая — предвестник, но high дало правило давности, и заявка «сработало
    правило высокого риска» обязана назвать его. Уровень правила стоит в тексте
    причины скобкой — sensor_risk.LEVEL_RU. Уровень normal — главная причина.
    Две причины с меткой уровня (давность и предвестник, обе «высокий риск») —
    самая весомая из них, как главнаяПричина() фронта, а не первая по порядку.
    """
    if level in sensor_risk.LEVEL_RU:
        метка = f"({sensor_risk.LEVEL_RU[level]})"
        давшие = [r for r in reasons if метка in r["text"]]
        if давшие:
            return max(давшие, key=lambda r: r["weight"])
    return главная_причина(reasons)


def по_участкам(участки: list[int], датчики: list[dict]) -> dict:
    """Датчики -> одно число, класс, факторы и текст на участок.

    `датчики` — словари {channel_id, section_id, name, score, level, reasons}.
    Возвращает списки одной длины и в порядке `участки`, как их ждёт publish.записать.
    """
    свои: dict[int, list[dict]] = {}
    for д in датчики:
        свои.setdefault(д["section_id"], []).append(д)
    итог = {
        "участки": участки,
        "вероятности": [],
        "уровни": [],
        "факторы": [],
        "тексты": [],
        "главные": {},
    }
    for sid in участки:
        д_уч = sorted(
            свои.get(sid, []),
            key=lambda д: (УРОВЕНЬ[д["level"]], -д["score"], д["channel_id"]),
        )
        риск = [д for д in д_уч if д["level"] != "normal"]
        if д_уч:
            первый = д_уч[0]
            итог["главные"][sid] = первый
        if not д_уч:
            p, уровень, ф = 0.0, 2, []
            текст = "Датчиков участка в расчёте нет — правила сработать не на чем"
        elif not риск:
            p, уровень = float(первый["score"]), 2
            ф = [
                {
                    "f": "sensor_rule",
                    "channel_id": первый["channel_id"],
                    "v": p,
                    "d": "up",
                    "text": п["text"],
                    "kind": п["kind"],
                }
                for п in первый["reasons"][:1]
            ]
            текст = (
                f"Датчиков участка: {len(д_уч)}, все в норме — ни правило давности, "
                f"ни правило предвестника не сработало"
            )
        else:
            p, уровень = float(первый["score"]), УРОВЕНЬ[первый["level"]]
            ф, строки = [], []
            for д in риск[:ПОКАЗАТЬ]:
                п = причина_уровня(д["reasons"], д["level"])
                строки.append(f"{д['name']}: {п['text']}" if п else д["name"])
                if п:
                    ф.append(
                        {
                            "f": "sensor_rule",
                            "channel_id": д["channel_id"],
                            "v": float(д["score"]),
                            "d": "up",
                            "text": п["text"],
                            "kind": п["kind"],
                        }
                    )
            if len(риск) > ПОКАЗАТЬ:
                строки.append(
                    f"Ещё датчиков с риском на участке: {len(риск) - ПОКАЗАТЬ}"
                )
            текст = "; ".join(строки)
        итог["вероятности"].append(p)
        итог["уровни"].append(уровень)
        итог["факторы"].append(ф)
        итог["тексты"].append(текст)
    return итог


async def собрать(conn, as_of) -> dict:
    """Балл каждого датчика на срез `as_of` и свёртка в участки. Базу не пишет."""
    строки = await sensor_scores.баллы(conn, as_of)
    каналы = {r["channel_id"]: r for r in await conn.fetch(КАНАЛЫ)}
    датчики = []
    for channel_id, _, real, synth, _, level_full, reasons, _ in строки:
        к = каналы.get(channel_id)
        if к is None:
            continue
        датчики.append(
            {
                "channel_id": channel_id,
                "section_id": к["section_id"],
                "name": к["name"] or f"канал {channel_id}",
                "score": round(real + synth, 6),
                "level": level_full,
                "reasons": json.loads(reasons),
            }
        )
    участки = [r["section_id"] for r in await conn.fetch(УЧАСТКИ)]
    итог = по_участкам(участки, датчики)
    итог["датчиков"] = len(датчики)
    итог["горизонт"] = sensor_risk.rules()["horizon_h"]
    return итог


def _selfcheck():
    def д(cid, sid, score, level, text="правило", kind="real"):
        return {
            "channel_id": cid,
            "section_id": sid,
            "name": f"Д{cid}",
            "score": score,
            "level": level,
            "reasons": [{"text": text, "weight": score, "kind": kind}],
        }

    # Уровень старше балла: high с 0,019 обходит watch с 0,075.
    и = по_участкам([1], [д(10, 1, 0.075, "watch", "w"), д(11, 1, 0.019, "high", "h")])
    assert и["вероятности"] == [0.019] and и["уровни"] == [0], и
    assert и["тексты"][0].startswith("Д11: h; Д10: w"), и["тексты"]
    assert и["главные"][1]["channel_id"] == 11
    # Участок без датчиков — ноль и normal, а не пропуск.
    и = по_участкам([2], [])
    assert и["вероятности"] == [0.0] and и["уровни"] == [2] and и["факторы"] == [[]], и
    # Все в норме — берём самый большой балл, класс normal.
    и = по_участкам([3], [д(1, 3, 0.001, "normal"), д(2, 3, 0.002, "normal")])
    assert и["вероятности"] == [0.002] and и["уровни"] == [2], и
    assert "все в норме" in и["тексты"][0]
    # Больше трёх с риском — хвост счётчиком.
    и = по_участкам([4], [д(i, 4, 0.5, "watch") for i in range(5)])
    assert и["тексты"][0].endswith("Ещё датчиков с риском на участке: 2"), и["тексты"]
    assert len(и["факторы"][0]) == 3
    # Причина — та, что дала уровень: high по давности, хотя предвестник watch весомее.
    давн = {"text": "правило давности (высокий риск): отказ 13 ч назад", "weight": 0.019,
            "kind": "real"}
    пред = {"text": "симуляция, правило предвестника (наблюдать): …", "weight": 0.49,
            "kind": "synthetic"}
    assert причина_уровня([пред, давн], "high") is давн
    assert причина_уровня([пред, давн], "watch") is пред
    # Две причины уровня high — самая весомая из них при любом порядке.
    пред_h = {**пред, "text": "симуляция, правило предвестника (высокий риск): …"}
    assert причина_уровня([давн, пред_h], "high") is пред_h
    assert причина_уровня([пред_h, давн], "high") is пред_h
    и = по_участкам([5], [{**д(1, 5, 0.5, "high"), "reasons": [пред, давн]}])
    assert "правило давности" in и["тексты"][0] and "предвестника" not in и["тексты"][0], и
    # ППР не становится главной причиной, пока есть весомая.
    assert (
        главная_причина(
            [
                {"text": "ппр", "weight": 0.0, "kind": "plan"},
                {"text": "x", "weight": 0.1, "kind": "real"},
            ]
        )["text"]
        == "x"
    )
    print("run_sensors selfcheck ok: уровень старше балла, пустой участок, хвост, ППР")


if __name__ == "__main__":
    _selfcheck()
