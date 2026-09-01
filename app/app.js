/* Touchline Forecast — dependency-free SPA */
const state = {
  league: localStorage.getItem('tf-league') || 'epl',
  datasets: {},
  data: null,
  sortKey: null,
  sortDir: -1,
  scheduleFilter: 'upcoming',
  scheduleTeam: 'all',
  matchupA: null,
  matchupB: null,
  venue: 'a-home',
  raceExpanded: {},
  newsFilter: 'all',
  themePreference: localStorage.getItem('tf-theme') || 'system',
};

const main = document.getElementById('main');
const searchDialog = document.getElementById('search-dialog');
const teamSearch = document.getElementById('team-search');
const searchResults = document.getElementById('search-results');
const themeToggle = document.getElementById('theme-toggle');

const esc = (value = '') => String(value).replace(/[&<>'"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));
const pct = (v, digits = 0) => `${(Number(v || 0) * 100).toFixed(digits)}%`;
const probPct = (v, digits = 1) => {
  const value = Number(v || 0);
  if (value === 0) return '0%';
  const percentage = value * 100;
  if (percentage < 0.1) return '<0.1%';
  if (percentage >= 99.95) return '100%';
  return `${percentage.toFixed(digits)}%`;
};
const signedPct = v => `${v >= 0 ? '+' : ''}${(Number(v || 0) * 100).toFixed(1)}%`;
const dateText = iso => new Intl.DateTimeFormat('en-US', {month:'short', day:'numeric', year:'numeric'}).format(new Date(`${iso}T12:00:00`));
const compactDate = iso => new Intl.DateTimeFormat('en-US', {month:'short', day:'numeric'}).format(new Date(`${iso}T12:00:00`));
const kickoffText = value => {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return new Intl.DateTimeFormat('en-US', {month:'short', day:'numeric', year:'numeric', hour:'numeric', minute:'2-digit', timeZoneName:'short'}).format(date);
};
const clamp = (v, min, max) => Math.max(min, Math.min(max, v));


function resolvedTheme(preference = state.themePreference) {
  if (preference === 'system') {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  return preference;
}

function updateThemeControl() {
  if (!themeToggle) return;
  const current = document.documentElement.dataset.theme || resolvedTheme();
  const dark = current === 'dark';
  themeToggle.setAttribute('aria-pressed', String(dark));
  themeToggle.setAttribute('aria-label', dark ? 'Switch to light mode' : 'Switch to dark mode');
  themeToggle.title = dark ? 'Switch to light mode' : 'Switch to dark mode';
  const icon = themeToggle.querySelector('.theme-icon');
  const label = themeToggle.querySelector('.theme-label');
  if (icon) icon.textContent = dark ? '☀' : '☾';
  if (label) label.textContent = dark ? 'Light' : 'Dark';
}

function applyTheme(preference, persist = true) {
  state.themePreference = preference;
  const theme = resolvedTheme(preference);
  document.documentElement.dataset.theme = theme;
  document.documentElement.dataset.themePreference = preference;
  if (persist) localStorage.setItem('tf-theme', preference);
  updateThemeControl();
}

function toggleTheme() {
  const current = document.documentElement.dataset.theme || resolvedTheme();
  const next = current === 'dark' ? 'light' : 'dark';
  applyTheme(next);
  toast(`${next === 'dark' ? 'Dark' : 'Light'} mode enabled`);
}

function teamMap() { return Object.fromEntries(state.data.teams.map(t => [t.slug, t])); }
function forecastMap() { return Object.fromEntries(state.data.forecast.map(f => [f.team, f])); }
function tableMap() { return Object.fromEntries(state.data.current_table.map(r => [r.team, r])); }
function team(slug) { return teamMap()[slug]; }
function forecast(slug) { return forecastMap()[slug]; }
function current(slug) { return tableMap()[slug] || {p:0,w:0,d:0,l:0,gf:0,ga:0,gd:0,pts:0}; }
function fixtureById(id) { return state.data.fixtures.find(f => String(f.id) === String(id)); }
function outcomeKey() { return state.league === 'epl' ? 'title' : 'champion'; }
function outcomeLabel() { return state.league === 'epl' ? 'Title' : 'MLS Cup'; }
function marketProbability(row) { return row?.market === null || row?.market === undefined ? null : Number(row.market); }
function kalshiProbability(row) { return row?.kalshi === null || row?.kalshi === undefined ? null : Number(row.kalshi); }
function consensusProbability(row) { return row?.market_consensus === null || row?.market_consensus === undefined ? null : Number(row.market_consensus); }
function hasMarket(row) { return marketProbability(row) !== null && Number.isFinite(marketProbability(row)); }
function hasKalshi(row) { return kalshiProbability(row) !== null && Number.isFinite(kalshiProbability(row)); }
function hasConsensus(row) { return consensusProbability(row) !== null && Number.isFinite(consensusProbability(row)); }
function marketEventUrl(details) { return details?.event_slug ? `https://polymarket.com/event/${encodeURIComponent(details.event_slug)}` : ''; }
function kalshiEventUrl(details) { return details?.event_url || ''; }
function marketUpdated(details) { return details?.updated_at ? kickoffText(details.updated_at) : 'Latest nightly snapshot'; }
function money(value) {
  const number = Number(value || 0);
  if (!number) return '—';
  return new Intl.NumberFormat('en-US', {style:'currency', currency:'USD', notation:'compact', maximumFractionDigits:1}).format(number);
}
function fixtureMarket(f) { return f?.polymarket?.probabilities ? f.polymarket : null; }
function fixtureKalshi(f) { return f?.kalshi?.probabilities ? f.kalshi : null; }
function fixtureConsensus(f) { return f?.market_consensus?.probabilities ? f.market_consensus : null; }
function fixtureExpectedTotal(f) {
  for (const key of ['expected_total_goals','expected_total_goals_raw']) {
    const raw=f?.[key];
    if (raw===null||raw===undefined||raw==='') continue;
    const value=Number(raw);
    if (Number.isFinite(value)) return value;
  }
  const home=Number(f?.xg_home), away=Number(f?.xg_away);
  return Number.isFinite(home)&&Number.isFinite(away) ? home+away : null;
}
function tripletFromMarket(market, digits = 1) {
  if (!market?.probabilities) return 'No exact market';
  const p = market.probabilities;
  return `${pct(p.home,digits)} · ${pct(p.draw,digits)} · ${pct(p.away,digits)}`;
}
function marketTriplet(f, digits = 1) { return tripletFromMarket(fixtureMarket(f), digits); }
function kalshiTriplet(f, digits = 1) { return tripletFromMarket(fixtureKalshi(f), digits); }
function consensusTriplet(f, digits = 1) { return tripletFromMarket(fixtureConsensus(f), digits); }
function marketOutcomeExplainer(key, label, model, poly, kalshi, consensus) {
  const modelProbability = Number(model?.[key]);
  const polyProbability = poly?.probabilities?.[key] === null || poly?.probabilities?.[key] === undefined ? null : Number(poly.probabilities[key]);
  const kalshiProbabilityValue = kalshi?.probabilities?.[key] === null || kalshi?.probabilities?.[key] === undefined ? null : Number(kalshi.probabilities[key]);
  const consensusProbabilityValue = consensus?.probabilities?.[key] === null || consensus?.probabilities?.[key] === undefined ? null : Number(consensus.probabilities[key]);

  if (!Number.isFinite(modelProbability) || !Number.isFinite(consensusProbabilityValue)) return '';

  const marketGap = consensusProbabilityValue - modelProbability;
  const absGap = Math.abs(marketGap);
  const subject = key === 'draw' ? 'the draw' : `${String(label).replace(/\s+win$/i, '')} winning`;

  let lead;
  if (absGap < 0.02) {
    lead = `The markets and our model are closely aligned on ${subject}.`;
  } else if (marketGap > 0) {
    const intensity = absGap >= 0.10 ? 'much more likely' : absGap >= 0.05 ? 'more likely' : 'somewhat more likely';
    lead = `The prediction markets see ${subject} as ${intensity} than our model does.`;
  } else {
    const intensity = absGap >= 0.10 ? 'much more likely' : absGap >= 0.05 ? 'more likely' : 'somewhat more likely';
    lead = `Our model sees ${subject} as ${intensity} than the prediction markets do.`;
  }

  const modelText = probPct(modelProbability, 1);
  const consensusText = probPct(consensusProbabilityValue, 1);
  const points = (absGap * 100).toFixed(1);
  const comparison = absGap < 0.02
    ? `just ${points} point${points === '1.0' ? '' : 's'} from the model's ${modelText}`
    : `${points} percentage points ${marketGap > 0 ? 'above' : 'below'} the model's ${modelText}`;

  const hasPoly = Number.isFinite(polyProbability);
  const hasKalshi = Number.isFinite(kalshiProbabilityValue);

  let sourceRead;
  if (hasPoly && hasKalshi) {
    const exchangeGap = Math.abs(polyProbability - kalshiProbabilityValue);
    if (exchangeGap <= 0.02) {
      sourceRead = `Polymarket (${probPct(polyProbability, 1)}) and Kalshi (${probPct(kalshiProbabilityValue, 1)}) are closely aligned around a ${consensusText} consensus`;
    } else if (exchangeGap <= 0.05) {
      sourceRead = `Polymarket (${probPct(polyProbability, 1)}) and Kalshi (${probPct(kalshiProbabilityValue, 1)}) are broadly similar, producing a ${consensusText} consensus`;
    } else {
      sourceRead = `Polymarket (${probPct(polyProbability, 1)}) and Kalshi (${probPct(kalshiProbabilityValue, 1)}) disagree by ${(exchangeGap * 100).toFixed(1)} points, with consensus at ${consensusText}`;
    }
  } else if (hasPoly) {
    sourceRead = `Polymarket prices ${subject} at ${probPct(polyProbability, 1)}, so the available-market consensus is ${consensusText}`;
  } else if (hasKalshi) {
    sourceRead = `Kalshi prices ${subject} at ${probPct(kalshiProbabilityValue, 1)}, so the available-market consensus is ${consensusText}`;
  } else {
    sourceRead = `The available-market consensus is ${consensusText}`;
  }

  return `${lead} ${sourceRead}—${comparison}.`;
}
function modelTriplet(f, digits = 1) {
  const p = f.probabilities || {};
  return `${pct(p.home,digits)} · ${pct(p.draw,digits)} · ${pct(p.away,digits)}`;
}
function marketSourceNote(details) {
  if (!details) return 'No matching Polymarket market';
  return details.normalized ? 'Normalized Polymarket probability' : 'Polymarket probability';
}
function kalshiSourceNote(details) {
  if (!details) return 'No matching Kalshi market';
  const method = details.estimate_method === 'bid_ask_midpoint' ? 'bid/ask midpoint' : 'latest trade';
  return details.normalized ? `Normalized Kalshi ${method}` : `Kalshi ${method}`;
}
function consensusSourceNote(row) {
  const sources = row?.consensus_details?.sources || [];
  return sources.length ? `${sources.join(' + ')} consensus` : 'No external market consensus';
}
function badge(t, size = '') { return `<span class="badge ${size}" style="background:${esc(t.color)}">${esc(t.short)}</span>`; }
function teamInline(t, sub = '') { return `<span class="team-inline">${badge(t)}<span><strong>${esc(t.name)}</strong>${sub ? `<small>${esc(sub)}</small>` : ''}</span></span>`; }
function pageHead(eyebrow, title, copy) {
  return `<header class="page-head"><div><div class="eyebrow">${esc(eyebrow)}</div><h1>${title}</h1><p>${copy}</p></div><div class="status-box"><strong>${esc(state.data.meta.season)} · ${state.data.meta.iterations.toLocaleString()} simulations</strong><span>Snapshot ${dateText(state.data.meta.as_of)}</span><span>${esc(state.data.meta.model_version)}</span></div></header>`;
}
function notice() { const live = String(state.data.meta.data_mode || '').startsWith('LIVE'); return `<div class="notice"><strong>${live ? 'Live Bayesian snapshot' : 'Demo snapshot'}</strong><span>${esc(state.data.meta.notice)}</span></div>`; }
function metric(label, value, detail) { return `<div class="metric-card"><small>${label}</small><strong>${value}</strong><span>${detail}</span></div>`; }
function toast(message) {
  const el = document.getElementById('toast');
  el.textContent = message;
  el.classList.add('show');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.remove('show'), 2200);
}

async function loadData() {
  try {
    const [epl, mls] = await Promise.all([
      fetch('data/epl.json').then(r => { if (!r.ok) throw new Error('EPL data failed'); return r.json(); }),
      fetch('data/mls.json').then(r => { if (!r.ok) throw new Error('MLS data failed'); return r.json(); }),
    ]);
    state.datasets = {epl, mls};
    switchLeague(state.league, false);
  } catch (error) {
    main.innerHTML = `<div class="page"><div class="card"><div class="empty"><h2>Could not load the snapshots</h2><p>${esc(error.message)}</p><p>Run this project through <code>python server.py</code> instead of opening index.html directly.</p></div></div></div>`;
  }
}

function switchLeague(key, announce = true) {
  state.league = key;
  state.data = state.datasets[key];
  state.sortKey = outcomeKey();
  state.sortDir = -1;
  state.scheduleFilter = 'upcoming';
  state.scheduleTeam = 'all';
  state.newsFilter = 'all';
  localStorage.setItem('tf-league', key);
  document.querySelectorAll('.league-button').forEach(b => b.classList.toggle('active', b.dataset.league === key));
  document.getElementById('model-version').textContent = state.data.meta.model_version;
  document.getElementById('snapshot-title').textContent = `${state.data.meta.name} ${state.data.meta.season}`;
  document.getElementById('snapshot-date').textContent = `Generated ${dateText(state.data.meta.as_of)}`;
  document.getElementById('data-mode-pill').textContent = String(state.data.meta.data_mode || 'Snapshot').replace('LIVE API + BAYESIAN MODEL', 'Live model');
  document.getElementById('races-label').textContent = key === 'mls' ? 'Playoff bracket' : 'Season races';
  if (announce) toast(`Switched to ${state.data.meta.name}`);
  renderRoute();
}

function parseRoute() {
  const raw = location.hash.replace(/^#\/?/, '') || 'home';
  const parts = raw.split('/');
  const page = parts.shift();
  const slug = decodeURIComponent(parts.join('/'));
  return {page, slug};
}

function updateNav(page) {
  const navPage = page === 'match' ? 'schedule' : page;
  document.querySelectorAll('#primary-nav a').forEach(a => a.classList.toggle('active', a.dataset.route === navPage));
  document.getElementById('sidebar').classList.remove('open');
}

function renderRoute() {
  if (!state.data) return;
  const {page, slug} = parseRoute();
  updateNav(page === 'team' ? 'forecast' : page);
  const routes = {
    home: renderHome,
    forecast: renderForecast,
    table: renderTable,
    matchups: renderMatchups,
    races: renderRaces,
    schedule: renderSchedule,
    scores: renderScores,
    accuracy: renderAccuracy,
    news: renderNews,
    method: renderMethod,
    team: () => renderTeam(slug),
    match: () => renderMatch(slug),
  };
  (routes[page] || renderHome)();
  main.focus({preventScroll:true});
  window.scrollTo({top:0, behavior:'instant'});
}

function topForecast(key = outcomeKey(), count = 5, descending = true) {
  return [...state.data.forecast].sort((a,b) => (Number(a[key] || 0) - Number(b[key] || 0)) * (descending ? -1 : 1)).slice(0, count);
}

function renderHome() {
  const tMap = teamMap();
  const completed = state.data.fixtures.filter(f => f.status === 'final').length;
  const remaining = state.data.fixtures.length - completed;
  const leaders = topForecast();
  const polyLeaders = [...state.data.forecast].filter(hasMarket).sort((a,b) => marketProbability(b) - marketProbability(a));
  const kalshiLeaders = [...state.data.forecast].filter(hasKalshi).sort((a,b) => kalshiProbability(b) - kalshiProbability(a));
  const next = state.data.fixtures.filter(f => f.status !== 'final').sort((a,b) => a.date.localeCompare(b.date)).slice(0,6);
  const top = leaders[0];
  const polyTop = polyLeaders[0] || null;
  const kalshiTop = kalshiLeaders[0] || null;
  const quotedMatches = state.data.fixtures.filter(f => f.status !== 'final' && (fixtureMarket(f) || fixtureKalshi(f))).length;
  main.innerHTML = `<div class="page">
    ${pageHead('Forecast laboratory', `${esc(state.data.meta.name)} <em>forecast</em>`, state.league === 'epl'
      ? 'A transparent, simulation-based view of the title race, European qualification and relegation, compared independently with Polymarket and Kalshi.'
      : 'A transparent, simulation-based view of the Supporters’ Shield, conference races and MLS Cup playoffs, compared independently with Polymarket and Kalshi.')}
    ${notice()}
    <section class="grid metrics market-metrics">
      ${metric(outcomeLabel() + ' model favorite', esc(tMap[top.team].short), pct(top[outcomeKey()]) + ' Bayesian probability')}
      ${metric('Polymarket favorite', polyTop ? esc(tMap[polyTop.team].short) : '—', polyTop ? `${probPct(polyTop.market)} normalized market probability` : 'No active season-winner market matched')}
      ${metric('Kalshi favorite', kalshiTop ? esc(tMap[kalshiTop.team].short) : '—', kalshiTop ? `${probPct(kalshiTop.kalshi)} normalized market estimate` : 'No active season-winner market matched')}
      ${metric('Matches modeled', state.data.fixtures.length.toLocaleString(), `${completed} completed · ${remaining} remaining`)}
      ${metric('External match markets', quotedMatches.toLocaleString(), quotedMatches ? 'Upcoming match quotes from either exchange' : 'Markets normally appear near kickoff')}
      ${metric('Simulation runs', state.data.meta.iterations.toLocaleString(), 'Posterior uncertainty included')}
    </section>
    <section class="grid split">
      <article class="card">
        <div class="card-head"><h2>${outcomeLabel()} forecast</h2><a href="#/forecast">Full forecast →</a></div>
        <div class="forecast-list market-forecast-list">
          <div class="forecast-comparison-head" aria-hidden="true"><span></span><span></span><span></span><span>Model</span><span>Polymarket</span><span>Kalshi</span><span>Consensus</span><span>vs consensus</span></div>
          ${leaders.map((f,i) => {
            const t = tMap[f.team];
            const v = f[outcomeKey()] || 0;
            const poly = marketProbability(f);
            const kalshi = kalshiProbability(f);
            const consensus = consensusProbability(f);
            return `<a class="forecast-item market-forecast-item" href="#/team/${t.slug}"><span class="rank">${String(i+1).padStart(2,'0')}</span>${teamInline(t, `${f.projected_points} projected pts`)}<span class="prob-bar"><span style="width:${Math.max(2,v*100)}%"></span></span><span class="comparison-value model-value">${probPct(v)}</span><span class="comparison-value market-value">${poly===null?'—':probPct(poly)}</span><span class="comparison-value kalshi-value">${kalshi===null?'—':probPct(kalshi)}</span><span class="comparison-value consensus-value">${consensus===null?'—':probPct(consensus)}</span><span class="comparison-value edge-value ${f.consensus_edge>0?'positive':f.consensus_edge<0?'negative':'neutral'}">${f.consensus_edge===null||f.consensus_edge===undefined?'—':signedPct(f.consensus_edge)}</span></a>`;
          }).join('')}
        </div>
      </article>
      <article class="card">
        <div class="card-head"><h2>Next modeled matches</h2><a href="#/schedule">All fixtures →</a></div>
        <div class="fixture-list">
          ${next.map(f => fixtureCompact(f, tMap)).join('') || '<div class="empty">No upcoming fixtures.</div>'}
        </div>
      </article>
    </section>
    <p class="market-disclaimer">Polymarket and Kalshi are trader-derived comparisons only. Neither exchange changes the Bayesian model. Market consensus is the equal-weight mean of whichever normalized external estimates are available.</p>
  </div>`;
}

function fixtureCompact(f, tMap) {
  const h = tMap[f.home], a = tMap[f.away];
  const href = `#/match/${encodeURIComponent(f.id)}`;
  const poly = fixtureMarket(f), kalshi = fixtureKalshi(f), consensus = fixtureConsensus(f);
  return `<a class="fixture-row fixture-link fixture-compare-compact" href="${href}" aria-label="View ${esc(h.name)} versus ${esc(a.name)} details"><span class="date">${compactDate(f.date)}</span><span class="fixture-teams"><span class="fixture-team"><span>${esc(h.short)} · ${esc(h.name)}</span>${f.status === 'final' ? `<b>${f.home_score}</b>` : ''}</span><span class="fixture-team"><span>${esc(a.short)} · ${esc(a.name)}</span>${f.status === 'final' ? `<b>${f.away_score}</b>` : ''}</span></span>${f.status === 'final' ? `<span class="score">${f.home_score}–${f.away_score}</span>` : `<span class="fixture-prob probability-compare"><span><small>Model</small><b>${modelTriplet(f,0)}</b></span><span class="market-line ${poly?'available':'unavailable'}"><small>Polymarket</small><b>${poly?marketTriplet(f,0):'No market'}</b></span><span class="kalshi-line ${kalshi?'available':'unavailable'}"><small>Kalshi</small><b>${kalshi?kalshiTriplet(f,0):'No market'}</b></span><span class="consensus-line ${consensus?'available':'unavailable'}"><small>Consensus</small><b>${consensus?consensusTriplet(f,0):'—'}</b></span><em>H · D · A</em><small>View match →</small></span>`}</a>`;
}

function forecastColumns() {
  if (state.league === 'epl') return [
    ['projected_points','Proj pts'], ['avg_position','Avg pos'], ['title','Title'], ['top4','Top 4'], ['europe','Europe'], ['relegation','Relegation'], ['attack','Attack'], ['defense_strength','Defense'], ['market','Polymarket'], ['kalshi','Kalshi'], ['market_consensus','Consensus'], ['consensus_edge','vs consensus']
  ];
  return [
    ['projected_points','Proj pts'], ['avg_position','Avg pos'], ['shield','Shield'], ['playoffs','Playoffs'], ['conf_semis','Conf semi'], ['cup_final','Cup final'], ['champion','MLS Cup'], ['attack','Attack'], ['defense_strength','Defense'], ['market','Polymarket'], ['kalshi','Kalshi'], ['market_consensus','Consensus'], ['consensus_edge','vs consensus']
  ];
}

function renderForecast() {
  const tMap = teamMap(), cur = tableMap();
  const cols = forecastColumns();
  const rows = [...state.data.forecast].sort((a,b) => {
    const av = Number(a[state.sortKey] ?? 0), bv = Number(b[state.sortKey] ?? 0);
    if (av === bv) return tMap[a.team].name.localeCompare(tMap[b.team].name);
    return (av - bv) * state.sortDir;
  });
  main.innerHTML = `<div class="page">
    ${pageHead('Full model output', 'The full forecast', state.league === 'epl'
      ? 'Sort every club by projected points, finish probabilities, underlying strength, Polymarket, Kalshi or the combined external-market consensus.'
      : 'Sort all 30 clubs by Shield, conference and MLS Cup outcomes, with independent Polymarket, Kalshi and market-consensus comparisons where contracts are available.')}
    ${notice()}
    <article class="card"><div class="table-wrap"><table id="forecast-table"><thead><tr>
      <th data-sort="name">#</th><th data-sort="name">Club</th><th>Now</th>
      ${cols.map(([key,label]) => `<th data-sort="${key}">${label}${state.sortKey===key ? (state.sortDir<0?' ↓':' ↑') : ''}</th>`).join('')}
    </tr></thead><tbody>
      ${rows.map((f,i) => {
        const t=tMap[f.team];
        return `<tr data-team="${t.slug}"><td>${i+1}</td><td><span class="table-team">${badge(t)}<span>${esc(t.name)}${state.league==='mls'?`<small style="display:block;color:var(--muted)">${t.conference}</small>`:''}</span></span></td><td>${cur[t.slug]?.pts ?? 0}</td>
          ${cols.map(([key]) => `<td>${formatForecastCell(key, f[key])}</td>`).join('')}</tr>`;
      }).join('')}
    </tbody></table></div></article>
  </div>`;
  document.querySelectorAll('#forecast-table th[data-sort]').forEach(th => th.addEventListener('click', () => {
    const key=th.dataset.sort;
    if (key==='name') { state.sortKey=outcomeKey(); state.sortDir=-1; }
    else if (state.sortKey===key) state.sortDir*=-1;
    else {state.sortKey=key; state.sortDir = key==='avg_position' ? 1 : -1;}
    renderForecast();
  }));
  document.querySelectorAll('#forecast-table tr[data-team]').forEach(row => row.addEventListener('click', () => location.hash=`#/team/${row.dataset.team}`));
}

function formatForecastCell(key, value) {
  if (['market','kalshi','market_consensus'].includes(key) && (value === null || value === undefined)) return '—';
  if (['title','top4','europe','relegation','shield','playoffs','conf_semis','cup_final','champion','market','kalshi','market_consensus'].includes(key)) return probPct(value,1);
  if (['edge','kalshi_edge','consensus_edge'].includes(key) && (value === null || value === undefined)) return '—';
  if (['edge','kalshi_edge','consensus_edge'].includes(key)) return `<span class="${value>0?'positive':value<0?'negative':'neutral'}">${signedPct(value)}</span>`;
  if (key === 'defense_strength') return Number(value).toFixed(2);
  if (key === 'attack') return Number(value).toFixed(2);
  return Number(value).toFixed(1);
}

function projectedRows(filter = null) {
  const tMap=teamMap(), fMap=forecastMap(), cMap=tableMap();
  return state.data.teams.filter(t => !filter || t.conference === filter).map(t => ({...cMap[t.slug], ...fMap[t.slug], team:t.slug})).sort((a,b) => b.projected_points-a.projected_points || b.gd-a.gd);
}
function positionClass(i, total, league) {
  if (i===0) return 'title';
  if (league==='epl' && i<4) return 'europe';
  if (league==='epl' && i>=total-3) return 'danger';
  if (league==='mls' && i<7) return 'europe';
  if (league==='mls' && i<9) return 'wildcard';
  return '';
}
function tableSection(rows, title='', id='') {
  const tMap=teamMap();
  const body=rows.map((r,i)=>{
    const t=tMap[r.team];
    const lo=Math.max(0, Math.round(r.projected_points-1.64*r.points_sd));
    const hi=Math.round(r.projected_points+1.64*r.points_sd);
    const poly=marketProbability(r), kalshi=kalshiProbability(r), consensus=consensusProbability(r);
    const row=`<tr data-team="${t.slug}" tabindex="0" aria-label="View ${esc(t.name)} forecast"><td><span class="position-chip ${positionClass(i,rows.length,state.league)}">${i+1}</span></td><td><span class="table-team">${badge(t)}${esc(t.name)}</span></td><td>${r.p||0}</td><td>${r.pts||0}</td><td>${r.gd>0?'+':''}${r.gd||0}</td><td><b>${r.projected_points.toFixed(1)}</b></td><td>${lo}–${hi}</td><td>${probPct(r[outcomeKey()],1)}</td><td class="market-cell">${poly===null?'—':probPct(poly,1)}</td><td class="kalshi-cell">${kalshi===null?'—':probPct(kalshi,1)}</td><td class="consensus-cell">${consensus===null?'—':probPct(consensus,1)}</td><td>${r.consensus_edge===null||r.consensus_edge===undefined?'—':`<span class="${r.consensus_edge>0?'positive':r.consensus_edge<0?'negative':'neutral'}">${signedPct(r.consensus_edge)}</span>`}</td></tr>`;
    if(state.league==='mls' && i===8) {
      return `${row}<tr class="playoff-cutoff-row" aria-hidden="true"><td colspan="12"><span>Playoff cutoff</span><small>Seeds 1–7 qualify directly · Seeds 8–9 enter the Wild Card round</small></td></tr>`;
    }
    return row;
  }).join('');
  return `<article class="card projection-card"${id?` id="${id}"`:''}>${title?`<div class="card-head"><h2>${title}</h2><span class="conference-legend"><i class="legend-direct"></i>Direct playoff <i class="legend-wildcard"></i>Wild Card</span></div>`:''}<div class="table-wrap projection-table-wrap"><table class="projection-table"><thead><tr><th>Pos</th><th>Club</th><th>P</th><th>Pts</th><th>GD</th><th>Projected</th><th>Range</th><th>Model ${outcomeLabel()}</th><th>Polymarket</th><th>Kalshi</th><th>Consensus</th><th>vs consensus</th></tr></thead><tbody>${body}</tbody></table></div></article>`;
}

function renderTable() {
  const copy = state.league==='epl'
    ? 'The mean final table across all season simulations. The range is an approximate 90% interval for final points; Polymarket, Kalshi and consensus are independent comparisons.'
    : 'Conference tables are ranked separately for postseason qualification. The Shield is determined across both conferences; Polymarket, Kalshi and consensus compare MLS Cup winner estimates.';
  const content=state.league==='epl'
    ? `<section class="grid">${tableSection(projectedRows())}</section>`
    : `<nav class="conference-jumps" aria-label="Jump to conference"><span>Jump to:</span><button class="conference-jump" data-target="east-conference">Eastern Conference</button><button class="conference-jump" data-target="west-conference">Western Conference</button></nav><section class="grid mls-table-stack">${tableSection(projectedRows('East'),'Eastern Conference','east-conference')}${tableSection(projectedRows('West'),'Western Conference','west-conference')}</section>`;
  main.innerHTML=`<div class="page">${pageHead('Season projection', state.league==='epl'?'Projected table':'Conference projections', copy)}${notice()}${content}</div>`;
  document.querySelectorAll('tr[data-team]').forEach(row=>{
    const open=()=>location.hash=`#/team/${row.dataset.team}`;
    row.addEventListener('click',open);
    row.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();open();}});
  });
  document.querySelectorAll('.conference-jump').forEach(button=>button.addEventListener('click',()=>{
    document.getElementById(button.dataset.target)?.scrollIntoView({behavior:'smooth',block:'start'});
  }));
}

