from __future__ import annotations

import calendar
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from .config import CACHE_DIR
from .utils import read_json, utc_now_iso, write_json

BASE_URLS = {
    "epl": "https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard",
    "mls": "https://site.api.espn.com/apis/site/v2/sports/soccer/usa.1/scoreboard",
}


def _cache_path(league: str, season: int) -> Path:
    return CACHE_DIR / "espn" / f"{league}_schedule_{season}.json"


def _fixture_id(league: str, event_id: str) -> str:
    digest = hashlib.sha1(f"espn:{league}:{event_id}".encode("utf-8")).hexdigest()[:16]
    return f"espn-{digest}"


def _parse_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def _pick_competitor(competitors: list[dict[str, Any]], side: str) -> dict[str, Any] | None:
    return next((c for c in competitors if c.get("homeAway") == side), None)


def _team_name(competitor: dict[str, Any]) -> str:
    team = competitor.get("team") or {}
    return str(team.get("displayName") or team.get("shortDisplayName") or team.get("name") or "").strip()


def _parse_event(
    event: dict[str, Any],
    league: str | int,
    fallback_season: int | None = None,
) -> dict[str, Any] | None:
    # The original MLS-only parser accepted ``(event, season)``. Keep that
    # calling convention while allowing the shared EPL/MLS source to provide
    # an explicit league for stable, league-specific fixture IDs.
    if fallback_season is None:
        fallback_season = int(league)
        league = "mls"

    event_id = str(event.get("id") or "").strip()
    competition = (event.get("competitions") or [{}])[0]
    competitors = competition.get("competitors") or []
    home = _pick_competitor(competitors, "home")
    away = _pick_competitor(competitors, "away")
    if not event_id or home is None or away is None:
        return None

    home_name, away_name = _team_name(home), _team_name(away)
    date_value = event.get("date") or competition.get("date")
    if not home_name or not away_name or not date_value:
        return None

    try:
        kickoff = datetime.fromisoformat(str(date_value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None

    status_type = ((competition.get("status") or event.get("status") or {}).get("type") or {})
    status_name = str(status_type.get("name") or "").upper()
    status_description = str(
        status_type.get("description")
        or status_type.get("detail")
        or status_type.get("shortDetail")
        or status_name
        or "Not Started"
    )
    completed = bool(status_type.get("completed")) or status_name in {
        "STATUS_FINAL", "STATUS_FULL_TIME", "STATUS_FINAL_AET", "STATUS_FINAL_PEN",
    }

    home_score = _parse_int(home.get("score"))
    away_score = _parse_int(away.get("score"))
    if completed and (home_score is None or away_score is None):
        completed = False

    status_upper = f"{status_name} {status_description}".upper()
    if completed:
        short_status = "FT"
    elif "POSTPON" in status_upper:
        short_status = "PST"
    elif "CANCEL" in status_upper:
        short_status = "CANC"
    elif "ABANDON" in status_upper:
        short_status = "ABD"
    else:
        short_status = "NS"

    venue = competition.get("venue") or {}
    season_year = _parse_int((event.get("season") or {}).get("year")) or fallback_season
    return {
        "fixture_id": _fixture_id(league, event_id),
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
    return [
        (f"{year}{month:02d}01", f"{year}{month:02d}{calendar.monthrange(year, month)[1]:02d}")
        for month in range(1, 13)
    ]


def _fetch_events(league: str, season: int) -> tuple[list[dict[str, Any]], list[str], int]:
    if league not in BASE_URLS:
        raise ValueError(f"Unsupported ESPN league: {league}")
    session = requests.Session()
    session.headers.update({"User-Agent": "TouchlineForecast/1.0"})
    events: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    successful_requests = 0

    for start, end in _month_windows(season):
        try:
            response = session.get(
                BASE_URLS[league],
                params={"dates": f"{start}-{end}", "limit": 1000},
                timeout=45,
            )
            response.raise_for_status()
            payload = response.json()
            successful_requests += 1
            for event in payload.get("events", []):
                event_id = str(event.get("id") or "")
                if event_id:
                    events[event_id] = event
        except (requests.RequestException, ValueError) as exc:
            errors.append(f"{start}-{end}: {exc}")

    if not events:
        try:
            response = session.get(
                BASE_URLS[league],
                params={"dates": str(season), "limit": 1000},
                timeout=45,
            )
            response.raise_for_status()
            payload = response.json()
            successful_requests += 1
            for event in payload.get("events", []):
                event_id = str(event.get("id") or "")
                if event_id:
                    events[event_id] = event
        except (requests.RequestException, ValueError) as exc:
            errors.append(f"whole-year fallback: {exc}")

    return list(events.values()), errors, successful_requests


def fetch_league_rows(
    league: str,
    season: int,
    refresh: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = _cache_path(league, season)
    cached_rows = (read_json(path, default=[]) or []) if path.exists() else []
    cached_rows = [
        row for row in cached_rows
        if isinstance(row, dict) and row.get("season") == season
    ]
    if path.exists() and not refresh:
        if cached_rows:
            return cached_rows, {
                "source": "ESPN",
                "purpose": f"current {league.upper()} schedule and results",
                "season": season,
                "fixtures_received": len(cached_rows),
                "cached": True,
                "cache_fallback": False,
                "live_request_attempted": False,
                "live_request_failed": False,
                "successful_requests": 0,
                "updated_at": utc_now_iso(),
            }

    events, errors, successful_requests = _fetch_events(league, season)
    live_request_failed = successful_requests == 0
    rows = [
        row for event in events
        if (row := _parse_event(event, league, season)) is not None and row["season"] == season
    ]
    rows = sorted({row["fixture_id"]: row for row in rows}.values(),
                  key=lambda row: (row["timestamp"], row["fixture_id"]))
    cache_fallback = live_request_failed and bool(cached_rows)
    if cache_fallback:
        rows = cached_rows
    elif rows:
        write_json(path, rows)

    metadata = {
        "source": "ESPN",
        "purpose": f"current {league.upper()} schedule and results",
        "season": season,
        "fixtures_received": len(rows),
        "request_errors": errors,
        "cached": cache_fallback,
        "cache_fallback": cache_fallback,
        "live_request_attempted": True,
        "live_request_failed": live_request_failed,
        "successful_requests": successful_requests,
        "updated_at": utc_now_iso(),
    }
    print(
        f"[ESPN] {league.upper()} {season}: {len(rows):,} fixtures; "
        f"live_request_failed={live_request_failed}; cache_fallback={cache_fallback}; "
        f"errors={errors}"
    )
    return rows, metadata


def fetch_mls_schedule(
    season: int,
    refresh: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Backward-compatible wrapper for existing tests/tooling."""
    return fetch_league_rows("mls", season, refresh=refresh)
