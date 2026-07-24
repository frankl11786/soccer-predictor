import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from predictor.bayes import calibrate_epl_state
from predictor.config import LEAGUES
from predictor.data_prep import prepare_league
from predictor.identity import team_catalog
from predictor.polymarket import fetch_match_quotes, fetch_winner_quotes


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class ModelCorrectionTests(unittest.TestCase):
    def test_epl_time_axis_ends_at_last_completed_match(self):
        cfg = LEAGUES["epl"]
        teams = team_catalog(cfg)
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        rows = []
        for index in range(80):
            home = teams[index % len(teams)]
            away = teams[(index + 1) % len(teams)]
            when = start + timedelta(days=index)
            rows.append(
                {
                    "fixture_id": f"history-{index}",
                    "season": 2024,
                    "round": f"Round {index}",
                    "date": when.isoformat().replace("+00:00", "Z"),
                    "timestamp": int(when.timestamp()),
                    "status": "FT",
                    "home_id": home["api_id"],
                    "home_name": home["name"],
                    "away_id": away["api_id"],
                    "away_name": away["name"],
                    "home_goals": index % 4,
                    "away_goals": (index + 1) % 3,
                }
            )
        future = datetime(2026, 8, 21, tzinfo=timezone.utc)
        rows.append(
            {
                "fixture_id": "future-1",
                "season": 2026,
                "round": "Matchday 1",
                "date": future.isoformat().replace("+00:00", "Z"),
                "timestamp": int(future.timestamp()),
                "status": "NS",
                "home_id": teams[0]["api_id"],
                "home_name": teams[0]["name"],
                "away_id": teams[1]["api_id"],
                "away_name": teams[1]["name"],
                "home_goals": None,
                "away_goals": None,
            }
        )

        prepared = prepare_league(cfg, rows)
        expected_last = start + timedelta(days=79)
        self.assertEqual(prepared.last_observed_at.date(), expected_last.date())
        self.assertLess(prepared.n_times, 10)
        self.assertTrue(np.allclose(prepared.history.value_diff.to_numpy(), 0.0))
        self.assertEqual(prepared.historical_value_mode, "future-only")

    def test_preseason_transition_uses_seed_and_recent_information(self):
        prepared = SimpleNamespace(
            seed_attack=np.asarray([0.45, -0.25], dtype=np.float32),
            seed_defense=np.asarray([0.40, -0.20], dtype=np.float32),
            recent_attack=np.asarray([0.30, 0.0], dtype=np.float32),
            recent_defense=np.asarray([0.35, 0.0], dtype=np.float32),
            recent_matches=np.asarray([38, 0], dtype=np.int32),
            current_season_matches=np.asarray([0, 0], dtype=np.int32),
            days_since_last_observed=60,
            recent_season=2025,
            teams=[{"slug": "established"}, {"slug": "promoted"}],
        )
        # Deliberately inverted raw fit. Calibration should pull the first club
        # back toward its strong prior and the promoted club toward its seed.
        attack = np.tile(np.asarray([-0.8, 0.8], dtype=np.float32), (200, 1))
        defense = np.tile(np.asarray([-0.7, 0.7], dtype=np.float32), (200, 1))
        calibrated_attack, calibrated_defense, summary = calibrate_epl_state(
            prepared,
            attack,
            defense,
        )
        self.assertTrue(summary["applied"])
        self.assertFalse(summary["uses_polymarket"])
        self.assertIn("promoted", summary["clubs_without_recent_epl_history"])
        self.assertGreater(calibrated_attack[:, 0].mean(), attack[:, 0].mean())
        self.assertGreater(calibrated_defense[:, 0].mean(), defense[:, 0].mean())
        self.assertTrue(np.allclose(calibrated_attack.mean(axis=1), 0.0, atol=1e-6))
        self.assertTrue(np.allclose(calibrated_defense.mean(axis=1), 0.0, atol=1e-6))

    def test_polymarket_winner_event_is_normalized(self):
        teams = ["Arsenal", "Manchester City", "Liverpool", "Chelsea", "Manchester United", "Newcastle United"]
        raw_prices = [0.40, 0.30, 0.15, 0.10, 0.06, 0.04]
        markets = []
        for index, (team, price) in enumerate(zip(teams, raw_prices)):
            markets.append(
                {
                    "id": str(index),
                    "active": True,
                    "closed": False,
                    "question": f"Will {team} win the Premier League?",
                    "outcomes": json.dumps(["Yes", "No"]),
                    "outcomePrices": json.dumps([str(price), str(1 - price)]),
                    "liquidityNum": 1000,
                    "volumeNum": 10000,
                }
            )
        markets.append(
            {
                "id": "other",
                "active": True,
                "closed": False,
                "question": "Will another club win the Premier League?",
                "outcomes": json.dumps(["Yes", "No"]),
                "outcomePrices": json.dumps(["0.05", "0.95"]),
                "liquidityNum": 1000,
                "volumeNum": 10000,
            }
        )
        event = {
            "id": "event-1",
            "slug": "epl-test",
            "title": "EPL Champion",
            "active": True,
            "closed": False,
            "markets": markets,
        }
        with patch("predictor.polymarket.requests.get", return_value=FakeResponse(event)):
            quotes, metadata = fetch_winner_quotes((), teams, event_slug="epl-test")

        self.assertTrue(metadata["normalization_applied"])
        raw_total = sum(raw_prices) + 0.05
        self.assertAlmostEqual(quotes["Arsenal"].probability, 0.40 / raw_total, places=6)
        self.assertAlmostEqual(quotes["Arsenal"].raw_probability, 0.40, places=6)
        self.assertTrue(quotes["Arsenal"].normalized)
        self.assertEqual(metadata["direct_event_slug_used"], "epl-test")



    def test_exact_winner_event_failure_does_not_use_broad_search(self):
        with patch(
            "predictor.polymarket.requests.get",
            side_effect=__import__("requests").RequestException("temporary failure"),
        ) as mocked_get:
            quotes, metadata = fetch_winner_quotes(
                ("Premier League winner",),
                ["Arsenal", "Manchester City"],
                event_slug="exact-event-slug",
            )
        self.assertEqual(quotes, {})
        self.assertEqual(mocked_get.call_count, 1)
        self.assertIsNone(metadata["direct_event_slug_used"])
        self.assertTrue(metadata["errors"])

    def test_exact_three_way_match_market_is_normalized(self):
        kickoff = datetime.now(timezone.utc) + timedelta(days=2)
        teams = [
            {"slug": "atlanta-united", "name": "Atlanta United", "short": "ATL"},
            {"slug": "seattle-sounders-fc", "name": "Seattle Sounders FC", "short": "SEA"},
        ]
        fixture = {
            "id": "mls-1",
            "status": "scheduled",
            "date": kickoff.isoformat().replace("+00:00", "Z"),
            "kickoff": kickoff.isoformat().replace("+00:00", "Z"),
            "home": "atlanta-united",
            "away": "seattle-sounders-fc",
        }
        prices = {"Atlanta United": 0.43, "Draw": 0.29, "Seattle Sounders FC": 0.31}
        markets = []
        for index, (label, price) in enumerate(prices.items()):
            markets.append(
                {
                    "id": f"match-{index}",
                    "active": True,
                    "closed": False,
                    "sportsMarketType": "moneyline",
                    "groupItemTitle": label,
                    "question": f"Will {label} win?" if label != "Draw" else "Will the match end in a draw?",
                    "outcomes": json.dumps(["Yes", "No"]),
                    "outcomePrices": json.dumps([str(price), str(1 - price)]),
                    "liquidityNum": 1000 + index,
                    "volumeNum": 5000 + index,
                }
            )
        event = {
            "id": "match-event",
            "slug": "atlanta-united-vs-seattle-sounders",
            "title": "MLS Soccer: Atlanta United vs Seattle Sounders FC",
            "eventStartTime": kickoff.isoformat().replace("+00:00", "Z"),
            "active": True,
            "closed": False,
            "markets": markets,
        }
        response = {"events": [event]}
        with patch("predictor.polymarket.requests.get", return_value=FakeResponse(response)):
            quotes, metadata = fetch_match_quotes(
                [fixture],
                teams,
                league_terms=("MLS", "Major League Soccer"),
                lookahead_days=7,
                max_fixtures=10,
            )

        self.assertIn("mls-1", quotes)
        quote = quotes["mls-1"]
        raw_total = sum(prices.values())
        self.assertAlmostEqual(quote.home_probability, prices["Atlanta United"] / raw_total, places=6)
        self.assertAlmostEqual(quote.draw_probability, prices["Draw"] / raw_total, places=6)
        self.assertAlmostEqual(quote.away_probability, prices["Seattle Sounders FC"] / raw_total, places=6)
        self.assertAlmostEqual(
            quote.home_probability + quote.draw_probability + quote.away_probability,
            1.0,
            places=6,
        )
        self.assertTrue(quote.normalized)
        self.assertEqual(metadata["quotes_found"], 1)
        self.assertEqual(metadata["eligible_fixtures"], 1)
        self.assertEqual(metadata["coverage"], 1.0)

    def test_incomplete_match_market_is_not_published(self):
        kickoff = datetime.now(timezone.utc) + timedelta(days=2)
        teams = [
            {"slug": "atlanta-united", "name": "Atlanta United", "short": "ATL"},
            {"slug": "seattle-sounders-fc", "name": "Seattle Sounders FC", "short": "SEA"},
        ]
        fixture = {
            "id": "mls-2",
            "status": "scheduled",
            "kickoff": kickoff.isoformat().replace("+00:00", "Z"),
            "home": "atlanta-united",
            "away": "seattle-sounders-fc",
        }
        event = {
            "id": "incomplete-event",
            "slug": "atlanta-united-vs-seattle-sounders",
            "title": "MLS: Atlanta United vs Seattle Sounders FC",
            "eventStartTime": kickoff.isoformat().replace("+00:00", "Z"),
            "active": True,
            "closed": False,
            "markets": [
                {
                    "id": "home-only",
                    "active": True,
                    "closed": False,
                    "sportsMarketType": "moneyline",
                    "groupItemTitle": "Atlanta United",
                    "outcomes": json.dumps(["Yes", "No"]),
                    "outcomePrices": json.dumps(["0.45", "0.55"]),
                },
                {
                    "id": "away-only",
                    "active": True,
                    "closed": False,
                    "sportsMarketType": "moneyline",
                    "groupItemTitle": "Seattle Sounders FC",
                    "outcomes": json.dumps(["Yes", "No"]),
                    "outcomePrices": json.dumps(["0.30", "0.70"]),
                },
            ],
        }
        with patch("predictor.polymarket.requests.get", return_value=FakeResponse({"events": [event]})):
            quotes, metadata = fetch_match_quotes(
                [fixture], teams, ("MLS",), lookahead_days=7, max_fixtures=10
            )
        self.assertEqual(quotes, {})
        self.assertEqual(metadata["quotes_found"], 0)
        self.assertGreaterEqual(metadata["rejected_incomplete_1x2"], 1)

    def test_spread_market_is_not_misclassified_as_match_result(self):
        kickoff = datetime.now(timezone.utc) + timedelta(days=2)
        teams = [
            {"slug": "arsenal", "name": "Arsenal", "short": "ARS"},
            {"slug": "chelsea", "name": "Chelsea", "short": "CHE"},
        ]
        fixture = {
            "id": "epl-1",
            "status": "scheduled",
            "kickoff": kickoff.isoformat().replace("+00:00", "Z"),
            "home": "arsenal",
            "away": "chelsea",
        }
        event = {
            "id": "spread-event",
            "slug": "arsenal-vs-chelsea-spread",
            "title": "Premier League: Arsenal vs Chelsea",
            "eventStartTime": kickoff.isoformat().replace("+00:00", "Z"),
            "active": True,
            "closed": False,
            "markets": [
                {
                    "id": "spread",
                    "active": True,
                    "closed": False,
                    "sportsMarketType": "spread",
                    "question": "Arsenal -1.5 spread",
                    "outcomes": json.dumps(["Arsenal", "Draw", "Chelsea"]),
                    "outcomePrices": json.dumps(["0.4", "0.3", "0.3"]),
                }
            ],
        }
        with patch("predictor.polymarket.requests.get", return_value=FakeResponse({"events": [event]})):
            quotes, _ = fetch_match_quotes(
                [fixture], teams, ("Premier League",), lookahead_days=7, max_fixtures=10
            )
        self.assertEqual(quotes, {})


if __name__ == "__main__":
    unittest.main()
