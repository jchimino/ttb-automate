#!/bin/bash
# pull-models.sh — pull required Ollama models and wait until each is fully downloaded.
#
# CPU (default): only qwen2.5:7b is needed (~4.4 GB).
# GPU users run:  docker compose -f docker-compose.yml -f docker-compose.gpu.yml
#   which also pulls llava:7b for vision-based assessment.
#
# Ollama's /api/pull endpoint streams newline-delimited JSON. The final line
# contains "status":"success" — we must consume the full stream so this script
# only exits after the weights are on disk.

set -euo pipefail

OLLAMA="http://ollama:11434"

pull_model() {
    local name="$1"
    echo "[pull] Pulling ${name} ..."

    local last_status
    last_status=$(curl -sf "${OLLAMA}/api/pull" \
        -d "{\"name\":\"${name}\"}" \
        --no-buffer \
        | grep -o '"status":"[^"]*"' | tail -1)

    if [[ "$last_status" == '"status":"success"' ]]; then
        echo "[pull] ${name} ready."
    else
        echo "[pull] WARNING: ${name} pull ended with: ${last_status} — model may not be fully loaded."
    fi
}

# CPU default: text model for OCR-based reconcile strategy
pull_model "qwen2.5:7b"

# GPU users: also pull the vision model (llava:7b)
# This block is only executed when PULL_VISION=1 (set in docker-compose.gpu.yml)
if [[ "${PULL_VISION:-0}" == "1" ]]; then
    pull_model "llava:7b"
fi

echo "[pull] All models ready."
