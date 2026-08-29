from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .history import (
    SOURCES,
    _actual_outcome,
    _grade_record,
    _parse_datetime,
    _record_from_fixture,
    attach_postgame_analysis,
    build_accuracy_summary,
)

ARCHIVE_SCHEMA_VERSION = 1
ARCHIVE_POLICY = "latest genuine pre-kickoff probability per source within the capture window; immutable after finalization"
DEFAULT_CAPTURE_WINDOW_HOURS = 72


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_fixture_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in text)


def _fixture_dir(archive_root: str | Path, league: str, fixture_id: str) -> Path:
    return Path(archive_root) / league / _safe_fixture_id(fixture_id)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return value if isinstance(value, dict) else None


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(path)


def _snapshot_generated_at(snapshot: dict[str, Any]) -> str | None:
    meta = snapshot.get("meta") or {}
    value = meta.get("generated_at") or meta.get("as_of")
    return str(value) if value else None


def _candidate_from_fixture(
    league: str,
    snapshot: dict[str, Any],
    fixture: dict[str, Any],
    capture_window_hours: int = DEFAULT_CAPTURE_WINDOW_HOURS,
) -> dict[str, Any] | None:
    captured_at = _snapshot_generated_at(snapshot)
    captured_dt = _parse_datetime(captured_at)
    kickoff = _parse_datetime(fixture.get("kickoff") or fixture.get("date"))
    if captured_dt is None or kickoff is None or captured_dt >= kickoff:
        return None
    hours_until_kickoff = (kickoff - captured_dt).total_seconds() / 3600.0
    if hours_until_kickoff > max(1, int(capture_window_hours)):
        return None

    fixture_id = str(fixture.get("id") or "")
    if not fixture_id or _actual_outcome(fixture) is not None:
        return None

    record = _record_from_fixture(
        fixture,
        captured_at,
        (snapshot.get("meta") or {}).get("model_version"),
        fixture_id=fixture_id,
        provenance={
            "type": "prediction_archive",
            "recovered": False,
            "capture_policy": ARCHIVE_POLICY,
        },
    )
    if not record:
        return None

    record["league"] = league
    record["schema_version"] = ARCHIVE_SCHEMA_VERSION
    record["locked"] = False
    record["source_captured_at"] = {
        source: captured_at for source in (record.get("sources") or {}) if source in SOURCES
    }
    record["goal_total_source_captured_at"] = {
        source: captured_at
        for source in (record.get("goal_totals") or {})
        if source in SOURCES
    }
    record["archive"] = {
        "policy": ARCHIVE_POLICY,
        "created_at": captured_at,
        "updated_at": captured_at,
    }
    return record


def _source_capture_time(record: dict[str, Any], source: str) -> datetime | None:
    mapping = record.get("source_captured_at") or {}
    return _parse_datetime(mapping.get(source) or record.get("captured_at"))


def _merge_pregame(existing: dict[str, Any] | None, candidate: dict[str, Any]) -> dict[str, Any]:
    if not existing:
        return candidate
    if existing.get("locked") or existing.get("status") == "final":
        return existing

    merged = dict(existing)
    merged.setdefault("sources", {})
    merged.setdefault("source_captured_at", {})
    merged.setdefault("market_refs", {})
    merged.setdefault("goal_totals", {})
    merged.setdefault("goal_total_source_captured_at", {})

    candidate_time = _parse_datetime(candidate.get("captured_at"))
    for source, probabilities in (candidate.get("sources") or {}).items():
        if source not in SOURCES:
            continue
        old_time = _source_capture_time(existing, source)
        if old_time is not None and candidate_time is not None and candidate_time < old_time:
            continue
        merged["sources"][source] = probabilities
        merged["source_captured_at"][source] = candidate.get("captured_at")
        if source in {"polymarket", "kalshi"}:
            candidate_ref = (candidate.get("market_refs") or {}).get(source)
            if candidate_ref:
                merged["market_refs"][source] = candidate_ref

    for source, total_row in (candidate.get("goal_totals") or {}).items():
        if source not in SOURCES or not isinstance(total_row, dict):
            continue
        old_text = (existing.get("goal_total_source_captured_at") or {}).get(source)
        old_time = _parse_datetime(old_text or existing.get("captured_at"))
        if old_time is not None and candidate_time is not None and candidate_time < old_time:
            continue
        merged["goal_totals"][source] = total_row
        merged["goal_total_source_captured_at"][source] = candidate.get("captured_at")

    # Keep fixture metadata current while preserving source-level capture timestamps.
    for key in ("fixture_id", "league", "date", "kickoff", "round", "home", "away", "model_version", "xg_home", "xg_away"):
        if candidate.get(key) is not None:
            merged[key] = candidate.get(key)

    model_captured = (merged.get("source_captured_at") or {}).get("model")
    if model_captured:
        merged["captured_at"] = model_captured
    elif candidate.get("captured_at"):
        merged["captured_at"] = candidate.get("captured_at")

    merged["schema_version"] = ARCHIVE_SCHEMA_VERSION
    merged["status"] = "pending"
    merged["locked"] = False
    merged["provenance"] = {
        "type": "prediction_archive",
        "recovered": bool((existing.get("provenance") or {}).get("recovered")),
        "capture_policy": ARCHIVE_POLICY,
    }
    archive = dict(existing.get("archive") or {})
    archive.setdefault("policy", ARCHIVE_POLICY)
    archive.setdefault("created_at", existing.get("captured_at") or candidate.get("captured_at"))
    archive["updated_at"] = candidate.get("captured_at") or archive.get("updated_at")
    merged["archive"] = archive
    return merged


