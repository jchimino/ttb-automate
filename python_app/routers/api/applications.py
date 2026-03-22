"""Applications CRUD API endpoints"""

from fastapi import APIRouter, HTTPException, Depends, Header
from pydantic import BaseModel
from typing import Optional, List
from supabase import create_client
from datetime import datetime, timezone
import uuid

from config import SUPABASE_URL, SUPABASE_ANON_KEY

router = APIRouter()

# ── Demo mode ─────────────────────────────────────────────────────────────────
DEMO_USERS = {
    "demo-admin":    {"id": "demo-admin-001",    "email": "admin47@treasury.gov", "role": "admin"},
    "demo-staff":    {"id": "demo-staff-001",    "email": "john@ttb.gov",        "role": "staff"},
    "demo-industry": {"id": "demo-industry-001", "email": "user45@gmail.com",    "role": "industry"},
}

# Reverse lookup: email → demo token (used by sign-in form interception)
DEMO_EMAIL_TO_TOKEN = {v["email"]: k for k, v in DEMO_USERS.items()}

# In-memory store for demo applications (keyed by user_id → list of apps)
_DEMO_APPS: dict[str, list] = {
    "demo-industry-001": [
        {
            "id": "demo-app-001",
            "user_id": "demo-industry-001",
            "product_name": "Blue Ridge Bourbon",
            "brand_name": "Blue Ridge Distillery",
            "product_type": "Straight Bourbon Whiskey",
            "alcohol_content": "45% Alc./Vol.",
            "net_contents": "750 mL",
            "status": "pending_review",
            "created_at": "2026-03-01T10:00:00Z",
            "updated_at": "2026-03-05T14:30:00Z",
            "submitted_at": "2026-03-05T14:30:00Z",
            "rejection_reason": None,
            "label_url": "/static/samples/sample-bourbon.jpg",
        },
        {
            "id": "demo-app-002",
            "user_id": "demo-industry-001",
            "product_name": "Pacific Pale Ale",
            "brand_name": "Coastal Craft Brewing",
            "product_type": "Malt Beverage",
            "alcohol_content": "5.2% Alc./Vol.",
            "net_contents": "355 mL",
            "status": "approved",
            "created_at": "2026-02-15T09:00:00Z",
            "updated_at": "2026-02-28T11:00:00Z",
            "submitted_at": "2026-02-16T08:00:00Z",
            "rejection_reason": None,
            "label_url": "/static/samples/sample-wine.jpg",
        },
        {
            "id": "demo-app-003",
            "user_id": "demo-industry-001",
            "product_name": "Sonoma Chardonnay",
            "brand_name": "Valley View Winery",
            "product_type": "Table Wine",
            "alcohol_content": "13.5% Alc./Vol.",
            "net_contents": "750 mL",
            "status": "draft",
            "created_at": "2026-03-10T15:00:00Z",
            "updated_at": "2026-03-10T15:00:00Z",
            "submitted_at": None,
            "rejection_reason": None,
            "label_url": "/static/samples/sample-wine.jpg",
        },
    ],
    "demo-admin-001": [],
    "demo-staff-001": [],
}

# All apps visible to staff/admin
_ALL_DEMO_APPS = [app for apps in _DEMO_APPS.values() for app in apps]


def _is_demo(user_id: str) -> bool:
    return user_id.startswith("demo-")

# ── Auth helper ────────────────────────────────────────────────────────────────

