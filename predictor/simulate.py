from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.stats import poisson

from .api_football import FINAL_STATUSES
from .bayes import PosteriorFit
from .config import RANDOM_SEED
from .data_prep import PreparedLeague


@dataclass
class SimulationResult:
    current_table: list[dict[str, Any]]
    forecast: list[dict[str, Any]]
    fixtures: list[dict[str, Any]]


def _score_probabilities(lh: np.ndarray, la: np.ndarray, max_goals: int = 10) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    goals = np.arange(max_goals + 1)
    home_pmf = poisson.pmf(goals[None, :], lh[:, None])
    away_pmf = poisson.pmf(goals[None, :], la[:, None])
    matrix = home_pmf[:, :, None] * away_pmf[:, None, :]
    home = np.tril(matrix, k=-1).sum(axis=(1, 2))
    draw = np.diagonal(matrix, axis1=1, axis2=2).sum(axis=1)
    away = np.triu(matrix, k=1).sum(axis=(1, 2))
    total = np.maximum(home + draw + away, 1e-12)
    return home / total, draw / total, away / total


def _current_baseline(prepared: PreparedLeague) -> dict[str, np.ndarray]:
    n = len(prepared.teams)
    pos_by_id = {team["api_id"]: i for i, team in enumerate(prepared.teams)}
    table = {key: np.zeros(n, dtype=np.int32) for key in ("p", "w", "d", "l", "gf", "ga", "pts")}
    for row in prepared.current_fixtures.to_dict("records"):
        if row["status"] not in FINAL_STATUSES or row["home_goals"] is None or row["away_goals"] is None:
            continue
        if row["home_id"] not in pos_by_id or row["away_id"] not in pos_by_id:
            continue
        h, a = pos_by_id[row["home_id"]], pos_by_id[row["away_id"]]
        hg, ag = int(row["home_goals"]), int(row["away_goals"])
        table["p"][[h, a]] += 1
        table["gf"][h] += hg; table["ga"][h] += ag
        table["gf"][a] += ag; table["ga"][a] += hg
        if hg > ag:
            table["w"][h] += 1; table["l"][a] += 1; table["pts"][h] += 3
        elif hg < ag:
            table["w"][a] += 1; table["l"][h] += 1; table["pts"][a] += 3
        else:
            table["d"][[h, a]] += 1; table["pts"][[h, a]] += 1
    table["gd"] = table["gf"] - table["ga"]
    return table


def _composite(points: np.ndarray, gd: np.ndarray, gf: np.ndarray, wins: np.ndarray) -> np.ndarray:
    return points.astype(np.int64) * 10**9 + (gd.astype(np.int64) + 300) * 10**6 + gf.astype(np.int64) * 10**3 + wins.astype(np.int64)


def _fixture_probabilities(prepared: PreparedLeague, fit: PosteriorFit) -> list[dict[str, Any]]:
    team_pos = {team["api_id"]: i for i, team in enumerate(prepared.teams)}
    fixtures: list[dict[str, Any]] = []
    for row in prepared.current_fixtures.to_dict("records"):
        if row["home_id"] not in team_pos or row["away_id"] not in team_pos:
            continue
        h, a = team_pos[row["home_id"]], team_pos[row["away_id"]]
        hv = max(float(prepared.teams[h]["market_value"]), 0.01)
        av = max(float(prepared.teams[a]["market_value"]), 0.01)
        value_diff = math.log(hv / av)
        log_h = fit.intercept + fit.home_advantage + fit.attack_current[:, h] - fit.defense_current[:, a] + fit.value_coefficient * value_diff
        log_a = fit.intercept + fit.attack_current[:, a] - fit.defense_current[:, h] - fit.value_coefficient * value_diff
        lh = np.exp(np.clip(log_h, -3, 2))
        la = np.exp(np.clip(log_a, -3, 2))
        ph, pd, pa = _score_probabilities(lh, la)
        status = "final" if row["status"] in FINAL_STATUSES else "scheduled"
        record = {
            "id": str(row["fixture_id"]),
            "round": row["round"],
            "date": str(row["date"])[:10],
            "kickoff": row["date"],
            "home": prepared.teams[h]["slug"],
            "away": prepared.teams[a]["slug"],
            "status": status,
            "xg_home": round(float(lh.mean()), 3),
            "xg_away": round(float(la.mean()), 3),
            "probabilities": {
                "home": round(float(ph.mean()), 5),
                "draw": round(float(pd.mean()), 5),
                "away": round(float(pa.mean()), 5),
                "home_interval": [round(float(np.quantile(ph, .05)), 5), round(float(np.quantile(ph, .95)), 5)],
                "draw_interval": [round(float(np.quantile(pd, .05)), 5), round(float(np.quantile(pd, .95)), 5)],
                "away_interval": [round(float(np.quantile(pa, .05)), 5), round(float(np.quantile(pa, .95)), 5)],
            },
        }
        if status == "final":
            record["home_score"] = int(row["home_goals"])
            record["away_score"] = int(row["away_goals"])
        fixtures.append(record)
    return fixtures


