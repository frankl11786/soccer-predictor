from __future__ import annotations

import argparse
import traceback

from .api_football import ApiFootballClient, current_team_rows, fetch_league_bundle, fixture_rows, standings_groups
from .bayes import fit_model
from .config import APP_DATA, LEAGUES
from .data_prep import prepare_league
from .output import build_snapshot
from .polymarket import fetch_winner_quotes
from .simulate import simulate_epl, simulate_mls
from .utils import utc_now_iso


def run_league(key: str, refresh: bool, steps: int | None = None) -> None:
    cfg = LEAGUES[key]
    print(f"\n=== {cfg.name} ===")
    client = ApiFootballClient.from_environment()
    bundle = fetch_league_bundle(client, cfg, refresh=refresh)
    raw_fixtures = fixture_rows(bundle)
    api_teams = current_team_rows(bundle)
    groups = standings_groups(bundle)
    prepared = prepare_league(cfg, raw_fixtures, api_teams, groups)
    print(f"Historical matches: {len(prepared.history):,}; current fixtures: {len(prepared.current_fixtures):,}")
    fit = fit_model(prepared, key, steps=steps or 5_000)
    print(f"Model fitted. Final ELBO loss: {fit.loss_final:,.1f}")
    simulation = simulate_epl(prepared, fit, cfg.simulations) if key == "epl" else simulate_mls(prepared, fit, cfg.simulations)
    quotes, market_meta = fetch_winner_quotes(cfg.polymarket_queries, [team["name"] for team in prepared.teams])
    api_meta = {
        "source": "API-Football",
        "league_id": cfg.api_league_id,
        "season": cfg.current_season,
        "seasons_requested": list(cfg.history_seasons),
        "unavailable_seasons": bundle["unavailable"],
        "updated_at": utc_now_iso(),
    }
    output_path = APP_DATA / f"{key}.json"
    build_snapshot(cfg, prepared, fit, simulation, quotes, market_meta, api_meta, output_path)
    print(f"Wrote {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh live Bayesian EPL and MLS forecasts.")
    parser.add_argument("--league", choices=["epl", "mls", "all"], default="all")
    parser.add_argument("--refresh", action="store_true", help="Ignore cached API-Football responses.")
    parser.add_argument("--steps", type=int, default=None, help="Override SVI optimization steps.")
    args = parser.parse_args()
    keys = list(LEAGUES) if args.league == "all" else [args.league]
    failures = []
    for key in keys:
        try:
            run_league(key, args.refresh, args.steps)
        except Exception as exc:  # keep the other league running and provide a useful log
            failures.append((key, str(exc)))
            print(f"ERROR [{key}]: {exc}")
            traceback.print_exc()
    if failures:
        raise SystemExit("; ".join(f"{key}: {message}" for key, message in failures))


if __name__ == "__main__":
    main()