function poissonP(lambda, goals) { return Math.exp(-lambda) * Math.pow(lambda, goals) / factorial(goals); }
function factorial(n) { let v=1; for(let i=2;i<=n;i++)v*=i; return v; }
function matchupModel(a,b,venue) {
  const m=state.data.model;
  const homeAdv=venue==='neutral'?0:m.home_advantage_log;
  const valueTerm=m.market_value_coefficient*Math.log(a.market_value/b.market_value);
  let lh=m.base_goals*Math.exp(homeAdv+a.attack+b.defense+valueTerm);
  let la=m.base_goals*Math.exp(b.attack+a.defense-valueTerm);
  if (venue==='b-home') {
    const reversed=matchupModel(b,a,'a-home');
    return {lh:reversed.la,la:reversed.lh,home:reversed.away,draw:reversed.draw,away:reversed.home,matrix:reversed.matrix.map((row,i)=>row.map((_,j)=>reversed.matrix[j][i]))};
  }
  lh=clamp(lh,.2,4.5); la=clamp(la,.2,4);
  const matrix=Array.from({length:7},(_,h)=>Array.from({length:7},(_,aw)=>poissonP(lh,h)*poissonP(la,aw)));
  let home=0,draw=0,away=0;
  matrix.forEach((row,h)=>row.forEach((p,aw)=>{if(h>aw)home+=p;else if(h===aw)draw+=p;else away+=p;}));
  const total=home+draw+away;
  return {lh,la,home:home/total,draw:draw/total,away:away/total,matrix};
}
function matchingMarketFixture(a, b, venue) {
  if (venue==='neutral') return null;
  const home=venue==='a-home'?a.slug:b.slug;
  const away=venue==='a-home'?b.slug:a.slug;
  return state.data.fixtures.filter(f=>f.status!=='final'&&f.home===home&&f.away===away&&(fixtureMarket(f)||fixtureKalshi(f))).sort((x,y)=>x.date.localeCompare(y.date))[0]||null;
}
function renderMatchups() {
  const teams=state.data.teams;
  if (!state.matchupA || !teams.some(t=>t.slug===state.matchupA)) state.matchupA=topForecast()[0].team;
  if (!state.matchupB || !teams.some(t=>t.slug===state.matchupB) || state.matchupB===state.matchupA) state.matchupB=topForecast()[1].team;
  const a=team(state.matchupA), b=team(state.matchupB), model=matchupModel(a,b,state.venue);
  const probs=[model.home,model.draw,model.away], max=Math.max(...probs);
  const matchedFixture=matchingMarketFixture(a,b,state.venue);
  const poly=fixtureMarket(matchedFixture), kalshi=fixtureKalshi(matchedFixture), consensus=fixtureConsensus(matchedFixture);
  let matchupMarket=`<div class="matchup-market-strip unavailable"><strong>Prediction markets</strong><span>No exact scheduled Polymarket or Kalshi market for this custom matchup and venue.</span></div>`;
  if(poly||kalshi){
    const lines=[];
    const marketLine=(label,market)=>{
      if(!market) return;
      const aMarket=state.venue==='a-home'?market.probabilities.home:market.probabilities.away;
      const bMarket=state.venue==='a-home'?market.probabilities.away:market.probabilities.home;
      lines.push(`${label}: ${esc(a.short)} ${probPct(aMarket,1)} · Draw ${probPct(market.probabilities.draw,1)} · ${esc(b.short)} ${probPct(bMarket,1)}`);
    };
    marketLine('Polymarket',poly); marketLine('Kalshi',kalshi); marketLine('Consensus',consensus);
    matchupMarket=`<a class="matchup-market-strip" href="#/match/${encodeURIComponent(matchedFixture.id)}"><strong>Prediction markets · ${compactDate(matchedFixture.date)}</strong><span>${lines.join('<br>')}</span><em>Open scheduled match →</em></a>`;
  }
  main.innerHTML=`<div class="page">${pageHead('Closed-form Poisson model','Matchup laboratory','Choose any two clubs and venue. The score matrix and win/draw/loss probabilities update immediately from the current attack and defensive ratings. Polymarket and Kalshi appear only when the selection matches an exact scheduled event.')}${notice()}
  <section class="grid matchup-grid">
    <article class="card"><div class="card-head"><h2>Set the matchup</h2></div><div class="card-body matchup-selector">
      <label><span class="eyebrow">Team A</span><div class="club-pick"><select id="matchup-a">${teams.map(t=>`<option value="${t.slug}" ${t.slug===a.slug?'selected':''}>${esc(t.name)}</option>`).join('')}</select>${badge(a)}</div></label>
      <label><span class="eyebrow">Team B</span><div class="club-pick"><select id="matchup-b">${teams.map(t=>`<option value="${t.slug}" ${t.slug===b.slug?'selected':''}>${esc(t.name)}</option>`).join('')}</select>${badge(b)}</div></label>
      <label><span class="eyebrow">Venue</span><select id="venue"><option value="a-home" ${state.venue==='a-home'?'selected':''}>${esc(a.name)} at home</option><option value="neutral" ${state.venue==='neutral'?'selected':''}>Neutral venue</option><option value="b-home" ${state.venue==='b-home'?'selected':''}>${esc(b.name)} at home</option></select></label>
      <div class="big-probs"><div class="big-prob ${model.home===max?'favorite':''}"><strong>${pct(model.home,1)}</strong><span>${esc(a.short)} win</span></div><div class="big-prob ${model.draw===max?'favorite':''}"><strong>${pct(model.draw,1)}</strong><span>Draw</span></div><div class="big-prob ${model.away===max?'favorite':''}"><strong>${pct(model.away,1)}</strong><span>${esc(b.short)} win</span></div></div>
      <div class="xg-row"><span>${esc(a.short)} xG <b>${model.lh.toFixed(2)}</b></span><span>${esc(b.short)} xG <b>${model.la.toFixed(2)}</b></span></div>
      ${matchupMarket}
    </div></article>
    <article class="card"><div class="card-head"><h2>Exact score probability</h2><span class="eyebrow">Rows ${esc(a.short)} · columns ${esc(b.short)}</span></div><div class="card-body table-wrap">${scoreMatrix(model.matrix)}</div></article>
  </section></div>`;
  ['matchup-a','matchup-b','venue'].forEach(id=>document.getElementById(id).addEventListener('change',e=>{
    if(id==='matchup-a') state.matchupA=e.target.value;
    if(id==='matchup-b') state.matchupB=e.target.value;
    if(state.matchupA===state.matchupB){ toast('Choose two different clubs'); state.matchupB=teams.find(t=>t.slug!==state.matchupA).slug; }
    if(id==='venue') state.venue=e.target.value;
    renderMatchups();
  }));
}

