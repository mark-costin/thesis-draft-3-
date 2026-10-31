"""OCR review: values OCR read are not used until the patient confirms them.

There is no OCR worker yet, so fake_ocr() does what the worker will do: save the values it read
as unconfirmed lab rows and set the document to ready_for_review.
"""

import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, update

from backend import audit, labs
from backend.config import settings
from backend.database import SessionLocal, set_request_identity
from backend.main import app
from backend.models import Document, LabResult, Patient, User

client = TestClient(app)
PASSWORD = "correct horse battery"
PNG = b"\x89PNG\r\n\x1a\n" + b"lab report pixels "
REPORT = [("FBS", "5.6", "mmol/L"), ("Cholesterol", "5.2", "mmol/L"), ("HDL-C", "45", "mg/dL")]


@pytest.fixture(autouse=True)
def upload_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", tmp_path)
    return tmp_path


@pytest.fixture
def make_patient():
    made = []

    def make():
        email = f"review-{uuid.uuid4().hex[:8]}@test.local"
        made.append(email)
        assert client.post("/auth/register", json={
            "email": email, "password": PASSWORD, "first_name": "Teresa", "last_name": "Magbanua",
            "date_of_birth": "1985-10-13", "biological_sex": "Female",
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
    data = PNG + uuid.uuid4().bytes  # a different file each time (same file twice is refused)
    response = client.post("/documents", headers=headers, files={"file": ("lab.png", data, "image/png")})
    assert response.status_code == 201
    return response.json()["document_id"]


def fake_ocr(email, document_id, lines=REPORT, collected_on=None):
    """What the OCR worker will do: unconfirmed rows + status ready_for_review."""
    with SessionLocal() as session:
        patient_id = as_patient(session, email)
        for label, value, unit in lines:
            mapped = labs.map_field(label, value, unit)
            session.add(LabResult(patient_id=patient_id, document_id=document_id, test_code=mapped.test_code,
                                  value=mapped.value, unit=mapped.unit, value_as_reported=mapped.value_as_reported,
                                  unit_as_reported=mapped.unit_as_reported, source="ocr", collected_on=collected_on))
        session.execute(update(Document).where(Document.document_id == document_id)
                        .values(status="ready_for_review", ocr_text="FBS 5.6 mmol/L ...", ocr_confidence=91.5))
        session.commit()


def ocr_text_of(email, document_id):
    with SessionLocal() as session:
        as_patient(session, email)
        return session.scalar(select(Document.ocr_text).where(Document.document_id == document_id))


def lab_ids(review):
    return [row["lab_result_id"] for row in review["lab_results"]]


def ready_document(make_patient, **ocr):
    email, headers = make_patient()
    document_id = upload(headers)
    fake_ocr(email, document_id, **ocr)
    return email, headers, document_id


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------
def test_review_shows_what_ocr_read_converted_and_unconfirmed(make_patient):
    _, headers, document_id = ready_document(make_patient)
    response = client.get(f"/documents/{document_id}/review", headers=headers)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    data = response.json()
    assert data["document"]["status"] == "ready_for_review"
    assert "ocr_text" not in data["document"]
    fpg, cholesterol, hdl = data["lab_results"]
    assert (fpg["test_code"], fpg["value"], fpg["value_as_reported"], fpg["unit_as_reported"]) == ("FPG", 100.89, 5.6, "mmol/L")
    assert (cholesterol["test_code"], cholesterol["value"]) == ("TOTAL_CHOLESTEROL", 201.084)
    assert hdl["value_as_reported"] is None
    assert all(row["source"] == "ocr" and row["is_confirmed"] is False for row in data["lab_results"])


def test_unconfirmed_values_do_not_count_for_readiness(make_patient):
    _, headers, document_id = ready_document(make_patient, lines=[("HbA1c", "6.1", "%")])

    def missing_labs():
        t2d = client.get("/assessment/readiness", headers=headers).json()["models"]["T2D"]
        return len([item for item in t2d["missing_optional"] if item["source"] == "lab_ocr"])

    before = missing_labs()
    assert client.post(f"/documents/{document_id}/confirm", headers=headers).status_code == 200
    assert missing_labs() == before - 1


def test_review_of_another_patients_document_is_not_found(make_patient):
    _, _, document_id = ready_document(make_patient)
    _, other = make_patient()
    assert client.get(f"/documents/{document_id}/review", headers=other).status_code == 404
    assert client.post(f"/documents/{document_id}/confirm", headers=other).status_code == 404
    assert client.post(f"/documents/{document_id}/reject", headers=other).status_code == 404


# ---------------------------------------------------------------------------
# Fixing values before confirming
# ---------------------------------------------------------------------------
def test_a_misread_value_can_be_fixed(make_patient):
    _, headers, document_id = ready_document(make_patient)
    fpg_id = lab_ids(client.get(f"/documents/{document_id}/review", headers=headers).json())[0]
    response = client.patch(f"/labs/{fpg_id}", headers=headers, json={
        "test_code": "FPG", "value": 6.5, "unit": "mmol/L", "collected_on": "2026-10-20"})
    assert response.status_code == 200
    data = response.json()
    assert data["value"] == 117.104 and data["value_as_reported"] == 6.5
    assert data["collected_on"] == "2026-10-20" and data["is_confirmed"] is False


def test_fixing_to_the_standard_unit_clears_the_reported_pair(make_patient):
    _, headers, document_id = ready_document(make_patient)
    fpg_id = lab_ids(client.get(f"/documents/{document_id}/review", headers=headers).json())[0]
    data = client.patch(f"/labs/{fpg_id}", headers=headers, json={"test_code": "FPG", "value": 101, "unit": "mg/dL"}).json()
    assert data["value"] == 101.0 and data["value_as_reported"] is None and data["unit_as_reported"] is None


def test_fixing_with_a_wrong_unit_is_refused(make_patient):
    _, headers, document_id = ready_document(make_patient)
    fpg_id = lab_ids(client.get(f"/documents/{document_id}/review", headers=headers).json())[0]
    assert client.patch(f"/labs/{fpg_id}", headers=headers, json={"test_code": "FPG", "value": 5, "unit": "%"}).status_code == 422


def test_a_confirmed_value_cannot_be_edited(make_patient):
    _, headers = make_patient()
    lab_id = client.post("/labs", headers=headers, json={"test_code": "LDL", "value": 130, "unit": "mg/dL"}).json()["lab_result_id"]
    response = client.patch(f"/labs/{lab_id}", headers=headers, json={"test_code": "LDL", "value": 100, "unit": "mg/dL"})
    assert response.status_code == 409


def test_another_patient_cannot_fix_a_value(make_patient):
    _, headers, document_id = ready_document(make_patient)
    fpg_id = lab_ids(client.get(f"/documents/{document_id}/review", headers=headers).json())[0]
    _, other = make_patient()
    assert client.patch(f"/labs/{fpg_id}", headers=other, json={"test_code": "FPG", "value": 90, "unit": "mg/dL"}).status_code == 404


def test_a_wrong_line_can_be_removed_before_confirming(make_patient):
    _, headers, document_id = ready_document(make_patient)
    hdl_id = lab_ids(client.get(f"/documents/{document_id}/review", headers=headers).json())[2]
    assert client.delete(f"/labs/{hdl_id}", headers=headers).status_code == 204
    confirmed = client.post(f"/documents/{document_id}/confirm", headers=headers).json()
    assert [row["test_code"] for row in confirmed["lab_results"]] == ["FPG", "TOTAL_CHOLESTEROL"]


# ---------------------------------------------------------------------------
# Confirm and reject
# ---------------------------------------------------------------------------
def test_confirm_marks_every_value_and_the_document_verified(make_patient):
    email, headers, document_id = ready_document(make_patient)
    response = client.post(f"/documents/{document_id}/confirm", headers=headers, json={"collected_on": "2026-10-15"})
    assert response.status_code == 200
    data = response.json()
    assert data["document"]["status"] == "verified" and data["document"]["verified_at"] is not None
    assert all(row["is_confirmed"] and row["confirmed_at"] for row in data["lab_results"])
    assert {row["collected_on"] for row in data["lab_results"]} == {"2026-10-15"}
    assert ocr_text_of(email, document_id) is None  # data minimization: raw OCR text is cleared


def test_confirm_keeps_a_date_ocr_already_read(make_patient):
    _, headers, document_id = ready_document(make_patient, collected_on=date(2026, 9, 1))
    data = client.post(f"/documents/{document_id}/confirm", headers=headers, json={"collected_on": "2026-10-15"}).json()
    assert {row["collected_on"] for row in data["lab_results"]} == {"2026-09-01"}


def test_confirm_without_a_body_works(make_patient):
    _, headers, document_id = ready_document(make_patient)
    data = client.post(f"/documents/{document_id}/confirm", headers=headers).json()
    assert data["document"]["status"] == "verified"
    assert {row["collected_on"] for row in data["lab_results"]} == {None}


def test_confirm_rejects_a_future_date(make_patient):
    _, headers, document_id = ready_document(make_patient)
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    assert client.post(f"/documents/{document_id}/confirm", headers=headers,
                       json={"collected_on": tomorrow}).status_code == 422


def test_confirm_twice_is_refused(make_patient):
    _, headers, document_id = ready_document(make_patient)
    assert client.post(f"/documents/{document_id}/confirm", headers=headers).status_code == 200
    second = client.post(f"/documents/{document_id}/confirm", headers=headers)
    assert second.status_code == 409 and "verified" in second.json()["detail"]
    assert client.post(f"/documents/{document_id}/reject", headers=headers).status_code == 409


def test_a_document_still_waiting_for_ocr_cannot_be_confirmed(make_patient):
    _, headers = make_patient()
    document_id = upload(headers)
    response = client.post(f"/documents/{document_id}/confirm", headers=headers)
    assert response.status_code == 409 and "pending" in response.json()["detail"]
    assert client.get(f"/documents/{document_id}/review", headers=headers).json()["lab_results"] == []


def test_confirm_with_every_value_removed_is_refused(make_patient):
    _, headers, document_id = ready_document(make_patient, lines=[("FBS", "95", "mg/dL")])
    only = lab_ids(client.get(f"/documents/{document_id}/review", headers=headers).json())[0]
    client.delete(f"/labs/{only}", headers=headers)
    response = client.post(f"/documents/{document_id}/confirm", headers=headers)
    assert response.status_code == 409
    # nothing changed, so the patient can still reject it
    assert client.get(f"/documents/{document_id}", headers=headers).json()["status"] == "ready_for_review"
    assert client.post(f"/documents/{document_id}/reject", headers=headers).status_code == 200


def test_reject_drops_the_ocr_values_and_keeps_the_file(make_patient):
    email, headers, document_id = ready_document(make_patient)
    response = client.post(f"/documents/{document_id}/reject", headers=headers)
    assert response.status_code == 200 and response.json()["status"] == "rejected"
    assert client.get("/labs", headers=headers).json() == []
    assert client.get(f"/documents/{document_id}/file", headers=headers).status_code == 200
    assert ocr_text_of(email, document_id) is None
    assert client.post(f"/documents/{document_id}/confirm", headers=headers).status_code == 409


def test_deleting_a_document_in_review_drops_its_unconfirmed_values(make_patient):
    _, headers, document_id = ready_document(make_patient)
    assert client.delete(f"/documents/{document_id}", headers=headers).status_code == 204
    assert client.get("/labs", headers=headers).json() == []


def test_deleting_a_verified_document_keeps_the_confirmed_values(make_patient):
    _, headers, document_id = ready_document(make_patient)
    client.post(f"/documents/{document_id}/confirm", headers=headers)
    assert client.delete(f"/documents/{document_id}", headers=headers).status_code == 204
    kept = client.get("/labs", headers=headers).json()
    assert len(kept) == 3 and all(row["is_confirmed"] and row["document_id"] is None for row in kept)


def test_confirm_and_reject_are_audited_without_values(make_patient, monkeypatch):
    recorded = []
    real = audit.log_event

    def recording(db, event_type, **kwargs):
        recorded.append((event_type, kwargs))
        return real(db, event_type, **kwargs)

    monkeypatch.setattr(audit, "log_event", recording)
    _, headers, first = ready_document(make_patient)
    client.post(f"/documents/{first}/confirm", headers=headers)
    verified = recorded[-1]
    assert verified[0] == "DOCUMENT_VERIFIED" and verified[1]["record_id"] == first
    assert verified[1]["confirmed_values"] == 3 and "100.89" not in str(verified) and "FPG" not in str(verified)

    _, headers, second = ready_document(make_patient)
    client.post(f"/documents/{second}/reject", headers=headers)
    rejected = recorded[-1]
    assert rejected[0] == "DOCUMENT_REJECTED" and rejected[1]["dropped_values"] == 3
