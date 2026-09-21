# Общий образ для api, worker и migrate — HLD разд. 7.1: код общий процентов
# на восемьдесят, различается только командой запуска в docker-compose.yml.
# Контекст сборки — корень репозитория (deploy/docker-compose.yml: context: ..).
FROM python:3.14-slim
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
# Миграции вшиты в образ, а не примонтированы — правка любого файла в
# db/migrations/ требует пересборки, иначе migrate.py увидит внутри
# контейнера старое содержимое и sha256 разойдётся с public.schema_migration.
COPY db/migrations ./db/migrations
# 22 признака в contracts/features.v1.yaml — их порядок сам является контрактом
# (docs/plan.md, Q7.2), и backend/app/mlclient/client.py читает файл в рантайме,
# а не только на этапе разработки.
COPY contracts ./contracts
# Приёмочные проверки едут в образ: на стенде заказчика метрики М-18…М-21
# доказывает запуск check_metrics.py внутри контейнера, а не наше слово.
# Порядок запуска — docs/install.md, раздел «Проверки на приёмке» (MOS-120).
COPY code ./code
CMD ["python", "-m", "app.api.main"]
