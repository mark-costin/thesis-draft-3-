"""Feature builder: each model gets its inputs from the patient's latest saved data, in the order
of ml/features.yaml; missing optional labs become None, missing required inputs stop the run."""

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.exc import NoResultFound

from backend import feature_builder
from backend.config import settings
from backend.database import SessionLocal, set_request_identity
from backend.main import app
from backend.models import Patient, User
from backend.readiness import load_features

client = TestClient(app)
PASSWORD = "correct horse battery"
AS_OF = date(2026, 11, 1)


@pytest.fixture
def make_patient():
    made = []

    def make(sex="Male", born="1980-06-15"):
        email = f"features-{uuid.uuid4().hex[:8]}@test.local"
        made.append(email)
        assert client.post("/auth/register", json={
            "email": email, "password": PASSWORD, "first_name": "Emilio", "last_name": "Jacinto",
            "date_of_birth": born, "biological_sex": sex,
            "privacy_consent": True, "consent_version": settings.privacy_notice_version,
        }).status_code == 201
        token = client.post("/auth/login", json={"email": email, "password": PASSWORD}).json()["access_token"]
        return email, {"Authorization": f"Bearer {token}"}

    yield make
    with SessionLocal() as session:
        session.execute(delete(User).where(func.lower(User.email).in_(made)))
        session.commit()


def ids_of(email):
    with SessionLocal() as session:
        user_id = session.scalar(select(User.user_id).where(User.email == email))
        set_request_identity(session, user_id=user_id)
        return user_id, session.scalar(select(Patient.patient_id))


def build(email, model=None, strict=True):
    user_id, patient_id = ids_of(email)
    with SessionLocal() as session:
        set_request_identity(session, user_id=user_id, patient_id=patient_id)
        if model:
            return feature_builder.build(session, patient_id, model, as_of=AS_OF, strict=strict)
        return feature_builder.build_all(session, patient_id, as_of=AS_OF, strict=strict)


def build_inputs(email, strict=True):
    user_id, patient_id = ids_of(email)
    with SessionLocal() as session:
        set_request_identity(session, user_id=user_id, patient_id=patient_id)
        return feature_builder.build_inputs(session, patient_id, as_of=AS_OF, strict=strict)


def post(headers, path, body, expected=(200, 201)):
    response = client.request("PUT" if path == "/health-history" else "POST", path, headers=headers, json=body)
    assert response.status_code in expected, response.text
    return response.json()


def fill_required(headers, sex="Male"):
    """Everything the four models require (ml/features.yaml)."""
    post(headers, "/vitals", {"sbp": 128, "dbp": 82, "resting_hr": 72, "weight_kg": 70.0, "height_cm": 175.0})
    post(headers, "/lifestyle", {"smoking_status": "never", "physical_activity_min_week": 150,
                                 "sedentary_hours_day": 6.0, "sleep_hours_day": 7.0, "occupational_dust": False,
                                 "substance_use": "past", "sodium_mg_day": 2300, "carbs_g_day": 250})
    history = {"acanthosis_nigricans": False, "allergic_rhinitis": True, "childhood_asthma": False,
               "tbi_history": False}
    if sex == "Female":
        history["gestational_diabetes"] = True
    post(headers, "/health-history", history)
    post(headers, "/questionnaires", {"instrument": "MMRC", "answers": [1]})
    post(headers, "/questionnaires", {"instrument": "PHQ9", "answers": [1, 1, 0, 0, 1, 0, 0, 0, 0]})
    post(headers, "/questionnaires", {"instrument": "GAD7", "answers": [1, 0, 0, 1, 0, 0, 0]})
    # father with heart disease at 60, not sure: 0.5 x 1.0 x 0.5 = 0.25 (FHRS Guide); all 4 domains saved
    post(headers, "/family-history", {"disease_domain": "CVD", "specific_diagnosis": "Heart disease",
                                      "relative": "father", "age_at_diagnosis": 60})


def test_inputs_follow_the_order_of_features_yaml(make_patient):
    email, headers = make_patient()
    fill_required(headers)
    built = build(email)
    for model, row in built.items():
        assert list(row) == [f.name for f in load_features() if f.model == model]


