from __future__ import annotations

import math
from typing import Any

DEFAULT_TOTAL_LINES = (0.5, 1.5, 2.5, 3.5, 4.5, 5.5)


def poisson_pmf(rate: float, goals: int) -> float:
    rate = max(0.0, float(rate))
    return math.exp(-rate) * (rate ** goals) / math.factorial(goals)


def model_goal_totals(xg_home: float, xg_away: float, lines: tuple[float, ...] = DEFAULT_TOTAL_LINES) -> dict[str, Any]:
    """Closed-form total-goals probabilities implied by the mean-xG Poisson view.

    Independent Poisson home/away goals imply a Poisson distribution for the
    total with rate lambda_home + lambda_away. This is intentionally the same
    mean-xG view used by the exact-score matrix; it is distinct from the full
    posterior 1X2 distribution, which includes parameter uncertainty.
    """

    total_rate = max(0.0, float(xg_home)) + max(0.0, float(xg_away))
    exact: dict[str, float] = {}
    cumulative = 0.0
    for goals in range(6):
        probability = poisson_pmf(total_rate, goals)
        exact[str(goals)] = round(probability, 6)
        cumulative += probability
    exact["6+"] = round(max(0.0, 1.0 - cumulative), 6)

    over: dict[str, float] = {}
    under: dict[str, float] = {}
    for line in lines:
        cutoff = int(math.floor(line))
        under_probability = sum(poisson_pmf(total_rate, goals) for goals in range(cutoff + 1))
        over_probability = max(0.0, 1.0 - under_probability)
        key = f"{line:.1f}"
        over[key] = round(over_probability, 6)
        under[key] = round(under_probability, 6)

    return {
        "method": "Poisson total with rate equal to home xG + away xG",
        "lambda": round(total_rate, 6),
        "exact": exact,
        "over": over,
        "under": under,
    }
