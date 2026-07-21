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
    history_seasons: tuple[int, ...]
    competition: str
    simulations: int
    polymarket_queries: tuple[str, ...]
    season_label: str


LEAGUES = {
    "epl": LeagueConfig(
        key="epl",
        name="English Premier League",
        api_league_id=39,
        current_season=2026,
        history_seasons=(2023, 2024, 2025, 2026),
        competition="epl",
        simulations=20_000,
        polymarket_queries=(
            "Premier League winner 2026 2027",
            "English Premier League winner",
            "Premier League champion",
        ),
        season_label="2026/27",
    ),
    "mls": LeagueConfig(
        key="mls",
        name="Major League Soccer",
        api_league_id=253,
        current_season=2026,
        history_seasons=(2023, 2024, 2025, 2026),
        competition="mls",
        simulations=20_000,
        polymarket_queries=(
            "MLS Cup winner 2026",
            "Major League Soccer champion",
            "MLS Cup champion",
        ),
        season_label="2026",
    ),
}

MODEL_VERSION = "bayes-ss-poisson-v1"
BUCKET_DAYS = 28
POSTERIOR_SAMPLES = 800
SVI_STEPS = 5_000
SVI_LEARNING_RATE = 0.025
RANDOM_SEED = 260721
