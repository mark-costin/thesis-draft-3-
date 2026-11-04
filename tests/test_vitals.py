"""Vitals endpoints: saved vitals return the BMI computed by the database; each patient sees
and changes only their own entries."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from backend import audit
from backend.config import settings
from backend.database import SessionLocal, set_request_identity
from backend.main import app
from backend.middleware import REPLAY_HEADER
from backend.models import Patient, User, VitalsRecord

client = TestClient(app)
PASSWORD = "correct horse battery"
FULL = {"sbp": 128, "dbp": 82, "resting_hr": 72, "weight_kg": 70.0, "height_cm": 175.0,
        "waist_cm": 84.5, "blood_glucose_mgdl": 96.0, "glucose_context": "fasting", "spo2": 98.0}


@pytest.fixture
def make_patient():
    """Registers and logs in patients; returns (email, auth headers). Deleted after the test."""
    made = []

    def make():
        email = f"vitals-{uuid.uuid4().hex[:8]}@test.local"
        made.append(email)
        assert client.post("/auth/register", json={
            "email": email, "password": PASSWORD, "first_name": "Melchora", "last_name": "Aquino",
            "date_of_birth": "1975-01-06", "biological_sex": "Female",
            "privacy_consent": True, "consent_version": settings.privacy_notice_version,
        }).status_code == 201
        token = client.post("/auth/login", json={"email": email, "password": PASSWORD}).json()["access_token"]
        return email, {"Authorization": f"Bearer {token}"}

    yield make
    with SessionLocal() as session:  # deleting the user cascades to the vitals
        session.execute(delete(User).where(func.lower(User.email).in_(made)))
        session.commit()


def rows_of(email):
    with SessionLocal() as session:
        user_id = session.scalar(select(User.user_id).where(User.email == email))
        set_request_identity(session, user_id=user_id)  # patients RLS: own row by user_id
        patient_id = session.scalar(select(Patient.patient_id))
        set_request_identity(session, user_id=user_id, patient_id=patient_id)
        return list(session.scalars(select(VitalsRecord)))


def test_saved_vitals_return_the_computed_bmi(make_patient):
    _, headers = make_patient()
    response = client.post("/vitals", headers=headers, json=FULL)
    assert response.status_code == 201
    data = response.json()
    assert data["bmi"] == 22.9  # 70 / 1.75^2 = 22.86, rounded by the database
    assert data["sbp"] == 128 and data["glucose_context"] == "fasting"
    assert response.headers["Location"] == f"/vitals/{data['vitals_id']}"
    assert response.headers["Cache-Control"] == "no-store"
    assert "patient_id" not in data


def test_partial_entry_has_no_bmi(make_patient):
    _, headers = make_patient()
    data = client.post("/vitals", headers=headers, json={"sbp": 120, "dbp": 80}).json()
    assert data["bmi"] is None and data["weight_kg"] is None


def test_list_is_newest_first_and_only_mine(make_patient):
    _, headers_a = make_patient()
    _, headers_b = make_patient()
    now = datetime.now(timezone.utc)
    client.post("/vitals", headers=headers_a, json={"resting_hr": 60, "measured_at": (now - timedelta(days=2)).isoformat()})
    client.post("/vitals", headers=headers_a, json={"resting_hr": 70, "measured_at": (now - timedelta(days=1)).isoformat()})
    client.post("/vitals", headers=headers_b, json={"resting_hr": 99})

    listing = client.get("/vitals", headers=headers_a)
    assert listing.status_code == 200
    assert [v["resting_hr"] for v in listing.json()] == [70, 60]
    assert listing.headers["Cache-Control"] == "no-store"
    assert [v["resting_hr"] for v in client.get("/vitals", headers=headers_b).json()] == [99]
    assert len(client.get("/vitals?limit=1", headers=headers_a).json()) == 1


def test_other_patients_entries_look_like_they_do_not_exist(make_patient):
    email_a, headers_a = make_patient()
    _, headers_b = make_patient()
    vitals_id = client.post("/vitals", headers=headers_a, json={"spo2": 97.5}).json()["vitals_id"]

    assert client.get(f"/vitals/{vitals_id}", headers=headers_a).status_code == 200
    assert client.get(f"/vitals/{vitals_id}", headers=headers_b).status_code == 404
    assert client.delete(f"/vitals/{vitals_id}", headers=headers_b).status_code == 404
    assert len(rows_of(email_a)) == 1


def test_delete_own_entry(make_patient):
    email, headers = make_patient()
    vitals_id = client.post("/vitals", headers=headers, json={"weight_kg": 65.2}).json()["vitals_id"]
    assert client.delete(f"/vitals/{vitals_id}", headers=headers).status_code == 204
    assert client.get(f"/vitals/{vitals_id}", headers=headers).status_code == 404
    assert rows_of(email) == []


def test_vitals_need_login():
    assert client.get("/vitals").status_code == 401
    assert client.post("/vitals", json={"resting_hr": 70}).status_code == 401


FUTURE = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()


@pytest.mark.parametrize("body", [
    {},
    {"sbp": 300, "dbp": 80},
    {"sbp": 120},
    {"dbp": 80},
    {"sbp": 80, "dbp": 90},
    {"resting_hr": 10},
    {"weight_kg": 500},
    {"weight_kg": 70.25},
    {"height_cm": 50},
    {"spo2": 101},
    {"blood_glucose_mgdl": 100},
    {"glucose_context": "fasting"},
    {"blood_glucose_mgdl": 100, "glucose_context": "after_lunch"},
    {"resting_hr": 70, "measured_at": FUTURE},
    {"resting_hr": 70, "measured_at": "2026-10-30T08:00:00"},
    {"resting_hr": 70, "bmi": 22.0},
    {"resting_hr": 70, "patient_id": 1},
], ids=["empty", "sbp-high", "sbp-only", "dbp-only", "sbp-not-above-dbp", "hr-low", "weight-high",
        "two-decimals", "height-low", "spo2-high", "glucose-no-context", "context-no-glucose",
        "bad-context", "future", "no-timezone", "bmi-sent", "patient-id-sent"])
def test_invalid_vitals_are_rejected(make_patient, body):
    email, headers = make_patient()
    assert client.post("/vitals", headers=headers, json=body).status_code == 422
    assert rows_of(email) == []


def test_double_submit_with_idempotency_key_saves_once_and_keeps_no_health_data(make_patient):
    email, headers = make_patient()
    with_key = headers | {"Idempotency-Key": "vitals-" + uuid.uuid4().hex}
    first = client.post("/vitals", headers=with_key, json=FULL)
    second = client.post("/vitals", headers=with_key, json=FULL)
    assert first.status_code == second.status_code == 201
    assert second.headers[REPLAY_HEADER] == "true"
    assert second.headers["Location"] == first.headers["Location"]
    assert "128" not in second.text and "bmi" not in second.text  # no health values replayed
    assert len(rows_of(email)) == 1
    assert client.get(second.headers["Location"], headers=headers).json()["bmi"] == 22.9


def test_create_and_delete_are_audited_without_values(make_patient, monkeypatch):
    recorded = []
    real = audit.log_event

    def recording(db, event_type, **kwargs):
        recorded.append((event_type, kwargs))
        return real(db, event_type, **kwargs)

    monkeypatch.setattr(audit, "log_event", recording)
    _, headers = make_patient()
    vitals_id = client.post("/vitals", headers=headers, json={"sbp": 135, "dbp": 85}).json()["vitals_id"]
    client.delete(f"/vitals/{vitals_id}", headers=headers)

    created, deleted = recorded[-2], recorded[-1]
    assert created[0] == "DATA_CREATED" and created[1]["table"] == "vitals_records"
    assert created[1]["fields"] == ["dbp", "sbp"] and created[1]["record_id"] == vitals_id
    assert "135" not in str(created)
    assert deleted[0] == "DATA_DELETED" and deleted[1]["record_id"] == vitals_id
