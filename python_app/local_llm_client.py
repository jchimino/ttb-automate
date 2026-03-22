"""
local_llm_client.py
────────────────────────────────────────────────────────────────────────────
Adapter between TTB Automate and the local Ollama assessment service.

Flow
────
  1. POST /assess on assess:8000  → raw decision + field results
  2. _classify_commodity()        → llava:7b classifies commodity via /api/generate
  3. _validate_abv()              → cross-checks declared ABV vs CFR tolerances
  4. _calculate_score()           → computes score from field results (no hardcoding)
  5. cfr_reference                → appended to every finding

Output schema is ComplianceCheck-compatible (same shape as the Anthropic path).
"""

from __future__ import annotations

import base64
import io
import json
import re
import uuid
from datetime import datetime
from typing import Optional

import httpx

from config import OLLAMA_HOST, OLLAMA_MODEL
from prompts import CLASSIFIER_PROMPT

HTTP_TIMEOUT     = 300.0   # vision/reconcile can be slow on CPU
CLASSIFY_TIMEOUT = 60.0    # classifier call is a single image → fast


# ── CFR Rules by commodity type ───────────────────────────────────────────────

_CFR_RULES: dict[str, dict] = {
    "Spirits": {
        "cfr_part":        "5",
        "mandatory_fields": {
            "brand_name", "class_type", "alcohol_content",
            "net_contents", "bottler_info", "health_warning",
        },
        "optional_fields": {"country_of_origin", "age_statement"},
        "abv_tolerance":   0.3,   # ±0.3% per 27 CFR 5.37
        "abv_range":       (20.0, 95.0),
        "field_cfr_refs": {
            "brand_name":      "27 CFR 5.34",
            "class_type":      "27 CFR 5.35",
            "alcohol_content": "27 CFR 5.37",
            "net_contents":    "27 CFR 5.38",
            "bottler_info":    "27 CFR 5.36",
            "health_warning":  "27 CFR Part 16",
        },
    },
    "Wine": {
        "cfr_part":        "4",
        "mandatory_fields": {
            "brand_name", "class_type", "alcohol_content",
            "net_contents", "bottler_info", "health_warning",
        },
        "optional_fields": {"appellation", "vintage", "sulfites"},
        "abv_tolerance":   0.3,   # ±0.3% per 27 CFR 4.36
        "abv_range":       (7.0, 24.0),
        "field_cfr_refs": {
            "brand_name":      "27 CFR 4.33",
            "class_type":      "27 CFR 4.34",
            "alcohol_content": "27 CFR 4.36",
            "net_contents":    "27 CFR 4.37",
            "bottler_info":    "27 CFR 4.35",
            "health_warning":  "27 CFR Part 16",
        },
    },
    "Malt": {
        "cfr_part":        "7",
        # alcohol_content is optional for malt beverages at federal level
        "mandatory_fields": {
            "brand_name", "class_type",
            "net_contents", "bottler_info", "health_warning",
        },
        "optional_fields": {"alcohol_content", "country_of_origin"},
        "abv_tolerance":   0.15,  # ±0.15% per 27 CFR 7.71 — stricter
        "abv_range":       (0.5, 15.0),
        "field_cfr_refs": {
            "brand_name":      "27 CFR 7.63",
            "class_type":      "27 CFR 7.64",
            "alcohol_content": "27 CFR 7.71",
            "net_contents":    "27 CFR 7.73",
            "bottler_info":    "27 CFR 7.65",
            "health_warning":  "27 CFR Part 16",
        },
    },
}

_DEFAULT_CFR = _CFR_RULES["Spirits"]   # safe fallback


# ── Keyword fallback for commodity classification ─────────────────────────────

_COMMODITY_KEYWORDS: dict[str, list[str]] = {
    "Spirits": ["spirit", "whisky", "whiskey", "vodka", "gin", "rum",
                "tequila", "bourbon", "brandy", "liqueur", "27 cfr part 5"],
    "Wine":    ["wine", "champagne", "sparkling", "cabernet", "merlot",
                "chardonnay", "27 cfr part 4"],
    "Malt":    ["beer", "ale", "lager", "stout", "porter", "malt",
                "ipa", "pilsner", "27 cfr part 7"],
}


def _keyword_commodity(fields: list[dict], reasoning: str | None) -> str:
    text = (reasoning or "").lower()
    for f in fields:
        text += " " + " ".join(str(v) for v in f.values() if v).lower()
    for ct, keywords in _COMMODITY_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return ct
    return "Spirits"


# ── Commodity classifier via llava:7b ────────────────────────────────────────

