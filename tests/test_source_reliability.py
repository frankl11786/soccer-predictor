import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd

from predictor.asa import _normalize_game_status
from predictor.config import LEAGUES
from predictor.data_prep import prepare_league
from predictor.epl_schedule import parse_fixture_download as parse_epl_fixture_download
from predictor.fixture_overrides import apply_fixture_status_overrides
from predictor.identity import canonicalize_fixture_rows, team_catalog
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

EPL_FIXTURE_DOWNLOAD_SAMPLE = [
    {
        "MatchNumber": 27,
        "RoundNumber": 3,
        "DateUtc": "2026-09-05 14:00:00Z",
        "Location": "The City Ground",
        "HomeTeam": "Nott'm Forest",
        "AwayTeam": "Spurs",
        "HomeTeamScore": 0,
        "AwayTeamScore": 0,
        "Winner": None,
    },
    {
        "MatchNumber": 30,
        "RoundNumber": 3,
        "DateUtc": "2026-09-06 15:30:00Z",
        "Location": "Emirates Stadium",
        "HomeTeam": "Arsenal",
        "AwayTeam": "Chelsea",
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

    def test_epl_fixture_download_parser_and_aliases(self):
        rows = parse_epl_fixture_download(EPL_FIXTURE_DOWNLOAD_SAMPLE, 2026)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["source"], "FixtureDownload")
        self.assertEqual(rows[0]["status"], "FT")
        self.assertEqual(rows[0]["home_goals"], 0)
        self.assertEqual(rows[1]["status"], "NS")

        canonical = canonicalize_fixture_rows(LEAGUES["epl"], rows)
        current_ids = {team["api_id"] for team in team_catalog(LEAGUES["epl"])}
        self.assertTrue(
            all(
                row["home_id"] in current_ids and row["away_id"] in current_ids
                for row in canonical
            )
        )

    def test_postponed_mls_status_is_preserved_when_a_feed_reports_it(self):
        kickoff = datetime(2026, 9, 5, 23, 30, tzinfo=timezone.utc)
        self.assertEqual(_normalize_game_status("Postponed", False, kickoff), "PST")

        rows = _snapshot_fallback(2026)
        target = next(
            row
            for row in rows
            if row["home_name"] == "FC Cincinnati"
            and row["away_name"] == "D.C. United"
        )
        postponed = {
            **target,
            "fixture_id": "asa-postponed-regression",
            "source": "American Soccer Analysis",
            "status": "PST",
            "status_long": "Postponed",
            "home_goals": None,
            "away_goals": None,
        }
        canonical = canonicalize_fixture_rows(LEAGUES["mls"], rows + [postponed])
        prepared = prepare_league(LEAGUES["mls"], canonical)
        current = prepared.current_fixtures.to_dict("records")
        overlaid = next(
            row
            for row in current
            if row["home_name"] == "FC Cincinnati"
            and row["away_name"] == "D.C. United"
            and str(row["date"]).startswith("2026-09-05")
        )
        self.assertEqual(overlaid["status"], "PST")
        self.assertEqual(overlaid["status_long"], "Postponed")
        self.assertEqual(overlaid["status_source"], "American Soccer Analysis")

    def test_official_override_handles_postponement_missing_from_all_feeds(self):
        prepared = SimpleNamespace(
            current_fixtures=pd.DataFrame(
                [
                    {
                        "season": 2026,
                        "date": "2026-09-05T23:30:00Z",
                        "timestamp": 1788651000,
                        "home_name": "FC Cincinnati",
                        "away_name": "D.C. United",
                        "status": "NS",
                        "status_long": "Scheduled",
                    }
                ]
            )
        )
        metadata = apply_fixture_status_overrides(prepared, LEAGUES["mls"])
        row = prepared.current_fixtures.iloc[0]
        self.assertEqual(metadata["configured"], 1)
        self.assertEqual(metadata["applied"], 1)
        self.assertEqual(row["status"], "PST")
        self.assertEqual(row["status_long"], "Postponed")
        self.assertEqual(row["status_source"], "Official Status Override")

    def test_official_override_does_not_follow_a_rescheduled_date(self):
        prepared = SimpleNamespace(
            current_fixtures=pd.DataFrame(
                [
                    {
                        "season": 2026,
                        "date": "2026-09-10T23:30:00Z",
                        "timestamp": 1789083000,
                        "home_name": "FC Cincinnati",
                        "away_name": "D.C. United",
                        "status": "NS",
                        "status_long": "Scheduled",
                    }
                ]
            )
        )
        metadata = apply_fixture_status_overrides(prepared, LEAGUES["mls"])
        row = prepared.current_fixtures.iloc[0]
        self.assertEqual(metadata["configured"], 1)
        self.assertEqual(metadata["applied"], 0)
        self.assertEqual(metadata["unmatched"], 1)
        self.assertEqual(row["status"], "NS")

    def test_published_snapshot_is_a_valid_emergency_mls_spine(self):
        rows = _snapshot_fallback(2026)
        self.assertEqual(len(rows), 510)
        canonical = canonicalize_fixture_rows(LEAGUES["mls"], rows)
        prepared = prepare_league(LEAGUES["mls"], canonical)
        self.assertEqual(len(prepared.current_fixtures), 510)
        self.assertGreaterEqual(len(prepared.history), 80)


if __name__ == "__main__":
    unittest.main()
