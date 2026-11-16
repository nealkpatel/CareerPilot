import { api } from '../api.js';
import { html, raw, icon, mount, timeAgo, fmtDate, initials, colorFor, toast, toastError, emptyState } from '../ui.js';
import { lineChart } from '../charts.js';
const dlabel = (s, o) => new Date(s + 'T00:00:00').toLocaleDateString(undefined, o);


export default async function ({ el, user, isCurrent }) {
  let timer = null, stopChart = null;
  async function draw() {
    clearTimeout(timer);
    const d = await api.get('/api/platform/overview');
    if (!isCurrent()) return;
    const e = d.engine, demo = e.mode === 'demo', linked = e.users.filter(u => u.connected).length;
    mount(el, html`
      <div class="page-head"><div><h1>Platform overview</h1><p>You manage CareerPilot: accounts, platform rules and the job pool. Every admin and bidder uses their own DeepSeek account.</p></div>
        <div class="row"><a class="btn" href="#/admin/users">${icon('users')}Users</a><a class="btn primary" href="#/settings?tab=engine">${icon('sparkles')}DeepSeek accounts</a></div></div>

      ${d.pending.length ? html`<div class="alert warn" style="margin-bottom:16px">${icon('users')}<div class="grow"><b>${d.pending.length} account${d.pending.length > 1 ? 's' : ''} waiting for approval</b>
        <div class="row wrap" style="margin-top:8px">${d.pending.map(p => html`<span class="row" style="gap:6px;background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:4px 6px 4px 10px">
          <span class="small"><b>${p.name}</b> · ${p.email}</span><button class="btn sm primary" data-approve="${p.id}">Approve</button></span>`)}</div></div></div>` : ''}

      <div class="grid g4" style="margin-bottom:20px">
        ${[
          ['users', 'tint-brand', 'Admins', d.users.admins, `${d.users.bidders} bidders in their teams`],
          ['briefcase', 'tint-cyan', 'Applications', d.applications, 'across all teams'],
          ['sparkles', 'tint-violet', 'AI résumés', d.generations.completed, `${d.generations.last_24h} in the last 24 hours`],
          ['globe', 'tint-green', 'Jobs in pool', d.jobs.toLocaleString(), d.last_import ? 'updated ' + timeAgo(d.last_import) : 'never imported'],
        ].map(([ico, tint, label, value, sub]) => html`<div class="card kpi"><div class="label"><span class="ico ${tint}">${icon(ico)}</span>${label}</div><div class="value">${value}</div><div class="sub">${sub}</div></div>`)}
      </div>

      <div class="grid" style="grid-template-columns:minmax(0,1.6fr) minmax(0,1fr);margin-bottom:20px" id="r1">
        <div class="card"><div class="card-head"><div><h3>Platform activity</h3><p class="muted small">Last 30 days</p></div></div>
          <div class="card-body"><div id="platChart"></div></div></div>
        <div class="card"><div class="card-head"><h3>DeepSeek accounts</h3><span class="badge">${demo ? 'Demo mode' : `${linked} of ${e.users.length} connected`}</span></div>
          <div class="card-body stack">
            <p class="muted small">${demo ? 'Demo mode is on: documents use placeholder text and nobody needs a DeepSeek account.' : 'Each admin and bidder connects their own DeepSeek account inside CareerPilot. Their requests run with their own account, side by side.'}</p>
            <dl class="kv"><dt>Generating now</dt><dd>${d.generations.active}</dd>
              ${d.generations.failed ? html`<dt>Failed</dt><dd style="color:var(--red)">${d.generations.failed} generation(s)</dd>` : ''}
              <dt>Not connected</dt><dd>${e.users.filter(u => !u.connected).map(u => u.name).slice(0, 4).join(', ') || '—'}${e.users.filter(u => !u.connected).length > 4 ? '…' : ''}</dd></dl>
            <div class="row"><a class="btn" href="#/settings?tab=engine">${icon('users')}See every user</a></div>
          </div></div>
      </div>

      <div class="grid" style="grid-template-columns:minmax(0,1.6fr) minmax(0,1fr)" id="r2">
        <div class="card"><div class="card-head"><h3>Teams</h3><a class="small" href="#/admin/users">Manage users</a></div>
          ${d.admins.length ? html`<div class="table-wrap"><table class="table"><thead><tr><th>Admin</th><th>Bidders</th><th>Applications</th><th class="hide-sm">AI résumés</th></tr></thead><tbody>
            ${d.admins.map(a => html`<tr><td><div class="co"><div class="avatar" style="background:${colorFor(a.name)};color:#fff">${initials(a.name)}</div><div style="min-width:0"><div class="co-name truncate">${a.name}${a.status !== 'active' ? html` <span class="badge rejected">${a.status}</span>` : ''}</div><div class="co-sub truncate">${a.email}</div></div></div></td>
              <td>${a.bidders}</td><td>${a.applications}</td><td class="hide-sm">${a.resumes}</td></tr>`)}</tbody></table></div>`
            : emptyState('users', 'No admins yet', 'Create admin accounts. Each admin then adds and manages their own bidders.', '<a class="btn primary" href="#/admin/users">Add an admin</a>')}</div>
        <div class="card"><div class="card-head"><h3>Recent activity</h3><a class="small" href="#/admin/audit">Audit log</a></div>
          <div class="card-body stack tight">${d.recent_events.map(ev => html`<div class="row top small"><span class="tag mono">${ev.action}</span><div class="grow" style="min-width:0"><div class="truncate">${ev.user_name || '—'} ${ev.detail ? '· ' + ev.detail : ''}</div><div class="muted">${timeAgo(ev.created_at)}</div></div></div>`)}</div></div>
      </div>`);
    const narrow = () => ['#r1', '#r2'].forEach(s => { const r = el.querySelector(s); if (r) r.style.gridTemplateColumns = innerWidth < 1100 ? 'minmax(0,1fr)' : 'minmax(0,1.6fr) minmax(0,1fr)'; });
    narrow();
    stopChart?.();
    stopChart = lineChart(el.querySelector('#platChart'), { height: 230, ariaLabel: 'Platform activity, last 30 days',
      labels: d.series.map(p => dlabel(p.day, { month: 'short', day: 'numeric' })), tooltipTitles: d.series.map(p => dlabel(p.day, { weekday: 'short', month: 'short', day: 'numeric' })),
      series: [{ label: 'Applications', color: 'var(--viz-1)', values: d.series.map(p => p.created) }, { label: 'AI résumés', color: 'var(--viz-2)', values: d.series.map(p => p.applied) }] });
    el.querySelectorAll('[data-approve]').forEach(b => b.onclick = async () => {
      try { await api.put(`/api/users/${b.dataset.approve}`, { status: 'active' }); toast('Account approved'); draw(); } catch (err) { toastError(err); }
    });
    timer = setTimeout(() => isCurrent() && draw(), 15000);
  }
  await draw();
  return () => { clearTimeout(timer); stopChart?.(); };
}
