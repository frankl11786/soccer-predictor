from __future__ import annotations

from math import exp, factorial, sqrt
from typing import Any, Iterable

OU_LINES = (1.5, 2.5, 3.5, 4.5)
MIN_CALIBRATION_MATCHES = 40
MAX_HISTORY = 2500


def num(value: Any) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return None if value != value else value


def raw_total(fixture: dict[str, Any]) -> float | None:
    home = next((num(fixture.get(k)) for k in ("expected_home_goals","lambda_home","xg_home") if num(fixture.get(k)) is not None), None)
    away = next((num(fixture.get(k)) for k in ("expected_away_goals","lambda_away","xg_away") if num(fixture.get(k)) is not None), None)
    if home is None or away is None:
        return None
    return max(0.05, home + away)


def is_final(fixture: dict[str, Any]) -> bool:
    return str(fixture.get("status") or "").lower() in {"final","finished","ft"}


def append_new_finals(
    history: list[dict[str, Any]],
    previous_fixtures: Iterable[dict[str, Any]],
    current_fixtures: Iterable[dict[str, Any]],
    *,
    league: str,
) -> list[dict[str, Any]]:
    previous = {str(f.get("id") or ""): f for f in previous_fixtures if f.get("id")}
    existing = {str(r.get("fixture_id") or "") for r in history}
    result = [dict(r) for r in history if isinstance(r, dict)]

    for current in current_fixtures:
        fid = str(current.get("id") or "")
        if not fid or fid in existing or not is_final(current):
            continue
        before = previous.get(fid)
        if not before or is_final(before):
            continue

        predicted = num(before.get("expected_total_goals"))
        if predicted is None:
            predicted = raw_total(before)
        hs, aw = num(current.get("home_score")), num(current.get("away_score"))
        if predicted is None or hs is None or aw is None:
            continue
        actual = hs + aw
        result.append({
            "fixture_id": fid,
            "league": league,
            "date": current.get("date") or current.get("kickoff"),
            "home": current.get("home"),
            "away": current.get("away"),
            "predicted_total": round(predicted, 4),
            "actual_total": round(actual, 1),
            "actual_home_goals": hs,
            "actual_away_goals": aw,
            "error_actual_minus_predicted": round(actual - predicted, 4),
            "absolute_error": round(abs(actual - predicted), 4),
            "source": "frozen_previous_published_snapshot",
        })
        existing.add(fid)
    return result[-MAX_HISTORY:]


def _pav(xs, ys, ws):
    blocks = []
    for x,y,w in zip(xs,ys,ws):
        blocks.append([x*w,y*w,w])
        while len(blocks) > 1 and blocks[-2][1]/blocks[-2][2] > blocks[-1][1]/blocks[-1][2]:
            a,b = blocks[-2],blocks[-1]
            blocks[-2:] = [[a[0]+b[0],a[1]+b[1],a[2]+b[2]]]
    return [b[0]/b[2] for b in blocks], [b[1]/b[2] for b in blocks]


