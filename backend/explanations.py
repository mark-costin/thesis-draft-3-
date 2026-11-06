"""Plain-language SHAP sentences (Sprint 5): turns each model input and its SHAP contribution
into one short sentence a patient can read, e.g.
    "Your systolic (upper) blood pressure of 145 mmHg raised your risk of heart disease and stroke."

Every feature in ml/features.yaml must have an entry here (checked by the tests and by
check_coverage()), so a new model input cannot reach the results screen without a sentence.
The sentences only describe what the model used; they never give medical advice.
"""

from dataclasses import dataclass

DISEASE_NAMES = {
    "CVD": "heart disease and stroke",
    "T2D": "type 2 diabetes",
    "CRD": "chronic lung disease",
    "NEURO": "depression and anxiety",
}

# Below this absolute SHAP value a factor is described as having almost no effect
NO_EFFECT = 0.001


@dataclass(frozen=True)
class Measure:
    """A number: '<subject> of <value> <unit>', or '<subject> (score <value>)'."""
    subject: str          # starts lower-case, e.g. "your systolic blood pressure"
    unit: str = ""        # shown after the value; "" = no unit
    score: bool = False   # shown as "(score 12)"; subject then has no brackets of its own


@dataclass(frozen=True)
class YesNo:
    """A yes/no input (1 / 0): a different phrase for each answer."""
    yes: str              # e.g. "being exposed to dust or fumes at work"
    no: str
    subject: str          # used when it was not entered, e.g. "dust or fume exposure at work"


@dataclass(frozen=True)
class Sex:
    subject: str = "your biological sex"


@dataclass(frozen=True)
class Category:
    """A text input with a few levels, in the order of its features.yaml encoding (0, 1, 2, ...):
    one phrase per level, so the encoded number reads the same as the text."""
    levels: tuple[tuple[str, str], ...]   # (stored text, phrase), e.g. ("current", "smoking now")
    subject: str


FEATURES: dict[str, Measure | YesNo | Sex | Category] = {
    # Profile
    "age": Measure("your age", "years"),
    "sex": Sex(),
    # Vitals
    "sbp": Measure("your systolic (upper) blood pressure", "mmHg"),
    "dbp": Measure("your diastolic (lower) blood pressure", "mmHg"),
    "resting_hr": Measure("your resting heart rate", "beats per minute"),
    "bmi": Measure("your body mass index (BMI)", "kg/m²"),
    "waist_cm": Measure("your waist size", "cm"),
    "spo2": Measure("your oxygen level (SpO2)", "%"),
    # Lifestyle
    "pack_years": Measure("your smoking history", "pack-years"),
    "sodium_mg_day": Measure("your daily salt (sodium) intake", "mg a day"),
    "physical_activity_min_week": Measure("your physical activity", "minutes a week"),
    "carbs_g_day": Measure("your daily carbohydrate intake", "g a day"),
    "sedentary_hours_day": Measure("the time you spend sitting", "hours a day"),
    "sleep_hours_day": Measure("your sleep", "hours a day"),
    "smoking_status": Category((("never", "never having smoked"), ("former", "having smoked in the past"),
                                ("current", "smoking now")), "your smoking status"),
    "occupational_dust": YesNo("being exposed to dust or fumes at work",
                               "not being exposed to dust or fumes at work",
                               "dust or fume exposure at work"),
    "alcohol_use": YesNo("drinking alcohol now or in the past", "not drinking alcohol", "alcohol use"),
    # Health history
    "gestational_diabetes": YesNo("having had diabetes during pregnancy",
                                  "not having had diabetes during pregnancy",
                                  "diabetes during pregnancy"),
    "acanthosis_nigricans": YesNo("having dark, velvety skin patches (acanthosis nigricans)",
                                  "not having dark, velvety skin patches (acanthosis nigricans)",
                                  "dark, velvety skin patches (acanthosis nigricans)"),
    "allergic_rhinitis": YesNo("having allergic rhinitis", "not having allergic rhinitis", "allergic rhinitis"),
    "childhood_asthma": YesNo("having had asthma as a child", "not having had asthma as a child",
                              "childhood asthma"),
    "tbi_history": YesNo("having had a head injury (traumatic brain injury)",
                         "not having had a head injury (traumatic brain injury)",
                         "head injury history"),
    # Family history (FHRS)
    "fhrs_cvd": Measure("your family history of heart disease", score=True),
    "fhrs_t2d": Measure("your family history of diabetes", score=True),
    "fhrs_crd": Measure("your family history of lung disease (asthma or COPD)", score=True),
    "fhrs_neuro": Measure("your family history of depression and anxiety", score=True),
    # Questionnaires
    "phq9": Measure("your PHQ-9 mood questionnaire", score=True),
    "gad7": Measure("your GAD-7 anxiety questionnaire", score=True),
    "psqi": Measure("your PSQI sleep quality questionnaire", score=True),
    "mmrc": Measure("your mMRC breathlessness scale", score=True),
    "act": Measure("your ACT asthma control test", score=True),
    "cat": Measure("your CAT COPD assessment test", score=True),
    # Lab results
    "total_cholesterol": Measure("your total cholesterol", "mg/dL"),
    "hdl": Measure("your HDL (good) cholesterol", "mg/dL"),
    "ldl": Measure("your LDL (bad) cholesterol", "mg/dL"),
    "triglycerides": Measure("your triglycerides", "mg/dL"),
    "hs_crp": Measure("your hs-CRP (a marker of inflammation)", "mg/L"),
    "creatinine": Measure("your creatinine (a kidney test)", "mg/dL"),
    "egfr": Measure("your eGFR (kidney function)", "mL/min/1.73m²"),
    "fpg": Measure("your fasting blood sugar", "mg/dL"),
    "ogtt_2h": Measure("your 2-hour blood sugar test", "mg/dL"),
    "hba1c": Measure("your HbA1c (average blood sugar)", "%"),
    "uacr": Measure("your urine albumin (UACR, a kidney test)", "mg/g"),
    "uric_acid": Measure("your uric acid", "mg/dL"),
    "eosinophils": Measure("your eosinophil count (a type of white blood cell)", "cells/µL"),
    "ige": Measure("your IgE level (an allergy marker)", "IU/mL"),
    "tsh": Measure("your TSH (a thyroid test)", "mIU/L"),
    "vitamin_b12": Measure("your vitamin B12", "pg/mL"),
}


