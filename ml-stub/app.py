#!/usr/bin/env python3
"""Заглушка модели: отвечает по контракту POST /predict, пока модели нет.

Зачем она нужна. Обученную модель делает Николай (задача 8.1), а расчётное ядро,
REST API и три экрана мы делаем параллельно — и всем им нужен собеседник, который
отвечает по контракту уже сегодня. Без заглушки половина работы стоит в очереди
за чужим образом. Когда образ приедет, мы поменяем одну строку `ML_IMAGE`
в `deploy/.env`, и наш код не узнает о подмене.

Контракт — `contracts/predict.v1.schema.json`, признаки и их порядок —
`contracts/features.v1.yaml`. Примеры сообщений лежат в `contracts/examples/`.
Методов два, оба названы в `docs/HLD.md` разд. 6: `POST /predict` считает,
`GET /model` рассказывает о себе. Worker дёргает `/model` на старте каждого расчёта
и не идёт дальше, если `feature_schema` разошлась с нашим контрактом.

**Заглушка придирчива к запросу, и это её главная польза.** Отвечать константой
умеет и пустой файл. Ценность в том, что она проверяет наш собственный запрос:
переставленные местами признаки, 21 колонка вместо 22, число строк values, не
совпавшее с числом участков. Все три ошибки собираются молча на нашей стороне
в Q3, и с настоящей моделью мы бы увидели не отказ, а просто неверные вероятности.
Здесь они дают 422 с именем поля.

Две проверки JSON Schema не делает, их контракт прямо оставляет ML-контейнеру:
длину values против section_ids и длину каждой строки values. Обе здесь есть.

**Число, которое мы возвращаем.** Не константа: одинаковая вероятность у всех
участков не даёт проверить ни сортировку по риску на дашборде, ни пороги светофора.
Берём воспроизводимое число от section_id — один и тот же участок всегда получает
одну и ту же вероятность, поэтому экран не мигает между обновлениями. Распределение
скошено к нулю возведением в куб: половина участков ниже 0,125, около процента
выше 0,78 — это похоже на то, что даст настоящая модель, и красным горит не весь
город.

**degraded: true в каждом ответе.** Поле необязательное, и мы ставим его нарочно:
пока в ответе стоит этот флаг, ни один экран и ни один протокол приёмки не выдаст
заглушку за прогноз. Пропадёт флаг — значит отвечает модель Николая.

Запуск:
    uvicorn app:app --host 0.0.0.0 --port 8100     # как в контейнере
    python3 ml-stub/app.py                         # самопроверка без сети
"""

import hashlib
import pathlib
import sys
from typing import Annotated, Literal

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field, model_validator

MODEL_VERSION = "stub-0.1"

# Двадцать два имени в порядке contracts/features.v1.yaml. Порядок сам является
# контрактом: переставленные колонки означают, что признаки собраны не те, и ловить
# это надо здесь, а не по странным вероятностям через неделю.
FEATURE_NAMES = [
    "readings_1d", "readings_7d", "readings_30d", "readings_365d",
    "alarms_7d", "alarms_365d", "days_since_last_reading", "sensor_count",
    "silent_share_30d", "chatter_7d", "fault_30d", "freeze_3",
    "undefined_share_7d", "rate_ratio_7d", "rate_ratio_year", "gap_to_median",
    "long_gap_share_7d", "silence_normalized", "neighbor_fault_7d", "bad7",
    "season_sin", "season_cos",
]


