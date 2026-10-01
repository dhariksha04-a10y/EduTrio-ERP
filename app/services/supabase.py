"""Existing Supabase Auth, PostgreSQL, and Storage integration."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
from urllib.parse import quote

import httpx
from app.core.config import Settings
from app.schemas.scan import ScanResult

log = logging.getLogger(__name__)
STUDENT_TABLE = "01_student_information"
ACADEMIC_TABLE = "02_academic_information"
DOCUMENT_TABLE = "03_student_documents"
TABLES = (STUDENT_TABLE, ACADEMIC_TABLE, DOCUMENT_TABLE)
IDENTIFIER_FIELDS = ("register_number", "registration_number", "roll_number", "certificate_number")


class SupabaseError(RuntimeError):
    """Safe-to-return persistence or authentication error."""


class DuplicateCertificateError(SupabaseError):
    """The certificate already has a persisted academic/document record."""


async def verify_user(settings: Settings, token: str) -> dict:
    if not settings.supabase_url or not settings.supabase_anon_key:
        raise SupabaseError("Supabase Auth is not configured")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(settings.supabase_url.rstrip("/") + "/auth/v1/user",
                headers={"apikey": settings.supabase_anon_key, "Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        log.warning("Supabase Auth request failed (%s)", type(exc).__name__)
        raise SupabaseError("Authentication service is unavailable") from exc
    if response.status_code != 200:
        raise SupabaseError("Authentication failed")
    return response.json()


def persistence_ready(settings: Settings) -> bool:
    return bool(settings.enable_persistence and settings.supabase_url and
                settings.supabase_anon_key and settings.supabase_service_role_key and
                settings.supabase_db_url and settings.storage_bucket == "student-documents")


def _identifier_values(result: ScanResult) -> list[tuple[str, str]]:
    return [(field, value.strip()) for field in IDENTIFIER_FIELDS
            if (value := getattr(result, field, None)) and value.strip()]


def _find_student(conn, result: ScanResult, identifiers: list[tuple[str, str]]):
    """Resolve an existing student by certificate identifiers, then name + DOB."""
    ids: set[object] = set()
    for field, value in identifiers:
        row = conn.execute(
            f'SELECT DISTINCT student_id FROM "public"."{ACADEMIC_TABLE}" WHERE "{field}" = %s',
            (value,)).fetchall()
        ids.update(item[0] for item in row)
    if len(ids) > 1:
        raise SupabaseError("Extracted certificate identifiers point to different students")
    if ids:
        return next(iter(ids))
    if result.student_name and result.date_of_birth:
        row = conn.execute(
            'SELECT id FROM "public"."01_student_information" '
            'WHERE lower(trim(student_name)) = lower(trim(%s)) AND date_of_birth = %s LIMIT 2',
            (result.student_name, result.date_of_birth)).fetchall()
        if len(row) > 1:
            raise SupabaseError("Student name and date of birth match multiple records")
        if row:
            return str(row[0][0])
    return None


def _duplicate_academic(conn, student_id: str, result: ScanResult,
                        identifiers: list[tuple[str, str]]) -> bool:
    clauses = []
    args: list[object] = [student_id, result.document_type.upper()]
    for field, value in identifiers:
        clauses.append(f'"{field}" = %s')
        args.append(value)
    if clauses:
        sql = ('SELECT 1 FROM "public"."02_academic_information" '
               'WHERE student_id = %s AND upper(academic_level) = %s AND (' + " OR ".join(clauses) + ') LIMIT 1')
        return conn.execute(sql, args).fetchone() is not None
    return conn.execute(
        'SELECT 1 FROM "public"."02_academic_information" '
        'WHERE student_id = %s AND upper(academic_level) = %s LIMIT 1',
        (student_id, result.document_type.upper())).fetchone() is not None


def _document_number(result: ScanResult) -> str | None:
    return next((getattr(result, name) for name in IDENTIFIER_FIELDS if getattr(result, name, None)), None)


def _safe_filename(filename: str | None) -> str:
    base = os.path.basename((filename or "certificate").replace("\\", "/"))
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")[:100]
    return clean or "certificate"


def _subject_json(result: ScanResult) -> list[dict]:
    return [{"subject": s.subject_name, "secured_marks": s.secured_marks,
             "maximum_marks": s.maximum_marks, "percentage": s.subject_percentage}
            for s in result.subjects]


async def _upload(settings: Settings, object_path: str, content_type: str, data: bytes) -> bool:
    """Return True only when this request created the object."""
    key = settings.supabase_service_role_key
    url = (settings.supabase_url.rstrip("/") + "/storage/v1/object/" +
           quote(settings.storage_bucket, safe="") + "/" + quote(object_path, safe="/"))
    headers = {"apikey": key, "Authorization": f"Bearer {key}",
               "Content-Type": content_type, "x-upsert": "false"}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, headers=headers, content=data)
    except httpx.HTTPError as exc:
        log.warning("Storage upload request failed (%s)", type(exc).__name__)
        raise SupabaseError("Certificate upload to Storage failed") from exc
    if response.status_code in (200, 201):
        return True
    # Deterministic path includes a complete content hash. A 409 therefore
    # means the exact same bytes already exist; reuse the object reference.
    if response.status_code == 409:
        return False
    log.warning("Storage upload returned HTTP %s", response.status_code)
    raise SupabaseError("Certificate upload to Storage failed")


async def _delete_storage_object(settings: Settings, object_path: str) -> None:
    key = settings.supabase_service_role_key
    url = settings.supabase_url.rstrip("/") + "/storage/v1/object/" + quote(settings.storage_bucket, safe="")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.delete(url,
                headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"prefixes": [object_path]})
        if response.status_code not in (200, 204):
            log.error("Storage cleanup failed with HTTP %s for %s", response.status_code, object_path)
    except httpx.HTTPError as exc:
        log.error("Storage cleanup request failed (%s) for %s", type(exc).__name__, object_path)


async def save_validated(settings: Settings, result: ScanResult, data: bytes,
                         content_type: str, filename: str | None = None) -> bool:
    """Save student, academic and document data using the supplied live schema.

    PostgreSQL operations run in a single transaction. The Storage upload is
    compensated if the transaction fails after the object is created.
    """
    if not persistence_ready(settings):
        raise SupabaseError("Persistence is not fully configured")
    if result.status != "accepted":
        raise SupabaseError("Only completely validated certificates may be saved")
    identifiers = _identifier_values(result)
    if not identifiers and not (result.student_name and result.date_of_birth):
        raise SupabaseError("A reliable student or certificate identifier is required")

    import psycopg
    from psycopg.types.json import Jsonb

    content_hash = hashlib.sha256(data).hexdigest()
    safe_name = _safe_filename(filename)
    object_path: str | None = None
    uploaded_by_this_request = False
    upload_outcome_uncertain = False
    committed = False
    try:
        with psycopg.connect(settings.supabase_db_url, connect_timeout=10) as conn:
            with conn.transaction():
                # Serialize scans by content and each extracted stable identifier.
                lock_keys = sorted({"sha256:" + content_hash} | {f"{field}:{value}" for field, value in identifiers})
                for lock_key in lock_keys:
                    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (lock_key,))
                doc_hash = conn.execute(
                    'SELECT 1 FROM "public"."03_student_documents" '
                    "WHERE document_data ->> 'content_sha256' = %s LIMIT 1", (content_hash,)).fetchone()
                if doc_hash:
                    raise DuplicateCertificateError("This certificate/document already exists.")

                student_id = _find_student(conn, result, identifiers)
                if student_id and _duplicate_academic(conn, student_id, result, identifiers):
                    raise DuplicateCertificateError("This certificate/document already exists.")
                document_number = _document_number(result)
                if student_id and document_number:
                    doc_duplicate = conn.execute(
                        'SELECT 1 FROM "public"."03_student_documents" '
                        'WHERE student_id = %s AND upper(document_type) = upper(%s) '
                        'AND document_number = %s LIMIT 1',
                        (student_id, result.document_type, document_number)).fetchone()
                    if doc_duplicate:
                        raise DuplicateCertificateError("This certificate/document already exists.")

                if student_id is None:
                    new_student_id = uuid.uuid4()
                    row = conn.execute(
                        'INSERT INTO "public"."01_student_information" (id, student_name, date_of_birth) '
                        'VALUES (%s, %s, %s) RETURNING id',
                        (new_student_id, result.student_name, result.date_of_birth)).fetchone()
                    student_id = row[0]

                academic_values = {
                    "id": uuid.uuid4(),
                    "student_id": student_id,
                    "board": result.board,
                    "academic_level": result.document_type.upper(),
                    "certificate_number": result.certificate_number,
                    "registration_number": result.registration_number,
                    "roll_number": result.roll_number,
                    "register_number": result.register_number,
                    "exam_year": result.exam_year,
                    "exam_month": result.exam_month,
                    "school_name": result.school_name,
                    "school_code": result.school_code,
                    "group_name": result.group_or_stream,
                    "group_code": result.group_code,
                    "medium": result.medium,
                    "subjects": Jsonb(_subject_json(result)),
                    "total_marks": result.total_marks,
                    "maximum_total_marks": result.maximum_total_marks,
                    "percentage": result.percentage,
                    "result": result.result,
                    "issue_date": result.issue_date,
                }
                academic_columns = ", ".join(f'"{name}"' for name in academic_values)
                academic_params = ", ".join("%s" for _ in academic_values)
                conn.execute(
                    f'INSERT INTO "public"."02_academic_information" ({academic_columns}) VALUES ({academic_params})',
                    list(academic_values.values()))

                object_path = (f"{student_id}/{result.document_type.lower()}/"
                               f"{content_hash}/{safe_name}")
                try:
                    uploaded_by_this_request = await _upload(settings, object_path, content_type, data)
                except SupabaseError:
                    # A transport failure can happen after Storage accepted the
                    # body, so make a best-effort cleanup for an uncertain result.
                    upload_outcome_uncertain = True
                    raise
                metadata = result.model_dump(mode="json")
                metadata.update({"content_sha256": content_hash, "original_filename": safe_name})
                document_values = {
                    "id": uuid.uuid4(),
                    "student_id": student_id,
                    "document_type": result.document_type,
                    "document_number": document_number,
                    "document_name": safe_name,
                    "issue_date": result.issue_date,
                    "issuing_authority": result.board,
                    "document_data": Jsonb(metadata),
                    "file_path": object_path,
                    "verification_status": "verified",
                }
                document_columns = ", ".join(f'"{name}"' for name in document_values)
                document_params = ", ".join("%s" for _ in document_values)
                conn.execute(
                    f'INSERT INTO "public"."03_student_documents" ({document_columns}) VALUES ({document_params})',
                    list(document_values.values()))
        committed = True
        return True
    except DuplicateCertificateError:
        raise
    except Exception as exc:
        log.warning("Certificate persistence failed (%s)", type(exc).__name__)
        raise SupabaseError("Certificate data could not be saved") from exc
    finally:
        if object_path and (uploaded_by_this_request or upload_outcome_uncertain) and not committed:
            await _delete_storage_object(settings, object_path)
