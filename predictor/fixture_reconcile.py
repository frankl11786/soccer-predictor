from __future__ import annotations

import json, re, unicodedata
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_URL="https://v3.football.api-sports.io/fixtures"
LEAGUE_IDS={"epl":39,"mls":253}

ALIASES={
 "nottm forest":"nottingham forest","nottingham forest":"nottingham forest",
 "man city":"manchester city","manchester city":"manchester city",
 "man utd":"manchester united","man united":"manchester united","manchester united":"manchester united",
 "afc bournemouth":"bournemouth","bournemouth":"bournemouth",
 "brighton hove albion":"brighton and hove albion","brighton and hove albion":"brighton and hove albion",
 "newcastle":"newcastle united","newcastle united":"newcastle united",
 "tottenham":"tottenham hotspur","tottenham hotspur":"tottenham hotspur",
 "inter miami":"inter miami cf","inter miami cf":"inter miami cf",
 "atlanta united fc":"atlanta united","atlanta united":"atlanta united",
 "ny red bulls":"new york red bulls","new york red bulls":"new york red bulls",
 "sporting kc":"sporting kansas city","sporting kansas city":"sporting kansas city",
 "montreal":"cf montreal","cf montreal":"cf montreal",
 "st louis city":"st louis city sc","st louis city sc":"st louis city sc",
}

def norm(value: Any) -> str:
    s=unicodedata.normalize("NFKD",str(value or "")).encode("ascii","ignore").decode().lower().replace("&"," and ")
    s=re.sub(r"\b(fc|afc|football club|soccer club)\b"," ",s)
    s=re.sub(r"[^a-z0-9]+"," ",s)
    s=re.sub(r"\s+"," ",s).strip()
    return ALIASES.get(s,s)

def dt(value: Any) -> datetime|None:
    if not value: return None
    s=str(value)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}",s):
        return datetime.fromisoformat(s+"T12:00:00+00:00")
    try:
        v=datetime.fromisoformat(s.replace("Z","+00:00"))
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    except ValueError:
        return None

def season(snapshot: dict[str,Any]) -> int:
    label=str((snapshot.get("meta") or {}).get("season") or "")
    m=re.search(r"20\d{2}",label)
    return int(m.group()) if m else datetime.now(timezone.utc).year

def fetch(league: str, season_year: int, key: str) -> list[dict[str,Any]]:
    req=Request(f"{API_URL}?"+urlencode({"league":LEAGUE_IDS[league],"season":season_year}),
                headers={"x-apisports-key":key})
    with urlopen(req,timeout=45) as response:
        payload=json.loads(response.read().decode())
    if payload.get("errors"):
        raise RuntimeError(f"API-Football error: {payload['errors']}")
    return payload.get("response") or []

def _row(r):
    fixture=r.get("fixture") or {}; teams=r.get("teams") or {}; goals=r.get("goals") or {}; status=fixture.get("status") or {}
    kickoff=dt(fixture.get("date"))
    return {"id":fixture.get("id"),"kickoff":fixture.get("date"),"date":kickoff.date().isoformat() if kickoff else None,
            "home":(teams.get("home") or {}).get("name"),"away":(teams.get("away") or {}).get("name"),
            "hn":norm((teams.get("home") or {}).get("name")),"an":norm((teams.get("away") or {}).get("name")),
            "status":status.get("short"),"status_long":status.get("long"),"hs":goals.get("home"),"as":goals.get("away")}

def mapped_status(code):
    code=str(code or "").upper()
    if code in {"FT","AET","PEN"}: return "final"
    if code in {"1H","HT","2H","ET","BT","P","LIVE","INT"}: return "live"
    if code=="PST": return "postponed"
    if code=="CANC": return "cancelled"
    if code in {"ABD","AWD","WO"}: return "abandoned"
    return "scheduled"

