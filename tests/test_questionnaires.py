"""Questionnaires: server-side scoring and the PHQ-9 crisis flag."""

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


@pytest.fixture
def make_patient():
    """Registers and logs in patients; returns auth headers. Deleted after the test."""
    made = []

    def make():
        email = f"quest-{uuid.uuid4().hex[:8]}@test.local"
        made.append(email)
        assert client.post("/auth/register", json={
            "email": email, "password": PASSWORD, "first_name": "Josefa", "last_name": "Llanes",
            "date_of_birth": "1990-09-09", "biological_sex": "Female",
            "privacy_consent": True, "consent_version": settings.privacy_notice_version,
        }).status_code == 201
        token = client.post("/auth/login", json={"email": email, "password": PASSWORD}).json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    yield make
    with SessionLocal() as session:
        session.execute(delete(User).where(func.lower(User.email).in_(made)))
        session.commit()


def submit(headers, instrument, answers):
    return client.post("/questionnaires", headers=headers, json={"instrument": instrument, "answers": answers})


# --- Crisis flag (the "Done when" of this task) ----------------------------------------------

@pytest.mark.parametrize("item9", [1, 2, 3])
def test_phq9_item9_above_zero_returns_the_crisis_prompt(make_patient, item9):
    headers = make_patient()
    response = submit(headers, "PHQ9", [0, 0, 0, 0, 0, 0, 0, 0, item9])
    assert response.status_code == 201
    data = response.json()
    assert data["requires_crisis_prompt"] is True
    assert data["crisis_resources"]["hotlines"][0]["number"] == "1553"


def test_phq9_item9_zero_has_no_crisis_prompt(make_patient):
    headers = make_patient()
    data = submit(headers, "PHQ9", [3, 3, 3, 3, 3, 3, 3, 3, 0]).json()
    assert data["requires_crisis_prompt"] is False and data["crisis_resources"] is None
    assert data["total_score"] == 24 and data["band"] == "severe"


def test_crisis_flag_is_kept_when_read_back(make_patient):
    headers = make_patient()
    response_id = submit(headers, "PHQ9", [1, 1, 1, 1, 1, 1, 1, 1, 1]).json()["response_id"]
    again = client.get(f"/questionnaires/{response_id}", headers=headers).json()
    assert again["requires_crisis_prompt"] is True and again["crisis_resources"] is not None


def test_only_phq9_can_raise_the_crisis_flag(make_patient):
    headers = make_patient()
    data = submit(headers, "CAT", [5, 5, 5, 5, 5, 5, 5, 5]).json()
    assert data["requires_crisis_prompt"] is False


# --- Scoring -----------------------------------------------------------------------------------

@pytest.mark.parametrize("instrument, answers, total, band", [
    ("PHQ9", [1, 1, 1, 1, 1, 0, 0, 0, 0], 5, "mild"),
    ("GAD7", [2, 2, 2, 2, 2, 0, 0], 10, "moderate"),
    ("PSQI", [1, 1, 1, 1, 1, 1, 0], 6, "poor sleep quality"),
    ("MMRC", [2], 2, "grade 2"),
    ("CAT", [1, 1, 1, 1, 1, 1, 1, 2], 9, "low impact"),
    ("ACT", [4, 4, 4, 4, 4], 20, "well controlled"),
    ("ACT", [1, 1, 1, 1, 1], 5, "very poorly controlled"),
])
def test_server_computes_total_and_band(make_patient, instrument, answers, total, band):
    headers = make_patient()
    data = submit(headers, instrument, answers).json()
    assert (data["instrument"], data["answers"], data["total_score"], data["band"]) == (instrument, answers, total, band)


def test_a_total_sent_by_the_client_is_refused(make_patient):
    headers = make_patient()
    body = {"instrument": "GAD7", "answers": [0] * 7, "total_score": 21}
    assert client.post("/questionnaires", headers=headers, json=body).status_code == 422


