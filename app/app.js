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
};

const main = document.getElementById('main');
const searchDialog = document.getElementById('search-dialog');
const teamSearch = document.getElementById('team-search');
const searchResults = document.getElementById('search-results');

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

function teamMap() { return Object.fromEntries(state.data.teams.map(t => [t.slug, t])); }
function forecastMap() { return Object.fromEntries(state.data.forecast.map(f => [f.team, f])); }
function tableMap() { return Object.fromEntries(state.data.current_table.map(r => [r.team, r])); }
function team(slug) { return teamMap()[slug]; }
function forecast(slug) { return forecastMap()[slug]; }
function current(slug) { return tableMap()[slug] || {p:0,w:0,d:0,l:0,gf:0,ga:0,gd:0,pts:0}; }
function fixtureById(id) { return state.data.fixtures.find(f => String(f.id) === String(id)); }
function outcomeKey() { return state.league === 'epl' ? 'title' : 'champion'; }
function outcomeLabel() { return state.league === 'epl' ? 'Title' : 'MLS Cup'; }
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
  const next = state.data.fixtures.filter(f => f.status !== 'final').sort((a,b) => a.date.localeCompare(b.date)).slice(0,6);
  const top = leaders[0];
  main.innerHTML = `<div class="page">
    ${pageHead('Forecast laboratory', `${esc(state.data.meta.name)} <em>forecast</em>`, state.league === 'epl'
      ? 'A transparent, simulation-based view of the title race, European qualification and relegation.'
      : 'A transparent, simulation-based view of the Supporters’ Shield, conference races and MLS Cup playoffs.')}
    ${notice()}
    <section class="grid metrics">
      ${metric(outcomeLabel() + ' favorite', esc(tMap[top.team].short), pct(top[outcomeKey()]) + ' model probability')}
      ${metric('Matches modeled', state.data.fixtures.length.toLocaleString(), `${completed} completed · ${remaining} remaining`)}
      ${metric('Clubs', state.data.teams.length, state.league === 'mls' ? '15 East · 15 West' : '38 matches per club')}
      ${metric('Simulation runs', state.data.meta.iterations.toLocaleString(), 'Posterior uncertainty included')}
    </section>
    <section class="grid split">
      <article class="card">
        <div class="card-head"><h2>${outcomeLabel()} forecast</h2><a href="#/forecast">Full forecast →</a></div>
        <div class="forecast-list">
          ${leaders.map((f,i) => {
            const t = tMap[f.team];
            const v = f[outcomeKey()] || 0;
            return `<a class="forecast-item" href="#/team/${t.slug}"><span class="rank">${String(i+1).padStart(2,'0')}</span>${teamInline(t, `${f.projected_points} projected pts`)}<span class="prob-bar"><span style="width:${Math.max(2,v*100)}%"></span></span><span class="prob-value">${pct(v,1)}</span></a>`;
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
  </div>`;
}

function fixtureCompact(f, tMap) {
  const h = tMap[f.home], a = tMap[f.away];
  const href = `#/match/${encodeURIComponent(f.id)}`;
  return `<a class="fixture-row fixture-link" href="${href}" aria-label="View ${esc(h.name)} versus ${esc(a.name)} details"><span class="date">${compactDate(f.date)}</span><span class="fixture-teams"><span class="fixture-team"><span>${esc(h.short)} · ${esc(h.name)}</span>${f.status === 'final' ? `<b>${f.home_score}</b>` : ''}</span><span class="fixture-team"><span>${esc(a.short)} · ${esc(a.name)}</span>${f.status === 'final' ? `<b>${f.away_score}</b>` : ''}</span></span>${f.status === 'final' ? `<span class="score">${f.home_score}–${f.away_score}</span>` : `<span class="fixture-prob">${pct(f.probabilities.home)} / ${pct(f.probabilities.draw)} / ${pct(f.probabilities.away)}<small>View match →</small></span>`}</a>`;
}

