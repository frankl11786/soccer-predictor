from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from .api_football import FINAL_STATUSES
from .config import BUCKET_DAYS, LeagueConfig
from .identity import team_catalog


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


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Cross-source dedupe uses canonical teams, season, and date. Prefer rows with a final score.
    chosen: dict[tuple, dict[str, Any]] = {}
    for row in rows:
        date_key = str(row.get("date") or "")[:10]
        key = (row.get("season"), date_key, row.get("home_id"), row.get("away_id"))
        previous = chosen.get(key)
        if previous is None:
            chosen[key] = row
            continue
        previous_final = previous.get("status") in FINAL_STATUSES
        current_final = row.get("status") in FINAL_STATUSES
        if current_final and not previous_final:
            chosen[key] = row
    return sorted(chosen.values(), key=lambda row: (row.get("timestamp") or 0, str(row.get("fixture_id"))))


def prepare_league(cfg: LeagueConfig, fixture_rows: list[dict[str, Any]]) -> PreparedLeague:
    teams = team_catalog(cfg)
    if not teams:
        raise ValueError(f"No team catalog exists for {cfg.name}.")
    fixture_rows = _dedupe(fixture_rows)
    current_ids = [team["api_id"] for team in teams]
    current_set = set(current_ids)
    current = [
        row for row in fixture_rows
        if row["season"] == cfg.current_season and row["home_id"] in current_set and row["away_id"] in current_set
    ]
    if cfg.key == "mls":
        regular = [row for row in current if "regular" in str(row.get("round", "")).lower()]
        if regular:
            current = regular
    if not current:
        raise ValueError(f"No current-season fixtures were found for {cfg.name}.")

    completed = [
        row for row in fixture_rows
        if row["status"] in FINAL_STATUSES and row["home_goals"] is not None and row["away_goals"] is not None
    ]
    if len(completed) < 80:
        raise ValueError(f"Only {len(completed)} completed fixtures were available; at least 80 are required.")

    all_ids = sorted({row["home_id"] for row in completed} | {row["away_id"] for row in completed} | current_set)
    team_index = {team_id: index for index, team_id in enumerate(all_ids)}
    name_by_id = {row["home_id"]: row["home_name"] for row in fixture_rows}
    name_by_id.update({row["away_id"]: row["away_name"] for row in fixture_rows})

    current_value = {team["api_id"]: team["market_value"] for team in teams}
    known_values = [value for value in current_value.values() if value > 0]
    default_value = float(np.median(known_values)) if known_values else 1.0
    value_by_id = {team_id: current_value.get(team_id, default_value) for team_id in all_ids}

    parsed_dates = [datetime.fromisoformat(str(row["date"]).replace("Z", "+00:00")) for row in completed]
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
        when = datetime.fromisoformat(str(row["date"]).replace("Z", "+00:00")).astimezone(timezone.utc)
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
