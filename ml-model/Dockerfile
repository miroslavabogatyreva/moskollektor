# Образ `ml`: сервер инференса, порт 8100. Контракт — HLD разд. 6.3–6.5.
#
# Базовый образ python:3.14-slim: требование HLD 6.1 «не старше нашего python:3.14-slim».
# Проверено 15.09.2026 по PyPI — колёса под CPython 3.14 есть у всех зависимостей:
# lightgbm 4.7.0 (py3-none-manylinux), numpy 2.5.3 (cp314), scipy 1.18.1 (cp314),
# pydantic-core (cp314), fastapi и uvicorn (py3-none-any). Откат на 3.13 не понадобился.
#
# Версия модели = тег образа: docker build -t moskollektor/ml:collector-20260922 .

FROM python:3.14-slim

# libgomp1 — единственная системная зависимость колеса LightGBM (OpenMP).
# Без неё импорт lightgbm падает на libgomp.so.1.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Зависимости отдельным слоем: правка кода не пересобирает установку пакетов.
COPY requirements/ml-serving.txt ./requirements/ml-serving.txt
RUN pip install --no-cache-dir -r requirements/ml-serving.txt

# Только сервер, а не весь пакет ml. Код сборки датасета и обучения (features*.py,
# labels.py, daily.py, grid.py) в образе инференса не нужен: он тянет duckdb и знает
# пути к данным заказчика. В образе — веса модели и то, что их обслуживает.
COPY src/ml/__init__.py ./src/ml/__init__.py
COPY src/ml/serving ./src/ml/serving

# Конкретный проверенный collector-релиз запекается в образ.
# Без подготовленных весов сборка останавливается.
ARG MODEL_DIR=models/v3_collector_20260922
COPY ${MODEL_DIR} ./models/v3_collector_20260922

ENV PYTHONPATH=/app/src \
    ML_MODEL_DIR=models/v3_collector_20260922

# Непривилегированный пользователь: контейнеру нечего писать на диск.
RUN useradd --create-home --uid 10001 mlsvc && chown -R mlsvc:mlsvc /app
USER mlsvc

EXPOSE 8100

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request as u; \
u.urlopen('http://127.0.0.1:8100/health').read()"

CMD ["python", "-m", "uvicorn", "ml.serving.app:app", "--host", "0.0.0.0", "--port", "8100"]