def archive_snapshot(
    league: str,
    snapshot: dict[str, Any],
    archive_root: str | Path,
    capture_window_hours: int = DEFAULT_CAPTURE_WINDOW_HOURS,
) -> dict[str, int]:
    """Capture/update pregame records from one published snapshot.

    Sources are updated independently. If a later snapshot temporarily loses a market,
    the last genuine pre-kickoff quote for that market is retained instead of erased.
    Finalized records are never modified.
    """

    captured = 0
    updated = 0
    skipped_locked = 0
    for fixture in snapshot.get("fixtures") or []:
        if not isinstance(fixture, dict):
            continue
        candidate = _candidate_from_fixture(league, snapshot, fixture, capture_window_hours)
        if not candidate:
            continue
        fixture_id = candidate["fixture_id"]
        directory = _fixture_dir(archive_root, league, fixture_id)
        final_path = directory / "final.json"
        pregame_path = directory / "pregame.json"
        if final_path.exists():
            skipped_locked += 1
            continue
        existing = _read_json(pregame_path)
        merged = _merge_pregame(existing, candidate)
        if existing == merged:
            continue
        _write_json_atomic(pregame_path, merged)
        if existing:
            updated += 1
        else:
            captured += 1
    return {"captured": captured, "updated": updated, "skipped_locked": skipped_locked}


def _normalize_seed_record(league: str, record: dict[str, Any]) -> dict[str, Any] | None:
    fixture_id = str(record.get("fixture_id") or "")
    if not fixture_id or not isinstance(record.get("sources"), dict):
        return None
    normalized = dict(record)
    normalized["league"] = league
    normalized["schema_version"] = ARCHIVE_SCHEMA_VERSION
    normalized.setdefault("source_captured_at", {})
    for source in normalized.get("sources") or {}:
        if source in SOURCES:
            normalized["source_captured_at"].setdefault(source, normalized.get("captured_at"))
    normalized.setdefault("goal_total_source_captured_at", {})
    for source in normalized.get("goal_totals") or {}:
        if source in SOURCES:
            normalized["goal_total_source_captured_at"].setdefault(source, normalized.get("captured_at"))
    normalized.setdefault("provenance", {})
    normalized["provenance"] = {
        **normalized.get("provenance", {}),
        "type": (normalized.get("provenance") or {}).get("type") or "prediction_archive_seed",
        "capture_policy": ARCHIVE_POLICY,
    }
    normalized.setdefault("archive", {})
    normalized["archive"] = {
        "policy": ARCHIVE_POLICY,
        "created_at": (normalized.get("archive") or {}).get("created_at") or normalized.get("captured_at"),
        "updated_at": (normalized.get("archive") or {}).get("updated_at") or normalized.get("graded_at") or normalized.get("captured_at"),
    }
    normalized["locked"] = normalized.get("status") == "final"
    return normalized


