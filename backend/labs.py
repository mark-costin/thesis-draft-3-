"""Lab field mapping and unit conversion.

Turns one line of a lab report (a label, a value and a unit, as OCR reads them or as the patient
types them) into a row for lab_results: the test code, the value in the one standard unit the
table allows (lab_unit_check in 08_lab_results.sql), and the value as reported.

Conversion factors are the standard SI <-> conventional factors (molar mass based). Many
Philippine lab reports use SI units (mmol/L, umol/L), so these conversions matter.
"""

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

# Standard unit per test code: must match lab_unit_check in 08_lab_results.sql
STANDARD_UNIT = {
    "TOTAL_CHOLESTEROL": "mg/dL", "HDL": "mg/dL", "LDL": "mg/dL", "TRIGLYCERIDES": "mg/dL",
    "FPG": "mg/dL", "OGTT_2H": "mg/dL", "HBA1C": "%", "CREATININE": "mg/dL",
    "EGFR": "mL/min/1.73m2", "URIC_ACID": "mg/dL", "UACR": "mg/g", "HS_CRP": "mg/L",
    "EOSINOPHILS": "cells/uL", "IGE": "IU/mL", "TSH": "mIU/L", "VITAMIN_B12": "pg/mL", "FOLATE": "ng/mL",
}

# Values outside these ranges are kept but flagged for the patient to double-check (OCR misreads,
# wrong unit). From ml/features.yaml where given; FOLATE from typical reporting ranges.
EXPECTED_RANGE = {
    "TOTAL_CHOLESTEROL": (100, 400), "HDL": (15, 120), "LDL": (30, 300), "TRIGLYCERIDES": (40, 1000),
    "FPG": (50, 450), "OGTT_2H": (60, 600), "HBA1C": (4.0, 15.0), "CREATININE": (0.4, 10.0),
    "EGFR": (5, 140), "URIC_ACID": (1.5, 14.0), "UACR": (1, 3500), "HS_CRP": (0.1, 50.0),
    "EOSINOPHILS": (0, 2500), "IGE": (2, 3000), "TSH": (0.01, 100.0), "VITAMIN_B12": (50, 2000),
    "FOLATE": (1, 40),
}

# Labels seen on lab reports -> test code. Matched after lower-casing and removing punctuation.
LABELS = {
    "TOTAL_CHOLESTEROL": ["total cholesterol", "cholesterol", "cholesterol total", "tc", "chole"],
    "HDL": ["hdl", "hdl cholesterol", "hdl c", "hdlc", "high density lipoprotein"],
    "LDL": ["ldl", "ldl cholesterol", "ldl c", "ldlc", "low density lipoprotein", "ldl direct", "ldl calculated"],
    "TRIGLYCERIDES": ["triglycerides", "triglyceride", "tg", "trig", "trigs"],
    "FPG": ["fbs", "fasting blood sugar", "fasting blood glucose", "fasting plasma glucose", "fpg",
            "glucose fasting", "fasting glucose", "rbs fasting"],
    "OGTT_2H": ["ogtt 2 hr", "ogtt 2hr", "ogtt 2 hours", "2 hour glucose", "2hr glucose", "2 hr pp glucose",
                "75g ogtt 2 hr", "ogtt 2h"],
    "HBA1C": ["hba1c", "hb a1c", "a1c", "glycated hemoglobin", "glycosylated hemoglobin", "hemoglobin a1c"],
    "CREATININE": ["creatinine", "crea", "serum creatinine", "creat"],
    "EGFR": ["egfr", "estimated gfr", "gfr"],
    "URIC_ACID": ["uric acid", "bua", "blood uric acid", "serum uric acid", "urate"],
    "UACR": ["uacr", "albumin creatinine ratio", "urine albumin creatinine ratio", "microalbumin creatinine ratio",
             "acr"],
    "HS_CRP": ["hs crp", "hscrp", "high sensitivity crp", "high sensitivity c reactive protein", "crp hs"],
    "EOSINOPHILS": ["eosinophils absolute", "absolute eosinophil count", "aec", "eosinophil count",
                    "eosinophils abs", "eos absolute", "eosinophils"],
    "IGE": ["ige", "total ige", "serum ige", "immunoglobulin e"],
    "TSH": ["tsh", "thyroid stimulating hormone", "thyrotropin"],
    "VITAMIN_B12": ["vitamin b12", "vit b12", "b12", "cobalamin", "cyanocobalamin"],
    "FOLATE": ["folate", "folic acid", "serum folate"],
}

# Units as written on reports -> one spelling
_UNIT_ALIASES = {
    "mg/dl": "mg/dL", "mmol/l": "mmol/L", "umol/l": "umol/L", "µmol/l": "umol/L", "μmol/l": "umol/L",
    "%": "%", "mmol/mol": "mmol/mol", "ml/min/1.73m2": "mL/min/1.73m2", "ml/min/1.73 m2": "mL/min/1.73m2",
    "ml/min/1.73m²": "mL/min/1.73m2", "mg/g": "mg/g", "mg/mmol": "mg/mmol",
    "mg/l": "mg/L", "cells/ul": "cells/uL", "cells/µl": "cells/uL", "cells/μl": "cells/uL", "/ul": "cells/uL",
    "/µl": "cells/uL", "/mm3": "cells/uL", "cells/mm3": "cells/uL", "x10^9/l": "10^9/L", "x 10^9/l": "10^9/L",
    "10^9/l": "10^9/L", "iu/ml": "IU/mL", "kiu/l": "IU/mL", "ku/l": "IU/mL", "miu/l": "mIU/L",
    "uiu/ml": "mIU/L", "µiu/ml": "mIU/L", "μiu/ml": "mIU/L", "pg/ml": "pg/mL", "pmol/l": "pmol/L",
    "ng/ml": "ng/mL", "nmol/l": "nmol/L", "ug/l": "ng/mL", "µg/l": "ng/mL",
}

