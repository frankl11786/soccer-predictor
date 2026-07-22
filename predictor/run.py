from __future__ import annotations

import argparse
import traceback

from .api_football import ApiFootballClient, fetch_history_rows
from .asa import fetch_mls_rows
from .bayes import fit_model
from .config import APP_DATA, LEAGUES
from .data_prep import prepare_league
from .identity import canonicalize_fixture_rows
from .openfootball import fetch_epl_rows
from .output import build_snapshot
from .polymarket import fetch_winner_quotes
from .simulate import simulate_epl, simulate_mls
from .utils import utc_now_iso


def run_league(key: str, refresh: bool, steps: int | None = None) -> None:
    cfg = LEAGUES[key]
    print(f"\n=== {cfg.name} ===")
    client = ApiFootballClient.from_environment()
    history_rows, api_meta = fetch_history_rows(client, cfg, refresh=refresh)
    if key == "epl":
        supplemental_rows, current_meta = fetch_epl_rows(cfg.supplemental_seasons, refresh=refresh)
    else:
        supplemental_rows, current_meta = fetch_mls_rows(cfg.supplemental_seasons, refresh=refresh)

    raw_fixtures = canonicalize_fixture_rows(cfg, history_rows + supplemental_rows)
    prepared = prepare_league(cfg, raw_fixtures)
    print(f"Historical matches: {len(prepared.history):,}; current fixtures: {len(prepared.current_fixtures):,}")
    fit = fit_model(prepared, key, steps=steps or 5_000)
    print(f"Model fitted. Final ELBO loss: {fit.loss_final:,.1f}")
    simulation = simulate_epl(prepared, fit, cfg.simulations) if key == "epl" else simulate_mls(prepared, fit, cfg.simulations)
    quotes, market_meta = fetch_winner_quotes(cfg.polymarket_queries, [team["name"] for team in prepared.teams])
    data_meta = {
        "sources": [api_meta, current_meta],
        "updated_at": utc_now_iso(),
    }
    output_path = APP_DATA / f"{key}.json"
    build_snapshot(cfg, prepared, fit, simulation, quotes, market_meta, data_meta, output_path)
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
