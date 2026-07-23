from __future__ import annotations

import math
from typing import Any

from .bayes import PosteriorFit
from .config import LeagueConfig, MODEL_VERSION
from .data_prep import PreparedLeague
from .polymarket import MarketQuote
from .simulate import SimulationResult
from .utils import read_json, utc_now_iso, write_json


def _source_names(data_meta: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for source in data_meta.get("sources", []):
        if not isinstance(source, dict):
            continue
        name = source.get("source") or source.get("name")
        if name and str(name) not in names:
            names.append(str(name))
    if "Polymarket" not in names:
        names.append("Polymarket")
    return names


def _source_errors(data_meta: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for source in data_meta.get("sources", []):
        if not isinstance(source, dict):
            continue
        source_name = str(source.get("source") or source.get("name") or "Data source")
        value = source.get("errors")
        if isinstance(value, dict):
            for key, message in value.items():
                if message:
                    errors.append(f"{source_name} ({key}): {message}")
        elif isinstance(value, (list, tuple)):
            for message in value:
                if message:
                    errors.append(f"{source_name}: {message}")
        elif value:
            errors.append(f"{source_name}: {value}")
    return errors


def _metric_specs(cfg: LeagueConfig) -> tuple[tuple[str, str], ...]:
    if cfg.key == "epl":
        return (
            ("title", "Premier League title"),
            ("top4", "Top-four finish"),
            ("relegation", "Relegation"),
        )
    return (
        ("champion", "Win MLS Cup"),
        ("cup_final", "Reach MLS Cup"),
        ("shield", "Supporters’ Shield"),
    )


def _movement_news(
    cfg: LeagueConfig,
    previous: dict[str, Any],
    current_forecast: list[dict[str, Any]],
    team_names: dict[str, str],
    generated: str,
) -> list[dict[str, Any]]:
    previous_rows = {
        str(row.get("team")): row
        for row in previous.get("forecast", [])
        if isinstance(row, dict) and row.get("team")
    }
    if not previous_rows:
        return []

    candidates: list[dict[str, Any]] = []
    for row in current_forecast:
        slug = str(row.get("team") or "")
        before_row = previous_rows.get(slug)
        if not slug or not before_row:
            continue

        changes = []
        for key, label in _metric_specs(cfg):
            before = float(before_row.get(key) or 0.0)
            after = float(row.get(key) or 0.0)
            changes.append((abs(after - before), key, label, before, after))
        magnitude, metric_key, metric_label, before, after = max(changes, key=lambda item: item[0])
        if magnitude < 0.0005:
            continue

        delta = after - before
        direction = "increased" if delta > 0 else "decreased"
        name = team_names.get(slug, slug.replace("-", " ").title())
        candidates.append(
            {
                "id": f"forecast-{generated[:10]}-{slug}-{metric_key}",
                "type": "forecast_mover",
                "date": generated[:10],
                "generated_at": generated,
                "team": slug,
                "headline": f"{name} {metric_label.lower()} probability {direction}",
                "summary": (
                    f"{metric_label} moved from {before * 100:.1f}% to {after * 100:.1f}% "
                    f"since the prior published snapshot."
                ),
                "affects_forecast": True,
                "impact": f"{delta * 100:+.1f} percentage points",
                "details": {
                    "metric": metric_label,
                    "metric_key": metric_key,
                    "before": round(before, 6),
                    "after": round(after, 6),
                    "delta": round(delta, 6),
                    "projected_points_before": before_row.get("projected_points"),
                    "projected_points_after": row.get("projected_points"),
                    "avg_position_before": before_row.get("avg_position"),
                    "avg_position_after": row.get("avg_position"),
                    "explanation": (
                        "The nightly rebuild incorporated the latest results, remaining schedule, "
                        "updated attack and defense states, and fresh season simulations."
                    ),
                },
            }
        )

    candidates.sort(key=lambda item: abs(float(item["details"]["delta"])), reverse=True)
    return candidates[:8]


def _manual_news(previous: dict[str, Any]) -> list[dict[str, Any]]:
    preserved: list[dict[str, Any]] = []
    for item in previous.get("news", []):
        if not isinstance(item, dict):
            continue
        entry_type = item.get("type")
        if entry_type in {"system", "forecast_mover", "warning"}:
            continue
        if not entry_type and item.get("headline") == "Automated model refresh completed":
            continue
        if item.get("team") or entry_type in {"team_update", "manual"}:
            copy = dict(item)
            copy.setdefault("type", "team_update")
            preserved.append(copy)
    return preserved[:40]


def _build_news(
    cfg: LeagueConfig,
    previous: dict[str, Any],
    current_forecast: list[dict[str, Any]],
    team_names: dict[str, str],
    generated: str,
    fit: PosteriorFit,
    simulation: SimulationResult,
    data_meta: dict[str, Any],
) -> list[dict[str, Any]]:
    completed = sum(1 for fixture in simulation.fixtures if fixture["status"] == "final")
    sources = _source_names(data_meta)
    entries: list[dict[str, Any]] = [
        {
            "id": f"system-{cfg.key}-{generated}",
            "type": "system",
            "date": generated[:10],
            "generated_at": generated,
            "team": None,
            "headline": "Automated model refresh completed",
            "summary": (
                f"The latest {cfg.name} data was downloaded, the Bayesian model was fully refitted, "
                f"and {cfg.simulations:,} season simulations were completed."
            ),
            "affects_forecast": True,
            "impact": "System update",
            "details": {
                "generated_at": generated,
                "model_version": MODEL_VERSION,
                "matches_fitted": fit.summary["matches"],
                "simulations": cfg.simulations,
                "completed_matches": completed,
                "sources": sources,
                "validation": "Snapshot generated successfully; workflow validation runs before publication.",
                "deployment": "Published to the live site after both league jobs completed.",
                "inference": "NumPyro stochastic variational inference with an automatic normal guide",
                "note": "This is an automated system entry. It is intentionally not assigned to a club.",
            },
        }
    ]

    errors = _source_errors(data_meta)
    if errors:
        entries.append(
            {
                "id": f"warning-{cfg.key}-{generated}",
                "type": "warning",
                "date": generated[:10],
                "generated_at": generated,
                "team": None,
                "headline": "One or more data-source warnings were reported",
                "summary": "The forecast completed, but at least one connected source returned a warning that should be reviewed.",
                "affects_forecast": False,
                "impact": "Review required",
                "details": {
                    "sources": sources,
                    "validation": "Completed with source warnings",
                    "note": " | ".join(errors),
                },
            }
        )

    entries.extend(_movement_news(cfg, previous, current_forecast, team_names, generated))
    entries.extend(_manual_news(previous))
    return entries


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
    previous = read_json(output_path, default={}) or {}
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
    team_names = {team["slug"]: team["name"] for team in teams}
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
            "compared_with_previous_snapshot": bool(previous.get("forecast")),
        },
        "teams": teams,
        "current_table": simulation.current_table,
        "forecast": simulation.forecast,
        "fixtures": simulation.fixtures,
        "news": _build_news(
            cfg,
            previous,
            simulation.forecast,
            team_names,
            generated,
            fit,
            simulation,
            data_meta,
        ),
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
