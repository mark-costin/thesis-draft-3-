"""Missing-inputs check: compares what the patient has saved with what each model needs.

The list of model inputs comes from ml/features.yaml (one source of truth for the ML track and
the app). Each feature names a med_db column, and sometimes a "where" (lab test code,
questionnaire, disease). A feature counts as present when the patient has at least one saved
value for it. Lab values count only when the patient has confirmed them (08_lab_results.sql).
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.config import ROOT_DIR
from backend.models import (
    Base,
    LabResult,
    LifestyleRecord,
    Patient,
    PatientFhrsStratification,
    PatientHealthHistory,
    QuestionnaireResponse,
    VitalsRecord,
)

FEATURES_PATH = ROOT_DIR / "ml" / "features.yaml"
MODELS = ("CVD", "T2D", "CRD", "NEURO")

# Where the patient enters each kind of input (shown on the readiness checklist)
WHERE_TO_ENTER = {
    "patients": ("Profile", "/profile"),
    "vitals_records": ("Vitals", "/vitals"),
    "lifestyle_records": ("Lifestyle", "/lifestyle"),
    "patient_health_history": ("Health history", "/health-history"),
    "questionnaire_responses": ("Questionnaire", "/questionnaires"),
    "patient_fhrs_stratification": ("Family history", "/family-history"),
    "lab_results": ("Lab result (upload or type it in)", "/labs"),
}

# Tables where any saved row with a value counts
_ROW_TABLES = {"vitals_records": VitalsRecord, "lifestyle_records": LifestyleRecord}


@dataclass(frozen=True)
class Feature:
    model: str
    name: str
    label: str
    required: bool
    table: str
    column: str
    where: tuple  # ((key, value),) or ()
    source: str

    @property
    def key(self) -> str:
        """Identifies the stored input, so features shared by several models are checked once."""
        suffix = "".join(f"[{k}={v}]" for k, v in self.where)
        return f"{self.table}.{self.column}{suffix}"


@lru_cache
def load_features(path: Path = FEATURES_PATH) -> tuple[Feature, ...]:
    """Reads ml/features.yaml and checks every column exists, so a typo fails at startup."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    features = []
    for model in MODELS:
        for item in data["models"][model]["features"]:
            table, column = item["column"].split(".")
            if table not in WHERE_TO_ENTER or column not in Base.metadata.tables[table].columns:
                raise ValueError(f"features.yaml: unknown column {item['column']} ({model}.{item['name']})")
            features.append(Feature(
                model=model, name=item["name"], label=item["label"], required=bool(item["required"]),
                table=table, column=column, where=tuple(sorted((item.get("where") or {}).items())),
                source=item["source"],
            ))
    return tuple(features)


def present_inputs(db: Session, features: tuple[Feature, ...]) -> set[str]:
    """Keys of the inputs this patient has saved. Every query runs under RLS, so it only ever
    sees the caller's own rows."""
    present: set[str] = set()

    patient = db.scalars(select(Patient)).one()
    for column in ("date_of_birth", "biological_sex"):
        if getattr(patient, column) is not None:
            present.add(f"patients.{column}")

    # Vitals and lifestyle: a column counts if any saved entry has a value in it
    for table, model in _ROW_TABLES.items():
        columns = sorted({f.column for f in features if f.table == table})
        if columns:
            counts = db.execute(select(*[func.count(getattr(model, c)) for c in columns])).one()
            present.update(f"{table}.{c}" for c, n in zip(columns, counts) if n > 0)

    # pack_years: a never-smoker counts as 0 (features.yaml: derive)
    if db.scalar(select(func.count()).select_from(LifestyleRecord)
                 .where(LifestyleRecord.smoking_status == "never")):
        present.add("lifestyle_records.pack_years")

    # Health history: one row; null = not answered
    history = db.scalars(select(PatientHealthHistory)).one_or_none()
    for column in ("gestational_diabetes", "acanthosis_nigricans", "allergic_rhinitis",
                   "childhood_asthma", "tbi_history"):
        if history is not None and getattr(history, column) is not None:
            present.add(f"patient_health_history.{column}")
    if patient.biological_sex != "Female":  # gestational diabetes: false for male patients
        present.add("patient_health_history.gestational_diabetes")

    for instrument in db.scalars(select(QuestionnaireResponse.instrument).distinct()):
        present.add(f"questionnaire_responses.total_score[instrument={instrument}]")

    for domain in db.scalars(select(PatientFhrsStratification.disease_domain).distinct()):
        present.add(f"patient_fhrs_stratification.calculated_fhrs[disease_domain={domain}]")

    for code in db.scalars(select(LabResult.test_code).where(LabResult.is_confirmed).distinct()):
        present.add(f"lab_results.value[test_code={code}]")

    return present


def readiness(db: Session) -> dict:
    features = load_features()
    present = present_inputs(db, features)

    models = {}
    checklist: dict[str, dict] = {}  # required inputs still missing, each listed once
    for model in MODELS:
        missing_required, missing_optional = [], []
        for f in (f for f in features if f.model == model):
            if f.key in present:
                continue
            area, path = WHERE_TO_ENTER[f.table]
            item = {"name": f.name, "label": f.label, "source": f.source, "enter_in": area, "path": path}
            if f.table == "questionnaire_responses":
                item["instrument"] = dict(f.where)["instrument"]
            if f.required:
                missing_required.append(item)
                checklist.setdefault(f.key, item | {"models": []})["models"].append(model)
            else:
                missing_optional.append(item)
        models[model] = {"ready": not missing_required,
                         "missing_required": missing_required,
                         "missing_optional": missing_optional}

    return {"ready": all(m["ready"] for m in models.values()),
            "missing_required": list(checklist.values()),
            "models": models}
