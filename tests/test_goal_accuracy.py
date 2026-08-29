import unittest
from predictor.goal_accuracy import (
    append_newly_resolved_forecasts, apply_total_calibration,
    build_goal_accuracy_state, fit_total_goals_calibration,
    poisson_over_probability,
)

class GoalAccuracyTests(unittest.TestCase):
    def test_frozen_snapshot_only(self):
        previous=[{"id":"m1","status":"scheduled","xg_home":1.6,"xg_away":1.1,"home":"a","away":"b"}]
        current=[{"id":"m1","status":"final","home_score":2,"away_score":1,"home":"a","away":"b"}]
        history=append_newly_resolved_forecasts([],previous,current,league="epl")
        self.assertEqual(len(history),1)
        self.assertAlmostEqual(history[0]["predicted_total"],2.7)
        self.assertEqual(history[0]["actual_total"],3.0)
        self.assertAlmostEqual(history[0]["error_actual_minus_predicted"],0.3)

    def test_no_post_result_leakage(self):
        previous=[{"id":"m1","status":"final","xg_home":1.6,"xg_away":1.1}]
        current=[{"id":"m1","status":"final","home_score":2,"away_score":1}]
        self.assertEqual(append_newly_resolved_forecasts([],previous,current,league="epl"),[])

    def test_calibrator_is_monotonic(self):
        history=[]
        for i in range(80):
            p=1.5+i/40
            a=1.8+(i/45)+(0.3 if i%7==0 else -0.1)
            history.append({"predicted_total":p,"actual_total":a})
        cal=fit_total_goals_calibration(history,min_matches=20)
        self.assertEqual(cal["status"],"calibrated")
        self.assertTrue(all(a<=b+1e-9 for a,b in zip(cal["y"],cal["y"][1:])))
        mapped=[apply_total_calibration(x,cal) for x in (1.8,2.2,2.8,3.2)]
        self.assertTrue(all(a<=b+1e-9 for a,b in zip(mapped,mapped[1:])))

    def test_poisson_over_probabilities_decline_with_line(self):
        self.assertGreater(poisson_over_probability(2.8,1.5),poisson_over_probability(2.8,2.5))
        self.assertGreater(poisson_over_probability(2.8,2.5),poisson_over_probability(2.8,3.5))

    def test_state_enriches_fixture(self):
        current=[{"id":"n1","status":"scheduled","xg_home":1.7,"xg_away":1.2}]
        history,calibration,accuracy=build_goal_accuracy_state("epl",[],[],current)
        self.assertEqual(history,[])
        self.assertAlmostEqual(current[0]["expected_total_goals_raw"],2.9)
        self.assertAlmostEqual(current[0]["expected_total_goals"],2.9)
        self.assertIn("2.5",current[0]["over_probabilities"])

if __name__=="__main__":
    unittest.main()
