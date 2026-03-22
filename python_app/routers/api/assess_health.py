"""assess_health.py – lightweight readiness probe for the assessment service.

GET /api/assess-ready
  200 OK      → assessment service is up and accepting requests
  503         → service is still loading (models downloading on first boot)

The frontend calls this before submitting a label so it can show a
friendly "warming up" UI immediately instead of hanging for minutes.
"""

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from config import LOCAL_LLM_URL

router = APIRouter()


@router.get("/assess-ready")
async def assess_ready():
    """Return 200 if the local assessment service is ready, 503 if not."""
    if not LOCAL_LLM_URL:
        # Cloud mode (Anthropic API) — always considered ready
        return JSONResponse(status_code=200, content={"ready": True, "mode": "cloud"})

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                LOCAL_LLM_URL.rstrip("/") + "/health",
                timeout=2.0,
            )
        if resp.status_code == 200:
            return JSONResponse(status_code=200, content={"ready": True, "mode": "local"})
        return JSONResponse(status_code=503, content={"ready": False, "mode": "local"})
    except (httpx.ConnectError, httpx.TimeoutException, Exception):
        return JSONResponse(
            status_code=503,
            content={"ready": False, "mode": "local"},
        )
