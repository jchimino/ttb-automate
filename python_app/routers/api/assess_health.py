"""assess_health.py – lightweight readiness probe for the assessment service.

GET /api/assess-ready
  200 OK  → assessment service has warmed up and is ready to process requests
  503     → service is still loading (models downloading / warm-up probe in progress)

The frontend calls this before submitting a label so it can show a friendly
"warming up" UI immediately instead of hanging for several minutes waiting
for an inference timeout.

Implementation note
-------------------
The assess service's /health endpoint returns 503 while its startup warm-up
probe is still running (models loading), and 200 once it has confirmed it can
respond to a real generate call.  We simply forward that signal here.
"""

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from config import LOCAL_LLM_URL

# The assess service URL is the same host that LOCAL_LLM_URL points to upstream,
# but the assess container itself lives at http://assess:8000 inside Docker.
import os

_ASSESS_HOST = os.getenv("LOCAL_LLM_URL", "http://assess:8000").rstrip("/")

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
                _ASSESS_HOST + "/health",
                timeout=3.0,
            )
        # The assess /health endpoint returns 503 while warm-up is in progress
        if resp.status_code == 200:
            return JSONResponse(status_code=200, content={"ready": True, "mode": "local"})
        return JSONResponse(status_code=503, content={"ready": False, "mode": "local", "detail": "warming up"})
    except (httpx.ConnectError, httpx.TimeoutException, Exception):
        return JSONResponse(
            status_code=503,
            content={"ready": False, "mode": "local", "detail": "unreachable"},
        )
