from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from . import api_football, asa, polymarket
from .api_football import FINAL_STATUSES
from .identity import team_catalog
from .utils import read_json, utc_now_iso, write_json

EPL_EXPECTED_FIXTURES = 380
EPL_EXPECTED_TEAM_MATCHES = 38
MLS_EXPECTED_FIXTURES = 510
MLS_EXPECTED_TEAM_MATCHES = 34
INTERRUPTED_STATUSES = {"PST", "CANC", "ABD", "SUSP"}


def _coerce_tabular(frame: Any) -> pd.DataFrame:
    """Normalize pandas/polars/list-like tabular results to pandas."""
    if frame is None:
        return pd.DataFrame()
    if isinstance(frame, pd.DataFrame):
        return frame
    collect = getattr(frame, "collect", None)
    if callable(collect) and not hasattr(frame, "to_dicts"):
        return _coerce_tabular(collect())
    to_dicts = getattr(frame, "to_dicts", None)
    if callable(to_dicts):
        return pd.DataFrame(to_dicts())
    if isinstance(frame, list):
        return pd.DataFrame(frame)
    to_pandas = getattr(frame, "to_pandas", None)
    if callable(to_pandas):
        converted = to_pandas()
        if isinstance(converted, pd.DataFrame):
            return converted
    to_dict = getattr(frame, "to_dict", None)
    if callable(to_dict):
        try:
            converted = to_dict(as_series=False)
        except TypeError:
            converted = to_dict()
        if isinstance(converted, dict):
            return pd.DataFrame(converted)
    raise TypeError(f"Unsupported tabular result from data source: {type(frame).__name__}")


class _PandasSoccerClient:
    """Adapter for itscalledsoccer versions that return polars DataFrames."""

    def __init__(self, client: Any):
        self._client = client

    def get_games(self, *args: Any, **kwargs: Any) -> pd.DataFrame:
        return _coerce_tabular(self._client.get_games(*args, **kwargs))

    def get_teams(self, *args: Any, **kwargs: Any) -> pd.DataFrame:
        return _coerce_tabular(self._client.get_teams(*args, **kwargs))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


