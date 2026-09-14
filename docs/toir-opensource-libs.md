# Open-source по FMEA / FMECA / RCM / RBI и предиктивной аналитике для ТОиР

Обзор того, что реально есть на GitHub по состоянию на сентябрь 2026. Общий вывод: зрелых библиотек «под ТОиР» нет. Есть три слоя разной зрелости:

| Слой | Зрелость | Что есть |
|---|---|---|
| Надёжностная математика (Weibull, survival, интервалы ТО) | Высокая | `reliability`, `lifelines`, `scikit-survival` |
| Прогностика / RUL / PHM | Средняя | `progpy` (NASA), много исследовательских реп на C-MAPSS |
| Методология FMEA / FMECA / RCM | Низкая | Одиночные Streamlit-тулзы, генераторы отчётов |
| RBI (API 580/581) | Отсутствует | Только коммерческий софт |
| Русскоязычный open-source по ТОиР | Отсутствует | — |

---

## 1. Надёжностная математика — ядро для расчёта стратегии ТО

### `reliability` — MatthewReid854
**Ссылка:** https://github.com/MatthewReid854/reliability
**Документация:** https://reliability.readthedocs.io

**Что это:** Python-библиотека для reliability engineering и survival analysis. Расширяет `scipy.stats` и содержит инструменты, которые обычно есть только в проприетарном софте (ReliaSoft Weibull++ и т.п.).

**Что умеет:**
- Подгонка распределений (Weibull 2P/3P, Exponential, Gamma, Lognormal, Loglogistic, Gumbel, Normal, Beta) к данным с правой цензурой
- Weibull-смеси, competing risks, defective subpopulation модели
- Непараметрика: Kaplan-Meier, Nelson-Aalen
- Reliability growth (Crow-AMSAA), оптимальное время замены, планировщики испытаний
- Physics of Failure: SN-диаграммы, механика разрушения, ползучесть
- Accelerated Life Testing — 24 модели
- `Fit_Everything` — подбирает все распределения разом и ранжирует по AICc/BIC

**Почему стоит смотреть:** Это единственная библиотека, которая закрывает статистическую часть RCM целиком — от истории отказов до оптимального интервала ТО. Если есть журнал ремонтов с датами отказов и наработками — это первый инструмент. Активно поддерживается, хорошая документация с примерами.

**Ограничение:** На >100k записей медленная (чистый Python). Для больших выборок есть `Fit_Weibull_2P_grouped`.

---

### `lifelines` — CamDavidsonPilon
**Ссылка:** https://github.com/CamDavidsonPilon/lifelines

**Что это:** Survival analysis в Python. Kaplan-Meier, Cox proportional hazards, AFT-модели, параметрические модели.

**Почему стоит смотреть:** Когда нужно учесть ковариаты — возраст оборудования, режим эксплуатации, производителя, условия среды — и понять, какие факторы ускоряют отказ. Cox-модель отвечает на вопрос «во сколько раз выше риск отказа у объекта с признаком X». Хорошо дополняет `reliability`, у которого ковариаты слабее.

---

### `scikit-survival` — sebp
**Ссылка:** https://github.com/sebp/scikit-survival

**Что это:** Survival-модели в sklearn-стиле: Random Survival Forest, Gradient Boosting Survival, Survival SVM, Cox с регуляризацией.

**Почему стоит смотреть:** Если данных много и зависимости нелинейные — Random Survival Forest часто бьёт Cox по качеству. Совместим с sklearn-пайплайнами, легко встроить в существующий ML-стек.

---

## 2. Прогностика / RUL / PHM

### `progpy` — NASA
**Ссылка:** https://github.com/nasa/progpy
**Документация:** https://nasa.github.io/progpy/
**Установка:** `pip install progpy`

**Что это:** Фреймворк NASA Prognostics Center of Excellence для модельной прогностики — расчёта остаточного ресурса (RUL) и health management инженерных систем. Объединяет бывшие `prog_models` и `prog_algs`. NASA Software of the Year 2024.

