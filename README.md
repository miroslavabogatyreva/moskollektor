# Сервис прогнозирования аварий инженерных коллекторов Москвы

**Работающий сервис: https://moskollektor.mbogatyreva.ru** — демо-учётки для входа
показаны прямо на экране входа. REST API и Swagger:
https://moskollektor.mbogatyreva.ru/docs

ЛЦТ-2026, задача 8, АО «Москоллектор», команда «Скайнет». Сервис прогнозирует отказы
датчиков в инженерных коллекторах на 24 часа вперёд, показывает диспетчеру ОДС риски
на схеме коллектора, ведёт журнал прогнозов и сам формирует заявки на превентивное
обслуживание. Всё это доступно и через REST API.

## Документы

| Что | Файл |
|---|---|
| пояснительная записка: что предсказываем, какие метрики и почему такие | `docs/explanatory-note.md` |
| архитектура и стек | `docs/HLD.md`, `docs/architecture.md`, `diagrams/` |
| как развернуть с нуля | `docs/install.md`, `deploy/README.md` |
| как собрать образы и фронт | `docs/build.md` |
| приёмка: требования постановки и ТЗ и чем каждое закрыто | `docs/acceptance-test.md` |
| замер метрик и обучение модели | `docs/metrics-report.md`, `docs/ml-training-report.md` |
| что из ТЗ не выполнено и почему | `docs/unmet-requirements.md` |
| обработка данных заказчика | `docs/data-processing.md` |
| правила формирования заявок | `docs/order-rules.md` |
| сценарии пользователей | `docs/user-stories.md` |
| протоколы: TLS, нагрузка, восстановление | `docs/protocol-*.md`, `docs/load-test.md`, `docs/restore.md` |
| библиотеки и лицензии | `docs/libraries.md` |
| постановка и ТЗ заказчика | `docs/task.md`, `docs/tz-djkh.pdf` |

## Локальный запуск

Нужны Docker с Compose и Node.js для сборки интерфейса. Коротко, из корня репозитория:

```sh
cd deploy
cp .env.example .env    # вписать POSTGRES_PASSWORD (openssl rand -hex 16)
                        # и AUTH_SECRET (openssl rand -hex 32)
sh make-cert.sh         # самоподписанный сертификат для nginx
docker compose up -d    # база и nginx
docker build -t moskollektor/ml-stub:latest -f ../ml-stub/Dockerfile ..
docker compose --profile app up -d   # миграции, API, расчёт, заглушка модели
cd ../frontend && npm ci && npm run build && mkdir -p ../deploy/nginx/app && cp -R dist/. ../deploy/nginx/app/
```

Каждый шаг с проверкой «что должно получиться» расписан в `deploy/README.md`,
там же заливка выгрузки заказчика. Полная инструкция для чистой машины —
`docs/install.md`. Выгрузку заказчика (13 ГБ) в репозиторий мы не кладём.

## Что где лежит

| Каталог | Что внутри |
|---|---|
| `frontend/` | интерфейс диспетчера: Vite + Preact + TypeScript + Tailwind, E2E-тесты в `frontend/e2e/` |
| `backend/` | REST API на FastAPI, расчёт прогноза и заявок по расписанию, заливка выгрузки |
| `db/` | миграции PostgreSQL 18 + PostGIS и начальные данные |
| `ml-model/` | модель: признаки, обучение, инференс, метрики |
| `ml-stub/` | заглушка модели для запуска без обученных весов |
| `contracts/` | форматы обмена между бэкендом и моделью |
| `deploy/` | docker compose, nginx, резервные копии, скрипты выкладки |
| `delivery/` | сборка пакета сдачи и сквозные проверки |
| `code/` | проверки схемы, метрик и выкладки |
| `diagrams/` | схемы архитектуры и базы |

Команда: Мирослава Богатырева — бэкенд, фронтенд, база, заявки, API, развёртывание.
Николай Тлехугов — модель.
