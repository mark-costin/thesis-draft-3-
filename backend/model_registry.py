"""Loads the four risk models the database allows, and refuses any file that was changed (Sprint 5).

model_versions (14_model_versions.sql) is read-only for the app: only the database owner can
register a model. For each active row the app:
  1. resolves artifact_path and refuses paths outside MODEL_DIR (no ../ tricks);
  2. recomputes the file's SHA-256 and refuses the model if it differs from artifact_sha256,
     BEFORE the file is opened by any model library (a pickled file runs code when loaded);
  3. checks that feature_list has exactly the model's inputs in ml/features.yaml;
  4. if metrics lists the package versions the file was built with, checks they match the
     installed ones (a .joblib file may not load, or may load wrongly, with other versions).

Text inputs (sex) become numbers with the "encoding" line of ml/features.yaml, the same line the
synthetic data and the training read, so the app and the models cannot disagree.

File formats: XGBoost JSON/UBJ (.json, .ubj) or a scikit-learn object saved with joblib
(.joblib), e.g. an XGBoost model wrapped in CalibratedClassifierCV.
"""

import hashlib
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config import ROOT_DIR, settings
from backend.models import ModelVersion
from backend.readiness import MODELS, load_features

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
    encodings: dict[str, dict[str, float]] = field(default_factory=dict)  # from ml/features.yaml

    def to_row(self, inputs: dict) -> np.ndarray:
        """Feature builder output -> one row in the model's order. Missing = NaN."""
        row = []
        for name in self.features:
            value = inputs.get(name)
            if isinstance(value, str):
                codes = self.encodings.get(name, {})
                if value not in codes:
                    raise ModelError(f"{self.disease}: cannot turn {name}={value!r} into a number "
                                     "(no encoding for it in ml/features.yaml)")
                value = codes[value]
            row.append(np.nan if value is None else float(value))
        return np.array([row], dtype=float)

    def _matrix(self, row: np.ndarray, names):
        """A model trained on a pandas DataFrame expects the same column names; one trained on a
        plain array expects an array."""
        if names is not None:
            import pandas as pd
            return pd.DataFrame(row, columns=list(names))
        return row

    def probability(self, inputs: dict) -> float:
        names = getattr(self.estimator, "feature_names_in_", None)
        X = self._matrix(self.to_row(inputs), self.features if names is not None else None)
        return float(self.estimator.predict_proba(X)[0, 1])

    def _boosters(self) -> list:
        """The XGBoost booster(s) inside the model: one for a plain XGBClassifier, one per fold for
        CalibratedClassifierCV (each fold has its own fitted copy)."""
        if hasattr(self.estimator, "get_booster"):
            return [self.estimator.get_booster()]
        folds = getattr(self.estimator, "calibrated_classifiers_", None) or []
        boosters = [fold.estimator.get_booster() for fold in folds if hasattr(fold.estimator, "get_booster")]
        if not boosters:
            raise ModelError(f"{self.disease}: SHAP values need an XGBoost model inside the file")
        return boosters

    def contributions(self, inputs: dict) -> tuple[dict[str, float], float]:
        """SHAP values per input and the base value, in log-odds, from XGBoost's own TreeSHAP
        (pred_contribs). For a calibrated model they explain the XGBoost score before calibration,
        averaged over the folds; the sign (raised / lowered risk) is what the patient sees."""
        from xgboost import DMatrix
        row = self.to_row(inputs)
        totals = np.zeros(len(self.features) + 1)
        boosters = self._boosters()
        for booster in boosters:
            names = booster.feature_names
            matrix = DMatrix(row, missing=np.nan, feature_names=self.features if names else None)
            totals += booster.predict(matrix, pred_contribs=True)[0]
        totals /= len(boosters)
        return dict(zip(self.features, (float(v) for v in totals[:-1]))), float(totals[-1])

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


def _read(path: Path):
    suffix = path.suffix.lower()
    if suffix in (".json", ".ubj"):
        from xgboost import XGBClassifier
        model = XGBClassifier()
        model.load_model(str(path))
        return model
    if suffix == ".joblib":
        import joblib
        return joblib.load(path)
    raise ModelError(f"Unsupported model file type: {path.name} (use .json, .ubj or .joblib)")


def load_model(row: ModelVersion) -> LoadedModel:
    path = resolve_path(row.artifact_path)
    if not path.is_file():
        raise ModelError(f"{row.disease_domain} v{row.version}: model file not found: {row.artifact_path}")
    if sha256_of(path) != row.artifact_sha256:
        raise ModelTampered(f"{row.disease_domain} v{row.version}: {path.name} does not match its registered "
                            "SHA-256. The file was changed; the model is not loaded.")

    model_features = [f for f in load_features() if f.model == row.disease_domain]
    expected = {f.name for f in model_features}
    features = list(row.feature_list)
    if len(features) != len(set(features)) or set(features) != expected:
        raise ModelError(f"{row.disease_domain} v{row.version}: feature_list does not match ml/features.yaml "
                         f"(missing {sorted(expected - set(features))}, extra {sorted(set(features) - expected)})")

    check_versions(row)
    estimator = _read(path)
    if not hasattr(estimator, "predict_proba"):
        raise ModelError(f"{row.disease_domain} v{row.version}: the file has no predict_proba")
    thresholds = {key: float(row.thresholds[key]) for key in ("moderate", "high")}
    encodings = {f.name: {text: float(number) for text, number in f.encoding}
                 for f in model_features if f.encoding}
    return LoadedModel(row.disease_domain, row.version, row.model_version_id, features, thresholds,
                       estimator, encodings)


def check_versions(row: ModelVersion) -> None:
    """metrics may hold {"versions": {"xgboost": "3.2.0", "scikit-learn": "1.9.1", ...}}, the
    packages the file was built with. Each must match what is installed here."""
    wanted = (row.metrics or {}).get("versions") or {}
    for package, version in wanted.items():
        try:
            installed = metadata.version(package)
        except metadata.PackageNotFoundError:
            installed = None
        if installed != str(version):
            raise ModelError(f"{row.disease_domain} v{row.version}: built with {package} {version}, "
                             f"but {installed or 'none'} is installed. Run uv sync, or retrain the model.")


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