def _future_paths(fit: PosteriorFit, simulations: int, future_buckets: int, rng: np.random.Generator):
    posterior_n = fit.attack_current.shape[0]
    sample_idx = rng.integers(0, posterior_n, simulations)
    attacks = np.empty((future_buckets + 1, simulations, fit.attack_current.shape[1]), dtype=np.float32)
    defenses = np.empty_like(attacks)
    attacks[0] = fit.attack_current[sample_idx]
    defenses[0] = fit.defense_current[sample_idx]
    sigma_a = fit.sigma_attack[sample_idx].astype(np.float32)
    sigma_d = fit.sigma_defense[sample_idx].astype(np.float32)
    for bucket in range(1, future_buckets + 1):
        attacks[bucket] = attacks[bucket - 1] + rng.normal(0, sigma_a[:, None], attacks[0].shape)
        defenses[bucket] = defenses[bucket - 1] + rng.normal(0, sigma_d[:, None], defenses[0].shape)
        attacks[bucket] -= attacks[bucket].mean(axis=1, keepdims=True)
        defenses[bucket] -= defenses[bucket].mean(axis=1, keepdims=True)
    return (
        sample_idx,
        attacks,
        defenses,
        fit.intercept[sample_idx],
        fit.home_advantage[sample_idx],
        fit.value_coefficient[sample_idx],
    )


def _simulate_regular(prepared: PreparedLeague, fit: PosteriorFit, simulations: int, rng: np.random.Generator):
    n = len(prepared.teams)
    baseline = _current_baseline(prepared)
    arrays = {key: np.broadcast_to(value, (simulations, n)).copy() for key, value in baseline.items() if key != "gd"}
    upcoming = [row for row in prepared.current_fixtures.to_dict("records") if row["status"] not in FINAL_STATUSES]
    max_bucket = max([int(row.get("future_bucket", 0)) for row in upcoming] + [0])
    sample_idx, attack_paths, defense_paths, intercept, home_adv, value_coef = _future_paths(fit, simulations, max_bucket, rng)
    team_pos = {team["api_id"]: i for i, team in enumerate(prepared.teams)}

    for row in upcoming:
        if row["home_id"] not in team_pos or row["away_id"] not in team_pos:
            continue
        h, a = team_pos[row["home_id"]], team_pos[row["away_id"]]
        bucket = min(max_bucket, max(0, int(row.get("future_bucket", 0))))
        hv = max(float(prepared.teams[h]["market_value"]), 0.01)
        av = max(float(prepared.teams[a]["market_value"]), 0.01)
        value_diff = math.log(hv / av)
        lh = np.exp(np.clip(intercept + home_adv + attack_paths[bucket, :, h] - defense_paths[bucket, :, a] + value_coef * value_diff, -3, 2))
        la = np.exp(np.clip(intercept + attack_paths[bucket, :, a] - defense_paths[bucket, :, h] - value_coef * value_diff, -3, 2))
        hg = rng.poisson(lh)
        ag = rng.poisson(la)
        arrays["p"][:, [h, a]] += 1
        arrays["gf"][:, h] += hg; arrays["ga"][:, h] += ag
        arrays["gf"][:, a] += ag; arrays["ga"][:, a] += hg
        hw = hg > ag; aw = ag > hg; dr = hg == ag
        arrays["w"][:, h] += hw; arrays["l"][:, a] += hw; arrays["pts"][:, h] += hw * 3
        arrays["w"][:, a] += aw; arrays["l"][:, h] += aw; arrays["pts"][:, a] += aw * 3
        arrays["d"][:, h] += dr; arrays["d"][:, a] += dr
        arrays["pts"][:, h] += dr; arrays["pts"][:, a] += dr
    arrays["gd"] = arrays["gf"] - arrays["ga"]
    return arrays, attack_paths[-1], defense_paths[-1], sample_idx, intercept, home_adv, value_coef


