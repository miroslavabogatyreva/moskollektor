"""Списочные методы API: риски и прогнозы. Задача MOS-40 (Q4.3), приёмка М-15, М-16.

Пустой результат — 200 и пустой список (Готовность блока Q4, docs/plan.md):
расчёт пишет соседняя сессия, до первого прогона строк в pred.forecast
и pred.forecast_current нет вовсе, и это не повод отвечать ошибкой.
"""
from datetime import date, timedelta

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.schemas import (
    DataStatus,
    DecisionOptions,
    FeedbackIn,
    ForecastDecision,
    ForecastDetail,
    ForecastList,
    RiskItem,
)
from app.auth.deps import require, видимые_участки, проверить_участок
from app.db import КРАЙ_ДАННЫХ, get_conn

router = APIRouter(prefix="/api")


@router.get("/risks", response_model=list[RiskItem])
async def list_risks(
    conn: asyncpg.Connection = Depends(get_conn),
    user=Depends(require("risks.read")),
):
    """Текущий риск по каждому участку — одна строка на участок (М-15).

    `as_of` — срез данных: момент, на который посчитаны признаки. Это НЕ время
    расчёта. До 21.09.2026 поле называлось здесь `computed_at`, а в GET /api/forecasts
    тем же именем ехало время работы расчёта — одно имя, две разные величины,
    и клиент, читавший оба метода, сравнивал несравнимое (MOS-118). Теперь
    `as_of` везде означает срез данных, `computed_at` — время расчёта.

    Пока расчёт брал срез по текущей дате, разница была микросекундной и увидеть
    её было нельзя; после MOS-142 срез идёт по краю выгрузки (30.06.2026), и две
    даты расходятся на 83 суток.

    `risk_class` — уровень риска, `high` или `normal`, посчитанный РАСЧЁТОМ,
    а не клиентом (MOS-106, строка приёмки М-05). Колонка
    `pred.forecast_current.risk_class` есть с миграции 026, но до 22.09.2026
    метод её не отдавал: схема коллектора читала поле, получала `undefined`
    и красила все 3 173 значка серым, то есть уровень риска на карте не был
    виден вовсе.

    ПОЧЕМУ КЛАСС СЧИТАЕТ СЕРВЕР, А НЕ ФРОНТ. `publish.класс_риска()` сравнивает
    вероятность с порогом `ref.app_setting.risk_threshold_high` и держит
    гистерезис `risk_class_hysteresis`: класс поднимается выше порога+дельта
    и снимается ниже порога−дельта, а между границами остаётся прежним.
    Сверх того `publish.удержать_класс()` не даёт классу меняться чаще, чем раз
    в `risk_class_hold_min` минут. И гистерезис, и удержание — это состояние
    МЕЖДУ прогонами; клиент видит одну точку во времени и воспроизвести их
    не может в принципе. Посчитай класс фронт — обе защиты от дребезга исчезли
    бы, и при расчёте раз в 4 минуты объект с вероятностью у порога мигал бы
    на экране у диспетчера 15 раз в час.

    NULL здесь возможен: колонка заполняется расчётом, и до первого прогона
    по объекту её нет. Клиент обязан разобрать этот случай, а не считать
    отсутствие класса низким риском.
    """
    rows = await conn.fetch(
        """
        SELECT section_id, probability, risk_rank, horizon_h, as_of, is_stale,
               risk_class
        FROM pred.forecast_current
        WHERE $1::int[] IS NULL OR section_id = ANY($1)
        ORDER BY risk_rank
        """,
        await видимые_участки(user, conn),
    )
    return [dict(r) for r in rows]


