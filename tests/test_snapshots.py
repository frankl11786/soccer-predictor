import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SnapshotTests(unittest.TestCase):
    def test_snapshots(self):
        for league, expected_min in (("epl", 18), ("mls", 28)):
            path = ROOT / "app" / "data" / f"{league}.json"
            data = json.loads(path.read_text())

            self.assertGreaterEqual(len(data["teams"]), expected_min)
            self.assertEqual(len(data["teams"]), len(data["forecast"]))
            self.assertGreater(len(data["fixtures"]), 100)

            outcome = "title" if league == "epl" else "champion"
            total = sum(
                float(row.get(outcome, 0))
                for row in data["forecast"]
            )
            self.assertAlmostEqual(total, 1.0, delta=0.03)

            model_version = str(data.get("meta", {}).get("model_version", ""))
            modern_market_schema = "market-comparison" in model_version or "market-consensus" in model_version
            consensus_schema = "market-consensus" in model_version

            if modern_market_schema:
                market_meta = data.get("meta", {}).get("polymarket", {})
                self.assertIsInstance(market_meta, dict)
                if consensus_schema:
                    self.assertIsInstance(data.get("meta", {}).get("kalshi", {}), dict)
                    self.assertIsInstance(data.get("meta", {}).get("market_consensus", {}), dict)
                self.assertTrue(
                    data.get("methodology", {})
                    .get("market_comparison", {})
                    .get("comparison_only")
                )

                team_by_slug = {
                    team["slug"]: team
                    for team in data["teams"]
                }

                for team in data["teams"]:
                    self.assertIn("defense_strength", team)
                    self.assertAlmostEqual(
                        float(team["defense_strength"]),
                        -float(team["defense"]),
                        delta=0.00001,
                    )

                for row in data["forecast"]:
                    self.assertIn("defense_strength", row)
                    self.assertAlmostEqual(
                        float(row["defense_strength"]),
                        -float(row["defense"]),
                        delta=0.00001,
                    )

                    team = team_by_slug.get(row["team"])
                    if team is not None:
                        self.assertAlmostEqual(
                            float(row["defense_strength"]),
                            float(team["defense_strength"]),
                            delta=0.00001,
                        )

                    interval = row.get("defense_strength_interval")
                    effect_interval = row.get("defense_interval")

                    if interval and effect_interval:
                        self.assertAlmostEqual(
                            float(interval[0]),
                            -float(effect_interval[1]),
                            delta=0.00001,
                        )
                        self.assertAlmostEqual(
                            float(interval[1]),
                            -float(effect_interval[0]),
                            delta=0.00001,
                        )

            for row in data["forecast"]:
                market = row.get("market")

                if market is None:
                    self.assertIsNone(row.get("edge"))
                    continue

                self.assertGreaterEqual(float(market), 0.0)
                self.assertLessEqual(float(market), 1.0)

                if modern_market_schema:
                    self.assertIn(outcome, row)

                self.assertAlmostEqual(
                    float(row["edge"]),
                    float(row.get(outcome, 0.0)) - float(market),
                    delta=0.00001,
                )

                details = row.get("market_details")

                if modern_market_schema:
                    self.assertIsInstance(details, dict)
                    self.assertTrue(details.get("comparison_only"))
                    self.assertIn("normalized_probability", details)
                    self.assertAlmostEqual(
                        float(details["normalized_probability"]),
                        float(market),
                        delta=0.00001,
                    )

            if consensus_schema:
                for row in data["forecast"]:
                    kalshi = row.get("kalshi")
                    if kalshi is None:
                        self.assertIsNone(row.get("kalshi_edge"))
                        self.assertIsNone(row.get("kalshi_details"))
                    else:
                        self.assertGreaterEqual(float(kalshi), 0.0)
                        self.assertLessEqual(float(kalshi), 1.0)
                        self.assertAlmostEqual(
                            float(row["kalshi_edge"]),
                            float(row.get(outcome, 0.0)) - float(kalshi),
                            delta=0.00001,
                        )
                        details = row.get("kalshi_details")
                        self.assertIsInstance(details, dict)
                        self.assertTrue(details.get("comparison_only"))
                        self.assertAlmostEqual(
                            float(details["normalized_probability"]),
                            float(kalshi),
                            delta=0.00001,
                        )

                    consensus = row.get("market_consensus")
                    available = [value for value in (row.get("market"), row.get("kalshi")) if value is not None]
                    if available:
                        self.assertIsNotNone(consensus)
                        self.assertAlmostEqual(float(consensus), sum(map(float, available)) / len(available), delta=0.00001)
                        self.assertAlmostEqual(
                            float(row["consensus_edge"]),
                            float(row.get(outcome, 0.0)) - float(consensus),
                            delta=0.00001,
                        )
                    else:
                        self.assertIsNone(consensus)
                        self.assertIsNone(row.get("consensus_edge"))

            for fixture in data["fixtures"]:
                probabilities = fixture["probabilities"]

                self.assertAlmostEqual(
                    probabilities["home"]
                    + probabilities["draw"]
                    + probabilities["away"],
                    1.0,
                    delta=0.01,
                )

                market = fixture.get("polymarket")

                if not market:
                    continue

                market_probabilities = market["probabilities"]
                raw_probabilities = market["raw_probabilities"]
                edges = market["model_edge"]

                self.assertEqual(
                    set(market_probabilities),
                    {"home", "draw", "away"},
                )

                self.assertAlmostEqual(
                    sum(
                        float(value)
                        for value in market_probabilities.values()
                    ),
                    1.0,
                    delta=0.00001,
                )

                self.assertAlmostEqual(
                    sum(
                        float(value)
                        for value in raw_probabilities.values()
                    ),
                    float(market["normalization_total"]),
                    delta=0.00001,
                )

                for result in ("home", "draw", "away"):
                    self.assertAlmostEqual(
                        float(edges[result]),
                        float(probabilities[result])
                        - float(market_probabilities[result]),
                        delta=0.00001,
                    )

                self.assertTrue(market.get("comparison_only"))
                self.assertTrue(
                    market.get("event_slug")
                    or market.get("event_id")
                )

            if consensus_schema:
                for fixture in data["fixtures"]:
                    probabilities = fixture["probabilities"]
                    kalshi = fixture.get("kalshi")
                    if kalshi:
                        market_probabilities = kalshi["probabilities"]
                        self.assertEqual(set(market_probabilities), {"home", "draw", "away"})
                        self.assertAlmostEqual(sum(float(value) for value in market_probabilities.values()), 1.0, delta=0.00001)
                        self.assertTrue(kalshi.get("comparison_only"))
                        self.assertTrue(kalshi.get("event_ticker"))
                        for result in ("home", "draw", "away"):
                            self.assertAlmostEqual(
                                float(kalshi["model_edge"][result]),
                                float(probabilities[result]) - float(market_probabilities[result]),
                                delta=0.00001,
                            )

                    consensus = fixture.get("market_consensus")
                    distributions = []
                    if fixture.get("polymarket"):
                        distributions.append(fixture["polymarket"]["probabilities"])
                    if fixture.get("kalshi"):
                        distributions.append(fixture["kalshi"]["probabilities"])
                    if distributions:
                        self.assertIsInstance(consensus, dict)
                        self.assertEqual(consensus.get("source_count"), len(distributions))
                        for result in ("home", "draw", "away"):
                            expected = sum(float(item[result]) for item in distributions) / len(distributions)
                            self.assertAlmostEqual(float(consensus["probabilities"][result]), expected, delta=0.00001)
                            self.assertAlmostEqual(
                                float(consensus["model_edge"][result]),
                                float(probabilities[result]) - float(consensus["probabilities"][result]),
                                delta=0.00001,
                            )


if __name__ == "__main__":
    unittest.main()
