# Лицензии образа `ml` (строка приёмки НФ-82)

Снято 21.09.2026 **внутри собранного образа** `moskollektor/ml:lgbm-v3-bag-2026.09.21`, а не
по `ml-serving.txt`: в образе есть транзитивные пакеты, которых в файле нет. Команда —
`importlib.metadata` по всем установленным дистрибутивам; для `libgomp1` — `dpkg -s` и
`/usr/share/doc/libgomp1/copyright`.

База: `python:3.14-slim` — Python 3.14.7 (PSF-2.0), Debian 13.7.

| Пакет | Версия | Лицензия | Откуда |
|---|---|---|---|
| lightgbm | 4.7.0 | MIT | `ml-serving.txt` |
| fastapi | 0.141.1 | MIT | `ml-serving.txt` |
| uvicorn | 0.53.0 | BSD-3-Clause | `ml-serving.txt` |
| numpy | 2.5.3 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | `ml-serving.txt` |
| pydantic | 2.13.5 | MIT | `ml-serving.txt` |
| scipy | 1.18.1 | BSD (классификатор `BSD License`) | `ml-serving.txt`, транзитивный |
| narwhals | 2.26.0 | MIT | `ml-serving.txt`, транзитивный |
| starlette | 1.6.0 | BSD-3-Clause | `ml-serving.txt`, транзитивный |
| pydantic_core | 2.46.5 | MIT | транзитивный |
| annotated-doc | 0.0.5 | MIT | транзитивный |
| annotated-types | 0.8.0 | MIT | транзитивный |
| anyio | 4.15.1 | MIT | транзитивный |
| click | 8.5.0 | BSD-3-Clause | транзитивный |
| h11 | 0.16.0 | MIT | транзитивный |
| idna | 3.20 | BSD-3-Clause | транзитивный |
| typing-inspection | 0.4.4 | MIT | транзитивный |
| typing_extensions | 4.16.0 | PSF-2.0 | транзитивный |
| pip | 26.2.1 | MIT | базовый образ |
| libgomp1 (Debian, из gcc-14) | 14.2.0-19 | GPL-3.0 с GCC Runtime Library Exception | `apt-get`, Dockerfile |

Копилефта, который распространялся бы на наш код, нет: `libgomp1` подключается как
библиотека времени выполнения, и исключение GCC Runtime Library Exception это прямо разрешает.
