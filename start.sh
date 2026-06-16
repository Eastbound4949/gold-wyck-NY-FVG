#!/usr/bin/env bash
set -e
mkdir -p /data/state

if [ "${SERVICE_TYPE}" = "web" ]; then
    exec streamlit run app.py \
        --server.port "${PORT:-8501}" \
        --server.address 0.0.0.0 \
        --server.headless true
else
    exec python worker.py
fi
