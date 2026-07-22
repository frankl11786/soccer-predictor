from __future__ import annotations

import calendar
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from .config import CACHE_DIR
from .utils import utc_now_iso

BASE = "https://raw.githubusercontent.com/openfootball/england/master"
DATE_RE = re.compile(r"^\s*(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+([A-Z][a-z]{2})\s+(\d{1,2})(?:\s+(\d{4}))?\s*$")
MATCHDAY_RE = re.compile(r"^\s*[▪#]?\s*Matchday\s+(\d+)\s*$", re.I)
MATCH_RE = re.compile(
    r"^\s*(?:(\d{1,2}:\d{2})\s+)?(.+?)\s+v\s+(.+?)(?:\s+(\d+)\s*[-–]\s*(\d+)(?:\s+\([^)]*\))?)?\s*$"
)


def _cache_path(season_label: str) -> Path:
    return CACHE_DIR / "openfootball" / f"epl_{season_label}.txt"


def _fetch_text(season_label: str, refresh: bool) -> str:
    path = _cache_path(season_label)
    if path.exists() and not refresh:
        return path.read_text(encoding="utf-8")
    response = requests.get(f"{BASE}/{season_label}/1-premierleague.txt", timeout=45)
    response.raise_for_status()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(response.text, encoding="utf-8")
    return response.text


def _fixture_id(season: int, date_iso: str, home: str, away: str) -> str:
    digest = hashlib.sha1(f"openfootball:{season}:{date_iso}:{home}:{away}".encode("utf-8")).hexdigest()[:16]
    return f"of-{digest}"


def parse_premier_league(text: str, season_start: int) -> list[dict]:
    rows: list[dict] = []
    current_date: datetime | None = None
    current_year = season_start
    current_time = "15:00"
    matchday = 0
    london = ZoneInfo("Europe/London")

    for raw in text.splitlines():
        line = raw.strip("\ufeff\n\r")
        if not line.strip() or line.lstrip().startswith(("=", "#")):
            continue
        md = MATCHDAY_RE.match(line)
        if md:
            matchday = int(md.group(1))
            continue
        date_match = DATE_RE.match(line)
        if date_match:
            month_name, day_text, explicit_year = date_match.groups()
            month = list(calendar.month_abbr).index(month_name)
            if explicit_year:
                current_year = int(explicit_year)
            elif month <= 6:
                current_year = season_start + 1
            else:
                current_year = season_start
            current_date = datetime(current_year, month, int(day_text), tzinfo=london)
            current_time = "15:00"
            continue
        match = MATCH_RE.match(line)
        if not match or current_date is None:
            continue
        time_text, home, away, home_goals, away_goals = match.groups()
        if time_text:
            current_time = time_text
        hour, minute = [int(value) for value in current_time.split(":")]
        kickoff_local = current_date.replace(hour=hour, minute=minute)
        kickoff_utc = kickoff_local.astimezone(timezone.utc)
        score_present = home_goals is not None and away_goals is not None
        date_iso = kickoff_utc.isoformat().replace("+00:00", "Z")
        rows.append(
            {
                "fixture_id": _fixture_id(season_start, date_iso, home, away),
                "source": "OpenFootball",
                "date": date_iso,
                "timestamp": int(kickoff_utc.timestamp()),
                "season": season_start,
                "round": f"Regular Season - {matchday}" if matchday else "Regular Season",
                "status": "FT" if score_present else "NS",
                "status_long": "Match Finished" if score_present else "Not Started",
                "home_id": 0,
                "home_name": home.strip(),
                "away_id": 0,
                "away_name": away.strip(),
                "home_goals": int(home_goals) if score_present else None,
                "away_goals": int(away_goals) if score_present else None,
                "penalty_home": None,
                "penalty_away": None,
                "venue_id": None,
                "venue_name": None,
            }
        )
    return rows


def fetch_epl_rows(seasons: tuple[int, ...], refresh: bool = False) -> tuple[list[dict], dict]:
    rows: list[dict] = []
    errors: list[str] = []
    for season in seasons:
        label = f"{season}-{str(season + 1)[-2:]}"
        try:
            rows.extend(parse_premier_league(_fetch_text(label, refresh=refresh), season))
        except (requests.RequestException, OSError, ValueError) as exc:
            errors.append(f"{label}: {exc}")
    metadata = {
        "source": "OpenFootball",
        "purpose": "recent and current EPL fixtures/results",
        "seasons_requested": [f"{year}-{str(year + 1)[-2:]}" for year in seasons],
        "fixtures_received": len(rows),
        "errors": errors,
        "updated_at": utc_now_iso(),
    }
    return rows, metadata
