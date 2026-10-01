from typing import Literal
from pydantic import BaseModel, Field


class SubjectResult(BaseModel):
    subject_name: str
    secured_marks: int | None = None
    maximum_marks: int | None = None
    subject_percentage: float | None = None


class ScanResult(BaseModel):
    success: bool = False
    status: Literal["accepted", "success", "review_required", "invalid_document", "duplicate"]
    message: str
    student_name: str | None = None
    register_number: str | None = None
    registration_number: str | None = None
    roll_number: str | None = None
    certificate_number: str | None = None
    date_of_birth: str | None = None
    school_name: str | None = None
    board: str | None = None
    exam_session: str | None = None
    exam_year: int | None = None
    exam_month: str | None = None
    group_or_stream: str | None = None
    school_code: str | None = None
    group_code: str | None = None
    medium: str | None = None
    document_type: str | None = None
    issue_date: str | None = None
    admission_identifier: str | None = None
    subjects: list[SubjectResult] = Field(default_factory=list)
    total_marks: int | None = None
    maximum_total_marks: int | None = None
    percentage: float | None = None
    result: Literal["PASS", "FAIL"] | None = None
    ocr_confidence: float | None = None
    issues: list[str] = Field(default_factory=list)
