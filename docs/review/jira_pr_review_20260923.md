# Сверка Jira и PR — 23.09.2026

Просмотрены все 220 задач проекта MOS в живой Jira; 33 задачи подробно прочитаны с описаниями и обсуждениями. Начальный снимок: 117 «Готово», 11 «В работе», 92 «К выполнению». Состояние других параллельных сессий может измениться после этого снимка.

## PR для ревью

| PR | Содержание | Состояние / зависимость |
|---|---|---|
| [10](https://github.com/miroslavabogatyreva/moskollektor/pull/10) | Матрица требований и сверка Jira | Документы готовы к ревью; не сертификат приёмки |
| [11](https://github.com/miroslavabogatyreva/moskollektor/pull/11) | Исходники ML-кандидата, manifests и focused checks | Draft: MOS-219 (24 ч), MOS-217 (детализация), внешние веса/входы |
| [12](https://github.com/miroslavabogatyreva/moskollektor/pull/12) | Неизменяемые предупреждения, отсутствие дублей, точная карта/API, защита горизонта | Draft: новый контракт, принятая модель, выбор отдельных заявок/сроков/лимита требуют ревью |
| [9](https://github.com/miroslavabogatyreva/moskollektor/pull/9) | Ранее открытый PR команды: готовность Q2 и MOS-220 | Не дублировался и не изменялся |

PR #4, #5, #6 уже слиты; PR #7 закрыт. Новые PR открыты поверх master 658b150. Автоматического закрытия задач, слияния PR и deployment не выполнялось.

## Обновления задач

- MOS-74 и MOS-145: в начало описания добавлено актуальное состояние кандидата; исходное описание и история сохранены.
- MOS-217: уточнён требуемый результат по последним 10 сообщениям Telegram и схеме Мирославы — прогноз до датчика, минимум участка. Простая подпись риска коллектора не считается решением. Отдельно учтён уже запущенный ею autoresearch.
- MOS-157, MOS-167, MOS-179, MOS-184, MOS-76: «К выполнению» → «В работе». Нет оснований переводить их в «Готово».
- В 15 связанных задачах добавлены адресные результаты проверки и ссылки на PR: MOS-74,145,157,167,179,182,184,217,219,76,80,81,111,125,21. Открытые требования оставлены открытыми; прежняя инвентаризация библиотек не объявлена приёмкой нового образа.

Повторным чтением Jira подтверждены все 15 комментариев, 26 ссылок на PR, три обновлённых описания и пять переходов статуса. Это подтверждение сохранённых изменений, а не закрытия требований.

## Проверки подготовленных исходников

Продукт: 90 backend/code тестов без пропусков, 38 миграций с нуля, сборка фронтенда, шесть selfcheck, четыре браузерных E2E на итоговом API и свежей базе с реальными архивными прогнозами. Низкоуровневый replay: 176 предупреждений → 352 заявки при явно тестовом лимите 2; конкурентные повторы не дают дублей; отказ записи откатывает всю операцию. Доказательства находятся в PR #12.

ML source package: 14 тестов прошли, 30 пропущены без внешних весов/данных. Это проверка упаковки исходников, не качества 24-часовой модели. Обе кодовые ветки остаются draft.

Остаются обязательные работы: реальное переобучение на 24 ч и метрики; детализация; лицензии принятого образа; согласование механики заявки; сборка/распаковка поставки; аутентификация и области видимости; потоковые сценарии, нагрузка 20 сессий и восстановление; презентация, PDF/DOCX и четыре доступные экспертам ссылки.

## Реестр просмотренных задач

Статусы ниже — начальный снимок до изменений этой сессии; результат изменений перечислен выше. Реальная Jira остаётся источником текущего состояния.

| Задача | Статус на начало проверки | Название |
|---|---|---|
| [MOS-1](https://gotham-city.atlassian.net/browse/MOS-1) | В работе | Q0. Вопросы к заказчику |
| [MOS-2](https://gotham-city.atlassian.net/browse/MOS-2) | В работе | Q1. Развёртывание и эксплуатация |
| [MOS-3](https://gotham-city.atlassian.net/browse/MOS-3) | В работе | Q2. Данные: загрузка, справочники, разведка |
| [MOS-4](https://gotham-city.atlassian.net/browse/MOS-4) | К выполнению | Q3. Расчётное ядро |
| [MOS-5](https://gotham-city.atlassian.net/browse/MOS-5) | К выполнению | Q4. REST API и доступ |
| [MOS-6](https://gotham-city.atlassian.net/browse/MOS-6) | К выполнению | Q5. Интерфейс |
| [MOS-7](https://gotham-city.atlassian.net/browse/MOS-7) | К выполнению | Q6. Модуль заявок |
| [MOS-8](https://gotham-city.atlassian.net/browse/MOS-8) | В работе | Q7. Граница с ML и объяснимость |
| [MOS-9](https://gotham-city.atlassian.net/browse/MOS-9) | К выполнению | Q8. Модель |
| [MOS-10](https://gotham-city.atlassian.net/browse/MOS-10) | В работе | Q9. Приёмка и поставка |
| [MOS-11](https://gotham-city.atlassian.net/browse/MOS-11) | Готово | Q0.1 Собрать таблицу «префикс тега → предполагаемый объект» на все 32 строки |
| [MOS-12](https://gotham-city.atlassian.net/browse/MOS-12) | Готово | Q0.2 ОВ-46: связь канала с объектом — закрыто, заказчик прислал колонку ид_объект |
| [MOS-13](https://gotham-city.atlassian.net/browse/MOS-13) | Готово | Q0.3 ОВ-47: спросить, считать ли отказом эпизод «Неисправен» длиннее часа |
| [MOS-14](https://gotham-city.atlassian.net/browse/MOS-14) | Готово | Q0.4 Спросить заказчика про единицу метрики и показать разрыв до испытаний |
| [MOS-15](https://gotham-city.atlassian.net/browse/MOS-15) | Готово | Q0.5 Ф-81: спросить, чем заменить геометрию в GeoJSON и WKT — координат в выгрузке нет |
| [MOS-16](https://gotham-city.atlassian.net/browse/MOS-16) | Готово | Q0.6 Спросить про три источника, которых у нас нет: реестр оборудования, учёт заявок, метеоданные |
| [MOS-17](https://gotham-city.atlassian.net/browse/MOS-17) | Готово | Q1.1 Завести репозиторий по структуре HLD и поднять пустой стенд с TLS 1.2 |
| [MOS-18](https://gotham-city.atlassian.net/browse/MOS-18) | К выполнению | Q1.2 Настроить резервное копирование по расписанию и написать порядок восстановления |
| [MOS-19](https://gotham-city.atlassian.net/browse/MOS-19) | К выполнению | Q1.3 Написать нагрузочный сценарий на 20 одновременных пользователей |
| [MOS-20](https://gotham-city.atlassian.net/browse/MOS-20) | К выполнению | Q1.4 Прогнать нагрузку на 20 сессий и починить то, что вылезет |
| [MOS-21](https://gotham-city.atlassian.net/browse/MOS-21) | Готово | Q1.5 Собрать перечень всех библиотек с лицензиями по четырём наборам плюс образ Николая |
| [MOS-22](https://gotham-city.atlassian.net/browse/MOS-22) | Готово | Q2.1 Перевести пять схем из code/ в нумерованные миграции 001…005 |
| [MOS-23](https://gotham-city.atlassian.net/browse/MOS-23) | Готово | Q2.2 Залить справочники данными: нормативы ТО, виды работ, уставки |
| [MOS-24](https://gotham-city.atlassian.net/browse/MOS-24) | Готово | Q2.3 Залить справочники каналов и объектов до журнала |
| [MOS-25](https://gotham-city.atlassian.net/browse/MOS-25) | Готово | Q2.4 Довести загрузчик выгрузок СМВУ до продуктивного |
| [MOS-26](https://gotham-city.atlassian.net/browse/MOS-26) | Готово | Q2.5 Залить все восемь файлов выгрузки и разобрать отчёт о качестве |
| [MOS-27](https://gotham-city.atlassian.net/browse/MOS-27) | Готово | Q2.6 Перенести разбор тега в продукт и посчитать, по скольким каналам участок определился |
| [MOS-28](https://gotham-city.atlassian.net/browse/MOS-28) | Готово | Q2.7 Описать, что реально прислал заказчик: файлы, колонки, периоды, число строк, дыры |
| [MOS-29](https://gotham-city.atlassian.net/browse/MOS-29) | Готово | Q2.8 Забирать реестр оборудования автоматически — ждёт ответа по Ф-84 |
| [MOS-30](https://gotham-city.atlassian.net/browse/MOS-30) | Готово | Q2.9 Проверить, можно ли прогнозировать на этих данных |
| [MOS-31](https://gotham-city.atlassian.net/browse/MOS-31) | Готово | Q3.1 Сверить таблицы pred.* с тем, что нужно расчёту, и добить недостающее |
| [MOS-32](https://gotham-city.atlassian.net/browse/MOS-32) | Готово | Q3.2 Написать прогон расчёта из восьми стадий |
| [MOS-33](https://gotham-city.atlassian.net/browse/MOS-33) | Готово | Q3.3 Собирать вектор признаков по contracts/features.v1.yaml |
| [MOS-34](https://gotham-city.atlassian.net/browse/MOS-34) | Готово | Q3.4 Запускать расчёт по расписанию так, чтобы два экземпляра не считали одно и то же |
| [MOS-35](https://gotham-city.atlassian.net/browse/MOS-35) | Готово | Q3.5 Уложить полный расчёт в 300 секунд |
| [MOS-36](https://gotham-city.atlassian.net/browse/MOS-36) | В работе | Q3.6 Забирать метеоданные и добавить шесть признаков погоды |
| [MOS-37](https://gotham-city.atlassian.net/browse/MOS-37) | К выполнению | Q3.7 Принимать поток показаний пачками до 5000 строк с задержкой не больше 300 секунд |
| [MOS-38](https://gotham-city.atlassian.net/browse/MOS-38) | Готово | Q4.1 Завести учётные записи и четыре роли — диспетчер, аналитик, инженер, администратор |
| [MOS-39](https://gotham-city.atlassian.net/browse/MOS-39) | К выполнению | Q4.2 Пускать в систему через службу каталогов, с запасным входом по локальной записи |
| [MOS-40](https://gotham-city.atlassian.net/browse/MOS-40) | Готово | Q4.3 Сделать проверку живости и три списочных метода: риски, прогнозы за период, один прогноз |
| [MOS-41](https://gotham-city.atlassian.net/browse/MOS-41) | Готово | Q4.4 Отдавать карточку объекта и ряд показаний датчика |
| [MOS-42](https://gotham-city.atlassian.net/browse/MOS-42) | К выполнению | Q4.5 Отдавать поток уведомлений, принимать отметку «принял» и отдавать журнал событий |
| [MOS-43](https://gotham-city.atlassian.net/browse/MOS-43) | Готово | Q4.6 Отдавать список заявок и карточку заявки |
| [MOS-44](https://gotham-city.atlassian.net/browse/MOS-44) | К выполнению | Q4.7 Отдавать XML, когда вызывающая система его просит |
| [MOS-45](https://gotham-city.atlassian.net/browse/MOS-45) | К выполнению | Q4.8 Отдавать геоданные в GeoJSON и WKT — ждёт ответа по Ф-81 |
| [MOS-46](https://gotham-city.atlassian.net/browse/MOS-46) | Готово | Q4.9 Описать API и собрать примеры вызовов |
| [MOS-47](https://gotham-city.atlassian.net/browse/MOS-47) | Готово | Q4.10 Писать журнал действий пользователей |
| [MOS-48](https://gotham-city.atlassian.net/browse/MOS-48) | Готово | Q5.1 Собрать каркас приложения: три маршрута, меню, цвета и шрифты внутри контура |
| [MOS-49](https://gotham-city.atlassian.net/browse/MOS-49) | Готово | Q5.2 Сделать дашборд рисков: плитки показателей и список объектов по уровню риска |
| [MOS-50](https://gotham-city.atlassian.net/browse/MOS-50) | Готово | Q5.3 Нарисовать схему коллектора по оси пикетов, а не карту Москвы |
| [MOS-51](https://gotham-city.atlassian.net/browse/MOS-51) | Готово | Q5.4 Сделать журнал прогнозов с отбором по дате, объекту и направлению |
| [MOS-52](https://gotham-city.atlassian.net/browse/MOS-52) | Готово | Q5.5 Сделать карточку объекта, которую открывают и дашборд, и схема |
| [MOS-53](https://gotham-city.atlassian.net/browse/MOS-53) | К выполнению | Q5.6 Показать полосу уведомлений поверх всех трёх экранов с кнопкой «Принял» |
| [MOS-54](https://gotham-city.atlassian.net/browse/MOS-54) | К выполнению | Q5.7 Показать таблицу технологических событий внутри карточки объекта |
| [MOS-55](https://gotham-city.atlassian.net/browse/MOS-55) | К выполнению | Q5.8 Сделать диалог вердикта: диспетчер выбирает одно из четырёх решений |
| [MOS-56](https://gotham-city.atlassian.net/browse/MOS-56) | Готово | Q6.1 Связать заявку с прогнозом: колонки forecast_id и due_at в maint.notification |
| [MOS-57](https://gotham-city.atlassian.net/browse/MOS-57) | Готово | Q6.2 Описать правило, по которому расчёт создаёт заявку |
| [MOS-58](https://gotham-city.atlassian.net/browse/MOS-58) | Готово | Q6.3 Не плодить заявки на один объект |
| [MOS-59](https://gotham-city.atlassian.net/browse/MOS-59) | Готово | Q6.4 Перенести автомат состояний заявки из code/toir_state_machine.py как есть |
| [MOS-60](https://gotham-city.atlassian.net/browse/MOS-60) | Готово | Q6.5 Связать заявку и прогноз в обе стороны через API |
| [MOS-61](https://gotham-city.atlassian.net/browse/MOS-61) | Готово | Q6.6 Сделать так, чтобы диспетчер попадал из заявки в прогноз и обратно одним кликом |
| [MOS-62](https://gotham-city.atlassian.net/browse/MOS-62) | Готово | Q6.7 Сделать экран заявок: таблица и карточка заявки |
| [MOS-63](https://gotham-city.atlassian.net/browse/MOS-63) | К выполнению | Q6.8 Читать статусы заявок из системы учёта заказчика — ждёт ответа по Ф-87 |
| [MOS-64](https://gotham-city.atlassian.net/browse/MOS-64) | Готово | Q7.1 Описать 19 признаков в contracts/features.v1.yaml |
| [MOS-65](https://gotham-city.atlassian.net/browse/MOS-65) | Готово | Q7.2 Описать формат запроса и ответа /predict и приложить примеры |
| [MOS-66](https://gotham-city.atlassian.net/browse/MOS-66) | Готово | Q7.3 Поднять заглушку модели, отвечающую константой по контракту |
| [MOS-67](https://gotham-city.atlassian.net/browse/MOS-67) | Готово | Q7.4 Вызывать модель по контракту: таймаут и разбор ошибки |
| [MOS-68](https://gotham-city.atlassian.net/browse/MOS-68) | Готово | Q7.5 Хранить готовый текст объяснения в той же строке, где лежит прогноз |
| [MOS-69](https://gotham-city.atlassian.net/browse/MOS-69) | Готово | Q7.6 Написать шаблоны фраз на семь признаков, которые дали сигнал |
| [MOS-70](https://gotham-city.atlassian.net/browse/MOS-70) | Готово | Q7.7 Собрать текст объяснения и отдать его наружу |
| [MOS-71](https://gotham-city.atlassian.net/browse/MOS-71) | К выполнению | Q7.8 Показать в карточке объекта, почему риск такой |
| [MOS-72](https://gotham-city.atlassian.net/browse/MOS-72) | К выполнению | Q7.9 Помечать прогноз классом инцидента: пожар, проникновение, подтопление |
| [MOS-73](https://gotham-city.atlassian.net/browse/MOS-73) | К выполнению | Q7.10 Считать и показывать признак «допуск на участок запрещён» |
| [MOS-74](https://gotham-city.atlassian.net/browse/MOS-74) | В работе | Q8.1 Заменить заглушку обученной моделью |
| [MOS-75](https://gotham-city.atlassian.net/browse/MOS-75) | Готово | Q9.1 Замерить четыре метрики методикой проекта |
| [MOS-76](https://gotham-city.atlassian.net/browse/MOS-76) | К выполнению | Q9.2 Прогнать часть 0 — 21 строку программы минимум — и исправить провалившееся |
| [MOS-77](https://gotham-city.atlassian.net/browse/MOS-77) | К выполнению | Q9.3 Прогнать части I и II — 80 строк |
| [MOS-78](https://gotham-city.atlassian.net/browse/MOS-78) | В работе | Q9.4 Написать docs/install.md и docs/build.md |
| [MOS-79](https://gotham-city.atlassian.net/browse/MOS-79) | К выполнению | Q9.5 Написать docs/architecture.md и docs/data-processing.md |
| [MOS-80](https://gotham-city.atlassian.net/browse/MOS-80) | К выполнению | Q9.6 Собрать презентацию по шаблону организаторов |
| [MOS-81](https://gotham-city.atlassian.net/browse/MOS-81) | К выполнению | Q9.7 Собрать страницу сдачи с четырьмя ссылками и поставить тег v1.0 |
| [MOS-82](https://gotham-city.atlassian.net/browse/MOS-82) | К выполнению | Q1.6 Закрыть четыре замечания по безопасности стенда, найденных на приёмке Q1.1 |
| [MOS-83](https://gotham-city.atlassian.net/browse/MOS-83) | Готово | Q2.10 Нарезать партиции smvu.reading вперёд: сейчас они кончаются июнем 2026 |
| [MOS-84](https://gotham-city.atlassian.net/browse/MOS-84) | Готово | Q9.8 Привести HLD, day-one.md и dashboard.md к фактической схеме базы |
| [MOS-85](https://gotham-city.atlassian.net/browse/MOS-85) | Готово | Q1.7 Собрать образ бэкенда и накат миграций: сейчас профиль app не поднимется |
| [MOS-86](https://gotham-city.atlassian.net/browse/MOS-86) | Готово | Q2.11 Пересчитать инциденты верным ключом: 166 посчитано склейкой без префикса |
| [MOS-87](https://gotham-city.atlassian.net/browse/MOS-87) | Готово | Q2.12 Исключить из обучения три пустых дня выгрузки: 6-11 апреля 2024 и 1 июня 2026 |
| [MOS-88](https://gotham-city.atlassian.net/browse/MOS-88) | Готово | Q9.9 Свести число участков к одному: 3173 и 4125 стоят рядом в HLD и приёмке |
| [MOS-89](https://gotham-city.atlassian.net/browse/MOS-89) | Готово | Q9.10 Решить судьбу М-05: строка требует привязки к местности, которой нет в постановке |
| [MOS-90](https://gotham-city.atlassian.net/browse/MOS-90) | Готово | Q1.8 Достроить стенд на сервере: nginx с TLS и заглушка модели |
| [MOS-91](https://gotham-city.atlassian.net/browse/MOS-91) | К выполнению | Q1.9 Сделать резервное копирование базы и проверить восстановление |
| [MOS-92](https://gotham-city.atlassian.net/browse/MOS-92) | Готово | Q2.13 Перестроить первичный ключ smvu.reading и вернуть около 7 ГБ |
| [MOS-93](https://gotham-city.atlassian.net/browse/MOS-93) | Готово | Q2.14 Разобрать 562 449 дублей в файле за 2023 год: повтор строки или расхождение значений |
| [MOS-94](https://gotham-city.atlassian.net/browse/MOS-94) | Готово | Q0.7 Запросить у заказчика выгрузку в xlsx либо закрыть Ф-76 оговоркой в протоколе |
| [MOS-95](https://gotham-city.atlassian.net/browse/MOS-95) | Готово | Q2.15 Разложить 19 типов датчика и 6 типов инженерной системы по справочникам |
| [MOS-96](https://gotham-city.atlassian.net/browse/MOS-96) | Готово | Q2.16 Построить эпизоды отказов: smvu.fault_episode пуста, и её никто не заполняет |
| [MOS-97](https://gotham-city.atlassian.net/browse/MOS-97) | К выполнению | Q7.11 Дописать в контракт, как девять признаков канала сворачиваются в строку участка |
| [MOS-98](https://gotham-city.atlassian.net/browse/MOS-98) | Готово | Q0.8 Спросить заказчика про два дня, когда коллектор замолчал и не вернулся |
| [MOS-99](https://gotham-city.atlassian.net/browse/MOS-99) | Готово | Q3.8 Предрасчёт «канал × сутки»: 26 секунд из 48 уходят на счётчики по каналу за год |
| [MOS-100](https://gotham-city.atlassian.net/browse/MOS-100) | Готово | Q2.17 Принять новую выгрузку справочника каналов: колонка ид_объект в smvu.channel |
| [MOS-101](https://gotham-city.atlassian.net/browse/MOS-101) | К выполнению | Q5.9 Показать дерево объектов диспетчера на экране «Карта объектов» |
| [MOS-102](https://gotham-city.atlassian.net/browse/MOS-102) | Готово | Q0.9 Анализ видео от Москоллектора: встреча с экспертами и доклад на форуме |
| [MOS-103](https://gotham-city.atlassian.net/browse/MOS-103) | Готово | Q3.9 Пересчитать инциденты новым ключом: узел дерева вместо префикса тега |
| [MOS-104](https://gotham-city.atlassian.net/browse/MOS-104) | Готово | Q2.9 Проверить, сколько эпизодов «Неисправен» — это плановое снятие с охраны |
| [MOS-105](https://gotham-city.atlassian.net/browse/MOS-105) | Готово | verdict() пропускает Precision ровно 0,700, хотя М-18 требует строго больше |
| [MOS-106](https://gotham-city.atlassian.net/browse/MOS-106) | Готово | Q5.22 Схема объектов под графику заказчика: значок с номером и цвет состояния |
| [MOS-107](https://gotham-city.atlassian.net/browse/MOS-107) | К выполнению | Q4.11 Роли заказчика и область видимости: техник, диспетчер района, диспетчер ОДС, администратор |
| [MOS-108](https://gotham-city.atlassian.net/browse/MOS-108) | К выполнению | Q9.11 Снять видеозапись работы решения |
| [MOS-109](https://gotham-city.atlassian.net/browse/MOS-109) | К выполнению | Q1.10 Демо-стенд, открытый снаружи без нашего участия |
| [MOS-110](https://gotham-city.atlassian.net/browse/MOS-110) | Готово | Q4.12 Пороги Precision, Recall и горизонт меняет администратор без пересборки образа |
| [MOS-111](https://gotham-city.atlassian.net/browse/MOS-111) | В работе | Q9.12 Пояснительная записка: почему у нас такие числа — каркас написан 21.09.2026, ждёт метрик |
| [MOS-112](https://gotham-city.atlassian.net/browse/MOS-112) | К выполнению | Q6.9 Квитирование: событие не уходит, пока диспетчер его не отработал |
| [MOS-113](https://gotham-city.atlassian.net/browse/MOS-113) | К выполнению | Q5.10 Экран «Настройки» для администратора |
| [MOS-114](https://gotham-city.atlassian.net/browse/MOS-114) | Готово | Q2.18 Убрать два ложных срабатывания check_schema.py на миграции 017 |
| [MOS-115](https://gotham-city.atlassian.net/browse/MOS-115) | Готово | Q2.19 Залить синтетический реестр ТОиР: 7 справочников и 3 173 func_location из object_xref — иначе заявку некуда вставить |
| [MOS-116](https://gotham-city.atlassian.net/browse/MOS-116) | Готово | Q2.20 check_schema.py: разбор не должен зависеть от переносов строк; плюс две лишние двойные проверки |
| [MOS-117](https://gotham-city.atlassian.net/browse/MOS-117) | Готово | Q4.13 Постраничность GET /api/forecasts: метод отдаёт 196 727 прогнозов одним куском, 36 МБ, экран журнала виснет |
| [MOS-118](https://gotham-city.atlassian.net/browse/MOS-118) | Готово | Q4.14 Одно имя computed_at значит в двух методах разное: в /api/risks это срез данных, в /api/forecasts время расчёта |
| [MOS-119](https://gotham-city.atlassian.net/browse/MOS-119) | К выполнению | Q1.12 Сиды из db/seed/ накатом не занимаются: права в базе стенда отстают от git, ловится только чьим-то 403 |
| [MOS-120](https://gotham-city.atlassian.net/browse/MOS-120) | Готово | Q1.13 Приёмочные проверки code/check_*.py не попадают в образ: на приёмке у заказчика метрики доказывать нечем |
| [MOS-121](https://gotham-city.atlassian.net/browse/MOS-121) | К выполнению | Q5.11 Даты в интерфейсе показываются в поясе браузера, а не по Москве: комиссия из другого пояса увидит другие часы у всех дат |
| [MOS-122](https://gotham-city.atlassian.net/browse/MOS-122) | Готово | Q5.12 Фильтры на карте по уровню риска, типу объекта и району |
| [MOS-123](https://gotham-city.atlassian.net/browse/MOS-123) | К выполнению | Q5.13 Обновление экранов без перезагрузки, не реже раза в минуту |
| [MOS-124](https://gotham-city.atlassian.net/browse/MOS-124) | К выполнению | Q5.14 Экран «Журнал действий» для администратора |
| [MOS-125](https://gotham-city.atlassian.net/browse/MOS-125) | К выполнению | Q9.13 Пакет сдачи и проверка его распаковкой на чистой машине |
| [MOS-126](https://gotham-city.atlassian.net/browse/MOS-126) | Готово | Q5.15 Масштабирование оси пикетов: две трети меток слипаются, а заказчик требует именно масштаб |
| [MOS-127](https://gotham-city.atlassian.net/browse/MOS-127) | Готово | Q5.16 Дашборд: показать уровень риска и убрать из строк три колонки-повтора, горизонт оставить в плитке |
| [MOS-128](https://gotham-city.atlassian.net/browse/MOS-128) | Готово | Q5.17 Карточку объекта не открыть с клавиатуры: на дашборде 3173 строки и ноль из них доступны без мыши |
| [MOS-129](https://gotham-city.atlassian.net/browse/MOS-129) | Готово | Q5.18 Плитка обещает данные на сегодня, а последняя запись участка от 22.04.2026: показывать край выгрузки и отставание |
| [MOS-130](https://gotham-city.atlassian.net/browse/MOS-130) | К выполнению | Q5.19 Заявки: 128 из 160 просрочены на 81 день, экран их не отличает, статус печатается кодом OPEN |
| [MOS-131](https://gotham-city.atlassian.net/browse/MOS-131) | Готово | Q5.20 Лента показаний без оси времени: 90 % ленты пусто, и когда была тревога — не прочитать |
| [MOS-132](https://gotham-city.atlassian.net/browse/MOS-132) | Готово | Q5.21 Уборка фронта одним заходом: ошибка со словом Error, пустая страница на опечатку, один заголовок вкладки, лишний шрифт 39,68 КБ |
| [MOS-133](https://gotham-city.atlassian.net/browse/MOS-133) | Готово | Q2.21 Правило отказа smvu.fault_rule стоит не на тех типах датчиков: «Неопределен» держит 59 % таблицы эпизодов без обоснования |
| [MOS-134](https://gotham-city.atlassian.net/browse/MOS-134) | К выполнению | Q9.14 Прогонять команды, записанные в документах приёмки, и сверять вывод с числом в тексте |
| [MOS-135](https://gotham-city.atlassian.net/browse/MOS-135) | Готово | Q4.15 Журнал действий не выгружается за период: GET /api/audit не берёт ни одного параметра, окно 500 записей — это 75 минут |
| [MOS-136](https://gotham-city.atlassian.net/browse/MOS-136) | Готово | Q9.15 Посчитать нашей методикой метрики при требовании упреждения не меньше 24 часов (М-20а) |
| [MOS-137](https://gotham-city.atlassian.net/browse/MOS-137) | Готово | Q9.16 HLD разд. 3.4 описывает API, которого нет: двенадцать методов, ни одного существующего, и строки приёмки уводят приёмщика в 404 |
| [MOS-138](https://gotham-city.atlassian.net/browse/MOS-138) | Готово | Q1.13 code/ не едет на стенд: образ несёт срез проверок от 06:54, и в контейнере М-21 зеленеет на 5,6 с вместо 32,7 |
| [MOS-139](https://gotham-city.atlassian.net/browse/MOS-139) | Готово | Q3.10 Выровнять слоты планировщика и сделать пропуск расчёта видимым (НФ-91) |
| [MOS-140](https://gotham-city.atlassian.net/browse/MOS-140) | Готово | Q1.14 Одиннадцать из семнадцати библиотек не закреплены: перечень для заказчика меняется сам при каждой пересборке |
| [MOS-141](https://gotham-city.atlassian.net/browse/MOS-141) | Готово | Q3.12 Журнал прогонов свёртки feat.refresh_run: пропуск и задержка стали измеримы |
| [MOS-142](https://gotham-city.atlassian.net/browse/MOS-142) | Готово | Q3.11 Считать прогноз на край выгрузки, а не на текущую дату, и назвать эту дату на всех трёх экранах |
| [MOS-143](https://gotham-city.atlassian.net/browse/MOS-143) | Готово | Q9.17 Протокол направлений прогноза (М-02) и наш отчёт об обучении модели v3 (М-01) |
| [MOS-144](https://gotham-city.atlassian.net/browse/MOS-144) | К выполнению | Q5.23 Участок без единого показания получает высокий риск: 0,7797 и ранг 252 при пустых факторах |
| [MOS-145](https://gotham-city.atlassian.net/browse/MOS-145) | В работе | Q8.2 Николаю: выгрузить веса модели v3 и ответить про horizon_h — без этого не закрывается М-01 |
| [MOS-146](https://gotham-city.atlassian.net/browse/MOS-146) | Готово | Q3.14 Интервал расчёта 4 минуты вместо часа: показание должно доходить до расчёта за 300 секунд (НФ-73) |
| [MOS-147](https://gotham-city.atlassian.net/browse/MOS-147) | Готово | Q3.13 Журнал прогнозов растёт линейно от частоты расчёта: миллион строк в сутки на интервале 4 минуты |
| [MOS-148](https://gotham-city.atlassian.net/browse/MOS-148) | Готово | Q5.24 Плитка «Данные по состоянию на» показывает срез последнего прогона, а обещает край выгрузки — ручной запуск ломает её молча |
| [MOS-149](https://gotham-city.atlassian.net/browse/MOS-149) | К выполнению | Q3.15 После смены интервала проверка слотов сутки врёт о причине: 23 пропуска вместо одного |
| [MOS-150](https://gotham-city.atlassian.net/browse/MOS-150) | Готово | Q3.16 Разнести вероятность объекта по пикетам весом из истории отказов: сейчас 190 плиток получат одно число |
| [MOS-151](https://gotham-city.atlassian.net/browse/MOS-151) | Готово | Q5.25 Карточка участка показывает каналы: какой датчик сыплется, сколько раз и как долго лежит |
| [MOS-152](https://gotham-city.atlassian.net/browse/MOS-152) | К выполнению | Q0.9 Одиннадцать газовых датчиков молчат больше года, а числятся исправными — спросить заказчика и показать это на экране |
| [MOS-153](https://gotham-city.atlassian.net/browse/MOS-153) | Готово | Q3.17 Свести определение отказа: признаки считают 919 событий, метрики 583, третье место считает по-своему |
| [MOS-154](https://gotham-city.atlassian.net/browse/MOS-154) | К выполнению | Q6.10 Порог заявки применять к вероятности объекта, а участок выбирать по доле: иначе разнос молча уносит М-12 и М-16 |
| [MOS-155](https://gotham-city.atlassian.net/browse/MOS-155) | Готово | Q4.16 Карточка потеряла направление и объяснение риска: связка с журналом по номеру прогона сломана политикой записи |
| [MOS-156](https://gotham-city.atlassian.net/browse/MOS-156) | К выполнению | Q9.18 Пять проверок из шестнадцати не работают в образе: код доехал, доказательства и сиды — нет |
| [MOS-157](https://gotham-city.atlassian.net/browse/MOS-157) | К выполнению | Q7.12 Наш вектор признаков и модель v3 говорят на разных языках: шлём 22 признака feat.v1, модель ждёт 42 других |
| [MOS-158](https://gotham-city.atlassian.net/browse/MOS-158) | Готово | Q7.13 Передать Николаю привязку каналов к участкам: без неё он не может спустить прогноз ниже коллектора |
| [MOS-159](https://gotham-city.atlassian.net/browse/MOS-159) | Готово | Q3.18 Вес участка считается по 19 810 эпизодам, модель училась на 10 726: свести окно веса с окном обучения |
| [MOS-160](https://gotham-city.atlassian.net/browse/MOS-160) | Готово | Q1.15 Три документа дают три разные команды выкладки, и каждая молча теряет свой каталог |
| [MOS-161](https://gotham-city.atlassian.net/browse/MOS-161) | Готово | Q2.22 Имя объекта не ключ: 95 узлов дерева на 88 имён, и одна пара склеивает 325 каналов двух разных объектов |
| [MOS-162](https://gotham-city.atlassian.net/browse/MOS-162) | К выполнению | Q3.19 Планировщик на стенде пропускает слоты: 21 промежуток из 93 за сутки, худший разрыв 121 минута при интервале 4 минуты |
| [MOS-163](https://gotham-city.atlassian.net/browse/MOS-163) | К выполнению | Q9.19 «30 коллекторов» в двенадцати местах документов: их 16, а 30 — это префиксы тега |
| [MOS-164](https://gotham-city.atlassian.net/browse/MOS-164) | Готово | Q7.14 Ключ коллектора для модели — из базы, а не из префикса тега; учётка ml_ro на чтение |
| [MOS-165](https://gotham-city.atlassian.net/browse/MOS-165) | Готово | Q1.16 Пакет не ставился с нуля: накат вставал на 029, и api с worker не поднимались вовсе |
| [MOS-166](https://gotham-city.atlassian.net/browse/MOS-166) | Готово | Q3.20 Worker считает по выдаче модели v3: score.json, мост ключа, 8,9 с вместо 33,7 |
| [MOS-167](https://gotham-city.atlassian.net/browse/MOS-167) | К выполнению | Q9.20 Перемерить М-18 и М-19 на цели модели по живому продукту: числа в документах посчитаны на другой цели |
| [MOS-168](https://gotham-city.atlassian.net/browse/MOS-168) | Готово | Q3.21 Флаг --limit не действует на пути модели v3: прогон считает весь парк и пишет 3 173 строки в журнал |
| [MOS-169](https://gotham-city.atlassian.net/browse/MOS-169) | К выполнению | Q5.26 Шкала загазованности в карточке объекта: значение метана с размеченными уставками 0,75 и 1,5 % об. |
| [MOS-170](https://gotham-city.atlassian.net/browse/MOS-170) | Готово | Q5.27 Легенда состояний под схемой и на дашборде: название рядом с цветом, а не во всплывающей подсказке |
| [MOS-171](https://gotham-city.atlassian.net/browse/MOS-171) | Готово | Q5.28 Называть объекты так, как их называет диспетчер: канал по имени, заявка по номеру |
| [MOS-172](https://gotham-city.atlassian.net/browse/MOS-172) | Готово | Q5.29 Каналы в карточке объекта сгруппированы по системе, тип показан фигурой рядом со словом |
| [MOS-173](https://gotham-city.atlassian.net/browse/MOS-173) | Готово | Q5.30 Состояние риска на схеме различается только цветом: на чёрно-белой распечатке все три станут одним квадратиком |
| [MOS-174](https://gotham-city.atlassian.net/browse/MOS-174) | Готово | Q5.31 Проверка доступности таблиц против дерева доступности: НФ-92, строки приёмки под клавиатуру не было вовсе |
| [MOS-175](https://gotham-city.atlassian.net/browse/MOS-175) | Готово | Q5.32 Карточка прогноза показывает чужой прогноз под своим адресом: у эффекта нет выключателя на ответ |
| [MOS-176](https://gotham-city.atlassian.net/browse/MOS-176) | К выполнению | Q9.21 Счётчики файлов в project-structure.md стареют молча: code/ отстал на 10, analysis/ на 1 |
| [MOS-177](https://gotham-city.atlassian.net/browse/MOS-177) | К выполнению | Q5.33 Полоска критического риска в тёмной теме невидима: контраст 1,05:1 при норме 3:1 (НФ-51) |
| [MOS-178](https://gotham-city.atlassian.net/browse/MOS-178) | К выполнению | Q5.34 check_stale_fetch.py ищет буквально «fetch(» и не видит два эффекта из семи: журнал и заявки грузят через обёртки |
| [MOS-179](https://gotham-city.atlassian.net/browse/MOS-179) | К выполнению | Q6.11 Срок заявки считается от выдуманного момента отказа as_of + horizon_h: при горизонте 720 срок уедет почти на месяц (М-13, М-20) |
| [MOS-180](https://gotham-city.atlassian.net/browse/MOS-180) | Готово | Q6.12 Предупреждение модели открылось, а заявки нет: кандидаты берутся из журнала с мёртвой зоной, суточный индекс глушит повторное открытие (М-10, М-11) |
| [MOS-181](https://gotham-city.atlassian.net/browse/MOS-181) | Готово | Q5.35 Схема группирует участки по префиксу тега — 30 групп, а прогноз считается по 16 коллекторам дерева: «коллектор» на карте не тот, что в прогнозе (М-05) |
| [MOS-182](https://gotham-city.atlassian.net/browse/MOS-182) | В работе | Q6.13 Worker заводит заявку только на предупреждение, открытое в момент среза, а список всех открытий alerts в score.json не читает: закрытое между обновлениями до бригады не доходит |
| [MOS-183](https://gotham-city.atlassian.net/browse/MOS-183) | К выполнению | Q3.22 Прогон, оборванный перезапуском worker, навсегда остаётся в pred.run со статусом running: прогон 669 от 22.09.2026 20:36 |
| [MOS-184](https://gotham-city.atlassian.net/browse/MOS-184) | К выполнению | Q6.14 Смена веса участков заводит на то же предупреждение новые заявки: 44 автозаявки вместо 33, у шести предупреждений по 4–6 при пределе 3 |
| [MOS-185](https://gotham-city.atlassian.net/browse/MOS-185) | К выполнению | Пользовательские истории по ролям заказчика |
| [MOS-186](https://gotham-city.atlassian.net/browse/MOS-186) | К выполнению | US-01. Начало смены: увидеть, где сейчас риск |
| [MOS-187](https://gotham-city.atlassian.net/browse/MOS-187) | К выполнению | US-02. Видеть, что данные живые |
| [MOS-188](https://gotham-city.atlassian.net/browse/MOS-188) | К выполнению | US-03. Переходить между разделами из меню |
| [MOS-189](https://gotham-city.atlassian.net/browse/MOS-189) | К выполнению | US-04. Уведомление о критическом прогнозе |
| [MOS-190](https://gotham-city.atlassian.net/browse/MOS-190) | К выполнению | US-05. Найти участок на схеме коллектора |
| [MOS-191](https://gotham-city.atlassian.net/browse/MOS-191) | К выполнению | US-06. Проверить прогноз в карточке участка |
| [MOS-192](https://gotham-city.atlassian.net/browse/MOS-192) | К выполнению | US-07. Прочитать график показаний |
| [MOS-193](https://gotham-city.atlassian.net/browse/MOS-193) | К выполнению | US-08. Отметить, чем проверил прогноз |
| [MOS-194](https://gotham-city.atlassian.net/browse/MOS-194) | К выполнению | US-09. Зафиксировать решение по прогнозу |
| [MOS-195](https://gotham-city.atlassian.net/browse/MOS-195) | К выполнению | US-10. Закрыть прогноз фактом |
| [MOS-196](https://gotham-city.atlassian.net/browse/MOS-196) | К выполнению | US-11. Найти прогноз в журнале |
| [MOS-197](https://gotham-city.atlassian.net/browse/MOS-197) | К выполнению | US-12. Журнал технологических событий |
| [MOS-198](https://gotham-city.atlassian.net/browse/MOS-198) | К выполнению | US-13. Участок в работах |
| [MOS-199](https://gotham-city.atlassian.net/browse/MOS-199) | К выполнению | US-14. Один участок — одно имя |
| [MOS-200](https://gotham-city.atlassian.net/browse/MOS-200) | К выполнению | US-15. Работать в Яндекс.Браузере |
| [MOS-201](https://gotham-city.atlassian.net/browse/MOS-201) | К выполнению | US-16. Видеть только свой район |
| [MOS-202](https://gotham-city.atlassian.net/browse/MOS-202) | К выполнению | US-17. Заявка на профилактику появилась сама (руководитель) |
| [MOS-203](https://gotham-city.atlassian.net/browse/MOS-203) | К выполнению | US-18. План профилактики на неделю (руководитель) |
| [MOS-204](https://gotham-city.atlassian.net/browse/MOS-204) | К выполнению | US-19. Статус заявки из системы учёта |
| [MOS-205](https://gotham-city.atlassian.net/browse/MOS-205) | К выполнению | US-20. Насколько верить прогнозу (руководитель) |
| [MOS-206](https://gotham-city.atlassian.net/browse/MOS-206) | К выполнению | US-21. Видеть только свой комплекс |
| [MOS-207](https://gotham-city.atlassian.net/browse/MOS-207) | К выполнению | US-22. Подготовиться к выезду |
| [MOS-208](https://gotham-city.atlassian.net/browse/MOS-208) | К выполнению | US-23. Настроить пороги без выкладки |
| [MOS-209](https://gotham-city.atlassian.net/browse/MOS-209) | К выполнению | US-24. Вход по учётной записи каталога |
| [MOS-210](https://gotham-city.atlassian.net/browse/MOS-210) | К выполнению | US-25. Журнал действий пользователей |
| [MOS-211](https://gotham-city.atlassian.net/browse/MOS-211) | К выполнению | US-26. Состояние источников данных |
| [MOS-212](https://gotham-city.atlassian.net/browse/MOS-212) | К выполнению | US-27. Забрать риски, прогнозы и заявки через REST API |
| [MOS-213](https://gotham-city.atlassian.net/browse/MOS-213) | К выполнению | US-28. Забрать геометрию участков |
| [MOS-214](https://gotham-city.atlassian.net/browse/MOS-214) | К выполнению | US-29. Убедиться, что сервис только читает |
| [MOS-215](https://gotham-city.atlassian.net/browse/MOS-215) | К выполнению | Q5.36 Рамки с номером пикета налезают друг на друга на разреженной линии: правило плотности считает число меток, а не расстояние |
| [MOS-216](https://gotham-city.atlassian.net/browse/MOS-216) | К выполнению | Q5.37 typecheck фронта не проверяет frontend/e2e/: ошибку в спецификации ловит только прогон Playwright |
| [MOS-217](https://gotham-city.atlassian.net/browse/MOS-217) | К выполнению | Q3.23 Выбрать, как опустить риск ниже коллектора — решаем после autoresearch Николая |
| [MOS-218](https://gotham-city.atlassian.net/browse/MOS-218) | Готово | Q2.24 Пачка «75 каналов 04.06.2026 09:22:32» из документов не подтверждается ни базой, ни CSV — найти источник и поправить |
| [MOS-219](https://gotham-city.atlassian.net/browse/MOS-219) | К выполнению | Q8.2 Дефект: модель v3 прогнозирует отказ на 30 суток, а нужен горизонт 24 часа |
| [MOS-220](https://gotham-city.atlassian.net/browse/MOS-220) | К выполнению | Q2.25 Префикс тега назван «коллектором» в трёх местах документов — переписать на «префикс N (коллектор «объект …»)» |
