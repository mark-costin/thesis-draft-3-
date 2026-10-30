"""Goals: can be added, completed and deleted, only by their owner."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func

from backend import audit
from backend.config import settings
from backend.database import SessionLocal
from backend.main import app
from backend.models import User

client = TestClient(app)
PASSWORD = "correct horse battery"


@pytest.fixture
def make_patient():
    """Registers and logs in patients; returns auth headers. Deleted after the test."""
    made = []

    def make():
        email = f"goals-{uuid.uuid4().hex[:8]}@test.local"
        made.append(email)
        assert client.post("/auth/register", json={
            "email": email, "password": PASSWORD, "first_name": "Marcela", "last_name": "Agoncillo",
            "date_of_birth": "1970-06-24", "biological_sex": "Female",
            "privacy_consent": True, "consent_version": settings.privacy_notice_version,
        }).status_code == 201
        token = client.post("/auth/login", json={"email": email, "password": PASSWORD}).json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    yield make
    with SessionLocal() as session:
        session.execute(delete(User).where(func.lower(User.email).in_(made)))
        session.commit()


def add(headers, **body):
    return client.post("/goals", headers=headers, json={"description": "Walk 30 minutes a day"} | body)


def test_goal_can_be_added_completed_and_deleted(make_patient):
    headers = make_patient()
    created = add(headers, disease_domain="CVD", target_metric="physical_activity_min_week >= 150")
    assert created.status_code == 201
    goal = created.json()
    assert goal["source"] == "self" and goal["is_completed"] is False and goal["completed_at"] is None
    assert created.headers["Location"] == f"/goals/{goal['goal_id']}"

    done = client.patch(f"/goals/{goal['goal_id']}", headers=headers, json={"is_completed": True}).json()
    assert done["is_completed"] is True and done["completed_at"] is not None

    undone = client.patch(f"/goals/{goal['goal_id']}", headers=headers, json={"is_completed": False}).json()
    assert undone["is_completed"] is False and undone["completed_at"] is None

    assert client.delete(f"/goals/{goal['goal_id']}", headers=headers).status_code == 204
    assert client.get(f"/goals/{goal['goal_id']}", headers=headers).status_code == 404


def test_goal_can_be_reworded(make_patient):
    headers = make_patient()
    goal_id = add(headers).json()["goal_id"]
    edited = client.patch(f"/goals/{goal_id}", headers=headers,
                          json={"description": "Walk 45 minutes a day", "target_metric": ""}).json()
    assert edited["description"] == "Walk 45 minutes a day" and edited["target_metric"] is None


def test_list_filters_open_and_completed_open_first(make_patient):
    headers = make_patient()
    first = add(headers, description="Sleep 7 hours").json()["goal_id"]
    add(headers, description="Drink water instead of soda")
    client.patch(f"/goals/{first}", headers=headers, json={"is_completed": True})

    everything = client.get("/goals", headers=headers)
    assert everything.headers["Cache-Control"] == "no-store"
    assert [g["description"] for g in everything.json()] == ["Drink water instead of soda", "Sleep 7 hours"]
    assert [g["description"] for g in client.get("/goals?status=open", headers=headers).json()] == ["Drink water instead of soda"]
    assert [g["description"] for g in client.get("/goals?status=completed", headers=headers).json()] == ["Sleep 7 hours"]
    assert client.get("/goals?status=done", headers=headers).status_code == 422


def test_goals_are_per_patient(make_patient):
    headers_a, headers_b = make_patient(), make_patient()
    goal_id = add(headers_a).json()["goal_id"]
    assert client.get("/goals", headers=headers_b).json() == []
    assert client.get(f"/goals/{goal_id}", headers=headers_b).status_code == 404
    assert client.patch(f"/goals/{goal_id}", headers=headers_b, json={"is_completed": True}).status_code == 404
    assert client.delete(f"/goals/{goal_id}", headers=headers_b).status_code == 404
    assert client.get(f"/goals/{goal_id}", headers=headers_a).json()["is_completed"] is False


def test_html_is_removed_from_goal_text(make_patient):
    headers = make_patient()
    goal = add(headers, description="<b>Walk</b> & stretch<script>alert(1)</script>").json()
    assert goal["description"] == "Walk & stretch"
    assert add(headers, description="<script>alert(1)</script>").status_code == 422  # nothing left


def test_unknown_result_id_is_refused(make_patient):
    headers = make_patient()
    response = add(headers, result_id=999999)
    assert response.status_code == 422
    assert client.get("/goals", headers=headers).json() == []


@pytest.mark.parametrize("body", [
    {"description": ""},
    {"description": "x" * 256},
    {"disease_domain": "CANCER"},
    {"target_metric": "x" * 101},
    {"source": "suggested"},
    {"is_completed": True},
    {"patient_id": 1},
    {"result_id": 0},
], ids=["empty", "too-long", "bad-domain", "long-target", "source-sent", "completed-on-create",
        "patient-id-sent", "result-id-zero"])
def test_invalid_goals_are_rejected(make_patient, body):
    headers = make_patient()
    assert add(headers, **body).status_code == 422


@pytest.mark.parametrize("body", [{}, {"is_completed": None}, {"description": None}, {"source": "self"},
                                  {"completed_at": "2026-01-01T00:00:00+00:00"}],
                         ids=["empty", "null-completed", "null-description", "source", "completed-at"])
def test_invalid_goal_updates_are_rejected(make_patient, body):
    headers = make_patient()
    goal_id = add(headers).json()["goal_id"]
    assert client.patch(f"/goals/{goal_id}", headers=headers, json=body).status_code == 422


def test_goals_need_login():
    assert client.get("/goals").status_code == 401


def test_audit_has_field_names_not_text(make_patient, monkeypatch):
    recorded = []
    real = audit.log_event

    def recording(db, event_type, **kwargs):
        recorded.append((event_type, kwargs))
        return real(db, event_type, **kwargs)

    monkeypatch.setattr(audit, "log_event", recording)
    headers = make_patient()
    goal_id = add(headers, description="Quit smoking by December").json()["goal_id"]
    client.patch(f"/goals/{goal_id}", headers=headers, json={"is_completed": True})
    client.delete(f"/goals/{goal_id}", headers=headers)

    created, updated, deleted = recorded[-3:]
    assert created[0] == "DATA_CREATED" and created[1]["fields"] == ["description"]
    assert updated[0] == "DATA_UPDATED" and updated[1]["fields"] == ["is_completed"]
    assert deleted[0] == "DATA_DELETED" and deleted[1]["record_id"] == goal_id
    assert "smoking" not in str(recorded[-3:])
