# Москоллектор — материалы решения

ЛЦТ-2026, задача 8. Команда «Скайнет». Комплект версии `v1.0`.

## Основные материалы

| Материал | Ссылка |
|---|---|
| Исходный код | [Репозиторий, версия v1.0](https://github.com/miroslavabogatyreva/moskollektor/tree/v1.0) |
| Документация | [Сопроводительная документация, PDF](https://github.com/miroslavabogatyreva/moskollektor/blob/v1.0/documentation.pdf) |
| Презентация | [Финальная презентация, PDF](https://github.com/miroslavabogatyreva/moskollektor/blob/v1.0/presentation.pdf) |
| Прототип | [Открыть демонстрационный стенд](https://moskollektor.mbogatyreva.ru/login) |

На экране входа стенда доступны демонстрационные учётные записи и кнопки «Войти как».
[Swagger API](https://moskollektor.mbogatyreva.ru/docs) доступен после входа в том же браузере.

## Пакет для самостоятельного развёртывания

- [Скачать moskollektor-v1.0.tar.gz](https://github.com/miroslavabogatyreva/moskollektor/releases/download/v1.0/moskollektor-v1.0.tar.gz).
- [Контрольные суммы SHA256SUMS](https://github.com/miroslavabogatyreva/moskollektor/releases/download/v1.0/SHA256SUMS).
- [Инструкция установки](https://github.com/miroslavabogatyreva/moskollektor/blob/v1.0/delivery/INSTALL.md).
- [Протокол проверки развёртывания](https://github.com/miroslavabogatyreva/moskollektor/blob/v1.0/delivery/VALIDATION.md).

Архив содержит исходный код, миграции и начальные справочники. Данные заказчика
передаются отдельно; для первой сборки требуется интернет. Проверка установки
на новой базе подтверждает запуск сервисов, миграции, вход и API. Она не заменяет
загрузку данных заказчика и не подтверждает качество прогнозов на новых данных.

После скачивания архива и файла контрольных сумм в один каталог проверьте архив:

```sh
sha256sum -c SHA256SUMS
# macOS: shasum -a 256 -c SHA256SUMS
```

## Дополнительные материалы

- [Отчёт о прогнозировании отказов датчиков, PDF](https://github.com/miroslavabogatyreva/moskollektor/blob/v1.0/delivery-materials/sensor-level-report.pdf).
- [Демонстрационное видео, MP4](https://github.com/miroslavabogatyreva/moskollektor/blob/v1.0/delivery-materials/brag.mp4).

Если GitHub не показывает видео в браузере, скачайте файл кнопкой **Download raw file**.

## Доступ к материалам

На момент подготовки комплекта репозиторий приватный. Для открытия исходников,
документов и файлов релиза экспертам нужен предоставленный доступ к GitHub либо
публичный репозиторий. Перед передачей ссылок необходимо проверить их открытие
из учётной записи эксперта или без авторизации после изменения видимости.

Исходники интерфейса, backend и ML находятся в одном репозитории. Тег `v1.0`
фиксирует общий комплект; отдельный репозиторий ML для его установки не требуется.
