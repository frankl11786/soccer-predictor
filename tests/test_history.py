import unittest

from predictor.history import (
    attach_postgame_analysis,
    build_accuracy_summary,
    recover_prediction_history_from_snapshots,
    update_prediction_history,
)


class PredictionHistoryTests(unittest.TestCase):
    def scheduled_fixture(self):
        return {
            "id": "match-1",
            "date": "2026-08-20",
            "kickoff": "2026-08-20T23:30:00Z",
            "round": 25,
            "home": "atlanta-united",
            "away": "red-bull-new-york",
            "status": "scheduled",
            "probabilities": {"home": 0.34, "draw": 0.26, "away": 0.40},
            "polymarket": {"probabilities": {"home": 0.49, "draw": 0.23, "away": 0.28}},
            "kalshi": {"probabilities": {"home": 0.50, "draw": 0.23, "away": 0.27}},
            "market_consensus": {"probabilities": {"home": 0.495, "draw": 0.23, "away": 0.275}},
        }

    def test_latest_pregame_snapshot_is_captured_and_then_frozen(self):
        pending = update_prediction_history([], [self.scheduled_fixture()], "2026-08-18T04:00:00Z")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["status"], "pending")
        self.assertAlmostEqual(pending[0]["sources"]["model"]["away"], 0.40)
        self.assertAlmostEqual(pending[0]["sources"]["polymarket"]["home"], 0.49)
        self.assertAlmostEqual(pending[0]["sources"]["kalshi"]["home"], 0.50)

        updated = self.scheduled_fixture()
        updated["probabilities"] = {"home": 0.36, "draw": 0.25, "away": 0.39}
        pending = update_prediction_history(pending, [updated], "2026-08-19T04:00:00Z")
        self.assertAlmostEqual(pending[0]["sources"]["model"]["home"], 0.36)
        self.assertEqual(pending[0]["captured_at"], "2026-08-19T04:00:00Z")

        final = {
            **updated,
            "status": "final",
            "home_score": 1,
            "away_score": 2,
            # These post-match probabilities must never replace the frozen row.
            "probabilities": {"home": 0.60, "draw": 0.20, "away": 0.20},
        }
        history = update_prediction_history(pending, [final], "2026-08-21T04:00:00Z")
        record = history[0]
        self.assertEqual(record["status"], "final")
        self.assertEqual(record["actual"]["outcome"], "away")
        self.assertAlmostEqual(record["sources"]["model"]["home"], 0.36)
        self.assertAlmostEqual(record["scores"]["model"]["actual_probability"], 0.39)
        self.assertTrue(record["scores"]["model"]["correct_pick"])
        self.assertFalse(record["scores"]["polymarket"]["correct_pick"])

    def test_final_fixture_without_pregame_snapshot_is_not_backfilled(self):
        final = self.scheduled_fixture()
        final.update({"status": "final", "home_score": 2, "away_score": 0})
        history = update_prediction_history([], [final], "2026-08-21T04:00:00Z")
        self.assertEqual(history, [])

    def test_accuracy_comparisons_use_same_match_subsets(self):
        pending = update_prediction_history([], [self.scheduled_fixture()], "2026-08-18T04:00:00Z")
        final = {**self.scheduled_fixture(), "status": "final", "home_score": 1, "away_score": 2}
        history = update_prediction_history(pending, [final], "2026-08-21T04:00:00Z")
        accuracy = build_accuracy_summary(history)
        self.assertEqual(accuracy["graded_matches"], 1)
        self.assertEqual(accuracy["comparisons"]["model_vs_polymarket"]["matches"], 1)
        self.assertEqual(accuracy["comparisons"]["model_vs_kalshi"]["matches"], 1)
        self.assertEqual(accuracy["comparisons"]["all_three"]["matches"], 1)
        self.assertLess(accuracy["overall"]["model"]["brier"], accuracy["overall"]["polymarket"]["brier"])

    def test_postgame_analysis_is_attached_to_completed_fixture(self):
        pending = update_prediction_history([], [self.scheduled_fixture()], "2026-08-18T04:00:00Z")
        final = {**self.scheduled_fixture(), "status": "final", "home_score": 1, "away_score": 2}
        history = update_prediction_history(pending, [final], "2026-08-21T04:00:00Z")
        fixtures = [dict(final)]
        attach_postgame_analysis(fixtures, history)
        postgame = fixtures[0]["postgame_analysis"]
        self.assertEqual(postgame["actual"]["outcome"], "away")
        self.assertIn("model", postgame["sources"])
        self.assertIn("polymarket", postgame["sources"])
        self.assertIn("kalshi", postgame["sources"])

    def test_archived_pregame_snapshot_backfills_completed_match(self):
        final = {**self.scheduled_fixture(), "status": "final", "home_score": 1, "away_score": 0}
        old_fixture = self.scheduled_fixture()
        snapshot = {
            "commit": "abcdef1234567890",
            "data": {
                "meta": {"generated_at": "2026-08-20T04:00:00Z", "model_version": "historical-v1"},
                "fixtures": [old_fixture],
            },
        }
        history = recover_prediction_history_from_snapshots(
            [snapshot], [final], recovered_at="2026-08-21T04:00:00Z"
        )
        self.assertEqual(len(history), 1)
        row = history[0]
        self.assertEqual(row["status"], "final")
        self.assertEqual(row["actual"]["outcome"], "home")
        self.assertAlmostEqual(row["sources"]["polymarket"]["home"], 0.49)
        self.assertAlmostEqual(row["sources"]["kalshi"]["home"], 0.50)
        self.assertEqual(row["provenance"]["type"], "archived_git_snapshot")
        self.assertEqual(row["provenance"]["commit"], "abcdef123456")

    def test_backfill_never_uses_post_kickoff_snapshot(self):
        final = {**self.scheduled_fixture(), "status": "final", "home_score": 1, "away_score": 0}
        snapshot = {
            "commit": "late",
            "data": {
                "meta": {"generated_at": "2026-08-21T04:00:00Z"},
                "fixtures": [self.scheduled_fixture()],
            },
        }
        history = recover_prediction_history_from_snapshots(
            [snapshot], [final], recovered_at="2026-08-21T05:00:00Z"
        )
        self.assertEqual(history, [])

    def test_backfill_prefers_richer_market_coverage_before_kickoff(self):
        final = {**self.scheduled_fixture(), "status": "final", "home_score": 1, "away_score": 0}
        rich = self.scheduled_fixture()
        thin = self.scheduled_fixture()
        thin.pop("kalshi")
        thin.pop("market_consensus")
        snapshots = [
            {
                "commit": "rich",
                "data": {
                    "meta": {"generated_at": "2026-08-19T04:00:00Z"},
                    "fixtures": [rich],
                },
            },
            {
                "commit": "thin",
                "data": {
                    "meta": {"generated_at": "2026-08-20T20:00:00Z"},
                    "fixtures": [thin],
                },
            },
        ]
        history = recover_prediction_history_from_snapshots(
            snapshots, [final], recovered_at="2026-08-21T04:00:00Z"
        )
        self.assertIn("kalshi", history[0]["sources"])
        self.assertEqual(history[0]["captured_at"], "2026-08-19T04:00:00Z")

    def test_accuracy_summary_reports_recovered_coverage(self):
        final = {**self.scheduled_fixture(), "status": "final", "home_score": 1, "away_score": 0}
        snapshot = {
            "commit": "abc",
            "data": {
                "meta": {"generated_at": "2026-08-20T04:00:00Z"},
                "fixtures": [self.scheduled_fixture()],
            },
        }
        history = recover_prediction_history_from_snapshots(
            [snapshot], [final], recovered_at="2026-08-21T04:00:00Z"
        )
        accuracy = build_accuracy_summary(history)
        self.assertEqual(accuracy["recovered_matches"], 1)
        self.assertEqual(accuracy["coverage_start"], "2026-08-20")
        self.assertEqual(accuracy["coverage_end"], "2026-08-20")


if __name__ == "__main__":
    unittest.main()
