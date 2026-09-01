#!/usr/bin/env python3
from __future__ import annotations
import argparse,json
from pathlib import Path
from predictor.fixture_reconcile import fetch,reconcile,recompute_table,coverage
from predictor.goal_accuracy import append_new_finals,fit_calibration,enrich_fixtures,metrics

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--league",required=True,choices=["epl","mls"])
    ap.add_argument("--current",required=True)
    ap.add_argument("--previous")
    args=ap.parse_args()
    path=Path(args.current); current=json.loads(path.read_text()); previous={}
    if args.previous and Path(args.previous).exists():previous=json.loads(Path(args.previous).read_text())

    provider=fetch(args.league,current.get("fixtures") or [])
    stats=reconcile(current.get("fixtures") or [],provider)

    history=append_new_finals(previous.get("accuracy_history") or [],previous.get("fixtures") or [],current.get("fixtures") or [],league=args.league)
    cal=fit_calibration(history); enrich_fixtures(current.get("fixtures") or [],cal)
    current["accuracy_history"]=history
    current["accuracy"]={"total_goals":metrics(history),"calibration":cal,
      "frozen_forecast_integrity":{"method":"previous_published_snapshot_only","post_result_leakage":False}}
    current["current_table"]=recompute_table(current.get("fixtures") or [],current.get("teams") or [])
    meta=current.setdefault("meta",{})
    meta["completed_matches"]=sum(1 for f in current.get("fixtures") or [] if str(f.get("status") or "").lower()=="final")
    meta["result_reconciliation"]={"source":"ESPN public scoreboard",**stats}
    meta["external_market_coverage"]=coverage(current.get("fixtures") or [])
    meta["forecast_freshness"]={"status":"needs_refit" if stats["finalized"] else "current",
      "reason":f"{stats['finalized']} completed fixture(s) were discovered after model generation." if stats["finalized"] else None}
    current.setdefault("methodology",{})["total_goals"]={
      "display":"expected_total_goals = calibrated(lambda_home + lambda_away)",
      "calibration":"Frozen out-of-sample monotonic calibration after 40 resolved forecasts.",
      "headline_accuracy_metric":"Mean absolute error (MAE)",
      "market_influence":"None; prediction markets remain comparison-only."}
    path.write_text(json.dumps(current,separators=(",",":")))
    print(json.dumps({"league":args.league,"result_reconciliation":stats,
      "accuracy_matches":len(history),"calibration":cal["status"],"market_coverage":meta["external_market_coverage"]},indent=2))
if __name__=="__main__":main()
