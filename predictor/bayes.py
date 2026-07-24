from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import jax
import jax.numpy as jnp
import numpy as np
import numpyro
import numpyro.distributions as dist
from numpyro.infer import SVI, Trace_ELBO
from numpyro.infer.autoguide import AutoNormal
from numpyro.optim import Adam

from .config import (
    EPL_CALIBRATION_MATCHES_TO_FADE,
    EPL_ESTABLISHED_FITTED_WEIGHT,
    EPL_PROMOTED_FITTED_WEIGHT,
    EPL_RECENT_PERFORMANCE_WEIGHT,
    EPL_SEED_UNCERTAINTY,
    EPL_VALUE_COEFFICIENT_MEAN,
    EPL_VALUE_COEFFICIENT_SD,
    MLS_VALUE_COEFFICIENT_MEAN,
    MLS_VALUE_COEFFICIENT_SD,
    MODEL_DIR,
    POSTERIOR_SAMPLES,
    RANDOM_SEED,
    SVI_LEARNING_RATE,
    SVI_STEPS,
)
from .data_prep import PreparedLeague

numpyro.set_platform("cpu")


@dataclass
class PosteriorFit:
    attack_current: np.ndarray
    defense_current: np.ndarray
    intercept: np.ndarray
    home_advantage: np.ndarray
    value_coefficient: np.ndarray
    sigma_attack: np.ndarray
    sigma_defense: np.ndarray
    loss_final: float
    team_ids: list[int]
    current_indices: np.ndarray
    summary: dict[str, Any]


def _model(
    home_idx,
    away_idx,
    time_idx,
    value_diff,
    goals_home,
    goals_away,
    n_teams: int,
    n_times: int,
    value_prior_mean: float,
    value_prior_sd: float,
):
    intercept = numpyro.sample("intercept", dist.Normal(jnp.log(1.35), 0.30))
    home_advantage = numpyro.sample("home_advantage", dist.Normal(0.18, 0.14))
    value_coefficient = numpyro.sample(
        "value_coefficient",
        dist.Normal(value_prior_mean, value_prior_sd),
    )
    sigma_attack = numpyro.sample("sigma_attack", dist.HalfNormal(0.10))
    sigma_defense = numpyro.sample("sigma_defense", dist.HalfNormal(0.10))

    attack0 = numpyro.sample("attack0", dist.Normal(0, 0.35).expand([n_teams]).to_event(1))
    defense0 = numpyro.sample("defense0", dist.Normal(0, 0.35).expand([n_teams]).to_event(1))
    attack_eps = numpyro.sample(
        "attack_eps", dist.Normal(0, sigma_attack).expand([n_times - 1, n_teams]).to_event(2)
    )
    defense_eps = numpyro.sample(
        "defense_eps", dist.Normal(0, sigma_defense).expand([n_times - 1, n_teams]).to_event(2)
    )
    attack = jnp.concatenate([attack0[None, :], attack0[None, :] + jnp.cumsum(attack_eps, axis=0)], axis=0)
    defense = jnp.concatenate([defense0[None, :], defense0[None, :] + jnp.cumsum(defense_eps, axis=0)], axis=0)
    attack = attack - jnp.mean(attack, axis=1, keepdims=True)
    defense = defense - jnp.mean(defense, axis=1, keepdims=True)

    log_home = (
        intercept
        + home_advantage
        + attack[time_idx, home_idx]
        - defense[time_idx, away_idx]
        + value_coefficient * value_diff
    )
    log_away = (
        intercept
        + attack[time_idx, away_idx]
        - defense[time_idx, home_idx]
        - value_coefficient * value_diff
    )
    numpyro.sample("goals_home", dist.Poisson(jnp.exp(jnp.clip(log_home, -3.0, 2.0))), obs=goals_home)
    numpyro.sample("goals_away", dist.Poisson(jnp.exp(jnp.clip(log_away, -3.0, 2.0))), obs=goals_away)


def _reconstruct_current(samples: dict[str, np.ndarray], n_times: int) -> tuple[np.ndarray, np.ndarray]:
    attack0 = samples["attack0"]
    defense0 = samples["defense0"]
    attack = attack0 + np.sum(samples["attack_eps"][:, : n_times - 1, :], axis=1)
    defense = defense0 + np.sum(samples["defense_eps"][:, : n_times - 1, :], axis=1)
    attack -= attack.mean(axis=1, keepdims=True)
    defense -= defense.mean(axis=1, keepdims=True)
    return attack, defense


