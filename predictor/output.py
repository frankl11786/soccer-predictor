from __future__ import annotations

import math
from typing import Any

from .bayes import PosteriorFit
from .config import LeagueConfig, MODEL_VERSION
from .data_prep import PreparedLeague
from .polymarket import MarketQuote
from .simulate import SimulationResult
from .utils import utc_now_iso, write_json


def build_snapshot(
    cfg: LeagueConfig,
    prepared: PreparedLeague,
    fit: PosteriorFit,
    simulation: SimulationResult,
    quotes: dict[str, MarketQuote],
    market_meta: dict[str, Any],
    data_meta: dict[str, Any],
    output_path,
) -> dict[str, Any]:
    forecast_by_slug = {row["team"]: row for row in simulation.forecast}
    teams = []
    for team in prepared.teams:
        row = forecast_by_slug[team["slug"]]
        teams.append(
            {
                **team,
                "attack": row["attack"],
                "defense": row["defense"],
            }
        )

    outcome_key = "title" if cfg.key == "epl" else "champion"
    for row, team in zip(simulation.forecast, prepared.teams):
        quote = quotes.get(team["name"])
        row["market"] = round(quote.probability, 6) if quote else None
        row["edge"] = round(row[outcome_key] - quote.probability, 6) if quote else None
        row["market_details"] = (
            {
                "source": "Polymarket",
                "question": quote.question,
                "event": quote.event_title,
                "event_slug": quote.event_slug,
                "liquidity": quote.liquidity,
                "volume": quote.volume,
                "updated_at": quote.updated_at,
            }
            if quote
            else None
        )

    generated = utc_now_iso()
    completed = sum(1 for fixture in simulation.fixtures if fixture["status"] == "final")
    snapshot = {
        "meta": {
            "league": cfg.key,
            "name": cfg.name,
            "season": cfg.season_label,
            "as_of": generated[:10],
            "generated_at": generated,
            "model_version": MODEL_VERSION,
            "data_mode": "LIVE API + BAYESIAN MODEL",
            "iterations": cfg.simulations,
            "notice": (
                f"Fixtures and results come from a free multi-source pipeline. Attack and defense ratings are fitted from "
                f"{fit.summary['matches']:,} completed matches using a Bayesian state-space Poisson model. "
                "Polymarket values are an independent comparison and appear only when an active matching market is found."
            ),
            "completed_matches": completed,
            "data_sources": data_meta,
            "polymarket": market_meta,
        },
        "teams": teams,
        "current_table": simulation.current_table,
        "forecast": simulation.forecast,
        "fixtures": simulation.fixtures,
        "news": [
            {
                "date": generated[:10],
                "team": teams[0]["slug"],
                "headline": "Automated model refresh completed",
                "summary": f"The latest {cfg.name} data was downloaded, the Bayesian model was refitted, and {cfg.simulations:,} season simulations were completed.",
                "affects_forecast": True,
                "impact": "System update",
            }
        ],
        "model": {
            "type": "Bayesian state-space Poisson",
            "base_goals": round(math.exp(fit.summary["intercept_mean"]), 4),
            "home_advantage_log": round(fit.summary["home_advantage_mean"], 4),
            "market_value_coefficient": round(fit.summary["market_value_coefficient_mean"], 4),
            "posterior_sd": round((fit.summary["sigma_attack_mean"] + fit.summary["sigma_defense_mean"]) / 2, 4),
            "sigma_attack": round(fit.summary["sigma_attack_mean"], 4),
            "sigma_defense": round(fit.summary["sigma_defense_mean"], 4),
            "posterior_samples": fit.summary["posterior_samples"],
            "time_buckets": fit.summary["time_buckets"],
            "bucket_days": fit.summary["bucket_days"],
            "matches_fitted": fit.summary["matches"],
            "inference": "NumPyro stochastic variational inference with an automatic normal guide",
            "equations": {
                "home": "log(lambda_home) = alpha + H + attack_home,t - defense_away,t + beta_value*log(value_home/value_away)",
                "away": "log(lambda_away) = alpha + attack_away,t - defense_home,t - beta_value*log(value_home/value_away)",
                "state": "rating_t = rating_(t-1) + Normal(0, sigma)",
            },
        },
    }
    write_json(output_path, snapshot)
    return snapshot
