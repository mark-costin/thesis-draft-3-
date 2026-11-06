"""Why are the T2D, CRD and NEURO AUROCs low? (investigation only: reads the generator, data and features.yaml;
changes nothing). Tests: test_auroc_ceiling.py. Report: python ml/investigate/auroc_ceiling.py"""
import contextlib
import io
import json
import os
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import train_test_split

ROOT = Path(__file__).resolve().parents[2]
DOMAINS = ["CVD", "T2D", "CRD", "NEURO"]
VARIANTS = {
    "full": "rate(age, sex) x family x M (M with frailty): the hazard that drew onset",
    "no_frailty": "same, frailty integrated out: everything observable in the generator",
    "age_sex_family": "rate(age, sex) x family (no risk factors, no frailty)",
    "age_sex": "rate(age, sex) only",
}


# ---------------------------------------------------------------- generator
def load_generator(nb_path):
    """Notebook 1's code cells (no tests, no RUN cell) in one namespace, run from the notebook's folder."""
    nb_path = Path(nb_path)
    ns, here = {"__name__": "syntgen"}, os.getcwd()
    try:
        os.chdir(nb_path.parent)
        with contextlib.redirect_stdout(io.StringIO()):
            for c in json.load(open(nb_path))["cells"][:34]:
                src = "".join(c["source"])
                if c["cell_type"] != "code" or src.startswith("%%ipytest"):
                    continue
                src = "\n".join(l for l in src.splitlines()
                                if "ipytest" not in l and "PytestAssertRewriteWarning" not in l)
                exec(compile(src, str(nb_path), "exec"), ns)
    finally:
        os.chdir(here)
    return ns


def regenerate(gen, seed, n):
    """The same patients generate() made (same per-patient random stream), without visits."""
    cfg = {**gen["CFG"], "seed": seed}
    return [gen["make_patient"](pid, np.random.default_rng([seed, pid]), cfg) for pid in range(n)]


# ---------------------------------------------------------------- 1. oracle
def window_years(index_date, years=5):
    """Onset years whose onset date (1 July) falls in (index, index + 5 years], as label_for counts them."""
    end = date(index_date.year + years, index_date.month, index_date.day)
    return [y for y in range(index_date.year, index_date.year + years + 1) if index_date < date(y, 7, 1) <= end]


def onset_probability(hazards):
    """1 - chance of no onset in any year of any disease; yearly hazard capped at 0.99 as in Part B."""
    free = 1.0
    for hs in hazards.values():
        for h in hs:
            free *= 1 - min(h, 0.99)
    return 1 - free


def multiplier_without_frailty(gen, factors, sex, disease):
    """E[M | risk factors]: the published relative risks, frailty integrated out (E[exp(sd Z)] = exp(sd^2/2))."""
    log_rr = sum(np.log(r) * factors[k] for k, r in gen["RR"][disease].items())
    return float(np.exp(log_rr + gen["P0"]["frailty_sd"] ** 2 / 2)) / gen["mean_raw_multiplier"](disease, sex)


def oracle_risk(gen, p, domain, index_date, variant="full"):
    """Probability of onset within 5 years of the index date from the generator's own hazards.
    Family multiplier: relatives diagnosed before the index year (what is true at the index date)."""
    diseases = gen["APP_DOMAINS"][domain] + (["HTN"] if domain == "CVD" else [])
    factors = gen["risk_factors"](p)
    hazards = {}
    for d in diseases:
        if variant == "full":
            m = p["risk_mult"][d]
        elif variant == "no_frailty":
            m = multiplier_without_frailty(gen, factors, p["sex"], d)
        else:
            m = 1.0
        fam = 1.0 if variant == "age_sex" else gen["family_multiplier_now"](p, d, index_date.year)
        tv = 1.0
        if variant in ("full", "no_frailty") and d in gen.get("TV_RR", {}):
            # factors that change over time (Phase 0c: diabetes, BMI >= 30), as known at the index date:
            # diabetes only if it began before the index year, BMI from the person's path in the index year
            t2d = p["dx"].get("T2D")
            tv = gen["onset_tv_mult"](p, d, index_date.year,
                                      t2d_year=t2d if t2d is not None and t2d < index_date.year else None)
        hazards[d] = [gen["yearly_hazard"](d, y - p["birth_year"], p["sex"]) * m * fam * tv
                      for y in window_years(index_date)]
    return onset_probability(hazards)


# ---------------------------------------------------------------- the test split of train_models.ipynb
def label_table(folder, disease):
    tr = pd.read_csv(Path(folder) / "training.csv", parse_dates=["index_date"])
    lab = tr.pivot(index="patient_id", columns="domain", values="label").sort_index()
    sub = lab[lab[disease].notna()]
    idx = tr[tr.domain == disease].set_index("patient_id").index_date
    return sub.index.to_numpy(), sub[disease].astype(int).to_numpy(), idx


