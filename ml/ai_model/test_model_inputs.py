"""The patient-reportable inputs the AUROC-ceiling investigation found missing (feat/model-missing-inputs).
Run: uv run --group ml python -m pytest -q ml/ai_model/test_model_inputs.py"""
import ast
import json
import sys
from pathlib import Path

import pandas as pd
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SPEC = yaml.safe_load((ROOT / "ml/features.yaml").read_text(encoding="utf-8"))
NB = json.load(open(ROOT / "ml/ai_model/train_models.ipynb"))
EXPOSURES = {"secondhand_smoke", "indoor_mold", "coal_heating", "high_salt_diet", "processed_diet", "low_home_activity"}


def names(d):
    return [f["name"] for f in SPEC["models"][d]["features"]]


def feature(d, name):
    return next(f for f in SPEC["models"][d]["features"] if f["name"] == name)


def notebook_literal(var):
    """A dict literal assigned in train_models.ipynb, Cell 0 (SOURCE_NAMES, EXPECTED_DIR)."""
    for c in NB["cells"]:
        for node in ast.parse("".join(c["source"])).body if c["cell_type"] == "code" else []:
            if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == var:
                return ast.literal_eval(node.value)
    raise KeyError(var)


def test_t2d_and_crd_have_age_and_sex():
    for d in ("T2D", "CRD"):
        assert {"age", "sex"} <= set(names(d)), d
        assert feature(d, "sex")["encoding"] == {"Male": 1, "Female": 0}


def test_crd_has_its_family_history_score():
    f = feature("CRD", "fhrs_crd")
    assert f["column"] == "patient_fhrs_stratification.calculated_fhrs" and f["where"]["disease_domain"] == "CRD"


def test_smoking_status_is_an_input_with_three_levels():
    for d in ("CVD", "T2D", "CRD"):
        f = feature(d, "smoking_status")
        assert f["encoding"] == {"never": 0, "former": 1, "current": 2}, d
        assert f["column"] == "lifestyle_records.smoking_status"
    assert "smoking_status" not in names("NEURO")


def test_household_exposures_are_not_inputs_until_the_app_collects_them():
    # option 2 (Nov 6): the app has no column for them yet (family-history form: one shared-household flag)
    for d in SPEC["models"]:
        assert not set(names(d)) & EXPOSURES, d


def test_every_model_input_is_in_the_training_data():
    rename = notebook_literal("SOURCE_NAMES")
    vis = pd.read_csv(ROOT / "ml/synthetic/output/visits.csv", nrows=5).rename(columns=rename)
    pat = pd.read_csv(ROOT / "ml/synthetic/output/patients.csv", nrows=5).rename(columns=rename)
    have = set(vis) | set(pat)
    for d in SPEC["models"]:
        missing = [n for n in names(d) if n not in have]
        assert not missing, (d, missing)


def test_new_inputs_have_an_expected_direction_for_the_monotone_constraints():
    exp = notebook_literal("EXPECTED_DIR")
    for d in ("CVD", "T2D", "CRD"):
        assert exp[d]["smoking_status"] == 1, d
        assert not set(exp[d]) & EXPOSURES, d
    assert exp["CRD"]["fhrs_crd"] == 1
    assert not {k for k in notebook_literal("SOURCE_NAMES") if k.startswith("exp_")}


def test_every_model_input_has_an_existing_med_db_column():
    """The app loads features.yaml at startup and refuses unknown columns (backend/readiness.load_features)."""
    sys.path.insert(0, str(ROOT))
    from backend.readiness import load_features
    loaded = {(f.model, f.name) for f in load_features()}
    assert loaded == {(d, n) for d in SPEC["models"] for n in names(d)}
    assert not any(f.get("planned_column") for d in SPEC["models"] for f in SPEC["models"][d]["features"])