def seed_archive_from_history(
    league: str,
    history: list[dict[str, Any]] | None,
    archive_root: str | Path,
) -> dict[str, int]:
    """Materialize existing embedded/git-recovered history into the durable archive."""

    seeded_pending = 0
    seeded_final = 0
    for row in history or []:
        if not isinstance(row, dict):
            continue
        record = _normalize_seed_record(league, row)
        if not record:
            continue
        directory = _fixture_dir(archive_root, league, record["fixture_id"])
        final_path = directory / "final.json"
        pregame_path = directory / "pregame.json"

        if record.get("status") == "final":
            if final_path.exists():
                continue
            record["locked"] = True
            _write_json_atomic(final_path, record)
            if not pregame_path.exists():
                pending = dict(record)
                pending.pop("actual", None)
                pending.pop("graded_at", None)
                pending.pop("scores", None)
                pending["status"] = "pending"
                pending["locked"] = False
                _write_json_atomic(pregame_path, pending)
            seeded_final += 1
        else:
            if final_path.exists():
                continue
            existing = _read_json(pregame_path)
            merged = _merge_pregame(existing, record)
            if existing != merged:
                _write_json_atomic(pregame_path, merged)
                seeded_pending += 1
    return {"seeded_pending": seeded_pending, "seeded_final": seeded_final}


def finalize_snapshot(
    league: str,
    snapshot: dict[str, Any],
    archive_root: str | Path,
    finalized_at: str | None = None,
) -> dict[str, int]:
    """Lock completed matches against their archived pregame probabilities."""

    finalized_at = finalized_at or utc_now_iso()
    finalized = 0
    missing_pregame = 0
    already_final = 0

    for fixture in snapshot.get("fixtures") or []:
        if not isinstance(fixture, dict) or _actual_outcome(fixture) is None:
            continue
        fixture_id = str(fixture.get("id") or "")
        if not fixture_id:
            continue
        directory = _fixture_dir(archive_root, league, fixture_id)
        final_path = directory / "final.json"
        pregame_path = directory / "pregame.json"
        if final_path.exists():
            already_final += 1
            continue
        pregame = _read_json(pregame_path)
        if not pregame:
            missing_pregame += 1
            continue
        final = _grade_record(pregame, fixture, finalized_at)
        if final.get("status") != "final":
            missing_pregame += 1
            continue
        final["league"] = league
        final["schema_version"] = ARCHIVE_SCHEMA_VERSION
        final["locked"] = True
        archive = dict(final.get("archive") or {})
        archive.setdefault("policy", ARCHIVE_POLICY)
        archive["finalized_at"] = finalized_at
        archive["locked"] = True
        final["archive"] = archive
        _write_json_atomic(final_path, final)
        finalized += 1

    return {
        "finalized": finalized,
        "missing_pregame": missing_pregame,
        "already_final": already_final,
    }


def load_archive_history(league: str, archive_root: str | Path) -> list[dict[str, Any]]:
    league_root = Path(archive_root) / league
    if not league_root.exists():
        return []
    rows: list[dict[str, Any]] = []
    for directory in sorted(path for path in league_root.iterdir() if path.is_dir()):
        final = _read_json(directory / "final.json")
        pregame = _read_json(directory / "pregame.json")
        record = final or pregame
        if record and record.get("fixture_id"):
            rows.append(record)
    rows.sort(key=lambda item: (str(item.get("date") or ""), str(item.get("fixture_id") or "")))
    return rows


def synchronize_snapshot_with_archive(
    league: str,
    snapshot: dict[str, Any],
    archive_root: str | Path,
    synchronized_at: str | None = None,
) -> dict[str, Any]:
    """Make the durable archive canonical for postgame analysis and accuracy output."""

    synchronized_at = synchronized_at or utc_now_iso()
    seed_stats = seed_archive_from_history(
        league,
        snapshot.get("prediction_history") if isinstance(snapshot, dict) else None,
        archive_root,
    )
    capture_stats = archive_snapshot(league, snapshot, archive_root)
    final_stats = finalize_snapshot(league, snapshot, archive_root, synchronized_at)
    history = load_archive_history(league, archive_root)

    fixtures = snapshot.get("fixtures") or []
    attach_postgame_analysis(fixtures, history)
    accuracy = build_accuracy_summary(history)
    snapshot["prediction_history"] = history
    snapshot["accuracy"] = accuracy
    meta = snapshot.setdefault("meta", {})
    meta["prediction_archive"] = {
        "schema_version": ARCHIVE_SCHEMA_VERSION,
        "policy": ARCHIVE_POLICY,
        "synchronized_at": synchronized_at,
        "records": len(history),
        "finalized_records": sum(1 for row in history if row.get("status") == "final"),
        "pending_records": sum(1 for row in history if row.get("status") != "final"),
        "seed_stats": seed_stats,
        "capture_stats": capture_stats,
        "finalize_stats": final_stats,
    }
    return meta["prediction_archive"]
