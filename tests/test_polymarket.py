import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import requests

from predictor.config import LEAGUES
from predictor.identity import team_catalog
from predictor.polymarket import fetch_match_quotes, fetch_winner_quotes


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def binary_market(market_id, label, price, *, question=None, sports_type="moneyline", liquidity=1000, volume=5000):
    return {
        "id": market_id,
        "active": True,
        "closed": False,
        "sportsMarketType": sports_type,
        "groupItemTitle": label,
        "question": question or (f"Will {label} win?" if label != "Draw" else "Will the match end in a draw?"),
        "outcomes": json.dumps(["Yes", "No"]),
        "outcomePrices": json.dumps([str(price), str(1 - price)]),
        "liquidityNum": liquidity,
        "volumeNum": volume,
    }


class PolymarketComparisonTests(unittest.TestCase):
    def setUp(self):
        self.kickoff = datetime.now(timezone.utc) + timedelta(days=2)
        self.teams = [
            {"slug": "atlanta-united", "name": "Atlanta United", "short": "ATL"},
            {"slug": "seattle-sounders-fc", "name": "Seattle Sounders FC", "short": "SEA"},
        ]
        self.fixture = {
            "id": "mls-1",
            "status": "scheduled",
            "date": self.kickoff.isoformat().replace("+00:00", "Z"),
            "kickoff": self.kickoff.isoformat().replace("+00:00", "Z"),
            "home": "atlanta-united",
            "away": "seattle-sounders-fc",
        }

    def event(self, markets, **overrides):
        event = {
            "id": "event-1",
            "slug": "mls-atl-sea-test",
            "title": "MLS Soccer: Atlanta United vs Seattle Sounders FC",
            "eventStartTime": self.kickoff.isoformat().replace("+00:00", "Z"),
            "active": True,
            "closed": False,
            "markets": markets,
        }
        event.update(overrides)
        return event

    def test_exact_binary_1x2_is_normalized(self):
        markets = [
            binary_market("home", "Atlanta United", 0.43),
            binary_market("draw", "Draw", 0.29),
            binary_market("away", "Seattle Sounders FC", 0.31),
        ]
        with patch("predictor.polymarket.requests.get", return_value=FakeResponse({"events": [self.event(markets)]})):
            quotes, metadata = fetch_match_quotes([self.fixture], self.teams, ("MLS",), 7, 10)

        quote = quotes["mls-1"]
        total = 1.03
        self.assertAlmostEqual(quote.home_probability, 0.43 / total, places=6)
        self.assertAlmostEqual(quote.draw_probability, 0.29 / total, places=6)
        self.assertAlmostEqual(quote.away_probability, 0.31 / total, places=6)
        self.assertAlmostEqual(quote.home_probability + quote.draw_probability + quote.away_probability, 1.0, places=6)
        self.assertEqual(set(quote.market_ids), {"home", "draw", "away"})
        self.assertEqual(metadata["quotes_found"], 1)

    def test_single_three_way_market_is_preferred_as_one_coherent_set(self):
        three_way = {
            "id": "three-way",
            "active": True,
            "closed": False,
            "sportsMarketType": "1x2",
            "question": "Atlanta United vs Seattle Sounders FC match result",
            "outcomes": json.dumps(["Atlanta United", "Draw", "Seattle Sounders FC"]),
            "outcomePrices": json.dumps(["0.50", "0.25", "0.25"]),
            "liquidityNum": 100,
            "volumeNum": 100,
        }
        conflicting_binary = [
            binary_market("home-b", "Atlanta United", 0.10, liquidity=5000),
            binary_market("draw-b", "Draw", 0.80, liquidity=5000),
            binary_market("away-b", "Seattle Sounders FC", 0.10, liquidity=5000),
        ]
        event = self.event([three_way, *conflicting_binary])
        with patch("predictor.polymarket.requests.get", return_value=FakeResponse({"events": [event]})):
            quotes, _ = fetch_match_quotes([self.fixture], self.teams, ("MLS",), 7, 10)

        quote = quotes["mls-1"]
        self.assertAlmostEqual(quote.home_probability, 0.50, places=6)
        self.assertAlmostEqual(quote.draw_probability, 0.25, places=6)
        self.assertAlmostEqual(quote.away_probability, 0.25, places=6)
        self.assertEqual(set(quote.market_ids.values()), {"three-way"})

    def test_incomplete_market_is_rejected(self):
        markets = [
            binary_market("home", "Atlanta United", 0.45),
            binary_market("away", "Seattle Sounders FC", 0.30),
        ]
        with patch("predictor.polymarket.requests.get", return_value=FakeResponse({"events": [self.event(markets)]})):
            quotes, metadata = fetch_match_quotes([self.fixture], self.teams, ("MLS",), 7, 10)
        self.assertEqual(quotes, {})
        self.assertGreaterEqual(metadata["rejected_incomplete_1x2"], 1)

    def test_event_without_verifiable_date_is_rejected(self):
        markets = [
            binary_market("home", "Atlanta United", 0.43),
            binary_market("draw", "Draw", 0.29),
            binary_market("away", "Seattle Sounders FC", 0.31),
        ]
        event = self.event(markets)
        event.pop("eventStartTime")
        with patch("predictor.polymarket.requests.get", return_value=FakeResponse({"events": [event]})):
            quotes, metadata = fetch_match_quotes([self.fixture], self.teams, ("MLS",), 7, 10)
        self.assertEqual(quotes, {})
        self.assertGreaterEqual(metadata["rejected_ambiguous"], 1)

    def test_derivative_market_is_rejected(self):
        markets = [
            binary_market("home", "Atlanta United", 0.43, question="Will Atlanta United qualify?", sports_type="winner"),
            binary_market("draw", "Draw", 0.29, question="Will the series end in a draw?", sports_type="winner"),
            binary_market("away", "Seattle Sounders FC", 0.31, question="Will Seattle Sounders FC qualify?", sports_type="winner"),
        ]
        with patch("predictor.polymarket.requests.get", return_value=FakeResponse({"events": [self.event(markets)]})):
            quotes, _ = fetch_match_quotes([self.fixture], self.teams, ("MLS",), 7, 10)
        self.assertEqual(quotes, {})

    def test_brighton_short_name_matches_exact_winner_event(self):
        market = {
            "id": "brighton",
            "active": True,
            "closed": False,
            "groupItemTitle": "Brighton",
            "question": "Will Brighton win the Premier League?",
            "outcomes": json.dumps(["Yes", "No"]),
            "outcomePrices": json.dumps(["0.01", "0.99"]),
        }
        event = {
            "id": "epl-event",
            "slug": "epl-test",
            "title": "EPL Champion",
            "active": True,
            "closed": False,
            "markets": [market],
        }
        with patch("predictor.polymarket.requests.get", return_value=FakeResponse(event)):
            quotes, _ = fetch_winner_quotes((), ["Brighton & Hove Albion"], event_slug="epl-test")
        self.assertIn("Brighton & Hove Albion", quotes)
        self.assertAlmostEqual(quotes["Brighton & Hove Albion"].raw_probability, 0.01, places=6)

    def test_exact_current_winner_event_names_map_one_to_one(self):
        display_names = {
            "epl": {
                "Brighton & Hove Albion": "Brighton",
                "Tottenham Hotspur": "Tottenham",
            },
            "mls": {
                "Atlanta United": "Atlanta United FC",
                "Red Bull New York": "New York Red Bulls",
                "St. Louis CITY SC": "St. Louis City SC",
            },
        }
        for league in ("epl", "mls"):
            with self.subTest(league=league):
                team_names = [team["name"] for team in team_catalog(LEAGUES[league])]
                raw_price = 1.0 / (len(team_names) + 1)
                markets = []
                for index, team_name in enumerate(team_names):
                    display = display_names.get(league, {}).get(team_name, team_name)
                    markets.append(
                        {
                            "id": f"{league}-{index}",
                            "active": True,
                            "closed": False,
                            "groupItemTitle": display,
                            "question": f"Will {display} win?",
                            "outcomes": json.dumps(["Yes", "No"]),
                            "outcomePrices": json.dumps([str(raw_price), str(1 - raw_price)]),
                        }
                    )
                markets.append(
                    {
                        "id": f"{league}-other",
                        "active": True,
                        "closed": False,
                        "groupItemTitle": "Other",
                        "question": "Will another club win?",
                        "outcomes": json.dumps(["Yes", "No"]),
                        "outcomePrices": json.dumps([str(raw_price), str(1 - raw_price)]),
                    }
                )
                event = {
                    "id": f"{league}-event",
                    "slug": f"{league}-exact-test",
                    "title": "Exact competition winner event",
                    "active": True,
                    "closed": False,
                    "markets": markets,
                }
                with patch("predictor.polymarket.requests.get", return_value=FakeResponse(event)):
                    quotes, metadata = fetch_winner_quotes(
                        (),
                        team_names,
                        event_slug=f"{league}-exact-test",
                    )
                self.assertEqual(len(quotes), len(team_names))
                self.assertEqual(len({quote.market_id for quote in quotes.values()}), len(team_names))
                self.assertTrue(metadata["normalization_applied"])
                self.assertTrue(all(quote.normalized for quote in quotes.values()))
                self.assertAlmostEqual(
                    sum(quote.probability for quote in quotes.values()),
                    len(team_names) / (len(team_names) + 1),
                    places=6,
                )

    def test_exact_winner_slug_failure_never_broad_searches(self):
        with patch("predictor.polymarket.requests.get", side_effect=requests.RequestException("temporary")) as mocked:
            quotes, metadata = fetch_winner_quotes(("Premier League winner",), ["Arsenal"], event_slug="exact")
        self.assertEqual(quotes, {})
        self.assertEqual(mocked.call_count, 1)
        self.assertTrue(metadata["errors"])

    def test_match_discovery_uses_active_soccer_events_before_public_search(self):
        markets = [
            binary_market("home-discovery", "Atlanta United", 0.43),
            binary_market("draw-discovery", "Draw", 0.29),
            binary_market("away-discovery", "Seattle Sounders FC", 0.31),
        ]
        # Generic startDate is deliberately unrelated; gameStartTime is the
        # sports-specific field and should be preferred for fixture matching.
        event = self.event(markets, startDate="2026-01-01T00:00:00Z")
        event.pop("eventStartTime", None)
        for market in event["markets"]:
            market["gameStartTime"] = self.kickoff.isoformat().replace("+00:00", "Z")

        calls = []

        def side_effect(url, **kwargs):
            calls.append((url, kwargs))
            if url.endswith("/events"):
                return FakeResponse([event])
            raise AssertionError(f"public-search should not be needed: {url}")

        with patch("predictor.polymarket.requests.get", side_effect=side_effect):
            quotes, metadata = fetch_match_quotes([self.fixture], self.teams, ("MLS",), 7, 10)

        self.assertIn("mls-1", quotes)
        self.assertEqual(metadata["search_fallbacks"], 0)
        self.assertEqual(metadata["discovery_events"], 1)
        params = calls[0][1]["params"]
        self.assertEqual(params["tag_slug"], "soccer")
        self.assertTrue(params["active"])
        self.assertFalse(params["closed"])

    def test_public_search_remains_fallback_when_event_discovery_misses(self):
        markets = [
            binary_market("home-fallback", "Atlanta United", 0.43),
            binary_market("draw-fallback", "Draw", 0.29),
            binary_market("away-fallback", "Seattle Sounders FC", 0.31),
        ]
        event = self.event(markets)

        def side_effect(url, **kwargs):
            if url.endswith("/events"):
                return FakeResponse([])
            if url.endswith("/public-search"):
                return FakeResponse({"events": [event]})
            raise AssertionError(f"unexpected URL {url}")

        with patch("predictor.polymarket.requests.get", side_effect=side_effect):
            quotes, metadata = fetch_match_quotes([self.fixture], self.teams, ("MLS",), 7, 10)

        self.assertIn("mls-1", quotes)
        self.assertEqual(metadata["search_fallbacks"], 1)
        self.assertEqual(metadata["queries_sent"], 1)


if __name__ == "__main__":
    unittest.main()
