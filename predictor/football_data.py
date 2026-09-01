from __future__ import annotations

import csv
import hashlib
import io
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

from .config import CACHE_DIR
from .utils import utc_now_iso

BASE_URL = "https://www.football-data.co.uk/mmz4281/{season_label}/E0.csv"


def _season_label(season: int) -> str:
    return f"{str(season)[-2:]}{str(season + 1)[-2:]}"


def _cache_path(season: int) -> Path:
    return CACHE_DIR / "football_data" / f"epl_{season}-{str(season + 1)[-2:]}.csv"


def _fixture_id(season: int, date_iso: str, home: str, away: str) -> str:
    digest = hashlib.sha1(
        f"football-data:{season}:{date_iso}:{home}:{away}".encode("utf-8")
    ).hexdigest()[:16]
    return f"fd-{digest}"


def parse_epl_results(text: str, season: int) -> list[dict[str, Any]]:
    """Parse completed Premier League rows from football-data.co.uk's E0 CSV."""
    london = ZoneInfo("Europe/London")
    rows: list[dict[str, Any]] = []
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    for source in reader:
        if str(source.get("Div") or "").strip() != "E0":
            continue
        home = str(source.get("HomeTeam") or "").strip()
        away = str(source.get("AwayTeam") or "").strip()
        home_goals = str(source.get("FTHG") or "").strip()
        away_goals = str(source.get("FTAG") or "").strip()
        date_text = str(source.get("Date") or "").strip()
        time_text = str(source.get("Time") or "15:00").strip() or "15:00"
        if not home or not away or not home_goals or not away_goals or not date_text:
            continue
        try:
            kickoff_local = datetime.strptime(
                f"{date_text} {time_text}", "%d/%m/%Y %H:%M"
            ).replace(tzinfo=london)
            home_score = int(home_goals)
            away_score = int(away_goals)
        except (TypeError, ValueError):
            continue
        kickoff = kickoff_local.astimezone(timezone.utc)
        date_iso = kickoff.isoformat().replace("+00:00", "Z")
        rows.append({
            "fixture_id": _fixture_id(season, date_iso, home, away),
            "source": "Football-Data.co.uk",
            "date": date_iso,
            "timestamp": int(kickoff.timestamp()),
            "season": season,
            "round": "Regular Season",
            "status": "FT",
            "status_long": "Match Finished",
            "home_id": 0,
            "home_name": home,
            "away_id": 0,
            "away_name": away,
            "home_goals": home_score,
            "away_goals": away_score,
            "penalty_home": None,
            "penalty_away": None,
            "venue_id": None,
            "venue_name": None,
        })
    return sorted(rows, key=lambda row: (row["timestamp"], row["fixture_id"]))


def fetch_epl_results(
    season: int,
    refresh: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = _cache_path(season)
    if path.exists() and not refresh:
        rows = parse_epl_results(path.read_text(encoding="utf-8-sig"), season)
        return rows, {
            "source": "Football-Data.co.uk",
            "purpose": "fallback current EPL completed results",
            "season": season,
            "fixtures_received": len(rows),
            "cached": True,
            "cache_fallback": False,
            "live_request_failed": False,
            "updated_at": utc_now_iso(),
        }

    error: str | None = None
    try:
        response = requests.get(
            BASE_URL.format(season_label=_season_label(season)),
            headers={"User-Agent": "TouchlineForecast/1.0"},
            timeout=45,
        )
        response.raise_for_status()
        text = response.content.decode("utf-8-sig")
        if not text.strip():
            raise ValueError("empty response")
        rows = parse_epl_results(text, season)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        live_request_failed = False
        cache_fallback = False
    except (requests.RequestException, OSError, UnicodeError, ValueError) as exc:
        error = str(exc)
        live_request_failed = True
        cache_fallback = path.exists()
        rows = (
            parse_epl_results(path.read_text(encoding="utf-8-sig"), season)
            if cache_fallback
            else []
        )

    metadata = {
        "source": "Football-Data.co.uk",
        "purpose": "fallback current EPL completed results",
        "season": season,
        "fixtures_received": len(rows),
        "cached": cache_fallback,
        "cache_fallback": cache_fallback,
        "live_request_failed": live_request_failed,
        "request_error": error,
        "updated_at": utc_now_iso(),
    }
    print(
        f"[Football-Data.co.uk] EPL {season}: {len(rows):,} completed fixtures; "
        f"live_request_failed={live_request_failed}; cache_fallback={cache_fallback}; "
        f"error={error}"
    )
    return rows, metadata
