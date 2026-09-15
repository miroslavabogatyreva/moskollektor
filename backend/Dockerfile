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
CMD ["python", "-m", "app.api.main"]
