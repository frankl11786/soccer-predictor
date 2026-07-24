from __future__ import annotations

import math
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

import numpy as np

from .bayes import fit_model
from .data_prep import PreparedLeague, _season_strength_inputs
from .simulate import _score_probabilities


def temporal_holdout_backtest(
    prepared: PreparedLeague,
    league_key: str,
    *,
    holdout_matches: int = 80,
    steps: int = 750,
) -> dict[str, Any]:
    """Run a small, genuine time-ordered holdout check.

    The latest eligible matches are removed before fitting. The resulting state
    predicts those unseen matches without updating between them. This is not a
    complete historical season-by-season study, but it catches gross calibration
    failures during the nightly workflow without doubling total runtime.
    """
    if league_key != "epl":
        return {"status": "not_run", "reason": "Nightly holdout currently runs for EPL only."}

    current_set = set(prepared.current_team_ids)
    eligible = prepared.history[
        prepared.history.home_id.isin(current_set)
        & prepared.history.away_id.isin(current_set)
    ].sort_values("timestamp")
    if len(eligible) < max(50, holdout_matches + 80):
        return {
            "status": "not_run",
            "reason": f"Only {len(eligible)} eligible current-club historical matches were available.",
        }

    holdout = eligible.tail(holdout_matches).copy()
    cutoff_timestamp = holdout["timestamp"].min()
    training = prepared.history[prepared.history.timestamp < cutoff_timestamp].copy()
    if len(training) < 200:
        return {
            "status": "not_run",
            "reason": f"Only {len(training)} matches remained before the holdout cutoff.",
        }

    holdout_season = int(holdout["season"].mode().iloc[0])
    recent_season, recent_attack, recent_defense, recent_matches, current_matches = _season_strength_inputs(
        training.to_dict("records"),
        prepared.current_team_ids,
        holdout_season,
    )
    last_date = max(
        datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
        for value in training["date"].tolist()
    )
    training_prepared = replace(
        prepared,
        history=training,
        n_times=max(2, int(training["time_idx"].max()) + 1),
        last_observed_at=last_date,
        days_since_last_observed=0,
        recent_season=recent_season,
        recent_attack=recent_attack,
        recent_defense=recent_defense,
        recent_matches=recent_matches,
        current_season_matches=current_matches,
    )

    fit = fit_model(
        training_prepared,
        league_key,
        steps=steps,
        save_posterior=False,
        posterior_samples=300,
        apply_preseason_calibration=False,
    )
    team_pos = {team_id: index for index, team_id in enumerate(prepared.current_team_ids)}
    probabilities: list[np.ndarray] = []
    outcomes: list[int] = []
    correct = 0

    for row in holdout.to_dict("records"):
        h = team_pos.get(int(row["home_id"]))
        a = team_pos.get(int(row["away_id"]))
        if h is None or a is None:
            continue
        log_h = fit.intercept + fit.home_advantage + fit.attack_current[:, h] - fit.defense_current[:, a]
        log_a = fit.intercept + fit.attack_current[:, a] - fit.defense_current[:, h]
        lh = np.exp(np.clip(log_h, -3, 2))
        la = np.exp(np.clip(log_a, -3, 2))
        ph, pd, pa = _score_probabilities(lh, la)
        probs = np.asarray([ph.mean(), pd.mean(), pa.mean()], dtype=float)
        probs /= max(probs.sum(), 1e-12)
        hg = int(row["home_goals"])
        ag = int(row["away_goals"])
        outcome = 0 if hg > ag else 2 if ag > hg else 1
        probabilities.append(probs)
        outcomes.append(outcome)
        correct += int(int(np.argmax(probs)) == outcome)

    if not probabilities:
        return {"status": "not_run", "reason": "No holdout matches could be scored."}

    p = np.vstack(probabilities)
    y = np.zeros_like(p)
    y[np.arange(len(outcomes)), np.asarray(outcomes)] = 1.0
    brier = float(np.mean(np.sum((p - y) ** 2, axis=1)))
    log_loss = float(-np.mean(np.log(np.clip(p[np.arange(len(outcomes)), outcomes], 1e-9, 1.0))))

    training_outcomes = []
    for row in training.to_dict("records"):
        hg = int(row["home_goals"])
        ag = int(row["away_goals"])
        training_outcomes.append(0 if hg > ag else 2 if ag > hg else 1)
    frequencies = np.bincount(training_outcomes, minlength=3).astype(float)
    frequencies /= max(frequencies.sum(), 1.0)
    baseline = np.broadcast_to(frequencies, y.shape)
    baseline_brier = float(np.mean(np.sum((baseline - y) ** 2, axis=1)))

    return {
        "status": "completed",
        "method": "time-ordered holdout; latest matches and current preseason seeds excluded before fitting",
        "holdout_matches": len(outcomes),
        "training_matches": int(len(training)),
        "holdout_season": holdout_season,
        "cutoff_timestamp": int(cutoff_timestamp),
        "svi_steps": steps,
        "brier_score": round(brier, 6),
        "log_loss": round(log_loss, 6),
        "most_likely_outcome_accuracy": round(correct / len(outcomes), 6),
        "naive_frequency_brier": round(baseline_brier, 6),
        "brier_skill_vs_naive": round(1.0 - brier / baseline_brier, 6) if baseline_brier > 0 else None,
        "limitations": (
            "This is a lightweight match-outcome holdout, not a complete season-title calibration study. "
            "Current preseason seeds and Polymarket data are excluded. It is intended to catch large regressions during automated rebuilds."
        ),
    }