class PredictRequest(BaseModel):
    # extra="forbid" повторяет additionalProperties: false из схемы: лишнее поле
    # в запросе — признак того, что кто-то расширил контракт в одну сторону.
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["feat.v1"]
    run_id: Annotated[int, Field(ge=1)]
    computed_at: str
    horizon_h: Annotated[int, Field(gt=0)]
    directions: Annotated[list[str], Field(min_length=1)]
    feature_names: list[str]
    section_ids: Annotated[list[int], Field(min_length=1)]
    values: Annotated[list[list[float | None]], Field(min_length=1)]

    @model_validator(mode="after")
    def сверить_формы(self):
        if self.feature_names != FEATURE_NAMES:
            лишние = set(self.feature_names) - set(FEATURE_NAMES)
            нехватка = set(FEATURE_NAMES) - set(self.feature_names)
            подробность = (f"состав разошёлся: лишние {sorted(лишние)}, "
                           f"не хватает {sorted(нехватка)}") if (лишние or нехватка) \
                else "состав тот же, порядок другой"
            raise ValueError(f"feature_names: ждали 22 имени в порядке features.v1.yaml, "
                             f"{подробность}")
        if len(self.values) != len(self.section_ids):
            raise ValueError(f"values: строк {len(self.values)}, "
                             f"а участков {len(self.section_ids)} — должно совпадать")
        for i, строка in enumerate(self.values):
            if len(строка) != len(FEATURE_NAMES):
                raise ValueError(f"values[{i}]: чисел {len(строка)}, "
                                 f"а признаков {len(FEATURE_NAMES)}")
        return self


class Prediction(BaseModel):
    direction: str
    section_ids: list[int]
    probability: list[float]


class PredictResponse(BaseModel):
    schema_version: Literal["pred.v1"] = "pred.v1"
    model_version: str = MODEL_VERSION
    run_id: int
    horizon_h: int
    predictions: list[Prediction]
    # Пока стоит этот флаг, ответ пришёл от заглушки, а не от модели.
    degraded: bool = True


def вероятность(section_id: int) -> float:
    """Воспроизводимое число в [0, 1) по номеру участка.

    Умножение на 2 654 435 761 (простое рядом с 2^32 / φ) размешивает соседние
    номера: участки 40421 и 40422 получают непохожие вероятности, а не соседние.
    Куб скашивает распределение к нулю — отказ редок, и дашборд не должен гореть
    красным целиком.
    """
    ровное = ((section_id * 2654435761) % 10_000) / 10_000
    return round(ровное ** 3, 4)


app = FastAPI(title="Заглушка модели Москоллектора", version=MODEL_VERSION)


@app.get("/health")
def health():
    """Живость контейнера. Её спрашивает docker compose и наш планировщик."""
    return {"status": "ok", "model_version": MODEL_VERSION, "degraded": True}


@app.get("/model")
def model():
    """Кто отвечает. Worker пишет этот ответ в pred.run и сверяет feature_schema.

    Чисел про качество здесь нет, и это не упущение: заглушка не обучалась,
    а holdout_precision = 0 читалось бы как «модель считает плохо» вместо
    «модели нет». Пустое значение спутать не с чем.

    sha256 считаем от собственного файла: у заглушки нет весов, но воспроизводимость
    нужна той же — протокол приёмки (М-02) требует назвать, чем именно посчитан
    прогноз, а «заглушка» без версии таким ответом не является.
    """
    сам = pathlib.Path(__file__).resolve()
    return {
        "model_version": MODEL_VERSION,
        "trained_at": None,
        "feature_schema": "feat.v1",
        "directions": ["sensor_failure"],
        "sha256": hashlib.sha256(сам.read_bytes()).hexdigest(),
        "train_rows": 0,
        "holdout_precision": None,
        "holdout_recall": None,
        "degraded": True,
    }


@app.post("/predict", response_model=PredictResponse)
def predict(запрос: PredictRequest) -> PredictResponse:
    вероятности = [вероятность(s) for s in запрос.section_ids]
    return PredictResponse(
        run_id=запрос.run_id,
        horizon_h=запрос.horizon_h,
        predictions=[
            Prediction(direction=направление,
                       section_ids=запрос.section_ids,
                       probability=вероятности)
            for направление in запрос.directions
        ],
    )


