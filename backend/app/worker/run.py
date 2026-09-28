"""Прогон расчёта. Задача Q3.2 (MOS-32), docs/HLD.md разд. 8.2.

С 28.09.2026 (решение Славы «переводим всё на датчики») число участка считают
правила датчика — backend/app/worker/run_sensors.py; модели коллектора и
score.json прогон не читает. Стадий семь, колонок ms_* по-прежнему шесть:

    0. взять блокировку, завести строку прогона (model_version = sensor-rules-24h)
    1. граница данных                                        → ms_fetch
    2. суточная свёртка за двое суток                        → ms_aggregate
    3–4. правила датчика по всему парку                      → ms_features, ms_inference = 0
    5. кто из участков high и что написать в заявке          → ms_explain
    6. запись прогноза, свёртка в текущее, NOTIFY            → ms_write
    7. автозаявки на участки с датчиком high                 → своей ms_* нет

**Окно двое суток на стадии 2, а не одни.** Результат проверки события приходит
с задержкой: бригада съездила сегодня, а событие было вчера.

**Что происходит при ошибке.** Прогон пишет status='failed' и текст ошибки,
прошлый прогноз остаётся на месте, но помечается is_stale: API продолжает
отвечать, а диспетчер видит пометку «расчёт не завершён» вместо свежего числа.

**Блокировка.** pg_try_advisory_lock(48217) взята здесь, а не в планировщике:
защищать надо сам расчёт, кто бы его ни запустил — APScheduler, рука на сервере
или вторая копия контейнера. Планировщик по расписанию — отдельная задача Q3.4,
он просто позовёт эту функцию.

Запуск:
    python -m app.worker.run                      # весь парк, срез = сейчас
    python -m app.worker.run --as-of 2026-06-30T23:59:59+03:00
    python -m app.worker.run --limit 50 --rollback # проверка без следов в базе

С `--rollback` прогон перед откатом печатает свои заявки строками `ЗАЯВКА_JSON {...}`:
откат их стирает, а замеру метрик по потоку заявок они нужны (91 срез апреля–июня).

`--limit N` берёт первые N участков по возрастанию section_id (MOS-168). Без `--rollback`
такой прогон заявок не заводит, но текущий прогноз заменяет этими N участками —
поэтому проверочный прогон запускаем только вместе с `--rollback`.
"""

import argparse
import asyncio
import json
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import asyncpg

from app.db import КРАЙ_ДАННЫХ
from app.domain import order_rules, sensor_risk
from app.worker import publish, run_sensors, run_v3

МОСКВА = ZoneInfo("Europe/Moscow")

БЛОКИРОВКА = 48217

# Причина, с которой закрывается прогон, оборванный перезапуском контейнера worker
# (MOS-183, строка НФ-41). Формулировку переносит приёмка задачи дословно — её
# ищут в pred.run.error_text при проверке, поэтому менять текст нельзя, только
# дополнить задачу.
ПРИЧИНА_ОБРЫВА = "оборван перезапуском"

# Путь к score.json нужен теперь только планировщику: при проигрывании архива
# (SCORE_V3_REPLAY=1) он берёт из файла срез, который двигает deploy/ml-score.sh
# (scheduler.срез_проигрывания). Сами числа файла прогон не читает.
ПУТЬ_SCORE = os.environ.get("SCORE_V3_PATH", "")
НАПРАВЛЕНИЕ = "sensor_failure"


class Секундомер:
    """Длительность стадии в миллисекундах, как её ждут колонки pred.run."""

    def __init__(self):
        self.мс = {}

    def стадия(self, имя):
        return _Стадия(self, имя)


class _Стадия:
    def __init__(self, с, имя):
        self.с, self.имя = с, имя

    def __enter__(self):
        self.t = time.perf_counter()
        return self

    def __exit__(self, *e):
        self.с.мс[self.имя] = int((time.perf_counter() - self.t) * 1000)
        return False


