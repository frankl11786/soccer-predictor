import unittest
from unittest.mock import Mock, patch

from predictor.kalshi import _market_price, fetch_match_quotes, fetch_winner_quotes


def response(payload):
    mock = Mock()
    mock.raise_for_status.return_value = None
    mock.json.return_value = payload
    return mock


class KalshiComparisonTests(unittest.TestCase):
    def test_bid_ask_midpoint_is_preferred(self):
        price = _market_price(
            {
                "yes_bid_dollars": "0.32",
                "yes_ask_dollars": "0.34",
                "last_price_dollars": "0.31",
            }
        )
        self.assertIsNotNone(price)
        self.assertAlmostEqual(price.probability, 0.33, places=8)
        self.assertAlmostEqual(price.spread, 0.02, places=8)
        self.assertEqual(price.method, "bid_ask_midpoint")

    def test_legacy_cent_price_one_means_one_percent(self):
        price = _market_price({"yes_bid": 1, "yes_ask": 3, "last_price": 2})
        self.assertIsNotNone(price)
        self.assertAlmostEqual(price.probability, 0.02, places=8)
        self.assertAlmostEqual(price.bid, 0.01, places=8)
        self.assertAlmostEqual(price.ask, 0.03, places=8)

    def test_last_trade_fallback_for_unusable_book(self):
        price = _market_price(
            {
                "yes_bid_dollars": "0.00",
                "yes_ask_dollars": "1.00",
                "last_price_dollars": "0.27",
            }
        )
        self.assertIsNotNone(price)
        self.assertAlmostEqual(price.probability, 0.27, places=8)
        self.assertEqual(price.method, "last_trade")

    @patch("predictor.kalshi.requests.get")
    def test_exact_winner_event_is_normalized(self, get):
        markets = [
            {"ticker": "A", "yes_sub_title": "Arsenal", "status": "open", "yes_bid_dollars": "0.32", "yes_ask_dollars": "0.34"},
            {"ticker": "B", "yes_sub_title": "Manchester City", "status": "open", "yes_bid_dollars": "0.24", "yes_ask_dollars": "0.26"},
            {"ticker": "C", "yes_sub_title": "Liverpool", "status": "open", "yes_bid_dollars": "0.14", "yes_ask_dollars": "0.16"},
            {"ticker": "D", "yes_sub_title": "Manchester United", "status": "open", "yes_bid_dollars": "0.11", "yes_ask_dollars": "0.13"},
            {"ticker": "E", "yes_sub_title": "Chelsea", "status": "open", "yes_bid_dollars": "0.09", "yes_ask_dollars": "0.11"},
        ]
        # The public Get Event endpoint can expose markets at the response top
        # level. The production parser accepts that shape even though it asks
        # Kalshi for nested markets explicitly.
        get.return_value = response(
            {
                "event": {
                    "event_ticker": "KXPREMIERLEAGUE-27",
                    "series_ticker": "KXPREMIERLEAGUE",
                    "title": "English Premier League Champion",
                    "mutually_exclusive": True,
                },
                "markets": markets,
            }
        )
        teams = ["Arsenal", "Manchester City", "Liverpool", "Manchester United", "Chelsea"]
        quotes, meta = fetch_winner_quotes("KXPREMIERLEAGUE-27", teams)
        self.assertEqual(set(quotes), set(teams))
        self.assertTrue(meta["normalization_applied"])
        self.assertAlmostEqual(sum(q.probability for q in quotes.values()), 1.0, places=6)
        self.assertEqual(quotes["Arsenal"].estimate_method, "bid_ask_midpoint")
        self.assertTrue(quotes["Arsenal"].event_url.endswith("kxpremierleague-27"))
        _, kwargs = get.call_args
        self.assertEqual(kwargs["params"], {"with_nested_markets": "true"})

    @patch("predictor.kalshi.requests.get")
    def test_exact_three_way_match_is_normalized(self, get):
        get.return_value = response(
            {
                "events": [
                    {
                        "event_ticker": "KXEPLGAME-26AUG22ARSLEE",
                        "series_ticker": "KXEPLGAME",
                        "title": "Arsenal vs Leeds United",
                        "markets": [
                            {"ticker": "HOME", "yes_sub_title": "Arsenal", "status": "open", "yes_bid_dollars": "0.61", "yes_ask_dollars": "0.63", "expected_expiration_time": "2026-08-22T16:00:00Z"},
                            {"ticker": "DRAW", "yes_sub_title": "Tie", "status": "open", "yes_bid_dollars": "0.21", "yes_ask_dollars": "0.23", "expected_expiration_time": "2026-08-22T16:00:00Z"},
                            {"ticker": "AWAY", "yes_sub_title": "Leeds United", "status": "open", "yes_bid_dollars": "0.17", "yes_ask_dollars": "0.19", "expected_expiration_time": "2026-08-22T16:00:00Z"},
                        ],
                    }
                ],
                "cursor": "",
            }
        )
        fixtures = [
            {
                "id": "epl-1",
                "status": "scheduled",
                "date": "2026-08-22",
                "kickoff": "2026-08-22T16:00:00Z",
                "home": "arsenal",
                "away": "leedsunited",
            }
        ]
        teams = [
            {"slug": "arsenal", "name": "Arsenal", "short": "ARS"},
            {"slug": "leedsunited", "name": "Leeds United", "short": "LEE"},
        ]
        quotes, meta = fetch_match_quotes(fixtures, teams, "KXEPLGAME", lookahead_days=365, max_fixtures=5)
        self.assertIn("epl-1", quotes)
        quote = quotes["epl-1"]
        self.assertAlmostEqual(quote.home_probability + quote.draw_probability + quote.away_probability, 1.0, places=6)
        self.assertEqual(quote.market_tickers["draw"], "DRAW")
        self.assertEqual(meta["quotes_found"], 1)

    @patch("predictor.kalshi.requests.get")
    def test_wrong_date_match_event_is_rejected(self, get):
        get.return_value = response(
            {
                "events": [
                    {
                        "event_ticker": "KXEPLGAME-26SEP30ARSLEE",
                        "series_ticker": "KXEPLGAME",
                        "title": "Arsenal vs Leeds United",
                        "markets": [
                            {"ticker": "H", "yes_sub_title": "Arsenal", "status": "open", "yes_bid_dollars": "0.6", "yes_ask_dollars": "0.62"},
                            {"ticker": "D", "yes_sub_title": "Tie", "status": "open", "yes_bid_dollars": "0.2", "yes_ask_dollars": "0.22"},
                            {"ticker": "A", "yes_sub_title": "Leeds United", "status": "open", "yes_bid_dollars": "0.18", "yes_ask_dollars": "0.20"},
                        ],
                    }
                ],
                "cursor": "",
            }
        )
        fixtures = [{"id": "f", "status": "scheduled", "date": "2026-08-22", "kickoff": "2026-08-22T16:00:00Z", "home": "arsenal", "away": "leedsunited"}]
        teams = [{"slug": "arsenal", "name": "Arsenal", "short": "ARS"}, {"slug": "leedsunited", "name": "Leeds United", "short": "LEE"}]
        quotes, _ = fetch_match_quotes(fixtures, teams, "KXEPLGAME", lookahead_days=365, max_fixtures=5)
        self.assertEqual(quotes, {})

    @patch("predictor.kalshi.requests.get")
    def test_kalshi_mls_short_labels_match_canonical_clubs(self, get):
        get.return_value = response(
            {
                "events": [
                    {
                        "event_ticker": "KXMLSGAME-26AUG22LAFCMIA",
                        "series_ticker": "KXMLSGAME",
                        "title": "Los Angeles F vs Miami",
                        "markets": [
                            {"ticker": "H", "yes_sub_title": "Los Angeles F", "status": "open", "yes_bid_dollars": "0.45", "yes_ask_dollars": "0.47"},
                            {"ticker": "D", "yes_sub_title": "Tie", "status": "open", "yes_bid_dollars": "0.25", "yes_ask_dollars": "0.27"},
                            {"ticker": "A", "yes_sub_title": "Miami", "status": "open", "yes_bid_dollars": "0.28", "yes_ask_dollars": "0.30"},
                        ],
                    }
                ],
                "cursor": "",
            }
        )
        fixtures = [
            {
                "id": "mls-short",
                "status": "scheduled",
                "date": "2026-08-22",
                "kickoff": "2026-08-22T23:00:00Z",
                "home": "los-angeles-fc",
                "away": "inter-miami-cf",
            }
        ]
        teams = [
            {"slug": "los-angeles-fc", "name": "Los Angeles FC", "short": "LAFC"},
            {"slug": "inter-miami-cf", "name": "Inter Miami CF", "short": "MIA"},
        ]
        quotes, _ = fetch_match_quotes(fixtures, teams, "KXMLSGAME", lookahead_days=365, max_fixtures=5)
        self.assertIn("mls-short", quotes)
        self.assertEqual(quotes["mls-short"].market_tickers["home"], "H")
        self.assertEqual(quotes["mls-short"].market_tickers["away"], "A")

    @patch("predictor.kalshi.requests.get")
    def test_incomplete_three_way_event_is_rejected(self, get):
        get.return_value = response(
            {
                "events": [
                    {
                        "event_ticker": "KXMLSGAME-26AUG22MIAATL",
                        "series_ticker": "KXMLSGAME",
                        "title": "Miami vs Atlanta",
                        "markets": [
                            {"ticker": "H", "yes_sub_title": "Miami", "status": "open", "yes_bid_dollars": "0.6", "yes_ask_dollars": "0.62"},
                            {"ticker": "A", "yes_sub_title": "Atlanta", "status": "open", "yes_bid_dollars": "0.18", "yes_ask_dollars": "0.20"},
                        ],
                    }
                ],
                "cursor": "",
            }
        )
        fixtures = [{"id": "f", "status": "scheduled", "date": "2026-08-22", "kickoff": "2026-08-22T23:00:00Z", "home": "intermiami", "away": "atlantaunited"}]
        teams = [{"slug": "intermiami", "name": "Inter Miami", "short": "MIA"}, {"slug": "atlantaunited", "name": "Atlanta United", "short": "ATL"}]
        quotes, _ = fetch_match_quotes(fixtures, teams, "KXMLSGAME", lookahead_days=365, max_fixtures=5)
        self.assertEqual(quotes, {})

    @patch("predictor.kalshi.requests.get")
    def test_atlanta_new_york_is_found_via_status_agnostic_markets_fallback(self, get):
        event_ticker = "KXMLSGAME-26AUG15ATLNYRB"
        market_rows = [
            {
                "ticker": f"{event_ticker}-ATL",
                "event_ticker": event_ticker,
                "yes_sub_title": "Atlanta",
                "status": "active",
                "yes_bid_dollars": "0.49",
                "yes_ask_dollars": "0.51",
                "close_time": "2026-08-16T03:30:00Z",
                "volume_fp": "1000",
            },
            {
                "ticker": f"{event_ticker}-TIE",
                "event_ticker": event_ticker,
                "yes_sub_title": "Tie",
                "status": "active",
                "yes_bid_dollars": "0.22",
                "yes_ask_dollars": "0.24",
                "close_time": "2026-08-16T03:30:00Z",
                "volume_fp": "1000",
            },
            {
                "ticker": f"{event_ticker}-NYRB",
                "event_ticker": event_ticker,
                "yes_sub_title": "New York RB",
                "status": "active",
                "yes_bid_dollars": "0.27",
                "yes_ask_dollars": "0.29",
                "close_time": "2026-08-16T03:30:00Z",
                "volume_fp": "1000",
            },
        ]

        def side_effect(url, **kwargs):
            if url.endswith("/events"):
                return response({"events": [], "cursor": ""})
            if url.endswith("/markets"):
                return response({"markets": market_rows, "cursor": ""})
            raise AssertionError(f"unexpected URL {url}")

        get.side_effect = side_effect
        fixtures = [
            {
                "id": "atl-nyrb",
                "status": "scheduled",
                "date": "2026-08-15",
                "kickoff": "2026-08-15T23:30:00Z",
                "home": "atlanta-united",
                "away": "red-bull-new-york",
            }
        ]
        teams = [
            {"slug": "atlanta-united", "name": "Atlanta United", "short": "ATL"},
            {"slug": "red-bull-new-york", "name": "Red Bull New York", "short": "RBNY"},
        ]

        quotes, meta = fetch_match_quotes(fixtures, teams, "KXMLSGAME", lookahead_days=365, max_fixtures=5)
        self.assertIn("atl-nyrb", quotes)
        quote = quotes["atl-nyrb"]
        self.assertEqual(quote.event_ticker, event_ticker)
        self.assertAlmostEqual(quote.home_raw_probability, 0.50, places=6)
        self.assertAlmostEqual(quote.draw_raw_probability, 0.23, places=6)
        self.assertAlmostEqual(quote.away_raw_probability, 0.28, places=6)
        self.assertEqual(meta["discovery"]["market_rows"], 3)

        event_call = next(call for call in get.call_args_list if call.args[0].endswith("/events"))
        market_call = next(call for call in get.call_args_list if call.args[0].endswith("/markets"))
        self.assertNotIn("status", event_call.kwargs["params"])
        self.assertNotIn("status", market_call.kwargs["params"])
        self.assertIn("min_close_ts", event_call.kwargs["params"])
        self.assertIn("min_close_ts", market_call.kwargs["params"])
        self.assertEqual(market_call.kwargs["params"]["mve_filter"], "exclude")


if __name__ == "__main__":
    unittest.main()
