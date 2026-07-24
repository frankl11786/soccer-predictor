from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from .api_football import FINAL_STATUSES
from .config import BUCKET_DAYS, LeagueConfig
from .identity import team_catalog


@dataclass
class PreparedLeague:
    history: pd.DataFrame
    current_fixtures: pd.DataFrame
    teams: list[dict[str, Any]]
    team_index: dict[int, int]
    current_team_ids: list[int]
    n_times: int
    time_origin: datetime
    bucket_days: int
    value_by_id: dict[int, float]
    last_observed_at: datetime
    days_since_last_observed: int
    recent_season: int | None
    recent_attack: np.ndarray
    recent_defense: np.ndarray
    recent_matches: np.ndarray
    current_season_matches: np.ndarray
    seed_attack: np.ndarray
    seed_defense: np.ndarray
    historical_value_mode: str


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Cross-source dedupe uses canonical teams, season, and date. Prefer rows with a final score.
    chosen: dict[tuple, dict[str, Any]] = {}
    for row in rows:
        date_key = str(row.get("date") or "")[:10]
        key = (row.get("season"), date_key, row.get("home_id"), row.get("away_id"))
        previous = chosen.get(key)
        if previous is None:
            chosen[key] = row
            continue
        previous_final = previous.get("status") in FINAL_STATUSES
        current_final = row.get("status") in FINAL_STATUSES
        if current_final and not previous_final:
            chosen[key] = row
    return sorted(chosen.values(), key=lambda row: (row.get("timestamp") or 0, str(row.get("fixture_id"))))


