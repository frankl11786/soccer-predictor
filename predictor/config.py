from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_DATA = ROOT / "app" / "data"
CACHE_DIR = ROOT / "data" / "cache"
MODEL_DIR = ROOT / "data" / "model"
OVERRIDE_DIR = ROOT / "model" / "data"


@dataclass(frozen=True)
class LeagueConfig:
    key: str
    name: str
    api_league_id: int
    current_season: int
    api_history_seasons: tuple[int, ...]
    supplemental_seasons: tuple[int, ...]
    competition: str
    simulations: int
    polymarket_queries: tuple[str, ...]
    polymarket_event_slug: str | None
    polymarket_league_terms: tuple[str, ...]
    polymarket_match_lookahead_days: int
    polymarket_match_max_fixtures: int
    season_label: str


LEAGUES = {
    "epl": LeagueConfig(
        key="epl",
        name="English Premier League",
        api_league_id=39,
        current_season=2026,
        api_history_seasons=(2022, 2023, 2024),
        supplemental_seasons=(2025, 2026),
        competition="epl",
        simulations=20_000,
        polymarket_queries=(
            "Premier League winner 2026 2027",
            "English Premier League winner",
            "Premier League champion",
        ),
        # A known event slug is safer than broad search because it prevents
        # unrelated Premier League markets from being matched to clubs.
        polymarket_event_slug="epl-2027-champion-20260701200428749",
        polymarket_league_terms=("EPL", "Premier League", "English Premier League"),
        polymarket_match_lookahead_days=21,
        polymarket_match_max_fixtures=60,
        season_label="2026/27",
    ),
    "mls": LeagueConfig(
        key="mls",
        name="Major League Soccer",
        api_league_id=253,
        current_season=2026,
        api_history_seasons=(2022, 2023, 2024),
        supplemental_seasons=(2025, 2026),
        competition="mls",
        simulations=20_000,
        polymarket_queries=(
            "MLS Cup winner 2026",
            "Major League Soccer champion",
            "MLS Cup champion",
        ),
        polymarket_event_slug="mls-cup-winner-2026",
        polymarket_league_terms=("MLS", "Major League Soccer", "MLS Soccer"),
        polymarket_match_lookahead_days=21,
        polymarket_match_max_fixtures=60,
        season_label="2026",
    ),
}

MODEL_VERSION = "bayes-ss-poisson-v4-market-comparison"
BUCKET_DAYS = 28
POSTERIOR_SAMPLES = 800
SVI_STEPS = 5_000
SVI_LEARNING_RATE = 0.025
RANDOM_SEED = 260721

# EPL preseason calibration. The fitted state remains the largest single
# input, but it is blended with stable prior-season and squad-strength seeds
# until the new season provides enough evidence of its own.
EPL_RECENT_PERFORMANCE_WEIGHT = 0.45
EPL_ESTABLISHED_FITTED_WEIGHT = 0.50
EPL_PROMOTED_FITTED_WEIGHT = 0.08
EPL_CALIBRATION_MATCHES_TO_FADE = 10
EPL_SEED_UNCERTAINTY = 0.08

# Ratings evolve between forecast dates, but a small amount of mean reversion
# prevents a season-long random walk from becoming unrealistically extreme.
FUTURE_STATE_RETENTION = 0.985

# Current squad values are not applied retrospectively to old EPL seasons.
# They are used only for future fixtures with an informed, intentionally
# modest prior coefficient.
EPL_VALUE_COEFFICIENT_MEAN = 0.055
EPL_VALUE_COEFFICIENT_SD = 0.018
MLS_VALUE_COEFFICIENT_MEAN = 0.035
MLS_VALUE_COEFFICIENT_SD = 0.045

# This never changes the model probability. It only creates a visible warning
# when a liquid independent market and the model are unusually far apart.
MARKET_SANITY_THRESHOLD = 0.20
