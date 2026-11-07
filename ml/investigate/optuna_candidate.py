"""Candidate (b) of the evaluation protocol: XGBoost tuned with Optuna (#52, G7).

The same rules as candidate (a), the 12-point grid (evaluation_protocol.py):
- the shared splits (shared_splits.csv): 3 repeats x 5 folds of train, the same patients in every fold;
- settings are chosen on repeats 1 and 2 only (TUNE_REPEATS); repeat 3 only scores the pick (compare_candidates);
- early stopping uses the stopping rows held out from each training fold, never the scored fold;
- the score is AUPRC only, the mean over folds;
- every fit goes through Task.check(), so the test split and seed 2 never reach a fit.

The search: TPE sampler (fixed seed) with a MedianPruner, N_TRIALS trials per disease. Trial 1 is the notebook's
old default, so the best trial can never score below it on the tuning repeats. The pruner stops a trial after a
fold when its running AUPRC is below the median of earlier trials at the same fold (not before one full repeat).

train_models.ipynb Cell 4 runs tune() on its own train (same make_folds rule), so the final model is tuned the
way this candidate was judged.

Tests: test_optuna_candidate.py
Comparison: uv run --group ml python ml/investigate/optuna_candidate.py <output folder> [trials]"""
import json
import sys
from pathlib import Path

import numpy as np
import optuna
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import evaluation_protocol as ep  # noqa: E402

N_TRIALS = 120                       # 100 to 150 agreed; about 1 to 2 hours for all four on a laptop
WARMUP = ep.FOLDS                    # no pruning before one full repeat (5 folds) has been scored
STARTUP = 10                         # nor before 10 trials have finished
# The search space. scale_pos_weight stays at the class ratio (Task.xgb), as in the grid.
SPACE = {
    "max_depth": ("int", 2, 6),
    "learning_rate": ("log", 0.01, 0.3),
    "min_child_weight": ("log", 1.0, 30.0),
    "subsample": ("float", 0.6, 1.0),
    "colsample_bytree": ("float", 0.6, 1.0),
    "reg_lambda": ("log", 0.1, 10.0),
    "gamma": ("float", 0.0, 5.0),
}
# The notebook's old default (depth 3, learning rate 0.1, the subsample and colsample in XGB_FIXED)
DEFAULT = {"max_depth": 3, "learning_rate": 0.1, "min_child_weight": 1.0, "subsample": 0.8,
           "colsample_bytree": 0.8, "reg_lambda": 1.0, "gamma": 0.0}
optuna.logging.set_verbosity(optuna.logging.WARNING)


def suggest(trial):
    out = {}
    for name, (kind, lo, hi) in SPACE.items():
        if kind == "int":
            out[name] = trial.suggest_int(name, lo, hi)
        else:
            out[name] = trial.suggest_float(name, lo, hi, log=kind == "log")
    return out


def as_params(values):
    """Optuna's values -> XGBoost settings (max_depth as int)."""
    return {k: int(v) if SPACE[k][0] == "int" else float(v) for k, v in values.items()}


def tune(task, folds, n_trials=N_TRIALS, seed=ep.SEED):
    """Optuna on the tuning repeats of the shared folds. Returns (best settings, the study).
    The best trial's user_attrs["n_trees"] is the mean early-stopping tree count over those folds (= ep.trees)."""
    steps = list(ep.shared_folds(task, folds, ep.TUNE_REPEATS))

    def objective(trial):
        params, scores, trees = suggest(trial), [], []
        for i, (_, _, fit, stop, (X, y, _)) in enumerate(steps):
            model = task.fit_xgb(fit, stop, params)          # stops on held-out rows, never on the scored fold
            scores.append(ep.ap(y, model.predict_proba(X)[:, 1]))
            trees.append(model.best_iteration + 1)
            trial.report(float(np.mean(scores)), i)
            if trial.should_prune():
                raise optuna.TrialPruned()
        trial.set_user_attr("n_trees", int(np.mean(trees)))
        return float(np.mean(scores))

    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=seed),
        pruner=optuna.pruners.MedianPruner(n_startup_trials=STARTUP, n_warmup_steps=WARMUP),
        study_name=f"xgboost-{task.disease}")
    study.enqueue_trial(DEFAULT)
    study.optimize(objective, n_trials=n_trials)
    return as_params(study.best_trial.params), study


