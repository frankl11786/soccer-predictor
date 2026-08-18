from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

OUTCOMES = ("home", "draw", "away")
SOURCES = ("model", "polymarket", "kalshi", "consensus")
SOURCE_LABELS = {
    "model": "Bayesian model",
    "polymarket": "Polymarket",
    "kalshi": "Kalshi",
    "consensus": "Market consensus",
}


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _distribution(value: Any) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    try:
        result = {outcome: float(value[outcome]) for outcome in OUTCOMES}
    except (KeyError, TypeError, ValueError):
        return None
    if any(probability < 0 for probability in result.values()):
        return None
    total = sum(result.values())
    if total <= 0:
        return None
    return {outcome: result[outcome] / total for outcome in OUTCOMES}


def _fixture_sources(fixture: dict[str, Any]) -> dict[str, dict[str, float]]:
    sources: dict[str, dict[str, float]] = {}
    model = _distribution(fixture.get("probabilities"))
    if model:
        sources["model"] = model
    polymarket = _distribution((fixture.get("polymarket") or {}).get("probabilities"))
    if polymarket:
        sources["polymarket"] = polymarket
    kalshi = _distribution((fixture.get("kalshi") or {}).get("probabilities"))
    if kalshi:
        sources["kalshi"] = kalshi
    consensus = _distribution((fixture.get("market_consensus") or {}).get("probabilities"))
    if consensus:
        sources["consensus"] = consensus
    return sources


def _actual_outcome(fixture: dict[str, Any]) -> str | None:
    if fixture.get("status") != "final":
        return None
    try:
        home_score = int(fixture["home_score"])
        away_score = int(fixture["away_score"])
    except (KeyError, TypeError, ValueError):
        return None
    if home_score > away_score:
        return "home"
    if away_score > home_score:
        return "away"
    return "draw"


def _score_distribution(probabilities: dict[str, float], actual: str) -> dict[str, Any]:
    top_pick = max(OUTCOMES, key=lambda outcome: probabilities[outcome])
    actual_probability = probabilities[actual]
    brier = sum(
        (probabilities[outcome] - (1.0 if outcome == actual else 0.0)) ** 2
        for outcome in OUTCOMES
    )
    log_loss = -math.log(max(actual_probability, 1e-12))
    return {
        "top_pick": top_pick,
        "correct_pick": top_pick == actual,
        "actual_probability": round(actual_probability, 6),
        "brier": round(brier, 6),
        "log_loss": round(log_loss, 6),
    }


def update_prediction_history(
    previous_history: list[dict[str, Any]] | None,
    current_fixtures: list[dict[str, Any]],
    generated_at: str,
    model_version: str | None = None,
) -> list[dict[str, Any]]:
    """Maintain the latest pre-kickoff prediction for each fixture and grade it later.

    A scheduled fixture is refreshed on every snapshot before kickoff, so the stored
    probabilities act like a closing pregame snapshot. Once the fixture is final,
    that record is frozen and graded against the observed 1X2 result.
    """

    generated_dt = _parse_datetime(generated_at) or datetime.now(timezone.utc)
    records: dict[str, dict[str, Any]] = {}
    for item in previous_history or []:
        if not isinstance(item, dict):
            continue
        fixture_id = str(item.get("fixture_id") or "")
        if fixture_id:
            records[fixture_id] = dict(item)

    for fixture in current_fixtures:
        fixture_id = str(fixture.get("id") or "")
        if not fixture_id:
            continue
        current = records.get(fixture_id)
        actual = _actual_outcome(fixture)

        if actual is not None:
            # Never manufacture a retrospective forecast from a post-match model.
            # Only grade a fixture if we previously captured a genuine pregame row.
            if not current or not current.get("sources"):
                continue
            current["status"] = "final"
            current["actual"] = {
                "outcome": actual,
                "home_score": int(fixture["home_score"]),
                "away_score": int(fixture["away_score"]),
            }
            current["graded_at"] = generated_at
            current["scores"] = {
                source: _score_distribution(probabilities, actual)
                for source, probabilities in (current.get("sources") or {}).items()
                if source in SOURCES and _distribution(probabilities)
            }
            records[fixture_id] = current
            continue

        kickoff = _parse_datetime(fixture.get("kickoff") or fixture.get("date"))
        if kickoff is None or kickoff <= generated_dt:
            continue
        sources = _fixture_sources(fixture)
        if "model" not in sources:
            continue

        # Keep the latest available pre-kickoff snapshot. Market sources are optional.
        records[fixture_id] = {
            "fixture_id": fixture_id,
            "status": "pending",
            "date": str(fixture.get("date") or "")[:10],
            "kickoff": str(fixture.get("kickoff") or ""),
            "round": fixture.get("round"),
            "home": fixture.get("home"),
            "away": fixture.get("away"),
            "captured_at": generated_at,
            "model_version": model_version,
            "market_refs": {
                "polymarket": {
                    "event_url": (fixture.get("polymarket") or {}).get("event_url"),
                    "updated_at": (fixture.get("polymarket") or {}).get("updated_at"),
                } if fixture.get("polymarket") else None,
                "kalshi": {
                    "event_url": (fixture.get("kalshi") or {}).get("event_url"),
                    "updated_at": (fixture.get("kalshi") or {}).get("updated_at"),
                } if fixture.get("kalshi") else None,
            },
            "sources": {
                source: {outcome: round(probabilities[outcome], 6) for outcome in OUTCOMES}
                for source, probabilities in sources.items()
            },
        }

    ordered = sorted(
        records.values(),
        key=lambda item: (str(item.get("date") or ""), str(item.get("fixture_id") or "")),
    )
    # More than enough for a full league season while preventing unbounded growth.
    return ordered[-1500:]


