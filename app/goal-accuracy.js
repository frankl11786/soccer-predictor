(() => {
  "use strict";
  const qs = new URLSearchParams(location.search);
  const league = (qs.get("league") || localStorage.getItem("touchlineLeague") || "epl").toLowerCase();
  const pct = v => v == null ? "—" : `${(Number(v)*100).toFixed(1)}%`;
  const num = (v,d=2) => v == null ? "—" : Number(v).toFixed(d);
  const esc = v => String(v ?? "").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"}[c]));
  const teamName = slug => String(slug||"").split("-").map(x=>x?x[0].toUpperCase()+x.slice(1):x).join(" ");

  function goalCard(f) {
    const home=f.expected_home_goals??f.lambda_home??f.xg_home;
    const away=f.expected_away_goals??f.lambda_away??f.xg_away;
    const total=f.expected_total_goals??((Number(home)||0)+(Number(away)||0));
    return `<div class="tg-goal-card"><div><span class="tg-eyebrow">EXPECTED GOALS</span><strong>${num(home)} – ${num(away)}</strong></div><div class="tg-total-emphasis"><span class="tg-eyebrow">EXPECTED TOTAL GOALS</span><strong>${num(total)}</strong></div></div>`;
  }

  function summaryCards(t) {
    if(!t||t.status!=="ready") return `<div class="tg-empty">Frozen pre-match forecasts are being collected. Accuracy metrics appear automatically as matches resolve.</div>`;
    return `<div class="tg-kpis">
      <article><span>Matches audited</span><strong>${t.matches}</strong></article>
      <article><span>Total-goals MAE</span><strong>${num(t.mae,3)}</strong></article>
      <article><span>RMSE</span><strong>${num(t.rmse,3)}</strong></article>
      <article><span>Model bias</span><strong>${Number(t.bias_predicted_minus_actual)>=0?"+":""}${num(t.bias_predicted_minus_actual,3)}</strong></article>
      <article><span>Within ±0.5</span><strong>${pct(t.within_0_5)}</strong></article>
      <article><span>Within ±1.0</span><strong>${pct(t.within_1_0)}</strong></article>
    </div>`;
  }

  function calibrationTable(t) {
    const rows=t?.calibration_bins||[]; if(!rows.length) return "";
    return `<section class="tg-panel"><div class="tg-panel-head"><div><span class="tg-eyebrow">CALIBRATION</span><h2>Does 2.8 really mean 2.8?</h2></div><p>Historical frozen predictions grouped by expected total.</p></div><div class="tg-table-wrap"><table><thead><tr><th>Predicted range</th><th>Matches</th><th>Predicted avg.</th><th>Actual avg.</th><th>Bias</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${esc(r.range)}</td><td>${r.matches}</td><td>${num(r.mean_predicted,3)}</td><td>${num(r.mean_actual,3)}</td><td>${Number(r.bias_predicted_minus_actual)>=0?"+":""}${num(r.bias_predicted_minus_actual,3)}</td></tr>`).join("")}</tbody></table></div></section>`;
  }

  function ouTable(t) {
    const rows=Object.entries(t?.over_under||{}); if(!rows.length) return "";
    return `<section class="tg-panel"><div class="tg-panel-head"><div><span class="tg-eyebrow">OVER / UNDER CALIBRATION</span><h2>Goal-line reliability</h2></div><p>Poisson total probability versus actual outcomes.</p></div><div class="tg-table-wrap"><table><thead><tr><th>Line</th><th>Matches</th><th>Predicted over</th><th>Actual over</th><th>Brier</th></tr></thead><tbody>${rows.map(([line,r])=>`<tr><td>Over ${esc(line)}</td><td>${r.matches??0}</td><td>${pct(r.mean_predicted_over_probability)}</td><td>${pct(r.actual_over_rate)}</td><td>${num(r.brier_score,4)}</td></tr>`).join("")}</tbody></table></div></section>`;
  }

  function historyTable(data) {
    const rows=[...(data.accuracy_history||[])].reverse().slice(0,100);
    return `<section class="tg-panel"><div class="tg-panel-head"><div><span class="tg-eyebrow">FROZEN FORECAST AUDIT</span><h2>Prediction vs. result</h2></div><p>Only forecasts published before the result was known are scored.</p></div><div class="tg-table-wrap"><table><thead><tr><th>Match</th><th>Predicted total</th><th>Actual total</th><th>Error</th><th>Abs. error</th></tr></thead><tbody>${rows.length?rows.map(r=>`<tr><td>${esc(teamName(r.home))} vs ${esc(teamName(r.away))}</td><td>${num(r.predicted_total)}</td><td>${num(r.actual_total,0)}</td><td>${Number(r.error_actual_minus_predicted)>=0?"+":""}${num(r.error_actual_minus_predicted)}</td><td>${num(r.absolute_error)}</td></tr>`).join(""):`<tr><td colspan="5">No resolved frozen forecasts yet.</td></tr>`}</tbody></table></div></section>`;
  }

  function upcoming(data) {
    const rows=(data.fixtures||[]).filter(f=>!["final","finished","ft"].includes(String(f.status||"").toLowerCase())&&f.expected_total_goals!=null).slice(0,12);
    return `<section class="tg-panel"><div class="tg-panel-head"><div><span class="tg-eyebrow">UPCOMING</span><h2>Absolute expected goals</h2></div><p>The calibrated total is displayed explicitly for every available match.</p></div><div class="tg-upcoming">${rows.length?rows.map(f=>`<article class="tg-match"><div><strong>${esc(teamName(f.home))}</strong><span>vs ${esc(teamName(f.away))}</span></div>${goalCard(f)}</article>`).join(""):`<div class="tg-empty">No upcoming fixtures with model goal rates are available.</div>`}</div></section>`;
  }

  async function renderAccuracyPage() {
    const root=document.querySelector("[data-touchline-accuracy]"); if(!root) return;
    try {
      const res=await fetch(`data/${league}.json`,{cache:"no-store"});
      if(!res.ok) throw new Error(`HTTP ${res.status}`);
      const data=await res.json(), t=data.accuracy?.total_goals;
      root.innerHTML=`<div class="tg-page-head"><div><span class="tg-eyebrow">${esc(data.meta?.name||league.toUpperCase())} · ${esc(data.meta?.season||"")}</span><h1>Forecast Accuracy</h1><p>Transparent, frozen pre-match auditing with total-goals calibration as a first-class model target.</p></div><nav><a href="?league=epl" class="${league==="epl"?"active":""}">Premier League</a><a href="?league=mls" class="${league==="mls"?"active":""}">MLS</a></nav></div>${summaryCards(t)}${upcoming(data)}${calibrationTable(t)}${ouTable(t)}${historyTable(data)}<section class="tg-method-note"><strong>Calibration integrity:</strong> ${esc(data.accuracy?.frozen_forecast_integrity?.note||"Pre-match snapshots only.")} Prediction-market prices remain comparison-only and do not force the model toward a market price.</section>`;
    } catch(err) {
      root.innerHTML=`<div class="tg-empty">Could not load forecast data: ${esc(err.message)}</div>`;
    }
  }
  window.TouchlineGoals={goalCard,renderAccuracyPage};
  document.addEventListener("DOMContentLoaded",renderAccuracyPage);
})();
