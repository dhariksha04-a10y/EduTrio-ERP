import re
from datetime import datetime
from app.schemas.scan import ScanResult, SubjectResult

ACADEMIC = re.compile(r"\b(mark\s*sheet|marksheet|secondary|higher secondary|SSLC|HSC|CBSE|ICSE|CISCE|matriculation|board of|statement of marks|subject|total marks)\b", re.I)
BOARD = re.compile(r"\b(CBSE|CISCE|ICSE|Tamil Nadu|Maharashtra|Karnataka|Kerala|Andhra Pradesh|Telangana|State Board|Board of Secondary)\b", re.I)
SUBJECTS = {"physics":"Physics", "chemistry":"Chemistry", "mathematics":"Mathematics", "maths":"Mathematics", "biology":"Biology", "english":"English", "computer science":"Computer Science", "accountancy":"Accountancy", "economics":"Economics", "history":"History", "geography":"Geography", "science":"Science", "social science":"Social Science", "social studies":"Social Science", "language":"Language"}


def _first(pattern: str, text: str, flags=re.I):
    m = re.search(pattern, text, flags)
    return m.group(1).strip(" :-") if m else None


def extract(text: str, confidence: float, min_confidence: float) -> ScanResult:
    issues: list[str] = []
    academic_signals = len(ACADEMIC.findall(text))
    board = _first(r"\b(CBSE|CISCE|ICSE|Tamil Nadu|Maharashtra|Karnataka|Kerala|Andhra Pradesh|Telangana|State Board|Board of Secondary[^\n]*)\b", text)
    name = _first(r"(?:student(?:'s)?\s+name|name\s+of\s+student|candidate(?:'s)?\s+name)\s*[:\-]?\s*([A-Z][A-Z .'-]{2,})", text)
    reg = _first(r"register\s*(?:no|number)\s*[:#\-]?\s*([A-Z0-9/-]{3,})", text)
    registration = _first(r"registration\s*(?:no|number)\s*[:#\-]?\s*([A-Z0-9/-]{3,})", text)
    roll = _first(r"(?:roll|hall\s*ticket)\s*(?:no|number)\s*[:#\-]?\s*([A-Z0-9/-]{3,})", text)
    certificate = _first(r"(?:certificate|document)\s*(?:no|number|id)\s*[:#\-]?\s*([A-Z0-9/-]{3,})", text)
    raw_dob = _first(r"(?:date\s+of\s+birth|DOB)\s*[:\-]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", text)
    dob = None
    if raw_dob:
        for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y", "%m/%d/%Y", "%m-%d-%Y"):
            try:
                dob = datetime.strptime(raw_dob, fmt).date().isoformat()
                break
            except ValueError:
                continue
    school = _first(r"(?:school|institution|college)(?:\s+name)?\s*[:\-]?\s*(?!code\b)([^\n]{4,100})", text)
    group = _first(r"(?:group|stream|course)\s*[:\-]?\s*([^\n]{3,60})", text)
    admission = _first(r"(?:admission\s*(?:no|number))\s*[:#\-]?\s*([A-Z0-9/-]{3,})", text)
    session = _first(r"(?:exam(?:ination)?\s*(?:session|year)?|academic\s*year)\s*[:\-]?\s*((?:19|20)\d{2}(?:\s*[-/]\s*(?:19|20)?\d{2})?)", text)
    year_match = re.search(r"\b((?:19|20)\d{2})\b", session or "")
    month = _first(r"(?:exam(?:ination)?\s*month|month)\s*[:\-]?\s*(January|February|March|April|May|June|July|August|September|October|November|December)", text)
    school_code = _first(r"school\s*code\s*[:#\-]?\s*([A-Z0-9/-]{2,})", text)
    group_code = _first(r"group\s*code\s*[:#\-]?\s*([A-Z0-9/-]{2,})", text)
    medium = _first(r"(?:medium\s*(?:of\s*instruction)?|instruction\s*medium)\s*[:\-]?\s*([A-Za-z ]{3,30})", text)
    raw_issue_date = _first(r"(?:date\s+of\s+issue|issue\s*date)\s*[:\-]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", text)
    issue_date = None
    if raw_issue_date:
        for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y", "%m/%d/%Y", "%m-%d-%Y"):
            try:
                issue_date = datetime.strptime(raw_issue_date, fmt).date().isoformat()
                break
            except ValueError:
                continue
    result_match = re.search(r"\b(?:result|overall\s*result)\s*[:\-]?\s*(PASS(?:ED)?|FAIL(?:ED)?)\b", text, re.I)
    result_value = ("PASS" if result_match.group(1).upper().startswith("PASS") else "FAIL") if result_match else None
    dtype = "10th" if re.search(r"\b(SSLC|10th|class\s*x|secondary school certificate|matriculation)\b", text, re.I) else "12th" if re.search(r"\b(HSC|12th|class\s*xii|higher secondary|senior secondary)\b", text, re.I) else None

    subjects: list[SubjectResult] = []
    for line in text.splitlines():
        lower = line.lower()
        subj = next((label for key, label in sorted(SUBJECTS.items(), key=lambda kv: -len(kv[0])) if key in lower), None)
        if not subj:
            continue
        nums = [int(x) for x in re.findall(r"(?<!\d)\d{1,3}(?!\d)", line)]
        if not nums:
            continue
        # The common pair is secured/max; a lone “091” is secured marks and
        # must never be mistaken for a maximum.
        secured = nums[0] if len(nums) >= 2 else nums[0]
        maximum = nums[1] if len(nums) >= 2 and 0 < nums[1] <= 1000 else None
        subjects.append(SubjectResult(subject_name=subj, secured_marks=secured, maximum_marks=maximum,
            subject_percentage=round(secured / maximum * 100, 2) if maximum and secured <= maximum else None))
    dedup: dict[str, SubjectResult] = {}
    for subject in subjects:
        prior = dedup.get(subject.subject_name.casefold())
        if prior and (prior.secured_marks, prior.maximum_marks) != (subject.secured_marks, subject.maximum_marks):
            issues.append(f"Conflicting marks for {subject.subject_name}")
        else:
            dedup[subject.subject_name.casefold()] = subject
    subjects = list(dedup.values())

    total = maximum_total = None
    pair = re.search(r"(?:grand\s*)?total(?:\s*marks)?\s*[:\-]?\s*(\d{2,4})\s*/\s*(\d{2,4})", text, re.I)
    if pair:
        total, maximum_total = map(int, pair.groups())
    else:
        t = re.search(r"(?:grand\s*)?total(?:\s*marks)?\s*[:\-]?\s*(\d{2,4})\b", text, re.I)
        mx = re.search(r"maximum(?:\s*total|\s*marks)?\s*[:\-]?\s*(\d{2,4})\b", text, re.I)
        if t: total = int(t.group(1))
        if mx: maximum_total = int(mx.group(1))

    if academic_signals < 2 or (not BOARD.search(text) and not re.search(r"\b(SSLC|HSC|10th|12th|class\s*x)\b", text, re.I)):
        return ScanResult(status="invalid_document", message="The upload does not appear to be an academic certificate.", ocr_confidence=round(confidence, 3), issues=["Insufficient academic document signals"])
    if not name: issues.append("Student name not confidently extracted")
    if not (reg or registration or roll or certificate): issues.append("Register/registration/roll/certificate identifier not confidently extracted")
    if not dtype: issues.append("Certificate level is uncertain")
    if confidence < min_confidence: issues.append("OCR confidence is below the configured threshold")
    if not subjects: issues.append("No subject marks could be extracted")
    if total is None or maximum_total is None: issues.append("Total and maximum total are incomplete")
    for s in subjects:
        if s.secured_marks is None or s.secured_marks < 0 or (s.maximum_marks is not None and s.secured_marks > s.maximum_marks):
            issues.append(f"Invalid marks for {s.subject_name}")
    percentage = None
    if total is not None and maximum_total is not None:
        if total < 0 or maximum_total <= 0 or total > maximum_total:
            issues.append("Total marks are outside the valid range")
        else:
            percentage = round(total / maximum_total * 100, 2)
    if subjects and total is not None and maximum_total is not None:
        # Compare totals only when rows have explicit maxima and account for
        # the full exam maximum; partial OCR subject lists are common.
        if all(s.maximum_marks is not None for s in subjects) and sum(s.maximum_marks or 0 for s in subjects) == maximum_total and sum(s.secured_marks or 0 for s in subjects) != total:
            issues.append("Subject marks do not match the extracted total")
    status = "review_required" if issues else "accepted"
    return ScanResult(status=status, message="Certificate requires human review." if issues else "Certificate validated.",
        student_name=name, register_number=reg, registration_number=registration, roll_number=roll,
        certificate_number=certificate, date_of_birth=dob, school_name=school, board=board,
        exam_session=session, exam_year=int(year_match.group(1)) if year_match else None,
        exam_month=month, group_or_stream=group, school_code=school_code, group_code=group_code,
        medium=medium, document_type=dtype, issue_date=issue_date, result=result_value,
        admission_identifier=admission,
        subjects=subjects, total_marks=total, maximum_total_marks=maximum_total, percentage=percentage,
        ocr_confidence=round(confidence, 3), issues=issues)