def _aggregate(records: list[dict[str, Any]], source: str) -> dict[str, Any]:
    scored = []
    for record in records:
        if record.get("status") != "final":
            continue
        source_score = (record.get("scores") or {}).get(source)
        if isinstance(source_score, dict):
            scored.append(source_score)
    if not scored:
        return {
            "source": source,
            "label": SOURCE_LABELS[source],
            "matches": 0,
            "pick_accuracy": None,
            "brier": None,
            "log_loss": None,
            "avg_actual_probability": None,
        }
    n = len(scored)
    return {
        "source": source,
        "label": SOURCE_LABELS[source],
        "matches": n,
        "pick_accuracy": round(sum(bool(row.get("correct_pick")) for row in scored) / n, 6),
        "brier": round(sum(float(row["brier"]) for row in scored) / n, 6),
        "log_loss": round(sum(float(row["log_loss"]) for row in scored) / n, 6),
        "avg_actual_probability": round(sum(float(row["actual_probability"]) for row in scored) / n, 6),
    }


def _subset(history: list[dict[str, Any]], required_sources: tuple[str, ...]) -> list[dict[str, Any]]:
    result = []
    for record in history:
        if record.get("status") != "final":
            continue
        scores = record.get("scores") or {}
        if all(source in scores for source in required_sources):
            result.append(record)
    return result


def build_accuracy_summary(history: list[dict[str, Any]]) -> dict[str, Any]:
    finalized = [record for record in history if record.get("status") == "final"]
    pending = [record for record in history if record.get("status") != "final"]
    overall = {source: _aggregate(finalized, source) for source in SOURCES}

    comparisons: dict[str, Any] = {}
    for key, sources in {
        "model_vs_polymarket": ("model", "polymarket"),
        "model_vs_kalshi": ("model", "kalshi"),
        "all_three": ("model", "polymarket", "kalshi"),
    }.items():
        rows = _subset(finalized, sources)
        comparisons[key] = {
            "matches": len(rows),
            "sources": {source: _aggregate(rows, source) for source in sources},
        }

    return {
        "tracking_method": "Latest captured pre-kickoff snapshot; no retrospective reconstruction",
        "primary_metric": "Multiclass Brier score (lower is better)",
        "graded_matches": len(finalized),
        "pending_matches": len(pending),
        "overall": overall,
        "comparisons": comparisons,
    }


def attach_postgame_analysis(
    fixtures: list[dict[str, Any]],
    history: list[dict[str, Any]],
) -> None:
    by_id = {
        str(record.get("fixture_id")): record
        for record in history
        if record.get("status") == "final" and record.get("fixture_id")
    }
    for fixture in fixtures:
        record = by_id.get(str(fixture.get("id") or ""))
        if not record:
            fixture.pop("postgame_analysis", None)
            continue
        fixture["postgame_analysis"] = {
            "captured_at": record.get("captured_at"),
            "actual": record.get("actual"),
            "sources": record.get("sources"),
            "scores": record.get("scores"),
            "comparison_only": True,
        }
