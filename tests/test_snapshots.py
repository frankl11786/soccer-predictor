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

            modern_market_schema = (
                "market-comparison"
                in str(data.get("meta", {}).get("model_version", ""))
            )

            if modern_market_schema:
                market_meta = data.get("meta", {}).get("polymarket", {})
                self.assertIsInstance(market_meta, dict)
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


if __name__ == "__main__":
    unittest.main()