function scoreMatrix(matrix) {
  const max=Math.max(...matrix.flat());
  return `<table class="score-matrix"><thead><tr><th></th>${matrix[0].map((_,i)=>`<th>${i}</th>`).join('')}</tr></thead><tbody>${matrix.map((row,h)=>`<tr><th>${h}</th>${row.map(p=>`<td><span class="score-cell" style="background:rgba(10,111,87,${Math.max(.03,p/max*.8)});display:block;padding:7px 2px"><span>${pct(p,1)}</span></span></td>`).join('')}</tr>`).join('')}</tbody></table>`;
}

function raceDefinition(key) {
  const definitions = state.league==='epl'
    ? {
        title: 'Finishes first in the Premier League table.',
        top4: 'Finishes in the top four in the final league table.',
        relegation: 'Finishes in the bottom three and is relegated.',
      }
    : {
        shield: 'Finishes with the best regular-season record across both conferences.',
        cup_final: 'Wins its conference playoff and reaches the MLS Cup championship match.',
        champion: 'Wins the postseason championship and lifts MLS Cup.',
      };
  return definitions[key] || 'Share of simulations in which this outcome occurs.';
}
function raceCard(title,key,reverse=false) {
  const tMap=teamMap();
  const expansionKey=`${state.league}:${key}`;
  const expanded=Boolean(state.raceExpanded[expansionKey]);
  const allRows=[...state.data.forecast].sort((a,b)=>(Number(a[key]||0)-Number(b[key]||0))*(reverse?1:-1));
  const rows=expanded?allRows:allRows.slice(0,6);
  const compareMarket=key===outcomeKey();
  const rowHtml=rows.map(f=>{
    const value=Number(f[key]||0);
    const width=value>0?Math.max(1,value*100):0;
    const poly=marketProbability(f), kalshi=kalshiProbability(f), consensus=consensusProbability(f);
    return `<a class="race-row race-link ${compareMarket?'race-row-market':''}" href="#/team/${encodeURIComponent(f.team)}" aria-label="View ${esc(tMap[f.team].name)} forecast">${teamInline(tMap[f.team])}<span class="race-probabilities"><span><small>Model</small><b>${probPct(value,1)}</b></span>${compareMarket?`<span><small>Polymarket</small><b>${poly===null?'—':probPct(poly,1)}</b></span><span><small>Kalshi</small><b>${kalshi===null?'—':probPct(kalshi,1)}</b></span><span><small>Consensus</small><b>${consensus===null?'—':probPct(consensus,1)}</b></span><span><small>vs consensus</small><b class="${f.consensus_edge>0?'positive':f.consensus_edge<0?'negative':'neutral'}">${f.consensus_edge===null||f.consensus_edge===undefined?'—':signedPct(f.consensus_edge)}</b></span>`:''}</span><span class="prob-bar" style="grid-column:1/-1"><span style="width:${width}%"></span></span></a>`;
  }).join('');
  const toggle=allRows.length>6?`<button class="race-toggle" type="button" data-race-key="${key}" aria-expanded="${expanded}">${expanded?'Show top 6 ↑':`View all ${allRows.length} teams ↓`}</button>`:'';
  const comparisonNote=compareMarket?`<p class="market-card-note">Polymarket and Kalshi are shown only when exact active season-winner contracts are available. Consensus averages the available normalized estimates.</p>`:'';
  return `<article class="card race-card"><div class="eyebrow">Probability</div><h3>${title}</h3><p class="race-definition"><span class="info-dot" aria-hidden="true">i</span>${esc(raceDefinition(key))}</p><div class="race-rows">${rowHtml}</div>${comparisonNote}${toggle}</article>`;
}

