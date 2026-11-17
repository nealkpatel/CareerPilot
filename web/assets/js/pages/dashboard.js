import { api } from '../api.js';
import { html, raw, icon, mount, logo, statusBadge, genBadge, timeAgo, STATUS, STATUS_COLORS, emptyState } from '../ui.js';
import { go, engineLabel, connectDeepSeek } from '../app.js';
import { lineChart, sparkline, funnel, segmentBar, seriesTable } from '../charts.js';

const RANGES = [[7, '7D', 'Last 7 days'], [30, '30D', 'Last 30 days'], [90, '90D', 'Last 90 days']];
const FUNNEL = [['added', 'Added'], ['applied', 'Applied'], ['screening', 'Screening'], ['interview', 'Interview'], ['offer', 'Offer']];
const day = (s, opts) => new Date(s + 'T00:00:00').toLocaleDateString(undefined, opts);

// 90-day view reads better as weekly totals
function bucket(series, days) {
  if (days < 90) return { labels: series.map(p => day(p.day, { month: 'short', day: 'numeric' })), titles: series.map(p => day(p.day, { weekday: 'short', month: 'short', day: 'numeric' })), rows: series };
  const rows = [];
  for (let i = 0; i < series.length; i += 7) {
    const chunk = series.slice(i, i + 7);
    const statuses = Object.fromEntries(Object.keys(STATUS).map(s => [s, chunk.reduce((a, p) => a + (p.statuses?.[s] || 0), 0)]));
    rows.push({ day: chunk[0].day, end: chunk[chunk.length - 1].day, added: chunk.reduce((a, p) => a + p.added, 0), applied: chunk.reduce((a, p) => a + p.applied, 0), resumes: chunk.reduce((a, p) => a + p.resumes, 0), statuses });
  }
  return { labels: rows.map(r => day(r.day, { month: 'short', day: 'numeric' })), titles: rows.map(r => `Week of ${day(r.day, { month: 'short', day: 'numeric' })} – ${day(r.end, { month: 'short', day: 'numeric' })}`), rows };
}

function delta(m, rangeLabel) {
  const diff = m.value - m.previous;
  if (!m.previous && !m.value) return html`<span class="delta">No activity yet</span>`;
  if (!m.previous) return html`<span class="delta up">${icon('arrowUp', 'xs')}<b>New</b>&nbsp;vs previous ${rangeLabel}</span>`;
  const pct = Math.round((diff / m.previous) * 100);
  if (diff === 0) return html`<span class="delta">— No change vs previous ${rangeLabel}</span>`;
  return html`<span class="delta ${diff > 0 ? 'up' : 'down'}">${icon(diff > 0 ? 'arrowUp' : 'arrowDown', 'xs')}<b>${diff > 0 ? '+' : ''}${pct}%</b>&nbsp;vs previous ${rangeLabel}</span>`;
}

