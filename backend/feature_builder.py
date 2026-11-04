"""Builds each risk model's inputs from the patient's saved data (Sprint 5).

The features, their order and their columns come from ml/features.yaml (the same file the
readiness check and the ML pipeline read), so the app and the models always agree.

Rules:
- the latest saved value is used: vitals by measured_at, lifestyle by recorded_at, questionnaires
  by completed_at, FHRS by evaluated_at, labs by collected_on (created_at when a lab has no date);
- each vitals or lifestyle column takes its latest non-empty value, because one entry may hold
  only some measurements;
- only confirmed lab values are used (patient-in-the-loop);
- a missing optional input becomes None (the model treats it as missing); a missing required
  input raises MissingRequired, because the assessment must not run without it;
- the "derive" rules of features.yaml: age on the assessment date, pack_years = 0 for a
  never-smoker, gestational_diabetes = 0 for male patients, alcohol_use from substance_use;
- every numeric input is clipped to its features.yaml range (the range the models were trained
  on). What was capped is returned by build_inputs(), so the assessment can save it next to the
  inputs: a saved result never silently shows a value the patient did not enter.

Every query filters on the patient_id from the verified JWT; RLS is the second layer.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, cast, func, select
from sqlalchemy.orm import Session

from backend.models import (
    LabResult,
    LifestyleRecord,
    Patient,
    PatientFhrsStratification,
    PatientHealthHistory,
    QuestionnaireResponse,
    VitalsRecord,
)
from backend.readiness import MODELS, Feature, load_features
from backend.schemas import age_on

Value = float | int | str | None

# substance_use -> alcohol_use (features.yaml: 1 when 'past' or 'current', 0 when 'none')
_ALCOHOL = {"none": 0, "past": 1, "current": 1}


class MissingRequired(Exception):
    def __init__(self, model: str, names: list[str]):
        super().__init__(f"{model} is missing required inputs: {', '.join(names)}")
        self.model = model
        self.names = names


def _number(value) -> Value:
    if value is None:
        return None
    if isinstance(value, bool):  # binary inputs are 1 / 0
        return int(value)
    if isinstance(value, Decimal):
        return float(value)
    return value


class _Reader:
    """Reads each stored input once, even when several models use it."""

    def __init__(self, db: Session, patient_id: int, as_of: date):
        self.db = db
        self.patient_id = patient_id
        self.as_of = as_of
        self._cache: dict[str, Value] = {}
        self._patient = db.scalars(select(Patient).where(Patient.patient_id == patient_id)).one()
        self._history = db.scalars(select(PatientHealthHistory)
                                   .where(PatientHealthHistory.patient_id == patient_id)).one_or_none()

    def _latest(self, model, column: str, order) -> Value:
        attribute = getattr(model, column)
        return self.db.scalar(select(attribute)
                              .where(model.patient_id == self.patient_id, attribute.is_not(None))
                              .order_by(*order).limit(1))

    def _vitals(self, column: str) -> Value:
        return self._latest(VitalsRecord, column,
                            (VitalsRecord.measured_at.desc(), VitalsRecord.vitals_id.desc()))

    def _lifestyle(self, column: str) -> Value:
        return self._latest(LifestyleRecord, column,
                            (LifestyleRecord.recorded_at.desc(), LifestyleRecord.lifestyle_id.desc()))

    def _read(self, f: Feature) -> Value:
        where = dict(f.where)
        if f.table == "patients":
            if f.column == "date_of_birth":
                return age_on(self._patient.date_of_birth, self.as_of)
            return getattr(self._patient, f.column)  # biological_sex, as stored ("Male" / "Female")

        if f.table == "vitals_records":
            return self._vitals(f.column)

        if f.table == "lifestyle_records":
            if f.column == "pack_years" and self._lifestyle("smoking_status") == "never":
                return 0
            if f.column == "substance_use":
                return _ALCOHOL.get(self._lifestyle("substance_use"))
            return self._lifestyle(f.column)

        if f.table == "patient_health_history":
            if f.column == "gestational_diabetes" and self._patient.biological_sex != "Female":
                return 0
            return None if self._history is None else getattr(self._history, f.column)

        if f.table == "questionnaire_responses":
            return self.db.scalar(select(QuestionnaireResponse.total_score)
                                  .where(QuestionnaireResponse.patient_id == self.patient_id,
                                         QuestionnaireResponse.instrument == where["instrument"])
                                  .order_by(QuestionnaireResponse.completed_at.desc(),
                                            QuestionnaireResponse.response_id.desc()).limit(1))

        if f.table == "patient_fhrs_stratification":
            return self.db.scalar(select(PatientFhrsStratification.calculated_fhrs)
                                  .where(PatientFhrsStratification.patient_id == self.patient_id,
                                         PatientFhrsStratification.disease_domain == where["disease_domain"])
                                  .order_by(PatientFhrsStratification.evaluated_at.desc(),
                                            PatientFhrsStratification.fhrs_id.desc()).limit(1))

        if f.table == "lab_results":
            return self.db.scalar(select(LabResult.value)
                                  .where(LabResult.patient_id == self.patient_id,
                                         LabResult.test_code == where["test_code"], LabResult.is_confirmed)
                                  # a lab with no sample date counts from the day it was saved
                                  .order_by(func.coalesce(LabResult.collected_on,
                                                          cast(LabResult.created_at, Date)).desc(),
                                            LabResult.created_at.desc(), LabResult.lab_result_id.desc())
                                  .limit(1))

        raise ValueError(f"No reader for {f.table}.{f.column}")  # load_features() prevents this

    def value(self, f: Feature) -> Value:
        if f.key not in self._cache:
            self._cache[f.key] = _number(self._read(f))
        return self._cache[f.key]


Capped = dict[str, dict[str, Value]]  # {feature name: {"entered": value, "used": value}}


def clip(value: Value, bounds: tuple | None) -> Value:
    """value limited to its features.yaml range; None on a side means no limit there."""
    if bounds is None or value is None or isinstance(value, (str, bool)):
        return value
    low, high = bounds
    used = value
    if low is not None and used < low:
        used = low
    if high is not None and used > high:
        used = high
    if isinstance(value, int) and float(used).is_integer():
        return int(used)
    return float(used)


@dataclass(frozen=True)
class Inputs:
    values: dict[str, dict[str, Value]]  # {model: {feature name: value}}, clipped
    capped: dict[str, Capped]            # {model: {feature name: {"entered": ..., "used": ...}}}


def build_inputs(db: Session, patient_id: int, as_of: date | None = None, strict: bool = True,
                 models: tuple[str, ...] = MODELS) -> Inputs:
    """Inputs for every model, clipped to the features.yaml ranges, with what was capped. With
    strict=True, a missing required input raises MissingRequired (the readiness check should
    have stopped the patient before this)."""
    unknown = set(models) - set(MODELS)
    if unknown:
        raise ValueError(f"Unknown model: {sorted(unknown)}")
    reader = _Reader(db, patient_id, as_of or date.today())
    features = load_features()
    values: dict[str, dict[str, Value]] = {}
    capped: dict[str, Capped] = {}
    for model in models:
        row: dict[str, Value] = {}
        caps: Capped = {}
        for f in (f for f in features if f.model == model):
            entered = reader.value(f)
            used = clip(entered, f.range)
            if used != entered:
                caps[f.name] = {"entered": entered, "used": used}
            row[f.name] = used
        missing = [f.name for f in features if f.model == model and f.required and row[f.name] is None]
        if strict and missing:
            raise MissingRequired(model, missing)
        values[model], capped[model] = row, caps
    return Inputs(values=values, capped=capped)


def build_all(db: Session, patient_id: int, as_of: date | None = None, strict: bool = True,
              models: tuple[str, ...] = MODELS) -> dict[str, dict[str, Value]]:
    """{model: {feature name: value}}, clipped. Use build_inputs() to also get what was capped."""
    return build_inputs(db, patient_id, as_of, strict, models).values


def build(db: Session, patient_id: int, model: str, as_of: date | None = None,
          strict: bool = True) -> dict[str, Value]:
    """{feature name: value} for one model, in the order of ml/features.yaml, clipped."""
    return build_all(db, patient_id, as_of, strict, models=(model,))[model]
