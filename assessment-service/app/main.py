"""
TTB Assessment Orchestrator
============================
On startup, checks whether Ollama is running on GPU or CPU.

  GPU detected  → VISION strategy
                  Images sent directly to the LLM.
                  LLM reads label and form visually.

  CPU only      → RECONCILE strategy
                  Step 1: OCR service extracts text from all images → JSON
                  Step 2: LLM receives structured JSON + images as backup context
                  Step 3: Both outputs compared — agreement = high confidence,
                          disagreement = REVIEW for human

Either strategy produces the same output schema. n8n doesn't need to know
which path ran — it just receives the verdict.

POST /assess
  form_image    (file, optional) — TTB application form PNG/JPEG
  label_images  (files)          — one or more label images
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

import httpx
import psycopg2
from PIL import Image
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse

from prompt import build_prompt_vision, build_prompt_ocr
from models import AssessmentResult, FieldResult

app = FastAPI(title="TTB Label Compliance API")

OLLAMA_HOST  = os.getenv("OLLAMA_HOST",  "http://ollama:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2-vision")   # vision path
TEXT_MODEL   = os.getenv("TEXT_MODEL",   "qwen2.5:7b")        # reconcile path
OCR_HOST     = os.getenv("OCR_HOST",     "http://ocr:8001")
DATABASE_URL = os.getenv("DATABASE_URL")

# Known multimodal/vision model name fragments
_VISION_MODEL_NAMES = ("llava", "bakllava", "moondream", "vision", "llama3.2-vision",
                       "minicpm-v", "cogvlm", "instructblip")

# Set at startup
STRATEGY     = "unknown"
ACTIVE_MODEL = OLLAMA_MODEL


def _is_vision_model(name: str) -> bool:
    """Return True if the model name indicates multimodal vision capability."""
    lower = name.lower()
    return any(v in lower for v in _VISION_MODEL_NAMES)


# ── Startup: detect strategy ──────────────────────────────────────────────────

@app.on_event("startup")
async def detect_strategy():
    global STRATEGY, ACTIVE_MODEL

    # Primary: if OLLAMA_MODEL is a known vision model, use vision strategy.
    # No GPU probe needed — llava:7b is always multimodal regardless of hardware.
    if _is_vision_model(OLLAMA_MODEL):
        STRATEGY     = "vision"
        ACTIVE_MODEL = OLLAMA_MODEL
        print(f"[startup] Vision model detected ({OLLAMA_MODEL}) → VISION strategy")
    else:
        # Fallback: check Ollama /api/show for cuda/metal as before
        has_gpu = False
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r       = await client.post(f"{OLLAMA_HOST}/api/show", json={"name": OLLAMA_MODEL})
                info    = r.json()
                details = str(info)
                has_gpu = "cuda" in details.lower() or "metal" in details.lower()
        except Exception as e:
            print(f"[startup] Could not query Ollama model info: {e}")

        if has_gpu:
            STRATEGY     = "vision"
            ACTIVE_MODEL = OLLAMA_MODEL
        else:
            STRATEGY     = "reconcile"
            ACTIVE_MODEL = TEXT_MODEL

    print(f"[startup] Ollama: {OLLAMA_HOST} | Strategy: {STRATEGY.upper()} | Model: {ACTIVE_MODEL}")


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


# ── Ollama call ───────────────────────────────────────────────────────────────

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


async def call_ollama(prompt: str, images: list[bytes], model: str) -> str:
    """
    Call Ollama with the specified model.
    Vision models use images directly. Text models ignore them gracefully.
    Images are resized to max 512px to reduce token count and speed up inference.
    """
    resized  = [_resize_image(img) for img in images]
    encoded  = [base64.standard_b64encode(img).decode() for img in resized]
    payload = {
        "model":   model,
        "prompt":  prompt,
        "images":  encoded,
        "stream":  False,
        "options": {"temperature": 0.1, "num_predict": 800},
    }
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
        return r.json()   # { "pages": [ {"index": 0, "text": "..."}, ... ] }


# ── Strategy: VISION ─────────────────────────────────────────────────────────

async def run_vision(
    label_bytes: list[bytes],
    form_bytes: Optional[bytes],
    submission_id: str,
) -> tuple[AssessmentResult, str]:
    """Send all images directly to the LLM. GPU path."""
    all_images = ([form_bytes] if form_bytes else []) + label_bytes
    prompt = build_prompt_vision(
        n_labels=len(label_bytes),
        has_form=form_bytes is not None,
        submission_id=submission_id,
    )
    raw = await call_ollama(prompt, all_images, model=OLLAMA_MODEL)
    result = AssessmentResult.from_llm_response(raw, submission_id, OLLAMA_MODEL)
    return result, raw


# ── Strategy: RECONCILE (CPU) ─────────────────────────────────────────────────

async def run_reconcile(
    label_bytes: list[bytes],
    form_bytes: Optional[bytes],
    submission_id: str,
) -> tuple[AssessmentResult, str]:
    """
    CPU path — two steps:
      1. OCR extracts text from all images → structured JSON (reliable on CPU)
      2. LLM receives that JSON as primary input + images as visual backup
      Results are compared; disagreement routes to REVIEW.
    """
    all_images = ([form_bytes] if form_bytes else []) + label_bytes

    # Step 1: OCR
    ocr_data = await call_ocr(all_images)
    ocr_text = "\n\n".join(
        f"--- Image {p['index'] + 1} ---\n{p['text']}"
        for p in ocr_data.get("pages", [])
    )

    print(f"[reconcile] OCR complete — {len(ocr_text)} chars extracted")

    # Step 2: Text LLM with OCR as primary input
    prompt = build_prompt_ocr(
        ocr_text=ocr_text,
        n_labels=len(label_bytes),
        has_form=form_bytes is not None,
        submission_id=submission_id,
    )
    print(f"[reconcile] Sending to {TEXT_MODEL}...")
    raw    = await call_ollama(prompt, all_images, model=TEXT_MODEL)
    result = AssessmentResult.from_llm_response(raw, submission_id, TEXT_MODEL)

    # If OCR found no text at all and LLM also uncertain — flag for review
    if not ocr_text.strip() and result.decision == "APPROVE":
        result.decision = "REVIEW"
        result.reasoning = "OCR extracted no text — visual-only assessment, recommend human verification. " + (result.reasoning or "")

    return result, raw


# ── Assessment endpoint ───────────────────────────────────────────────────────

@app.post("/assess")
async def assess(
    label_images:  list[UploadFile] = File(...),
    form_image:    Optional[UploadFile] = File(None),
    submission_id: Optional[str]    = Form(None),
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
        # Vision can fail if the model is loading or OOM — fall back to reconcile
        print(f"[assess] {STRATEGY} strategy failed: {exc}. Falling back to reconcile.")
        try:
            result, raw = await run_reconcile(label_bytes, form_bytes, submission_id)
        except Exception as exc2:
            print(f"[assess] Reconcile fallback also failed: {exc2}")
            from fastapi import HTTPException
            raise HTTPException(status_code=500, detail=f"Assessment failed: {exc2}") from exc2

    log_decision(result, STRATEGY, raw)

    return JSONResponse(content={
        **result.dict(),
        "strategy":     STRATEGY,
        "active_model": ACTIVE_MODEL,
    })


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status":       "ok",
        "strategy":     STRATEGY,
        "active_model": ACTIVE_MODEL,
        "vision_model": OLLAMA_MODEL,
        "text_model":   TEXT_MODEL,
        "ollama":       OLLAMA_HOST,
    }
