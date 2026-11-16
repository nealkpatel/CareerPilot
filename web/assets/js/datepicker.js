// Calendar date-range picker: From/To boxes, month grid, pick a first day then a last day.
import { esc, icon } from './ui.js';

const pad = (n) => String(n).padStart(2, '0');
export const isoDate = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const parse = (s) => (s ? new Date(s + 'T00:00:00') : null);
export const niceDate = (s) => (s ? parse(s).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }) : '');
const WEEK = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'];

/**
 * Mount a range picker into `root`.
 * opts: { from, to, onChange(from, to), onClose() }
 */
export function rangePicker(root, opts) {
  let from = opts.from || '', to = opts.to || '';
  let hover = '';
  const start = parse(from) || new Date();
  let month = new Date(start.getFullYear(), start.getMonth(), 1);
  const today = isoDate(new Date());
  const tz = (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone; } catch { return ''; } })();

  function cells() {
    const first = new Date(month.getFullYear(), month.getMonth(), 1);
    const gridStart = new Date(first);
    gridStart.setDate(1 - first.getDay());
    return Array.from({ length: 42 }, (_, i) => { const d = new Date(gridStart); d.setDate(gridStart.getDate() + i); return d; });
  }

  function draw() {
    const picking = from && !to;               // waiting for the last day
    const end = to || (picking && hover) || '';
    const [lo, hi] = from && end ? [from, end].sort() : [from, from];
    const label = month.toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
    root.innerHTML = `
      <div class="dp">
        <div class="dp-fields">
          <div class="dp-field ${!picking ? 'on' : ''}"><span>From</span><b>${esc(niceDate(from)) || '—'}</b></div>
          <div class="dp-field ${picking ? 'on' : ''}"><span>To</span><b>${esc(niceDate(to)) || '—'}</b></div>
        </div>
        <div class="dp-head">
          <button type="button" class="icon-btn" data-nav="-1" aria-label="Previous month">${icon('back')}</button>
          <b>${esc(label)}</b>
          <button type="button" class="icon-btn" data-nav="1" aria-label="Next month">${icon('chevron')}</button>
        </div>
        <div class="dp-grid">
          ${WEEK.map(w => `<span class="dp-wd">${w}</span>`).join('')}
          ${cells().map(d => {
            const s = isoDate(d);
            const out = d.getMonth() !== month.getMonth();
            const inRange = lo && hi && s >= lo && s <= hi;
            const cls = ['dp-day', out ? 'out' : '', inRange ? 'in' : '', s === lo || s === hi ? 'edge' : '', s === today ? 'today' : ''].filter(Boolean).join(' ');
            return `<button type="button" class="${cls}" data-day="${s}" aria-label="${esc(d.toDateString())}" aria-pressed="${inRange ? 'true' : 'false'}">${d.getDate()}</button>`;
          }).join('')}
        </div>
        <p class="dp-hint">${picking ? 'Now choose the last day.' : 'Choose a first day, then a last day.'}${tz ? ` · ${esc(tz)}` : ''}</p>
        <div class="dp-foot">
          <button type="button" class="btn sm" data-act="today">Today</button>
          <div class="row" style="gap:4px">${from || to ? '<button type="button" class="btn sm ghost" data-act="clear">All dates</button>' : ''}<button type="button" class="btn sm ghost" data-act="close">Close</button></div>
        </div>
      </div>`;
    root.querySelectorAll('[data-nav]').forEach(b => b.onclick = () => { month = new Date(month.getFullYear(), month.getMonth() + Number(b.dataset.nav), 1); draw(); });
    root.querySelectorAll('[data-day]').forEach(b => {
      b.onclick = () => {
        const s = b.dataset.day;
        if (!from || to) { from = s; to = ''; hover = ''; }
        else { [from, to] = [from, s].sort(); opts.onChange?.(from, to); }
        const d = parse(s);
        if (d.getMonth() !== month.getMonth()) month = new Date(d.getFullYear(), d.getMonth(), 1);
        draw();
      };
      b.onmouseenter = () => { if (from && !to && hover !== b.dataset.day) { hover = b.dataset.day; paintHover(); } };
    });
    root.querySelector('[data-act="today"]').onclick = () => { from = to = today; month = new Date(); month.setDate(1); opts.onChange?.(from, to); draw(); };
    root.querySelector('[data-act="clear"]')?.addEventListener('click', () => { from = to = ''; opts.onChange?.('', ''); draw(); });
    root.querySelector('[data-act="close"]').onclick = () => opts.onClose?.();
  }

  // Lightweight hover preview without rebuilding the grid.
  function paintHover() {
    const [lo, hi] = [from, hover].sort();
    root.querySelectorAll('[data-day]').forEach(b => {
      const s = b.dataset.day;
      b.classList.toggle('in', s >= lo && s <= hi);
      b.classList.toggle('edge', s === lo || s === hi);
    });
  }

  draw();
  return { set(f, t) { from = f || ''; to = t || ''; const d = parse(from); if (d) month = new Date(d.getFullYear(), d.getMonth(), 1); draw(); } };
}
