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
    if frame is None or frame.empty:
        return []
    clean = frame.where(pd.notnull(frame), None)
    return clean.to_dict("records")


def _cache_path(season: int) -> Path:
    return CACHE_DIR / "asa" / f"mls_games_{season}.json"


def _filter_frame_to_season(frame: pd.DataFrame, season: int) -> pd.DataFrame:
    """Filter an unfiltered ASA games frame locally when the API season filter returns nothing."""
    if frame is None or frame.empty:
        return pd.DataFrame()

    for column in ("season_name", "season"):
        if column in frame.columns:
            values = frame[column].astype(str).str.extract(r"(\d{4})", expand=False)
            return frame.loc[values == str(season)].copy()

    for column in ("date_time_utc", "datetime_utc", "date_time", "date", "kickoff"):
        if column in frame.columns:
            dates = pd.to_datetime(frame[column], utc=True, errors="coerce")
            return frame.loc[dates.dt.year == season].copy()

    return pd.DataFrame()


def _fetch_season(client, season: int, refresh: bool) -> list[dict[str, Any]]:
    path = _cache_path(season)
    if path.exists() and not refresh:
        return read_json(path, default=[]) or []

    # itscalledsoccer 2.1 uses season_name for get_games.
    # Some documentation and older releases used seasons, so retain a compatibility fallback.
    frame = None
    errors: list[str] = []

    try:
        frame = client.get_games(leagues="mls", season_name=str(season))
    except TypeError as exc:
        errors.append(f"season_name call: {exc}")
        try:
            frame = client.get_games(leagues="mls", seasons=str(season))
        except Exception as fallback_exc:
            errors.append(f"seasons call: {fallback_exc}")
    except Exception as exc:
        errors.append(f"season_name call: {exc}")

    # If the filtered endpoint returns no rows, retrieve all MLS games and filter locally.
    if frame is None or frame.empty:
        try:
            all_games = client.get_games(leagues="mls")
            frame = _filter_frame_to_season(all_games, season)
        except Exception as exc:
            errors.append(f"unfiltered fallback: {exc}")

    if frame is None:
        frame = pd.DataFrame()

    print(
        f"[ASA] season={season} rows={len(frame):,} "
        f"columns={list(frame.columns)} errors={errors}"
    )
    if not frame.empty:
        sample = frame.iloc[0].where(pd.notnull(frame.iloc[0]), None).to_dict()
        print(f"[ASA] season={season} sample={sample}")

    records = _to_records(frame)
    write_json(path, records)
    return records


def _safe_year(value: Any, fallback: int) -> int:
    if value is None:
        return fallback
    try:
        match = pd.Series([str(value)]).str.extract(r"(\d{4})", expand=False).iloc[0]
        return int(match) if pd.notna(match) else fallback
    except Exception:
        return fallback


def fetch_mls_rows(
    seasons: tuple[int, ...],
    refresh: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    client = _client()

    team_names: dict[str, str] = {}
    try:
        team_frame = client.get_teams(leagues="mls")
        for row in _to_records(team_frame):
            team_id = _pick(row, "team_id", "id")
            team_name = _pick(row, "team_name", "name")
            if team_id is not None and team_name:
                team_names[str(team_id)] = str(team_name)
    except Exception as exc:
        print(f"[ASA] team lookup failed: {exc}")

    rows: list[dict[str, Any]] = []
    errors: list[str] = []

    for season in seasons:
        try:
            records = _fetch_season(client, season, refresh=refresh)
        except Exception as exc:
            errors.append(f"{season}: {exc}")
            print(f"[ASA] season={season} fetch failed: {exc}")
            continue

        parsed_for_season = 0

        for record in records:
            game_id = str(_pick(record, "game_id", "id", default=""))

            home_team_id = str(
                _pick(
                    record,
                    "home_team_id",
                    "team_home_id",
                    "home_id",
                    default="",
                )
            )
            away_team_id = str(
                _pick(
                    record,
                    "away_team_id",
                    "team_away_id",
                    "away_id",
                    default="",
                )
            )

            home_name = str(
                _pick(
                    record,
                    "home_team_name",
                    "team_home_name",
                    "home_name",
                    default=team_names.get(home_team_id, ""),
                )
            )
            away_name = str(
                _pick(
                    record,
                    "away_team_name",
                    "team_away_name",
                    "away_name",
                    default=team_names.get(away_team_id, ""),
                )
            )

            date_value = _pick(
                record,
                "date_time_utc",
                "datetime_utc",
                "date_time",
                "date",
                "kickoff",
                "match_date",
            )

            if not game_id or not date_value or not home_name or not away_name:
                continue

            kickoff = pd.to_datetime(date_value, utc=True, errors="coerce")
            if pd.isna(kickoff):
                continue
            kickoff_py = kickoff.to_pydatetime()

            home_score = _pick(
                record,
                "home_score",
                "home_goals",
                "score_home",
            )
            away_score = _pick(
                record,
                "away_score",
                "away_goals",
                "score_away",
            )

            raw_status = str(
                _pick(record, "status", "game_status", default="")
            )
            status_lower = raw_status.lower()
            score_present = home_score is not None and away_score is not None
            is_final = score_present and (
                kickoff_py <= datetime.now(timezone.utc)
                or any(
                    token in status_lower
                    for token in ("final", "complete", "finished", "fulltime", "full time")
                )
            )

            stage = str(
                _pick(
                    record,
                    "stage_name",
                    "stage",
                    "competition_stage",
                    default="Regular Season",
                )
            )

            season_value = _safe_year(
                _pick(record, "season_name", "season"),
                fallback=kickoff_py.year,
            )

            rows.append(
                {
                    "fixture_id": _fixture_id(game_id),
                    "source": "American Soccer Analysis",
                    "date": kickoff_py.isoformat().replace("+00:00", "Z"),
                    "timestamp": int(kickoff_py.timestamp()),
                    "season": season_value,
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
            parsed_for_season += 1

        print(
            f"[ASA] season={season} raw_records={len(records):,} "
            f"parsed_fixtures={parsed_for_season:,}"
        )

    metadata = {
        "source": "American Soccer Analysis",
        "purpose": "recent and current MLS fixtures/results",
        "seasons_requested": list(seasons),
        "fixtures_received": len(rows),
        "errors": errors,
        "updated_at": utc_now_iso(),
    }

    print(
        f"[ASA] total parsed fixtures={len(rows):,}; "
        f"errors={errors}"
    )

    return rows, metadata
