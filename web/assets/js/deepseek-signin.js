// Connect your own DeepSeek account from wherever you are: CareerPilot opens a plain Chrome window on the server
// computer, shows you its pixels a few times a second, and replays your clicks, drags, wheel and typing there as real
// mouse and keyboard input. Nothing is attached to Chrome, so DeepSeek's sign-in and captcha behave as they normally do.
// When you click Done (or the window is closed), the server checks the saved sign-in and tells you whether it worked.
import { api } from './api.js';
import { html, icon, mount, modal, toast } from './ui.js';

const FRAME_MS = 250;          // pause between frame requests (the server answers 204 while nothing changed)
const KEY_BATCH_MS = 120;      // keystrokes typed within this window travel together
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const NAMED_KEYS = new Set(['Enter', 'Backspace', 'Tab', 'Escape', 'Delete', 'Home', 'End', 'PageUp', 'PageDown', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown']);

export function openDeepSeekSignIn({ onClose } = {}) {
  const m = modal({
    title: 'Connect your DeepSeek account',
    size: 'wide',
    body: `<div class="stack" id="dsBody"><div class="row"><span class="spinner"></span><span>Opening your DeepSeek sign-in window on the server…</span></div></div>`,
    foot: `<button class="btn" id="dsCancel">Cancel</button><button class="btn primary" id="dsDone" disabled>Done</button>`,
  });
  const body = m.$('#dsBody'), done = m.$('#dsDone'), cancel = m.$('#dsCancel');
  let phase = 'opening';   // opening → live → checking → result
  let hash = '', frameW = 0, frameH = 0, imgUrl = null, failures = 0, timer = null;

  // ------------------------------------------------------------------ views
  function show(state, text = '') {
    const box = {
      checking: html`<div class="row"><span class="spinner"></span><span>Checking your DeepSeek sign-in…</span></div>`,
      ok: html`<div class="alert ok">${icon('checkCircle')}<div><b>You're signed in to DeepSeek</b>Your requests will use this account.</div></div>`,
      failed: html`<div class="alert warn">${icon('alert')}<div><b>DeepSeek sign-in was not finished</b>${text}</div></div>`,
      error: html`<div class="alert error">${icon('alert')}<div><b>Could not open the sign-in window</b>${text}</div></div>`,
    }[state];
    mount(body, box);
  }
  function result(ok, text) {
    phase = 'result';
    show(ok ? 'ok' : 'failed', text);
    done.disabled = false; done.textContent = 'Close'; cancel.hidden = true;
    if (ok) toast('DeepSeek connected — your requests will use your account');
  }
  function showLive() {
    mount(body, html`<div class="stack tight" id="dsStack">
      <p class="muted small" style="margin:0">Your DeepSeek window on the CareerPilot server, live. Sign in just as you would in a browser — click into it, type, solve any check DeepSeek shows — and click <b>Done</b> when the chat page appears.</p>
      <div id="dsLock"></div>
      <div class="ds-live" id="dsLive" tabindex="0" aria-label="Live DeepSeek sign-in window"><img id="dsImg" alt="" draggable="false"><div class="ds-live-hint" id="dsHint"><span class="spinner"></span>Connecting to the window…</div></div>
      <form class="row" id="dsTypeForm"><input id="dsText" autocomplete="off" placeholder="Or type here and press Enter to send the text to the window — handy for pasting a password"><button class="btn sm" type="submit">${icon('send')}Send</button></form>
      <div class="muted small" id="dsStatus">Tip: click a field in the window first, then type. Esc, Tab, Backspace, arrows and Ctrl+A work; paste with Ctrl+V.</div>
    </div>`);
    wireLive(); fit();
  }
  // Size the picture so the whole dialog fits the browser window: nothing to scroll while signing in.
  function fit() {
    const live = m.$('#dsLive'), img = m.$('#dsImg'), stack = m.$('#dsStack'); if (!live || !img || !stack) return;
    const h = (sel) => m.$(sel)?.offsetHeight || 0;
    let used = 0; for (const el of stack.children) if (el !== live) used += el.offsetHeight;
    const avail = innerHeight - 40 - h('.modal-head') - h('.modal-foot') - 44 - used - 8 * (stack.children.length - 1) - 6;
    img.style.maxHeight = Math.max(220, avail) + 'px';
  }
  addEventListener('resize', fit);
  const status = (t) => { const el = m.$('#dsStatus'); if (el) el.textContent = t; };
  let lockedShown = null;
  function lockBanner(locked) {
    if (locked === lockedShown) return;
    lockedShown = locked;
    const el = m.$('#dsLock'); if (!el) return;
    mount(el, locked ? html`<div class="alert warn">${icon('alert')}<div><b>The server computer's screen is locked</b>You can watch, but clicks and typing won't arrive until someone unlocks it there (on a VPS: leave Remote Desktop with deploy\\disconnect-rdp.cmd instead of locking).</div></div>` : html``);
    fit();
  }

  // ------------------------------------------------------------------ frames
  async function frames() {
    const img = m.$('#dsImg'), hint = m.$('#dsHint');
    while (phase === 'live') {
      try {
        const res = await fetch(`/api/engine/signin/frame?h=${encodeURIComponent(hash)}`, { credentials: 'same-origin' });
        if (res.status === 200 || res.status === 204) lockBanner(res.headers.get('X-Server-Locked') === '1');
        if (res.status === 200) {
          hash = res.headers.get('X-Frame-Hash') || '';
          frameW = +res.headers.get('X-Frame-Width') || frameW; frameH = +res.headers.get('X-Frame-Height') || frameH;
          const url = URL.createObjectURL(await res.blob());
          img.src = url; if (imgUrl) URL.revokeObjectURL(imgUrl); imgUrl = url;
          if (hint) hint.hidden = true;
          failures = 0;
        } else if (res.status === 204) {
          failures = 0;
        } else if (res.status === 404) {
          // The window is gone: closed on the server, or the time limit passed. The server already started the check.
          startCheck(); return;
        } else {
          const d = await res.json().catch(() => ({}));
          status(d.detail || `The live view failed (${res.status}).`); failures++;
        }
      } catch { failures++; }
      await sleep(failures ? Math.min(3000, 400 * failures) : FRAME_MS);
    }
  }

  // ------------------------------------------------------------------ input: one request at a time, in order
  let chain = Promise.resolve(), keyBuf = [], keyTimer = null;
  function send(actions) {
    chain = chain.then(() => api.post('/api/engine/signin/input', { actions })).then(r => {
      if (r && r.focused === false) status('The window could not take the keyboard — click inside the view once, then type.');
    }, err => {
      if (err.status === 404) { startCheck(); return; }
      status(err.message || 'Input failed.');
    });
    return chain;
  }
  function flushKeys() {
    clearTimeout(keyTimer); keyTimer = null;
    if (!keyBuf.length) return;
    const batch = [];
    for (const a of keyBuf) {   // merge consecutive typing into one action
      const last = batch[batch.length - 1];
      if (a.type === 'type' && last?.type === 'type') last.text += a.text; else batch.push({ ...a });
    }
    keyBuf = [];
    send(batch);
  }
  function queueKey(a) { keyBuf.push(a); clearTimeout(keyTimer); keyTimer = setTimeout(flushKeys, KEY_BATCH_MS); }

  function wireLive() {
    const live = m.$('#dsLive'), img = m.$('#dsImg'), form = m.$('#dsTypeForm'), text = m.$('#dsText');
    // browser pixels → pixels of the real window; one rectangle per gesture, so a layout shift mid-gesture can't skew it
    const pt = (e, r) => [Math.round((e.clientX - r.left) / r.width * frameW), Math.round((e.clientY - r.top) / r.height * frameH)];
    let drag = null, wheel = 0, wheelAt = null, wheelTimer = null;
    live.onpointerdown = (e) => {
      if (!frameW || e.button > 2) return;
      e.preventDefault(); live.focus({ preventScroll: true }); live.setPointerCapture(e.pointerId);
      const rect = img.getBoundingClientRect();
      const [x, y] = pt(e, rect);
      drag = { button: e.button === 2 ? 'right' : 'left', points: [[x, y, 0]], t0: performance.now(), last: 0, rect };
    };
    live.onpointermove = (e) => {
      if (!drag) return;
      const t = performance.now() - drag.t0;
      if (t - drag.last < 20) return;
      drag.last = t;
      const [x, y] = pt(e, drag.rect); drag.points.push([x, y, Math.round(t)]);
    };
    live.onpointerup = (e) => {
      if (!drag) return;
      e.preventDefault();
      const d = drag; drag = null;
      const [x, y] = pt(e, d.rect); d.points.push([x, y, Math.round(performance.now() - d.t0)]);
      const [sx, sy] = d.points[0];
      flushKeys();
      if (Math.hypot(x - sx, y - sy) > 3) send([{ type: 'drag', button: d.button, points: d.points }]);
      else send([{ type: 'click', button: d.button, x: sx, y: sy, count: e.detail >= 2 ? 2 : 1 }]);
    };
    live.onpointercancel = () => { drag = null; };
    live.oncontextmenu = (e) => e.preventDefault();
    live.onwheel = (e) => {
      e.preventDefault();
      wheel += e.deltaY; wheelAt = pt(e, img.getBoundingClientRect());
      clearTimeout(wheelTimer);
      wheelTimer = setTimeout(() => { const dy = wheel; wheel = 0; send([{ type: 'scroll', x: wheelAt[0], y: wheelAt[1], dy }]); }, 120);
    };
    live.onkeydown = (e) => {
      e.stopPropagation();   // keep Esc and the rest for the window, not the dialog
      if (e.ctrlKey && e.key.toLowerCase() === 'v') return;   // the paste event below carries the text
      if (e.key.length === 1 && !e.ctrlKey && !e.altKey && !e.metaKey) { e.preventDefault(); queueKey({ type: 'type', text: e.key }); }
      else if (NAMED_KEYS.has(e.key) || (e.ctrlKey && /^[a-z]$/i.test(e.key))) { e.preventDefault(); queueKey({ type: 'key', key: e.key, ctrl: e.ctrlKey, shift: e.shiftKey, alt: e.altKey }); }
    };
    live.onpaste = (e) => { const t = e.clipboardData?.getData('text'); if (t) { e.preventDefault(); queueKey({ type: 'type', text: t }); } };
    form.onsubmit = (e) => {
      e.preventDefault();
      const t = text.value; if (!t) return;
      send([{ type: 'type', text: t }]); text.value = ''; live.focus();
      status('Sent. Press Enter in the window (or click Send again) if a form needs it.');
    };
  }

  // ------------------------------------------------------------------ finishing
  async function check(tries = 0) {
    if (phase !== 'checking') return;
    try {
      const e = await api.get('/api/engine');
      if (!e.verifying && e.status !== 'starting') {
        if (e.connected && ['ready', 'idle', 'busy'].includes(e.status)) return result(true);
        if (e.status === 'offline') return result(false, 'DeepSeek could not be reached to check the sign-in. CareerPilot retries automatically.');
        return result(false, 'Click Connect DeepSeek to try again, and wait for the chat page before clicking Done.');
      }
    } catch { /* keep trying */ }
    if (tries > 60) return result(false, 'The check is taking long. See Settings → My DeepSeek account for the result.');
    timer = setTimeout(() => check(tries + 1), 2000);
  }
  function startCheck() {
    if (phase !== 'live') return;
    phase = 'checking'; done.disabled = true; cancel.hidden = true;
    if (imgUrl) { URL.revokeObjectURL(imgUrl); imgUrl = null; }
    show('checking'); check();
  }

  done.onclick = async () => {
    if (phase !== 'live') return m.close();
    clearTimeout(keyTimer); done.disabled = true;
    try { await chain; await api.post('/api/engine/signin/done'); } catch { /* the window may already be closed */ }
    startCheck();
  };
  cancel.onclick = async () => {
    if (phase === 'live') { phase = 'cancelled'; try { await api.post('/api/engine/signin/cancel'); } catch { /* ignore */ } toast('DeepSeek sign-in cancelled'); }
    m.close();
  };
  // While the window is live, Esc and clicks beside the dialog belong to the sign-in, not to closing it.
  m.el.addEventListener('keydown', (e) => { if (e.key === 'Escape' && phase === 'live') e.stopPropagation(); });
  m.el.addEventListener('mousedown', (e) => { if (e.target === m.el && phase === 'live') e.stopImmediatePropagation(); }, true);
  // Closing the dialog any other way counts as Done: the window is closed and the sign-in is checked by the server.
  new MutationObserver((_, obs) => {
    if (document.body.contains(m.el)) return;
    obs.disconnect(); clearTimeout(timer); clearTimeout(keyTimer); removeEventListener('resize', fit);
    if (imgUrl) URL.revokeObjectURL(imgUrl);
    if (phase === 'live') { phase = 'closed'; api.post('/api/engine/signin/done').catch(() => {}); }
    else if (phase === 'checking') phase = 'closed';
    onClose?.();
  }).observe(document.body, { childList: true });

  api.post('/api/engine/connect').then(() => {
    phase = 'live'; done.disabled = false;
    showLive(); frames();
  }).catch(err => { phase = 'result'; show('error', err.message || 'Please try again.'); done.disabled = false; done.textContent = 'Close'; cancel.hidden = true; });
  return m;
}