@router.get("/data-status", response_model=DataStatus)
async def data_status(
    conn: asyncpg.Connection = Depends(get_conn),
    _user=Depends(require("risks.read")),
):
    """Состояние данных, на которых стоит текущий прогноз (MOS-148, М-04, М-15).

    Три величины, которые на экране легко спутать, и которые обязаны стоять
    рядом с разными подписями:

    * `data_edge` — докуда доехала выгрузка заказчика. Берётся из данных
      (app.db.КРАЙ_ДАННЫХ), тем же запросом, которым планировщик выбирает срез.
    * `as_of` — срез, на котором считал последний прогон.
    * `computed_at` — когда этот прогон отработал.
    * `lag_days` — на сколько суток срез отстал от края данных.

    ЗАЧЕМ ОТДЕЛЬНЫЙ МЕТОД, А НЕ ПОЛЕ В /api/risks. Дашборд до MOS-148 выводил
    край выгрузки из самих прогнозов: брал max(as_of) по 3 173 строкам ответа
    и подписывал «конец выгрузки заказчика». Пока срез прогону назначает
    планировщик, эти два числа совпадают, и подмена не видна. Стоит запустить
    расчёт руками с другим срезом — экран показывает дату, которой в данных нет.
    Снято на стенде 22.09.2026: прогон 501 со срезом 01.07.2026 01:00 (край при
    этом 30.06.2026), плитка «Данные по состоянию на» показала 01.07.2026.
    Держится это до ближайшего УСПЕШНОГО планового прогона — обычно четыре
    минуты, но прогон падает, если файл ml-score старше 1,5 ч (run.py:70),
    и тогда чужая дата стоит на экране до починки cron.

    Край — величина одна на весь ответ, и в списочный /api/risks её класть
    некуда: либо ломать форму ответа со списка на объект, либо повторять одно
    число в 3 173 строках. Отдельный метод стоит 2 мс и позволяет плитке
    обновляться раз в минуту (НФ-89), не перекачивая 435 КБ рисков.

    `data_edge = null` означает «суточная свёртка пуста», а не ошибку: до первого
    feat.refresh_channel_daily таблица законно пустая. Клиент обязан сказать это
    словами, а не оставить пустое место — иначе пустая плитка читается как ноль.
    `lag_days` в этом случае тоже null: вычитать не из чего.

    ОТСТАВАНИЕ СЧИТАЕТ СЕРВЕР, А НЕ БРАУЗЕР (MOS-129, сделано здесь по решению
    оркестратора 22.09.2026, чтобы не править HLD и приёмочный лист дважды).
    Вычти клиент эти две даты сам — он вычтет их в поясе браузера. Мы на этом
    уже обжигались: срез файла модели читался московским вместо пояса прогона
    (коммит d176033). Здесь обе даты приходят из базы в одном поясе, и разность
    считается там же.
    ЗНАМЕНАТЕЛЬ ТОТ ЖЕ, ЧТО У ПРОГОНА: `run.прогон()` меряет отставание от края
    свёртки теми же сутками (run.py, стадия ms_fetch). Одно понятие — одно число,
    и сверить их можно вычитанием, а не сравнением двух методик.

    Поле мёртвое до MOS-129: на экране его пока никто не показывает.
    """
    # Край в запросе встречается РОВНО ОДИН РАЗ — отсюда вложенный SELECT.
    # Посчитай я отставание вторым обращением к КРАЙ_ДАННЫХ, в одном методе
    # оказалось бы два независимых чтения края, и они разошлись бы ровно тогда,
    # когда свёртка обновится между ними: data_edge от одного чтения, lag_days
    # от другого. Это тот же дефект, который MOS-148 и чинит, только внутри
    # одного запроса.
    row = await conn.fetchrow(
        f"""
        SELECT data_edge, as_of, computed_at,
               (EXTRACT(EPOCH FROM (as_of - data_edge)) / 86400)::double precision
                   AS lag_days
          FROM (
            SELECT ({КРАЙ_ДАННЫХ}) AS data_edge,
                   max(r.as_of)      AS as_of,
                   max(r.started_at) AS computed_at
              FROM pred.forecast_current fc
              JOIN pred.run r ON r.run_id = fc.run_id
          ) t
        """
    )
    return dict(row)


