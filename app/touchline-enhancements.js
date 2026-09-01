(() => {
  "use strict";

  let snapshots = null;
  const terminal = new Set(["final","finished","ft","postponed","cancelled","abandoned"]);

  const clean = value => String(value || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
  const teamLabel = (teams, slug) => (teams.find(t => t.slug === slug) || {}).name || String(slug || "").replace(/-/g, " ");

  async function load() {
    if (snapshots) return snapshots;
    const rows = await Promise.all(["epl","mls"].map(async league => {
      try {
        const response = await fetch(`data/${league}.json?ts=${Date.now()}`, {cache:"no-store"});
        return response.ok ? await response.json() : null;
      } catch {
        return null;
      }
    }));
    snapshots = rows.filter(Boolean);
    return snapshots;
  }

  function findFixture(text, data) {
    const hay = clean(text);
    let best = null, score = 0;
    for (const f of data.fixtures || []) {
      const home = clean(teamLabel(data.teams || [], f.home));
      const away = clean(teamLabel(data.teams || [], f.away));
      if (home && away && hay.includes(home) && hay.includes(away)) {
        const next = home.length + away.length;
        if (next > score) { score = next; best = f; }
      }
    }
    return best;
  }

  function expectedTotal(f) {
    const calibrated = Number(f?.expected_total_goals);
    if (Number.isFinite(calibrated)) return calibrated;
    const lambda = Number(f?.goal_totals?.model?.lambda);
    if (Number.isFinite(lambda)) return lambda;
    const home = Number(f?.xg_home), away = Number(f?.xg_away);
    return Number.isFinite(home) && Number.isFinite(away) ? home + away : null;
  }

  function upcomingActive() {
    if (!location.hash.includes("/schedule")) return false;
    return [...document.querySelectorAll("button,a,[role=tab]")].some(el => {
      if (!/^upcoming$/i.test((el.textContent || "").trim())) return false;
      return el.classList.contains("active") || el.getAttribute("aria-selected") === "true";
    });
  }

  function enhance(data) {
    if (!location.hash.includes("/schedule")) return;
    const nodes = [...document.querySelectorAll("article, li, tr, [class*=fixture], [class*=match]")];

    for (const el of nodes) {
      const f = findFixture(el.textContent, data);
      if (!f) continue;

      if (upcomingActive() && terminal.has(String(f.status || "").toLowerCase())) {
        el.style.display = "none";
        continue;
      }

      const total = expectedTotal(f);
      if (total === null || el.dataset.expectedTotalInjected) continue;
      const row = document.createElement("div");
      row.className = "tf-expected-total";
      row.innerHTML = `<span>Expected total goals</span><strong>${total.toFixed(2)}</strong>`;
      el.appendChild(row);
      el.dataset.expectedTotalInjected = "1";
    }
  }

  async function run() {
    const sets = await load();
    for (const data of sets) enhance(data);
  }

  const observer = new MutationObserver(() => requestAnimationFrame(run));
  document.addEventListener("DOMContentLoaded", () => {
    observer.observe(document.body, {subtree:true, childList:true});
    run();
  });
  window.addEventListener("hashchange", run);
})();
