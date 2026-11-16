import { api } from '../api.js';
import { html, icon, mount, emptyState, initials, colorFor, timeAgo, toast, toastError, confirmDialog, esc } from '../ui.js';
import { go, loadSettings, state } from '../app.js';

export default async function ({ el }) {
  const [{ profiles }, settings] = await Promise.all([api.get('/api/profiles'), loadSettings(true)]);
  const def = String(settings.user.default_profile_id || '');
  const isAdmin = state.user.role === 'admin';
  mount(el, html`
    <div class="page-head"><div><h1>${isAdmin ? 'Profiles' : 'My profiles'}</h1><p>${isAdmin ? 'Candidate profiles hold the facts that never change. Assign them to your bidders so they can apply with them.' : 'The candidate profiles your admin assigned to you. Your admin keeps them up to date.'}</p></div>
      ${isAdmin ? html`<a class="btn primary" href="#/profiles/new">${icon('plus')}New profile</a>` : ''}</div>
    ${profiles.length ? html`<div class="grid g3">${profiles.map(p => html`<div class="card" style="display:flex;flex-direction:column">
      <div class="card-body stack" style="flex:1">
        <div class="row"><div class="avatar" style="width:46px;height:46px;font-size:16px;background:${colorFor(p.name)};color:#fff">${initials(p.name)}</div>
          <div class="grow"><div class="row"><h3 class="truncate">${p.label}</h3>${String(p.id) === def ? html`<span class="badge applied">Default</span>` : ''}</div>
          <p class="muted small truncate">${p.headline || p.jobs[0]?.role || 'No headline'}</p></div></div>
        <div class="stack tight small">${p.jobs.slice(0, 3).map(j => html`<div class="row" style="gap:8px">${icon('briefcase')}<span class="truncate"><b>${j.role}</b> · ${j.company}</span><span class="muted" style="margin-left:auto;white-space:nowrap">${(j.dates || '').split(/ to | - |–/)[0]}</span></div>`)}
          ${p.jobs.length > 3 ? html`<span class="muted">+${p.jobs.length - 3} more roles</span>` : ''}${!p.jobs.length ? html`<span class="muted">No work history yet</span>` : ''}</div>
      </div>
      <div class="card-foot" style="justify-content:space-between">${p.can_edit
        ? html`<span class="muted small">${p.assigned_to.length ? `Assigned to ${p.assigned_to.length} bidder${p.assigned_to.length === 1 ? '' : 's'}` : 'Not assigned'} · ${timeAgo(p.updated_at)}</span>
        <div class="row" style="gap:6px"><button class="btn sm ghost icon" data-dup="${p.id}" title="Duplicate">${icon('copy')}</button>
        <button class="btn sm ghost icon" data-del="${p.id}" title="Delete">${icon('trash')}</button><a class="btn sm" href="#/profiles/${p.id}">${icon('edit')}Edit</a></div>`
        : html`<span class="muted small">Assigned by ${p.owner_name} · ${p.app_count} application${p.app_count === 1 ? '' : 's'}</span>
        <a class="btn sm" href="#/applications/new">${icon('sparkles')}Use</a>`}</div>
    </div>`)}</div>`
      : html`<div class="card">${isAdmin ? emptyState('user', 'Create your first profile', 'Add the candidate\'s work history once. Every tailored résumé keeps these facts exactly as entered — the AI only rewrites the wording.', '<a class="btn primary" href="#/profiles/new">New profile</a>')
        : emptyState('user', 'No profiles assigned yet', 'Ask your admin to assign you a candidate profile. You can tailor résumés as soon as one is assigned.')}</div>`}`);

  el.querySelectorAll('[data-dup]').forEach(b => b.onclick = async () => { const r = await api.post(`/api/profiles/${b.dataset.dup}/duplicate`); toast('Profile duplicated'); go(`/profiles/${r.profile.id}`); });
  el.querySelectorAll('[data-del]').forEach(b => b.onclick = async () => {
    const p = profiles.find(x => String(x.id) === b.dataset.del);
    if (await confirmDialog({ title: 'Delete profile?', message: `<b>${esc(p.label)}</b> will be removed. Existing applications and documents are kept.`, confirm: 'Delete', danger: true })) {
      try { await api.del(`/api/profiles/${p.id}`); toast('Profile deleted'); go('/profiles?r=' + Date.now()); } catch (e) { toastError(e); }
    }
  });
}
