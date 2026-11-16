import { api } from '../api.js';
import { html, icon, mount, timeAgo, toast, toastError, busy, modal, confirmDialog, esc } from '../ui.js';

// The platform-wide import filter (master only): which jobs get into the Job Pool at all.
async function drawFilter(box) {
  const { filter: f } = await api.get('/api/job-filter');
  mount(box, html`<form class="card card-pad stack" id="jfForm">
    <div class="row between wrap"><div><h3 style="margin:0">Import filter</h3><p class="muted small" style="margin:2px 0 0">Applies to every source. Only you (the platform manager) can change it.</p></div>
      <div class="row"><button type="button" class="btn" id="jfApply">${icon('trash')}Apply to current pool</button><button class="btn primary">${icon('check')}Save filter</button></div></div>
    <div class="grid g2">
      <label class="check"><input type="checkbox" name="jobfilter_us_remote" ${f.jobfilter_us_remote === '1' ? 'checked' : ''}><div><b>US remote only</b><div class="muted small">Remote jobs open to the US (US, a US state, North America or anywhere). Other countries and on-site jobs are skipped.</div></div></label>
      <label class="check"><input type="checkbox" name="jobfilter_software" ${f.jobfilter_software === '1' ? 'checked' : ''}><div><b>Software jobs only</b><div class="muted small">The title must contain one of the software keywords below.</div></div></label>
    </div>
    <div class="grid g2">
      <label class="field">Software keywords — the title must contain one<span class="hint">whole words · commas or one per line · /regex/ allowed</span><textarea name="jobfilter_include" rows="5">${f.jobfilter_include}</textarea></label>
      <label class="field">Blocked keywords — never import a job whose title has one<span class="hint">e.g. senior, staff, clearance, contract · checked before everything else</span><textarea name="jobfilter_exclude" rows="5">${f.jobfilter_exclude}</textarea></label>
    </div></form>`);
  const form = box.querySelector('#jfForm');
  const values = () => ({ jobfilter_us_remote: form.jobfilter_us_remote.checked ? '1' : '0', jobfilter_software: form.jobfilter_software.checked ? '1' : '0',
    jobfilter_include: form.jobfilter_include.value, jobfilter_exclude: form.jobfilter_exclude.value });
  form.onsubmit = async (e) => {
    e.preventDefault();
    const b = form.querySelector('button.primary'); busy(b, true);
    try { await api.put('/api/job-filter', values()); toast('Import filter saved — it applies from the next import'); } catch (err) { toastError(err); }
    busy(b, false);
  };
  box.querySelector('#jfApply').onclick = async (e) => {
    const b = e.currentTarget;
    if (!await confirmDialog({ title: 'Apply the filter to the current pool?', message: 'Saves the filter, then removes every job in the pool that does not match it. Applications you already created are kept.', confirm: 'Apply and remove', danger: true })) return;
    busy(b, true);
    try {
      await api.put('/api/job-filter', values());
      const r = await api.post('/api/job-filter/apply');
      toast(r.total ? `${r.total} job${r.total === 1 ? '' : 's'} removed — ${Object.entries(r.removed).map(([k, n]) => `${n} ${k}`).join(', ')}` : 'Every job in the pool already matches');
    } catch (err) { toastError(err); }
    busy(b, false);
  };
}

