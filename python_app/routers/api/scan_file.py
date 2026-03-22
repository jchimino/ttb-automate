"""File scanning API endpoint - malware detection"""

import httpx
from fastapi import APIRouter, HTTPException, Depends, Header, UploadFile, File
from typing import Optional
from supabase import create_client
import json
from datetime import datetime

from config import MALWARE_SCAN_API, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_ANON_KEY, MAX_IMAGE_SIZE_BYTES

router = APIRouter()


async def verify_jwt_token(authorization: Optional[str] = Header(None)) -> str:
    """Verify JWT token and return user ID"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")

    token = authorization.replace("Bearer ", "")

    supabase = create_client(SUPABASE_URL, SUPABASE_ANON_KEY, {"global": {"headers": {"Authorization": authorization}}})

    try:
        result = supabase.auth.get_user(token)
        if not result.user:
            raise HTTPException(status_code=401, detail="Unauthorized")
        return result.user.id
    except Exception:
        raise HTTPException(status_code=401, detail="Unauthorized")


async def scan_file_with_malware_detection(file_content: bytes, filename: str) -> dict:
    """Scan file with malwaredetection.me API"""

    try:
        async with httpx.AsyncClient() as client:
            files = {"file": (filename, file_content)}
            response = await client.post(MALWARE_SCAN_API, files=files)

            if response.status_code != 200:
                return {
                    "clean": True,
                    "message": "Scan service unavailable - file accepted with warning",
                }

            result = response.json()

            if result.get("infected") or result.get("malware") or result.get("threat"):
                return {
                    "clean": False,
                    "threat": result.get("threat") or result.get("malware") or "Unknown threat",
                    "message": f"Malware detected: {result.get('threat') or result.get('malware')}",
                    "scan_details": result,
                }

            return {
                "clean": True,
                "message": "File is clean",
            }
    except Exception as e:
        print(f"Scan error: {e}")
        return {
            "clean": True,
            "message": "Scan unavailable - file accepted with warning",
        }


async def quarantine_file(
    file_content: bytes,
    filename: str,
    file_size: int,
    mime_type: Optional[str],
    threat_name: str,
    user_id: str,
    uploader_ip: Optional[str],
    scan_details: Optional[dict],
) -> dict:
    """Quarantine infected file to Supabase storage"""

    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

        # Generate storage path
        timestamp = int(datetime.now().timestamp() * 1000)
        sanitized_filename = "".join(c if c.isalnum() or c in ".-_" else "_" for c in filename)
        storage_path = f"{user_id}/{timestamp}-{sanitized_filename}"

        # Upload to quarantine bucket
        supabase.storage.from_("quarantine").upload(
            storage_path,
            file_content,
            {"content_type": mime_type or "application/octet-stream", "upsert": False},
        )

        # Record in database
        supabase.table("quarantined_files").insert({
            "original_filename": filename,
            "file_size": file_size,
            "mime_type": mime_type,
            "threat_name": threat_name,
            "storage_path": storage_path,
            "uploader_user_id": user_id,
            "uploader_ip": uploader_ip,
            "scan_details": scan_details,
            "status": "pending",
        }).execute()

        return {"success": True}
    except Exception as e:
        print(f"Quarantine error: {e}")
        return {"success": False, "error": str(e)}


@router.post("/scan-file")
async def scan_file(
    file: UploadFile = File(...),
    user_id: str = Depends(verify_jwt_token),
):
    """Scan uploaded file for malware"""

    if file.size > MAX_IMAGE_SIZE_BYTES:
        raise HTTPException(status_code=400, detail="File too large. Maximum size is 10MB.")

    file_content = await file.read()

    # Scan the file
    scan_result = await scan_file_with_malware_detection(file_content, file.filename)

    if not scan_result.get("clean"):
        # Quarantine the infected file
        uploader_ip = None  # In FastAPI, we'd need to extract from request
        quarantine_result = await quarantine_file(
            file_content,
            file.filename,
            file.size,
            file.content_type,
            scan_result.get("threat", "Unknown threat"),
            user_id,
            uploader_ip,
            scan_result.get("scan_details"),
        )

        return {
            "clean": False,
            "error": scan_result.get("message"),
            "threat": scan_result.get("threat"),
            "quarantined": quarantine_result.get("success", False),
        }

    return {
        "clean": True,
        "message": scan_result.get("message"),
    }