# (test code, unit as reported) -> function to the standard unit. Same unit = factor 1.
_CONVERT = {
    # Lipids: mmol/L x 38.67 (cholesterol), x 88.57 (triglycerides)
    ("TOTAL_CHOLESTEROL", "mmol/L"): lambda v: v * Decimal("38.67"),
    ("HDL", "mmol/L"): lambda v: v * Decimal("38.67"),
    ("LDL", "mmol/L"): lambda v: v * Decimal("38.67"),
    ("TRIGLYCERIDES", "mmol/L"): lambda v: v * Decimal("88.57"),
    # Glucose: mmol/L x 18.016
    ("FPG", "mmol/L"): lambda v: v * Decimal("18.016"),
    ("OGTT_2H", "mmol/L"): lambda v: v * Decimal("18.016"),
    # HbA1c: IFCC mmol/mol -> NGSP % (NGSP = 0.09148 x IFCC + 2.152)
    ("HBA1C", "mmol/mol"): lambda v: v * Decimal("0.09148") + Decimal("2.152"),
    # Creatinine: umol/L / 88.42; uric acid: umol/L / 59.48 and mmol/L x 16.81
    ("CREATININE", "umol/L"): lambda v: v / Decimal("88.42"),
    ("URIC_ACID", "umol/L"): lambda v: v / Decimal("59.48"),
    ("URIC_ACID", "mmol/L"): lambda v: v * Decimal("16.81"),
    # UACR: mg/mmol x 8.84 -> mg/g
    ("UACR", "mg/mmol"): lambda v: v * Decimal("8.84"),
    # hs-CRP: mg/dL x 10 -> mg/L
    ("HS_CRP", "mg/dL"): lambda v: v * Decimal("10"),
    # Eosinophils: x10^9/L x 1000 -> cells/uL
    ("EOSINOPHILS", "10^9/L"): lambda v: v * Decimal("1000"),
    # Vitamin B12: pmol/L / 0.7378 -> pg/mL; folate: nmol/L / 2.266 -> ng/mL
    ("VITAMIN_B12", "pmol/L"): lambda v: v / Decimal("0.7378"),
    ("FOLATE", "nmol/L"): lambda v: v / Decimal("2.266"),
}


class MappingError(ValueError):
    pass


@dataclass(frozen=True)
class MappedLab:
    test_code: str
    value: Decimal                       # in the standard unit, 3 decimals (numeric(10,3))
    unit: str                            # the standard unit
    value_as_reported: Decimal | None    # set only when a conversion was made
    unit_as_reported: str | None
    out_of_expected_range: bool          # keep it, but ask the patient to double-check


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9%]+", " ", text.lower()).strip()


_LABEL_INDEX = {_key(label): code for code, labels in LABELS.items() for label in labels}


def test_code_for(label: str) -> str:
    """The test code for a report label, or MappingError when the label is not one we use."""
    code = _LABEL_INDEX.get(_key(label))
    if code is None:
        raise MappingError(f"Unknown lab test: {label!r}")
    return code


def normalize_unit(unit: str) -> str:
    cleaned = unit.strip().lower().replace(" ", "") if unit else ""
    for alias, canonical in _UNIT_ALIASES.items():
        if cleaned == alias.replace(" ", ""):
            return canonical
    raise MappingError(f"Unknown unit: {unit!r}")


def convert(test_code: str, value: Decimal, unit: str) -> MappedLab:
    """Converts a value for a known test code into its standard unit."""
    if test_code not in STANDARD_UNIT:
        raise MappingError(f"Unknown test code: {test_code!r}")
    if not value.is_finite() or value <= 0:
        raise MappingError("A lab value must be a number above 0")
    if value >= Decimal("10000000"):  # value_as_reported is numeric(10,3)
        raise MappingError("The value is outside what a lab report can show")
    value = value.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    reported_unit = normalize_unit(unit)
    standard = STANDARD_UNIT[test_code]
    if reported_unit == standard:
        converted, as_reported, unit_reported = value, None, None
    elif (test_code, reported_unit) in _CONVERT:
        converted = _CONVERT[(test_code, reported_unit)](value)
        as_reported, unit_reported = value, reported_unit
    else:
        raise MappingError(f"{unit!r} cannot be used for {test_code} (expected {standard})")
    converted = converted.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    if not Decimal("0") < converted < Decimal("100000"):  # lab_value_check
        raise MappingError("The value is outside what a lab report can show")
    low, high = EXPECTED_RANGE[test_code]
    return MappedLab(test_code=test_code, value=converted, unit=standard,
                     value_as_reported=as_reported, unit_as_reported=unit_reported,
                     out_of_expected_range=not (Decimal(str(low)) <= converted <= Decimal(str(high))))


def map_field(label: str, value, unit: str) -> MappedLab:
    """One line of a lab report (label, value, unit as read) -> lab_results values. For OCR review
    and POST /labs/map; manual entry already knows the test code and calls convert()."""
    text = str(value).strip()
    if "," in text:
        # Only a thousands separator (1,250 or 1,250.5). "5,6" could mean 5.6 or 56, so it is refused.
        if not re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", text):
            raise MappingError(f"Not a number: {value!r} (use a dot for decimals)")
        text = text.replace(",", "")
    try:
        number = Decimal(text)
    except ArithmeticError as exc:  # decimal.InvalidOperation
        raise MappingError(f"Not a number: {value!r}") from exc
    return convert(test_code_for(label), number, unit)
