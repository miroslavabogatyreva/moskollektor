"""Клиент модели: GET /model и POST /predict. Задача Q7.4 (MOS-67).

Контракт — `contracts/predict.v1.schema.json`, признаки и их порядок —
`contracts/features.v1.yaml`, правила падения — `docs/HLD.md` разд. 6.6.

**Почему urllib, а не httpx.** В `backend/requirements.txt` лежит один `asyncpg`,
и в таблице версий `docs/HLD.md` разд. 7.1.2 HTTP-клиента нет вовсе. Ради одного
POST в час и одного GET перед ним ставить библиотеку в поставку городской
организации не за что: перечень зависимостей у нас спросят. Проверка живости
контейнера `ml` в `deploy/docker-compose.yml` ходит по HTTP тем же urllib —
берём то же самое.

Вызовы блокирующие. Расчёт зовёт их из `asyncio.to_thread`: на весь прогон
приходится два запроса, свой цикл событий ради них держать не за чем.

**Два разных отказа, и путать их нельзя.**

`ModelRejected` — модель ответила и сказала «запрос неверен» (4xx). Виноваты мы:
переставлены признаки, не то число колонок, строк values не столько, сколько
участков. Повторять бессмысленно, второй раз придёт тот же отказ, а расчёт
потеряет 3 секунды. Прогон помечается `failed`, и в `error_text` едет имя поля,
которое назвала модель.

`ModelUnavailable` — модель не ответила вовсе: сеть, таймаут, 5xx, либо ответила
не по контракту. Вот здесь повтор помогает, и здесь же начинается запасной путь
из HLD разд. 6.6: `run.status='degraded'` и скоринг критичности вместо прогноза.

**22 имени лежат в коде, и это вынужденно.** `backend/Dockerfile` копирует
в образ `backend/app` и `db/migrations`, каталога `contracts/` там нет — прочитать
контракт в контейнере нечем. Список продублирован
и охраняется самопроверкой: `selfcheck()` сверяет его с
`contracts/predict.v1.schema.json` и падает на первом же расхождении. Уберём
дублирование одной строкой `COPY contracts ./contracts` в `backend/Dockerfile` —
но Dockerfile ведёт другая сессия, поэтому пока так.

Запуск самопроверки (поднимает свой HTTP-сервер на 127.0.0.1, сеть наружу не нужна):
    .venv/bin/python backend/app/mlclient/client.py
"""

import json
import os
import pathlib
import time
import urllib.error
import urllib.request

ML_URL = os.environ.get("ML_URL", "http://ml:8100")

# Таймаут 30 с при бюджете стадии 5 с (HLD разд. 8.2): запас шестикратный,
# потому что первый запрос после старта контейнера включает загрузку модели с диска.
TIMEOUT_S = float(os.environ.get("ML_TIMEOUT_S", "30"))

# Пауза перед единственным повтором. HLD разд. 6.6: «один повтор через 3 с».
RETRY_AFTER_S = 3.0

SCHEMA_VERSION = "feat.v1"
RESPONSE_SCHEMA_VERSION = "pred.v1"

# Двадцать два имени в порядке contracts/features.v1.yaml. Порядок сам является
# контрактом: переставленные колонки дают уверенный неверный ответ без единой ошибки.
FEATURE_NAMES = [
    "readings_1d", "readings_7d", "readings_30d", "readings_365d",
    "alarms_7d", "alarms_365d", "days_since_last_reading", "sensor_count",
    "silent_share_30d", "chatter_7d", "fault_30d", "freeze_3",
    "undefined_share_7d", "rate_ratio_7d", "rate_ratio_year", "gap_to_median",
    "long_gap_share_7d", "silence_normalized", "neighbor_fault_7d", "bad7",
    "season_sin", "season_cos",
]


class ModelRejected(RuntimeError):
    """Модель ответила отказом: запрос неверен. Повторять нечего."""


class ModelUnavailable(RuntimeError):
    """Модель не ответила или ответила не по контракту. Дальше — запасной путь."""