def test_a_complete_patient_gets_every_required_input(make_patient):
    email, headers = make_patient()
    fill_required(headers)
    built = build(email)
    cvd, t2d, crd, neuro = built["CVD"], built["T2D"], built["CRD"], built["NEURO"]
    assert cvd["age"] == 46 and cvd["sex"] == "Male"        # born 1980-06-15, assessed 2026-11-01
    assert (cvd["sbp"], cvd["dbp"], cvd["resting_hr"], cvd["bmi"]) == (128, 82, 72, 22.9)
    assert cvd["pack_years"] == 0                           # never-smoker (derive rule)
    assert cvd["physical_activity_min_week"] == 150 and cvd["sodium_mg_day"] == 2300
    assert cvd["fhrs_cvd"] == 0.25 and t2d["fhrs_t2d"] == 0.0
    assert t2d["gestational_diabetes"] == 0                 # male patient (derive rule)
    assert t2d["acanthosis_nigricans"] == 0 and t2d["sedentary_hours_day"] == 6.0
    assert crd["mmrc"] == 1 and crd["allergic_rhinitis"] == 1 and crd["occupational_dust"] == 0
    assert neuro["phq9"] == 3 and neuro["gad7"] == 2 and neuro["alcohol_use"] == 1  # 'past' -> 1
    assert neuro["sleep_hours_day"] == 7.0


def test_missing_optional_labs_become_none_not_errors(make_patient):
    email, headers = make_patient()
    fill_required(headers)
    built = build(email)  # strict: would raise if anything required were missing
    assert built["CVD"]["ldl"] is None and built["T2D"]["hba1c"] is None and built["NEURO"]["tsh"] is None
    assert built["CRD"]["act"] is None  # optional questionnaire not answered


def test_a_missing_required_input_stops_the_run(make_patient):
    email, _ = make_patient()
    with pytest.raises(feature_builder.MissingRequired) as error:
        build(email, "CVD")
    assert error.value.model == "CVD"
    assert {"sbp", "dbp", "bmi", "pack_years", "fhrs_cvd", "physical_activity_min_week"} <= set(error.value.names)
    assert "age" not in error.value.names and "ldl" not in error.value.names


def test_not_strict_returns_what_there_is(make_patient):
    email, _ = make_patient(sex="Female", born="1990-11-02")
    neuro = build(email, "NEURO", strict=False)
    assert neuro["age"] == 35 and neuro["sex"] == "Female"   # birthday is the day after AS_OF
    assert neuro["phq9"] is None and neuro["fhrs_neuro"] is None


def test_each_vitals_column_takes_its_latest_value(make_patient):
    email, headers = make_patient()
    fill_required(headers)
    later = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()
    post(headers, "/vitals", {"sbp": 140, "dbp": 90, "measured_at": later})  # only blood pressure
    cvd = build(email, "CVD")
    assert (cvd["sbp"], cvd["dbp"]) == (140, 90)
    assert cvd["resting_hr"] == 72 and cvd["bmi"] == 22.9  # from the earlier, fuller entry


def test_an_older_measurement_entered_later_does_not_win(make_patient):
    email, headers = make_patient()
    fill_required(headers)
    earlier = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    post(headers, "/vitals", {"sbp": 150, "dbp": 95, "measured_at": earlier})
    assert build(email, "CVD")["sbp"] == 128


def test_pack_years_follow_the_latest_smoking_status(make_patient):
    email, headers = make_patient()
    fill_required(headers)
    post(headers, "/lifestyle", {"smoking_status": "current", "pack_years": 12.5})
    assert build(email, "CVD")["pack_years"] == 12.5
    post(headers, "/lifestyle", {"substance_use": "none"})
    assert build(email, "NEURO")["alcohol_use"] == 0


def test_a_female_patient_must_answer_gestational_diabetes(make_patient):
    email, headers = make_patient(sex="Female")
    fill_required(headers, sex="Female")
    assert build(email, "T2D")["gestational_diabetes"] == 1
    post(headers, "/health-history", {"acanthosis_nigricans": False})  # PUT: the rest become null
    with pytest.raises(feature_builder.MissingRequired) as error:
        build(email, "T2D")
    assert "gestational_diabetes" in error.value.names


