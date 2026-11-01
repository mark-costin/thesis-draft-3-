"""Model registry: only registered, unchanged model files are loaded, and only with the inputs
ml/features.yaml lists. Small throw-away models stand in for the real ones."""

import hashlib

import numpy as np
import pytest
from sqlalchemy.exc import DBAPIError

from backend import model_registry
from backend.config import settings
from backend.models import ModelVersion
from backend.readiness import MODELS, load_features

NEURO = [f.name for f in load_features() if f.model == "NEURO"]
INPUTS = {"age": 46, "sex": "Male", "phq9": 12, "gad7": 9, "psqi": 7, "sleep_hours_day": 6.0,
          "alcohol_use": 1, "tbi_history": 0, "fhrs_neuro": 0.25, "tsh": None, "vitamin_b12": None}


@pytest.fixture(autouse=True)
def model_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "model_dir", tmp_path)
    return tmp_path


def train_xgboost(path, n_features=len(NEURO), seed=0):
    from xgboost import XGBClassifier
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(200, n_features))
    y = (x[:, 0] + rng.normal(size=200) > 0).astype(int)
    model = XGBClassifier(n_estimators=5, max_depth=2)
    model.fit(x, y)
    model.save_model(str(path))
    return path


def train_calibrated(path, n_features=len(NEURO)):
    import joblib
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.linear_model import LogisticRegression
    rng = np.random.default_rng(1)
    x = rng.normal(size=(200, n_features))
    y = (x[:, 1] > 0).astype(int)
    joblib.dump(CalibratedClassifierCV(LogisticRegression(), cv=3).fit(x, y), path)
    return path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def registered(path, disease="NEURO", features=None, digest=None, thresholds=None):
    """A model_versions row as the database owner would register it (not saved: med_app can't)."""
    return ModelVersion(model_version_id=7, disease_domain=disease, version="1.0", artifact_path=str(path),
                        artifact_sha256=digest or sha(path), feature_list=features or NEURO,
                        thresholds=thresholds or {"moderate": 0.2, "high": 0.5}, is_active=True)


def test_a_registered_model_loads_and_predicts(model_dir):
    model = model_registry.load_model(registered(train_xgboost(model_dir / "neuro_v1.json")))
    assert (model.disease, model.version, model.model_version_id) == ("NEURO", "1.0", 7)
    assert model.features == NEURO
    probability = model.probability(INPUTS)  # missing labs are NaN, which XGBoost accepts
    assert 0.0 <= probability <= 1.0
    assert model.probability(INPUTS) == probability  # same inputs, same result


def test_a_calibrated_joblib_model_loads(model_dir):
    model = model_registry.load_model(registered(train_calibrated(model_dir / "neuro_v1.joblib")))
    full = INPUTS | {"tsh": 2.0, "vitamin_b12": 400}
    assert 0.0 <= model.probability(full) <= 1.0


def test_a_changed_file_is_refused(model_dir):
    path = train_xgboost(model_dir / "neuro_v1.json")
    row = registered(path)
    train_xgboost(path, seed=99)  # someone swaps in a different model under the same name
    with pytest.raises(model_registry.ModelTampered):
        model_registry.load_model(row)


def test_the_checksum_is_checked_before_the_file_is_opened(model_dir, monkeypatch):
    """A joblib (pickle) file runs code when loaded, so a changed one must never be opened."""
    import joblib
    path = train_calibrated(model_dir / "neuro_v1.joblib")
    row = registered(path)
    path.write_bytes(path.read_bytes() + b"\x00")
    monkeypatch.setattr(joblib, "load", lambda *a, **k: pytest.fail("a changed file was opened"))
    with pytest.raises(model_registry.ModelTampered):
        model_registry.load_model(row)


def test_files_outside_the_model_folder_are_refused(model_dir, tmp_path_factory):
    outside = train_xgboost(tmp_path_factory.mktemp("elsewhere") / "neuro.json")
    with pytest.raises(model_registry.ModelError):
        model_registry.load_model(registered(outside))
    sneaky = model_dir / ".." / outside.parent.name / "neuro.json"
    with pytest.raises(model_registry.ModelError):
        model_registry.load_model(registered(sneaky, digest=sha(outside)))


