#!/bin/bash
# pull-models.sh — detect hardware and pull the right Ollama models.
#
# GPU detected        → pull qwen2.5:7b + llava:7b (~8.8 GB, vision strategy)
# CPU, no API key     → pull qwen2.5:7b only (~4.4 GB, local reconcile)
# CPU + API key set   → skip all downloads (Anthropic handles inference)

set -euo pipefail
OLLAMA="http://ollama:11434"

HAS_GPU=0
if command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null; then
    HAS_GPU=1
    echo "[pull] NVIDIA GPU detected."
else
    echo "[pull] No GPU detected — CPU mode."
fi

# Skip downloads entirely when Anthropic API key is set and no GPU is present.
# qwen2.5:7b would never be called — no point pulling 4.4 GB.
if [[ "$HAS_GPU" == "0" && -n "${ANTHROPIC_API_KEY:-}" ]]; then
    echo "[pull] ANTHROPIC_API_KEY set — skipping model downloads (Anthropic handles inference)."
    exit 0
fi

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
        echo "[pull] WARNING: ${name} ended with: ${last_status}"
    fi
}

pull_model "qwen2.5:7b"

if [[ "$HAS_GPU" == "1" ]]; then
    pull_model "llava:7b"
fi

echo "[pull] All models ready."
