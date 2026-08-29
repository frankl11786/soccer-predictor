import json
import tempfile
import unittest
from pathlib import Path

from predictor.archive import (
    archive_snapshot,
    finalize_snapshot,
    load_archive_history,
    seed_archive_from_history,
    synchronize_snapshot_with_archive,
)
from predictor.history import update_prediction_history


class PredictionArchiveTests(unittest.TestCase):
    def scheduled_fixture(self):
        return {
            "id": "fd-mls-2026-272",
            "date": "2026-08-20",
            "kickoff": "2026-08-20T23:30:00Z",
            "round": 25,
            "home": "atlanta-united",
            "away": "red-bull-new-york",
            "status": "scheduled",
            "probabilities": {"home": 0.34, "draw": 0.26, "away": 0.40},
            "polymarket": {
                "probabilities": {"home": 0.49, "draw": 0.23, "away": 0.28},
                "event_slug": "atlanta-new-york",
            },
            "kalshi": {
                "probabilities": {"home": 0.50, "draw": 0.23, "away": 0.27},
                "event_ticker": "KXMLSGAME-26AUG20ATLNYRB",
            },
            "market_consensus": {"probabilities": {"home": 0.495, "draw": 0.23, "away": 0.275}},
            "goal_totals": {
                "model": {
                    "over": {"2.5": 0.62},
                    "under": {"2.5": 0.38},
                    "exact": {"0": 0.05, "1": 0.14, "2": 0.19, "3": 0.20, "4": 0.16, "5": 0.11, "6+": 0.15},
                },
                "polymarket": {
                    "over": {"2.5": 0.58},
                    "under": {"2.5": 0.42},
                    "lines": {"2.5": {"over": 0.58, "under": 0.42}},
                },
                "kalshi": {
                    "over": {"2.5": 0.57},
                    "under": {"2.5": 0.43},
                    "lines": {"2.5": {"over": 0.57, "under": 0.43}},
                },
                "consensus": {
                    "over": {"2.5": 0.575},
                    "under": {"2.5": 0.425},
                    "model_edge": {"2.5": 0.045},
                },
            },
        }

    def snapshot(self, generated_at, fixture=None):
        return {
            "meta": {"generated_at": generated_at, "model_version": "test-v1"},
            "fixtures": [fixture or self.scheduled_fixture()],
            "forecast": [{"team": "atlanta-united"}],
        }

    def test_sources_are_updated_independently_before_kickoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive_snapshot("mls", self.snapshot("2026-08-18T04:00:00Z"), root)

            later = self.scheduled_fixture()
            later["probabilities"] = {"home": 0.36, "draw": 0.25, "away": 0.39}
            later["polymarket"] = {
                "probabilities": {"home": 0.48, "draw": 0.24, "away": 0.28},
                "event_slug": "atlanta-new-york",
            }
            later.pop("kalshi")
            later.pop("market_consensus")
            later["goal_totals"].pop("kalshi")
            later["goal_totals"].pop("consensus")
            archive_snapshot("mls", self.snapshot("2026-08-20T18:00:00Z", later), root)

            history = load_archive_history("mls", root)
            self.assertEqual(len(history), 1)
            row = history[0]
            self.assertAlmostEqual(row["sources"]["model"]["home"], 0.36)
            self.assertAlmostEqual(row["sources"]["polymarket"]["home"], 0.48)
            # The later temporary Kalshi miss must not erase the earlier genuine quote.
            self.assertAlmostEqual(row["sources"]["kalshi"]["home"], 0.50)
            self.assertEqual(row["source_captured_at"]["kalshi"], "2026-08-18T04:00:00Z")
            self.assertEqual(row["source_captured_at"]["model"], "2026-08-20T18:00:00Z")
            self.assertAlmostEqual(row["goal_totals"]["kalshi"]["over"]["2.5"], 0.57)
            self.assertEqual(row["goal_total_source_captured_at"]["kalshi"], "2026-08-18T04:00:00Z")

    def test_snapshot_generated_after_kickoff_is_never_archived(self):
        with tempfile.TemporaryDirectory() as tmp:
            stats = archive_snapshot("mls", self.snapshot("2026-08-21T04:00:00Z"), Path(tmp))
            self.assertEqual(stats["captured"], 0)
            self.assertEqual(load_archive_history("mls", Path(tmp)), [])

    def test_final_record_is_immutable_after_first_finalization(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive_snapshot("mls", self.snapshot("2026-08-20T18:00:00Z"), root)

            final_fixture = self.scheduled_fixture()
            final_fixture.update({"status": "final", "home_score": 1, "away_score": 2})
            final_snapshot = self.snapshot("2026-08-21T04:00:00Z", final_fixture)
            stats = finalize_snapshot("mls", final_snapshot, root, "2026-08-21T04:05:00Z")
            self.assertEqual(stats["finalized"], 1)

            final_path = root / "mls" / "fd-mls-2026-272" / "final.json"
            original = json.loads(final_path.read_text())
            self.assertEqual(original["actual"]["outcome"], "away")
            self.assertTrue(original["locked"])

            # A later/corrupt final score must not rewrite the frozen graded record.
            changed_fixture = self.scheduled_fixture()
            changed_fixture.update({"status": "final", "home_score": 5, "away_score": 0})
            finalize_snapshot("mls", self.snapshot("2026-08-22T04:00:00Z", changed_fixture), root)
            after = json.loads(final_path.read_text())
            self.assertEqual(after, original)

    def test_existing_embedded_history_can_seed_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pending = update_prediction_history(
                [], [self.scheduled_fixture()], "2026-08-20T18:00:00Z", "test-v1"
            )
            stats = seed_archive_from_history("mls", pending, root)
            self.assertEqual(stats["seeded_pending"], 1)
            self.assertEqual(len(load_archive_history("mls", root)), 1)

    def test_sync_attaches_postgame_and_accuracy_from_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive_snapshot("mls", self.snapshot("2026-08-20T18:00:00Z"), root)
            final_fixture = self.scheduled_fixture()
            final_fixture.update({"status": "final", "home_score": 1, "away_score": 2})
            data = self.snapshot("2026-08-21T04:00:00Z", final_fixture)
            stats = synchronize_snapshot_with_archive("mls", data, root, "2026-08-21T04:05:00Z")

            self.assertEqual(stats["finalized_records"], 1)
            self.assertEqual(data["accuracy"]["graded_matches"], 1)
            review = data["fixtures"][0]["postgame_analysis"]
            self.assertEqual(review["actual"]["outcome"], "away")
            self.assertIn("polymarket", review["sources"])
            self.assertIn("kalshi", review["sources"])
            self.assertAlmostEqual(review["sources"]["model"]["away"], 0.40)
            self.assertIn("goal_totals", review)
            self.assertIn("totals_scores", review)
            self.assertIn("2.5", review["totals_scores"]["model"])


if __name__ == "__main__":
    unittest.main()
