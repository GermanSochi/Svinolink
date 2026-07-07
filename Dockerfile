FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
  && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl \
  && rm -rf /var/lib/apt/lists/*

# Установка xray-core для проксирования через VLESS
RUN curl -L -o /tmp/xray.zip "https://github.com/XTLS/Xray-core/releases/download/v26.3.27/Xray-linux-64.zip" \
  && unzip -o /tmp/xray.zip -d /usr/local/bin/ \
  && rm /tmp/xray.zip \
  && chmod +x /usr/local/bin/xray

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

RUN mkdir -p /app/downloads /app/data

ENV DOWNLOADS_DIR=/app/downloads \
    PORT=10000 \
    PROXY_ENABLED=1
CMD ["bash", "-c", "if [ \"$PROXY_ENABLED\" = \"1\" ] && [ -f /app/xray_config.json ]; then xray run -c /app/xray_config.json &>/dev/null & sleep 2; fi && python main.py"]