def test_split(folder, disease, seed=20261101):
    """Patient ids and labels of the test part, exactly as split() in train_models.ipynb (70/15/15, stratified)."""
    pid, y, _ = label_table(folder, disease)
    i = np.arange(len(y))
    _, rest = train_test_split(i, test_size=0.30, stratify=y, random_state=seed)
    _, te = train_test_split(rest, test_size=0.50, stratify=y[rest], random_state=seed)
    return pid[te], y[te]


# ---------------------------------------------------------------- 2. feature coverage
# generator factor -> model features that can carry it, best first: (feature, how)
_VIA_FHRS = "partly (generator FHRS only; the app FHRS uses a shared-household flag)"
FACTOR_FEATURES = {
    "age": [("age", "yes")], "sex": [("sex", "yes")], "family_history": [("fhrs_{d}", "yes")],
    "smoker_current": [("smoking_status", "yes"), ("pack_years", "partly (pack_years)")],
    "smoker_former": [("smoking_status", "yes"), ("pack_years", "partly (pack_years)")],
    **{e: [(e, "yes"), ("fhrs_{d}", _VIA_FHRS)] for e in
       ("secondhand_smoke", "high_salt_diet", "processed_diet", "low_home_activity", "indoor_mold", "coal_heating")},
    "low_activity": [("physical_activity_min_week", "yes")], "sedentary": [("sedentary_hours_day", "yes")],
    "gestational_diabetes": [("gestational_diabetes", "yes")], "allergic_rhinitis": [("allergic_rhinitis", "yes")],
    "occupational_dust": [("occupational_dust", "yes")], "tbi_history": [("tbi_history", "yes")],
    # Phase 0c (NEURO): insomnia = the baseline sleep band; obesity and diabetes change year by year
    "insomnia": [("sleep_hours_day", "yes (baseline sleep band)"), ("psqi", "yes (baseline PSQI band)")],
    "obesity": [("bmi", "yes")],
    "t2d": [],                                                    # a diagnosis is never a model input
}
REPORTABLE = {
    "age": "yes (profile)", "sex": "yes (profile)", "family_history": "yes (family history)",
    "smoker_current": "yes (lifestyle: smoking_status)", "smoker_former": "yes (lifestyle: smoking_status)",
    "secondhand_smoke": "not collected: the app asks only if an affected relative shared the household",
    "high_salt_diet": "not collected: the app asks only if an affected relative shared the household",
    "processed_diet": "not collected: the app asks only if an affected relative shared the household",
    "low_home_activity": "not collected: the app asks only if an affected relative shared the household",
    "indoor_mold": "not collected: the app asks only if an affected relative shared the household",
    "coal_heating": "not collected: the app asks only if an affected relative shared the household",
    "low_activity": "yes (lifestyle)", "sedentary": "yes (lifestyle)",
    "gestational_diabetes": "yes (health history)", "allergic_rhinitis": "yes (health history)",
    "occupational_dust": "yes (lifestyle)", "tbi_history": "yes (health history)",
    "frailty": "no (random term, unobservable by design)",
    "insomnia": "yes (sleep hours, PSQI)", "obesity": "yes (BMI)", "t2d": "yes (diagnosis in the app)",
}


def coverage(gen, features_yaml):
    spec = yaml.safe_load(open(features_yaml))
    rows = []
    for dom in DOMAINS:
        feats = {f["name"] for f in spec["models"][dom]["features"]}
        diseases = gen["APP_DOMAINS"][dom] + (["HTN"] if dom == "CVD" else [])
        factors = {}
        for d in diseases:
            for k, r in gen["RR"][d].items():
                factors.setdefault(k, []).append(f"{d} x{r}")
            for k, r in gen.get("TV_RR", {}).get(d, {}).items():
                factors.setdefault(k, []).append(f"{d} x{r} year by year")
        for k in ("age", "sex", "family_history"):
            factors.setdefault(k, ["GBD rate" if k != "family_history" else f"x{gen['CFG']['family_multiplier']}"])
        for k, used in factors.items():
            hit = next(((n.format(d=dom.lower()), how) for n, how in FACTOR_FEATURES[k]
                        if n.format(d=dom.lower()) in feats), ("", "no"))
            rows.append({"domain": dom, "factor": k, "used for": ", ".join(used),
                         "model_input": hit[1], "feature": hit[0],
                         "patient can report it": REPORTABLE[k]})
        rows.append({"domain": dom, "factor": "frailty", "used for": f"all, sd {gen['P0']['frailty_sd']} (log)",
                     "model_input": "no (unobservable)", "feature": "", "patient can report it": REPORTABLE["frailty"]})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 3. information before a later index visit
