from fastapi.testclient import TestClient
import pytest
from app.main import app
from app.ocr.engine import OCRResult
from app.validation.certificate import extract
from app.core.config import Settings
import app.api.routes as routes

GOOD10 = """Tamil Nadu State Board SSLC Marksheet
Student Name: ANANYA DEVI M
Register Number: 345678
School: Example Higher Secondary School
Physics 091
Chemistry 088
Mathematics 094
Biology 092
TOTAL 545 / 600
"""
GOOD12 = """CBSE Senior Secondary Class XII Mark Sheet
Candidate Name: KIRAN KUMAR
Roll Number: 987654
English 085
Physics 091
Chemistry 088
Mathematics 094
Biology 092
Grand Total 450 / 500
"""


def test_valid_tenth_extracts_leading_zero_as_mark():
    r = extract(GOOD10, .91, .55)
    assert r.document_type == "10th"
    assert r.subjects[0].secured_marks == 91
    assert r.total_marks == 545 and r.maximum_total_marks == 600
    assert r.percentage == 90.83
    assert r.status == "accepted"


def test_valid_twelfth_certificate():
    r = extract(GOOD12, .9, .55)
    assert r.document_type == "12th" and r.status == "accepted"
    assert r.percentage == 90.0


def test_different_student_is_extracted():
    r = extract(GOOD12, .9, .55)
    assert r.student_name == "KIRAN KUMAR"
    assert r.roll_number == "987654"


def test_duplicate_certificate_returns_duplicate_status(monkeypatch):
    async def duplicate(*args, **kwargs): raise routes.DuplicateCertificateError("duplicate")
    monkeypatch.setattr(routes, "save_validated", duplicate)
    monkeypatch.setattr(routes, "read_text", lambda _: OCRResult(GOOD10, .95))
    monkeypatch.setattr(routes, "verify_user", lambda *_: _resolved({"id":"test"}))
    response = TestClient(app).post("/scan", headers={"Authorization":"Bearer test"}, files={"file":("cert.png", b"image", "image/png")})
    assert response.status_code == 200 and response.json()["status"] == "duplicate"


def test_random_photo_is_invalid():
    r = extract("A beach at sunset. Family holiday 2024", .99, .55)
    assert r.status == "invalid_document"


def test_unrelated_document_is_invalid():
    r = extract("INVOICE total 545 / 600 customer order", .95, .55)
    assert r.status == "invalid_document"


def test_poor_quality_requires_review():
    r = extract(GOOD10, .2, .55)
    assert r.status == "review_required"


def test_missing_name_requires_review():
    r = extract(GOOD10.replace("Student Name: ANANYA DEVI M\n", ""), .9, .55)
    assert r.status == "review_required"
    assert any("name" in x.lower() for x in r.issues)


def test_missing_register_requires_review():
    r = extract(GOOD10.replace("Register Number: 345678\n", ""), .9, .55)
    assert r.status == "review_required"
    assert any("register" in x.lower() for x in r.issues)


def test_invalid_subject_marks_review():
    text = GOOD10.replace("Physics 091", "Physics 150 / 100")
    r = extract(text, .9, .55)
    assert r.status == "review_required"
    assert any("Physics" in x for x in r.issues)


def test_invalid_total_range_review():
    r = extract(GOOD10.replace("545 / 600", "650 / 600"), .9, .55)
    assert r.status == "review_required"
    assert r.percentage is None


def test_review_required_on_uncertainty():
    r = extract(GOOD10, .4, .55)
    assert r.status == "review_required"


def test_database_failure_never_claims_saved(monkeypatch):
    async def fail(*args, **kwargs): raise routes.SupabaseError("offline")
    monkeypatch.setattr(routes, "save_validated", fail)
    monkeypatch.setattr(routes, "read_text", lambda _: OCRResult(GOOD10, .95))
    monkeypatch.setattr(routes, "verify_user", lambda *_: _resolved({"id":"test"}))
    c = TestClient(app)
    response = c.post("/scan", headers={"Authorization":"Bearer test"}, files={"file":("cert.png", b"image", "image/png")})
    assert response.status_code == 200
    assert response.json()["status"] == "review_required"


def test_storage_failure_is_not_hidden(monkeypatch):
    import asyncio, sys, types
    import app.services.supabase as supabase
    from app.services.supabase import save_validated, SupabaseError
    state = {"rolled_back":False, "document_inserted":False}
    class Cursor:
        def __init__(self, row=None): self.row=row
        def fetchone(self): return self.row
        def fetchall(self): return []
    class Transaction:
        def __enter__(self): return self
        def __exit__(self, kind, value, tb):
            if kind: state["rolled_back"] = True
            return False
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def transaction(self): return Transaction()
        def execute(self, query, params=()):
            q=str(query)
            if 'INSERT INTO "public"."01_student_information"' in q: return Cursor(("student-uuid",))
            if 'INSERT INTO "public"."03_student_documents"' in q: state["document_inserted"] = True
            return Cursor()
    fake_pg=types.ModuleType("psycopg")
    fake_pg.connect=lambda *args, **kwargs: Connection()
    fake_types=types.ModuleType("psycopg.types")
    fake_json=types.ModuleType("psycopg.types.json")
    fake_json.Jsonb=lambda value: value
    monkeypatch.setitem(sys.modules, "psycopg", fake_pg)
    monkeypatch.setitem(sys.modules, "psycopg.types", fake_types)
    monkeypatch.setitem(sys.modules, "psycopg.types.json", fake_json)
    async def failed_upload(*args, **kwargs): raise SupabaseError("Storage upload failed")
    monkeypatch.setattr(supabase, "_upload", failed_upload)
    settings = Settings(supabase_url="https://example.invalid", supabase_anon_key="anon",
        supabase_service_role_key="service", supabase_db_url="postgresql://dummy",
        storage_bucket="student-documents", enable_persistence=True)
    with pytest.raises(SupabaseError):
        asyncio.run(save_validated(settings, extract(GOOD10, .9, .5), b"data", "image/png"))
    assert state["rolled_back"] and not state["document_inserted"]


def test_authentication_failure():
    monkeypatch_client = TestClient(app)
    response = monkeypatch_client.post("/scan", files={"file":("cert.png", b"x", "image/png")})
    assert response.status_code == 401


def _resolved(value):
    import asyncio
    f = asyncio.get_running_loop().create_future()
    f.set_result(value)
    return f
