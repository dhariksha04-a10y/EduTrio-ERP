# EduTrio ERP certificate scanning backend

FastAPI backend for scanning 10th and 12th certificate images. It does not include a frontend and does not create Supabase projects, tables, or buckets.

## Setup

Use Python 3.11 or newer. From this folder:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

EasyOCR downloads its language model the first time it runs. Tesseract is used only as fallback and must be installed separately with its executable available on PATH.

## Supabase

Configure the existing project's `SUPABASE_URL`, `SUPABASE_ANON_KEY`, and service credentials in `.env`; never commit `.env`. `student-documents` is the required existing bucket. No table or bucket is created by this service.

Set the existing project's `SUPABASE_DB_URL` and use the inspected schema documented below. Persistence is enabled through `ENABLE_PERSISTENCE=true` only when the Supabase URL, Auth anon key, service role key and database URL are all configured. The backend writes to the existing schema described below and never creates or alters resources.

```powershell
python -m scripts.inspect_supabase
```

The inspector remains available as a read-only check. Student name and DOB map to `01_student_information.student_name` and `.date_of_birth`. Academic values map directly to `board`, `academic_level`, `certificate_number`, `registration_number`, `roll_number`, `register_number`, `exam_year`, `exam_month`, `school_name`, `school_code`, `group_name`, `group_code`, `medium`, `subjects` JSONB, `total_marks`, `maximum_total_marks`, `percentage`, `result` and `issue_date` in `02_academic_information`. The document's `student_id`, `document_type`, `document_number`, `document_name`, `issue_date`, `issuing_authority`, `document_data` JSONB, `file_path` and `verification_status` map to `03_student_documents`. Student matching checks existing academic register, registration, roll and certificate identifiers, then falls back to exact normalized name plus DOB. Academic duplicates are checked by student, level and available identifiers; documents are also checked by content SHA-256 and document number. Invalid and review-required results never reach persistence. Academic and document inserts (and any new student insert) run in one PostgreSQL transaction. A deterministic Storage path includes the student UUID, document level, file SHA-256 and safe filename. Duplicate checks happen before upload. If a newly uploaded object is followed by a database error, the transaction rolls back and the service attempts to delete that object; Storage cleanup is compensating best effort, not a cross-service transaction. Supabase Auth validates the bearer token with the existing project's Auth endpoint.

## API

- `GET /health` is public.
- `POST /scan` requires `Authorization: Bearer <existing Supabase access token>` and a multipart `file` field. Accepts JPEG, PNG, WebP, and TIFF images.
- `GET /docs` provides Swagger UI.

Responses distinguish `success`, `review_required`, `invalid_document`, and `duplicate`, and include a `success` boolean. Invalid/review documents are never persisted. Certificate extraction uses academic/board signals, labeled student identifiers, subjects, marks, totals, OCR confidence, and consistency checks. Parsing is heuristic and board layouts vary, so uncertain cases require review.

## Frontend integration and CORS

Set comma-separated `ALLOWED_ORIGINS` to the existing frontend's exact origins. The API uses standard multipart upload and JSON responses for compatibility with browser JavaScript/TypeScript clients.

## Tests

Run `python -m pytest`. Tests exercise extraction and API behavior with mocked OCR; they do not verify live Supabase operations or real-world OCR quality.

## Start

```powershell
python -m uvicorn main:app --reload
```

Swagger is at http://127.0.0.1:8000/docs.
