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
def train_models_namespace():
    """train_models.ipynb Cells 0-2 (setup, load_tables, split) run from ml/ai_model/. They read only committed
    files (features.yaml, output/*.csv), never the git-ignored training run reports."""
    import contextlib, io, json, os
    cells = json.load(open(ROOT / "ml/ai_model/train_models.ipynb"))["cells"]
    ns, here = {"__name__": "train_models"}, os.getcwd()
    try:
        os.chdir(ROOT / "ml/ai_model")
        with contextlib.redirect_stdout(io.StringIO()):
            for i in (2, 4, 6):
                exec(compile("".join(cells[i]["source"]), f"train_models cell {i}", "exec"), ns)
    finally:
        os.chdir(here)
    return ns


def test_test_split_is_the_one_train_models_makes():
    tm = train_models_namespace()
    for d in ("CVD", "T2D", "CRD", "NEURO"):
        ids, y = ac.test_split(ROOT / "ml/synthetic/output", d, seed=tm["SEED"])
        _, y_nb, pid_nb = tm["SPLITS"][d]["test"]
        assert list(ids) == list(pid_nb) and (y == y_nb).all(), d


def ignored_paths_named_in(text):
    """The "ml/..." paths written in a test file that git ignores (works on a fresh clone: the files need not exist)."""
    import re, subprocess
    paths = sorted(set(re.findall(r'"(ml/[^"\s]+)"', text)))
    return [p for p in paths if subprocess.run(["git", "check-ignore", "-q", p], cwd=ROOT).returncode == 0]


def test_no_ml_test_reads_a_git_ignored_file():
    """Git-ignored files (training run reports, the seed-2 CSVs) differ per machine: no tracked test may need one."""
    import subprocess
    tests = subprocess.run(["git", "ls-files", "ml/**/test_*.py"], cwd=ROOT, capture_output=True, text=True,
                           check=True).stdout.split()
    assert "ml/investigate/test_auroc_ceiling.py" in tests
    for test_file in tests:
        assert ignored_paths_named_in((ROOT / test_file).read_text(encoding="utf-8")) == [], test_file
    # the check itself catches the old dependency
    old_line = 'm = pd.read_csv(ROOT / "ml/' + 'artifacts/metrics.csv")'      # split so this file does not name it
    assert ignored_paths_named_in(old_line) == ["ml/" + "artifacts/metrics.csv"]


# ---------- 2. feature coverage ----------
def test_coverage_lists_every_onset_factor_and_marks_model_inputs(gen):
    cov = ac.coverage(gen, ROOT / "ml/features.yaml")
    factors = ({k for rr in gen["RR"].values() for k in rr} | {k for tv in gen["TV_RR"].values() for k in tv}
               | {"age", "sex", "family_history", "frailty"})
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
    # NEURO (Phase 0c): insomnia is the baseline sleep band; obesity and diabetes change year by year
    assert look[("NEURO", "insomnia")].startswith("yes") and "sleep_hours_day" in feats["NEURO"]
    assert look[("NEURO", "obesity")] == ("yes" if "bmi" in feats["NEURO"] else "no")
    assert look[("NEURO", "t2d")] == "no"                                  # a diagnosis is never a model input
    used = cov.set_index(["domain", "factor"])["used for"]
    assert "year by year" in used[("NEURO", "t2d")] and "year by year" in used[("NEURO", "obesity")]


def test_oracle_counts_diabetes_only_if_it_began_before_the_index_year(gen):
    p = ac.regenerate(gen, seed=gen["CFG"]["seed"], n=40)[37]
    index = date(2021, 3, 1)
    risk = {}
    for label, year in (("none", None), ("before", 2015), ("after", 2023)):
        q = dict(p, dx={**p["dx"], "T2D": year})
        risk[label] = ac.oracle_risk(gen, q, "NEURO", index, "full")
    assert risk["before"] > risk["none"]                                   # x1.15 on depression
    assert risk["after"] == pytest.approx(risk["none"])                    # a later onset is not known at the index
    q = dict(p, dx={**p["dx"], "T2D": 2015})
    assert ac.oracle_risk(gen, q, "NEURO", index, "age_sex") == pytest.approx(
        ac.oracle_risk(gen, p, "NEURO", index, "age_sex"))                 # age+sex variants have no risk factors


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
