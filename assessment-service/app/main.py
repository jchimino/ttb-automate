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

OLLAMA_HOST  = os.getenv("OLLAMA_HOST",  "http://ollama:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llava:7b")
TEXT_MODEL   = os.getenv("TEXT_MODEL",   "qwen2.5:7b")
OCR_HOST     = os.getenv("OCR_HOST",     "http://ocr:8001")
DATABASE_URL = os.getenv("DATABASE_URL")

_VISION_MODEL_NAMES = (
    "llava", "bakllava", "moondream", "vision",
    "llama3.2-vision", "minicpm-v", "cogvlm", "instructblip",
)

STRATEGY     = "unknown"
ACTIVE_MODEL = OLLAMA_MODEL
MODEL_READY  = False


def _is_vision_model(name: str) -> bool:
    return any(v in name.lower() for v in _VISION_MODEL_NAMES)


async def _init_strategy():
    global STRATEGY, ACTIVE_MODEL, MODEL_READY

    vision_ready = False
    attempt = 0
    print(f"[startup] Probing Ollama for {OLLAMA_MODEL} ...")
    while attempt < 30:
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

    has_gpu = False
    if vision_ready:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.post(
                    f"{OLLAMA_HOST}/api/show",
                    json={"name": OLLAMA_MODEL},
                )
                info = str(r.json()).lower()
                has_gpu = "cuda" in info or "metal" in info or "rocm" in info
                print(f"[startup] GPU probe -> has_gpu={has_gpu}")
        except Exception as e:
            print(f"[startup] GPU probe failed (assuming CPU): {e}")

    if vision_ready and has_gpu and _is_vision_model(OLLAMA_MODEL):
        STRATEGY     = "vision"
        ACTIVE_MODEL = OLLAMA_MODEL
        print(f"[startup] Strategy: VISION ({OLLAMA_MODEL})")
    else:
        STRATEGY     = "reconcile"
        ACTIVE_MODEL = TEXT_MODEL
        reason = "no GPU" if (vision_ready and not has_gpu) else "llava not available"
        print(f"[startup] Strategy: RECONCILE ({TEXT_MODEL}) -- {reason}")

    print(f"[startup] Warming up {ACTIVE_MODEL} ...")
    warm_attempt = 0
    while True:
        warm_attempt += 1
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                r = await client.post(
                    f"{OLLAMA_HOST}/api/generate",
                    json={
                        "model": ACTIVE_MODEL,
                        "prompt": "hi",
                        "stream": False,
                        "options": {"num_predict": 1},
                    },
                )
                if r.is_success:
                    MODEL_READY = True
                    print(f"[startup] {ACTIVE_MODEL} ready (attempt {warm_attempt}).")
                    break
        except Exception:
            pass
        print(f"[startup] Warm-up attempt {warm_attempt} failed -- retrying in 10 s ...")
        await asyncio.sleep(10)


@app.on_event("startup")
async def detect_strategy():
    asyncio.create_task(_init_strategy())


