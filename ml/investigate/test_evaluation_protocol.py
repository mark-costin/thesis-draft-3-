"""Tests for the shared evaluation protocol. Hand-worked numbers are in the comments.
Run: uv run --group ml python -m pytest -q ml/investigate/test_evaluation_protocol.py"""
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("fairlearn")          # "ml" dependency group
pytest.importorskip("matplotlib")
sys.path.insert(0, str(Path(__file__).parent))
import evaluation_protocol as ep  # noqa: E402


# ---------- DeLong ----------
def test_delong_hand_example():
    # positives A .9 .7 .4, B .8 .5 .3; negatives A .6 .3 .2, B .6 .4 .1
    # AUC A = 8/9, AUC B = 2/3. Placement values give var(A - B) = 1/81 + 1/81 = 2/81,
    # so z = (2/9) / (sqrt(2) / 9) = sqrt(2) and p = erfc(1).
    y = np.array([1, 1, 1, 0, 0, 0])
    a = np.array([.9, .7, .4, .6, .3, .2])
    b = np.array([.8, .5, .3, .6, .4, .1])
    r = ep.delong(y, a, b)
    assert r["auc_a"] == pytest.approx(8 / 9)
    assert r["auc_b"] == pytest.approx(2 / 3)
    assert r["z"] == pytest.approx(math.sqrt(2))
    assert r["p"] == pytest.approx(math.erfc(1))


def test_delong_same_model_gives_p_1():
    y = np.array([1, 1, 1, 0, 0, 0])
    a = np.array([.9, .7, .4, .6, .3, .2])
    assert ep.delong(y, a, a)["p"] == 1.0


def test_delong_counts_ties_as_half():
    # one positive and one negative tie: AUC = (1 + 0.5 + 1 + 1) / 4
    y = np.array([1, 1, 0, 0])
    a = np.array([.9, .5, .5, .1])
    assert ep.delong(y, a, a)["auc_a"] == pytest.approx(3.5 / 4)


# ---------- ECE ----------
def test_ece_hand_example():
    # bin [0, .1): conf .05, rate .5, gap .45; bin [.1, .2): gap .15; bin [.9, 1]: gap .05
    # each bin holds 2 of 6 rows: (.45 + .15 + .05) / 3
    p = np.array([.05, .05, .15, .15, .95, .95])
    y = np.array([0, 1, 0, 0, 1, 1])
    assert ep.ece(y, p) == pytest.approx(.65 / 3)


def test_ece_keeps_exact_0_and_1_in_the_end_bins():
    assert ep.ece(np.array([0, 1]), np.array([0.0, 1.0])) == 0
    assert ep.ece(np.array([1, 0]), np.array([0.0, 1.0])) == 1


# ---------- calibration slope ----------
def group(p, n, events):
    return np.full(n, p), np.r_[np.ones(events, int), np.zeros(n - events, int)]


def calibrated_rows():
    parts = [group(.1, 100, 10), group(.5, 100, 50), group(.9, 100, 90)]
    return np.concatenate([a for a, _ in parts]), np.concatenate([b for _, b in parts])


def test_slope_is_1_when_predictions_match_the_rates():
    p, y = calibrated_rows()
    r = ep.calibration_slope(y, p)
    assert r["slope"] == pytest.approx(1, abs=1e-4)
    assert r["excluded"] == 0 and r["excluded_events"] == 0


def test_slope_is_one_half_when_the_logits_are_doubled():
    p, y = calibrated_rows()
    doubled = 1 / (1 + np.exp(-2 * np.log(p / (1 - p))))
    assert ep.calibration_slope(y, doubled)["slope"] == pytest.approx(.5, abs=1e-4)


def test_exact_zero_rows_do_not_move_the_slope():
    # the T2D case: 54 rows at exactly p = 0 hold 4 events
    p, y = calibrated_rows()
    zp, zy = group(0.0, 54, 4)
    r = ep.calibration_slope(np.r_[y, zy], np.r_[p, zp])
    assert r["slope"] == pytest.approx(1, abs=1e-4)
    assert r["excluded"] == 54 and r["excluded_events"] == 4
    # the old clip turns the same rows into a far lower slope
    pc = np.clip(np.r_[p, zp], 1e-6, 1 - 1e-6)
    from sklearn.linear_model import LogisticRegression
    old = LogisticRegression(C=1e6, max_iter=1000).fit(np.log(pc / (1 - pc))[:, None], np.r_[y, zy]).coef_[0, 0]
    assert old < .7


