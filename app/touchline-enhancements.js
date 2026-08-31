(() => {
  "use strict";
  let snapshots=null;

  const clean=s=>String(s||"").toLowerCase().replace(/[^a-z0-9]+/g," ").trim();
  const finalStatus=s=>["final","finished","ft"].includes(String(s||"").toLowerCase());
  const teamLabel=(teams,slug)=>(teams.find(t=>t.slug===slug)||{}).name || String(slug||"").replace(/-/g," ");

  async function load(){
    if(snapshots) return snapshots;
    const results=await Promise.all(["epl","mls"].map(async league=>{
      try{const r=await fetch(`data/${league}.json?ts=${Date.now()}`,{cache:"no-store"});return r.ok?await r.json():null}catch{return null}
    }));
    snapshots=results.filter(Boolean);
    return snapshots;
  }

  function allUpcoming(data){
    return (data.fixtures||[]).filter(f=>!finalStatus(f.status)&&!["postponed","cancelled","abandoned"].includes(String(f.status||"").toLowerCase()));
  }

  function findFixture(text,data){
    const hay=clean(text);
    let best=null,score=0;
    for(const f of data.fixtures||[]){
      const h=clean(teamLabel(data.teams||[],f.home)),a=clean(teamLabel(data.teams||[],f.away));
      if(hay.includes(h)&&hay.includes(a)){
        const s=h.length+a.length;
        if(s>score){score=s;best=f}
      }
    }
    return best;
  }

  function injectTotals(data){
    if(!location.hash.includes("/schedule")) return;
    const nodes=[...document.querySelectorAll("article, li, tr, [class*=card], [class*=fixture], [class*=match]")];
    for(const el of nodes){
      if(el.dataset.expectedTotalInjected) continue;
      const f=findFixture(el.textContent,data);
      if(!f || f.expected_total_goals==null) continue;
      const badge=document.createElement("div");
      badge.className="tf-expected-total";
      badge.innerHTML=`<span>Expected total goals</span><strong>${Number(f.expected_total_goals).toFixed(2)}</strong>`;
      el.appendChild(badge);
      el.dataset.expectedTotalInjected="1";
    }
  }

  function hideStaleUpcoming(data){
    if(!location.hash.includes("/schedule")) return;
    const active=[...document.querySelectorAll("button,a,[role=tab]")].some(x=>/^upcoming$/i.test((x.textContent||"").trim())&&(x.classList.contains("active")||x.getAttribute("aria-selected")==="true"));
    if(!active) return;
    const nodes=[...document.querySelectorAll("article, li, tr, [class*=card], [class*=fixture], [class*=match]")];
    for(const el of nodes){
      const f=findFixture(el.textContent,data);
      if(f && (finalStatus(f.status)||f.result_sync_warning)) el.style.display="none";
    }
  }

  async function run(){
    const sets=await load();
    for(const data of sets){injectTotals(data);hideStaleUpcoming(data)}
  }

  const obs=new MutationObserver(()=>requestAnimationFrame(run));
  document.addEventListener("DOMContentLoaded",()=>{obs.observe(document.body,{subtree:true,childList:true});run()});
  window.addEventListener("hashchange",run);
})();
