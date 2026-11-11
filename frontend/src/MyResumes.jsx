// My resumes: upload, see what each one proves (skill + capability levels), rename, delete.
import { useEffect, useRef, useState } from "react";
import { api, uploadResume } from "./api.js";
import { bump, track, useChanged, useKept } from "./store.js";

const MAX_BYTES = 10 * 1024 * 1024;
const LEVEL_WORD = ["", "listed", "used", "built", "owned", "owned + result"];

function fileError(f) {
  if (!f) return null;
  if (!f.name.toLowerCase().endsWith(".pdf")) return `${f.name} isn't a PDF.`;
  if (f.size > MAX_BYTES) return `${f.name} is larger than 10 MB.`;
  if (f.size === 0) return `${f.name} is empty.`;
  return null;
}

export function LevelDots({ level }) {
  return (
    <span className="dots" aria-label={`level ${level} of 5`} title={`${level}/5 · ${LEVEL_WORD[level] || ""}`}>
      {[1, 2, 3, 4, 5].map((i) => <span key={i} className={i <= level ? "dot on" : "dot"} />)}
    </span>
  );
}

// One upload at a time, kept in the app-wide store: it keeps going (and shows its progress on both
// the New page and My info) when you switch pages. Lists reload when it's saved.
export function ResumeUploader({ compact }) {
  const input = useRef(null);
  const [file, setFile] = useKept("upload:file", null);
  const [name, setName] = useKept("upload:name", "");
  const [notes, setNotes] = useKept("upload:notes", "");
  const [progress, setProgress] = useKept("upload:progress", null);
  const [error, setError] = useKept("upload:error", null);
  const err = fileError(file);

  async function save() {
    if (progress) return;
    setError(null);
    setProgress({ phase: "upload", pct: 0 });
    try {
      await track("upload", "Adding your resume", "#/me/resumes", () =>
        uploadResume(file, name.trim() || file.name.replace(/\.pdf$/i, ""), notes, setProgress));
      setFile(null); setName(""); setNotes("");
      bump("resumes");
    } catch (e) {
      setError(e.message);
    } finally {
      setProgress(null);
    }
  }

  return (
    <div className={`card ${compact ? "card-flat" : ""} stack-sm`}>
      <div className="strong">Add a resume</div>
      <div
        className={`drop ${file && !err ? "drop-done" : ""} ${err ? "drop-bad" : ""}`}
        role="button" tabIndex={0} onClick={() => !progress && input.current?.click()}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => { e.preventDefault(); if (!progress) setFile(e.dataTransfer.files[0]); }}
      >
        <input ref={input} type="file" accept=".pdf" hidden onChange={(e) => setFile(e.target.files[0])} />
        <div className="drop-label">{file ? file.name : "Choose your resume (PDF)"}</div>
        <div className="muted small">{file ? "Tap to change" : "or drop it here · up to 10 MB"}</div>
        {err && <div className="err-text">{err}</div>}
      </div>
      <input className="input" placeholder="Name, e.g. Backend - payments" value={name} maxLength={80}
             onChange={(e) => setName(e.target.value)} disabled={!!progress} />
      <input className="input" placeholder="Notes (optional), e.g. for platform / Kafka-heavy roles" value={notes}
             onChange={(e) => setNotes(e.target.value)} disabled={!!progress} />
      {progress && (
        <div className="stack-sm">
          <span className="bar"><span className="bar-fill" style={{ width: `${progress.pct}%` }} /></span>
          <div className="small muted">
            {progress.phase === "upload" ? `Uploading… ${progress.pct}%`
              : "Reading it and rating every skill strictly (blind, with quotes)… 20–60 s"}
          </div>
        </div>
      )}
      {error && <div className="alert alert-bad small">{error}</div>}
      <div>
        <button className="btn btn-primary" disabled={!file || !!err || !!progress} onClick={save}>Save to My resumes</button>
      </div>
    </div>
  );
}

