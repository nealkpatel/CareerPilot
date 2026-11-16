import { api } from '../api.js';
import { html, icon, mount, timeAgo, initials, colorFor, toast, toastError, busy, modal, confirmDialog, emptyState, esc } from '../ui.js';
import { go } from '../app.js';

const tempPassword = () => 'Cp-' + Math.random().toString(36).slice(2, 8) + Math.random().toString(36).slice(2, 6).toUpperCase();

export default async function ({ el }) {
  async function draw() {
    const { bidders, profiles } = await api.get('/api/team');
    const label = (id) => profiles.find(p => p.id === id)?.label || '—';
    mount(el, html`
      <div class="page-head"><div><h1>My bidders</h1><p>Create accounts for your bidders, choose which candidate profiles each one can use, and follow their work.</p></div>
        <button class="btn primary" id="addBtn">${icon('plus')}Add bidder</button></div>
      ${!profiles.length ? html`<div class="alert info" style="margin-bottom:16px">${icon('info')}<div><b>Create a profile first</b>Bidders tailor résumés with the profiles you assign them. <a href="#/profiles/new">Create a profile</a></div></div>` : ''}
      <div class="card">${bidders.length ? html`<div class="table-wrap"><table class="table"><thead><tr><th>Bidder</th><th>Profiles</th><th>Applications</th><th class="hide-sm">Last activity</th><th>Status</th><th></th></tr></thead><tbody>
        ${bidders.map(b => html`<tr>
          <td><div class="co"><div class="avatar" style="background:${colorFor(b.name)};color:#fff">${initials(b.name)}</div><div style="min-width:0"><div class="co-name truncate">${b.name}</div><div class="co-sub truncate">${b.email}</div></div></div></td>
          <td>${b.profiles.length ? html`<div class="row wrap" style="gap:4px">${b.profiles.map(id => html`<span class="tag">${label(id)}</span>`)}</div>` : html`<span class="muted small">None assigned</span>`}</td>
          <td><a href="#/applications?scope=team&owner=${b.id}">${b.applications}</a> <span class="muted small">· ${b.applied} applied · ${b.resumes} résumés</span></td>
          <td class="hide-sm muted small">${b.last_activity ? timeAgo(b.last_activity) : b.last_login ? 'Signed in ' + timeAgo(b.last_login) : 'Never'}</td>
          <td><span class="badge ${b.status === 'active' ? 'completed' : 'rejected'}">${b.status}</span></td>
          <td style="text-align:right;white-space:nowrap"><button class="btn sm" data-edit="${b.id}">${icon('edit')}Manage</button></td></tr>`)}
      </tbody></table></div>`
        : emptyState('users', 'No bidders yet', 'Add a bidder, give them a temporary password, and assign the candidate profiles they should apply with.', '<button class="btn primary" id="addBtn2">Add your first bidder</button>')}</div>`);
    el.querySelector('#addBtn').onclick = () => openForm(null, profiles);
    el.querySelector('#addBtn2')?.addEventListener('click', () => openForm(null, profiles));
    el.querySelectorAll('[data-edit]').forEach(b => b.onclick = () => openForm(bidders.find(x => String(x.id) === b.dataset.edit), profiles));
  }

  function openForm(b, profiles) {
    const isNew = !b;
    const pw = isNew ? tempPassword() : '';
    const m = modal({
      title: isNew ? 'Add a bidder' : `Manage ${b.name}`,
      body: String(html`<div class="stack">
        <label class="field">Full name<input id="fName" value="${b?.name || ''}" placeholder="Sam Lee"></label>
        ${isNew ? html`<label class="field">Email (used to sign in)<input id="fEmail" type="email" placeholder="sam@company.com"></label>` : html`<label class="field">Email<input value="${b.email}" disabled></label>`}
        <label class="field">${isNew ? 'Temporary password' : 'Reset password'} <span class="hint">${isNew ? 'Share it with the bidder; they can change it in Settings → Account.' : 'Leave blank to keep the current password.'}</span>
          <div class="row"><input id="fPw" value="${pw}" placeholder="${isNew ? '' : 'New password (8+ characters)'}"><button type="button" class="btn sm" id="genPw">Generate</button><button type="button" class="btn sm ghost icon" id="copyPw" title="Copy">${icon('copy')}</button></div></label>
        <div class="field">Profiles this bidder can use
          ${profiles.length ? html`<div class="stack tight" style="margin-top:4px">${profiles.map(p => html`<label class="check"><input type="checkbox" data-prof="${p.id}" ${b?.profiles.includes(p.id) ? 'checked' : ''}><span>${p.label}</span></label>`)}</div>`
            : html`<p class="muted small">You have no profiles yet. <a href="#/profiles/new">Create one</a>.</p>`}</div>
        ${isNew ? '' : html`<div class="row wrap" style="padding-top:8px;border-top:1px solid var(--border)">
          <button type="button" class="btn sm" id="toggle">${b.status === 'active' ? 'Suspend account' : 'Reactivate account'}</button>
          <a class="btn sm ghost" href="#/applications?scope=team&owner=${b.id}">${icon('briefcase')}View applications</a><div class="grow"></div>
          <button type="button" class="btn sm danger" id="del">${icon('trash')}Delete</button></div>`}
        <div id="fErr"></div></div>`),
      foot: `<button class="btn" data-close>Cancel</button><button class="btn primary" data-save>${isNew ? 'Create bidder' : 'Save changes'}</button>`,
    });
    m.$('#genPw').onclick = () => { m.$('#fPw').value = tempPassword(); };
    m.$('#copyPw').onclick = () => { navigator.clipboard?.writeText(m.$('#fPw').value); toast('Password copied'); };
    m.$$('a').forEach(a => a.addEventListener('click', () => m.close()));
    m.$('#toggle')?.addEventListener('click', async () => {
      try { await api.put(`/api/team/${b.id}`, { status: b.status === 'active' ? 'suspended' : 'active' }); toast(b.status === 'active' ? 'Bidder suspended' : 'Bidder reactivated'); m.close(); draw(); } catch (err) { toastError(err); }
    });
    m.$('#del')?.addEventListener('click', async () => {
      if (!await confirmDialog({ title: 'Delete bidder?', message: `<b>${esc(b.name)}</b> and all of their applications and documents will be permanently deleted.`, confirm: 'Delete bidder', danger: true })) return;
      try { await api.del(`/api/team/${b.id}`); toast('Bidder deleted'); m.close(); draw(); } catch (err) { toastError(err); }
    });
    m.$('[data-save]').onclick = async (e) => {
      const btn = e.currentTarget;
      const body = { name: m.$('#fName').value.trim(), password: m.$('#fPw').value.trim(), profile_ids: m.$$('[data-prof]').filter(c => c.checked).map(c => Number(c.dataset.prof)) };
      if (isNew) body.email = m.$('#fEmail').value.trim();
      busy(btn, true, 'Saving…');
      try {
        if (isNew) await api.post('/api/team', body); else await api.put(`/api/team/${b.id}`, body);
        m.close();
        toast(isNew ? 'Bidder created — share the email and temporary password with them' : 'Bidder updated');
        draw();
      } catch (err) { m.$('#fErr').innerHTML = `<div class="alert error">${icon('alert')}<div>${esc(err.message)}</div></div>`; busy(btn, false); }
    };
  }
  await draw();
}
