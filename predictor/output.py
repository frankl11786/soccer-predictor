from __future__ import annotations

import math
from typing import Any

from .bayes import PosteriorFit
from .config import FUTURE_STATE_RETENTION, LeagueConfig, MARKET_SANITY_THRESHOLD, MODEL_VERSION
from .data_prep import PreparedLeague
from .history import attach_postgame_analysis, build_accuracy_summary, update_prediction_history
from .kalshi import KalshiMatchQuote, KalshiWinnerQuote
from .polymarket import MarketQuote, MatchMarketQuote
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
    if "Kalshi" not in names:
        names.append("Kalshi")
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


def _market_errors(source: str, market_meta: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for label, section in (("Season winner", market_meta), ("Match markets", market_meta.get("match_markets") or {})):
        values = section.get("errors") if isinstance(section, dict) else None
        if isinstance(values, (list, tuple)):
            errors.extend(f"{source} {label}: {value}" for value in values if value)
        elif values:
            errors.append(f"{source} {label}: {values}")
    if len(errors) > 20:
        hidden = len(errors) - 20
        errors = errors[:20] + [f"{hidden} additional {source} request error(s) omitted from this entry"]
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
    market_meta: dict[str, Any],
    kalshi_meta: dict[str, Any],
) -> list[dict[str, Any]]:
    completed = sum(1 for fixture in simulation.fixtures if fixture["status"] == "final")
    sources = _source_names(data_meta)
    backtest = data_meta.get("backtest") or {}
    if backtest.get("status") == "completed":
        validation_text = (
            "Snapshot generated successfully. Temporal holdout: "
            f"Brier {float(backtest.get('brier_score', 0)):.3f}; "
            f"skill vs naive {float(backtest.get('brier_skill_vs_naive', 0)):+.1%}."
        )
    else:
        validation_text = "Snapshot generated successfully; workflow validation runs before publication."
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
                "validation": validation_text,
                "deployment": "Published to the live site after both league jobs completed.",
                "inference": "NumPyro stochastic variational inference with an automatic normal guide",
                "temporal_holdout": data_meta.get("backtest"),
                "preseason_calibration": fit.summary.get("state_adjustment"),
                "polymarket_season_quotes": market_meta.get("quotes_found", 0),
                "polymarket_match_quotes": (market_meta.get("match_markets") or {}).get("quotes_found", 0),
                "polymarket_match_coverage": (market_meta.get("match_markets") or {}).get("coverage", 0),
                "kalshi_season_quotes": kalshi_meta.get("quotes_found", 0),
                "kalshi_match_quotes": (kalshi_meta.get("match_markets") or {}).get("quotes_found", 0),
                "kalshi_match_coverage": (kalshi_meta.get("match_markets") or {}).get("coverage", 0),
                "note": "This is an automated system entry. It is intentionally not assigned to a club.",
            },
        }
    ]

    errors = _source_errors(data_meta) + _market_errors("Polymarket", market_meta) + _market_errors("Kalshi", kalshi_meta)
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


    if backtest.get("status") == "completed" and float(backtest.get("brier_skill_vs_naive") or 0.0) < -0.05:
        entries.append(
            {
                "id": f"warning-backtest-{cfg.key}-{generated}",
                "type": "warning",
                "date": generated[:10],
                "generated_at": generated,
                "team": None,
                "headline": "Temporal holdout performance fell below the naive baseline",
                "summary": (
                    f"The holdout Brier skill score was {float(backtest.get('brier_skill_vs_naive', 0)):+.1%}. "
                    "The forecast was published with a model-review warning."
                ),
                "affects_forecast": False,
                "impact": "Backtest review flag",
                "details": {
                    "validation": (
                        f"Brier {float(backtest.get('brier_score', 0)):.3f}; "
                        f"log loss {float(backtest.get('log_loss', 0)):.3f}; "
                        f"skill vs naive {float(backtest.get('brier_skill_vs_naive', 0)):+.1%}."
                    ),
                    "review_status": "Model review recommended before treating large edges as actionable",
                    "model_treatment": "The holdout result does not alter the production forecast automatically.",
                    "note": backtest.get("limitations"),
                },
            }
        )

    market_alerts = (market_meta.get("sanity_checks") or {}).get("large_divergences") or []
    if market_alerts:
        largest = market_alerts[0]
        entries.append(
            {
                "id": f"warning-market-{cfg.key}-{generated}",
                "type": "warning",
                "date": generated[:10],
                "generated_at": generated,
                "team": largest.get("team"),
                "headline": "Model-to-market divergence exceeds the review threshold",
                "summary": (
                    f"The largest gap is {abs(float(largest.get('difference', 0))) * 100:.1f} percentage points. "
                    "Polymarket remains an independent comparison and is not used to force the model toward market prices."
                ),
                "affects_forecast": False,
                "impact": "Model review flag",
                "details": {
                    "validation": (
                        f"{largest.get('team_name', largest.get('team'))}: "
                        f"model {float(largest.get('model', 0)) * 100:.1f}% vs "
                        f"normalized market {float(largest.get('market', 0)) * 100:.1f}%."
                    ),
                    "review_status": (
                        f"{len(market_alerts)} club(s) exceeded the "
                        f"{MARKET_SANITY_THRESHOLD * 100:.0f}-point threshold."
                    ),
                    "model_treatment": "Polymarket remains comparison-only; no market price was added to the Bayesian model.",
                    "note": "This flag does not alter either the model probability or the normalized market probability.",
                    "threshold": MARKET_SANITY_THRESHOLD,
                    "largest_divergence": largest,
                    "all_large_divergences": market_alerts,
                },
            }
        )

    match_alerts = ((market_meta.get("match_markets") or {}).get("sanity_checks") or {}).get("large_divergences") or []
    if match_alerts:
        largest_match = match_alerts[0]
        home_name = team_names.get(str(largest_match.get("home") or ""), str(largest_match.get("home") or "Home"))
        away_name = team_names.get(str(largest_match.get("away") or ""), str(largest_match.get("away") or "Away"))
        entries.append(
            {
                "id": f"warning-match-market-{cfg.key}-{generated}",
                "type": "warning",
                "date": generated[:10],
                "generated_at": generated,
                "team": None,
                "headline": "A match forecast differs materially from Polymarket",
                "summary": (
                    f"The largest exact-match gap is {abs(float(largest_match.get('difference', 0))) * 100:.1f} percentage points "
                    f"for {home_name} vs {away_name}."
                ),
                "affects_forecast": False,
                "impact": "Match review flag",
                "details": {
                    "validation": (
                        f"{largest_match.get('outcome', 'Outcome')}: "
                        f"model {float(largest_match.get('model', 0)) * 100:.1f}% vs "
                        f"normalized market {float(largest_match.get('market', 0)) * 100:.1f}%."
                    ),
                    "review_status": (
                        f"{len(match_alerts)} exact match market(s) exceeded the "
                        f"{MARKET_SANITY_THRESHOLD * 100:.0f}-point threshold."
                    ),
                    "model_treatment": "The market comparison is diagnostic only and never changes the match forecast.",
                    "affected_fixtures": f"{home_name} vs {away_name}",
                    "note": "Open the scheduled match page to compare all three outcomes.",
                    "threshold": MARKET_SANITY_THRESHOLD,
                    "largest_divergence": largest_match,
                    "all_large_divergences": match_alerts,
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
    match_quotes: dict[str, MatchMarketQuote],
    market_meta: dict[str, Any],
    kalshi_quotes: dict[str, KalshiWinnerQuote],
    kalshi_match_quotes: dict[str, KalshiMatchQuote],
    kalshi_meta: dict[str, Any],
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
                # Backward-compatible goal effect used by Matchup Lab: more
                # negative means a stronger defense because it is added to the
                # opponent's log-goal rate. A positive display metric is also
                # published to avoid sign ambiguity in tables and team cards.
                "defense": row["defense"],
                "defense_strength": round(-float(row["defense"]), 4),
            }
        )

    outcome_key = "title" if cfg.key == "epl" else "champion"
    market_divergences: list[dict[str, Any]] = []
    kalshi_divergences: list[dict[str, Any]] = []
    consensus_divergences: list[dict[str, Any]] = []
    for team in prepared.teams:
        row = forecast_by_slug[team["slug"]]
        row["defense_strength"] = round(-float(row["defense"]), 4)
        if isinstance(row.get("defense_interval"), list) and len(row["defense_interval"]) == 2:
            row["defense_strength_interval"] = [
                round(-float(row["defense_interval"][1]), 4),
                round(-float(row["defense_interval"][0]), 4),
            ]

        quote = quotes.get(team["name"])
        row["market"] = round(quote.probability, 6) if quote else None
        row["market_raw"] = round(quote.raw_probability, 6) if quote else None
        row["edge"] = round(row[outcome_key] - quote.probability, 6) if quote else None
        row["market_details"] = (
            {
                "source": "Polymarket",
                "question": quote.question,
                "market_id": quote.market_id,
                "event_id": quote.event_id,
                "event": quote.event_title,
                "event_slug": quote.event_slug,
                "raw_probability": round(quote.raw_probability, 6),
                "normalized_probability": round(quote.probability, 6),
                "normalized": quote.normalized,
                "normalization_total": round(quote.normalization_total, 6) if quote.normalization_total else None,
                "liquidity": quote.liquidity,
                "volume": quote.volume,
                "updated_at": quote.updated_at,
                "comparison_only": True,
            }
            if quote
            else None
        )

        kalshi_quote = kalshi_quotes.get(team["name"])
        row["kalshi"] = round(kalshi_quote.probability, 6) if kalshi_quote else None
        row["kalshi_raw"] = round(kalshi_quote.raw_probability, 6) if kalshi_quote else None
        row["kalshi_edge"] = round(row[outcome_key] - kalshi_quote.probability, 6) if kalshi_quote else None
        row["kalshi_details"] = (
            {
                "source": "Kalshi",
                "market_ticker": kalshi_quote.market_ticker,
                "event_ticker": kalshi_quote.event_ticker,
                "event": kalshi_quote.event_title,
                "event_url": kalshi_quote.event_url,
                "raw_probability": round(kalshi_quote.raw_probability, 6),
                "normalized_probability": round(kalshi_quote.probability, 6),
                "normalized": kalshi_quote.normalized,
                "normalization_total": round(kalshi_quote.normalization_total, 6) if kalshi_quote.normalization_total else None,
                "bid": round(kalshi_quote.bid, 6) if kalshi_quote.bid is not None else None,
                "ask": round(kalshi_quote.ask, 6) if kalshi_quote.ask is not None else None,
                "last": round(kalshi_quote.last, 6) if kalshi_quote.last is not None else None,
                "spread": round(kalshi_quote.spread, 6) if kalshi_quote.spread is not None else None,
                "estimate_method": kalshi_quote.estimate_method,
                "liquidity": round(kalshi_quote.liquidity, 2),
                "volume": round(kalshi_quote.volume, 2),
                "volume_24h": round(kalshi_quote.volume_24h, 2),
                "open_interest": round(kalshi_quote.open_interest, 2),
                "updated_at": kalshi_quote.updated_at,
                "comparison_only": True,
            }
            if kalshi_quote
            else None
        )

        consensus_values: list[tuple[str, float]] = []
        if quote:
            consensus_values.append(("Polymarket", float(quote.probability)))
        if kalshi_quote:
            consensus_values.append(("Kalshi", float(kalshi_quote.probability)))
        if consensus_values:
            consensus_probability = sum(value for _, value in consensus_values) / len(consensus_values)
            row["market_consensus"] = round(consensus_probability, 6)
            row["consensus_edge"] = round(float(row[outcome_key]) - consensus_probability, 6)
            row["consensus_details"] = {
                "sources": [name for name, _ in consensus_values],
                "source_count": len(consensus_values),
                "method": "Equal-weight mean of available normalized prediction-market estimates",
                "comparison_only": True,
            }
        else:
            row["market_consensus"] = None
            row["consensus_edge"] = None
            row["consensus_details"] = None

        if quote:
            difference = float(row[outcome_key]) - float(quote.probability)
            if abs(difference) >= MARKET_SANITY_THRESHOLD:
                market_divergences.append(
                    {
                        "team": team["slug"],
                        "team_name": team["name"],
                        "metric": outcome_key,
                        "model": round(float(row[outcome_key]), 6),
                        "market": round(float(quote.probability), 6),
                        "market_raw": round(float(quote.raw_probability), 6),
                        "difference": round(difference, 6),
                    }
                )
        if kalshi_quote:
            difference = float(row[outcome_key]) - float(kalshi_quote.probability)
            if abs(difference) >= MARKET_SANITY_THRESHOLD:
                kalshi_divergences.append(
                    {
                        "team": team["slug"],
                        "team_name": team["name"],
                        "metric": outcome_key,
                        "model": round(float(row[outcome_key]), 6),
                        "market": round(float(kalshi_quote.probability), 6),
                        "market_raw": round(float(kalshi_quote.raw_probability), 6),
                        "difference": round(difference, 6),
                    }
                )
        if row.get("market_consensus") is not None:
            difference = float(row[outcome_key]) - float(row["market_consensus"])
            if abs(difference) >= MARKET_SANITY_THRESHOLD:
                consensus_divergences.append(
                    {
                        "team": team["slug"],
                        "team_name": team["name"],
                        "metric": outcome_key,
                        "model": round(float(row[outcome_key]), 6),
                        "market": round(float(row["market_consensus"]), 6),
                        "difference": round(difference, 6),
                        "sources": row.get("consensus_details", {}).get("sources", []),
                    }
                )

    market_divergences.sort(key=lambda item: abs(float(item["difference"])), reverse=True)
    market_meta["sanity_checks"] = {
        "threshold": MARKET_SANITY_THRESHOLD,
        "status": "warning" if market_divergences else "passed",
        "large_divergences": market_divergences,
        "note": "Sanity checks never feed Polymarket prices into the Bayesian model.",
    }
    kalshi_divergences.sort(key=lambda item: abs(float(item["difference"])), reverse=True)
    kalshi_meta["sanity_checks"] = {
        "threshold": MARKET_SANITY_THRESHOLD,
        "status": "warning" if kalshi_divergences else "passed",
        "large_divergences": kalshi_divergences,
        "note": "Sanity checks never feed Kalshi prices into the Bayesian model.",
    }
    consensus_divergences.sort(key=lambda item: abs(float(item["difference"])), reverse=True)

    match_divergences: list[dict[str, Any]] = []
    kalshi_match_divergences: list[dict[str, Any]] = []
    consensus_match_divergences: list[dict[str, Any]] = []
    for fixture in simulation.fixtures:
        fixture_id = str(fixture.get("id") or "")
        model_probabilities = fixture.get("probabilities") or {}

        quote = match_quotes.get(fixture_id)
        if quote:
            market_probabilities = {
                "home": round(quote.home_probability, 6),
                "draw": round(quote.draw_probability, 6),
                "away": round(quote.away_probability, 6),
            }
            raw_probabilities = {
                "home": round(quote.home_raw_probability, 6),
                "draw": round(quote.draw_raw_probability, 6),
                "away": round(quote.away_raw_probability, 6),
            }
            edges = {
                outcome: round(float(model_probabilities.get(outcome) or 0.0) - probability, 6)
                for outcome, probability in market_probabilities.items()
            }
            fixture["polymarket"] = {
                "source": "Polymarket",
                "probabilities": market_probabilities,
                "raw_probabilities": raw_probabilities,
                "model_edge": edges,
                "normalized": quote.normalized,
                "normalization_total": round(quote.normalization_total, 6),
                "event_id": quote.event_id,
                "event_title": quote.event_title,
                "event_slug": quote.event_slug,
                "event_url": quote.event_url,
                "market_ids": quote.market_ids,
                "questions": quote.questions,
                "liquidity": round(quote.liquidity, 2),
                "volume": round(quote.volume, 2),
                "kickoff": quote.kickoff,
                "updated_at": quote.updated_at,
                "comparison_only": True,
            }
            largest_outcome, largest_gap = max(edges.items(), key=lambda item: abs(item[1]))
            if abs(largest_gap) >= MARKET_SANITY_THRESHOLD:
                match_divergences.append(
                    {
                        "fixture_id": fixture.get("id"),
                        "home": fixture.get("home"),
                        "away": fixture.get("away"),
                        "outcome": largest_outcome,
                        "model": round(float(model_probabilities.get(largest_outcome) or 0.0), 6),
                        "market": market_probabilities[largest_outcome],
                        "difference": largest_gap,
                    }
                )
        else:
            fixture.pop("polymarket", None)

        kalshi_quote = kalshi_match_quotes.get(fixture_id)
        if kalshi_quote:
            kalshi_probabilities = {
                "home": round(kalshi_quote.home_probability, 6),
                "draw": round(kalshi_quote.draw_probability, 6),
                "away": round(kalshi_quote.away_probability, 6),
            }
            kalshi_raw_probabilities = {
                "home": round(kalshi_quote.home_raw_probability, 6),
                "draw": round(kalshi_quote.draw_raw_probability, 6),
                "away": round(kalshi_quote.away_raw_probability, 6),
            }
            kalshi_edges = {
                outcome: round(float(model_probabilities.get(outcome) or 0.0) - probability, 6)
                for outcome, probability in kalshi_probabilities.items()
            }
            fixture["kalshi"] = {
                "source": "Kalshi",
                "probabilities": kalshi_probabilities,
                "raw_probabilities": kalshi_raw_probabilities,
                "model_edge": kalshi_edges,
                "normalized": kalshi_quote.normalized,
                "normalization_total": round(kalshi_quote.normalization_total, 6),
                "event_ticker": kalshi_quote.event_ticker,
                "event_title": kalshi_quote.event_title,
                "event_url": kalshi_quote.event_url,
                "market_tickers": kalshi_quote.market_tickers,
                "bids": kalshi_quote.bids,
                "asks": kalshi_quote.asks,
                "lasts": kalshi_quote.lasts,
                "spreads": kalshi_quote.spreads,
                "estimate_methods": kalshi_quote.estimate_methods,
                "liquidity": round(kalshi_quote.liquidity, 2),
                "volume": round(kalshi_quote.volume, 2),
                "volume_24h": round(kalshi_quote.volume_24h, 2),
                "open_interest": round(kalshi_quote.open_interest, 2),
                "kickoff": kalshi_quote.kickoff,
                "updated_at": kalshi_quote.updated_at,
                "comparison_only": True,
            }
            largest_outcome, largest_gap = max(kalshi_edges.items(), key=lambda item: abs(item[1]))
            if abs(largest_gap) >= MARKET_SANITY_THRESHOLD:
                kalshi_match_divergences.append(
                    {
                        "fixture_id": fixture.get("id"),
                        "home": fixture.get("home"),
                        "away": fixture.get("away"),
                        "outcome": largest_outcome,
                        "model": round(float(model_probabilities.get(largest_outcome) or 0.0), 6),
                        "market": kalshi_probabilities[largest_outcome],
                        "difference": largest_gap,
                    }
                )
        else:
            fixture.pop("kalshi", None)

        external_distributions: list[tuple[str, dict[str, float]]] = []
        if fixture.get("polymarket"):
            external_distributions.append(("Polymarket", fixture["polymarket"]["probabilities"]))
        if fixture.get("kalshi"):
            external_distributions.append(("Kalshi", fixture["kalshi"]["probabilities"]))
        if external_distributions:
            consensus_probabilities = {
                outcome: round(
                    sum(float(distribution[outcome]) for _, distribution in external_distributions) / len(external_distributions),
                    6,
                )
                for outcome in ("home", "draw", "away")
            }
            consensus_edges = {
                outcome: round(float(model_probabilities.get(outcome) or 0.0) - consensus_probabilities[outcome], 6)
                for outcome in ("home", "draw", "away")
            }
            fixture["market_consensus"] = {
                "probabilities": consensus_probabilities,
                "model_edge": consensus_edges,
                "sources": [name for name, _ in external_distributions],
                "source_count": len(external_distributions),
                "method": "Equal-weight mean of available normalized prediction-market estimates",
                "comparison_only": True,
            }
            largest_outcome, largest_gap = max(consensus_edges.items(), key=lambda item: abs(item[1]))
            if abs(largest_gap) >= MARKET_SANITY_THRESHOLD:
                consensus_match_divergences.append(
                    {
                        "fixture_id": fixture.get("id"),
                        "home": fixture.get("home"),
                        "away": fixture.get("away"),
                        "outcome": largest_outcome,
                        "model": round(float(model_probabilities.get(largest_outcome) or 0.0), 6),
                        "market": consensus_probabilities[largest_outcome],
                        "difference": largest_gap,
                        "sources": [name for name, _ in external_distributions],
                    }
                )
        else:
            fixture.pop("market_consensus", None)

    match_divergences.sort(key=lambda item: abs(float(item["difference"])), reverse=True)
    match_meta = market_meta.setdefault("match_markets", {})
    match_meta["sanity_checks"] = {
        "threshold": MARKET_SANITY_THRESHOLD,
        "status": "warning" if match_divergences else "passed",
        "large_divergences": match_divergences[:20],
        "note": "Match-market checks are informational and never change model probabilities.",
    }

    kalshi_match_divergences.sort(key=lambda item: abs(float(item["difference"])), reverse=True)
    kalshi_match_meta = kalshi_meta.setdefault("match_markets", {})
    kalshi_match_meta["sanity_checks"] = {
        "threshold": MARKET_SANITY_THRESHOLD,
        "status": "warning" if kalshi_match_divergences else "passed",
        "large_divergences": kalshi_match_divergences[:20],
        "note": "Kalshi match-market checks are informational and never change model probabilities.",
    }

    consensus_match_divergences.sort(key=lambda item: abs(float(item["difference"])), reverse=True)
    consensus_meta = {
        "source": "Prediction-market consensus",
        "sources": ["Polymarket", "Kalshi"],
        "method": "Equal-weight mean of available normalized estimates; no market data enters the Bayesian model",
        "sanity_checks": {
            "threshold": MARKET_SANITY_THRESHOLD,
            "season_large_divergences": consensus_divergences[:20],
            "match_large_divergences": consensus_match_divergences[:20],
        },
        "updated_at": utc_now_iso(),
    }

    generated = utc_now_iso()
    prediction_history = update_prediction_history(
        previous.get("prediction_history") if isinstance(previous, dict) else None,
        simulation.fixtures,
        generated,
        MODEL_VERSION,
    )
    attach_postgame_analysis(simulation.fixtures, prediction_history)
    accuracy = build_accuracy_summary(prediction_history)

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
                "Polymarket and Kalshi season-winner and exact 1X2 match prices are displayed as independent comparisons when exact markets are available. "
                "Each source is normalized within its event for like-for-like comparison; a simple cross-market consensus is also shown. None of these prices are used as model inputs."
            ),
            "completed_matches": completed,
            "data_sources": data_meta,
            "polymarket": market_meta,
            "kalshi": kalshi_meta,
            "market_consensus": consensus_meta,
            "compared_with_previous_snapshot": bool(previous.get("forecast")),
        },
        "methodology": {
            "market_comparison": {
                "source": "Polymarket + Kalshi",
                "comparison_only": True,
                "used_as_model_input": False,
                "season_winner_prices": (
                    "The pipeline retrieves one exact season-winner event from each exchange. "
                    "Polymarket contract prices and Kalshi bid/ask midpoint estimates are normalized "
                    "within their own event when coverage is sufficient."
                ),
                "match_prices": (
                    "A match comparison is published only for a date-verified full-match home/draw/away "
                    "event that matches both clubs and contains all three outcomes."
                ),
                "kalshi_price_method": (
                    "Kalshi uses the midpoint of the best Yes bid and ask when the spread is usable; "
                    "otherwise the latest trade is used. The three match outcomes are then normalized to 100%."
                ),
                "consensus": (
                    "Market consensus is the equal-weight mean of the available normalized Polymarket "
                    "and Kalshi estimates. It is diagnostic only."
                ),
                "edge_definition": "Bayesian probability minus the selected external-market probability",
                "sanity_warning_threshold": MARKET_SANITY_THRESHOLD,
            },
            "preseason_calibration": {
                "applies_to": "EPL",
                "last_fitted_state": "The fitted time axis ends at the last completed match.",
                "prediction_market_influence": "None",
            },
        },
        "teams": teams,
        "current_table": simulation.current_table,
        "forecast": simulation.forecast,
        "fixtures": simulation.fixtures,
        "prediction_history": prediction_history,
        "accuracy": accuracy,
        "news": _build_news(
            cfg,
            previous,
            simulation.forecast,
            team_names,
            generated,
            fit,
            simulation,
            data_meta,
            market_meta,
            kalshi_meta,
        ),
        "model": {
            "type": "Bayesian state-space Poisson with calibrated preseason transition",
            "base_goals": round(math.exp(fit.summary["intercept_mean"]), 4),
            "home_advantage_log": round(fit.summary["home_advantage_mean"], 4),
            "market_value_coefficient": round(fit.summary["market_value_coefficient_mean"], 4),
            "market_value_historical_mode": fit.summary.get("market_value_historical_mode"),
            "posterior_sd": round((fit.summary["sigma_attack_mean"] + fit.summary["sigma_defense_mean"]) / 2, 4),
            "sigma_attack": round(fit.summary["sigma_attack_mean"], 4),
            "sigma_defense": round(fit.summary["sigma_defense_mean"], 4),
            "posterior_samples": fit.summary["posterior_samples"],
            "time_buckets": fit.summary["time_buckets"],
            "bucket_days": fit.summary["bucket_days"],
            "matches_fitted": fit.summary["matches"],
            "last_observed_at": fit.summary.get("last_observed_at"),
            "preseason_calibration": fit.summary.get("state_adjustment"),
            "temporal_holdout": data_meta.get("backtest"),
            "future_state_retention": FUTURE_STATE_RETENTION,
            "inference": "NumPyro stochastic variational inference with an automatic normal guide",
            "equations": {
                "home": "log(lambda_home) = alpha + H + attack_home,t - defense_away,t + beta_value*log(value_home/value_away)",
                "away": "log(lambda_away) = alpha + attack_away,t - defense_home,t - beta_value*log(value_home/value_away)",
                "historical_state": "rating_t = rating_(t-1) + Normal(0, sigma), ending at the last observed match",
                "future_state": "rating_future = retention*rating_previous + Normal(0, sigma)",
                "preseason": "rating_start = w*fitted_last_state + (1-w)*preseason_target",
            },
        },
    }
    write_json(output_path, snapshot)
    return snapshot
