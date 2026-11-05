"""The frontend's API client (frontend/patient_portal/api.py) against the real API, in-process.

These tests do not start Reflex: they check that the requests the screens send are the ones
the API accepts, and that the API's errors become plain sentences for the patient.
"""

import uuid

import httpx
import pytest
from sqlalchemy import delete, func

from backend.config import settings
from backend.database import SessionLocal
from backend.main import app
from backend.models import User
from frontend.patient_portal import api

PASSWORD = "a long passphrase 123"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def in_process(monkeypatch):
    monkeypatch.setattr(api, "_TRANSPORT", httpx.ASGITransport(app=app))
    made = []
    yield made
    with SessionLocal() as session:
        session.execute(delete(User).where(func.lower(User.email).in_(made)))
        session.commit()


def register_body(email):
    """What pages/register.py sends (State.register)."""
    return {"email": email, "password": PASSWORD, "first_name": "Juan", "last_name": "Dela Cruz",
            "date_of_birth": "1988-08-21", "biological_sex": "Male", "privacy_consent": True,
            "consent_version": settings.privacy_notice_version, "contact_number": "09171234567",
            "street": None, "province": "Bulacan", "city_municipality": "Marilao", "barangay": "Abangan Norte"}


async def signed_in(made) -> str:
    email = f"ui-{uuid.uuid4().hex[:8]}@test.local"
    made.append(email)
    assert (await api.call("POST", "/auth/register", json=register_body(email), idempotent=True)).status == 201
    login = await api.call("POST", "/auth/login", json={"email": email, "password": PASSWORD})
    assert login.ok
    return login.data["access_token"]


@pytest.mark.anyio
async def test_the_screens_requests_are_accepted(in_process):
    token = await signed_in(in_process)
    profile = await api.call("GET", "/profile", token)
    assert profile.ok and profile.data["barangay"] == "Abangan Norte" and profile.data["sign_in"] == "email"

    # Vitals form (State.save_vitals), lifestyle form (State.save_lifestyle), health history (State.save_history)
    assert (await api.call("POST", "/vitals", token, json={"sbp": 128, "dbp": 82, "weight_kg": 70, "height_cm": 175,
                                                            "blood_glucose_mgdl": 92, "glucose_context": "fasting"}, idempotent=True)).status == 201
    assert (await api.call("POST", "/lifestyle", token, json={"smoking_status": "never", "physical_activity_min_week": 150,
                                                               "occupational_dust": False, "substance_use": "none"}, idempotent=True)).status == 201
    assert (await api.call("POST", "/labs", token, json={"test_code": "TSH", "value": 2.1, "unit": "mIU/L"}, idempotent=True)).status == 201
    assert (await api.call("PUT", "/health-history", token, json={"childhood_asthma": False, "allergic_rhinitis": None,
                                                                   "acanthosis_nigricans": None, "tbi_history": None})).ok

    # Family history form (State.add_family_entry) and the FHRS bands
    entry = await api.call("POST", "/family-history", token, json={"relative": "maternal_aunt", "disease_domain": "T2D",
                                                                    "specific_diagnosis": "Type 2 diabetes", "diagnosis_confirmed": "yes",
                                                                    "shared_environment": False, "age_at_diagnosis": 45}, idempotent=True)
    assert entry.status == 201
    bands = await api.call("GET", "/family-history/fhrs", token)
    assert {b["disease_domain"] for b in bands.data} == {"CVD", "T2D", "CRD", "NEURO"}

    # Dashboard (State.load_dashboard)
    assert (await api.call("GET", "/vitals", token, params={"limit": 50})).data[0]["bmi"] == 22.9
    readiness = await api.call("GET", "/assessment/readiness", token)
    assert readiness.ok and all("enter_in" in item for item in readiness.data["missing_required"])


@pytest.mark.anyio
async def test_errors_become_plain_sentences(in_process):
    token = await signed_in(in_process)
    wrong = await api.call("POST", "/auth/login", json={"email": "nobody@test.local", "password": "wrong"})
    assert wrong.status == 401 and wrong.message() == "Incorrect email or password"
    invalid = await api.call("POST", "/vitals", token, json={"sbp": 120})
    assert invalid.status == 422 and "systolic and diastolic" in invalid.message()
    assert (await api.call("GET", "/profile")).status == 401  # no token


def test_network_and_rate_limit_messages():
    assert "Cannot reach the server" in api.Reply(0).message()
    assert "wait a minute" in api.Reply(429).message()
    assert api.Reply(500, None).message("Fallback") == "Fallback"


@pytest.mark.anyio
async def test_questionnaire_screen_requests(in_process):
    """What pages/questionnaires.py sends (State.submit_questionnaire), and the readiness links."""
    from frontend.patient_portal import questionnaires as qn

    token = await signed_in(in_process)
    for code in qn.ORDER:  # every questionnaire on the screen has the item count and range the API scores
        spec = qn.INSTRUMENTS[code]
        answers = [spec["low"]] * len(spec["items"])
        reply = await api.call("POST", "/questionnaires", token, json={"instrument": code, "answers": answers}, idempotent=True)
        assert reply.status == 201, (code, reply.data)
        top = [spec["low"] + len(spec["options"]) - 1] * len(spec["items"])  # the highest answer each option list allows
        assert (await api.call("POST", "/questionnaires", token, json={"instrument": code, "answers": top}, idempotent=True)).status == 201, code

    crisis = await api.call("POST", "/questionnaires", token, json={"instrument": "PHQ9", "answers": [0] * 8 + [1]}, idempotent=True)
    assert crisis.data["requires_crisis_prompt"] is True
    assert crisis.data["crisis_resources"]["hotlines"][0]["number"] == qn.CRISIS_HOTLINES[0]["number"]  # screen and API agree

    readiness = await api.call("GET", "/assessment/readiness", token)
    assert not any(item["enter_in"] == "Questionnaire" for item in readiness.data["missing_required"])
