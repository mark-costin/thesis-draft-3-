"""OCR worker: the text an engine reads becomes unconfirmed lab values, and the document waits for
the patient's review. A fake engine stands in for Chandra or Tesseract, so these tests need no
GPU, no OCR install and no Redis."""

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from backend import audit, ocr, ocr_parser, storage, worker
from backend.config import settings
from backend.database import SessionLocal, set_request_identity
from backend.main import app
from backend.models import Document, LabResult, Patient, User

client = TestClient(app)
PASSWORD = "correct horse battery"
PNG = b"\x89PNG\r\n\x1a\n" + b"lab report pixels "

SAMPLE_REPORT = """CLINICAL CHEMISTRY
Patient: Juan Dela Cruz   Age: 45
Date collected: 10/01/2026
TEST            RESULT    UNIT      REFERENCE
Fasting Blood Sugar (FBS)   5.6 H   mmol/L   3.9 - 5.5
Cholesterol, Total   5.2   mmol/L   < 5.2
HDL-C: 1.3 mmol/L
Triglycerides 1.7 mmol/L (0.0-1.7)
Creatinine 88.42 umol/L
HbA1c 6.5 %
OGTT 2 hr 140 mg/dL
Glucose 99 mg/dL
LDL <0.1 mg/dL
Eosinophils Absolute 0.4 x 10^9/L
"""

CHANDRA_TABLE = """| Test | Result | Unit | Reference |
|---|---|---|---|
| Vitamin B12 | 300 | pmol/L | 145-569 |
| TSH | 2.50 | 0.4 - 4.0 | uIU/mL |
| Uric Acid | 416 L | umol/L | |
| eGFR | 90 | mL/min/1.73 m2 | >60 |
| FBS | 5,6 | mmol/L | |
"""


# ---------------------------------------------------------------------------
# Parser (no database)
# ---------------------------------------------------------------------------
def values_of(text):
    return {value.test_code: (str(value.value), value.unit) for value in ocr_parser.extract(text).values}


def test_a_plain_text_report_maps_to_the_right_codes_and_units():
    assert values_of(SAMPLE_REPORT) == {
        "FPG": ("100.890", "mg/dL"),
        "TOTAL_CHOLESTEROL": ("201.084", "mg/dL"),
        "HDL": ("50.271", "mg/dL"),
        "TRIGLYCERIDES": ("150.569", "mg/dL"),
        "CREATININE": ("1.000", "mg/dL"),
        "HBA1C": ("6.500", "%"),
        "OGTT_2H": ("140.000", "mg/dL"),
        "EOSINOPHILS": ("400.000", "cells/uL"),
    }


def test_a_markdown_table_maps_to_the_right_codes_and_units():
    assert values_of(CHANDRA_TABLE) == {
        "VITAMIN_B12": ("406.614", "pg/mL"),
        "TSH": ("2.500", "mIU/L"),
        "URIC_ACID": ("6.994", "mg/dL"),
        "EGFR": ("90.000", "mL/min/1.73m2"),
    }


@pytest.mark.parametrize("line", [
    "Glucose 99 mg/dL",                 # random or fasting? not guessed
    "LDL <0.1 mg/dL",                   # below what the lab can measure
    "FBS 95",                           # no unit
    "Hemoglobin 13.5 g/dL",             # not a test this app uses
    "Patient: Juan Dela Cruz   Age: 45",
    "|---|---|---|",
    "",
])
def test_lines_that_cannot_be_read_safely_are_skipped(line):
    assert ocr_parser.parse_line(line) is None


def test_a_decimal_comma_or_wrong_unit_is_counted_as_skipped():
    found = ocr_parser.extract("FBS 5,6 mmol/L\nHbA1c 6.5 mg/dL\nTSH 2.5 mIU/L")
    assert [value.test_code for value in found.values] == ["TSH"]
    assert found.skipped_lines == 2


def test_the_first_value_of_a_test_is_kept():
    found = ocr_parser.extract("FBS 95 mg/dL\nFasting Blood Sugar 110 mg/dL")
    assert [(value.test_code, value.value) for value in found.values] == [("FPG", Decimal("95.000"))]


