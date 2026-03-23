#!/bin/bash
# pull-models.sh — pull all required models unconditionally.
#
# Models always pulled regardless of hardware:
#   llava:7b          — vision model (used when GPU is available via Ollama)
#   qwen2.5:7b        — text/reconcile model (support/fallback)
#   nomic-embed-text  — embedding model for RAG (cfr_loader pgvector)
#
# GPU detection is NOT done here because this container has no GPU passthrough.
# The assess service detects GPU capability at runtime via the Ollama API.
set -euo pipefail

OLLAMA="http://ollama:11434"

pull_model() {
  local name="$1"
  echo "[pull] Pulling ${name} ..."
  local last_status
  last_status=$(curl -sf "${OLLAMA}/api/pull" \
    -d "{\"name\":\"$name\"}" \
    --no-buffer \
    | grep -o '"status":"[^"]*"' | tail -1)
  if [[ "$last_status" == '"status":"success"' ]]; then
    echo "[pull] ${name} ready."
  else
    echo "[pull] WARNING: ${name} ended with: ${last_status}"
  fi
}

echo "[pull] Pulling all required models ..."
pull_model "llava:7b"
pull_model "qwen2.5:7b"
pull_model "nomic-embed-text"
echo "[pull] All models ready."