def test_slope_is_nan_when_predictions_do_not_vary():
    assert math.isnan(ep.calibration_slope(np.array([0, 1, 0]), np.full(3, .3))["slope"])


# ---------- net benefit ----------
def test_net_benefit_hand_example():
    # 10 people, 2 events. At 0.20: treated = the 2 events + 1 non-event.
    # 2/10 - 1/10 * (.2 / .8) = .175
    y = np.array([1, 1, 0, 0, 0, 0, 0, 0, 0, 0])
    p = np.array([.6, .3, .4, .1, .1, .1, .1, .1, .1, .1])
    assert ep.net_benefit(y, p, .20) == pytest.approx(.175)


def test_treat_all_and_treat_none():
    y = np.array([1, 1, 0, 0, 0, 0, 0, 0, 0, 0])
    assert ep.net_benefit(y, np.ones(10), .20) == pytest.approx(0)             # .2 - .8 * .25
    assert ep.net_benefit(y, np.ones(10), .10) == pytest.approx(.2 - .8 / 9)
    assert ep.net_benefit(y, np.zeros(10), .20) == 0                           # nobody treated
    assert ep.treat_all(y, .10) == pytest.approx(.2 - .8 / 9)


# ---------- fairness ----------
Y = np.array([0, 0, 1, 1, 0, 0, 1, 1])
P = np.array([.1, .3, .2, .4, .1, .2, .3, .4])
SEX = np.array(["M"] * 4 + ["F"] * 4)


def test_subgroup_auroc():
    # M: positives .2 and .4 beat 3 of the 4 pairs. F: all 4 pairs.
    r = ep.subgroup_auroc(Y, P, SEX)
    assert r["M"] == pytest.approx(.75) and r["F"] == 1


def test_equalized_odds_at_a_cut_off():
    # cut .25: M has TPR .5, FPR .5; F has TPR 1, FPR 0, so the worst gap is .5
    assert ep.equalized_odds(Y, P, SEX, .25) == pytest.approx(.5)
    # cut .35: both groups have TPR .5, FPR 0
    assert ep.equalized_odds(Y, P, SEX, .35) == 0


def test_equalized_odds_is_nan_when_nobody_is_flagged():
    # all-Low must not read as "perfectly fair"
    assert math.isnan(ep.equalized_odds(Y, P, SEX, .50))


def test_equalized_odds_leaves_out_a_group_with_one_class():
    y = np.r_[Y, 0, 0]
    p = np.r_[P, .9, .9]
    s = np.concatenate([SEX, ["X", "X"]])                              # group X has no events
    assert ep.equalized_odds(y, p, s, .25) == pytest.approx(.5)


def test_age_bands():
    assert list(ep.age_band(np.array([18, 39, 40, 59, 60, 100]))) == ["18-39", "18-39", "40-59", "40-59", "60+", "60+"]


# ---------- leakage guard ----------
def toy_rows(n=300, seed=0, start=0):
    rng = np.random.default_rng(seed)
    X = pd.DataFrame({"a": rng.normal(size=n), "b": rng.normal(size=n)})
    y = (rng.random(n) < 1 / (1 + np.exp(1.5 - 1.2 * X.a))).astype(int)
    return X, y, np.arange(start, start + n)


def toy_task():
    return ep.Task("CVD", toy_rows(300), toy_rows(120, seed=1, start=300), [1, 0])


def test_guard_allows_train_and_validation():
    t = toy_task()
    t.check(t.train)
    t.check(t.val)


def test_guard_raises_on_a_test_row():
    t = toy_task()
    with pytest.raises(ep.LeakError):
        t.check(toy_rows(5, seed=2, start=420))


