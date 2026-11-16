import { api } from '../api.js';
import { html, mount, fmtDate, emptyState, debounce, icon } from '../ui.js';

export default async function ({ el }) {
  const { events } = await api.get('/api/audit');
  const draw = (q = '') => {
    const list = events.filter(e => !q || `${e.action} ${e.detail} ${e.user_name}`.toLowerCase().includes(q));
    mount(el.querySelector('#rows'), list.length ? html`<table class="table"><thead><tr><th>When</th><th>User</th><th>Action</th><th>Detail</th></tr></thead><tbody>
      ${list.map(e => html`<tr><td class="small muted" style="white-space:nowrap">${fmtDate(e.created_at)}</td><td>${e.user_name || '—'}</td><td><span class="tag mono">${e.action}</span></td><td class="small">${e.detail}</td></tr>`)}</tbody></table>`
      : emptyState('activity', 'No events', 'Sign-ins, generations and admin changes appear here.'));
  };
  mount(el, html`<div class="page-head"><div><h1>Audit log</h1><p>The latest 300 events across the workspace.</p></div>
    <div class="input-icon" style="width:280px">${icon('search')}<input id="q" placeholder="Filter events"></div></div><div class="card table-wrap" id="rows"></div>`);
  el.querySelector('#q').oninput = debounce((e) => draw(e.target.value.toLowerCase()), 200);
  draw();
}
