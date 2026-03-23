"""
TTB Assessment Orchestrator
============================
Strategy priority (evaluated at startup):

  1. VISION    — llava:7b available in Ollama (GPU present)
                 llava reads the image directly; Tesseract OCR runs in
                 parallel as supplementary confirmation text.

  2. ANTHROPIC — No GPU / llava unavailable, but ANTHROPIC_API_KEY is set.
                 OCR text + images sent to claude-haiku-4-5.
                 Intended as a secondary/demo option only.

  3. RECONCILE — No GPU, no API key.
                 OCR text sent to local qwen2.5:7b via Ollama.

Local inference (strategy 1 or 3) is the production path.
Anthropic (strategy 2) is an optional fallback for environments
without a GPU — set ANTHROPIC_API_KEY in the environment to enable it.
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
from cfr_loader import load_cfr_chunks, retrieve_relevant_chunks

app = FastAPI(title="TTB Label Compliance API")

OLLAMA_HOST       = os.getenv("OLLAMA_HOST",  "http://ollama:11434")
OLLAMA_MODEL      = os.getenv("OLLAMA_MODEL", "llava:7b")
TEXT_MODEL        = os.getenv("TEXT_MODEL",   "qwen2.5:7b")
OCR_HOST          = os.getenv("OCR_HOST",     "http://ocr:8001")
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
RAG_READY       = False   # True once CFR chunks are loaded into pgvector


def _is_vision_model(name: str) -> bool:
    return any(v in name.lower() for v in _VISION_MODEL_NAMES)


# ── Startup ───────────────────────────────────────────────────────────────────
#
# IMPORTANT: detect_strategy runs as a background task (not inline in the
# on_event handler) so FastAPI can start accepting connections — and /health
# can respond with status="loading" — immediately. Without this decoupling,
# Docker's healthcheck times out while the app is still probing Ollama.

async def _init_strategy():
    """Detect GPU/model availability and warm up the active model."""
    global STRATEGY, ACTIVE_MODEL, MODEL_READY, USING_CLOUD_API, RAG_READY

    # ── Step 1: probe Ollama for the vision model (up to 5 min) ──────────────
    vision_ready = False
    attempt = 0
    print(f"[startup] Probing Ollama for {OLLAMA_MODEL} ...")
    while attempt < 30:   # 30 × 10 s = 5 min max
        attempt += 1
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                r = await client.get(f"{OLLAMA_HOST}/api/tags")
                if r.is_success:
                    models = [m["name"] for m in r.json().get("models", [])]
                    base = OLLAMA_MODEL.split(":")[0].lower()
                    vision_ready = any(base in m.lower() for m in models)
                    if vision_ready:
                        print(f"[startup] {OLLAMA_MODEL} found in Ollama model list.")
                        break
        except Exception:
            pass
        print(f"[startup] Waiting for {OLLAMA_MODEL} ... attempt {attempt}/30")
        await asyncio.sleep(10)

    # ── Step 2: choose strategy ───────────────────────────────────────────────
    if vision_ready and _is_vision_model(OLLAMA_MODEL):
        STRATEGY, ACTIVE_MODEL, USING_CLOUD_API = "vision", OLLAMA_MODEL, False
        print(f"[startup] Strategy: VISION ({OLLAMA_MODEL}) — llava reads images; OCR used as confirmation")
    elif ANTHROPIC_API_KEY:
        STRATEGY, ACTIVE_MODEL, USING_CLOUD_API = "reconcile", ANTHROPIC_MODEL, True
        print(f"[startup] Strategy: ANTHROPIC ({ANTHROPIC_MODEL}) — no GPU, API key present")
    else:
        STRATEGY, ACTIVE_MODEL, USING_CLOUD_API = "reconcile", TEXT_MODEL, False
        print(f"[startup] Strategy: RECONCILE via local Ollama ({TEXT_MODEL}) — no GPU, no API key")

    print(f"[startup] Active model: {ACTIVE_MODEL} | Cloud: {USING_CLOUD_API}")

    # ── Step 3: cloud path — mark ready immediately, skip warm-up ────────────
    if USING_CLOUD_API:
        MODEL_READY = True
        try:
            RAG_READY = await load_cfr_chunks(force=False)
        except Exception as e:
            print(f"[startup] CFR RAG load failed (non-fatal): {e}")
        return

    # ── Step 4: warm up the local Ollama model ────────────────────────────────
    print(f"[startup] Warming up {ACTIVE_MODEL} ...")
    warm_attempt = 0
    while True:
        warm_attempt += 1
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                r = await client.post(
                    f"{OLLAMA_HOST}/api/generate",
                    json={"model": ACTIVE_MODEL, "prompt": "hi", "stream": False,
                          "options": {"num_predict": 1}},
                )
                if r.is_success:
                    MODEL_READY = True
                    print(f"[startup] {ACTIVE_MODEL} ready (warm-up attempt {warm_attempt})")
                    break
        except Exception:
            pass
        print(f"[startup] Warm-up attempt {warm_attempt} failed — retrying in 10 s ...")
        await asyncio.sleep(10)

    # ── Step 5: load CFR corpus into pgvector for RAG ─────────────────────────
    try:
        RAG_READY = await load_cfr_chunks(force=False)
    except Exception as e:
        print(f"[startup] CFR RAG load failed (non-fatal): {e}")


@app.on_event("startup")
async def detect_strategy():
    """
    Fire _init_strategy() as a background task so the HTTP server starts
    immediately and /health can respond (status='loading') while the slow
    Ollama probe and model warm-up run in the background.
    """
    asyncio.create_task(_init_strategy())


# ── Database ──────────────────────────────────────────────────────────────────
def log_decision(result: AssessmentResult, strategy: str, raw: str):
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO assessments
              (submission_id, decision, brand_name, model, strategy,
               fields_json, reasoning, raw_response, assessed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            result.submission_id, result.decision, result.brand_name,
            result.model, strategy,
            json.dumps([f.dict() for f in result.fields]),
            result.reasoning, raw, datetime.utcnow(),
        ))
        conn.commit(); cur.close(); conn.close()
    except Exception as e:
        print(f"[audit] DB write failed: {e}")


# ── Helpers ───────────────────────────────────────────────────────────────────
def _resize_image(img_bytes: bytes, max_px: int = 320) -> bytes:
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    w, h = img.size
    if max(w, h) > max_px:
        scale = max_px / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


async def call_anthropic(prompt: str) -> str:
    """POST to Anthropic Messages API. Secondary option when no GPU is available."""
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
        if not r.is_success:
            raise RuntimeError(f"Anthropic {r.status_code}: {r.text[:300]}")
        return r.json()["content"][0]["text"]


async def call_ollama(prompt: str, images: list[bytes], model: str) -> str:
    resized = [_resize_image(img) for img in images] if (images and _is_vision_model(model)) else []
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.1,
            "num_predict": 800,
            "num_ctx": 2048,     # limit context window to reduce VRAM usage
        },
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

def _format_rag_context(chunks: list[dict]) -> str:
    """Format retrieved CFR chunks as a structured context block for injection into the prompt."""
    if not chunks:
        return ""
    lines = ["\n═══════════════════════════════════════════════════════"]
    lines.append("RETRIEVED REGULATORY CONTEXT (27 CFR — live from database)")
    lines.append("Use these authoritative sections to evaluate compliance.")
    lines.append("═══════════════════════════════════════════════════════")
    for chunk in chunks:
        lines.append(f"\n[{chunk['section']} — {chunk['topic']}]")
        lines.append(chunk['chunk_text'])
    lines.append("═══════════════════════════════════════════════════════\n")
    return "\n".join(lines)


async def run_vision(label_bytes, form_bytes, submission_id):
    """
    Primary path: llava:7b reads the label image directly.
    Tesseract OCR runs in parallel and its output is appended as supplementary
    confirmation text. llava's visual interpretation takes precedence.
    """
    all_images = ([form_bytes] if form_bytes else []) + label_bytes

    ocr_text = ""
    try:
        ocr_data = await asyncio.wait_for(call_ocr(label_bytes), timeout=30.0)
        ocr_text = "\n\n".join(
            f"--- Image {p['index'] + 1} ---\n{p['text']}"
            for p in ocr_data.get("pages", [])
        )
        print(f"[vision] OCR confirmation available — {len(ocr_text)} chars")
    except Exception as e:
        print(f"[vision] OCR confirmation unavailable (non-fatal): {e}")

    # RAG: retrieve relevant CFR sections for this submission
    rag_context = ""
    if RAG_READY:
        rag_chunks = await retrieve_relevant_chunks(
            query="label compliance class type health warning government",
            top_k=4,
        )
        rag_context = _format_rag_context(rag_chunks)
        if rag_context:
            print(f"[vision] RAG: injected {len(rag_chunks)} CFR chunks into prompt")

    prompt = build_prompt_vision(
        n_labels=len(label_bytes),
        has_form=form_bytes is not None,
        submission_id=submission_id,
        ocr_supplement=ocr_text,
        rag_context=rag_context,
    )

    raw = await call_ollama(prompt, all_images, model=OLLAMA_MODEL)
    result = AssessmentResult.from_llm_response(raw, submission_id, OLLAMA_MODEL).post_process()
    return result, raw


async def run_reconcile(label_bytes, form_bytes, submission_id):
    """
    Secondary/fallback path: Tesseract OCR extracts text, then either
    Anthropic (if API key set) or local qwen2.5:7b evaluates compliance.
    """
    all_images = ([form_bytes] if form_bytes else []) + label_bytes
    ocr_data = await call_ocr(all_images)
    ocr_text = "\n\n".join(
        f"--- Image {p['index'] + 1} ---\n{p['text']}"
        for p in ocr_data.get("pages", [])
    )
    print(f"[reconcile] OCR complete — {len(ocr_text)} chars")

    # RAG: retrieve relevant CFR sections for this submission
    rag_context = ""
    if RAG_READY:
        rag_chunks = await retrieve_relevant_chunks(
            query=f"label compliance class type health warning government",
            top_k=4,
        )
        rag_context = _format_rag_context(rag_chunks)
        if rag_context:
            print(f"[reconcile] RAG: injected {len(rag_chunks)} CFR chunks into prompt")

    prompt = build_prompt_ocr(
        ocr_text=ocr_text,
        n_labels=len(label_bytes),
        has_form=form_bytes is not None,
        submission_id=submission_id,
        rag_context=rag_context,
    )

    if USING_CLOUD_API:
        print(f"[reconcile] → Anthropic ({ANTHROPIC_MODEL})")
        raw = await call_anthropic(prompt)
        result = AssessmentResult.from_llm_response(raw, submission_id, ANTHROPIC_MODEL).post_process()
    else:
        print(f"[reconcile] → Ollama ({TEXT_MODEL})")
        raw = await call_ollama(prompt, all_images, model=TEXT_MODEL)
        result = AssessmentResult.from_llm_response(raw, submission_id, TEXT_MODEL).post_process()

    if not ocr_text.strip() and result.decision == "APPROVE":
        result.decision = "REVIEW"
        result.reasoning = "OCR extracted no text — recommend human verification. " + (result.reasoning or "")

    return result, raw


# ── Endpoints ─────────────────────────────────────────────────────────────────
@app.post("/assess")
async def assess(
    label_images: list[UploadFile] = File(...),
    form_image: Optional[UploadFile] = File(None),
    submission_id: Optional[str] = Form(None),
):
    if not submission_id:
        submission_id = f"SUB-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"

    label_bytes = [await img.read() for img in label_images]
    form_bytes  = await form_image.read() if form_image else None

    try:
        run = run_vision if STRATEGY == "vision" else run_reconcile
        result, raw = await run(label_bytes, form_bytes, submission_id)
    except Exception as exc:
        print(f"[assess] {STRATEGY} failed: {exc!r}\n{traceback.format_exc()} — falling back to reconcile")
        try:
            result, raw = await run_reconcile(label_bytes, form_bytes, submission_id)
        except Exception as exc2:
            from fastapi import HTTPException
            raise HTTPException(status_code=500, detail=f"Assessment failed: {type(exc2).__name__}: {exc2}") from exc2

    log_decision(result, STRATEGY, raw)
    return JSONResponse(content={
        **result.dict(),
        "strategy": STRATEGY,
        "active_model": ACTIVE_MODEL,
        "cloud_api": USING_CLOUD_API,
        "cloud_provider": "Anthropic" if USING_CLOUD_API else None,
    })


@app.get("/health")
async def health():
    if not MODEL_READY:
        return JSONResponse(status_code=503, content={
            "status": "loading",
            "strategy": STRATEGY,
            "active_model": ACTIVE_MODEL,
            "cloud_api": USING_CLOUD_API,
        })
    return {
        "status": "ok",
        "strategy": STRATEGY,
        "active_model": ACTIVE_MODEL,
        "cloud_api": USING_CLOUD_API,
        "cloud_provider": "Anthropic" if USING_CLOUD_API else None,
        "vision_model": OLLAMA_MODEL,
        "text_model": TEXT_MODEL,
        "rag_ready": RAG_READY,
    }
