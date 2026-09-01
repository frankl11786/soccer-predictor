import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from predictor.espn import fetch_league_rows


FINAL_EVENT = {
    "id": "401999999",
    "date": "2026-08-29T14:00:00Z",
    "season": {"year": 2026},
    "status": {
        "type": {
            "name": "STATUS_FINAL",
            "description": "Final",
            "completed": True,
        }
    },
    "competitions": [{
        "competitors": [
            {"homeAway": "home", "score": "2", "team": {"displayName": "Liverpool"}},
            {"homeAway": "away", "score": "1", "team": {"displayName": "Nottingham Forest"}},
        ]
    }],
}


class EspnResultsPipelineTests(unittest.TestCase):
    def test_fresh_upstream_final_is_returned_for_requested_league(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "epl_schedule_2026.json"
            with (
                patch("predictor.espn._cache_path", return_value=cache_path),
                patch("predictor.espn._fetch_events", return_value=([FINAL_EVENT], [])) as fetch,
            ):
                rows, metadata = fetch_league_rows("epl", 2026, refresh=True)

        fetch.assert_called_once_with("epl", 2026)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "FT")
        self.assertEqual((rows[0]["home_goals"], rows[0]["away_goals"]), (2, 1))
        self.assertEqual(rows[0]["source"], "ESPN")
        self.assertFalse(metadata["cached"])
        self.assertEqual(metadata["fixtures_received"], 1)

    def test_cached_upstream_results_avoid_network_fetch(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "mls_schedule_2026.json"
            with (
                patch("predictor.espn._cache_path", return_value=cache_path),
                patch("predictor.espn._fetch_events", return_value=([FINAL_EVENT], [])),
            ):
                expected, _ = fetch_league_rows("mls", 2026, refresh=True)
                with patch("predictor.espn._fetch_events") as fetch:
                    actual, metadata = fetch_league_rows("mls", 2026)

        fetch.assert_not_called()
        self.assertEqual(actual, expected)
        self.assertTrue(metadata["cached"])
        self.assertEqual(metadata["fixtures_received"], 1)


if __name__ == "__main__":
    unittest.main()
