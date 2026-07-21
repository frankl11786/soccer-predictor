from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jax
import jax.numpy as jnp
import numpy as np
import numpyro
import numpyro.distributions as dist
from numpyro.infer import SVI, Trace_ELBO
from numpyro.infer.autoguide import AutoNormal
from numpyro.optim import Adam

from .config import MODEL_DIR, POSTERIOR_SAMPLES, RANDOM_SEED, SVI_LEARNING_RATE, SVI_STEPS
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


def _model(home_idx, away_idx, time_idx, value_diff, goals_home, goals_away, n_teams: int, n_times: int):
    intercept = numpyro.sample("intercept", dist.Normal(jnp.log(1.35), 0.30))
    home_advantage = numpyro.sample("home_advantage", dist.Normal(0.18, 0.14))
    value_coefficient = numpyro.sample("value_coefficient", dist.Normal(0.035, 0.045))
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

    log_home = intercept + home_advantage + attack[time_idx, home_idx] - defense[time_idx, away_idx] + value_coefficient * value_diff
    log_away = intercept + attack[time_idx, away_idx] - defense[time_idx, home_idx] - value_coefficient * value_diff
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


def fit_model(prepared: PreparedLeague, league_key: str, steps: int = SVI_STEPS) -> PosteriorFit:
    history = prepared.history
    args = {
        "home_idx": jnp.asarray(history.home_idx.to_numpy(), dtype=jnp.int32),
        "away_idx": jnp.asarray(history.away_idx.to_numpy(), dtype=jnp.int32),
        "time_idx": jnp.asarray(history.time_idx.to_numpy(), dtype=jnp.int32),
        "value_diff": jnp.asarray(history.value_diff.to_numpy(), dtype=jnp.float32),
        "goals_home": jnp.asarray(history.home_goals.to_numpy(), dtype=jnp.int32),
        "goals_away": jnp.asarray(history.away_goals.to_numpy(), dtype=jnp.int32),
        "n_teams": len(prepared.team_index),
        "n_times": prepared.n_times,
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
    raw = guide.sample_posterior(sample_key, params, sample_shape=(POSTERIOR_SAMPLES,), **args)
    samples = {name: np.asarray(value) for name, value in raw.items()}
    attack_all, defense_all = _reconstruct_current(samples, prepared.n_times)
    current_indices = np.asarray([prepared.team_index[team_id] for team_id in prepared.current_team_ids], dtype=int)
    attack_current = attack_all[:, current_indices]
    defense_current = defense_all[:, current_indices]

    summary = {
        "matches": int(len(history)),
        "teams_in_history": int(len(prepared.team_index)),
        "time_buckets": int(prepared.n_times),
        "bucket_days": int(prepared.bucket_days),
        "posterior_samples": POSTERIOR_SAMPLES,
        "svi_steps": steps,
        "loss_final": float(loss),
        "intercept_mean": float(samples["intercept"].mean()),
        "home_advantage_mean": float(samples["home_advantage"].mean()),
        "market_value_coefficient_mean": float(samples["value_coefficient"].mean()),
        "sigma_attack_mean": float(samples["sigma_attack"].mean()),
        "sigma_defense_mean": float(samples["sigma_defense"].mean()),
    }
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
