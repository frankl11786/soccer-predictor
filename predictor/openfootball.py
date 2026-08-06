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
DATE_RE = re.compile(
    r"^\s*(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+([A-Z][a-z]{2})\s+"
    r"(\d{1,2})(?:\s+(\d{4}))?\s*$"
)
ROUND_RE = re.compile(
    r"^\s*[▪#]?\s*(?:Matchday\s+|Regular\s+Season\s*-\s*)(\d+)\s*$",
    re.I,
)
# Older files and some future-season files use: Home v Away 2-1 (1-0)
VERSUS_RE = re.compile(
    r"^\s*(?:(\d{1,2}:\d{2})\s+)?(.+?)\s+v\s+(.+?)"
    r"(?:\s+(\d+)\s*[-–]\s*(\d+)(?:\s+\([^)]*\))?)?\s*$"
)
# The 2025/26 file uses: Home  2-1 (1-0)  Away
SCORE_MIDDLE_RE = re.compile(
    r"^\s*(?:(\d{1,2}:\d{2})\s+)?(.+?)\s{2,}"
    r"(\d+)\s*[-–]\s*(\d+)(?:\s+\([^)]*\))?\s{2,}(.+?)\s*$"
)


def _cache_path(season_label: str) -> Path:
    return CACHE_DIR / "openfootball" / f"epl_{season_label}.txt"


def _fetch_text(season_label: str, refresh: bool) -> tuple[str, bool, str | None]:
    """Fetch a season file, falling back to a previously committed cache.

    A source outage should not erase a known-good season from a nightly rebuild.
    The returned boolean indicates whether the cache was used.
    """
    path = _cache_path(season_label)
    if path.exists() and not refresh:
        return path.read_text(encoding="utf-8"), True, None

    try:
        response = requests.get(
            f"{BASE}/{season_label}/1-premierleague.txt",
            headers={
                "User-Agent": "TouchlineForecast/1.0 (+https://predictor.francislavelle.com)",
                "Accept": "text/plain,*/*",
            },
            timeout=45,
        )
        response.raise_for_status()
        text = response.text
        if not text.strip():
            raise ValueError("empty response")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return text, False, None
    except (requests.RequestException, OSError, ValueError) as exc:
        if path.exists():
            return (
                path.read_text(encoding="utf-8"),
                True,
                f"live refresh failed; used committed cache: {exc}",
            )
        raise


def _fixture_id(season: int, date_iso: str, home: str, away: str) -> str:
    digest = hashlib.sha1(
        f"openfootball:{season}:{date_iso}:{home}:{away}".encode("utf-8")
    ).hexdigest()[:16]
    return f"of-{digest}"


def _match_parts(line: str) -> tuple[str | None, str, str, str | None, str | None] | None:
    versus = VERSUS_RE.match(line)
    if versus:
        time_text, home, away, home_goals, away_goals = versus.groups()
        return time_text, home.strip(), away.strip(), home_goals, away_goals

    score_middle = SCORE_MIDDLE_RE.match(line)
    if score_middle:
        time_text, home, home_goals, away_goals, away = score_middle.groups()
        return time_text, home.strip(), away.strip(), home_goals, away_goals

    return None


def parse_premier_league(text: str, season_start: int) -> list[dict]:
    rows: list[dict] = []
    current_date: datetime | None = None
    current_year = season_start
    current_time = "15:00"
    matchday = 0
    london = ZoneInfo("Europe/London")

    for raw in text.splitlines():
        line = raw.strip("\ufeff\n\r")
        if not line.strip() or line.lstrip().startswith(("=", "#", "(")):
            continue

        round_match = ROUND_RE.match(line)
        if round_match:
            matchday = int(round_match.group(1))
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

        parts = _match_parts(line)
        if parts is None or current_date is None:
            continue

        time_text, home, away, home_goals, away_goals = parts
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
                "home_name": home,
                "away_id": 0,
                "away_name": away,
                "home_goals": int(home_goals) if score_present else None,
                "away_goals": int(away_goals) if score_present else None,
                "penalty_home": None,
                "penalty_away": None,
                "venue_id": None,
                "venue_name": None,
            }
        )
    return rows


def fetch_epl_rows(
    seasons: tuple[int, ...],
    refresh: bool = False,
) -> tuple[list[dict], dict]:
    rows: list[dict] = []
    errors: list[str] = []
    cached_seasons: list[str] = []
    season_counts: dict[str, int] = {}

    for season in seasons:
        label = f"{season}-{str(season + 1)[-2:]}"
        try:
            text, used_cache, warning = _fetch_text(label, refresh=refresh)
            parsed = parse_premier_league(text, season)
            rows.extend(parsed)
            season_counts[label] = len(parsed)
            if used_cache:
                cached_seasons.append(label)
            if warning:
                errors.append(f"{label}: {warning}")
            if not parsed:
                errors.append(f"{label}: source file parsed but produced zero fixtures")
        except (requests.RequestException, OSError, ValueError) as exc:
            errors.append(f"{label}: {exc}")
            season_counts[label] = 0

    metadata = {
        "source": "OpenFootball",
        "purpose": "independent EPL historical results and current schedule",
        "seasons_requested": [f"{year}-{str(year + 1)[-2:]}" for year in seasons],
        "season_fixture_counts": season_counts,
        "fixtures_received": len(rows),
        "cached_seasons": cached_seasons,
        "errors": errors,
        "updated_at": utc_now_iso(),
    }
    print(
        f"[OpenFootball] EPL seasons={list(seasons)} fixtures={len(rows):,} "
        f"counts={season_counts} errors={errors}"
    )
    return rows, metadata
