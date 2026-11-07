"""Shared evaluation protocol for the four risk models (investigation only: writes no model, changes no data).

Candidates (a) grid, (c) logistic and (d) calibration run here. Candidate (b) Optuna runs in #52 on the same
splits (shared_splits.csv) and is judged by compare_candidates(). Both choose settings on repeats 1 and 2 only,
and compare_candidates() scores repeat 3 only. Only train and validation are ever read: the test split is dropped
in load_tasks(), seed 2 is never opened, and Task.check() guards every fit.

Tests: test_evaluation_protocol.py
Splits: uv run --group ml python ml/investigate/evaluation_protocol.py splits
Report: uv run --group ml python ml/investigate/evaluation_protocol.py run <output folder>
Candidate (a) alone: uv run --group ml python ml/investigate/evaluation_protocol.py grid <output folder>"""
import contextlib
import io
import json
import sys
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from fairlearn.metrics import MetricFrame, equalized_odds_difference
from scipy.stats import norm, rankdata
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, precision_recall_curve, roc_auc_score, roc_curve
from sklearn.model_selection import RepeatedStratifiedKFold, StratifiedKFold

sys.path.insert(0, str(Path(__file__).parent))
import auroc_ceiling as ac  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "ml/synthetic/output"
SPLIT_FILE = Path(__file__).parent / "shared_splits.csv"
SEED = 20261101
DISEASES = ["CVD", "T2D", "CRD", "NEURO"]
REPEATS, FOLDS = 3, 5
# Rule 4: settings are chosen on repeats 1 and 2 and judged on repeat 3. These are the 0-based `repeat` values.
TUNE_REPEATS, SCORE_REPEAT = (0, 1), 2
N_BOOT = 2000
CUTS = (0.20, 0.50)
METHODS = ("isotonic", "sigmoid")
THRESHOLDS = np.round(np.arange(0.01, 0.301, 0.01), 2)
# Raw scores of different fold models are not on one scale (13 to 43 trees at the same settings), so anything that
# pools folds into one ranking uses this calibrated column. Scores per fold are not affected.
POOLED = "p_sigmoid"

# Copies of the notebook's settings, because #52 replaces them there and (a) must stay the same; a test compares them.
GRID = [dict(max_depth=d, learning_rate=lr, min_child_weight=mcw) for d in (2, 3, 4) for lr in (0.03, 0.1) for mcw in (1, 10)]
MAX_TREES, EARLY = 600, 50
XGB_FIXED = dict(eval_metric="aucpr", tree_method="hist", subsample=0.8, colsample_bytree=0.8, random_state=SEED, n_jobs=4)


# ---------------------------------------------------------------- statistics
def delong(y, a, b):
    """DeLong test of two correlated AUROCs on the same patients (fast form, midranks for ties)."""
    y = np.asarray(y) == 1
    m, n = y.sum(), (~y).sum()

    def placements(s):
        s = np.asarray(s, float)
        whole = rankdata(s)
        return (whole[y] - rankdata(s[y])) / n, 1 - (whole[~y] - rankdata(s[~y])) / m

    (a10, a01), (b10, b01) = placements(a), placements(b)
    var = np.var(a10 - b10, ddof=1) / m + np.var(a01 - b01, ddof=1) / n
    z = (a10.mean() - b10.mean()) / np.sqrt(var) if var > 0 else 0.0
    return {"auc_a": a10.mean(), "auc_b": b10.mean(), "z": z, "p": float(2 * norm.sf(abs(z)))}


def ece(y, p, bins=10):
    """Expected calibration error, equal-width bins [lo, hi); p = 1 goes in the last bin."""
    y, p = np.asarray(y), np.asarray(p, float)
    b = np.minimum((p * bins).astype(int), bins - 1)
    return float(sum(abs(y[b == k].mean() - p[b == k].mean()) * (b == k).mean() for k in np.unique(b)))


def calibration_slope(y, p):
    """Logistic slope of y on logit(p). Exact 0 and 1 have no logit, so those rows are left out and counted:
    a clip would invent a logit for them, and the slope would then depend on the clip (T2D: 0.027)."""
    y, p = np.asarray(y), np.asarray(p, float)
    edge = (p <= 0) | (p >= 1)
    out = {"slope": np.nan, "excluded": int(edge.sum()), "excluded_events": int(y[edge].sum())}
    q, yq = p[~edge], y[~edge]
    if len(np.unique(q)) > 1 and 0 < yq.sum() < len(yq):
        fit = LogisticRegression(C=np.inf, tol=1e-8, max_iter=10000).fit(np.log(q / (1 - q))[:, None], yq)
        out["slope"] = float(fit.coef_[0, 0])
    return out