function forecastColumns() {
  if (state.league === 'epl') return [
    ['projected_points','Proj pts'], ['avg_position','Avg pos'], ['title','Title'], ['top4','Top 4'], ['europe','Europe'], ['relegation','Relegation'], ['attack','Attack'], ['defense','Defense'], ['market','Market'], ['edge','Edge']
  ];
  return [
    ['projected_points','Proj pts'], ['avg_position','Avg pos'], ['shield','Shield'], ['playoffs','Playoffs'], ['conf_semis','Conf semi'], ['cup_final','Cup final'], ['champion','MLS Cup'], ['attack','Attack'], ['defense','Defense'], ['market','Market'], ['edge','Edge']
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
      ? 'Sort every club by projected points, finish probabilities, underlying strength or model-versus-market edge.'
      : 'Sort all 30 clubs by Shield, conference and MLS Cup outcomes. Conference qualification is simulated independently.')}
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
  if (key === 'market' && (value === null || value === undefined)) return '—';
  if (['title','top4','europe','relegation','shield','playoffs','conf_semis','cup_final','champion','market'].includes(key)) return pct(value,1);
  if (key === 'edge' && (value === null || value === undefined)) return '—';
  if (key === 'edge') return `<span class="${value>0?'positive':value<0?'negative':'neutral'}">${signedPct(value)}</span>`;
  if (key === 'defense') return (-Number(value)).toFixed(2);
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
    const row=`<tr data-team="${t.slug}" tabindex="0" aria-label="View ${esc(t.name)} forecast"><td><span class="position-chip ${positionClass(i,rows.length,state.league)}">${i+1}</span></td><td><span class="table-team">${badge(t)}${esc(t.name)}</span></td><td>${r.p||0}</td><td>${r.pts||0}</td><td>${r.gd>0?'+':''}${r.gd||0}</td><td><b>${r.projected_points.toFixed(1)}</b></td><td>${lo}–${hi}</td><td>${probPct(r[outcomeKey()],1)}</td></tr>`;
    if(state.league==='mls' && i===8) {
      return `${row}<tr class="playoff-cutoff-row" aria-hidden="true"><td colspan="8"><span>Playoff cutoff</span><small>Seeds 1–7 qualify directly · Seeds 8–9 enter the Wild Card round</small></td></tr>`;
    }
    return row;
  }).join('');
  return `<article class="card projection-card"${id?` id="${id}"`:''}>${title?`<div class="card-head"><h2>${title}</h2><span class="conference-legend"><i class="legend-direct"></i>Direct playoff <i class="legend-wildcard"></i>Wild Card</span></div>`:''}<div class="table-wrap projection-table-wrap"><table class="projection-table"><thead><tr><th>Pos</th><th>Club</th><th>P</th><th>Pts</th><th>GD</th><th>Projected</th><th>Range</th><th>${outcomeLabel()}</th></tr></thead><tbody>${body}</tbody></table></div></article>`;
}
function renderTable() {
  const copy = state.league==='epl'
    ? 'The mean final table across all season simulations. The range is an approximate 90% interval for final points.'
    : 'Conference tables are ranked separately for postseason qualification. The Shield is determined across both conferences.';
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
function renderMatchups() {
  const teams=state.data.teams;
  if (!state.matchupA || !teams.some(t=>t.slug===state.matchupA)) state.matchupA=topForecast()[0].team;
  if (!state.matchupB || !teams.some(t=>t.slug===state.matchupB) || state.matchupB===state.matchupA) state.matchupB=topForecast()[1].team;
  const a=team(state.matchupA), b=team(state.matchupB), model=matchupModel(a,b,state.venue);
  const probs=[model.home,model.draw,model.away], max=Math.max(...probs);
  main.innerHTML=`<div class="page">${pageHead('Closed-form Poisson model','Matchup laboratory','Choose any two clubs and venue. The score matrix and win/draw/loss probabilities update immediately from the current attack and defensive ratings.')}${notice()}
  <section class="grid matchup-grid">
    <article class="card"><div class="card-head"><h2>Set the matchup</h2></div><div class="card-body matchup-selector">
      <label><span class="eyebrow">Team A</span><div class="club-pick"><select id="matchup-a">${teams.map(t=>`<option value="${t.slug}" ${t.slug===a.slug?'selected':''}>${esc(t.name)}</option>`).join('')}</select>${badge(a)}</div></label>
      <label><span class="eyebrow">Team B</span><div class="club-pick"><select id="matchup-b">${teams.map(t=>`<option value="${t.slug}" ${t.slug===b.slug?'selected':''}>${esc(t.name)}</option>`).join('')}</select>${badge(b)}</div></label>
      <label><span class="eyebrow">Venue</span><select id="venue"><option value="a-home" ${state.venue==='a-home'?'selected':''}>${esc(a.name)} at home</option><option value="neutral" ${state.venue==='neutral'?'selected':''}>Neutral venue</option><option value="b-home" ${state.venue==='b-home'?'selected':''}>${esc(b.name)} at home</option></select></label>
      <div class="big-probs"><div class="big-prob ${model.home===max?'favorite':''}"><strong>${pct(model.home,1)}</strong><span>${esc(a.short)} win</span></div><div class="big-prob ${model.draw===max?'favorite':''}"><strong>${pct(model.draw,1)}</strong><span>Draw</span></div><div class="big-prob ${model.away===max?'favorite':''}"><strong>${pct(model.away,1)}</strong><span>${esc(b.short)} win</span></div></div>
      <div class="xg-row"><span>${esc(a.short)} xG <b>${model.lh.toFixed(2)}</b></span><span>${esc(b.short)} xG <b>${model.la.toFixed(2)}</b></span></div>
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
  const rowHtml=rows.map(f=>{
    const value=Number(f[key]||0);
    const width=value>0?Math.max(1,value*100):0;
    return `<a class="race-row race-link" href="#/team/${encodeURIComponent(f.team)}" aria-label="View ${esc(tMap[f.team].name)} forecast">${teamInline(tMap[f.team])}<span class="prob-value">${probPct(value,1)}</span><span class="prob-bar" style="grid-column:1/-1"><span style="width:${width}%"></span></span></a>`;
  }).join('');
  const toggle=allRows.length>6?`<button class="race-toggle" type="button" data-race-key="${key}" aria-expanded="${expanded}">${expanded?'Show top 6 ↑':`View all ${allRows.length} teams ↓`}</button>`:'';
  return `<article class="card race-card"><div class="eyebrow">Probability</div><h3>${title}</h3><p class="race-definition"><span class="info-dot" aria-hidden="true">i</span>${esc(raceDefinition(key))}</p><div class="race-rows">${rowHtml}</div>${toggle}</article>`;
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
  main.innerHTML=`<div class="page">${pageHead('Fixtures and probabilities','Schedule & results','Every fixture includes a frozen pre-match-style probability snapshot, expected goals and the current result status.')}${notice()}
  <div class="toolbar"><div class="segmented">${[['upcoming','Upcoming'],['completed','Completed'],['all','All']].map(([v,l])=>`<button data-filter="${v}" class="${state.scheduleFilter===v?'active':''}">${l}</button>`).join('')}</div><select id="schedule-team"><option value="all">All clubs</option>${state.data.teams.map(t=>`<option value="${t.slug}" ${state.scheduleTeam===t.slug?'selected':''}>${esc(t.name)}</option>`).join('')}</select></div>
  <article class="card"><div class="fixture-list">${fixtures.map(f=>fixtureDetailed(f,tMap)).join('')||'<div class="empty">No fixtures match these filters.</div>'}</div></article></div>`;
  document.querySelectorAll('[data-filter]').forEach(b=>b.addEventListener('click',()=>{state.scheduleFilter=b.dataset.filter;renderSchedule();}));
  document.getElementById('schedule-team').addEventListener('change',e=>{state.scheduleTeam=e.target.value;renderSchedule();});
}
function fixtureDetailed(f,tMap) {
  const h=tMap[f.home],a=tMap[f.away];
  const href = `#/match/${encodeURIComponent(f.id)}`;
  return `<a class="fixture-row fixture-row-detailed fixture-link" href="${href}" aria-label="View ${esc(h.name)} versus ${esc(a.name)} details"><span class="date">Round ${esc(f.round)}<br>${dateText(f.date)}</span><span class="fixture-teams"><span class="fixture-team"><span class="team-inline">${badge(h)}<strong>${esc(h.name)}</strong></span>${f.status==='final'?`<b>${f.home_score}</b>`:''}</span><span class="fixture-team"><span class="team-inline">${badge(a)}<strong>${esc(a.name)}</strong></span>${f.status==='final'?`<b>${f.away_score}</b>`:''}</span></span><span class="fixture-prob"><b>${pct(f.probabilities.home,1)} · ${pct(f.probabilities.draw,1)} · ${pct(f.probabilities.away,1)}</b><br>xG ${f.xg_home}–${f.xg_away}${f.status==='final'?`<br><span class="score">${f.home_score}–${f.away_score}</span>`:''}<small>View match details →</small></span></a>`;
}

function fixtureScoreModel(f) {
  const lh = clamp(Number(f.xg_home || 0), .05, 6);
  const la = clamp(Number(f.xg_away || 0), .05, 6);
  const matrix = Array.from({length:7},(_,h)=>Array.from({length:7},(_,a)=>poissonP(lh,h)*poissonP(la,a)));
  return {lh, la, matrix};
}
function likelyScores(matrix, home, away) {
  return matrix.flatMap((row,h)=>row.map((prob,a)=>({h,a,prob}))).sort((x,y)=>y.prob-x.prob).slice(0,5).map(row=>`<div class="likely-score"><strong>${row.h}–${row.a}</strong><span>${pct(row.prob,1)}</span><small>${esc(home.short)}–${esc(away.short)}</small></div>`).join('');
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
  const defenseRange=Array.isArray(f?.defense_interval)?`${f.defense_interval[0].toFixed(2)} to ${f.defense_interval[1].toFixed(2)}`:'Not available';
  return `<article class="card rating-card"><div class="rating-team">${badge(t,'large')}<div><h3>${esc(t.name)}</h3><a href="#/team/${t.slug}">Open club forecast →</a></div></div><div class="rating-stats"><div><small>Attack rating</small><strong>${Number(f?.attack??t.attack).toFixed(2)}</strong><span>90% range: ${attackRange}</span></div><div><small>Defense rating</small><strong>${Number(f?.defense??t.defense).toFixed(2)}</strong><span>90% range: ${defenseRange}</span></div><div><small>Projected points</small><strong>${Number(f?.projected_points||0).toFixed(1)}</strong><span>${state.league==='epl'?`Title ${pct(f?.title||0,1)}`:`MLS Cup ${pct(f?.champion||0,1)}`}</span></div></div></article>`;
}
function renderMatch(id) {
  const f=fixtureById(id);
  if(!f){ main.innerHTML=`<div class="page"><article class="card"><div class="empty"><h2>Match not found</h2><p>The fixture may have changed during the latest data refresh.</p><a class="button" href="#/schedule">Back to schedule</a></div></article></div>`; return; }
  const h=team(f.home), a=team(f.away), hForecast=forecast(h.slug), aForecast=forecast(a.slug);
  const model=fixtureScoreModel(f), probs=f.probabilities, max=Math.max(probs.home,probs.draw,probs.away);
  const favorite=max===probs.home?h:max===probs.away?a:null;
  const status=f.status==='final'?`Final · ${f.home_score}–${f.away_score}`:'Upcoming';
  const timing=f.kickoff?kickoffText(f.kickoff):dateText(f.date);
  const read=favorite?`${favorite.name} has the highest win probability at ${pct(max,1)}. The model’s mean scoring expectation is ${Number(f.xg_home).toFixed(2)}–${Number(f.xg_away).toFixed(2)} in expected goals.`:`The draw is the single most likely 1X2 outcome at ${pct(probs.draw,1)}. The model’s mean scoring expectation is ${Number(f.xg_home).toFixed(2)}–${Number(f.xg_away).toFixed(2)} in expected goals.`;
  main.innerHTML=`<div class="page match-page">
    <a class="back-link" href="#/schedule">← Back to schedule</a>
    <section class="match-hero card"><div class="match-meta"><span class="eyebrow">${esc(state.data.meta.name)} · Round ${esc(f.round)}</span><strong>${esc(timing)}</strong><span class="status-pill ${f.status==='final'?'final':''}">${esc(status)}</span></div><div class="match-teams"><a href="#/team/${h.slug}" class="match-club">${badge(h,'large')}<span><strong>${esc(h.name)}</strong><small>Home</small></span></a><div class="match-score"><strong>${f.status==='final'?`${f.home_score}–${f.away_score}`:'vs'}</strong><span>${Number(f.xg_home).toFixed(2)}–${Number(f.xg_away).toFixed(2)} xG</span></div><a href="#/team/${a.slug}" class="match-club away">${badge(a,'large')}<span><strong>${esc(a.name)}</strong><small>Away</small></span></a></div></section>
    <section class="grid match-detail-grid"><article class="card"><div class="card-head"><h2>Outcome forecast</h2><span class="eyebrow">Posterior probability</span></div><div class="card-body"><div class="outcome-grid"><div class="outcome-card ${probs.home===max?'favorite':''}"><small>${esc(h.short)} win</small><strong>${pct(probs.home,1)}</strong><span>90% range ${intervalLabel(probs.home_interval)}</span></div><div class="outcome-card ${probs.draw===max?'favorite':''}"><small>Draw</small><strong>${pct(probs.draw,1)}</strong><span>90% range ${intervalLabel(probs.draw_interval)}</span></div><div class="outcome-card ${probs.away===max?'favorite':''}"><small>${esc(a.short)} win</small><strong>${pct(probs.away,1)}</strong><span>90% range ${intervalLabel(probs.away_interval)}</span></div></div><div class="model-read"><strong>Model read</strong><p>${esc(read)}</p></div></div></article><article class="card"><div class="card-head"><h2>Most likely scores</h2><span class="eyebrow">Mean-xG Poisson</span></div><div class="card-body likely-scores">${likelyScores(model.matrix,h,a)}<p class="detail-note">Exact-score probabilities use the mean expected goals. The 1X2 probabilities include posterior uncertainty, so the two views will not match perfectly.</p></div></article></section>
    <section class="grid rating-grid">${ratingCard(h,hForecast)}${ratingCard(a,aForecast)}</section>
    <section class="grid match-detail-grid"><article class="card"><div class="card-head"><h2>Exact score matrix</h2><span class="eyebrow">Rows ${esc(h.short)} · columns ${esc(a.short)}</span></div><div class="card-body table-wrap">${scoreMatrix(model.matrix)}</div></article><article class="card"><div class="card-head"><h2>Recent form</h2><span class="eyebrow">Last five completed</span></div><div class="card-body form-columns"><div><h3>${esc(h.short)}</h3>${recentForm(h.slug,f.id)}</div><div><h3>${esc(a.short)}</h3>${recentForm(a.slug,f.id)}</div></div></article></section>
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

function renderNews() {
  const tMap=teamMap();
  main.innerHTML=`<div class="page">${pageHead('Human-in-the-loop adjustments','Model news','This page records automated refreshes and will later hold reviewed injury, suspension, transfer and manager adjustments.')}${notice()}<section class="news-list">${state.data.news.map(n=>`<article class="card news-item"><time>${dateText(n.date)}</time><div>${teamInline(tMap[n.team], n.impact)}<h3>${esc(n.headline)}</h3><p>${esc(n.summary)}</p></div><span class="${n.affects_forecast?'affects':'pending'}">${n.affects_forecast?'◆ Affects forecast':'Review pending'}</span></article>`).join('')}</section></div>`;
}

function renderMethod() {
  const leagueSpecific = state.league==='epl'
    ? `<p>Each run simulates every remaining league match, ranks all 20 clubs by points, goal difference, goals scored and wins, then records the champion, top-four, European and bottom-three outcomes.</p>`
    : `<p>Each run ranks the Eastern and Western Conferences separately, identifies the Shield winner, plays the 8–9 Wild Card matches, Round One best-of-three series, single-elimination conference rounds and MLS Cup.</p>`;
  main.innerHTML=`<div class="page">${pageHead('Transparent by design','How we predict','The implementation below follows the reference site’s Bayesian state-space Poisson concept, while documenting the assumptions the reference site leaves unpublished.')}${notice()}<section class="grid method-layout">
    <aside class="card method-toc"><div class="eyebrow">On this page</div><a href="#model">1. Match model</a><a href="#dynamic">2. Dynamic team strength</a><a href="#simulation">3. Season simulation</a><a href="#league">4. League rules</a><a href="#validation">5. Validation</a><a href="#limitations">6. Limitations</a></aside>
    <article class="card method-copy">
      <section id="model"><div class="eyebrow">01</div><h2>Match-level goal model</h2><p>Home and away goals are modeled as Poisson variables. Each club has a time-specific attacking effect and defensive strength. Home advantage and the log ratio of squad values are added as covariates.</p><div class="equation">G_home ~ Poisson(λ_home)<br>G_away ~ Poisson(λ_away)<br><br>log(λ_home) = α + H + A_home,t − D_away,t + βv log(V_home / V_away)<br>log(λ_away) = α + A_away,t − D_home,t − βv log(V_home / V_away)</div><p>The current posterior mean implies μ=${state.data.model.base_goals}, H=${state.data.model.home_advantage_log} and βv=${state.data.model.market_value_coefficient}. These values are learned from match results rather than typed into the forecast.</p></section>
      <section id="dynamic"><div class="eyebrow">02</div><h2>Dynamic team strength</h2><p>Attack and defense are latent states that evolve every ${state.data.model.bucket_days} days. The model was fitted to ${state.data.model.matches_fitted.toLocaleString()} completed matches and retains ${state.data.model.posterior_samples.toLocaleString()} posterior samples.</p><div class="equation">A_team,t ~ Normal(A_team,t−1, σA)<br>D_team,t ~ Normal(D_team,t−1, σD)</div><p>The fitted step sizes are σA=${state.data.model.sigma_attack} and σD=${state.data.model.sigma_defense}. Every season simulation starts from a posterior draw and carries that uncertainty forward.</p></section>
      <section id="simulation"><div class="eyebrow">03</div><h2>From matches to season probabilities</h2><ol><li>Draw one set of attack, defense and coefficient values from the posterior.</li><li>Simulate every unplayed match.</li><li>Apply official table and postseason rules.</li><li>Record each club’s final position and outcomes.</li><li>Repeat thousands of times.</li></ol><p>A probability is the share of simulations in which the event occurred. It is never manually adjusted after the run.</p></section>
      <section id="league"><div class="eyebrow">04</div><h2>${esc(state.data.meta.name)} rules</h2>${leagueSpecific}<p>Competition rules belong in a configuration layer so changes to playoff formats, qualification places or tiebreakers do not require rewriting the statistical model.</p></section>
      <section id="validation"><div class="eyebrow">05</div><h2>How the production model should be judged</h2><table><thead><tr><th>Metric</th><th>Purpose</th></tr></thead><tbody><tr><td>Multiclass Brier score</td><td>Accuracy of home/draw/away probabilities</td></tr><tr><td>Log loss</td><td>Penalizes confidently wrong forecasts</td></tr><tr><td>Calibration</td><td>Tests whether 60% events occur about 60% of the time</td></tr><tr><td>Ranked probability score</td><td>Quality of final-position distributions</td></tr><tr><td>Baseline comparison</td><td>Must beat naive home advantage and Elo baselines</td></tr></tbody></table></section>
      <section id="limitations"><div class="eyebrow">06</div><h2>Current limitations</h2><ul><li>Fixtures and results depend on the seasons available through the connected API-Football plan.</li><li>Squad market values remain a manually maintained covariate and should be refreshed after transfer windows.</li><li>Polymarket probabilities appear only when an active market can be matched confidently to a club.</li><li>Player availability, rest, travel and congestion are not yet included.</li><li>The scoring model is independent Poisson; a future validation phase should compare it with a Dixon–Coles correction.</li></ul></section>
    </article>
  </section></div>`;
}

function renderTeam(slug) {
  const t=team(slug);
  if(!t){ location.hash='#/forecast'; return; }
  const f=forecast(slug), c=current(slug);
  const outcome=outcomeKey();
  const upcoming=state.data.fixtures.filter(x=>x.status!=='final'&&(x.home===slug||x.away===slug)).sort((a,b)=>a.date.localeCompare(b.date)).slice(0,8);
  const metrics=state.league==='epl'
    ? [['Projected points',f.projected_points.toFixed(1)],['Average finish',f.avg_position.toFixed(1)],['Title',pct(f.title,1)],['Top four',pct(f.top4,1)],['Relegation',pct(f.relegation,1)]]
    : [['Projected points',f.projected_points.toFixed(1)],['Average overall',f.avg_position.toFixed(1)],['Shield',pct(f.shield,1)],['Playoffs',pct(f.playoffs,1)],['MLS Cup',pct(f.champion,1)]];
  const max=Math.max(...f.position_distribution);
  main.innerHTML=`<div class="page"><section class="team-hero" style="--team-color:${t.color}">${badge(t)}<div class="eyebrow" style="color:var(--lime)">${esc(t.conference)} · ${esc(state.data.meta.season)}</div><h1>${esc(t.name)}</h1><p>${c.p} played · ${c.pts} points · ${c.gf} GF · ${c.ga} GA</p></section>
  <section class="grid team-metrics">${metrics.map(([l,v])=>metric(l,v,l===outcomeLabel()?'Primary outcome probability':'Current model snapshot')).join('')}</section>
  ${notice()}
  <section class="grid split"><article class="card"><div class="card-head"><h2>Final-position distribution</h2><span class="eyebrow">${state.league==='epl'?'1–20':'Overall 1–30'}</span></div><div class="card-body"><div class="dist-chart">${f.position_distribution.map((v,i)=>`<div class="dist-bar" title="Position ${i+1}: ${pct(v,1)}"><i style="height:${Math.max(2,v/max*150)}px"></i><span>${i+1}</span></div>`).join('')}</div></div></article>
  <article class="card"><div class="card-head"><h2>Model profile</h2></div><div class="card-body"><div class="grid" style="grid-template-columns:1fr 1fr">${metric('Attack rating',f.attack.toFixed(2),'Posterior mean')}${metric('Defense rating',(-f.defense).toFixed(2),'Higher is stronger')}${metric('Squad value',`€${t.market_value}m`,'Model covariate')}${metric('Model edge',f.edge===null||f.edge===undefined?'No active market':signedPct(f.edge),f.market_details?'vs Polymarket':'Market unavailable')}</div></div></article></section>
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
  searchResults.innerHTML=rows.map(t=>`<div class="search-result" data-team="${t.slug}">${badge(t)}<span><strong>${esc(t.name)}</strong><small>${esc(t.conference)} · ${pct(forecast(t.slug)[outcomeKey()],1)} ${outcomeLabel().toLowerCase()}</small></span></div>`).join('')||'<div class="empty">No clubs found.</div>';
  document.querySelectorAll('.search-result[data-team]').forEach(el=>el.addEventListener('click',()=>{searchDialog.close();location.hash=`#/team/${el.dataset.team}`;}));
}

window.addEventListener('hashchange', renderRoute);
document.querySelectorAll('.league-button').forEach(b=>b.addEventListener('click',()=>switchLeague(b.dataset.league)));
document.getElementById('search-open').addEventListener('click', openSearch);
document.getElementById('mobile-menu').addEventListener('click',()=>document.getElementById('sidebar').classList.toggle('open'));
teamSearch.addEventListener('input',e=>renderSearch(e.target.value));
document.addEventListener('keydown',e=>{
  if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='k'){e.preventDefault();openSearch();}
  if(e.key==='Escape'&&searchDialog.open)searchDialog.close();
});

loadData();
