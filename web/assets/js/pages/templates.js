import { api } from '../api.js';
import { html, raw, icon, mount, busy, toast, toastError, modal, confirmDialog, esc, debounce } from '../ui.js';
import { loadSettings, state } from '../app.js';

// ---------------------------------------------------------------- labels
const L = {
  align: { left: 'Left', center: 'Center', right: 'Right' },
  page_size: { letter: 'Letter (8.5 × 11 in)', a4: 'A4 (210 × 297 mm)' },
  heading_rule: { none: 'None', solid: 'Solid line', double: 'Double line', dotted: 'Dotted', dashed: 'Dashed', thick: 'Thick line' },
  skills_layout: { category: 'Category: items', inline: 'Inline (comma separated)', grid: 'Two-column grid', bullets: 'Bulleted categories' },
  bullet_style: { disc: 'Disc •', circle: 'Circle ◦', square: 'Square ▪', dash: 'Dash –', arrow: 'Arrow ›', check: 'Check ✓', none: 'None' },
  contact_layout: { single: 'Single line', double: 'Two sides (left / right)', stacked: 'Stacked lines' },
  entry_layout: { double: 'Double line', single: 'Single line' },
  expertise_layout: { inline: 'Inline (dot-separated)', bullets: 'Bulleted list', grid: 'Three-column grid', tags: 'Tags' },
};
const TABS = [
  ['typography', 'Typography', 'file'], ['layout', 'Layout', 'layout'], ['colors', 'Colors', 'sparkles'], ['contact', 'Contact', 'user'],
  ['experience', 'Experience', 'briefcase'], ['education', 'Education', 'bookmark'], ['certifications', 'Certifications', 'shield'],
  ['headline', 'Headline', 'wand'], ['expertise', 'Expertise', 'target'], ['projects', 'Projects', 'activity'], ['sections', 'Sections', 'list'], ['pdf', 'PDF Style', 'layout'],
];
const ELEMENTS = {
  experience: [['title', 'Job Title'], ['company', 'Company'], ['location', 'Location'], ['dates', 'Dates'], ['job_type', 'Job Type']],
  education: [['degree', 'Degree'], ['institution', 'Institution'], ['location', 'Location'], ['dates', 'Dates']],
  certifications: [['name', 'Certificate Name'], ['issuer', 'Issuer'], ['date', 'Issue Date']],
  projects: [['name', 'Project Name'], ['link', 'Project Link'], ['tech', 'Technologies'], ['dates', 'Dates']],
};
const CONTACT = [['email', 'Email'], ['phone', 'Phone'], ['location', 'Location'], ['linkedin', 'LinkedIn'], ['links', 'Other links']];
const COLORS = [['name', 'Candidate Name'], ['headings', 'Section Headings'], ['body', 'Body Text'], ['accent', 'Accent'], ['contact', 'Contact Text'],
  ['secondary', 'Secondary Text'], ['section_bg', 'Section Background'], ['section_label', 'Section Label Text']];

const getp = (o, path) => path.split('.').reduce((a, k) => (a == null ? a : a[k]), o);
function setp(o, path, v) { const ks = path.split('.'); const last = ks.pop(); ks.reduce((a, k) => (a[k] ??= {}), o)[last] = v; }
const clone = (x) => JSON.parse(JSON.stringify(x));