export default async function ({ el, isCurrent }) {
  let timer = null;
  async function draw() {
    clearTimeout(timer);
    const d = await api.get('/api/job-sources');
    if (!isCurrent()) return;
    const total = d.sources.reduce((a, s) => a + s.jobs, 0);
    mount(el, html`<div class="page-head"><div><h1>Job sources</h1><p>Free public job feeds and employer boards. No API keys or AI calls are used for imports.</p></div>
      <div class="row"><button class="btn" id="addBtn">${icon('plus')}Add source</button><button class="btn primary" id="importBtn" ${d.importing ? 'disabled' : ''}>${d.importing ? html`<span class="spinner"></span>Importing…` : html`${icon('refresh')}Import now`}</button></div></div>
      <div class="grid g3" style="margin-bottom:16px">
        <div class="card kpi"><div class="label">Sources</div><div class="value">${d.sources.filter(s => s.enabled).length}<span class="muted" style="font-size:16px"> / ${d.sources.length}</span></div><div class="sub">enabled</div></div>
        <div class="card kpi"><div class="label">Jobs in pool</div><div class="value">${total.toLocaleString()}</div><div class="sub">across all sources</div></div>
        <div class="card kpi"><div class="label">Last import</div><div class="value" style="font-size:22px">${d.last_import ? timeAgo(d.last_import) : 'Never'}</div><div class="sub">${Number(d.interval_hours) ? `Automatic every ${Number(d.interval_hours)}h` : 'Manual only — set a schedule in Settings → Workspace'}</div></div></div>
      <div id="filterBox" style="margin-bottom:16px"></div>
      <div class="card table-wrap"><table class="table"><thead><tr><th>Source</th><th>Provider</th><th>Jobs</th><th>Last run</th><th>Enabled</th><th></th></tr></thead><tbody>
        ${d.sources.map(s => html`<tr><td><b>${s.name}</b>${s.token ? html`<div class="muted small mono">${s.token}</div>` : ''}</td><td>${d.providers[s.provider]?.name || s.provider}</td><td>${s.jobs}</td>
          <td class="small"><span style="color:${s.last_status.startsWith('Error') ? 'var(--red)' : 'var(--text-2)'}">${s.last_status || '—'}</span>${s.last_run ? html`<div class="muted">${timeAgo(s.last_run)}</div>` : ''}</td>
          <td><input type="checkbox" data-toggle="${s.id}" ${s.enabled ? 'checked' : ''}></td><td><button class="btn sm ghost icon" data-del="${s.id}">${icon('trash')}</button></td></tr>`)}
      </tbody></table></div>`);
    drawFilter(el.querySelector('#filterBox'));
    el.querySelector('#importBtn').onclick = async (e) => { const cbtn = e.currentTarget; busy(cbtn, true, 'Importing…'); await api.post('/api/job-sources/import'); toast('Import started'); timer = setTimeout(draw, 2500); };
    el.querySelectorAll('[data-toggle]').forEach(c => c.onchange = async () => { await api.put(`/api/job-sources/${c.dataset.toggle}`, { enabled: c.checked }); toast(c.checked ? 'Source enabled' : 'Source disabled'); });
    el.querySelectorAll('[data-del]').forEach(b => b.onclick = async () => { if (await confirmDialog({ title: 'Remove source?', message: 'Jobs already imported stay in the pool.', confirm: 'Remove', danger: true })) { await api.del(`/api/job-sources/${b.dataset.del}`); draw(); } });
    el.querySelector('#addBtn').onclick = () => {
      const m = modal({ title: 'Add a job source', body: `<div class="stack">
        <label class="field">Provider<select id="prov">${Object.entries(d.providers).map(([k, p]) => `<option value="${k}">${esc(p.name)}</option>`).join('')}</select></label>
        <label class="field">Token / slug <span class="hint" id="hint"></span><input id="tok" placeholder=""></label>
        <label class="field">Display name<input id="nm" placeholder="Optional"></label>
        <label class="check"><input type="checkbox" id="test" checked><span>Import from it now to test</span></label><div id="res"></div></div>`,
        foot: `<button class="btn" data-close>Cancel</button><button class="btn primary" data-save>Add source</button>` });
      const hint = () => { m.$('#hint').textContent = d.providers[m.$('#prov').value].hint; };
      m.$('#prov').onchange = hint; hint();
      m.$('[data-save]').onclick = async (e) => {
        const cbtn = e.currentTarget; busy(cbtn, true, 'Adding…');
        try {
          const r = await api.post('/api/job-sources', { provider: m.$('#prov').value, token: m.$('#tok').value, name: m.$('#nm').value, test: m.$('#test').checked });
          if (r.result && !r.result.ok) { m.$('#res').innerHTML = `<div class="alert warn">${icon('alert')}<div><b>Added, but the test import failed</b>${esc(r.result.error)}</div></div>`; busy(cbtn, false); draw(); return; }
          m.close(); toast(r.result ? `Source added · ${r.result.read} jobs read` : 'Source added'); draw();
        } catch (err) { toastError(err); busy(cbtn, false); }
      };
    };
    if (d.importing) timer = setTimeout(draw, 3000);
  }
  await draw();
  return () => clearTimeout(timer);
}
