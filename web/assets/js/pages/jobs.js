import { api } from '../api.js';
import { html, icon, mount, logo, timeAgo, emptyState, debounce, toast, toastError, modal, busy } from '../ui.js';
import { go, state } from '../app.js';
import { warningsList } from './application-form.js';

const POSTED = [[0, 'Any time'], [24, 'Last 24 hours'], [72, 'Last 3 days'], [168, 'Last 7 days'], [336, 'Last 14 days'], [720, 'Last 30 days']];
const MARKS = {
  boards: [['not_applied', 'Not applied'], ['saved', 'Saved'], ['applied', 'Applied'], ['all', 'All jobs']],
  shared: [['not_applied', 'Not applied'], ['applied', 'Applied by me'], ['all', 'All shared']],
};

export default async function ({ el }) {
  const canApply = state.user.role !== 'master';
  const f = { q: '', hours: 0, remote: 0, provider: '', mark: 'not_applied', view: 'boards' };
  try { Object.assign(f, JSON.parse(localStorage.getItem('cp-jobs-filters') || '{}')); } catch {}
  delete f.apply; // the apply-destination filter belonged to the removed auto apply
  if (!MARKS[f.view]) f.view = 'boards';
  if (!MARKS[f.view].some(([k]) => k === f.mark)) f.mark = 'not_applied';
  let offset = 0, jobs = [], total = 0, meta = null;

  mount(el, html`
    <div class="page-head"><div><h1>Job Pool</h1><p>Fresh openings from free public job boards, plus links shared by other CareerPilot users.</p></div>
      ${state.user.role === 'master' ? html`<div class="row"><button class="btn" id="importBtn">${icon('refresh')}Import now</button><a class="btn ghost" href="#/admin/sources">${icon('database')}Sources</a></div>` : ''}</div>
    <div class="tabs" id="viewTabs">
      <button data-view="boards">${icon('globe')}Job boards <span class="n" id="nBoards"></span></button>
      <button data-view="shared">${icon('share')}Shared by users <span class="n" id="nShared"></span></button>
    </div>
    <div class="filters">
      <div class="input-icon grow" style="min-width:240px;max-width:420px">${icon('search')}<input id="q" placeholder="Title, company, skill… e.g. python remote" value="${f.q}"></div>
      <select id="hours">${POSTED.map(([v, l]) => html`<option value="${v}" ${f.hours == v ? 'selected' : ''}>${l}</option>`)}</select>
      <select id="provider"><option value="">All sources</option></select>
      <label class="chip ${f.remote ? 'on' : ''}" id="remoteChip"><input type="checkbox" class="hidden" ${f.remote ? 'checked' : ''}>${icon('globe')}Remote only</label>
    </div>
    <div id="sharedNote"></div>
    <div class="row between wrap" style="margin-bottom:14px">
      <div class="seg" id="markSeg"></div>
      <span class="muted small" id="meta"></span>
    </div>
    <div class="stack tight" id="jobList"></div>
    <div class="row" style="justify-content:center;margin-top:16px"><button class="btn hidden" id="moreBtn">Load more</button></div>`);

  const listEl = el.querySelector('#jobList');
  const save = () => { try { localStorage.setItem('cp-jobs-filters', JSON.stringify(f)); } catch {} };

  function drawChrome() {
    el.querySelectorAll('#viewTabs button').forEach(b => b.classList.toggle('on', b.dataset.view === f.view));
    el.querySelector('#provider').classList.toggle('hidden', f.view !== 'boards');
    mount(el.querySelector('#markSeg'), html`${MARKS[f.view].map(([k, l]) => html`<button data-m="${k}" class="${f.mark === k ? 'on' : ''}">${l}</button>`)}`);
    el.querySelectorAll('#markSeg button').forEach(b => b.onclick = () => { f.mark = b.dataset.m; save(); drawChrome(); load(); });
    mount(el.querySelector('#sharedNote'), f.view === 'shared' ? html`<div class="alert info" style="margin-bottom:14px">${icon('share')}<div>
      Job links that CareerPilot users added to their applications and that aren't already on the job boards. Only the public posting is shared — never who added it, their profile or documents.${canApply ? ' Links you already applied to are marked, and their Apply button is disabled.' : ''}</div></div>` : '');
  }

  // ---------------------------------------------------------- job boards
  function boardCard(j) {
    return html`<div class="card job-card" data-id="${j.id}">
      ${logo(j.company, 44, { jobId: j.id })}
      <div class="grow" style="min-width:0">
        <div class="row between top"><div style="min-width:0"><div class="title truncate">${j.title}</div>
          <div class="meta"><b style="color:var(--text-2)">${j.company}</b>${j.location ? html`<span>${icon('map')}${j.location}</span>` : ''}
          ${j.salary ? html`<span>${icon('dollar')}${j.salary}</span>` : ''}<span>${icon('clock')}${j.posted_at ? timeAgo(j.posted_at) : 'Posting time unavailable'}</span></div></div>
          <div class="row" style="gap:6px">${j.remote ? html`<span class="badge ready">Remote</span>` : ''}${j.mark === 'applied' ? html`<span class="badge applied">Applied</span>` : ''}
            ${canApply ? html`<button class="btn sm ghost icon" data-save="${j.id}" title="${j.mark === 'saved' ? 'Unsave' : 'Save'}" style="${j.mark === 'saved' ? 'color:var(--brand)' : ''}">${icon('bookmark')}</button>` : ''}</div></div>
        <div class="snip">${j.snippet}</div>
        ${j.tags ? html`<div class="row wrap" style="gap:6px;margin-top:8px">${j.tags.split(',').slice(0, 5).filter(t => t.trim()).map(t => html`<span class="tag">${t.trim()}</span>`)}</div>` : ''}
      </div></div>`;
  }

  // ---------------------------------------------------------- shared by users
  function applyButton(j, size = 'sm') {
    if (!canApply) return '';
    return j.applied_by_me
      ? html`<button class="btn ${size}" disabled title="You already have an application for this link">${icon('check')}Applied</button>`
      : html`<button class="btn ${size} primary" data-apply="${j.id}">${icon('sparkles')}Apply</button>`;
  }
  function sharedCard(j) {
    const remote = j.work_arrangement === 'Remote' || /remote/i.test(j.location || '');
    return html`<div class="card job-card ${j.applied_by_me ? 'mine' : ''}" data-shared="${j.id}">
      ${logo(j.company, 44, { url: j.url })}
      <div class="grow" style="min-width:0">
        <div class="row between top"><div style="min-width:0"><div class="title truncate">${j.title}</div>
          <div class="meta"><b style="color:var(--text-2)">${j.company}</b>${j.location ? html`<span>${icon('map')}${j.location}</span>` : ''}
          ${j.salary ? html`<span>${icon('dollar')}${j.salary}</span>` : ''}<span>${icon('clock')}Shared ${timeAgo(j.first_added)}</span>
          <span>${icon('users')}${j.applicants} applicant${j.applicants === 1 ? '' : 's'}</span></div></div>
          <div class="row" style="gap:6px;flex:none">${remote ? html`<span class="badge ready">Remote</span>` : ''}${j.work_arrangement && !remote ? html`<span class="tag">${j.work_arrangement}</span>` : ''}
            <a class="btn sm ghost icon" href="${j.url}" target="_blank" rel="noopener" title="Open job posting" data-stop>${icon('external')}</a>${applyButton(j)}</div></div>
        <div class="snip">${j.snippet || 'No description saved.'}</div>
      </div></div>`;
  }

  async function load(reset = true) {
    if (reset) { offset = 0; jobs = []; mount(listEl, html`<div class="row" style="justify-content:center;padding:40px"><span class="spinner"></span></div>`); }
    const shared = f.view === 'shared';
    try {
      const p = new URLSearchParams({ q: f.q, hours: f.hours, remote: f.remote, mark: f.mark, offset, limit: 30, ...(shared ? {} : { provider: f.provider }) });
      meta = await api.get(`/api/jobs${shared ? '/shared' : ''}?` + p);
    } catch (e) { toastError(e); return; }
    jobs = jobs.concat(meta.jobs);
    total = meta.total;
    offset = jobs.length;
    if (shared) {
      el.querySelector('#nShared').textContent = meta.all_shared || '';
      el.querySelector('#meta').textContent = `${total.toLocaleString()} shared job${total === 1 ? '' : 's'}`;
    } else {
      const sel = el.querySelector('#provider');
      if (sel.options.length === 1) meta.providers.forEach(p => sel.add(new Option(`${p.name} (${p.count})`, p.id, false, p.id === f.provider)));
      el.querySelector('#nBoards').textContent = meta.providers.reduce((a, p) => a + p.count, 0).toLocaleString() || '';
      el.querySelector('#meta').textContent = `${total.toLocaleString()} job${total === 1 ? '' : 's'}${meta.last_import ? ' · updated ' + timeAgo(meta.last_import) : ''}${meta.importing ? ' · importing…' : ''}`;
    }

    if (!jobs.length) {
      if (shared) {
        mount(listEl, html`<div class="card">${meta.all_shared ? emptyState('search', 'No shared jobs match', f.mark === 'not_applied' ? 'You may have applied to all of them already — try "All shared".' : 'Try widening the time window or clearing the search.')
          : emptyState('share', 'Nothing shared yet', 'When anyone adds an application with a job link that isn\'t on the job boards, it appears here for everyone else.')}</div>`);
      } else {
        const none = !meta.providers.length;
        mount(listEl, html`<div class="card">${none ? emptyState('globe', 'The job pool is empty', state.user.role === 'master' ? 'Import jobs from the free public sources configured under Job sources.' : 'The platform master needs to run the first import.', state.user.role === 'master' ? '<button class="btn primary" id="firstImport">Import jobs now</button>' : '')
          : emptyState('search', 'No jobs match', 'Try widening the posting window or clearing the search.')}</div>`);
        el.querySelector('#firstImport')?.addEventListener('click', runImport);
      }
    } else {
      mount(listEl, html`${jobs.map(j => shared ? sharedCard(j) : boardCard(j))}`);
    }
    el.querySelector('#moreBtn').classList.toggle('hidden', jobs.length >= total);
    bind();
  }

  function bind() {
    listEl.querySelectorAll('[data-id]').forEach(c => c.onclick = (e) => { if (!e.target.closest('[data-save]')) openJob(Number(c.dataset.id)); });
    listEl.querySelectorAll('[data-shared]').forEach(c => c.onclick = (e) => { if (!e.target.closest('button, a, [data-stop]')) openShared(Number(c.dataset.shared)); });
    listEl.querySelectorAll('[data-apply]').forEach(b => b.onclick = () => go(`/applications/new?shared=${b.dataset.apply}`));
    listEl.querySelectorAll('[data-save]').forEach(b => b.onclick = async () => {
      const j = jobs.find(x => x.id === Number(b.dataset.save));
      const mark = j.mark === 'saved' ? null : 'saved';
      await api.post(`/api/jobs/${j.id}/mark`, { mark }); j.mark = mark; toast(mark ? 'Saved for later' : 'Removed from saved');
      b.style.color = mark ? 'var(--brand)' : '';
    });
  }

  async function openJob(id) {
    const { job } = await api.get(`/api/jobs/${id}`);
    const m = modal({ drawer: true, title: job.company, body: String(html`<div class="stack">
      <div class="row" style="gap:14px">${logo(job.company, 52, { jobId: job.id })}<div><h2>${job.title}</h2><p class="muted">${job.company}${job.location ? ' · ' + job.location : ''}</p></div></div>
      <div class="row wrap">${job.remote ? html`<span class="badge ready">Remote</span>` : ''}${job.salary ? html`<span class="tag">${job.salary}</span>` : ''}
        <span class="tag">Posted ${job.posted_at ? timeAgo(job.posted_at) : 'date unknown'}</span><span class="tag">via ${job.provider}</span></div>
      <div class="row wrap"><button class="btn primary" id="tailor">${icon('sparkles')}Tailor résumé for this job</button>
        <a class="btn" href="${job.url}" target="_blank" rel="noopener">${icon('external')}Original posting</a>
        <button class="btn ghost" id="markApplied">${icon('check')}Mark applied</button><button class="btn ghost" id="hideJob">${icon('eyeOff')}Hide</button></div>
      ${warningsList(job.warnings)}
      <div class="desc-text" style="max-height:none">${job.description || 'No description provided by the source.'}</div></div>`) });
    m.$('#tailor').onclick = () => { m.close(); go(`/applications/new?job=${job.id}`); };
    m.$('#markApplied').onclick = async () => { await api.post(`/api/jobs/${job.id}/mark`, { mark: 'applied' }); toast('Marked as applied'); m.close(); load(); };
    m.$('#hideJob').onclick = async () => { await api.post(`/api/jobs/${job.id}/mark`, { mark: 'hidden' }); toast('Job hidden'); m.close(); load(); };
    if (!canApply) ['#tailor', '#markApplied', '#hideJob'].forEach(sel => m.$(sel)?.remove());
  }

  async function openShared(id) {
    let job;
    try { ({ job } = await api.get(`/api/jobs/shared/${id}`)); } catch (e) { toastError(e); return load(); }
    const m = modal({ drawer: true, title: job.company, body: String(html`<div class="stack">
      <div class="row" style="gap:14px">${logo(job.company, 52, { url: job.url })}<div><h2>${job.title}</h2><p class="muted">${job.company}${job.location ? ' · ' + job.location : ''}</p></div></div>
      <div class="row wrap">${job.work_arrangement ? html`<span class="badge ready">${job.work_arrangement}</span>` : ''}${job.salary ? html`<span class="tag">${job.salary}</span>` : ''}
        <span class="tag">Shared ${timeAgo(job.first_added)}</span><span class="tag">${job.applicants} applicant${job.applicants === 1 ? '' : 's'} on CareerPilot</span></div>
      ${job.applied_by_me ? html`<div class="alert ok">${icon('checkCircle')}<div><b>You already applied to this job</b>You have an application with this link, so applying again is disabled.</div></div>` : ''}
      <div class="row wrap">${canApply ? (job.applied_by_me
          ? html`<button class="btn primary" disabled>${icon('check')}Already applied</button>`
          : html`<button class="btn primary" id="tailor">${icon('sparkles')}Tailor résumé for this job</button>`) : ''}
        <a class="btn" href="${job.url}" target="_blank" rel="noopener">${icon('external')}Original posting</a></div>
      ${warningsList(job.warnings.filter(w => w.id !== 'duplicate' || !job.applied_by_me))}
      <div class="desc-text" style="max-height:none">${job.description || 'No description was saved with this link.'}</div></div>`) });
    m.$('#tailor')?.addEventListener('click', () => { m.close(); go(`/applications/new?shared=${job.id}`); });
  }

  async function runImport(e) {
    const btn = e?.currentTarget;
    busy(btn, true, 'Importing…');
    try {
      await api.post('/api/job-sources/import');
      toast('Import started — new jobs appear as sources finish');
      const wait = async (n = 0) => { const s = await api.get('/api/jobs?limit=1'); if (s.importing && n < 60) return setTimeout(() => wait(n + 1), 3000); busy(btn, false); el.querySelector('#provider').length = 1; load(); toast('Job pool updated'); };
      setTimeout(wait, 2500);
    } catch (err) { toastError(err); busy(btn, false); }
  }

  el.querySelectorAll('#viewTabs button').forEach(b => b.onclick = () => { if (f.view === b.dataset.view) return; f.view = b.dataset.view; f.mark = 'not_applied'; save(); drawChrome(); load(); });
  el.querySelector('#q').oninput = debounce((e) => { f.q = e.target.value.trim(); save(); load(); }, 300);
  el.querySelector('#hours').onchange = (e) => { f.hours = Number(e.target.value); save(); load(); };
  el.querySelector('#provider').onchange = (e) => { f.provider = e.target.value; save(); load(); };
  el.querySelector('#remoteChip').onclick = (e) => { e.preventDefault(); f.remote = f.remote ? 0 : 1; e.currentTarget.classList.toggle('on', !!f.remote); save(); load(); };
  el.querySelector('#moreBtn').onclick = () => load(false);
  el.querySelector('#importBtn')?.addEventListener('click', runImport);
  drawChrome();
  // fill the other tab's count too
  api.get(f.view === 'shared' ? '/api/jobs?limit=1' : '/api/jobs/shared?limit=1&mark=all').then(r => {
    if (f.view === 'shared') el.querySelector('#nBoards').textContent = r.providers.reduce((a, p) => a + p.count, 0).toLocaleString() || '';
    else el.querySelector('#nShared').textContent = r.all_shared || '';
  }).catch(() => {});
  await load();
}