async def прогон(conn, as_of: datetime | None = None,
                 предел: int | None = None, full_log: bool | None = None,
                 заявки: bool = True) -> dict:
    """Один расчёт. Возвращает итог прогона — то же, что легло в pred.run.

    `предел` — считать только первые N участков по возрастанию section_id.
    `заявки` — заводить ли автозаявки на стадии 7; решает `run_v3.заявки_разрешены()`.

    `full_log` — писать ли в журнал каждый объект (MOS-147). По умолчанию его
    решает срез: названный явно `--as-of` означает обратный расчёт для замера
    метрик, и такой прогон обязан писать всё, потому что метрики М-18 и М-19
    считаются по журналу. Прогон по расписанию среза не называет и пишет
    изменения плюс пульс.
    """
    часы = Секундомер()
    if not await conn.fetchval("SELECT pg_try_advisory_lock($1)", БЛОКИРОВКА):
        print("занято: расчёт уже идёт в другом процессе")
        return {"status": "занято"}

    if full_log is None:
        full_log = as_of is not None
    as_of = as_of or datetime.now().astimezone()
    run_id = await conn.fetchval(
        "INSERT INTO pred.run (status, as_of, full_log) VALUES ('running', $1, $2) "
        "RETURNING run_id", as_of, full_log)

    итог, ошибка, участков, посчитано = "failed", None, None, None
    try:
        # Горизонт задают правила датчика (sensor_rules.json, 24 ч), а не
        # ref.app_setting.forecast_horizon_h: балл — частота отказа именно за эти
        # 24 ч, и подписать его другим горизонтом значило бы соврать на экране.
        horizon_h = sensor_risk.rules()["horizon_h"]
        print(f"прогон {run_id}, срез {as_of:%d.%m.%Y %H:%M}, горизонт {horizon_h} ч")
        план, по_плану = None, None
        версия = run_sensors.ВЕРСИЯ
        await conn.execute("UPDATE pred.run SET model_version = $1 WHERE run_id = $2", версия, run_id)

        # --- 1. Граница данных --------------------------------------------------
        # Живого потока СМВУ у нас пока нет — приём показаний это задача Q3.7.
        # Стадия честно меряет то, что делает: узнаёт, докуда доехали данные.
        #
        # ОДНО ОПРЕДЕЛЕНИЕ КРАЯ НА ВЕСЬ ПРОДУКТ (MOS-148), запрос — в app.db.
        # До этой правки здесь стоял max(read_time) по smvu.reading с окном
        # в 90 суток, а планировщик выбирал срез по max(day) свёртки — два
        # определения одной величины в двух соседних файлах. Замер 22.09.2026
        # на стенде: оба дают 2026-06-30 23:59:59+03, но журнал считается
        # 60 964 мс (перемерено проверяющей — 64 974 мс, тот же порядок), а край
        # по свёртке — 0–1 мс на живом соединении прогона и 49,5 мс первым вызовом
        # в новом соединении. Три числа у одного запроса, и различает их методика,
        # а не удача: первый вызов платит за план и прогрев, дальше он бесплатен.
        #
        # Вместе с журналом ушла и ловушка окна. Оно отсчитывалось от as_of,
        # а не от now(): плановый прогон брал край как раз своим срезом и всегда
        # попадал внутрь, а ручной прогон с сегодняшним срезом отстоял от края
        # на 84 суток — до края окна оставалось 6 суток. С 28.09.2026 такой
        # прогон получил бы край = None и отставание = None при живых данных,
        # и ни в логе, ни в API об этом не сказалось бы ни слова. У свёртки
        # окна нет вовсе.
        with часы.стадия("ms_fetch"):
            край = await conn.fetchval(КРАЙ_ДАННЫХ)
        отставание = (as_of - край).total_seconds() / 86400 if край else None
        if край is None:
            # Пустая свёртка — это законный случай до первого refresh_channel_daily,
            # и молчать о нём нельзя: дальше расчёт пойдёт по срезу, который никто
            # не сверил с данными.
            print("   край данных неизвестен: свёртка feat.channel_daily пуста")
        else:
            # Печатаем край в поясе СРЕЗА, а не в том, в котором его отдала база.
            # asyncpg возвращает timestamptz в UTC, а as_of приходит московским —
            # и две соседние строки журнала оказывались в разных поясах: «срез
            # 01.07.2026 01:00» и «данные доехали до 30.06.2026 20:59». Кто вычтет
            # их глазами, получит 4 часа 1 минуту вместо 1 часа 1 секунды. Нашла
            # проверяющая сессия 22.09.2026 на прогоне 507. Тот же класс ошибки,
            # что в коммите d176033: величина верна, пояс печати чужой.
            print(f"   данные доехали до {край.astimezone(as_of.tzinfo):%d.%m.%Y %H:%M}"
                  f"{f', срез старше на {отставание:.0f} сут' if отставание and отставание > 1 else ''}")

        # --- 2. Суточная свёртка за двое суток -----------------------------------
        with часы.стадия("ms_aggregate"):
            свёрнуто = await conn.fetchval(
                "SELECT feat.refresh_section_daily($1::date - 1, $2::date)", as_of, as_of)
        print(f"   свёртка обновила {свёрнуто} строк")

        # --- 3–5. Правила датчика: балл, уровень и причина на участок -------------
        # Решение Славы 28.09.2026: весь прогноз продукта — по датчикам. Модели
        # и score.json прогон больше не читает; как участок получает число —
        # шапка backend/app/worker/run_sensors.py. Признаков и инференса у правил
        # нет, поэтому ms_features меряет весь расчёт правил, ms_inference — ноль.
        with часы.стадия("ms_features"):
            д = await run_sensors.собрать(conn, as_of)
        часы.мс["ms_inference"] = 0
        участки, вероятности, уровни = д["участки"], д["вероятности"], д["уровни"]
        ф, тексты = д["факторы"], д["тексты"]
        if предел:
            всего = len(участки)
            участки, вероятности, ф = run_v3.урезать(участки, вероятности, ф, предел)
            оставить = set(участки)
            уровни = [у for sid, у in zip(д["участки"], д["уровни"]) if sid in оставить]
            тексты = [т for sid, т in zip(д["участки"], д["тексты"]) if sid in оставить]
            print(f"   --limit {предел}: берём {len(участки)} участков из {всего} "
                  f"по возрастанию section_id")
        участков = посчитано = len(участки)
        with часы.стадия("ms_explain"):
            высокие = {sid: {"датчик": д["главные"][sid]["name"],
                             "причина": (run_sensors.главная_причина(
                                 д["главные"][sid]["reasons"]) or {"text": "—"})["text"]}
                       for sid, у in zip(участки, уровни) if у == 0}
        if заявки:
            # План — ДО записи прогноза: его участки publish.записать положит
            # в журнал этого прогона, даже если мёртвая зона их бы пропустила (MOS-180).
            по_плану = await order_rules.план_по_датчикам(conn, высокие, as_of)
            план = по_плану["участки"]
        print(f"   {версия}: датчиков {д['датчиков']}, участков {участков}, "
              f"high {sum(1 for у in уровни if у == 0)}, watch {sum(1 for у in уровни if у == 1)}; "
              f"вероятность участка: максимум {max(вероятности, default=0):.4f}")

        # --- 6. Запись, свёртка, NOTIFY --------------------------------------------
        with часы.стадия("ms_write"):
            записано = await publish.записать(conn, run_id, as_of, horizon_h,
                                              НАПРАВЛЕНИЕ, участки, вероятности, ф, тексты,
                                              full_log=full_log,
                                              обязательно=frozenset(план or ()),
                                              уровни=уровни)
        разбор = ", ".join(f"{имя} {n}" for имя, n in sorted(записано["причины"].items()))
        print(f"   в журнал {записано['журнал']} строк из {записано['участков']} "
              f"посчитанных ({разбор or 'ничего не менялось'}), "
              f"разослан NOTIFY {publish.КАНАЛ}")

        # --- 7. Автозаявки ---------------------------------------------------------
        # Стадия идёт ПОСЛЕ записи прогноза, а не вместе с ней: заявка ссылается
        # на forecast_id, и пока прогноз не лёг в базу, ссылаться не на что.
        # Своей колонки ms_* у стадии нет — в pred.run их ровно шесть, и заводить
        # седьмую ради заявок значит менять схему прогонов из блока Q6.
        if заявки:
            счёт = await order_rules.завести(conn, run_id, НАПРАВЛЕНИЕ, план)
            print(f"   участков high в плане заявок {len(план)}, "
                  f"с живой заявкой {по_плану['живых']}")
            print(f"   заявки ({счёт['правило']}): отобрано {счёт['отобрано']} участков "
                  f"из {счёт['участков']}, заведено {счёт['заявок']}, "
                  f"отброшено повторами {счёт['повторов']}, "
                  f"без строки журнала {счёт['без_строки_журнала']}")
        else:
            print("   заявки НЕ заводятся: прогон с --limit без --rollback")
        итог = "done"

    except Exception as e:
        итог, ошибка = "failed", f"{type(e).__name__}: {e}"
        print(f"   ОШИБКА: {ошибка}")
        await publish.пометить_устаревшим(conn, ошибка)
    finally:
        await conn.execute(
            """UPDATE pred.run SET finished_at = now(), status = $2, error_text = $3,
                   objects_total = $4, objects_scored = $5,
                   ms_fetch = $6, ms_aggregate = $7, ms_features = $8,
                   ms_inference = $9, ms_explain = $10, ms_write = $11
                 WHERE run_id = $1""",
            run_id, итог, ошибка, участков, посчитано,
            *[часы.мс.get(к) for к in ("ms_fetch", "ms_aggregate", "ms_features",
                                       "ms_inference", "ms_explain", "ms_write")])
        await conn.fetchval("SELECT pg_advisory_unlock($1)", БЛОКИРОВКА)

    строка = dict(await conn.fetchrow("SELECT * FROM pred.run WHERE run_id = $1", run_id))
    всего_мс = sum(v for k, v in строка.items() if k.startswith("ms_") and v)
    print(f"прогон {run_id}: {итог}, стадии {sum(1 for k, v in строка.items() if k.startswith('ms_') and v is not None)}/6, "
          f"суммарно {всего_мс / 1000:.1f} с")
    return строка


