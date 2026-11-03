"""Risk endpoint: the four models run on the patient's saved data, and the assessment, results and
SHAP values are saved together. Throw-away models stand in for the trained ones; they use the
placeholder model_versions rows from tests/seed_test_models.sql (risk_results must point to a
registered model)."""

import math
import uuid
import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from backend import audit, model_registry, risk
from backend.config import settings
from backend.database import SessionLocal, set_request_identity
from backend.main import app
from backend.models import ModelVersion, Patient, RiskAssessment, RiskResult, ShapExplanation, User
from backend.readiness import MODELS, load_features

client = TestClient(app)
PASSWORD = "correct horse battery"
FEATURES = {m: [f.name for f in load_features() if f.model == m] for m in MODELS}


# ---------------------------------------------------------------------------
# Throw-away models
# ---------------------------------------------------------------------------
def train(path, n_features, seed, calibrated=False):
    from xgboost import XGBClassifier
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(300, n_features)) * 50 + 50
    y = (x[:, 0] + x[:, 1] + rng.normal(size=300) * 30 > 100).astype(int)
    model = XGBClassifier(n_estimators=8, max_depth=3, random_state=seed)
    if calibrated:
        import joblib
        from sklearn.calibration import CalibratedClassifierCV
        joblib.dump(CalibratedClassifierCV(model, cv=3).fit(x, y), path)
    else:
        model.fit(x, y)
        model.save_model(str(path))
    return path


@pytest.fixture(scope="module")
def placeholder_ids():
    with SessionLocal() as session:
        ids = dict(session.execute(select(ModelVersion.disease_domain, ModelVersion.model_version_id)
                                   .where(ModelVersion.version == "test-only")).all())
    if set(ids) != set(MODELS):
        pytest.skip("Run tests/seed_test_models.sql on med_db_test as postgres "
                    "(or rebuild it with database/setup_windows.ps1 -Test)")
    return ids


@pytest.fixture
def models_on(tmp_path, monkeypatch, placeholder_ids):
    """Loads four throw-away models (CRD as a calibrated .joblib, like the real ones will be)."""
    import hashlib
    monkeypatch.setattr(settings, "model_dir", tmp_path)
    monkeypatch.setattr(settings, "show_draft_care_plans", True)  # templates are still drafts
    loaded = {}
    for seed, disease in enumerate(MODELS):
        calibrated = disease == "CRD"
        path = train(tmp_path / f"{disease.lower()}.{'joblib' if calibrated else 'json'}",
                     len(FEATURES[disease]), seed, calibrated)
        row = ModelVersion(model_version_id=placeholder_ids[disease], disease_domain=disease, version="test-only",
                           artifact_path=str(path), artifact_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                           feature_list=FEATURES[disease], thresholds={"moderate": 0.2, "high": 0.5})
        loaded[disease] = model_registry.load_model(row)
    monkeypatch.setattr(model_registry, "ACTIVE", loaded)
    return loaded


# ---------------------------------------------------------------------------
# Patients
# ---------------------------------------------------------------------------
@pytest.fixture
def make_patient():
    made = []

    def make():
        email = f"risk-{uuid.uuid4().hex[:8]}@test.local"
        made.append(email)
        assert client.post("/auth/register", json={
            "email": email, "password": PASSWORD, "first_name": "Marcelo", "last_name": "del Pilar",
            "date_of_birth": "1978-08-30", "biological_sex": "Male",
            "privacy_consent": True, "consent_version": settings.privacy_notice_version,
        }).status_code == 201
        token = client.post("/auth/login", json={"email": email, "password": PASSWORD}).json()["access_token"]
        return email, {"Authorization": f"Bearer {token}"}

    yield make
    with SessionLocal() as session:
        session.execute(delete(User).where(func.lower(User.email).in_(made)))
        session.commit()


def send(headers, path, body):
    response = client.request("PUT" if path == "/health-history" else "POST", path, headers=headers, json=body)
    assert response.status_code in (200, 201), response.text