function bindRaceControls() {
  document.querySelectorAll('.race-toggle').forEach(button=>button.addEventListener('click',()=>{
    const expansionKey=`${state.league}:${button.dataset.raceKey}`;
    state.raceExpanded[expansionKey]=!state.raceExpanded[expansionKey];
    renderRaces();
  }));
}
function conferenceBracket(conf) {
  const rows=projectedRows(conf).slice(0,9), tMap=teamMap();
  const seed=i=>rows[i] ? `${i+1}. ${esc(tMap[rows[i].team].short)}` : 'TBD';
  return `<article class="card"><div class="card-head"><h2>${conf} projected bracket</h2><span class="eyebrow">Mean table</span></div><div class="card-body"><div class="bracket">
    <div class="bracket-column"><h3>Wild Card</h3><div class="bracket-match"><div class="bracket-team"><span>${seed(7)}</span><b>${pct(rows[7]?.playoffs)}</b></div><div class="bracket-team"><span>${seed(8)}</span><b>${pct(rows[8]?.playoffs)}</b></div></div></div>
    <div class="bracket-column"><h3>Round One · Best of 3</h3>${[[0,'8/9'],[3,4],[1,6],[2,5]].map(pair=>`<div class="bracket-match"><div class="bracket-team"><span>${typeof pair[0]==='number'?seed(pair[0]):pair[0]}</span></div><div class="bracket-team"><span>${typeof pair[1]==='number'?seed(pair[1]):pair[1]}</span></div></div>`).join('')}</div>
    <div class="bracket-column"><h3>Conference semifinals</h3><div class="bracket-match"><div class="bracket-team"><span>1/WC winner</span></div><div class="bracket-team"><span>4/5 winner</span></div></div><div class="bracket-match"><div class="bracket-team"><span>2/7 winner</span></div><div class="bracket-team"><span>3/6 winner</span></div></div></div>
    <div class="bracket-column"><h3>Conference final</h3><div class="bracket-match"><div class="bracket-team"><span>Semifinal winners</span><b>→ Cup</b></div></div></div>
  </div></div></article>`;
}
function renderRaces() {
  if(state.league==='epl') {
    main.innerHTML=`<div class="page">${pageHead('Outcome distributions','The season races','The same season simulations can be sliced into separate title, European qualification and relegation races. These probabilities are dependent, not standalone predictions.')}${notice()}<section class="grid races-grid">${raceCard('Premier League title','title')}${raceCard('Top-four finish','top4')}${raceCard('Relegation','relegation')}</section></div>`;
  } else {
    main.innerHTML=`<div class="page">${pageHead('Postseason simulation','Projected playoff bracket','The mean conference tables populate this visual bracket. Every MLS Cup probability, however, comes from rebuilding and playing the bracket in each simulation.')}${notice()}<section class="grid races-grid" style="margin-bottom:16px">${raceCard('Supporters’ Shield','shield')}${raceCard('Reach MLS Cup','cup_final')}${raceCard('Win MLS Cup','champion')}</section><section class="grid">${conferenceBracket('East')}${conferenceBracket('West')}</section></div>`;
  }
  bindRaceControls();
}

function filteredFixtures() {
  return state.data.fixtures.filter(f => {
    const statusOk=state.scheduleFilter==='all'||(state.scheduleFilter==='upcoming'&&f.status!=='final')||(state.scheduleFilter==='completed'&&f.status==='final');
    const teamOk=state.scheduleTeam==='all'||f.home===state.scheduleTeam||f.away===state.scheduleTeam;
    return statusOk&&teamOk;
  }).sort((a,b)=>state.scheduleFilter==='completed'?b.date.localeCompare(a.date):a.date.localeCompare(b.date));
}
function renderSchedule() {
  const tMap=teamMap(), fixtures=filteredFixtures().slice(0,120);
  const polyCount=fixtures.filter(f=>fixtureMarket(f)).length;
  const kalshiCount=fixtures.filter(f=>fixtureKalshi(f)).length;
  const eitherCount=fixtures.filter(f=>fixtureMarket(f)||fixtureKalshi(f)).length;
  main.innerHTML=`<div class="page">${pageHead('Fixtures and probabilities','Schedule & results','Every fixture includes the Bayesian 1X2 forecast. Exact Polymarket and Kalshi match estimates appear alongside it when those exchanges have opened a verified full-match three-outcome event.')}${notice()}
  <div class="toolbar"><div class="segmented">${[['upcoming','Upcoming'],['completed','Completed'],['all','All']].map(([v,l])=>`<button data-filter="${v}" class="${state.scheduleFilter===v?'active':''}">${l}</button>`).join('')}</div><select id="schedule-team"><option value="all">All clubs</option>${state.data.teams.map(t=>`<option value="${t.slug}" ${state.scheduleTeam===t.slug?'selected':''}>${esc(t.name)}</option>`).join('')}</select><span class="market-coverage-pill">${eitherCount}/${fixtures.length} external · PM ${polyCount} · Kalshi ${kalshiCount}</span></div>
  <article class="card"><div class="fixture-list">${fixtures.map(f=>fixtureDetailed(f,tMap)).join('')||'<div class="empty">No fixtures match these filters.</div>'}</div></article><p class="market-disclaimer">H · D · A means home win, draw and away win. A missing exchange value means no exact verified market was matched; the site does not fill it with sportsbook odds or an inferred estimate.</p></div>`;
  document.querySelectorAll('[data-filter]').forEach(b=>b.addEventListener('click',()=>{state.scheduleFilter=b.dataset.filter;renderSchedule();}));
  document.getElementById('schedule-team').addEventListener('change',e=>{state.scheduleTeam=e.target.value;renderSchedule();});
}

function fixtureDetailed(f,tMap) {
  const h=tMap[f.home],a=tMap[f.away];
  const href = `#/match/${encodeURIComponent(f.id)}`;
  const poly=fixtureMarket(f), kalshi=fixtureKalshi(f), consensus=fixtureConsensus(f);
  const expectedTotal=fixtureExpectedTotal(f);
  const consensusEdge=consensus?.model_edge;
  const edgeText=consensusEdge?`vs consensus ${signedPct(consensusEdge.home)} · ${signedPct(consensusEdge.draw)} · ${signedPct(consensusEdge.away)}`:'Consensus unavailable';
  return `<a class="fixture-row fixture-row-detailed fixture-link fixture-comparison-row" data-fixture-id="${esc(f.id)}" href="${href}" aria-label="View ${esc(h.name)} versus ${esc(a.name)} details"><span class="date">Round ${esc(f.round)}<br>${dateText(f.date)}</span><span class="fixture-teams"><span class="fixture-team"><span class="team-inline">${badge(h)}<strong>${esc(h.name)}</strong></span>${f.status==='final'?`<b>${f.home_score}</b>`:''}</span><span class="fixture-team"><span class="team-inline">${badge(a)}<strong>${esc(a.name)}</strong></span>${f.status==='final'?`<b>${f.away_score}</b>`:''}</span></span><span class="fixture-market-compare"><span class="fixture-expected-total"><span>Expected Total Goals</span><strong>${expectedTotal===null?'—':expectedTotal.toFixed(2)}</strong></span><span class="comparison-row model"><small>Bayesian model</small><b>${modelTriplet(f,1)}</b></span><span class="comparison-row market ${poly?'available':'unavailable'}"><small>Polymarket</small><b>${poly?marketTriplet(f,1):'No exact market'}</b></span><span class="comparison-row kalshi ${kalshi?'available':'unavailable'}"><small>Kalshi</small><b>${kalshi?kalshiTriplet(f,1):'No exact market'}</b></span><span class="comparison-row consensus ${consensus?'available':'unavailable'}"><small>Consensus</small><b>${consensus?consensusTriplet(f,1):'—'}</b></span><em>H · D · A</em><span class="comparison-edge">${esc(edgeText)}</span><small>xG ${f.xg_home}–${f.xg_away}${f.status==='final'?` · Final ${f.home_score}–${f.away_score}`:''}</small><small class="view-detail">View match details →</small></span></a>`;
}

function fixtureScoreModel(f) {
  const lh = clamp(Number(f.xg_home || 0), .05, 6);
  const la = clamp(Number(f.xg_away || 0), .05, 6);
  const matrix = Array.from({length:7},(_,h)=>Array.from({length:7},(_,a)=>poissonP(lh,h)*poissonP(la,a)));
  return {lh, la, matrix};
}
function probabilityBand(value) {
  const p=Number(value||0);
  if(p>=.60) return {className:'prob-very-high',label:'High probability'};
  if(p>=.45) return {className:'prob-high',label:'Above average'};
  if(p>=.30) return {className:'prob-medium',label:'Competitive'};
  if(p>=.15) return {className:'prob-low',label:'Low probability'};
  return {className:'prob-very-low',label:'Long shot'};
}
function outcomeCard(label, probability, max, detail) {
  const band=probabilityBand(probability);
  const favorite=Number(probability)===Number(max);
  return `<div class="outcome-card ${band.className} ${favorite?'favorite':''}" aria-label="${esc(label)} ${probPct(probability,1)}, ${esc(band.label)}${favorite?', highest probability outcome':''}"><small>${esc(label)}</small><strong>${probPct(probability,1)}</strong><span class="probability-strength">${esc(band.label)}${favorite?' · highest':''}</span><span class="probability-detail">${esc(detail)}</span></div>`;
}
function goalTotalsData(f) {
  const archived=f?.status==='final' ? f?.postgame_analysis?.goal_totals : null;
  if(archived?.model?.over) return {data:archived,archived:true};
  if(f?.goal_totals?.model?.over) return {data:f.goal_totals,archived:false};
  const lambda=Math.max(.01,Number(f?.xg_home||0)+Number(f?.xg_away||0));
  const exact={}; let cumulative=0;
  for(let goals=0;goals<6;goals++){const value=poissonP(lambda,goals);exact[String(goals)]=value;cumulative+=value;}
  exact['6+']=Math.max(0,1-cumulative);
  const over={},under={};
  [0.5,1.5,2.5,3.5,4.5,5.5].forEach(line=>{
    let u=0; for(let goals=0;goals<=Math.floor(line);goals++)u+=poissonP(lambda,goals);
    over[line.toFixed(1)]=Math.max(0,1-u); under[line.toFixed(1)]=u;
  });
  return {data:{model:{lambda,exact,over,under,method:'Poisson total from home xG + away xG'}},archived:false};
}
function overUnderText(source,line) {
  const over=source?.over?.[line];
  if(over===null||over===undefined||!Number.isFinite(Number(over))) return '—';
  const under=source?.under?.[line] ?? (1-Number(over));
  return `O ${probPct(over,1)} · U ${probPct(under,1)}`;
}
function goalTotalsExplainer(totals) {
  const model=totals?.model?.over||{}, consensus=totals?.consensus?.over||{};
  const candidates=Object.keys(consensus).filter(line=>Number.isFinite(Number(model[line]))&&Number.isFinite(Number(consensus[line]))).map(line=>({line,edge:Number(model[line])-Number(consensus[line])}));
  if(!candidates.length) return 'No exact prediction-market total-goals line is available yet. The model distribution is still shown from the mean expected-goals view.';
  candidates.sort((a,b)=>Math.abs(b.edge)-Math.abs(a.edge));
  const top=candidates[0], abs=Math.abs(top.edge);
  const direction=top.edge>0?'higher-scoring':'lower-scoring';
  const relation=abs<.02?'is closely aligned with':top.edge>0?'puts more weight on the over than':'puts less weight on the over than';
  return `The model ${relation} the prediction markets at Over ${top.line}: model ${probPct(model[top.line],1)} vs ${probPct(consensus[top.line],1)} consensus (${signedPct(top.edge)}). Overall, the largest available totals gap points to a ${direction} model view.`;
}
function goalTotalsCard(f) {
  const resolved=goalTotalsData(f), totals=resolved.data||{}, model=totals.model||{};
  const poly=totals.polymarket||null, kalshi=totals.kalshi||null, consensus=totals.consensus||null;
  const lines=['0.5','1.5','2.5','3.5','4.5','5.5'];
  const exactKeys=['0','1','2','3','4','5','6+'];
  const distribution=exactKeys.map(key=>`<div class="goal-total-chip"><small>${key} goal${key==='1'?'':'s'}</small><strong>${model.exact?.[key]===undefined?'—':probPct(model.exact[key],1)}</strong></div>`).join('');
  const rows=lines.map(line=>{
    const edge=consensus?.model_edge?.[line];
    const kline=kalshi?.lines?.[line];
    const range=kline?.bid!==null&&kline?.bid!==undefined&&kline?.ask!==null&&kline?.ask!==undefined?`<small>Bid–ask ${probPct(kline.bid,1)}–${probPct(kline.ask,1)}</small>`:'';
    const hasExternal=poly?.over?.[line]!==undefined||kalshi?.over?.[line]!==undefined;
    return `<tr class="${hasExternal?'has-market':''}"><td><strong>Over ${line}</strong></td><td>${overUnderText(model,line)}</td><td>${overUnderText(poly,line)}</td><td>${overUnderText(kalshi,line)}${range}</td><td>${overUnderText(consensus,line)}</td><td><strong class="${Number(edge)>0?'positive':Number(edge)<0?'negative':'neutral'}">${edge===null||edge===undefined?'—':signedPct(edge)}</strong></td></tr>`;
  }).join('');
  const polyUrl=poly?.event_url||''; const kalshiUrl=kalshi?.event_url||'';
  const archiveNote=resolved.archived?'Frozen pregame totals':'Current pregame totals';
  return `<article class="card goal-totals-card"><div class="card-head"><h2>Total goals</h2><span class="eyebrow">${esc(archiveNote)}</span></div><div class="card-body"><div class="goal-total-distribution">${distribution}</div><p class="detail-note goal-total-distribution-note">Exact total-goal probabilities use the full Poisson total distribution; the 6+ bucket includes the entire scoring tail beyond five goals.</p><div class="table-wrap"><table class="goal-totals-table"><thead><tr><th>Line</th><th>Our model</th><th>Polymarket</th><th>Kalshi</th><th>Consensus</th><th>Model vs consensus</th></tr></thead><tbody>${rows}</tbody></table></div><div class="goal-total-read"><strong>Totals read</strong><p>${esc(goalTotalsExplainer(totals))}</p></div><div class="market-meta goal-total-meta">${poly?`<span>Polymarket totals · Volume ${money(poly.volume)}</span>${polyUrl?`<a href="${esc(polyUrl)}" target="_blank" rel="noopener noreferrer">Open Polymarket ↗</a>`:''}`:'<span>Polymarket totals unavailable</span>'}${kalshi?`<span>Kalshi totals · Volume ${money(kalshi.volume)}</span>${kalshiUrl?`<a href="${esc(kalshiUrl)}" target="_blank" rel="noopener noreferrer">Open Kalshi ↗</a>`:''}`:'<span>Kalshi totals unavailable</span>'}</div><p class="detail-note">Over/under markets are regulation-time contest totals only. Team totals are excluded. Market prices are comparison-only and never enter the Bayesian model.</p></div></article>`;
}
function likelyScores(matrix, home, away) {
  return matrix.flatMap((row,h)=>row.map((prob,a)=>({h,a,prob}))).sort((x,y)=>y.prob-x.prob).slice(0,5).map(row=>`<div class="likely-score"><strong>${row.h}–${row.a}</strong><span>${pct(row.prob,1)}</span><small>${esc(home.short)}–${esc(away.short)}</small></div>`).join('');
}
function exactScoreExplainer(matrix, probabilities, home, away) {
  const top=matrix.flatMap((row,h)=>row.map((prob,a)=>({h,a,prob}))).sort((x,y)=>y.prob-x.prob)[0];
  const favoriteKey=['home','draw','away'].sort((x,y)=>Number(probabilities[y]||0)-Number(probabilities[x]||0))[0];
  const favoriteLabel=favoriteKey==='home'?`${home.short} win`:favoriteKey==='away'?`${away.short} win`:'draw';
  const resultDescription=favoriteKey==='home'?`${home.short} scores more than ${away.short}`:favoriteKey==='away'?`${away.short} scores more than ${home.short}`:'the teams finish level';
  return `Why ${top.h}–${top.a} can be the top score: ${top.h}–${top.a} is the most likely single exact scoreline at ${pct(top.prob,1)}. The ${favoriteLabel} probability of ${pct(probabilities[favoriteKey],1)} adds together every scoreline where ${resultDescription}. A single scoreline can therefore rank first even when a different overall result category is more likely.`;
}
function intervalLabel(value) {
  return Array.isArray(value) && value.length===2 ? `${pct(value[0],1)}–${pct(value[1],1)}` : 'Not available';
}
function recentForm(slug, beforeId) {
  const reference=fixtureById(beforeId);
  const cutoff=reference?.date||'9999-12-31';
  const fixtures=state.data.fixtures.filter(f=>f.status==='final'&&f.id!==beforeId&&f.date<=cutoff&&(f.home===slug||f.away===slug)).sort((a,b)=>b.date.localeCompare(a.date)).slice(0,5);
  return fixtures.map(f=>{
    const isHome=f.home===slug;
    const gf=isHome?f.home_score:f.away_score, ga=isHome?f.away_score:f.home_score;
    const result=gf>ga?'W':gf<ga?'L':'D';
    const opp=team(isHome?f.away:f.home);
    return `<a class="form-match" href="#/match/${encodeURIComponent(f.id)}"><span class="form-pill ${result.toLowerCase()}">${result}</span><span><strong>${gf}–${ga}</strong><small>${esc(opp.short)} · ${compactDate(f.date)}</small></span></a>`;
  }).join('')||'<div class="empty compact">No completed matches yet.</div>';
}
function ratingCard(t, f) {
  const attackRange=Array.isArray(f?.attack_interval)?`${f.attack_interval[0].toFixed(2)} to ${f.attack_interval[1].toFixed(2)}`:'Not available';
  const defenseRange=Array.isArray(f?.defense_strength_interval)?`${f.defense_strength_interval[0].toFixed(2)} to ${f.defense_strength_interval[1].toFixed(2)}`:Array.isArray(f?.defense_interval)?`${(-f.defense_interval[1]).toFixed(2)} to ${(-f.defense_interval[0]).toFixed(2)}`:'Not available';
  return `<article class="card rating-card"><div class="rating-team">${badge(t,'large')}<div><h3>${esc(t.name)}</h3><a href="#/team/${t.slug}">Open club forecast →</a></div></div><div class="rating-stats"><div><small>Attack rating</small><strong>${Number(f?.attack??t.attack).toFixed(2)}</strong><span>90% range: ${attackRange}</span></div><div><small>Defense strength</small><strong>${Number(f?.defense_strength??(-Number(f?.defense??t.defense))).toFixed(2)}</strong><span>Higher is stronger · 90% range: ${defenseRange}</span></div><div><small>Projected points</small><strong>${Number(f?.projected_points||0).toFixed(1)}</strong><span>${state.league==='epl'?`Title ${pct(f?.title||0,1)}`:`MLS Cup ${pct(f?.champion||0,1)}`}</span></div></div></article>`;
}
function archivedMarketComparison(f, h, a) {
  const review=f?.postgame_analysis;
  const sources=review?.sources||{};
  const model=sources.model, poly=sources.polymarket, kalshi=sources.kalshi, consensus=sources.consensus;
  if(!model || (!poly && !kalshi)) return '';
  const rows=[['home',`${h.short} win`],['draw','Draw'],['away',`${a.short} win`]];
  const polyObj=poly?{probabilities:poly}:null;
  const kalshiObj=kalshi?{probabilities:kalshi}:null;
  const consensusObj=consensus?{probabilities:consensus}:null;
  const captured=review.captured_at?kickoffText(review.captured_at):'Archived pregame snapshot';
  const recovered=review?.provenance?.type==='archived_git_snapshot';
  const polyUrl=review?.market_refs?.polymarket?.event_url||'';
  const kalshiUrl=review?.market_refs?.kalshi?.event_url||'';
  return `<article class="card market-comparison-card archived-market-card"><div class="card-head"><h2>Pregame prediction-market comparison</h2><span class="eyebrow">${recovered?'Recovered historical snapshot':'Frozen pregame snapshot'}</span></div><div class="card-body"><div class="market-outcome-grid">${rows.map(([key,label])=>{
    const marketConsensus=consensus?.[key];
    const edge=Number.isFinite(Number(marketConsensus))?Number(model[key])-Number(marketConsensus):null;
    const explainer=consensusObj?marketOutcomeExplainer(key,label,model,polyObj,kalshiObj,consensusObj):'';
    return `<div class="market-outcome"><small>${esc(label)}</small><div><span>Model</span><strong>${probPct(model[key],1)}</strong></div><div><span>Polymarket</span><strong>${poly?probPct(poly[key],1):'—'}</strong></div><div><span>Kalshi</span><strong>${kalshi?probPct(kalshi[key],1):'—'}</strong></div><div><span>Consensus</span><strong>${consensus?probPct(consensus[key],1):'—'}</strong></div><div><span>Model vs consensus</span><strong class="${edge>0?'positive':edge<0?'negative':'neutral'}">${edge===null?'—':signedPct(edge)}</strong></div>${explainer?`<p class="market-explainer">${esc(explainer)}</p>`:''}</div>`;
  }).join('')}</div><div class="market-meta"><span>Snapshot captured ${esc(captured)}</span>${recovered?'<span>Recovered from an archived nightly forecast committed before kickoff</span>':''}${polyUrl?`<a href="${esc(polyUrl)}" target="_blank" rel="noopener noreferrer">Archived Polymarket event ↗</a>`:''}${kalshiUrl?`<a href="${esc(kalshiUrl)}" target="_blank" rel="noopener noreferrer">Archived Kalshi event ↗</a>`:''}</div><p class="detail-note">These are the actual probabilities stored before kickoff. They are preserved for historical review and are never recalculated using the final result.</p></div></article>`;
}