def _rank_all(arrays: dict[str, np.ndarray]) -> np.ndarray:
    score = _composite(arrays["pts"], arrays["gd"], arrays["gf"], arrays["w"])
    return np.argsort(-score, axis=1)


def _current_table_rows(prepared: PreparedLeague) -> list[dict[str, Any]]:
    baseline = _current_baseline(prepared)
    order = np.argsort(-_composite(baseline["pts"], baseline["gd"], baseline["gf"], baseline["w"]))
    rows = []
    for idx in order:
        rows.append({
            "team": prepared.teams[idx]["slug"],
            "p": int(baseline["p"][idx]), "w": int(baseline["w"][idx]), "d": int(baseline["d"][idx]), "l": int(baseline["l"][idx]),
            "gf": int(baseline["gf"][idx]), "ga": int(baseline["ga"][idx]), "gd": int(baseline["gd"][idx]), "pts": int(baseline["pts"][idx]),
        })
    return rows


def _base_forecast(prepared: PreparedLeague, fit: PosteriorFit, arrays: dict[str, np.ndarray], ranks: np.ndarray) -> list[dict[str, Any]]:
    simulations, n = ranks.shape
    positions = np.empty_like(ranks)
    positions[np.arange(simulations)[:, None], ranks] = np.arange(n)[None, :]
    result = []
    for i, team in enumerate(prepared.teams):
        distribution = np.bincount(positions[:, i], minlength=n) / simulations
        attack_samples = fit.attack_current[:, i]
        defense_samples = fit.defense_current[:, i]
        result.append({
            "team": team["slug"],
            "projected_points": round(float(arrays["pts"][:, i].mean()), 2),
            "points_sd": round(float(arrays["pts"][:, i].std()), 2),
            "points_interval": [round(float(np.quantile(arrays["pts"][:, i], .05)), 1), round(float(np.quantile(arrays["pts"][:, i], .95)), 1)],
            "avg_position": round(float(positions[:, i].mean() + 1), 2),
            "position_distribution": [round(float(value), 6) for value in distribution],
            "attack": round(float(attack_samples.mean()), 4),
            "attack_interval": [round(float(np.quantile(attack_samples, .05)), 4), round(float(np.quantile(attack_samples, .95)), 4)],
            "defense": round(float(-defense_samples.mean()), 4),
            "defense_interval": [round(float(-np.quantile(defense_samples, .95)), 4), round(float(-np.quantile(defense_samples, .05)), 4)],
        })
    return result


def simulate_epl(prepared: PreparedLeague, fit: PosteriorFit, simulations: int) -> SimulationResult:
    rng = np.random.default_rng(RANDOM_SEED + 100)
    arrays, *_ = _simulate_regular(prepared, fit, simulations, rng)
    ranks = _rank_all(arrays)
    forecast = _base_forecast(prepared, fit, arrays, ranks)
    n = len(prepared.teams)
    positions = np.empty_like(ranks)
    positions[np.arange(simulations)[:, None], ranks] = np.arange(n)[None, :]
    for i, row in enumerate(forecast):
        row["title"] = round(float(np.mean(positions[:, i] == 0)), 6)
        row["top4"] = round(float(np.mean(positions[:, i] < 4)), 6)
        row["europe"] = round(float(np.mean(positions[:, i] < 7)), 6)
        row["relegation"] = round(float(np.mean(positions[:, i] >= n - 3)), 6)
    return SimulationResult(_current_table_rows(prepared), forecast, _fixture_probabilities(prepared, fit))


def _penalty_win_probability(att_h, def_h, att_a, def_a):
    difference = (att_h - def_h) - (att_a - def_a)
    return np.clip(1 / (1 + np.exp(-0.8 * difference)), 0.35, 0.65)


