from __future__ import annotations

import json
import math
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

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
    elif polymarket and kalshi:
        consensus = {
            outcome: (polymarket[outcome] + kalshi[outcome]) / 2.0
            for outcome in OUTCOMES
        }
        sources["consensus"] = consensus
    elif polymarket:
        sources["consensus"] = dict(polymarket)
    elif kalshi:
        sources["consensus"] = dict(kalshi)
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


def _binary_score(probability: float, actual: bool) -> dict[str, Any]:
    probability = min(1.0, max(0.0, float(probability)))
    actual_probability = probability if actual else 1.0 - probability
    predicted_over = probability >= 0.5
    return {
        "predicted_over": predicted_over,
        "correct_pick": predicted_over == actual,
        "actual_probability": round(actual_probability, 6),
        "brier": round((probability - (1.0 if actual else 0.0)) ** 2, 6),
        "log_loss": round(-math.log(max(actual_probability, 1e-12)), 6),
    }


def _goal_totals_from_fixture(fixture: dict[str, Any]) -> dict[str, Any] | None:
    value = fixture.get("goal_totals")
    if not isinstance(value, dict) or not isinstance(value.get("model"), dict):
        return None
    copied: dict[str, Any] = {}
    for source in ("model", "polymarket", "kalshi", "consensus"):
        row = value.get(source)
        if not isinstance(row, dict):
            continue
        over = row.get("over")
        under = row.get("under")
        if not isinstance(over, dict):
            continue
        copied[source] = {
            **{key: val for key, val in row.items() if key not in {"over", "under"}},
            "over": {str(line): round(float(prob), 6) for line, prob in over.items()},
            "under": {str(line): round(float(prob), 6) for line, prob in (under or {}).items()},
        }
    return copied or None


def _score_goal_totals(goal_totals: dict[str, Any], total_goals: int) -> dict[str, Any]:
    scored: dict[str, Any] = {}
    for source in SOURCES:
        source_row = goal_totals.get(source)
        over = source_row.get("over") if isinstance(source_row, dict) else None
        if not isinstance(over, dict):
            continue
        line_scores: dict[str, Any] = {}
        for line, probability in over.items():
            try:
                threshold = float(line)
                value = float(probability)
            except (TypeError, ValueError):
                continue
            actual_over = total_goals > threshold
            line_scores[str(line)] = {
                "actual_over": actual_over,
                "over_probability": round(value, 6),
                **_binary_score(value, actual_over),
            }
        if line_scores:
            scored[source] = line_scores
    return scored


def _market_refs(fixture: dict[str, Any]) -> dict[str, Any]:
    return {
        "polymarket": {
            "event_url": (fixture.get("polymarket") or {}).get("event_url"),
            "event_slug": (fixture.get("polymarket") or {}).get("event_slug"),
            "updated_at": (fixture.get("polymarket") or {}).get("updated_at"),
        }
        if fixture.get("polymarket")
        else None,
        "kalshi": {
            "event_url": (fixture.get("kalshi") or {}).get("event_url"),
            "event_ticker": (fixture.get("kalshi") or {}).get("event_ticker"),
            "updated_at": (fixture.get("kalshi") or {}).get("updated_at"),
        }
        if fixture.get("kalshi")
        else None,
    }


