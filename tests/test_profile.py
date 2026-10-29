"""Profile endpoints: a patient sees and edits only their own profile."""

import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from backend import audit
from backend.config import settings
from backend.database import SessionLocal, set_request_identity
from backend.main import app
from backend.middleware import REPLAY_HEADER
from backend.models import Patient, User

client = TestClient(app)
PASSWORD = "correct horse battery"


@pytest.fixture
def make_patient():
    """Registers and logs in patients; returns (email, auth headers). Deleted after the test."""
    made = []

    def make(first_name="Gabriela", last_name="Silang", born="1985-03-19", sex="Female"):
        email = f"profile-{uuid.uuid4().hex[:8]}@test.local"
        made.append(email)
        assert client.post("/auth/register", json={
            "email": email, "password": PASSWORD, "first_name": first_name, "last_name": last_name,
            "date_of_birth": born, "biological_sex": sex,
            "privacy_consent": True, "consent_version": settings.privacy_notice_version,
        }).status_code == 201
        token = client.post("/auth/login", json={"email": email, "password": PASSWORD}).json()["access_token"]
        return email, {"Authorization": f"Bearer {token}"}

    yield make
    with SessionLocal() as session:
        session.execute(delete(User).where(func.lower(User.email).in_(made)))
        session.commit()


def patient_row(email) -> Patient:
    """Reads a patient row the way the app would: as that patient (RLS)."""
    with SessionLocal() as session:
        user_id = session.scalar(select(User.user_id).where(User.email == email))
        set_request_identity(session, user_id=user_id)
        return session.scalars(select(Patient)).one()


def years_ago(years):
    today = date.today()
    try:
        return today.replace(year=today.year - years)
    except ValueError:  # 29 February
        return today.replace(year=today.year - years, day=28)


def test_patient_sees_own_profile(make_patient):
    email, headers = make_patient()
    response = client.get("/profile", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == email
    assert (data["first_name"], data["last_name"], data["biological_sex"]) == ("Gabriela", "Silang", "Female")
    assert data["date_of_birth"] == "1985-03-19"
    born = date(1985, 3, 19)
    today = date.today()
    assert data["age"] == today.year - 1985 - ((today.month, today.day) < (born.month, born.day))
    assert data["consent_version"] == settings.privacy_notice_version
    assert "patient_id" not in data and "user_id" not in data and "password" not in str(data)


def test_profile_needs_login():
    assert client.get("/profile").status_code == 401
    assert client.patch("/profile", json={"first_name": "X"}).status_code == 401


def test_patient_edits_own_profile(make_patient):
    email, headers = make_patient()
    response = client.patch("/profile", headers=headers, json={
        "first_name": "  Gabriela Josefa ", "date_of_birth": "1980-01-02", "biological_sex": "Female",
    })
    assert response.status_code == 200
    assert response.json()["first_name"] == "Gabriela Josefa"
    row = patient_row(email)
    assert row.first_name == "Gabriela Josefa"
    assert row.last_name == "Silang"  # not sent, so not changed
    assert row.date_of_birth == date(1980, 1, 2)


def test_edit_only_touches_the_callers_profile(make_patient):
    email_a, headers_a = make_patient(first_name="Ana")
    email_b, _ = make_patient(first_name="Bea")
    client.patch("/profile", headers=headers_a, json={"last_name": "Changed"})
    assert patient_row(email_a).last_name == "Changed"
    assert patient_row(email_b).last_name == "Silang"
    assert client.get("/profile", headers=headers_a).json()["email"] == email_a


@pytest.mark.parametrize("body", [
    {},
    {"first_name": "   "},
    {"first_name": None},
    {"last_name": "x" * 101},
    {"biological_sex": "Other"},
    {"date_of_birth": years_ago(17).isoformat()},
    {"date_of_birth": "2999-01-01"},
    {"date_of_birth": "1899-12-31"},
    {"email": "new@test.local"},
    {"consent_version": "9.9"},
    {"patient_id": 1},
    {"user_id": 1},
], ids=["empty", "blank-name", "null-name", "long-name", "bad-sex", "under-18", "future",
        "before-1900", "email", "consent", "patient-id", "user-id"])
def test_invalid_edits_are_rejected(make_patient, body):
    email, headers = make_patient()
    assert client.patch("/profile", headers=headers, json=body).status_code == 422
    row = patient_row(email)
    assert (row.first_name, row.last_name, row.biological_sex) == ("Gabriela", "Silang", "Female")


def test_edit_is_audited_with_field_names_only(make_patient, monkeypatch):
    recorded = []
    real = audit.log_event

    def recording(db, event_type, **kwargs):
        recorded.append((event_type, kwargs))
        return real(db, event_type, **kwargs)

    monkeypatch.setattr(audit, "log_event", recording)
    _, headers = make_patient()
    client.patch("/profile", headers=headers, json={"last_name": "Dela Paz", "first_name": "Gabriela"})
    event_type, details = recorded[-1]
    assert event_type == "DATA_UPDATED"
    assert details["table"] == "patients"
    assert details["fields"] == ["last_name"]  # first_name was unchanged
    assert "Dela Paz" not in str(details)


def test_no_change_writes_no_audit_entry(make_patient, monkeypatch):
    _, headers = make_patient()
    recorded = []
    monkeypatch.setattr(audit, "log_event", lambda db, event_type, **kw: recorded.append(event_type))
    assert client.patch("/profile", headers=headers, json={"first_name": "Gabriela"}).status_code == 200
    assert recorded == []


def test_profile_edits_are_not_stored_for_idempotent_replay(make_patient):
    email, headers = make_patient()
    with_key = headers | {"Idempotency-Key": "profile-" + uuid.uuid4().hex}
    client.patch("/profile", headers=with_key, json={"last_name": "First"})
    client.patch("/profile", headers=headers, json={"last_name": "Second"})
    again = client.patch("/profile", headers=with_key, json={"last_name": "First"})
    assert REPLAY_HEADER not in again.headers
    assert patient_row(email).last_name == "First"
