# Москоллектор — материалы решения

ЛЦТ-2026, задача 8. Команда «Скайнет».

| Материал | Ссылка |
|---|---|
| Исходный код | [Репозиторий](https://github.com/miroslavabogatyreva/moskollektor) |
| Документация | [PDF](documentation.pdf) |
| Презентация | [PDF](presentation.pdf) |
| Прототип | [Демонстрационный стенд](https://moskollektor.mbogatyreva.ru/login) |
| Видеодемонстрация | [MP4](delivery-materials/brag.mp4) |

На странице входа стенда доступны демо-учётные записи и кнопки «Войти как».

## Установка

[Скачать архив и SHA256SUMS](https://github.com/miroslavabogatyreva/moskollektor/releases/tag/v1.0.1).
[Инструкция установки](delivery/INSTALL.md).
[Результаты проверки установки](delivery/VALIDATION.md).

Архив содержит исходники, миграции и начальные справочники. Выгрузка заказчика
передаётся отдельно. Для первой сборки нужен интернет.

Проверка контрольной суммы после скачивания архива и SHA256SUMS в один каталог:

```sh
sha256sum -c SHA256SUMS
# macOS: shasum -a 256 -c SHA256SUMS
```
