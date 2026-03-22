#!/bin/bash
# pull-models.sh — detect hardware and pull the right Ollama models.
#
# CPU-only  →  qwen2.5:7b only  (~4.4 GB)  — reconcile (OCR + text LLM) strategy
# NVIDIA GPU →  qwen2.5:7b + llava:7b (~8.8 GB) — vision strategy enabled
#
# Detection is automatic: no env vars or extra files needed.
# Ollama's /api/pull streams newline-delimited JSON; we consume the full
# stream so this script only exits once the weights are on disk.

set -euo pipefail

OLLAMA="http://ollama:11434"

# ── Detect GPU ──────────────────────────────────────────────────────────────
# Ask Ollama directly: a running GPU-enabled instance reports its GPU list
# via GET /api/ps or shows cuda/metal in model info.  The simplest and most
# portable check is nvidia-smi — present and exit-0 only when NVIDIA drivers
# are accessible inside the container.
HAS_GPU=0
if command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null; then
    HAS_GPU=1
    echo "[pull] NVIDIA GPU detected — will pull vision model."
else
    echo "[pull] No GPU detected — CPU-only mode (reconcile strategy)."
fi

# ── Pull helper ─────────────────────────────────────────────────────────────
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

# ── Pull models based on hardware ───────────────────────────────────────────
# Always pull the text model (used by both CPU and GPU for reconcile fallback)
pull_model "qwen2.5:7b"

# Vision model only on GPU — too slow for CPU inference
if [[ "$HAS_GPU" == "1" ]]; then
    pull_model "llava:7b"
fi

echo "[pull] All models ready."