def net_benefit(y, p, threshold):
    y, treated = np.asarray(y), np.asarray(p) >= threshold
    return ((y[treated] == 1).sum() - (y[treated] == 0).sum() * threshold / (1 - threshold)) / len(y)


def treat_all(y, threshold):
    return net_benefit(y, np.ones(len(y)), threshold)


def subgroup_auroc(y, s, groups):
    return MetricFrame(metrics=ac.auc, y_true=np.asarray(y), y_pred=np.asarray(s), sensitive_features=np.asarray(groups)).by_group


def equalized_odds(y, p, groups, cut):
    """Worst gap in TPR or FPR between groups at a cut-off. NaN if nobody is flagged, which is not "fair".
    A group with one class has no TPR or FPR, so it is left out."""
    y, flagged, groups = np.asarray(y), (np.asarray(p) >= cut).astype(int), np.asarray(groups)
    keep = pd.Series(y).groupby(groups).transform("nunique").to_numpy() == 2
    if not flagged[keep].any():
        return np.nan
    return float(equalized_odds_difference(y[keep], flagged[keep], sensitive_features=groups[keep]))


def age_band(age):
    return pd.cut(np.asarray(age, float), [0, 39, 59, 200], labels=["18-39", "40-59", "60+"]).astype(str)


def ap(y, s, w=None):
    return average_precision_score(y, s, sample_weight=w)


def auroc(y, s, w=None):
    return roc_auc_score(y, s, sample_weight=w)


def brier(y, s, w=None):
    return brier_score_loss(y, s, sample_weight=w)


def mean_of_folds(frame, metric, by=("repeat", "fold"), col="score"):
    return float(np.mean([metric(g.y.to_numpy(), g[col].to_numpy()) for _, g in frame.groupby(list(by))]))


def boot_means(frame, metric, n_boot, seed, by=("repeat", "fold"), col="score"):
    """mean_of_folds over patient resamples. A patient keeps one weight across repeats, and the same seed gives
    the same draws, so two frames of the same patients stay paired. A draw with a one-class group gives NaN."""
    ids = np.sort(frame.patient_id.unique())
    groups = [(np.searchsorted(ids, g.patient_id.to_numpy()), g.y.to_numpy(), g[col].to_numpy())
              for _, g in frame.groupby(list(by))]
    rng, out = np.random.default_rng(seed), np.empty(n_boot)
    for i in range(n_boot):
        w = np.bincount(rng.integers(0, len(ids), len(ids)), minlength=len(ids))
        out[i] = np.mean([metric(y, s, w[idx]) if w[idx][y == 1].sum() and w[idx][y == 0].sum() else np.nan
                          for idx, y, s in groups])
    return out


def ci(draws):
    return np.nanpercentile(draws, [2.5, 97.5])


# ---------------------------------------------------------------- the notebook's own code
@cache
def notebook():
    """CODE and TEST parts of Cells 0 to 6 of train_models.ipynb, never a RUN part (as ac.load_generator does)."""
    path, ns = ROOT / "ml/ai_model/train_models.ipynb", {"__name__": "train_models"}
    with contextlib.chdir(path.parent), contextlib.redirect_stdout(io.StringIO()):
        for c in json.load(open(path))["cells"][:15]:
            if c["cell_type"] == "code":
                exec(compile("".join(c["source"]).split("# ---------- RUN")[0], str(path), "exec"), ns)
    return ns


# ---------------------------------------------------------------- guard + fitting
class LeakError(Exception):
    """A row from outside train and validation, or from other splits, reached a fit, tuning or choice step."""


def row_keys(rows):
    # Content, not patient id: seed 1 and seed 2 share id numbers, so an id check cannot see a seed-2 row.
    return set(pd.util.hash_pandas_object(rows[0].assign(_patient_id=rows[2]), index=False))