def selfcheck():
    """Проверка обязана ловить подделки, а не только пропускать правильный запрос."""
    import json
    from pydantic import ValidationError

    # В репозитории contracts/ лежит рядом с ml-stub/, в образе — внутри WORKDIR.
    # Берём первый существующий: иначе самопроверка проходит на машине разработчика
    # и падает в контейнере, где её и запускают при сборке.
    здесь = pathlib.Path(__file__).resolve().parent
    корень = next((к for к in (здесь, здесь.parent) if (к / "contracts/examples").is_dir()), None)
    assert корень, f"не нашёл contracts/examples ни в {здесь}, ни в {здесь.parent}"
    образец = json.loads((корень / "contracts/examples/predict.request.json").read_text())

    # 1. Настоящий запрос из contracts/examples проходит и даёт ответ по контракту.
    ответ = predict(PredictRequest(**образец))
    as_json = json.loads(ответ.model_dump_json())
    assert as_json["schema_version"] == "pred.v1"
    assert as_json["run_id"] == образец["run_id"] == 48217
    assert as_json["horizon_h"] == образец["horizon_h"] == 24
    assert as_json["degraded"] is True, "ответ заглушки обязан быть помечен degraded"
    assert len(as_json["predictions"]) == len(образец["directions"]) == 1
    п = as_json["predictions"][0]
    assert п["direction"] == "sensor_failure"
    assert п["section_ids"] == образец["section_ids"] == [40421, 40422]
    assert len(п["probability"]) == len(п["section_ids"])
    assert all(0.0 <= v <= 1.0 for v in п["probability"]), п["probability"]
    # Набор полей ответа совпадает с образцом плюс degraded, который образец
    # не показывает: additionalProperties: false в схеме ловит лишнее поле.
    ждали = set(json.loads(
        (корень / "contracts/examples/predict.response.json").read_text())) | {"degraded"}
    assert set(as_json) == ждали, (set(as_json) ^ ждали)

    # 2. GET /model: worker сверяет feature_schema и без совпадения не считает.
    м = model()
    assert м["feature_schema"] == "feat.v1", м
    assert м["directions"] == ["sensor_failure"]
    assert len(м["sha256"]) == 64 and м["sha256"].isalnum()
    assert м["degraded"] is True
    # Пустые, а не нулевые: ноль читался бы как «посчитала плохо».
    assert м["holdout_precision"] is None and м["holdout_recall"] is None
    assert м["trained_at"] is None

    # 3. Одинаковый участок даёт одинаковое число, разные — разные.
    assert вероятность(40421) == вероятность(40421)
    assert вероятность(40421) != вероятность(40422)
    # Соседние номера не дают соседних вероятностей — иначе сортировка по риску
    # на дашборде выстроилась бы просто по номеру участка.
    assert abs(вероятность(40421) - вероятность(40422)) > 0.01, \
        (вероятность(40421), вероятность(40422))
    # Скос к нулю: на трёх тысячах участков половина обязана лежать ниже 0,2.
    выборка = sorted(вероятность(s) for s in range(40000, 43173))
    assert выборка[len(выборка) // 2] < 0.2, выборка[len(выборка) // 2]
    assert выборка[0] >= 0.0 and выборка[-1] < 1.0

    # 4. Четыре подделки, каждая из которых должна получить отказ.
    #    Без этого куска проверка не отличает придирчивую заглушку от всеядной.
    подделки = {
        "переставленные признаки":
            {**образец, "feature_names": образец["feature_names"][::-1]},
        "двадцать один признак":
            {**образец, "feature_names": образец["feature_names"][:-1],
             "values": [строка[:-1] for строка in образец["values"]]},
        "строк values больше, чем участков":
            {**образец, "values": образец["values"] + [образец["values"][0]]},
        "лишнее поле в запросе":
            {**образец, "contributions": [[0.1] * 22]},
    }
    for имя, тело in подделки.items():
        try:
            PredictRequest(**тело)
        except ValidationError:
            pass
        else:
            raise AssertionError(f"подделка прошла, а должна была получить отказ: {имя}")

    # 5. Сообщение об отказе обязано называть, что именно не так: иначе Q3 будет
    #    искать причину в своих признаках, а не в порядке колонок.
    try:
        PredictRequest(**подделки["переставленные признаки"])
    except ValidationError as e:
        assert "порядок другой" in str(e), str(e)

    print("selfcheck ok")
    return 0


if __name__ == "__main__":
    sys.exit(selfcheck())
