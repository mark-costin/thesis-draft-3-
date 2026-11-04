"""Goals: can be added, completed and deleted, only by their owner."""

import re
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from backend import audit
from backend.config import settings
from backend.database import SessionLocal, set_request_identity
from backend.main import app
from backend.models import Patient, User

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


@pytest.mark.parametrize("description", ["&lt;script&gt;alert(1)&lt;/script&gt;", "&lt;b&gt;hi&lt;/b&gt;"],
                         ids=["escaped-script", "escaped-bold"])
def test_escaped_html_does_not_come_back_as_tags(make_patient, description):
    """Unescaping after nh3 must not turn "&lt;script&gt;" into a real <script> tag."""
    headers = make_patient()
    response = add(headers, description=description)
    saved = response.json().get("description", "") if response.status_code == 201 else ""
    assert "<" not in saved and ">" not in saved


@pytest.mark.parametrize("description", ["Walk & stretch", "Keep BP < 130", "Sleep > 7 h, 5 days/week",
                                         "Eat less salt (< 2 g/day) & more fish"])
def test_normal_text_with_symbols_is_saved_unchanged(make_patient, description):
    headers = make_patient()
    response = add(headers, description=description)
    assert response.status_code == 201 and response.json()["description"] == description


@pytest.mark.parametrize("description", ["&amp;lt;script&amp;gt;alert(1)&amp;lt;/script&amp;gt;Walk",
                                         "<scr<script>ipt>alert(1)</script>Walk",
                                         "&lt;img src=x onerror=alert(1)&gt;Walk"],
                         ids=["double-escaped", "nested-tag", "escaped-img"])
def test_tricky_html_is_cleaned(make_patient, description):
    """Whatever is left is plain text: nothing that a browser could read as a tag."""
    headers = make_patient()
    response = add(headers, description=description)
    assert response.status_code == 201
    saved = response.json()["description"]
    assert "Walk" in saved
    assert re.search(r"<\s*[A-Za-z/!?]", saved) is None  # no "<tag", "</", "<!--" or "<?"
    assert "onerror" not in saved and "script" not in saved.lower()


def test_list_limit(make_patient):
    headers = make_patient()
    for n in range(3):
        add(headers, description=f"Goal {n}")
    assert [g["description"] for g in client.get("/goals?limit=2", headers=headers).json()] == ["Goal 2", "Goal 1"]
    assert len(client.get("/goals", headers=headers).json()) == 3  # default 50
    assert len(client.get("/goals?limit=200", headers=headers).json()) == 3
    for bad in ("0", "201", "x"):
        assert client.get(f"/goals?limit={bad}", headers=headers).status_code == 422


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


def risk_result_for(headers, disease="T2D"):
    """A saved risk result for the caller, linked to the test-only model row from
    tests/seed_test_models.sql (only the database owner can register models)."""
    from decimal import Decimal
    from backend.models import ModelVersion, RiskAssessment, RiskResult
    email = client.get("/profile", headers=headers).json()["email"]
    with SessionLocal() as session:
        user_id = session.scalar(select(User.user_id).where(User.email == email))
        set_request_identity(session, user_id=user_id)
        patient_id = session.scalar(select(Patient.patient_id))
        set_request_identity(session, user_id=user_id, patient_id=patient_id)
        model_id = session.scalar(select(ModelVersion.model_version_id).where(
            ModelVersion.disease_domain == disease, ModelVersion.version == "test-only"))
        assert model_id is not None, "rebuild med_db_test: tests/seed_test_models.sql is missing"
        assessment = RiskAssessment(patient_id=patient_id, input_snapshot={disease: {}})
        session.add(assessment)
        session.flush()
        result = RiskResult(assessment_id=assessment.assessment_id, patient_id=patient_id,
                            model_version_id=model_id, disease_domain=disease,
                            risk_probability=Decimal("0.3100"), risk_level="Moderate")
        session.add(result)
        session.commit()
        return result.result_id


def test_a_goal_from_a_care_plan_links_to_its_result_and_takes_its_disease(make_patient):
    headers = make_patient()
    result_id = risk_result_for(headers, "T2D")
    response = add(headers, description="Walk after dinner", result_id=result_id)
    assert response.status_code == 201
    goal = response.json()
    assert (goal["result_id"], goal["disease_domain"], goal["source"]) == (result_id, "T2D", "suggested")
    same = add(headers, description="Cut down on sweet drinks", result_id=result_id, disease_domain="T2D")
    assert same.status_code == 201


def test_a_goal_with_a_different_disease_than_its_result_is_refused(make_patient):
    headers = make_patient()
    result_id = risk_result_for(headers, "T2D")
    response = add(headers, result_id=result_id, disease_domain="CVD")
    assert response.status_code == 422 and "T2D" in response.json()["detail"]
    assert client.get("/goals", headers=headers).json() == []


def test_another_patients_result_cannot_be_used_for_a_goal(make_patient):
    owner, other = make_patient(), make_patient()
    result_id = risk_result_for(owner, "CVD")
    assert add(other, result_id=result_id).status_code == 422