@dataclass
class Task:
    """One disease: train and validation as (X, y, patient_id). Every fit goes through check()."""
    disease: str
    train: tuple
    val: tuple
    mono: list

    def __post_init__(self):
        self.allowed = row_keys(self.train) | row_keys(self.val)

    def check(self, rows):
        if not row_keys(rows) <= self.allowed:
            raise LeakError(f"{self.disease}: rows outside train and validation (test or seed 2?)")

    def xgb(self, params, y, **kwargs):
        s = {**XGB_FIXED, **params}
        s.setdefault("scale_pos_weight", (y == 0).sum() / max(1, (y == 1).sum()))
        return xgb.XGBClassifier(monotone_constraints=tuple(self.mono), **s, **kwargs)

    def fit_xgb(self, fit, stop, params):
        self.check(fit)
        self.check(stop)
        model = self.xgb(params, fit[1], n_estimators=MAX_TREES, early_stopping_rounds=EARLY)
        return model.fit(fit[0], fit[1], eval_set=[(stop[0], stop[1])], verbose=False)

    def fit_final(self, params, n_trees):
        self.check(self.train)
        return self.xgb(params, self.train[1], n_estimators=n_trees).fit(self.train[0], self.train[1])

    def fit_logistic(self, rows):
        self.check(rows)
        return notebook()["fit_baseline"](rows[0], rows[1])

    def calibrator(self, model, rows, method):
        self.check(rows)
        return notebook()["calibrate"](model, rows[0], rows[1], self.disease, method)

    def without(self, column):
        i = list(self.train[0].columns).index(column)
        drop = lambda r: (r[0].drop(columns=column), r[1], r[2])  # noqa: E731
        return Task(self.disease, drop(self.train), drop(self.val), self.mono[:i] + self.mono[i + 1:])


@cache
def load_tasks():
    """Train and validation per disease, split exactly as the notebook does. The test part stops here."""
    nb = notebook()
    tables = nb["load_tables"](DATA, nb["SPEC"])
    parts = {d: nb["split"](*tables[d]) for d in DISEASES}
    return {d: Task(d, parts[d]["train"], parts[d]["val"], nb["directions"](nb["SPEC"], d)) for d in DISEASES}


# ---------------------------------------------------------------- shared splits
def make_folds(pid, y, seed=SEED):
    """fold_r: the scored fold in repeat r. stop_r = 1: a stopping row whenever its fold is not the scored one."""
    out = pd.DataFrame({"patient_id": pid})
    splits = RepeatedStratifiedKFold(n_splits=FOLDS, n_repeats=REPEATS, random_state=seed).split(pid, y)
    for r in range(REPEATS):
        fold, stop = np.zeros(len(y), int), np.zeros(len(y), int)
        for k in range(FOLDS):
            part = next(splits)[1]
            fold[part] = k
            stop[part[next(StratifiedKFold(5, shuffle=True, random_state=seed + r).split(part, y[part]))[1]]] = 1
        out[f"fold_{r}"], out[f"stop_{r}"] = fold, stop
    return out


def write_folds():
    pd.concat([make_folds(t.train[2], t.train[1]).assign(disease=d) for d, t in load_tasks().items()]) \
        .set_index("disease").reset_index().to_csv(SPLIT_FILE, index=False)


def load_folds():
    return pd.read_csv(SPLIT_FILE)


def shared_folds(task, folds, repeats=range(REPEATS)):
    """The steps of the shared CV, 15 by default: (repeat, fold, fit rows, stopping rows, scored rows).
    Anything that chooses settings passes TUNE_REPEATS."""
    X, y, pid = task.train
    task.check(task.train)
    f = folds[folds.disease == task.disease].set_index("patient_id").loc[pid]
    take = lambda m: (X[m], y[m], pid[m])  # noqa: E731
    for r in repeats:
        fold, stop = f[f"fold_{r}"].to_numpy(), f[f"stop_{r}"].to_numpy() == 1
        for k in range(FOLDS):
            scored = fold == k
            yield r, k, take(~scored & ~stop), take(~scored & stop), take(scored)


def cv_predict(task, folds, fit, methods=(), repeats=range(REPEATS)):
    """Out-of-fold scores on the shared splits. fit(fit_rows, stop_rows) -> model. With methods, also p_<method>:
    the fold model calibrated on validation, which that fold model never saw."""
    out = []
    for r, k, fit_rows, stop_rows, (X, y, pid) in shared_folds(task, folds, repeats):
        model = fit(fit_rows, stop_rows)
        part = pd.DataFrame({"disease": task.disease, "repeat": r, "fold": k, "patient_id": pid, "y": y,
                             "score": model.predict_proba(X)[:, 1], "n_trees": getattr(model, "best_iteration", -1) + 1})
        for m in methods:
            part[f"p_{m}"] = task.calibrator(model, task.val, m).predict_proba(X)[:, 1]
        out.append(part)
    return pd.concat(out, ignore_index=True)


