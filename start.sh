#!/usr/bin/env bash
set -e
mkdir -p /data/state

echo "=== 4-Strategy Paper Bot Starting ==="
echo "Starting worker in background..."
python -u worker.py &
WORKER_PID=$!
echo "Worker PID: $WORKER_PID"

echo "Starting Streamlit dashboard..."
exec streamlit run app.py \
    --server.port "${PORT:-8501}" \
    --server.address 0.0.0.0 \
    --server.headless true
