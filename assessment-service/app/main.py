"""
TTB Assessment Orchestrator
============================
On startup, detects GPU availability and chooses a processing strategy.

GPU detected  → VISION strategy
  Images sent directly to llava:7b running in Ollama.
  The LLM reads label and form visually.

CPU only      → RECONCILE + GROQ strategy
  Step 1: OCR service extracts text from all images → JSON
  Step 2: Groq API (llama-3.3-70b-versatile) receives structured JSON
  Step 3: Result returned to caller

  ⚠  Groq API is used for demonstration purposes when a local GPU is
     unavailable. In a production deployment all inference runs on-premises
     via Ollama. Set GROQ_API_KEY in .env to enable; leave blank and the
     service will fall back to the local qwen2.5:7b model via Ollama.

Either strategy produces the same output schema. n8n doesn't need to know
which path ran — it just receives the verdict.

POST /assess
  form_image   (file, optional) — TTB application form PNG/JPEG
  label_images (files)          — one or more label images
  submission_id (string, optional)
"""

import base64
import io
import json
import os
import re
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
from models import AssessmentResult, FieldResult

app = FastAPI(title="TTB Label Compliance API")

OLLAMA_HOST   = os.getenv("OLLAMA_HOST",   "http://ollama:11434")
OLLAMA_MODEL  = os.getenv("OLLAMA_MODEL",  "llama3.2-vision")   # vision path
TEXT_MODEL    = os.getenv("TEXT_MODEL",    "qwen2.5:7b")        # local reconcile fallback
OCR_HOST      = os.getenv("OCR_HOST",      "http://ocr:8001")
DATABASE_URL  = os.getenv("DATABASE_URL")
GROQ_API_KEY  = os.getenv("GROQ_API_KEY",  "")                  # demo cloud fallback

# Groq model names
GROQ_TEXT_MODEL   = "llama-3.3-70b-versatile"
GROQ_VISION_MODEL = "llama-3.2-11b-vision-preview"

# Known multimodal/vision model name fragments
_VISION_MODEL_NAMES = (
    "llava", "bakllava", "moondream", "vision",
    "llama3.2-vision", "minicpm-v", "cogvlm", "instructblip",
)

# Set at startup
STRATEGY        = "unknown"
ACTIVE_MODEL    = OLLAMA_MODEL
MODEL_READY     = False   # True once warm-up probe succeeds (or Groq is ready)
USING_CLOUD_API = False   # True when Groq handles inference (no local GPU)


def _is_vision_model(name: str) -> bool:
    """Return True if the model name indicates multimodal vision capability."""
    lower = name.lower()
    return any(v in lower for v in _VISION_MODEL_NAMES)


# ── Startup: detect strategy ──────────────────────────────────────────────────

@app.on_event("startup")
async def detect_strategy():
    """
    Detect hardware FIRST, then choose strategy.

    GPU + vision model  → VISION      (Ollama / llava:7b, direct image inference)
    CPU only + Groq key → RECONCILE   (OCR → Groq API, fast cloud inference)
    CPU only, no key    → RECONCILE   (OCR → local qwen2.5:7b via Ollama)

    ⚠  Groq is used for demonstration purposes only when no local GPU is
       present. In production all inference runs on-premises.
    """
    global STRATEGY, ACTIVE_MODEL, MODEL_READY, USING_CLOUD_API

    # ── Step 1: Probe GPU via Ollama /api/show ─────────────────────────────────
    has_gpu = False
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r       = await client.post(f"{OLLAMA_HOST}/api/show", json={"name": OLLAMA_MODEL})
            info    = r.json()
            details = str(info).lower()
            has_gpu = "cuda" in details or "metal" in details or "rocm" in details
            print(f"[startup] GPU probe via /api/show → has_gpu={has_gpu}")
    except Exception as e:
        print(f"[startup] Could not query Ollama model info (assuming CPU): {e}")

    # ── Step 2: Choose strategy ────────────────────────────────────────────────
    if has_gpu and _is_vision_model(OLLAMA_MODEL):
        # GPU path — full local vision inference
        STRATEGY        = "vision"
        ACTIVE_MODEL    = OLLAMA_MODEL
        USING_CLOUD_API = False
        print(f"[startup] GPU confirmed + vision model ({OLLAMA_MODEL}) → VISION strategy (Ollama)")

    elif GROQ_API_KEY:
        # No GPU, but Groq key present — use cloud API for demo
        STRATEGY        = "reconcile"
        ACTIVE_MODEL    = GROQ_TEXT_MODEL
        USING_CLOUD_API = True
        print(f"[startup] No GPU detected → RECONCILE strategy (Groq API / {GROQ_TEXT_MODEL})")
        print(f"[startup] ⚠  Groq API active — demo mode, no local GPU available")

    else:
        # No GPU, no Groq key — fall back to local text model
        STRATEGY        = "reconcile"
        ACTIVE_MODEL    = TEXT_MODEL
        USING_CLOUD_API = False
        reason = "no GPU detected, no GROQ_API_KEY set"
        print(f"[startup] {reason} → RECONCILE strategy (local Ollama / {TEXT_MODEL})")

    print(f"[startup] Ollama: {OLLAMA_HOST} | Strategy: {STRATEGY.upper()} | Active model: {ACTIVE_MODEL} | Cloud API: {USING_CLOUD_API}")

    # ── Step 3: Warm-up probe ──────────────────────────────────────────────────
    # Groq is always ready — no warm-up needed.
    # For local Ollama paths, block until the model can respond.
    if USING_CLOUD_API:
        MODEL_READY = True
        print(f"[startup] Groq API ready — no warm-up probe needed.")
        return

    probe_model = ACTIVE_MODEL
    attempt = 0
    while True:
        attempt += 1
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(
                    f"{OLLAMA_HOST}/api/generate",
                    json={
                        "model":  probe_model,
                        "prompt": "hi",
                        "stream": False,
                        "options": {"num_predict": 1},
                    },
                )
            if r.is_success:
                MODEL_READY = True
                print(f"[startup] Model {probe_model} is ready (attempt {attempt}).")
                break
            print(f"[startup] Warm-up attempt {attempt}: Ollama returned {r.status_code} — retrying in 10 s …")
        except Exception as exc:
            print(f"[startup] Warm-up attempt {attempt}: {exc} — retrying in 10 s …")
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
            result.submission_id,
            result.decision,
            result.brand_name,
            result.model,
            strategy,
            json.dumps([f.dict() for f in result.fields]),
            result.reasoning,
            raw,
            datetime.utcnow(),
        ))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[audit] DB write failed: {e}")


