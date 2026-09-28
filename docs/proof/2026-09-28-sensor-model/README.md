# Модель до датчика: данные для обучения без базы

Каталог создан 28.09.2026 под задачу «модель до датчика на симуляции по регламенту»
(эпик MOS-248). Облачная сессия не видит базу стенда, поэтому всё нужное для обучения
лежит здесь CSV-файлами, выгруженными со стенда `f197d6e` запросами ниже.

## Файлы `data/`

| Файл | Строк | Откуда | Что в нём |
|---|---|---|---|
| `channels.csv` | 12 636 | `smvu.channel` | все каналы; в прогнозе участвуют `is_active = t AND is_stub = f` — их 11 485. `collector` — номер коллектора выгрузки, `picket` — пикет, может быть пустым (765 каналов), `object_id` — узел `smvu.object_tree` |
| `object_tree.csv` | 95 | `smvu.object_tree` | дерево объектов; коллектор — узел уровня 2, «объект Каппа ДУ» — узел 5657 |
| `failures.csv` | 12 139 | `smvu.model_failure_event` | реальные отказы: эпизод `Неисправен` и словарь D5 длиннее часа (`contracts/failure.v3.json`), 2019-01-01…2026-06-30. Для обучения — только с 2022-04-01: до этой даты парк каналов другой, а 2021 год заказчик советует исключить |
| `ppr_windows.csv` | 26 | `maint.ppr_window` | график ППР заказчика 2026; эпизод газового датчика узла из строки с `match = sure`, начатый в окне `dismantle_from 00:00 … return_to 23:59:59` МСК, — плановый демонтаж, не отказ |
| `passports.csv` | 11 485 | `asset.equipment`, `source_system = 'synthetic-demo'` | **синтетические** паспорта: вид оборудования (`object_kind`), дата ввода, нормативный срок службы. Выведены из хеша номера канала сидом `db/seed/sensor_demo.sql`, с отказами не связаны |
| `checks.csv` | 21 417 | `asset.measurement` синтетических паспортов | **синтетические** поверки (`SYN_CALIB_ERR`) и моточасы (`is_counter = t`) по датам |

Время в `failures.csv` и `checks.csv` — `timestamptz` с поясом `+03`.

## Как выгружено

```bash
q() { ssh root@135.106.216.101 "docker exec -i moskollektor-db-1 psql -U moskollektor -d moskollektor -q" \
      <<<"\\copy ($2) to stdout with csv header" > data/$1.csv; }
q channels    "select channel_id, system_kind, sensor_kind, tag, name, collector, picket, section_id, object_id, is_active, is_stub from smvu.channel order by channel_id"
q object_tree "select object_id, level, parent_id, kind, name from smvu.object_tree order by object_id"
q failures    "select episode_id, channel_id, section_id, started_at, ended_at, fault_value from smvu.model_failure_event order by started_at, channel_id"
q ppr_windows "select plan_row, object_id, match, sensor_kind, qty, dismantle_from, return_to, accepted_on from maint.ppr_window order by id"
q passports   "select e.source_key::int as channel_id, e.equipment_no, k.code as object_kind, e.model_no, e.build_year, e.in_service_from, e.service_life_years from asset.equipment e left join ref.object_kind k on k.id=e.object_kind_id where e.source_system='synthetic-demo' order by 1"
q checks      "select e.source_key::int as channel_id, mp.point_no, c.code as characteristic, mp.is_counter, m.measured_at, m.value_num, m.value_text from asset.measurement m join asset.measuring_point mp on mp.id=m.point_id join asset.equipment e on e.id=mp.equipment_id left join ref.characteristic c on c.id=mp.characteristic_id where e.source_system='synthetic-demo' order by 1, m.measured_at"
```

Контрольные числа выгрузки: отказов по годам 2019 — 638, 2020 — 2 849, 2021 — 3 562,
2022 — 1 870, 2023 — 763, 2024 — 323, 2025 — 1 153, 2026 — 981.

## Что сделано на этих данных (SL.10, MOS-263)

| Файл | Что в нём |
|---|---|
| `simulation.md` | параметры симуляции отказов по паспорту: β, η, интервал поверки, множитель при просрочке — у каждого числа источник или «допущение»; итог прогона генератора |
| `sim_failures.csv` | 4 341 искусственный отказ, выход `backend/app/domain/failure_sim.py`; реальные отказы генератор не читает |
| `train_sensor_model.py` | обучение «канал × сутки», перебор горизонтов 12/24/36/48 ч, выбор на январе–марте 2026, замер на апреле–июне |
| `run.json`, `run.log` | все числа прогона и его ход |
| `metrics.md` | таблицы метрик обоих режимов, кривая порога, выводы |
| `training-report.md` | отчёт об обучении: данные, окна, признаки, метка, архитектура, повтор со сверкой SHA-256 |

| `rules.py`, `rules.json` | правила экрана: давность (горизонт H = 24 ч задан решением 28.09.2026, порог выбран на январе–марте 2026; прежде H = 12 ч брался из `run.json` регрессии) и предвестник P-F 2 сут; замер на апреле–июне в обоих режимах, уровни парка на срезах 01.06.2026 00:00 и 27.06.2026 21:00; пишет `backend/app/domain/sensor_rules.json` |

Продукт читает правила — `backend/app/domain/sensor_rules.json`. Регрессия `backend/app/domain/sensor_model.json` (сумма — `SHA256SUMS` рядом) проверена и не дала прироста, worker её не вызывает.

Повтор правил: `uv run --no-project --with numpy==2.3.3 --with pandas==2.3.3 --with scikit-learn==1.7.2 python3 docs/proof/2026-09-28-sensor-model/rules.py`.

## Проверка MOS-264

Текущая причинная версия `sim-v3-causal`; два режима сохранены. Коэффициенты заморожены по 2025 год, Q1 используется для выбора; Q2 — ретроспектива. Актуальные метрики и ограничения — metrics.md и training-report.md; итоговая проверка — ../2026-09-28-mos264-review/review.md. Старые числа в истории Git не относятся к текущему JSON.

## Признаки показаний, MOS-225 (28.09.2026)

`data/reading_features.csv.gz` — отдельная суточная таблица по исходным журналам
2025 и января–июня 2026. Она содержит очищенный метан, флаг вероятной поверки
и счётчики отметок эпохи. Это реальные данные, синтетических паспортов в ней нет.
Аудит исключений и список дней — `data/reading_features_audit.json`, контрольные
суммы исходных CSV и результата — `data/reading_features_manifest.json`.

Подробности и замер: `ml-model/docs/mos225-reading-features.md`.
Контракт: `contracts/sensor-reading-features.v1.json`. Пример чтения:

```python
import csv
import gzip
from datetime import datetime

cut = datetime.fromisoformat("2026-06-23T21:00:00+03:00")
with gzip.open("docs/proof/2026-09-28-sensor-model/data/reading_features.csv.gz", "rt") as f:
    history = [row for row in csv.DictReader(f)
               if datetime.fromisoformat(row["available_at"]) <= cut]
```

Перед обучением выбрать историческое окно и агрегировать строки по `channel_id`;
не присоединять итог за всё полугодие к каждой дате. В 21:00 сегодняшние сутки
ещё недоступны. Дней до 2025 года в этой таблице нет: их отсутствие не равно нулю.
`epoch_marker` и `selected_rows` — аудит, не признаки ошибки или молчания.
`calibration_candidate` — общий флаг дня, а не доказанная поверка каждого канала.
Действующая модель датчика этот файл пока не читает: подключение и повторная
оценка модели требуют отдельного эксперимента после MOS-263/MOS-264.