_CONF_MAP = {"HIGH": 0.9, "MEDIUM": 0.7, "LOW": 0.5}
_MD_FENCE  = re.compile(r"```(?:json)?\s*|\s*```")


async def _classify_commodity(image_b64: str) -> tuple[str, str, float]:
    """
    POST image to Ollama /api/generate with CLASSIFIER_PROMPT.

    Returns
    -------
    (commodity_type, detected_class, confidence_numeric)
    Falls back to ("Spirits", "Unknown", 0.0) on any error.
    """
    if not OLLAMA_HOST:
        return ("Spirits", "Unknown", 0.0)

    payload = {
        "model":  OLLAMA_MODEL,
        "prompt": CLASSIFIER_PROMPT,
        "images": [image_b64],
        "stream": False,
    }

    try:
        async with httpx.AsyncClient(timeout=CLASSIFY_TIMEOUT) as client:
            resp = await client.post(
                f"{OLLAMA_HOST}/api/generate",
                json=payload,
            )
            resp.raise_for_status()
            raw = resp.json().get("response", "")

        # Strip markdown fences that some models add
        raw = _MD_FENCE.sub("", raw).strip()
        parsed = json.loads(raw)

        ct = parsed.get("commodity_type", "Spirits")
        if ct not in _CFR_RULES:
            ct = "Spirits"

        conf     = _CONF_MAP.get(parsed.get("confidence", "LOW"), 0.5)
        detected = parsed.get("detected_class", "Unknown")

        return (ct, detected, conf)

    except Exception:
        return ("Spirits", "Unknown", 0.0)


# ── ABV extraction & validation ──────────────────────────────────────────────

_ABV_PATTERN = re.compile(r"(\d{1,3}(?:\.\d{1,2})?)\s*%")


def _extract_abv(text: str | None) -> float | None:
    if not text:
        return None
    m = _ABV_PATTERN.search(str(text))
    return float(m.group(1)) if m else None


def _validate_abv(
    fields: list[dict],
    commodity_type: str,
    detected_class: str,
) -> dict:
    """
    Cross-check the declared ABV against commodity-specific CFR tolerances.
    Returns an abv_validation dict compatible with the Anthropic path.
    """
    rules        = _CFR_RULES.get(commodity_type, _DEFAULT_CFR)
    tol          = rules["abv_tolerance"]
    abv_min, abv_max = rules["abv_range"]

    abv_field   = next(
        (f for f in fields if f.get("name", "").lower() == "alcohol_content"), None
    )
    found_text   = (abv_field or {}).get("found_on_label")
    detected_abv = _extract_abv(found_text)

    if detected_abv is None:
        status = "NOT_APPLICABLE"
    elif not (abv_min - tol <= detected_abv <= abv_max + tol):
        status = "FAIL"
    else:
        status = "PASS"

    return {
        "detected_abv":   detected_abv,
        "class_detected": detected_class,
        "min_required":   abv_min,
        "max_allowed":    abv_max,
        "tolerance":      tol,
        "status":         status,
    }


# ── Score calculation ─────────────────────────────────────────────────────────

def _calculate_score(
    findings: list[dict],
    critical_failures: list[str],
    warnings: list[str],
    abv_status: str,
    commodity_type: str,
) -> tuple[int, str]:
    """
    Compute compliance score from field results.
    Replaces the old hardcoded _DECISION_MAP.

    Deductions
    ----------
    health_warning FAIL    → -25  (ABLA — most critical)
    other mandatory FAIL   → -20 each
    non-mandatory FAIL     → -10 each
    any WARNING            → -5 each
    abv_validation FAIL    → -15

    Thresholds: PASS ≥ 80, WARNING 50-79, FAIL < 50
    Override:   FAIL if critical_failures is non-empty
    """
    rules     = _CFR_RULES.get(commodity_type, _DEFAULT_CFR)
    mandatory = rules["mandatory_fields"]

    score = 100

    for f in findings:
        name   = f.get("field", "").lower().replace(" ", "_")
        status = f.get("status", "WARNING")

        if status == "FAIL":
            if name == "health_warning":
                score -= 25
            elif name in mandatory:
                score -= 20
            else:
                score -= 10
        elif status == "WARNING":
            score -= 5

    if abv_status == "FAIL":
        score -= 15

    score = max(0, score)

    if critical_failures or score < 50:
        overall = "FAIL"
    elif score < 80:
        overall = "WARNING"
    else:
        overall = "PASS"

    return score, overall


# ── Field status mapper ───────────────────────────────────────────────────────

