import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from predictor import api_football, asa
from predictor.config import LEAGUES
from predictor.identity import team_catalog
from predictor.integrity import (
    _PandasSoccerClient,
    _PolymarketRequestsProxy,
    fetch_history_rows_resilient,
    fetch_mls_rows_compatible,
    reconcile_epl_fixture_rows,
    repair_published_snapshot,
    sanitize_kalshi_match_quotes,
    validate_prepared_schedule,
)
from predictor.kalshi import KalshiMatchQuote


class _PolarsLike:
    def __init__(self, rows):
        self._rows = rows

    def to_dicts(self):
        return list(self._rows)


class _SoccerClient:
    def get_games(self, *args, **kwargs):
        return _PolarsLike([{"game_id": "1", "season": 2026}])

    def get_teams(self, *args, **kwargs):
        return _PolarsLike([{"team_id": "a", "team_name": "Example"}])


class _FakeRequests:
    def __init__(self):
        self.calls = []

    def get(self, url, *args, **kwargs):
        self.calls.append((url, kwargs.get("params")))
        return object()


def _kalshi_quote(raw, methods):
    total = sum(raw.values())
    probabilities = {key: value / total for key, value in raw.items()}
    return KalshiMatchQuote(
        fixture_id="fixture-1",
        home_probability=probabilities["home"],
        draw_probability=probabilities["draw"],
        away_probability=probabilities["away"],
        home_raw_probability=raw["home"],
        draw_raw_probability=raw["draw"],
        away_raw_probability=raw["away"],
        normalized=True,
        normalization_total=total,
        event_ticker="EVENT",
        event_title="Home vs Away",
        event_url="https://example.test/event",
        kickoff="2026-10-24T14:30:00Z",
        market_tickers={"home": "H", "draw": "D", "away": "A"},
        bids={"home": 0.1, "draw": 0.1, "away": 0.1},
        asks={"home": 0.2, "draw": 0.2, "away": 0.2},
        lasts=dict(raw),
        spreads={"home": 0.1, "draw": 0.1, "away": 0.1},
        estimate_methods=dict(methods),
        volume=1.0,
        volume_24h=1.0,
        liquidity=1.0,
        open_interest=1.0,
        updated_at="2026-10-10T00:00:00Z",
    )


