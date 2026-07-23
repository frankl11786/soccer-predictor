from __future__ import annotations

import calendar
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from .config import CACHE_DIR
from .utils import read_json, utc_now_iso, write_json

BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/usa.1/scoreboard"


def _cache_path(season: int) -> Path:
    return CACHE_DIR / "espn" / f"mls_schedule_{season}.json"


def _fixture_id(event_id: str) -> str:
    digest = hashlib.sha1(f"espn:{event_id}".encode("utf-8")).hexdigest()[:16]
    return f"espn-{digest}"


def _parse_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def _pick_competitor(competitors: list[dict[str, Any]], side: str) -> dict[str, Any] | None:
    for competitor in competitors:
        if competitor.get("homeAway") == side:
            return competitor
    return None


def _team_name(competitor: dict[str, Any]) -> str:
    team = competitor.get("team") or {}
    return str(
        team.get("displayName")
        or team.get("shortDisplayName")
        or team.get("name")
        or ""
    ).strip()


def _parse_event(event: dict[str, Any], fallback_season: int) -> dict[str, Any] | None:
    event_id = str(event.get("id") or "").strip()
    competition = (event.get("competitions") or [{}])[0]
    competitors = competition.get("competitors") or []
    home = _pick_competitor(competitors, "home")
    away = _pick_competitor(competitors, "away")
    if not event_id or home is None or away is None:
        return None

    home_name = _team_name(home)
    away_name = _team_name(away)
    date_value = event.get("date") or competition.get("date")
    if not home_name or not away_name or not date_value:
        return None

    try:
        kickoff = datetime.fromisoformat(str(date_value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None

    status_type = ((event.get("status") or {}).get("type") or {})
    status_name = str(status_type.get("name") or "").upper()
    status_description = str(
        status_type.get("description")
        or status_type.get("detail")
        or status_name
        or "Not Started"
    )
    completed = bool(status_type.get("completed")) or status_name in {
        "STATUS_FINAL",
        "STATUS_FULL_TIME",
        "STATUS_FINAL_AET",
        "STATUS_FINAL_PEN",
    }

    home_score = _parse_int(home.get("score"))
    away_score = _parse_int(away.get("score"))
    if completed and (home_score is None or away_score is None):
        completed = False

    if completed:
        short_status = "FT"
    elif "POSTPON" in status_name:
        short_status = "PST"
    elif "CANCEL" in status_name:
        short_status = "CANC"
    else:
        short_status = "NS"

    venue = competition.get("venue") or {}
    season = event.get("season") or {}
    season_year = _parse_int(season.get("year")) or fallback_season

    return {
        "fixture_id": _fixture_id(event_id),
        "source": "ESPN",
        "date": kickoff.isoformat().replace("+00:00", "Z"),
        "timestamp": int(kickoff.timestamp()),
        "season": season_year,
        "round": "Regular Season",
        "status": short_status,
        "status_long": status_description,
        "home_id": 0,
        "home_name": home_name,
        "away_id": 0,
        "away_name": away_name,
        "home_goals": home_score if completed else None,
        "away_goals": away_score if completed else None,
        "penalty_home": _parse_int(home.get("shootoutScore")),
        "penalty_away": _parse_int(away.get("shootoutScore")),
        "venue_id": venue.get("id"),
        "venue_name": venue.get("fullName") or venue.get("name"),
    }


def _month_windows(year: int) -> list[tuple[str, str]]:
    windows: list[tuple[str, str]] = []
    for month in range(1, 13):
        last_day = calendar.monthrange(year, month)[1]
        start = f"{year}{month:02d}01"
        end = f"{year}{month:02d}{last_day:02d}"
        windows.append((start, end))
    return windows


def _fetch_events(season: int) -> tuple[list[dict[str, Any]], list[str]]:
    session = requests.Session()
    session.headers.update({"User-Agent": "TouchlineForecast/1.0"})
    events: dict[str, dict[str, Any]] = {}
    errors: list[str] = []

    # Monthly ranges avoid the scoreboard's normal per-response event cap.
    for start, end in _month_windows(season):
        try:
            response = session.get(
                BASE_URL,
                params={"dates": f"{start}-{end}", "limit": 1000},
                timeout=45,
            )
            response.raise_for_status()
            payload = response.json()
            for event in payload.get("events", []):
                event_id = str(event.get("id") or "")
                if event_id:
                    events[event_id] = event
        except (requests.RequestException, ValueError) as exc:
            errors.append(f"{start}-{end}: {exc}")

    # Some ESPN deployments support a whole-year query. Use it as a fallback
    # if monthly requests returned an unexpectedly small schedule.
    if len(events) < 450:
        try:
            response = session.get(
                BASE_URL,
                params={"dates": str(season), "limit": 1000},
                timeout=45,
            )
            response.raise_for_status()
            payload = response.json()
            for event in payload.get("events", []):
                event_id = str(event.get("id") or "")
                if event_id:
                    events[event_id] = event
        except (requests.RequestException, ValueError) as exc:
            errors.append(f"whole-year fallback: {exc}")

    return list(events.values()), errors


def fetch_mls_schedule(
    season: int,
    refresh: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = _cache_path(season)
    if path.exists() and not refresh:
        rows = read_json(path, default=[]) or []
        return rows, {
            "source": "ESPN",
            "purpose": "complete current MLS regular-season schedule",
            "season": season,
            "fixtures_received": len(rows),
            "cached": True,
            "updated_at": utc_now_iso(),
        }

    events, errors = _fetch_events(season)
    rows: list[dict[str, Any]] = []
    for event in events:
        row = _parse_event(event, season)
        if row is not None and row["season"] == season:
            rows.append(row)

    unique = {row["fixture_id"]: row for row in rows}
    rows = sorted(unique.values(), key=lambda row: (row["timestamp"], row["fixture_id"]))
    write_json(path, rows)

    metadata = {
        "source": "ESPN",
        "purpose": "complete current MLS regular-season schedule",
        "season": season,
        "fixtures_received": len(rows),
        "request_errors": errors,
        "cached": False,
        "updated_at": utc_now_iso(),
    }
    print(f"[ESPN] MLS {season}: {len(rows):,} fixtures; errors={errors}")
    return rows, metadata
