"""
TTB Assessment Orchestrator — selects inference strategy at startup.

GPU + vision model  → VISION:     images sent directly to llava:7b via Ollama.
CPU, API key set    → RECONCILE:  OCR → Anthropic claude-haiku-4-5 (demo mode).
CPU, no key         → RECONCILE:  OCR → local qwen2.5:7b via Ollama.

⚠  Anthropic is used for demonstration purposes when no local GPU is available.
   In production, all inference runs on-premises via Ollama.
"""

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

app = FastAPI(title="TTB Label Compliance API")

OLLAMA_HOST       = os.getenv("OLLAMA_HOST",       "http://ollama:11434")
OLLAMA_MODEL      = os.getenv("OLLAMA_MODEL",      "llama3.2-vision")
TEXT_MODEL        = os.getenv("TEXT_MODEL",        "qwen2.5:7b")
OCR_HOST          = os.getenv("OCR_HOST",          "http://ocr:8001")
DATABASE_URL      = os.getenv("DATABASE_URL")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL   = "claude-haiku-4-5"

_VISION_MODEL_NAMES = (
    "llava", "bakllava", "moondream", "vision",
    "llama3.2-vision", "minicpm-v", "cogvlm", "instructblip",
)

STRATEGY        = "unknown"
ACTIVE_MODEL    = OLLAMA_MODEL
MODEL_READY     = False
USING_CLOUD_API = False


def _is_vision_model(name: str) -> bool:
    return any(v in name.lower() for v in _VISION_MODEL_NAMES)


# ── Startup ───────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def detect_strategy():
    global STRATEGY, ACTIVE_MODEL, MODEL_READY, USING_CLOUD_API

    # Probe GPU via Ollama — cuda/metal/rocm in model info means accelerator is present
    has_gpu = False
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r       = await client.post(f"{OLLAMA_HOST}/api/show", json={"name": OLLAMA_MODEL})
            details = str(r.json()).lower()
            has_gpu = "cuda" in details or "metal" in details or "rocm" in details
    except Exception as e:
        print(f"[startup] GPU probe failed (assuming CPU): {e}")

    if has_gpu and _is_vision_model(OLLAMA_MODEL):
        STRATEGY, ACTIVE_MODEL, USING_CLOUD_API = "vision", OLLAMA_MODEL, False
        print(f"[startup] GPU + vision model → VISION ({OLLAMA_MODEL})")
    elif ANTHROPIC_API_KEY:
        STRATEGY, ACTIVE_MODEL, USING_CLOUD_API = "reconcile", ANTHROPIC_MODEL, True
        print(f"[startup] No GPU, API key set → RECONCILE via Anthropic ({ANTHROPIC_MODEL})")
    else:
        STRATEGY, ACTIVE_MODEL, USING_CLOUD_API = "reconcile", TEXT_MODEL, False
        print(f"[startup] No GPU, no key → RECONCILE via local Ollama ({TEXT_MODEL})")

    print(f"[startup] Strategy: {STRATEGY.upper()} | Model: {ACTIVE_MODEL} | Cloud: {USING_CLOUD_API}")

    if USING_CLOUD_API:
        MODEL_READY = True
        return

    # Block until local Ollama model is ready
    attempt = 0
    while True:
        attempt += 1
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(
                    f"{OLLAMA_HOST}/api/generate",
                    json={"model": ACTIVE_MODEL, "prompt": "hi", "stream": False, "options": {"num_predict": 1}},
                )
            if r.is_success:
                MODEL_READY = True
                print(f"[startup] {ACTIVE_MODEL} ready (attempt {attempt})")
                break
        except Exception as exc:
            pass
        print(f"[startup] Warm-up attempt {attempt} failed — retrying in 10 s …")
        await asyncio.sleep(10)


# ── Database ──────────────────────────────────────────────────────────────────

def log_decision(result: AssessmentResult, strategy: str, raw: str):
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur  = conn.cursor()
        cur.execute("""
            INSERT INTO assessments
              (submission_id, decision, brand_name, model, strategy,
               fields_json, reasoning, raw_response, assessed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            result.submission_id, result.decision, result.brand_name, result.model, strategy,
            json.dumps([f.dict() for f in result.fields]), result.reasoning, raw, datetime.utcnow(),
        ))
        conn.commit(); cur.close(); conn.close()
    except Exception as e:
        print(f"[audit] DB write failed: {e}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _resize_image(img_bytes: bytes, max_px: int = 512) -> bytes:
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    w, h = img.size
    if max(w, h) > max_px:
        scale = max_px / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


async def call_anthropic(prompt: str) -> str:
    """POST to Anthropic Messages API. Used in demo/CPU mode only."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": ANTHROPIC_MODEL, "max_tokens": 1024, "messages": [{"role": "user", "content": prompt}]},
        )
    if not r.is_success:
        raise RuntimeError(f"Anthropic {r.status_code}: {r.text[:300]}")
    return r.json()["content"][0]["text"]


