import json
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
                patch("predictor.espn._fetch_events", return_value=([FINAL_EVENT], [], 12)) as fetch,
            ):
                rows, metadata = fetch_league_rows("epl", 2026, refresh=True)

        fetch.assert_called_once_with("epl", 2026)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "FT")
        self.assertEqual((rows[0]["home_goals"], rows[0]["away_goals"]), (2, 1))
        self.assertEqual(rows[0]["source"], "ESPN")
        self.assertFalse(metadata["cached"])
        self.assertFalse(metadata["live_request_failed"])
        self.assertEqual(metadata["fixtures_received"], 1)

    def test_cached_upstream_results_avoid_network_fetch(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "mls_schedule_2026.json"
            with (
                patch("predictor.espn._cache_path", return_value=cache_path),
                patch("predictor.espn._fetch_events", return_value=([FINAL_EVENT], [], 12)),
            ):
                expected, _ = fetch_league_rows("mls", 2026, refresh=True)
                with patch("predictor.espn._fetch_events") as fetch:
                    actual, metadata = fetch_league_rows("mls", 2026)

        fetch.assert_not_called()
        self.assertEqual(actual, expected)
        self.assertTrue(metadata["cached"])
        self.assertEqual(metadata["fixtures_received"], 1)

    def test_total_403_failure_is_distinguishable_from_empty_response(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "epl_schedule_2026.json"
            errors = [f"request {number}: 403 Client Error" for number in range(13)]
            with (
                patch("predictor.espn._cache_path", return_value=cache_path),
                patch("predictor.espn._fetch_events", return_value=([], errors, 0)),
            ):
                rows, metadata = fetch_league_rows("epl", 2026, refresh=True)

        self.assertEqual(rows, [])
        self.assertTrue(metadata["live_request_failed"])
        self.assertFalse(metadata["cache_fallback"])
        self.assertEqual(metadata["successful_requests"], 0)
        self.assertEqual(metadata["request_errors"], errors)

    def test_refresh_uses_usable_cache_after_espn_failure(self):
        cached = [{
            "fixture_id": "espn-cached",
            "source": "ESPN",
            "season": 2026,
            "status": "FT",
            "home_goals": 2,
            "away_goals": 1,
        }]
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "epl_schedule_2026.json"
            cache_path.write_text(json.dumps(cached), encoding="utf-8")
            with (
                patch("predictor.espn._cache_path", return_value=cache_path),
                patch(
                    "predictor.espn._fetch_events",
                    return_value=([], ["403 Client Error"], 0),
                ),
            ):
                rows, metadata = fetch_league_rows("epl", 2026, refresh=True)

        self.assertEqual(rows, cached)
        self.assertTrue(metadata["live_request_failed"])
        self.assertTrue(metadata["cached"])
        self.assertTrue(metadata["cache_fallback"])


if __name__ == "__main__":
    unittest.main()