function ResumeCard({ r, onChanged, onDeleted }) {
  const [edit, setEdit] = useState(false);
  const [name, setName] = useState(r.name);
  const [notes, setNotes] = useState(r.notes || "");
  const [busy, setBusy] = useKept(`resume:${r.id}:busy`, null);     // kept across pages
  const [error, setError] = useKept(`resume:${r.id}:error`, null);
  const [all, setAll] = useState(false);

  async function act(label, fn) {
    if (busy) return;
    setBusy(label); setError(null);
    try { await track(`resume:${r.id}`, label, "#/me/resumes", fn); bump("resumes"); }
    catch (e) { setError(e.message); }
    finally { setBusy(null); }
  }
  const skills = all ? r.skills : r.skills.slice(0, 10);
  const src = r.source?.type === "run"
    ? `Saved from a run${r.source.job?.title ? `: ${r.source.job.title}${r.source.job.company ? ` · ${r.source.job.company}` : ""}` : ""} (v${r.source.version})`
    : `Uploaded${r.source?.filename ? `: ${r.source.filename}` : ""}`;

  return (
    <div className="card resume-card">
      {!edit ? (
        <div className="row wrap">
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="strong">{r.name}</div>
            {r.notes && <div className="small">{r.notes}</div>}
            <div className="small muted">{src} · {new Date(r.created_at).toLocaleDateString()}</div>
          </div>
          {r.outdated && <span className="chip chip-warn">description outdated</span>}
        </div>
      ) : (
        <div className="stack-sm">
          <input className="input" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} />
          <input className="input" value={notes} placeholder="Notes" onChange={(e) => setNotes(e.target.value)} />
          <div className="row wrap">
            <button className="btn btn-sm btn-primary" disabled={!!busy || !name.trim()}
                    onClick={() => act("Saving…", async () => { onChanged(await api.updateResume(r.id, { name, notes })); setEdit(false); })}>
              Save
            </button>
            <button className="btn btn-sm" onClick={() => setEdit(false)}>Cancel</button>
          </div>
        </div>
      )}

      <div className="small strong" style={{ marginTop: 10 }}>Skills (how strongly this resume proves each)</div>
      <ul className="skill-levels">
        {skills.map((s) => (
          <li key={s.name}>
            <span className="skill-name">{s.name}</span>
            <LevelDots level={s.level} />
            <span className="small muted">
              {LEVEL_WORD[s.level]}
              {s.years ? ` · ${s.years} yrs` : ""}
              {s.last_used && s.last_used !== "present" ? ` · last ${s.last_used}` : ""}
            </span>
          </li>
        ))}
      </ul>
      {r.skills.length > 10 && (
        <button className="linklike small" onClick={() => setAll(!all)}>{all ? "Show fewer" : `Show all ${r.skills.length}`}</button>
      )}
      {r.capabilities.length > 0 && (
        <>
          <div className="small strong" style={{ marginTop: 10 }}>Capabilities</div>
          <ul className="skill-levels">
            {r.capabilities.map((c) => (
              <li key={c.id}>
                <span className="skill-name" title={c.label}>{c.label.split(" (")[0]}</span>
                <LevelDots level={c.level} />
              </li>
            ))}
          </ul>
        </>
      )}

      {error && <div className="alert alert-bad small">{error}</div>}
      {busy && <div className="small muted">{busy}</div>}
      <div className="row wrap" style={{ marginTop: 8 }}>
        {!edit && <button className="btn btn-sm" onClick={() => setEdit(true)}>Rename / notes</button>}
        {r.has_file && <a className="btn btn-sm" href={api.resumeFileUrl(r.id)}>Download</a>}
        <button className="btn btn-sm" disabled={!!busy}
                onClick={() => act("Rebuilding the description… 20–60 s", async () => onChanged(await api.reprofileResume(r.id)))}>
          Rebuild description
        </button>
        <button className="btn btn-sm btn-danger" disabled={!!busy}
                onClick={() => window.confirm(`Delete "${r.name}"? Runs that used it are kept.`) &&
                  act("Deleting…", async () => { await api.deleteResume(r.id); onDeleted(r.id); })}>
          Delete
        </button>
      </div>
    </div>
  );
}

export default function MyResumes() {
  const [list, setList] = useState(null);
  const [error, setError] = useState(null);
  const changed = useChanged("resumes");
  useEffect(() => {
    api.resumes().then(setList).catch((e) => setError(e.message));
  }, [changed]);
  if (error) return <div className="alert alert-bad">{error}</div>;
  if (!list) return <div className="muted small">Loading…</div>;
  return (
    <div className="stack">
      <p className="small muted">
        Your saved resumes. When you paste a job description, Matcher ranks these by how strongly each one proves
        what the job asks for. Each skill is rated 1–5 from the resume alone: 1 listed, 2 used, 3 built with it,
        4 owned/designed, 5 owned with a measured result.
      </p>
      <ResumeUploader />
      {list.length === 0 && <div className="muted small">No saved resumes yet.</div>}
      {list.map((r) => (
        <ResumeCard key={r.id} r={r}
                    onChanged={(n) => setList((l) => l.map((x) => (x.id === n.id ? n : x)))}
                    onDeleted={(id) => setList((l) => l.filter((x) => x.id !== id))} />
      ))}
    </div>
  );
}