def simulate_mls(prepared: PreparedLeague, fit: PosteriorFit, simulations: int) -> SimulationResult:
    rng = np.random.default_rng(RANDOM_SEED + 200)
    arrays, end_attack, end_defense, sample_idx, intercept, home_adv, value_coef = _simulate_regular(prepared, fit, simulations, rng)
    ranks = _rank_all(arrays)
    forecast = _base_forecast(prepared, fit, arrays, ranks)
    n = len(prepared.teams)
    east = np.asarray([i for i, team in enumerate(prepared.teams) if team["conference"] == "East"], dtype=int)
    west = np.asarray([i for i, team in enumerate(prepared.teams) if team["conference"] == "West"], dtype=int)
    if len(east) < 9 or len(west) < 9:
        raise ValueError(f"MLS conference mapping failed: East={len(east)}, West={len(west)}.")

    counters = {name: np.zeros(n, dtype=np.int32) for name in ("shield", "playoffs", "conf_semis", "conf_final", "cup_final", "champion")}
    score = _composite(arrays["pts"], arrays["gd"], arrays["gf"], arrays["w"])
    values = np.asarray([max(float(team["market_value"]), .01) for team in prepared.teams])

    def match_winner(sim: int, home: int, away: int, neutral: bool = False) -> int:
        value_diff = math.log(values[home] / values[away])
        lh = math.exp(float(np.clip(intercept[sim] + (0 if neutral else home_adv[sim]) + end_attack[sim, home] - end_defense[sim, away] + value_coef[sim] * value_diff, -3, 2)))
        la = math.exp(float(np.clip(intercept[sim] + end_attack[sim, away] - end_defense[sim, home] - value_coef[sim] * value_diff, -3, 2)))
        hg, ag = rng.poisson(lh), rng.poisson(la)
        if hg > ag: return home
        if ag > hg: return away
        p_home = _penalty_win_probability(end_attack[sim, home], end_defense[sim, home], end_attack[sim, away], end_defense[sim, away])
        return home if rng.random() < p_home else away

    def best_of_three(sim: int, high: int, low: int) -> int:
        wins = {high: 0, low: 0}
        for home, away in ((high, low), (low, high), (high, low)):
            winner = match_winner(sim, home, away)
            wins[winner] += 1
            if wins[winner] == 2:
                return winner
        return high

    conf_champions = np.zeros((simulations, 2), dtype=int)
    for sim in range(simulations):
        shield = int(np.argmax(score[sim]))
        counters["shield"][shield] += 1
        champions = []
        for conference in (east, west):
            seeded = conference[np.argsort(-score[sim, conference])]
            qualifiers = seeded[:9]
            counters["playoffs"][qualifiers] += 1
            wild = match_winner(sim, int(seeded[7]), int(seeded[8]))
            r1 = [
                best_of_three(sim, int(seeded[0]), wild),
                best_of_three(sim, int(seeded[1]), int(seeded[6])),
                best_of_three(sim, int(seeded[2]), int(seeded[5])),
                best_of_three(sim, int(seeded[3]), int(seeded[4])),
            ]
            counters["conf_semis"][r1] += 1
            semi1_home, semi1_away = (r1[0], r1[3]) if score[sim, r1[0]] >= score[sim, r1[3]] else (r1[3], r1[0])
            semi2_home, semi2_away = (r1[1], r1[2]) if score[sim, r1[1]] >= score[sim, r1[2]] else (r1[2], r1[1])
            s1 = match_winner(sim, semi1_home, semi1_away)
            s2 = match_winner(sim, semi2_home, semi2_away)
            counters["conf_final"][[s1, s2]] += 1
            home, away = (s1, s2) if score[sim, s1] >= score[sim, s2] else (s2, s1)
            champions.append(match_winner(sim, home, away))
        counters["cup_final"][champions] += 1
        cup_home, cup_away = (champions[0], champions[1]) if score[sim, champions[0]] >= score[sim, champions[1]] else (champions[1], champions[0])
        winner = match_winner(sim, cup_home, cup_away)
        counters["champion"][winner] += 1
        conf_champions[sim] = champions

    for i, row in enumerate(forecast):
        for key, values_count in counters.items():
            row[key] = round(float(values_count[i] / simulations), 6)
    return SimulationResult(_current_table_rows(prepared), forecast, _fixture_probabilities(prepared, fit))