def test_guard_raises_on_a_seed_2_row_with_a_shared_patient_id():
    # seed 1 and seed 2 use the same id numbers: an id check alone would let this row through
    t = toy_task()
    X, y, pid = t.train
    with pytest.raises(ep.LeakError):
        t.check((X.iloc[:5] + 1, y[:5], pid[:5]))


def test_guard_stops_every_fit_step():
    t = toy_task()
    bad = toy_rows(40, seed=2, start=420)
    with pytest.raises(ep.LeakError):
        t.fit_xgb(bad, t.val, {"max_depth": 2})
    with pytest.raises(ep.LeakError):
        t.fit_xgb(t.train, bad, {"max_depth": 2})
    with pytest.raises(ep.LeakError):
        t.fit_logistic(bad)
    with pytest.raises(ep.LeakError):
        t.calibrator(None, bad, "sigmoid")


def test_guard_raises_on_the_real_test_split():
    import auroc_ceiling as ac
    tasks = ep.load_tasks()
    test_ids, _ = ac.test_split(ep.DATA, "NEURO")
    assert not set(test_ids) & (set(tasks["NEURO"].train[2]) | set(tasks["NEURO"].val[2]))
    full = ep.notebook()["load_tables"](ep.DATA, ep.notebook()["SPEC"])["NEURO"]
    i = np.isin(full[2], test_ids)
    with pytest.raises(ep.LeakError):
        tasks["NEURO"].check((full[0][i], full[1][i], full[2][i]))


# ---------- shared splits ----------
def toy_labels():
    return np.arange(1000, 1200), np.r_[np.ones(40, int), np.zeros(160, int)]


def test_same_seed_gives_identical_folds():
    pd.testing.assert_frame_equal(ep.make_folds(*toy_labels()), ep.make_folds(*toy_labels()))


def test_other_seed_gives_other_folds():
    assert not ep.make_folds(*toy_labels()).equals(ep.make_folds(*toy_labels(), seed=7))


def test_each_patient_is_in_one_fold_per_repeat_and_folds_are_stratified():
    pid, y = toy_labels()
    f = ep.make_folds(pid, y)
    assert f.patient_id.is_unique and set(f.patient_id) == set(pid)        # one row = one patient
    for r in range(ep.REPEATS):
        assert set(f[f"fold_{r}"]) == set(range(ep.FOLDS))
        assert (pd.Series(y)[f[f"fold_{r}"] == 0].sum()) == 8               # 40 events / 5 folds
        assert all(y[f[f"fold_{r}"].to_numpy() == k].sum() == 8 for k in range(ep.FOLDS))
        assert all((f[f"fold_{r}"] == k).sum() == 40 for k in range(ep.FOLDS))


def test_repeats_differ():
    f = ep.make_folds(*toy_labels())
    assert not f.fold_0.equals(f.fold_1) and not f.fold_1.equals(f.fold_2)


def test_stopping_rows_are_one_fifth_of_each_fold_and_stratified():
    pid, y = toy_labels()
    f = ep.make_folds(pid, y)
    for r in range(ep.REPEATS):
        for k in range(ep.FOLDS):
            part = (f[f"fold_{r}"] == k).to_numpy()
            stop = f[f"stop_{r}"].to_numpy()[part].astype(bool)
            assert stop.sum() == 8 and y[part][stop].sum() in (1, 2)


def test_saved_file_is_what_make_folds_gives_and_holds_no_test_or_value():
    import auroc_ceiling as ac
    saved = ep.load_folds()
    assert set(saved.columns) == {"disease", "patient_id"} | {f"{c}_{r}" for c in ("fold", "stop") for r in range(ep.REPEATS)}
    tasks = ep.load_tasks()
    for d, t in tasks.items():
        got = saved[saved.disease == d].drop(columns="disease").reset_index(drop=True)
        pd.testing.assert_frame_equal(got, ep.make_folds(t.train[2], t.train[1]))
        assert not set(got.patient_id) & set(ac.test_split(ep.DATA, d)[0])
        assert not set(got.patient_id) & set(t.val[2])


