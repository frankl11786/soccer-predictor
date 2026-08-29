import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from predictor.goal_totals import model_goal_totals
from predictor.kalshi import _total_line, fetch_total_goals_quotes as fetch_kalshi_totals
from predictor.polymarket import _extract_total_goals_lines, fetch_total_goals_quotes as fetch_polymarket_totals


class GoalTotalsTests(unittest.TestCase):
    def test_model_goal_totals_is_complete_and_monotonic(self):
        totals = model_goal_totals(1.9, 1.2)
        self.assertAlmostEqual(totals["lambda"], 3.1, places=6)
        self.assertAlmostEqual(sum(totals["exact"].values()), 1.0, delta=0.00001)

        lines = ["0.5", "1.5", "2.5", "3.5", "4.5", "5.5"]
        overs = [totals["over"][line] for line in lines]
        self.assertEqual(overs, sorted(overs, reverse=True))
        for line in lines:
            self.assertAlmostEqual(
                totals["over"][line] + totals["under"][line],
                1.0,
                delta=0.00001,
            )

    def test_kalshi_total_line_uses_descriptive_half_goal(self):
        market = {
            "ticker": "KXMLSTOTAL-26AUG15ATLNYRB-3",
            "yes_sub_title": "Over 2.5 goals scored",
        }
        self.assertEqual(_total_line(market), 2.5)

    def test_kalshi_total_line_ticker_fallback_maps_integer_marker(self):
        market = {"ticker": "KXEPLTOTAL-26AUG29TOTNEW-4"}
        self.assertEqual(_total_line(market), 3.5)

    def test_polymarket_total_lines_are_normalized_and_team_totals_rejected(self):
        event = {
            "markets": [
                {
                    "id": "contest-total",
                    "active": True,
                    "closed": False,
                    "sportsMarketType": "total",
                    "line": 2.5,
                    "question": "Over/Under 2.5 total goals",
                    "outcomes": '["Over", "Under"]',
                    "outcomePrices": '["0.61", "0.41"]',
                    "liquidityNum": 1000,
                    "volumeNum": 5000,
                },
                {
                    "id": "team-total",
                    "active": True,
                    "closed": False,
                    "sportsMarketType": "total",
                    "line": 1.5,
                    "question": "Will Atlanta United score over 1.5 goals?",
                    "outcomes": '["Yes", "No"]',
                    "outcomePrices": '["0.52", "0.48"]',
                    "liquidityNum": 10000,
                    "volumeNum": 20000,
                },
            ]
        }
        home_aliases = ("atlanta united", "atlanta", "atl")
        away_aliases = ("charlotte fc", "charlotte", "clt")
        lines = _extract_total_goals_lines(event, home_aliases, away_aliases)
        self.assertEqual(set(lines), {"2.5"})
        self.assertAlmostEqual(lines["2.5"]["over"], 0.61 / 1.02, places=6)
        self.assertAlmostEqual(lines["2.5"]["under"], 0.41 / 1.02, places=6)
        self.assertTrue(lines["2.5"]["normalized"])

    @patch("predictor.kalshi._upcoming_events")
    def test_kalshi_total_event_matches_fixture_and_returns_lines(self, upcoming):
        upcoming.return_value = (
            [
                {
                    "event_ticker": "KXMLSTOTAL-26AUG30ATLMIA",
                    "series_ticker": "KXMLSTOTAL",
                    "title": "Atlanta vs Miami: Total Goals",
                    "markets": [
                        {
                            "ticker": "KXMLSTOTAL-26AUG30ATLMIA-3",
                            "yes_sub_title": "Over 2.5 goals scored",
                            "status": "open",
                            "yes_bid_dollars": "0.59",
                            "yes_ask_dollars": "0.61",
                            "expected_expiration_time": "2026-08-30T23:30:00Z",
                            "volume_fp": "1200",
                        }
                    ],
                }
            ],
            [],
            {"method": "test"},
        )
        fixtures = [{
            "id": "mls-total-1",
            "status": "scheduled",
            "date": "2026-08-30",
            "kickoff": "2026-08-30T23:30:00Z",
            "home": "atlanta-united",
            "away": "inter-miami-cf",
        }]
        teams = [
            {"slug": "atlanta-united", "name": "Atlanta United", "short": "ATL"},
            {"slug": "inter-miami-cf", "name": "Inter Miami CF", "short": "MIA"},
        ]
        quotes, meta = fetch_kalshi_totals(
            fixtures, teams, "KXMLSTOTAL",
            as_of=datetime(2026, 8, 29, 12, tzinfo=timezone.utc),
        )
        self.assertIn("mls-total-1", quotes)
        self.assertAlmostEqual(quotes["mls-total-1"].lines["2.5"]["over"], 0.60, places=6)
        self.assertEqual(meta["quotes_found"], 1)

    @patch("predictor.polymarket._search_match_event")
    @patch("predictor.polymarket._discover_active_soccer_events")
    def test_polymarket_total_event_matches_fixture_and_returns_lines(self, discover, search):
        event = {
            "id": "event-1",
            "slug": "mls-atl-mia-2026-08-30",
            "title": "Atlanta United vs Inter Miami CF",
            "active": True,
            "closed": False,
            "gameStartTime": "2026-08-30T23:30:00Z",
            "markets": [
                {
                    "id": "total-25",
                    "active": True,
                    "closed": False,
                    "sportsMarketType": "total",
                    "line": 2.5,
                    "question": "Over/Under 2.5 total goals",
                    "outcomes": '["Over", "Under"]',
                    "outcomePrices": '["0.60", "0.40"]',
                    "liquidityNum": 1500,
                    "volumeNum": 7000,
                    "gameStartTime": "2026-08-30T23:30:00Z",
                }
            ],
        }
        discover.return_value = ([event], [], 1)
        search.return_value = ([], None)
        fixtures = [{
            "id": "mls-total-pm",
            "status": "scheduled",
            "date": "2026-08-30",
            "kickoff": "2026-08-30T23:30:00Z",
            "home": "atlanta-united",
            "away": "inter-miami-cf",
        }]
        teams = [
            {"slug": "atlanta-united", "name": "Atlanta United", "short": "ATL"},
            {"slug": "inter-miami-cf", "name": "Inter Miami CF", "short": "MIA"},
        ]
        quotes, meta = fetch_polymarket_totals(
            fixtures, teams, ("MLS",),
            as_of=datetime(2026, 8, 29, 12, tzinfo=timezone.utc),
        )
        self.assertIn("mls-total-pm", quotes)
        self.assertAlmostEqual(quotes["mls-total-pm"].lines["2.5"]["over"], 0.60, places=6)
        self.assertEqual(meta["quotes_found"], 1)
        search.assert_not_called()


if __name__ == "__main__":
    unittest.main()
