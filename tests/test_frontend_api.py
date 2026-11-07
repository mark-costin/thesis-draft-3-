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


def test_scan_screen_lab_lists_match_the_api():
    """Scan & assess types lab values in the API's own units, and knows every lab the models need."""
    from backend import labs, readiness
    from frontend.patient_portal.labinfo import LAB_LABEL, LAB_UNIT

    assert LAB_UNIT == labs.STANDARD_UNIT
    assert set(LAB_LABEL) == set(LAB_UNIT)
    lab_features = {f.name.upper() for f in readiness.load_features() if f.source == "lab_ocr"}
    assert lab_features <= set(LAB_UNIT), lab_features - set(LAB_UNIT)


@pytest.mark.anyio
async def test_typed_lab_values_leave_the_scan_list(in_process):
    """State.save_typed_labs: each missing lab is sent to POST /labs in its standard unit."""
    from frontend.patient_portal.labinfo import LAB_UNIT

    token = await signed_in(in_process)
    def lab_codes(readiness):  # the way State._load_scan_missing reads the list
        return {i["name"].upper() for m in readiness["models"].values() for i in m["missing_required"] + m["missing_optional"]
                if i["name"].upper() in LAB_UNIT}

    before = await api.call("GET", "/assessment/readiness", token)
    missing = sorted(lab_codes(before.data))
    assert missing  # a new patient has no lab values yet
    reply = await api.call("POST", "/labs", token, json={"test_code": missing[0], "value": 1, "unit": LAB_UNIT[missing[0]]}, idempotent=True)
    assert reply.status == 201
    after = await api.call("GET", "/assessment/readiness", token)
    assert missing[0] not in lab_codes(after.data)


@pytest.mark.anyio
async def test_goal_and_report_requests(in_process):
    """Reports & goals: the goal calls the screens make, and the messages for a missing assessment."""
    token = await signed_in(in_process)
    created = await api.call("POST", "/goals", token, json={"description": "Walk 30 minutes a day"}, idempotent=True)
    assert created.status == 201
    goal_id = created.data["goal_id"]
    assert (await api.call("PATCH", f"/goals/{goal_id}", token, json={"is_completed": True})).data["is_completed"] is True
    assert [g["goal_id"] for g in (await api.call("GET", "/goals", token, params={"limit": 200})).data] == [goal_id]

    assert (await api.call("GET", "/reports", token)).data == []
    missing = await api.call("POST", "/reports", token, json={"assessment_id": 999999}, idempotent=True)
    assert missing.status == 404 and missing.message()
    run = await api.call("POST", "/assessment/run", token, idempotent=True)
    assert run.status in (409, 503)  # models off (503) or inputs missing (409): the screen explains both


def test_messages_from_a_detail_object():
    reply = api.Reply(409, {"detail": {"message": "This assessment is already saved as a report.", "report_id": 7}})
    assert reply.message() == "This assessment is already saved as a report."


# ---------------------------------------------------------------------------
# Double submit (Sprint 6 failure cases)
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_a_double_click_saves_one_record(in_process):
    """Reflex runs the second click after the first one finishes, so the second request must
    reuse the first one's Idempotency-Key. The repeat still gets the saved record's data."""
    token = await signed_in(in_process)
    body = {"sbp": 131, "dbp": 85}
    first = await api.call("POST", "/vitals", token, json=body, idempotent=True)
    second = await api.call("POST", "/vitals", token, json=body, idempotent=True)
    assert first.status == second.status == 201
    assert second.headers.get("idempotent-replayed") == "true"
    assert second.data["vitals_id"] == first.data["vitals_id"] and second.data["sbp"] == 131
    assert len((await api.call("GET", "/vitals", token)).data) == 1


@pytest.mark.anyio
async def test_a_double_submit_keeps_the_crisis_prompt(in_process):
    token = await signed_in(in_process)
    body = {"instrument": "PHQ9", "answers": [0] * 8 + [2]}
    first = await api.call("POST", "/questionnaires", token, json=body, idempotent=True)
    second = await api.call("POST", "/questionnaires", token, json=body, idempotent=True)
    assert second.data["response_id"] == first.data["response_id"]
    assert second.data["requires_crisis_prompt"] is True and second.data["crisis_resources"]["hotlines"]
    assert len((await api.call("GET", "/questionnaires", token)).data) == 1


@pytest.mark.anyio
async def test_a_new_entry_is_saved_again(in_process, monkeypatch):
    """Different values, or the same values after the window, are a new entry."""
    token = await signed_in(in_process)
    await api.call("POST", "/vitals", token, json={"sbp": 120, "dbp": 80}, idempotent=True)
    await api.call("POST", "/vitals", token, json={"sbp": 121, "dbp": 80}, idempotent=True)
    monkeypatch.setattr(api, "REPEAT_WINDOW", 0.0)  # as if the window has passed
    await api.call("POST", "/vitals", token, json={"sbp": 120, "dbp": 80}, idempotent=True)
    assert len((await api.call("GET", "/vitals", token)).data) == 3


def test_the_key_is_kept_for_a_retry_but_not_shared():
    _, key = api._request_key("token-a", "POST", "/vitals", {"sbp": 1})
    assert api._request_key("token-a", "POST", "/vitals", {"sbp": 1})[1] == key  # retry after a lost connection
    assert api._request_key("token-b", "POST", "/vitals", {"sbp": 1})[1] != key  # another patient
    assert api._request_key("token-a", "POST", "/labs", {"sbp": 1})[1] != key
    assert all("token-a" not in fingerprint for fingerprint in api._recent)  # only hashes are kept


@pytest.mark.anyio
async def test_a_server_error_lets_the_retry_run_again(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request.headers["idempotency-key"])
        return httpx.Response(500 if len(calls) == 1 else 201, json={"detail": "x"})

    monkeypatch.setattr(api, "_TRANSPORT", httpx.MockTransport(handler))
    assert (await api.call("POST", "/vitals", "t", json={"sbp": 2}, idempotent=True)).status == 500
    assert (await api.call("POST", "/vitals", "t", json={"sbp": 2}, idempotent=True)).status == 201
    assert calls[0] != calls[1]


def test_chart_names_come_from_features_yaml():
    from frontend.patient_portal.labinfo import feature_label
    assert feature_label("hdl") == "HDL"
    assert feature_label("sodium_mg_day") == "Daily Sodium Intake"
    assert feature_label("resting_hr") == "Heart Rate (Resting)"  # "(Resting)" is not an abbreviation
    assert feature_label("phq9") == "PHQ-9" and feature_label("age") == "Age"
    assert feature_label("not_in_the_file") == "Not in the file"


def test_risk_is_shown_as_a_percent_never_0_or_100():
    from frontend.patient_portal.state import risk_percent
    assert risk_percent(0.024) == "2.4%" and risk_percent(0.31) == "31%"
    assert risk_percent(0.004) == "below 1%" and risk_percent(0.0) == "below 1%"
    assert risk_percent(0.999) == "above 95%" and risk_percent(1.0) == "above 95%"
    assert risk_percent(0.95) == "95%"
