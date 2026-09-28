# Архитектура сервиса прогнозирования аварий

Документ входит в сопроводительную документацию поставки и закрывает раздел
«функциональная и компонентная архитектура» из перечня ТЗ разд. 14 (строка приёмки
НФ-83) и строку НФ-86: сервис разворачивается отдельно и работает без систем, которых
у заказчика нет. Три остальных раздела перечня лежат рядом: методы обработки данных,
условия и ограничения — в `docs/data-processing.md`, сборка — в `docs/build.md`,
установка — в `docs/install.md`.

Здесь описано, что стоит в поставке и как части связаны между собой. Почему выбрано
именно так, с замерами и отвергнутыми вариантами, разбирает рабочий документ команды
`docs/HLD.md`, ссылки на его разделы стоят по тексту. Где HLD и код расходятся, этот
документ пишет так, как сделано в коде: `deploy/docker-compose.yml`,
`backend/app/`, `db/migrations/`.

## 1. Что система делает

Сервис считает для каждого участка коллектора вероятность потери связи с его датчиками
за горизонт прогноза, объясняет число по-русски, заводит по предупреждению заявку
на работы и показывает всё это диспетчеру в браузере. Участок — пара «коллектор, пикет»,
пикет 10 м. В выгрузке заказчика 3 173 участка на 16 коллекторах и 11 485 каналов
датчиков.

Функции системы, экран, на котором их видит человек, и код, который их выполняет:

| Функция | Экран | Метод API | Код | Где лежит результат |
|---|---|---|---|---|
| приём показаний СМВУ | «Источники данных» `/admin/sources` | `POST /api/ingest/readings` | `backend/app/ingest/readings.py`, файловая заливка `backend/app/ingest/smvu_csv.py` | `smvu.reading`, `load.batch` |
| расчёт прогноза | «Дашборд рисков» `/dashboard` | `GET /api/risks`, `GET /api/data-status` | `backend/app/worker/run.py` | `pred.run`, `pred.forecast`, `pred.forecast_current` |
| объяснение прогноза | карточка участка `/objects/:id`, карточка прогноза `/forecasts/:id` | `GET /api/objects/{id}`, `GET /api/forecasts/{id}` | `backend/app/domain/explain.py` | `pred.forecast.explanation_ru` |
| схема коллектора | «Карта объектов» `/map` | `GET /api/objects/tree`, `GET /api/geo/sections` | `frontend/src/screens/map/` | `smvu.object_tree`, `geo.geo_object` |
| журнал прогнозов и решение диспетчера | «Журнал прогнозов» `/log` | `GET /api/forecasts`, `POST /api/forecasts/{id}/feedback`, `POST /api/forecasts/{id}/outcome` | `backend/app/api/routes.py` | `pred.feedback`, `pred.forecast_outcome` |
| автозаявки | «Заявки» `/orders`, карточка `/orders/:id` | `GET /api/orders`, `GET /api/orders/{id}` | `backend/app/domain/order_rules.py`, стадия 7 в `run.py` | `maint.notification`, `maint.work_order` |
| уведомления диспетчеру | полоса тревог на всех экранах | `GET /api/alerts/stream` (SSE), `POST /api/notifications/{id}/ack` | `backend/app/api/notifications.py` | `maint.notification.acked_at` |
| журнал технологических событий | «Журнал событий» `/tech-events` | `GET /api/tech-events` | `backend/app/api/tech_events.py` | `smvu.reading`, `maint.ods_event` |
| настройки, пользователи, аудит | `/admin/settings`, `/admin/users`, `/admin/audit`, `/admin/directory` | `GET/PUT /api/settings`, `GET/PATCH /api/auth/users`, `GET /api/audit` | `backend/app/api/settings.py`, `auth.py`, `audit.py` | `ref.app_setting`, `ref.app_user`, `audit.user_action` |

Как заявка заводится по прогнозу и как считается её срок, описывает
`docs/order-rules.md`.

## 2. Компоненты

Система поставляется одним файлом `deploy/docker-compose.yml` и встаёт на один хост.
Служб в нём девять. Три поднимаются командой `docker compose up -d` без профиля,
пять — с профилем `app`, каталог LDAP — только с профилем `ldap`.

