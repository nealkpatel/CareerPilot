import { api } from '../api.js';
import { html, raw, icon, mount, logo, statusBadge, STATUS, timeAgo, fmtDate, busy, toast, toastError, confirmDialog, modal, copyText, esc, emptyState } from '../ui.js';
import { go, state, refreshEngine, engineLabel, connectDeepSeek, siteOrigin } from '../app.js';
const ownerNote = (a) => a.owner_id !== state.user.id ? html`<span class="badge screening">${icon('user')}${a.owner_name}'s application</span>` : '';
import { warningsList } from './application-form.js';

const DOCS = [['resume', 'Résumé', 'file'], ['cover', 'Cover letter', 'message'], ['answers', 'Answers', 'list']];

export default async function ({ el, params, query, isCurrent }) {
  const id = Number(params[0]);
  let a, templates = [], gen = null, tab = query.tab || 'documents', doc = 'resume', tpl = '', selectedGen = null;
  let poll = null;

  async function loadApp() {
    a = (await api.get(`/api/applications/${id}`)).application;
    const done = a.generations.filter(g => g.status === 'completed');
    if (!selectedGen || !a.generations.find(g => g.id === selectedGen)) selectedGen = (done[0] || a.generations[0])?.id || null;
  }
  async function loadGen() {
    gen = selectedGen ? await api.get(`/api/generations/${selectedGen}`) : null;
    tpl = gen?.template_id || tpl;
  }
  [{ templates }] = await Promise.all([api.get('/api/templates'), loadApp()]);
  await loadGen();

  function header() {
    return html`
      <div class="crumb"><a href="#/applications${a.owner_id !== state.user.id ? '?scope=team' : ''}">Applications</a>${icon('chevron')}<span class="truncate">${a.company}</span>${ownerNote(a)}</div>
      <div class="page-head" style="align-items:center">
        <div class="row" style="gap:14px;min-width:0">${logo(a.company, 52, { url: a.url })}<div style="min-width:0"><h1 class="truncate">${a.title}</h1>
          <p class="row wrap" style="gap:12px;margin-top:4px"><b style="color:var(--text)">${a.company}</b>
          ${a.location ? html`<span class="row" style="gap:4px">${icon('map')}${a.location}</span>` : ''}
          ${a.work_arrangement ? html`<span class="tag">${a.work_arrangement}</span>` : ''}${a.salary ? html`<span class="row" style="gap:4px">${icon('dollar')}${a.salary}</span>` : ''}</p></div></div>
        <div class="row wrap">
          <select id="statusSel" style="width:auto;font-weight:650">${Object.entries(STATUS).map(([k, v]) => html`<option value="${k}" ${a.status === k ? 'selected' : ''}>${v.label}</option>`)}</select>
          ${a.url ? html`<a class="btn" href="${a.url}" target="_blank" rel="noopener">${icon('external')}Job post</a>` : ''}
          <a class="btn" href="#/applications/${id}/edit">${icon('edit')}Edit</a>
          <button class="btn ghost icon" id="delBtn" title="Delete application">${icon('trash')}</button>
        </div>
      </div>`;
  }

  function progressView(g) {
    const pct = g.status === 'queued' ? 0 : g.progress;
    const step = g.status === 'queued' ? 0 : pct < 90 ? 1 : 2;
    const eng = state.engine;
    return html`<div class="card"><div class="gen-status">
      <div class="big-ico">${g.status === 'queued' ? icon('clock') : raw('<span class="spinner" style="width:30px;height:30px;border-width:3px"></span>')}</div>
      <div><h2>${g.status === 'queued' ? 'Waiting in the queue' : 'Tailoring your documents'}</h2>
      <p class="muted" style="margin-top:6px">${g.message || ''}${g.queue_position ? ` · position ${g.queue_position}` : ''}</p></div>
      <div class="progress ${g.status === 'queued' ? 'indeterminate' : ''}" style="width:100%;max-width:520px"><div style="width:${Math.max(4, pct)}%"></div></div>
      <div class="steps"><div class="${step > 0 ? 'done' : 'on'}">Queued</div><div class="${step > 1 ? 'done' : step === 1 ? 'on' : ''}">Writing</div><div class="${step === 2 ? 'on' : ''}">Validating</div><div>Ready</div></div>
      ${eng && eng.mode !== 'demo' && ['login_required', 'not_connected'].includes(eng.status) ? html`<div class="alert warn" style="text-align:left;max-width:520px">${icon('alert')}<div class="grow"><b>${eng.status === 'not_connected' ? 'Connect your DeepSeek account' : 'Sign in to DeepSeek again'}</b>Your request is waiting for your own DeepSeek account and continues automatically once you connect.
        <div style="margin-top:8px"><button class="btn sm primary" id="genConnect">${icon('link')}Connect DeepSeek</button></div></div></div>` : ''}
      <p class="muted small">A résumé usually takes 1–3 minutes. You can leave this page — it keeps running.</p>
      <button class="btn" id="cancelGen">${icon('stop')}Cancel</button>
    </div></div>`;
  }

  function documentsView() {
    if (!a.generations.length) {
      return html`<div class="card">${emptyState('sparkles', 'No documents yet', a.description ? 'Tailor a résumé and cover letter to this job with DeepSeek.' : 'Add the job description first, then tailor your documents.',
        a.description ? `<div class="row"><select id="firstTpl" style="width:auto">${templates.map(t => `<option value="${esc(t.id)}">${esc(t.label)}</option>`).join('')}</select><button class="btn primary" id="genBtn">${icon('sparkles')}Tailor documents</button></div>`
          : `<a class="btn primary" href="#/applications/${id}/edit">Add job description</a>`)}</div>`;
    }
    const latest = a.generations[0];
    const running = ['queued', 'generating'].includes(latest.status) ? latest : null;
    const parts = [];
    if (running && (!gen || gen.id === running.id || gen.status !== 'completed')) parts.push(progressView(gen && gen.id === running.id ? gen : running));
    else if (running) parts.push(html`<div class="alert info" style="margin-bottom:14px">${icon('refresh')}<div><b>A new version is being generated</b>${running.message} — ${running.progress}%. You're viewing v${gen.version}.</div></div>`);
    if (gen && gen.status === 'failed' && !running) {
      parts.push(html`<div class="alert error" style="margin-bottom:14px">${icon('alert')}<div class="grow"><b>Generation failed</b>${gen.error || 'DeepSeek did not return a usable résumé.'}
        <div class="row" style="margin-top:10px"><button class="btn sm primary" id="retryBtn">${icon('refresh')}Try again</button><a class="btn sm" href="#/settings?tab=engine">Engine status</a></div></div></div>`);
    }
    if (gen && gen.status === 'completed') {
      const versions = a.generations.filter(g => g.status === 'completed');
      const src = `/api/generations/${gen.id}/doc/${doc}.html?template=${encodeURIComponent(tpl)}`;
      parts.push(html`<div class="card">
        <div class="card-head" style="flex-wrap:wrap">
          <div class="seg" id="docSeg">${DOCS.map(([k, label, ico]) => html`<button data-d="${k}" class="${doc === k ? 'on' : ''}">${icon(ico)}${label}${k === 'answers' && gen.answers?.length ? html` <span class="n">${gen.answers.length}</span>` : ''}</button>`)}</div>
          <div class="row wrap">
            <select id="verSel" style="width:auto;height:34px" title="Version">${versions.map(v => html`<option value="${v.id}" ${v.id === gen.id ? 'selected' : ''}>v${v.version}${v.kind === 'edit' ? ' (edited)' : ''} · ${timeAgo(v.finished_at || v.created_at)}</option>`)}</select>
            ${doc === 'resume' ? html`<select id="tplSel" style="width:auto;height:34px" title="Template">${templates.map(t => html`<option value="${t.id}" ${t.id === tpl ? 'selected' : ''}>${t.label}</option>`)}</select>` : ''}
          </div>
        </div>
        <div class="card-body" style="padding:14px 16px;border-bottom:1px solid var(--border)"><div class="row wrap">
          <div class="btn-group">
            <a class="btn sm" href="/api/generations/${gen.id}/doc/${doc}.pdf?template=${encodeURIComponent(tpl)}&download=1" id="pdfBtn">${icon('download')}PDF</a>
            <a class="btn sm" href="/api/generations/${gen.id}/doc/${doc}.docx?download=1">Word</a>
            <a class="btn sm" href="/api/generations/${gen.id}/doc/${doc}.txt?download=1">Text</a>
          </div>
          <button class="btn sm" id="copyTxt">${icon('copy')}Copy text</button>
          <a class="btn sm ghost" href="${src}" target="_blank" rel="noopener">${icon('external')}Open</a>
          <div class="grow"></div>
          <button class="btn sm" id="editBtn">${icon('edit')}Edit</button>
          ${doc === 'resume' ? html`<button class="btn sm" id="shareBtn">${icon('share')}Share link</button>` : ''}
          <button class="btn sm" id="regenBtn" ${running ? 'disabled' : ''}>${icon('refresh')}Regenerate</button>
        </div>${gen.error ? html`<p class="small" style="color:var(--amber);margin-top:8px">${gen.error}</p>` : ''}</div>
        ${doc === 'answers' && !gen.answers?.length ? html`<div class="card-body">${emptyState('list', 'No answers yet', 'Add application questions in the Answers tab and they will be answered from this résumé.', '<button class="btn primary" data-tab="answers">Write answers</button>')}</div>`
          : html`<div class="doc-frame-wrap" style="border-radius:0 0 var(--radius) var(--radius)"><iframe class="doc-frame" id="docFrame" src="${src}" title="Document preview"></iframe></div>`}
      </div>`);
    }
    return parts;
  }

  function answersView() {
    if (!gen || gen.status !== 'completed') return html`<div class="card">${emptyState('list', 'Generate the résumé first', 'Answers are written from the tailored résumé and cover letter, so they stay consistent.')}</div>`;
    const pending = (gen.answer_requests || []).filter(r => ['queued', 'running'].includes(r.status));
    const failed = (gen.answer_requests || []).filter(r => r.status === 'failed').slice(0, 1);
    return html`<div class="stack">
      <div class="card card-pad stack"><div><h3>Ask application questions</h3><p class="muted small">One per line or numbered. Answers use v${gen.version} of the résumé and cover letter.</p></div>
        <textarea id="qText" rows="4" placeholder="Why are you interested in this role?&#10;Tell us about a challenging project.">${a.questions && !gen.answers?.length && !pending.length ? a.questions : ''}</textarea>
        <div class="row"><button class="btn primary" id="askBtn">${icon('send')}Generate answers</button>${pending.length ? html`<span class="row muted small"><span class="spinner"></span>Writing ${pending.length} batch(es)…</span>` : ''}</div>
        ${failed.length ? html`<div class="alert error">${icon('alert')}<div><b>Last request failed</b>${failed[0].error}</div></div>` : ''}</div>
      ${(gen.answers || []).map((qa, i) => html`<div class="qa card"><div class="row between top"><div class="q">${i + 1}. ${qa.q}</div><button class="btn sm ghost" data-copy="${i}">${icon('copy')}Copy</button></div><div class="a">${qa.a}</div></div>`)}
    </div>`;
  }

  function detailsView() {
    return html`<div class="card"><div class="card-head"><h3>Job description</h3><button class="btn sm" id="copyJd">${icon('copy')}Copy</button></div>
      <div class="card-body"><div class="desc-text">${a.description || 'No description saved.'}</div></div></div>
      ${a.notes ? html`<div class="card" style="margin-top:16px"><div class="card-head"><h3>Notes</h3></div><div class="card-body"><div class="desc-text">${a.notes}</div></div></div>` : ''}`;
  }

  function timelineView() {
    return html`<div class="card card-pad"><div class="timeline">${a.history.map(h => html`<div class="tl-item"><div class="tl-dot" style="background:${STATUS[h.status]?.color || 'var(--brand)'}"></div>
      <div><div class="row">${statusBadge(h.status)}<span class="muted small">${fmtDate(h.created_at)}</span></div>${h.note ? html`<p class="small" style="margin-top:4px;color:var(--text-2)">${h.note}</p>` : ''}</div></div>`)}</div>
      <h3 style="margin:18px 0 10px">Document versions</h3>
      <div class="stack tight">${a.generations.map(g => html`<div class="row small"><span class="badge ${g.status}">v${g.version}</span><span>${g.kind === 'edit' ? 'Manual edit' : 'AI generation'} · ${g.status}</span><span class="muted">${fmtDate(g.created_at)}</span></div>`)}</div></div>`;
  }

  function side() {
    return html`<div class="stack">
      <div class="card card-pad stack"><h3>Overview</h3><dl class="kv">
        <dt>Status</dt><dd>${statusBadge(a.status)}</dd>${a.owner_id !== state.user.id ? html`<dt>Bidder</dt><dd>${a.owner_name}</dd>` : ''}<dt>Profile</dt><dd>${a.profile_label || '—'}</dd>
        <dt>Source</dt><dd>${{ manual: 'Manual entry', link: 'Job link', job_pool: 'Job pool' }[a.source] || a.source}</dd>
        <dt>Added</dt><dd>${fmtDate(a.created_at)}</dd>${a.applied_at ? html`<dt>Applied</dt><dd>${fmtDate(a.applied_at)}</dd>` : ''}
        <dt>Versions</dt><dd>${a.generations.length}</dd></dl>
        ${a.status === 'ready' ? html`<button class="btn primary" id="markApplied">${icon('send')}I've applied</button>` : ''}</div>
      <div class="card card-pad stack"><h3>Things to know</h3>${warningsList(a.warnings)}</div>
      <div class="card card-pad stack"><h3>Your DeepSeek</h3><div class="row"><span class="pulse-dot ${state.engine?.mode === 'demo' ? 'ready' : state.engine?.status}"></span><span class="small">${engineLabel(state.engine)}</span></div>
        <p class="muted small">${state.engine?.message || ''}</p></div>
    </div>`;
  }

  function draw() {
    if (!isCurrent()) return;
    const tabs = [['documents', 'Documents'], ['answers', 'Answers', gen?.answers?.length], ['details', 'Job description'], ['timeline', 'Timeline']];
    const body = { documents: documentsView, answers: answersView, details: detailsView, timeline: timelineView }[tab]();
    mount(el, html`${header()}<div class="detail-grid"><div style="min-width:0">
      <div class="tabs">${tabs.map(([k, label, n]) => html`<button data-tab="${k}" class="${tab === k ? 'on' : ''}">${label}${n ? html`<span class="n">${n}</span>` : ''}</button>`)}</div>
      ${body}</div>${side()}</div>`);
    bind();
  }

  async function refresh(full = true) {
    if (full) await loadApp();
    const latest = a.generations[0];
    if (latest && ['queued', 'generating'].includes(latest.status) && (!gen || gen.status !== 'completed' || gen.id === latest.id)) selectedGen = latest.id;
    await loadGen();
    draw();
    schedule();
  }

  function schedule() {
    clearTimeout(poll);
    const latest = a.generations[0];
    const genRunning = latest && ['queued', 'generating'].includes(latest.status);
    const ansRunning = gen?.answer_requests?.some(r => ['queued', 'running'].includes(r.status));
    if (genRunning || ansRunning) poll = setTimeout(async () => {
      if (!isCurrent()) return;
      try {
        if (genRunning && gen?.id === latest.id) {
          const g = await api.get(`/api/generations/${latest.id}`);
          const finished = g.status !== gen.status && !['queued', 'generating'].includes(g.status);
          gen = g;
          if (finished) { if (g.status === 'completed') toast('Your tailored documents are ready'); return refresh(); }
          if (tab === 'documents') draw();
          schedule();
        } else refresh();
      } catch { schedule(); }
    }, 2000);
  }

  async function startGen(btn, template) {
    busy(btn, true, 'Starting…');
    try {
      await api.post(`/api/applications/${id}/generate`, template ? { template_id: template } : {});
      refreshEngine();
      selectedGen = null;
      tab = 'documents';
      await refresh();
    } catch (e) { toastError(e); busy(btn, false); }
  }

  function bind() {
    el.querySelectorAll('[data-tab]').forEach(b => b.onclick = () => { tab = b.dataset.tab; draw(); });
    el.querySelector('#statusSel').onchange = async (e) => {
      try { a = (await api.post(`/api/applications/${id}/status`, { status: e.target.value })).application; toast(`Marked as ${STATUS[a.status].label}`); draw(); }
      catch (err) { toastError(err); }
    };
    el.querySelector('#markApplied')?.addEventListener('click', async () => {
      a = (await api.post(`/api/applications/${id}/status`, { status: 'applied' })).application; toast('Marked as applied — good luck!'); draw();
    });
    el.querySelector('#delBtn').onclick = async () => {
      if (await confirmDialog({ title: 'Delete application?', message: `This permanently removes <b>${esc(a.company)} — ${esc(a.title)}</b>, its document versions, answers and share links.`, confirm: 'Delete', danger: true })) {
        await api.del(`/api/applications/${id}`); toast('Application deleted'); go('/applications');
      }
    };
    el.querySelector('#genBtn')?.addEventListener('click', (e) => startGen(e.currentTarget, el.querySelector('#firstTpl')?.value));
    el.querySelector('#retryBtn')?.addEventListener('click', (e) => startGen(e.currentTarget));
    el.querySelector('#cancelGen')?.addEventListener('click', async () => {
      const latest = a.generations[0];
      await api.post(`/api/generations/${latest.id}/cancel`); toast('Generation cancelled'); selectedGen = null; refresh();
    });
    el.querySelectorAll('#docSeg button').forEach(b => b.onclick = () => { doc = b.dataset.d; draw(); });
    el.querySelector('#verSel')?.addEventListener('change', async (e) => { selectedGen = Number(e.target.value); await loadGen(); draw(); });
    el.querySelector('#tplSel')?.addEventListener('change', async (e) => {
      tpl = e.target.value;
      api.put(`/api/generations/${gen.id}/template`, { template_id: tpl }).catch(() => {});
      gen.template_id = tpl;
      draw();
    });
    el.querySelector('#pdfBtn')?.addEventListener('click', (e) => { const b = e.currentTarget; b.style.opacity = '.6'; setTimeout(() => b.style.opacity = '', 2500); toast('Preparing PDF…'); });
    el.querySelector('#copyTxt')?.addEventListener('click', async () => {
      const t = await fetch(`/api/generations/${gen.id}/doc/${doc}.txt`).then(r => r.text()); copyText(t);
    });
    el.querySelector('#regenBtn')?.addEventListener('click', async (e) => {
      if (await confirmDialog({ title: 'Regenerate documents?', message: 'DeepSeek writes a new version. Your current versions stay available in the version picker.', confirm: 'Regenerate' })) startGen(e.currentTarget, tpl);
    });
    el.querySelector('#editBtn')?.addEventListener('click', openEditor);
    el.querySelector('#shareBtn')?.addEventListener('click', openShare);
    el.querySelector('#copyJd')?.addEventListener('click', () => copyText(a.description));
    el.querySelector('#askBtn')?.addEventListener('click', async (e) => {
      const q = el.querySelector('#qText').value.trim();
      if (!q) return toast('Type at least one question.', 'error');
      const cbtn = e.currentTarget; busy(cbtn, true, 'Queuing…');
      try { await api.post(`/api/generations/${gen.id}/answers`, { questions: q }); refreshEngine(); toast('Writing answers…'); await refresh(false); }
      catch (err) { toastError(err); busy(cbtn, false); }
    });
    el.querySelectorAll('[data-copy]').forEach(b => b.onclick = () => copyText(gen.answers[Number(b.dataset.copy)].a));
  }

  // ---------------------------------------------------------- editor
  function openEditor() {
    const r = JSON.parse(JSON.stringify(gen.resume));
    const jobs = gen.profile?.jobs || [];
    let letter = gen.cover_letter || '';
    let answers = JSON.parse(JSON.stringify(gen.answers || []));
    const body = () => html`<div class="stack">
      <div class="alert info">${icon('info')}<div>Edits are saved as a new version without calling DeepSeek. Name, contact details, employers and dates come from the profile.</div></div>
      <div class="tabs" id="edTabs"><button class="on" data-e="resume">Résumé</button><button data-e="cover">Cover letter</button><button data-e="answers">Answers</button></div>
      <div data-pane="resume" class="stack">
        <label class="field">Professional summary <span class="hint">${r.summary.length} characters</span><textarea data-k="summary" rows="5">${r.summary}</textarea></label>
        <div class="row between"><h3>Skills</h3><button class="btn sm" data-add-skill>${icon('plus')}Category</button></div>
        <div class="stack tight" id="skillRows">${r.skills.map((s, i) => { const [k, v] = Object.entries(s)[0]; return html`<div class="row top" data-skill="${i}">
          <input value="${k}" data-sk="cat" style="max-width:220px"><textarea data-sk="items" rows="2" style="min-height:42px">${v}</textarea><button class="btn sm ghost icon" data-rm-skill="${i}">${icon('x')}</button></div>`; })}</div>
        <h3>Experience</h3>
        ${r.experiences.map((x, i) => html`<div class="repeat-item stack tight" data-exp="${i}">
          <div class="drag-head">${icon('briefcase')}${jobs[i]?.company || 'Experience ' + (i + 1)}<span class="muted small">${jobs[i]?.dates || ''}</span></div>
          <label class="field">Role title<input data-role="${i}" value="${x.role || jobs[i]?.role || ''}"></label>
          ${x.bullets.map((b, j) => html`<div class="bullet-edit"><span class="muted" style="padding-top:10px">•</span><textarea data-b="${i}:${j}" rows="2">${b}</textarea><button class="btn sm ghost icon" data-rm-b="${i}:${j}" title="Remove">${icon('x')}</button></div>`)}
          <button class="btn sm ghost" data-add-b="${i}" style="align-self:flex-start">${icon('plus')}Add bullet</button></div>`)}
      </div>
      <div data-pane="cover" class="hidden"><textarea data-k="letter" rows="22">${letter}</textarea></div>
      <div data-pane="answers" class="hidden stack">${answers.length ? answers.map((qa, i) => html`<div class="repeat-item stack tight"><input data-aq="${i}" value="${qa.q}"><textarea data-aa="${i}" rows="5">${qa.a}</textarea></div>`) : html`<p class="muted">No answers on this version.</p>`}</div>
    </div>`;
    const m = modal({ title: `Edit documents · v${gen.version}`, size: 'wide', body: String(body()),
      foot: `<button class="btn" data-close>Cancel</button><button class="btn primary" data-save>${icon('check')}Save as new version</button>` });
    const collect = () => {
      r.summary = m.$('[data-k=summary]').value;
      r.skills = m.$$('[data-skill]').map(row => ({ [row.querySelector('[data-sk=cat]').value.trim() || 'Skills']: row.querySelector('[data-sk=items]').value.trim() })).filter(s => Object.values(s)[0]);
      r.experiences.forEach((x, i) => { x.role = m.$(`[data-role="${i}"]`).value; x.bullets = m.$$(`[data-b^="${i}:"]`).map(t => t.value.trim()).filter(Boolean); });
      letter = m.$('[data-k=letter]').value;
      answers = answers.map((qa, i) => ({ q: m.$(`[data-aq="${i}"]`)?.value ?? qa.q, a: m.$(`[data-aa="${i}"]`)?.value ?? qa.a }));
    };
    const rerender = () => { const pane = m.$('#edTabs .on')?.dataset.e; m.$('.modal-body').innerHTML = String(body()); wire(); if (pane) m.$(`#edTabs [data-e=${pane}]`).click(); };
    const wire = () => {
      m.$$('#edTabs button').forEach(b => b.onclick = () => { m.$$('#edTabs button').forEach(x => x.classList.toggle('on', x === b)); m.$$('[data-pane]').forEach(p => p.classList.toggle('hidden', p.dataset.pane !== b.dataset.e)); });
      m.$$('[data-add-b]').forEach(b => b.onclick = () => { collect(); r.experiences[b.dataset.addB].bullets.push(''); rerender(); });
      m.$$('[data-rm-b]').forEach(b => b.onclick = () => { collect(); const [i, j] = b.dataset.rmB.split(':').map(Number); r.experiences[i].bullets.splice(j, 1); rerender(); });
      m.$('[data-add-skill]').onclick = () => { collect(); r.skills.push({ 'New category': '' }); rerender(); };
      m.$$('[data-rm-skill]').forEach(b => b.onclick = () => { collect(); r.skills.splice(Number(b.dataset.rmSkill), 1); rerender(); });
    };
    wire();
    m.$('[data-save]').onclick = async (e) => {
      collect();
      const cbtn = e.currentTarget; busy(cbtn, true, 'Saving…');
      try {
        const res = await api.put(`/api/generations/${gen.id}`, { resume: r, cover_letter: letter, answers, template_id: tpl });
        m.close(); toast(`Saved as v${res.generation.version}`); selectedGen = res.generation.id; refresh();
      } catch (err) { toastError(err); busy(cbtn, false); }
    };
  }

  async function openShare() {
    try {
      const s = await api.post(`/api/generations/${gen.id}/share`);
      const url = siteOrigin() + s.path;
      const m = modal({ title: 'Share your résumé', body: String(html`<div class="stack">
        <p class="muted">Anyone with this link can view the résumé (not the cover letter or your application data). It expires on <b>${fmtDate(s.expires_at)}</b>.</p>
        <div class="row"><input readonly value="${url}" id="shareUrl"><button class="btn primary" id="copyShare">${icon('copy')}Copy</button></div>
        <p class="muted small">Manage or revoke links in Settings → Shared links. Note: the link only works where this CareerPilot server is reachable.</p></div>`),
        foot: `<a class="btn" href="${esc(url)}" target="_blank" rel="noopener">${icon('external')}Open</a><button class="btn" data-close>Done</button>` });
      m.$('#copyShare').onclick = () => copyText(url, 'Link copied');
      m.$('#shareUrl').select();
    } catch (e) { toastError(e); }
  }

  const onEngine = () => { if (tab === 'documents' && a.generations[0] && ['queued', 'generating'].includes(a.generations[0].status)) draw(); };
  window.addEventListener('cp:engine', onEngine);
  el.addEventListener('click', (e) => { if (e.target.closest('#genConnect')) connectDeepSeek(); });
  draw();
  schedule();
  return () => { clearTimeout(poll); window.removeEventListener('cp:engine', onEngine); };
}
