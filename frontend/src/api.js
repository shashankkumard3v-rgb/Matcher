// Small client for the Matcher API (same server that serves this app).

export class ApiError extends Error {
  constructor(message, { status, code } = {}) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

// Tells the app to show the sign-in screen (session missing or expired).
function signedOut(status, body) {
  if (status === 401 && body?.detail?.code === "not_signed_in") {
    window.dispatchEvent(new Event("matcher:signed-out"));
  }
}

function errorFrom(status, body) {
  signedOut(status, body);
  const d = body && body.detail;
  if (d && typeof d === "object" && d.message) return new ApiError(d.message, { status, code: d.code });
  if (typeof d === "string") return new ApiError(d, { status });
  if (Array.isArray(d)) return new ApiError(d.map((x) => x.msg).join("; "), { status });
  return new ApiError(`Request failed (HTTP ${status})`, { status });
}

// Requests still waiting for the server (the app doesn't auto-reload while one is running).
export const pending = { count: 0 };

async function request(method, path, body) {
  pending.count++;
  try {
    return await send(method, path, body);
  } finally {
    pending.count--;
  }
}

async function send(method, path, body) {
  let res;
  try {
    res = await fetch(`/api${path}`, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError("Can't reach the Matcher server. Is run.bat still running?", { status: 0 });
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) throw errorFrom(res.status, data);
  return data;
}

const R = (id) => `/runs/${encodeURIComponent(id)}`;

export const api = {
  // sign-in
  authConfig: () => request("GET", "/auth/config"),
  me: () => request("GET", "/auth/me"),
  signInGoogle: (credential) => request("POST", "/auth/google", { credential }),
  logout: () => request("POST", "/auth/logout"),

  skills: () => request("GET", "/skills"),
  runs: () => request("GET", "/runs"),
  run: (id) => request("GET", `/runs/${encodeURIComponent(id)}`),
  deleteRun: (id) => request("DELETE", `/runs/${encodeURIComponent(id)}`),
  score: (id, again = false) => request("POST", `/runs/${encodeURIComponent(id)}/score`, { again }),
  // your info: notes files + project template
  notes: () => request("GET", "/context/notes"),
  saveNotes: (kind, text) => request("PUT", `/context/notes/${kind}`, { text }),
  context: () => request("GET", "/context"),
  saveContext: (text) => request("PUT", "/context", { text }),
  deleteSavedAnswer: (id) => request("DELETE", `/context/projects/${encodeURIComponent(id)}`),
  previewContext: (text) => request("POST", "/context/preview", { text }),

  // My resumes + JD-first runs
  resumes: () => request("GET", "/resumes"),
  resume: (id) => request("GET", `/resumes/${encodeURIComponent(id)}`),
  updateResume: (id, fields) => request("PATCH", `/resumes/${encodeURIComponent(id)}`, fields),
  deleteResume: (id) => request("DELETE", `/resumes/${encodeURIComponent(id)}`),
  reprofileResume: (id) => request("POST", `/resumes/${encodeURIComponent(id)}/profile`),
  resumeFileUrl: (id) => `/api/resumes/${encodeURIComponent(id)}/file`,
  matchJd: (jd_text) => request("POST", "/match", { jd_text }),
  startRun: (resume_id, jd, jd_text) => request("POST", "/runs/start", { resume_id, jd, jd_text }),
  saveResume: (id, body) => request("POST", `${R(id)}/save-resume`, body),

  // step 3: apply gate
  gate: (id) => request("GET", `${R(id)}/gate`),
  gateCompute: (id) => request("POST", `${R(id)}/gate`),
  gateAnswer: (id, question_id, answer) => request("POST", `${R(id)}/gate/answer`, { question_id, answer }),
  gateSkip: (id, qid) => request("POST", `${R(id)}/gate/skip/${encodeURIComponent(qid)}`),
  gateContext: (id, text) => request("POST", `${R(id)}/gate/context`, { text }),
  gateProposal: (id, pid, accept) => request("POST", `${R(id)}/gate/proposals/${encodeURIComponent(pid)}`, { accept }),
  gateOverride: (id, target, reason) => request("POST", `${R(id)}/gate/override`, { target, reason }),
  gateDecide: (id, decision, reason = "") => request("POST", `${R(id)}/gate/decide`, { decision, reason }),

  // step 4: improve loop
  loop: (id) => request("GET", `${R(id)}/loop`),
  loopStart: (id, target) => request("POST", `${R(id)}/loop/start`, { target }),
  loopTarget: (id, target) => request("POST", `${R(id)}/loop/target`, { target }),
  loopAnswer: (id, question_id, answer) => request("POST", `${R(id)}/loop/answer`, { question_id, answer }),
  loopSkip: (id, qid) => request("POST", `${R(id)}/loop/skip/${encodeURIComponent(qid)}`),
  loopContext: (id, text) => request("POST", `${R(id)}/loop/context`, { text }),
  loopProposal: (id, pid, accept) => request("POST", `${R(id)}/loop/proposals/${encodeURIComponent(pid)}`, { accept }),
  loopNotes: (id, text) => request("POST", `${R(id)}/loop/notes`, { text }),
  loopSuggest: (id) => request("POST", `${R(id)}/loop/suggest`),
  loopChange: (id, cid, action, extra = {}) =>
    request("POST", `${R(id)}/loop/changes/${encodeURIComponent(cid)}`, { action, ...extra }),
  loopApply: (id) => request("POST", `${R(id)}/loop/apply`),
  loopNext: (id) => request("POST", `${R(id)}/loop/next`),
  loopRollback: (id, version) => request("POST", `${R(id)}/loop/rollback`, { version }),
  loopFinish: (id, version) => request("POST", `${R(id)}/loop/finish`, { version }),
  loopVersion: (id, n) => request("GET", `${R(id)}/loop/versions/${n}`),
  loopBullets: (id, version) =>
    request("GET", `${R(id)}/loop/bullets${version == null ? "" : `?version=${version}`}`),

  // step 5: one-page Word export
  exportGet: (id) => request("GET", `${R(id)}/export`),
  exportBuild: (id) => request("POST", `${R(id)}/export`),
  exportTrim: (id, option_ids) => request("POST", `${R(id)}/export/trim`, { option_ids }),
  exportReopen: (id) => request("POST", `${R(id)}/export/reopen-loop`),
  fitStart: (id) => request("POST", `${R(id)}/export/fit/start`),
  fitApply: (id) => request("POST", `${R(id)}/export/fit/apply`),
  freshScore: (id) => request("POST", `${R(id)}/export/fresh-score`),
  freshScores: (id) => request("GET", `${R(id)}/export/fresh-scores`),
  previewUrl: (id, stamp) => `/api${R(id)}/export/preview.png?v=${encodeURIComponent(stamp || "")}`,
  fileUrl: (id, kind) => `/api${R(id)}/export/file/${kind}`,
};

// GET that returns null on 404 (step not started yet)
export async function maybe(promise) {
  try {
    return await promise;
  } catch (e) {
    if (e.status === 404) return null;
    throw e;
  }
}

export async function uploadNotes(kind, file, mode) {
  const form = new FormData();
  form.append("file", file);
  form.append("mode", mode);
  let res;
  try {
    res = await fetch(`/api/context/notes/${kind}/upload`, { method: "POST", body: form });
  } catch {
    throw new ApiError("Can't reach the Matcher server. Is it still running?", { status: 0 });
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) throw errorFrom(res.status, data);
  return data;
}

// Upload with progress (fetch can't report upload progress, XHR can).
export function uploadForParse(resume, jdText, onProgress) {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("resume", resume);
    form.append("jd_text", jdText);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/parse");
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress?.({ phase: "upload", pct: Math.round((e.loaded / e.total) * 100) });
    };
    xhr.upload.onload = () => onProgress?.({ phase: "reading", pct: 100 });
    xhr.onerror = () => reject(new ApiError("Can't reach the Matcher server. Is run.bat still running?", { status: 0 }));
    xhr.onload = () => {
      let data = null;
      try {
        data = JSON.parse(xhr.responseText);
      } catch {
        /* not JSON */
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(errorFrom(xhr.status, data));
    };
    xhr.send(form);
  });
}

// Upload a resume PDF to My resumes, with upload progress.
export function uploadResume(file, name, notes, onProgress) {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("file", file);
    form.append("name", name || "");
    form.append("notes", notes || "");
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/resumes");
    pending.count++;
    const done = () => { pending.count--; };
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress?.({ phase: "upload", pct: Math.round((e.loaded / e.total) * 100) });
    };
    xhr.upload.onload = () => onProgress?.({ phase: "reading", pct: 100 });
    xhr.onerror = () => { done(); reject(new ApiError("Can't reach the Matcher server.", { status: 0 })); };
    xhr.onload = () => {
      done();
      let data = null;
      try { data = JSON.parse(xhr.responseText); } catch { /* not JSON */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(errorFrom(xhr.status, data));
    };
    xhr.send(form);
  });
}
