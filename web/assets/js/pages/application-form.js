import { api } from '../api.js';
import { html, raw, icon, mount, busy, toast, toastError, debounce, confirmDialog, esc, formData } from '../ui.js';
import { go, loadSettings, state } from '../app.js';

const words = (t) => (t.trim().match(/\S+/g) || []).length;

export function warningsList(ws) {
  if (!ws?.length) return html`<div class="alert ok">${icon('checkCircle')}<div><b>No flags</b>Nothing in this posting matches your warning rules.</div></div>`;
  return html`<div class="stack tight">${ws.map(w => html`<div class="alert ${w.behavior === 'warn' ? 'warn' : 'error'}">${icon('alert')}<div>
    <b>${w.label}</b>${w.matches?.length ? html`<span class="small">${w.id === 'duplicate' ? '' : 'Found: '}${w.matches.join(', ')}</span>` : ''}
    ${w.applications?.length ? html`<div class="small" style="margin-top:4px">${w.applications.map(a => html`<a href="#/applications/${a.id}">${a.title}</a> `)}</div>` : ''}
  </div></div>`)}</div>`;
}

export default async function ({ el, params, query }) {
  const editId = params[0] ? Number(params[0]) : null;
  const [{ profiles }, settings] = await Promise.all([api.get('/api/profiles'), loadSettings()]);
  let a = { company: '', title: '', url: '', location: '', work_arrangement: '', salary: '', description: '', questions: '', notes: '', profile_id: settings.user.default_profile_id || profiles[0]?.id || '' };
  let jobId = null, sharedFrom = null;
  if (editId) a = (await api.get(`/api/applications/${editId}`)).application;
  else if (query.job) {
    const { job } = await api.get(`/api/jobs/${query.job}`);
    jobId = job.id;
    Object.assign(a, { company: job.company, title: job.title, url: job.url, location: job.location, salary: job.salary, description: job.description, work_arrangement: job.remote ? 'Remote' : '' });
  } else if (query.shared) {
    // A job another user shared: copy only its public posting details.
    const { job } = await api.get(`/api/jobs/shared/${query.shared}`);
    sharedFrom = job.id;
    Object.assign(a, { company: job.company, title: job.title, url: job.url, location: job.location, salary: job.salary, description: job.description, work_arrangement: job.work_arrangement });
  }

  mount(el, html`
    <div class="crumb"><a href="#/applications">Applications</a>${icon('chevron')}<span>${editId ? 'Edit' : 'New'}</span></div>
    <div class="page-head"><div><h1>${editId ? 'Edit application' : 'New application'}</h1><p>${editId ? 'Update the job details. Regenerate to apply changes to the documents.' : 'Start from a job link, or fill the details in yourself.'}</p></div></div>
    ${!profiles.length ? html`<div class="alert warn" style="margin-bottom:16px">${icon('alert')}<div><b>${state.user.role === 'admin' ? 'Create a profile first' : 'No profile assigned yet'}</b>Résumés are built from a profile's fixed facts (name, employers, dates, education). ${state.user.role === 'admin' ? raw('<a href="#/profiles/new">Create a profile</a>') : 'Ask your admin to assign you one.'}</div></div>` : ''}
    <div class="detail-grid">
      <form id="appForm" class="stack" novalidate>
        ${editId ? '' : html`<div class="card card-pad stack">
          <div class="row"><span class="ico tint-brand" style="width:34px;height:34px;border-radius:10px;display:grid;place-items:center">${icon('link')}</span>
            <div><h3>Import from a job link</h3><p class="muted small">Works with Greenhouse, Lever, Ashby and most career pages that publish job data.</p></div></div>
          <div class="row"><input id="scrapeUrl" placeholder="https://boards.greenhouse.io/company/jobs/123456" value="${a.url}" class="grow">
            <button type="button" class="btn primary" id="scrapeBtn">${icon('download')}Fetch posting</button></div>
          <div id="scrapeMsg"></div></div>`}

        <div class="card card-pad stack">
          <h3>Job details</h3>
          <div class="grid g2">
            <label class="field">Company *<input name="company" required value="${a.company}" placeholder="Acme Corp"></label>
            <label class="field">Position *<input name="title" required value="${a.title}" placeholder="Senior Software Engineer"></label>
            <label class="field">Location<input name="location" value="${a.location}" placeholder="Austin, TX"></label>
            <label class="field">Work arrangement<select name="work_arrangement">${['', 'Remote', 'Hybrid', 'Onsite'].map(w => html`<option value="${w}" ${a.work_arrangement === w ? 'selected' : ''}>${w || 'Not specified'}</option>`)}</select></label>
            <label class="field">Salary<input name="salary" value="${a.salary}" placeholder="$150k – $180k"></label>
            <label class="field">Job link<input name="url" value="${a.url}" placeholder="https://…"></label>
          </div>
          <label class="field"><span class="row between">Job description * <span class="hint" id="wc"></span></span>
            <textarea name="description" rows="14" placeholder="Paste the full job description here.">${a.description}</textarea></label>
        </div>

        <div class="card card-pad stack">
          <div><h3>Application questions</h3><p class="muted small">Optional. Put each question on its own line or number them — answers are written after the résumé.</p></div>
          <label class="field"><span class="row between">Questions <span class="hint" id="qc"></span></span>
            <textarea name="questions" rows="5" placeholder="1. Why do you want to work at Acme?&#10;2. Describe a system you designed end to end.">${a.questions}</textarea></label>
          <label class="field">Private notes<textarea name="notes" rows="3" placeholder="Referral, recruiter name, follow-up dates…">${a.notes || ''}</textarea></label>
        </div>
      </form>

      <div class="stack" style="position:sticky;top:80px">
        <div class="card card-pad stack">
          <h3>Candidate profile</h3>
          <select name="profile_id" form="appForm">${profiles.map(p => html`<option value="${p.id}" ${String(p.id) === String(a.profile_id) ? 'selected' : ''}>${p.label}</option>`)}${profiles.length ? '' : html`<option value="">No profiles yet</option>`}</select>
          <p class="muted small">The profile supplies fixed facts. DeepSeek writes the summary, skills, bullets, cover letter and answers.</p>
        </div>
        <div class="card card-pad stack"><div class="row between"><h3>Things to know</h3><span id="wSpin"></span></div><div id="warnings"><p class="muted small">Add a company and description to check for duplicates and warning rules.</p></div></div>
        <div class="card card-pad stack">
          <button class="btn primary lg" id="saveGen" ${profiles.length ? '' : 'disabled'}>${icon('sparkles')}${editId ? 'Save & regenerate' : 'Save & tailor documents'}</button>
          <button class="btn" id="saveOnly">${icon('bookmark')}${editId ? 'Save changes' : 'Save for later'}</button>
          <p class="muted small" style="text-align:center"><span class="kbd">Ctrl</span> + <span class="kbd">Enter</span> saves and tailors</p>
        </div>
      </div>
    </div>`);

  const form = el.querySelector('#appForm');
  const counts = () => {
    el.querySelector('#wc').textContent = `${words(form.description.value)} words`;
    const qn = form.questions.value.split('\n').filter(l => l.trim()).length;
    el.querySelector('#qc').textContent = qn ? `${qn} line${qn > 1 ? 's' : ''}` : '';
  };
  const check = debounce(async () => {
    const d = formData(form);
    if (!d.company && !d.description) return;
    mount(el.querySelector('#wSpin'), '<span class="spinner"></span>');
    try {
      const r = await api.post('/api/applications/check', { ...d, id: editId });
      mount(el.querySelector('#warnings'), warningsList(r.warnings));
    } catch {}
    mount(el.querySelector('#wSpin'), '');
  }, 500);
  form.addEventListener('input', (e) => { counts(); if (['company', 'url', 'description'].includes(e.target.name)) check(); });
  counts();
  if (a.company || a.description) check();

  el.querySelector('#scrapeBtn')?.addEventListener('click', async (e) => {
    const url = el.querySelector('#scrapeUrl').value.trim();
    if (!url) return el.querySelector('#scrapeUrl').focus();
    const cbtn = e.currentTarget; busy(cbtn, true, 'Reading…');
    mount(el.querySelector('#scrapeMsg'), '');
    try {
      const r = await api.post('/api/scrape', { url });
      for (const k of ['company', 'title', 'location', 'salary', 'url', 'description']) if (r[k]) form[k].value = r[k];
      if (r.work_arrangement) form.work_arrangement.value = r.work_arrangement;
      counts();
      mount(el.querySelector('#warnings'), warningsList(r.warnings));
      mount(el.querySelector('#scrapeMsg'), html`<div class="alert ok">${icon('checkCircle')}<div><b>Posting imported</b><span class="small">Source: ${r.source} · ${words(r.description)} words. Review the fields below.</span></div></div>`);
    } catch (err) {
      mount(el.querySelector('#scrapeMsg'), html`<div class="alert warn">${icon('alert')}<div><b>Couldn't read that page</b><span class="small">${err.message}</span></div></div>`);
    }
    busy(cbtn, false);
  });
  el.querySelector('#scrapeUrl')?.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); el.querySelector('#scrapeBtn').click(); } });

  async function save(generate, btn, confirm = false) {
    const d = formData(form);
    d.profile_id = el.querySelector('[name=profile_id]').value;
    if (!d.company || !d.title) { toast('Company and position are required.', 'error'); (d.company ? form.title : form.company).focus(); return; }
    if (generate && !d.description) { toast('Add the job description to tailor documents.', 'error'); form.description.focus(); return; }
    busy(btn, true, 'Saving…');
    try {
      if (editId) {
        await api.put(`/api/applications/${editId}`, d);
        if (generate) await api.post(`/api/applications/${editId}/generate`, {});
        toast(generate ? 'Saved — regenerating documents' : 'Changes saved');
        go(`/applications/${editId}`);
      } else {
        const r = await api.post('/api/applications', { ...d, generate, confirm, job_id: jobId, shared_from: sharedFrom });
        toast(generate ? 'Application saved — tailoring started' : 'Application saved');
        go(`/applications/${r.application.id}`);
      }
    } catch (err) {
      busy(btn, false);
      if (err.status === 409 && err.data?.needs_confirmation) {
        const list = err.data.warnings.filter(w => w.behavior !== 'warn').map(w => `<li><b>${esc(w.label)}</b>${w.matches?.length ? ' — ' + esc(w.matches.join(', ')) : ''}</li>`).join('');
        const ok = await confirmDialog({ title: 'Please confirm', message: `<ul style="padding-left:18px;margin:0 0 8px">${list}</ul>Do you still want to continue?`, confirm: 'Continue anyway' });
        if (ok) save(generate, btn, true);
        return;
      }
      toastError(err);
    }
  }
  el.querySelector('#saveGen').onclick = (e) => { e.preventDefault(); save(true, e.currentTarget); };
  el.querySelector('#saveOnly').onclick = (e) => { e.preventDefault(); save(false, e.currentTarget); };
  const onKey = (e) => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); el.querySelector('#saveGen').click(); } };
  document.addEventListener('keydown', onKey);
  (editId ? form.company : el.querySelector('#scrapeUrl'))?.focus();
  return () => document.removeEventListener('keydown', onKey);
}