class IntegrityTests(unittest.TestCase):
    def _epl_spine(self):
        cfg = LEAGUES["epl"]
        teams = team_catalog(cfg)
        rows = []
        n = 0
        for home in teams:
            for away in teams:
                if home["api_id"] == away["api_id"]:
                    continue
                n += 1
                rows.append(
                    {
                        "fixture_id": f"fd-epl-test-{n}",
                        "source": "FixtureDownload",
                        "date": f"2026-11-{(n % 20) + 1:02d}T15:00:00Z",
                        "timestamp": 1793500000 + n * 3600,
                        "season": cfg.current_season,
                        "round": "Regular Season",
                        "status": "NS",
                        "status_long": "Not Started",
                        "home_id": home["api_id"],
                        "home_name": home["name"],
                        "away_id": away["api_id"],
                        "away_name": away["name"],
                        "home_goals": None,
                        "away_goals": None,
                        "penalty_home": None,
                        "penalty_away": None,
                        "venue_id": None,
                        "venue_name": None,
                    }
                )
        self.assertEqual(len(rows), 380)
        return cfg, rows

    def test_epl_reconciliation_keeps_exact_spine_when_other_source_moves_fixture(self):
        cfg, rows = self._epl_spine()
        target = rows[0]
        stale_duplicate = {
            **target,
            "fixture_id": "of-stale-date",
            "source": "OpenFootball",
            "date": "2026-11-30T15:00:00Z",
            "timestamp": target["timestamp"] + 20 * 86400,
        }
        final_result = {
            **target,
            "fixture_id": "espn-final",
            "source": "ESPN",
            "date": "2026-11-02T16:00:00Z",
            "timestamp": target["timestamp"] + 86400,
            "status": "FT",
            "status_long": "Full Time",
            "home_goals": 2,
            "away_goals": 1,
        }

        reconciled = reconcile_epl_fixture_rows(
            cfg,
            rows + [stale_duplicate, final_result],
        )
        current = [row for row in reconciled if row["season"] == cfg.current_season]
        self.assertEqual(len(current), 380)
        pair_rows = [
            row
            for row in current
            if row["home_id"] == target["home_id"]
            and row["away_id"] == target["away_id"]
        ]
        self.assertEqual(len(pair_rows), 1)
        self.assertEqual(pair_rows[0]["fixture_id"], target["fixture_id"])
        self.assertEqual(pair_rows[0]["status"], "FT")
        self.assertEqual(pair_rows[0]["home_goals"], 2)
        self.assertEqual(pair_rows[0]["date"], final_result["date"])

        prepared = SimpleNamespace(current_fixtures=pd.DataFrame(current))
        validate_prepared_schedule(prepared, "epl")

    def test_polars_like_soccer_frames_are_normalized_to_pandas(self):
        client = _PandasSoccerClient(_SoccerClient())
        games = client.get_games(leagues="mls")
        teams = client.get_teams(leagues="mls")
        self.assertIsInstance(games, pd.DataFrame)
        self.assertIsInstance(teams, pd.DataFrame)
        self.assertEqual(games.iloc[0]["game_id"], "1")
        self.assertEqual(teams.iloc[0]["team_name"], "Example")

    def test_mls_fetch_wrapper_exposes_pandas_frame_to_existing_asa_parser(self):
        real_client = _SoccerClient()

        def fake_fetch(seasons, refresh=False):
            frame = asa._client().get_games(leagues="mls")
            self.assertIsInstance(frame, pd.DataFrame)
            return (
                [{"fixture_id": "asa-test", "season": 2026, "timestamp": 1}],
                {"source": "American Soccer Analysis", "fixtures_received": 1, "errors": []},
            )

        with patch.object(asa, "_client", return_value=real_client):
            with patch.object(asa, "fetch_mls_rows", side_effect=fake_fetch):
                rows, meta = fetch_mls_rows_compatible((2026,), refresh=True)

        self.assertEqual(len(rows), 1)
        self.assertFalse(meta["cache_fallback"])

    def test_api_history_refresh_uses_committed_cache_when_live_source_is_down(self):
        cfg = SimpleNamespace(key="mls", api_history_seasons=(2022,))
        wrapped = {
            "payload": {
                "response": [
                    {
                        "fixture": {
                            "id": 99,
                            "date": "2022-03-01T00:00:00+00:00",
                            "timestamp": 1646092800,
                            "status": {"short": "FT", "long": "Match Finished"},
                            "venue": {},
                        },
                        "league": {"season": 2022},
                        "teams": {
                            "home": {"name": "Home"},
                            "away": {"name": "Away"},
                        },
                        "goals": {"home": 1, "away": 0},
                        "score": {"penalty": {}},
                    }
                ]
            }
        }

        with tempfile.TemporaryDirectory() as tempdir:
            cache_path = Path(tempdir) / "fixtures_2022.json"
            cache_path.write_text(json.dumps(wrapped), encoding="utf-8")
            live_meta = {
                "source": "API-Football",
                "seasons_unavailable": [{"season": 2022, "error": "suspended"}],
                "fixtures_received": 0,
            }
            with patch.object(
                api_football,
                "fetch_history_rows",
                return_value=([], live_meta),
            ):
                with patch.object(
                    api_football,
                    "_cache_path",
                    return_value=cache_path,
                ):
                    rows, meta = fetch_history_rows_resilient(
                        object(),
                        cfg,
                        refresh=True,
                    )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["fixture_id"], "api-99")
        self.assertEqual(meta["cache_fallback_seasons"], [2022])
        self.assertTrue(meta["cache_fallback"])

    def test_invalid_zero_last_trade_kalshi_triplet_is_rejected(self):
        quote = _kalshi_quote(
            {"home": 0.0, "draw": 0.0, "away": 0.77},
            {"home": "last_trade", "draw": "last_trade", "away": "last_trade"},
        )
        kept, meta = sanitize_kalshi_match_quotes(
            {"fixture-1": quote},
            {"quotes_found": 1},
        )
        self.assertEqual(kept, {})
        self.assertEqual(meta["quotes_found"], 0)
        self.assertEqual(meta["rejected_unreliable_triplets"], 1)

    def test_valid_kalshi_triplet_is_preserved(self):
        quote = _kalshi_quote(
            {"home": 0.45, "draw": 0.30, "away": 0.25},
            {
                "home": "bid_ask_midpoint",
                "draw": "bid_ask_midpoint",
                "away": "bid_ask_midpoint",
            },
        )
        kept, meta = sanitize_kalshi_match_quotes(
            {"fixture-1": quote},
            {"quotes_found": 1},
        )
        self.assertEqual(kept["fixture-1"], quote)
        self.assertEqual(meta["rejected_unreliable_triplets"], 0)

    def test_polymarket_proxy_drops_related_tags_when_filtering_by_slug(self):
        requests_module = _FakeRequests()
        proxy = _PolymarketRequestsProxy(requests_module)
        proxy.get(
            "https://gamma-api.polymarket.com/events",
            params={
                "tag_slug": "soccer",
                "related_tags": True,
                "active": True,
            },
        )
        params = requests_module.calls[0][1]
        self.assertEqual(params["tag_slug"], "soccer")
        self.assertNotIn("related_tags", params)

    def test_snapshot_repair_relinks_history_and_freezes_final_probabilities(self):
        payload = {
            "meta": {},
            "fixtures": [
                {
                    "id": "fd-epl-current",
                    "status": "final",
                    "date": "2026-08-21",
                    "kickoff": "2026-08-21T19:00:00Z",
                    "home": "arsenal",
                    "away": "coventry-city",
                    "probabilities": {
                        "home": 0.70,
                        "draw": 0.20,
                        "away": 0.10,
                        "home_interval": [0.65, 0.75],
                    },
                }
            ],
            "prediction_history": [
                {
                    "fixture_id": "of-old-id",
                    "status": "final",
                    "date": "2026-08-21",
                    "kickoff": "2026-08-21T19:00:00Z",
                    "home": "arsenal",
                    "away": "coventry-city",
                    "captured_at": "2026-08-21T18:00:00Z",
                    "sources": {
                        "model": {
                            "home": 0.60,
                            "draw": 0.25,
                            "away": 0.15,
                        }
                    },
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "snapshot.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            result = repair_published_snapshot(path)
            repaired = json.loads(path.read_text(encoding="utf-8"))

        history = repaired["prediction_history"][0]
        fixture = repaired["fixtures"][0]
        self.assertEqual(history["fixture_id"], "fd-epl-current")
        self.assertEqual(fixture["probabilities"]["home"], 0.60)
        self.assertEqual(fixture["probabilities"]["draw"], 0.25)
        self.assertEqual(fixture["probabilities"]["away"], 0.15)
        self.assertEqual(fixture["probabilities"]["source"], "frozen_pregame")
        self.assertEqual(result["history_links_repaired"], 1)
        self.assertEqual(result["unresolved_final_history_links"], 0)
        self.assertEqual(result["final_fixtures_using_frozen_pregame_probabilities"], 1)


if __name__ == "__main__":
    unittest.main()
