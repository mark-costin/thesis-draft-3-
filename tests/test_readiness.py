"""Missing-inputs check: a new patient sees the full list; after all inputs are saved, it is empty."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from backend import readiness
from backend.config import settings
from backend.database import SessionLocal, set_request_identity
from backend.main import app
from backend.models import Patient, PatientFhrsStratification, User

client = TestClient(app)
PASSWORD = "correct horse battery"


@pytest.fixture
def make_patient():
    """Registers and logs in patients; returns (email, auth headers). Deleted after the test."""
    made = []

    def make(sex="Female"):
        email = f"ready-{uuid.uuid4().hex[:8]}@test.local"
        made.append(email)
        assert client.post("/auth/register", json={
            "email": email, "password": PASSWORD, "first_name": "Trinidad", "last_name": "Tecson",
            "date_of_birth": "1978-11-18", "biological_sex": sex,
            "privacy_consent": True, "consent_version": settings.privacy_notice_version,
        }).status_code == 201
        token = client.post("/auth/login", json={"email": email, "password": PASSWORD}).json()["access_token"]
        return email, {"Authorization": f"Bearer {token}"}

    yield make
    with SessionLocal() as session:
        session.execute(delete(User).where(func.lower(User.email).in_(made)))
        session.commit()


def add_fhrs(email, domains=("CVD", "T2D", "CRD", "NEURO")):
    """FHRS rows come from the family history task (not built yet), so the test writes them directly."""
    with SessionLocal() as session:
        user_id = session.scalar(select(User.user_id).where(User.email == email))
        set_request_identity(session, user_id=user_id)
        patient_id = session.scalar(select(Patient.patient_id))
        set_request_identity(session, user_id=user_id, patient_id=patient_id)
        for domain in domains:
            session.add(PatientFhrsStratification(
                patient_id=patient_id, disease_domain=domain, affected_relatives=0, clustering_score=0,
                environmental_modifier=1, calculated_fhrs=0, risk_band="Baseline"))
        session.commit()


def enter_everything(headers, smoker=True, female=True):
    assert client.post("/vitals", headers=headers, json={
        "sbp": 124, "dbp": 80, "weight_kg": 62.0, "height_cm": 160.0}).status_code == 201
    lifestyle = {"physical_activity_min_week": 120, "sedentary_hours_day": 7.0, "sleep_hours_day": 7.0,
                 "occupational_dust": False, "substance_use": "none"}
    lifestyle |= {"smoking_status": "former", "pack_years": 5} if smoker else {"smoking_status": "never"}
    assert client.post("/lifestyle", headers=headers, json=lifestyle).status_code == 201
    history = {"acanthosis_nigricans": False, "allergic_rhinitis": True, "childhood_asthma": False}
    if female:
        history["gestational_diabetes"] = False
    assert client.put("/health-history", headers=headers, json=history).status_code == 200
    for instrument, answers in (("PHQ9", [0] * 9), ("GAD7", [0] * 7), ("MMRC", [1])):
        assert client.post("/questionnaires", headers=headers,
                           json={"instrument": instrument, "answers": answers}).status_code == 201


def required_names(body):
    return {item["name"] for item in body["missing_required"]}


def test_features_file_matches_the_database():
    features = readiness.load_features()
    assert {f.model for f in features} == {"CVD", "T2D", "CRD", "NEURO"}
    assert any(f.required for f in features) and any(not f.required for f in features)


def test_new_patient_sees_the_full_list(make_patient):
    _, headers = make_patient()
    response = client.get("/assessment/readiness", headers=headers)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    body = response.json()
    assert body["ready"] is False
    names = required_names(body)
    for expected in ("sbp", "dbp", "bmi", "pack_years", "physical_activity_min_week", "fhrs_cvd",
                     "phq9", "gad7", "mmrc", "gestational_diabetes", "acanthosis_nigricans",
                     "sleep_hours_day", "alcohol_use"):
        assert expected in names, expected
    assert "age" not in names and "sex" not in names  # known from registration
    assert all(not m["ready"] for m in body["models"].values())

    # Shared inputs appear once on the checklist, with every model that needs them
    sbp = next(item for item in body["missing_required"] if item["name"] == "sbp")
    assert sbp["models"] == ["CVD", "T2D"] and sbp["enter_in"] == "Vitals" and sbp["path"] == "/vitals"
    phq9 = next(item for item in body["missing_required"] if item["name"] == "phq9")
    assert phq9["instrument"] == "PHQ9" and phq9["path"] == "/questionnaires"

    # Optional lab values are listed separately and never block the assessment
    cvd_optional = {item["name"] for item in body["models"]["CVD"]["missing_optional"]}
    assert {"total_cholesterol", "hdl", "ldl"} <= cvd_optional


def test_list_is_empty_after_all_inputs_are_saved(make_patient):
    email, headers = make_patient()
    enter_everything(headers)
    add_fhrs(email)
    body = client.get("/assessment/readiness", headers=headers).json()
    assert body["missing_required"] == []
    assert body["ready"] is True
    assert all(m["ready"] and m["missing_required"] == [] for m in body["models"].values())


def test_only_family_history_left(make_patient):
    _, headers = make_patient()
    enter_everything(headers)
    body = client.get("/assessment/readiness", headers=headers).json()
    assert required_names(body) == {"fhrs_cvd", "fhrs_t2d", "fhrs_neuro"}
    assert {item["enter_in"] for item in body["missing_required"]} == {"Family history"}
    assert body["models"]["CRD"]["ready"] is True


def test_never_smoker_needs_no_pack_years(make_patient):
    email, headers = make_patient()
    enter_everything(headers, smoker=False)
    add_fhrs(email)
    assert client.get("/assessment/readiness", headers=headers).json()["ready"] is True


def test_male_patient_is_not_asked_about_gestational_diabetes(make_patient):
    email, headers = make_patient(sex="Male")
    assert "gestational_diabetes" not in required_names(client.get("/assessment/readiness", headers=headers).json())
    enter_everything(headers, female=False)
    add_fhrs(email)
    assert client.get("/assessment/readiness", headers=headers).json()["ready"] is True


def test_unconfirmed_answer_counts_as_missing_until_given(make_patient):
    _, headers = make_patient()
    client.put("/health-history", headers=headers, json={"acanthosis_nigricans": None})  # "don't know"
    assert "acanthosis_nigricans" in required_names(client.get("/assessment/readiness", headers=headers).json())


def test_readiness_is_per_patient(make_patient):
    email_a, headers_a = make_patient()
    _, headers_b = make_patient()
    enter_everything(headers_a)
    add_fhrs(email_a)
    assert client.get("/assessment/readiness", headers=headers_a).json()["ready"] is True
    assert client.get("/assessment/readiness", headers=headers_b).json()["ready"] is False


def test_readiness_needs_login():
    assert client.get("/assessment/readiness").status_code == 401
