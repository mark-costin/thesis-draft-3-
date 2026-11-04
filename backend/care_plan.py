"""Care plans: one physician-reviewed template per disease and risk tier, plus the add-ons whose
trigger is true for the patient (careplan_oct27.docx, 19_care_plan_templates.sql).

The advice itself is never written or changed here: the text comes only from
care_plan_templates, which the app can read but not edit. This module only decides WHICH
template and WHICH add-ons apply.

Drafts (not yet reviewed, is_active = false) are never shown to patients. include_drafts=True
exists for tests and for previewing the drafts before the physician signs them.
"""

from collections.abc import Callable

from sqlalchemy import Date, cast, func, select
from sqlalchemy.orm import Session

from backend.models import (
    CarePlanTemplate,
    LabResult,
    LifestyleRecord,
    PatientFhrsStratification,
    PatientHealthHistory,
    QuestionnaireResponse,
    VitalsRecord,
)

DISEASES = ("CVD", "T2D", "CRD", "NEURO")
TIERS = ("Low", "Moderate", "High")

# FHRS bands (21_famhistory.sql): Baseline = 0, Mild = 1, Moderate = 2, Pronounced = 3, Critical = 4
_BAND_3_OR_4 = {"Pronounced", "Critical"}
# Ranges written in the NEURO add-on: "TSH outside 0.34 to 4.25 mIU/L, or vitamin B12 below 200 pg/mL"
TSH_RANGE = (0.34, 4.25)
B12_LOW = 200


class TemplateMissing(Exception):
    pass


class UnknownTrigger(Exception):
    pass


def _band(facts: dict, disease: str) -> bool:
    return (facts.get("fhrs_band") or {}).get(disease) in _BAND_3_OR_4


def _above(facts: dict, name: str, limit: float) -> bool:
    value = facts.get(name)
    return value is not None and value > limit


def _at_least(facts: dict, name: str, limit: float) -> bool:
    value = facts.get(name)
    return value is not None and value >= limit


def _yes(facts: dict, name: str) -> bool:
    return bool(facts.get(name))


def _sleep_out_of_range(facts: dict) -> bool:
    hours = facts.get("sleep_hours_day")
    return hours is not None and (hours < 7 or hours > 9)


def _tsh_or_b12(facts: dict) -> bool:
    tsh, b12 = facts.get("tsh"), facts.get("vitamin_b12")
    return ((tsh is not None and not TSH_RANGE[0] <= tsh <= TSH_RANGE[1])
            or (b12 is not None and b12 < B12_LOW))


# trigger_key (in plan_items.addons) -> rule. Missing data is always False: an add-on is shown
# only when the patient's saved data says it applies.
TRIGGERS: dict[str, Callable[[dict], bool]] = {
    "current_smoker": lambda f: f.get("smoking_status") == "current",
    "current_alcohol_use": lambda f: f.get("substance_use") == "current",
    "sodium_above_2300mg": lambda f: _above(f, "sodium_mg_day", 2300),
    "sedentary_8h_or_more": lambda f: _at_least(f, "sedentary_hours_day", 8),
    "bmi_25_or_more": lambda f: _at_least(f, "bmi", 25),
    "history_gestational_diabetes": lambda f: _yes(f, "gestational_diabetes"),
    "acanthosis_nigricans": lambda f: _yes(f, "acanthosis_nigricans"),
    "occupational_dust_or_fumes": lambda f: _yes(f, "occupational_dust"),
    "allergic_rhinitis_or_childhood_asthma_or_fhrs_crd_band_3_or_4":
        lambda f: _yes(f, "allergic_rhinitis") or _yes(f, "childhood_asthma") or _band(f, "CRD"),
    "fhrs_cvd_band_3_or_4": lambda f: _band(f, "CVD"),
    "fhrs_t2d_band_3_or_4": lambda f: _band(f, "T2D"),
    "sleep_below_7_or_above_9h": _sleep_out_of_range,
    "psqi_above_5": lambda f: _above(f, "psqi", 5),
    "tsh_or_b12_out_of_range": _tsh_or_b12,
}


def trigger_is_true(key: str, facts: dict) -> bool:
    try:
        rule = TRIGGERS[key]
    except KeyError:
        raise UnknownTrigger(key) from None
    return bool(rule(facts))


def select_addons(plan_items: dict, facts: dict) -> list[dict]:
    """The add-ons whose trigger is true, in the template's order."""
    return [addon for addon in plan_items.get("addons", []) if trigger_is_true(addon["trigger_key"], facts)]


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------
def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))  # '1.10' after '1.9'


def get_template(db: Session, disease: str, tier: str, include_drafts: bool = False) -> CarePlanTemplate:
    """The active template for a disease and tier. With include_drafts, the newest version is used
    when none is active yet."""
    if disease not in DISEASES:
        raise ValueError(f"Unknown disease: {disease!r}")
    if tier not in TIERS:
        raise ValueError(f"Unknown risk tier: {tier!r}")
    query = select(CarePlanTemplate).where(CarePlanTemplate.disease_domain == disease,
                                           CarePlanTemplate.risk_level == tier)
    if not include_drafts:
        query = query.where(CarePlanTemplate.is_active)
    rows = list(db.scalars(query))
    if not rows:
        raise TemplateMissing(f"No {'' if include_drafts else 'approved, active '}care plan template "
                              f"for {disease} {tier}")
    return max(rows, key=lambda row: (row.is_active, _version_key(row.version)))


