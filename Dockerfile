# Sanadex.wanis.online — production image
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SANADEX_DB_PATH=/data/sanadex.db \
    SANADEX_BACKUP_DIR=/data/backups \
    SANADEX_UPLOAD_DIR=/data/uploads \
    PORT=8000 \
    WEB_THREADS=8

WORKDIR /app

RUN pip install --no-cache-dir "jinja2>=3.1" "waitress>=3.0"

COPY . /app
RUN useradd --create-home --shell /usr/sbin/nologin sanadex \
    && mkdir -p /data/backups /data/uploads \
    && chown -R sanadex:sanadex /data /app

USER sanadex
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).status == 200 else 1)"

CMD ["python", "run_prod.py"]
