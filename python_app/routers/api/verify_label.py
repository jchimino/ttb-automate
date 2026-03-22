"""verify_label.py – TTB label compliance check

Backend engine is selected at request time:
  LOCAL_LLM_URL set   →  local Ollama service  (moondream/app)
  LOCAL_LLM_URL empty →  Anthropic Claude API  (cloud, requires key)
"""

import json
import re
from typing import Optional

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel
from supabase import create_client

from config import (
    ANTHROPIC_API_KEY,
    LOCAL_LLM_URL,
    SUPABASE_URL,
    SUPABASE_ANON_KEY,
    SUPABASE_SERVICE_ROLE_KEY,
)
from prompts import CLASSIFIER_PROMPT, get_bam_verifier_prompt
from local_llm_client import run_local_assessment

router = APIRouter()


# ── Pydantic models ──────────────────────────────────────────────────────────

class VerifyRequest(BaseModel):
    image_base64:    str
    product_details: Optional[str] = None
    commodity_type:  Optional[str] = None


class ComplianceCheck(BaseModel):
    field:       str
    status:      str
    label_value: Optional[str] = None
    text_value:  Optional[str] = None
    reason:      Optional[str] = None


class VerifyResponse(BaseModel):
    commodity_type:    str
    overall_status:    str
    compliance_score:  int
    checks:            list[ComplianceCheck]
    critical_failures: list[str]
    warnings:          list[str]
    abv_validation:    Optional[dict] = None
    engine:            Optional[str] = None   # "local (...)" | "anthropic"


# ── Auth helper ──────────────────────────────────────────────────────────────

_DEMO_TOKENS = {"demo-admin", "demo-staff", "demo-industry"}
_DEMO_IDS = {
    "demo-admin":    "demo-admin-001",
    "demo-staff":    "demo-staff-001",
    "demo-industry": "demo-industry-001",
}


def _get_user_id(authorization: Optional[str]) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization[7:]
    if token in _DEMO_TOKENS:
        return _DEMO_IDS[token]
    if not SUPABASE_SERVICE_ROLE_KEY:
        raise HTTPException(status_code=401, detail="Server not configured for authentication")
    try:
        admin = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
        resp = admin.auth.get_user(token)
        if not resp or not resp.user:
            raise HTTPException(status_code=401, detail="Unauthorized")
        return resp.user.id
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"Token validation failed: {exc}") from exc


# ── JSON extraction (Anthropic path) ────────────────────────────────────────

def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text.strip())
    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object found in response")
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start: i + 1])
    raise ValueError("Malformed JSON in response")


# ── Local LLM path ───────────────────────────────────────────────────────────

async def _verify_with_local_llm(image_b64: str) -> VerifyResponse:
    """Call the local Ollama /assess service and map the result."""
    import httpx
    try:
        result = await run_local_assessment(
            base_url=LOCAL_LLM_URL,
            image_b64=image_b64,
        )
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        raise HTTPException(
            status_code=502,
            detail=f"AI assessment service is still starting up — the models (~11 GB) may still be downloading. Please wait a moment and try again. ({exc})"
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Local LLM returned HTTP {exc.response.status_code}: {exc.response.text[:200]}"
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Local LLM assessment failed: {exc}"
        ) from exc

    checks = [
        ComplianceCheck(
            field=       f.get("field", "Unknown"),
            status=      f.get("status", "FAIL"),
            label_value= f.get("label_value"),
            text_value=  f.get("expected_value"),
            reason=      f.get("reason"),
        )
        for f in result.get("findings", [])
    ]

    return VerifyResponse(
        commodity_type=    result["commodity_type"],
        overall_status=    result["overall_status"],
        compliance_score=  result["compliance_score"],
        checks=            checks,
        critical_failures= result["critical_failures"],
        warnings=          result["warnings"],
        abv_validation=    result.get("abv_validation"),
        engine=f"local ({result.get('_local_strategy', '?')}/{result.get('_local_model', '?')})",
    )


