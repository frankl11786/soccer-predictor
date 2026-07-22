import unittest

from predictor.config import LEAGUES
from predictor.identity import canonicalize_fixture_rows, team_catalog
from predictor.openfootball import parse_premier_league


SAMPLE = """= English Premier League 2026/27
▪ Matchday 1
Fri Aug 21 2026
  20:00 Arsenal FC v Coventry City FC
Sat Aug 22
  15:00 Hull City AFC v Manchester United FC 1-2 (0-1)
"""


class SourceTests(unittest.TestCase):
    def test_openfootball_parser(self):
        rows = parse_premier_league(SAMPLE, 2026)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["status"], "NS")
        self.assertEqual(rows[1]["status"], "FT")
        self.assertEqual(rows[1]["home_goals"], 1)

    def test_canonical_ids_match_catalog(self):
        cfg = LEAGUES["epl"]
        rows = parse_premier_league(SAMPLE, 2026)
        rows = canonicalize_fixture_rows(cfg, rows)
        ids = {team["name"]: team["api_id"] for team in team_catalog(cfg)}
        self.assertEqual(rows[0]["home_id"], ids["Arsenal"])
        self.assertEqual(rows[0]["away_id"], ids["Coventry City"])


if __name__ == "__main__":
    unittest.main()
