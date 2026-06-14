const base = '/api';

// Session token is stored in memory for the lifetime of the tab.
// The backend issues it on POST /sessions and requires it on all
// session-scoped requests via the X-Session-Token header.
let _sessionToken = null;

export function setSessionToken(token) {
  _sessionToken = token;
}

async function req(path, options = {}) {
  const headers = options.body instanceof FormData
    ? {}
    : { 'Content-Type': 'application/json' };

  if (_sessionToken) {
    headers['X-Session-Token'] = _sessionToken;
  }

  const res = await fetch(base + path, { headers, ...options });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    const msg = detail.detail || `Request failed (${res.status})`;
    const err = new Error(msg);
    err.error_code = detail.error_code || null;
    throw err;
  }
  return res.json();
}

const POLL_INTERVAL_MS = 1500;

export const api = {
  createSession: () => req('/sessions', { method: 'POST' }),

  uploadFile: (id, file) => {
    const fd = new FormData();
    fd.append('file', file);
    return req(`/sessions/${id}/files`, { method: 'POST', body: fd });
  },

  parse: (id, text) =>
    req(`/sessions/${id}/parse`, { method: 'POST', body: JSON.stringify({ text }) }),

  confirmMapping: (id, mapping) =>
    req(`/sessions/${id}/column-mapping`, { method: 'POST', body: JSON.stringify(mapping) }),

  clarify: (id, answers) =>
    req(`/sessions/${id}/clarify`, { method: 'POST', body: JSON.stringify({ answers }) }),

  // Starts an async solve job, polls until complete, and returns the session state.
  // Pass an AbortSignal to allow cancellation from the caller.
  solve: async (id, { signal } = {}) => {
    const { job_id } = await req(`/sessions/${id}/solve`, { method: 'POST' });

    while (true) {
      await new Promise((resolve, reject) => {
        const t = setTimeout(resolve, POLL_INTERVAL_MS);
        if (signal) {
          signal.addEventListener('abort', () => {
            clearTimeout(t);
            reject(new DOMException('Aborted', 'AbortError'));
          }, { once: true });
        }
      });

      if (signal?.aborted) {
        await api.cancelJob(job_id).catch(() => {});
        const err = new Error('Solve was cancelled.');
        err.error_code = 'cancelled';
        throw err;
      }

      const job = await req(`/jobs/${job_id}`);

      if (job.status === 'done') return job.session;

      if (job.status === 'failed') {
        const err = new Error(job.error || 'Solve failed.');
        err.error_code = job.error_code || 'solver_error';
        throw err;
      }

      if (job.status === 'cancelled') {
        const err = new Error('Solve was cancelled.');
        err.error_code = 'cancelled';
        throw err;
      }
    }
  },

  cancelJob: (jobId) => req(`/jobs/${jobId}/cancel`, { method: 'POST' }),

  editSpec: (id, edits) =>
    req(`/sessions/${id}/spec`, { method: 'POST', body: JSON.stringify(edits) }),

  getSession: (id) => req(`/sessions/${id}`),

  submitFeedback: (id, feedback) =>
    req(`/sessions/${id}/feedback`, { method: 'POST', body: JSON.stringify(feedback) }),

  getPreferences: () => req('/preferences'),
};