async def закрыть_оборванные(conn) -> dict:
    """Разобрать прогоны, оборванные перезапуском worker. Возвращает счётчик.

    Зачем (MOS-183, строка НФ-41). Контейнер `worker` гоняет расчёт через
    `прогон()`, а та закрывает свою строку `pred.run` в `finally`. Перезапуск
    или `kill` посреди расчёта убивает процесс вместе с `finally` — строка
    остаётся со `status='running'` навсегда. Вред двойной: журнал прогонов
    показывает «идёт расчёт», которого нет, и всё, что ищет «последний прогон»
    или считает разрывы по `pred.run`, видит фантомный живой прогон. Так остался
    прогон 669 от 22.09.2026: его оборвала выкладка MOS-180 в 20:36.

    **Почему безопасно закрывать ВСЕ running разом, а не одну строку.**
    `прогон()` держит `БЛОКИРОВКА` (48217) от вызова `pg_try_advisory_lock` до
    `pg_advisory_unlock` в том же `finally` — то есть весь промежуток между
    `INSERT INTO pred.run (status='running')` и записью финального статуса.
    Значит, пока держим блокировку мы, ни одного живого `прогона` не существует,
    и любая строка `status='running'` — это сирота от мёртвого процесса. Живой
    расчёт этот шаг задеть не может: блокировку мы бы просто не взяли.

    Поэтому же нельзя звать эту функцию из `прогон()` «для надёжности»: там она
    оказалась бы внутри транзакции `--rollback` и её UPDATE откатился бы вместе с
    прогоном, оставив сироту на месте. Точка вызова — старт службы worker
    (`scheduler._serve()`), отдельным соединением и вне транзакции.

    Возвращает `{"закрыто": n}` (n может быть нулем — база чистая) либо
    `{"занято": True}`, если блокировку держит настоящий расчёт: тогда ничего не
    трогаем и не снимаем чужую блокировку.
    """
    if not await conn.fetchval("SELECT pg_try_advisory_lock($1)", БЛОКИРОВКА):
        return {"занято": True}
    try:
        закрыто = await conn.fetchval(
            """WITH закрытые AS (
                   UPDATE pred.run
                      SET status = 'failed', finished_at = now(), error_text = $1
                    WHERE status = 'running'
                   RETURNING run_id
               )
               SELECT count(*) FROM закрытые""",
            ПРИЧИНА_ОБРЫВА)
        return {"закрыто": закрыто}
    finally:
        await conn.fetchval("SELECT pg_advisory_unlock($1)", БЛОКИРОВКА)


