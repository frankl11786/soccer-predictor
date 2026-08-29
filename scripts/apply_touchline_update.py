#!/usr/bin/env python3
from pathlib import Path
import shutil, sys

BUNDLE=Path(__file__).resolve().parents[1]

def insert_after(text,anchor,addition):
    if addition.strip() in text: return text
    pos=text.find(anchor)
    if pos<0: raise RuntimeError(f"Could not find anchor: {anchor!r}")
    pos+=len(anchor)
    return text[:pos]+addition+text[pos:]

def insert_before(text,anchor,addition):
    if addition.strip() in text: return text
    pos=text.find(anchor)
    if pos<0: raise RuntimeError(f"Could not find anchor: {anchor!r}")
    return text[:pos]+addition+text[pos:]

def main(repo):
    repo=repo.resolve()
    if not (repo/"predictor").exists():
        raise SystemExit(f"{repo} does not look like the Touchline Forecast repository.")

    for rel in ("predictor/goal_accuracy.py","tests/test_goal_accuracy.py","app/goal-accuracy.js","app/goal-accuracy.css","app/accuracy.html"):
        src,dst=BUNDLE/rel,repo/rel
        dst.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(src,dst)

    output=repo/"predictor/output.py"
    text=output.read_text(encoding="utf-8")
    text=insert_after(text,"from .simulate import SimulationResult\n","from .goal_accuracy import build_goal_accuracy_state\n")
    accuracy_call=(
        "    accuracy_history, total_goals_calibration, accuracy = build_goal_accuracy_state(\n"
        "        cfg.key,\n"
        "        previous.get(\"accuracy_history\") or [],\n"
        "        previous.get(\"fixtures\") or [],\n"
        "        simulation.fixtures,\n"
        "    )\n"
    )
    text=insert_before(text,"    generated = utc_now_iso()\n",accuracy_call)
    text=insert_before(text,'        "teams": teams,\n','        "accuracy": accuracy,\n        "accuracy_history": accuracy_history,\n')
    text=insert_after(text,'            "type": "Bayesian state-space Poisson with calibrated preseason transition",\n','            "total_goals_calibration": total_goals_calibration,\n')
    methodology=(
        '            "total_goals": {\n'
        '                "display": "expected_total_goals = calibrated(lambda_home + lambda_away)",\n'
        '                "raw_display": "expected_total_goals_raw = lambda_home + lambda_away",\n'
        '                "calibration": "Out-of-sample isotonic calibration trained only on frozen pre-match snapshots.",\n'
        '                "headline_accuracy_metric": "Mean absolute error (MAE)",\n'
        '                "market_influence": "None; prediction-market data remains comparison-only.",\n'
        '            },\n'
    )
    text=insert_after(text,'        "methodology": {\n',methodology)
    output.write_text(text,encoding="utf-8")

    index=repo/"app/index.html"
    if index.exists():
        html=index.read_text(encoding="utf-8")
        if "goal-accuracy.css" not in html:
            html=html.replace("</head>",'  <link rel="stylesheet" href="goal-accuracy.css">\n</head>')
        if "goal-accuracy.js" not in html:
            html=html.replace("</body>",'  <script src="goal-accuracy.js"></script>\n</body>')
        index.write_text(html,encoding="utf-8")

    workflow=repo/".github/workflows/update-forecast.yml"
    if workflow.exists():
        wf=workflow.read_text(encoding="utf-8")
        anchor='              if not data.get("fixtures") or not data.get("forecast"):\n                  raise SystemExit(f"Generated {league} snapshot is incomplete")\n'
        validation=(
            '              if not data.get("accuracy"):\n'
            '                  raise SystemExit(f"Generated {league} snapshot is missing accuracy data")\n'
            '              for fixture in data["fixtures"]:\n'
            '                  if fixture.get("xg_home") is not None and fixture.get("xg_away") is not None:\n'
            '                      if fixture.get("expected_total_goals") is None:\n'
            '                          raise SystemExit(f"Generated {league} fixture {fixture.get(\'id\')} is missing expected_total_goals")\n'
        )
        if "snapshot is missing accuracy data" not in wf and anchor in wf:
            wf=wf.replace(anchor,anchor+validation)
            workflow.write_text(wf,encoding="utf-8")

    print("Touchline total-goals + accuracy update applied.")
    print("Run: python -m unittest discover -s tests -v")
    print("Then rebuild EPL and MLS and open app/accuracy.html?league=epl or ?league=mls.")

if __name__=="__main__":
    if len(sys.argv)!=2: raise SystemExit("Usage: python scripts/apply_touchline_update.py /path/to/repository")
    main(Path(sys.argv[1]))
