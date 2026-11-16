import { api } from '../api.js';
import { html, icon, mount, busy, toast, toastError, confirmDialog, modal, esc, timeAgo } from '../ui.js';

export default async function ({ el, query }) {
  let data = await api.get('/api/prompts');
  let sel = Number(query.id) || data.prompts[0]?.id || null;
  const { profiles } = await api.get('/api/profiles');

  function draw() {
    const p = data.prompts.find(x => x.id === sel) || { id: null, label: '', text: '', question_text: '' };
    mount(el, html`
      <div class="page-head"><div><h1>Prompts</h1><p>Your private instructions for DeepSeek. CareerPilot adds the output format rules automatically.</p></div>
        <button class="btn primary" id="newBtn">${icon('plus')}New prompt</button></div>
      <div class="grid" style="grid-template-columns:280px minmax(0,1fr);align-items:start" id="pGrid">
        <div class="card"><div class="card-head"><h3>Library</h3><span class="muted small">${data.prompts.length}</span></div>
          ${data.prompts.map(x => html`<div class="list-item" data-sel="${x.id}" style="${x.id === sel ? 'background:var(--brand-50)' : ''}">
            <div class="grow"><div class="row" style="gap:6px"><b class="truncate" style="font-size:13.5px">${x.label}</b>${x.is_default ? html`<span class="badge applied">Default</span>` : ''}</div>
            <div class="muted small">Edited ${timeAgo(x.updated_at)}</div></div></div>`)}</div>
        <form id="pForm" class="card" novalidate>
          <div class="card-head"><input name="label" value="${p.label}" placeholder="Prompt name" style="font-weight:700;max-width:420px">
            <div class="row">${p.id && !p.is_default ? html`<button type="button" class="btn sm" id="defBtn">Make default</button>` : ''}
            ${p.id ? html`<button type="button" class="btn sm ghost icon" id="delBtn" title="Delete">${icon('trash')}</button>` : ''}</div></div>
          <div class="card-body stack">
            <div class="row wrap" style="gap:6px"><span class="muted small">Insert:</span>${data.placeholders.map(ph => html`<button type="button" class="chip placeholder-chip" data-ph="${ph}">${ph}</button>`)}</div>
            <label class="field">Résumé + cover letter instructions<textarea name="text" class="code-input" rows="20">${p.text}</textarea></label>
            <label class="field">Application answers instructions <span class="hint">Optional. The résumé, cover letter, job description and questions are appended automatically.</span>
              <textarea name="question_text" rows="6">${p.question_text}</textarea></label>
            <details><summary class="small" style="cursor:pointer;font-weight:650;color:var(--text-2)">Output rules CareerPilot always appends</summary><div class="code" style="margin-top:8px">${data.contract}</div></details>
          </div>
          <div class="card-foot" style="justify-content:space-between"><div class="row"><select id="pvProfile" style="width:auto;height:34px"><option value="">Sample profile</option>${profiles.map(x => html`<option value="${x.id}">${x.label}</option>`)}</select>
            <button type="button" class="btn sm" id="previewBtn">${icon('eye')}Preview full prompt</button></div>
            <button class="btn primary" id="saveBtn">${icon('check')}Save prompt</button></div>
        </form>
      </div>`);
    const grid = el.querySelector('#pGrid');
    if (innerWidth < 900) grid.style.gridTemplateColumns = '1fr';
    let lastFocus = el.querySelector('[name=text]');
    el.querySelectorAll('textarea').forEach(t => t.addEventListener('focus', () => lastFocus = t));
    el.querySelectorAll('[data-ph]').forEach(b => b.onclick = () => {
      const t = lastFocus, s = t.selectionStart, ph = b.dataset.ph;
      t.value = t.value.slice(0, s) + ph + t.value.slice(t.selectionEnd); t.focus(); t.selectionStart = t.selectionEnd = s + ph.length;
    });
    el.querySelectorAll('[data-sel]').forEach(r => r.onclick = () => { sel = Number(r.dataset.sel); draw(); });
    el.querySelector('#newBtn').onclick = () => { sel = null; draw(); el.querySelector('[name=label]').focus(); };
    el.querySelector('#defBtn')?.addEventListener('click', async () => { await api.post(`/api/prompts/${p.id}/default`); data = await api.get('/api/prompts'); toast('Default prompt updated'); draw(); });
    el.querySelector('#delBtn')?.addEventListener('click', async () => {
      if (!await confirmDialog({ title: 'Delete prompt?', message: `<b>${esc(p.label)}</b> will be deleted. Profiles using it fall back to your default.`, confirm: 'Delete', danger: true })) return;
      try { await api.del(`/api/prompts/${p.id}`); data = await api.get('/api/prompts'); sel = data.prompts[0]?.id; toast('Prompt deleted'); draw(); } catch (e) { toastError(e); }
    });
    el.querySelector('#previewBtn').onclick = async (e) => {
      const cbtn = e.currentTarget; busy(cbtn, true);
      try {
        const r = await api.post('/api/prompts/preview', { text: el.querySelector('[name=text]').value, profile_id: el.querySelector('#pvProfile').value || null });
        modal({ title: 'Full prompt sent to DeepSeek', size: 'wide', body: `<div class="code" style="max-height:none">${esc(r.text)}</div>` });
      } catch (err) { toastError(err); }
      busy(cbtn, false);
    };
    el.querySelector('#pForm').onsubmit = async (e) => {
      e.preventDefault();
      const f = e.target, btn = el.querySelector('#saveBtn');
      const body = { label: f.label.value, text: f.text.value, question_text: f.question_text.value };
      busy(btn, true, 'Saving…');
      try {
        const r = p.id ? await api.put(`/api/prompts/${p.id}`, body) : await api.post('/api/prompts', body);
        data = await api.get('/api/prompts'); sel = r.prompt.id; toast('Prompt saved'); draw();
      } catch (err) { toastError(err); busy(btn, false); }
    };
  }
  draw();
}
