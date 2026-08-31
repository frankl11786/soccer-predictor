#!/usr/bin/env python3
import argparse,json
from datetime import datetime,timezone
from pathlib import Path
from predictor.fixture_reconcile import dt

def check(path):
    data=json.loads(Path(path).read_text()); errors=[]
    now=datetime.now(timezone.utc)
    if "accuracy" not in data: errors.append("missing accuracy payload")
    for f in data.get("fixtures") or []:
        status=str(f.get("status") or "").lower()
        if status=="final" and (f.get("home_score") is None or f.get("away_score") is None):
            errors.append(f"{f.get('id')}: final without score")
        raw=(f.get("expected_home_goals",f.get("lambda_home",f.get("xg_home"))),
             f.get("expected_away_goals",f.get("lambda_away",f.get("xg_away"))))
        if raw[0] is not None and raw[1] is not None and f.get("expected_total_goals") is None:
            errors.append(f"{f.get('id')}: missing expected_total_goals")
        when=dt(f.get("kickoff") or f.get("date"))
        if when and status=="scheduled" and (now-when.astimezone(timezone.utc)).total_seconds()>12*3600:
            errors.append(f"{f.get('id')}: stale scheduled fixture past kickoff")
    rec=(data.get("meta") or {}).get("result_reconciliation") or {}
    if rec.get("stale_past_unresolved",0):
        errors.append(f"{rec['stale_past_unresolved']} unresolved past fixture(s)")
    return errors

ap=argparse.ArgumentParser();ap.add_argument("paths",nargs="+");args=ap.parse_args()
all_errors=[]
for p in args.paths:
    e=check(p)
    if e: all_errors += [f"{p}: {x}" for x in e]
    else: print("OK",p)
if all_errors: raise SystemExit("\n".join(all_errors))