def fit_calibration(history: Iterable[dict[str, Any]], min_matches: int = MIN_CALIBRATION_MATCHES) -> dict[str, Any]:
    rows = sorted(
        (num(r.get("predicted_total")), num(r.get("actual_total")))
        for r in history
        if num(r.get("predicted_total")) is not None and num(r.get("actual_total")) is not None
    )
    if len(rows) < min_matches:
        return {"status":"collecting","matches":len(rows),"min_matches":min_matches,"method":"identity_until_threshold","x":[],"y":[]}
    bins = min(12, max(5, round(len(rows)**0.5)))
    chunk = max(1, len(rows)//bins)
    bx,by,bw=[],[],[]
    for i in range(0,len(rows),chunk):
        sample=rows[i:i+chunk]
        bx.append(sum(p for p,_ in sample)/len(sample))
        by.append(sum(a for _,a in sample)/len(sample))
        bw.append(len(sample))
    xs,ys=_pav(bx,by,bw)
    return {"status":"calibrated","matches":len(rows),"min_matches":min_matches,
            "method":"frozen_out_of_sample_isotonic","x":[round(x,5) for x in xs],"y":[round(y,5) for y in ys]}


def apply_calibration(value: float, calibration: dict[str,Any]) -> float:
    if calibration.get("status") != "calibrated":
        return value
    xs,ys=calibration.get("x") or [],calibration.get("y") or []
    if not xs or len(xs)!=len(ys):
        return value
    if value <= xs[0]:
        return max(.05, ys[0] + value-xs[0])
    if value >= xs[-1]:
        return max(.05, ys[-1] + value-xs[-1])
    for i in range(1,len(xs)):
        if value <= xs[i]:
            frac=(value-xs[i-1])/max(1e-9,xs[i]-xs[i-1])
            return max(.05,ys[i-1]+frac*(ys[i]-ys[i-1]))
    return value


def poisson_over(lam: float, line: float) -> float:
    kmax=int(line)
    under=sum(exp(-lam)*(lam**k)/factorial(k) for k in range(kmax+1))
    return max(0.0,min(1.0,1-under))


def enrich_fixtures(fixtures: Iterable[dict[str,Any]], calibration: dict[str,Any]) -> None:
    for f in fixtures:
        raw=raw_total(f)
        if raw is None:
            continue
        final=apply_calibration(raw,calibration)
        f["expected_total_goals_raw"]=round(raw,3)
        f["expected_total_goals"]=round(final,2)
        f["total_goals_calibration_applied"]=calibration.get("status")=="calibrated"
        f["over_probabilities"]={str(line):round(poisson_over(final,line),4) for line in OU_LINES}


def metrics(history: Iterable[dict[str,Any]]) -> dict[str,Any]:
    rows=[r for r in history if num(r.get("predicted_total")) is not None and num(r.get("actual_total")) is not None]
    if not rows:
        return {"status":"collecting","matches":0,"headline_metric":"MAE"}
    p=[float(r["predicted_total"]) for r in rows]
    a=[float(r["actual_total"]) for r in rows]
    e=[x-y for x,y in zip(p,a)]
    ae=[abs(x) for x in e]

    bins=[]
    ranges=[(0,1.99),(2,2.49),(2.5,2.99),(3,3.49),(3.5,3.99),(4,99)]
    for lo,hi in ranges:
        subset=[r for r in rows if lo <= float(r["predicted_total"]) <= hi]
        if not subset: continue
        mp=sum(float(r["predicted_total"]) for r in subset)/len(subset)
        ma=sum(float(r["actual_total"]) for r in subset)/len(subset)
        bins.append({"range":f"{lo:.2f}-{hi:.2f}" if hi<90 else f"{lo:.2f}+",
                     "matches":len(subset),"mean_predicted":round(mp,3),"mean_actual":round(ma,3),
                     "bias_predicted_minus_actual":round(mp-ma,3)})

    ou={}
    for line in OU_LINES:
        probs=[poisson_over(float(r["predicted_total"]),line) for r in rows]
        actual=[1.0 if float(r["actual_total"])>line else 0.0 for r in rows]
        ou[str(line)]={"matches":len(rows),
                       "mean_predicted_over_probability":round(sum(probs)/len(probs),4),
                       "actual_over_rate":round(sum(actual)/len(actual),4),
                       "brier_score":round(sum((x-y)**2 for x,y in zip(probs,actual))/len(rows),4)}

    return {"status":"ready","matches":len(rows),"headline_metric":"MAE",
            "mae":round(sum(ae)/len(ae),3),
            "rmse":round(sqrt(sum(x*x for x in e)/len(e)),3),
            "bias_predicted_minus_actual":round(sum(e)/len(e),3),
            "mean_predicted":round(sum(p)/len(p),3),"mean_actual":round(sum(a)/len(a),3),
            "within_0_5":round(sum(x<=.5 for x in ae)/len(ae),4),
            "within_1_0":round(sum(x<=1 for x in ae)/len(ae),4),
            "calibration_bins":bins,"over_under":ou}
