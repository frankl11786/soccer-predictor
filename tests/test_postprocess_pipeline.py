import unittest
from datetime import datetime,timezone
from predictor.fixture_reconcile import norm,reconcile,recompute_table,coverage
from predictor.goal_accuracy import append_new_finals,fit_calibration,enrich_fixtures

def api(date,home,away,status,hs=None,aw=None):
 return {"fixture":{"id":1,"date":date,"status":{"short":status,"long":status}},"teams":{"home":{"name":home},"away":{"name":away}},"goals":{"home":hs,"away":aw}}

class PipelineTests(unittest.TestCase):
 def test_alias_and_final(self):
  self.assertEqual(norm("Nottm Forest"),norm("Nottingham Forest"))
  f=[{"id":"x","date":"2026-08-29","home":"liverpool","away":"nottingham-forest","status":"scheduled","xg_home":1.7,"xg_away":1.1}]
  s=reconcile(f,[api("2026-08-29T14:00:00+00:00","Liverpool","Nottingham Forest","FT",2,2)],now=datetime(2026,8,31,tzinfo=timezone.utc))
  self.assertEqual(f[0]["status"],"final");self.assertEqual((f[0]["home_score"],f[0]["away_score"]),(2,2));self.assertEqual(s["finalized"],1)
 def test_postponed(self):
  f=[{"id":"x","date":"2026-08-29","home":"liverpool","away":"nottingham-forest","status":"scheduled"}]
  reconcile(f,[api("2026-08-29T14:00:00+00:00","Liverpool","Nottingham Forest","PST")],now=datetime(2026,8,31,tzinfo=timezone.utc))
  self.assertEqual(f[0]["status"],"postponed")
 def test_frozen_accuracy_and_totals(self):
  prev=[{"id":"x","status":"scheduled","home":"a","away":"b","xg_home":1.5,"xg_away":1.2}]
  cur=[{"id":"x","status":"final","home":"a","away":"b","home_score":2,"away_score":1,"xg_home":1.5,"xg_away":1.2}]
  h=append_new_finals([],prev,cur,league="epl");self.assertEqual(len(h),1);self.assertAlmostEqual(h[0]["predicted_total"],2.7)
  cal=fit_calibration(h);enrich_fixtures(cur,cal);self.assertAlmostEqual(cur[0]["expected_total_goals"],2.7)
 def test_market_denominator(self):
  f=[{"status":"final","polymarket":{}},{"status":"scheduled","kalshi":{"x":1}},{"status":"scheduled"}]
  c=coverage(f);self.assertEqual(c["eligible_fixtures"],2);self.assertEqual(c["external_matched"],1)

if __name__=="__main__": unittest.main()