**Что умеет:**
- Физические модели деградации компонентов (батареи, насосы, клапаны, двигатели)
- Алгоритмы оценки состояния (UKF, particle filter) и предсказания с распространением неопределённости
- Data-driven модели (LSTM) — ставятся отдельно через `pip install progpy[datadriven]`
- Шаблоны для своих моделей, предикторов и оценщиков состояния

**Почему стоит смотреть:** Если есть телеметрия с датчиков и можно описать физику деградации — это лучший открытый инструмент. Даёт не точку, а распределение RUL с доверительными интервалами. Академически выверенный, авторитетный источник для КП.

**Ограничение:** Сильнее для физических моделей, чем для «взяли выгрузку из CMMS и обучили». Порог входа выше, чем у sklearn.

---

### GitHub-топики по RUL и predictive maintenance
**Ссылки:**
- https://github.com/topics/remaining-useful-life
- https://github.com/topics/rul-prediction
- https://github.com/topics/remaining-useful-life-prediction
- https://github.com/topics/predictive-maintenance
- https://github.com/topics/time-to-failure

**Что там:** Сотни реп, ~90% — учебные проекты на датасетах NASA C-MAPSS (турбины) и литиевых батарей. Из полезного:
- Коллекция RUL-датасетов как PyTorch Lightning DataModules (CMAPSS, N-CMAPSS, FEMTO, XJTU, PRONOSTIA) — удобно для бенчмарков
- Библиотеки для DL-моделей промышленного мониторинга: anomaly detection, RUL, health index, fault diagnosis
- Awesome-list по predictive maintenance / PHM с подборкой статей, датасетов и кода
- ProFeld — survival analysis + predictive maintenance + RUL в одном пакете (не обновлялся с 2023)

**Почему стоит смотреть:** Не как зависимости, а как референс архитектур (LSTM/CNN/Transformer для RUL) и готовые пайплайны подготовки данных. Полезно, чтобы быстро собрать бейзлайн и не изобретать feature engineering по временным рядам.

---

## 3. Методология FMEA / FMECA / RCM

### `pythonasset/FMECA` — FMECA & RCM Analysis Tool
**Ссылка:** https://github.com/pythonasset/FMECA

**Что это:** Streamlit-приложение для FMECA и RCM на инфраструктурных активах. Реализует полный RCM-цикл по методике Murrumbidgee Irrigation:
1. Планирование — определение активов и операционного контекста
2. RCM-анализ (FMECA) — режимы отказов, последствия, критичность
3. Внедрение — планы ТО и изменения
4. Отчёты и экспорт

Включает фреймворк «7 вопросов RCM» (SAE JA1011).

**Почему стоит смотреть:** Единственная находка, где RCM реализован как процесс от начала до конца, а не как таблица RPN. Ирригационная инфраструктура по структуре похожа на коллекторы — линейные объекты, распределённые активы. Хорошая основа для модели данных FMECA→план ТО.

**Ограничение:** Это приложение, а не библиотека. Логику придётся выдирать.

---

### GitHub-топик `fmea`
**Ссылка:** https://github.com/topics/fmea?l=python

**Что там:**
- Генератор PFMEA и 8D-отчётов в Word (Python-библиотека + веб-приложение)
- CLI-валидатор согласованности PFMEA и Control Plan из Excel
- Streamlit-тул FMEA Risk Prioritization: RPN, флаги AIAG FMEA-4, Pareto, heatmap
- Реестр FMEA + Weibull/Kaplan-Meier + sklearn-классификатор вероятности отказа по цензурированным данным
- RAMS analysis program (Reliability, Availability, Maintainability, Safety)

**Почему стоит смотреть:** Как референс структуры данных FMEA и форматов отчётов. Вариант с FMEA-реестром + Weibull + классификатором — ближе всего к связке «методология + предиктивка».