def log_decision(result: AssessmentResult, strategy: str, raw: str):
    try:
        if not DATABASE_URL:
            return
        conn = psycopg2.connect(DATABASE_URL)
        cur  = conn.cursor()
        cur.execute(
            """
            INSERT INTO assessments
                (submission_id, decision, brand_name, model, strategy,
                 fields_json, reasoning, raw_response, assessed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                result.submission_id,
                result.decision,
                result.brand_name,
                result.model,
                strategy,
                json.dumps([f.dict() for f in result.fields]),
                result.reasoning,
                raw,
                datetime.utcnow(),
            ),
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[audit] DB write failed: {e}")


def _resize_image(img_bytes: bytes, max_px: int = 512) -> bytes:
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    w, h = img.size
    if max(w, h) > max_px:
        scale = max_px / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


async def call_ollama(prompt: str, images: list[bytes], model: str) -> str:
    is_vision = _is_vision_model(model)
    resized   = [_resize_image(img) for img in images] if (images and is_vision) else []
    encoded   = [base64.standard_b64encode(img).decode() for img in resized]
    payload: dict = {
        "model":  model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 800, "num_ctx": 2048},
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


async def call_ocr(images: list[bytes]) -> dict:
    files = [
        ("images", (f"img_{i}.png", img, "image/png"))
        for i, img in enumerate(images)
    ]
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(f"{OCR_HOST}/extract", files=files)
        r.raise_for_status()
    return r.json()


async def run_vision(
    label_bytes: list[bytes],
    form_bytes: Optional[bytes],
    submission_id: str,
) -> tuple[AssessmentResult, str]:
    all_images = ([form_bytes] if form_bytes else []) + label_bytes
    ocr_text = ""
    try:
        ocr_data = await asyncio.wait_for(call_ocr(label_bytes), timeout=30.0)
        ocr_text = "\n\n".join(
            f"--- Image {p['index'] + 1} ---\n{p['text']}"
            for p in ocr_data.get("pages", [])
        )
        print(f"[vision] OCR confirmation available -- {len(ocr_text)} chars")
    except Exception as e:
        print(f"[vision] OCR confirmation unavailable (non-fatal): {e}")
    prompt = build_prompt_vision(
        n_labels=len(label_bytes),
        has_form=form_bytes is not None,
        submission_id=submission_id,
        ocr_supplement=ocr_text,
    )
    raw    = await call_ollama(prompt, all_images, model=OLLAMA_MODEL)
    result = AssessmentResult.from_llm_response(raw, submission_id, OLLAMA_MODEL).post_process()
    return result, raw


async def run_reconcile(
    label_bytes: list[bytes],
    form_bytes: Optional[bytes],
    submission_id: str,
) -> tuple[AssessmentResult, str]:
    all_images = ([form_bytes] if form_bytes else []) + label_bytes
    ocr_data = await call_ocr(all_images)
    ocr_text = "\n\n".join(
        f"--- Image {p['index'] + 1} ---\n{p['text']}"
        for p in ocr_data.get("pages", [])
    )
    print(f"[reconcile] OCR complete -- {len(ocr_text)} chars extracted")
    prompt = build_prompt_ocr(
        ocr_text=ocr_text,
        n_labels=len(label_bytes),
        has_form=form_bytes is not None,
        submission_id=submission_id,
    )
    print(f"[reconcile] Sending to {TEXT_MODEL} ...")
    raw    = await call_ollama(prompt, all_images, model=TEXT_MODEL)
    result = AssessmentResult.from_llm_response(raw, submission_id, TEXT_MODEL).post_process()
    if not ocr_text.strip() and result.decision == "APPROVE":
        result.decision  = "REVIEW"
        result.reasoning = (
            "OCR extracted no text -- recommend human verification. "
            + (result.reasoning or "")
        )
    return result, raw


@app.post("/assess")
async def assess(
    label_images: list[UploadFile] = File(...),
    form_image:   Optional[UploadFile] = File(None),
    submission_id: Optional[str] = Form(None),
):
    if not submission_id:
        submission_id = (
            f"SUB-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"
            f"-{uuid.uuid4().hex[:6].upper()}"
        )
    label_bytes = [await img.read() for img in label_images]
    form_bytes  = await form_image.read() if form_image else None
    try:
        run = run_vision if STRATEGY == "vision" else run_reconcile
        result, raw = await run(label_bytes, form_bytes, submission_id)
    except Exception as exc:
        print(f"[assess] {STRATEGY} failed: {exc!r}\n{traceback.format_exc()} -- falling back")
        try:
            result, raw = await run_reconcile(label_bytes, form_bytes, submission_id)
        except Exception as exc2:
            from fastapi import HTTPException
            raise HTTPException(
                status_code=500,
                detail=f"Assessment failed: {type(exc2).__name__}: {exc2}",
            ) from exc2
    log_decision(result, STRATEGY, raw)
    return JSONResponse(content={
        **result.dict(),
        "strategy":     STRATEGY,
        "active_model": ACTIVE_MODEL,
    })


@app.get("/health")
async def health():
    if not MODEL_READY:
        return JSONResponse(
            status_code=503,
            content={
                "status":       "loading",
                "strategy":     STRATEGY,
                "active_model": ACTIVE_MODEL,
            },
        )
    return {
        "status":       "ok",
        "strategy":     STRATEGY,
        "active_model": ACTIVE_MODEL,
        "vision_model": OLLAMA_MODEL,
        "text_model":   TEXT_MODEL,
        "ollama":       OLLAMA_HOST,
    }
