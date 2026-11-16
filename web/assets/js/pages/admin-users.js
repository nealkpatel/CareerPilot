import { api } from '../api.js';
import { html, icon, mount, initials, colorFor, timeAgo, toast, toastError, confirmDialog, modal, busy, esc, debounce } from '../ui.js';
import { ROLE_LABEL } from '../app.js';

const tempPassword = () => 'Cp-' + Math.random().toString(36).slice(2, 8) + Math.random().toString(36).slice(2, 6).toUpperCase();
const ROLE_BADGE = { master: 'interview', admin: 'applied', bidder: 'saved' };

export default async function ({ el, user }) {
  let filter = 'all', q = '';

  async function draw() {
    const { users } = await api.get('/api/users');
    const admins = users.filter(u => u.role === 'admin');
    const counts = { all: users.length, master: 0, admin: 0, bidder: 0, pending: 0 };
    users.forEach(u => { counts[u.role]++; if (u.status === 'pending') counts.pending++; });
    const shown = users.filter(u => (filter === 'all' || u.role === filter || (filter === 'pending' && u.status === 'pending'))
      && (!q || `${u.name} ${u.email} ${u.parent_name || ''}`.toLowerCase().includes(q)));
    mount(el, html`
      <div class="page-head"><div><h1>Users</h1><p>Admins run their own teams of bidders. Each person's profiles, prompts and applications stay private to their team.</p></div>
        <button class="btn primary" id="addBtn">${icon('plus')}Add user</button></div>
      <div class="filters"><div class="seg">${[['all', 'All'], ['admin', 'Admins'], ['bidder', 'Bidders'], ['master', 'Masters'], ['pending', 'Pending']].map(([k, l]) => html`<button data-f="${k}" class="${filter === k ? 'on' : ''}">${l} <span class="n">${counts[k]}</span></button>`)}</div>
        <div class="grow"></div><div class="input-icon" style="width:260px">${icon('search')}<input id="q" placeholder="Search name, email, team" value="${q}"></div></div>
      <div class="card table-wrap"><table class="table"><thead><tr><th>User</th><th>Role</th><th>Team</th><th>Status</th><th class="hide-sm">Activity</th><th></th></tr></thead><tbody>
      ${shown.map(u => html`<tr>
        <td><div class="co"><div class="avatar" style="background:${colorFor(u.name)};color:#fff">${initials(u.name)}</div><div style="min-width:0"><div class="co-name truncate">${u.name}${u.id === user.id ? html` <span class="muted small">(you)</span>` : ''}</div><div class="co-sub truncate">${u.email}</div></div></div></td>
        <td><span class="badge ${ROLE_BADGE[u.role]}">${ROLE_LABEL[u.role]}</span></td>
        <td class="small">${u.role === 'admin' ? html`${u.bidders} bidder${u.bidders === 1 ? '' : 's'}` : u.role === 'bidder' ? (u.parent_name ? html`under <b>${u.parent_name}</b>` : html`<span style="color:var(--amber)">No admin</span>`) : html`<span class="muted">Platform</span>`}</td>
        <td><span class="badge ${u.status === 'active' ? 'completed' : u.status === 'pending' ? 'interview' : 'rejected'}">${u.status}</span></td>
        <td class="hide-sm muted small">${u.role === 'master' ? '—' : `${u.applications} apps · ${u.resumes} résumés`}<div>${u.last_login ? 'Signed in ' + timeAgo(u.last_login) : 'Never signed in'}</div></td>
        <td style="text-align:right;white-space:nowrap">${u.status === 'pending' ? html`<button class="btn sm primary" data-approve="${u.id}">Approve</button> ` : ''}<button class="btn sm" data-edit="${u.id}">${icon('edit')}Manage</button></td></tr>`)}
      ${shown.length ? '' : html`<tr><td colspan="6" class="muted" style="text-align:center;padding:28px">No users match.</td></tr>`}
      </tbody></table></div>`);
    el.querySelectorAll('[data-f]').forEach(b => b.onclick = () => { filter = b.dataset.f; draw(); });
    el.querySelector('#q').oninput = debounce((e) => { q = e.target.value.toLowerCase(); draw().then(() => { const i = el.querySelector('#q'); i.focus(); i.setSelectionRange(i.value.length, i.value.length); }); }, 250);
    el.querySelector('#addBtn').onclick = () => openForm(null, admins);
    el.querySelectorAll('[data-edit]').forEach(b => b.onclick = () => openForm(users.find(x => String(x.id) === b.dataset.edit), admins));
    el.querySelectorAll('[data-approve]').forEach(b => b.onclick = async () => { try { await api.put(`/api/users/${b.dataset.approve}`, { status: 'active' }); toast('Account approved'); draw(); } catch (e) { toastError(e); } });
  }

  function openForm(u, admins) {
    const isNew = !u, self = u && u.id === user.id;
    const m = modal({
      title: isNew ? 'Add a user' : `Manage ${u.name}`,
      body: String(html`<div class="stack">
        ${isNew ? html`<label class="field">Full name<input id="fName" placeholder="Alex Morgan"></label><label class="field">Email<input id="fEmail" type="email" placeholder="alex@company.com"></label>`
          : html`<div class="row"><div class="avatar" style="background:${colorFor(u.name)};color:#fff">${initials(u.name)}</div><div><b>${u.name}</b><div class="muted small">${u.email}</div></div></div>`}
        <label class="field">Role<select id="fRole" ${self ? 'disabled' : ''}>${['admin', 'bidder', 'master'].map(r => html`<option value="${r}" ${(u?.role || 'admin') === r ? 'selected' : ''}>${ROLE_LABEL[r]}</option>`)}</select>
          <span class="hint" id="roleHint"></span></label>
        <label class="field" id="parentWrap">Admin (team)<select id="fParent"><option value="">— Choose an admin —</option>${admins.filter(a => !u || a.id !== u.id).map(a => html`<option value="${a.id}" ${u?.parent_id === a.id ? 'selected' : ''}>${a.name} · ${a.email}</option>`)}</select>
          <span class="hint">Moving a bidder to another admin removes the profiles the old admin assigned.</span></label>
        <label class="field">${isNew ? 'Temporary password' : 'Reset password'} <span class="hint">${isNew ? 'Share it with the user; they can change it in Settings → Account.' : 'Leave blank to keep the current password.'}</span>
          <div class="row"><input id="fPw" value="${isNew ? tempPassword() : ''}" placeholder="${isNew ? '' : 'New password (8+ characters)'}"><button type="button" class="btn sm" id="genPw">Generate</button><button type="button" class="btn sm ghost icon" id="copyPw" title="Copy">${icon('copy')}</button></div></label>
        ${isNew || self ? '' : html`<div class="row wrap" style="padding-top:8px;border-top:1px solid var(--border)">
          <button type="button" class="btn sm" id="toggle">${u.status === 'active' ? 'Suspend account' : u.status === 'pending' ? 'Approve account' : 'Reactivate account'}</button><div class="grow"></div>
          <button type="button" class="btn sm danger" id="del">${icon('trash')}Delete user</button></div>`}
        <div id="fErr"></div></div>`),
      foot: `<button class="btn" data-close>Cancel</button><button class="btn primary" data-save>${isNew ? 'Create user' : 'Save changes'}</button>`,
    });
    const hints = { master: 'Manages the whole platform. Does not bid.', admin: 'Bids, owns profiles and prompts, and manages their own bidders.', bidder: 'Bids using profiles their admin assigns.' };
    const sync = () => { const r = m.$('#fRole').value; m.$('#roleHint').textContent = hints[r]; m.$('#parentWrap').classList.toggle('hidden', r !== 'bidder'); };
    m.$('#fRole').onchange = sync; sync();
    m.$('#genPw').onclick = () => { m.$('#fPw').value = tempPassword(); };
    m.$('#copyPw').onclick = () => { navigator.clipboard?.writeText(m.$('#fPw').value); toast('Password copied'); };
    m.$('#toggle')?.addEventListener('click', async () => {
      try { await api.put(`/api/users/${u.id}`, { status: u.status === 'active' ? 'suspended' : 'active' }); toast('Account updated'); m.close(); draw(); } catch (e) { toastError(e); }
    });
    m.$('#del')?.addEventListener('click', async () => {
      const extra = u.role === 'admin' && u.bidders ? ` Their ${u.bidders} bidder(s) will be left without an admin.` : '';
      if (!await confirmDialog({ title: 'Delete user?', message: `<b>${esc(u.name)}</b> and all of their profiles, prompts, applications and documents will be permanently deleted.${extra}`, confirm: 'Delete user', danger: true })) return;
      try { await api.del(`/api/users/${u.id}`); toast('User deleted'); m.close(); draw(); } catch (e) { toastError(e); }
    });
    m.$('[data-save]').onclick = async (e) => {
      const btn = e.currentTarget, role = m.$('#fRole').value;
      const body = { role, parent_id: role === 'bidder' ? m.$('#fParent').value : null };
      const pw = m.$('#fPw').value.trim();
      if (pw) body.password = pw;
      if (self) delete body.role;
      if (isNew) Object.assign(body, { name: m.$('#fName').value.trim(), email: m.$('#fEmail').value.trim() });
      busy(btn, true, 'Saving…');
      try {
        if (isNew) await api.post('/api/users', body); else await api.put(`/api/users/${u.id}`, body);
        m.close(); toast(isNew ? 'User created — share the email and temporary password' : 'User updated'); draw();
      } catch (err) { m.$('#fErr').innerHTML = `<div class="alert error">${icon('alert')}<div>${esc(err.message)}</div></div>`; busy(btn, false); }
    };
  }
  await draw();
}
