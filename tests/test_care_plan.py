"""Care plan selection: one template per disease and tier, and only the add-ons whose trigger is
true for the patient (careplan_oct27.docx, seed_care_plan_templates.sql)."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from backend import care_plan
from backend.config import settings
from backend.database import SessionLocal, set_request_identity
from backend.main import app
from backend.models import CarePlanTemplate, Patient, User

client = TestClient(app)
PASSWORD = "correct horse battery"
COMBINATIONS = [(d, t) for d in care_plan.DISEASES for t in care_plan.TIERS]


# ---------------------------------------------------------------------------
# Triggers (no database)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("key, facts, expected", [
    ("current_smoker", {"smoking_status": "current"}, True),
    ("current_smoker", {"smoking_status": "former"}, False),
    ("current_alcohol_use", {"substance_use": "current"}, True),
    ("current_alcohol_use", {"substance_use": "past"}, False),
    ("sodium_above_2300mg", {"sodium_mg_day": 2301}, True),
    ("sodium_above_2300mg", {"sodium_mg_day": 2300}, False),
    ("sedentary_8h_or_more", {"sedentary_hours_day": 8.0}, True),
    ("sedentary_8h_or_more", {"sedentary_hours_day": 7.5}, False),
    ("bmi_25_or_more", {"bmi": 25.0}, True),
    ("bmi_25_or_more", {"bmi": 24.9}, False),
    ("history_gestational_diabetes", {"gestational_diabetes": 1}, True),
    ("history_gestational_diabetes", {"gestational_diabetes": 0}, False),
    ("acanthosis_nigricans", {"acanthosis_nigricans": 1}, True),
    ("occupational_dust_or_fumes", {"occupational_dust": 1}, True),
    ("allergic_rhinitis_or_childhood_asthma_or_fhrs_crd_band_3_or_4", {"allergic_rhinitis": 1}, True),
    ("allergic_rhinitis_or_childhood_asthma_or_fhrs_crd_band_3_or_4", {"childhood_asthma": 1}, True),
    ("allergic_rhinitis_or_childhood_asthma_or_fhrs_crd_band_3_or_4", {"fhrs_band": {"CRD": "Critical"}}, True),
    ("allergic_rhinitis_or_childhood_asthma_or_fhrs_crd_band_3_or_4",
     {"allergic_rhinitis": 0, "childhood_asthma": 0, "fhrs_band": {"CRD": "Moderate"}}, False),
    ("fhrs_cvd_band_3_or_4", {"fhrs_band": {"CVD": "Pronounced"}}, True),   # Band 3
    ("fhrs_cvd_band_3_or_4", {"fhrs_band": {"CVD": "Critical"}}, True),     # Band 4
    ("fhrs_cvd_band_3_or_4", {"fhrs_band": {"CVD": "Moderate"}}, False),    # Band 2
    ("fhrs_cvd_band_3_or_4", {"fhrs_band": {"T2D": "Critical"}}, False),    # other disease
    ("fhrs_t2d_band_3_or_4", {"fhrs_band": {"T2D": "Critical"}}, True),
    ("sleep_below_7_or_above_9h", {"sleep_hours_day": 6.5}, True),
    ("sleep_below_7_or_above_9h", {"sleep_hours_day": 9.5}, True),
    ("sleep_below_7_or_above_9h", {"sleep_hours_day": 7.0}, False),
    ("sleep_below_7_or_above_9h", {"sleep_hours_day": 9.0}, False),
    ("psqi_above_5", {"psqi": 6}, True),
    ("psqi_above_5", {"psqi": 5}, False),
    ("tsh_or_b12_out_of_range", {"tsh": 0.2}, True),
    ("tsh_or_b12_out_of_range", {"tsh": 5.0}, True),
    ("tsh_or_b12_out_of_range", {"vitamin_b12": 150}, True),
    ("tsh_or_b12_out_of_range", {"tsh": 2.0, "vitamin_b12": 400}, False),
])
def test_trigger_rules(key, facts, expected):
    assert care_plan.trigger_is_true(key, facts) is expected


@pytest.mark.parametrize("key", sorted(care_plan.TRIGGERS))
def test_missing_data_never_shows_an_add_on(key):
    assert care_plan.trigger_is_true(key, {}) is False


def test_unknown_trigger_is_an_error():
    with pytest.raises(care_plan.UnknownTrigger):
        care_plan.trigger_is_true("not_a_trigger", {})


def test_add_ons_keep_the_template_order():
    items = {"base": [], "addons": [
        {"code": "A1", "trigger_key": "current_smoker"},
        {"code": "A2", "trigger_key": "fhrs_cvd_band_3_or_4"},
        {"code": "A3", "trigger_key": "sodium_above_2300mg"},
    ]}
    facts = {"smoking_status": "current", "sodium_mg_day": 3000}
    assert [a["code"] for a in care_plan.select_addons(items, facts)] == ["A1", "A3"]


# ---------------------------------------------------------------------------
# Templates in med_db_test (the 12 drafts from seed_care_plan_templates.sql)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("disease, tier", COMBINATIONS)
def test_every_disease_and_tier_has_a_template(db, disease, tier):
    template = care_plan.get_template(db, disease, tier, include_drafts=True)
    assert (template.disease_domain, template.risk_level) == (disease, tier)
    base = template.plan_items["base"]
    assert base and any(item["category"] == "Urgent warning signs" for item in base)
    if disease == "NEURO":
        assert any("1553" in item["action"] for item in base)  # NCMH Crisis Hotline


def test_every_add_on_trigger_has_a_rule(db):
    assert care_plan.check_templates(db, include_drafts=True) == []


def test_drafts_are_never_used_without_include_drafts(db):
    active = db.scalar(select(func.count()).select_from(CarePlanTemplate).where(CarePlanTemplate.is_active))
    if active:
        pytest.skip("approved templates are loaded in this database")
    with pytest.raises(care_plan.TemplateMissing):
        care_plan.get_template(db, "CVD", "Low")
    assert len(care_plan.check_templates(db)) == 12


def test_unknown_disease_or_tier_is_refused(db):
    with pytest.raises(ValueError):
        care_plan.get_template(db, "CANCER", "Low", include_drafts=True)
    with pytest.raises(ValueError):
        care_plan.get_template(db, "CVD", "Severe", include_drafts=True)


# ---------------------------------------------------------------------------
# A real patient: facts read from saved data, add-ons chosen per disease
# ---------------------------------------------------------------------------
@pytest.fixture
def make_patient():
    made = []

    def make(sex="Male"):
        email = f"careplan-{uuid.uuid4().hex[:8]}@test.local"
        made.append(email)
        assert client.post("/auth/register", json={
            "email": email, "password": PASSWORD, "first_name": "Gabriela", "last_name": "Silang",
            "date_of_birth": "1975-03-19", "biological_sex": sex,
            "privacy_consent": True, "consent_version": settings.privacy_notice_version,
        }).status_code == 201
        token = client.post("/auth/login", json={"email": email, "password": PASSWORD}).json()["access_token"]
        return email, {"Authorization": f"Bearer {token}"}

    yield make
    with SessionLocal() as session:
        session.execute(delete(User).where(func.lower(User.email).in_(made)))
        session.commit()


def send(headers, path, body):
    method = "PUT" if path == "/health-history" else "POST"
    response = client.request(method, path, headers=headers, json=body)
    assert response.status_code in (200, 201), response.text


def plans_for(email, tiers):
    with SessionLocal() as session:
        user_id = session.scalar(select(User.user_id).where(User.email == email))
        set_request_identity(session, user_id=user_id)
        patient_id = session.scalar(select(Patient.patient_id))
        set_request_identity(session, user_id=user_id, patient_id=patient_id)
        return care_plan.build_care_plans(session, patient_id, tiers, include_drafts=True)


def codes(plan):
    return [item["code"] for item in plan["addons"]]


def test_add_ons_follow_the_patients_data(make_patient):
    email, headers = make_patient()
    send(headers, "/vitals", {"sbp": 132, "dbp": 85, "weight_kg": 85.0, "height_cm": 170.0})  # BMI 29.4
    send(headers, "/lifestyle", {"smoking_status": "current", "pack_years": 10.0,
                                 "physical_activity_min_week": 60, "sedentary_hours_day": 9.0,
                                 "sleep_hours_day": 6.0, "occupational_dust": True,
                                 "substance_use": "past", "sodium_mg_day": 2500, "carbs_g_day": 300})
    send(headers, "/health-history", {"acanthosis_nigricans": False, "allergic_rhinitis": False,
                                      "childhood_asthma": False, "tbi_history": False})

    plans = plans_for(email, {"CVD": "Low", "T2D": "Moderate", "CRD": "High", "NEURO": "Low"})

    assert codes(plans["CVD"]) == ["A1", "A3"]          # smoker, sodium; no family history band
    assert codes(plans["T2D"]) == ["A4", "A5", "A6"]    # smoker, sedentary, BMI; male: no A2
    assert codes(plans["CRD"]) == ["A1", "A2"]          # smoker, dust; High has no A3
    assert codes(plans["NEURO"]) == ["A1"]              # sleep 6 h; alcohol 'past' is not 'current'
    for disease, plan in plans.items():
        assert plan["risk_level"] == {"CVD": "Low", "T2D": "Moderate", "CRD": "High", "NEURO": "Low"}[disease]
        assert plan["base"] and plan["disclaimer"].startswith("This care plan is for educational")


def test_a_new_patient_gets_base_items_only(make_patient):
    email, _ = make_patient()
    plans = plans_for(email, {d: "Low" for d in care_plan.DISEASES})
    assert all(plan["addons"] == [] for plan in plans.values())
    assert all(plan["base"] for plan in plans.values())


def test_templates_used_names_each_version(make_patient):
    email, _ = make_patient()
    plans = plans_for(email, {"CVD": "High", "NEURO": "Moderate"})
    used = care_plan.templates_used(plans)
    assert set(used) == {"CVD", "NEURO"}
    assert all(set(v) == {"template_id", "version"} for v in used.values())

def test_facts_come_only_from_the_given_patient(make_patient):
    """Second layer: with the RLS identity of a patient whose data fires add-ons, asking for
    another patient must not use that data (the other patient's row is simply not visible)."""
    smoker_email, smoker = make_patient()
    send(smoker, "/lifestyle", {"smoking_status": "current", "pack_years": 10})
    other_email, _ = make_patient()
    with SessionLocal() as session:
        ids = {}
        for email in (smoker_email, other_email):
            user_id = session.scalar(select(User.user_id).where(User.email == email))
            set_request_identity(session, user_id=user_id)
            ids[email] = (user_id, session.scalar(select(Patient.patient_id)))
        set_request_identity(session, user_id=ids[smoker_email][0], patient_id=ids[smoker_email][1])
        assert care_plan.patient_facts(session, ids[smoker_email][1])["smoking_status"] == "current"
        assert care_plan.patient_facts(session, ids[other_email][1])["smoking_status"] is None


def test_a_lab_without_a_sample_date_counts_from_the_day_it_was_saved(make_patient):
    email, headers = make_patient()
    send(headers, "/labs", {"test_code": "TSH", "value": 2.0, "unit": "mIU/L", "collected_on": "2025-01-15"})
    send(headers, "/labs", {"test_code": "TSH", "value": 6.0, "unit": "mIU/L"})  # no date, saved today
    with SessionLocal() as session:
        user_id = session.scalar(select(User.user_id).where(User.email == email))
        set_request_identity(session, user_id=user_id)
        patient_id = session.scalar(select(Patient.patient_id))
        set_request_identity(session, user_id=user_id, patient_id=patient_id)
        assert care_plan.patient_facts(session, patient_id)["tsh"] == 6.0