def trees(scores):
    """The refit's tree count is a setting too, so repeat 3 has no say in it."""
    return int(scores[scores.repeat.isin(TUNE_REPEATS)].groupby(["repeat", "fold"]).n_trees.first().mean())


def score_candidate(params, tasks=None, folds=None):
    """{disease: XGBoost settings} -> out-of-fold scores of all four diseases, ready for compare_candidates."""
    tasks, folds = tasks or load_tasks(), load_folds() if folds is None else folds
    return pd.concat([cv_predict(t, folds, lambda a, b, t=t, d=d: t.fit_xgb(a, b, params[d])) for d, t in tasks.items()],
                     ignore_index=True)


def validation_scores(params, cv_scores, tasks=None):
    """Refit on all of train with the CV tree count, score validation: the input of confirm_on_validation."""
    out = []
    for d, t in (tasks or load_tasks()).items():
        X, y, pid = t.val
        model = t.fit_final(params[d], trees(cv_scores[cv_scores.disease == d]))
        out.append(pd.DataFrame({"disease": d, "patient_id": pid, "y": y, "score": model.predict_proba(X)[:, 1]}))
    return pd.concat(out, ignore_index=True)


def select_grid(tasks=None, folds=None):
    """Candidate (a), chosen on repeats 1 and 2. Only the pick is fitted on repeat 3, so that repeat cannot sway it.
    Returns ({disease: best grid point}, its out-of-fold scores on all 3 repeats, the whole grid table)."""
    tasks, folds = tasks or load_tasks(), load_folds() if folds is None else folds
    table, best, scores = [], {}, []
    for d, t in tasks.items():
        runs = [cv_predict(t, folds, lambda a, b, g=g: t.fit_xgb(a, b, g), repeats=TUNE_REPEATS) for g in GRID]
        means = [mean_of_folds(s, ap) for s in runs]
        table += [{"disease": d, **g, "tune_auprc": m, "n_trees": trees(s)} for g, m, s in zip(GRID, means, runs)]
        best[d] = GRID[int(np.argmax(means))]
        scores += [runs[int(np.argmax(means))],
                   cv_predict(t, folds, lambda a, b: t.fit_xgb(a, b, best[d]), repeats=(SCORE_REPEAT,))]
    return best, pd.concat(scores, ignore_index=True), pd.DataFrame(table)


# ---------------------------------------------------------------- decision rule
def on_shared_splits(scores, folds):
    keys = ["disease", "repeat", "fold", "patient_id"]
    want = folds.assign(repeat=SCORE_REPEAT, fold=folds[f"fold_{SCORE_REPEAT}"])
    same = lambda f: f[keys].sort_values(keys).reset_index(drop=True).astype({"disease": object})  # noqa: E731
    if not same(want).equals(same(scores)):
        raise LeakError("scores are not on the shared splits (other folds, other rows, or a disease missing)")


def same_rows(a, b, keys):
    if not a.sort_values(keys)[keys + ["y"]].reset_index(drop=True).equals(b.sort_values(keys)[keys + ["y"]].reset_index(drop=True)):
        raise LeakError("the two candidates were not scored on the same patients")


def compare_candidates(a_scores, b_scores, *, b_keeps_constraints=False, n_boot=N_BOOT, seed=SEED, folds=None):
    """Rule 4, on repeat 3 only: both candidates must have chosen their settings on repeats 1 and 2, which this
    function cannot see. b wins a disease only if the whole 95% CI of (b - a) is above 0: a CI below 0 also
    "excludes 0", but it means b is worse. Picks b for all four only with 3 or more wins and b_keeps_constraints=True."""
    folds = load_folds() if folds is None else folds
    a_scores, b_scores = a_scores[a_scores.repeat == SCORE_REPEAT], b_scores[b_scores.repeat == SCORE_REPEAT]
    on_shared_splits(a_scores, folds)
    on_shared_splits(b_scores, folds)
    same_rows(a_scores, b_scores, ["disease", "repeat", "patient_id"])
    rows = []
    for d, a in a_scores.groupby("disease", sort=False):
        b = b_scores[b_scores.disease == d]
        lo, hi = ci(boot_means(b, ap, n_boot, seed) - boot_means(a, ap, n_boot, seed))
        rows.append({"disease": d, "a_auprc": mean_of_folds(a, ap), "b_auprc": mean_of_folds(b, ap), "lo": lo, "hi": hi})
    table = pd.DataFrame(rows).assign(diff=lambda t: t.b_auprc - t.a_auprc, b_better=lambda t: t.lo > 0)
    wins = int(table.b_better.sum())
    choice = "b" if wins >= 3 and b_keeps_constraints else "a"
    why = (f"b better in {wins} of 4; monotone constraints and TreeSHAP "
           f"{'confirmed' if b_keeps_constraints else 'not confirmed'}")
    return {"table": table, "wins": wins, "choice": choice, "reason": why}