| Служба | Образ | Что делает | Порт | Профиль |
|---|---|---|---|---|
| `nginx` | `nginx:1.31-alpine` | раздаёт собранный фронт из `deploy/nginx/app`, проксирует `/api/*`, `/docs`, `/openapi.json` и `/health` на `api`, держит TLS 1.2 и 1.3 и HTTP/2 | 80 (только редирект на https), 443 | — |
| `db` | `postgis/postgis:18-3.6` | PostgreSQL 18.6 с PostGIS 3.6, одна база на одиннадцать схем; архив журнала WAL в том `backups` не реже раза в 15 минут | 5432, только на `127.0.0.1` хоста | — |
| `backup` | тот же, что у `db` | `deploy/backup.sh`: раз в сутки копия кластера `pg_basebackup` в том `backups` | — | — |
| `api` | собираем из `backend/Dockerfile` на `python:3.14-slim` | FastAPI и uvicorn: REST, поток SSE, два эмулятора внешних систем | 8000 внутри сети compose | `app` |
| `worker` | тот же образ, другая команда | планировщик APScheduler: расчёт прогноза, свёртка, погода, статусы заявок | — | `app` |
| `migrate` | тот же образ | накат `db/migrations/*.sql` и `db/seed/*.sql`, живёт секунды и выходит | — | `app` |
| `emulator-smvu` | тот же образ | раз в минуту шлёт в `POST /api/ingest/readings` показания архива со сдвигом на 364 дня | — | `app` |
| `ml` | задаётся `ML_IMAGE`; в поставке — заглушка `ml-stub/` | модель: `GET /model`, `POST /predict`; с 28.09.2026 worker её не зовёт — прогноз считают правила датчика | 8100 внутри сети compose | `app` |
| `ldap` | собираем из `deploy/ldap/Dockerfile` | демонстрационный каталог LDAP с четырьмя учётками | 389 внутри сети compose | `ldap` |

Ещё одна часть живёт вне compose: скрипт `deploy/ml-score.sh`. Его раз в час
запускает crontab хоста; он поднимает образ `ml-score` модели v3 и кладёт файл
`score.json` в том `score`. Worker читает этот том только на чтение, и с 28.09.2026
берёт из файла одно поле `as_of` — срез при проигрывании архива; вероятности модели
прогон не читает, прогноз считают правила датчика (`backend/app/worker/run_sensors.py`). Скрипт запускается
снаружи, потому что иначе worker должен был бы получить сокет Docker, а это права root
на всю машину (разбор — в шапке самого скрипта).

Именованных томов пять: `pgdata` (база), `backups` (копии и архив WAL), `score`
(выдача модели v3), `parquet` и `uploads`. Два последних смонтированы в `worker`,
но код их сейчас не использует.

Одна и та же сборка `backend/Dockerfile` даёт образ для `api`, `worker`, `migrate`
и `emulator-smvu`: код у них общий, различается только команда запуска. Зачем так —
`docs/HLD.md` разд. 7.1.

## 3. Схема потоков

```
  браузер АРМ диспетчера
        │  HTTPS 443, HTTP/2
        ▼
  ┌──────────┐  /api/*, /docs, /health   ┌─────────┐  /emu/open-meteo/v1/archive
  │  nginx   │──────────────────────────▶│   api   │◀──────────────┐  /emu/helpdesk/v1/tickets
  └──────────┘      HTTP 8000            └────┬────┘               │
        ▲ статика из deploy/nginx/app         │ asyncpg            │ HTTP
                                              ▼                    │
  ┌───────────────┐ POST /api/ingest/  ┌──────────────┐     ┌──────┴─────┐  GET /model
  │ emulator-smvu │───readings────────▶│  db :5432    │◀────│   worker   │─ POST /predict ─▶ ml :8100
  └───────────────┘   (через api)      │ PostgreSQL 18│     └──────┬─────┘
                                       └──────────────┘            │ читает /score/score.json
                                                                   │
  crontab хоста ── раз в час ── deploy/ml-score.sh ── образ ml-score ── пишет ─▶ том score
```