def prior_availability(visits, index_visit, values):
    have = visits.loc[visits.visit_no == index_visit, "patient_id"]
    v = visits[visits.patient_id.isin(have) & (visits.visit_no <= index_visit)]
    rows = []
    for name in values:
        k = v.groupby("patient_id")[name].count()
        rows.append({"value": name, "patients with index visit": int(len(have)),
                     "mean readings up to index": float(k.mean()) if len(k) else np.nan,
                     "share with >= 2 readings (slope possible)": float((k >= 2).mean()) if len(k) else np.nan,
                     "share with >= 3 readings": float((k >= 3).mean()) if len(k) else np.nan})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- report
def auc(y, s):
    return roc_auc_score(y, s) if 0 < np.sum(y) < len(y) else np.nan


def oracle_table(gen, folder, seed, n):
    pts = {p["patient_id"]: p for p in regenerate(gen, seed, n)}
    rows = []
    for dom in DOMAINS:
        pid, y, idx = label_table(folder, dom)
        scores = {v: np.array([oracle_risk(gen, pts[i], dom, idx[i].date(), v) for i in pid]) for v in VARIANTS}
        te_ids, te_y = test_split(folder, dom)
        te = np.isin(pid, te_ids)
        assert te.sum() == len(te_ids) and y[te].sum() == te_y.sum()
        for part, mask in (("all rows", np.ones(len(y), bool)), ("test split", te)):
            rows.append({"seed": seed, "domain": dom, "rows": part, "n": int(mask.sum()), "pos": int(y[mask].sum()),
                         **{f"oracle {v}": auc(y[mask], scores[v][mask]) for v in VARIANTS}})
    return pd.DataFrame(rows)


def main():
    gen = load_generator(ROOT / "ml/synthetic/01_syntgen.ipynb")
    pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30); pd.set_option("display.max_colwidth", 70)
    out = []
    for folder, seed in ((ROOT / "ml/synthetic/output", 20261101), (ROOT / "ml/synthetic/output_seed2", 20261102)):
        n = len(pd.read_csv(folder / "patients.csv"))
        out.append(oracle_table(gen, folder, seed, n))
    oracle = pd.concat(out, ignore_index=True)
    print("== 1. Oracle AUROC (generator's own risk at the index date) ==")
    for v, text in VARIANTS.items():
        print(f"  oracle {v}: {text}")
    print(oracle.round(3).to_string(index=False))

    print("\n== 2. Feature coverage ==")
    print(coverage(gen, ROOT / "ml/features.yaml").to_string(index=False))

    print("\n== 3. Visits and readings up to a later index visit (seed 20261101) ==")
    vis = pd.read_csv(ROOT / "ml/synthetic/output/visits.csv")
    nv = vis.groupby("patient_id").visit_no.max()
    print("visits per patient:", nv.value_counts().sort_index().to_dict())
    vis["visit_date"] = pd.to_datetime(vis.visit_date)
    first = vis[vis.visit_no == 1].set_index("patient_id").visit_date
    for k in (2, 3):
        at = vis[vis.visit_no == k].set_index("patient_id").visit_date
        months = ((at - first.reindex(at.index)).dt.days / 30.4)
        print(f"index at visit {k}: {len(at):,} patients ({len(at) / len(nv):.0%}), "
              f"median {months.median():.0f} months after visit 1; index date up to {at.max().date()}")
        print(prior_availability(vis, k, ["hba1c", "fpg", "sbp", "dbp", "phq9", "gad7", "bmi", "fev1_fvc"])
              .round(2).to_string(index=False))

    m = pd.read_csv(ROOT / "ml/artifacts/metrics.csv")
    x = pd.read_csv(ROOT / "ml/artifacts/metrics_xgb_all.csv")
    print("\n== 4. Oracle vs models ==")
    rows = []
    for dom in DOMAINS:
        for data, seed, part in (("test", 20261101, "test split"), ("seed2", 20261102, "all rows")):
            o = oracle[(oracle.seed == seed) & (oracle.domain == dom) & (oracle.rows == part)].iloc[0]
            mm = m[(m.disease == dom) & (m.data == data)].iloc[0]
            xx = x[(x.disease == dom) & (x.data == data)].iloc[0]
            rows.append({"domain": dom, "data": data, "pos": int(o.pos), "oracle full": o["oracle full"],
                         "oracle no frailty": o["oracle no_frailty"], "oracle age+sex": o["oracle age_sex"],
                         "chosen model": mm.model, "model AUROC": mm.auroc, "xgb AUROC": xx.xgb_auroc,
                         "gap (no frailty - model)": o["oracle no_frailty"] - mm.auroc})
    print(pd.DataFrame(rows).round(3).to_string(index=False))


if __name__ == "__main__":
    main()
