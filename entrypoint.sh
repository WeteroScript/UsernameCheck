#!/usr/bin/env bash
set -Eeuo pipefail

PIDS=()

cleanup() {
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
    for pid in "${PIDS[@]}"; do
        wait "$pid" 2>/dev/null || true
    done
}
trap cleanup EXIT INT TERM

# Bothost reverse-proxy health endpoint; Telegram updates still use long polling.
python /app/health_server.py &
HEALTH_PID=$!
PIDS+=("$HEALTH_PID")

if [[ "${USE_LOCAL_BOT_API:-true}" == "true" ]]; then
    : "${API_ID:?Set API_ID in Bothost environment variables}"
    : "${API_HASH:?Set API_HASH in Bothost environment variables}"

    mkdir -p /app/data/telegram-bot-api /tmp/telegram-bot-api
    telegram-bot-api \
        --api-id="$API_ID" \
        --api-hash="$API_HASH" \
        --local \
        --http-ip-address=127.0.0.1 \
        --http-port=8081 \
        --dir=/app/data/telegram-bot-api \
        --temp-dir=/tmp/telegram-bot-api &
    API_PID=$!
    PIDS+=("$API_PID")

    ready=false
    for _ in $(seq 1 60); do
        if ! kill -0 "$API_PID" 2>/dev/null; then
            echo "Local Telegram Bot API exited during startup." >&2
            exit 1
        fi
        if (exec 3<>/dev/tcp/127.0.0.1/8081) 2>/dev/null; then
            exec 3>&- 3<&-
            ready=true
            break
        fi
        sleep 1
    done
    if [[ "$ready" != "true" ]]; then
        echo "Local Telegram Bot API did not start listening on 127.0.0.1:8081." >&2
        exit 1
    fi
fi

python /app/Bot.py &
BOT_PID=$!
PIDS+=("$BOT_PID")

wait -n "${PIDS[@]}"
