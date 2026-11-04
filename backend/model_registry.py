"""Loads the four risk models the database allows, and refuses any file that was changed (Sprint 5).

model_versions (14_model_versions.sql) is read-only for the app: only the database owner can
register a model. For each active row the app:
  1. resolves artifact_path and refuses paths outside MODEL_DIR (no ../ tricks);
  2. reads the file ONCE into memory, hashes those bytes and refuses the model if the SHA-256
     differs from artifact_sha256; the model is then loaded from the same bytes, so the bytes
     that were checked are exactly the bytes that run (a pickled file runs code when loaded,
     and a file swapped on disk after the check is never read again);
  3. checks that feature_list has exactly the model's inputs in ml/features.yaml.

File formats: XGBoost JSON/UBJ (.json, .ubj) or a scikit-learn object saved with joblib
(.joblib), e.g. an XGBoost model wrapped in CalibratedClassifierCV.
"""

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config import ROOT_DIR, settings
from backend.models import ModelVersion
from backend.readiness import MODELS, load_features

# How text inputs are given to the models. PROVISIONAL: must match how the training data
# encodes sex (to be confirmed by the ML track).
SEX_CODES = {"Male": 1, "Female": 0}

ACTIVE: dict[str, "LoadedModel"] = {}  # filled at startup when RISK_MODELS_ENABLED=true


class ModelError(Exception):
    pass


class ModelTampered(ModelError):
    pass


class ModelsMissing(ModelError):
    pass


@dataclass
class LoadedModel:
    disease: str
    version: str
    model_version_id: int
    features: list[str]            # the order the model expects
    thresholds: dict[str, float]   # {"moderate": p, "high": p}
    estimator: object

    def to_row(self, inputs: dict) -> np.ndarray:
        """Feature builder output -> one row in the model's order. Missing = NaN."""
        row = []
        for name in self.features:
            value = inputs.get(name)
            if isinstance(value, str):
                if name != "sex" or value not in SEX_CODES:
                    raise ModelError(f"{self.disease}: cannot turn {name}={value!r} into a number")
                value = SEX_CODES[value]
            row.append(np.nan if value is None else float(value))
        return np.array([row], dtype=float)

    def probability(self, inputs: dict) -> float:
        return float(self.estimator.predict_proba(self.to_row(inputs))[0, 1])

    def tier(self, probability: float) -> str:
        """Low / Moderate / High, with this model's cutoffs (same names as the care plan tiers)."""
        if probability >= self.thresholds["high"]:
            return "High"
        if probability >= self.thresholds["moderate"]:
            return "Moderate"
        return "Low"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_path(artifact_path: str) -> Path:
    path = Path(artifact_path)
    path = (path if path.is_absolute() else ROOT_DIR / path).resolve()
    allowed = Path(settings.model_dir).resolve()
    if allowed not in path.parents:
        raise ModelError(f"Model file must be inside {allowed}: {artifact_path}")
    return path


_FILE_TYPES = (".json", ".ubj", ".joblib")


def _read(data: bytes, suffix: str):
    """Loads a model from bytes already checked against artifact_sha256 (never from the disk)."""
    if suffix in (".json", ".ubj"):
        from xgboost import XGBClassifier
        model = XGBClassifier()
        model.load_model(bytearray(data))
        return model
    if suffix == ".joblib":
        import joblib
        return joblib.load(io.BytesIO(data))
    raise ModelError(f"Unsupported model file type {suffix!r} (use .json, .ubj or .joblib)")


def load_model(row: ModelVersion) -> LoadedModel:
    path = resolve_path(row.artifact_path)
    suffix = path.suffix.lower()
    if suffix not in _FILE_TYPES:
        raise ModelError(f"Unsupported model file type: {path.name} (use .json, .ubj or .joblib)")
    if not path.is_file():
        raise ModelError(f"{row.disease_domain} v{row.version}: model file not found: {row.artifact_path}")
    data = path.read_bytes()  # read once: these bytes are hashed and these bytes are loaded
    if hashlib.sha256(data).hexdigest() != row.artifact_sha256:
        raise ModelTampered(f"{row.disease_domain} v{row.version}: {path.name} does not match its registered "
                            "SHA-256. The file was changed; the model is not loaded.")

    expected = {f.name for f in load_features() if f.model == row.disease_domain}
    features = list(row.feature_list)
    if len(features) != len(set(features)) or set(features) != expected:
        raise ModelError(f"{row.disease_domain} v{row.version}: feature_list does not match ml/features.yaml "
                         f"(missing {sorted(expected - set(features))}, extra {sorted(set(features) - expected)})")

    estimator = _read(data, suffix)
    if not hasattr(estimator, "predict_proba"):
        raise ModelError(f"{row.disease_domain} v{row.version}: the file has no predict_proba")
    thresholds = {key: float(row.thresholds[key]) for key in ("moderate", "high")}
    return LoadedModel(row.disease_domain, row.version, row.model_version_id, features, thresholds, estimator)


def load_active(db: Session, required: bool = True) -> dict[str, LoadedModel]:
    """The active model for each disease. required=True: all four must be there."""
    rows = list(db.scalars(select(ModelVersion).where(ModelVersion.is_active)))
    missing = sorted(set(MODELS) - {row.disease_domain for row in rows})
    if required and missing:
        raise ModelsMissing(f"No active model for: {', '.join(missing)}")
    return {row.disease_domain: load_model(row) for row in rows}


def load_at_startup() -> None:
    """Called by main.py. Does nothing until RISK_MODELS_ENABLED=true; then any missing or
    changed model, or a missing care plan template, stops the app from starting."""
    if not settings.risk_models_enabled:
        return
    from backend import care_plan
    from backend.database import SessionLocal

    with SessionLocal() as db:
        models = load_active(db, required=True)
        problems = care_plan.check_templates(db)
    if problems:
        raise ModelError("Care plan templates are not ready: " + "; ".join(problems))
    ACTIVE.clear()
    ACTIVE.update(models)
