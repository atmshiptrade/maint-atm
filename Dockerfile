FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY db/migrations ./db/migrations
COPY db/demo ./db/demo

RUN useradd --system --uid 10001 appuser \
    && mkdir -p /srv/datos/fotos && chown -R appuser /srv/datos
USER appuser

EXPOSE 8000
# 1) aplica migraciones pendientes  2) arranca la aplicación
# PORT lo define Render; en Docker Compose no existe y se usa el 8000
CMD ["sh", "-c", "python -m app.migrate && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