def _разобрать_отказ(тело: bytes) -> str:
    """Достать из ответа FastAPI, какое поле не понравилось.

    Пустой ответ и чужой формат — не повод потерять причину: возвращаем то, что
    пришло, обрезав до одной строки. Сообщение уезжает в pred.run.error_text,
    и читать его будет человек на приёмке.
    """
    текст = тело.decode("utf-8", "replace").strip()
    try:
        подробность = json.loads(текст).get("detail", текст)
    except (json.JSONDecodeError, AttributeError):
        return текст[:500] or "модель не объяснила отказ"
    if isinstance(подробность, str):
        return подробность[:500]
    куски = []
    for пункт in подробность if isinstance(подробность, list) else [подробность]:
        if isinstance(пункт, dict):
            где = ".".join(str(ч) for ч in пункт.get("loc", [])) or "запрос"
            куски.append(f"{где}: {пункт.get('msg', пункт)}")
        else:
            куски.append(str(пункт))
    return "; ".join(куски)[:500] or "модель не объяснила отказ"


def _запрос(адрес: str, тело: dict | None, таймаут: float) -> dict:
    """Одна попытка. Сюда повторы не заглядывают — они этажом выше."""
    данные = json.dumps(тело).encode() if тело is not None else None
    req = urllib.request.Request(
        адрес, data=данные, method="POST" if тело is not None else "GET",
        headers={"Content-Type": "application/json"} if тело is not None else {})
    try:
        with urllib.request.urlopen(req, timeout=таймаут) as ответ:
            return json.loads(ответ.read())
    except urllib.error.HTTPError as e:
        причина = _разобрать_отказ(e.read())
        if e.code < 500:
            raise ModelRejected(f"{адрес}: {e.code} — {причина}") from e
        raise ModelUnavailable(f"{адрес}: {e.code} — {причина}") from e
    except json.JSONDecodeError as e:
        raise ModelUnavailable(f"{адрес}: ответ не разбирается как JSON — {e}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ModelUnavailable(f"{адрес}: {type(e).__name__} — {e}") from e


def _с_повтором(адрес: str, тело: dict | None, таймаут: float, пауза: float) -> dict:
    """Повторяем то, что может пройти со второго раза, и только это.

    Сеть, таймаут и 5xx — повторяем. 4xx не повторяем: модель уже сказала,
    что запрос неверен, и через 3 секунды скажет то же самое.
    """
    try:
        return _запрос(адрес, тело, таймаут)
    except ModelUnavailable:
        time.sleep(пауза)
        return _запрос(адрес, тело, таймаут)


def get_model(*, url: str = None, таймаут: float = None, пауза: float = RETRY_AFTER_S) -> dict:
    """Кто отвечает. Расчёт зовёт это на стадии 0 и пишет ответ в pred.run."""
    return _с_повтором(f"{(url or ML_URL).rstrip('/')}/model", None,
                       таймаут or TIMEOUT_S, пауза)


def проверить_контракт(модель: dict, ждём: str | None = None) -> None:
    """Схема признаков разошлась — дальше не считаем.

    `ждём` называет схему явно: расчёт по выдаче модели v3 ждёт `feat.v3`,
    прежний путь с заглушкой — `feat.v1`. Умолчание оставлено прежним, чтобы
    старая ветка вела себя ровно как вела.

    HLD разд. 8.2, стадия 0: «Не совпало → failed, выход. Молча считать нельзя».
    Модель, обученная на другом наборе признаков, ответит числами, а не ошибкой,
    и отличить их от правильных будет нечем.
    """
    пришло = модель.get("feature_schema")
    if пришло != (ждём or SCHEMA_VERSION):
        raise ModelRejected(
            f"GET /model: feature_schema='{пришло}', а наш контракт — "
            f"'{ждём or SCHEMA_VERSION}'. Модель обучена на другом наборе признаков")


def predict(run_id: int, computed_at: str, horizon_h: int,
            section_ids: list[int], values: list[list[float | None]],
            directions: tuple[str, ...] = ("sensor_failure",),
            *, url: str = None, таймаут: float = None,
            пауза: float = RETRY_AFTER_S,
            feature_names: list[str] | None = None,
            schema_version: str | None = None) -> dict:
    """POST /predict колонками. Возвращает разобранный ответ модели.

    Имена признаков едут один раз, значения — массивом на каждый участок:
    22 имени против 22 чисел × 3173 участка, это разница между 30 КБ и 900 КБ.

    **Поле `computed_at` контракта несёт срез данных, а не время расчёта.**
    `backend/app/worker/run.py` кладёт сюда `as_of` прогона — момент, на который
    посчитаны признаки. Пока расчёт брал срез по текущей дате, разницы не было;
    после MOS-142 срез идёт по краю выгрузки, и число здесь отстаёт от времени
    запроса на месяцы. Имя поля менять нельзя в одиночку — это граница с
    ML-командой, `contracts/predict.v1.schema.json`; вопрос вынесен в MOS-145.
    """
    if len(values) != len(section_ids):
        raise ValueError(f"строк values {len(values)}, а участков {len(section_ids)}")
    # Имена и версию схемы можно назвать явно — так ходит ветка v3: у модели
    # Николая 42 своих признака, и список приезжает в score.json вместе с числами
    # (backend/app/worker/score_v3.py). Умолчания оставлены прежние, чтобы путь
    # feat.v1 с заглушкой работал ровно как работал.
    имена = feature_names or FEATURE_NAMES
    if len(values) and len(values[0]) != len(имена):
        raise ValueError(
            f"значений в строке {len(values[0])}, а имён признаков {len(имена)} — "
            f"порядок колонок не сойдётся, и модель ответит уверенно и неверно")
    тело = {
        "schema_version": schema_version or SCHEMA_VERSION,
        "run_id": run_id,
        "computed_at": computed_at,
        "horizon_h": horizon_h,
        "directions": list(directions),
        "feature_names": имена,
        "section_ids": section_ids,
        "values": values,
    }
    ответ = _с_повтором(f"{(url or ML_URL).rstrip('/')}/predict", тело,
                        таймаут or TIMEOUT_S, пауза)
    _сверить_ответ(ответ, run_id, section_ids)
    return ответ


def _сверить_ответ(ответ: dict, run_id: int, section_ids: list[int]) -> None:
    """Проверяем длину и порядок, потому что их не проверяет никто другой.

    JSON Schema длины двух разных полей не сравнивает — это сказано прямо
    в `contracts/predict.v1.schema.json`. Ответ, где вероятностей меньше,
    чем участков, разъедет номера и вероятности на единицу: участок получит
    чужой риск, и ошибка эта молчаливая. Заглушка проверяет НАШ запрос,
    а ЕЁ ответ проверяем здесь мы.
    """
    if not isinstance(ответ, dict) or ответ.get("schema_version") != RESPONSE_SCHEMA_VERSION:
        raise ModelUnavailable(
            f"ответ не по контракту: schema_version={ответ.get('schema_version') if isinstance(ответ, dict) else type(ответ).__name__}, "
            f"ждали '{RESPONSE_SCHEMA_VERSION}'")
    if ответ.get("run_id") != run_id:
        raise ModelUnavailable(f"ответ на чужой прогон: run_id={ответ.get('run_id')}, наш {run_id}")
    предсказания = ответ.get("predictions") or []
    if not предсказания:
        raise ModelUnavailable("в ответе нет ни одного направления")
    for п in предсказания:
        вероятности, участки = п.get("probability") or [], п.get("section_ids") or []
        if len(вероятности) != len(section_ids) or len(участки) != len(section_ids):
            raise ModelUnavailable(
                f"направление '{п.get('direction')}': вероятностей {len(вероятности)}, "
                f"участков в ответе {len(участки)}, а спрашивали про {len(section_ids)}")
        if участки != section_ids:
            raise ModelUnavailable(
                f"направление '{п.get('direction')}': порядок участков в ответе "
                f"не совпал с запросом, первое расхождение на позиции "
                f"{next(i for i, (а, б) in enumerate(zip(участки, section_ids)) if а != б)}")


# ---------------------------------------------------------------------------
# Самопроверка
# ---------------------------------------------------------------------------

def _контракт_на_диске():
    """Путь к схеме в репозитории. В образе её нет — тогда сверять не с чем."""
    здесь = pathlib.Path(__file__).resolve()
    for корень in здесь.parents:
        файл = корень / "contracts" / "predict.v1.schema.json"
        if файл.is_file():
            return файл
    return None


def selfcheck():
    """Проверка обязана ловить подделки, а не только пропускать удачный ответ."""
    import http.server
    import threading

    # 1. Список признаков в коде против контракта на диске.
    файл = _контракт_на_диске()
    assert файл, "contracts/predict.v1.schema.json не найден — сверять список не с чем"
    схема = json.loads(файл.read_text())
    запрос = схема["$defs"]["PredictRequest"]["properties"]
    assert FEATURE_NAMES == запрос["feature_names"]["const"], \
        "FEATURE_NAMES разошлись с contracts/predict.v1.schema.json"
    assert SCHEMA_VERSION == запрос["schema_version"]["const"]
    assert RESPONSE_SCHEMA_VERSION == \
        схема["$defs"]["PredictResponse"]["properties"]["schema_version"]["const"]

    # 2. Поддельная модель на 127.0.0.1: отвечает тем, что ей велели.
    сценарий = {"ответы": [], "запросов": 0}

    class Модель(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def _отдать(self):
            сценарий["запросов"] += 1
            код, тело = сценарий["ответы"].pop(0)
            полезное = json.dumps(тело).encode() if not isinstance(тело, bytes) else тело
            self.send_response(код)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(полезное)))
            self.end_headers()
            self.wfile.write(полезное)

        do_GET = do_POST = _отдать

    сервер = http.server.HTTPServer(("127.0.0.1", 0), Модель)
    threading.Thread(target=сервер.serve_forever, daemon=True).start()
    адрес = f"http://127.0.0.1:{сервер.server_port}"

    участки = [40421, 40422]
    значения = [[0.0] * 22, [1.0] * 22]
    хороший = {"schema_version": "pred.v1", "model_version": "stub-0.1", "run_id": 1,
               "horizon_h": 24, "degraded": True,
               "predictions": [{"direction": "sensor_failure",
                                "section_ids": участки, "probability": [0.1, 0.2]}]}

    # 2.1. Удачный вызов: один запрос, ответ разобран.
    сценарий.update(ответы=[(200, хороший)], запросов=0)
    ответ = predict(1, "2026-06-30T23:59:59+03:00", 24, участки, значения, url=адрес, пауза=0.01)
    assert ответ["predictions"][0]["probability"] == [0.1, 0.2], ответ
    assert сценарий["запросов"] == 1, "повтор при удачном ответе не нужен"

    # 2.2. 422: имя поля доехало до нас, повтора НЕ было.
    отказ = {"detail": [{"type": "value_error", "loc": ["body", "feature_names"],
                         "msg": "Value error, feature_names: состав тот же, порядок другой"}]}
    сценарий.update(ответы=[(422, отказ)], запросов=0)
    try:
        predict(1, "2026-06-30T23:59:59+03:00", 24, участки, значения, url=адрес, пауза=0.01)
    except ModelRejected as e:
        assert "порядок другой" in str(e), str(e)
        assert "body.feature_names" in str(e), str(e)
    else:
        raise AssertionError("422 обязан дать ModelRejected")
    assert сценарий["запросов"] == 1, "422 повторять нельзя: второй раз придёт тот же отказ"

    # 2.3. 500, потом 200: повтор ровно один, и он спасает расчёт.
    сценарий.update(ответы=[(500, {"detail": "boom"}), (200, хороший)], запросов=0)
    ответ = predict(1, "2026-06-30T23:59:59+03:00", 24, участки, значения, url=адрес, пауза=0.01)
    assert ответ["model_version"] == "stub-0.1"
    assert сценарий["запросов"] == 2, сценарий["запросов"]

    # 2.4. 500 дважды: дальше запасной путь, а не бесконечные попытки.
    сценарий.update(ответы=[(500, {"detail": "boom"}), (500, {"detail": "boom"})], запросов=0)
    try:
        predict(1, "2026-06-30T23:59:59+03:00", 24, участки, значения, url=адрес, пауза=0.01)
    except ModelUnavailable as e:
        assert "500" in str(e), str(e)
    else:
        raise AssertionError("два отказа подряд обязаны дать ModelUnavailable")
    assert сценарий["запросов"] == 2, "попыток ровно две: первая и один повтор"

    # 2.5. Вероятностей меньше, чем участков. Это самый дорогой случай: числа
    #      разъедутся на единицу, и участок получит чужой риск молча.
    короткий = json.loads(json.dumps(хороший))
    короткий["predictions"][0]["probability"] = [0.1]
    сценарий.update(ответы=[(200, короткий)], запросов=0)
    try:
        predict(1, "2026-06-30T23:59:59+03:00", 24, участки, значения, url=адрес, пауза=0.01)
    except ModelUnavailable as e:
        assert "вероятностей 1" in str(e), str(e)
    else:
        raise AssertionError("короткий массив вероятностей обязан быть отвергнут")

    # 2.6. Порядок участков переставлен: тот же случай, но заметить его труднее.
    переставленный = json.loads(json.dumps(хороший))
    переставленный["predictions"][0]["section_ids"] = участки[::-1]
    сценарий.update(ответы=[(200, переставленный)], запросов=0)
    try:
        predict(1, "2026-06-30T23:59:59+03:00", 24, участки, значения, url=адрес, пауза=0.01)
    except ModelUnavailable as e:
        assert "позиции 0" in str(e), str(e)
    else:
        raise AssertionError("переставленные участки обязаны быть отвергнуты")

    # 2.7. Ответ на чужой прогон.
    чужой = json.loads(json.dumps(хороший)); чужой["run_id"] = 999
    сценарий.update(ответы=[(200, чужой)], запросов=0)
    try:
        predict(1, "2026-06-30T23:59:59+03:00", 24, участки, значения, url=адрес, пауза=0.01)
    except ModelUnavailable as e:
        assert "чужой прогон" in str(e), str(e)
    else:
        raise AssertionError("ответ с чужим run_id обязан быть отвергнут")

    # 2.8. GET /model и сверка схемы признаков.
    сценарий.update(ответы=[(200, {"model_version": "stub-0.1", "feature_schema": "feat.v1"})], запросов=0)
    проверить_контракт(get_model(url=адрес, пауза=0.01))
    сценарий.update(ответы=[(200, {"model_version": "lgbm-2026.09", "feature_schema": "feat.v2"})], запросов=0)
    try:
        проверить_контракт(get_model(url=адрес, пауза=0.01))
    except ModelRejected as e:
        assert "feat.v2" in str(e) and "feat.v1" in str(e), str(e)
    else:
        raise AssertionError("чужая схема признаков обязана останавливать расчёт")

    # 2.9. Модель не отвечает вовсе — порт закрыт.
    сервер.shutdown()
    try:
        get_model(url=адрес, таймаут=0.5, пауза=0.01)
    except ModelUnavailable as e:
        assert "URLError" in str(e) or "Error" in str(e), str(e)
    else:
        raise AssertionError("мёртвый контейнер обязан дать ModelUnavailable")

    # 3. Несовпадение своих же длин ловим до сети, а не после.
    try:
        predict(1, "2026-06-30T23:59:59+03:00", 24, участки, [[0.0] * 22], url=адрес, пауза=0.01)
    except ValueError as e:
        assert "участков" in str(e)
    else:
        raise AssertionError("строк values меньше, чем участков — это наша ошибка, ловим у себя")

    print("selfcheck ok: 22 признака сверены с контрактом, девять случаев отказа разобраны")
    return 0


if __name__ == "__main__":
    raise SystemExit(selfcheck())