def select_optuna(tasks=None, folds=None, n_trials=N_TRIALS):
    """Candidate (b) for all four diseases: ({disease: settings}, out-of-fold scores on all 3 repeats, {disease: study}).
    Repeat 3 is fitted only with the pick, as select_grid does, so compare_candidates can judge it."""
    tasks, folds = tasks or ep.load_tasks(), ep.load_folds() if folds is None else folds
    best, scores, studies = {}, [], {}
    for d, t in tasks.items():
        best[d], studies[d] = tune(t, folds, n_trials)
        scores.append(ep.cv_predict(t, folds, lambda a, b, t=t, p=best[d]: t.fit_xgb(a, b, p)))
    return best, pd.concat(scores, ignore_index=True), studies


def keeps_constraints(tasks, params, scores):
    """The refit of every disease keeps its monotone constraints and exact TreeSHAP."""
    return all(ep.keeps_monotone_and_treeshap(t.fit_final(params[d], ep.trees(scores[scores.disease == d])), t.mono, t.val[0])
               for d, t in tasks.items())


def run(out, n_trials=N_TRIALS):
    """(a) grid against (b) Optuna on repeat 3, then the validation check. Writes CSVs into out."""
    out.mkdir(parents=True, exist_ok=True)
    say = lambda *a: print(pd.Timestamp.now().strftime("%H:%M:%S"), *a, flush=True)  # noqa: E731
    tasks, folds = ep.load_tasks(), ep.load_folds()
    say("(a) grid")
    a_params, a_scores = ep.run_grid(out, tasks, folds)
    say(f"(b) Optuna, {n_trials} trials per disease")
    b_params, b_scores, studies = select_optuna(tasks, folds, n_trials)
    for d, s in studies.items():
        s.trials_dataframe().to_csv(out / f"optuna_trials_{d}.csv", index=False)
    pd.Series({d: json.dumps(p) for d, p in b_params.items()}).to_csv(out / "optuna_choice.csv", header=False)
    pd.DataFrame([{"disease": d, "trials": len(s.trials),
                   "pruned": sum(t.state == optuna.trial.TrialState.PRUNED for t in s.trials),
                   "default_tune_auprc": s.trials[0].value, "best_tune_auprc": s.best_value,
                   "best_trial": s.best_trial.number + 1, "trees": s.best_trial.user_attrs["n_trees"]}
                  for d, s in studies.items()]).to_csv(out / "optuna_summary.csv", index=False)

    say("compare on repeat 3")
    keeps = keeps_constraints(tasks, b_params, b_scores)
    result = ep.compare_candidates(a_scores, b_scores, b_keeps_constraints=keeps, folds=folds)
    result["table"].to_csv(out / "compare_repeat3.csv", index=False)
    say("validation")
    confirm = ep.confirm_on_validation(ep.validation_scores(a_params, a_scores, tasks),
                                       ep.validation_scores(b_params, b_scores, tasks))
    confirm["table"].to_csv(out / "compare_validation.csv", index=False)
    decision = "b (Optuna)" if result["choice"] == "b" and confirm["confirmed"] else "a (grid)"
    (out / "decision.txt").write_text(
        f"Rule 4 (repeat 3): {result['reason']}; choice {result['choice']}\n"
        f"Validation: b above a in at least 3 of 4: {confirm['confirmed']}\n"
        f"Decision: {decision}\n", encoding="utf-8")
    say("done:", decision)
    return result, confirm


if __name__ == "__main__":
    run(Path(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else N_TRIALS)
