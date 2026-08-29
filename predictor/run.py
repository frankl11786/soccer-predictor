from __future__ import annotations

import argparse
import traceback

from .api_football import ApiFootballClient, fetch_history_rows
from .asa import fetch_mls_rows
from .backtest import temporal_holdout_backtest
from .bayes import fit_model
from .config import APP_DATA, LEAGUES
from .data_prep import prepare_league
from .mls_schedule import fetch_complete_mls_schedule
from .identity import canonicalize_fixture_rows
from .kalshi import (
    fetch_match_quotes as fetch_kalshi_match_quotes,
    fetch_total_goals_quotes as fetch_kalshi_total_goals_quotes,
    fetch_winner_quotes as fetch_kalshi_winner_quotes,
)
from .openfootball import fetch_epl_rows
from .output import build_snapshot
from .polymarket import fetch_match_quotes, fetch_total_goals_quotes, fetch_winner_quotes
from .simulate import simulate_epl, simulate_mls
from .utils import utc_now_iso


def run_league(key: str, refresh: bool, steps: int | None = None) -> None:
    cfg = LEAGUES[key]
    print(f"\n=== {cfg.name} ===")
    client = ApiFootballClient.from_environment()
    history_rows, api_meta = fetch_history_rows(client, cfg, refresh=refresh)

    if key == "epl":
        # OpenFootball now supplies an independent historical fallback as well
        # as the current schedule. This keeps the model operational when the
        # API-Football free tier cannot return one or more older seasons.
        openfootball_seasons = tuple(
            sorted(set(cfg.api_history_seasons + cfg.supplemental_seasons))
        )
        supplemental_rows, current_meta = fetch_epl_rows(
            openfootball_seasons,
            refresh=refresh,
        )
        source_meta = [api_meta, current_meta]
    else:
        asa_rows, asa_meta = fetch_mls_rows(
            cfg.supplemental_seasons,
            refresh=refresh,
        )
        schedule_rows, schedule_meta = fetch_complete_mls_schedule(
            cfg.current_season,
            refresh=refresh,
        )
        supplemental_rows = asa_rows + schedule_rows
        source_meta = [api_meta, asa_meta, schedule_meta]

    raw_fixtures = canonicalize_fixture_rows(
        cfg,
        history_rows + supplemental_rows,
    )
    prepared = prepare_league(cfg, raw_fixtures)
    print(
        f"Historical matches: {len(prepared.history):,}; "
        f"current fixtures: {len(prepared.current_fixtures):,}"
    )
    requested_steps = steps or 5_000
    backtest_steps = min(750, max(100, int(requested_steps * 0.15)))
    backtest_meta = temporal_holdout_backtest(
        prepared,
        key,
        steps=backtest_steps,
    )
    if backtest_meta.get("status") == "completed":
        print(
            "Temporal holdout: "
            f"Brier={backtest_meta['brier_score']:.4f}; "
            f"skill vs naive={backtest_meta.get('brier_skill_vs_naive', 0):+.1%}"
        )
    else:
        print(f"Temporal holdout not run: {backtest_meta.get('reason', 'not applicable')}")

    fit = fit_model(prepared, key, steps=requested_steps)
    print(f"Model fitted. Final ELBO loss: {fit.loss_final:,.1f}")
    simulation = (
        simulate_epl(prepared, fit, cfg.simulations)
        if key == "epl"
        else simulate_mls(prepared, fit, cfg.simulations)
    )
    quotes, market_meta = fetch_winner_quotes(
        cfg.polymarket_queries,
        [team["name"] for team in prepared.teams],
        event_slug=cfg.polymarket_event_slug,
    )
    match_quotes, match_market_meta = fetch_match_quotes(
        simulation.fixtures,
        prepared.teams,
        league_terms=cfg.polymarket_league_terms,
        lookahead_days=cfg.polymarket_match_lookahead_days,
        max_fixtures=cfg.polymarket_match_max_fixtures,
    )
    market_meta["match_markets"] = match_market_meta
    total_goal_quotes, total_goal_meta = fetch_total_goals_quotes(
        simulation.fixtures,
        prepared.teams,
        league_terms=cfg.polymarket_league_terms,
        lookahead_days=cfg.polymarket_match_lookahead_days,
        max_fixtures=cfg.polymarket_match_max_fixtures,
    )
    market_meta["total_goals"] = total_goal_meta
    print(
        "Polymarket comparison: "
        f"{len(quotes)} season-winner quotes; "
        f"{len(match_quotes)} exact match markets; "
        f"{len(total_goal_quotes)} total-goals events"
    )

    kalshi_quotes, kalshi_meta = fetch_kalshi_winner_quotes(
        cfg.kalshi_event_ticker,
        [team["name"] for team in prepared.teams],
    )
    kalshi_match_quotes, kalshi_match_meta = fetch_kalshi_match_quotes(
        simulation.fixtures,
        prepared.teams,
        series_ticker=cfg.kalshi_game_series_ticker,
        lookahead_days=cfg.polymarket_match_lookahead_days,
        max_fixtures=cfg.polymarket_match_max_fixtures,
    )
    kalshi_meta["match_markets"] = kalshi_match_meta
    kalshi_total_goal_quotes, kalshi_total_goal_meta = fetch_kalshi_total_goals_quotes(
        simulation.fixtures,
        prepared.teams,
        series_ticker=cfg.kalshi_total_series_ticker,
        lookahead_days=cfg.polymarket_match_lookahead_days,
        max_fixtures=cfg.polymarket_match_max_fixtures,
    )
    kalshi_meta["total_goals"] = kalshi_total_goal_meta
    print(
        "Kalshi comparison: "
        f"{len(kalshi_quotes)} season-winner quotes; "
        f"{len(kalshi_match_quotes)} exact match markets; "
        f"{len(kalshi_total_goal_quotes)} total-goals events"
    )
    data_meta = {
        "sources": source_meta,
        "backtest": backtest_meta,
        "updated_at": utc_now_iso(),
    }
    output_path = APP_DATA / f"{key}.json"
    build_snapshot(
        cfg,
        prepared,
        fit,
        simulation,
        quotes,
        match_quotes,
        market_meta,
        kalshi_quotes,
        kalshi_match_quotes,
        kalshi_meta,
        total_goal_quotes,
        kalshi_total_goal_quotes,
        data_meta,
        output_path,
    )
    print(f"Wrote {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", choices=["epl", "mls", "all"], default="all")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--steps", type=int, default=None)
    args = parser.parse_args()
    leagues = ("epl", "mls") if args.league == "all" else (args.league,)
    errors = []
    for key in leagues:
        try:
            run_league(key, refresh=args.refresh, steps=args.steps)
        except Exception as exc:
            errors.append((key, exc))
            traceback.print_exc()
    if errors:
        for key, exc in errors:
            print(f"ERROR [{key}]: {exc}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
