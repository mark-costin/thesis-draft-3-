"""Account export (right to access) and permanent deletion (right to erasure)."""

import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from backend import audit
from backend.config import settings
from backend.database import SessionLocal, set_request_identity
from backend.main import app
from backend.models import (
    LifestyleRecord,
    Patient,
    PatientFamilyHistory,
    PatientFhrsStratification,
    PatientGoal,
    PatientHealthHistory,
    QuestionnaireResponse,
    User,
    VitalsRecord,
)
from backend.schemas import DELETE_CONFIRMATION

client = TestClient(app)
PASSWORD = "correct horse battery"
PATIENT_TABLES = (VitalsRecord, LifestyleRecord, PatientHealthHistory, QuestionnaireResponse,
                  PatientFamilyHistory, PatientFhrsStratification, PatientGoal)


@pytest.fixture
def make_patient():
    """Registers and logs in patients; returns (email, headers). Deleted after the test if still there."""
    made = []

    def make():
        email = f"acct-{uuid.uuid4().hex[:8]}@test.local"
        made.append(email)
        register(email)
        return email, login_headers(email)

    yield make
    with SessionLocal() as session:
        session.execute(delete(User).where(func.lower(User.email).in_(made)))
        session.commit()


def register(email):
    assert client.post("/auth/register", json={
        "email": email, "password": PASSWORD, "first_name": "Apolinario", "last_name": "Mabini",
        "date_of_birth": "1964-07-23", "biological_sex": "Male",
        "privacy_consent": True, "consent_version": settings.privacy_notice_version,
    }).status_code == 201


