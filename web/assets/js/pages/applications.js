import { api } from '../api.js';
import { html, raw, icon, mount, logo, statusBadge, genBadge, timeAgo, STATUS, emptyState, debounce, toast, toastError, modal, confirmDialog, copyText, esc, busy } from '../ui.js';
import { go, state } from '../app.js';
import { openAnswersChat } from '../answers-chat.js';
import { rangePicker, niceDate as nice } from '../datepicker.js';

export default async function ({ el, query }) {
  const isAdmin = state.user.role === 'admin';
  let status = query.status || '', q = query.q || '', profileId = query.profile || '';
  let scope = isAdmin && ['team', 'all'].includes(query.scope) ? query.scope : 'mine';
  let ownerId = query.owner || '';
  let [dateFrom, dateTo] = ['', ''];
  try { [dateFrom, dateTo] = JSON.parse(localStorage.getItem('cp-apps-dates') || '["",""]'); } catch {}
  let view = 'table';
  try { view = localStorage.getItem('cp-apps-view') || 'table'; } catch {}
  const selected = new Set();
  let apps = [], timer = null;
  const { profiles } = await api.get('/api/profiles');

  mount(el, html`
    <div class="page-head"><div><h1>Applications</h1><p>Manage and track every application, with its tailored documents.</p></div>
      <a class="btn primary" href="#/applications/new">${icon('plus')}New application</a></div>
    ${isAdmin ? html`<div class="tabs" id="scopeTabs"><button data-sc="mine">${icon('user')}My applications</button><button data-sc="team">${icon('users')}Bidder applications</button><button data-sc="all">${icon('briefcase')}All applications</button></div>` : ''}
    <div class="filters">
      <div class="input-icon grow" style="min-width:220px;max-width:360px">${icon('search')}<input id="q" placeholder="Search company, role, description…" value="${q}"></div>
      <div style="position:relative"><button class="btn" id="dateBtn">${icon('calendar')}<span id="dateLabel"></span>${icon('chevronDown')}</button><div class="dp-pop hidden" id="datePop" role="dialog" aria-label="Choose a date range"></div></div>
      <select id="profile"><option value="">All profiles</option>${profiles.map(p => html`<option value="${p.id}" ${String(p.id) === profileId ? 'selected' : ''}>${p.label}</option>`)}</select>
      ${isAdmin ? html`<select id="owner" style="width:auto;min-width:170px"></select>` : ''}
      <div class="grow"></div>
      <div class="seg" id="viewSeg"><button data-v="table" title="Table">${icon('list')}</button><button data-v="board" title="Board">${icon('layout')}</button></div>
    </div>
    <div class="seg" id="statusSeg" style="margin-bottom:14px"></div>
    <div id="bulkBar"></div>
    <div id="list"></div>`);

  const listEl = el.querySelector('#list');

  // ---------------------------------------------------------- date range control
  function drawDate() {
    el.querySelector('#dateLabel').textContent = dateFrom || dateTo ? `${dateFrom ? nice(dateFrom) : '…'} – ${dateTo ? nice(dateTo) : '…'}` : 'All dates';
    el.querySelector('#dateBtn').classList.toggle('active', !!(dateFrom || dateTo));
  }
  const pop = el.querySelector('#datePop');
  rangePicker(pop, {
    from: dateFrom, to: dateTo,
    onChange: (f, t) => {
      dateFrom = f; dateTo = t;
      try { localStorage.setItem('cp-apps-dates', JSON.stringify([f, t])); } catch {}
      drawDate(); load();
      setTimeout(() => pop.classList.add('hidden'), 250);
    },
    onClose: () => pop.classList.add('hidden'),
  });
  el.querySelector('#dateBtn').onclick = (e) => { e.stopPropagation(); el.querySelector('#datePop').classList.toggle('hidden'); };
  el.querySelector('#datePop').addEventListener('click', (e) => e.stopPropagation());
  const closePop = () => el.querySelector('#datePop')?.classList.add('hidden');
  document.addEventListener('click', closePop);
  drawDate();

  // ---------------------------------------------------------- bulk bar
  function drawBulk() {
    const bar = el.querySelector('#bulkBar');
    if (!selected.size) { mount(bar, ''); return; }
    mount(bar, html`<div class="card row wrap" style="padding:10px 14px;margin-bottom:12px;border-color:var(--brand-100);background:var(--brand-50)">
      <b>${selected.size} selected</b>
      <select id="bulkStatus" style="width:auto;height:34px"><option value="">Set status…</option>${Object.entries(STATUS).map(([k, v]) => html`<option value="${k}">${v.label}</option>`)}</select>
      <button class="btn sm" id="bulkGen">${icon('sparkles')}Generate documents</button>
      <button class="btn sm" id="bulkPdf">${icon('download')}Download PDFs</button>
      <button class="btn sm" id="bulkDocx">${icon('download')}Download Word</button>
      <div class="grow"></div>
      <button class="btn sm danger" id="bulkDel">${icon('trash')}Delete</button>
      <button class="btn sm ghost" id="bulkClear">Clear</button></div>`);
    const ids = () => [...selected];
    const run = async (body, msg) => {
      try {
        const r = await api.post('/api/applications/bulk', { ids: ids(), ...body });
        toast(`${msg}: ${r.done} done${r.skipped.length ? `, ${r.skipped.length} skipped` : ''}`, r.skipped.length && !r.done ? 'error' : 'ok');
        if (r.skipped.length) console.info('Skipped:', r.skipped);
        if (body.action === 'delete') selected.clear();
        load();
      } catch (e) { toastError(e); }
    };
    bar.querySelector('#bulkStatus').onchange = (e) => e.target.value && run({ action: 'status', status: e.target.value }, `Marked as ${STATUS[e.target.value].label}`);
    bar.querySelector('#bulkGen').onclick = async () => {
      if (await confirmDialog({ title: `Generate documents for ${selected.size} application${selected.size > 1 ? 's' : ''}?`, message: 'Each one is queued and tailored in turn. Applications without a job description or profile are skipped.', confirm: 'Queue generation' })) run({ action: 'generate' }, 'Queued');
    };
    const dl = (fmt) => { location.href = `/api/applications/export.zip?ids=${ids().join(',')}&docs=resume,cover&fmt=${fmt}`; toast('Preparing your ZIP…'); };
    bar.querySelector('#bulkPdf').onclick = () => dl('pdf');
    bar.querySelector('#bulkDocx').onclick = () => dl('docx');
    bar.querySelector('#bulkDel').onclick = async () => {
      if (await confirmDialog({ title: `Delete ${selected.size} application${selected.size > 1 ? 's' : ''}?`, message: 'Their document versions, answers and share links are permanently removed.', confirm: 'Delete', danger: true })) run({ action: 'delete' }, 'Deleted');
    };
    bar.querySelector('#bulkClear').onclick = () => { selected.clear(); el.querySelectorAll('[data-sel], #selAll').forEach(c => c.checked = false); drawBulk(); };
  }

  // ---------------------------------------------------------- row helpers
  const ib = (attrs, ico, title, color = '') => raw(`<button class="icon-btn" ${attrs} title="${esc(title)}" aria-label="${esc(title)}"${color ? ` style="color:${color}"` : ''}>${icon(ico)}</button>`);
  const il = (href, ico, title, color = '') => raw(`<a class="icon-btn" href="${esc(href)}" title="${esc(title)}" aria-label="${esc(title)}"${color ? ` style="color:${color}"` : ''}>${icon(ico)}</a>`);
  function docCells(a) {
    const g = a.latest_generation, r = a.ready;
    if (g && ['queued', 'generating'].includes(g.status)) {
      return html`<td colspan="2"><div class="row" style="gap:8px">${genBadge(g)}${g.status === 'generating' ? html`<div class="progress mini-progress"><div style="width:${g.progress}%"></div></div>` : ''}</div></td>`;
    }
    if (!r) {
      return html`<td><div class="row" style="gap:2px">${g?.status === 'failed' ? html`<span class="badge failed" title="${g.error}">Failed</span>` : ''}${ib(`data-gen="${a.id}"`, 'sparkles', g?.status === 'failed' ? 'Try again' : 'Generate documents', 'var(--brand)')}</div></td><td><span class="muted small">—</span></td>`;
    }
    const base = `/api/generations/${r.id}/doc`;
    return html`<td><div class="row" style="gap:2px">
        ${ib(`data-preview="${r.id}"`, 'eye', 'Preview résumé')}
        ${il(`${base}/resume.docx?download=1`, 'file', 'Résumé (Word)', 'var(--brand)')}
        ${il(`${base}/resume.pdf?download=1`, 'download', 'Résumé (PDF)', 'var(--red)')}
        ${ib(`data-gen="${a.id}"`, 'refresh', 'Regenerate', 'var(--text-3)')}
        ${ib(`data-json="${r.id}"`, 'database', 'Résumé JSON', 'var(--text-3)')}
        ${g?.status === 'failed' ? html`<span title="Latest regeneration failed: ${g.error}" style="color:var(--red);display:inline-flex">${icon('alert')}</span>` : ''}
      </div></td>
      <td><div class="row" style="gap:2px">${r.has_cover ? html`${il(`${base}/cover.docx?download=1`, 'file', 'Cover letter (Word)', 'var(--brand)')}${il(`${base}/cover.pdf?download=1`, 'download', 'Cover letter (PDF)', 'var(--amber)')}` : html`<span class="muted small">—</span>`}</div></td>`;
  }

  // ---------------------------------------------------------- load + render
  async function load() {
    clearTimeout(timer);
    let data;
    try {
      const params = new URLSearchParams({ status: view === 'board' ? '' : status, q, profile_id: profileId || 0, scope, owner_id: ownerId || 0, date_from: dateFrom, date_to: dateTo });
      data = await api.get('/api/applications?' + params);
    } catch (e) { toastError(e); return; }
    apps = data.applications;
    const visible = new Set(apps.map(a => a.id));
    [...selected].forEach(id => { if (!visible.has(id)) selected.delete(id); });
    const total = Object.values(data.counts).reduce((a, b) => a + b, 0);
    mount(el.querySelector('#statusSeg'), html`<button data-s="" class="${!status ? 'on' : ''}">All <span class="n">${total}</span></button>
      ${data.statuses.map(s => html`<button data-s="${s}" class="${status === s ? 'on' : ''}">${STATUS[s].label} <span class="n">${data.counts[s] || 0}</span></button>`)}`);
    el.querySelector('#statusSeg').style.display = view === 'board' ? 'none' : '';
    el.querySelectorAll('#statusSeg button').forEach(b => b.onclick = () => { status = b.dataset.s; load(); });
    el.querySelectorAll('#viewSeg button').forEach(b => b.classList.toggle('on', b.dataset.v === view));
    if (isAdmin) {
      el.querySelectorAll('#scopeTabs button').forEach(b => b.classList.toggle('on', b.dataset.sc === scope));
      const sel = el.querySelector('#owner');
      sel.classList.toggle('hidden', scope === 'mine');
      mount(sel, html`<option value="">${scope === 'all' ? 'Everyone' : 'All bidders'}</option>${scope === 'all' ? html`<option value="${state.user.id}" ${String(state.user.id) === String(ownerId) ? 'selected' : ''}>Me</option>` : ''}
        ${data.team.map(t => html`<option value="${t.id}" ${String(t.id) === String(ownerId) ? 'selected' : ''}>${t.name}</option>`)}`);
    }
    const showOwner = scope !== 'mine';

    if (!apps.length) {
      mount(listEl, html`<div class="card">${total || dateFrom || dateTo || q ? emptyState('search', 'No matches', 'Try a different search, date range or status filter.')
        : emptyState('briefcase', 'Start your first application', 'Paste a job link or description. CareerPilot scrapes the posting, flags anything you should know, and tailors your documents.', '<a class="btn primary" href="#/applications/new">New application</a>')}</div>`);
    } else if (view === 'board') {
      const cols = ['saved', 'ready', 'applied', 'screening', 'interview', 'offer'];
      mount(listEl, html`<div style="display:grid;grid-template-columns:repeat(${cols.length},minmax(220px,1fr));gap:14px;overflow-x:auto;padding-bottom:10px">
        ${cols.map(c => { const items = apps.filter(a => a.status === c); return html`<div class="stack tight" style="min-width:220px">
          <div class="row between" style="padding:2px 4px">${statusBadge(c)}<span class="muted small">${items.length}</span></div>
          ${items.map(a => html`<div class="card" style="padding:12px;cursor:pointer" data-go="/applications/${a.id}">
            <div class="row" style="gap:10px">${logo(a.company, 30, { url: a.url })}<div class="grow"><div class="co-name truncate" style="font-size:13.5px">${a.title}</div><div class="co-sub truncate">${a.company}${showOwner ? ' · ' + a.owner_name : ''}</div></div></div>
            <div class="row between" style="margin-top:10px">${genBadge(a.latest_generation)}<span class="muted small">${timeAgo(a.updated_at)}</span></div></div>`)}
          ${items.length ? '' : html`<div class="muted small" style="border:1px dashed var(--border-strong);border-radius:12px;padding:16px;text-align:center">Nothing here</div>`}</div>`; })}</div>
        <p class="muted small">Rejected and withdrawn applications are hidden on the board.</p>`);
    } else {
      const allOn = apps.every(a => selected.has(a.id));
      mount(listEl, html`<div class="card table-wrap"><table class="table apps-table"><thead><tr>
        <th style="width:36px"><input type="checkbox" id="selAll" ${allOn ? 'checked' : ''} aria-label="Select all"></th><th class="hide-md" style="width:36px">#</th>
        <th>Role</th>${showOwner ? html`<th>Bidder</th>` : ''}<th class="hide-md">Profile</th><th>Status</th><th class="hide-sm">Date</th>
        <th>Résumé</th><th class="hide-sm">Cover letter</th><th style="text-align:right">Actions</th></tr></thead><tbody>
        ${apps.map((a, i) => html`<tr class="click ${selected.has(a.id) ? 'sel' : ''}" data-row="${a.id}">
          <td><input type="checkbox" data-sel="${a.id}" ${selected.has(a.id) ? 'checked' : ''} aria-label="Select ${a.company}"></td>
          <td class="hide-md muted small">${i + 1}</td>
          <td><div class="co" style="max-width:300px">${logo(a.company, 32, { url: a.url })}<div style="min-width:0"><div class="row" style="gap:6px;flex-wrap:nowrap"><span class="co-name truncate" title="${a.title}">${a.title}</span>
            ${a.url ? html`<a href="${a.url}" target="_blank" rel="noopener" class="row" style="color:var(--text-3)" title="Open job posting" data-stop>${icon('external', 'xs')}</a>` : ''}
            ${a.flags.length ? html`<span class="row" style="color:var(--amber)" title="${a.flags.join(' · ')}">${icon('alert', 'xs')}</span>` : ''}</div>
            <div class="co-sub truncate">${a.company}${a.location ? ' · ' + a.location : ''}${a.work_arrangement ? ' · ' + a.work_arrangement : ''}</div></div></div></td>
          ${showOwner ? html`<td><b class="small">${a.owner_id === state.user.id ? 'Me' : a.owner_name}</b></td>` : ''}
          <td class="hide-md muted small">${a.profile_label || '—'}</td>
          <td><select class="status-select ${a.status}" data-status="${a.id}" aria-label="Status">${Object.entries(STATUS).map(([k, v]) => html`<option value="${k}" ${a.status === k ? 'selected' : ''}>${v.label}</option>`)}</select></td>
          <td class="hide-sm muted small" title="${new Date(a.created_at).toLocaleString()}">${new Date(a.created_at).toLocaleDateString()}</td>
          ${docCells(a)}
          <td><div class="row" style="gap:2px;justify-content:flex-end">
            <button class="icon-btn ans-btn ${a.answers_count ? 'has' : ''}" data-answers="${a.id}" title="${a.answers_count ? `Get answers · ${a.answers_count} answered` : 'Get answers'}" aria-label="Get answers">${icon('chat')}${a.answers_count ? html`<span class="ans-n">${a.answers_count}</span>` : ''}</button>
            ${ib(`data-notes="${a.id}"`, 'note', a.has_notes ? 'Notes' : 'Add notes', a.has_notes ? 'var(--amber)' : '')}
            ${il(`#/applications/${a.id}`, 'eye', 'Open')}
            ${il(`#/applications/${a.id}/edit`, 'edit', 'Edit')}
            ${ib(`data-del="${a.id}"`, 'trash', 'Delete', 'var(--red)')}</div></td></tr>`)}
      </tbody></table></div><p class="muted small" style="margin-top:8px">${apps.length} application${apps.length === 1 ? '' : 's'} shown.</p>`);
    }
    bindRows();
    drawBulk();
    if (apps.some(a => ['queued', 'generating'].includes(a.latest_generation?.status))) timer = setTimeout(load, 4000);
  }

  function bindRows() {
    listEl.querySelectorAll('[data-go]').forEach(r => r.onclick = () => go(r.dataset.go));
    listEl.querySelectorAll('tr[data-row]').forEach(tr => tr.onclick = (e) => {
      if (e.target.closest('button, a, input, select, label, [data-stop]')) return;
      go(`/applications/${tr.dataset.row}`);
    });
    const selAll = listEl.querySelector('#selAll');
    if (selAll) selAll.onchange = () => { apps.forEach(a => selAll.checked ? selected.add(a.id) : selected.delete(a.id)); listEl.querySelectorAll('[data-sel]').forEach(c => { c.checked = selAll.checked; c.closest('tr').classList.toggle('sel', selAll.checked); }); drawBulk(); };
    listEl.querySelectorAll('[data-sel]').forEach(c => c.onchange = () => {
      const id = Number(c.dataset.sel);
      c.checked ? selected.add(id) : selected.delete(id);
      c.closest('tr').classList.toggle('sel', c.checked);
      if (selAll) selAll.checked = apps.every(a => selected.has(a.id));
      drawBulk();
    });
    listEl.querySelectorAll('[data-status]').forEach(s => s.onchange = async () => {
      try { await api.post(`/api/applications/${s.dataset.status}/status`, { status: s.value }); s.className = `status-select ${s.value}`; toast(`Marked as ${STATUS[s.value].label}`); load(); }
      catch (e) { toastError(e); load(); }
    });
    listEl.querySelectorAll('[data-gen]').forEach(b => b.onclick = async () => {
      const a = apps.find(x => x.id === Number(b.dataset.gen));
      if (a.ready && !await confirmDialog({ title: 'Regenerate documents?', message: `A new version is written for <b>${esc(a.company)}</b>. Earlier versions stay available.`, confirm: 'Regenerate' })) return;
      try { await api.post(`/api/applications/${a.id}/generate`, {}); toast('Queued for generation'); load(); } catch (e) { toastError(e); }
    });
    listEl.querySelectorAll('[data-preview]').forEach(b => b.onclick = () => {
      const a = apps.find(x => x.ready?.id === Number(b.dataset.preview));
      modal({ title: `${a.company} — résumé v${a.ready.version}`, size: 'xl',
        body: `<div class="doc-frame-wrap" style="height:100%"><iframe class="doc-frame" src="/api/generations/${a.ready.id}/doc/resume.html"></iframe></div>`,
        foot: `<a class="btn" href="/api/generations/${a.ready.id}/doc/resume.docx?download=1">${icon('file')}Word</a><a class="btn" href="/api/generations/${a.ready.id}/doc/resume.pdf?download=1">${icon('download')}PDF</a><a class="btn primary" href="#/applications/${a.id}">Open application</a>` });
    });
    listEl.querySelectorAll('[data-json]').forEach(b => b.onclick = async () => {
      const gid = b.dataset.json;
      try {
        const data = await api.get(`/api/generations/${gid}/doc/resume.json`);
        const text = JSON.stringify(data, null, 2);
        const m = modal({ title: 'Résumé JSON', size: 'wide', body: `<div class="code" style="max-height:60vh">${esc(text)}</div>`,
          foot: `<a class="btn" href="/api/generations/${gid}/doc/resume.json?download=1">${icon('download')}Download</a><button class="btn primary" data-copy>${icon('copy')}Copy</button>` });
        m.$('[data-copy]').onclick = () => copyText(text, 'JSON copied');
      } catch (e) { toastError(e); }
    });
    listEl.querySelectorAll('[data-answers]').forEach(b => b.onclick = () => openAnswersChat(Number(b.dataset.answers), { onChange: load }));
    listEl.querySelectorAll('[data-notes]').forEach(b => b.onclick = async () => {
      const id = b.dataset.notes;
      const { application: a } = await api.get(`/api/applications/${id}`);
      const m = modal({ title: `Notes · ${a.company}`, body: `<div class="stack"><p class="muted small">${esc(a.title)}</p><textarea id="noteText" rows="9" placeholder="Recruiter, referral, follow-up dates, interview notes…">${esc(a.notes || '')}</textarea></div>`,
        foot: `<button class="btn" data-close>Cancel</button><button class="btn primary" data-save>${icon('check')}Save notes</button>` });
      m.$('#noteText').focus();
      m.$('[data-save]').onclick = async (e) => {
        const btn = e.currentTarget; busy(btn, true, 'Saving…');
        try { await api.put(`/api/applications/${id}/notes`, { notes: m.$('#noteText').value }); m.close(); toast('Notes saved'); load(); }
        catch (err) { toastError(err); busy(btn, false); }
      };
    });
    listEl.querySelectorAll('[data-del]').forEach(b => b.onclick = async () => {
      const a = apps.find(x => x.id === Number(b.dataset.del));
      if (!await confirmDialog({ title: 'Delete application?', message: `<b>${esc(a.company)} — ${esc(a.title)}</b> and its document versions, answers and share links will be permanently removed.`, confirm: 'Delete', danger: true })) return;
      try { await api.del(`/api/applications/${a.id}`); selected.delete(a.id); toast('Application deleted'); load(); } catch (e) { toastError(e); }
    });
  }

  el.querySelector('#q').oninput = debounce((e) => { q = e.target.value.trim(); load(); }, 250);
  el.querySelector('#profile').onchange = (e) => { profileId = e.target.value; load(); };
  el.querySelectorAll('#scopeTabs button').forEach(b => b.onclick = () => { scope = b.dataset.sc; ownerId = ''; status = ''; selected.clear(); load(); });
  el.querySelector('#owner')?.addEventListener('change', (e) => { ownerId = e.target.value; load(); });
  el.querySelectorAll('#viewSeg button').forEach(b => b.onclick = () => { view = b.dataset.v; try { localStorage.setItem('cp-apps-view', view); } catch {} load(); });
  await load();
  return () => { clearTimeout(timer); document.removeEventListener('click', closePop); };
}
