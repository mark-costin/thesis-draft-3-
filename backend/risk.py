"""Risk assessment (Sprint 5): runs the four models on the patient's saved data and saves the
assessment, the four results and every SHAP value in ONE transaction.

Flow: feature_builder (inputs) -> model_registry (probability, tier, SHAP) -> saved rows
(risk_assessments, risk_results, shap_explanations). The results view adds the plain-language
sentences (explanations) and the care plan for each tier (care_plan).

Same inputs always give the same saved result: the models are fixed files (checked by SHA-256),
the inputs are saved in input_snapshot, and nothing random is used.

input_snapshot: {"CVD": {inputs}, "T2D": ..., "CRD": ..., "NEURO": ...,
                 "capped": {disease: {feature: {"entered": x, "used": y}}},   # clipped to features.yaml
                 "templates_used": {disease: {"template_id": n, "version": "v"}}}  # care plans at run time
Every query filters on the patient_id from the verified JWT; RLS is the second layer.
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
# Safety message for T2D, shown whatever the tier. The model never saw diabetes-range glucose in
# training (the synthetic generator caps FPG and HbA1c before diagnosis), so it can give Low for a
# value that already meets a diagnostic cut-off. Cut-offs: ADA Standards of Care (FPG 126 mg/dL,
# 2-hour OGTT 200 mg/dL, HbA1c 6.5%). The physician decides the final values.
DIABETES_RANGE = {  # model input -> (cut-off, words on screen, unit)
    "fpg": (126, "fasting blood sugar", "mg/dL"),
    "ogtt_2h": (200, "2-hour glucose test", "mg/dL"),
    "hba1c": (6.5, "HbA1c", "%"),
}
SAFETY_MESSAGE = ("{values} in the diabetes range. Please see a doctor for a check-up, whatever your risk "
                  "level here. This is a screening estimate, not a diagnosis.")


def safety_message(disease: str, inputs: dict, capped: dict) -> str | None:
    """'Your fasting blood sugar of 140 mg/dL is in the diabetes range ...', or None.
    Uses the value the patient entered, also when the model got a capped value."""
    if disease != "T2D":
        return None
    found = []
    for name, (cut, words, unit) in DIABETES_RANGE.items():
        value = capped.get(name, {}).get("entered", inputs.get(name))
        if value is not None and float(value) >= cut:
            number = f"{float(value):g}"
            found.append(f"{words} of {number}{unit if unit == '%' else ' ' + unit}")
    if not found:
        return None
    values = "Your " + " and your ".join(found) + (" is" if len(found) == 1 else " are")
    return SAFETY_MESSAGE.format(values=values)


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
    built = feature_builder.build_inputs(db, patient_id, as_of=as_of)  # strict: required inputs must be there
    inputs = built.values

    try:
        # 1. Score every model first: assessments are immutable (no UPDATE), so the snapshot must
        #    be complete before the row is inserted
        scored = {}
        for disease in MODELS:
            model = models[disease]
            probability = round(model.probability(inputs[disease]), 4)  # numeric(5,4), as saved
            shap_values, base = model.contributions(inputs[disease])
            scored[disease] = (probability, model.tier(probability), shap_values, base)
        # The care plan templates for these tiers, as they are now: an old assessment can then be
        # shown with the advice that applied when it was run
        plans = _care_plans(db, patient_id, {disease: scored[disease][1] for disease in MODELS})
        used = care_plan.templates_used({disease: plan for disease, plan in plans.items() if plan is not None})

        # 2. Save it all in one transaction
        assessment = RiskAssessment(patient_id=patient_id, input_snapshot={
            **inputs, "capped": built.capped, "templates_used": used})
        db.add(assessment)
        db.flush()
        for disease in MODELS:
            model = models[disease]
            probability, tier, shap_values, base = scored[disease]
            result = RiskResult(assessment_id=assessment.assessment_id, patient_id=patient_id,
                                model_version_id=model.model_version_id, disease_domain=disease,
                                risk_probability=_dec(probability, 4), risk_level=tier,
                                shap_base_value=_dec(base, 5))
            db.add(result)
            db.flush()
            encoded = dict(zip(model.features, model.to_row(inputs[disease])[0]))  # sex: 1 / 0 (features.yaml)
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


def _care_plans(db: Session, patient_id: int, tiers: dict[str, str]) -> dict[str, dict | None]:
    plans: dict[str, dict | None] = {}
    for disease, tier in tiers.items():
        try:
            plans.update(care_plan.build_care_plans(db, patient_id, {disease: tier},
                                                    include_drafts=settings.show_draft_care_plans))
        except care_plan.TemplateMissing:
            plans[disease] = None  # no approved template yet: the screen says so
    return plans


def view(db: Session, patient_id: int, assessment_id: int) -> dict:
    """The saved assessment as the results screen shows it: own data only."""
    assessment = db.scalars(select(RiskAssessment)
                            .where(RiskAssessment.assessment_id == assessment_id,
                                   RiskAssessment.patient_id == patient_id)).one_or_none()
    if assessment is None:
        raise NotFound
    results = {row.disease_domain: row for row in db.scalars(
        select(RiskResult).where(RiskResult.assessment_id == assessment_id,
                                 RiskResult.patient_id == patient_id))}
    versions = dict(db.execute(select(ModelVersion.model_version_id, ModelVersion.version)
                               .where(ModelVersion.model_version_id.in_(
                                   [r.model_version_id for r in results.values()]))).all())
    shap_rows = db.scalars(select(ShapExplanation)
                           .where(ShapExplanation.result_id.in_([r.result_id for r in results.values()]),
                                  ShapExplanation.patient_id == patient_id,
                                  ShapExplanation.rank <= TOP_FACTORS)
                           .order_by(ShapExplanation.result_id, ShapExplanation.rank)).all()
    plans = _care_plans(db, patient_id, {d: results[d].risk_level for d in MODELS if d in results})
    capped = assessment.input_snapshot.get("capped", {})

    out = []
    for disease in MODELS:
        if disease not in results:
            continue
        result = results[disease]
        inputs = assessment.input_snapshot.get(disease, {})
        caps = capped.get(disease, {})
        factors = [{"rank": row.rank, "feature": row.feature_name, "shap_value": float(row.shap_value),
                    "missing": inputs.get(row.feature_name) is None,  # not entered: the screen shows no bar
                    "sentence": explanations.sentence(row.feature_name, inputs.get(row.feature_name),
                                                      float(row.shap_value), disease,
                                                      entered=caps.get(row.feature_name, {}).get("entered"))}
                   for row in shap_rows if row.result_id == result.result_id]
        out.append({
            "result_id": result.result_id,  # lets a goal from the care plan link to this result
            "disease": disease,
            "disease_name": explanations.DISEASE_NAMES[disease],
            "probability": float(result.risk_probability),
            "risk_level": result.risk_level,
            "model_version": versions.get(result.model_version_id),
            "consult_recommended": result.risk_level == "High",
            "consult_message": CONSULT_MESSAGE if result.risk_level == "High" else None,
            "safety_message": safety_message(disease, inputs, caps),
            "top_factors": factors,
            "care_plan": plans.get(disease),
        })
    return {"assessment_id": assessment.assessment_id, "created_at": assessment.created_at,
            "disclaimer": DISCLAIMER, "results": out}


def list_assessments(db: Session, patient_id: int, limit: int) -> list[dict]:
    rows = db.scalars(select(RiskAssessment).where(RiskAssessment.patient_id == patient_id)
                      .order_by(RiskAssessment.created_at.desc(),
                                                      RiskAssessment.assessment_id.desc()).limit(limit)).all()
    levels: dict[int, dict[str, str]] = {}
    if rows:
        for result in db.scalars(select(RiskResult).where(
                RiskResult.patient_id == patient_id,
                RiskResult.assessment_id.in_([row.assessment_id for row in rows]))):
            levels.setdefault(result.assessment_id, {})[result.disease_domain] = result.risk_level
    return [{"assessment_id": row.assessment_id, "created_at": row.created_at,
             "risk_levels": levels.get(row.assessment_id, {})} for row in rows]
