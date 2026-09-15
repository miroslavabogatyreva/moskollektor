# Заглушка модели. Живёт до тех пор, пока Николай не пришлёт образ ml (задача 8.1);
# подмена — правка ML_IMAGE в deploy/.env, наш код при этом не меняется.
# Контекст сборки — корень репозитория: в образ едет и contracts/, без него
# не пройдёт самопроверка (она сверяется с примерами сообщений).
FROM python:3.14-slim
WORKDIR /app
COPY ml-stub/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY ml-stub/app.py .
COPY contracts ./contracts
EXPOSE 8100
# Один процесс: заглушка отвечает за микросекунды, очередь ей не нужна.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8100"]
