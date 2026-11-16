import { api } from '../api.js';
import { html, icon, mount, busy, toast, toastError, fmtDate, copyText, timeAgo, emptyState, confirmDialog } from '../ui.js';
import { state, loadSettings, refreshEngine, engineLabel, connectDeepSeek, ROLE_LABEL, siteOrigin } from '../app.js';

export default async function ({ el, query, user, isCurrent }) {
  const isMaster = user.role === 'master';
  let tab = query.tab || (isMaster ? 'engine' : 'preferences');
  let timer = null;

  async function draw() {
    clearTimeout(timer);
    const tabs = isMaster
      ? [['engine', 'DeepSeek accounts', 'sparkles'], ['workspace', 'Platform', 'shield'], ['account', 'Account', 'user']]
      : [['preferences', 'Preferences', 'settings'], ['engine', 'My DeepSeek account', 'sparkles'], ['shares', 'Shared links', 'share'], ['account', 'Account', 'user']];
    if (!tabs.some(t => t[0] === tab)) tab = tabs[0][0];
    mount(el, html`<div class="page-head"><div><h1>Settings</h1><p>${isMaster ? 'Who has connected their DeepSeek account, platform rules and your account.' : 'Your preferences, your own DeepSeek account and your account.'}</p></div></div>
      <div class="tabs">${tabs.map(([k, l, i]) => html`<button data-tab="${k}" class="${tab === k ? 'on' : ''}">${icon(i)}${l}</button>`)}</div><div id="pane"></div>`);
    el.querySelectorAll('[data-tab]').forEach(b => b.onclick = () => { tab = b.dataset.tab; history.replaceState(null, '', `#/settings?tab=${tab}`); draw(); });
    const pane = el.querySelector('#pane');
    await ({ preferences, engine: isMaster ? accounts : engine, shares, account, workspace }[tab])(pane);
  }

  async function preferences(pane) {
    const [s, { profiles }, { templates }] = await Promise.all([loadSettings(true), api.get('/api/profiles'), api.get('/api/templates')]);
    const u = s.user;
    mount(pane, html`<form class="card card-pad" id="prefForm">
      <div class="form-section"><div class="desc"><h3>Defaults</h3><p>Used for new applications.</p></div><div class="grid g2">
        <label class="field">Default profile<select name="default_profile_id"><option value="">—</option>${profiles.map(p => html`<option value="${p.id}" ${String(p.id) === String(u.default_profile_id) ? 'selected' : ''}>${p.label}</option>`)}</select></label>
        <label class="field">Default template<select name="default_template_id">${templates.map(t => html`<option value="${t.id}" ${t.id === u.default_template_id ? 'selected' : ''}>${t.label}</option>`)}</select></label></div></div>
      <div class="form-section"><div class="desc"><h3>Role titles</h3><p>How job titles in your history are handled.</p></div><div class="stack tight">
        <label class="check"><input type="radio" name="role_mode" value="fixed" ${u.role_mode !== 'match' ? 'checked' : ''}><div><b>Keep saved role titles</b><div class="muted small">Titles stay exactly as in your profile.</div></div></label>
        <label class="check"><input type="radio" name="role_mode" value="match" ${u.role_mode === 'match' ? 'checked' : ''}><div><b>Match roles to the job description</b><div class="muted small">The most recent title matches the target role; earlier titles step up toward it.</div></div></label></div></div>
      <div class="form-section"><div class="desc"><h3>Résumé appearance</h3></div>
        <label class="check"><input type="checkbox" name="show_title" ${u.show_title === '1' ? 'checked' : ''}><div><b>Show job title below your name</b><div class="muted small">Uses the most recent role on the résumé. Applies to previews, downloads and share links.</div></div></label></div>
      <div class="row" style="justify-content:flex-end"><button class="btn primary">${icon('check')}Save preferences</button></div></form>`);
    pane.querySelector('#prefForm').onsubmit = async (e) => {
      e.preventDefault();
      const f = e.target;
      busy(f.querySelector('button'), true);
      try {
        await api.put('/api/settings', { user: { default_profile_id: f.default_profile_id.value, default_template_id: f.default_template_id.value, role_mode: f.role_mode.value, show_title: f.show_title.checked ? '1' : '0' } });
        state.settings = null; toast('Preferences saved');
      } catch (err) { toastError(err); }
      busy(f.querySelector('button'), false);
    };
  }

  // Admins and bidders: their own DeepSeek account.
  async function engine(pane) {
    const e = await api.get('/api/engine');
    state.engine = { ...state.engine, ...e };
    const demo = e.mode === 'demo';
    const connected = e.connected && e.status !== 'not_connected';
    const expired = e.status === 'login_required';
    mount(pane, html`<div class="grid g2" style="align-items:start">
      <div class="card card-pad stack">
        <div class="row"><span class="pulse-dot ${demo ? 'ready' : connected && !expired ? e.status : 'login_required'}"></span><h3 class="grow">${demo ? 'Demo mode' : 'Your DeepSeek account'}</h3><span class="badge">${engineLabel(e)}</span></div>
        <p class="muted">${demo ? 'The platform runs in demo mode: documents are filled with placeholder text, so no DeepSeek account is needed right now.' : e.message}</p>
        ${!demo && !connected ? html`<div class="alert warn">${icon('alert')}<div><b>Not connected yet</b>CareerPilot writes your résumés, cover letters and answers with your own DeepSeek account. Click Connect DeepSeek and sign in — it takes a minute, and you only do it once.</div></div>` : ''}
        ${!demo && expired ? html`<div class="alert warn">${icon('alert')}<div><b>Sign-in expired</b>DeepSeek asked to sign in again. ${e.queue ? `${e.queue} request(s) are waiting and run as soon as you reconnect.` : ''}</div></div>` : ''}
        ${e.paused && e.status === 'offline' ? html`<div class="alert error">${icon('alert')}<div><b>${e.held || 0} request(s) on hold</b>${e.message}</div></div>` : ''}
        <dl class="kv"><dt>Queue</dt><dd>${e.queue} waiting</dd>
          <dt>Connected</dt><dd>${e.connected_at ? fmtDate(e.connected_at) : 'Never'}</dd>
          <dt>Last used</dt><dd>${e.last_ok ? fmtDate(e.last_ok) : 'Never'}</dd>
          <dt>Browser</dt><dd>${e.browser_open ? 'Open (hidden, on the CareerPilot server)' : 'Closed'}</dd>
          ${e.current ? html`<dt>Working on</dt><dd>${e.current.label}</dd>` : ''}</dl>
        <div class="row wrap">
          <button class="btn primary" id="connectBtn" ${demo ? 'disabled' : ''}>${icon('link')}${e.signing_in ? 'Continue sign-in' : connected ? (expired ? 'Sign in again' : 'Reconnect / switch account') : 'Connect DeepSeek'}</button>
          <button class="btn" id="closeBtn" ${e.browser_open ? '' : 'disabled'}>${icon('stop')}Close browser</button>
          <button class="btn danger" id="disconnectBtn" ${connected ? '' : 'disabled'}>${icon('x')}Disconnect</button></div>
        <p class="muted small">Your sign-in is saved on the CareerPilot server in a browser profile that only your requests use. Other users never see or use your account, and CareerPilot never stores your password.</p>
      </div>
      <div class="card card-pad stack"><h3>How it works</h3>
        <ol class="stack tight" style="padding-left:18px;margin:0;color:var(--text-2)">
          <li>Click <b>Connect DeepSeek</b>. Your DeepSeek sign-in window opens on the CareerPilot server and is shown to you live, right here — sign in with your email or phone, solve any check DeepSeek shows, then click <b>Done</b>.</li>
          <li>Your requests run with your account, in order, without waiting for anyone else's.</li>
          <li>The DeepSeek window stays hidden while it works and closes after 10 idle minutes.</li>
          <li>Every résumé reply is checked for valid, complete content and retried in a new chat up to 3 times.</li>
          <li>If DeepSeek asks you to sign in again later, your requests wait and a banner asks you to reconnect.</li>
        </ol>
        <p class="muted small">CareerPilot automates the DeepSeek website with your account. It can break if DeepSeek changes its interface and may be affected by rate limits or captchas. Review DeepSeek's terms of use for your situation.</p></div>
      ${e.log ? html`<div class="card card-pad stack" style="grid-column:1/-1"><div class="row between"><h3>Activity</h3><span class="muted small">Refreshes automatically</span></div>
        <div class="log" id="log">${e.log.length ? e.log.join('\n') : 'No activity yet.'}</div></div>` : ''}
    </div>`);
    const log = pane.querySelector('#log');
    if (log) log.scrollTop = log.scrollHeight;
    pane.querySelector('#connectBtn').onclick = () => { connectDeepSeek(); };
    pane.querySelector('#closeBtn').onclick = async () => { await api.post('/api/engine/close'); toast('Closing your DeepSeek browser'); setTimeout(draw, 1500); };
    pane.querySelector('#disconnectBtn').onclick = async (ev) => {
      if (!await confirmDialog({ title: 'Disconnect DeepSeek?', message: 'CareerPilot forgets your DeepSeek sign-in on the server. New requests wait until you connect again.', confirm: 'Disconnect', danger: true })) return;
      const b = ev.currentTarget; busy(b, true);
      try { await api.post('/api/engine/disconnect'); toast('DeepSeek disconnected'); refreshEngine(); } catch (err) { toastError(err); }
      draw();
    };
    timer = setTimeout(() => { if (isCurrent() && tab === 'engine') draw(); }, ['busy', 'starting'].includes(e.status) || e.signing_in ? 2500 : 6000);
  }

  // Master: which admins and bidders have connected their own DeepSeek account (no account details).
  async function accounts(pane) {
    const { users, mode } = await api.get('/api/engine/users');
    const n = users.filter(u => u.connected).length;
    mount(pane, html`<div class="stack">
      <div class="card card-pad stack">
        <div class="row"><h3 class="grow">DeepSeek accounts</h3><span class="badge">${n} of ${users.length} connected</span></div>
        <p class="muted">Every admin and bidder connects their <b>own</b> DeepSeek account from Settings → My DeepSeek account. Their requests run with their own account, side by side. You don't manage anyone's DeepSeek sign-in.</p>
        ${mode === 'demo' ? html`<div class="alert warn">${icon('info')}<div><b>Demo mode is on</b>Documents use placeholder text and nobody needs a DeepSeek account. Switch to live mode under the Platform tab.</div></div>` : ''}
      </div>
      <div class="card">${users.length ? html`<div class="table-wrap"><table class="table"><thead><tr><th>User</th><th>Role</th><th>DeepSeek</th><th>Queue</th><th>Last used</th></tr></thead><tbody>
        ${users.map(u => html`<tr><td><b>${u.name}</b><div class="muted small">${u.email}</div></td><td>${ROLE_LABEL[u.role] || u.role}</td>
          <td>${u.connected ? (u.status === 'login_required' ? html`<span class="badge screening">Sign-in expired</span>` : html`<span class="badge offer">Connected</span>`) : html`<span class="badge withdrawn">Not connected</span>`}</td>
          <td>${u.queue || 0}</td><td>${u.last_ok ? timeAgo(u.last_ok) : '—'}</td></tr>`)}</tbody></table></div>`
        : emptyState('users', 'No admins or bidders yet', 'People who bid appear here once their accounts exist.')}</div></div>`);
    timer = setTimeout(() => { if (isCurrent() && tab === 'engine') draw(); }, 10000);
  }

  async function shares(pane) {
    const { shares } = await api.get('/api/shares');
    mount(pane, html`<div class="card">${shares.length ? html`<div class="table-wrap"><table class="table"><thead><tr><th>Résumé</th><th>Views</th><th>Expires</th><th></th></tr></thead><tbody>
      ${shares.map(s => { const dead = s.revoked || new Date(s.expires_at) < new Date(); return html`<tr><td><b>${s.title}</b><div class="muted small">${s.company} · v${s.version} · created ${timeAgo(s.created_at)}</div></td>
        <td>${s.views}</td><td>${s.revoked ? html`<span class="badge rejected">Revoked</span>` : dead ? html`<span class="badge withdrawn">Expired</span>` : fmtDate(s.expires_at)}</td>
        <td style="text-align:right">${dead ? '' : html`<button class="btn sm" data-copy="${s.token}">${icon('copy')}Copy</button> <button class="btn sm danger" data-revoke="${s.token}">Revoke</button>`}</td></tr>`; })}</tbody></table></div>`
      : emptyState('share', 'No shared links', 'Create a public link to a résumé from its application page. Links expire after 30 days.')}</div>`);
    pane.querySelectorAll('[data-copy]').forEach(b => b.onclick = () => copyText(`${siteOrigin()}/r/${b.dataset.copy}`, 'Link copied'));
    pane.querySelectorAll('[data-revoke]').forEach(b => b.onclick = async () => { await api.del(`/api/shares/${b.dataset.revoke}`); toast('Link revoked'); draw(); });
  }

  async function account(pane) {
    mount(pane, html`<div class="grid g2" style="align-items:start">
      <form class="card card-pad stack" id="nameForm"><h3>Profile</h3><label class="field">Name<input name="name" value="${user.name}"></label>
        <label class="field">Email<input value="${user.email}" disabled></label><div><button class="btn primary">Save</button></div></form>
      <form class="card card-pad stack" id="pwForm"><h3>Change password</h3><label class="field">Current password<input type="password" name="current_password" autocomplete="current-password"></label>
        <label class="field">New password<input type="password" name="new_password" minlength="8" autocomplete="new-password"></label><div><button class="btn primary">Update password</button></div></form></div>`);
    pane.querySelector('#nameForm').onsubmit = async (e) => { e.preventDefault(); try { const r = await api.put('/api/me', { name: e.target.name.value }); state.user.name = r.user.name; toast('Saved — refresh to update the menu'); } catch (err) { toastError(err); } };
    pane.querySelector('#pwForm').onsubmit = async (e) => { e.preventDefault(); try { await api.put('/api/me', { current_password: e.target.current_password.value, new_password: e.target.new_password.value }); e.target.reset(); toast('Password updated'); } catch (err) { toastError(err); } };
  }

  async function workspace(pane) {
    const [s, { rules }] = await Promise.all([loadSettings(true), api.get('/api/warning-rules')]);
    const a = s.platform;
    mount(pane, html`<div class="stack">
      <form class="card card-pad" id="wsForm">
        <div class="form-section"><div class="desc"><h3>AI engine mode</h3><p>Demo mode lets people explore without connecting a DeepSeek account.</p></div><div class="stack tight">
          <label class="check"><input type="radio" name="engine_mode" value="live" ${a.engine_mode !== 'demo' ? 'checked' : ''}><div><b>Live — each user's own DeepSeek</b><div class="muted small">Real tailoring through chat.deepseek.com, with the account each admin or bidder connected.</div></div></label>
          <label class="check"><input type="radio" name="engine_mode" value="demo" ${a.engine_mode === 'demo' ? 'checked' : ''}><div><b>Demo</b><div class="muted small">Instant placeholder documents. No browser needed.</div></div></label></div></div>
        <div class="form-section"><div class="desc"><h3>Sign-ups</h3><p>Self sign-ups become admins and stay pending until you approve them (Users → Pending). Bidders are always created by their admin.</p></div>
          <select name="registration_mode" style="max-width:320px">${[['approval', 'Sign-ups need master approval'], ['closed', 'Closed — accounts are created by you or by admins']].map(([v, l]) => html`<option value="${v}" ${a.registration_mode === v ? 'selected' : ''}>${l}</option>`)}</select></div>
        <div class="form-section"><div class="desc"><h3>Job pool imports</h3><p>Automatic imports from the configured sources.</p></div>
          <select name="jobpool_interval_hours" style="max-width:320px">${[['0', 'Manual only'], ['1', 'Every hour'], ['6', 'Every 6 hours'], ['12', 'Every 12 hours'], ['24', 'Once a day']].map(([v, l]) => html`<option value="${v}" ${String(Number(a.jobpool_interval_hours)) === v ? 'selected' : ''}>${l}</option>`)}</select></div>
        <div class="row" style="justify-content:flex-end"><button class="btn primary">${icon('check')}Save workspace settings</button></div></form>
      <div class="card"><div class="card-head"><div><h3>Warning rules</h3><p class="muted small">Keywords in a job description raise a flag. "Confirm" asks before saving; "Block" prevents it.</p></div><button class="btn sm" id="addRule">${icon('plus')}Rule</button></div>
        <div class="card-body stack tight" id="rules">${rules.map(r => html`<div class="row top" data-rule><input data-k="label" value="${r.label}" style="max-width:260px"><textarea data-k="keywords" rows="2" style="min-height:40px">${r.keywords}</textarea>
          <select data-k="behavior" style="width:120px">${['warn', 'confirm', 'block'].map(b => html`<option ${r.behavior === b ? 'selected' : ''}>${b}</option>`)}</select><button class="btn ghost icon" data-rm>${icon('x')}</button></div>`)}</div>
        <div class="card-foot"><button class="btn primary" id="saveRules">Save rules</button></div></div></div>`);
    pane.querySelector('#wsForm').onsubmit = async (e) => {
      e.preventDefault(); const f = e.target;
      try { await api.put('/api/settings', { platform: { engine_mode: f.engine_mode.value, registration_mode: f.registration_mode.value, jobpool_interval_hours: f.jobpool_interval_hours.value } }); toast('Workspace settings saved'); refreshEngine(); }
      catch (err) { toastError(err); }
    };
    const wireRm = () => pane.querySelectorAll('[data-rm]').forEach(b => b.onclick = () => b.closest('[data-rule]').remove());
    wireRm();
    pane.querySelector('#addRule').onclick = () => {
      pane.querySelector('#rules').insertAdjacentHTML('beforeend', `<div class="row top" data-rule><input data-k="label" placeholder="Rule name" style="max-width:260px"><textarea data-k="keywords" rows="2" style="min-height:40px" placeholder="comma, separated, keywords"></textarea><select data-k="behavior" style="width:120px"><option>warn</option><option>confirm</option><option>block</option></select><button class="btn ghost icon" data-rm>${icon('x')}</button></div>`);
      wireRm();
    };
    pane.querySelector('#saveRules').onclick = async () => {
      const items = [...pane.querySelectorAll('[data-rule]')].map(r => Object.fromEntries([...r.querySelectorAll('[data-k]')].map(i => [i.dataset.k, i.value])));
      try { await api.put('/api/warning-rules', { rules: items }); toast('Warning rules saved'); } catch (err) { toastError(err); }
    };
  }

  await draw();
  return () => clearTimeout(timer);
}
