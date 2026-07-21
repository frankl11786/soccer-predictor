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
            total = sum(float(row.get(outcome, 0)) for row in data["forecast"])
            self.assertAlmostEqual(total, 1.0, delta=0.03)
            for fixture in data["fixtures"]:
                probs = fixture["probabilities"]
                self.assertAlmostEqual(probs["home"] + probs["draw"] + probs["away"], 1.0, delta=0.01)


if __name__ == "__main__":
    unittest.main()