export default async function ({ el, user, isCurrent }) {
  el.addEventListener('click', (e) => { if (e.target.closest('#dashConnect')) connectDeepSeek(); });
  let days = 30;
  try { days = Number(localStorage.getItem('cp-dash-days')) || 30; } catch {}
  if (![7, 30, 90].includes(days)) days = 30;
  let tableView = false;
  const cleanups = [];
  const hour = new Date().getHours();
  const greet = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';
  const isAdmin = user.role === 'admin';

  async function draw() {
    const d = await api.get(`/api/dashboard?days=${days}`);
    if (!isCurrent()) return;
    cleanups.splice(0).forEach(f => f());
    const A = d.analytics, M = A.metrics, k = d.kpis;
    const rangeLabel = days === 7 ? '7 days' : days === 30 ? '30 days' : '90 days';
    const steps = isAdmin ? [
      ['profile', '/profiles/new', 'Create a profile', 'Your fixed facts: name, jobs, education'],
      ['prompt', '/prompts', 'Review your prompt', 'Tune how résumés are written'],
      ['team', '/team', 'Add your bidders', 'Create accounts and assign profiles'],
      ['application', '/applications/new', 'Add an application', 'Paste a job link or description'],
    ] : [
      ['profile', '/profiles', 'Get a profile', 'Your admin assigns the profiles you use'],
      ['engine', '/settings?tab=engine', 'Connect your DeepSeek account', 'Your own account writes your documents'],
      ['application', '/applications/new', 'Add an application', 'Paste a job link or description'],
    ];
    d.onboarding.team = (d.team || []).length > 0;
    const done = steps.filter(s => d.onboarding[s[0]]).length;
    const rr = M.response_rate;

    mount(el, html`
      <div class="page-head"><div><h1>${greet}, ${user.name.split(' ')[0]}</h1><p>Here's where your search stands — ${day(A.range.start, { month: 'short', day: 'numeric' })} to ${day(A.range.end, { month: 'short', day: 'numeric', year: 'numeric' })}.</p></div>
        <div class="row"><a class="btn" href="#/jobs">${icon('globe')}Browse jobs${d.new_jobs_24h ? html` <span class="badge applied">${d.new_jobs_24h} new</span>` : ''}</a>
          <a class="btn primary" href="#/applications/new">${icon('sparkles')}Tailor for a job</a></div></div>

      ${done < steps.length ? html`<div class="card card-pad stack" style="margin-bottom:16px">
        <div class="row between"><div><h3>Get set up</h3><p class="muted small">${done} of ${steps.length} steps complete</p></div>
        <div class="progress" style="width:180px"><div style="width:${(done / steps.length) * 100}%"></div></div></div>
        <div class="checklist" style="--steps:${steps.length}">${steps.map(([key, href, title, sub], i) => html`<a class="check-item ${d.onboarding[key] ? 'done' : ''}" href="#${href}">
          <div class="num">${d.onboarding[key] ? icon('check') : i + 1}</div><div><b>${title}</b><span>${sub}</span></div></a>`)}</div></div>` : ''}

      <div class="dash-toolbar">
        <div class="seg" id="rangeSeg" role="group" aria-label="Date range">${RANGES.map(([v, s, l]) => html`<button data-days="${v}" class="${days === v ? 'on' : ''}" title="${l}">${s}</button>`)}</div>
        <span class="muted small">All figures below cover the ${rangeLabel.replace('days', 'days')} shown · compared with the ${rangeLabel} before</span>
      </div>

      <div class="grid g4" style="margin-bottom:16px">
        ${[
          ['briefcase', 'Applications added', M.added, sparkline(M.added.spark, 'var(--viz-1)')],
          ['send', 'Applied', M.applied, sparkline(M.applied.spark, 'var(--viz-2)')],
          ['sparkles', 'Résumés tailored', M.resumes, sparkline(M.resumes.spark, 'var(--viz-3)')],
          ['calendar', 'Interviews', M.interviews, sparkline(M.interviews.spark, 'var(--viz-1)')],
        ].map(([ico, label, m, spark]) => html`<div class="card kpi2">
          <div class="kpi-top"><span class="row" style="gap:8px">${icon(ico)}${label}</span></div>
          <div class="kpi-mid"><div class="value">${m.value.toLocaleString()}</div>${raw(spark)}</div>${delta(m, rangeLabel)}</div>`)}
      </div>

      <div class="dash-grid">
        <div class="card"><div class="card-head"><div><h3>Activity</h3><p class="muted small">${days === 90 ? 'Weekly totals' : 'Daily'} · ${RANGES.find(r => r[0] === days)[2]}</p></div>
          <div class="seg" id="viewSeg"><button data-view="chart" class="${tableView ? '' : 'on'}">${icon('activity')}Chart</button><button data-view="table" class="${tableView ? 'on' : ''}">${icon('list')}Table</button></div></div>
          <div class="card-body"><div id="activity"></div><div class="act-stats" id="actStats"></div></div></div>
        <div class="card"><div class="card-head"><div><h3>Pipeline</h3><p class="muted small">Applications added in this period, by furthest stage reached</p></div><a class="small" href="#/applications">View all</a></div>
          <div class="card-body stack">
            <div id="funnel"></div>
            <div class="row between" style="padding-top:12px;border-top:1px solid var(--border)">
              <div><div class="muted small">Response rate</div><div style="font-size:22px;font-weight:800">${rr.value == null ? '—' : rr.value + '%'}</div></div>
              <div class="muted small" style="text-align:right;max-width:60%">${rr.applied ? html`${rr.responded} of ${rr.applied} applications sent in this period reached screening or beyond` : 'Mark applications as applied to see how many get a response'}</div></div>
            <div><div class="row between" style="margin-bottom:8px"><b class="small">Current status</b><span class="muted small">${A.status_mix.reduce((a, s) => a + s.count, 0)} applications</span></div><div id="mix"></div></div>
          </div></div>
      </div>

      <div class="dash-grid">
        <div class="card"><div class="card-head"><h3>Recent applications</h3><a class="small" href="#/applications">See all</a></div>
          ${d.recent.length ? d.recent.map(a => html`<div class="list-item" data-go="/applications/${a.id}">
            ${logo(a.company, 36, { url: a.url })}<div class="grow"><div class="co-name truncate">${a.title}</div><div class="co-sub truncate">${a.company}${a.location ? ' · ' + a.location : ''}</div></div>
            <div class="hide-sm">${genBadge(a.latest_generation)}</div>${statusBadge(a.status)}<span class="muted small hide-sm" style="width:64px;text-align:right">${timeAgo(a.updated_at)}</span></div>`)
            : emptyState('inbox', 'No applications yet', 'Add your first application by pasting a job link — the posting is scraped automatically.', '<a class="btn primary" href="#/applications/new">New application</a>')}
        </div>
        <div class="card"><div class="card-head"><h3>Your DeepSeek</h3><span class="row small" style="gap:6px"><span class="pulse-dot ${d.engine.mode === 'demo' ? 'ready' : d.engine.status}"></span>${engineLabel(d.engine)}</span></div>
          <div class="card-body stack">
            <div><div style="font-weight:700">${d.engine.mode === 'demo' ? 'Demo mode' : d.engine.connected ? 'Your DeepSeek account' : 'Not connected'}</div><p class="muted small" style="margin-top:4px">${d.engine.message}</p></div>
            <div class="kv"><dt>Queue</dt><dd>${d.engine.queue} waiting · ${k.active_generations} active</dd>
              <dt>All-time</dt><dd>${k.applications} applications · ${k.resumes} résumés</dd>
              ${k.failed_generations ? html`<dt>Failed</dt><dd style="color:var(--red)">${k.failed_generations} generation(s)</dd>` : ''}</div>
            ${d.engine.mode !== 'demo' && (!d.engine.connected || d.engine.status === 'login_required') ? html`<button class="btn primary" id="dashConnect">${icon('link')}Connect DeepSeek</button>` : html`<a class="btn" href="#/settings?tab=engine">${icon('info')}DeepSeek account</a>`}
          </div></div>
      </div>
      ${isAdmin ? html`<div class="card"><div class="card-head"><div><h3>My bidders</h3><p class="muted small">Your team's activity</p></div><a class="btn sm" href="#/team">${icon('users')}Manage team</a></div>
        ${d.team.length ? html`<div class="table-wrap"><table class="table"><thead><tr><th>Bidder</th><th>Applications</th><th>Applied</th><th class="hide-sm">Résumés</th><th class="hide-sm">Last activity</th></tr></thead><tbody>
          ${d.team.map(b => html`<tr class="click" data-go="/applications?scope=team&owner=${b.id}"><td><b>${b.name}</b>${b.status !== 'active' ? html` <span class="badge rejected">${b.status}</span>` : ''}<div class="muted small">${b.email}</div></td>
            <td>${b.applications}</td><td>${b.applied}</td><td class="hide-sm">${b.resumes}</td><td class="hide-sm muted small">${b.last_activity ? timeAgo(b.last_activity) : '—'}</td></tr>`)}</tbody></table></div>`
          : emptyState('users', 'No bidders yet', 'Add bidder accounts and assign them your candidate profiles. Their work shows up here.', '<a class="btn primary" href="#/team">Add a bidder</a>')}</div>` : ''}`);

    // ---- activity chart / table
    const b = bucket(A.series, days);
    // one line per status: how many applications entered it each day (Saved = added)
    const series = Object.keys(STATUS).map(s => ({ label: STATUS[s].label, color: STATUS_COLORS[s], values: b.rows.map(r => r.statuses?.[s] || 0) }));
    const act = el.querySelector('#activity');
    if (tableView) seriesTable(act, b.titles, series);
    else cleanups.push(lineChart(act, { labels: b.labels, tooltipTitles: b.titles, series, height: 330, ariaLabel: `Applications by status, ${rangeLabel}` }));
    // summary strip: what the chart adds up to
    const totalAdded = A.series.reduce((a, p) => a + p.added, 0), totalApplied = A.series.reduce((a, p) => a + p.applied, 0);
    const busiest = A.series.reduce((best, p) => (p.added + p.applied > best.added + best.applied ? p : best), A.series[0] || { added: 0, applied: 0 });
    const activeDays = A.series.filter(p => p.added || p.applied).length;
    mount(el.querySelector('#actStats'), html`${[
      ['Added', totalAdded.toLocaleString(), `${(totalAdded / days).toFixed(1)} per day`],
      ['Applied', totalApplied.toLocaleString(), totalAdded ? `${Math.round((totalApplied / Math.max(totalAdded, 1)) * 100)}% of added` : '—'],
      ['Active days', `${activeDays}`, `of ${days} days`],
      ['Busiest day', busiest && (busiest.added || busiest.applied) ? day(busiest.day, { month: 'short', day: 'numeric' }) : '—', busiest && (busiest.added || busiest.applied) ? `${busiest.added} added · ${busiest.applied} applied` : 'No activity yet'],
    ].map(([l, v, sub]) => html`<div><div class="muted small">${l}</div><div class="act-v">${v}</div><div class="muted small">${sub}</div></div>`)}`);

    // ---- funnel + status mix
    const counts = Object.fromEntries(A.funnel.map(f => [f.stage, f.count]));
    funnel(el.querySelector('#funnel'), FUNNEL.map(([key, label], i) => ({ label, count: counts[key] || 0, color: `var(--funnel-${i + 1})` })),
      [null, A.conversion.applied, A.conversion.screening, A.conversion.interview, A.conversion.offer]);
    segmentBar(el.querySelector('#mix'), A.status_mix.map(s => ({ label: STATUS[s.status].label, count: s.count, color: STATUS_COLORS[s.status] })));

    el.querySelectorAll('#rangeSeg [data-days]').forEach(btn => btn.onclick = () => { days = Number(btn.dataset.days); try { localStorage.setItem('cp-dash-days', String(days)); } catch {} el.style.opacity = '.6'; draw().finally(() => { el.style.opacity = ''; }); });
    el.querySelectorAll('#viewSeg [data-view]').forEach(btn => btn.onclick = () => { tableView = btn.dataset.view === 'table'; draw(); });
    el.querySelectorAll('[data-go]').forEach(r => r.onclick = () => go(r.dataset.go));
  }

  await draw();
  return () => cleanups.splice(0).forEach(f => f());
}
