from __future__ import annotations

import hashlib
from typing import Any

from .config import LeagueConfig, OVERRIDE_DIR
from .utils import deterministic_color, load_csv, normalize_name, slugify

# Source-specific names are collapsed to one canonical spelling before IDs are made.
ALIASES = {
    "newyorkredbulls": "redbullnewyork",
    "nyredbulls": "redbullnewyork",
    "lafc": "losangeles",
    "losangelesfc": "losangeles",
    "montrealimpact": "montreal",
    "cfmontreal": "montreal",
    "intermiamicf": "intermiami",
    "stlouiscitysc": "stlouiscity",
    "stlouiscity": "stlouiscity",
    "brightonhovealbion": "brightonandhovealbion",
    "brightonandhovealbion": "brightonandhovealbion",
    "wolverhamptonwanderers": "wolves",
    "wolverhampton": "wolves",
    "tottenham": "tottenhamhotspur",
    "manchesterutd": "manchesterunited",
    "manutd": "manchesterunited",
    "nycfc": "newyorkcity",
    "newyorkcityfc": "newyorkcity",
    "sportingkc": "sportingkansascity",
    "sportingkansascitysc": "sportingkansascity",
    "dcu": "dcunited",
}


def normalized_key(name: str) -> str:
    value = normalize_name(name)
    return ALIASES.get(value, value)


def stable_team_id(key: str) -> int:
    # Positive, deterministic 31-bit integer. Stable across APIs and workflow runs.
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) & 0x7FFFFFFF


def override_rows(cfg: LeagueConfig) -> list[dict[str, str]]:
    return load_csv(OVERRIDE_DIR / f"teams_{cfg.key}.csv")


def match_override(name: str, overrides: list[dict[str, str]]) -> dict[str, str] | None:
    key = normalized_key(name)
    best = None
    for row in overrides:
        candidate = normalized_key(row.get("name", ""))
        if candidate == key:
            return row
        if candidate and key and (candidate in key or key in candidate):
            best = row
    return best


def team_catalog(cfg: LeagueConfig) -> list[dict[str, Any]]:
    teams: list[dict[str, Any]] = []
    for row in override_rows(cfg):
        slug = row.get("slug") or slugify(row["name"])
        teams.append(
            {
                "api_id": stable_team_id(slug),
                "name": row["name"],
                "short": row.get("short") or "".join(word[0] for word in row["name"].split())[:4].upper(),
                "conference": row.get("conference") or ("Premier League" if cfg.key == "epl" else "Unknown"),
                "market_value": float(row.get("market_value") or 1.0),
                # CSV convention: positive attack is stronger; negative defense
                # means fewer goals conceded. The Bayesian model stores defense
                # internally with the opposite sign, so conversion happens in
                # data preparation.
                "seed_attack": float(row.get("attack") or 0.0),
                "seed_defense": float(row.get("defense") or 0.0),
                "color": row.get("color") or deterministic_color(row["name"]),
                "slug": slug,
                "logo": None,
                "venue": None,
                "venue_city": None,
                "venue_surface": None,
            }
        )
    return teams


def canonical_team(name: str, cfg: LeagueConfig, overrides: list[dict[str, str]]) -> tuple[int, str, str]:
    override = match_override(name, overrides)
    if override:
        slug = override.get("slug") or slugify(override["name"])
        return stable_team_id(slug), override["name"], slug
    key = normalized_key(name) or slugify(name)
    slug = slugify(name)
    return stable_team_id(key), name.strip(), slug


def canonicalize_fixture_rows(cfg: LeagueConfig, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    overrides = override_rows(cfg)
    canonical: list[dict[str, Any]] = []
    for row in rows:
        home_id, home_name, _ = canonical_team(str(row["home_name"]), cfg, overrides)
        away_id, away_name, _ = canonical_team(str(row["away_name"]), cfg, overrides)
        canonical.append(
            {
                **row,
                "home_id": home_id,
                "home_name": home_name,
                "away_id": away_id,
                "away_name": away_name,
            }
        )
    # Prefer the latest row when the same source fixture is encountered more than once.
    unique = {str(row["fixture_id"]): row for row in canonical}
    return sorted(unique.values(), key=lambda row: (row.get("timestamp") or 0, str(row["fixture_id"])))
