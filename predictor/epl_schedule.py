from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from .config import APP_DATA, CACHE_DIR
from .utils import read_json, utc_now_iso, write_json

BASE_URL = "https://fixturedownload.com/feed/json/epl-{season}"
EXPECTED_FIXTURES = 380
STALE_GRACE_HOURS = 18


def _cache_path(season: int) -> Path:
    # The existing Actions workflow already persists epl_schedule_*.json files
    # from data/cache/espn, so this gains durable cache fallback without a
    # workflow-file change.
    return CACHE_DIR / "espn" / f"epl_schedule_fixture_download_{season}.json"


def _fixture_id(season: int, match_number: Any, date_iso: str, home: str, away: str) -> str:
    if match_number not in (None, ""):
        return f"fd-epl-{season}-{match_number}"
    digest = hashlib.sha1(
        f"fixture-download-epl:{season}:{date_iso}:{home}:{away}".encode("utf-8")
    ).hexdigest()[:16]
    return f"fd-epl-{digest}"


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
    """Recover the last published EPL schedule/results spine if live sources are down."""
    snapshot = read_json(APP_DATA / "epl.json", default={}) or {}
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
        kickoff = _parse_kickoff(fixture.get("kickoff") or fixture.get("date"))
        home_slug = str(fixture.get("home") or "")
        away_slug = str(fixture.get("away") or "")
        if kickoff is None or not home_slug or not away_slug:
            return []
        final = (
            str(fixture.get("status") or "").lower() == "final"
            and fixture.get("home_score") is not None
            and fixture.get("away_score") is not None
        )
        round_value = fixture.get("round")
        rows.append(
            {
                "fixture_id": f"snapshot-epl-{season}-{index}",
                "source": "PublishedSnapshotFallback",
                "date": kickoff.isoformat().replace("+00:00", "Z"),
                "timestamp": int(kickoff.timestamp()),
                "season": season,
                "round": (
                    f"Regular Season - {round_value}"
                    if round_value not in (None, "")
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


def _stale_scheduled(
    rows: list[dict[str, Any]],
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff = current.timestamp() - STALE_GRACE_HOURS * 3600
    return [
        row
        for row in rows
        if str(row.get("status") or "").upper() in {"NS", "TBD", "SCHEDULED"}
        and int(row.get("timestamp") or 0) > 0
        and int(row.get("timestamp") or 0) < cutoff
    ]


def _validate_rows(rows: list[dict[str, Any]], label: str) -> None:
    if not _is_complete(rows):
        raise ValueError(f"{label} returned {len(rows)} fixtures; expected {EXPECTED_FIXTURES}")
    stale = _stale_scheduled(rows)
    if stale:
        sample = "; ".join(
            f"{row.get('home_name')} vs {row.get('away_name')} ({row.get('date')})"
            for row in stale[:5]
        )
        raise ValueError(
            f"{label} still has {len(stale)} scheduled fixtures more than "
            f"{STALE_GRACE_HOURS} hours past kickoff: {sample}"
        )


def fetch_complete_epl_schedule(
    season: int,
    refresh: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = _cache_path(season)
    errors: list[str] = []

    if path.exists() and not refresh:
        try:
            cached = read_json(path, default=[]) or []
            _validate_rows(cached, "committed FixtureDownload cache")
            return cached, {
                "source": "FixtureDownload",
                "purpose": "complete current EPL schedule and results",
                "season": season,
                "fixtures_received": len(cached),
                "cached": True,
                "fallback": None,
                "errors": [],
                "updated_at": utc_now_iso(),
            }
        except (ValueError, OSError) as exc:
            errors.append(f"committed cache: {exc}")

    try:
        response = requests.get(
            BASE_URL.format(season=season),
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; TouchlineForecast/1.0)",
                "Accept": "application/json,text/plain,*/*",
                "Referer": f"https://fixturedownload.com/results/epl-{season}",
            },
            timeout=60,
        )
        response.raise_for_status()
        rows = parse_fixture_download(response.json(), season)
        _validate_rows(rows, "live FixtureDownload feed")
        write_json(path, rows)
        print(f"[FixtureDownload] EPL {season}: {len(rows):,} fixtures")
        return rows, {
            "source": "FixtureDownload",
            "purpose": "complete current EPL schedule and results",
            "season": season,
            "fixtures_received": len(rows),
            "cached": False,
            "fallback": None,
            "errors": errors,
            "updated_at": utc_now_iso(),
        }
    except (requests.RequestException, ValueError, OSError) as exc:
        errors.append(f"live feed: {exc}")

    if path.exists():
        try:
            cached = read_json(path, default=[]) or []
            _validate_rows(cached, "committed FixtureDownload cache")
            print(
                f"[FixtureDownload] EPL {season}: live feed failed; "
                f"using {len(cached):,}-fixture committed cache"
            )
            return cached, {
                "source": "FixtureDownload",
                "purpose": "complete current EPL schedule and results",
                "season": season,
                "fixtures_received": len(cached),
                "cached": True,
                "fallback": "committed source cache",
                "errors": errors,
                "updated_at": utc_now_iso(),
            }
        except (ValueError, OSError) as exc:
            errors.append(f"committed cache fallback: {exc}")

    snapshot_rows = _snapshot_fallback(season)
    try:
        _validate_rows(snapshot_rows, "last published EPL snapshot")
        write_json(path, snapshot_rows)
        print(
            f"[FixtureDownload] EPL {season}: source unavailable; "
            "using last published 380-fixture schedule"
        )
        return snapshot_rows, {
            "source": "PublishedSnapshotFallback",
            "purpose": "complete current EPL schedule and results",
            "season": season,
            "fixtures_received": len(snapshot_rows),
            "cached": True,
            "fallback": "last published site snapshot",
            "errors": errors,
            "updated_at": utc_now_iso(),
        }
    except (ValueError, OSError) as exc:
        errors.append(f"published snapshot: {exc}")

    raise RuntimeError(
        "Could not obtain a complete, fresh EPL schedule/results spine from "
        "FixtureDownload, its committed cache, or the last published snapshot. "
        + " | ".join(errors)
    )
