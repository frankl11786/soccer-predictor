from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .config import CACHE_DIR
from .utils import read_json, utc_now_iso, write_json


def _client():
    try:
        from itscalledsoccer import AmericanSoccerAnalysis
    except ImportError:
        from itscalledsoccer.client import AmericanSoccerAnalysis
    return AmericanSoccerAnalysis()


def _pick(row: dict[str, Any], *names: str, default=None):
    for name in names:
        if name in row and pd.notna(row[name]):
            return row[name]
    return default


def _fixture_id(game_id: str) -> str:
    digest = hashlib.sha1(f"asa:{game_id}".encode("utf-8")).hexdigest()[:16]
    return f"asa-{digest}"


def _to_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    clean = frame.where(pd.notnull(frame), None)
    return clean.to_dict("records")


def _cache_path(season: int) -> Path:
    return CACHE_DIR / "asa" / f"mls_games_{season}.json"


def _fetch_season(client, season: int, refresh: bool) -> list[dict[str, Any]]:
    path = _cache_path(season)
    if path.exists() and not refresh:
        return read_json(path, default=[]) or []
    # Current versions of itscalledsoccer use `season_name` (singular).
    # Keep a fallback for older package versions that used `seasons`.
    try:
        frame = client.get_games(leagues="mls", season_name=str(season))
    except TypeError:
        frame = client.get_games(leagues="mls", seasons=str(season))
    records = _to_records(frame)
    write_json(path, records)
    return records


def fetch_mls_rows(seasons: tuple[int, ...], refresh: bool = False) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    client = _client()
    team_frame = client.get_teams(leagues="mls")
    team_names = {
        str(row.get("team_id")): str(row.get("team_name"))
        for row in _to_records(team_frame)
        if row.get("team_id") is not None and row.get("team_name")
    }
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for season in seasons:
        try:
            records = _fetch_season(client, season, refresh=refresh)
        except Exception as exc:  # The public wrapper can surface API/schema errors of several types.
            errors.append(f"{season}: {exc}")
            continue
        for record in records:
            game_id = str(_pick(record, "game_id", "id", default=""))
            home_team_id = str(_pick(record, "home_team_id", default=""))
            away_team_id = str(_pick(record, "away_team_id", default=""))
            home_name = str(_pick(record, "home_team_name", default=team_names.get(home_team_id, home_team_id)))
            away_name = str(_pick(record, "away_team_name", default=team_names.get(away_team_id, away_team_id)))
            date_value = _pick(record, "date_time_utc", "datetime_utc", "date_time", "date", "kickoff")
            if not game_id or not date_value or not home_name or not away_name:
                continue
            kickoff = pd.to_datetime(date_value, utc=True, errors="coerce")
            if pd.isna(kickoff):
                continue
            kickoff_py = kickoff.to_pydatetime()
            home_score = _pick(record, "home_score", "home_goals")
            away_score = _pick(record, "away_score", "away_goals")
            score_present = home_score is not None and away_score is not None
            raw_status = str(_pick(record, "status", "game_status", default=""))
            status_lower = raw_status.lower()
            is_final = score_present and (
                kickoff_py <= datetime.now(timezone.utc)
                or any(token in status_lower for token in ("final", "complete", "finished", "full"))
            )
            stage = str(_pick(record, "stage_name", "stage", "competition_stage", default="Regular Season"))
            rows.append(
                {
                    "fixture_id": _fixture_id(game_id),
                    "source": "American Soccer Analysis",
                    "date": kickoff_py.isoformat().replace("+00:00", "Z"),
                    "timestamp": int(kickoff_py.timestamp()),
                    "season": int(_pick(record, "season_name", "season", default=season)),
                    "round": stage,
                    "status": "FT" if is_final else "NS",
                    "status_long": "Match Finished" if is_final else (raw_status or "Not Started"),
                    "home_id": 0,
                    "home_name": home_name,
                    "away_id": 0,
                    "away_name": away_name,
                    "home_goals": int(home_score) if is_final else None,
                    "away_goals": int(away_score) if is_final else None,
                    "penalty_home": _pick(record, "home_penalties"),
                    "penalty_away": _pick(record, "away_penalties"),
                    "venue_id": _pick(record, "stadium_id", "venue_id"),
                    "venue_name": _pick(record, "stadium_name", "venue_name"),
                }
            )
    metadata = {
        "source": "American Soccer Analysis",
        "purpose": "recent and current MLS fixtures/results",
        "seasons_requested": list(seasons),
        "fixtures_received": len(rows),
        "errors": errors,
        "updated_at": utc_now_iso(),
    }
    return rows, metadata