def confirm_on_validation(a_val, b_val):
    """b stays only if its validation AUPRC is above a's in at least 3 of 4 diseases."""
    same_rows(a_val, b_val, ["disease", "patient_id"])
    table = pd.DataFrame([{"disease": d, "a_auprc": ap(a.y, a.score), "b_auprc": ap(b.y, b.score)}
                          for (d, a), (_, b) in zip(a_val.groupby("disease"), b_val.groupby("disease"))])
    return {"table": table, "confirmed": bool((table.b_auprc > table.a_auprc).sum() >= 3)}


def keeps_monotone_and_treeshap(model, mono, X):
    """The expected constraints are set, it is a tree booster, and pred_contribs adds up to the raw score."""
    p, booster = model.get_params(), model.get_booster()
    data = xgb.DMatrix(X, missing=np.nan, feature_names=list(X.columns))
    exact = np.allclose(booster.predict(data, pred_contribs=True).sum(1), booster.predict(data, output_margin=True), atol=1e-4)
    return tuple(p["monotone_constraints"] or ()) == tuple(mono) and p.get("booster") in (None, "gbtree") and bool(exact)


# ---------------------------------------------------------------- the run
def crossfit(task, model):
    """Calibrated scores on validation, each scored by a calibrator fitted on the other validation folds."""
    X, y, pid = task.val
    raw, out = model.predict_proba(X)[:, 1], []
    for i, (tr, te) in enumerate(RepeatedStratifiedKFold(n_splits=FOLDS, n_repeats=REPEATS, random_state=SEED).split(X, y)):
        part = pd.DataFrame({"disease": task.disease, "repeat": i // FOLDS, "fold": i % FOLDS, "patient_id": pid[te],
                             "y": y[te], "score": raw[te]})
        for m in METHODS:
            part[f"p_{m}"] = task.calibrator(model, (X.iloc[tr], y[tr], pid[tr]), m).predict_proba(X.iloc[te])[:, 1]
        out.append(part)
    return pd.concat(out, ignore_index=True)


def per_repeat(frame, fn):
    return pd.DataFrame([fn(g) for _, g in frame.groupby("repeat")]).mean()


def calibration_rows(frame):
    rows = []
    for m in METHODS:
        c = f"p_{m}"
        rows.append({"method": m, **per_repeat(frame, lambda g: {
            **calibration_slope(g.y.to_numpy(), g[c].to_numpy()), "ece": ece(g.y, g[c]), "brier": brier(g.y, g[c]),
            "max_p": g[c].max(), "distinct_p": g[c].nunique()}).to_dict()})
    t = pd.DataFrame(rows)
    d = boot_means(frame, brier, N_BOOT, SEED, ("repeat",), "p_isotonic") - boot_means(frame, brier, N_BOOT, SEED, ("repeat",), "p_sigmoid")
    lo, hi = ci(d)
    return t.assign(pick=t.method == t.method[t.brier.idxmin()], iso_minus_sig=t.brier[0] - t.brier[1], lo=lo, hi=hi)


def cut_rows(frame, col):
    nb = notebook()

    def one(g):
        y, p = g.y.to_numpy(), g[col].to_numpy()
        tiers = pd.Series([nb["tier"](x) for x in p]).value_counts(normalize=True)
        return {**{f"{k}@{c}": v for c in CUTS for k, v in nb["at_cut"](y, p, c).items()},
                **{f"share_{t}": tiers.get(t, 0.0) for t in ("Low", "Moderate", "High")}}
    return per_repeat(frame, one).to_dict()