# Заявки прогона с откатом — строкой JSON в stdout, до отката (просьба 78, замер метрик
# по потоку заявок): откат снимает побочные эффекты, но вместе с ними и сами заявки.
# LEFT JOIN на коллектор: участок без коллектора попадает в выгрузку с null, а не пропадает.
ЗАЯВКИ_ПРОГОНА = """
SELECT n.id, so.object_id, f.section_id, n.source_key, n.reported_at, n.due_at,
       n.warning_opened_at
  FROM maint.notification n
  JOIN pred.forecast f ON f.forecast_id = n.forecast_id
  LEFT JOIN pred.section_object so ON so.section_id = f.section_id
 WHERE n.source_system = 'forecast' AND f.run_id = $1
 ORDER BY n.id
"""
ПРИСТАВКА_ЗАЯВКИ = "ЗАЯВКА_JSON"


async def выгрузить_заявки(conn, run_id: int) -> int:
    """Напечатать заявки прогона по одной строке `ЗАЯВКА_JSON {...}`. -> сколько."""
    строки = await conn.fetch(ЗАЯВКИ_ПРОГОНА, run_id)
    for r in строки:
        print(ПРИСТАВКА_ЗАЯВКИ, json.dumps(
            {"id": r["id"], "object_id": r["object_id"], "section_id": r["section_id"],
             "source_key": r["source_key"],
             "reported_at": r["reported_at"].astimezone(МОСКВА).isoformat(),
             "due_at": r["due_at"].astimezone(МОСКВА).isoformat(),
             "warning_opened_at": r["warning_opened_at"] and
                                  r["warning_opened_at"].astimezone(МОСКВА).isoformat()},
            ensure_ascii=False))
    return len(строки)