def fill_required(headers):
    send(headers, "/vitals", {"sbp": 138, "dbp": 88, "resting_hr": 80, "weight_kg": 82.0, "height_cm": 168.0})
    send(headers, "/lifestyle", {"smoking_status": "current", "pack_years": 15.0, "physical_activity_min_week": 60,
                                 "sedentary_hours_day": 9.0, "sleep_hours_day": 6.0, "occupational_dust": True,
                                 "substance_use": "current", "sodium_mg_day": 3200, "carbs_g_day": 320})
    send(headers, "/health-history", {"acanthosis_nigricans": False, "allergic_rhinitis": True,
                                      "childhood_asthma": False, "tbi_history": False})
    send(headers, "/questionnaires", {"instrument": "MMRC", "answers": [2]})
    send(headers, "/questionnaires", {"instrument": "PHQ9", "answers": [1, 2, 1, 1, 1, 0, 1, 0, 0]})
    send(headers, "/questionnaires", {"instrument": "GAD7", "answers": [1, 1, 1, 1, 0, 0, 0]})
    send(headers, "/family-history", {"disease_domain": "CVD", "specific_diagnosis": "Heart attack",
                                      "relative": "father", "age_at_diagnosis": 48, "diagnosis_confirmed": "yes"})
    send(headers, "/labs", {"test_code": "LDL", "value": 160, "unit": "mg/dL"})


def saved_rows(email):
    with SessionLocal() as session:
        user_id = session.scalar(select(User.user_id).where(User.email == email))
        set_request_identity(session, user_id=user_id)
        patient_id = session.scalar(select(Patient.patient_id))
        set_request_identity(session, user_id=user_id, patient_id=patient_id)
        return (session.scalars(select(RiskAssessment)).all(), session.scalars(select(RiskResult)).all(),
                session.scalars(select(ShapExplanation)).all())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_models_off_returns_503(make_patient, monkeypatch):
    monkeypatch.setattr(model_registry, "ACTIVE", {})
    _, headers = make_patient()
    fill_required(headers)
    assert client.post("/assessment/run", headers=headers).status_code == 503


def test_missing_required_inputs_return_the_checklist(make_patient, models_on):
    email, headers = make_patient()
    response = client.post("/assessment/run", headers=headers)
    assert response.status_code == 409
    missing = [item["name"] for item in response.json()["detail"]["missing_required"]]
    assert "sbp" in missing and "phq9" in missing
    assert saved_rows(email) == ([], [], [])


def test_a_run_saves_the_assessment_results_and_every_shap_value(make_patient, models_on):
    email, headers = make_patient()
    fill_required(headers)
    response = client.post("/assessment/run", headers=headers)
    assert response.status_code == 201, response.text
    assert response.headers["Cache-Control"] == "no-store"
    data = response.json()
    assert response.headers["Location"] == f"/assessment/{data['assessment_id']}"
    assert [r["disease"] for r in data["results"]] == list(MODELS)
    assessments, results, shap = saved_rows(email)
    assert len(assessments) == 1 and len(results) == 4
    assert len(shap) == sum(len(names) for names in FEATURES.values())  # every input of every model
    for result in results:
        ranks = sorted(row.rank for row in shap if row.result_id == result.result_id)
        assert ranks == list(range(1, len(FEATURES[result.disease_domain]) + 1))
    assert set(assessments[0].input_snapshot) == set(MODELS)


def test_the_same_inputs_always_give_the_same_saved_result(make_patient, models_on):
    email, headers = make_patient()
    fill_required(headers)
    first = client.post("/assessment/run", headers=headers).json()
    second = client.post("/assessment/run", headers=headers).json()
    assert first["assessment_id"] != second["assessment_id"]
    strip = lambda d: [{k: v for k, v in r.items() if k != "care_plan"} for r in d["results"]]
    assert strip(first) == strip(second)
    assessments, results, shap = saved_rows(email)
    by_assessment = {}
    for row in shap:
        result = next(r for r in results if r.result_id == row.result_id)
        by_assessment.setdefault(result.assessment_id, set()).add(
            (result.disease_domain, row.feature_name, row.feature_value, row.shap_value, row.rank))
    a, b = by_assessment.values()
    assert a == b


def test_probabilities_and_tiers_match_the_models(make_patient, models_on):
    _, headers = make_patient()
    fill_required(headers)
    data = client.post("/assessment/run", headers=headers).json()
    for result in data["results"]:
        assert 0.0 <= result["probability"] <= 1.0
        model = models_on[result["disease"]]
        assert result["risk_level"] == model.tier(result["probability"])
        assert result["model_version"] == "test-only"
        assert result["consult_recommended"] == (result["risk_level"] == "High")
        assert (result["consult_message"] is not None) == result["consult_recommended"]


def test_shap_values_add_up_to_the_model_score(make_patient, models_on):
    """For a plain XGBoost model, base value + SHAP values = the model's log-odds (TreeSHAP)."""
    email, headers = make_patient()
    fill_required(headers)
    data = client.post("/assessment/run", headers=headers).json()
    assessments, results, shap = saved_rows(email)
    cvd = next(r for r in results if r.disease_domain == "CVD")
    total = float(cvd.shap_base_value) + sum(float(row.shap_value) for row in shap if row.result_id == cvd.result_id)
    probability = next(r["probability"] for r in data["results"] if r["disease"] == "CVD")
    assert abs(1 / (1 + math.exp(-total)) - probability) < 1e-3