def test_a_missing_file_is_refused(model_dir):
    with pytest.raises(model_registry.ModelError):
        model_registry.load_model(registered(model_dir / "gone.json", digest="0" * 64))


@pytest.mark.parametrize("features", [
    NEURO[:-1],                     # one input missing
    NEURO + ["shoe_size"],          # an input no one enters
    NEURO[:-1] + [NEURO[0]],        # the same input twice
], ids=["missing", "extra", "duplicate"])
def test_the_feature_list_must_match_features_yaml(model_dir, features):
    path = train_xgboost(model_dir / "neuro_v1.json", n_features=len(features))
    with pytest.raises(model_registry.ModelError):
        model_registry.load_model(registered(path, features=features))


def test_the_model_keeps_its_own_feature_order(model_dir):
    order = list(reversed(NEURO))
    model = model_registry.load_model(registered(train_xgboost(model_dir / "neuro_v1.json"), features=order))
    row = model.to_row(INPUTS)
    assert row.shape == (1, len(NEURO))
    assert row[0, order.index("phq9")] == 12 and row[0, order.index("sex")] == 1
    assert np.isnan(row[0, order.index("tsh")])


def test_an_unknown_file_type_is_refused(model_dir):
    path = model_dir / "neuro_v1.pkl"
    path.write_bytes(b"not a model")
    with pytest.raises(model_registry.ModelError):
        model_registry.load_model(registered(path))


def test_text_inputs_other_than_sex_are_refused(model_dir):
    model = model_registry.load_model(registered(train_xgboost(model_dir / "neuro_v1.json")))
    with pytest.raises(model_registry.ModelError):
        model.to_row(INPUTS | {"sex": "Other"})
    with pytest.raises(model_registry.ModelError):
        model.to_row(INPUTS | {"phq9": "twelve"})


@pytest.mark.parametrize("probability, tier", [
    (0.0, "Low"), (0.199, "Low"), (0.2, "Moderate"), (0.499, "Moderate"), (0.5, "High"), (0.97, "High"),
])
def test_risk_tiers_use_the_registered_cutoffs(model_dir, probability, tier):
    model = model_registry.load_model(registered(train_xgboost(model_dir / "neuro_v1.json")))
    assert model.tier(probability) == tier


# ---------------------------------------------------------------------------
# Database and startup
# ---------------------------------------------------------------------------
def test_no_active_models_yet(db):
    with pytest.raises(model_registry.ModelsMissing) as error:
        model_registry.load_active(db)
    assert all(disease in str(error.value) for disease in MODELS)
    assert model_registry.load_active(db, required=False) == {}


def test_the_app_cannot_register_a_model(db, model_dir):
    """model_versions is read-only for med_app: only the database owner can add a model."""
    row = registered(train_xgboost(model_dir / "neuro_v1.json"))
    row.model_version_id = None  # let the database number it, as the owner would
    db.add(row)
    with pytest.raises(DBAPIError, match="permission denied"):
        db.flush()


def test_startup_does_nothing_while_risk_models_are_off(monkeypatch):
    monkeypatch.setattr(settings, "risk_models_enabled", False)
    monkeypatch.setattr(model_registry, "load_active", lambda *a, **k: pytest.fail("should not load"))
    model_registry.load_at_startup()


def test_startup_stops_when_models_are_missing(monkeypatch):
    monkeypatch.setattr(settings, "risk_models_enabled", True)
    with pytest.raises(model_registry.ModelsMissing):
        model_registry.load_at_startup()


def test_a_changed_model_file_stops_the_app_from_starting(model_dir, monkeypatch):
    path = train_xgboost(model_dir / "neuro_v1.json")
    row = registered(path)
    path.write_bytes(path.read_bytes().replace(b"}", b" }", 1))  # same model, one byte different
    monkeypatch.setattr(settings, "risk_models_enabled", True)
    monkeypatch.setattr(model_registry, "load_active",
                        lambda db, required=True: {"NEURO": model_registry.load_model(row)})
    with pytest.raises(model_registry.ModelTampered):
        model_registry.load_at_startup()
    assert model_registry.ACTIVE == {}