def test_a_label_with_a_number_in_it_is_read_whole():
    assert ocr_parser.parse_line("OGTT 2 hr 140 mg/dL") == ocr_parser.ParsedLine("OGTT_2H", "140", "mg/dL")
    assert ocr_parser.parse_line("Vitamin B12 300 pmol/L") == ocr_parser.ParsedLine("VITAMIN_B12", "300", "pmol/L")


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------
class FakeEngine:
    name = "Fake OCR"
    text = SAMPLE_REPORT
    confidence = 87.5
    error: Exception | None = None

    def read(self, data, mime_type):
        assert data.startswith(b"\x89PNG") and mime_type == "image/png"  # decrypted original
        if self.error:
            raise self.error
        return ocr.OcrResult(self.text, self.confidence)


@pytest.fixture(autouse=True)
def upload_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", tmp_path)
    return tmp_path


@pytest.fixture
def engine(monkeypatch):
    """Switches OCR on with the fake engine. Change its text/error in the test."""
    fake = type("ConfiguredFake", (FakeEngine,), {})
    monkeypatch.setattr(settings, "ocr_engine", "fake")
    monkeypatch.setitem(ocr.ENGINES, "fake", fake)
    return fake


@pytest.fixture
def make_patient():
    made = []

    def make():
        email = f"ocr-{uuid.uuid4().hex[:8]}@test.local"
        made.append(email)
        assert client.post("/auth/register", json={
            "email": email, "password": PASSWORD, "first_name": "Apolinario", "last_name": "Mabini",
            "date_of_birth": "1964-07-23", "biological_sex": "Male",
            "privacy_consent": True, "consent_version": settings.privacy_notice_version,
        }).status_code == 201
        token = client.post("/auth/login", json={"email": email, "password": PASSWORD}).json()["access_token"]
        return email, {"Authorization": f"Bearer {token}"}

    yield make
    with SessionLocal() as session:
        session.execute(delete(User).where(func.lower(User.email).in_(made)))
        session.commit()


def as_patient(session, email):
    user_id = session.scalar(select(User.user_id).where(User.email == email))
    set_request_identity(session, user_id=user_id)
    patient_id = session.scalar(select(Patient.patient_id))
    set_request_identity(session, user_id=user_id, patient_id=patient_id)
    return patient_id


def upload(headers):
    response = client.post("/documents", headers=headers,
                           files={"file": ("lab.png", PNG + uuid.uuid4().bytes, "image/png")})
    assert response.status_code == 202
    return response.json()["document_id"]


def document_of(email, document_id):
    with SessionLocal() as session:
        as_patient(session, email)
        return session.scalars(select(Document).where(Document.document_id == document_id)).one()


def test_upload_returns_202_and_ocr_makes_it_ready_for_review(make_patient, engine):
    email, headers = make_patient()
    document_id = upload(headers)
    document = client.get(f"/documents/{document_id}", headers=headers).json()
    assert document["status"] == "ready_for_review"
    assert document["ocr_confidence"] == 87.5 and document["processed_at"] is not None
    assert document_of(email, document_id).ocr_engine == "Fake OCR"

    review = client.get(f"/documents/{document_id}/review", headers=headers).json()
    rows = {row["test_code"]: row for row in review["lab_results"]}
    assert len(rows) == 8 and rows["FPG"]["value"] == 100.89 and rows["FPG"]["unit_as_reported"] == "mmol/L"
    assert all(row["source"] == "ocr" and not row["is_confirmed"] for row in rows.values())


def test_ocr_values_count_only_after_the_patient_confirms(make_patient, engine):
    engine.text = "HbA1c 6.1 %"
    _, headers = make_patient()

    def missing_labs():
        t2d = client.get("/assessment/readiness", headers=headers).json()["models"]["T2D"]
        return len([item for item in t2d["missing_optional"] if item["source"] == "lab_ocr"])

    before = missing_labs()
    document_id = upload(headers)
    assert missing_labs() == before  # read by OCR, not confirmed yet
    assert client.post(f"/documents/{document_id}/confirm", headers=headers).status_code == 200
    assert missing_labs() == before - 1


def test_a_report_with_no_values_found_still_goes_to_review(make_patient, engine):
    engine.text = "This page is blurry"
    _, headers = make_patient()
    document_id = upload(headers)
    review = client.get(f"/documents/{document_id}/review", headers=headers).json()
    assert review["document"]["status"] == "ready_for_review" and review["lab_results"] == []
    confirmed = client.post(f"/documents/{document_id}/confirm", headers=headers,
                            json={"typed_values": [{"test_code": "FPG", "value": 95, "unit": "mg/dL"}]})
    assert confirmed.status_code == 200