def _record_from_fixture(
    fixture: dict[str, Any],
    captured_at: str,
    model_version: str | None,
    *,
    fixture_id: str | None = None,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    sources = _fixture_sources(fixture)
    if "model" not in sources:
        return None
    return {
        "fixture_id": fixture_id or str(fixture.get("id") or ""),
        "status": "pending",
        "date": str(fixture.get("date") or "")[:10],
        "kickoff": str(fixture.get("kickoff") or ""),
        "round": fixture.get("round"),
        "home": fixture.get("home"),
        "away": fixture.get("away"),
        "captured_at": captured_at,
        "model_version": model_version,
        "xg_home": fixture.get("xg_home"),
        "xg_away": fixture.get("xg_away"),
        "market_refs": _market_refs(fixture),
        "goal_totals": _goal_totals_from_fixture(fixture),
        "sources": {
            source: {outcome: round(probabilities[outcome], 6) for outcome in OUTCOMES}
            for source, probabilities in sources.items()
        },
        **({"provenance": provenance} if provenance else {}),
    }


def _grade_record(record: dict[str, Any], fixture: dict[str, Any], graded_at: str) -> dict[str, Any]:
    actual = _actual_outcome(fixture)
    if actual is None:
        return record
    graded = dict(record)
    graded["status"] = "final"
    home_score = int(fixture["home_score"])
    away_score = int(fixture["away_score"])
    total_goals = home_score + away_score
    graded["actual"] = {
        "outcome": actual,
        "home_score": home_score,
        "away_score": away_score,
        "total_goals": total_goals,
    }
    graded["graded_at"] = graded_at
    graded["scores"] = {
        source: _score_distribution(probabilities, actual)
        for source, probabilities in (graded.get("sources") or {}).items()
        if source in SOURCES and _distribution(probabilities)
    }
    goal_totals = graded.get("goal_totals")
    if isinstance(goal_totals, dict):
        graded["totals_scores"] = _score_goal_totals(goal_totals, total_goals)
    return graded


def _fixture_identity(fixture: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(fixture.get("date") or "")[:10],
        str(fixture.get("home") or ""),
        str(fixture.get("away") or ""),
    )


def _market_source_count(record: dict[str, Any]) -> int:
    sources = record.get("sources") or {}
    return int("polymarket" in sources) + int("kalshi" in sources)


def _candidate_rank(record: dict[str, Any]) -> tuple[int, int, float]:
    captured = _parse_datetime(record.get("captured_at"))
    timestamp = captured.timestamp() if captured else 0.0
    sources = record.get("sources") or {}
    return (_market_source_count(record), int("consensus" in sources), timestamp)


def recover_prediction_history_from_snapshots(
    snapshots: Iterable[dict[str, Any]],
    current_fixtures: list[dict[str, Any]],
    existing_history: list[dict[str, Any]] | None = None,
    recovered_at: str | None = None,
) -> list[dict[str, Any]]:
    """Recover genuine pregame forecasts from archived JSON snapshots.

    Each snapshot must be a dict with `data` containing an old league JSON document.
    Optional `commit` and `generated_at` keys are retained as provenance. A historical
    row is used only when that archived snapshot was generated before the match kickoff.
    No post-match probability is used to manufacture a forecast.
    """

    recovered_at = recovered_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    current_by_id = {str(f.get("id") or ""): f for f in current_fixtures if f.get("id")}
    current_by_identity = {_fixture_identity(f): f for f in current_fixtures}
    candidates: dict[str, dict[str, Any]] = {}

    for wrapper in snapshots:
        data = wrapper.get("data") if isinstance(wrapper, dict) and "data" in wrapper else wrapper
        if not isinstance(data, dict):
            continue
        meta = data.get("meta") or {}
        generated_at = (
            (wrapper.get("generated_at") if isinstance(wrapper, dict) else None)
            or meta.get("generated_at")
            or meta.get("as_of")
        )
        generated_dt = _parse_datetime(generated_at)
        if generated_dt is None:
            continue
        model_version = meta.get("model_version")
        commit = wrapper.get("commit") if isinstance(wrapper, dict) else None

        for old_fixture in data.get("fixtures") or []:
            if not isinstance(old_fixture, dict):
                continue
            current = current_by_id.get(str(old_fixture.get("id") or ""))
            if current is None:
                current = current_by_identity.get(_fixture_identity(old_fixture))
            if current is None or _actual_outcome(current) is None:
                continue
            kickoff = _parse_datetime(current.get("kickoff") or current.get("date"))
            if kickoff is None or generated_dt >= kickoff:
                continue
            record = _record_from_fixture(
                old_fixture,
                str(generated_at),
                model_version,
                fixture_id=str(current.get("id") or old_fixture.get("id") or ""),
                provenance={
                    "type": "archived_git_snapshot",
                    "recovered": True,
                    "commit": str(commit)[:12] if commit else None,
                    "snapshot_generated_at": str(generated_at),
                    "recovered_at": recovered_at,
                },
            )
            if not record:
                continue
            fixture_id = record["fixture_id"]
            prior = candidates.get(fixture_id)
            if prior is None or _candidate_rank(record) > _candidate_rank(prior):
                candidates[fixture_id] = record

    merged: dict[str, dict[str, Any]] = {}
    for row in existing_history or []:
        if isinstance(row, dict) and row.get("fixture_id"):
            merged[str(row["fixture_id"])] = dict(row)

    now_text = recovered_at
    for fixture_id, candidate in candidates.items():
        current = current_by_id.get(fixture_id)
        if current is None:
            continue
        existing = merged.get(fixture_id)
        # Prefer an already captured row when it has at least as much market coverage.
        # Otherwise fill the historical gap with the richer archived pregame snapshot.
        if existing and _market_source_count(existing) >= _market_source_count(candidate):
            continue
        merged[fixture_id] = _grade_record(candidate, current, now_text)

    return sorted(
        merged.values(),
        key=lambda item: (str(item.get("date") or ""), str(item.get("fixture_id") or "")),
    )[-1500:]


def _run_git(repo_root: Path, args: list[str]) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout


def recover_prediction_history_from_git(
    league: str,
    current_fixtures: list[dict[str, Any]],
    existing_history: list[dict[str, Any]] | None = None,
    *,
    repo_root: str | Path | None = None,
    max_commits: int = 500,
    recovered_at: str | None = None,
) -> list[dict[str, Any]]:
    """Recover historical rows from committed `app/data/<league>.json` snapshots.

    This intentionally fails soft: if a checkout is shallow or git is unavailable,
    existing tracking data is returned unchanged.
    """

    root = Path(repo_root or Path.cwd()).resolve()
    path = f"app/data/{league}.json"
    try:
        commit_text = _run_git(
            root,
            ["log", f"--max-count={int(max_commits)}", "--format=%H", "--", path],
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return list(existing_history or [])

    snapshots: list[dict[str, Any]] = []
    for commit in [line.strip() for line in commit_text.splitlines() if line.strip()]:
        try:
            raw = _run_git(root, ["show", f"{commit}:{path}"])
            data = json.loads(raw)
        except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError, OSError):
            continue
        snapshots.append({"commit": commit, "data": data})

    return recover_prediction_history_from_snapshots(
        snapshots,
        current_fixtures,
        existing_history=existing_history,
        recovered_at=recovered_at,
    )


def update_prediction_history(
    previous_history: list[dict[str, Any]] | None,
    current_fixtures: list[dict[str, Any]],
    generated_at: str,
    model_version: str | None = None,
) -> list[dict[str, Any]]:
    """Maintain the latest pre-kickoff prediction for each fixture and grade it later."""

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
            # Only grade a fixture if a genuine pregame row already exists.
            if not current or not current.get("sources"):
                continue
            records[fixture_id] = _grade_record(current, fixture, generated_at)
            continue

        kickoff = _parse_datetime(fixture.get("kickoff") or fixture.get("date"))
        if kickoff is None or kickoff <= generated_dt:
            continue
        record = _record_from_fixture(fixture, generated_at, model_version)
        if not record:
            continue
        # Keep the latest available pre-kickoff snapshot. Market sources are optional.
        records[fixture_id] = record

    ordered = sorted(
        records.values(),
        key=lambda item: (str(item.get("date") or ""), str(item.get("fixture_id") or "")),
    )
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


def _aggregate_totals(records: list[dict[str, Any]], source: str) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    matches: set[str] = set()
    for record in records:
        if record.get("status") != "final":
            continue
        source_scores = (record.get("totals_scores") or {}).get(source)
        if not isinstance(source_scores, dict):
            continue
        for score in source_scores.values():
            if isinstance(score, dict):
                rows.append(score)
                matches.add(str(record.get("fixture_id") or ""))
    if not rows:
        return {
            "source": source,
            "label": SOURCE_LABELS[source],
            "matches": 0,
            "line_forecasts": 0,
            "pick_accuracy": None,
            "brier": None,
            "log_loss": None,
            "avg_actual_probability": None,
        }
    n = len(rows)
    return {
        "source": source,
        "label": SOURCE_LABELS[source],
        "matches": len({value for value in matches if value}),
        "line_forecasts": n,
        "pick_accuracy": round(sum(bool(row.get("correct_pick")) for row in rows) / n, 6),
        "brier": round(sum(float(row["brier"]) for row in rows) / n, 6),
        "log_loss": round(sum(float(row["log_loss"]) for row in rows) / n, 6),
        "avg_actual_probability": round(sum(float(row["actual_probability"]) for row in rows) / n, 6),
    }


def _shared_total_line_records(history: list[dict[str, Any]], required_sources: tuple[str, ...]) -> list[dict[str, Any]]:
    shared: list[dict[str, Any]] = []
    for record in history:
        if record.get("status") != "final":
            continue
        totals_scores = record.get("totals_scores") or {}
        if not all(isinstance(totals_scores.get(source), dict) for source in required_sources):
            continue
        common_lines = set(totals_scores[required_sources[0]])
        for source in required_sources[1:]:
            common_lines &= set(totals_scores[source])
        for line in common_lines:
            shared.append({
                "fixture_id": record.get("fixture_id"),
                "line": line,
                "scores": {source: totals_scores[source][line] for source in required_sources},
            })
    return shared


def _aggregate_total_shared(rows: list[dict[str, Any]], source: str) -> dict[str, Any]:
    scores = [row["scores"][source] for row in rows if source in (row.get("scores") or {})]
    matches = {str(row.get("fixture_id") or "") for row in rows}
    if not scores:
        return _aggregate_totals([], source)
    n = len(scores)
    return {
        "source": source,
        "label": SOURCE_LABELS[source],
        "matches": len({value for value in matches if value}),
        "line_forecasts": n,
        "pick_accuracy": round(sum(bool(row.get("correct_pick")) for row in scores) / n, 6),
        "brier": round(sum(float(row["brier"]) for row in scores) / n, 6),
        "log_loss": round(sum(float(row["log_loss"]) for row in scores) / n, 6),
        "avg_actual_probability": round(sum(float(row["actual_probability"]) for row in scores) / n, 6),
    }


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

    dates = sorted(str(record.get("date") or "") for record in finalized if record.get("date"))
    recovered = [
        record
        for record in finalized
        if (record.get("provenance") or {}).get("type") == "archived_git_snapshot"
    ]

    totals_overall = {source: _aggregate_totals(finalized, source) for source in SOURCES}
    totals_comparisons: dict[str, Any] = {}
    for key, sources in {
        "model_vs_polymarket": ("model", "polymarket"),
        "model_vs_kalshi": ("model", "kalshi"),
        "all_three": ("model", "polymarket", "kalshi"),
    }.items():
        rows = _shared_total_line_records(finalized, sources)
        totals_comparisons[key] = {
            "line_forecasts": len(rows),
            "matches": len({str(row.get("fixture_id") or "") for row in rows if row.get("fixture_id")}),
            "sources": {source: _aggregate_total_shared(rows, source) for source in sources},
        }

    return {
        "tracking_method": (
            "Latest genuine pre-kickoff probabilities persisted in a permanent per-fixture archive, with older gaps "
            "recoverable from committed git snapshots; no post-match model reconstruction"
        ),
        "primary_metric": "Multiclass Brier score (lower is better)",
        "graded_matches": len(finalized),
        "pending_matches": len(pending),
        "recovered_matches": len(recovered),
        "coverage_start": dates[0] if dates else None,
        "coverage_end": dates[-1] if dates else None,
        "overall": overall,
        "comparisons": comparisons,
        "goal_totals": {
            "primary_metric": "Binary Brier score across frozen over/under lines (lower is better)",
            "overall": totals_overall,
            "comparisons": totals_comparisons,
        },
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
            "goal_totals": record.get("goal_totals"),
            "totals_scores": record.get("totals_scores"),
            "market_refs": record.get("market_refs"),
            "source_captured_at": record.get("source_captured_at"),
            "archive": record.get("archive"),
            "provenance": record.get("provenance"),
            "comparison_only": True,
        }
