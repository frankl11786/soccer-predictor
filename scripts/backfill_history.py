from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from predictor.archive import seed_archive_from_history, synchronize_snapshot_with_archive
from predictor.history import recover_prediction_history_from_git, update_prediction_history


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def backfill_league(league: str, archive_root: Path) -> dict[str, int | str]:
    path = ROOT / "app" / "data" / f"{league}.json"
    data = json.loads(path.read_text())
    fixtures = data.get("fixtures") or []
    previous = data.get("prediction_history") or []
    now = utc_now_iso()

    recovered = recover_prediction_history_from_git(
        league,
        fixtures,
        existing_history=previous,
        repo_root=ROOT,
        recovered_at=now,
    )
    history = update_prediction_history(
        recovered,
        fixtures,
        now,
        (data.get("meta") or {}).get("model_version"),
    )

    seed_stats = seed_archive_from_history(league, history, archive_root)
    archive_stats = synchronize_snapshot_with_archive(league, data, archive_root, now)

    data.setdefault("meta", {})["history_backfill_at"] = now
    data["meta"]["history_backfill_method"] = (
        "archived committed pregame snapshots materialized into the permanent prediction archive"
    )
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    return {
        "league": league,
        "history_rows": len(data.get("prediction_history") or []),
        "graded_matches": int((data.get("accuracy") or {}).get("graded_matches") or 0),
        "recovered_matches": int((data.get("accuracy") or {}).get("recovered_matches") or 0),
        "seeded_pending": int(seed_stats.get("seeded_pending") or 0),
        "seeded_final": int(seed_stats.get("seeded_final") or 0),
        "archive_records": int(archive_stats.get("records") or 0),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Recover and permanently archive pregame forecast history from git.")
    parser.add_argument("--league", choices=("epl", "mls", "both"), default="both")
    parser.add_argument("--archive-root", default=str(ROOT / "history"))
    args = parser.parse_args()
    leagues = ("epl", "mls") if args.league == "both" else (args.league,)
    for league in leagues:
        result = backfill_league(league, Path(args.archive_root))
        print(
            f"{league}: {result['history_rows']} history rows; "
            f"{result['graded_matches']} graded; {result['recovered_matches']} recovered from git; "
            f"{result['archive_records']} archive records"
        )


if __name__ == "__main__":
    main()