def test_an_engine_error_marks_the_document_failed_with_its_safe_message(make_patient, engine):
    engine.error = ocr.OcrFailed("This file has no readable text. Please type the values in.")
    _, headers = make_patient()
    document = client.get(f"/documents/{upload(headers)}", headers=headers).json()
    assert document["status"] == "failed"
    assert document["error_message"] == "This file has no readable text. Please type the values in."


def test_an_engine_crash_never_stores_its_message(make_patient, engine):
    engine.error = RuntimeError("could not parse: Juan Dela Cruz FBS 5.6")
    _, headers = make_patient()
    document = client.get(f"/documents/{upload(headers)}", headers=headers).json()
    assert document["status"] == "failed" and "Juan" not in document["error_message"]
    assert client.get("/labs", headers=headers).json() == []


def test_a_changed_file_on_disk_fails_the_integrity_check(make_patient, engine, monkeypatch):
    monkeypatch.setattr(settings, "ocr_engine", "none")  # upload first, run OCR by hand after
    email, headers = make_patient()
    document_id = upload(headers)
    document = document_of(email, document_id)
    storage.save(document.storage_key, PNG + b"edited")
    monkeypatch.setattr(settings, "ocr_engine", "fake")
    assert worker.process_document(document_id, document.patient_id) == "failed"
    assert "integrity" in client.get(f"/documents/{document_id}", headers=headers).json()["error_message"]


def test_with_ocr_off_the_upload_stays_pending(make_patient):
    _, headers = make_patient()
    assert settings.ocr_engine == "none"
    document = client.get(f"/documents/{upload(headers)}", headers=headers).json()
    assert document["status"] == "pending"


def test_running_the_job_twice_does_not_save_values_twice(make_patient, engine):
    email, headers = make_patient()
    document_id = upload(headers)
    patient_id = document_of(email, document_id).patient_id
    assert worker.process_document(document_id, patient_id) == "skipped"
    assert len(client.get("/labs", headers=headers).json()) == 8


def test_the_job_cannot_touch_another_patients_document(make_patient, engine, monkeypatch):
    monkeypatch.setattr(settings, "ocr_engine", "none")
    owner_email, owner = make_patient()
    other_email, other = make_patient()
    document_id = upload(owner)
    other_patient_id = document_of(other_email, upload(other)).patient_id
    monkeypatch.setattr(settings, "ocr_engine", "fake")
    assert worker.process_document(document_id, other_patient_id) == "skipped"  # RLS hides it
    assert document_of(owner_email, document_id).status == "pending"


def test_ocr_is_audited_with_counts_only(make_patient, engine, monkeypatch):
    recorded = []
    real = audit.log_event

    def recording(db, event_type, **kwargs):
        recorded.append((event_type, kwargs))
        return real(db, event_type, **kwargs)

    monkeypatch.setattr(audit, "log_event", recording)
    _, headers = make_patient()
    document_id = upload(headers)
    event_type, details = recorded[-1]
    assert event_type == "DATA_CREATED" and details["document_id"] == document_id
    assert details["source"] == "ocr" and details["values_found"] == 8 and details["skipped_lines"] == 0
    assert "100.89" not in str(details) and "Juan" not in str(details)


def test_with_redis_on_the_job_goes_to_the_rq_queue(make_patient, engine, monkeypatch):
    import rq
    queued = []

    class FakeQueue:
        def __init__(self, name, connection):
            self.name = name

        def enqueue(self, func, *args, **kwargs):
            queued.append((self.name, func, args))

    monkeypatch.setattr(rq, "Queue", FakeQueue)
    monkeypatch.setattr(settings, "redis_enabled", True)
    _, headers = make_patient()
    document_id = upload(headers)
    assert queued and queued[0][0] == "ocr" and queued[0][1] is worker.process_document
    assert queued[0][2][0] == document_id
    # not processed in the API process: it waits for the worker
    assert client.get(f"/documents/{document_id}", headers=headers).json()["status"] == "pending"


def test_an_unknown_engine_name_is_refused(monkeypatch):
    monkeypatch.setattr(settings, "ocr_engine", "typo")
    with pytest.raises(RuntimeError):
        ocr.get_engine()