def check_templates(db: Session, include_drafts: bool = False) -> list[str]:
    """Problems that would stop care plans from working; [] when every disease and tier has a
    template and every add-on trigger has a rule. Meant to run at startup once the risk models
    are switched on."""
    problems = []
    for disease in DISEASES:
        for tier in TIERS:
            try:
                template = get_template(db, disease, tier, include_drafts)
            except TemplateMissing as exc:
                problems.append(str(exc))
                continue
            for addon in template.plan_items.get("addons", []):
                if addon.get("trigger_key") not in TRIGGERS:
                    problems.append(f"{disease} {tier} v{template.version}: add-on {addon.get('code')} "
                                    f"has unknown trigger {addon.get('trigger_key')!r}")
    return problems


# ---------------------------------------------------------------------------
# Facts from the patient's saved data. Every query filters on the patient_id from the verified
# JWT; RLS is the second layer. Same "latest value" rules as the feature builder.
# ---------------------------------------------------------------------------
def _number(value):
    return None if value is None else float(value)


def patient_facts(db: Session, patient_id: int) -> dict:
    """The saved values the triggers need: the latest non-empty value of each one."""
    def latest(model, column, *order):
        attribute = getattr(model, column)
        return db.scalar(select(attribute).where(model.patient_id == patient_id, attribute.is_not(None))
                         .order_by(*order).limit(1))

    def lifestyle(column):
        return latest(LifestyleRecord, column, LifestyleRecord.recorded_at.desc(), LifestyleRecord.lifestyle_id.desc())

    def lab(code):
        return _number(db.scalar(select(LabResult.value)
                                 .where(LabResult.patient_id == patient_id, LabResult.test_code == code,
                                        LabResult.is_confirmed)
                                 # no sample date: counts from the day it was saved (as feature_builder)
                                 .order_by(func.coalesce(LabResult.collected_on,
                                                         cast(LabResult.created_at, Date)).desc(),
                                           LabResult.created_at.desc(), LabResult.lab_result_id.desc()).limit(1)))

    history = db.scalars(select(PatientHealthHistory)
                         .where(PatientHealthHistory.patient_id == patient_id)).one_or_none()
    bands = db.execute(select(PatientFhrsStratification.disease_domain, PatientFhrsStratification.risk_band)
                       .where(PatientFhrsStratification.patient_id == patient_id)
                       .distinct(PatientFhrsStratification.disease_domain)
                       .order_by(PatientFhrsStratification.disease_domain,
                                 PatientFhrsStratification.evaluated_at.desc(),
                                 PatientFhrsStratification.fhrs_id.desc())).all()
    psqi = db.scalar(select(QuestionnaireResponse.total_score)
                     .where(QuestionnaireResponse.patient_id == patient_id, QuestionnaireResponse.instrument == "PSQI")
                     .order_by(QuestionnaireResponse.completed_at.desc(),
                               QuestionnaireResponse.response_id.desc()).limit(1))
    facts = {
        "smoking_status": lifestyle("smoking_status"),
        "substance_use": lifestyle("substance_use"),
        "sodium_mg_day": lifestyle("sodium_mg_day"),
        "sedentary_hours_day": _number(lifestyle("sedentary_hours_day")),
        "sleep_hours_day": _number(lifestyle("sleep_hours_day")),
        "occupational_dust": lifestyle("occupational_dust"),
        "bmi": _number(latest(VitalsRecord, "bmi", VitalsRecord.measured_at.desc(), VitalsRecord.vitals_id.desc())),
        "fhrs_band": dict(bands),
        "psqi": psqi,
        "tsh": lab("TSH"),
        "vitamin_b12": lab("VITAMIN_B12"),
    }
    for column in ("gestational_diabetes", "acanthosis_nigricans", "allergic_rhinitis", "childhood_asthma"):
        facts[column] = None if history is None else getattr(history, column)
    return facts


def _plan(template: CarePlanTemplate, disease: str, tier: str, facts: dict) -> dict:
    return {
        "disease_domain": disease,
        "risk_level": tier,
        "template_id": template.template_id,
        "version": template.version,
        "is_draft": not template.is_active,
        "reviewed_by": template.reviewed_by,
        "reviewed_on": template.reviewed_on,
        "guideline_source": template.guideline_source,
        "disclaimer": template.disclaimer,
        "base": template.plan_items["base"],
        "addons": select_addons(template.plan_items, facts),
    }


def build_care_plans(db: Session, patient_id: int, tiers: dict[str, str],
                     include_drafts: bool = False) -> dict[str, dict]:
    """{disease: care plan} for the given risk tiers, e.g. {"CVD": "High", "T2D": "Low"}."""
    facts = patient_facts(db, patient_id)
    return {disease: _plan(get_template(db, disease, tier, include_drafts), disease, tier, facts)
            for disease, tier in tiers.items()}


def build_recorded_care_plans(db: Session, patient_id: int, tiers: dict[str, str],
                              recorded: dict[str, dict]) -> dict[str, dict]:
    """The care plans with the EXACT templates recorded when the assessment was run
    (templates_used: {disease: {"template_id", "version"}}), not today's active ones, so a saved
    report keeps the advice that applied then. Reviewed templates cannot be edited
    (protect_reviewed_template), so the id and version always mean the same text. Raises
    TemplateMissing when a disease has no recorded template or it no longer matches."""
    facts = patient_facts(db, patient_id)
    plans = {}
    for disease, tier in tiers.items():
        used = recorded.get(disease)
        template = db.get(CarePlanTemplate, used["template_id"]) if used else None
        if (template is None or template.version != used["version"]
                or template.disease_domain != disease or template.risk_level != tier):
            raise TemplateMissing(f"The {disease} care plan template used for this assessment is not available")
        plans[disease] = _plan(template, disease, tier, facts)
    return plans


def templates_used(plans: dict[str, dict]) -> dict[str, dict]:
    """What a saved report records, so an old report still shows the advice actually given."""
    return {disease: {"template_id": plan["template_id"], "version": plan["version"]}
            for disease, plan in plans.items()}
