#!/bin/bash
# pull-models.sh — detect hardware and pull the right Ollama models.
#
# CPU only + GROQ_API_KEY set → no models pulled (Groq handles inference)
# CPU only, no GROQ_API_KEY   → qwen2.5:7b only (~4.4 GB, reconcile strategy)
# NVIDIA GPU                  → qwen2.5:7b + llava:7b (~8.8 GB, vision strategy)
#
# Detection is automatic: no manual env vars or extra files needed beyond GROQ_API_KEY.
# Ollama's /api/pull streams newline-delimited JSON; we consume the full
# stream so this script only exits once the weights are on disk.

set -euo pipefail
OLLAMA="http://ollama:11434"

# ── Detect GPU ────────────────────────────────────────────────────────────────
# nvidia-smi is present and exits 0 only when NVIDIA drivers are accessible.
HAS_GPU=0
if command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null; then
    HAS_GPU=1
    echo "[pull] NVIDIA GPU detected — will pull vision model."
else
    echo "[pull] No GPU detected — CPU-only mode."
fi

# ── Groq check ────────────────────────────────────────────────────────────────
# When GROQ_API_KEY is set AND no GPU is present, Groq handles CPU inference.
# No local text model is needed in that case — skip the 4.4 GB download.
GROQ_KEY="${GROQ_API_KEY:-}"

if [[ "$HAS_GPU" == "0" && -n "$GROQ_KEY" ]]; then
    echo "[pull] GROQ_API_KEY set and no GPU detected — using Groq for inference."
    echo "[pull] Skipping local model download (~4.4 GB saved)."
    echo "[pull] All models ready."
    exit 0
fi

# ── Pull helper ───────────────────────────────────────────────────────────────
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

# ── Pull models based on hardware ─────────────────────────────────────────────
# Always pull the text model (CPU reconcile path fallback when no Groq key)
pull_model "qwen2.5:7b"

# Vision model only on GPU — too slow for CPU inference
if [[ "$HAS_GPU" == "1" ]]; then
    pull_model "llava:7b"
fi

echo "[pull] All models ready."