def login_headers(email):
    token = client.post("/auth/login", json={"email": email, "password": PASSWORD}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def fill_everything(headers):
    client.post("/vitals", headers=headers, json={"sbp": 130, "dbp": 85, "weight_kg": 70.0, "height_cm": 170.0})
    client.post("/lifestyle", headers=headers, json={"smoking_status": "never", "sleep_hours_day": 7.0})
    client.put("/health-history", headers=headers, json={"allergic_rhinitis": True})
    client.post("/questionnaires", headers=headers, json={"instrument": "GAD7", "answers": [1] * 7})
    client.post("/goals", headers=headers, json={"description": "Walk every morning"})
    client.post("/family-history", headers=headers, json={
        "disease_domain": "T2D", "specific_diagnosis": "Type 2 diabetes", "relative": "mother"})


def ids_of(email):
    with SessionLocal() as session:
        user_id = session.scalar(select(User.user_id).where(User.email == email))
        set_request_identity(session, user_id=user_id)
        return user_id, session.scalar(select(Patient.patient_id))


def count_rows(patient_id):
    """Counts each table's rows for this patient id. RLS shows exactly that patient's rows,
    so a 0 here means the rows are really gone."""
    with SessionLocal() as session:
        set_request_identity(session, user_id=None, patient_id=patient_id)
        return {model.__tablename__: session.scalar(select(func.count()).select_from(model))
                for model in PATIENT_TABLES}


def delete_account(headers, password=PASSWORD, confirm=DELETE_CONFIRMATION):
    return client.post("/account/delete", headers=headers, json={"password": password, "confirm": confirm})


# --- Export ----------------------------------------------------------------------------------

def test_export_holds_everything_about_the_patient(make_patient):
    email, headers = make_patient()
    fill_everything(headers)
    response = client.get("/account/export", headers=headers)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Content-Disposition"].startswith('attachment; filename="my-health-data-')
    data = json.loads(response.content)

    assert data["account"]["email"] == email
    assert data["profile"]["first_name"] == "Apolinario"
    assert data["vitals"][0]["sbp"] == 130 and data["vitals"][0]["bmi"] == 24.2
    assert data["lifestyle"][0]["smoking_status"] == "never"
    assert data["health_history"][0]["allergic_rhinitis"] is True
    assert data["questionnaires"][0]["total_score"] == 7
    assert data["goals"][0]["description"] == "Walk every morning"
    assert data["family_history"][0]["relative"] == "mother"
    assert {row["disease_domain"] for row in data["family_history_scores"]} == {"CVD", "T2D", "CRD", "NEURO"}
    assert data["lab_results"] == [] and data["documents"] == [] and data["health_reports"] == []


def test_export_never_holds_secrets_or_internal_ids(make_patient):
    _, headers = make_patient()
    text = client.get("/account/export", headers=headers).text
    for secret in ("password_hash", "argon2", "token_version", "patient_id", "user_id", "storage_key"):
        assert secret not in text, secret


def test_export_holds_only_my_data(make_patient):
    _, headers_a = make_patient()
    _, headers_b = make_patient()
    client.post("/vitals", headers=headers_b, json={"resting_hr": 99})
    data = client.get("/account/export", headers=headers_a).json()
    assert data["vitals"] == []


def test_export_is_audited(make_patient, monkeypatch):
    recorded = []
    real = audit.log_event
    monkeypatch.setattr(audit, "log_event",
                        lambda db, event_type, **kw: recorded.append((event_type, kw)) or real(db, event_type, **kw))
    _, headers = make_patient()
    client.get("/account/export", headers=headers)
    assert recorded[-1][0] == "DATA_EXPORTED"


# --- Deletion --------------------------------------------------------------------------------

def test_deletion_removes_every_patient_row(make_patient):
    email, headers = make_patient()
    fill_everything(headers)
    user_id, patient_id = ids_of(email)
    assert all(n > 0 for n in count_rows(patient_id).values())

    assert delete_account(headers).status_code == 204

    assert all(n == 0 for n in count_rows(patient_id).values())
    with SessionLocal() as session:
        assert session.get(User, user_id) is None


def test_deleted_account_cannot_log_in_and_old_token_stops_working(make_patient):
    email, headers = make_patient()
    delete_account(headers)
    assert client.get("/profile", headers=headers).status_code == 401
    assert client.post("/auth/login", json={"email": email, "password": PASSWORD}).status_code == 401


def test_email_can_register_again_after_deletion(make_patient):
    email, headers = make_patient()
    delete_account(headers)
    register(email)  # a fresh, empty account
    assert client.get("/vitals", headers=login_headers(email)).json() == []


def test_deletion_keeps_the_audit_entry(make_patient, monkeypatch):
    recorded = []
    real = audit.log_event
    monkeypatch.setattr(audit, "log_event",
                        lambda db, event_type, **kw: recorded.append((event_type, kw)) or real(db, event_type, **kw))
    email, headers = make_patient()
    user_id, patient_id = ids_of(email)
    delete_account(headers)
    event_type, details = recorded[-1]
    assert event_type == "ACCOUNT_DELETED"
    assert (details["user_id"], details["patient_id"], details["files"]) == (user_id, patient_id, 0)


def test_other_patients_are_untouched(make_patient):
    _, headers_a = make_patient()
    email_b, headers_b = make_patient()
    fill_everything(headers_b)
    delete_account(headers_a)
    _, patient_b = ids_of(email_b)
    assert all(n > 0 for n in count_rows(patient_b).values())


@pytest.mark.parametrize("password, confirm, expected", [
    ("wrong password", DELETE_CONFIRMATION, 403),
    (PASSWORD, "delete my account", 422),
    (PASSWORD, "", 422),
], ids=["wrong-password", "phrase-lowercase", "no-phrase"])
def test_deletion_needs_password_and_exact_phrase(make_patient, password, confirm, expected):
    email, headers = make_patient()
    assert delete_account(headers, password, confirm).status_code == expected
    assert client.get("/profile", headers=headers).status_code == 200  # nothing was deleted


def test_account_endpoints_need_login():
    assert client.get("/account/export").status_code == 401
    assert client.post("/account/delete", json={"password": PASSWORD, "confirm": DELETE_CONFIRMATION}).status_code == 401
