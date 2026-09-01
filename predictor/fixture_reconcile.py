from __future__ import annotations
import json, re, unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard"
ESPN_LEAGUES = {"epl": "eng.1", "mls": "usa.1"}

ALIASES = {
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

def dt(value: Any):
    if not value: return None
    s=str(value)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}",s):
        return datetime.fromisoformat(s+"T12:00:00+00:00")
    try:
        out=datetime.fromisoformat(s.replace("Z","+00:00"))
        return out if out.tzinfo else out.replace(tzinfo=timezone.utc)
    except ValueError:
        return None

def fetch(league: str, fixtures=None, now=None):
    if league not in ESPN_LEAGUES:
        raise ValueError(f"Unsupported league: {league}")
    now=now or datetime.now(timezone.utc)
    unresolved=[]
    for f in fixtures or []:
        status=str(f.get("status") or "scheduled").lower()
        when=dt(f.get("kickoff") or f.get("date"))
        if when and status not in {"final","postponed","cancelled","abandoned"} and when <= now+timedelta(days=2):
            unresolved.append(when)
    start=max(min(unresolved,default=now-timedelta(days=14))-timedelta(days=2), now-timedelta(days=45))
    end=now+timedelta(days=3)

    events=[]; cursor=start
    while cursor.date() <= end.date():
        chunk_end=min(cursor+timedelta(days=13),end)
        dates=f"{cursor:%Y%m%d}-{chunk_end:%Y%m%d}"
        url=ESPN_URL.format(league=ESPN_LEAGUES[league])+"?"+urlencode({"dates":dates,"limit":500})
        req=Request(url,headers={"User-Agent":"TouchlineForecast/1.0"})
        with urlopen(req,timeout=30) as resp:
            payload=json.loads(resp.read().decode("utf-8"))
        events.extend(payload.get("events") or [])
        cursor=chunk_end+timedelta(days=1)
    return list({str(e.get("id")):e for e in events if e.get("id")}.values())

def _row(event):
    comp=(event.get("competitions") or [{}])[0]
    competitors=comp.get("competitors") or []
    home=next((c for c in competitors if c.get("homeAway")=="home"),{})
    away=next((c for c in competitors if c.get("homeAway")=="away"),{})
    stype=((comp.get("status") or event.get("status") or {}).get("type") or {})
    def score(c):
        try:return int(float(c.get("score")))
        except (TypeError,ValueError):return None
    name=lambda c:(c.get("team") or {}).get("displayName") or (c.get("team") or {}).get("shortDisplayName")
    event_dt=dt(event.get("date"))
    return {
        "id":event.get("id"),"kickoff":event.get("date"),
        "date":event_dt.date().isoformat() if event_dt else None,
        "home":name(home),"away":name(away),"hn":norm(name(home)),"an":norm(name(away)),
        "completed":bool(stype.get("completed")),"state":str(stype.get("state") or "").lower(),
        "detail":str(stype.get("detail") or stype.get("shortDetail") or ""),
        "name":str(stype.get("name") or ""),"hs":score(home),"as":score(away),
    }

def mapped_status(r):
    detail=(r.get("detail") or "").lower(); name=(r.get("name") or "").lower()
    if r.get("completed"): return "final"
    if "postpon" in detail or "postpon" in name:return "postponed"
    if "cancel" in detail or "cancel" in name:return "cancelled"
    if "abandon" in detail or "abandon" in name:return "abandoned"
    if r.get("state")=="in":return "live"
    return "scheduled"

def reconcile(fixtures, provider_response, now=None):
    now=now or datetime.now(timezone.utc)
    provider=[_row(x) for x in provider_response]
    stats={"matched":0,"finalized":0,"status_updates":0,"score_updates":0,"unmatched":0,"ambiguous":0,"stale_past_unresolved":0}
    for f in fixtures:
        fd=dt(f.get("kickoff") or f.get("date")); day=fd.date().isoformat() if fd else None
        h,a=norm(f.get("home")),norm(f.get("away"))
        c=[r for r in provider if r["hn"]==h and r["an"]==a and r["date"]==day]
        if not c and fd:
            c=[r for r in provider if r["hn"]==h and r["an"]==a and r["kickoff"] and abs((dt(r["kickoff"]).date()-fd.date()).days)<=1]
        if len(c)!=1:
            stats["ambiguous" if len(c)>1 else "unmatched"]+=1
            old=str(f.get("status") or "").lower()
            if fd and old not in {"final","postponed","cancelled","abandoned"} and (now-fd.astimezone(timezone.utc)).total_seconds()>43200:
                f["result_sync_warning"]=True; f["display_status"]="result_pending"; stats["stale_past_unresolved"]+=1
            continue
        r=c[0]; stats["matched"]+=1; old=str(f.get("status") or "scheduled").lower(); new=mapped_status(r)
        f["espn_fixture_id"]=r["id"]; f["provider_status"]=r["detail"] or r["state"]; f["result_source"]="ESPN"
        if r["kickoff"]:f["kickoff"]=r["kickoff"]
        if new!=old:f["status"]=new;stats["status_updates"]+=1
        if new=="final" and r["hs"] is not None and r["as"] is not None:
            prior=(f.get("home_score"),f.get("away_score")); f["home_score"],f["away_score"]=r["hs"],r["as"]
            if prior!=(r["hs"],r["as"]):stats["score_updates"]+=1
            if old!="final":stats["finalized"]+=1
        f.pop("result_sync_warning",None);f.pop("display_status",None)
    return stats

def recompute_table(fixtures,teams):
    rows={str(t["slug"]):{"team":t["slug"],"p":0,"w":0,"d":0,"l":0,"gf":0,"ga":0,"gd":0,"pts":0} for t in teams if t.get("slug")}
    for f in fixtures:
        if str(f.get("status") or "").lower()!="final" or f.get("home_score") is None or f.get("away_score") is None:continue
        h,a=rows.get(str(f.get("home"))),rows.get(str(f.get("away")))
        if h is None or a is None:continue
        hs,aw=int(f["home_score"]),int(f["away_score"]);h["p"]+=1;a["p"]+=1;h["gf"]+=hs;h["ga"]+=aw;a["gf"]+=aw;a["ga"]+=hs
        if hs>aw:h["w"]+=1;a["l"]+=1;h["pts"]+=3
        elif hs<aw:a["w"]+=1;h["l"]+=1;a["pts"]+=3
        else:h["d"]+=1;a["d"]+=1;h["pts"]+=1;a["pts"]+=1
        h["gd"]=h["gf"]-h["ga"];a["gd"]=a["gf"]-a["ga"]
    return sorted(rows.values(),key=lambda x:(x["pts"],x["gd"],x["gf"],x["w"]),reverse=True)

def coverage(fixtures):
    eligible=[f for f in fixtures if str(f.get("status") or "").lower() in {"scheduled","live"} and not f.get("result_sync_warning")]
    external=sum(1 for f in eligible if f.get("polymarket") or f.get("kalshi"))
    return {"eligible_fixtures":len(eligible),"external_matched":external,
            "polymarket_matched":sum(1 for f in eligible if f.get("polymarket")),
            "kalshi_matched":sum(1 for f in eligible if f.get("kalshi")),
            "external_coverage":round(external/len(eligible),4) if eligible else 0.0}
