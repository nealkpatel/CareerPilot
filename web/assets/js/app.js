import { api } from './api.js';
import { html, raw, icon, mount, $, $$, esc, toast, toastError, busy, initials, formData } from './ui.js';
import { openDeepSeekSignIn } from './deepseek-signin.js';

export const state = { user: null, engine: null, settings: null, publicUrl: '' };
// The address people use for this site (set on the server when it's published through Netlify), else this page's origin.
export const siteOrigin = () => state.publicUrl || location.origin;

const ROUTES = [
  [/^\/?$/, 'dashboard'],
  [/^\/applications\/new$/, 'application-form'],
  [/^\/applications\/(\d+)\/edit$/, 'application-form'],
  [/^\/applications\/(\d+)$/, 'application'],
  [/^\/applications$/, 'applications'],
  [/^\/jobs$/, 'jobs'],
  [/^\/profiles\/new$/, 'profile-form'],
  [/^\/profiles\/(\d+)$/, 'profile-form'],
  [/^\/profiles$/, 'profiles'],
  [/^\/prompts$/, 'prompts'],
  [/^\/templates$/, 'templates'],
  [/^\/settings$/, 'settings'],
  [/^\/team$/, 'team'],
  [/^\/admin\/users$/, 'admin-users'],
  [/^\/admin\/sources$/, 'admin-sources'],
  [/^\/admin\/audit$/, 'admin-audit'],
];

// Master manages the platform (no bidding). Admins bid and manage their own bidders.
// Bidders bid with the profiles their admin assigns.
const NAV = {
  master: [
    ['Platform', [['/', 'home', 'Overview'], ['/admin/users', 'users', 'Users'], ['/jobs', 'globe', 'Job Pool'], ['/admin/sources', 'database', 'Job sources']]],
    ['System', [['/settings', 'settings', 'Settings'], ['/admin/audit', 'activity', 'Audit log']]],
  ],
  admin: [
    ['Workspace', [['/', 'home', 'Dashboard'], ['/applications', 'briefcase', 'Applications'], ['/jobs', 'globe', 'Job Pool']]],
    ['Manage', [['/team', 'users', 'My bidders'], ['/profiles', 'user', 'Profiles'], ['/prompts', 'message', 'Prompts'], ['/templates', 'layout', 'Templates'], ['/settings', 'settings', 'Settings']]],
  ],
  bidder: [
    ['Workspace', [['/', 'home', 'Dashboard'], ['/applications', 'briefcase', 'Applications'], ['/jobs', 'globe', 'Job Pool']]],
    ['Setup', [['/profiles', 'user', 'My profiles'], ['/templates', 'layout', 'Templates'], ['/settings', 'settings', 'Settings']]],
  ],
};
// Pages each role may open (anything else redirects home).
const ACCESS = {
  master: ['platform', 'admin-users', 'admin-sources', 'admin-audit', 'jobs', 'settings'],
  admin: ['dashboard', 'applications', 'application', 'application-form', 'jobs', 'team', 'profiles', 'profile-form', 'prompts', 'templates', 'settings'],
  bidder: ['dashboard', 'applications', 'application', 'application-form', 'jobs', 'profiles', 'profile-form', 'templates', 'settings'],
};
export const ROLE_LABEL = { master: 'Platform master', admin: 'Admin', bidder: 'Bidder' };
// Every admin and bidder connects their own DeepSeek account.
export const provider = () => state.engine?.provider || 'DeepSeek';
export const providerUrl = () => state.engine?.provider_url || 'chat.deepseek.com';

export const go = (path) => { location.hash = '#' + path; };
let cleanup = null;
let enginePoll = null;

