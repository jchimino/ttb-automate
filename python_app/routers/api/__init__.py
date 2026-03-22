"""API routers"""

from fastapi import APIRouter

from . import verify_label, scan_file, applications, history

router = APIRouter(prefix="/api")

# Include sub-routers
router.include_router(verify_label.router)
router.include_router(scan_file.router)
router.include_router(applications.router)
router.include_router(history.router)
