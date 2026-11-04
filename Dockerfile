# Build the official Telegram Bot API server from source.
FROM debian:bookworm-slim AS bot-api-build

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates cmake g++ git gperf libssl-dev make zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
RUN git clone --depth 1 --recursive https://github.com/tdlib/telegram-bot-api.git
WORKDIR /src/telegram-bot-api
RUN mkdir build \
    && cd build \
    && cmake -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr/local .. \
    && cmake --build . --target install --parallel 2

FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 \
    PORT=3000 \
    USE_LOCAL_BOT_API=true \
    TELEGRAM_BOT_API_URL=http://127.0.0.1:8081

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libssl3 zlib1g \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=bot-api-build /usr/local/bin/telegram-bot-api /usr/local/bin/telegram-bot-api
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x /app/entrypoint.sh

EXPOSE 3000

CMD ["/bin/bash", "/app/entrypoint.sh"]
