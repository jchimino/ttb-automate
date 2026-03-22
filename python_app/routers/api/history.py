"""Verification history API endpoints"""

from fastapi import APIRouter, HTTPException, Depends, Header
from pydantic import BaseModel
from typing import Optional, List, Any
from datetime import datetime, timezone
import uuid
from supabase import create_client

from config import SUPABASE_URL, SUPABASE_ANON_KEY

router = APIRouter()

# ── Demo mode ─────────────────────────────────────────────────────────────────
_DEMO_TOKENS = {
    "demo-admin":    "demo-admin-001",
    "demo-staff":    "demo-staff-001",
    "demo-industry": "demo-industry-001",
}

# In-memory verification history for demo sessions
_DEMO_HISTORY: dict[str, list] = {k: [] for k in _DEMO_TOKENS.values()}


def _is_demo(user_id: str) -> bool:
    return user_id.startswith("demo-")


async def verify_jwt_token(authorization: Optional[str] = Header(None)) -> tuple[str, any]:
    """Verify JWT token and return user ID and client.
    Accepts demo-<role> tokens for local testing."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")

    token = authorization.replace("Bearer ", "")

    # Demo mode bypass
    if token in _DEMO_TOKENS:
        return _DEMO_TOKENS[token], None

    supabase = create_client(SUPABASE_URL, SUPABASE_ANON_KEY, {"global": {"headers": {"Authorization": authorization}}})
    try:
        result = supabase.auth.get_user(token)
        if not result.user:
            raise HTTPException(status_code=401, detail="Unauthorized")
        return result.user.id, supabase
    except Exception:
        raise HTTPException(status_code=401, detail="Unauthorized")


class SaveHistoryRequest(BaseModel):
    image_thumbnail: Optional[str] = None   # base64 or URL thumbnail
    commodity_type:  Optional[str] = None
    overall_status:  str                    # "PASS" | "FAIL"
    compliance_score: Optional[int] = None
    checks:          List[Any] = []
    product_details: Optional[str] = None


@router.post("/verification-history")
async def save_verification_history(request: SaveHistoryRequest, auth: tuple = Depends(verify_jwt_token)):
    """Save a verification result to history"""
    user_id, supabase = auth

    record = {
        "id":               str(uuid.uuid4()),
        "user_id":          user_id,
        "overall_status":   request.overall_status,
        "commodity_type":   request.commodity_type,
        "compliance_score": request.compliance_score,
        "checks":           request.checks,
        "product_details":  request.product_details,
        "image_thumbnail":  request.image_thumbnail,
        "created_at":       datetime.now(timezone.utc).isoformat(),
    }

    if _is_demo(user_id):
        history = _DEMO_HISTORY.setdefault(user_id, [])
        history.insert(0, record)   # newest first
        # Keep last 50 entries to avoid memory bloat
        if len(history) > 50:
            _DEMO_HISTORY[user_id] = history[:50]
        return record

    try:
        result = supabase.table("verification_history").insert({
            "user_id":          user_id,
            "overall_status":   request.overall_status,
            "commodity_type":   request.commodity_type,
            "compliance_score": request.compliance_score,
            "checks":           request.checks,
            "product_details":  request.product_details,
            "image_thumbnail":  request.image_thumbnail,
        }).execute()
        return result.data[0] if result.data else record
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/verification-history")
async def get_verification_history(auth: tuple = Depends(verify_jwt_token)):
    """Get user's verification history"""
    user_id, supabase = auth

    if _is_demo(user_id):
        return {"history": _DEMO_HISTORY.get(user_id, [])}

    try:
        result = supabase.table("verification_history").select("*").eq("user_id", user_id).order("created_at", desc=True).execute()
        return {"history": result.data or []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/verification-history/{record_id}")
async def delete_history_record(record_id: str, auth: tuple = Depends(verify_jwt_token)):
    """Delete a verification history record"""
    user_id, supabase = auth

    if _is_demo(user_id):
        records = _DEMO_HISTORY.get(user_id, [])
        _DEMO_HISTORY[user_id] = [r for r in records if r.get("id") != record_id]
        return {"deleted": True}

    try:
        result = supabase.table("verification_history").delete().eq("id", record_id).eq("user_id", user_id).execute()
        return {"deleted": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/verification-history")
async def clear_all_history(auth: tuple = Depends(verify_jwt_token)):
    """Clear all verification history for user"""
    user_id, supabase = auth

    if _is_demo(user_id):
        _DEMO_HISTORY[user_id] = []
        return {"cleared": True}

    try:
        result = supabase.table("verification_history").delete().eq("user_id", user_id).execute()
        return {"cleared": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
