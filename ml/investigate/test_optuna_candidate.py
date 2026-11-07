"""Tests for candidate (b), Optuna on the shared folds (#52).
Run: uv run --group ml python -m pytest -q ml/investigate/test_optuna_candidate.py"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("optuna")            # "ml" dependency group
pytest.importorskip("fairlearn")
sys.path.insert(0, str(Path(__file__).parent))
import evaluation_protocol as ep  # noqa: E402
import optuna  # noqa: E402
import optuna_candidate as oc  # noqa: E402

TRIALS = 6


def toy_rows(n=300, seed=0, start=0):
    rng = np.random.default_rng(seed)
    X = pd.DataFrame({"a": rng.normal(size=n), "b": rng.normal(size=n)})
    y = (rng.random(n) < 1 / (1 + np.exp(1.5 - 1.2 * X.a))).astype(int)
    return X, y, np.arange(start, start + n)


def toy():
    t = ep.Task("CVD", toy_rows(300), toy_rows(120, seed=1, start=300), [1, 0])
    return t, ep.make_folds(t.train[2], t.train[1]).assign(disease="CVD")


def spied(monkeypatch, t):
    """Records the patient ids of every fit's training rows and early-stopping rows."""
    seen, real = [], t.fit_xgb
    monkeypatch.setattr(t, "fit_xgb", lambda fit, stop, p: seen.append((set(fit[2]), set(stop[2]))) or real(fit, stop, p))
    return seen


def test_trials_use_the_tuning_repeats_only(monkeypatch):
    t, folds = toy()
    tune_steps = [(set(f[2]), set(s[2])) for _, _, f, s, _ in ep.shared_folds(t, folds, ep.TUNE_REPEATS)]
    repeat_3 = [(set(f[2]), set(s[2])) for _, _, f, s, _ in ep.shared_folds(t, folds, (ep.SCORE_REPEAT,))]
    seen = spied(monkeypatch, t)
    oc.tune(t, folds, TRIALS)
    assert seen and all(step in tune_steps for step in seen)
    assert not any(step in repeat_3 for step in seen)


def test_early_stopping_never_uses_the_scored_fold(monkeypatch):
    t, folds = toy()
    scored = [set(s[2]) for *_, s in ep.shared_folds(t, folds, ep.TUNE_REPEATS)]
    seen = spied(monkeypatch, t)
    oc.tune(t, folds, 1)
    assert len(seen) == len(scored)
    for (fit, stop), held in zip(seen, scored):
        assert not stop & held and not fit & held and not fit & stop


def test_the_score_is_the_mean_auprc_of_the_tuning_folds():
    t, folds = toy()
    _, study = oc.tune(t, folds, 1)
    scores = ep.cv_predict(t, folds, lambda a, b: t.fit_xgb(a, b, oc.DEFAULT), repeats=ep.TUNE_REPEATS)
    assert study.trials[0].value == pytest.approx(ep.mean_of_folds(scores, ep.ap))
    assert study.trials[0].user_attrs["n_trees"] == ep.trees(scores)


def test_trial_1_is_the_old_default_and_the_best_is_not_worse():
    t, folds = toy()
    params, study = oc.tune(t, folds, TRIALS)
    assert study.trials[0].params == oc.DEFAULT
    assert study.best_value >= study.trials[0].value
    for k, (_, lo, hi) in oc.SPACE.items():
        assert lo <= params[k] <= hi
    assert isinstance(params["max_depth"], int)


def test_tpe_with_a_median_pruner_and_a_fixed_seed():
    t, folds = toy()
    _, study = oc.tune(t, folds, 2)
    assert isinstance(study.sampler, optuna.samplers.TPESampler)
    assert isinstance(study.pruner, optuna.pruners.MedianPruner)
    a, b = oc.tune(t, folds, TRIALS)[1], oc.tune(t, folds, TRIALS)[1]
    assert [x.params for x in a.trials] == [x.params for x in b.trials]


def test_a_bad_trial_is_pruned(monkeypatch):
    # After the startup trials, a trial whose running AUPRC is far below the median stops early
    t, folds = toy()
    real = t.fit_xgb

    def worse_for_deep_trees(fit, stop, p):
        model = real(fit, stop, p)
        if p["max_depth"] == 6:                      # a "bad" setting: scores are made useless
            model.predict_proba = lambda X: np.tile([.5, .5], (len(X), 1))
        return model
    monkeypatch.setattr(t, "fit_xgb", worse_for_deep_trees)
    study = optuna.create_study(direction="maximize", pruner=optuna.pruners.MedianPruner(2, oc.WARMUP))
    monkeypatch.setattr(optuna, "create_study", lambda **kw: study)
    for depth in (3, 3, 6):
        study.enqueue_trial({**oc.DEFAULT, "max_depth": depth})
    oc.tune(t, folds, 3)
    assert study.trials[2].state == optuna.trial.TrialState.PRUNED


def test_scores_for_compare_candidates_are_on_the_shared_splits():
    t, folds = toy()
    best, scores, studies = oc.select_optuna({"CVD": t}, folds, TRIALS)
    assert sorted(scores.repeat.unique()) == [0, 1, 2]
    ep.on_shared_splits(scores[scores.repeat == ep.SCORE_REPEAT], folds)
    result = ep.compare_candidates(scores, scores, b_keeps_constraints=True, n_boot=20, folds=folds)
    assert result["table"]["diff"].iloc[0] == 0


def test_the_refit_keeps_constraints_and_treeshap():
    t, folds = toy()
    best, scores, _ = oc.select_optuna({"CVD": t}, folds, 2)
    assert oc.keeps_constraints({"CVD": t}, best, scores)


def test_the_notebook_tunes_with_this_code():
    nb = ep.notebook()
    assert nb["oc"] is oc
    assert "GRID" not in nb                                  # the grid now lives only in evaluation_protocol
    assert (nb["MAX_TREES"], nb["EARLY"]) == (ep.MAX_TREES, ep.EARLY)