def test_only_confirmed_labs_are_used_and_the_latest_sample_wins(make_patient):
    email, headers = make_patient()
    fill_required(headers)
    post(headers, "/labs", {"test_code": "FPG", "value": 5.6, "unit": "mmol/L", "collected_on": "2026-06-01"})
    post(headers, "/labs", {"test_code": "FPG", "value": 90, "unit": "mg/dL", "collected_on": "2026-01-01"})
    post(headers, "/labs", {"test_code": "HBA1C", "value": 48, "unit": "mmol/mol"})
    t2d = build(email, "T2D")
    assert t2d["fpg"] == 100.89 and t2d["hba1c"] == 6.543
    assert build(email, "CVD")["ldl"] is None


def test_unconfirmed_ocr_values_are_not_used(make_patient):
    from backend.models import LabResult
    email, headers = make_patient()
    fill_required(headers)
    with SessionLocal() as session:
        user_id = session.scalar(select(User.user_id).where(User.email == email))
        set_request_identity(session, user_id=user_id)
        patient_id = session.scalar(select(Patient.patient_id))
        set_request_identity(session, user_id=user_id, patient_id=patient_id)
        session.add(LabResult(patient_id=patient_id, test_code="LDL", value=130, unit="mg/dL", source="ocr"))
        session.commit()
    assert build(email, "CVD")["ldl"] is None


def test_another_patients_data_is_never_used(make_patient):
    _, filled = make_patient()
    fill_required(filled)
    empty_email, _ = make_patient()
    cvd = build(empty_email, "CVD", strict=False)
    assert cvd["sbp"] is None and cvd["fhrs_cvd"] is None and cvd["pack_years"] is None


def test_an_unknown_model_is_refused(make_patient):
    email, _ = make_patient()
    with pytest.raises(ValueError):
        build(email, "CANCER")


def test_the_patient_id_filter_works_even_if_rls_shows_someone_else(make_patient):
    """Second layer: the session's RLS identity is the filled patient, but the builder is asked
    for the empty one. It must not read the filled patient's data as the empty patient's."""
    filled_email, filled = make_patient()
    fill_required(filled)
    empty_email, _ = make_patient()
    filled_user, filled_patient = ids_of(filled_email)
    _, empty_patient = ids_of(empty_email)
    with SessionLocal() as session:
        set_request_identity(session, user_id=filled_user, patient_id=filled_patient)
        with pytest.raises(NoResultFound):  # the empty patient's row is not visible: no data at all
            feature_builder.build_all(session, empty_patient, as_of=AS_OF, strict=False)


def test_a_value_above_the_range_is_clipped_and_listed_as_capped(make_patient):
    email, headers = make_patient()
    fill_required(headers)
    post(headers, "/vitals", {"sbp": 240, "dbp": 82})  # features.yaml sbp range: 70-220
    result = build_inputs(email)
    assert result.values["CVD"]["sbp"] == 220 and result.values["T2D"]["sbp"] == 220
    assert result.capped["CVD"]["sbp"] == {"entered": 240, "used": 220}
    assert result.values["CVD"]["dbp"] == 82 and "dbp" not in result.capped["CVD"]  # inside: untouched


def test_values_inside_the_range_are_not_capped(make_patient):
    email, headers = make_patient()
    fill_required(headers)
    result = build_inputs(email)
    assert result.capped == {"CVD": {}, "T2D": {}, "CRD": {}, "NEURO": {}}
    assert result.values["CVD"]["sbp"] == 128


@pytest.mark.parametrize("value, bounds, expected", [
    (240, (70, 220), 220), (50, (70, 220), 70), (128, (70, 220), 128),
    (65.0, (12.0, 60.0), 60.0), (7.5, (1.0, 16.0), 7.5),
    (50.0, (0.0, None), 50.0), (-1.0, (0.0, None), 0.0),   # FHRS: no fixed maximum
    (None, (70, 220), None), ("Male", None, "Male"), (1, None, 1),
])
def test_clip(value, bounds, expected):
    assert feature_builder.clip(value, bounds) == expected
    assert type(feature_builder.clip(value, bounds)) is type(expected)


def test_a_lab_without_a_sample_date_counts_from_the_day_it_was_saved(make_patient):
    email, headers = make_patient()
    fill_required(headers)
    post(headers, "/labs", {"test_code": "LDL", "value": 110, "unit": "mg/dL", "collected_on": "2025-01-15"})
    post(headers, "/labs", {"test_code": "LDL", "value": 140, "unit": "mg/dL"})  # no date, saved today
    assert build(email, "CVD")["ldl"] == 140
