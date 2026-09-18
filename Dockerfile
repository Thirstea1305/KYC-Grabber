FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    KYC_WEB__HOST=0.0.0.0 \
    KYC_WEB__PORT=8080 \
    KYC_DATA_DIR=/app/data \
    KYC_STORE_PATH=/app/data/kyc_grabber.sqlite3

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY kyc_grabber ./kyc_grabber
COPY data/internal_db_seed.json ./data/internal_db_seed.json
COPY README.md ./

# Writable state lives in the mounted volume
RUN mkdir -p /app/data/inbox /app/data/inbox/processed /app/data/outbox /app/data/out

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/health').status==200 else 1)"

CMD ["python", "-m", "kyc_grabber", "run"]
