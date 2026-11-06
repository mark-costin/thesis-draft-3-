"""Tests for the AUROC-ceiling investigation (read only: nothing here changes the generator, data or models).
Run: uv run --group ml python -m pytest -q ml/investigate/test_auroc_ceiling.py"""
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent))
import auroc_ceiling as ac  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def gen():
    return ac.load_generator(ROOT / "ml/synthetic/01_syntgen.ipynb")


# ---------- 1. oracle ----------
def test_onset_years_inside_the_5_year_window():
    # onset is dated 1 July of its year; the window is (index, index + 5 years]
    assert ac.window_years(date(2021, 3, 1)) == [2021, 2022, 2023, 2024, 2025]
    assert ac.window_years(date(2021, 9, 1)) == [2022, 2023, 2024, 2025, 2026]


def test_onset_probability_is_one_minus_the_chance_of_no_onset_in_any_year():
    hazards = {"A": [0.1, 0.1], "B": [0.5, 0.0]}
    assert ac.onset_probability(hazards) == pytest.approx(1 - 0.9 * 0.9 * 0.5 * 1.0)
    assert ac.onset_probability({"A": [2.0]}) == pytest.approx(0.99)      # hazard capped at 0.99 as in Part B


def test_regenerated_patients_are_the_ones_in_the_saved_data(gen):
    pts = pd.read_csv(ROOT / "ml/synthetic/output/patients.csv").set_index("patient_id")
    for p in ac.regenerate(gen, seed=gen["CFG"]["seed"], n=150):
        row = pts.loc[p["patient_id"]]
        assert row["birth_year"] == p["birth_year"] and row["sex"][0] == p["sex"]
        for d, y in p["dx"].items():
            saved = row[f"true_dx_year_{d}"]
            assert (pd.isna(saved) and y is None) or saved == y, (p["patient_id"], d)


def test_multiplier_without_frailty_ignores_the_frailty_draw(gen):
    p = ac.regenerate(gen, seed=gen["CFG"]["seed"], n=1)[0]
    factors = gen["risk_factors"](p)
    a = gen["personal_multipliers"](factors, p["sex"], np.random.default_rng(1))
    b = gen["personal_multipliers"](factors, p["sex"], np.random.default_rng(2))
    assert a != b                                                        # frailty differs
    for d in gen["RISK_DISEASES"]:
        assert ac.multiplier_without_frailty(gen, factors, p["sex"], d) == pytest.approx(
            ac.multiplier_without_frailty(gen, factors, p["sex"], d))
    # full multiplier = no-frailty multiplier x exp(frailty) / E[exp(frailty)]
    sd = gen["P0"]["frailty_sd"]
    for d in gen["RISK_DISEASES"]:
        ratio = p["risk_mult"][d] / ac.multiplier_without_frailty(gen, factors, p["sex"], d)
        z = (np.log(ratio) + sd ** 2 / 2) / sd
        assert abs(z) < 6                                                # a plausible N(0,1) draw


def test_oracle_risk_is_higher_for_people_whose_onset_falls_in_the_window(gen):
    tr = pd.read_csv(ROOT / "ml/synthetic/output/training.csv", parse_dates=["index_date"])
    pts = {p["patient_id"]: p for p in ac.regenerate(gen, seed=gen["CFG"]["seed"], n=600)}
    t = tr[tr.patient_id.isin(pts) & (tr.domain == "CVD")]
    risk = [ac.oracle_risk(gen, pts[r.patient_id], "CVD", r.index_date.date(), "full") for r in t.itertuples()]
    risk = np.array(risk)
    assert risk[t.label.to_numpy() == 1].mean() > risk[t.label.to_numpy() == 0].mean()


# ---------- the same test split as train_models.ipynb ----------
def test_test_split_has_the_sizes_train_models_reported():
    m = pd.read_csv(ROOT / "ml/artifacts/metrics.csv")
    for d in ("CVD", "T2D", "CRD", "NEURO"):
        ids, y = ac.test_split(ROOT / "ml/synthetic/output", d, seed=20261101)
        row = m[(m.disease == d) & (m.data == "test")].iloc[0]
        assert len(ids) == row.n and y.sum() == row.pos, d


# ---------- 2. feature coverage ----------
def test_coverage_lists_every_onset_factor_and_marks_model_inputs(gen):
    cov = ac.coverage(gen, ROOT / "ml/features.yaml")
    factors = {k for rr in gen["RR"].values() for k in rr} | {"age", "sex", "family_history", "frailty"}
    assert factors <= set(cov["factor"])
    look = cov.set_index(["domain", "factor"])["model_input"]
    import yaml
    spec = yaml.safe_load(open(ROOT / "ml/features.yaml"))
    feats = {d: {f["name"] for f in m["features"]} for d, m in spec["models"].items()}
    assert look[("T2D", "gestational_diabetes")] == "yes" and look[("CRD", "allergic_rhinitis")] == "yes"
    for d in ("T2D", "CRD", "CVD"):                                       # follows features.yaml, whatever it holds
        assert look[(d, "age")] == ("yes" if "age" in feats[d] else "no"), d
    smoking = look[("T2D", "smoker_current")]
    assert smoking == ("yes" if "smoking_status" in feats["T2D"] else "no")
    assert look[("CVD", "frailty")] == "no (unobservable)"


# ---------- 3. information before a later index date ----------
def test_prior_visit_availability_counts_measured_values_before_the_index_visit():
    v = pd.DataFrame({"patient_id": [1, 1, 1, 2, 2], "visit_no": [1, 2, 3, 1, 2],
                      "hba1c": [5.5, np.nan, 5.9, 5.1, 5.2], "sbp": [120, 125, 130, 110, 112]})
    out = ac.prior_availability(v, index_visit=3, values=["hba1c", "sbp"]).set_index("value")
    assert out.loc["sbp", "patients with index visit"] == 1               # patient 2 has no visit 3
    assert out.loc["sbp", "mean readings up to index"] == 3.0
    assert out.loc["hba1c", "mean readings up to index"] == 2.0           # visit 2 HbA1c missing
    assert out.loc["hba1c", "share with >= 2 readings (slope possible)"] == 1.0
    assert out.loc["hba1c", "share with >= 3 readings"] == 0.0
