# Общий образ для api, worker и migrate — HLD разд. 7.1: код общий процентов
# на восемьдесят, различается только командой запуска в docker-compose.yml.
# Контекст сборки — корень репозитория (deploy/docker-compose.yml: context: ..).
#
# Двухступенчатая сборка (MOS-39): python-ldap собирается из исходников и
# требует gcc + заголовки libldap/libsasl на этапе сборки, а в рантайме
# нужна только сама библиотека libldap2 без gcc и без -dev пакетов.
FROM python:3.14-slim AS builder
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libldap-dev libsasl2-dev \
    && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.14-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    libldap2 libsasl2-2 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /install /usr/local
WORKDIR /app
COPY backend/app ./app
# Миграции вшиты в образ, а не примонтированы — правка любого файла в
# db/migrations/ требует пересборки, иначе migrate.py увидит внутри
# контейнера старое содержимое и sha256 разойдётся с public.schema_migration.
COPY db/migrations ./db/migrations
# Сиды вшиты рядом с миграциями и тоже требуют пересборки при правке: контейнер
# migrate накатывает их после миграций на каждом прогоне (MOS-119). Без этой
# строки каталога db/seed в образе нет, и права в базе отстают от git — 18.09.2026
# admin получал 403 на GET /api/settings, пока сид не накатали руками.
COPY db/seed ./db/seed
# 22 признака в contracts/features.v1.yaml — их порядок сам является контрактом
# (docs/plan.md, Q7.2), и backend/app/mlclient/client.py читает файл в рантайме,
# а не только на этапе разработки.
COPY contracts ./contracts
# Приёмочные проверки едут в образ: на стенде заказчика метрики М-18…М-21
# доказывает запуск check_metrics.py внутри контейнера, а не наше слово.
# Порядок запуска — docs/install.md, раздел «Проверки на приёмке» (MOS-120).
COPY code ./code
CMD ["python", "-m", "app.api.main"]
