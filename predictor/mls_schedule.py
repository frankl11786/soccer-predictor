from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from .config import APP_DATA, CACHE_DIR
from .utils import read_json, utc_now_iso, write_json

BASE_URL = "https://fixturedownload.com/feed/json/mls-{season}"
EXPECTED_FIXTURES = 510


def _cache_path(season: int) -> Path:
    # Kept beneath the existing ESPN cache directory so the current GitHub
    # Actions workflow persists it without requiring a hidden-workflow update.
    return CACHE_DIR / "espn" / f"fixture_download_mls_{season}.json"


def _fixture_id(season: int, match_number: Any, date_iso: str, home: str, away: str) -> str:
    if match_number not in (None, ""):
        return f"fd-mls-{season}-{match_number}"
    digest = hashlib.sha1(
        f"fixture-download:{season}:{date_iso}:{home}:{away}".encode("utf-8")
    ).hexdigest()[:16]
    return f"fd-{digest}"


def _as_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_kickoff(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace(" ", "T", 1)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def parse_fixture_download(payload: Any, season: int) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise ValueError("FixtureDownload response was not a JSON list")

    rows: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        home = str(item.get("HomeTeam") or "").strip()
        away = str(item.get("AwayTeam") or "").strip()
        kickoff = _parse_kickoff(item.get("DateUtc"))
        if not home or not away or kickoff is None:
            continue

        home_score = _as_int(item.get("HomeTeamScore"))
        away_score = _as_int(item.get("AwayTeamScore"))
        final = home_score is not None and away_score is not None
        date_iso = kickoff.isoformat().replace("+00:00", "Z")
        round_number = _as_int(item.get("RoundNumber"))
        rows.append(
            {
                "fixture_id": _fixture_id(
                    season,
                    item.get("MatchNumber"),
                    date_iso,
                    home,
                    away,
                ),
                "source": "FixtureDownload",
                "date": date_iso,
                "timestamp": int(kickoff.timestamp()),
                "season": season,
                "round": (
                    f"Regular Season - {round_number}"
                    if round_number is not None
                    else "Regular Season"
                ),
                "status": "FT" if final else "NS",
                "status_long": "Match Finished" if final else "Not Started",
                "home_id": 0,
                "home_name": home,
                "away_id": 0,
                "away_name": away,
                "home_goals": home_score if final else None,
                "away_goals": away_score if final else None,
                "penalty_home": None,
                "penalty_away": None,
                "venue_id": None,
                "venue_name": item.get("Location"),
            }
        )

    unique = {row["fixture_id"]: row for row in rows}
    return sorted(unique.values(), key=lambda row: (row["timestamp"], row["fixture_id"]))


def _snapshot_fallback(season: int) -> list[dict[str, Any]]:
    """Recover the last published complete schedule if the live feed is down."""
    snapshot = read_json(APP_DATA / "mls.json", default={}) or {}
    fixtures = snapshot.get("fixtures") or []
    teams = snapshot.get("teams") or []
    if len(fixtures) != EXPECTED_FIXTURES:
        return []

    name_by_slug = {
        str(team.get("slug")): str(team.get("name") or team.get("slug"))
        for team in teams
        if isinstance(team, dict) and team.get("slug")
    }
    rows: list[dict[str, Any]] = []
    for index, fixture in enumerate(fixtures, start=1):
        if not isinstance(fixture, dict):
            return []
        kickoff = _parse_kickoff(fixture.get("date"))
        home_slug = str(fixture.get("home") or "")
        away_slug = str(fixture.get("away") or "")
        if kickoff is None or not home_slug or not away_slug:
            return []
        final = (
            str(fixture.get("status") or "").lower() == "final"
            and fixture.get("home_score") is not None
            and fixture.get("away_score") is not None
        )
        rows.append(
            {
                "fixture_id": f"snapshot-mls-{season}-{index}",
                "source": "PublishedSnapshotFallback",
                "date": kickoff.isoformat().replace("+00:00", "Z"),
                "timestamp": int(kickoff.timestamp()),
                "season": season,
                "round": (
                    f"Regular Season - {fixture.get('round')}"
                    if fixture.get("round") not in (None, "")
                    else "Regular Season"
                ),
                "status": "FT" if final else "NS",
                "status_long": "Match Finished" if final else "Not Started",
                "home_id": 0,
                "home_name": name_by_slug.get(home_slug, home_slug),
                "away_id": 0,
                "away_name": name_by_slug.get(away_slug, away_slug),
                "home_goals": _as_int(fixture.get("home_score")) if final else None,
                "away_goals": _as_int(fixture.get("away_score")) if final else None,
                "penalty_home": None,
                "penalty_away": None,
                "venue_id": None,
                "venue_name": fixture.get("venue"),
            }
        )
    return rows


def _is_complete(rows: list[dict[str, Any]]) -> bool:
    return len(rows) == EXPECTED_FIXTURES


def fetch_complete_mls_schedule(
    season: int,
    refresh: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = _cache_path(season)
    errors: list[str] = []

    if path.exists() and not refresh:
        cached = read_json(path, default=[]) or []
        if _is_complete(cached):
            return cached, {
                "source": "FixtureDownload",
                "purpose": "complete current MLS regular-season schedule",
                "season": season,
                "fixtures_received": len(cached),
                "cached": True,
                "fallback": None,
                "errors": [],
                "updated_at": utc_now_iso(),
            }

    try:
        response = requests.get(
            BASE_URL.format(season=season),
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; TouchlineForecast/1.0)",
                "Accept": "application/json,text/plain,*/*",
                "Referer": f"https://fixturedownload.com/results/mls-{season}",
            },
            timeout=60,
        )
        response.raise_for_status()
        rows = parse_fixture_download(response.json(), season)
        if not _is_complete(rows):
            raise ValueError(
                f"live feed returned {len(rows)} fixtures; expected {EXPECTED_FIXTURES}"
            )
        write_json(path, rows)
        print(f"[FixtureDownload] MLS {season}: {len(rows):,} fixtures")
        return rows, {
            "source": "FixtureDownload",
            "purpose": "complete current MLS regular-season schedule",
            "season": season,
            "fixtures_received": len(rows),
            "cached": False,
            "fallback": None,
            "errors": [],
            "updated_at": utc_now_iso(),
        }
    except (requests.RequestException, ValueError, OSError) as exc:
        errors.append(f"live feed: {exc}")

    if path.exists():
        cached = read_json(path, default=[]) or []
        if _is_complete(cached):
            print(
                f"[FixtureDownload] MLS {season}: live feed failed; "
                f"using {len(cached):,}-fixture committed cache"
            )
            return cached, {
                "source": "FixtureDownload",
                "purpose": "complete current MLS regular-season schedule",
                "season": season,
                "fixtures_received": len(cached),
                "cached": True,
                "fallback": "committed source cache",
                "errors": errors,
                "updated_at": utc_now_iso(),
            }

    snapshot_rows = _snapshot_fallback(season)
    if _is_complete(snapshot_rows):
        write_json(path, snapshot_rows)
        print(
            f"[FixtureDownload] MLS {season}: source unavailable; "
            "using last published 510-fixture schedule"
        )
        return snapshot_rows, {
            "source": "PublishedSnapshotFallback",
            "purpose": "complete current MLS regular-season schedule",
            "season": season,
            "fixtures_received": len(snapshot_rows),
            "cached": True,
            "fallback": "last published site snapshot",
            "errors": errors,
            "updated_at": utc_now_iso(),
        }

    raise RuntimeError(
        "Could not obtain a complete MLS schedule from FixtureDownload, the committed "
        "source cache, or the last published snapshot. " + " | ".join(errors)
    )