@router.get("/forecasts", response_model=ForecastList)
async def list_forecasts(
    from_: date | None = Query(None, alias="from", description="дата начала периода, включительно"),
    to: date | None = Query(None, description="дата конца периода, включительно — весь день целиком"),
    limit: int = Query(200, ge=1, le=1000, description="сколько записей вернуть, потолок 1000"),
    offset: int = Query(0, ge=0, description="сколько записей пропустить от начала выборки"),
    conn: asyncpg.Connection = Depends(get_conn),
    user=Depends(require("forecasts.read")),
):
    """Прогнозы за период (М-16). Две даты в каждой строке, и они значат разное.

    `as_of` — срез данных, на котором считали (`pred.run.as_of`); `computed_at` —
    время работы расчёта (`pred.run.started_at`). Раньше строка несла только вторую
    дату, а GET /api/risks отдавал под этим же именем первую — MOS-118. Обе даты
    рядом стоят дешевле одной: `pred.run` и так в соединении, лишнего чтения нет,
    а читатель ответа видит, чем они отличаются, не заглядывая в документ.

    **ПРОПУСК В ЖУРНАЛЕ ОЗНАЧАЕТ «ЗНАЧЕНИЕ НЕ МЕНЯЛОСЬ», А НЕ «РАСЧЁТ НЕ ШЁЛ»**
    (MOS-147). С миграции 026 расчёт пишет строку, только когда вероятность вышла
    за мёртвую зону, сменился класс риска или подошёл безусловный «пульс» — раз
    в час на объект. Между двумя строками объекта расчёт по нему шёл, и результат
    был прежним. Отличить одно от другого позволяет `write_reason` в каждой строке:
    `first` — первый прогноз объекта, `change` — изменение, `heartbeat` — пульс,
    `full` — прогон с полным журналом (обратный расчёт для замера метрик),
    `legacy` — строка написана до политики, безусловно (миграция 027).
    Пропуски самого расчёта видны не здесь, а в `pred.run` — их стережёт строка
    приёмки НФ-91 в `code/check_runtime.py`.

    from/to — даты, а не моменты времени, и обе границы включительны. Раньше
    to сравнивался как timestamptz <= 'ГГГГ-ММ-ДД 00:00' и вырезал весь день,
    который назвали: запрос «сегодня с сегодня» при полной базе отвечал пустым
    списком — нашла фронт-сессия 16.09.2026 на боевом контуре. Здесь верхняя
    граница — начало СЛЕДУЮЩЕГО дня, сравнение строгое: включает весь to целиком.

    limit/offset — М-06: без них метод отдаёт журнал целиком (425 183 строки,
    78 МБ на 21.09.2026 на боевом стенде) — страница браузера с этим не
    справляется. total в ответе — число совпадений по фильтру без обрезки
    страницей, а не длина items. **Два запроса, а не count(*) OVER() в одном:**
    на полном журнале без фильтра по дате `pred.forecast` идёт последовательным
    сканированием (индекса на `run_id`/`started_at` под эту сортировку нет),
    и оконная функция считает total до обрезки страницей — заставляет
    материализовать и отсортировать все 425 183 строки вместо top-N по LIMIT.
    Замер 21.09.2026 на боевом стенде: один запрос с count(*) OVER() — 415 мс;
    отдельные COUNT и LIMIT-запрос — 68 мс + 121 мс = 189 мс, больше чем
    вдвое быстрее. WHERE не дублирован текстом — это одна переменная `where`,
    вставленная в оба запроса f-строкой, чтобы условие не могло разойтись
    между двумя местами так же, как разошлась когда-то граница `to`.
    """
    to_exclusive = to + timedelta(days=1) if to else None
    where = """
        WHERE ($1::date IS NULL OR r.started_at >= $1)
          AND ($2::timestamptz IS NULL OR r.started_at < $2)
          AND ($3::int[] IS NULL OR f.section_id = ANY($3))
    """
    участки = await видимые_участки(user, conn)
    total = await conn.fetchval(
        f"""
        SELECT count(*)
        FROM pred.forecast f
        JOIN pred.run r ON r.run_id = f.run_id
        {where}
        """,
        from_, to_exclusive, участки,
    )
    rows = await conn.fetch(
        f"""
        SELECT f.forecast_id, f.section_id, f.direction, f.horizon_h,
               f.probability, f.risk_rank, r.as_of, r.started_at AS computed_at,
               f.write_reason
        FROM pred.forecast f
        JOIN pred.run r ON r.run_id = f.run_id
        {where}
        ORDER BY r.started_at DESC, f.risk_rank, f.forecast_id
        LIMIT $4 OFFSET $5
        """,
        from_, to_exclusive, участки, limit, offset,
    )
    return {
        "total": total,
        "items": [
            {
                "forecast_id": r["forecast_id"],
                "section_id": r["section_id"],
                "direction": r["direction"],
                "horizon_h": r["horizon_h"],
                "probability": r["probability"],
                "risk_rank": r["risk_rank"],
                "as_of": r["as_of"],
                "computed_at": r["computed_at"],
                "write_reason": r["write_reason"],
            }
            for r in rows
        ],
    }


