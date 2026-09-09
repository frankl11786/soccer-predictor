from __future__ import annotations

import argparse
import traceback
from datetime import datetime, timezone

from .api_football import ApiFootballClient, fetch_history_rows
from .asa import fetch_mls_rows
from .backtest import temporal_holdout_backtest
from .bayes import fit_model
from .config import APP_DATA, LEAGUES
from .data_prep import prepare_league
from .epl_schedule import fetch_complete_epl_schedule
from .espn import fetch_league_rows
from .fixture_overrides import apply_fixture_status_overrides
from .football_data import fetch_epl_results as fetch_football_data_epl_results
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


STALE_FIXTURE_GRACE_HOURS = 18


def _assert_current_fixture_freshness(prepared, key: str) -> None:
    """Abort before Bayesian fitting if a current schedule is obviously stale."""
    cutoff = int(datetime.now(timezone.utc).timestamp()) - STALE_FIXTURE_GRACE_HOURS * 3600
    stale = []
    for row in prepared.current_fixtures.to_dict("records"):
        status = str(row.get("status") or "").upper()
        timestamp = int(row.get("timestamp") or 0)
        if status not in {"NS", "TBD", "SCHEDULED"} or not timestamp or timestamp >= cutoff:
            continue
        stale.append(row)

    if stale:
        sample = "; ".join(
            f"{row.get('home_name')} vs {row.get('away_name')} ({row.get('date')})"
            for row in stale[:10]
        )
        raise RuntimeError(
            f"{key}: {len(stale)} stale scheduled fixtures remain more than "
            f"{STALE_FIXTURE_GRACE_HOURS} hours past kickoff. Refusing to fit the "
            f"Bayesian model until a current results source is available: {sample}"
        )


def run_league(
    key: str,
    refresh: bool,
    steps: int | None = None,
    preflight_only: bool = False,
) -> None:
    cfg = LEAGUES[key]
    print(f"\n=== {cfg.name} ===")
    client = ApiFootballClient.from_environment()
    history_rows, api_meta = fetch_history_rows(client, cfg, refresh=refresh)

    # Current-result sources are fetched BEFORE data preparation/model fitting.
    # Newly completed matches must update the table, Bayesian fit and future
    # forecasts, not merely be cosmetically patched into the published JSON.
    espn_rows, espn_meta = fetch_league_rows(
        key,
        cfg.current_season,
        refresh=refresh,
    )

    if key == "epl":
        fixture_download_rows = []
        try:
            fixture_download_rows, fixture_download_meta = fetch_complete_epl_schedule(
                cfg.current_season,
                refresh=refresh,
            )
        except RuntimeError as exc:
            print(f"[FixtureDownload] EPL {cfg.current_season}: unavailable; {exc}")
            fixture_download_meta = {
                "source": "FixtureDownload",
                "purpose": "complete current EPL schedule and results",
                "season": cfg.current_season,
                "fixtures_received": 0,
                "cached": False,
                "fallback": None,
                "errors": [str(exc)],
                "updated_at": utc_now_iso(),
            }

        football_data_rows = []
        football_data_meta = None
        if espn_meta.get("live_request_failed") or not espn_rows:
            football_data_rows, football_data_meta = fetch_football_data_epl_results(
                cfg.current_season,
                refresh=refresh,
            )

        openfootball_seasons = tuple(
            sorted(set(cfg.api_history_seasons + cfg.supplemental_seasons))
        )
        openfootball_rows, current_meta = fetch_epl_rows(
            openfootball_seasons,
            refresh=refresh,
        )

        # OpenFootball remains the long-form schedule source. FixtureDownload is
        # an independent 380-match schedule/results spine and can replace stale
        # scheduled rows with finals when ESPN or Football-Data are unavailable.
        supplemental_rows = (
            openfootball_rows
            + fixture_download_rows
            + espn_rows
            + football_data_rows
        )
        source_meta = [api_meta, current_meta, fixture_download_meta, espn_meta]
        if football_data_meta is not None:
            source_meta.append(football_data_meta)
    else:
        asa_rows, asa_meta = fetch_mls_rows(
            cfg.supplemental_seasons,
            refresh=refresh,
        )
        schedule_rows, schedule_meta = fetch_complete_mls_schedule(
            cfg.current_season,
            refresh=refresh,
        )
        supplemental_rows = asa_rows + schedule_rows + espn_rows
        source_meta = [api_meta, asa_meta, schedule_meta, espn_meta]

    raw_fixtures = canonicalize_fixture_rows(
        cfg,
        history_rows + supplemental_rows,
    )
    prepared = prepare_league(cfg, raw_fixtures)

    # Exceptional fixture states such as an officially postponed match may lag
    # in every machine-readable feed. Apply narrowly scoped, source-cited status
    # overrides after schedule/result reconciliation and before freshness checks.
    override_meta = apply_fixture_status_overrides(prepared, cfg)
    if override_meta.get("configured"):
        source_meta.append(override_meta)

    # Cheap freshness guard: fail here, before the temporal backtest and 5,000
    # SVI steps, rather than wasting a long Actions run on stale source data.
    _assert_current_fixture_freshness(prepared, key)

    print(
        f"Historical matches: {len(prepared.history):,}; "
        f"current fixtures: {len(prepared.current_fixtures):,}"
    )

    if preflight_only:
        print(f"{key}: source and fixture freshness preflight passed; model fitting skipped")
        return

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

    data_meta = {
        "sources": source_meta,
        "backtest": backtest_meta,
        "updated_at": utc_now_iso(),
    }
    output_path = APP_DATA / f"{key}.json"
    build_snapshot(
        cfg, prepared, fit, simulation,
        quotes, match_quotes, market_meta,
        kalshi_quotes, kalshi_match_quotes, kalshi_meta,
        total_goal_quotes, kalshi_total_goal_quotes,
        data_meta, output_path,
    )
    print(f"Wrote {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", choices=["epl", "mls", "all"], default="all")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--steps", type=int, default=None)
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Fetch and reconcile current data, validate freshness, then exit before model fitting.",
    )
    args = parser.parse_args()
    leagues = ("epl", "mls") if args.league == "all" else (args.league,)
    errors = []
    for key in leagues:
        try:
            run_league(
                key,
                refresh=args.refresh,
                steps=args.steps,
                preflight_only=args.preflight_only,
            )
        except Exception as exc:
            errors.append((key, exc))
            traceback.print_exc()
    if errors:
        for key, exc in errors:
            print(f"ERROR [{key}]: {exc}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