function matchMarketComparison(f, h, a) {
  if(f?.status==='final') {
    const archived=archivedMarketComparison(f,h,a);
    if(archived) return archived;
  }
  const poly=fixtureMarket(f), kalshi=fixtureKalshi(f), consensus=fixtureConsensus(f);
  if(!poly && !kalshi) {
    const finalMessage=f?.status==='final'
      ? 'No archived pregame prediction-market snapshot was found for this match. Historical coverage is available only when a real pregame snapshot was saved before kickoff.'
      : 'Soccer match markets commonly open close to kickoff. This site does not substitute sportsbook odds or manufacture an external-market estimate when no exact three-outcome event is available.';
    return `<article class="card market-comparison-card"><div class="card-head"><h2>Prediction-market comparison</h2><span class="eyebrow">Independent markets</span></div><div class="card-body empty market-empty"><strong>${f?.status==='final'?'No archived Polymarket or Kalshi snapshot found':'No exact Polymarket or Kalshi match market found'}</strong><p>${esc(finalMessage)}</p></div></article>`;
  }
  const model=f.probabilities;
  const rows=[['home',`${h.short} win`],['draw','Draw'],['away',`${a.short} win`]];
  const polyUrl=poly?.event_url||`https://polymarket.com/event/${encodeURIComponent(poly?.event_slug||'')}`;
  const kalshiUrl=kalshi?.event_url||'';
  const kalshiRange=key=>{
    const bid=kalshi?.bids?.[key], ask=kalshi?.asks?.[key];
    if(bid===null||bid===undefined||ask===null||ask===undefined) return '';
    return `${probPct(bid,1)}–${probPct(ask,1)}`;
  };
  return `<article class="card market-comparison-card"><div class="card-head"><h2>Prediction-market comparison</h2><span class="eyebrow">Independent markets</span></div><div class="card-body"><div class="market-outcome-grid">${rows.map(([key,label])=>`<div class="market-outcome"><small>${esc(label)}</small><div><span>Model</span><strong>${probPct(model[key],1)}</strong></div><div><span>Polymarket</span><strong>${poly?probPct(poly.probabilities[key],1):'—'}</strong></div><div><span>Kalshi</span><strong>${kalshi?probPct(kalshi.probabilities[key],1):'—'}</strong></div>${kalshi&&kalshiRange(key)?`<div><span>Kalshi bid–ask</span><strong class="market-range">${kalshiRange(key)}</strong></div>`:''}<div><span>Consensus</span><strong>${consensus?probPct(consensus.probabilities[key],1):'—'}</strong></div><div><span>Model vs consensus</span><strong class="${consensus?.model_edge?.[key]>0?'positive':consensus?.model_edge?.[key]<0?'negative':'neutral'}">${consensus?signedPct(consensus.model_edge[key]):'—'}</strong></div>${consensus?`<p class="market-explainer">${esc(marketOutcomeExplainer(key,label,model,poly,kalshi,consensus))}</p>`:''}</div>`).join('')}</div><div class="market-meta">${poly?`<span>Polymarket: ${poly.normalized?`normalized from ${Number(poly.normalization_total).toFixed(3)}`:'approximately 100% raw total'} · Volume ${money(poly.volume)}</span><span>PM updated ${esc(marketUpdated(poly))}</span>${polyUrl?`<a href="${esc(polyUrl)}" target="_blank" rel="noopener noreferrer">Open Polymarket ↗</a>`:''}`:'<span>Polymarket unavailable</span>'}${kalshi?`<span>Kalshi: ${kalshi.normalized?`normalized from ${Number(kalshi.normalization_total).toFixed(3)}`:'approximately 100% raw total'} · Volume ${money(kalshi.volume)}</span><span>Kalshi updated ${esc(marketUpdated(kalshi))}</span>${kalshiUrl?`<a href="${esc(kalshiUrl)}" target="_blank" rel="noopener noreferrer">Open Kalshi ↗</a>`:''}`:'<span>Kalshi unavailable</span>'}</div><p class="detail-note">Both exchanges are comparison-only. Kalshi uses a bid/ask midpoint when the spread is usable and otherwise falls back to the latest trade. Consensus is an equal-weight mean of the available normalized exchange estimates. None of these values enter the Bayesian fit or simulations.</p></div></article>`;
}
function outcomeDisplay(outcome, h, a) {
  if (outcome === 'home') return `${h.name} win`;
  if (outcome === 'away') return `${a.name} win`;
  return 'Draw';
}

function sourceDisplay(source) {
  return ({model:'Bayesian model', polymarket:'Polymarket', kalshi:'Kalshi', consensus:'Market consensus'})[source] || source;
}