def fairness_rows(frame, demo, col, rank):
    """col: the probability for the cut-offs. rank: the column for AUROC (POOLED when fold models are mixed)."""
    f = frame.merge(demo, on="patient_id")
    groups, eo = [], []
    for attr in ("sex", "age_band"):
        auc_by = per_repeat(f, lambda g: subgroup_auroc(g.y, g[rank], g[attr]).to_dict())
        for v, h in f.groupby(attr):
            lo, hi = ci(boot_means(h, auroc, 1000, SEED, ("repeat",), rank))
            groups.append({"attribute": attr, "group": v, "n": len(h) // REPEATS, "events": h.y.sum() / REPEATS,
                           "auroc": auc_by[v], "lo": lo, "hi": hi})
        for c in CUTS:
            eo.append({"attribute": attr, "cut": c, "flagged": (f[col] >= c).sum() / REPEATS,
                       "eod": per_repeat(f, lambda g: {"e": equalized_odds(g.y, g[col], g[attr], c)})["e"]})
    return pd.DataFrame(groups), pd.DataFrame(eo)


def decision_rows(frames):
    """frames: {name: (frame, probability column)}. Mean net benefit over repeats per threshold."""
    rows = []
    for t in THRESHOLDS:
        any_frame = next(iter(frames.values()))[0]
        row = {"threshold": t, "treat_all": per_repeat(any_frame, lambda g: {"v": treat_all(g.y, t)})["v"], "treat_none": 0.0}
        for name, (f, col) in frames.items():
            row[name] = per_repeat(f, lambda g: {"v": net_benefit(g.y, g[col], t)})["v"]
        rows.append(row)
    return pd.DataFrame(rows)


def oracle_rows(tasks):
    gen = ac.load_generator(ROOT / "ml/synthetic/01_syntgen.ipynb")
    pts = {p["patient_id"]: p for p in ac.regenerate(gen, SEED, len(pd.read_csv(DATA / "patients.csv")))}
    rows = []
    for d, t in tasks.items():
        index_date = ac.label_table(DATA, d)[2]
        for part, (_, y, pid) in (("train", t.train), ("validation", t.val)):
            rows.append({"disease": d, "rows": part, **{f"oracle_{v}": ac.auc(y, [ac.oracle_risk(gen, pts[i], d, index_date[i].date(), v) for i in pid])
                                                        for v in ("full", "no_frailty")}})
    return pd.DataFrame(rows)


def plots(out, cv, val, dca):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    ink, grid, blue, orange, aqua = "#52514e", "#e6e5e0", "#2a78d6", "#eb6834", "#1baf7a"
    plt.rcParams.update({"axes.spines.top": False, "axes.spines.right": False, "axes.edgecolor": ink, "text.color": ink,
                         "axes.labelcolor": ink, "xtick.color": ink, "ytick.color": ink, "axes.grid": True, "grid.color": grid,
                         "grid.linewidth": .6, "lines.linewidth": 2, "font.size": 10})
    series = {"xgboost": ("XGBoost (a)", blue), "logistic": ("Logistic (c)", orange)}

    def panels(name, title, draw, xlabel, ylabel):
        fig, axes = plt.subplots(1, 4, figsize=(15, 3.9))
        for ax, d in zip(axes, DISEASES):
            draw(ax, d)
            ax.set_title(d, loc="left", fontweight="bold")
            ax.set_xlabel(xlabel)
        axes[0].set_ylabel(ylabel)
        handles, labels = axes[0].get_legend_handles_labels()
        fig.legend(handles, labels, loc="lower center", ncol=len(labels), frameon=False)
        fig.suptitle(title, x=.01, ha="left", fontsize=12)
        fig.tight_layout(rect=(0, .07, 1, .96))
        fig.savefig(out / f"{name}.png", dpi=150)
        plt.close(fig)

    def first_repeat(d, m):
        g = cv[d][m]
        return g[g.repeat == 0]

    def roc(ax, d):
        for m, (label, color) in series.items():
            g = first_repeat(d, m)
            fpr, tpr, _ = roc_curve(g.y, g[POOLED])
            ax.plot(fpr, tpr, color=color, label=label)
        ax.plot([0, 1], [0, 1], color=ink, lw=1, ls=":", label="No skill")

    def pr(ax, d):
        for m, (label, color) in series.items():
            g = first_repeat(d, m)
            prec, rec, _ = precision_recall_curve(g.y, g[POOLED])
            ax.plot(rec, prec, color=color, label=label)
        ax.axhline(first_repeat(d, "xgboost").y.mean(), color=ink, lw=1, ls=":", label="Event rate")
        ax.set_ylim(0, .5)

    def calib(ax, d):
        top = 0
        for m, color in (("isotonic", blue), ("sigmoid", aqua)):
            g = val[d]["xgboost"]
            g = g[g.repeat == 0]
            b = g.groupby(pd.qcut(g[f"p_{m}"], 10, duplicates="drop"), observed=True)[[f"p_{m}", "y"]].mean()
            ax.plot(b[f"p_{m}"], b.y, "o-", color=color, ms=4, label=m.capitalize())
            top = max(top, b.to_numpy().max())
        ax.plot([0, top], [0, top], color=ink, lw=1, ls=":", label="Perfect")

    def decision(ax, d):
        t = dca[d]
        for m, (label, color) in series.items():
            ax.plot(t.threshold, t[m], color=color, label=label)
        ax.plot(t.threshold, t.treat_all, color=ink, lw=1, ls="--", label="Treat all")
        ax.plot(t.threshold, t.treat_none, color=ink, lw=1, ls=":", label="Treat none")
        top = t[["xgboost", "logistic", "treat_all"]].max().max()
        ax.set_ylim(-.15 * top, 1.1 * top)

    panels("roc", "ROC, out-of-fold on train, repeat 1 of 3", roc, "1 - specificity", "Sensitivity")
    panels("pr", "Precision-recall, out-of-fold on train, repeat 1 of 3", pr, "Recall", "Precision")
    panels("calibration", "XGBoost calibration, cross-fitted on validation, tenths of predicted risk", calib, "Predicted risk", "Observed rate")
    panels("decision", "Decision curve, out-of-fold on train, mean of 3 repeats", decision, "Threshold probability", "Net benefit")