export default async function ({ el, isCurrent }) {
  const [list, opts, settings, { profiles }] = await Promise.all([api.get('/api/templates'), api.get('/api/templates/options'),
    loadSettings(true), state.user.role === 'master' ? { profiles: [] } : api.get('/api/profiles')]);
  let templates = list.templates;
  let defId = list.default_id;
  let selected = templates.find(t => t.id === defId && t.kind === 'style')?.id || templates[0].id;
  let tab = 'typography', style = null, saved = null, customised = false, profileId = '';
  // inject decoration CSS used by the PDF-style galleries
  if (!document.getElementById('cp-sec-css')) { const st = document.createElement('style'); st.id = 'cp-sec-css'; st.textContent = opts.section_css; document.head.appendChild(st); }

  mount(el, html`
    <div class="page-head"><div><h1>Templates</h1><p>Single-column, ATS-friendly designs researched from the layouts software engineers use most. Pick one, then fine-tune every detail.</p></div>
      <div class="row"><label class="row small muted" style="gap:8px">Preview with<select id="pvProfile" style="width:auto;height:34px"><option value="">Sample candidate</option>${profiles.map(p => html`<option value="${p.id}">${p.label}</option>`)}</select></label>
      <button class="btn" id="uploadBtn">${icon('upload')}Upload HTML</button></div></div>
    <div class="tpl-strip" id="gallery"></div>
    <div id="editor"></div>`);

  // ---------------------------------------------------------------- gallery
  function drawGallery() {
    mount(el.querySelector('#gallery'), html`${templates.map(t => html`<div class="card tpl-card ${t.id === selected ? 'on' : ''}" data-id="${t.id}" title="${t.description}">
      <div class="tpl-thumb"><iframe src="/api/templates/${t.id}/preview?profile_id=${profileId || 0}&v=${t.customised ? Date.now() % 1e6 : 0}" title="${t.label}" tabindex="-1"></iframe></div>
      <div class="tpl-info"><div class="row between" style="gap:6px"><b class="truncate">${t.label}</b>
        <span class="row" style="gap:4px">${t.id === defId ? html`<span class="badge applied" title="Default">${icon('check')}</span>` : ''}${t.customised ? html`<span class="badge screening">Edited</span>` : ''}${t.custom ? html`<span class="badge">HTML</span>` : ''}</span></div>
        <div class="muted small truncate">${(t.tags || []).join(' · ') || t.description}</div></div></div>`)}`);
    el.querySelectorAll('.tpl-card').forEach(c => c.onclick = () => selectTemplate(c.dataset.id));
  }

  async function selectTemplate(id) {
    if (style && JSON.stringify(style) !== JSON.stringify(saved) && !await confirmDialog({ title: 'Discard unsaved changes?', message: 'You changed this template\'s style without saving.', confirm: 'Discard' })) return;
    selected = id;
    drawGallery();
    const t = templates.find(x => x.id === id);
    if (t.kind !== 'style') { style = saved = null; drawCustomInfo(t); return; }
    const r = await api.get(`/api/templates/${id}/style`);
    style = clone(r.style); saved = clone(r.style); customised = r.customised;
    drawEditor();
  }

  function drawCustomInfo(t) {
    mount(el.querySelector('#editor'), html`<div class="card card-pad stack" style="margin-top:18px"><div class="row between"><h3>${t.label}</h3>
      <div class="row">${t.id === defId ? html`<span class="badge applied">${icon('check')}Default</span>` : html`<button class="btn" id="mkDef">Use as default</button>`}<button class="btn danger" id="delTpl">${icon('trash')}Delete</button></div></div>
      <p class="muted">Uploaded HTML templates are used as-is. Detailed style settings are available for the built-in templates.</p>
      <div class="doc-frame-wrap"><iframe class="doc-frame" src="/api/templates/${t.id}/preview?profile_id=${profileId || 0}"></iframe></div></div>`);
    el.querySelector('#mkDef')?.addEventListener('click', () => setDefault(t.id));
    el.querySelector('#delTpl').onclick = async () => {
      if (!await confirmDialog({ title: 'Delete template?', message: 'Documents using it fall back to the default template.', confirm: 'Delete', danger: true })) return;
      await api.del(`/api/templates/${t.id}`); templates = templates.filter(x => x.id !== t.id); toast('Template deleted'); selectTemplate(templates[0].id);
    };
  }

  // ---------------------------------------------------------------- field builders
  const F = {
    select: (path, label, options, hint = '') => html`<label class="field">${label}${hint ? html`<span class="hint">${hint}</span>` : ''}<select data-path="${path}">
      ${Object.entries(options).map(([v, l]) => html`<option value="${v}" ${String(getp(style, path)) === v ? 'selected' : ''}>${l}</option>`)}</select></label>`,
    num: (path, label, step = 1, hint = '') => html`<label class="field">${label}${hint ? html`<span class="hint">${hint}</span>` : ''}<input type="number" step="${step}" data-path="${path}" data-type="num" value="${getp(style, path)}"></label>`,
    text: (path, label, hint = '') => html`<label class="field">${label}${hint ? html`<span class="hint">${hint}</span>` : ''}<input data-path="${path}" value="${getp(style, path)}"></label>`,
    color: (path, label) => html`<label class="field">${label}<div class="color-field"><input class="mono" data-path="${path}" data-type="hex" value="${String(getp(style, path)).replace('#', '')}" maxlength="7">
      <input type="color" data-path="${path}" data-type="swatch" value="${getp(style, path)}" aria-label="${label} color"></div></label>`,
    check: (path, label) => html`<label class="check"><input type="checkbox" data-path="${path}" data-type="bool" ${getp(style, path) ? 'checked' : ''}><span>${label}</span></label>`,
    alignBtns: (path, label) => html`<div class="field">${label}<div class="seg align-seg">${['left', 'center', 'right'].map(a => html`<button type="button" data-align="${path}" data-v="${a}" class="${getp(style, path) === a ? 'on' : ''}">${L.align[a]}</button>`)}</div></div>`,
    eye: (path) => html`<button type="button" class="icon-btn eye ${getp(style, path) ? 'on' : ''}" data-toggle="${path}" title="${getp(style, path) ? 'Visible — click to hide' : 'Hidden — click to show'}">${icon(getp(style, path) ? 'eye' : 'eyeOff')}</button>`,
  };

  function elementRows(sec) {
    return html`${ELEMENTS[sec].map(([k, label]) => { const p = `${sec}.elements.${k}`; const e = getp(style, p); return html`<div class="el-row ${e.visible ? '' : 'off'}">
      <div class="row between wrap" style="gap:8px"><div class="row" style="gap:8px">${F.eye(p + '.visible')}<b>${label}</b></div>
        <div class="row" style="gap:6px">${getp(style, sec + '.layout') === 'double' ? html`<select data-path="${p}.line" data-type="num" class="sm-select">${[1, 2].map(n => html`<option value="${n}" ${e.line == n ? 'selected' : ''}>Line ${n}</option>`)}</select>` : ''}
          <select data-path="${p}.align" class="sm-select">${['left', 'right'].map(a => html`<option value="${a}" ${e.align === a ? 'selected' : ''}>${L.align[a]}</option>`)}</select></div></div>
      <div class="row between wrap" style="gap:8px;margin-top:8px"><div class="row" style="gap:14px">${F.check(p + '.bold', 'Bold')}${F.check(p + '.italic', 'Italic')}</div>
        <div class="color-field sm"><input class="mono" data-path="${p}.color" data-type="hex" value="${e.color.replace('#', '')}" maxlength="7"><input type="color" data-path="${p}.color" data-type="swatch" value="${e.color}" aria-label="${label} color"></div></div></div>`; })}`;
  }

  function entryTab(sec, hint) {
    return html`<div class="grid ${sec === 'experience' || sec === 'projects' ? 'g3' : 'g2'}">${F.select(sec + '.layout', 'Layout', L.entry_layout)}${F.num(sec + '.line_gap', 'Line gap (twips)', 10)}
      ${sec === 'experience' || sec === 'projects' ? F.num(sec + '.bullet_gap', 'Bullet gap (twips)', 10) : ''}</div>
      <p class="muted small">Set each element's line, alignment, visibility, text style and color. 20 twips = 1 point.</p>
      <div class="stack tight">${elementRows(sec)}</div>${F.text(sec + '.separator', 'Element separator')}${hint ? html`<p class="muted small">${hint}</p>` : ''}`;
  }

  function mini(accentHtml, wmHtml, secStyle, acc) {
    const lines = '<div class="mini-line" style="width:88%"></div><div class="mini-line" style="width:76%"></div><div class="mini-line" style="width:82%"></div>';
    return raw(`<div class="mini-sheet" style="--acc:${acc};--acc2:${acc}88;--sbg:${style.colors.section_bg};--slab:${style.colors.section_label}">
      <div class="mini-scale"><div class="mini-deco">${accentHtml || ''}</div><div class="mini-deco" style="opacity:${style.pdf.watermark_strength / 100}">${wmHtml || ''}</div>
      <div class="mini-body"><div class="mini-name"></div>${['Experience', 'Skills'].map(t => `<div class="cp-sh ${secStyle}" style="font:700 22pt Arial;color:${acc};margin:46pt 0 18pt;text-transform:uppercase;letter-spacing:2pt">${t}</div>${lines}`).join('')}</div></div></div>`);
  }

  function gallery(kind) {
    const acc = style.colors.accent;
    const P = style.pdf;
    if (kind === 'themes') return html`<div class="deco-grid">${opts.themes.map(t => { const a = opts.accents.find(x => x.id === t.header_accent), w = opts.watermarks.find(x => x.id === t.watermark);
      return html`<button type="button" class="deco-card ${P.theme === t.id ? 'on' : ''}" data-theme="${t.id}">${mini(a?.html, w?.html, t.section_style, t.accent || acc)}<span>${t.label}</span></button>`; })}</div>`;
    if (kind === 'accents') return html`<div class="deco-grid">${opts.accents.map(a => html`<button type="button" class="deco-card ${P.header_accent === a.id ? 'on' : ''}" data-pick="pdf.header_accent" data-v="${a.id}">${mini(a.html, '', 'none', acc)}<span>${a.label}</span></button>`)}</div>`;
    if (kind === 'watermarks') return html`<div class="deco-grid">${opts.watermarks.map(w => html`<button type="button" class="deco-card ${P.watermark === w.id ? 'on' : ''}" data-pick="pdf.watermark" data-v="${w.id}">${mini('', w.html, 'none', acc)}<span>${w.label}</span></button>`)}</div>`;
    return html`<div class="deco-grid">${opts.section_styles.map(sx => html`<button type="button" class="deco-card ${P.section_style === sx.id ? 'on' : ''}" data-pick="pdf.section_style" data-v="${sx.id}">${mini('', '', sx.id, acc)}<span>${sx.label}</span></button>`)}</div>`;
  }

  function tabBody() {
    const fonts = Object.fromEntries(opts.fonts.map(f => [f, f]));
    switch (tab) {
      case 'typography': return html`<div class="grid g2">${F.select('typography.body_font', 'Body font', fonts)}${F.select('typography.heading_font', 'Heading font', fonts)}
        ${F.num('typography.name_size', 'Name size (pt)', 0.5)}${F.num('typography.heading_size', 'Heading size (pt)', 0.5)}${F.num('typography.body_size', 'Body size (pt)', 0.25)}
        ${F.num('typography.contact_size', 'Contact size (pt)', 0.25)}${F.num('typography.subtext_size', 'Sub-text size (pt)', 0.25, 'Dates and right-aligned details')}${F.num('typography.letter_spacing', 'Letter spacing (pt)', 0.1)}</div>
        <div class="row wrap" style="gap:18px;margin-top:6px">${F.check('typography.name_upper', 'Name uppercase')}${F.check('typography.heading_upper', 'Heading uppercase')}</div>
        <p class="muted small">Calibri, Arial, Helvetica, Georgia and Times are installed everywhere. The others are web fonts embedded in the PDF; Word uses them only when installed.</p>`;
      case 'layout': return html`<div class="grid g2">${F.select('layout.header_align', 'Header alignment', L.align)}${F.text('layout.contact_sep', 'Contact separator')}
        ${F.num('layout.line_spacing', 'Line spacing (%)', 5)}${F.num('layout.section_spacing', 'Section spacing (twips)', 20)}${F.num('layout.name_contact_gap', 'Name–contact gap (twips)', 10)}</div>
        <hr class="sep">${F.select('layout.page_size', 'Page size', L.page_size)}
        <div class="field" style="margin-top:10px">Page margins <span class="hint">twips, 720 = 0.5 inch</span><div class="grid g4" style="gap:8px">
          ${['top', 'bottom', 'left', 'right'].map(k => html`<label class="field small">${k[0].toUpperCase() + k.slice(1)}<input type="number" step="20" data-path="layout.margins.${k}" data-type="num" value="${style.layout.margins[k]}"></label>`)}</div></div>
        <hr class="sep">${F.select('layout.heading_rule', 'Section heading line', L.heading_rule, 'A line beneath each section heading')}
        <div class="grid g3">${F.color('layout.rule_color', 'Line color')}${F.num('layout.rule_thickness', 'Thickness (1/8 pt)', 1)}${F.num('layout.rule_spacing', 'Spacing after (twips)', 10)}</div>
        <hr class="sep"><div class="grid g2">${F.select('layout.skills_layout', 'Skills layout', L.skills_layout)}${F.select('layout.bullet_style', 'Bullet style', L.bullet_style)}</div>`;
      case 'colors': return html`<p class="muted small">Enter a hex color or use the swatch. The accent color also drives decorations in PDF Style.</p>
        <div class="grid g2">${COLORS.map(([k, l]) => F.color('colors.' + k, l))}</div>`;
      case 'contact': return html`${F.select('contact.layout', 'Layout', L.contact_layout)}
        <div style="margin:12px 0">${F.check('contact.show_title', 'Show job title below candidate name')}</div>
        <p class="muted small">Contact lines are recognised automatically from the profile (email, phone, LinkedIn, links, location). "Right" is used by the two-sides layout.</p>
        <div class="stack tight">${CONTACT.map(([k, l]) => { const p = `contact.items.${k}`; const it = getp(style, p); return html`<div class="el-row ${it.visible ? '' : 'off'}"><div class="row between wrap" style="gap:8px">
          <div class="row" style="gap:8px">${F.eye(p + '.visible')}<b>${l}</b></div><div class="row" style="gap:6px"><select data-path="${p}.align" class="sm-select">${['left', 'right'].map(a => html`<option value="${a}" ${it.align === a ? 'selected' : ''}>${L.align[a]}</option>`)}</select>
          <div class="color-field sm"><input class="mono" data-path="${p}.color" data-type="hex" value="${it.color.replace('#', '')}" maxlength="7"><input type="color" data-path="${p}.color" data-type="swatch" value="${it.color}" aria-label="${l} color"></div></div></div></div>`; })}</div>`;
      case 'experience': return entryTab('experience');
      case 'education': return entryTab('education', 'Education location comes from the profile when entered.');
      case 'certifications': return entryTab('certifications');
      case 'projects': return entryTab('projects', 'Projects appear when a résumé version has projects (add them under Applications → Edit documents). Empty sections are omitted.');
      case 'headline': return html`<p class="muted small">Style the Headline section (enable it under Sections). It shows the résumé headline or the profile headline. The job title under the name is controlled in Contact.</p>
        <div class="grid g3">${F.text('headline.size', 'Font size (pt)', '“auto” or a number')}${F.num('headline.top_gap', 'Top gap (twips)', 20)}${F.num('headline.bottom_gap', 'Bottom gap (twips)', 20)}</div>
        ${F.color('headline.color', 'Headline color')}<div class="row" style="gap:18px;margin:10px 0">${F.check('headline.bold', 'Bold')}${F.check('headline.italic', 'Italic')}</div>${F.alignBtns('headline.align', 'Headline alignment')}`;
      case 'expertise': return html`<p class="muted small">Core Expertise uses the résumé's expertise entries, or the generated skill categories when there are none. Enable it under Sections.</p>${F.select('expertise.layout', 'Layout', L.expertise_layout)}`;
      case 'sections': return html`${F.alignBtns('sections.align', 'Section heading alignment')}
        <p class="muted small" style="margin:12px 0 8px">Reorder sections, rename headings and toggle visibility. Drag a handle or use the arrows. Empty sections are omitted.</p>
        <div class="stack tight" id="secList">${style.sections.order.map((s, i) => html`<div class="sec-row ${s.visible ? '' : 'off'}" draggable="true" data-i="${i}">
          <span class="grip" title="Drag to reorder">${icon('grip')}</span><input data-sec-title="${i}" value="${s.title}" aria-label="Heading for ${s.key}">
          <button type="button" class="icon-btn" data-move="${i}:-1" ${i === 0 ? 'disabled' : ''} title="Move up">${icon('arrowUp')}</button>
          <button type="button" class="icon-btn" data-move="${i}:1" ${i === style.sections.order.length - 1 ? 'disabled' : ''} title="Move down">${icon('arrowDown')}</button>
          <button type="button" class="icon-btn eye ${s.visible ? 'on' : ''}" data-sec-eye="${i}" title="${s.visible ? 'Visible' : 'Hidden'}">${icon(s.visible ? 'eye' : 'eyeOff')}</button></div>`)}</div>`;
      case 'pdf': return html`<div class="row between"><h3>Decorative shape effects</h3><span class="tag">Applied to preview & PDF</span></div>
        <p class="muted small">Mix a header accent, a subtle watermark and a section style, or choose a professional theme to set all three. Change the accent in Colors. Decorations are pure shapes — they never affect ATS text, and Word exports leave them out.</p>
        <h4 class="deco-h">Professional themes</h4><p class="muted small">One click sets header + watermark + section style (and the accent color).</p>${gallery('themes')}
        <h4 class="deco-h">Header accent</h4><p class="muted small">Crisp corner and edge accents on every page.</p>${gallery('accents')}
        <h4 class="deco-h">Subtle watermark</h4><p class="muted small">Faint background shapes behind your content.</p>${gallery('watermarks')}
        <div style="max-width:240px;margin:10px 0 4px">${F.num('pdf.watermark_strength', 'Watermark strength (%)', 1)}</div>
        <h4 class="deco-h">Section style</h4><p class="muted small">How each section heading is decorated.</p>${gallery('sections')}`;
    }
  }

  // ---------------------------------------------------------------- editor
  function drawEditor() {
    const t = templates.find(x => x.id === selected);
    const dirty = JSON.stringify(style) !== JSON.stringify(saved);
    mount(el.querySelector('#editor'), html`<div class="editor-grid">
      <div class="card editor-card"><div class="card-head"><div><h3>Style · ${t.label}</h3><p class="muted small">${t.description}</p></div>
        <div class="row" style="gap:6px;flex:none">${selected === defId ? html`<span class="btn sm" style="pointer-events:none;color:var(--green)">${icon('check')}Default</span>` : html`<button class="btn sm" id="mkDef">Set default</button>`}
          ${customised || dirty ? html`<button class="btn sm ghost" id="resetBtn" title="Back to the original preset">${icon('refresh')}Reset</button>` : ''}
          <button class="btn sm primary" id="saveBtn" ${dirty ? '' : 'disabled'}>${icon('check')}Save style</button></div></div>
        <div class="style-tabs">${TABS.map(([k, l, i]) => html`<button type="button" data-tab="${k}" class="${tab === k ? 'on' : ''}">${icon(i)}${l}</button>`)}</div>
        <div class="card-body stack" id="tabBody">${tabBody()}</div></div>
      <div class="preview-col"><div class="card"><div class="card-head"><h3>Live preview</h3><span class="muted small" id="pvState">${dirty ? 'Unsaved changes' : 'Saved'}</span></div>
        <div class="pv-wrap" id="pvWrap"><iframe id="pvFrame" title="Live preview"></iframe></div></div></div></div>`);
    bindEditor();
    refreshPreview();
  }

  const refreshPreview = debounce(async () => {
    if (!isCurrent() || !style) return;
    try {
      const htmlDoc = await fetch(`/api/templates/${selected}/preview`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ style, profile_id: profileId || 0 }) }).then(r => r.text());
      const f = el.querySelector('#pvFrame'); if (f) f.srcdoc = htmlDoc;
      fitPreview();
    } catch {}
  }, 250);
  function fitPreview() {
    const wrap = el.querySelector('#pvWrap'), f = el.querySelector('#pvFrame');
    if (!wrap || !f) return;
    const w = style?.layout?.page_size === 'a4' ? 794 : 816, h = style?.layout?.page_size === 'a4' ? 1123 : 1056;
    const k = Math.min(1, wrap.clientWidth / w);
    f.style.width = w + 'px'; f.style.height = h + 'px'; f.style.transform = `scale(${k})`; wrap.style.height = Math.ceil(h * k) + 'px';
  }

  function changed(redraw = false) {
    const dirty = JSON.stringify(style) !== JSON.stringify(saved);
    const sb = el.querySelector('#saveBtn'); if (sb) sb.disabled = !dirty;
    const ps = el.querySelector('#pvState'); if (ps) ps.textContent = dirty ? 'Unsaved changes' : 'Saved';
    if (redraw) { const top = el.querySelector('#tabBody')?.scrollTop; mount(el.querySelector('#tabBody'), tabBody()); bindTab(); if (top) el.querySelector('#tabBody').scrollTop = top; }
    refreshPreview();
  }

  function bindTab() {
    const body = el.querySelector('#tabBody');
    body.querySelectorAll('[data-path]').forEach(inp => {
      const evt = inp.tagName === 'SELECT' || inp.type === 'checkbox' || inp.type === 'color' ? 'change' : 'input';
      inp.addEventListener(evt, () => {
        const path = inp.dataset.path, type = inp.dataset.type;
        let v = inp.value;
        if (type === 'num') { if (v === '' || isNaN(Number(v))) return; v = Number(v); }
        if (type === 'bool') v = inp.checked;
        if (type === 'hex') { v = v.trim().replace(/^#?/, '#'); if (!/^#[0-9a-fA-F]{6}$/.test(v)) return; const sw = inp.parentElement.querySelector('[data-type="swatch"]'); if (sw) sw.value = v; }
        if (type === 'swatch') { const hx = inp.parentElement.querySelector('[data-type="hex"]'); if (hx) hx.value = v.replace('#', ''); }
        if (path === 'headline.size') v = /^\s*auto\s*$/i.test(v) || v === '' ? 'auto' : (isNaN(Number(v)) ? 'auto' : Number(v));
        setp(style, path, v);
        // layout switches change which controls are shown
        changed(/\.layout$/.test(path) && path !== 'contact.layout' || path === 'colors.accent' && tab === 'pdf');
      });
    });
    body.querySelectorAll('[data-toggle]').forEach(b => b.onclick = () => { setp(style, b.dataset.toggle, !getp(style, b.dataset.toggle)); changed(true); });
    body.querySelectorAll('[data-align]').forEach(b => b.onclick = () => { setp(style, b.dataset.align, b.dataset.v); changed(true); });
    body.querySelectorAll('[data-pick]').forEach(b => b.onclick = () => { setp(style, b.dataset.pick, b.dataset.v); style.pdf.theme = 'none'; changed(true); });
    body.querySelectorAll('[data-theme]').forEach(b => b.onclick = () => {
      const t = opts.themes.find(x => x.id === b.dataset.theme);
      Object.assign(style.pdf, { theme: t.id, header_accent: t.header_accent, watermark: t.watermark, section_style: t.section_style });
      if (t.accent) style.colors.accent = t.accent;
      changed(true);
    });
    // sections tab
    const order = style.sections.order;
    body.querySelectorAll('[data-sec-title]').forEach(inp => inp.oninput = () => { order[Number(inp.dataset.secTitle)].title = inp.value; changed(); });
    body.querySelectorAll('[data-sec-eye]').forEach(b => b.onclick = () => { const s = order[Number(b.dataset.secEye)]; s.visible = !s.visible; changed(true); });
    body.querySelectorAll('[data-move]').forEach(b => b.onclick = () => { const [i, d] = b.dataset.move.split(':').map(Number); [order[i], order[i + d]] = [order[i + d], order[i]]; changed(true); });
    let dragFrom = null;
    body.querySelectorAll('.sec-row').forEach(row => {
      row.addEventListener('dragstart', () => { dragFrom = Number(row.dataset.i); row.classList.add('dragging'); });
      row.addEventListener('dragend', () => row.classList.remove('dragging'));
      row.addEventListener('dragover', (e) => { e.preventDefault(); row.classList.add('drop'); });
      row.addEventListener('dragleave', () => row.classList.remove('drop'));
      row.addEventListener('drop', (e) => { e.preventDefault(); const to = Number(row.dataset.i); if (dragFrom !== null && dragFrom !== to) { const [m] = order.splice(dragFrom, 1); order.splice(to, 0, m); changed(true); } });
    });
  }

  function bindEditor() {
    el.querySelectorAll('.style-tabs [data-tab]').forEach(b => b.onclick = () => { tab = b.dataset.tab; el.querySelectorAll('.style-tabs [data-tab]').forEach(x => x.classList.toggle('on', x === b)); mount(el.querySelector('#tabBody'), tabBody()); bindTab(); });
    bindTab();
    el.querySelector('#mkDef')?.addEventListener('click', () => setDefault(selected));
    el.querySelector('#saveBtn').onclick = async (e) => {
      const btn = e.currentTarget; busy(btn, true, 'Saving…');
      try {
        const r = await api.put(`/api/templates/${selected}/style`, { style });
        style = clone(r.style); saved = clone(r.style); customised = true;
        templates.find(t => t.id === selected).customised = true;
        toast('Style saved — new previews, PDFs and Word files use it'); drawGallery(); drawEditor();
      } catch (err) { toastError(err); busy(btn, false); }
    };
    el.querySelector('#resetBtn')?.addEventListener('click', async () => {
      if (!await confirmDialog({ title: 'Reset to the original preset?', message: 'Your saved changes to this template are removed.', confirm: 'Reset' })) return;
      const r = await api.del(`/api/templates/${selected}/style`);
      style = clone(r.style); saved = clone(r.style); customised = false;
      templates.find(t => t.id === selected).customised = false; toast('Template reset'); drawGallery(); drawEditor();
    });
  }

  async function setDefault(id) {
    try { await api.put('/api/settings', { user: { default_template_id: id } }); defId = id; state.settings = null; toast('Default template updated'); drawGallery(); templates.find(t => t.id === id)?.kind === 'style' ? drawEditor() : drawCustomInfo(templates.find(t => t.id === id)); }
    catch (e) { toastError(e); }
  }

  function upload() {
    const m = modal({ title: 'Upload an HTML template', size: 'wide', body: `<div class="stack">
      <p class="muted">A full HTML document with the placeholders <span class="mono">{{name}} {{contact}} {{summary}} {{experience}} {{skills}} {{education}} {{certifications}}</span> — or a single <span class="mono">{{resume}}</span>. Scripts are not allowed. Uploaded templates are used as-is (no style editor).</p>
      <label class="field">Name<input id="tLabel" placeholder="My template"></label>
      <label class="field">HTML file<input type="file" id="tFile" accept=".html,.htm"></label>
      <label class="field">…or paste HTML<textarea id="tHtml" class="code-input" rows="12"></textarea></label><div id="tErr"></div></div>`,
      foot: `<button class="btn" data-close>Cancel</button><button class="btn primary" data-save>${icon('upload')}Upload</button>` });
    m.$('#tFile').onchange = async (e) => { const f = e.target.files[0]; if (f) { m.$('#tHtml').value = await f.text(); if (!m.$('#tLabel').value) m.$('#tLabel').value = f.name.replace(/\.html?$/i, ''); } };
    m.$('[data-save]').onclick = async (e) => {
      const btn = e.currentTarget; busy(btn, true);
      try { const r = await api.post('/api/templates', { label: m.$('#tLabel').value, html: m.$('#tHtml').value }); m.close(); toast('Template uploaded'); templates = (await api.get('/api/templates')).templates; selectTemplate(r.id); }
      catch (err) { m.$('#tErr').innerHTML = `<div class="alert error">${icon('alert')}<div>${esc(err.message)}</div></div>`; busy(btn, false); }
    };
  }

  el.querySelector('#uploadBtn').onclick = upload;
  el.querySelector('#pvProfile').onchange = (e) => { profileId = e.target.value; drawGallery(); refreshPreview(); };
  const onResize = () => fitPreview();
  addEventListener('resize', onResize);
  drawGallery();
  await selectTemplate(selected);
  return () => removeEventListener('resize', onResize);
}