def reconcile(fixtures, api_response, now=None):
    now=now or datetime.now(timezone.utc)
    api=[_row(r) for r in api_response]
    stats={"matched":0,"finalized":0,"status_updates":0,"score_updates":0,"unmatched":0,"ambiguous":0,"stale_past_unresolved":0}
    for f in fixtures:
        fd=dt(f.get("kickoff") or f.get("date")); date=fd.date().isoformat() if fd else None
        h,a=norm(f.get("home")),norm(f.get("away"))
        candidates=[r for r in api if r["hn"]==h and r["an"]==a and r["date"]==date]
        if not candidates and fd:
            candidates=[r for r in api if r["hn"]==h and r["an"]==a and r["kickoff"] and abs((dt(r["kickoff"]).date()-fd.date()).days)<=1]
        if len(candidates)!=1:
            stats["ambiguous" if len(candidates)>1 else "unmatched"]+=1
            old=str(f.get("status") or "").lower()
            if fd and old not in {"final","postponed","cancelled","abandoned"}:
                if (now-fd.astimezone(timezone.utc)).total_seconds()>12*3600:
                    f["result_sync_warning"]=True
                    f["display_status"]="result_pending"
                    stats["stale_past_unresolved"]+=1
            continue
        r=candidates[0]; stats["matched"]+=1
        old=str(f.get("status") or "scheduled").lower()
        new=mapped_status(r["status"])
        f["api_football_fixture_id"]=r["id"]; f["provider_status"]=r["status"]; f["provider_status_long"]=r["status_long"]; f["result_source"]="API-Football"
        if r["kickoff"]: f["kickoff"]=r["kickoff"]
        if new!=old: f["status"]=new; stats["status_updates"]+=1
        if new=="final" and r["hs"] is not None and r["as"] is not None:
            prior=(f.get("home_score"),f.get("away_score"))
            f["home_score"],f["away_score"]=int(r["hs"]),int(r["as"])
            if prior!=(int(r["hs"]),int(r["as"])): stats["score_updates"]+=1
            if old!="final": stats["finalized"]+=1
        f.pop("result_sync_warning",None); f.pop("display_status",None)
    return stats

def recompute_table(fixtures,teams):
    rows={str(t["slug"]):{"team":t["slug"],"p":0,"w":0,"d":0,"l":0,"gf":0,"ga":0,"gd":0,"pts":0} for t in teams if t.get("slug")}
    for f in fixtures:
        if str(f.get("status") or "").lower()!="final" or f.get("home_score") is None or f.get("away_score") is None: continue
        h,a=rows.get(str(f.get("home"))),rows.get(str(f.get("away")))
        if h is None or a is None: continue
        hs,aw=int(f["home_score"]),int(f["away_score"])
        for row in (h,a): row["p"]+=1
        h["gf"]+=hs;h["ga"]+=aw;a["gf"]+=aw;a["ga"]+=hs
        if hs>aw: h["w"]+=1;a["l"]+=1;h["pts"]+=3
        elif hs<aw: a["w"]+=1;h["l"]+=1;a["pts"]+=3
        else: h["d"]+=1;a["d"]+=1;h["pts"]+=1;a["pts"]+=1
        h["gd"]=h["gf"]-h["ga"];a["gd"]=a["gf"]-a["ga"]
    return sorted(rows.values(),key=lambda x:(x["pts"],x["gd"],x["gf"],x["w"]),reverse=True)

def coverage(fixtures):
    eligible=[f for f in fixtures if str(f.get("status") or "").lower() in {"scheduled","live"} and not f.get("result_sync_warning")]
    external=sum(1 for f in eligible if f.get("polymarket") or f.get("kalshi"))
    return {"eligible_fixtures":len(eligible),"external_matched":external,
            "polymarket_matched":sum(1 for f in eligible if f.get("polymarket")),
            "kalshi_matched":sum(1 for f in eligible if f.get("kalshi")),
            "external_coverage":round(external/len(eligible),4) if eligible else 0.0}
