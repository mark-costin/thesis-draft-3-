"""Lifestyle entries and health history: both save and load correctly, only for their owner."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func

from backend import audit
from backend.config import settings
from backend.database import SessionLocal
from backend.main import app
from backend.middleware import REPLAY_HEADER
from backend.models import User

client = TestClient(app)
PASSWORD = "correct horse battery"
FULL_LIFESTYLE = {"smoking_status": "former", "pack_years": 12.5, "physical_activity_min_week": 150,
                  "sedentary_hours_day": 8.0, "sleep_hours_day": 7.5, "sodium_mg_day": 2400,
                  "carbs_g_day": 250, "occupational_dust": True, "substance_use": "none"}


@pytest.fixture
def make_patient():
    """Registers and logs in patients; returns auth headers. Deleted after the test."""
    made = []

    def make(sex="Female"):
        email = f"life-{uuid.uuid4().hex[:8]}@test.local"
        made.append(email)
        assert client.post("/auth/register", json={
            "email": email, "password": PASSWORD, "first_name": "Teresa", "last_name": "Magbanua",
            "date_of_birth": "1980-10-13", "biological_sex": sex,
            "privacy_consent": True, "consent_version": settings.privacy_notice_version,
        }).status_code == 201
        token = client.post("/auth/login", json={"email": email, "password": PASSWORD}).json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    yield make
    with SessionLocal() as session:
        session.execute(delete(User).where(func.lower(User.email).in_(made)))
        session.commit()


# --- Lifestyle -------------------------------------------------------------------------------

def test_lifestyle_saves_and_loads(make_patient):
    headers = make_patient()
    created = client.post("/lifestyle", headers=headers, json=FULL_LIFESTYLE)
    assert created.status_code == 201
    data = created.json()
    for name, value in FULL_LIFESTYLE.items():
        assert data[name] == value
    assert created.headers["Location"] == f"/lifestyle/{data['lifestyle_id']}"
    assert created.headers["Cache-Control"] == "no-store"

    assert client.get(f"/lifestyle/{data['lifestyle_id']}", headers=headers).json() == data
    assert client.get("/lifestyle", headers=headers).json() == [data]


def test_lifestyle_list_is_newest_first_and_only_mine(make_patient):
    headers_a, headers_b = make_patient(), make_patient()
    client.post("/lifestyle", headers=headers_a, json={"sleep_hours_day": 6.0})
    client.post("/lifestyle", headers=headers_a, json={"sleep_hours_day": 8.0})
    client.post("/lifestyle", headers=headers_b, json={"sleep_hours_day": 5.0})
    assert [e["sleep_hours_day"] for e in client.get("/lifestyle", headers=headers_a).json()] == [8.0, 6.0]
    assert [e["sleep_hours_day"] for e in client.get("/lifestyle", headers=headers_b).json()] == [5.0]


def test_other_patients_lifestyle_is_hidden(make_patient):
    headers_a, headers_b = make_patient(), make_patient()
    entry_id = client.post("/lifestyle", headers=headers_a, json={"substance_use": "past"}).json()["lifestyle_id"]
    assert client.get(f"/lifestyle/{entry_id}", headers=headers_b).status_code == 404
    assert client.delete(f"/lifestyle/{entry_id}", headers=headers_b).status_code == 404
    assert client.delete(f"/lifestyle/{entry_id}", headers=headers_a).status_code == 204
    assert client.get(f"/lifestyle/{entry_id}", headers=headers_a).status_code == 404


@pytest.mark.parametrize("body", [
    {},
    {"smoking_status": "sometimes"},
    {"pack_years": 5},
    {"smoking_status": "never", "pack_years": 3},
    {"smoking_status": "current", "pack_years": 250},
    {"physical_activity_min_week": -1},
    {"physical_activity_min_week": 20000},
    {"sleep_hours_day": 25},
    {"sleep_hours_day": 7.25},
    {"sedentary_hours_day": 16, "sleep_hours_day": 10},
    {"sodium_mg_day": 30000},
    {"carbs_g_day": 2000},
    {"substance_use": "yes"},
    {"sleep_hours_day": 7, "patient_id": 1},
], ids=["empty", "bad-smoking", "pack-years-alone", "never-with-pack-years", "pack-years-high",
        "activity-negative", "activity-high", "sleep-high", "two-decimals", "over-24h",
        "sodium-high", "carbs-high", "bad-substance", "patient-id-sent"])
def test_invalid_lifestyle_is_rejected(make_patient, body):
    headers = make_patient()
    assert client.post("/lifestyle", headers=headers, json=body).status_code == 422
    assert client.get("/lifestyle", headers=headers).json() == []


def test_never_smoker_with_zero_pack_years_is_fine(make_patient):
    headers = make_patient()
    assert client.post("/lifestyle", headers=headers,
                       json={"smoking_status": "never", "pack_years": 0}).status_code == 201


def test_lifestyle_double_submit_saves_once(make_patient):
    headers = make_patient()
    with_key = headers | {"Idempotency-Key": "life-" + uuid.uuid4().hex}
    client.post("/lifestyle", headers=with_key, json=FULL_LIFESTYLE)
    second = client.post("/lifestyle", headers=with_key, json=FULL_LIFESTYLE)
    assert second.headers[REPLAY_HEADER] == "true"
    assert "2400" not in second.text
    assert len(client.get("/lifestyle", headers=headers).json()) == 1


# --- Health history --------------------------------------------------------------------------

def test_history_is_empty_before_the_first_answer(make_patient):
    headers = make_patient()
    response = client.get("/health-history", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"gestational_diabetes": None, "acanthosis_nigricans": None,
                               "allergic_rhinitis": None, "childhood_asthma": None,
                               "tbi_history": None, "updated_at": None}
    assert response.headers["Cache-Control"] == "no-store"


def test_history_saves_loads_and_is_replaced_as_a_whole(make_patient):
    headers = make_patient()
    first = client.put("/health-history", headers=headers, json={
        "gestational_diabetes": True, "allergic_rhinitis": False, "tbi_history": False})
    assert first.status_code == 200
    assert first.json()["gestational_diabetes"] is True and first.json()["updated_at"] is not None
    assert client.get("/health-history", headers=headers).json() == first.json()

    # PUT replaces everything: fields left out go back to "don't know" (null)
    second = client.put("/health-history", headers=headers, json={"childhood_asthma": True}).json()
    assert second["childhood_asthma"] is True
    assert second["gestational_diabetes"] is None and second["allergic_rhinitis"] is None


def test_gestational_diabetes_is_for_female_patients_only(make_patient):
    headers = make_patient(sex="Male")
    assert client.put("/health-history", headers=headers, json={"gestational_diabetes": False}).status_code == 422
    assert client.put("/health-history", headers=headers, json={"allergic_rhinitis": True}).status_code == 200


def test_history_is_per_patient(make_patient):
    headers_a, headers_b = make_patient(), make_patient()
    client.put("/health-history", headers=headers_a, json={"childhood_asthma": True})
    assert client.get("/health-history", headers=headers_b).json()["childhood_asthma"] is None


@pytest.mark.parametrize("body", [{"childhood_asthma": "maybe"}, {"diabetes": True}, {"patient_id": 1}],
                         ids=["not-a-boolean", "unknown-question", "patient-id-sent"])
def test_invalid_history_is_rejected(make_patient, body):
    headers = make_patient()
    assert client.put("/health-history", headers=headers, json=body).status_code == 422


def test_audit_entries_hold_field_names_only(make_patient, monkeypatch):
    recorded = []
    real = audit.log_event

    def recording(db, event_type, **kwargs):
        recorded.append((event_type, kwargs))
        return real(db, event_type, **kwargs)

    monkeypatch.setattr(audit, "log_event", recording)
    headers = make_patient()
    client.post("/lifestyle", headers=headers, json={"smoking_status": "current", "pack_years": 20})
    client.put("/health-history", headers=headers, json={"tbi_history": True})
    client.put("/health-history", headers=headers, json={"tbi_history": False})

    lifestyle, history_first, history_again = recorded[-3:]
    assert lifestyle[0] == "DATA_CREATED" and lifestyle[1]["table"] == "lifestyle_records"
    assert lifestyle[1]["fields"] == ["pack_years", "smoking_status"]
    assert history_first[0] == "DATA_CREATED" and history_again[0] == "DATA_UPDATED"
    assert history_again[1]["table"] == "patient_health_history" and history_again[1]["fields"] == ["tbi_history"]
    assert "current" not in str(recorded[-3:]) and "True" not in str(recorded[-2:])
