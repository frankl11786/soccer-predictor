from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from .config import CACHE_DIR, LeagueConfig
from .utils import read_json, utc_now_iso, write_json

BASE_URL = "https://v3.football.api-sports.io"
FINAL_STATUSES = {"FT", "AET", "PEN", "AWD", "WO"}


class ApiFootballError(RuntimeError):
    pass


@dataclass
class ApiFootballClient:
    api_key: str
    timeout: int = 45

    @classmethod
    def from_environment(cls) -> "ApiFootballClient":
        key = os.environ.get("API_FOOTBALL_KEY", "").strip()
        if not key:
            raise ApiFootballError("API_FOOTBALL_KEY is not set.")
        return cls(api_key=key)

    def get(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        response = requests.get(
            f"{BASE_URL}/{endpoint.lstrip('/')}",
            headers={"x-apisports-key": self.api_key},
            params=params,
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        errors = payload.get("errors")
        if errors:
            raise ApiFootballError(f"API-Football error for {endpoint} {params}: {errors}")
        return payload


def _cache_path(league: str, name: str) -> Path:
    return CACHE_DIR / "api_football" / league / f"{name}.json"


def cached_request(
    client: ApiFootballClient,
    league_key: str,
    cache_name: str,
    endpoint: str,
    params: dict[str, Any],
    refresh: bool,
) -> dict[str, Any]:
    path = _cache_path(league_key, cache_name)
    if path.exists() and not refresh:
        cached = read_json(path)
        if cached:
            return cached
    payload = client.get(endpoint, params)
    wrapped = {"fetched_at": utc_now_iso(), "endpoint": endpoint, "params": params, "payload": payload}
    write_json(path, wrapped)
    time.sleep(0.2)
    return wrapped


def fetch_league_bundle(client: ApiFootballClient, cfg: LeagueConfig, refresh: bool = False) -> dict[str, Any]:
    fixture_payloads: list[dict[str, Any]] = []
    unavailable: list[dict[str, Any]] = []
    for season in cfg.history_seasons:
        try:
            wrapped = cached_request(
                client,
                cfg.key,
                f"fixtures_{season}",
                "fixtures",
                {"league": cfg.api_league_id, "season": season, "timezone": "UTC"},
                refresh=refresh or season == cfg.current_season,
            )
            fixture_payloads.append(wrapped)
        except (requests.RequestException, ApiFootballError) as exc:
            unavailable.append({"season": season, "error": str(exc)})

    teams = cached_request(
        client,
        cfg.key,
        f"teams_{cfg.current_season}",
        "teams",
        {"league": cfg.api_league_id, "season": cfg.current_season},
        refresh=refresh,
    )
    try:
        standings = cached_request(
            client,
            cfg.key,
            f"standings_{cfg.current_season}",
            "standings",
            {"league": cfg.api_league_id, "season": cfg.current_season},
            refresh=True,
        )
    except (requests.RequestException, ApiFootballError) as exc:
        standings = {"fetched_at": utc_now_iso(), "payload": {"response": []}, "warning": str(exc)}

    return {
        "fixtures": fixture_payloads,
        "teams": teams,
        "standings": standings,
        "unavailable": unavailable,
    }


def fixture_rows(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for wrapped in bundle["fixtures"]:
        payload = wrapped.get("payload", {})
        for item in payload.get("response", []):
            fixture = item.get("fixture", {})
            league = item.get("league", {})
            teams = item.get("teams", {})
            goals = item.get("goals", {})
            score = item.get("score", {})
            status = fixture.get("status", {})
            rows.append(
                {
                    "fixture_id": int(fixture["id"]),
                    "date": fixture.get("date"),
                    "timestamp": fixture.get("timestamp"),
                    "season": int(league.get("season")),
                    "round": league.get("round") or "",
                    "status": status.get("short") or "",
                    "status_long": status.get("long") or "",
                    "home_id": int(teams["home"]["id"]),
                    "home_name": teams["home"]["name"],
                    "away_id": int(teams["away"]["id"]),
                    "away_name": teams["away"]["name"],
                    "home_goals": goals.get("home"),
                    "away_goals": goals.get("away"),
                    "penalty_home": (score.get("penalty") or {}).get("home"),
                    "penalty_away": (score.get("penalty") or {}).get("away"),
                    "venue_id": (fixture.get("venue") or {}).get("id"),
                    "venue_name": (fixture.get("venue") or {}).get("name"),
                }
            )
    unique = {row["fixture_id"]: row for row in rows}
    return sorted(unique.values(), key=lambda row: (row.get("timestamp") or 0, row["fixture_id"]))


def current_team_rows(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    response = bundle.get("teams", {}).get("payload", {}).get("response", [])
    rows = []
    for item in response:
        team = item.get("team", {})
        venue = item.get("venue", {})
        rows.append(
            {
                "api_id": int(team["id"]),
                "name": team["name"],
                "code": team.get("code"),
                "country": team.get("country"),
                "founded": team.get("founded"),
                "logo": team.get("logo"),
                "venue": venue.get("name"),
                "venue_city": venue.get("city"),
                "venue_surface": venue.get("surface"),
            }
        )
    return rows


def standings_groups(bundle: dict[str, Any]) -> dict[int, str]:
    result: dict[int, str] = {}
    response = bundle.get("standings", {}).get("payload", {}).get("response", [])
    for competition in response:
        groups = competition.get("league", {}).get("standings", [])
        for group in groups:
            for row in group:
                team_id = row.get("team", {}).get("id")
                group_name = row.get("group") or ""
                if team_id is not None:
                    result[int(team_id)] = group_name
    return result