function parseHash() {
  const h = location.hash.replace(/^#/, '') || '/';
  const [path, qs] = h.split('?');
  return { path, query: Object.fromEntries(new URLSearchParams(qs || '')) };
}

// ------------------------------------------------------------------ auth screens
const ROUTE_SVG = `<svg class="route" viewBox="0 0 400 400" fill="none" stroke="white" stroke-width="2"><circle cx="200" cy="200" r="190"/><circle cx="200" cy="200" r="130"/><circle cx="200" cy="200" r="70"/><path d="M200 10v380M10 200h380" stroke-dasharray="4 8"/><path d="M80 300 C 150 250, 180 150, 320 90" stroke-width="3"/><circle cx="80" cy="300" r="7" fill="white"/><circle cx="320" cy="90" r="7" fill="white"/></svg>`;

function authHero() {
  return html`<div class="auth-hero">${raw(ROUTE_SVG)}
    <div class="brand" style="padding:0"><div class="brand-mark">${icon('compass')}</div><div><div class="brand-name" style="color:#fff">CareerPilot</div></div></div>
    <h1>Helping professionals navigate their careers.</h1>
    <p class="lead">Tailor a résumé, cover letter and application answers to every role in minutes — then track each application from saved to signed offer.</p>
    <ul>
      <li><span>${icon('sparkles')}</span>AI tailoring for every role — résumé, cover letter and answers</li>
      <li><span>${icon('link')}</span>Paste a job link and the posting is scraped for you</li>
      <li><span>${icon('globe')}</span>A job pool fed by free public job boards</li>
      <li><span>${icon('activity')}</span>A pipeline view from applied to offer</li>
    </ul>
  </div>`;
}

async function renderAuth(hasUsers, registration) {
  const app = $('#app');
  let mode = hasUsers ? 'login' : 'register';
  const draw = () => {
    const first = !hasUsers;
    mount(app, html`<div class="auth">${authHero()}<div class="auth-form">
      <form id="authForm" class="stack" novalidate>
        <div>
          <h2>${mode === 'login' ? 'Welcome back' : first ? 'Set up CareerPilot' : 'Create your account'}</h2>
          <p class="muted" style="margin-top:6px">${mode === 'login' ? 'Sign in to continue your search.' : first ? 'The first account becomes the platform master.' : 'Create an admin account. Bidders get their accounts from their admin.'}</p>
        </div>
        <div id="authErr"></div>
        ${mode === 'register' ? html`<label class="field">Full name<input name="name" autocomplete="name" required placeholder="Alex Morgan"></label>` : ''}
        <label class="field">Email<input name="email" type="email" autocomplete="email" required placeholder="you@example.com"></label>
        <label class="field">Password
          <div style="position:relative"><input name="password" type="password" autocomplete="${mode === 'login' ? 'current-password' : 'new-password'}" required minlength="8" placeholder="${mode === 'login' ? '••••••••' : 'At least 8 characters'}">
          <button type="button" class="btn ghost sm icon" id="pwToggle" style="position:absolute;right:4px;top:4px" aria-label="Show password">${icon('eye')}</button></div>
        </label>
        <button class="btn primary lg" type="submit">${mode === 'login' ? 'Sign in' : 'Create account'}</button>
        ${hasUsers && registration !== 'closed' ? html`<p class="muted" style="text-align:center">${mode === 'login' ? 'New to CareerPilot?' : 'Already have an account?'}
          <a href="#" id="switchMode">${mode === 'login' ? 'Create an account' : 'Sign in'}</a></p>` : ''}
      </form></div></div>`);
    $('#pwToggle').onclick = () => { const i = $('input[name=password]'); i.type = i.type === 'password' ? 'text' : 'password'; };
    $('#switchMode')?.addEventListener('click', (e) => { e.preventDefault(); mode = mode === 'login' ? 'register' : 'login'; draw(); });
    $('#authForm').onsubmit = async (e) => {
      e.preventDefault();
      const btn = e.target.querySelector('button[type=submit]');
      busy(btn, true);
      try {
        const res = await api.post(`/api/auth/${mode === 'login' ? 'login' : 'register'}`, formData(e.target));
        if (res.pending) {
          mount($('#authErr'), html`<div class="alert ok">${icon('checkCircle')}<div><b>Account created</b>The platform master needs to approve it before you can sign in.</div></div>`);
          busy(btn, false);
          return;
        }
        state.user = res.user;
        boot();
      } catch (err) {
        mount($('#authErr'), html`<div class="alert error">${icon('alert')}<div>${err.message}</div></div>`);
        busy(btn, false);
      }
    };
    $('input', $('#authForm')).focus();
  };
  draw();
}

// ------------------------------------------------------------------ shell
function shell() {
  const u = state.user;
  const groups = NAV[u.role] || NAV.bidder;
  const bids = u.role !== 'master';
  mount($('#app'), html`<div class="shell">
    <aside class="sidebar" id="sidebar">
      <a class="brand" href="#/" style="text-decoration:none;color:inherit"><div class="brand-mark">${icon('compass')}</div>
        <div><div class="brand-name">CareerPilot</div><div class="brand-tag">Navigate your career</div></div></a>
      ${groups.map(([label, items]) => html`<div class="nav-label">${label}</div><nav class="nav">
        ${items.map(([href, ico, text]) => html`<a href="#${href}" data-href="${href}">${icon(ico)}<span>${text}</span></a>`)}</nav>`)}
    </aside>
    <div class="main">
      <header class="topbar" id="topbar">
        <button class="btn ghost icon menu-btn" id="menuBtn" aria-label="Menu">${icon('menu')}</button>
        ${bids ? html`<form class="search-box" id="globalSearch"><span>${icon('search')}</span><input name="q" placeholder="Search applications…" autocomplete="off"></form>` : ''}
        <div class="grow"></div>
        ${bids ? html`<a class="btn primary" href="#/applications/new">${icon('plus')}<span class="hide-sm">New application</span></a>`
          : html`<a class="btn primary" href="#/admin/users">${icon('users')}<span class="hide-sm">Manage users</span></a>`}
        <button class="btn ghost icon" id="themeBtn" title="Toggle theme" aria-label="Toggle theme">${icon('moon')}</button>
        <div class="user-menu">
          <button class="btn ghost" id="userBtn" style="padding:0 6px 0 4px;height:42px"><div class="avatar">${initials(u.name)}</div>
            <span class="hide-sm" style="text-align:left;line-height:1.2"><b style="display:block;font-size:13px">${u.name}</b><span class="muted small">${ROLE_LABEL[u.role] || u.role}</span></span>${icon('chevronDown')}</button>
          <div class="dropdown hidden" id="userDrop">
            <div style="padding:8px 10px"><b>${u.name}</b><div class="muted small">${u.email}</div></div><hr>
            <a href="#/settings">${icon('settings')}Settings</a>
            ${bids ? html`<a href="#/profiles">${icon('user')}My profiles</a>` : html`<a href="#/admin/users">${icon('users')}Users</a>`}<hr>
            <button id="logoutBtn">${icon('logout')}Sign out</button>
          </div>
        </div>
      </header>
      <div id="engineBanner"></div>
      <main class="content" id="view"></main>
    </div>
  </div>`);
  $('#menuBtn').onclick = () => $('#sidebar').classList.toggle('open');
  $('#userBtn').onclick = (e) => { e.stopPropagation(); $('#userDrop').classList.toggle('hidden'); };
  document.addEventListener('click', () => $('#userDrop')?.classList.add('hidden'));
  $('#logoutBtn').onclick = async () => { await api.post('/api/auth/logout'); state.user = null; location.hash = ''; boot(); };
  $('#themeBtn').onclick = toggleTheme;
  if ($('#globalSearch')) $('#globalSearch').onsubmit = (e) => { e.preventDefault(); go('/applications?q=' + encodeURIComponent(e.target.q.value)); };
  window.addEventListener('scroll', () => $('#topbar')?.classList.toggle('scrolled', scrollY > 4), { passive: true });
  syncThemeIcon();
  return pollEngine();
}

function toggleTheme() {
  const root = document.documentElement;
  const dark = root.dataset.theme ? root.dataset.theme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
  root.dataset.theme = dark ? 'light' : 'dark';
  try { localStorage.setItem('cp-theme', root.dataset.theme); } catch {}
  syncThemeIcon();
}
function syncThemeIcon() {
  const root = document.documentElement;
  const dark = root.dataset.theme ? root.dataset.theme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
  const b = $('#themeBtn');
  if (b) b.innerHTML = String(icon(dark ? 'sun' : 'moon'));
}

const ENGINE_TEXT = {
  not_connected: 'Not connected', idle: 'Connected · starts on demand', starting: 'Opening DeepSeek…', ready: 'Connected', busy: 'Writing…',
  login_required: 'Sign-in needed', offline: "Can't reach DeepSeek", error: 'Needs attention',
};
export function engineLabel(e) {
  if (!e) return '';
  if (e.mode === 'demo') return 'Demo mode';
  if (e.signing_in) return 'Signing in…';
  return ENGINE_TEXT[e.status] || e.status;
}
// Only people who generate need their own DeepSeek account (the master manages the platform and doesn't bid).
const isBlocked = (e) => !!(e && e.needs_account && e.mode !== 'demo' && (e.blocked_since || ['not_connected', 'login_required', 'offline', 'error'].includes(e.status)));
let wasBlocked = null;

// Opens a Chrome window on the server for the user's own DeepSeek sign-in, then refreshes the engine state.
export function connectDeepSeek() {
  return openDeepSeekSignIn({ onClose: () => pollEngine() });
}

function renderEngineBanner(e) {
  const host = $('#engineBanner');
  if (!host) return;
  const blocked = isBlocked(e);
  const sig = blocked ? [e.status, e.signing_in, e.blocked_reason, e.queue].join('|') : '';
  if (host.dataset.sig === sig) return;
  host.dataset.sig = sig;
  if (!blocked) { host.innerHTML = ''; return; }
  const waiting = e.queue || 0;
  const wait = waiting ? ` ${waiting} request${waiting === 1 ? ' is' : 's are'} waiting and run${waiting === 1 ? 's' : ''} automatically once you connect.` : '';
  const what = {
    not_connected: ['Connect your DeepSeek account', `CareerPilot writes your résumés, cover letters and answers with your own DeepSeek account.${wait}`],
    login_required: ['Sign in to DeepSeek again', `Your DeepSeek sign-in has expired.${wait}`],
    offline: ["DeepSeek can't be reached", `CareerPilot retries automatically.${wait}`],
    error: ['Your DeepSeek needs attention', e.message || ''],
  }[e.status] || ['Your DeepSeek needs attention', e.message || ''];
  const action = ['not_connected', 'login_required'].includes(e.status);
  mount(host, html`<div class="engine-banner ${action ? 'warn' : 'crit'}" role="alert">${icon('alert')}
    <div class="eb-text"><b>${e.signing_in ? 'Finish signing in to DeepSeek' : what[0]}</b><span>${what[1]}</span></div>
    <a class="btn sm" href="#/settings?tab=engine">Details</a>
    ${action ? html`<button class="btn sm primary" id="ebConnect">${icon('link')}${e.signing_in ? 'Continue sign-in' : 'Connect DeepSeek'}</button>` : ''}</div>`);
  $('#ebConnect')?.addEventListener('click', () => connectDeepSeek());
}

async function pollEngine() {
  clearTimeout(enginePoll);
  try {
    state.engine = await api.get('/api/engine');
    const e = state.engine;
    const blocked = isBlocked(e);
    if (wasBlocked !== null && blocked !== wasBlocked && !blocked) toast('Your DeepSeek is connected — waiting requests are running');
    wasBlocked = blocked;
    renderEngineBanner(e);
    window.dispatchEvent(new CustomEvent('cp:engine', { detail: e }));
  } catch {}
  const e = state.engine;
  const fast = e && (['busy', 'starting', 'login_required', 'offline'].includes(e.status) || e.signing_in);
  enginePoll = setTimeout(pollEngine, fast ? 2500 : 8000);
}
export const refreshEngine = () => pollEngine();

// ------------------------------------------------------------------ router
async function route() {
  if (!state.user) return;
  const { path, query } = parseHash();
  let page = null, params = [];
  for (const [re, name] of ROUTES) {
    const m = path.match(re);
    if (m) { page = name; params = m.slice(1); break; }
  }
  if (cleanup) { try { cleanup(); } catch {} cleanup = null; }
  $$('.nav a').forEach(a => {
    const href = a.dataset.href;
    a.classList.toggle('active', href === '/' ? path === '/' || path === '' : path.startsWith(href));
  });
  $('#sidebar')?.classList.remove('open');
  const view = $('#view');
  window.scrollTo(0, 0);
  if (!page) { mount(view, html`<div class="empty"><h3>Page not found</h3><a class="btn" href="#/">Go to dashboard</a></div>`); return; }
  if (page === 'dashboard' && state.user.role === 'master') page = 'platform';
  if (!(ACCESS[state.user.role] || []).includes(page)) { go('/'); return; }
  mount(view, html`<div class="row" style="padding:60px;justify-content:center"><span class="spinner"></span></div>`);
  try {
    const mod = await import(`./pages/${page}.js`);
    const token = Symbol();
    route.token = token;
    const result = await mod.default({ el: view, params, query, user: state.user, isCurrent: () => route.token === token });
    if (route.token === token && typeof result === 'function') cleanup = result;
    document.title = `${$('h1', view)?.textContent || 'CareerPilot'} · CareerPilot`;
  } catch (err) {
    console.error(err);
    mount(view, html`<div class="alert error" style="margin-top:20px">${icon('alert')}<div><b>Something went wrong</b>${err.message}</div></div>`);
  }
}

export async function loadSettings(force = false) {
  if (!state.settings || force) state.settings = await api.get('/api/settings');
  return state.settings;
}

async function boot() {
  try {
    const s = await api.get('/api/auth/state');
    state.publicUrl = s.public_url || '';
    if (!s.user) { state.user = null; clearTimeout(enginePoll); return renderAuth(s.has_users, s.registration); }
    state.user = s.user;
  } catch (e) {
    mount($('#app'), html`<div class="empty" style="padding-top:120px"><h3>CareerPilot server is not reachable</h3><p>${e.message}</p><button class="btn primary" onclick="location.reload()">Retry</button></div>`);
    return;
  }
  state.settings = null;
  await shell();
  route();
}

window.addEventListener('hashchange', route);
window.addEventListener('cp:unauthorized', () => { if (state.user) { state.user = null; toast('Your session ended. Please sign in again.', 'error'); boot(); } });
boot();
