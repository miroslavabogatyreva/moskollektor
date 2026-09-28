# Установка из архива поставки

Этот файл входит в архив и не требует доступа к внутреннему каталогу `docs/`.
Пакет содержит исходный код, миграции и начальные справочники. Выгрузка заказчика,
пароли, сертификаты, готовые Docker-образы и `node_modules` в пакет не включены.
Для первой сборки нужен интернет (Docker Hub, Debian, PyPI, npm).

## 1. Требования и распаковка

Установите Docker Engine/Desktop с плагином `docker compose`, Node.js 22+ с npm,
OpenSSL и tar. На Apple Silicon контейнер PostgreSQL использует эмуляцию amd64.
Проверьте `docker info`, `docker compose version`, `node --version`.

```sh
tar -xzf moskollektor-v1.0.tar.gz
cd moskollektor-v1.0
```

Если у архива другое имя версии, используйте имя каталога из `tar -tzf`.

## 2. Конфигурация и сборка интерфейса

```sh
cp deploy/.env.example deploy/.env
openssl rand -hex 16   # вписать результат в POSTGRES_PASSWORD в deploy/.env
openssl rand -hex 32   # вписать результат в AUTH_SECRET
openssl rand -hex 32   # вписать результат в INGEST_TOKEN для эмулятора потока
cd frontend
npm ci
npm run build
mkdir -p ../deploy/nginx/app
cp -R dist/. ../deploy/nginx/app/
cd ../deploy
sh make-cert.sh
```

Не публикуйте `.env` и приватный ключ. `AUTH_TRUST_HEADER` оставьте равным `0`:
вход выполняется паролем и cookie, заголовок `X-User-Login` прав не даёт.
Для демонстрации `AUTH_DEMO_HINTS=1` показывает тестовые учётные записи.
Перед эксплуатацией замените демонстрационные пароли и выключите подсказки.

Чтобы не занять порты другого стенда, добавьте в `.env`:

```dotenv
DB_PORT=25432
HTTP_PORT=28080
HTTPS_PORT=28443
```

Ниже везде используйте одно выбранное имя проекта. Изолированный проект создаёт
свои контейнеры, сеть и тома; не подставляйте имя уже действующего стенда.

## 3. Запуск

Из каталога `deploy`:

```sh
docker compose -p moskollektor-review --profile app up -d --build
docker compose -p moskollektor-review --profile app ps -a
docker compose -p moskollektor-review logs migrate
```

`migrate` должен завершиться с кодом 0. `db`, `nginx` и `api` должны перейти в
`healthy`; `worker`, `backup`, `emulator-smvu` остаются запущенными. Первичная
сборка может занимать несколько минут. Возврат `up -d` сам по себе недостаточен.
Для отдельной демонстрации LDAP добавьте `--profile ldap` при запуске; локальные
демо-учётные записи работают без LDAP.

## 4. Минимальная проверка

Откройте `https://localhost:28443/login`. Самоподписанный сертификат предназначен
для локальной проверки; для внешнего стенда нужен доверенный сертификат.

```sh
curl -kfsS https://localhost:28443/health
curl -kfsS https://localhost:28443/login > /dev/null
curl -kfsS -c /tmp/moskollektor-review.cookies \
  -H 'Content-Type: application/json' \
  -d '{"login":"ods1","password":"ods123456"}' \
  https://localhost:28443/api/auth/login
curl -kfsS -b /tmp/moskollektor-review.cookies https://localhost:28443/api/auth/me
curl -kfsS -b /tmp/moskollektor-review.cookies https://localhost:28443/api/objects/tree
curl -kfsS -b /tmp/moskollektor-review.cookies https://localhost:28443/api/forecasts
curl -kfsS -b /tmp/moskollektor-review.cookies https://localhost:28443/api/orders
rm /tmp/moskollektor-review.cookies
docker compose -p moskollektor-review logs --tail 30 worker emulator-smvu
```

`-k` допустим здесь только для своего самоподписанного локального сертификата.
На новом томе база содержит начальные справочники, но не многолетние измерения
заказчика. Пустые списки прогнозов/заявок и отсутствие поступающих показаний без
выгрузки ожидаемы. Этот smoke подтверждает установку, миграции, вход и API;
он не подтверждает качество прогнозов и полный цикл на данных заказчика.

## 5. Данные заказчика

Получите выгрузку отдельно. Полный журнал содержит сотни миллионов строк;
оцените свободное место перед импортом. Порядок загрузки — объекты, каналы,
затем измерения. Конкретные команды и ограничения повторной загрузки приведены
в `deploy/README.md`, раздел «Залить выгрузку СМВУ». После импорта повторите
`docker compose -p moskollektor-review --profile app run --rm migrate`, чтобы
сиды, зависящие от каналов, применились к появившимся данным.
Не выдавайте запуск на пустой базе за воспроизведение исторических метрик.

## 6. Остановка

```sh
docker compose -p moskollektor-review --profile app down
```

Эта команда сохраняет тома. Только для ненужной тестовой установки добавьте
`-v`: это удалит её базу и резервные копии без возможности восстановления.
Подробная конфигурация, импорт и резервное копирование: `deploy/README.md`.