def test_saved_feature_values_are_the_numbers_the_model_got(make_patient, models_on):
    email, headers = make_patient()
    fill_required(headers)
    client.post("/assessment/run", headers=headers)
    _, results, shap = saved_rows(email)
    neuro = next(r for r in results if r.disease_domain == "NEURO")
    values = {row.feature_name: row.feature_value for row in shap if row.result_id == neuro.result_id}
    assert values["sex"] == 1          # Male, from the encoding in ml/features.yaml
    assert values["tsh"] is None       # optional lab not entered
    assert values["phq9"] == 7


def test_results_explain_the_top_factors_in_plain_language(make_patient, models_on):
    _, headers = make_patient()
    fill_required(headers)
    data = client.post("/assessment/run", headers=headers).json()
    for result in data["results"]:
        factors = result["top_factors"]
        assert [f["rank"] for f in factors] == [1, 2, 3]
        assert all(f["sentence"].endswith(".") for f in factors)
        assert abs(factors[0]["shap_value"]) >= abs(factors[1]["shap_value"]) >= abs(factors[2]["shap_value"])
    assert data["disclaimer"].startswith("This is an educational screening estimate")


def test_each_result_comes_with_the_care_plan_for_its_tier(make_patient, models_on):
    _, headers = make_patient()
    fill_required(headers)
    data = client.post("/assessment/run", headers=headers).json()
    for result in data["results"]:
        plan = result["care_plan"]
        assert plan["disease_domain"] == result["disease"] and plan["risk_level"] == result["risk_level"]
        assert plan["base"] and plan["is_draft"] is True
    cvd_plan = data["results"][0]["care_plan"]
    assert "A1" in [a["code"] for a in cvd_plan["addons"]]  # current smoker


def test_without_approved_templates_the_care_plan_is_empty(make_patient, models_on, monkeypatch):
    monkeypatch.setattr(settings, "show_draft_care_plans", False)
    _, headers = make_patient()
    fill_required(headers)
    data = client.post("/assessment/run", headers=headers).json()
    assert all(result["care_plan"] is None for result in data["results"])


def test_saved_assessments_can_be_read_back(make_patient, models_on):
    _, headers = make_patient()
    fill_required(headers)
    created = client.post("/assessment/run", headers=headers).json()
    again = client.get(f"/assessment/{created['assessment_id']}", headers=headers)
    assert again.status_code == 200 and again.json() == created
    listed = client.get("/assessment", headers=headers).json()
    assert [row["assessment_id"] for row in listed] == [created["assessment_id"]]
    assert set(listed[0]["risk_levels"]) == set(MODELS)


def test_another_patient_cannot_read_an_assessment(make_patient, models_on):
    _, owner = make_patient()
    fill_required(owner)
    assessment_id = client.post("/assessment/run", headers=owner).json()["assessment_id"]
    _, other = make_patient()
    assert client.get(f"/assessment/{assessment_id}", headers=other).status_code == 404
    assert client.get("/assessment", headers=other).json() == []


def test_a_failure_part_way_saves_nothing(make_patient, models_on, monkeypatch):
    """One transaction: if the third model fails, the first two results are not kept either."""
    email, headers = make_patient()
    fill_required(headers)

    def broken(inputs):
        raise RuntimeError("model crashed")

    monkeypatch.setattr(models_on["CRD"], "contributions", broken)
    with pytest.raises(RuntimeError):
        client.post("/assessment/run", headers=headers)
    assert saved_rows(email) == ([], [], [])


def test_a_run_is_audited_without_results(make_patient, models_on, monkeypatch):
    recorded = []
    real = audit.log_event

    def recording(db, event_type, **kwargs):
        recorded.append((event_type, kwargs))
        return real(db, event_type, **kwargs)

    monkeypatch.setattr(audit, "log_event", recording)
    _, headers = make_patient()
    fill_required(headers)
    data = client.post("/assessment/run", headers=headers).json()
    event_type, details = recorded[-1]
    assert event_type == "RISK_ASSESSMENT_RUN" and details["record_id"] == data["assessment_id"]
    assert details["model_versions"] == {d: "test-only" for d in MODELS}
    text = str(details)
    assert "High" not in text and "Low" not in text and "probability" not in text


def test_assessments_need_a_login():
    assert client.post("/assessment/run").status_code == 401
    assert client.get("/assessment").status_code == 401
