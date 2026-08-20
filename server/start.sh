#!/usr/bin/env bash
set -euo pipefail

mkdir -p "$CLIPFORGE_HOME" "$CLIPFORGE_SERVER_HOME" "$OLLAMA_MODELS"
ln -sf /opt/clipforge/pipeline/.venv/bin/clipforge /usr/local/bin/clipforge

ollama serve &
OLLAMA_PID=$!

for i in $(seq 1 60); do
  if curl -sf http://127.0.0.1:11434/api/tags > /dev/null 2>&1; then
    break
  fi
  sleep 2
done

if [ "${CLIPFORGE_OLLAMA_MODEL:-}" = "" ]; then
  CLIPFORGE_OLLAMA_MODEL="llama3.1:8b"
fi
nohup ollama pull "$CLIPFORGE_OLLAMA_MODEL" > /data/ollama-pull.log 2>&1 &

export PATH="/opt/clipforge/pipeline/.venv/bin:$PATH"
exec uvicorn app:app --host 0.0.0.0 --port 7860 --app-dir /opt/clipforge/server