| № | Отправитель | Получатель | Что передаёт | Протокол, порт | Наружу |
|---|---|---|---|---|---|
| 1 | браузер | `nginx` | интерфейс, REST, SSE | HTTPS, 443 | да |
| 2 | `nginx` | `api` | запросы `/api/*` без обрезки префикса | HTTP, 8000 | нет |
| 3 | `api`, `worker`, `migrate` | `db` | запросы SQL | PostgreSQL, 5432 | нет |
| 4 | `worker` | `ml` | `GET /model` перед каждым расчётом, `POST /predict`; с 28.09.2026 не используется | HTTP, 8100 | нет |
| 5 | `ml-score` | `worker` | `score.json` через том `score`; с 28.09.2026 worker берёт из него только срез `as_of` при проигрывании | файл | нет |
| 6 | `worker` | `api` | погода и статусы заявок из эмуляторов | HTTP, 8000 | нет |
| 7 | `emulator-smvu` | `api` | пачки показаний с токеном `INGEST_TOKEN` | HTTP, 8000 | нет |
| 8 | `api` | `ldap` | проверка пароля simple bind | LDAP, 389 | нет |

Наружу открыт один порт с содержимым, 443. Порт 80 отвечает только редиректом
`301` на https, порт базы опубликован на петлю хоста. Замер портов на стенде —
`docs/install.md` разд. 2.

## 4. Стек

| Слой | Что стоит | Версия | Почему так |
|---|---|---|---|
| СУБД | PostgreSQL + PostGIS | 18.6 + 3.6 | `docs/HLD.md` разд. 5, 7.1.1 |
| бэкенд | Python, FastAPI, uvicorn, asyncpg, APScheduler 3.x | 3.14, 0.141.1, 0.52.4, 0.31.0, 3.11.3 | `docs/HLD.md` разд. 3.1, 7.1.2 |
| фронтенд | Vite, Preact, TypeScript, Tailwind | — | `docs/HLD.md` разд. 4.1: бюджет 150 КБ сжатого JS на всё |
| веб-сервер | nginx | 1.31 | `docs/HLD.md` разд. 7.1 |
| прогноз | правила датчика `backend/app/domain/sensor_rules.json` в `worker` (с 28.09.2026); до этого — LightGBM в образе ML-команды | `sensor-rules-24h`; прежде `lgbm-v3-bag-2026.09.21b` | `docs/HLD.md` разд. 3.4, 6 |
| поставка | Docker Compose, один хост | — | `docs/HLD.md` разд. 7 |

Все зависимости Python закреплены версиями в `backend/requirements.txt`, их перечень
с лицензиями — `docs/libraries.md`. Библиотек под GPL, LGPL и AGPL в поставке нет,
правило и проверка — `docs/HLD.md` разд. 7.3.

Наш стек совпадает со стеком действующей СМВУ заказчика в пяти позициях из двенадцати:
PostgreSQL, Python, nginx, Debian, JavaScript (`docs/HLD.md` разд. 1.2).

**Минимальная версия PostgreSQL — 15.** Поставка поднимает свою базу 18.6, и требование
ТЗ «PostgreSQL 12 и выше» этим выполнено. Но если разворачивать схему на своей базе,
она должна быть не старше 15-й ветки: в `db/migrations/003_permits.sql` стоит
`UNIQUE NULLS NOT DISTINCT`, а эта конструкция появилась в PostgreSQL 15. На 12, 13 и 14
накат остановится на третьей миграции (строка приёмки НФ-80, `docs/install.md` разд. 0).

## 5. REST API

Под префиксом `/api` объявлено 41 метод, у каждого описание в `GET /docs`
и в `GET /openapi.json` — их FastAPI генерирует сам из типов ответа. Живость отдаёт
`GET /health` без входа и без записи в журнал аудита. Полная таблица методов
с правами и полями ответа — `docs/HLD.md` разд. 3.4.

