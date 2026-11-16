// Small fetch wrapper for the CareerPilot API.
export class ApiError extends Error {
  constructor(message, status, data) { super(message); this.status = status; this.data = data; }
}

async function request(method, url, body) {
  const opts = { method, headers: {}, credentials: 'same-origin' };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(url, opts);
  } catch (e) {
    throw new ApiError('Cannot reach the CareerPilot server. Is it running?', 0);
  }
  let data = null;
  const type = res.headers.get('content-type') || '';
  if (type.includes('application/json')) data = await res.json();
  else data = await res.text();
  if (!res.ok) {
    const detail = data && typeof data === 'object' ? data.detail : null;
    let msg = Array.isArray(detail) ? detail.map(d => d.msg).join(', ') : (detail || `Request failed (${res.status})`);
    // 502/503/504 without JSON come from the proxy in front of the server (e.g. Netlify), not from CareerPilot itself.
    if ([502, 503, 504].includes(res.status) && !type.includes('application/json')) msg = 'The CareerPilot server is offline right now. Try again in a minute.';
    const err = new ApiError(msg, res.status, data);
    if (res.status === 401 && !url.startsWith('/api/auth')) window.dispatchEvent(new CustomEvent('cp:unauthorized'));
    throw err;
  }
  return data;
}

export const api = {
  get: (u) => request('GET', u),
  post: (u, b = {}) => request('POST', u, b),
  put: (u, b = {}) => request('PUT', u, b),
  del: (u) => request('DELETE', u),
};