function postgameAnalysis(f, h, a) {
  const review = f?.postgame_analysis;
  if (f?.status !== 'final' || !review?.actual || !review?.sources || !review?.scores) return '';
  const actual = review.actual.outcome;
  if (!review.sources.model || (!review.sources.polymarket && !review.sources.kalshi)) return '';
  const rows = ['model','polymarket','kalshi','consensus'].filter(source => review.sources[source] && review.scores[source]);
  const best = [...rows].sort((x,y) => Number(review.scores[x].brier) - Number(review.scores[y].brier))[0];
  const actualLabel = outcomeDisplay(actual,h,a);
  const recovered=review?.provenance?.type==='archived_git_snapshot';
  const topSummary=rows.map(source=>{
    const score=review.scores[source];
    return `${sourceDisplay(source)} ${outcomeDisplay(score.top_pick,h,a)} (${probPct(review.sources[source][score.top_pick],1)})`;
  }).join(' · ');
  const distributionRows=rows.map(source=>{
    const probabilities=review.sources[source], score=review.scores[source];
    return `<tr class="${score.correct_pick?'correct':'miss'}"><td><strong>${esc(sourceDisplay(source))}</strong></td><td>${probPct(probabilities.home,1)}</td><td>${probPct(probabilities.draw,1)}</td><td>${probPct(probabilities.away,1)}</td><td><strong>${probPct(score.actual_probability,1)}</strong></td><td>${esc(outcomeDisplay(score.top_pick,h,a))}</td><td>${score.correct_pick?'✓':'—'}</td><td>${Number(score.brier).toFixed(3)}</td><td>${Number(score.log_loss).toFixed(3)}</td></tr>`;
  }).join('');
  const modelScore=review.scores.model;
  const marketWinner=['polymarket','kalshi'].filter(source=>review.scores[source]).sort((x,y)=>Number(review.scores[x].brier)-Number(review.scores[y].brier))[0];
  const modelVsMarket=marketWinner
    ? (Number(modelScore.brier)<Number(review.scores[marketWinner].brier)
      ? `Our model produced a better probability forecast than the best available prediction market on this match.`
      : Number(modelScore.brier)>Number(review.scores[marketWinner].brier)
        ? `${sourceDisplay(marketWinner)} produced a better probability forecast than our model on this match.`
        : `Our model and ${sourceDisplay(marketWinner)} finished with the same Brier score on this match.`)
    : '';
  return `<article class="card postgame-card"><div class="card-head"><h2>Final postgame comparison</h2><span class="eyebrow">${recovered?'Historical pregame data recovered':'Frozen pregame snapshot'}</span></div><div class="card-body"><p class="postgame-lead"><strong>${esc(actualLabel)}</strong> was the final 1X2 result (${Number(review.actual.home_score)}–${Number(review.actual.away_score)}). The table below grades the exact probabilities that were stored before kickoff.</p><div class="postgame-verdict"><strong>What each forecast expected</strong><span>${esc(topSummary)}</span></div><div class="table-wrap"><table class="postgame-table"><thead><tr><th>Forecast</th><th>${esc(h.short)}</th><th>Draw</th><th>${esc(a.short)}</th><th>Prob. on actual</th><th>Top pick</th><th>Hit</th><th>Brier ↓</th><th>Log loss ↓</th></tr></thead><tbody>${distributionRows}</tbody></table></div><div class="postgame-summary"><strong>Best probability forecast: ${esc(sourceDisplay(best))}</strong><span>${esc(modelVsMarket)} Lower Brier and log-loss values indicate better probabilistic accuracy.</span></div><p class="detail-note">Snapshot captured ${esc(review.captured_at?kickoffText(review.captured_at):'before kickoff')}.${recovered?' This row was recovered from a committed historical forecast rather than reconstructed after the match.':''}</p></div></article>`;
}
function renderMatch(id) {
  const f=fixtureById(id);
  if(!f){ main.innerHTML=`<div class="page"><article class="card"><div class="empty"><h2>Match not found</h2><p>The fixture may have changed during the latest data refresh.</p><a class="button" href="#/schedule">Back to schedule</a></div></article></div>`; return; }
  const h=team(f.home), a=team(f.away), hForecast=forecast(h.slug), aForecast=forecast(a.slug);
  const model=fixtureScoreModel(f);
  const archivedPregameModel=f?.status==='final' ? f?.postgame_analysis?.sources?.model : null;
  const hasArchivedPregameModel=archivedPregameModel && ['home','draw','away'].every(key=>Number.isFinite(Number(archivedPregameModel[key])));
  const probs=hasArchivedPregameModel
    ? {home:Number(archivedPregameModel.home), draw:Number(archivedPregameModel.draw), away:Number(archivedPregameModel.away)}
    : f.probabilities;
  const max=Math.max(probs.home,probs.draw,probs.away);
  const favorite=max===probs.home?h:max===probs.away?a:null;
  const status=f.status==='final'?`Final · ${f.home_score}–${f.away_score}`:'Upcoming';
  const timing=f.kickoff?kickoffText(f.kickoff):dateText(f.date);
  const archivedCapture=hasArchivedPregameModel && f?.postgame_analysis?.captured_at ? kickoffText(f.postgame_analysis.captured_at) : '';
  const read=hasArchivedPregameModel
    ? (favorite
      ? `The frozen pregame model had ${favorite.name} as the highest-probability outcome at ${pct(max,1)}. These probabilities were captured before kickoff${archivedCapture?` (${archivedCapture})`:''} and are not recalculated using the final result.`
      : `The frozen pregame model had the draw as the most likely 1X2 outcome at ${pct(probs.draw,1)}. These probabilities were captured before kickoff${archivedCapture?` (${archivedCapture})`:''} and are not recalculated using the final result.`)
    : (favorite
      ? `${favorite.name} has the highest win probability at ${pct(max,1)}. The model’s mean scoring expectation is ${Number(f.xg_home).toFixed(2)}–${Number(f.xg_away).toFixed(2)} in expected goals.`
      : `The draw is the single most likely 1X2 outcome at ${pct(probs.draw,1)}. The model’s mean scoring expectation is ${Number(f.xg_home).toFixed(2)}–${Number(f.xg_away).toFixed(2)} in expected goals.`);
  const outcomeEyebrow=hasArchivedPregameModel?'Frozen pregame probability':'Posterior probability';
  const outcomeSubtext=hasArchivedPregameModel?'Saved before kickoff':null;
  main.innerHTML=`<div class="page match-page">
    <a class="back-link" href="#/schedule">← Back to schedule</a>
    <section class="match-hero card"><div class="match-meta"><span class="eyebrow">${esc(state.data.meta.name)} · Round ${esc(f.round)}</span><strong>${esc(timing)}</strong><span class="status-pill ${f.status==='final'?'final':''}">${esc(status)}</span></div><div class="match-teams"><a href="#/team/${h.slug}" class="match-club">${badge(h,'large')}<span><strong>${esc(h.name)}</strong><small>Home</small></span></a><div class="match-score"><strong>${f.status==='final'?`${f.home_score}–${f.away_score}`:'vs'}</strong><span>${Number(f.xg_home).toFixed(2)}–${Number(f.xg_away).toFixed(2)} xG</span></div><a href="#/team/${a.slug}" class="match-club away">${badge(a,'large')}<span><strong>${esc(a.name)}</strong><small>Away</small></span></a></div></section>
    <section class="grid match-detail-grid"><article class="card"><div class="card-head"><h2>Outcome forecast</h2><span class="eyebrow">${esc(outcomeEyebrow)}</span></div><div class="card-body"><div class="outcome-grid">${outcomeCard(`${h.short} win`,probs.home,max,outcomeSubtext||`90% range ${intervalLabel(f.probabilities.home_interval)}`)}${outcomeCard('Draw',probs.draw,max,outcomeSubtext||`90% range ${intervalLabel(f.probabilities.draw_interval)}`)}${outcomeCard(`${a.short} win`,probs.away,max,outcomeSubtext||`90% range ${intervalLabel(f.probabilities.away_interval)}`)}</div><div class="model-read"><strong>${hasArchivedPregameModel?'Pregame model read':'Model read'}</strong><p>${esc(read)}</p></div></div></article><article class="card"><div class="card-head"><h2>Most likely scores</h2><span class="eyebrow">Mean-xG Poisson</span></div><div class="card-body likely-scores">${likelyScores(model.matrix,h,a)}<div class="score-explainer"><strong>How to read this</strong><p>${esc(exactScoreExplainer(model.matrix,probs,h,a))}</p></div><p class="detail-note">${hasArchivedPregameModel?'The 1X2 probabilities above are the frozen pregame forecast. Exact-score probabilities on this completed-match page use the currently stored mean expected goals, so they should be read separately from the archived pregame 1X2 forecast.':'Exact-score probabilities use the mean expected goals. The 1X2 probabilities include posterior uncertainty, so the two views will not match perfectly.'}</p></div></article></section>
    ${matchMarketComparison(f,h,a)}
    ${postgameAnalysis(f,h,a)}
    <section class="grid rating-grid">${ratingCard(h,hForecast)}${ratingCard(a,aForecast)}</section>
    <section class="grid match-detail-grid"><article class="card"><div class="card-head"><h2>Exact score matrix</h2><span class="eyebrow">Rows ${esc(h.short)} · columns ${esc(a.short)}</span></div><div class="card-body"><div class="score-matrix-axis"><span>Home goals ↓</span><span>Away goals →</span></div><div class="table-wrap">${scoreMatrix(model.matrix)}</div><p class="detail-note">The matrix displays 0–6 goals for each club. The total-goals section below uses the complete Poisson distribution, including outcomes beyond the visible matrix.</p></div></article><article class="card"><div class="card-head"><h2>Recent form</h2><span class="eyebrow">Last five completed</span></div><div class="card-body form-columns"><div><h3>${esc(h.short)}</h3>${recentForm(h.slug,f.id)}</div><div><h3>${esc(a.short)}</h3>${recentForm(a.slug,f.id)}</div></div></article></section>
    ${goalTotalsCard(f)}
  </div>`;
}

function accuracyMetric(value, type='number') {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
  if (type === 'pct') return probPct(Number(value),1);
  return Number(value).toFixed(3);
}

function accuracySourceRows(metrics, sources=['model','polymarket','kalshi','consensus']) {
  return sources.map(source => {
    const row = metrics?.[source] || {};
    return `<tr><td><strong>${esc(row.label || sourceDisplay(source))}</strong></td><td>${Number(row.matches||0).toLocaleString()}</td><td>${accuracyMetric(row.pick_accuracy,'pct')}</td><td>${accuracyMetric(row.brier)}</td><td>${accuracyMetric(row.log_loss)}</td><td>${accuracyMetric(row.avg_actual_probability,'pct')}</td></tr>`;
  }).join('');
}

function comparisonPanel(title, comparison, sources) {
  const n = Number(comparison?.matches || 0);
  return `<article class="card accuracy-comparison"><div class="card-head"><h2>${esc(title)}</h2><span class="eyebrow">${n} shared match${n===1?'':'es'}</span></div><div class="card-body">${n?`<div class="table-wrap"><table><thead><tr><th>Forecast</th><th>Matches</th><th>Top-pick accuracy</th><th>Brier ↓</th><th>Log loss ↓</th><th>Avg prob. on actual</th></tr></thead><tbody>${accuracySourceRows(comparison.sources,sources)}</tbody></table></div>`:'<div class="empty"><p>No completed matches with this exact coverage set have been graded yet.</p></div>'}</div></article>`;
}

function totalsAccuracyRows(metrics, sources=['model','polymarket','kalshi','consensus']) {
  return sources.map(source=>{
    const row=metrics?.[source]||{};
    return `<tr><td><strong>${esc(row.label||sourceDisplay(source))}</strong></td><td>${Number(row.matches||0).toLocaleString()}</td><td>${Number(row.line_forecasts||0).toLocaleString()}</td><td>${accuracyMetric(row.pick_accuracy,'pct')}</td><td>${accuracyMetric(row.brier)}</td><td>${accuracyMetric(row.log_loss)}</td><td>${accuracyMetric(row.avg_actual_probability,'pct')}</td></tr>`;
  }).join('');
}
function totalsComparisonPanel(title, comparison, sources) {
  const n=Number(comparison?.line_forecasts||0), matches=Number(comparison?.matches||0);
  return `<article class="card accuracy-comparison"><div class="card-head"><h2>${esc(title)}</h2><span class="eyebrow">${n} shared line${n===1?'':'s'} · ${matches} match${matches===1?'':'es'}</span></div><div class="card-body">${n?`<div class="table-wrap"><table><thead><tr><th>Forecast</th><th>Matches</th><th>Lines</th><th>Side accuracy</th><th>Brier ↓</th><th>Log loss ↓</th><th>Avg prob. on actual</th></tr></thead><tbody>${totalsAccuracyRows(comparison.sources,sources)}</tbody></table></div>`:'<div class="empty"><p>No completed matches with shared frozen total-goals lines have been graded yet.</p></div>'}</div></article>`;
}

function renderAccuracy() {
  const accuracy=state.data.accuracy || {};
  const history=(state.data.prediction_history || []).filter(row=>row.status==='final').sort((a,b)=>String(b.date||'').localeCompare(String(a.date||'')));
  const comparisons=accuracy.comparisons || {};
  const totalsAccuracy=accuracy.goal_totals || {};
  const totalsComparisons=totalsAccuracy.comparisons || {};
  const tMap=teamMap();
  const recent=history.slice(0,25);
  const tracked=Number(accuracy.graded_matches||0);
  main.innerHTML=`<div class="page accuracy-page">
    ${pageHead('Forecast accountability','Historical accuracy','A running audit of genuine pregame probabilities from our Bayesian model, Polymarket and Kalshi. Archived nightly snapshots are recovered from Git history when available, while new matches continue to be frozen before kickoff.')}
    <div class="notice"><strong>How accuracy is measured</strong><span>Multiclass Brier score is the primary metric and lower is better. Log loss also rewards calibrated confidence. Top-pick accuracy simply asks whether the highest-probability home/draw/away outcome occurred.</span></div>
    <section class="grid accuracy-kpis">${metric('Graded matches',tracked.toLocaleString(),'Genuine pregame snapshots')}${metric('Recovered history',Number(accuracy.recovered_matches||0).toLocaleString(),'Archived Git snapshots')}${metric('Coverage',accuracy.coverage_start?`${accuracy.coverage_start} → ${accuracy.coverage_end||accuracy.coverage_start}`:'—','Available stored history')}${metric('Primary metric','Brier ↓','Lower is better')}</section>
    <article class="card"><div class="card-head"><h2>Overall recorded performance</h2><span class="eyebrow">Coverage differs by source</span></div><div class="card-body">${tracked?`<div class="table-wrap"><table class="accuracy-table"><thead><tr><th>Forecast</th><th>Matches</th><th>Top-pick accuracy</th><th>Brier ↓</th><th>Log loss ↓</th><th>Avg prob. on actual</th></tr></thead><tbody>${accuracySourceRows(accuracy.overall)}</tbody></table></div>`:'<div class="empty"><h3>No graded matches yet</h3><p>No completed pregame snapshots have been recovered yet. Run the historical backfill once, then future matches will continue to be tracked automatically.</p></div>'}<p class="detail-note">Overall rows can contain different numbers of matches because prediction-market coverage varies. Use the shared-match comparisons below for the fairest model-versus-market comparison.</p></div></article>
    <section class="grid accuracy-pair-grid">${comparisonPanel('Model vs Polymarket',comparisons.model_vs_polymarket,['model','polymarket'])}${comparisonPanel('Model vs Kalshi',comparisons.model_vs_kalshi,['model','kalshi'])}</section>
    ${comparisonPanel('All three on the same matches',comparisons.all_three,['model','polymarket','kalshi'])}
    <div class="accuracy-section-heading"><span class="eyebrow">Scoring calibration</span><h2>Goal totals accuracy</h2><p>Each frozen Over/Under line is graded as a binary probability forecast. Lower Brier and log loss are better; shared-line panels compare the exact same match and threshold on both sources.</p></div>
    <article class="card"><div class="card-head"><h2>Overall total-goals performance</h2><span class="eyebrow">Coverage differs by line</span></div><div class="card-body"><div class="table-wrap"><table class="accuracy-table"><thead><tr><th>Forecast</th><th>Matches</th><th>Lines</th><th>Side accuracy</th><th>Brier ↓</th><th>Log loss ↓</th><th>Avg prob. on actual</th></tr></thead><tbody>${totalsAccuracyRows(totalsAccuracy.overall)}</tbody></table></div><p class="detail-note">Totals history begins only when an actual pregame total-goals price was captured. Older 1X2 history does not manufacture missing totals prices retroactively.</p></div></article>
    <section class="grid accuracy-pair-grid">${totalsComparisonPanel('Totals · Model vs Polymarket',totalsComparisons.model_vs_polymarket,['model','polymarket'])}${totalsComparisonPanel('Totals · Model vs Kalshi',totalsComparisons.model_vs_kalshi,['model','kalshi'])}</section>
    ${totalsComparisonPanel('Totals · All three on the same lines',totalsComparisons.all_three,['model','polymarket','kalshi'])}
    <article class="card"><div class="card-head"><h2>Recent graded matches</h2><span class="eyebrow">Frozen before kickoff</span></div><div class="card-body">${recent.length?`<div class="accuracy-history">${recent.map(row=>{const h=tMap[row.home],a=tMap[row.away],actual=row.actual?.outcome; const model=row.scores?.model; return `<a class="accuracy-match" href="#/match/${encodeURIComponent(row.fixture_id)}"><span><small>${esc(row.date||'')}</small><strong>${esc(h?.short||row.home)} vs ${esc(a?.short||row.away)}</strong></span><span><small>Actual</small><strong>${esc(outcomeDisplay(actual,h||{name:row.home},a||{name:row.away}))}</strong></span><span><small>Model on actual</small><strong>${model?probPct(model.actual_probability,1):'—'}</strong></span><span><small>Model Brier</small><strong>${model?Number(model.brier).toFixed(3):'—'}</strong></span><em>Review →</em></a>`;}).join('')}</div>`:'<div class="empty"><p>No completed tracked matches yet.</p></div>'}</div></article>
  </div>`;
}

