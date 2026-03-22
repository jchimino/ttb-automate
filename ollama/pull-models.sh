#!/bin/bash
# pull-models.sh — pull required Ollama models and wait until each is fully downloaded.
#
# Ollama's /api/pull streams newline-delimited JSON. The final line for a successful
# pull contains "status":"success". We must consume the full stream (not just the HTTP
# header) so this script only exits after the weights are on disk.

set -euo pipefail

OLLAMA="http://ollama:11434"

pull_model() {
    local name="$1"
    echo "[pull] Pulling ${name} ..."

    # Stream the response body line-by-line until the server closes the connection.
    # The last JSON line will be {"status":"success"} when the download completes.
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

pull_model "llava:7b"
pull_model "qwen2.5:7b"

echo "[pull] All models ready."