# ---------- early stopping (P6-15) ----------
def test_stopping_rows_are_held_out_and_never_scored_in_the_same_step():
    t = toy_task()
    folds = ep.make_folds(t.train[2], t.train[1]).assign(disease="CVD")
    seen = []

    def spy(fit, stop):
        seen.append((set(fit[2]), set(stop[2])))
        return t.fit_xgb(fit, stop, {"max_depth": 2, "learning_rate": .1, "min_child_weight": 1})

    scores = ep.cv_predict(t, folds, spy)
    assert len(seen) == ep.REPEATS * ep.FOLDS
    for (fit, stop), (_, g) in zip(seen, scores.groupby(["repeat", "fold"], sort=True)):
        scored = set(g.patient_id)
        assert not fit & stop and not fit & scored and not stop & scored
        assert fit | stop | scored == set(t.train[2])
    assert (scores.groupby("repeat").patient_id.nunique() == 300).all()


def test_scale_pos_weight_can_be_set_below_the_default():
    t = toy_task()
    m = t.fit_xgb(t.train, t.val, {"max_depth": 2, "scale_pos_weight": .5})
    assert m.get_params()["scale_pos_weight"] == .5


def test_same_seed_gives_identical_results_twice():
    def once():
        t = toy_task()
        folds = ep.make_folds(t.train[2], t.train[1]).assign(disease="CVD")
        return ep.cv_predict(t, folds, lambda a, b: t.fit_xgb(a, b, {"max_depth": 2}), ["sigmoid"])
    a, b = once(), once()
    pd.testing.assert_frame_equal(a, b)
    assert (ep.boot_means(a, ep.ap, 20, 1) == ep.boot_means(b, ep.ap, 20, 1)).all()


def test_grid_and_fixed_settings_match_the_notebook():
    nb = ep.notebook()
    if "GRID" not in nb:
        pytest.skip("this checkout's notebook has no grid (#52 replaces it)")
    assert ep.GRID == nb["GRID"]
    m = nb["make_gbm"](ep.GRID[0], 10, 1.0, [0, 0])
    for k, v in ep.XGB_FIXED.items():
        assert m.get_params()[k] == v, k
    assert (ep.MAX_TREES, ep.EARLY) == (nb["MAX_TREES"], nb["EARLY"])


