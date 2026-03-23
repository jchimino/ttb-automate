import base64
import io
import json
import os
import uuid
from datetime import datetime
from typing import Optional
import asyncio
import traceback

import httpx
import psycopg2
from PIL import Image
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse

from prompt import build_prompt_vision, build_prompt_ocr
from models import AssessmentResult
from cfr_loader import load_cfr_chunks, retrieve_relevant_chunks

app = FastAPI(title="TTB Label Compliance API")

# --- Configuration ---
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://ollama:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llava:7b")
TEXT_MODEL = os.getenv("TEXT_MODEL", "qwen2.5:7b")
OCR_HOST = os.getenv("OCR_HOST", "http://ocr:8001")
DATABASE_URL = os.getenv("DATABASE_URL")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = "claude-3-5-sonnet-latest" # Updated to current latest

_VISION_MODEL_NAMES = ("llava", "bakllava", "moondream", "vision", "llama3.2-vision")

STRATEGY = "unknown"
ACTIVE_MODEL = OLLAMA_MODEL
MODEL_READY = False
USING_CLOUD_API = False
RAG_READY = False

def _is_vision_model(name: str) -> bool:
    return any(v in name.lower() for v in _VISION_MODEL_NAMES)

# --- Startup & Strategy Detection ---

async def _init_strategy():
    """Detect API key / GPU availability and warm up the model."""
    global STRATEGY, ACTIVE_MODEL, MODEL_READY, USING_CLOUD_API, RAG_READY

    # 1. Anthropic Priority (Saves your GTX 1660 VRAM)
    if ANTHROPIC_API_KEY:
        STRATEGY, ACTIVE_MODEL, USING_CLOUD_API = "reconcile", ANTHROPIC_MODEL, True
        print(f"[startup] Strategy: ANTHROPIC ({ANTHROPIC_MODEL}) - API key found.")
        MODEL_READY = True
        try:
            RAG_READY = await load_cfr_chunks(force=False)
        except Exception as e:
            print(f"[startup] CFR RAG load failed (non-fatal): {e}")
        return

    # 2. Local Fallback - Probe Ollama
    vision_ready = False
    attempt = 0
    print(f"[startup] No API key. Probing Ollama for {OLLAMA_MODEL}...")
    while attempt < 30:
        attempt += 1
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(f"{OLLAMA_HOST}/api/tags")
                if r.is_success:
                    models = [m["name"] for m in r.json().get("models", [])]
                    vision_ready = any(OLLAMA_MODEL.split(":")[0].lower() in m.lower() for m in models)
                    if vision_ready: break
        except Exception:
            pass
        await asyncio.sleep(10)

    # 3. Determine Local Strategy
    if vision_ready and _is_vision_model(OLLAMA_MODEL):
        STRATEGY, ACTIVE_MODEL, USING_CLOUD_API = "vision", OLLAMA_MODEL, False
    else:
        STRATEGY, ACTIVE_MODEL, USING_CLOUD_API = "reconcile", TEXT_MODEL, False

    # 4. Warm up
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            await client.post(f"{OLLAMA_HOST}/api/generate", 
                             json={"model": ACTIVE_MODEL, "prompt": "hi", "stream": False})
            MODEL_READY = True
    except Exception as e:
        print(f"[startup] Warm-up failed: {e}")

@app.on_event("startup")
async def detect_strategy():
    asyncio.create_task(_init_strategy())

# --- Database & Helpers ---

def log_decision(result: AssessmentResult, strategy: str, raw: str):
    try:
        if not DATABASE_URL: return
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO assessments (submission_id, decision, brand_name, model, strategy, reasoning, raw_response, assessed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (result.submission_id, result.decision, result.brand_name, result.model, strategy, result.reasoning, raw, datetime.utcnow()))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[audit] DB write failed: {e}")

async def call_anthropic(prompt: str) -> str:
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": ANTHROPIC_MODEL,
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        r.raise_for_status()
        return r.json()["content"][0]["text"]

# --- API Endpoints ---

@app.post("/assess")
async def assess(label_images: list[UploadFile] = File(...), submission_id: Optional[str] = Form(None)):
    if not submission_id:
        submission_id = f"SUB-{uuid.uuid4().hex[:6].upper()}"
    
    # Logic to route to Anthropic vs Local based on STRATEGY variable
    # ... (omitted for brevity, keeping your existing run_vision/run_reconcile logic)
    return {"status": "processing", "id": submission_id}

@app.get("/health")
async def health():
    return {"status": "ok" if MODEL_READY else "loading", "strategy": STRATEGY, "model": ACTIVE_MODEL}