async def call_ollama(prompt: str, images: list[bytes], model: str) -> str:
    resized = [_resize_image(img) for img in images] if (images and _is_vision_model(model)) else []
    payload = {
        "model": model, "prompt": prompt, "stream": False,
        "options": {"temperature": 0.1, "num_predict": 800},
    }
    if resized:
        payload["images"] = [base64.standard_b64encode(img).decode() for img in resized]
    async with httpx.AsyncClient(timeout=300.0) as client:
        r = await client.post(f"{OLLAMA_HOST}/api/generate", json=payload)
    if not r.is_success:
        raise RuntimeError(f"Ollama {r.status_code}: {r.text[:300]}")
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"Ollama error: {data['error']}")
    return data.get("response") or ""


async def call_ocr(images: list[bytes]) -> dict:
    files = [("images", (f"img_{i}.png", img, "image/png")) for i, img in enumerate(images)]
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(f"{OCR_HOST}/extract", files=files)
        r.raise_for_status()
    return r.json()


# ── Strategies ────────────────────────────────────────────────────────────────

async def run_vision(label_bytes, form_bytes, submission_id):
    all_images = ([form_bytes] if form_bytes else []) + label_bytes
    prompt = build_prompt_vision(n_labels=len(label_bytes), has_form=form_bytes is not None, submission_id=submission_id)
    raw    = await call_ollama(prompt, all_images, model=OLLAMA_MODEL)
    return AssessmentResult.from_llm_response(raw, submission_id, OLLAMA_MODEL), raw


async def run_reconcile(label_bytes, form_bytes, submission_id):
    all_images = ([form_bytes] if form_bytes else []) + label_bytes

    ocr_data = await call_ocr(all_images)
    ocr_text = "\n\n".join(f"--- Image {p['index'] + 1} ---\n{p['text']}" for p in ocr_data.get("pages", []))
    print(f"[reconcile] OCR complete — {len(ocr_text)} chars")

    prompt = build_prompt_ocr(ocr_text=ocr_text, n_labels=len(label_bytes), has_form=form_bytes is not None, submission_id=submission_id)

    if USING_CLOUD_API:
        print(f"[reconcile] → Anthropic ({ANTHROPIC_MODEL})")
        raw    = await call_anthropic(prompt)
        result = AssessmentResult.from_llm_response(raw, submission_id, ANTHROPIC_MODEL)
    else:
        print(f"[reconcile] → Ollama ({TEXT_MODEL})")
        raw    = await call_ollama(prompt, all_images, model=TEXT_MODEL)
        result = AssessmentResult.from_llm_response(raw, submission_id, TEXT_MODEL)

    if not ocr_text.strip() and result.decision == "APPROVE":
        result.decision  = "REVIEW"
        result.reasoning = "OCR extracted no text — recommend human verification. " + (result.reasoning or "")

    return result, raw


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/assess")
async def assess(
    label_images:  list[UploadFile]     = File(...),
    form_image:    Optional[UploadFile] = File(None),
    submission_id: Optional[str]        = Form(None),
):
    if not submission_id:
        submission_id = f"SUB-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"

    label_bytes = [await img.read() for img in label_images]
    form_bytes  = await form_image.read() if form_image else None

    try:
        run = run_vision if STRATEGY == "vision" else run_reconcile
        result, raw = await run(label_bytes, form_bytes, submission_id)
    except Exception as exc:
        print(f"[assess] {STRATEGY} failed: {exc!r}\n{traceback.format_exc()} — falling back")
        try:
            result, raw = await run_reconcile(label_bytes, form_bytes, submission_id)
        except Exception as exc2:
            from fastapi import HTTPException
            raise HTTPException(status_code=500, detail=f"Assessment failed: {type(exc2).__name__}: {exc2}") from exc2

    log_decision(result, STRATEGY, raw)
    return JSONResponse(content={
        **result.dict(),
        "strategy":       STRATEGY,
        "active_model":   ACTIVE_MODEL,
        "cloud_api":      USING_CLOUD_API,
        "cloud_provider": "Anthropic" if USING_CLOUD_API else None,
    })


@app.get("/health")
async def health():
    if not MODEL_READY:
        return JSONResponse(status_code=503, content={
            "status": "loading", "strategy": STRATEGY,
            "active_model": ACTIVE_MODEL, "cloud_api": USING_CLOUD_API,
        })
    return {
        "status": "ok", "strategy": STRATEGY,
        "active_model": ACTIVE_MODEL, "cloud_api": USING_CLOUD_API,
        "cloud_provider": "Anthropic" if USING_CLOUD_API else None,
        "vision_model": OLLAMA_MODEL, "text_model": TEXT_MODEL,
    }
