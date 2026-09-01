import unittest

from predictor.config import LEAGUES
from predictor.data_prep import _dedupe
from predictor.football_data import parse_epl_results
from predictor.identity import canonicalize_fixture_rows, team_catalog
from predictor.openfootball import parse_premier_league


SAMPLE = """= English Premier League 2026/27
▪ Matchday 1
Fri Aug 21 2026
  20:00 Arsenal FC v Coventry City FC
Sat Aug 22
  15:00 Hull City AFC v Manchester United FC 1-2 (0-1)
"""

FOOTBALL_DATA_SAMPLE = """Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR
E0,29/08/2026,12:30,Liverpool,Nott'm Forest,2,2,D
"""

STALE_AUGUST_SCHEDULE = """= English Premier League 2026/27
Fri Aug 28 2026
  20:00 Crystal Palace FC v Manchester City FC
Sat Aug 29
  12:30 Liverpool FC v Nottingham Forest FC
  15:00 AFC Bournemouth v Everton FC
  15:00 Coventry City FC v Hull City AFC
  17:30 Tottenham Hotspur FC v Newcastle United FC
Sun Aug 30
  14:00 Sunderland AFC v Fulham FC
  14:00 Chelsea FC v Brighton & Hove Albion FC
  14:00 Leeds United FC v Brentford FC
  16:30 Manchester United FC v Ipswich Town FC
"""

FOOTBALL_DATA_AUGUST_RESULTS = """Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR
E0,28/08/2026,20:00,Crystal Palace,Man City,1,4,A
E0,29/08/2026,12:30,Liverpool,Nott'm Forest,2,2,D
E0,29/08/2026,15:00,Bournemouth,Everton,1,1,D
E0,29/08/2026,15:00,Coventry,Hull,0,1,A
E0,29/08/2026,17:30,Tottenham,Newcastle,0,2,A
E0,30/08/2026,14:00,Chelsea,Brighton,4,3,H
E0,30/08/2026,14:00,Leeds,Brentford,1,1,D
E0,30/08/2026,14:00,Sunderland,Fulham,1,0,H
E0,30/08/2026,16:30,Man United,Ipswich,5,2,H
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

    def test_football_data_epl_result_parser(self):
        rows = parse_epl_results(FOOTBALL_DATA_SAMPLE, 2026)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "Football-Data.co.uk")
        self.assertEqual(rows[0]["date"], "2026-08-29T11:30:00Z")
        self.assertEqual(rows[0]["home_name"], "Liverpool")
        self.assertEqual(rows[0]["away_name"], "Nott'm Forest")
        self.assertEqual(rows[0]["status"], "FT")
        self.assertEqual((rows[0]["home_goals"], rows[0]["away_goals"]), (2, 2))

    def test_football_data_final_replaces_openfootball_schedule(self):
        scheduled = parse_premier_league(
            """= English Premier League 2026/27
Sat Aug 29 2026
  12:30 Liverpool FC v Nottingham Forest FC
""",
            2026,
        )
        final = parse_epl_results(FOOTBALL_DATA_SAMPLE, 2026)
        canonical = canonicalize_fixture_rows(LEAGUES["epl"], scheduled + final)
        rows = _dedupe(canonical)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "Football-Data.co.uk")
        self.assertEqual(rows[0]["status"], "FT")
        self.assertEqual((rows[0]["home_goals"], rows[0]["away_goals"]), (2, 2))

    def test_august_28_to_30_results_do_not_remain_scheduled(self):
        scheduled = parse_premier_league(STALE_AUGUST_SCHEDULE, 2026)
        finals = parse_epl_results(FOOTBALL_DATA_AUGUST_RESULTS, 2026)
        canonical = canonicalize_fixture_rows(LEAGUES["epl"], scheduled + finals)
        rows = _dedupe(canonical)

        self.assertEqual(len(rows), 9)
        self.assertTrue(all(row["status"] == "FT" for row in rows))
        self.assertTrue(all(row["source"] == "Football-Data.co.uk" for row in rows))


if __name__ == "__main__":
    unittest.main()