def calibrate_epl_state(
    prepared: PreparedLeague,
    attack_current: np.ndarray,
    defense_current: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Blend the last observed EPL state with stable preseason information.

    The adjustment is strongest before Matchweek 1 and fades independently for
    each club over its first ten league matches. Polymarket is intentionally not
    used here; it remains an external comparison only.
    """
    seed_attack = np.asarray(prepared.seed_attack, dtype=np.float32).copy()
    seed_defense = np.asarray(prepared.seed_defense, dtype=np.float32).copy()
    seed_attack -= seed_attack.mean()
    seed_defense -= seed_defense.mean()

    reliability = np.clip(np.asarray(prepared.recent_matches, dtype=np.float32) / 38.0, 0.0, 1.0)
    recent_weight = EPL_RECENT_PERFORMANCE_WEIGHT * reliability
    target_attack = (1.0 - recent_weight) * seed_attack + recent_weight * prepared.recent_attack
    target_defense = (1.0 - recent_weight) * seed_defense + recent_weight * prepared.recent_defense
    target_attack -= target_attack.mean()
    target_defense -= target_defense.mean()

    base_fitted_weight = (
        EPL_PROMOTED_FITTED_WEIGHT
        + (EPL_ESTABLISHED_FITTED_WEIGHT - EPL_PROMOTED_FITTED_WEIGHT) * reliability
    )
    progress = np.clip(
        np.asarray(prepared.current_season_matches, dtype=np.float32)
        / float(EPL_CALIBRATION_MATCHES_TO_FADE),
        0.0,
        1.0,
    )
    fitted_weight = base_fitted_weight + (1.0 - base_fitted_weight) * progress

    # A longer offseason modestly lowers reliance on the last observed state.
    # Once current-season matches arrive, the progress term quickly dominates.
    offseason_decay = float(0.5 ** (prepared.days_since_last_observed / 365.0))
    fitted_weight = np.where(progress > 0, fitted_weight, fitted_weight * offseason_decay)
    fitted_weight = np.clip(fitted_weight, 0.03, 1.0).astype(np.float32)

    rng = np.random.default_rng(RANDOM_SEED + 77)
    uncertainty_scale = EPL_SEED_UNCERTAINTY * (1.0 - fitted_weight)
    attack_noise = rng.normal(0.0, uncertainty_scale[None, :], size=attack_current.shape)
    defense_noise = rng.normal(0.0, uncertainty_scale[None, :], size=defense_current.shape)

    attack_adjusted = (
        fitted_weight[None, :] * attack_current
        + (1.0 - fitted_weight[None, :]) * target_attack[None, :]
        + attack_noise
    )
    defense_adjusted = (
        fitted_weight[None, :] * defense_current
        + (1.0 - fitted_weight[None, :]) * target_defense[None, :]
        + defense_noise
    )
    attack_adjusted -= attack_adjusted.mean(axis=1, keepdims=True)
    defense_adjusted -= defense_adjusted.mean(axis=1, keepdims=True)

    promoted = [
        prepared.teams[i]["slug"]
        for i, match_count in enumerate(prepared.recent_matches)
        if int(match_count) == 0
    ]
    summary = {
        "applied": True,
        "method": "last-observed state blended with prior-season scoring rates and squad-strength seeds",
        "recent_season": prepared.recent_season,
        "days_since_last_observed_match": prepared.days_since_last_observed,
        "recent_performance_max_weight": EPL_RECENT_PERFORMANCE_WEIGHT,
        "matches_to_fade": EPL_CALIBRATION_MATCHES_TO_FADE,
        "fitted_weight_mean": float(fitted_weight.mean()),
        "fitted_weight_min": float(fitted_weight.min()),
        "fitted_weight_max": float(fitted_weight.max()),
        "clubs_without_recent_epl_history": promoted,
        "uses_polymarket": False,
    }
    return attack_adjusted.astype(np.float32), defense_adjusted.astype(np.float32), summary


def fit_model(
    prepared: PreparedLeague,
    league_key: str,
    steps: int = SVI_STEPS,
    *,
    save_posterior: bool = True,
    posterior_samples: int = POSTERIOR_SAMPLES,
    apply_preseason_calibration: bool = True,
) -> PosteriorFit:
    history = prepared.history
    if league_key == "epl":
        value_prior_mean = EPL_VALUE_COEFFICIENT_MEAN
        value_prior_sd = EPL_VALUE_COEFFICIENT_SD
    else:
        value_prior_mean = MLS_VALUE_COEFFICIENT_MEAN
        value_prior_sd = MLS_VALUE_COEFFICIENT_SD

    args = {
        "home_idx": jnp.asarray(history.home_idx.to_numpy(), dtype=jnp.int32),
        "away_idx": jnp.asarray(history.away_idx.to_numpy(), dtype=jnp.int32),
        "time_idx": jnp.asarray(history.time_idx.to_numpy(), dtype=jnp.int32),
        "value_diff": jnp.asarray(history.value_diff.to_numpy(), dtype=jnp.float32),
        "goals_home": jnp.asarray(history.home_goals.to_numpy(), dtype=jnp.int32),
        "goals_away": jnp.asarray(history.away_goals.to_numpy(), dtype=jnp.int32),
        "n_teams": len(prepared.team_index),
        "n_times": prepared.n_times,
        "value_prior_mean": value_prior_mean,
        "value_prior_sd": value_prior_sd,
    }
    key = jax.random.PRNGKey(RANDOM_SEED + (1 if league_key == "mls" else 0))
    guide = AutoNormal(_model, init_scale=0.08)
    svi = SVI(_model, guide, Adam(SVI_LEARNING_RATE), Trace_ELBO())
    state = svi.init(key, **args)
    loss = np.nan
    for step in range(steps):
        state, loss = svi.update(state, **args)
        if step and step % 1000 == 0:
            print(f"[{league_key}] SVI step {step:,}/{steps:,}, loss={float(loss):,.1f}")
    params = svi.get_params(state)
    sample_key = jax.random.split(key, 2)[1]
    raw = guide.sample_posterior(sample_key, params, sample_shape=(posterior_samples,), **args)
    samples = {name: np.asarray(value) for name, value in raw.items()}
    attack_all, defense_all = _reconstruct_current(samples, prepared.n_times)
    current_indices = np.asarray([prepared.team_index[team_id] for team_id in prepared.current_team_ids], dtype=int)
    attack_current = attack_all[:, current_indices]
    defense_current = defense_all[:, current_indices]

    state_adjustment: dict[str, Any] = {"applied": False, "uses_polymarket": False}
    if league_key == "epl" and apply_preseason_calibration:
        attack_current, defense_current, state_adjustment = calibrate_epl_state(
            prepared,
            attack_current,
            defense_current,
        )
    elif league_key == "epl":
        state_adjustment = {
            "applied": False,
            "uses_polymarket": False,
            "reason": "disabled for temporal validation so current preseason seeds cannot leak into historical holdout predictions",
        }

    summary = {
        "matches": int(len(history)),
        "teams_in_history": int(len(prepared.team_index)),
        "time_buckets": int(prepared.n_times),
        "bucket_days": int(prepared.bucket_days),
        "last_observed_at": prepared.last_observed_at.isoformat(),
        "posterior_samples": posterior_samples,
        "svi_steps": steps,
        "loss_final": float(loss),
        "intercept_mean": float(samples["intercept"].mean()),
        "home_advantage_mean": float(samples["home_advantage"].mean()),
        "market_value_coefficient_mean": float(samples["value_coefficient"].mean()),
        "market_value_historical_mode": prepared.historical_value_mode,
        "sigma_attack_mean": float(samples["sigma_attack"].mean()),
        "sigma_defense_mean": float(samples["sigma_defense"].mean()),
        "state_adjustment": state_adjustment,
    }
    if save_posterior:
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            MODEL_DIR / f"posterior_{league_key}.npz",
            attack_current=attack_current,
            defense_current=defense_current,
            intercept=samples["intercept"],
            home_advantage=samples["home_advantage"],
            value_coefficient=samples["value_coefficient"],
            sigma_attack=samples["sigma_attack"],
            sigma_defense=samples["sigma_defense"],
            current_team_ids=np.asarray(prepared.current_team_ids),
        )
    return PosteriorFit(
        attack_current=attack_current,
        defense_current=defense_current,
        intercept=samples["intercept"],
        home_advantage=samples["home_advantage"],
        value_coefficient=samples["value_coefficient"],
        sigma_attack=samples["sigma_attack"],
        sigma_defense=samples["sigma_defense"],
        loss_final=float(loss),
        team_ids=prepared.current_team_ids,
        current_indices=current_indices,
        summary=summary,
    )