# ── Image helpers ─────────────────────────────────────────────────────────────

def _resize_image(img_bytes: bytes, max_px: int = 512) -> bytes:
    """Resize image so longest side ≤ max_px. Returns JPEG bytes."""
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    w, h = img.size
    if max(w, h) > max_px:
        scale = max_px / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


# ── Groq call ─────────────────────────────────────────────────────────────────

async def call_groq(prompt: str, model: str = GROQ_TEXT_MODEL) -> str:
    """
    Call the Groq API for fast cloud inference.
    Used when no local GPU is detected (demo / CPU-only mode).
    ⚠  For demonstration purposes only — production runs on-premises via Ollama.
    """
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type":  "application/json",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 1024,
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers=headers,
            json=payload,
        )
    if not r.is_success:
        raise RuntimeError(f"Groq API {r.status_code}: {r.text[:300]}")
    data = r.json()
    return data["choices"][0]["message"]["content"]


# ── Ollama call ───────────────────────────────────────────────────────────────

async def call_ollama(prompt: str, images: list[bytes], model: str) -> str:
    """
    Call Ollama with the specified model.
    Vision models receive images directly; text-only models ignore them.
    """
    is_vision = _is_vision_model(model)
    resized   = [_resize_image(img) for img in images] if (images and is_vision) else []
    encoded   = [base64.standard_b64encode(img).decode() for img in resized]

    payload = {
        "model":   model,
        "prompt":  prompt,
        "stream":  False,
        "options": {"temperature": 0.1, "num_predict": 800},
    }
    if encoded:
        payload["images"] = encoded

    async with httpx.AsyncClient(timeout=300.0) as client:
        r = await client.post(f"{OLLAMA_HOST}/api/generate", json=payload)
    if not r.is_success:
        raise RuntimeError(f"Ollama {r.status_code}: {r.text[:300]}")
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"Ollama error: {data['error']}")
    return data.get("response") or ""


# ── OCR call ──────────────────────────────────────────────────────────────────

async def call_ocr(images: list[bytes]) -> dict:
    """Send images to the OCR service, get back extracted text per image."""
    files = [("images", (f"img_{i}.png", img, "image/png")) for i, img in enumerate(images)]
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(f"{OCR_HOST}/extract", files=files)
        r.raise_for_status()
    return r.json()  # { "pages": [ {"index": 0, "text": "..."}, ... ] }


# ── Strategy: VISION ──────────────────────────────────────────────────────────

async def run_vision(
    label_bytes:   list[bytes],
    form_bytes:    Optional[bytes],
    submission_id: str,
) -> tuple[AssessmentResult, str]:
    """Send all images directly to llava:7b via Ollama. GPU path."""
    all_images = ([form_bytes] if form_bytes else []) + label_bytes
    prompt = build_prompt_vision(
        n_labels=len(label_bytes),
        has_form=form_bytes is not None,
        submission_id=submission_id,
    )
    raw    = await call_ollama(prompt, all_images, model=OLLAMA_MODEL)
    result = AssessmentResult.from_llm_response(raw, submission_id, OLLAMA_MODEL)
    return result, raw