async def main():
    р = argparse.ArgumentParser(description="Прогон расчёта прогноза")
    р.add_argument("--as-of", help="момент среза, например 2026-06-30T23:59:59+03:00")
    р.add_argument("--limit", type=int,
                   help="считать только первые N участков по section_id; "
                        "без --rollback заявки не заводятся, а текущий прогноз "
                        "заменяется этими N участками")
    р.add_argument("--rollback", action="store_true", help="откатить всё, что записали")
    р.add_argument("--full-log", action="store_true",
                   help="писать в журнал каждый объект; без флага это решает --as-of")
    р.add_argument("--selfcheck", action="store_true", help="проверка без базы")
    а = р.parse_args()
    if а.selfcheck:
        run_sensors._selfcheck()
        return 0

    conn = await asyncpg.connect(os.environ["DATABASE_URL"], command_timeout=900)
    try:
        as_of = datetime.fromisoformat(а.as_of) if а.as_of else None
        заявки = run_v3.заявки_разрешены(а.limit, а.rollback)
        if а.rollback:
            tr = conn.transaction()
            await tr.start()
            try:
                строка = await прогон(conn, as_of, а.limit,
                                      True if а.full_log else None, заявки)
                if строка.get("run_id"):
                    print(f"   заявок прогона выгружено строкой {ПРИСТАВКА_ЗАЯВКИ}: "
                          f"{await выгрузить_заявки(conn, строка['run_id'])}")
            finally:
                await tr.rollback()
                print("откат сделан: в базе следов прогона нет")
        else:
            строка = await прогон(conn, as_of, а.limit,
                                  True if а.full_log else None, заявки)
        return 0 if строка.get("status") in ("done", "занято") else 1
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
