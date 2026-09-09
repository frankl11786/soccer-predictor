from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .api_football import FINAL_STATUSES
from .config import OVERRIDE_DIR, LeagueConfig
from .utils import load_csv, normalize_name, utc_now_iso

INTERRUPTED_STATUSES = {"PST", "CANC", "ABD", "SUSP"}
STATUS_LABELS = {
    "PST": "Postponed",
    "CANC": "Cancelled",
    "ABD": "Abandoned",
    "SUSP": "Suspended",
}


def _date_key(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).date().isoformat()
    except ValueError:
        return text[:10]


def _configured_overrides(cfg: LeagueConfig) -> list[dict[str, str]]:
    path = OVERRIDE_DIR / f"fixture_status_{cfg.key}.csv"
    rows = load_csv(path)
    configured: list[dict[str, str]] = []
    for index, row in enumerate(rows, start=2):
        season_text = str(row.get("season") or "").strip()
        try:
            season = int(season_text)
        except ValueError as exc:
            raise ValueError(
                f"{path}: row {index} has invalid season {season_text!r}"
            ) from exc
        if season != cfg.current_season:
            continue

        status = str(row.get("status") or "").strip().upper()
        if status not in INTERRUPTED_STATUSES:
            raise ValueError(
                f"{path}: row {index} has unsupported status {status!r}; "
                f"expected one of {sorted(INTERRUPTED_STATUSES)}"
            )

        date = _date_key(row.get("date"))
        home = str(row.get("home") or "").strip()
        away = str(row.get("away") or "").strip()
        if not date or not home or not away:
            raise ValueError(
                f"{path}: row {index} requires date, home, and away"
            )

        configured.append(
            {
                "season": str(season),
                "date": date,
                "home": home,
                "away": away,
                "status": status,
                "status_long": (
                    str(row.get("status_long") or "").strip()
                    or STATUS_LABELS[status]
                ),
                "source_url": str(row.get("source_url") or "").strip(),
            }
        )
    return configured


def apply_fixture_status_overrides(
    prepared: Any,
    cfg: LeagueConfig,
) -> dict[str, Any]:
    """Apply explicit league-confirmed interruption statuses to current fixtures.

    The match must have the same teams and UTC calendar date as the override.
    This deliberately does not follow a fixture to a later rescheduled date.
    Final results always win over an override.
    """
    overrides = _configured_overrides(cfg)
    frame = prepared.current_fixtures
    applied = 0
    unmatched = 0
    superseded_by_final = 0

    if overrides and "status_source" not in frame.columns:
        frame["status_source"] = None

    for override in overrides:
        home_key = normalize_name(override["home"])
        away_key = normalize_name(override["away"])
        matching_indexes: list[int] = []

        for idx, fixture in frame.iterrows():
            if int(fixture.get("season") or cfg.current_season) != cfg.current_season:
                continue
            if _date_key(fixture.get("date")) != override["date"]:
                continue
            if normalize_name(str(fixture.get("home_name") or "")) != home_key:
                continue
            if normalize_name(str(fixture.get("away_name") or "")) != away_key:
                continue
            matching_indexes.append(int(idx))

        if len(matching_indexes) > 1:
            raise RuntimeError(
                f"{cfg.key}: official fixture-status override matched more than one "
                f"fixture: {override['home']} vs {override['away']} ({override['date']})"
            )

        if not matching_indexes:
            # A source may already have moved the fixture to its rescheduled date.
            # In that case the original-date override should naturally stop applying.
            unmatched += 1
            continue

        idx = matching_indexes[0]
        current_status = str(frame.at[idx, "status"] or "").upper()
        if current_status in FINAL_STATUSES:
            superseded_by_final += 1
            continue

        frame.at[idx, "status"] = override["status"]
        frame.at[idx, "status_long"] = override["status_long"]
        frame.at[idx, "status_source"] = "Official Status Override"
        applied += 1

    metadata = {
        "source": "Official Status Override",
        "purpose": "league-confirmed postponed/cancelled/abandoned/suspended fixtures",
        "league": cfg.key,
        "configured": len(overrides),
        "applied": applied,
        "unmatched": unmatched,
        "superseded_by_final": superseded_by_final,
        "source_urls": sorted(
            {row["source_url"] for row in overrides if row.get("source_url")}
        ),
        "updated_at": utc_now_iso(),
    }
    if overrides:
        print(
            f"[Official Status Override] {cfg.key}: configured={len(overrides)}; "
            f"applied={applied}; unmatched={unmatched}; "
            f"superseded_by_final={superseded_by_final}"
        )
    return metadata