@router.get("/forecasts/{forecast_id}", response_model=ForecastDetail)
async def get_forecast(
    forecast_id: int,
    conn: asyncpg.Connection = Depends(get_conn),
    user=Depends(require("forecasts.read")),
):
    row = await conn.fetchrow(
        """
        SELECT f.forecast_id, f.section_id, f.direction, f.horizon_h,
               f.probability, f.risk_rank, f.factors,
               -- Два разных момента: as_of — срез данных, на котором считали
               -- (снимок выгрузки заказчика); computed_at (r.started_at) —
               -- когда сам расчёт выполнился. Разводит их та же подпись, что
               -- в ObjectCard.current_risk (оркестратор, 17.09.2026).
               r.as_of, r.started_at AS computed_at,
               -- М-12: заявки, которых породил этот прогноз (Q6.5, MOS-60).
               -- Массив, не null: заявок может не быть, метода — не бывает.
               COALESCE(
                   (SELECT array_agg(n.id ORDER BY n.id)
                      FROM maint.notification n
                     WHERE n.forecast_id = f.forecast_id),
                   ARRAY[]::bigint[]
               ) AS order_ids
        FROM pred.forecast f
        JOIN pred.run r ON r.run_id = f.run_id
        WHERE f.forecast_id = $1
        """,
        forecast_id,
    )
    await проверить_участок(user, conn, row["section_id"] if row else None)
    if row is None:
        raise HTTPException(404, "прогноз не найден")
    решение = await conn.fetchrow(ПОСЛЕДНЕЕ_РЕШЕНИЕ, forecast_id)
    return {**dict(row), "decision": dict(решение) if решение else None}


# Последнее решение по прогнозу (MOS-55, US-09 сц. 4). Строки pred.feedback без
# decision_code (до миграции 052) решением не считаем — JOIN их отбрасывает.
ПОСЛЕДНЕЕ_РЕШЕНИЕ = """
    SELECT fb.feedback_id, fb.decision_code, d.name AS decision_name,
           fb.reason_code, r.name AS reason_name, fb.comment,
           fb.decided_by, fb.decided_at
      FROM pred.feedback fb
      JOIN ref.dispatcher_decision d ON d.code = fb.decision_code
      LEFT JOIN ref.feedback_reason r ON r.code = fb.reason_code
     WHERE fb.forecast_id = $1
     ORDER BY fb.decided_at DESC, fb.feedback_id DESC
     LIMIT 1
"""


