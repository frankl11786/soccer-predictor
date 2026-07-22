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


def fetch_history_rows(client: ApiFootballClient, cfg: LeagueConfig, refresh: bool = False) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    unavailable: list[dict[str, Any]] = []
    for season in cfg.api_history_seasons:
        try:
            payloads.append(
                cached_request(
                    client,
                    cfg.key,
                    f"fixtures_{season}",
                    "fixtures",
                    {"league": cfg.api_league_id, "season": season, "timezone": "UTC"},
                    refresh=refresh,
                )
            )
        except (requests.RequestException, ApiFootballError) as exc:
            unavailable.append({"season": season, "error": str(exc)})

    rows = fixture_rows(payloads)
    metadata = {
        "source": "API-Football",
        "purpose": "historical results",
        "league_id": cfg.api_league_id,
        "seasons_requested": list(cfg.api_history_seasons),
        "seasons_unavailable": unavailable,
        "fixtures_received": len(rows),
        "updated_at": utc_now_iso(),
    }
    return rows, metadata


def fixture_rows(wrapped_payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for wrapped in wrapped_payloads:
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
                    "fixture_id": f"api-{fixture['id']}",
                    "source": "API-Football",
                    "date": fixture.get("date"),
                    "timestamp": fixture.get("timestamp"),
                    "season": int(league.get("season")),
                    "round": league.get("round") or "",
                    "status": status.get("short") or "",
                    "status_long": status.get("long") or "",
                    "home_id": 0,
                    "home_name": teams["home"]["name"],
                    "away_id": 0,
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
