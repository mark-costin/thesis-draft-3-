"""Lab tests as the screens show them. No Reflex import, so the tests can check it against the API."""

# Lab tests: the one unit the API stores (backend/labs.py STANDARD_UNIT; other units are converted there)
LAB_UNIT = {"TOTAL_CHOLESTEROL": "mg/dL", "HDL": "mg/dL", "LDL": "mg/dL", "TRIGLYCERIDES": "mg/dL", "FPG": "mg/dL", "OGTT_2H": "mg/dL",
            "HBA1C": "%", "CREATININE": "mg/dL", "EGFR": "mL/min/1.73m2", "URIC_ACID": "mg/dL", "UACR": "mg/g", "HS_CRP": "mg/L",
            "EOSINOPHILS": "cells/uL", "IGE": "IU/mL", "TSH": "mIU/L", "VITAMIN_B12": "pg/mL", "FOLATE": "ng/mL"}
LAB_LABEL = {"TOTAL_CHOLESTEROL": "Total cholesterol", "HDL": "HDL cholesterol", "LDL": "LDL cholesterol", "TRIGLYCERIDES": "Triglycerides",
             "FPG": "Fasting blood sugar (FBS)", "OGTT_2H": "OGTT 2-hour glucose", "HBA1C": "HbA1c", "CREATININE": "Creatinine",
             "EGFR": "eGFR", "URIC_ACID": "Uric acid", "UACR": "Urine albumin-creatinine ratio", "HS_CRP": "hs-CRP",
             "EOSINOPHILS": "Eosinophils", "IGE": "Total IgE", "TSH": "TSH", "VITAMIN_B12": "Vitamin B12", "FOLATE": "Folate"}
DOC_STATUS = {"pending": "Waiting for OCR", "processing": "Reading the document", "ready_for_review": "Ready for review",
              "verified": "Confirmed", "rejected": "Rejected", "failed": "Could not be read"}


# ---------------------------------------------------------------------------
# Input names in the Results chart, from ml/features.yaml (the file the models and the API use).
# Short form for the chart: the abbreviation in brackets when the label has one
# ("High-Density Lipoprotein (HDL)" -> "HDL"), otherwise the whole label ("Heart Rate (Resting)").
# ---------------------------------------------------------------------------
def _load_feature_labels() -> dict[str, str]:
    import re
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "ml" / "features.yaml"
    try:
        import yaml
        spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:  # no file or no yaml: the chart falls back to the input name
        return {}
    labels = {}
    for model in (spec.get("models") or {}).values():
        for f in model.get("features") or []:
            label = str(f.get("label") or "")
            short = re.search(r"\(([^()]+)\)\s*$", label)
            abbreviation = short.group(1) if short else ""
            is_abbreviation = " " not in abbreviation and sum(c.isupper() for c in abbreviation) >= 2
            labels.setdefault(f["name"], abbreviation if is_abbreviation else label)
    return labels


FEATURE_LABEL = _load_feature_labels()


def feature_label(name: str) -> str:
    """Chart name for a model input: "hdl" -> "HDL", "sodium_mg_day" -> "Daily Sodium Intake"."""
    return FEATURE_LABEL.get(name) or name.replace("_", " ").capitalize()
