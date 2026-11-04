"""Shared by the risk and report tests: four throw-away models loaded as the active ones, and
a patient with every required input saved. The models use the placeholder model_versions rows
from tests/seed_test_models.sql (risk_results must point to a registered model)."""

import uuid

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from backend import model_registry
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
