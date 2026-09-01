import json
import re
import unittest
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"


def schedule_rows(fixtures, filter_name):
    rows = [
        fixture
        for fixture in fixtures
        if filter_name == "all"
        or (filter_name == "upcoming" and fixture.get("status") != "final")
        or (filter_name == "completed" and fixture.get("status") == "final")
    ]
    return sorted(
        rows,
        key=lambda fixture: fixture["date"],
        reverse=filter_name == "completed",
    )


class ScheduleUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshots = {
            league: json.loads((APP / "data" / f"{league}.json").read_text())
            for league in ("epl", "mls")
        }
        cls.app_js = (APP / "app.js").read_text()
        cls.styles = (APP / "styles.css").read_text()
        cls.index = (APP / "index.html").read_text()

    def test_mls_and_epl_upcoming_filters_have_fixtures(self):
        self.assertEqual(len(schedule_rows(self.snapshots["mls"]["fixtures"], "upcoming")), 181)
        self.assertGreater(len(schedule_rows(self.snapshots["epl"]["fixtures"], "upcoming")), 0)

    def test_completed_and_all_filters_preserve_their_status_contract(self):
        for league, data in self.snapshots.items():
            fixtures = data["fixtures"]
            completed = schedule_rows(fixtures, "completed")
            all_rows = schedule_rows(fixtures, "all")
            with self.subTest(league=league):
                self.assertTrue(completed)
                self.assertTrue(all(row.get("status") == "final" for row in completed))
                self.assertEqual(len(all_rows), len(fixtures))
                self.assertEqual({row["id"] for row in all_rows}, {row["id"] for row in fixtures})

    def test_repeat_opponent_is_not_used_to_classify_future_fixture(self):
        fixtures = self.snapshots["mls"]["fixtures"]
        by_pair = defaultdict(list)
        for fixture in fixtures:
            by_pair[frozenset((fixture["home"], fixture["away"]))].append(fixture)

        repeated_future = next(
            future
            for meetings in by_pair.values()
            for future in meetings
            if future.get("status") != "final"
            and any(
                past.get("status") == "final" and past["date"] < future["date"]
                for past in meetings
            )
        )
        upcoming_ids = {row["id"] for row in schedule_rows(fixtures, "upcoming")}
        completed_ids = {row["id"] for row in schedule_rows(fixtures, "completed")}
        self.assertIn(repeated_future["id"], upcoming_ids)
        self.assertNotIn(repeated_future["id"], completed_ids)

    def test_native_rows_have_exact_ids_and_one_expected_total(self):
        detail = self.app_js.split("function fixtureDetailed", 1)[1].split(
            "function fixtureScoreModel", 1
        )[0]
        self.assertEqual(detail.count('data-fixture-id="${esc(f.id)}"'), 1)
        self.assertEqual(detail.count('class="fixture-expected-total"'), 1)
        self.assertEqual(detail.count("Expected Total Goals"), 1)

        helper = self.app_js.split("function fixtureExpectedTotal", 1)[1].split(
            "function tripletFromMarket", 1
        )[0]
        calibrated = helper.index("expected_total_goals'")
        raw = helper.index("expected_total_goals_raw'")
        xg = helper.index("xg_home")
        self.assertLess(calibrated, raw)
        self.assertLess(raw, xg)

    def test_expected_total_has_light_and_dark_theme_styles(self):
        self.assertIn(".fixture-expected-total {", self.styles)
        self.assertIn('html[data-theme="dark"] .fixture-expected-total {', self.styles)

    def test_no_enhancement_can_match_or_hide_schedule_containers(self):
        self.assertFalse((APP / "touchline-enhancements.js").exists())
        self.assertNotIn("touchline-enhancements.js", self.index)
        javascript = "\n".join(path.read_text() for path in APP.glob("*.js"))
        self.assertIsNone(re.search(r"\[class\*=(?:fixture|match)\]", javascript))
        self.assertNotIn("findFixture", javascript)
        self.assertNotIn("expectedTotalInjected", javascript)
        self.assertNotIn("MutationObserver", javascript)
        self.assertNotIn("style.display", javascript)


if __name__ == "__main__":
    unittest.main()