| Группа | Методы |
|---|---|
| вход | `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me`, `GET /api/auth/info`, `GET /api/auth/directory`, `POST /api/auth/directory/check`, `GET /api/auth/users`, `PATCH /api/auth/users/{login}` |
| риск и прогнозы | `GET /api/risks`, `GET /api/data-status`, `GET /api/forecasts`, `GET /api/forecasts/{id}`, `GET /api/dispatcher-decisions`, `POST /api/forecasts/{id}/feedback`, `POST /api/forecasts/{id}/outcome`, `GET /api/forecast-outcomes` |
| объекты | `GET /api/objects/tree`, `GET /api/objects/{id}`, `GET /api/objects/{id}/readings`, `GET /api/objects/{id}/channels`, `GET /api/objects/{id}/channels/{channel_id}/episodes`, `GET /api/geo/sections`, `GET /api/sensor-risk` (балл по каждому датчику, MOS-249, MOS-253), `GET /api/sensor-risk/summary` (плитки дашборда по датчикам, MOS-253) |
| заявки и уведомления | `GET /api/orders`, `GET /api/orders/{id}`, `GET /api/notifications`, `POST /api/notifications/{id}/ack`, `GET /api/alerts/stream` |
| приём данных | `POST /api/ingest/readings`, `POST /api/ingest/ods-events`, `POST /api/permits`, `POST /api/permits/{id}/close` |
| источники и погода | `GET /api/sources`, `GET /api/weather`, `GET /api/weather/now` |
| журналы и настройки | `GET /api/tech-events`, `GET /api/tech-events/ods-last`, `GET /api/audit`, `GET /api/settings`, `PUT /api/settings/{key}` |

Методы отвечают JSON, а при заголовке `Accept: application/xml` — XML
(`backend/app/api/xml.py`). Геометрию `GET /api/geo/sections` отдаёт в GeoJSON
или в WKT параметром `?geometry=`.

Два метода эмулируют внешние системы и живут без префикса `/api`:
`GET /emu/open-meteo/v1/archive` и `GET /emu/helpdesk/v1/tickets`. nginx их наружу
не отдаёт, к ним ходит только `worker` внутри сети compose.

## 6. Интерфейс

Фронт — одностраничное приложение, собранное в статику; на сервере Node нет.
Пункты главного меню перечислены в `frontend/src/routes.ts`: «Дашборд рисков»,
«Карта объектов», «Журнал прогнозов», «Заявки», «Журнал событий». Экраны
администратора открываются по путям `/admin/*`.

**«Карта объектов» — схема коллектора на SVG, а не карта Москвы.** Координат в
обезличенной выгрузке нет, и заказчик письменно 19.09.2026 назвал такую схему
допустимой и даже рекомендуемой: «например, линейная схема с пикетами». Экран рисует
ось пикетов коллектора с масштабом («+ приблизить», «вся ось») и дерево
из 16 коллекторов слева (`frontend/src/screens/map/AxisLine.tsx`,
`ObjectTree.tsx`). Геометрия для GeoJSON и WKT сгенерирована нами: осевые
`MultiLineString` коллекторов и `LineString` участков в `geo.geo_object`,
система координат `EPSG:4326`, в ответе стоит `"geometry_source": "synthetic"`
(`backend/app/ingest/synthetic_geometry.py`).

**Данные на экране обновляются без перезагрузки.** Экраны перезапрашивают API
раз в 60 секунд (`frontend/src/lib/poll.ts`, строка приёмки НФ-89). Полоса тревог
держит поток SSE `GET /api/alerts/stream`: сервер смотрит новые уведомления в базе
раз в 5 секунд и шлёт пинг после 20 секунд тишины.

## 7. Вход, роли, аудит

Пользователь входит логином и паролем через `POST /api/auth/login` и получает куку
`mk_session`, подписанную ключом `AUTH_SECRET`. Пароль проверяется по локальной
учётной записи (хеш argon2 в `ref.app_user`) или в каталоге LDAP, если задан
`LDAP_URI`. Ролей четыре: `dispatcher` (свой район), `ods_dispatcher` (весь парк),
`technician` (свой комплекс), `admin` (настройки и журнал). Роли складываются,
область видимости задаёт `ref.user_scope` (миграция `044_roles_scope.sql`).
Каждый запрос, кроме `/health`, оставляет строку в `audit.user_action`.
Подробности — `docs/HLD.md` разд. 3.5.

## 8. Работа без систем заказчика (НФ-86)

Заказчик написал 19.09.2026: «Никакие реальные интеграции не предоставляются»
и «Все внешние взаимодействия необходимо реализовать через шаблоны и эмуляцию событий».
Поэтому каждая внешняя система в поставке либо заменена эмулятором, либо выключается
пустой переменной, и сервис поднимается на изолированной машине без доступа наружу.

