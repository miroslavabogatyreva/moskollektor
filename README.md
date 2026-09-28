<div align="center">

# Москоллектор · прогноз отказов датчиков

**Сервис прогнозирования аварий инженерных коллекторов Москвы**<br>
ЛЦТ-2026 · задача 8 · АО «Москоллектор» · команда «Скайнет»

<br>

[![Открыть сервис](https://img.shields.io/badge/▶_Открыть_сервис-moskollektor.mbogatyreva.ru-0B4EA2?style=for-the-badge)](https://moskollektor.mbogatyreva.ru)
&nbsp;
[![REST API](https://img.shields.io/badge/REST_API-Swagger-85EA2D?style=for-the-badge&logo=swagger&logoColor=black)](https://moskollektor.mbogatyreva.ru/docs)

<sub>Демо-учётки показаны прямо на экране входа — нажмите «Войти как».</sub>

<br>

![Python](https://img.shields.io/badge/Python-FastAPI-009688?logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL_18-PostGIS_3.6-336791?logo=postgresql&logoColor=white)
![Preact](https://img.shields.io/badge/Preact-TypeScript-673AB8?logo=preact&logoColor=white)
![Tailwind](https://img.shields.io/badge/Tailwind_CSS-4-06B6D4?logo=tailwindcss&logoColor=white)
![Docker](https://img.shields.io/badge/Docker_Compose-2496ED?logo=docker&logoColor=white)
![ML](https://img.shields.io/badge/ML-scikit--learn_·_LightGBM-F7931E?logo=scikitlearn&logoColor=white)

</div>

---

## Что делает сервис

Москоллектор эксплуатирует 825 км коллекторов с датчиками охранной и пожарной сигнализации
и диспетчерского управления; в выгрузке заказчика их 11 485. Сервис оценивает, какой датчик
откажет в ближайшие **24 часа**, и помогает диспетчеру ОДС успеть раньше отказа.

| | |
|---|---|
| 🗺️ **Схема коллектора** | линия с пикетами вместо карты: где на трассе стоит датчик с риском, масштаб до одного пикета |
| 📊 **Дашборд рисков** | датчики и участки по уровню риска и главной причине |
| 📓 **Журнал прогнозов** | каждый прогноз с моментом расчёта, вероятностью и решением диспетчера |
| 🛠️ **Заявки** | превентивные заявки создаются сами по правилам, со сроком и приоритетом |
| 🔔 **Уведомления** | высокий риск приходит сразу, без перезагрузки страницы |
| 🔌 **REST API** | всё, что есть на экранах, доступно внешним системам; описание — Swagger |

<table>
<tr>
<td width="50%"><img src=".github/readme/map.png" alt="Схема коллектора по пикетам"></td>
<td width="50%"><img src=".github/readme/dashboard.png" alt="Дашборд рисков"></td>
</tr>
<tr>
<td align="center"><sub>Схема коллектора по пикетам</sub></td>
<td align="center"><sub>Дашборд рисков по датчикам</sub></td>
</tr>
</table>

## Как считается прогноз

Отказ — эпизод «Неисправен» журнала СМВУ длиннее часа, то есть потеря связи с датчиком.
Прогноз считают два правила: **давность** (канал недавно отказывал) и **предвестник**
(симулированное предупреждение прибора). Пороги выбраны на январе–марте 2026,
замер — на апреле–июне 2026, срез в 21:00 каждых суток.

| Режим | Precision | Recall |
|---|:---:|:---:|
| на реальных отказах (606 отказов) | **0,120** | **0,097** |
| с симуляцией паспортов и предвестников | 0,334 | 0,405 |

Precision 0,120 в 252 раза выше случайного выбора. Порогов 0,7 и 0,5 реальные данные
не дают: отказ канала редок, а предвестника отказа в журнале нет. Как повторить замер —
[`ml-model/sensor/`](ml-model/sensor/README.md).

## Запуск у себя

Нужны Docker с Compose и Node.js.

```sh
cd deploy
cp .env.example .env    # вписать POSTGRES_PASSWORD (openssl rand -hex 16)
                        # и AUTH_SECRET (openssl rand -hex 32)
sh make-cert.sh         # самоподписанный сертификат для nginx
docker compose up -d    # база и nginx
docker build -t moskollektor/ml-stub:latest -f ../ml-stub/Dockerfile ..
docker compose --profile app up -d   # миграции, API, расчёт
cd ../frontend && npm ci && npm run build \
  && mkdir -p ../deploy/nginx/app && cp -R dist/. ../deploy/nginx/app/
```

Интерфейс откроется на https://localhost. Каждый шаг с проверкой и заливка выгрузки
заказчика расписаны в [`deploy/README.md`](deploy/README.md). Выгрузку (13 ГБ)
в репозиторий мы не кладём.

## Что где лежит

| Каталог | Что внутри |
|---|---|
| [`frontend/`](frontend) | интерфейс диспетчера: Vite + Preact + TypeScript + Tailwind, E2E-тесты на Playwright |
| [`backend/`](backend) | REST API на FastAPI, расчёт прогноза и заявок по расписанию, заливка выгрузки |
| [`db/`](db) | миграции PostgreSQL + PostGIS и начальные данные |
| [`ml-model/`](ml-model) | правила датчика и модели: признаки, обучение, метрики |
| [`contracts/`](contracts) | форматы обмена между бэкендом и моделью |
| [`deploy/`](deploy) | Docker Compose, nginx, резервные копии, выкладка |
| [`delivery/`](delivery) | сборка пакета сдачи и сквозные проверки |
| [`code/`](code) | самопроверки схемы, метрик и выкладки |

## Команда

**Мирослава Богатырева** — бэкенд, фронтенд, база, заявки, API, развёртывание<br>
**Николай Тлехугов** — модель
