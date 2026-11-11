import { useEffect, useState } from "react";
import { api } from "./api.js";

export function confirmDelete(r) {
  const what = [r.job_title || "Untitled role", r.company].filter(Boolean).join(" · ");
  return window.confirm(`Delete the run "${what}"?\n\nIts versions, scores and downloads are deleted for good. ` +
    "Resumes you saved to My resumes from it are kept.");
}

function when(created) {
  // "20261103-121233" -> "3 Nov 2026, 12:12"
  const m = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})/.exec(created || "");
  if (!m) return created;
  const d = new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]);
  return d.toLocaleString(undefined, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

export default function RunsList() {
  const [runs, setRuns] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(null);
  const [delError, setDelError] = useState(null);
  async function remove(r) {
    if (!confirmDelete(r)) return;
    setBusy(r.run_id); setDelError(null);
    try {
      await api.deleteRun(r.run_id);
      setRuns((list) => list.filter((x) => x.run_id !== r.run_id));
    } catch (e) {
      setDelError(e.message);
    } finally {
      setBusy(null);
    }
  }
  useEffect(() => {
    api.runs().then(setRuns).catch((e) => setError(e.message));
  }, []);

  if (error) return <div className="alert alert-bad">{error}</div>;
  if (!runs) return <div className="muted">Loading…</div>;
  return (
    <section className="stack">
      <h1>Your runs</h1>
      {runs.length === 0 && (
        <div className="card">
          No runs yet. <a href="#/">Match a job</a> to start one.
        </div>
      )}
      {delError && <div className="alert alert-bad" role="alert">{delError}</div>}
      {runs.map((r) => (
        <div key={r.run_id} className={`card run-item ${busy === r.run_id ? "run-deleting" : ""}`}>
        <a className="run-card" href={`#/run/${encodeURIComponent(r.run_id)}`}>
          <div className="run-main">
            <div className="run-title">{r.job_title || "Untitled role"}</div>
            <div className="muted">
              {r.company || "Unknown company"} · {when(r.created)}
            </div>
            <div className="small muted">{r.stage}</div>
          </div>
          <div className="run-score">
            {r.score != null ? (
              <>
                <div className="score-num">{Math.round(r.score)}</div>
                {r.best_score != null && r.best_score > r.score && (
                  <div className="small muted">best {Math.round(r.best_score)}</div>
                )}
              </>
            ) : (
              <div className="small muted">not scored</div>
            )}
          </div>
        </a>
        <button className="btn btn-sm btn-danger run-delete" disabled={!!busy} onClick={() => remove(r)}
                aria-label={`Delete run ${r.job_title || ""}`}>
          {busy === r.run_id ? "Deleting…" : "Delete"}
        </button>
        </div>
      ))}
    </section>
  );
}
