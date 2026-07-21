from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from .api_football import FINAL_STATUSES
from .config import BUCKET_DAYS, LeagueConfig, OVERRIDE_DIR
from .utils import deterministic_color, load_csv, normalize_name, slugify


ALIASES = {
    "newyorkredbulls": "redbullnewyork",
    "montrealimpact": "cfmontreal",
    "intermiami": "intermiamicf",
    "lafc": "losangelesfc",
    "losangelesgalaxy": "lagalaxy",
    "stlouiscity": "stlouiscitysc",
    "dcunited": "dcunited",
    "brightonhovealbion": "brightonandhovealbion",
    "wolverhamptonwanderers": "wolves",
    "tottenham": "tottenhamhotspur",
    "manchesterutd": "manchesterunited",
    "manchestercity": "manchestercity",
    "nottinghamforest": "nottinghamforest",
}


@dataclass
class PreparedLeague:
    history: pd.DataFrame
    current_fixtures: pd.DataFrame
    teams: list[dict[str, Any]]
    team_index: dict[int, int]
    current_team_ids: list[int]
    n_times: int
    time_origin: datetime
    bucket_days: int
    value_by_id: dict[int, float]


def _override_rows(cfg: LeagueConfig) -> list[dict[str, str]]:
    return load_csv(OVERRIDE_DIR / f"teams_{cfg.key}.csv")


def _match_override(name: str, overrides: list[dict[str, str]]) -> dict[str, str] | None:
    normalized = ALIASES.get(normalize_name(name), normalize_name(name))
    best = None
    for row in overrides:
        candidate = ALIASES.get(normalize_name(row.get("name", "")), normalize_name(row.get("name", "")))
        if candidate == normalized:
            return row
        if candidate and (candidate in normalized or normalized in candidate):
            best = row
    return best


def _conference(api_id: int, name: str, standings_groups: dict[int, str], override: dict[str, str] | None, cfg: LeagueConfig) -> str:
    if cfg.key == "epl":
        return "Premier League"
    group = standings_groups.get(api_id, "")
    if "east" in group.lower():
        return "East"
    if "west" in group.lower():
        return "West"
    if override and override.get("conference"):
        return override["conference"]
    return "Unknown"


def prepare_league(
    cfg: LeagueConfig,
    fixture_rows: list[dict[str, Any]],
    api_teams: list[dict[str, Any]],
    standings_groups: dict[int, str],
) -> PreparedLeague:
    if not api_teams:
        raise ValueError(f"API-Football returned no current teams for {cfg.name} {cfg.current_season}.")
    overrides = _override_rows(cfg)
    teams: list[dict[str, Any]] = []
    for item in api_teams:
        override = _match_override(item["name"], overrides)
        market_value = float((override or {}).get("market_value") or 1.0)
        short = item.get("code") or (override or {}).get("short") or "".join(word[0] for word in item["name"].split())[:4].upper()
        teams.append(
            {
                "api_id": int(item["api_id"]),
                "name": item["name"],
                "short": short,
                "conference": _conference(int(item["api_id"]), item["name"], standings_groups, override, cfg),
                "market_value": market_value,
                "color": (override or {}).get("color") or deterministic_color(item["name"]),
                "slug": slugify(item["name"]),
                "logo": item.get("logo"),
                "venue": item.get("venue"),
                "venue_city": item.get("venue_city"),
                "venue_surface": item.get("venue_surface"),
            }
        )

    current_ids = [team["api_id"] for team in teams]
    current_set = set(current_ids)
    current = [row for row in fixture_rows if row["season"] == cfg.current_season and row["home_id"] in current_set and row["away_id"] in current_set]
    if cfg.key == "mls":
        regular = [row for row in current if "regular" in row["round"].lower()]
        if regular:
            current = regular
    if not current:
        raise ValueError(f"API-Football returned no current-season fixtures for {cfg.name}.")

    completed = [
        row for row in fixture_rows
        if row["status"] in FINAL_STATUSES and row["home_goals"] is not None and row["away_goals"] is not None
    ]
    if len(completed) < 80:
        raise ValueError(
            f"Only {len(completed)} completed historical fixtures were available. "
            "The Bayesian model needs at least 80. The free API plan may not expose enough seasons yet."
        )

    all_ids = sorted({row["home_id"] for row in completed} | {row["away_id"] for row in completed} | current_set)
    team_index = {team_id: index for index, team_id in enumerate(all_ids)}
    name_by_id = {row["home_id"]: row["home_name"] for row in fixture_rows}
    name_by_id.update({row["away_id"]: row["away_name"] for row in fixture_rows})

    override_values = {normalize_name(row.get("name", "")): float(row.get("market_value") or 1.0) for row in overrides}
    current_value = {team["api_id"]: team["market_value"] for team in teams}
    known_values = [value for value in current_value.values() if value > 0]
    default_value = float(np.median(known_values)) if known_values else 1.0
    value_by_id: dict[int, float] = {}
    for team_id in all_ids:
        if team_id in current_value:
            value_by_id[team_id] = current_value[team_id]
        else:
            value_by_id[team_id] = override_values.get(normalize_name(name_by_id.get(team_id, "")), default_value)

    parsed_dates = [datetime.fromisoformat(row["date"].replace("Z", "+00:00")) for row in completed]
    origin = min(parsed_dates).astimezone(timezone.utc)
    now = datetime.now(timezone.utc)
    max_date = max(max(parsed_dates), now)
    n_times = max(2, int(math.floor((max_date - origin).days / BUCKET_DAYS)) + 1)

    history_rows = []
    for row, when in zip(completed, parsed_dates):
        bucket = min(n_times - 1, max(0, int((when.astimezone(timezone.utc) - origin).days // BUCKET_DAYS)))
        hv = max(value_by_id[row["home_id"]], 0.01)
        av = max(value_by_id[row["away_id"]], 0.01)
        history_rows.append(
            {
                **row,
                "home_idx": team_index[row["home_id"]],
                "away_idx": team_index[row["away_id"]],
                "time_idx": bucket,
                "value_diff": math.log(hv / av),
            }
        )

    current_rows = []
    for row in current:
        when = datetime.fromisoformat(row["date"].replace("Z", "+00:00")).astimezone(timezone.utc)
        current_rows.append({**row, "future_bucket": max(0, int((when - now).days // BUCKET_DAYS))})

    return PreparedLeague(
        history=pd.DataFrame(history_rows).sort_values("timestamp").reset_index(drop=True),
        current_fixtures=pd.DataFrame(current_rows).sort_values("timestamp").reset_index(drop=True),
        teams=teams,
        team_index=team_index,
        current_team_ids=current_ids,
        n_times=n_times,
        time_origin=origin,
        bucket_days=BUCKET_DAYS,
        value_by_id=value_by_id,
    )
