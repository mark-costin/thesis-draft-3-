"""Risk assessment (Sprint 5): runs the four models on the patient's saved data and saves the
assessment, the four results and every SHAP value in ONE transaction.

Flow: feature_builder (inputs) -> model_registry (probability, tier, SHAP) -> saved rows
(risk_assessments, risk_results, shap_explanations). The results view adds the plain-language
sentences (explanations) and the care plan for each tier (care_plan).

Same inputs always give the same saved result: the models are fixed files (checked by SHA-256),
the inputs are saved in input_snapshot, and nothing random is used.
"""

import math
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import audit, care_plan, explanations, feature_builder, model_registry
from backend.config import settings
from backend.models import ModelVersion, RiskAssessment, RiskResult, ShapExplanation
from backend.readiness import MODELS

TOP_FACTORS = 3
CONSULT_MESSAGE = ("Your result is in the high range. Please consult a doctor for a check-up. "
                   "This result is a screening estimate, not a diagnosis.")
DISCLAIMER = ("This is an educational screening estimate based on the information you entered. "
              "It is not a medical diagnosis. Please consult a healthcare provider for medical advice.")


class ModelsOff(Exception):
    pass


class NotFound(Exception):
    pass


def _dec(value: float, places: int) -> Decimal:
    return Decimal(str(round(value, places)))


def run_assessment(db: Session, user_id: int, patient_id: int, as_of: date | None = None) -> int:
    """Returns the new assessment_id. Raises ModelsOff when the models are not loaded and
    feature_builder.MissingRequired when the patient still has to enter something."""
    models = model_registry.ACTIVE
    if set(models) != set(MODELS):
        raise ModelsOff
    inputs = feature_builder.build_all(db, as_of=as_of)  # strict: required inputs must be there

    try:
        assessment = RiskAssessment(patient_id=patient_id, input_snapshot=inputs)
        db.add(assessment)
        db.flush()
        for disease in MODELS:
            model = models[disease]
            probability = round(model.probability(inputs[disease]), 4)  # numeric(5,4), as saved
            shap_values, base = model.contributions(inputs[disease])
            result = RiskResult(assessment_id=assessment.assessment_id, patient_id=patient_id,
                                model_version_id=model.model_version_id, disease_domain=disease,
                                risk_probability=_dec(probability, 4), risk_level=model.tier(probability),
                                shap_base_value=_dec(base, 5))
            db.add(result)
            db.flush()
            encoded = dict(zip(model.features, model.to_row(inputs[disease])[0]))
            ranked = sorted(model.features, key=lambda name: -abs(shap_values[name]))  # ties: model order
            for rank, name in enumerate(ranked, start=1):
                value = encoded[name]
                db.add(ShapExplanation(result_id=result.result_id, patient_id=patient_id, feature_name=name,
                                       feature_value=None if math.isnan(value) else Decimal(str(value)),
                                       shap_value=_dec(shap_values[name], 5), rank=rank))
        # IDs and model versions only: no probabilities, levels or inputs in the audit ledger
        audit.log_event(db, "RISK_ASSESSMENT_RUN", user_id=user_id, patient_id=patient_id,
                        record_id=assessment.assessment_id,
                        model_versions={disease: models[disease].version for disease in MODELS})
        db.commit()
    except Exception:
        db.rollback()  # all or nothing: no half-saved assessment
        raise
    return assessment.assessment_id


def _care_plans(db: Session, tiers: dict[str, str]) -> dict[str, dict | None]:
    plans: dict[str, dict | None] = {}
    for disease, tier in tiers.items():
        try:
            plans.update(care_plan.build_care_plans(db, {disease: tier},
                                                    include_drafts=settings.show_draft_care_plans))
        except care_plan.TemplateMissing:
            plans[disease] = None  # no approved template yet: the screen says so
    return plans


def view(db: Session, assessment_id: int) -> dict:
    """The saved assessment as the results screen shows it. Read under RLS: own data only."""
    assessment = db.scalars(select(RiskAssessment)
                            .where(RiskAssessment.assessment_id == assessment_id)).one_or_none()
    if assessment is None:
        raise NotFound
    results = {row.disease_domain: row for row in db.scalars(
        select(RiskResult).where(RiskResult.assessment_id == assessment_id))}
    versions = dict(db.execute(select(ModelVersion.model_version_id, ModelVersion.version)
                               .where(ModelVersion.model_version_id.in_(
                                   [r.model_version_id for r in results.values()]))).all())
    shap_rows = db.scalars(select(ShapExplanation)
                           .where(ShapExplanation.result_id.in_([r.result_id for r in results.values()]),
                                  ShapExplanation.rank <= TOP_FACTORS)
                           .order_by(ShapExplanation.result_id, ShapExplanation.rank)).all()
    plans = _care_plans(db, {d: results[d].risk_level for d in MODELS if d in results})

    out = []
    for disease in MODELS:
        if disease not in results:
            continue
        result = results[disease]
        inputs = assessment.input_snapshot.get(disease, {})
        factors = [{"rank": row.rank, "feature": row.feature_name, "shap_value": float(row.shap_value),
                    "sentence": explanations.sentence(row.feature_name, inputs.get(row.feature_name),
                                                      float(row.shap_value), disease)}
                   for row in shap_rows if row.result_id == result.result_id]
        out.append({
            "disease": disease,
            "disease_name": explanations.DISEASE_NAMES[disease],
            "probability": float(result.risk_probability),
            "risk_level": result.risk_level,
            "model_version": versions.get(result.model_version_id),
            "consult_recommended": result.risk_level == "High",
            "consult_message": CONSULT_MESSAGE if result.risk_level == "High" else None,
            "top_factors": factors,
            "care_plan": plans.get(disease),
        })
    return {"assessment_id": assessment.assessment_id, "created_at": assessment.created_at,
            "disclaimer": DISCLAIMER, "results": out}


def list_assessments(db: Session, limit: int) -> list[dict]:
    rows = db.scalars(select(RiskAssessment).order_by(RiskAssessment.created_at.desc(),
                                                      RiskAssessment.assessment_id.desc()).limit(limit)).all()
    levels: dict[int, dict[str, str]] = {}
    if rows:
        for result in db.scalars(select(RiskResult).where(
                RiskResult.assessment_id.in_([row.assessment_id for row in rows]))):
            levels.setdefault(result.assessment_id, {})[result.disease_domain] = result.risk_level
    return [{"assessment_id": row.assessment_id, "created_at": row.created_at,
             "risk_levels": levels.get(row.assessment_id, {})} for row in rows]
