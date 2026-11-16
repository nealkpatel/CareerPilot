import { api } from '../api.js';
import { html, icon, mount, busy, toast, toastError, $$ } from '../ui.js';
import { go, loadSettings, state } from '../app.js';

const blankJob = () => ({ role: '', company: '', dates: '', work_method: '', location: '', notes: '' });

export default async function ({ el, params }) {
  const id = params[0] ? Number(params[0]) : null;
  if (state.user.role !== 'admin') { go('/profiles'); return; }
  const [{ prompts }, { templates }, settings, team] = await Promise.all([api.get('/api/prompts'), api.get('/api/templates'), loadSettings(true), api.get('/api/team')]);
  const bidders = team.bidders;
  const p = id ? (await api.get(`/api/profiles/${id}`)).profile
    : { label: '', name: '', headline: '', contact: ['', '', '', ''], jobs: [blankJob()], education: [{ degree: '', school: '', dates: '' }], certifications: [], background: '', prompt_id: '', template_id: '', assigned_to: [] };
  p.assigned_to = p.assigned_to || [];
  while (p.contact.length < 4) p.contact.push('');

  function collect() {
    const f = el.querySelector('#pf');
    p.label = f.label.value; p.name = f.name.value; p.headline = f.headline.value; p.background = f.background.value;
    p.prompt_id = f.prompt_id.value; p.template_id = f.template_id.value;
    p.assigned_to = $$('[data-assign]', el).filter(c => c.checked).map(c => Number(c.dataset.assign));
    p.contact = $$('[data-contact]', el).map(i => i.value);
    const rows = (sel, keys) => $$(sel, el).map(r => Object.fromEntries(keys.map(k => [k, r.querySelector(`[data-f=${k}]`).value])));
    p.jobs = rows('[data-job]', ['role', 'company', 'dates', 'work_method', 'location', 'notes']);
    p.education = rows('[data-edu]', ['degree', 'school', 'location', 'dates']);
    p.certifications = rows('[data-cert]', ['name', 'issuer', 'dates']);
  }

  function draw() {
    mount(el, html`
      <div class="crumb"><a href="#/profiles">Profiles</a>${icon('chevron')}<span>${id ? p.label : 'New'}</span></div>
      <div class="page-head"><div><h1>${id ? 'Edit profile' : 'New profile'}</h1><p>These facts are fixed on every résumé. The AI only writes summary, skills and bullet wording.</p></div>
        <div class="row"><a class="btn" href="#/profiles">Cancel</a><button class="btn primary" id="saveBtn">${icon('check')}Save profile</button></div></div>
      <form id="pf" class="card card-pad" novalidate>
        <div class="form-section"><div class="desc"><h3>Identity</h3><p>How the candidate appears at the top of each document.</p></div>
          <div class="grid g2">
            <label class="field">Full name *<input name="name" value="${p.name}" placeholder="Alex Morgan" required></label>
            <label class="field">Profile label <span class="hint">Shown in menus — e.g. "Alex · Backend"</span><input name="label" value="${p.label}" placeholder="Defaults to the name"></label>
            <label class="field" style="grid-column:1/-1">Headline <span class="hint">Used when no role title is shown</span><input name="headline" value="${p.headline}" placeholder="Senior Software Engineer"></label>
          </div></div>
        <div class="form-section"><div class="desc"><h3>Contact</h3><p>Shown in the header, in this order. Leave blank to omit.</p></div>
          <div class="grid g2">${['Email', 'Phone', 'LinkedIn / website', 'City, State'].map((l, i) => html`<label class="field">${l}<input data-contact value="${p.contact[i] || ''}"></label>`)}
            ${p.contact.slice(4).map(c => html`<label class="field">Extra<input data-contact value="${c}"></label>`)}</div>
          <button type="button" class="btn sm ghost" id="addContact" style="margin-top:8px">${icon('plus')}Add contact line</button></div>
        <div class="form-section"><div class="desc"><h3>Work history</h3><p>Most recent first. Company and dates are never changed by the AI. Notes guide what each role's bullets should cover.</p></div>
          <div class="stack">${p.jobs.map((j, i) => html`<div class="repeat-item stack tight" data-job>
            <div class="drag-head">${icon('briefcase')}Role ${i + 1}<div class="grow"></div>
              <button type="button" class="btn sm ghost icon" data-up="${i}" ${i ? '' : 'disabled'} title="Move up">${icon('arrowUp')}</button>
              <button type="button" class="btn sm ghost icon" data-down="${i}" ${i < p.jobs.length - 1 ? '' : 'disabled'} title="Move down">${icon('arrowDown')}</button>
              <button type="button" class="btn sm ghost icon" data-rmjob="${i}" title="Remove">${icon('trash')}</button></div>
            <div class="grid g2">
              <label class="field">Job title<input data-f="role" value="${j.role}" placeholder="Senior Software Engineer"></label>
              <label class="field">Company<input data-f="company" value="${j.company}" placeholder="Northwind"></label>
              <label class="field">Dates<input data-f="dates" value="${j.dates}" placeholder="Jan 2022 - Present"></label>
              <div class="grid g2"><label class="field">Arrangement<select data-f="work_method">${['', 'Remote', 'Hybrid', 'Onsite'].map(w => html`<option ${j.work_method === w ? 'selected' : ''} value="${w}">${w || '—'}</option>`)}</select></label>
              <label class="field">Location<input data-f="location" value="${j.location || ''}" placeholder="Seattle, WA"></label></div>
            </div>
            <label class="field">Notes for the AI <span class="hint">Industry, products, responsibilities — one per line</span><textarea data-f="notes" rows="3" placeholder="Healthcare billing platform&#10;Owned projects end-to-end; mentored 3 engineers">${j.notes || ''}</textarea></label>
          </div>`)}
          <button type="button" class="btn" id="addJob" style="align-self:flex-start">${icon('plus')}Add role</button></div></div>
        <div class="form-section"><div class="desc"><h3>Education</h3></div>
          <div class="stack tight">${p.education.map((e, i) => html`<div class="row top" data-edu><div class="grid g4 grow">
            <input data-f="degree" value="${e.degree}" placeholder="B.S. Computer Science"><input data-f="school" value="${e.school}" placeholder="University"><input data-f="location" value="${e.location || ''}" placeholder="City, State (optional)"><input data-f="dates" value="${e.dates}" placeholder="2014 - 2018"></div>
            <button type="button" class="btn ghost icon" data-rmedu="${i}">${icon('x')}</button></div>`)}
          <button type="button" class="btn sm ghost" id="addEdu" style="align-self:flex-start">${icon('plus')}Add education</button></div></div>
        <div class="form-section"><div class="desc"><h3>Certifications</h3><p>Optional.</p></div>
          <div class="stack tight">${p.certifications.map((c, i) => html`<div class="row top" data-cert><div class="grid g3 grow">
            <input data-f="name" value="${c.name}" placeholder="AWS Solutions Architect"><input data-f="issuer" value="${c.issuer}" placeholder="Issuer"><input data-f="dates" value="${c.dates}" placeholder="2023"></div>
            <button type="button" class="btn ghost icon" data-rmcert="${i}">${icon('x')}</button></div>`)}
          <button type="button" class="btn sm ghost" id="addCert" style="align-self:flex-start">${icon('plus')}Add certification</button></div></div>
        <div class="form-section"><div class="desc"><h3>Background notes</h3><p>Anything else the AI should know: years of experience, domains, achievements, preferences. Inserted as {background}.</p></div>
          <textarea name="background" rows="6" placeholder="10 years building distributed systems in fintech and healthcare…">${p.background}</textarea></div>
        <div class="form-section"><div class="desc"><h3>Bidders</h3><p>Bidders you assign can tailor résumés with this profile. They can't edit it.</p></div>
          ${bidders.length ? html`<div class="grid g2">${bidders.map(b => html`<label class="check"><input type="checkbox" data-assign="${b.id}" ${p.assigned_to.includes(b.id) ? 'checked' : ''}><div><b>${b.name}</b><div class="muted small">${b.email}${b.status !== 'active' ? ' · ' + b.status : ''}</div></div></label>`)}</div>`
            : html`<p class="muted">You have no bidders yet. <a href="#/team">Add bidders</a>, then assign this profile.</p>`}</div>
        <div class="form-section"><div class="desc"><h3>Defaults</h3><p>Used when generating for this profile — also when your bidders use it.</p></div>
          <div class="grid g2">
            <label class="field">Prompt<select name="prompt_id"><option value="">Use my default prompt</option>${prompts.map(x => html`<option value="${x.id}" ${String(x.id) === String(p.prompt_id) ? 'selected' : ''}>${x.label}</option>`)}</select></label>
            <label class="field">Template<select name="template_id"><option value="">Use my default (${templates.find(t => t.id === settings.user.default_template_id)?.label || 'Classic'})</option>${templates.map(t => html`<option value="${t.id}" ${t.id === p.template_id ? 'selected' : ''}>${t.label}</option>`)}</select></label>
          </div></div>
      </form>`);
    bind();
  }

  function bind() {
    const re = (fn) => () => { collect(); fn(); draw(); };
    el.querySelector('#addJob').onclick = re(() => p.jobs.push(blankJob()));
    el.querySelector('#addEdu').onclick = re(() => p.education.push({ degree: '', school: '', location: '', dates: '' }));
    el.querySelector('#addCert').onclick = re(() => p.certifications.push({ name: '', issuer: '', dates: '' }));
    el.querySelector('#addContact').onclick = re(() => p.contact.push(''));
    el.querySelectorAll('[data-rmjob]').forEach(b => b.onclick = re(() => p.jobs.splice(Number(b.dataset.rmjob), 1)));
    el.querySelectorAll('[data-rmedu]').forEach(b => b.onclick = re(() => p.education.splice(Number(b.dataset.rmedu), 1)));
    el.querySelectorAll('[data-rmcert]').forEach(b => b.onclick = re(() => p.certifications.splice(Number(b.dataset.rmcert), 1)));
    el.querySelectorAll('[data-up]').forEach(b => b.onclick = re(() => { const i = Number(b.dataset.up); [p.jobs[i - 1], p.jobs[i]] = [p.jobs[i], p.jobs[i - 1]]; }));
    el.querySelectorAll('[data-down]').forEach(b => b.onclick = re(() => { const i = Number(b.dataset.down); [p.jobs[i + 1], p.jobs[i]] = [p.jobs[i], p.jobs[i + 1]]; }));
    el.querySelector('#saveBtn').onclick = async (e) => {
      collect();
      if (!p.name.trim()) { toast('Enter the candidate name.', 'error'); el.querySelector('[name=name]').focus(); return; }
      const cbtn = e.currentTarget; busy(cbtn, true, 'Saving…');
      try {
        const r = id ? await api.put(`/api/profiles/${id}`, p) : await api.post('/api/profiles', p);
        if (bidders.length) await api.put(`/api/profiles/${r.profile.id}/assignments`, { user_ids: p.assigned_to });
        toast('Profile saved');
        go(id ? '/profiles' : `/profiles`);
        return r;
      } catch (err) { toastError(err); busy(cbtn, false); }
    };
  }
  draw();
}