def _season_strength_inputs(
    completed: list[dict[str, Any]],
    current_ids: list[int],
    current_season: int,
) -> tuple[int | None, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Build stable prior-season attack/defense signals for current clubs.

    The inputs are based only on matches available before the forecast season.
    Rates are shrunk toward the league average by eight pseudo-matches so that
    small samples and promoted clubs do not create extreme priors.
    """
    previous_seasons = sorted(
        {
            int(row["season"])
            for row in completed
            if row.get("season") is not None and int(row["season"]) < current_season
        }
    )
    recent_season = previous_seasons[-1] if previous_seasons else None
    n = len(current_ids)
    attack = np.zeros(n, dtype=np.float32)
    defense = np.zeros(n, dtype=np.float32)
    matches = np.zeros(n, dtype=np.int32)
    current_matches = np.zeros(n, dtype=np.int32)
    pos = {team_id: index for index, team_id in enumerate(current_ids)}

    for row in completed:
        if int(row.get("season", -1)) != current_season:
            continue
        for team_id in (row["home_id"], row["away_id"]):
            if team_id in pos:
                current_matches[pos[team_id]] += 1

    if recent_season is None:
        return None, attack, defense, matches, current_matches

    gf = np.zeros(n, dtype=np.float64)
    ga = np.zeros(n, dtype=np.float64)
    all_goals = 0.0
    all_team_games = 0
    for row in completed:
        if int(row.get("season", -1)) != recent_season:
            continue
        hg = float(row["home_goals"])
        ag = float(row["away_goals"])
        all_goals += hg + ag
        all_team_games += 2
        h = pos.get(row["home_id"])
        a = pos.get(row["away_id"])
        if h is not None:
            matches[h] += 1
            gf[h] += hg
            ga[h] += ag
        if a is not None:
            matches[a] += 1
            gf[a] += ag
            ga[a] += hg

    league_rate = all_goals / max(all_team_games, 1)
    league_rate = max(league_rate, 0.25)
    pseudo_matches = 8.0
    for i in range(n):
        if matches[i] == 0:
            continue
        scored_rate = (gf[i] + pseudo_matches * league_rate) / (matches[i] + pseudo_matches)
        allowed_rate = (ga[i] + pseudo_matches * league_rate) / (matches[i] + pseudo_matches)
        attack[i] = float(np.clip(math.log(scored_rate / league_rate), -0.55, 0.55))
        # Bayesian internal convention: larger defense means stronger defense.
        defense[i] = float(np.clip(-math.log(allowed_rate / league_rate), -0.55, 0.55))

    return recent_season, attack, defense, matches, current_matches


def prepare_league(cfg: LeagueConfig, fixture_rows: list[dict[str, Any]]) -> PreparedLeague:
    teams = team_catalog(cfg)
    if not teams:
        raise ValueError(f"No team catalog exists for {cfg.name}.")
    fixture_rows = _dedupe(fixture_rows)
    current_ids = [team["api_id"] for team in teams]
    current_set = set(current_ids)
    current = [
        row for row in fixture_rows
        if row["season"] == cfg.current_season and row["home_id"] in current_set and row["away_id"] in current_set
    ]
    if cfg.key == "mls":
        regular = [row for row in current if "regular" in str(row.get("round", "")).lower()]
        if regular:
            current = regular

        expected_matches = 510
        appearances = {team_id: 0 for team_id in current_ids}
        for row in current:
            if row["home_id"] in appearances:
                appearances[row["home_id"]] += 1
            if row["away_id"] in appearances:
                appearances[row["away_id"]] += 1
        bad_counts = {
            team_id: count
            for team_id, count in appearances.items()
            if count != 34
        }
        if len(current) != expected_matches or bad_counts:
            sample = ", ".join(
                f"{team_id}:{count}"
                for team_id, count in list(bad_counts.items())[:8]
            )
            raise ValueError(
                "MLS schedule validation failed: "
                f"expected {expected_matches} regular-season fixtures and 34 per club, "
                f"received {len(current)} fixtures. Bad club counts: {sample or 'none'}."
            )

    if not current:
        raise ValueError(f"No current-season fixtures were found for {cfg.name}.")

    completed = [
        row for row in fixture_rows
        if row["status"] in FINAL_STATUSES and row["home_goals"] is not None and row["away_goals"] is not None
    ]
    if len(completed) < 80:
        raise ValueError(f"Only {len(completed)} completed fixtures were available; at least 80 are required.")

    all_ids = sorted({row["home_id"] for row in completed} | {row["away_id"] for row in completed} | current_set)
    team_index = {team_id: index for index, team_id in enumerate(all_ids)}

    current_value = {team["api_id"]: team["market_value"] for team in teams}
    known_values = [value for value in current_value.values() if value > 0]
    default_value = float(np.median(known_values)) if known_values else 1.0
    value_by_id = {team_id: current_value.get(team_id, default_value) for team_id in all_ids}

    parsed_dates = [datetime.fromisoformat(str(row["date"]).replace("Z", "+00:00")) for row in completed]
    origin = min(parsed_dates).astimezone(timezone.utc)
    last_observed = max(parsed_dates).astimezone(timezone.utc)
    now = datetime.now(timezone.utc)

    # Critical preseason fix: the latent state ends at the final observed match.
    # It no longer creates June/July state buckets with no likelihood evidence.
    n_times = max(2, int(math.floor((last_observed - origin).days / BUCKET_DAYS)) + 1)

    history_rows = []
    for row, when in zip(completed, parsed_dates):
        bucket = min(n_times - 1, max(0, int((when.astimezone(timezone.utc) - origin).days // BUCKET_DAYS)))
        hv = max(value_by_id[row["home_id"]], 0.01)
        av = max(value_by_id[row["away_id"]], 0.01)
        # The override CSV contains current squad values, not historical values.
        # Applying them to 2022-2025 EPL matches creates look-ahead bias, so EPL
        # history deliberately omits the value term. Current values are still
        # used for all future-fixture simulations.
        value_diff = 0.0 if cfg.key == "epl" else math.log(hv / av)
        history_rows.append(
            {
                **row,
                "home_idx": team_index[row["home_id"]],
                "away_idx": team_index[row["away_id"]],
                "time_idx": bucket,
                "value_diff": value_diff,
            }
        )

    current_rows = []
    for row in current:
        when = datetime.fromisoformat(str(row["date"]).replace("Z", "+00:00")).astimezone(timezone.utc)
        current_rows.append({**row, "future_bucket": max(0, int((when - now).days // BUCKET_DAYS))})

    recent_season, recent_attack, recent_defense, recent_matches, current_season_matches = _season_strength_inputs(
        completed,
        current_ids,
        cfg.current_season,
    )
    seed_attack = np.asarray([float(team.get("seed_attack", 0.0)) for team in teams], dtype=np.float32)
    # CSV defense is negative for a strong defense; the model uses positive.
    seed_defense = -np.asarray([float(team.get("seed_defense", 0.0)) for team in teams], dtype=np.float32)

    return PreparedLeague(
        history=pd.DataFrame(history_rows).sort_values("timestamp").reset_index(drop=True),
        current_fixtures=pd.DataFrame(current_rows).sort_values("timestamp").reset_index(drop=True),
        teams=teams,
        team_index=team_index,
        current_team_ids=current_ids,
        n_times=n_times,
        time_origin=origin,
        bucket_days=BUCKET_DAYS,
        value_by_id=value_by_id,
        last_observed_at=last_observed,
        days_since_last_observed=max(0, (now - last_observed).days),
        recent_season=recent_season,
        recent_attack=recent_attack,
        recent_defense=recent_defense,
        recent_matches=recent_matches,
        current_season_matches=current_season_matches,
        seed_attack=seed_attack,
        seed_defense=seed_defense,
        historical_value_mode="future-only" if cfg.key == "epl" else "historical-and-future",
    )
