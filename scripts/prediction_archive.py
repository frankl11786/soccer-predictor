from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from predictor.archive import (
    archive_snapshot,
    seed_archive_from_history,
    synchronize_snapshot_with_archive,
)


def load_snapshot(league: str) -> tuple[Path, dict]:
    path = ROOT / "app" / "data" / f"{league}.json"
    return path, json.loads(path.read_text())


def capture(league: str, archive_root: Path) -> None:
    _, data = load_snapshot(league)
    seed = seed_archive_from_history(league, data.get("prediction_history") or [], archive_root)
    stats = archive_snapshot(league, data, archive_root)
    print(
        f"{league}: pre-publish archive captured={stats['captured']} updated={stats['updated']} "
        f"locked={stats['skipped_locked']} seeded_pending={seed['seeded_pending']} seeded_final={seed['seeded_final']}"
    )


def sync(league: str, archive_root: Path) -> None:
    path, data = load_snapshot(league)
    stats = synchronize_snapshot_with_archive(league, data, archive_root)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(
        f"{league}: archive records={stats['records']} final={stats['finalized_records']} "
        f"pending={stats['pending_records']} newly_finalized={stats['finalize_stats']['finalized']}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Maintain immutable pregame prediction archives.")
    parser.add_argument("command", choices=("capture", "sync"))
    parser.add_argument("--league", choices=("epl", "mls", "both"), default="both")
    parser.add_argument("--archive-root", default=str(ROOT / "history"))
    args = parser.parse_args()

    archive_root = Path(args.archive_root)
    leagues = ("epl", "mls") if args.league == "both" else (args.league,)
    for league in leagues:
        if args.command == "capture":
            capture(league, archive_root)
        else:
            sync(league, archive_root)


if __name__ == "__main__":
    main()