def run_grid(out, tasks=None, folds=None):
    """Candidate (a) alone: the pick from repeats 1 and 2, and its score on repeat 3."""
    out.mkdir(parents=True, exist_ok=True)
    params, scores, grid = select_grid(tasks, folds)
    grid.to_csv(out / "grid.csv", index=False)
    pd.Series({d: json.dumps(p) for d, p in params.items()}).to_csv(out / "grid_choice.csv", header=False)
    rows = []
    for d, g in scores.groupby("disease", sort=False):
        held = g[g.repeat == SCORE_REPEAT]
        lo, hi = ci(boot_means(held, ap, N_BOOT, SEED))
        rows.append({"disease": d, "tune_auprc": mean_of_folds(g[g.repeat != SCORE_REPEAT], ap), "repeat3_auprc": mean_of_folds(held, ap),
                     "lo": lo, "hi": hi, "trees": trees(g)})
    pd.DataFrame(rows).to_csv(out / "grid_repeat3.csv", index=False)
    return params, scores


def run(out):
    out.mkdir(parents=True, exist_ok=True)
    say = lambda *a: print(pd.Timestamp.now().strftime("%H:%M:%S"), *a, flush=True)  # noqa: E731
    tasks, folds = load_tasks(), load_folds()
    say("grid")
    params, a_scores = run_grid(out, tasks, folds)
    say("compare (a) with itself:", compare_candidates(a_scores, a_scores, b_keeps_constraints=True, n_boot=20, folds=folds)["reason"])
    perf, dl, cal, cuts, sub, eo, cv, val, dca, gap = [], [], [], [], [], [], {}, {}, {}, []
    for d, t in tasks.items():
        say(d, params[d])
        no = t.without(f"fhrs_{d.lower()}")
        cv[d] = {"xgboost": cv_predict(t, folds, lambda a, b: t.fit_xgb(a, b, params[d]), METHODS),
                 "logistic": cv_predict(t, folds, lambda a, b: t.fit_logistic((pd.concat([a[0], b[0]]), np.r_[a[1], b[1]], np.r_[a[2], b[2]])), METHODS),
                 "no_fhrs": cv_predict(no, folds, lambda a, b: no.fit_xgb(a, b, params[d]), METHODS)}
        final = {"xgboost": t.fit_final(params[d], trees(cv[d]["xgboost"])), "logistic": t.fit_logistic(t.train)}
        val[d] = {m: crossfit(t, f) for m, f in final.items()}
        v = t.val[1]
        raw = {**{m: f.predict_proba(t.val[0])[:, 1] for m, f in final.items()},
               "no_fhrs": no.fit_final(params[d], trees(cv[d]["no_fhrs"])).predict_proba(no.val[0])[:, 1]}

        say(d, "performance")
        for m in ("xgboost", "logistic"):
            row = {"disease": d, "model": m, "train_rows": len(t.train[1]), "train_events": int(t.train[1].sum()),
                   "val_rows": len(v), "val_events": int(v.sum()), "trees": trees(cv[d][m])}
            for name, fn in (("auprc", ap), ("auroc", auroc)):
                row[f"cv_{name}"] = mean_of_folds(cv[d][m], fn)
                row[f"cv_{name}_lo"], row[f"cv_{name}_hi"] = ci(boot_means(cv[d][m], fn, N_BOOT, SEED))
                row[f"val_{name}"] = fn(v, raw[m])
                row[f"val_{name}_lo"], row[f"val_{name}_hi"] = ci(boot_means(val[d][m], fn, N_BOOT, SEED, ("repeat",)))
            perf.append(row)

        for label, x, other in (("XGBoost vs logistic", "xgboost", "logistic"), ("with vs without FHRS", "xgboost", "no_fhrs")):
            r = delong(v, raw[x], raw[other])
            dl.append({"disease": d, "comparison": label, "data": "validation", "auc_first": r["auc_a"], "auc_second": r["auc_b"],
                       "p": r["p"], "p_low": r["p"], "p_high": r["p"]})
            pairs = [delong(a.sort_values("patient_id").y, a.sort_values("patient_id")[POOLED], b.sort_values("patient_id")[POOLED])
                     for (_, a), (_, b) in zip(cv[d][x].groupby("repeat"), cv[d][other].groupby("repeat"))]
            ps = [r["p"] for r in pairs]
            dl.append({"disease": d, "comparison": label, "data": "repeated CV", "auc_first": np.mean([r["auc_a"] for r in pairs]),
                       "auc_second": np.mean([r["auc_b"] for r in pairs]), "p": float(np.median(ps)), "p_low": min(ps), "p_high": max(ps)})

        say(d, "calibration")
        pick = {}
        for m in ("xgboost", "logistic"):
            t_cal = calibration_rows(val[d][m]).assign(disease=d, model=m)
            pick[m] = t_cal.method[t_cal["pick"]].iloc[0]
            cal.append(t_cal)

        say(d, "cut-offs, fairness")
        X = pd.concat([t.train[0], t.val[0]])
        demo = pd.DataFrame({"patient_id": np.r_[t.train[2], t.val[2]], "sex": X.sex.map({1: "Male", 0: "Female"}).to_numpy(),
                             "age_band": age_band(X.age)})
        for source, frame, rank in (("repeated CV", cv[d]["xgboost"], POOLED), ("validation", val[d]["xgboost"], "score")):
            col = f"p_{pick['xgboost']}"
            cuts.append({"disease": d, "data": source, "method": pick["xgboost"], **cut_rows(frame, col)})
            s, e = fairness_rows(frame, demo, col, rank)
            sub.append(s.assign(disease=d, data=source))
            eo.append(e.assign(disease=d, data=source))

        dca[d] = decision_rows({m: (cv[d][m], f"p_{pick[m]}") for m in ("xgboost", "logistic")}).assign(disease=d, data="repeated CV")
        dca[f"{d} val"] = decision_rows({m: (val[d][m], f"p_{pick[m]}") for m in ("xgboost", "logistic")}).assign(disease=d, data="validation")
        gap.append({"disease": d, "cv_auroc_xgboost": mean_of_folds(cv[d]["xgboost"], auroc), "val_auroc_xgboost": auroc(v, raw["xgboost"]),
                    "cv_auroc_logistic": mean_of_folds(cv[d]["logistic"], auroc), "val_auroc_logistic": auroc(v, raw["logistic"])})

    say("oracle")
    oracle = oracle_rows(tasks).merge(pd.DataFrame(gap), on="disease")
    for name, table in (("performance", pd.DataFrame(perf)), ("delong", pd.DataFrame(dl)), ("calibration", pd.concat(cal)),
                        ("cuts", pd.DataFrame(cuts)), ("subgroups", pd.concat(sub)), ("equalized_odds", pd.concat(eo)),
                        ("decision", pd.concat(dca.values())), ("oracle", oracle)):
        table.to_csv(out / f"{name}.csv", index=False)
    plots(out, cv, val, dca)
    say("done")


if __name__ == "__main__":
    if sys.argv[1] == "splits":
        write_folds()
    elif sys.argv[1] == "grid":
        run_grid(Path(sys.argv[2]))
    else:
        run(Path(sys.argv[2]))
