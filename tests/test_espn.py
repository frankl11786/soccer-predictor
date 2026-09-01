import unittest
from unittest.mock import Mock, patch

import requests

from predictor.espn import _fetch_events, _parse_event


SAMPLE = {
    "id": "401999999",
    "date": "2026-08-01T23:30Z",
    "season": {"year": 2026},
    "status": {
        "type": {
            "name": "STATUS_SCHEDULED",
            "description": "Scheduled",
            "completed": False,
        }
    },
    "competitions": [
        {
            "competitors": [
                {
                    "homeAway": "home",
                    "score": "0",
                    "team": {"displayName": "Atlanta United FC"},
                },
                {
                    "homeAway": "away",
                    "score": "0",
                    "team": {"displayName": "Inter Miami CF"},
                },
            ],
            "venue": {"id": "123", "fullName": "Example Stadium"},
        }
    ],
}


class EspnSourceTests(unittest.TestCase):
    def test_scheduled_event_parser(self):
        row = _parse_event(SAMPLE, 2026)
        self.assertIsNotNone(row)
        self.assertEqual(row["season"], 2026)
        self.assertEqual(row["home_name"], "Atlanta United FC")
        self.assertEqual(row["away_name"], "Inter Miami CF")
        self.assertEqual(row["status"], "NS")
        self.assertEqual(row["round"], "Regular Season")

    def test_final_event_parser(self):
        sample = dict(SAMPLE)
        sample["status"] = {
            "type": {
                "name": "STATUS_FINAL",
                "description": "Final",
                "completed": True,
            }
        }
        sample["competitions"] = [
            {
                "competitors": [
                    {
                        "homeAway": "home",
                        "score": "2",
                        "team": {"displayName": "Atlanta United FC"},
                    },
                    {
                        "homeAway": "away",
                        "score": "1",
                        "team": {"displayName": "Inter Miami CF"},
                    },
                ]
            }
        ]
        row = _parse_event(sample, 2026)
        self.assertEqual(row["status"], "FT")
        self.assertEqual(row["home_goals"], 2)
        self.assertEqual(row["away_goals"], 1)

    def test_all_espn_requests_returning_403_is_a_total_live_failure(self):
        response = Mock()
        response.raise_for_status.side_effect = requests.HTTPError("403 Client Error")
        session = Mock()
        session.get.return_value = response
        with patch("predictor.espn.requests.Session", return_value=session):
            events, errors, successful_requests = _fetch_events("epl", 2026)

        self.assertEqual(events, [])
        self.assertEqual(successful_requests, 0)
        self.assertEqual(len(errors), 13)
        self.assertEqual(session.get.call_count, 13)
        self.assertTrue(all("403 Client Error" in error for error in errors))

    def test_successful_empty_responses_are_not_a_live_request_failure(self):
        response = Mock()
        response.json.return_value = {"events": []}
        session = Mock()
        session.get.return_value = response
        with patch("predictor.espn.requests.Session", return_value=session):
            events, errors, successful_requests = _fetch_events("epl", 2026)

        self.assertEqual(events, [])
        self.assertEqual(errors, [])
        self.assertEqual(successful_requests, 13)


if __name__ == "__main__":
    unittest.main()