---

### `dromation/open-fmea`
**Ссылка:** https://github.com/dromation/open-fmea

**Что это:** Desktop-приложение FMEA с редактируемыми таблицами Severity/Occurrence/Detection, автоматическим расчётом RPN с цветовой кодировкой, экспортом в JSON/XML/SQL.

**Почему стоит смотреть:** Если нужен готовый UI-паттерн для ввода FMEA. Формат экспорта в JSON/SQL можно взять за основу схемы.

---

### `benranderson/fmeca`
**Ссылка:** https://github.com/benranderson/fmeca

**Что это:** Django-приложение для FMECA (Failure Mode Effect Criticality Assessment).

**Почему стоит смотреть:** Если стек на Django — есть готовые модели данных. В остальном простой и давно не обновлялся.

---

### `ovitrac/FMECAengine`
**Ссылка:** https://github.com/ovitrac/FMECAengine

**Что это:** Научный тулкит FMECA для задач массопереноса (пищевая упаковка, пористые среды). Ранжирование критичности S×O×D, связка с физическими моделями, Monte-Carlo по тысячам сценариев, внутренний язык `key2key()` для правил.

**Почему стоит смотреть:** Только как пример того, как FMECA связывают с физической моделью и прогоняют сценарии батчем. Предметная область далёкая.

---

### `ApratimR/FMEA` (PyPI: `pip install FMEA`)
**Ссылка:** https://github.com/ApratimR/FMEA

**Что это:** Минимальный пакет для FMEA на PyPI.

**Почему стоит смотреть:** Скорее не стоит — почти нет описания. Упомянут, чтобы не искать повторно.

---

### `cowboy2718/FMEA` (R)
**Ссылка:** https://github.com/cowboy2718/FMEA

**Что это:** R-пакет для визуализации FMEA: hazard/disutility графики severity×occurrence, detection×occurrence на ggplot.

**Почему стоит смотреть:** Только если стек на R. Идеи графиков можно перенести в matplotlib/plotly.

---

## 4. RBI — Risk-Based Inspection (API 580/581)

**На GitHub:** пусто. Поиск по «risk-based inspection» выдаёт только кибербезопасность и финансовый риск.

**Почему так:** API RP 581 — платный стандарт с детальными таблицами для расчёта POF (probability of failure) и COF (consequence of failure) по механизмам повреждения. Открытые реализации нарушали бы лицензию API.

**Коммерческие референсы (для понимания, что должно быть на выходе):**
- DNV Synergi RBI — https://www.dnv.com/software/services/risk-based-inspection-software/ (API 580/581, DNV-RP-G101, EEMUA 159)
- Antea RBI — https://antea.tech/antea-rbi-risk-based-inspection-software/ (API 580/581 с 3D digital twin)
- GE Vernova APM RBI 581 — https://www.gevernova.com/software/documentation/apm-classic/v46/help/pdf/Risk_Based_Inspection_581.pdf (открытая документация — полезна как описание модели данных и workflow)

**Что делать:** Логику POF×COF и матрицу риска 5×5 писать самим. Для полуколичественного RBI (без таблиц API 581) это несложно; стандарт нужен только для полного количественного варианта.

---

## Рекомендуемый стек для практики

```
История отказов из CMMS
    → reliability        (Weibull, MTBF, оптимальный интервал замены)
    → lifelines           (Cox: какие факторы ускоряют отказ)
    → scikit-survival     (Random Survival Forest, если данных много)

Телеметрия с датчиков
    → progpy              (RUL с неопределённостью, если есть физика)
    → sklearn / CatBoost  (классификация «откажет в горизонте N дней»)

Методологический слой
    → своя схема данных по образцу pythonasset/FMECA и open-fmea
    → своя логика RCM decision tree и RBI-матрицы
```

Готовой «коробки» для методологического слоя нет — это и есть ниша для продукта.