@pytest.mark.parametrize("instrument, answers", [
    ("PHQ9", [0] * 8),
    ("PHQ9", [0] * 10),
    ("PHQ9", [0, 0, 0, 0, 0, 0, 0, 0, 4]),
    ("GAD7", [-1, 0, 0, 0, 0, 0, 0]),
    ("ACT", [0, 1, 1, 1, 1]),
    ("CAT", [6, 0, 0, 0, 0, 0, 0, 0]),
    ("MMRC", [5]),
    ("MMSE", [1]),
    ("PHQ9", ["a"] * 9),
], ids=["phq9-too-few", "phq9-too-many", "phq9-item-high", "gad7-negative", "act-below-1",
        "cat-item-high", "mmrc-high", "unknown-instrument", "not-numbers"])
def test_invalid_answers_are_rejected(make_patient, instrument, answers):
    headers = make_patient()
    assert submit(headers, instrument, answers).status_code == 422
    assert client.get("/questionnaires", headers=headers).json() == []


# --- Listing, ownership, privacy ---------------------------------------------------------------

def test_list_filters_by_instrument_newest_first_and_only_mine(make_patient):
    headers_a, headers_b = make_patient(), make_patient()
    submit(headers_a, "GAD7", [0] * 7)
    submit(headers_a, "GAD7", [1] * 7)
    submit(headers_a, "MMRC", [1])
    submit(headers_b, "GAD7", [3] * 7)

    gad = client.get("/questionnaires?instrument=GAD7", headers=headers_a)
    assert gad.headers["Cache-Control"] == "no-store"
    assert [r["total_score"] for r in gad.json()] == [7, 0]
    assert len(client.get("/questionnaires", headers=headers_a).json()) == 3
    assert [r["total_score"] for r in client.get("/questionnaires", headers=headers_b).json()] == [21]
    assert client.get("/questionnaires?instrument=XYZ", headers=headers_a).status_code == 422


def test_other_patients_responses_are_hidden(make_patient):
    headers_a, headers_b = make_patient(), make_patient()
    response_id = submit(headers_a, "PHQ9", [0] * 9).json()["response_id"]
    assert client.get(f"/questionnaires/{response_id}", headers=headers_b).status_code == 404
    assert client.delete(f"/questionnaires/{response_id}", headers=headers_b).status_code == 404
    assert client.delete(f"/questionnaires/{response_id}", headers=headers_a).status_code == 204
    assert client.get(f"/questionnaires/{response_id}", headers=headers_a).status_code == 404


def test_questionnaires_need_login():
    assert client.post("/questionnaires", json={"instrument": "MMRC", "answers": [0]}).status_code == 401


def test_double_submit_saves_once_without_keeping_answers(make_patient):
    headers = make_patient()
    with_key = headers | {"Idempotency-Key": "quest-" + uuid.uuid4().hex}
    first = client.post("/questionnaires", headers=with_key, json={"instrument": "PHQ9", "answers": [1] * 9})
    second = client.post("/questionnaires", headers=with_key, json={"instrument": "PHQ9", "answers": [1] * 9})
    assert second.headers[REPLAY_HEADER] == "true" and "total_score" not in second.text
    assert len(client.get("/questionnaires", headers=headers).json()) == 1
    # Reading the record back from Location still shows the crisis prompt
    assert client.get(second.headers["Location"], headers=headers).json()["requires_crisis_prompt"] is True
    assert first.json()["requires_crisis_prompt"] is True


def test_audit_holds_the_instrument_only(make_patient, monkeypatch):
    recorded = []
    real = audit.log_event

    def recording(db, event_type, **kwargs):
        recorded.append((event_type, kwargs))
        return real(db, event_type, **kwargs)

    monkeypatch.setattr(audit, "log_event", recording)
    headers = make_patient()
    submit(headers, "PHQ9", [2, 2, 2, 2, 2, 2, 2, 2, 2])
    event_type, details = recorded[-1]
    assert event_type == "DATA_CREATED" and details["instrument"] == "PHQ9"
    assert set(details) == {"user_id", "patient_id", "table", "record_id", "instrument"}