function renderScores() {
  const tMap=teamMap();
  const finals=state.data.fixtures.filter(f=>f.status==='final').sort((a,b)=>b.date.localeCompare(a.date)).slice(0,100);
  main.innerHTML=`<div class="page">${pageHead('Frozen forecast audit','Scores','Completed matches beside the probability the model currently assigns to the outcome that occurred. A filled dot marks the model’s most likely 1X2 result.')}${notice()}<article class="card"><div class="table-wrap"><table><thead><tr><th>Date</th><th>Match</th><th>Score</th><th>Actual outcome</th><th>Assigned probability</th><th>Most likely?</th></tr></thead><tbody>${finals.map(f=>{
    const outcome=f.home_score>f.away_score?'home':f.home_score<f.away_score?'away':'draw';
    const max=Math.max(f.probabilities.home,f.probabilities.draw,f.probabilities.away);
    return `<tr><td>${compactDate(f.date)}</td><td><span class="table-team">${badge(tMap[f.home])}${esc(tMap[f.home].short)} vs ${esc(tMap[f.away].short)}</span></td><td><b>${f.home_score}–${f.away_score}</b></td><td>${outcome==='home'?tMap[f.home].name:outcome==='away'?tMap[f.away].name:'Draw'}</td><td>${pct(f.probabilities[outcome],1)}</td><td>${f.probabilities[outcome]===max?'●':'○'}</td></tr>`;
  }).join('')}</tbody></table></div></article></div>`;
}

function newsType(entry) {
  if (entry.type) return entry.type;
  if (entry.headline === 'Automated model refresh completed' || !entry.team) return 'system';
  return 'team_update';
}

function newsCategory(entry) {
  const type = newsType(entry);
  if (type === 'forecast_mover') return 'forecast';
  if (type === 'warning') return 'warning';
  if (type === 'system') return 'system';
  return 'team';
}

function systemIdentity(kind = 'system') {
  const warning = kind === 'warning';
  return `<span class="team-inline system-identity"><span class="badge system-badge ${warning ? 'warning-badge' : ''}">${warning ? '!' : 'TF'}</span><span><strong>${warning ? 'Data warning' : 'Touchline Forecast'}</strong><small>${warning ? 'Review required' : 'System update'}</small></span></span>`;
}

function detailValue(value) {
  if (value === null || value === undefined || value === '') return '—';
  if (Array.isArray(value)) return value.map(esc).join(', ');
  return esc(value);
}

function newsDetails(entry) {
  const details = entry.details || {};
  const type = newsType(entry);
  if (type === 'forecast_mover') {
    const before = Number(details.before || 0);
    const after = Number(details.after || 0);
    const delta = Number(details.delta || 0);
    const max = Math.max(before, after, 0.001);
    return `<div class="news-expanded mover-details">
      <div class="movement-summary">
        <div><small>Previous snapshot</small><strong>${probPct(before)}</strong></div>
        <span class="movement-arrow">→</span>
        <div><small>Current snapshot</small><strong>${probPct(after)}</strong></div>
        <span class="movement-delta ${delta >= 0 ? 'up' : 'down'}">${delta >= 0 ? '▲' : '▼'} ${Math.abs(delta * 100).toFixed(1)} pts</span>
      </div>
      <div class="movement-bars" aria-label="Probability movement">
        <span><i style="width:${clamp(before / max * 100, 1, 100)}%"></i></span>
        <span class="current"><i style="width:${clamp(after / max * 100, 1, 100)}%"></i></span>
      </div>
      <dl class="news-facts">
        <div><dt>Outcome</dt><dd>${detailValue(details.metric)}</dd></div>
        <div><dt>Projected points</dt><dd>${detailValue(details.projected_points_before)} → ${detailValue(details.projected_points_after)}</dd></div>
        <div><dt>Average finish</dt><dd>${detailValue(details.avg_position_before)} → ${detailValue(details.avg_position_after)}</dd></div>
        <div><dt>Why it moved</dt><dd>${detailValue(details.explanation || 'New results, schedule information and updated model ratings were incorporated in the nightly rebuild.')}</dd></div>
      </dl>
      ${entry.team ? `<a class="news-action" href="#/team/${encodeURIComponent(entry.team)}">Open team forecast →</a>` : ''}
    </div>`;
  }

  const facts = [
    ['Published', details.generated_at ? kickoffText(details.generated_at) : entry.date ? dateText(entry.date) : '—'],
    ['Model version', details.model_version],
    ['Matches fitted', details.matches_fitted?.toLocaleString?.() || details.matches_fitted],
    ['Simulations', details.simulations?.toLocaleString?.() || details.simulations],
    ['Completed league matches', details.completed_matches],
    ['Data sources', details.sources],
    ['Validation', details.validation],
    ['Deployment', details.deployment],
    ['Inference', details.inference],
    ['Polymarket season quotes', details.polymarket_season_quotes],
    ['Polymarket match quotes', details.polymarket_match_quotes],
    ['Polymarket match coverage', details.polymarket_match_coverage === null || details.polymarket_match_coverage === undefined ? null : probPct(details.polymarket_match_coverage, 1)],
    ['Kalshi season quotes', details.kalshi_season_quotes],
    ['Kalshi match quotes', details.kalshi_match_quotes],
    ['Kalshi match coverage', details.kalshi_match_coverage === null || details.kalshi_match_coverage === undefined ? null : probPct(details.kalshi_match_coverage, 1)],
    ['Review status', details.review_status],
    ['Model treatment', details.model_treatment],
    ['Affected fixtures', details.affected_fixtures],
  ].filter(([, value]) => value !== null && value !== undefined && value !== '');

  return `<div class="news-expanded">
    ${facts.length ? `<dl class="news-facts">${facts.map(([label, value]) => `<div><dt>${esc(label)}</dt><dd>${detailValue(value)}</dd></div>`).join('')}</dl>` : '<p class="news-detail-copy">No additional structured details were supplied for this entry.</p>'}
    ${details.note ? `<p class="news-detail-copy">${esc(details.note)}</p>` : ''}
    ${entry.team ? `<a class="news-action" href="#/team/${encodeURIComponent(entry.team)}">Open team forecast →</a>` : ''}
  </div>`;
}

function renderNews() {
  const tMap = teamMap();
  const entries = (state.data.news || []).map((entry, index) => ({...entry, _index:index, _type:newsType(entry), _category:newsCategory(entry)}));
  const movers = entries.filter(entry => entry._type === 'forecast_mover').sort((a,b) => Math.abs(Number(b.details?.delta || 0)) - Math.abs(Number(a.details?.delta || 0)));
  const filtered = state.newsFilter === 'all' ? entries : entries.filter(entry => entry._category === state.newsFilter);
  const filterCounts = {
    all: entries.length,
    forecast: entries.filter(entry => entry._category === 'forecast').length,
    team: entries.filter(entry => entry._category === 'team').length,
    system: entries.filter(entry => entry._category === 'system').length,
    warning: entries.filter(entry => entry._category === 'warning').length,
  };
  const filters = [
    ['all','All'],
    ['forecast','Forecast changes'],
    ['team','Team updates'],
    ['system','System'],
    ['warning','Warnings'],
  ];

  const moversPanel = movers.length ? `<section class="card movers-panel">
    <div class="card-head"><div><div class="eyebrow">Since the prior snapshot</div><h2>Biggest forecast movements</h2></div><button class="text-button" data-news-filter="forecast">View all changes →</button></div>
    <div class="movers-grid">${movers.slice(0,3).map(entry => {
      const t = tMap[entry.team];
      const delta = Number(entry.details?.delta || 0);
      return `<a href="#/team/${encodeURIComponent(entry.team)}" class="mover-tile">${t ? badge(t) : ''}<span><strong>${t ? esc(t.name) : esc(entry.team || 'Team')}</strong><small>${esc(entry.details?.metric || 'Forecast')}</small></span><b class="${delta >= 0 ? 'up' : 'down'}">${delta >= 0 ? '▲' : '▼'} ${Math.abs(delta*100).toFixed(1)} pts</b></a>`;
    }).join('')}</div>
  </section>` : '';

  const cards = filtered.map(entry => {
    const type = entry._type;
    const t = entry.team ? tMap[entry.team] : null;
    const identity = (type === 'system' || type === 'warning' || !t)
      ? systemIdentity(type)
      : teamInline(t, entry.impact || (type === 'forecast_mover' ? 'Forecast movement' : 'Team update'));
    const statusLabel = type === 'warning' ? '⚠ Warning' : entry.affects_forecast ? '◆ Affects forecast' : 'Review pending';
    const statusClass = type === 'warning' ? 'warning-pill' : entry.affects_forecast ? 'affects' : 'pending';
    return `<details class="card news-item news-${esc(type)}">
      <summary class="news-summary">
        <time>${entry.date ? dateText(entry.date) : 'Latest update'}</time>
        <div class="news-main">${identity}<h3>${esc(entry.headline)}</h3><p>${esc(entry.summary)}</p></div>
        <span class="news-status ${statusClass}">${statusLabel}</span>
        <span class="expand-control"><span class="show-label">View details</span><span class="hide-label">Hide details</span><b>⌄</b></span>
      </summary>
      ${newsDetails(entry)}
    </details>`;
  }).join('');

  main.innerHTML = `<div class="page">
    ${pageHead('Transparent change log','Model news','See what changed in each nightly rebuild, why team probabilities moved, and whether any reviewed team news or data warnings affected the forecast.')}
    ${notice()}
    ${moversPanel}
    <div class="news-toolbar" role="group" aria-label="Filter model news">
      ${filters.map(([key,label]) => `<button class="news-filter ${state.newsFilter === key ? 'active' : ''}" data-news-filter="${key}">${label}<span>${filterCounts[key]}</span></button>`).join('')}
    </div>
    <section class="news-list">${cards || '<div class="card empty"><h2>No entries in this category</h2><p>Try another filter or wait for the next nightly model rebuild.</p></div>'}</section>
  </div>`;

  document.querySelectorAll('[data-news-filter]').forEach(button => button.addEventListener('click', event => {
    event.preventDefault();
    state.newsFilter = button.dataset.newsFilter;
    renderNews();
  }));
}

