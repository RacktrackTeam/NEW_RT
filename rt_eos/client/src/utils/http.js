import { apiUrl, authFetch } from './api';

/**
 * One way to talk JSON to the server.
 *
 * Every error the server sends is { error: "plain English sentence" }; that
 * sentence is what the screen shows. The thrown Error carries the HTTP status
 * so a page can tell "not built yet" (404) from "refused" (403).
 */
export class HttpError extends Error {
  constructor(message, status, body) {
    super(message);
    this.name = 'HttpError';
    this.status = status;
    this.body = body;
  }
}

const FALLBACK = {
  400: 'That was not accepted. Check what you entered and try again.',
  401: 'Your session has ended. Sign in again.',
  403: 'Your account cannot do this.',
  404: 'That could not be found.',
  409: 'That clashes with something that already exists.',
  413: 'That file is too large.',
  422: 'That could not be read. Check it and try again.',
  429: 'Too many tries. Wait a moment and try again.',
};

async function readBody(res) {
  const type = res.headers?.get?.('content-type') || '';
  if (type.includes('application/json')) {
    try { return await res.json(); } catch { return null; }
  }
  try {
    const text = await res.text();
    try { return JSON.parse(text); } catch { return null; }
  } catch { return null; }
}

export async function request(path, { method = 'GET', body, form, signal, headers } = {}) {
  let init = { method, signal, headers: { ...(headers || {}) } };
  if (form) {
    init.body = form;
  } else if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await authFetch(apiUrl(path), init);
  } catch (err) {
    if (err?.name === 'AbortError' || err?.name === 'TimeoutError') throw err;
    throw new HttpError('No connection to the server. Check your network and try again.', 0, null);
  }
  const data = await readBody(res);
  if (!res.ok) {
    const said = data && typeof data.error === 'string' && data.error !== 'rate_limited' ? data.error : null;
    const msg = said || (data && data.message) || FALLBACK[res.status] || 'Something went wrong. Try again.';
    throw new HttpError(msg, res.status, data);
  }
  return data;
}

export const getJSON = (path, opts) => request(path, { ...opts, method: 'GET' });
export const postJSON = (path, body, opts) => request(path, { ...opts, method: 'POST', body: body ?? {} });
export const putJSON = (path, body, opts) => request(path, { ...opts, method: 'PUT', body: body ?? {} });
export const patchJSON = (path, body, opts) => request(path, { ...opts, method: 'PATCH', body: body ?? {} });
export const deleteJSON = (path, opts) => request(path, { ...opts, method: 'DELETE' });
export const postForm = (path, form, opts) => request(path, { ...opts, method: 'POST', form });

/** True when a route is not on this server yet (so a page can fall back). */
export const notBuilt = (err) => err instanceof HttpError && (err.status === 404 || err.status === 405);

/** A plain link to a file the server makes (reports, exports). */
export const fileUrl = (path) => apiUrl(path);

export const enc = encodeURIComponent;
