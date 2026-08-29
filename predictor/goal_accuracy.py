from __future__ import annotations

from math import exp, factorial, sqrt
from typing import Any, Iterable

DEFAULT_TOTAL_BINS = (
    (0.0, 1.99),
    (2.0, 2.49),
    (2.5, 2.99),
    (3.0, 3.49),
    (3.5, 3.99),
    (4.0, 99.0),
)
OU_LINES = (1.5, 2.5, 3.5, 4.5)
MIN_CALIBRATION_MATCHES = 40
MAX_ACCURACY_HISTORY = 2000


def _num(value: Any) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if value != value:
        return None
    return value


def raw_expected_total(fixture: dict[str, Any]) -> float | None:
    home = None
    away = None
    for key in ("expected_home_goals", "lambda_home", "xg_home"):
        home = _num(fixture.get(key))
        if home is not None:
            break
    for key in ("expected_away_goals", "lambda_away", "xg_away"):
        away = _num(fixture.get(key))
        if away is not None:
            break
    if home is None or away is None:
        return None
    return max(0.05, home + away)


def actual_total(fixture: dict[str, Any]) -> float | None:
    home = _num(fixture.get("home_score"))
    away = _num(fixture.get("away_score"))
    if home is None or away is None:
        return None
    return home + away


def _fixture_id(fixture: dict[str, Any]) -> str:
    return str(fixture.get("id") or fixture.get("fixture_id") or "")


def _status_final(fixture: dict[str, Any]) -> bool:
    return str(fixture.get("status") or "").lower() in {"final", "finished", "ft"}


def _snapshot_prediction(previous_fixture: dict[str, Any]) -> float | None:
    for key in ("expected_total_goals", "expected_total_goals_raw"):
        value = _num(previous_fixture.get(key))
        if value is not None:
            return value
    return raw_expected_total(previous_fixture)


def append_newly_resolved_forecasts(
    history: list[dict[str, Any]],
    previous_fixtures: Iterable[dict[str, Any]],
    current_fixtures: Iterable[dict[str, Any]],
    *,
    league: str,
) -> list[dict[str, Any]]:
    previous = {_fixture_id(f): f for f in previous_fixtures if _fixture_id(f)}
    existing = {str(row.get("fixture_id") or "") for row in history}
    merged = [dict(row) for row in history if isinstance(row, dict)]

    for current in current_fixtures:
        fid = _fixture_id(current)
        if not fid or fid in existing or not _status_final(current):
            continue
        before = previous.get(fid)
        if not before or _status_final(before):
            continue

        predicted = _snapshot_prediction(before)
        observed = actual_total(current)
        if predicted is None or observed is None:
            continue

        raw = raw_expected_total(before)
        home_pred = None
        away_pred = None
        for key in ("expected_home_goals", "lambda_home", "xg_home"):
            home_pred = _num(before.get(key))
            if home_pred is not None:
                break
        for key in ("expected_away_goals", "lambda_away", "xg_away"):
            away_pred = _num(before.get(key))
            if away_pred is not None:
                break

        merged.append({
            "fixture_id": fid,
            "league": league,
            "date": current.get("date") or current.get("kickoff"),
            "home": current.get("home"),
            "away": current.get("away"),
            "predicted_total": round(predicted, 4),
            "raw_predicted_total": round(raw, 4) if raw is not None else None,
            "predicted_home_goals": round(home_pred, 4) if home_pred is not None else None,
            "predicted_away_goals": round(away_pred, 4) if away_pred is not None else None,
            "actual_home_goals": _num(current.get("home_score")),
            "actual_away_goals": _num(current.get("away_score")),
            "actual_total": observed,
            "error_actual_minus_predicted": round(observed - predicted, 4),
            "error_predicted_minus_actual": round(predicted - observed, 4),
            "absolute_error": round(abs(predicted - observed), 4),
            "source": "frozen_previous_snapshot",
        })
        existing.add(fid)

    return merged[-MAX_ACCURACY_HISTORY:]