def fetch_mls_rows_compatible(
    seasons: tuple[int, ...],
    refresh: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch ASA rows across pandas- and polars-returning client versions.

    If a live refresh fails, retry from the committed ASA cache instead of
    silently discarding historical seasons.
    """
    original_client_factory = asa._client
    real_client = original_client_factory()
    asa._client = lambda: _PandasSoccerClient(real_client)
    try:
        try:
            rows, metadata = asa.fetch_mls_rows(seasons, refresh=refresh)
        except Exception as exc:
            if not refresh:
                raise
            rows = []
            metadata = {
                "source": "American Soccer Analysis",
                "purpose": "MLS fixtures and results",
                "seasons_requested": list(seasons),
                "fixtures_received": 0,
                "errors": [f"live compatibility fetch: {exc}"],
                "updated_at": utc_now_iso(),
            }

        received_seasons = {int(row["season"]) for row in rows if row.get("season") is not None}
        missing = [season for season in seasons if season not in received_seasons]
        if refresh and (metadata.get("errors") or missing):
            cached_rows, cached_meta = asa.fetch_mls_rows(seasons, refresh=False)
            by_id = {str(row.get("fixture_id")): row for row in rows}
            for row in cached_rows:
                by_id.setdefault(str(row.get("fixture_id")), row)
            rows = sorted(
                by_id.values(),
                key=lambda row: (row.get("timestamp") or 0, str(row.get("fixture_id"))),
            )
            metadata = {
                **metadata,
                "fixtures_received": len(rows),
                "cache_fallback": True,
                "cache_fallback_fixtures": len(cached_rows),
                "cache_fallback_reason": (
                    "live ASA refresh was incomplete or incompatible; committed cache retained"
                ),
                "cached_source_errors": cached_meta.get("errors") or [],
            }
        else:
            metadata = {**metadata, "cache_fallback": False}
        return rows, metadata
    finally:
        asa._client = original_client_factory


def fetch_history_rows_resilient(
    client: Any,
    cfg: Any,
    refresh: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Use committed API-Football history when a refresh is unavailable."""
    rows, metadata = api_football.fetch_history_rows(client, cfg, refresh=refresh)
    by_id = {str(row.get("fixture_id")): row for row in rows}
    seasons_present = {int(row["season"]) for row in rows if row.get("season") is not None}
    fallback_seasons: list[int] = []

    if refresh:
        for season in cfg.api_history_seasons:
            if season in seasons_present:
                continue
            path = api_football._cache_path(cfg.key, f"fixtures_{season}")
            cached = read_json(path, default=None)
            if not cached:
                continue
            cached_rows = api_football.fixture_rows([cached])
            if not cached_rows:
                continue
            fallback_seasons.append(season)
            for row in cached_rows:
                by_id.setdefault(str(row.get("fixture_id")), row)

    combined = sorted(
        by_id.values(),
        key=lambda row: (row.get("timestamp") or 0, str(row.get("fixture_id"))),
    )
    metadata = {
        **metadata,
        "fixtures_received": len(combined),
        "cache_fallback_seasons": fallback_seasons,
        "cache_fallback": bool(fallback_seasons),
    }
    return combined, metadata


def _valid_epl_spine(
    rows: list[dict[str, Any]],
    current_ids: list[int],
) -> tuple[bool, dict[int, int]]:
    appearances = {team_id: 0 for team_id in current_ids}
    pairs: set[tuple[int, int]] = set()
    duplicate_pair = False

    for row in rows:
        home_id = row.get("home_id")
        away_id = row.get("away_id")
        if home_id in appearances:
            appearances[home_id] += 1
        if away_id in appearances:
            appearances[away_id] += 1
        pair = (home_id, away_id)
        if pair in pairs:
            duplicate_pair = True
        pairs.add(pair)

    valid = (
        len(rows) == EPL_EXPECTED_FIXTURES
        and len(pairs) == EPL_EXPECTED_FIXTURES
        and not duplicate_pair
        and all(count == EPL_EXPECTED_TEAM_MATCHES for count in appearances.values())
    )
    return valid, appearances


def reconcile_epl_fixture_rows(
    cfg: Any,
    fixture_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep one 380-match EPL schedule spine and overlay results by team pair.

    A directed home/away pairing is unique in an EPL season, so rescheduled
    fixtures are the same match even when two sources publish different dates.
    """
    teams = team_catalog(cfg)
    current_ids = [int(team["api_id"]) for team in teams]
    current_set = set(current_ids)
    season = int(cfg.current_season)

    historical = [row for row in fixture_rows if int(row.get("season", -1)) != season]
    current = [
        row
        for row in fixture_rows
        if int(row.get("season", -1)) == season
        and row.get("home_id") in current_set
        and row.get("away_id") in current_set
    ]

    source_order = (
        "FixtureDownload",
        "PublishedSnapshotFallback",
        "OpenFootball",
    )
    spine: list[dict[str, Any]] | None = None
    source_counts: dict[str, int] = {}

    for source in source_order:
        candidates_by_pair: dict[tuple[int, int], dict[str, Any]] = {}
        for row in current:
            if str(row.get("source")) != source:
                continue
            pair = (int(row["home_id"]), int(row["away_id"]))
            existing = candidates_by_pair.get(pair)
            if existing is None or int(row.get("timestamp") or 0) > int(existing.get("timestamp") or 0):
                candidates_by_pair[pair] = row
        candidates = sorted(
            candidates_by_pair.values(),
            key=lambda row: (row.get("timestamp") or 0, str(row.get("fixture_id"))),
        )
        source_counts[source] = len(candidates)
        valid, _ = _valid_epl_spine(candidates, current_ids)
        if valid:
            spine = candidates
            break

    if spine is None:
        schedule_rank = {
            "FixtureDownload": 0,
            "ESPN": 1,
            "PublishedSnapshotFallback": 2,
            "OpenFootball": 3,
        }
        best_by_pair: dict[tuple[int, int], dict[str, Any]] = {}
        for row in current:
            pair = (int(row["home_id"]), int(row["away_id"]))
            previous = best_by_pair.get(pair)
            if previous is None:
                best_by_pair[pair] = row
                continue
            previous_rank = schedule_rank.get(str(previous.get("source")), 9)
            current_rank = schedule_rank.get(str(row.get("source")), 9)
            if current_rank < previous_rank:
                best_by_pair[pair] = row
        candidates = sorted(
            best_by_pair.values(),
            key=lambda row: (row.get("timestamp") or 0, str(row.get("fixture_id"))),
        )
        valid, appearances = _valid_epl_spine(candidates, current_ids)
        if not valid:
            bad = {team_id: count for team_id, count in appearances.items() if count != 38}
            sample = ", ".join(f"{team_id}:{count}" for team_id, count in list(bad.items())[:8])
            raise ValueError(
                "EPL schedule validation failed: no complete 380-match schedule spine "
                f"was available. Source counts: {source_counts}. Unique directed pairs: "
                f"{len(best_by_pair)}. Bad club counts: {sample or 'none'}."
            )
        spine = candidates

    final_rank = {
        "ESPN": 0,
        "Football-Data": 1,
        "Football-Data.co.uk": 1,
        "FixtureDownload": 2,
        "OpenFootball": 3,
        "API-Football": 4,
        "PublishedSnapshotFallback": 5,
    }
    final_by_pair: dict[tuple[int, int], list[dict[str, Any]]] = {}
    interrupted_by_pair: dict[tuple[int, int], list[dict[str, Any]]] = {}

    for row in current:
        pair = (int(row["home_id"]), int(row["away_id"]))
        status = str(row.get("status") or "").upper()
        if (
            status in FINAL_STATUSES
            and row.get("home_goals") is not None
            and row.get("away_goals") is not None
        ):
            final_by_pair.setdefault(pair, []).append(row)
        elif status in INTERRUPTED_STATUSES:
            interrupted_by_pair.setdefault(pair, []).append(row)

    merged: list[dict[str, Any]] = []
    for scheduled in spine:
        pair = (int(scheduled["home_id"]), int(scheduled["away_id"]))
        finals = final_by_pair.get(pair, [])
        if finals:
            result = min(
                finals,
                key=lambda row: (
                    final_rank.get(str(row.get("source")), 9),
                    -int(row.get("timestamp") or 0),
                ),
            )
            merged.append(
                {
                    **scheduled,
                    "date": result.get("date") or scheduled.get("date"),
                    "timestamp": result.get("timestamp") or scheduled.get("timestamp"),
                    "status": result.get("status"),
                    "status_long": result.get("status_long"),
                    "home_goals": result.get("home_goals"),
                    "away_goals": result.get("away_goals"),
                    "penalty_home": result.get("penalty_home"),
                    "penalty_away": result.get("penalty_away"),
                    "venue_id": result.get("venue_id") or scheduled.get("venue_id"),
                    "venue_name": result.get("venue_name") or scheduled.get("venue_name"),
                    "schedule_source": scheduled.get("source"),
                    "result_source": result.get("source"),
                }
            )
            continue

        interrupted = interrupted_by_pair.get(pair, [])
        if interrupted:
            status_row = min(
                interrupted,
                key=lambda row: final_rank.get(str(row.get("source")), 9),
            )
            merged.append(
                {
                    **scheduled,
                    "status": str(status_row.get("status") or "").upper(),
                    "status_long": status_row.get("status_long") or "Interrupted",
                    "schedule_source": scheduled.get("source"),
                    "status_source": status_row.get("source"),
                }
            )
        else:
            merged.append({**scheduled, "schedule_source": scheduled.get("source")})

    valid, appearances = _valid_epl_spine(merged, current_ids)
    if not valid:
        bad = {team_id: count for team_id, count in appearances.items() if count != 38}
        raise ValueError(
            "EPL schedule validation failed after reconciliation: "
            f"{len(merged)} fixtures; bad club counts={bad}"
        )

    return sorted(
        historical + merged,
        key=lambda row: (row.get("timestamp") or 0, str(row.get("fixture_id"))),
    )


def validate_prepared_schedule(prepared: Any, key: str) -> None:
    rows = prepared.current_fixtures.to_dict("records")
    if key == "epl":
        expected_fixtures = EPL_EXPECTED_FIXTURES
        expected_team_matches = EPL_EXPECTED_TEAM_MATCHES
    elif key == "mls":
        expected_fixtures = MLS_EXPECTED_FIXTURES
        expected_team_matches = MLS_EXPECTED_TEAM_MATCHES
    else:
        return

    if len(rows) != expected_fixtures:
        raise RuntimeError(
            f"{key}: reconciled current schedule has {len(rows)} fixtures; "
            f"expected {expected_fixtures}"
        )

    counts: dict[int, int] = {}
    directed_pairs: set[tuple[int, int]] = set()
    for row in rows:
        home_id = int(row["home_id"])
        away_id = int(row["away_id"])
        counts[home_id] = counts.get(home_id, 0) + 1
        counts[away_id] = counts.get(away_id, 0) + 1
        pair = (home_id, away_id)
        if key == "epl" and pair in directed_pairs:
            raise RuntimeError(
                f"epl: duplicate directed fixture remains after reconciliation: {pair}"
            )
        directed_pairs.add(pair)

    bad = {team_id: count for team_id, count in counts.items() if count != expected_team_matches}
    if bad:
        raise RuntimeError(
            f"{key}: current schedule has invalid per-club match counts: {bad}"
        )


class _PolymarketRequestsProxy:
    """Remove a now-invalid Gamma parameter combination.

    Gamma accepts tag_slug by itself. related_tags is only valid with tag_id,
    so sending related_tags together with tag_slug can produce HTTP 422.
    """

    def __init__(self, requests_module: Any):
        self._requests = requests_module

    def get(self, url: str, *args: Any, **kwargs: Any) -> Any:
        params = kwargs.get("params")
        if (
            url.rstrip("/").endswith("/events")
            and isinstance(params, dict)
            and params.get("tag_slug")
            and "related_tags" in params
            and not params.get("tag_id")
        ):
            params = dict(params)
            params.pop("related_tags", None)
            kwargs["params"] = params
        return self._requests.get(url, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._requests, name)


@contextmanager
def _polymarket_compatible_requests():
    original = polymarket.requests
    polymarket.requests = _PolymarketRequestsProxy(original)
    try:
        yield
    finally:
        polymarket.requests = original


def fetch_polymarket_match_quotes_compatible(*args: Any, **kwargs: Any):
    with _polymarket_compatible_requests():
        quotes, metadata = polymarket.fetch_match_quotes(*args, **kwargs)
    metadata = {
        **metadata,
        "event_discovery_compatibility": (
            "related_tags omitted when filtering by tag_slug; Gamma requires tag_id "
            "for related_tags"
        ),
    }
    return quotes, metadata


def fetch_polymarket_total_goals_quotes_compatible(*args: Any, **kwargs: Any):
    with _polymarket_compatible_requests():
        quotes, metadata = polymarket.fetch_total_goals_quotes(*args, **kwargs)
    metadata = {
        **metadata,
        "event_discovery_compatibility": (
            "related_tags omitted when filtering by tag_slug; Gamma requires tag_id "
            "for related_tags"
        ),
    }
    return quotes, metadata


def _kalshi_quote_is_reliable(quote: Any) -> tuple[bool, str | None]:
    raw = {
        "home": float(getattr(quote, "home_raw_probability")),
        "draw": float(getattr(quote, "draw_raw_probability")),
        "away": float(getattr(quote, "away_raw_probability")),
    }
    methods = dict(getattr(quote, "estimate_methods", {}) or {})

    if any(not (0.0 <= value <= 1.0) for value in raw.values()):
        return False, "raw probability outside [0,1]"
    if not (0.50 <= sum(raw.values()) <= 1.50):
        return False, "three-way raw probability total outside accepted range"

    # A 0% or 100% last-trade fallback commonly means that outcome has never
    # traded, not that the event is certain. Do not normalize those placeholders
    # into a misleading 100/0/0 comparison.
    for outcome, value in raw.items():
        if methods.get(outcome) == "last_trade" and value in {0.0, 1.0}:
            return False, f"{outcome} uses boundary last-trade price"

    normalized = [
        float(getattr(quote, "home_probability")),
        float(getattr(quote, "draw_probability")),
        float(getattr(quote, "away_probability")),
    ]
    if any(not (0.0 <= value <= 1.0) for value in normalized):
        return False, "normalized probability outside [0,1]"
    if abs(sum(normalized) - 1.0) > 1e-6:
        return False, "normalized probabilities do not sum to one"
    return True, None


def sanitize_kalshi_match_quotes(
    quotes: dict[str, Any],
    metadata: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    kept: dict[str, Any] = {}
    rejected: list[dict[str, Any]] = []
    for fixture_id, quote in quotes.items():
        valid, reason = _kalshi_quote_is_reliable(quote)
        if valid:
            kept[fixture_id] = quote
        else:
            rejected.append({"fixture_id": str(fixture_id), "reason": reason})

    updated_meta = {
        **metadata,
        "quotes_found": len(kept),
        "rejected_unreliable_triplets": len(rejected),
        "rejected_unreliable_examples": rejected[:10],
    }
    checked = int(metadata.get("fixtures_checked") or 0)
    if checked:
        updated_meta["coverage"] = len(kept) / checked

    sanity = dict(metadata.get("sanity_checks") or {})
    if sanity:
        kept_ids = {str(fixture_id) for fixture_id in kept}
        divergences = [
            row
            for row in (sanity.get("large_divergences") or [])
            if str(row.get("fixture_id")) in kept_ids
        ]
        sanity["large_divergences"] = divergences
        sanity["status"] = "warning" if divergences else "passed"
        updated_meta["sanity_checks"] = sanity

    return kept, updated_meta


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) == 10:
        text = f"{text}T12:00:00+00:00"
    elif text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _history_fixture_match(
    row: dict[str, Any],
    fixtures: list[dict[str, Any]],
) -> dict[str, Any] | None:
    candidates = [
        fixture
        for fixture in fixtures
        if fixture.get("home") == row.get("home")
        and fixture.get("away") == row.get("away")
    ]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    target = _parse_dt(row.get("kickoff") or row.get("date"))
    if target is None:
        return None

    scored = []
    for fixture in candidates:
        when = _parse_dt(fixture.get("kickoff") or fixture.get("date"))
        if when is None:
            continue
        scored.append((abs((when - target).total_seconds()), fixture))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0])
    return scored[0][1] if scored[0][0] <= 7 * 86400 else None


def repair_published_snapshot(path: Path) -> dict[str, int]:
    """Relink archived history and expose frozen pregame probabilities for finals."""
    data = read_json(path, default={}) or {}
    fixtures = data.get("fixtures") or []
    history = data.get("prediction_history") or []
    fixture_by_id = {str(fixture.get("id")): fixture for fixture in fixtures}

    relinked = 0
    unresolved = 0
    for row in history:
        if row.get("status") != "final":
            continue
        current_id = str(row.get("fixture_id") or "")
        if current_id in fixture_by_id:
            continue
        match = _history_fixture_match(row, fixtures)
        if match is None:
            unresolved += 1
            continue
        row["fixture_id"] = str(match.get("id"))
        relinked += 1

    history_by_id = {
        str(row.get("fixture_id")): row
        for row in history
        if row.get("status") == "final"
    }
    frozen = 0
    for fixture in fixtures:
        if fixture.get("status") != "final":
            continue
        archived = history_by_id.get(str(fixture.get("id")))
        model = (archived or {}).get("sources", {}).get("model")
        if not isinstance(model, dict):
            continue
        if not all(model.get(key) is not None for key in ("home", "draw", "away")):
            continue
        captured = _parse_dt((archived or {}).get("captured_at"))
        kickoff = _parse_dt(fixture.get("kickoff") or fixture.get("date"))
        if captured is not None and kickoff is not None and captured >= kickoff:
            continue
        probabilities = dict(fixture.get("probabilities") or {})
        probabilities.update(
            {
                "home": float(model["home"]),
                "draw": float(model["draw"]),
                "away": float(model["away"]),
                "source": "frozen_pregame",
            }
        )
        fixture["probabilities"] = probabilities
        frozen += 1

    integrity = data.setdefault("meta", {}).setdefault("integrity", {})
    integrity.update(
        {
            "history_links_repaired": relinked,
            "unresolved_final_history_links": unresolved,
            "final_fixtures_using_frozen_pregame_probabilities": frozen,
            "checked_at": utc_now_iso(),
        }
    )
    write_json(path, data)
    return {
        "history_links_repaired": relinked,
        "unresolved_final_history_links": unresolved,
        "final_fixtures_using_frozen_pregame_probabilities": frozen,
    }
