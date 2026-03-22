#!/bin/bash
set -e
echo "[pull] Pulling llava:7b (vision model)..."
curl -sf -o /dev/null http://ollama:11434/api/pull -d '{"name":"llava:7b"}'
echo "[pull] Pulling qwen2.5:7b (text/reconcile fallback)..."
curl -sf -o /dev/null http://ollama:11434/api/pull -d '{"name":"qwen2.5:7b"}'
echo "[pull] All models ready."