# ── Anthropic path ───────────────────────────────────────────────────────────

async def _verify_with_anthropic(
    image_b64: str,
    commodity_type: Optional[str],
    user_id: str,
) -> VerifyResponse:
    """Original two-step Claude flow: classify commodity → BAM verify."""
    try:
        import anthropic as _anthropic
    except ImportError:
        raise HTTPException(status_code=500, detail="anthropic package not installed")

    if not ANTHROPIC_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="No ANTHROPIC_API_KEY configured and LOCAL_LLM_URL is not set. "
                   "Set LOCAL_LLM_URL in .env to use the local Ollama service."
        )

    client = _anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    # Step 1: classify commodity type
    if not commodity_type:
        try:
            cls_resp = client.messages.create(
                model="claude-opus-4-5",
                max_tokens=512,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type":       "base64",
                                "media_type": "image/jpeg",
                                "data":       image_b64,
                            },
                        },
                        {"type": "text", "text": CLASSIFIER_PROMPT},
                    ],
                }],
            )
            cls_json = _extract_json(cls_resp.content[0].text)
            commodity_type = cls_json.get("commodity_type", "Spirits")
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Classification step failed: {exc}") from exc

    # Step 2: fetch spirit_classes from DB (non-fatal)
    spirit_classes = []
    if SUPABASE_SERVICE_ROLE_KEY and user_id not in _DEMO_IDS.values():
        try:
            admin = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
            sc_resp = admin.table("spirit_classes").select("*").execute()
            spirit_classes = sc_resp.data or []
        except Exception:
            spirit_classes = []

    # Step 3: BAM compliance verification
    bam_prompt = get_bam_verifier_prompt(commodity_type, spirit_classes)
    try:
        ver_resp = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=2500,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type":       "base64",
                            "media_type": "image/jpeg",
                            "data":       image_b64,
                        },
                    },
                    {"type": "text", "text": bam_prompt},
                ],
            }],
        )
        ver_json = _extract_json(ver_resp.content[0].text)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Claude returned invalid JSON: {exc}") from exc
    except _anthropic.APIError as exc:
        raise HTTPException(status_code=500, detail=f"Anthropic API error: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Verification step failed: {exc}") from exc

    checks = [
        ComplianceCheck(
            field=       f.get("field", "Unknown"),
            status=      f.get("status", "FAIL"),
            label_value= f.get("label_value"),
            text_value=  f.get("expected_value"),
            reason=      f.get("reason"),
        )
        for f in ver_json.get("findings", [])
    ]

    overall_raw = ver_json.get("overall_status", "")
    overall_status = "PASS" if overall_raw.upper() in ("COMPLIANT", "PASS") else "FAIL"

    return VerifyResponse(
        commodity_type=    commodity_type,
        overall_status=    overall_status,
        compliance_score=  int(ver_json.get("compliance_score", 0)),
        checks=            checks,
        critical_failures= ver_json.get("critical_failures", []),
        warnings=          ver_json.get("warnings", []),
        abv_validation=    ver_json.get("abv_validation"),
        engine=            "anthropic",
    )


# ── Endpoint ─────────────────────────────────────────────────────────────────

@router.post("/verify-label", response_model=VerifyResponse)
async def verify_label(
    request: VerifyRequest,
    authorization: Optional[str] = Header(None),
):
    user_id = _get_user_id(authorization)   # raises 401 if invalid

    if not request.image_base64:
        raise HTTPException(status_code=400, detail="No image provided")

    # Strip data-URI prefix if present
    image_b64 = request.image_base64
    if "," in image_b64:
        image_b64 = image_b64.split(",", 1)[1]

    # Route: local Ollama when configured, fall back to Anthropic
    if LOCAL_LLM_URL:
        return await _verify_with_local_llm(image_b64)
    else:
        return await _verify_with_anthropic(
            image_b64=image_b64,
            commodity_type=request.commodity_type,
            user_id=user_id,
        )