# ---------- bootstrap ----------
def toy_scores(signal, n=300, seed=0, disease="CVD"):
    rng = np.random.default_rng(seed)
    y = np.r_[np.ones(n // 3, int), np.zeros(n - n // 3, int)]
    s = np.random.default_rng(99).normal(size=n) + signal * y
    del rng
    pid = np.arange(n)
    return pd.concat([pd.DataFrame({"disease": disease, "repeat": r, "fold": (pid + r) % 5, "patient_id": pid,
                                    "y": y, "score": s}) for r in range(2)], ignore_index=True)


def test_mean_of_folds_uses_every_fold():
    f = toy_scores(1.0)
    from sklearn.metrics import average_precision_score as aps
    want = np.mean([aps(g.y, g.score) for _, g in f.groupby(["repeat", "fold"])])
    assert ep.mean_of_folds(f, ep.ap) == pytest.approx(want)


def test_bootstrap_is_paired_so_equal_candidates_differ_by_zero():
    f = toy_scores(1.0)
    d = ep.boot_means(f, ep.ap, 30, 1) - ep.boot_means(f.copy(), ep.ap, 30, 1)
    assert (d == 0).all()


def test_bootstrap_resample_with_weights_equals_a_real_resample():
    f = toy_scores(1.0)
    one = f[(f.repeat == 0)]
    w = np.bincount(np.random.default_rng(3).integers(0, 300, 300), minlength=300)
    from sklearn.metrics import average_precision_score as aps
    rep = np.repeat(np.arange(300), w)
    assert ep.ap(one.y.to_numpy(), one.score.to_numpy(), w) == pytest.approx(aps(one.y.to_numpy()[rep], one.score.to_numpy()[rep]))


# ---------- compare_candidates ----------
DISEASES = ["CVD", "T2D", "CRD", "NEURO"]


def frames(strong):
    """a is weak everywhere. b = a, except in the `strong` diseases, where b is clearly better."""
    a = pd.concat([toy_scores(.3, disease=d) for d in DISEASES], ignore_index=True)
    b = pd.concat([toy_scores(2.5 if d in strong else .3, disease=d) for d in DISEASES], ignore_index=True)
    return a, b


def folds_for(a):
    w = a.pivot(index=["disease", "patient_id"], columns="repeat", values="fold").add_prefix("fold_").reset_index()
    return w


def test_better_in_3_of_4_picks_b():
    a, b = frames(DISEASES[:3])
    r = ep.compare_candidates(a, b, b_keeps_constraints=True, n_boot=200, folds=folds_for(a))
    assert r["choice"] == "b" and r["wins"] == 3
    assert (r["table"].set_index("disease").loc["NEURO", ["lo", "hi"]] == 0).all()


def test_better_in_2_of_4_picks_a():
    a, b = frames(DISEASES[:2])
    r = ep.compare_candidates(a, b, b_keeps_constraints=True, n_boot=200, folds=folds_for(a))
    assert r["choice"] == "a" and r["wins"] == 2


def test_3_of_4_without_constraints_or_shap_picks_a():
    a, b = frames(DISEASES[:3])
    assert ep.compare_candidates(a, b, n_boot=100, folds=folds_for(a))["choice"] == "a"


def test_clearly_worse_does_not_count_as_excluding_zero():
    b, a = frames(DISEASES[:3])                        # swapped: b is worse in 3 of 4
    r = ep.compare_candidates(a, b, b_keeps_constraints=True, n_boot=200, folds=folds_for(a))
    assert (r["table"].hi < 0).sum() == 3 and r["wins"] == 0 and r["choice"] == "a"


def test_compare_refuses_other_splits():
    a, b = frames(DISEASES[:3])
    with pytest.raises(ep.LeakError):
        ep.compare_candidates(a, b.assign(fold=(b.fold + 1) % 5), n_boot=10, folds=folds_for(a))


def test_compare_refuses_rows_outside_the_shared_splits():
    a, b = frames(DISEASES[:3])
    f = folds_for(a)
    with pytest.raises(ep.LeakError):
        ep.compare_candidates(a, b, n_boot=10, folds=f[f.patient_id != 7])


def test_confirm_on_validation():
    def val(signal, d):
        return toy_scores(signal, disease=d)[lambda f: f.repeat == 0][["disease", "patient_id", "y", "score"]]
    a = pd.concat([val(.3, d) for d in DISEASES], ignore_index=True)
    b3 = pd.concat([val(2.5 if d != "NEURO" else .3, d) for d in DISEASES], ignore_index=True)
    b2 = pd.concat([val(2.5 if d in ("CVD", "T2D") else .3, d) for d in DISEASES], ignore_index=True)
    assert ep.confirm_on_validation(a, b3)["confirmed"] is True
    assert ep.confirm_on_validation(a, b2)["confirmed"] is False


def test_keeps_monotone_and_treeshap():
    t = toy_task()
    good = t.fit_xgb(t.train, t.val, {"max_depth": 2})
    assert ep.keeps_monotone_and_treeshap(good, [1, 0], t.train[0])
    assert not ep.keeps_monotone_and_treeshap(good, [1, 1], t.train[0])
    dropped = ep.Task("CVD", t.train, t.val, [0, 0]).fit_xgb(t.train, t.val, {"max_depth": 2})
    assert not ep.keeps_monotone_and_treeshap(dropped, [1, 0], t.train[0])


def test_shared_folds_gives_the_15_steps_of_cv_predict():
    # #52's pruner needs a score after each fold, so it walks these steps itself
    t = toy_task()
    folds = ep.make_folds(t.train[2], t.train[1]).assign(disease="CVD")
    steps = list(ep.shared_folds(t, folds))
    assert [(r, k) for r, k, *_ in steps] == [(r, k) for r in range(ep.REPEATS) for k in range(ep.FOLDS)]
    params = {"max_depth": 2}
    scores = ep.cv_predict(t, folds, lambda a, b: t.fit_xgb(a, b, params))
    r, k, fit, stop, scored = steps[7]
    got = scores[(scores.repeat == r) & (scores.fold == k)]
    assert list(got.patient_id) == list(scored[2])
    assert np.allclose(got.score, t.fit_xgb(fit, stop, params).predict_proba(scored[0])[:, 1])