@router.get("/dispatcher-decisions", response_model=DecisionOptions)
async def decision_options(
    conn: asyncpg.Connection = Depends(get_conn),
    user=Depends(require("forecasts.read")),
):
    """Два справочника для диалога решения (MOS-55): решения диспетчера и причины
    ложного срабатывания. Отдельным методом, а не полем карточки прогноза: они одни
    на все прогнозы, и таскать их в каждом GET /api/forecasts/{id} незачем."""
    decisions = await conn.fetch("SELECT code, name FROM ref.dispatcher_decision ORDER BY sort_order")
    # У причин колонки порядка нет (004_events.sql): по алфавиту, «Неизвестно» последней.
    # COLLATE "C" — порядок байтов UTF-8, для кириллицы без «ё» это алфавит на любом
    # сервере; en_US.UTF-8 на macOS ставил «Погода» раньше «Отказ датчика».
    reasons = await conn.fetch(
        "SELECT code, name FROM ref.feedback_reason"
        " ORDER BY code = 'unknown', name COLLATE \"C\""
    )
    return {"decisions": [dict(r) for r in decisions], "reasons": [dict(r) for r in reasons]}


@router.post("/forecasts/{forecast_id}/feedback", response_model=ForecastDecision, status_code=201)
async def post_feedback(
    forecast_id: int,
    body: FeedbackIn,
    request: Request,
    conn: asyncpg.Connection = Depends(get_conn),
    user=Depends(require("forecasts.decide")),
):
    """Решение диспетчера по прогнозу (MOS-55, Ф-92). Пишут только dispatcher
    и ods_dispatcher (разрешение forecasts.decide, миграция 052) — ту же пару
    ролей проверяет половина Б code/check_no_auto_verdict.py (Ф-75).

    decided_by — из сессии, а не из тела: иначе диспетчер записал бы решение
    от чужого имени. verdict выводится из решения: false_alarm → 0, прочие → 1;
    для 0 причина из ref.feedback_reason обязательна (CHECK в 004_events.sql),
    поэтому без неё отвечаем 422 до INSERT, а не 500 на нарушении CHECK.

    Каждый вызов — новая строка, а не правка прежней: история решений — часть
    журнала, карточка показывает последнее. Область видимости — до 404, тем же
    порядком, что GET /api/forecasts/{id}.
    """
    section_id = await conn.fetchval(
        "SELECT section_id FROM pred.forecast WHERE forecast_id = $1", forecast_id
    )
    await проверить_участок(user, conn, section_id)
    if section_id is None:
        raise HTTPException(404, "прогноз не найден")

    if not await conn.fetchval(
        "SELECT true FROM ref.dispatcher_decision WHERE code = $1", body.decision_code
    ):
        raise HTTPException(422, f"решения {body.decision_code!r} нет в справочнике")
    if body.reason_code is not None and not await conn.fetchval(
        "SELECT true FROM ref.feedback_reason WHERE code = $1", body.reason_code
    ):
        raise HTTPException(422, f"причины {body.reason_code!r} нет в справочнике")
    verdict = 0 if body.decision_code == "false_alarm" else 1
    if verdict == 0 and body.reason_code is None:
        raise HTTPException(422, "для ложного срабатывания нужна причина из справочника")

    comment = (body.comment or "").strip() or None
    feedback_id = await conn.fetchval(
        """
        INSERT INTO pred.feedback (forecast_id, verdict, reason_code, comment, decided_by, decision_code)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING feedback_id
        """,
        forecast_id, verdict, body.reason_code, comment, user["login"], body.decision_code,
    )
    # Middleware в app.api.main дописывает это в тот же ряд audit.user_action (Ф-53).
    request.state.audit_details = {
        "forecast_id": forecast_id,
        "decision_code": body.decision_code,
        "reason_code": body.reason_code,
    }
    return dict(await conn.fetchrow(ПОСЛЕДНЕЕ_РЕШЕНИЕ, forecast_id))