_FIELD_STATUS_MAP = {
    "PASS":   "PASS",
    "REVIEW": "WARNING",
    "FAIL":   "FAIL",
}


# ── Main entry point ──────────────────────────────────────────────────────────

async def run_local_assessment(
    base_url: str,
    image_b64: str,
    submission_id: Optional[str] = None,
) -> dict:
    """
    Call POST /assess on the local Ollama assessment service, then enrich with
    commodity classification, CFR-specific field references, calculated score,
    and ABV validation.

    Parameters
    ----------
    base_url      : e.g. "http://assess:8000"
    image_b64     : base64-encoded image (data-URI prefix already stripped)
    submission_id : optional, auto-generated if absent

    Returns
    -------
    Dict matching the shape that verify_label.py consumes.
    """
    if not submission_id:
        submission_id = (
            f"TTB-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"
            f"-{uuid.uuid4().hex[:6].upper()}"
        )

    raw_bytes = base64.b64decode(image_b64)

    # ── 1. Call the assessment service ────────────────────────────────────────
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.post(
            f"{base_url.rstrip('/')}/assess",
            files={"label_images": ("label.jpg", io.BytesIO(raw_bytes), "image/jpeg")},
            data={"submission_id": submission_id},
        )
        resp.raise_for_status()
        data = resp.json()

    decision  = (data.get("decision") or "REVIEW").upper()
    fields    = data.get("fields") or []
    reasoning = data.get("reasoning") or ""

    # ── 2. Classify commodity type from returned data (no extra LLM call) ───────
    # The assess service already ran vision analysis — use keyword heuristic on
    # the fields + reasoning it returned instead of making a second llava call.
    commodity_type  = _keyword_commodity(fields, reasoning)
    detected_class  = "keyword"
    classifier_conf = 0.7

    rules          = _CFR_RULES.get(commodity_type, _DEFAULT_CFR)
    field_cfr_refs = rules["field_cfr_refs"]

    # ── 3. Map field results → ComplianceCheck-compatible dicts ──────────────
    findings:         list[dict] = []
    critical_failures: list[str] = []
    warnings:          list[str] = []

    for f in fields:
        name       = f.get("name", "Unknown Field")
        raw_status = (f.get("status") or "REVIEW").upper()
        status     = _FIELD_STATUS_MAP.get(raw_status, "WARNING")
        note       = f.get("note") or ""
        cfr_ref    = field_cfr_refs.get(
            name.lower().replace(" ", "_"),
            f"27 CFR Part {rules['cfr_part']}",
        )

        findings.append({
            "field":          name,
            "status":         status,
            "label_value":    f.get("found_on_label"),
            "expected_value": f.get("reference_value"),
            "reason":         note,
            "cfr_reference":  cfr_ref,
        })

        if status == "FAIL":
            critical_failures.append(f"{name}: {note}" if note else name)
        elif status == "WARNING":
            warnings.append(f"{name}: {note}" if note else name)

    # Synthesise a single finding if the service returned none
    if not findings:
        synth_status = (
            "FAIL"    if decision == "DENY"
            else "WARNING" if decision == "REVIEW"
            else "PASS"
        )
        findings.append({
            "field":          "Overall Assessment",
            "status":         synth_status,
            "label_value":    data.get("brand_name"),
            "expected_value": "TTB-compliant label",
            "reason":         reasoning[:400] if reasoning else f"Decision: {decision}",
            "cfr_reference":  f"27 CFR Part {rules['cfr_part']}",
        })
        if synth_status == "FAIL":
            critical_failures.append(reasoning[:200] or "Label denied by local LLM")
        elif synth_status == "WARNING":
            warnings.append(reasoning[:200] or "Label flagged for review by local LLM")

    # ── 4. ABV validation ─────────────────────────────────────────────────────
    abv_validation = _validate_abv(fields, commodity_type, detected_class)
    abv_status     = abv_validation["status"]

    # ── 5. Calculate score ────────────────────────────────────────────────────
    compliance_score, overall_status = _calculate_score(
        findings, critical_failures, warnings, abv_status, commodity_type
    )

    return {
        "commodity_type":          commodity_type,
        "overall_status":          overall_status,
        "compliance_score":        compliance_score,
        "critical_failures":       critical_failures,
        "warnings":                warnings,
        "findings":                findings,
        "abv_validation":          abv_validation,
        # pass-through for logging / UI
        "_local_strategy":         data.get("strategy", "unknown"),
        "_local_model":            data.get("active_model", "unknown"),
        "_classifier_confidence":  classifier_conf,
        "_detected_class":         detected_class,
    }
