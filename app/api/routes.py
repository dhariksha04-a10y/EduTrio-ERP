from __future__ import annotations
import logging
from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from app.core.config import Settings, get_settings
from app.ocr.engine import read_text
from app.schemas.scan import ScanResult
from app.services.supabase import DuplicateCertificateError, SupabaseError, persistence_ready, save_validated, verify_user
from app.validation.certificate import extract

log = logging.getLogger(__name__)
router = APIRouter()
ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp", "image/tiff"}


async def auth(authorization: str | None = Header(default=None), settings: Settings = Depends(get_settings)):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Bearer token required")
    try:
        return await verify_user(settings, authorization.split(None, 1)[1])
    except SupabaseError as exc:
        raise HTTPException(401, str(exc)) from exc


@router.get("/health")
async def health(settings: Settings = Depends(get_settings)):
    return {"status": "ok", "service": "edutrio-erp-backend", "persistence_enabled": persistence_ready(settings),
            "supabase_configured": bool(settings.supabase_url and settings.supabase_anon_key)}


@router.post("/scan", response_model=ScanResult)
async def scan(file: UploadFile = File(...), user=Depends(auth), settings: Settings = Depends(get_settings)):
    if file.content_type not in ALLOWED_MIME:
        raise HTTPException(415, "Upload a JPEG, PNG, WebP, or TIFF certificate image")
    data = await file.read(settings.max_upload_bytes + 1)
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(413, "File exceeds upload size limit")
    try:
        ocr = read_text(data)
    except ValueError as exc:
        raise HTTPException(400, "Invalid or unreadable image") from exc
    except RuntimeError as exc:
        log.warning("OCR processing failed")
        raise HTTPException(503, "OCR service is unavailable") from exc
    result = extract(ocr.text, ocr.confidence, settings.ocr_min_confidence)
    if result.status != "accepted":
        return result
    try:
        await save_validated(settings, result, data, file.content_type, filename=file.filename)
        result.success = True
        result.status = "success"
        result.message = "Certificate scanned and data saved successfully."
    except DuplicateCertificateError:
        result.success = False
        result.status = "duplicate"
        result.message = "This certificate/document already exists."
    except SupabaseError as exc:
        log.warning("Validated certificate not persisted: %s", exc)
        result.success = False
        result.status = "review_required"
        result.message = "Certificate could not be saved and requires manual review."
        result.issues.append("Database or Storage persistence failed")
    if result.status == "invalid_document":
        result.success = False
        result.message = "Invalid student certificate/document."
    elif result.status == "review_required" and result.message != "Certificate could not be saved and requires manual review.":
        result.success = False
        result.message = "OCR result requires manual review."
    return result