def _number(value) -> str:
    value = float(value)
    if value.is_integer():
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _effect(shap_value: float, disease: str) -> str:
    name = DISEASE_NAMES[disease]
    if abs(shap_value) < NO_EFFECT:
        return f"had almost no effect on your risk of {name}"
    return f"{'raised' if shap_value > 0 else 'lowered'} your risk of {name}"


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _shown(value, entered) -> str:
    """The number the patient entered; when the model used a capped value, say so."""
    if entered is None or float(entered) == float(value):
        return _number(value)
    edge = "highest" if float(entered) > float(value) else "lowest"
    return f"{_number(entered)}{{unit}} (counted as {_number(value)}, the {edge} value the model uses)"


def sentence(feature: str, value, shap_value: float, disease: str, entered=None) -> str:
    """One sentence for one input. value is the model input (None when it was not entered).
    entered: what the patient entered, when the feature builder capped it to the model's range
    (feature_builder.build_inputs().capped); the sentence then shows the entered value and
    what the model counted it as, so a result never shows a number the patient did not enter."""
    if disease not in DISEASE_NAMES:
        raise ValueError(f"Unknown disease: {disease!r}")
    try:
        entry = FEATURES[feature]
    except KeyError:
        raise ValueError(f"No plain-language sentence for feature {feature!r}") from None

    if value is None:
        return (f"{_cap(entry.subject)} was not entered yet. "
                "Adding it can make this result more accurate.")
    if isinstance(entry, YesNo):
        return f"{_cap(entry.yes if float(value) >= 1 else entry.no)} {_effect(shap_value, disease)}."
    if feature == "pack_years" and float(value) == 0:  # never-smokers get 0 (features.yaml derive)
        return f"Not smoking {_effect(shap_value, disease)}."
    if isinstance(entry, Category):
        label = str(value).strip().lower()
        phrases = dict(entry.levels)
        if label not in phrases:  # the encoded number (0, 1, 2 ...)
            label = entry.levels[int(float(label))][0]
        return f"{_cap(phrases[label])} {_effect(shap_value, disease)}."
    if isinstance(entry, Sex):
        label = str(value).strip().lower()
        if label in {"1", "1.0"}:  # in case the models encode sex as a number
            label = "male"
        elif label in {"0", "0.0"}:
            label = "female"
        return f"Being {label} {_effect(shap_value, disease)}."
    number = _shown(value, entered)
    if entry.score:
        shown = f"{entry.subject} (score {number.replace('{unit}', '')})"
    else:
        unit = entry.unit if entry.unit in ("", "%") else f" {entry.unit}"
        shown = (f"{entry.subject} of {number.replace('{unit}', unit)}" if "{unit}" in number
                 else f"{entry.subject} of {number}{unit}")
    return f"{_cap(shown)} {_effect(shap_value, disease)}."


def ranked_factors(disease: str, shap_values: dict[str, float], inputs: dict[str, object],
                   limit: int | None = 3, capped: dict | None = None) -> list[dict]:
    """The inputs that moved this result the most, largest first (rank 1 = largest absolute SHAP),
    in the shape of shap_explanations rows plus the sentence. Ties keep the model's input order.
    capped: feature_builder.build_inputs().capped[disease], so a capped value is explained."""
    capped = capped or {}
    order = sorted(shap_values, key=lambda name: -abs(shap_values[name]))
    rows = []
    for rank, name in enumerate(order[:limit] if limit else order, start=1):
        value = inputs.get(name)
        rows.append({"rank": rank, "feature_name": name, "feature_value": value,
                     "shap_value": shap_values[name],
                     "sentence": sentence(name, value, shap_values[name], disease,
                                          entered=capped.get(name, {}).get("entered"))})
    return rows


def check_coverage(feature_names) -> list[str]:
    """Feature names with no sentence; [] when every input is covered."""
    return sorted(set(feature_names) - set(FEATURES))