def _pav(x: list[float], y: list[float], weights: list[float]) -> tuple[list[float], list[float]]:
    blocks: list[dict[str, float]] = []
    for xi, yi, wi in zip(x, y, weights):
        blocks.append({"xw": xi * wi, "yw": yi * wi, "w": wi, "xmin": xi, "xmax": xi})
        while len(blocks) >= 2:
            a, b = blocks[-2], blocks[-1]
            if a["yw"] / a["w"] <= b["yw"] / b["w"]:
                break
            blocks[-2:] = [{
                "xw": a["xw"] + b["xw"],
                "yw": a["yw"] + b["yw"],
                "w": a["w"] + b["w"],
                "xmin": a["xmin"],
                "xmax": b["xmax"],
            }]
    xs, ys = [], []
    for block in blocks:
        xs.append(block["xw"] / block["w"])
        ys.append(block["yw"] / block["w"])
    return xs, ys


def fit_total_goals_calibration(
    history: Iterable[dict[str, Any]],
    *,
    min_matches: int = MIN_CALIBRATION_MATCHES,
) -> dict[str, Any]:
    rows = []
    for row in history:
        predicted = _num(row.get("predicted_total"))
        actual = _num(row.get("actual_total"))
        if predicted is not None and actual is not None:
            rows.append((predicted, actual))
    rows.sort()

    if len(rows) < min_matches:
        return {
            "status": "insufficient_frozen_history",
            "matches": len(rows),
            "min_matches": min_matches,
            "method": "identity_until_enough_frozen_predictions",
            "x": [],
            "y": [],
        }

    target_bins = min(12, max(5, round(len(rows) ** 0.5)))
    chunk = max(1, len(rows) // target_bins)
    bx, by, bw = [], [], []
    for start in range(0, len(rows), chunk):
        sample = rows[start:start + chunk]
        if not sample:
            continue
        bw.append(float(len(sample)))
        bx.append(sum(p for p, _ in sample) / len(sample))
        by.append(sum(a for _, a in sample) / len(sample))

    xs, ys = _pav(bx, by, bw)
    return {
        "status": "calibrated",
        "matches": len(rows),
        "min_matches": min_matches,
        "method": "out_of_sample_isotonic_piecewise_linear",
        "x": [round(v, 5) for v in xs],
        "y": [round(v, 5) for v in ys],
    }


def apply_total_calibration(raw_total: float, calibration: dict[str, Any] | None) -> float:
    if not calibration or calibration.get("status") != "calibrated":
        return raw_total
    xs = [_num(v) for v in calibration.get("x", [])]
    ys = [_num(v) for v in calibration.get("y", [])]
    if any(v is None for v in xs + ys) or not xs or len(xs) != len(ys):
        return raw_total
    xs = [float(v) for v in xs]
    ys = [float(v) for v in ys]
    if raw_total <= xs[0]:
        return max(0.05, ys[0] + (raw_total - xs[0]))
    if raw_total >= xs[-1]:
        return max(0.05, ys[-1] + (raw_total - xs[-1]))
    for i in range(1, len(xs)):
        if raw_total <= xs[i]:
            lo_x, hi_x = xs[i - 1], xs[i]
            lo_y, hi_y = ys[i - 1], ys[i]
            frac = (raw_total - lo_x) / max(1e-9, hi_x - lo_x)
            return max(0.05, lo_y + frac * (hi_y - lo_y))
    return raw_total


def poisson_over_probability(lam: float, line: float) -> float:
    max_under = int(line // 1)
    under_or_equal = sum(exp(-lam) * (lam ** k) / factorial(k) for k in range(max_under + 1))
    return max(0.0, min(1.0, 1.0 - under_or_equal))


def enrich_fixtures_with_goal_totals(
    fixtures: Iterable[dict[str, Any]],
    calibration: dict[str, Any] | None,
) -> None:
    for fixture in fixtures:
        raw = raw_expected_total(fixture)
        if raw is None:
            continue
        calibrated = apply_total_calibration(raw, calibration)
        fixture["expected_total_goals_raw"] = round(raw, 3)
        fixture["expected_total_goals"] = round(calibrated, 2)
        fixture["total_goals_calibration_applied"] = bool(
            calibration and calibration.get("status") == "calibrated"
        )
        fixture["over_probabilities"] = {
            str(line): round(poisson_over_probability(calibrated, line), 4)
            for line in OU_LINES
        }


def _calibration_bins(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for low, high in DEFAULT_TOTAL_BINS:
        members = []
        for row in rows:
            p = _num(row.get("predicted_total"))
            if p is not None and low <= p <= high:
                members.append(row)
        if not members:
            continue
        pred = [_num(row.get("predicted_total")) for row in members]
        act = [_num(row.get("actual_total")) for row in members]
        pred = [v for v in pred if v is not None]
        act = [v for v in act if v is not None]
        if not pred or not act:
            continue
        result.append({
            "range": f"{low:.2f}-{high:.2f}" if high < 90 else f"{low:.2f}+",
            "matches": len(members),
            "mean_predicted": round(sum(pred) / len(pred), 3),
            "mean_actual": round(sum(act) / len(act), 3),
            "bias_predicted_minus_actual": round(sum(pred) / len(pred) - sum(act) / len(act), 3),
        })
    return result


def _ou_metrics(rows: list[dict[str, Any]], line: float) -> dict[str, Any]:
    scored = []
    for row in rows:
        predicted = _num(row.get("predicted_total"))
        actual = _num(row.get("actual_total"))
        if predicted is None or actual is None:
            continue
        probability = poisson_over_probability(predicted, line)
        outcome = 1.0 if actual > line else 0.0
        scored.append((probability, outcome))
    if not scored:
        return {"matches": 0}
    brier = sum((p - y) ** 2 for p, y in scored) / len(scored)
    return {
        "matches": len(scored),
        "mean_predicted_over_probability": round(sum(p for p, _ in scored) / len(scored), 4),
        "actual_over_rate": round(sum(y for _, y in scored) / len(scored), 4),
        "brier_score": round(brier, 4),
    }


def build_total_goals_accuracy(history: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = [
        row for row in history
        if _num(row.get("predicted_total")) is not None and _num(row.get("actual_total")) is not None
    ]
    if not rows:
        return {"status": "collecting_frozen_predictions", "matches": 0, "headline_metric": "MAE"}

    predicted = [float(row["predicted_total"]) for row in rows]
    actual = [float(row["actual_total"]) for row in rows]
    errors = [p - a for p, a in zip(predicted, actual)]
    abs_errors = [abs(e) for e in errors]
    squared = [e * e for e in errors]

    return {
        "status": "ready",
        "matches": len(rows),
        "headline_metric": "MAE",
        "mae": round(sum(abs_errors) / len(rows), 3),
        "rmse": round(sqrt(sum(squared) / len(rows)), 3),
        "bias_predicted_minus_actual": round(sum(errors) / len(rows), 3),
        "mean_predicted": round(sum(predicted) / len(rows), 3),
        "mean_actual": round(sum(actual) / len(rows), 3),
        "within_0_5": round(sum(e <= 0.5 for e in abs_errors) / len(rows), 4),
        "within_1_0": round(sum(e <= 1.0 for e in abs_errors) / len(rows), 4),
        "rounded_exact_hit_rate": round(
            sum(round(p) == round(a) for p, a in zip(predicted, actual)) / len(rows), 4
        ),
        "calibration_bins": _calibration_bins(rows),
        "over_under": {str(line): _ou_metrics(rows, line) for line in OU_LINES},
    }


def build_goal_accuracy_state(
    league: str,
    previous_history: list[dict[str, Any]] | None,
    previous_fixtures: Iterable[dict[str, Any]],
    current_fixtures: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    history = append_newly_resolved_forecasts(
        list(previous_history or []), previous_fixtures, current_fixtures, league=league
    )
    calibration = fit_total_goals_calibration(history)
    enrich_fixtures_with_goal_totals(current_fixtures, calibration)
    accuracy = {
        "total_goals": build_total_goals_accuracy(history),
        "calibration": calibration,
        "frozen_forecast_integrity": {
            "method": "previous_published_snapshot_only",
            "post_result_leakage": False,
            "note": (
                "A completed match enters the archive only when the immediately prior "
                "published snapshot contained an unfinished pre-match forecast."
            ),
        },
    }
    return history, calibration, accuracy