# ── Strategy: RECONCILE ───────────────────────────────────────────────────────

async def run_reconcile(
    label_bytes:   list[bytes],
    form_bytes:    Optional[bytes],
    submission_id: str,
) -> tuple[AssessmentResult, str]:
    """
    Two-step path for CPU-only machines.
    Step 1: OCR extracts text from all images.
    Step 2: LLM (Groq cloud or local Ollama) interprets OCR text against CFR rules.

    When USING_CLOUD_API is True, Step 2 calls the Groq API.
    ⚠  Groq is used for demonstration purposes only when no local GPU is available.
    """
    all_images = ([form_bytes] if form_bytes else []) + label_bytes

    # Step 1: OCR
    ocr_data = await call_ocr(all_images)
    ocr_text = "\n\n".join(
        f"--- Image {p['index'] + 1} ---\n{p['text']}"
        for p in ocr_data.get("pages", [])
    )
    print(f"[reconcile] OCR complete — {len(ocr_text)} chars extracted")

    # Step 2: LLM inference
    prompt = build_prompt_ocr(
        ocr_text=ocr_text,
        n_labels=len(label_bytes),
        has_form=form_bytes is not None,
        submission_id=submission_id,
    )

    if USING_CLOUD_API:
        print(f"[reconcile] Sending to Groq ({GROQ_TEXT_MODEL}) — demo mode, no local GPU ⚠")
        raw    = await call_groq(prompt, model=GROQ_TEXT_MODEL)
        result = AssessmentResult.from_llm_response(raw, submission_id, GROQ_TEXT_MODEL)
    else:
        print(f"[reconcile] Sending to Ollama ({TEXT_MODEL})...")
        raw    = await call_ollama(prompt, all_images, model=TEXT_MODEL)
        result = AssessmentResult.from_llm_response(raw, submission_id, TEXT_MODEL)

    # If OCR found no text at all — flag for review
    if not ocr_text.strip() and result.decision == "APPROVE":
        result.decision  = "REVIEW"
        result.reasoning = (
            "OCR extracted no text — visual-only assessment, "
            "recommend human verification. " + (result.reasoning or "")
        )

    return result, raw


# ── Assessment endpoint ───────────────────────────────────────────────────────

@app.post("/assess")
async def assess(
    label_images:  list[UploadFile]  = File(...),
    form_image:    Optional[UploadFile] = File(None),
    submission_id: Optional[str]     = Form(None),
):
    if not submission_id:
        submission_id = f"SUB-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"

    label_bytes = [await img.read() for img in label_images]
    form_bytes  = await form_image.read() if form_image else None

    try:
        if STRATEGY == "vision":
            result, raw = await run_vision(label_bytes, form_bytes, submission_id)
        else:
            result, raw = await run_reconcile(label_bytes, form_bytes, submission_id)
    except Exception as exc:
        print(f"[assess] {STRATEGY} strategy failed: {exc!r}\n{traceback.format_exc()}. Falling back to reconcile.")
        try:
            result, raw = await run_reconcile(label_bytes, form_bytes, submission_id)
        except Exception as exc2:
            print(f"[assess] Reconcile fallback also failed: {exc2!r}\n{traceback.format_exc()}")
            from fastapi import HTTPException
            raise HTTPException(
                status_code=500,
                detail=f"Assessment failed: {type(exc2).__name__}: {exc2}",
            ) from exc2

    log_decision(result, STRATEGY, raw)

    return JSONResponse(content={
        **result.dict(),
        "strategy":       STRATEGY,
        "active_model":   ACTIVE_MODEL,
        "cloud_api":      USING_CLOUD_API,
        "cloud_provider": "Groq" if USING_CLOUD_API else None,
    })


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    if not MODEL_READY:
        return JSONResponse(
            status_code=503,
            content={
                "status":       "loading",
                "strategy":     STRATEGY,
                "active_model": ACTIVE_MODEL,
                "cloud_api":    USING_CLOUD_API,
                "ollama":       OLLAMA_HOST,
            },
        )
    return {
        "status":           "ok",
        "strategy":         STRATEGY,
        "active_model":     ACTIVE_MODEL,
        "cloud_api":        USING_CLOUD_API,
        "cloud_provider":   "Groq" if USING_CLOUD_API else None,
        "vision_model":     OLLAMA_MODEL,
        "text_model":       TEXT_MODEL,
        "ollama":           OLLAMA_HOST,
    }