async def verify_jwt_token(authorization: Optional[str] = Header(None)) -> tuple[str, any]:
    """Verify JWT token and return (user_id, supabase_client).
    Accepts demo-<role> tokens for local testing without a live Supabase instance."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")

    token = authorization.replace("Bearer ", "")

    # Demo mode bypass
    if token in DEMO_USERS:
        return DEMO_USERS[token]["id"], None

    supabase = create_client(SUPABASE_URL, SUPABASE_ANON_KEY, {"global": {"headers": {"Authorization": authorization}}})
    try:
        result = supabase.auth.get_user(token)
        if not result.user:
            raise HTTPException(status_code=401, detail="Unauthorized")
        return result.user.id, supabase
    except Exception:
        raise HTTPException(status_code=401, detail="Unauthorized")


# ── Pydantic models ────────────────────────────────────────────────────────────

class CreateApplicationRequest(BaseModel):
    product_name: str
    brand_name: str
    product_type: str
    alcohol_content: str
    net_contents: Optional[str] = None


class ApplicationResponse(BaseModel):
    id: str
    product_name: str
    brand_name: str
    product_type: str
    alcohol_content: str
    status: str
    created_at: str


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/applications", response_model=ApplicationResponse)
async def create_application(
    request: CreateApplicationRequest,
    auth: tuple = Depends(verify_jwt_token),
):
    """Create a new application"""
    user_id, supabase = auth

    if _is_demo(user_id):
        new_app = {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "product_name": request.product_name,
            "brand_name": request.brand_name,
            "product_type": request.product_type,
            "alcohol_content": request.alcohol_content,
            "net_contents": request.net_contents,
            "status": "draft",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "submitted_at": None,
            "rejection_reason": None,
        }
        _DEMO_APPS.setdefault(user_id, []).append(new_app)
        _ALL_DEMO_APPS.append(new_app)
        return ApplicationResponse(**new_app)

    try:
        result = supabase.table("applications").insert({
            "user_id": user_id,
            "product_name": request.product_name,
            "brand_name": request.brand_name,
            "product_type": request.product_type,
            "alcohol_content": request.alcohol_content,
            "net_contents": request.net_contents,
            "status": "draft",
        }).execute()

        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to create application")

        app = result.data[0]
        return ApplicationResponse(**app)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/applications")
async def list_applications(auth: tuple = Depends(verify_jwt_token)):
    """List applications (own apps for industry; all for staff/admin)"""
    user_id, supabase = auth

    if _is_demo(user_id):
        # staff/admin see all; industry sees own
        demo_info = next((v for v in DEMO_USERS.values() if v["id"] == user_id), {})
        role = demo_info.get("role", "industry")
        if role in ("staff", "admin"):
            return {"applications": _ALL_DEMO_APPS}
        return {"applications": _DEMO_APPS.get(user_id, [])}

    try:
        result = supabase.table("applications").select("*").eq("user_id", user_id).execute()
        return {"applications": result.data or []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/applications/{app_id}")
async def get_application(app_id: str, auth: tuple = Depends(verify_jwt_token)):
    """Get application details"""
    user_id, supabase = auth

    if _is_demo(user_id):
        app = next((a for a in _ALL_DEMO_APPS if a["id"] == app_id), None)
        if not app:
            raise HTTPException(status_code=404, detail="Application not found")
        return app

    try:
        result = supabase.table("applications").select("*").eq("id", app_id).maybeSingle().execute()
        if not result.data:
            raise HTTPException(status_code=404, detail="Application not found")
        return result.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/applications/{app_id}")
async def update_application(app_id: str, request: CreateApplicationRequest, auth: tuple = Depends(verify_jwt_token)):
    """Update application"""
    user_id, supabase = auth

    if _is_demo(user_id):
        app = next((a for a in _ALL_DEMO_APPS if a["id"] == app_id and a["user_id"] == user_id), None)
        if not app:
            raise HTTPException(status_code=404, detail="Application not found")
        app.update({
            "product_name": request.product_name,
            "brand_name": request.brand_name,
            "product_type": request.product_type,
            "alcohol_content": request.alcohol_content,
            "net_contents": request.net_contents,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        return app

    try:
        result = supabase.table("applications").update({
            "product_name": request.product_name,
            "brand_name": request.brand_name,
            "product_type": request.product_type,
            "alcohol_content": request.alcohol_content,
            "net_contents": request.net_contents,
        }).eq("id", app_id).eq("user_id", user_id).execute()

        if not result.data:
            raise HTTPException(status_code=404, detail="Application not found")
        return result.data[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/applications/{app_id}")
async def delete_application(app_id: str, auth: tuple = Depends(verify_jwt_token)):
    """Delete application"""
    user_id, supabase = auth

    if _is_demo(user_id):
        user_apps = _DEMO_APPS.get(user_id, [])
        _DEMO_APPS[user_id] = [a for a in user_apps if a["id"] != app_id]
        idx = next((i for i, a in enumerate(_ALL_DEMO_APPS) if a["id"] == app_id), None)
        if idx is not None:
            _ALL_DEMO_APPS.pop(idx)
        return {"deleted": True}

    try:
        supabase.table("applications").delete().eq("id", app_id).eq("user_id", user_id).execute()
        return {"deleted": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class SubmitApplicationRequest(BaseModel):
    pass  # no body needed

class ReviewApplicationRequest(BaseModel):
    action: str  # "approve", "reject", "return"
    notes: Optional[str] = None
    rejection_reason: Optional[str] = None

@router.put("/applications/{app_id}/submit")
async def submit_application(app_id: str, auth: tuple = Depends(verify_jwt_token)):
    """Submit draft application for review"""
    user_id, supabase = auth

    if _is_demo(user_id):
        app = next((a for a in _ALL_DEMO_APPS if a["id"] == app_id and a["user_id"] == user_id), None)
        if not app:
            raise HTTPException(status_code=404, detail="Application not found")
        if app["status"] != "draft":
            raise HTTPException(status_code=400, detail="Only draft applications can be submitted")
        app["status"] = "pending_review"
        app["submitted_at"] = datetime.now(timezone.utc).isoformat()
        app["updated_at"] = datetime.now(timezone.utc).isoformat()
        return app

    try:
        result = supabase.table("applications").update({
            "status": "pending_review",
            "submitted_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", app_id).eq("user_id", user_id).execute()
        if not result.data:
            raise HTTPException(status_code=404, detail="Application not found")
        return result.data[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/applications/{app_id}/review")
async def review_application(app_id: str, request: ReviewApplicationRequest, auth: tuple = Depends(verify_jwt_token)):
    """Staff/admin: approve, reject, or return an application"""
    user_id, supabase = auth

    # Determine role
    if _is_demo(user_id):
        demo_info = next((v for v in DEMO_USERS.values() if v["id"] == user_id), {})
        role = demo_info.get("role", "industry")
    else:
        role = "staff"  # real Supabase would check user_roles table

    if role not in ("staff", "admin"):
        raise HTTPException(status_code=403, detail="Forbidden")

    if request.action not in ("approve", "reject", "return"):
        raise HTTPException(status_code=400, detail="action must be approve, reject, or return")

    status_map = {"approve": "approved", "reject": "rejected", "return": "returned"}
    new_status = status_map[request.action]

    if _is_demo(user_id):
        app = next((a for a in _ALL_DEMO_APPS if a["id"] == app_id), None)
        if not app:
            raise HTTPException(status_code=404, detail="Application not found")
        app["status"] = new_status
        app["reviewer_id"] = user_id
        app["reviewer_notes"] = request.notes
        app["rejection_reason"] = request.rejection_reason
        app["reviewed_at"] = datetime.now(timezone.utc).isoformat()
        app["updated_at"] = datetime.now(timezone.utc).isoformat()
        return app

    try:
        result = supabase.table("applications").update({
            "status": new_status,
            "reviewer_id": user_id,
            "reviewer_notes": request.notes,
            "rejection_reason": request.rejection_reason,
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", app_id).execute()
        if not result.data:
            raise HTTPException(status_code=404, detail="Application not found")
        return result.data[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
