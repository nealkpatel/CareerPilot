// Small, dependency-free SVG charts for CareerPilot dashboards.
// Colors are CSS custom properties (validated palette, separate light/dark steps in app.css).
// Labels are always inserted with textContent (series and category names are data).

const NS = 'http://www.w3.org/2000/svg';
const svgEl = (tag, attrs = {}) => { const n = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); return n; };
const div = (cls, text) => { const d = document.createElement('div'); if (cls) d.className = cls; if (text != null) d.textContent = text; return d; };
const fmt = (n) => Number(n || 0).toLocaleString();

function niceMax(v) {
  // whole-number ticks: the axis always has 4 equal integer steps for counts
  if (v <= 4) return 4;
  if (v <= 40) return Math.ceil(v / 4) * 4;
  const pow = 10 ** Math.floor(Math.log10(v));
  for (const m of [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (m * pow >= v) return m * pow;
  return 10 * pow;
}

// Monotone cubic interpolation (never overshoots - counts can't dip below zero)
function monotonePath(pts) {
  if (pts.length < 2) return pts.length ? `M${pts[0][0]},${pts[0][1]}` : '';
  const n = pts.length, dx = [], dy = [], m = [], t = [];
  for (let i = 0; i < n - 1; i++) { dx[i] = pts[i + 1][0] - pts[i][0]; dy[i] = pts[i + 1][1] - pts[i][1]; m[i] = dy[i] / dx[i]; }
  t[0] = m[0]; t[n - 1] = m[n - 2];
  for (let i = 1; i < n - 1; i++) t[i] = m[i - 1] * m[i] <= 0 ? 0 : (m[i - 1] + m[i]) / 2;
  for (let i = 0; i < n - 1; i++) {
    if (m[i] === 0) { t[i] = 0; t[i + 1] = 0; continue; }
    const a = t[i] / m[i], b = t[i + 1] / m[i], s = a * a + b * b;
    if (s > 9) { const k = 3 / Math.sqrt(s); t[i] = k * a * m[i]; t[i + 1] = k * b * m[i]; }
  }
  let d = `M${pts[0][0]},${pts[0][1]}`;
  for (let i = 0; i < n - 1; i++) {
    const h = dx[i] / 3;
    d += ` C${pts[i][0] + h},${pts[i][1] + h * t[i]} ${pts[i + 1][0] - h},${pts[i + 1][1] - h * t[i + 1]} ${pts[i + 1][0]},${pts[i + 1][1]}`;
  }
  return d;
}

/**
 * Line/area chart with crosshair tooltip.
 * opts: { labels: [display strings], tooltipTitles: [strings], series: [{label, color, values}], height }
 */
export function lineChart(host, opts) {
  host.classList.add('viz');
  host.replaceChildren();
  const legend = div('viz-legend');
  opts.series.forEach(s => { const it = div('viz-legend-item'); const k = div('viz-key-line'); k.style.background = s.color; it.append(k, document.createTextNode(s.label)); legend.append(it); });
  const plot = div('viz-plot');
  const tip = div('viz-tip'); tip.hidden = true;
  host.append(legend, plot);
  plot.append(tip);

  const draw = () => {
    const W = Math.max(280, plot.clientWidth), H = opts.height || 240;
    const pad = { l: 34, r: 92, t: 10, b: 28 };
    const n = opts.labels.length;
    const max = niceMax(Math.max(1, ...opts.series.flatMap(s => s.values)));
    const x = (i) => pad.l + (n === 1 ? (W - pad.l - pad.r) / 2 : (i / (n - 1)) * (W - pad.l - pad.r));
    const y = (v) => pad.t + (1 - v / max) * (H - pad.t - pad.b);
    const svg = svgEl('svg', { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: 'img', 'aria-label': opts.ariaLabel || 'Chart' });
    // grid + y ticks
    for (let i = 0; i <= 4; i++) {
      const v = (max / 4) * i, yy = y(v);
      svg.append(svgEl('line', { x1: pad.l, x2: W - pad.r, y1: yy, y2: yy, class: i ? 'viz-grid' : 'viz-axis' }));
      const t = svgEl('text', { x: pad.l - 8, y: yy + 4, 'text-anchor': 'end', class: 'viz-tick' }); t.textContent = fmt(Math.round(v * 10) / 10); svg.append(t);
    }
    // x ticks (about 6)
    const every = Math.max(1, Math.ceil(n / 6));
    opts.labels.forEach((lab, i) => {
      if (i % every && i !== n - 1) return;
      if (i !== n - 1 && n - 1 - i < every * 0.6) return;   // avoid crowding the last label
      const t = svgEl('text', { x: x(i), y: H - 8, 'text-anchor': i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle', class: 'viz-tick' }); t.textContent = lab; svg.append(t);
    });
    // series: soft area + 2px line + direct label at the end
    const ends = [];
    opts.series.forEach((s, si) => {
      const pts = s.values.map((v, i) => [x(i), y(v)]);
      const line = monotonePath(pts);
      const gid = `g${si}${Math.random().toString(36).slice(2, 7)}`;
      const grad = svgEl('linearGradient', { id: gid, x1: 0, x2: 0, y1: 0, y2: 1 });
      grad.append(svgEl('stop', { offset: '0%', 'stop-color': s.color, 'stop-opacity': si === 0 ? 0.22 : 0 }), svgEl('stop', { offset: '100%', 'stop-color': s.color, 'stop-opacity': 0 }));
      svg.append(grad);
      if (pts.length > 1) svg.append(svgEl('path', { d: `${line} L${pts[pts.length - 1][0]},${y(0)} L${pts[0][0]},${y(0)} Z`, fill: `url(#${gid})`, stroke: 'none' }));
      svg.append(svgEl('path', { d: line, fill: 'none', stroke: s.color, 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));
      if (pts.length === 1) svg.append(svgEl('circle', { cx: pts[0][0], cy: pts[0][1], r: 4, fill: s.color }));
      ends.push({ y: pts[pts.length - 1][1], label: s.label, total: s.values.reduce((a, b) => a + b, 0), color: s.color });
    });
    // direct labels: series name + period total, spaced so they never collide
    ends.sort((a, b) => a.y - b.y);
    for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 15) ends[i].y = ends[i - 1].y + 15;
    ends.forEach(e => {
      const g = svgEl('g');
      g.append(svgEl('line', { x1: W - pad.r + 6, x2: W - pad.r + 14, y1: e.y, y2: e.y, stroke: e.color, 'stroke-width': 2, 'stroke-linecap': 'round' }));
      const t = svgEl('text', { x: W - pad.r + 18, y: e.y + 4, class: 'viz-direct' }); t.textContent = `${e.label} ${fmt(e.total)}`;
      g.append(t); svg.append(g);
    });
    // crosshair layer
    const cross = svgEl('line', { y1: pad.t, y2: H - pad.b, class: 'viz-cross', visibility: 'hidden' });
    const dots = opts.series.map(s => { const c = svgEl('circle', { r: 4, fill: s.color, class: 'viz-dot', visibility: 'hidden' }); return c; });
    svg.append(cross, ...dots);
    const hit = svgEl('rect', { x: pad.l, y: pad.t, width: W - pad.l - pad.r, height: H - pad.t - pad.b, fill: 'transparent', tabindex: 0, 'aria-label': 'Chart data — use arrow keys' });
    svg.append(hit);
    let active = -1;
    const show = (i) => {
      active = Math.max(0, Math.min(n - 1, i));
      const xx = x(active);
      cross.setAttribute('x1', xx); cross.setAttribute('x2', xx); cross.setAttribute('visibility', 'visible');
      dots.forEach((d, si) => { d.setAttribute('cx', xx); d.setAttribute('cy', y(opts.series[si].values[active])); d.setAttribute('visibility', 'visible'); });
      tip.replaceChildren(div('viz-tip-title', (opts.tooltipTitles || opts.labels)[active]));
      opts.series.forEach(s => {
        const row = div('viz-tip-row'); const k = div('viz-key-line'); k.style.background = s.color;
        const v = document.createElement('b'); v.textContent = fmt(s.values[active]);
        row.append(k, v, document.createTextNode(' ' + s.label)); tip.append(row);
      });
      tip.hidden = false;
      const tw = tip.offsetWidth;
      tip.style.left = `${Math.min(W - tw - 4, Math.max(4, xx + 12 + tw > W - pad.r ? xx - tw - 12 : xx + 12))}px`;
      tip.style.top = `${pad.t + 4}px`;
    };
    const hide = () => { cross.setAttribute('visibility', 'hidden'); dots.forEach(d => d.setAttribute('visibility', 'hidden')); tip.hidden = true; active = -1; };
    hit.addEventListener('pointermove', (e) => { const r = svg.getBoundingClientRect(); const px = (e.clientX - r.left) * (W / r.width); show(Math.round(((px - pad.l) / (W - pad.l - pad.r)) * (n - 1))); });
    hit.addEventListener('pointerleave', hide);
    hit.addEventListener('focus', () => show(n - 1));
    hit.addEventListener('blur', hide);
    hit.addEventListener('keydown', (e) => { if (e.key === 'ArrowLeft') { show(active - 1); e.preventDefault(); } if (e.key === 'ArrowRight') { show(active + 1); e.preventDefault(); } });
    plot.querySelector('svg')?.remove();
    plot.prepend(svg);
  };
  draw();
  const ro = new ResizeObserver(() => draw());
  ro.observe(plot);
  return () => ro.disconnect();
}

/** Tiny single-series sparkline (no axes, no legend - the tile title names it). */
export function sparkline(values, color = 'var(--viz-1)', w = 120, h = 34) {
  const n = values.length;
  if (!n) return '';
  const max = Math.max(1, ...values);
  const pts = values.map((v, i) => [n === 1 ? w / 2 : (i / (n - 1)) * (w - 4) + 2, h - 3 - (v / max) * (h - 8)]);
  const line = monotonePath(pts);
  const last = pts[pts.length - 1];
  return `<svg class="viz-spark" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" aria-hidden="true">
    <path d="${line} L${last[0]},${h} L${pts[0][0]},${h} Z" fill="${color}" opacity=".12"/>
    <path d="${line}" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
    <circle cx="${last[0]}" cy="${last[1]}" r="3" fill="${color}"/></svg>`;
}

/**
 * Conversion funnel: horizontal bars (ordinal ramp) with stage-to-stage conversion.
 * stages: [{label, count, color}]; conversions: [null, pct, pct, ...] aligned with stages
 */
export function funnel(host, stages, conversions) {
  host.classList.add('viz');
  host.replaceChildren();
  const max = Math.max(1, stages[0]?.count || 0, ...stages.map(s => s.count));
  const tip = div('viz-tip'); tip.hidden = true;
  const wrap = div('viz-funnel');
  stages.forEach((s, i) => {
    if (i > 0) {
      const c = conversions[i];
      const conv = div('viz-conv');
      const arrow = div('viz-conv-arrow', '↓');
      conv.append(arrow, document.createTextNode(c == null ? '—' : `${c}% converted`));
      wrap.append(conv);
    }
    const row = div('viz-frow');
    row.tabIndex = 0;
    const lab = div('viz-flabel', s.label);
    const track = div('viz-ftrack');
    const bar = div('viz-fbar');
    bar.style.width = `${s.count ? Math.max(2.5, (s.count / max) * 100) : 0}%`;
    bar.style.background = s.color;
    track.append(bar);
    const val = div('viz-fval', fmt(s.count));
    const share = div('viz-fshare', stages[0].count ? `${Math.round((s.count / stages[0].count) * 100)}%` : '—');
    row.append(lab, track, val, share);
    const showTip = () => {
      tip.replaceChildren(div('viz-tip-title', s.label));
      const r1 = div('viz-tip-row'); const b1 = document.createElement('b'); b1.textContent = fmt(s.count); r1.append(b1, document.createTextNode(' applications'));
      const r2 = div('viz-tip-row'); const b2 = document.createElement('b'); b2.textContent = stages[0].count ? `${Math.round((s.count / stages[0].count) * 100)}%` : '—'; r2.append(b2, document.createTextNode(' of all added'));
      tip.append(r1, r2);
      if (i > 0) { const r3 = div('viz-tip-row'); const b3 = document.createElement('b'); b3.textContent = conversions[i] == null ? '—' : `${conversions[i]}%`; r3.append(b3, document.createTextNode(` from ${stages[i - 1].label}`)); tip.append(r3); }
      tip.hidden = false;
      tip.style.left = `${Math.min(row.offsetLeft + track.offsetLeft + bar.offsetWidth + 8, host.clientWidth - tip.offsetWidth - 4)}px`;
      tip.style.top = `${row.offsetTop - 6}px`;
    };
    row.addEventListener('pointerenter', showTip); row.addEventListener('focus', showTip);
    row.addEventListener('pointerleave', () => { tip.hidden = true; }); row.addEventListener('blur', () => { tip.hidden = true; });
    wrap.append(row);
  });
  host.append(wrap, tip);
}

/** 100% segmented bar with a legend (counts + share). items: [{label, count, color}] */
export function segmentBar(host, items) {
  host.classList.add('viz');
  host.replaceChildren();
  const total = items.reduce((a, b) => a + b.count, 0);
  const bar = div('viz-seg');
  const tip = div('viz-tip'); tip.hidden = true;
  const shown = items.filter(i => i.count > 0);
  if (!total) bar.append(div('viz-seg-empty'));
  shown.forEach(it => {
    const s = div('viz-seg-part'); s.style.flex = String(it.count); s.style.background = it.color; s.tabIndex = 0;
    const showTip = () => {
      tip.replaceChildren(div('viz-tip-title', it.label));
      const r = div('viz-tip-row'); const b = document.createElement('b'); b.textContent = fmt(it.count);
      r.append(b, document.createTextNode(` · ${Math.round((it.count / total) * 100)}%`)); tip.append(r);
      tip.hidden = false; tip.style.left = `${Math.min(s.offsetLeft, host.clientWidth - tip.offsetWidth - 4)}px`; tip.style.top = '22px';
    };
    s.addEventListener('pointerenter', showTip); s.addEventListener('focus', showTip);
    s.addEventListener('pointerleave', () => { tip.hidden = true; }); s.addEventListener('blur', () => { tip.hidden = true; });
    bar.append(s);
  });
  const legend = div('viz-seg-legend');
  items.forEach(it => {
    const row = div('viz-seg-li' + (it.count ? '' : ' zero'));
    const k = div('viz-key-box'); k.style.background = it.color;
    const name = div('viz-seg-name', it.label);
    const v = div('viz-seg-val', total ? `${fmt(it.count)} · ${Math.round((it.count / total) * 100)}%` : '0');
    row.append(k, name, v); legend.append(row);
  });
  host.append(bar, tip, legend);
}

/** Accessible table view of a series chart. */
export function seriesTable(host, titles, series) {
  host.replaceChildren();
  const table = document.createElement('table'); table.className = 'table viz-table';
  const head = document.createElement('tr');
  ['Date', ...series.map(s => s.label)].forEach(h => { const th = document.createElement('th'); th.textContent = h; head.append(th); });
  const thead = document.createElement('thead'); thead.append(head);
  const tbody = document.createElement('tbody');
  titles.forEach((t, i) => {
    if (!series.some(s => s.values[i])) return;
    const tr = document.createElement('tr');
    [t, ...series.map(s => fmt(s.values[i]))].forEach(c => { const td = document.createElement('td'); td.textContent = c; tr.append(td); });
    tbody.append(tr);
  });
  if (!tbody.children.length) { const tr = document.createElement('tr'); const td = document.createElement('td'); td.colSpan = series.length + 1; td.textContent = 'No activity in this period.'; td.className = 'muted'; tr.append(td); tbody.append(tr); }
  table.append(thead, tbody);
  const wrap = div('table-wrap'); wrap.append(table);
  host.append(wrap);
}