| Внешняя система | Чем заменена в поставке | Как подключить настоящую |
|---|---|---|
| поток СМВУ | служба `emulator-smvu`: проигрывает собственный архив базы со сдвигом +364 дня | слать показания в `POST /api/ingest/readings` с токеном `INGEST_TOKEN` |
| архив погоды Open-Meteo | эмулятор `GET /emu/open-meteo/v1/archive` на `api`, отдаёт погоду из файла `backend/app/api/weather_moscow.csv.gz` | `WEATHER_URL=https://archive-api.open-meteo.com/v1/archive` |
| хелпдеск заявок заказчика | эмулятор `GET /emu/helpdesk/v1/tickets` на `api`: статус заявки — функция её номера и возраста, без случайных чисел | `ORDER_SYSTEM_URL`; обратно во внешнюю систему сервис не пишет ничего |
| журнал ОДС | метод `POST /api/ingest/ods-events`, его вызывает тест или оператор | тот же метод |
| реестр нарядов-допусков | методы `POST /api/permits` и `POST /api/permits/{id}/close` | те же методы |
| служба каталогов AD | пустой `LDAP_URI` — вход только по локальным учётным записям; для показа есть свой каталог, служба `ldap` | `LDAP_URI`, `LDAP_BASE_DN`, `LDAP_USER_TEMPLATE` |
| модель ML-команды | заглушка `ml-stub/`, отвечает по контракту `contracts/predict.v1.schema.json` | `ML_IMAGE` в `deploy/.env` |

Какой источник отстал и насколько, показывает экран «Источники данных»
(`GET /api/sources`). Если остановить эмулятор командой `docker compose stop
emulator-smvu`, остальная система продолжит работать, а экран назовёт отставший источник.

**С 28.09.2026 прогноз на стенде и в поставке считают правила датчика**
(`backend/app/worker/run_sensors.py`, `backend/app/domain/sensor_rules.json`), без образов
`ml` и `ml-score` и без весов модели: worker на каждом прогоне считает балл каждого
датчика и отдаёт участку балл самого рискованного. Абзац ниже описывает, как было
до 28.09.2026.

**Оговорка про модель v3 (до 28.09.2026).** Прогноз на стенде считала модель v3 ML-команды:
её образы `ml` и `ml-score` собираются из `ml-model/Dockerfile` и
`ml-model/Dockerfile.score`, а веса модели и подготовленные файлы журнала в репозиторий
не входят (`ml-model/README.md`). Без них поставка работает на заглушке: переменная
`SCORE_V3_PATH` пуста, worker сам собирает 22 признака по контракту
`contracts/features.v1.yaml` и шлёт их в `ml-stub`. Экраны, заявки и API при этом
работают, но вероятности — ответ заглушки, а не модели. Чем отличаются два пути
расчёта — `docs/data-processing.md` разд. 3.

## 9. Развёртывание

| Шаг | Где описан |
|---|---|
| подготовить машину, сеть, Docker | `docs/install.md` разд. 1–3 |
| заполнить `deploy/.env` из `deploy/.env.example`, выпустить сертификат | `docs/install.md` разд. 4, `deploy/README.md` |
| собрать образы и фронт | `docs/build.md` |
| накатить схему, залить выгрузку, поднять `api` и `worker` | `deploy/README.md`, `docs/install.md` разд. 5 |
| включить проигрывание архива по crontab (срез из `score.json`) | `docs/server.md`, раздел «Проигрывание архива» |
| резервные копии и восстановление | `docs/install.md` разд. 6, `docs/restore.md` |

Базовая команда подъёма всей системы:

```
cd deploy && docker compose --profile app up -d
```

Требование к серверу, по которому считались бюджеты: 6 ядер, 12 ГБ памяти, 120 ГБ
диска при условии, что вся история не хранится в одной базе (`docs/HLD.md` разд. 9.5).
Стенд, на котором проверена поставка, — 6 ядер, 11 ГБ ОЗУ, 119 ГиБ диска; база на нём
с полной историей весит около 55 ГБ.

## 10. Где обоснования

| Вопрос | Раздел `docs/HLD.md` |
|---|---|
| почему FastAPI, а не Go или NestJS | 3.1 |
| планировщик и блокировка `pg_try_advisory_lock(48217)` против двойного расчёта | 3.3 |
| почему Preact, а не Next.js; бюджет фронта | 4.1 |
| почему одна база PostgreSQL, а не TimescaleDB | 5, введение |
| граница с ML-командой и контракты | 6 |
| состав контейнеров, версии образов, лицензии | 7.1, 7.1.1, 7.3 |
| требования к серверу и точки слома | 9.5 |
