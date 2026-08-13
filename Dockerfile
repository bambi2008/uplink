FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UPLINK_HOST=0.0.0.0 \
    UPLINK_PORT=8800 \
    UPLINK_COMMERCIAL_MODE=1 \
    UPLINK_DATA_DIR=/data

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py doubao.py commercial.py ./
COPY static ./static

RUN useradd --create-home --uid 10001 uplink && mkdir -p /data && chown -R uplink:uplink /app /data
USER uplink

EXPOSE 8800
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8800/api/health/ready', timeout=3)"

CMD ["python", "server.py"]
