import unittest

from predictor.config import LEAGUES
from predictor.data_prep import prepare_league
from predictor.identity import canonicalize_fixture_rows
from predictor.mls_schedule import _snapshot_fallback, parse_fixture_download
from predictor.openfootball import parse_premier_league


OPENFOOTBALL_RESULT_MIDDLE = """= England | Premier League 2025/26
▪ Regular Season - 1
Fri Aug 15 2025
  19:00   Liverpool  4-2 (1-0)  Bournemouth
                  (Example scorer 37')
Sat Aug 16
  12:30   Aston Villa  0-0 (0-0)  Newcastle United
"""

OPENFOOTBALL_VERSUS = """= English Premier League 2026/27
▪ Matchday 1
Fri Aug 21 2026
  20:00 Arsenal FC v Coventry City FC
Sat Aug 22
  15:00 Hull City AFC v Manchester United FC 1-2 (0-1)
"""

FIXTURE_DOWNLOAD_SAMPLE = [
    {
        "MatchNumber": 1,
        "RoundNumber": 1,
        "DateUtc": "2026-02-21 21:45:00Z",
        "Location": "TQL Stadium",
        "HomeTeam": "FC Cincinnati",
        "AwayTeam": "Atlanta United",
        "HomeTeamScore": 2,
        "AwayTeamScore": 0,
        "Winner": "FC Cincinnati",
    },
    {
        "MatchNumber": 2,
        "RoundNumber": 27,
        "DateUtc": "2026-09-20 00:30:00Z",
        "Location": "Energizer Park",
        "HomeTeam": "St. Louis CITY SC",
        "AwayTeam": "Toronto FC",
        "HomeTeamScore": None,
        "AwayTeamScore": None,
        "Winner": None,
    },
]


class SourceReliabilityTests(unittest.TestCase):
    def test_openfootball_result_middle_format(self):
        rows = parse_premier_league(OPENFOOTBALL_RESULT_MIDDLE, 2025)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["home_name"], "Liverpool")
        self.assertEqual(rows[0]["away_name"], "Bournemouth")
        self.assertEqual(rows[0]["home_goals"], 4)
        self.assertEqual(rows[0]["away_goals"], 2)
        self.assertEqual(rows[0]["round"], "Regular Season - 1")
        self.assertEqual(rows[1]["status"], "FT")

    def test_openfootball_versus_format(self):
        rows = parse_premier_league(OPENFOOTBALL_VERSUS, 2026)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["status"], "NS")
        self.assertEqual(rows[1]["status"], "FT")
        self.assertEqual(rows[1]["home_goals"], 1)

    def test_fixture_download_parser(self):
        rows = parse_fixture_download(FIXTURE_DOWNLOAD_SAMPLE, 2026)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["source"], "FixtureDownload")
        self.assertEqual(rows[0]["status"], "FT")
        self.assertEqual(rows[0]["home_goals"], 2)
        self.assertEqual(rows[1]["status"], "NS")
        self.assertEqual(rows[1]["round"], "Regular Season - 27")

    def test_published_snapshot_is_a_valid_emergency_mls_spine(self):
        rows = _snapshot_fallback(2026)
        self.assertEqual(len(rows), 510)
        canonical = canonicalize_fixture_rows(LEAGUES["mls"], rows)
        prepared = prepare_league(LEAGUES["mls"], canonical)
        self.assertEqual(len(prepared.current_fixtures), 510)
        self.assertGreaterEqual(len(prepared.history), 80)


if __name__ == "__main__":
    unittest.main()
