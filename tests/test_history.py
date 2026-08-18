import unittest

from predictor.history import attach_postgame_analysis, build_accuracy_summary, update_prediction_history


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


if __name__ == "__main__":
    unittest.main()