function renderMethod() {
  const leagueSpecific = state.league==='epl'
    ? `<p>Each run simulates every remaining league match, ranks all 20 clubs by points, goal difference, goals scored and wins, then records the champion, top-four, European and bottom-three outcomes.</p>`
    : `<p>Each run ranks the Eastern and Western Conferences separately, identifies the Shield winner, plays the 8–9 Wild Card matches, Round One best-of-three series, single-elimination conference rounds and MLS Cup.</p>`;
  main.innerHTML=`<div class="page">${pageHead('Transparent by design','How we predict','The implementation below follows a Bayesian state-space Poisson approach and separates statistical forecasts from independent Polymarket and Kalshi prices.')}${notice()}<section class="grid method-layout">
    <aside class="card method-toc"><div class="eyebrow">On this page</div><a href="#model">1. Match model</a><a href="#dynamic">2. Dynamic team strength</a><a href="#simulation">3. Season simulation</a><a href="#league">4. League rules</a><a href="#market-comparison">5. Prediction-market comparison</a><a href="#validation">6. Validation</a><a href="#limitations">7. Limitations</a></aside>
    <article class="card method-copy">
      <section id="model"><div class="eyebrow">01</div><h2>Match-level goal model</h2><p>Home and away goals are modeled as Poisson variables. Each club has a time-specific attacking effect and defensive strength. Home advantage is learned from results. Current squad value is used as a modest future-fixture covariate; for EPL, today’s squad values are not applied retrospectively to older seasons.</p><div class="equation">G_home ~ Poisson(λ_home)<br>G_away ~ Poisson(λ_away)<br><br>log(λ_home) = α + H + A_home,t − D_away,t + βv log(V_home / V_away)<br>log(λ_away) = α + A_away,t − D_home,t − βv log(V_home / V_away)</div><p>The current posterior mean implies μ=${state.data.model.base_goals}, H=${state.data.model.home_advantage_log} and βv=${state.data.model.market_value_coefficient}. Polymarket and Kalshi are not inputs to these values.</p></section>
      <section id="dynamic"><div class="eyebrow">02</div><h2>Dynamic team strength</h2><p>Attack and defense are latent states that evolve every ${state.data.model.bucket_days} days. The fitted history ends at the last completed match rather than creating offseason state changes with no match evidence. The model was fitted to ${state.data.model.matches_fitted.toLocaleString()} completed matches and retains ${state.data.model.posterior_samples.toLocaleString()} posterior samples.</p><div class="equation">Historical: rating_t = rating_t−1 + Normal(0, σ)<br>Preseason: rating_start = w × fitted state + (1−w) × preseason target<br>Future: rating_next = ${state.data.model.future_state_retention ?? 'retention'} × rating_previous + Normal(0, σ)</div><p>For EPL, the preseason target blends prior-season scoring rates with maintained attack and defense seeds. Promoted clubs rely more heavily on those priors, and the adjustment fades over each club’s first 10 league matches. Future paths use modest mean reversion so uncertainty does not become an unconstrained season-long random walk.</p></section>
      <section id="simulation"><div class="eyebrow">03</div><h2>From matches to season probabilities</h2><ol><li>Draw one set of attack, defense and coefficient values from the posterior.</li><li>Simulate every unplayed match.</li><li>Apply official table and postseason rules.</li><li>Record each club’s final position and outcomes.</li><li>Repeat thousands of times.</li></ol><p>A model probability is the share of simulations in which the event occurred. It is never manually adjusted after the run.</p></section>
      <section id="league"><div class="eyebrow">04</div><h2>${esc(state.data.meta.name)} rules</h2>${leagueSpecific}<p>Competition rules belong in a configuration layer so changes to playoff formats, qualification places or tiebreakers do not require rewriting the statistical model.</p></section>
      <section id="market-comparison"><div class="eyebrow">05</div><h2>Independent Polymarket + Kalshi comparison</h2><p>Both exchanges are displayed as separate benchmarks, never as training data, priors or calibration targets. The Bayesian model is fitted and the season is simulated before either market source is attached to the published snapshot.</p><div class="market-method-grid"><div><strong>Polymarket</strong><p>Season-winner contracts and exact full-match home/draw/away markets are normalized within their matched event. Raw contract prices are retained so the normalized comparison remains auditable.</p></div><div><strong>Kalshi</strong><p>The pipeline retrieves the exact season-winner event and open league-game events. For each Kalshi contract, it uses the midpoint of the best Yes bid and ask when the spread is usable; otherwise it falls back to the latest trade. Bid, ask, last trade and spread are retained.</p></div><div><strong>Individual matches</strong><p>A match quote is published only when both clubs match, the event date is verified, and all three 90-minute outcomes—home win, draw and away win—are available. Each exchange’s three outcomes are normalized independently to 100%.</p></div><div><strong>Market consensus</strong><p>When at least one external source is available, the site reports a market consensus. When both are available, it is the equal-weight mean of the normalized Polymarket and Kalshi estimates. It remains comparison-only and never feeds back into the Bayesian model.</p></div></div><div class="equation">Exchange-normalized probability = source estimate ÷ sum of estimates in the exact event<br><br>Market consensus = mean of available normalized Polymarket and Kalshi estimates<br><br>Model vs consensus = Bayesian probability − market consensus</div><p>A dash means no exact active market was safely matched. The site does not substitute sportsbook odds or infer a missing exchange price from the other exchange. Prediction-market prices can change between nightly snapshots.</p></section>
      <section id="validation"><div class="eyebrow">06</div><h2>How the production model is checked</h2><p>Every EPL rebuild removes the latest eligible historical matches, fits a smaller training-only model, and predicts the unseen holdout before the full production fit. The resulting Brier score, log loss and skill versus a naive frequency baseline are stored with the forecast and surfaced in Model News.</p><table><thead><tr><th>Metric</th><th>Purpose</th></tr></thead><tbody><tr><td>Multiclass Brier score</td><td>Accuracy of home/draw/away probabilities</td></tr><tr><td>Log loss</td><td>Penalizes confidently wrong forecasts</td></tr><tr><td>Calibration</td><td>Tests whether 60% events occur about 60% of the time</td></tr><tr><td>Ranked probability score</td><td>Quality of final-position distributions</td></tr><tr><td>Baseline comparison</td><td>Checks whether the holdout beats naive outcome frequencies</td></tr></tbody></table><p>Large model-to-market gaps create diagnostic review warnings. These warnings never alter the Bayesian forecast or either exchange’s displayed estimate.</p></section>
      <section id="limitations"><div class="eyebrow">07</div><h2>Current limitations</h2><ul><li>Fixtures and results depend on the seasons available through the connected data sources.</li><li>Squad values and attack/defense seeds remain manually maintained and should be refreshed after transfer windows.</li><li>The nightly holdout checks match outcomes; it is not yet a full historical backtest of preseason title probabilities.</li><li>Polymarket and Kalshi market availability varies. Season contracts can become unavailable, and individual match markets are commonly listed closer to kickoff.</li><li>Kalshi bid/ask spreads can be wide in thin markets; the site exposes the range rather than presenting the midpoint as perfectly precise.</li><li>Prediction-market estimates reflect trader activity and liquidity, not objective ground truth.</li><li>Historical match accuracy uses only genuine pre-kickoff snapshots. Older rows are recovered from committed nightly JSON snapshots when they exist; the system never substitutes a post-match model probability.</li><li>Player availability, rest, travel and congestion are not yet included.</li><li>The scoring model is independent Poisson; a future validation phase should compare it with a Dixon–Coles correction.</li></ul></section>
    </article>
  </section></div>`;
}

function renderTeam(slug) {
  const t=team(slug);
  if(!t){ location.hash='#/forecast'; return; }
  const f=forecast(slug), c=current(slug);
  const poly=marketProbability(f), kalshi=kalshiProbability(f), consensus=consensusProbability(f);
  const upcoming=state.data.fixtures.filter(x=>x.status!=='final'&&(x.home===slug||x.away===slug)).sort((a,b)=>a.date.localeCompare(b.date)).slice(0,8);
  const metrics=state.league==='epl'
    ? [['Projected points',f.projected_points.toFixed(1),'Current model snapshot'],['Average finish',f.avg_position.toFixed(1),'Current model snapshot'],['Title',probPct(f.title,1),'Bayesian probability'],['Top four',probPct(f.top4,1),'Bayesian probability'],['Polymarket',poly===null?'—':probPct(poly,1),marketSourceNote(f.market_details)],['Kalshi',kalshi===null?'—':probPct(kalshi,1),kalshiSourceNote(f.kalshi_details)],['Market consensus',consensus===null?'—':probPct(consensus,1),consensusSourceNote(f)]]
    : [['Projected points',f.projected_points.toFixed(1),'Current model snapshot'],['Average overall',f.avg_position.toFixed(1),'Current model snapshot'],['Shield',probPct(f.shield,1),'Bayesian probability'],['Playoffs',probPct(f.playoffs,1),'Bayesian probability'],['MLS Cup',probPct(f.champion,1),'Bayesian probability'],['Polymarket',poly===null?'—':probPct(poly,1),marketSourceNote(f.market_details)],['Kalshi',kalshi===null?'—':probPct(kalshi,1),kalshiSourceNote(f.kalshi_details)],['Market consensus',consensus===null?'—':probPct(consensus,1),consensusSourceNote(f)]];
  const max=Math.max(...f.position_distribution);
  const polyUrl=marketEventUrl(f.market_details);
  const kalshiUrl=kalshiEventUrl(f.kalshi_details);
  const marketPanel=(f.market_details||f.kalshi_details)?`<div class="team-market-panel"><div><small>Bayesian ${outcomeLabel()}</small><strong>${probPct(f[outcomeKey()],1)}</strong></div><div><small>Polymarket</small><strong>${poly===null?'—':probPct(poly,1)}</strong></div><div><small>Kalshi</small><strong>${kalshi===null?'—':probPct(kalshi,1)}</strong></div><div><small>Market consensus</small><strong>${consensus===null?'—':probPct(consensus,1)}</strong></div><div class="team-market-meta">${f.market_details?`<span>Polymarket ${f.market_details.normalized?'normalized':'raw'} · Volume ${money(f.market_details.volume)}</span><span>PM updated ${esc(marketUpdated(f.market_details))}</span>${polyUrl?`<a href="${esc(polyUrl)}" target="_blank" rel="noopener noreferrer">Open Polymarket ↗</a>`:''}`:'<span>Polymarket unavailable</span>'}${f.kalshi_details?`<span>Kalshi ${esc(f.kalshi_details.estimate_method==='bid_ask_midpoint'?'bid/ask midpoint':'latest trade')} · Volume ${money(f.kalshi_details.volume)}</span>${f.kalshi_details.bid!==null&&f.kalshi_details.bid!==undefined&&f.kalshi_details.ask!==null&&f.kalshi_details.ask!==undefined?`<span>Bid–ask ${probPct(f.kalshi_details.bid,1)}–${probPct(f.kalshi_details.ask,1)}</span>`:''}<span>Kalshi updated ${esc(marketUpdated(f.kalshi_details))}</span>${kalshiUrl?`<a href="${esc(kalshiUrl)}" target="_blank" rel="noopener noreferrer">Open Kalshi ↗</a>`:''}`:'<span>Kalshi unavailable</span>'}<span>Model vs consensus ${f.consensus_edge===null||f.consensus_edge===undefined?'—':signedPct(f.consensus_edge)}</span></div></div>`:`<div class="team-market-panel unavailable"><strong>No active prediction-market season-winner contract matched</strong><p>The Bayesian forecast remains available. This page does not estimate or substitute an external-market probability.</p></div>`;
  main.innerHTML=`<div class="page"><section class="team-hero" style="--team-color:${t.color}">${badge(t)}<div class="eyebrow" style="color:var(--lime)">${esc(t.conference)} · ${esc(state.data.meta.season)}</div><h1>${esc(t.name)}</h1><p>${c.p} played · ${c.pts} points · ${c.gf} GF · ${c.ga} GA</p></section>
  <section class="grid team-metrics">${metrics.map(([l,v,d])=>metric(l,v,d)).join('')}</section>
  ${notice()}
  <article class="card team-market-card"><div class="card-head"><h2>Model vs prediction markets</h2><span class="eyebrow">Independent comparison</span></div><div class="card-body">${marketPanel}</div></article>
  <section class="grid split team-profile-grid"><article class="card"><div class="card-head"><h2>Final-position distribution</h2><span class="eyebrow">${state.league==='epl'?'1–20':'Overall 1–30'}</span></div><div class="card-body"><div class="dist-chart">${f.position_distribution.map((v,i)=>`<div class="dist-bar" title="Position ${i+1}: ${pct(v,1)}"><i style="height:${Math.max(2,v/max*150)}px"></i><span>${i+1}</span></div>`).join('')}</div></div></article>
  <article class="card"><div class="card-head"><h2>Model profile</h2></div><div class="card-body"><div class="grid profile-metrics">${metric('Attack rating',f.attack.toFixed(2),'Posterior mean')}${metric('Defense strength',Number(f.defense_strength??(-f.defense)).toFixed(2),'Higher is stronger')}${metric('Squad value',`€${t.market_value}m`,'Future-fixture covariate')}${metric('vs consensus',f.consensus_edge===null||f.consensus_edge===undefined?'—':signedPct(f.consensus_edge),consensus===null?'Market unavailable':'Model minus market consensus')}</div></div></article></section>
  <article class="card" style="margin-top:16px"><div class="card-head"><h2>Next fixtures</h2><a href="#/matchups">Open matchup lab →</a></div><div class="fixture-list">${upcoming.map(x=>fixtureDetailed(x,teamMap())).join('')||'<div class="empty">No remaining fixtures.</div>'}</div></article></div>`;
}

function openSearch() {
  searchDialog.showModal();
  teamSearch.value='';
  renderSearch('');
  setTimeout(()=>teamSearch.focus(),30);
}
function renderSearch(query) {
  const q=query.trim().toLowerCase();
  const rows=state.data.teams.filter(t=>!q||t.name.toLowerCase().includes(q)||t.short.toLowerCase().includes(q)).slice(0,20);
  searchResults.innerHTML=rows.map(t=>{const f=forecast(t.slug), poly=marketProbability(f), kalshi=kalshiProbability(f), consensus=consensusProbability(f); return `<div class="search-result" data-team="${t.slug}">${badge(t)}<span><strong>${esc(t.name)}</strong><small>${esc(t.conference)} · Model ${probPct(f[outcomeKey()],1)}${poly===null?'':` · PM ${probPct(poly,1)}`}${kalshi===null?'':` · Kalshi ${probPct(kalshi,1)}`}${consensus===null?'':` · Consensus ${probPct(consensus,1)}`}</small></span></div>`;}).join('')||'<div class="empty">No clubs found.</div>';
  document.querySelectorAll('.search-result[data-team]').forEach(el=>el.addEventListener('click',()=>{searchDialog.close();location.hash=`#/team/${el.dataset.team}`;}));
}

window.addEventListener('hashchange', renderRoute);
document.querySelectorAll('.league-button').forEach(b=>b.addEventListener('click',()=>switchLeague(b.dataset.league)));
if (themeToggle) themeToggle.addEventListener('click', toggleTheme);
document.getElementById('search-open').addEventListener('click', openSearch);
document.getElementById('mobile-menu').addEventListener('click',()=>document.getElementById('sidebar').classList.toggle('open'));
teamSearch.addEventListener('input',e=>renderSearch(e.target.value));
document.addEventListener('keydown',e=>{
  if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='k'){e.preventDefault();openSearch();}
  if(e.key==='Escape'&&searchDialog.open)searchDialog.close();
});

applyTheme(state.themePreference, false);
window.matchMedia('(prefers-color-scheme: dark)').addEventListener?.('change', () => {
  if (state.themePreference === 'system') applyTheme('system', false);
});

loadData();
