// Chat-style "Get answers" panel: ask application questions, answers come back as chat bubbles.
import { api } from './api.js';
import { html, icon, mount, logo, modal, toast, toastError, copyText } from './ui.js';
import { refreshEngine, connectDeepSeek } from './app.js';

const SUGGESTIONS = [
  'Why do you want to work at {company}?',
  'Why are you a good fit for this role?',
  'Tell us about a challenging project you led.',
  'What are your salary expectations?',
  'Describe your experience with our tech stack.',
  'When can you start?',
];

const words = (t) => (t.trim().match(/\S+/g) || []).length;

export function openAnswersChat(appId, { onChange } = {}) {
  let data = null, timer = null, closed = false, sending = false, lastCount = -1;

  const m = modal({
    drawer: true, title: 'Get answers',
    body: `<div class="chat"><div class="chat-head" id="chHead"></div><div id="chAlert"></div><div class="chat-log" id="chLog" aria-live="polite"><div class="chat-loading"><span class="spinner"></span></div></div>
      <form class="chat-composer" id="chForm"><textarea id="chInput" rows="1" placeholder="Ask an application question…" aria-label="Question"></textarea>
      <button class="btn primary icon" id="chSend" type="submit" aria-label="Send" title="Send">${icon('send')}</button></form>
      <p class="chat-hint">Enter to send · Shift+Enter for a new line · several questions? put each on its own line</p></div>`,
  });
  m.el.querySelector('.drawer').classList.add('chat-drawer');
  const log = m.$('#chLog'), input = m.$('#chInput'), form = m.$('#chForm');

  const stop = () => { closed = true; clearTimeout(timer); };
  new MutationObserver((_, obs) => { if (!document.body.contains(m.el)) { obs.disconnect(); stop(); onChange?.(); } }).observe(document.body, { childList: true });

  const grow = () => { input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight, 160) + 'px'; };
  input.addEventListener('input', grow);
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); form.requestSubmit(); } });
  form.onsubmit = (e) => { e.preventDefault(); ask(input.value); };

  async function ask(text) {
    text = (text || '').trim();
    if (!text || sending || !data?.generation) return;
    sending = true;
    input.value = ''; grow();
    // optimistic bubble while the request is queued
    data.pending.push({ id: 'tmp', questions: [text], status: 'queued' });
    draw(true);
    try { data = await api.post(`/api/applications/${appId}/answers`, { questions: text }); refreshEngine?.(); }
    catch (err) { toastError(err); data.pending = data.pending.filter(p => p.id !== 'tmp'); input.value = text; grow(); }
    sending = false;
    draw(true); schedule();
  }

  const fill = (s) => s.replace('{company}', data?.application.company || 'your company');

  function bubbleQ(q) { return html`<div class="msg me"><div class="bubble">${q}</div></div>`; }

  function bubbleA(item, i) {
    return html`<div class="msg ai"><div class="avatar">${icon('sparkles')}</div><div class="bubble-wrap">
      <div class="bubble">${item.a}</div>
      <div class="msg-meta"><span>${words(item.a)} words</span><span>résumé v${item.version}</span>
        <button class="msg-act" data-copy="${i}" title="Copy answer">${icon('copy')}Copy</button>
        <button class="msg-act" data-again="${i}" title="Ask this question again">${icon('refresh')}Rewrite</button></div></div></div>`;
  }

  const queued = () => html`<div class="msg ai"><div class="avatar wait">${icon('clock')}</div><div class="bubble queued">Waiting for your DeepSeek — this answer is written automatically once it's connected.</div></div>`;
  const typing = () => data?.service && !data.service.available ? queued() : html`<div class="msg ai"><div class="avatar">${icon('sparkles')}</div><div class="bubble typing" aria-label="Writing an answer"><i></i><i></i><i></i></div></div>`;

  function draw(scroll = false) {
    if (!data) return;
    const a = data.application;
    mount(m.$('#chHead'), html`${logo(a.company, 34, { url: a.url })}<div class="grow" style="min-width:0"><div class="co-name truncate">${a.company}</div><div class="co-sub truncate">${a.title}</div></div>
      ${data.generation ? html`<span class="chip-soft" title="Answers are written from this résumé version">${icon('file', 'xs')}v${data.generation.version}</span>` : ''}`);

    const svc = data.service || { available: true };
    mount(m.$('#chAlert'), svc.available ? '' : html`<div class="chat-alert" role="alert">${icon('alert')}<div><b>Your DeepSeek isn't available</b>
      <span>${svc.message}</span><button class="btn sm primary" id="chConnect" style="margin-top:6px">Connect DeepSeek</button>${svc.since ? html`<span class="since">Since ${new Date(svc.since).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}</span>` : ''}</div></div>`);
    m.$('#chConnect')?.addEventListener('click', () => connectDeepSeek());
    const busyNow = data.pending.length > 0;
    let body;
    if (!data.generation) {
      body = html`<div class="chat-empty"><div class="ico">${icon('file')}</div><h3>${data.generating ? 'Résumé is being written…' : 'Generate the résumé first'}</h3>
        <p>Answers are written from the tailored résumé and cover letter, so everything you send stays consistent.</p>
        ${data.generating ? html`<span class="spinner"></span>` : html`<a class="btn primary" href="#/applications/${a.id}">${icon('sparkles')}Open application</a>`}</div>`;
    } else if (!data.messages.length && !busyNow && !data.failed.length) {
      body = html`<div class="chat-empty"><div class="ico">${icon('chat')}</div><h3>Ask anything the application form asks</h3>
        <p>Paste questions from the application and get answers tailored to ${a.company}, written from résumé v${data.generation.version}.</p>
        <div class="chat-suggest">${SUGGESTIONS.map((s, i) => html`<button type="button" data-sug="${i}">${fill(s)}</button>`)}</div></div>`;
    } else {
      body = html`${data.messages.map((it, i) => html`${it.q ? bubbleQ(it.q) : ''}${bubbleA(it, i)}`)}
        ${data.failed.map(f => html`${f.questions.map(bubbleQ)}<div class="msg ai"><div class="avatar err">${icon('alert')}</div><div class="bubble-wrap"><div class="bubble err">${f.error || 'Could not write this answer.'}</div>
          <div class="msg-meta"><button class="msg-act" data-retry="${f.id}">${icon('refresh')}Try again</button></div></div></div>`)}
        ${data.pending.map(p => html`${p.questions.map(bubbleQ)}${typing()}`)}
        ${!busyNow ? html`<div class="chat-suggest inline">${SUGGESTIONS.filter(s => !data.messages.some(x => x.q === fill(s))).slice(0, 3).map(s => html`<button type="button" data-sugt="${fill(s)}">${fill(s)}</button>`)}</div>` : ''}`;
    }
    const nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 80;
    mount(log, body);
    if (scroll || nearBottom || data.messages.length !== lastCount) log.scrollTop = log.scrollHeight;
    lastCount = data.messages.length;

    input.disabled = !data.generation;
    m.$('#chSend').disabled = !data.generation || sending;
    log.querySelectorAll('[data-sug]').forEach(b => b.onclick = () => ask(fill(SUGGESTIONS[Number(b.dataset.sug)])));
    log.querySelectorAll('[data-sugt]').forEach(b => b.onclick = () => ask(b.dataset.sugt));
    log.querySelectorAll('[data-copy]').forEach(b => b.onclick = () => copyText(data.messages[Number(b.dataset.copy)].a, 'Answer copied'));
    log.querySelectorAll('[data-again]').forEach(b => b.onclick = () => ask(data.messages[Number(b.dataset.again)].q));
    log.querySelectorAll('[data-retry]').forEach(b => b.onclick = () => { const f = data.failed.find(x => String(x.id) === b.dataset.retry); data.failed = []; ask(f.questions.join('\n')); });
  }

  function schedule() {
    clearTimeout(timer);
    if (!closed && data && (data.pending.length || data.generating || data.service?.available === false)) timer = setTimeout(load, data.service?.available === false ? 4000 : 2000);
  }

  async function load(first = false) {
    try {
      const before = data?.messages.length ?? 0;
      const wasDown = data?.service?.available === false;
      data = await api.get(`/api/applications/${appId}/answers`);
      draw(first);
      if (wasDown && data.service?.available) toast('Your DeepSeek is connected — writing your answers now');
      if (!first && data.messages.length > before) toast(data.messages.length - before === 1 ? 'Answer ready' : `${data.messages.length - before} answers ready`);
    } catch (e) { if (first) { toastError(e); m.close(); return; } }
    schedule();
  }

  load(true).then(() => { if (data?.generation) input.focus(); });
  return m;
}
