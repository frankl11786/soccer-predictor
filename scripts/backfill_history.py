from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from predictor.history import (
    attach_postgame_analysis,
    build_accuracy_summary,
    recover_prediction_history_from_git,
    update_prediction_history,
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def backfill_league(league: str) -> dict[str, int | str]:
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
    attach_postgame_analysis(fixtures, history)
    accuracy = build_accuracy_summary(history)

    data["prediction_history"] = history
    data["accuracy"] = accuracy
    data.setdefault("meta", {})["history_backfill_at"] = now
    data["meta"]["history_backfill_method"] = "archived committed pregame snapshots"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    return {
        "league": league,
        "history_rows": len(history),
        "graded_matches": int(accuracy.get("graded_matches") or 0),
        "recovered_matches": int(accuracy.get("recovered_matches") or 0),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Recover pregame forecast history from committed JSON snapshots.")
    parser.add_argument("--league", choices=("epl", "mls", "both"), default="both")
    args = parser.parse_args()
    leagues = ("epl", "mls") if args.league == "both" else (args.league,)
    for league in leagues:
        result = backfill_league(league)
        print(
            f"{league}: {result['history_rows']} history rows; "
            f"{result['graded_matches']} graded; {result['recovered_matches']} recovered from git"
        )


if __name__ == "__main__":
    main()
