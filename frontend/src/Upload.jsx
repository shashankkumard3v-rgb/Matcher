// New: paste a job description -> your saved resumes ranked for it -> pick one -> improve that one.
import { useEffect, useState } from "react";
import { api } from "./api.js";
import { get as storeGet, track, useChanged, useKept } from "./store.js";
import { go } from "./nav.js";
import { LevelDots, ResumeUploader } from "./MyResumes.jsx";

const MIN_JD_CHARS = 80;
const MAX_JD_CHARS = 60000;
// Same text = same words, whatever the spaces/line breaks (Windows \r\n, blank lines, trailing spaces).
const sameText = (a, b) => (a || "").replace(/\s+/g, " ").trim() === (b || "").replace(/\s+/g, " ").trim();
const PART_LABEL = { required: "Must-haves", nice: "Nice-to-haves", capabilities: "Kind of work", years: "Years", title: "Title" };

function useElapsed(startedAt) {   // seconds since the step started (kept across pages)
  const [, tick] = useState(0);
  useEffect(() => {
    if (!startedAt) return;
    const t = setInterval(() => tick((x) => x + 1), 500);
    return () => clearInterval(t);
  }, [startedAt]);
  return startedAt ? Math.round((Date.now() - startedAt) / 1000) : 0;
}

function SkillRow({ f }) {
  return (
    <li>
      <span className="skill-name">{f.jd_skill}</span>
      {f.matched ? <LevelDots level={f.level} /> : <span className="chip chip-bad">missing</span>}
      <span className="small muted">
        {f.matched && f.matched.toLowerCase() !== f.jd_skill.toLowerCase() ? `${f.matched} · ` : ""}
        {f.matched ? f.note : ""}
      </span>
    </li>
  );
}

function ResultCard({ r, rank, onUse, busy }) {
  const [open, setOpen] = useState(rank === 0);
  const tone = r.score >= 70 ? "chip-good" : r.score >= 45 ? "chip-warn" : "chip-bad";
  return (
    <div className={`card resume-card ${rank === 0 ? "best-match" : ""}`}>
      <div className="row wrap">
        <span className={`chip ${tone} match-score`}>{Math.round(r.score)}</span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="strong">{r.name}{rank === 0 && <span className="chip chip-good" style={{ marginLeft: 8 }}>best match</span>}</div>
          <div className="small muted">{r.summary}</div>
        </div>
      </div>
      {open && (
        <div className="stack-sm" style={{ marginTop: 10 }}>
          <div className="small muted">
            {Object.entries(r.parts).map(([k, v]) => `${PART_LABEL[k] || k} ${Math.round(v)}`).join(" · ")}
          </div>
          {r.required.length > 0 && (<>
            <div className="small strong">Must-haves</div>
            <ul className="skill-levels">{r.required.map((f) => <SkillRow key={f.jd_skill} f={f} />)}</ul>
          </>)}
          {r.nice.length > 0 && (<>
            <div className="small strong">Nice-to-haves</div>
            <ul className="skill-levels">{r.nice.map((f) => <SkillRow key={f.jd_skill} f={f} />)}</ul>
          </>)}
          {r.capabilities.length > 0 && (<>
            <div className="small strong">Kind of work the job asks for</div>
            <ul className="skill-levels">
              {r.capabilities.map((c) => (
                <li key={c.id}>
                  <span className="skill-name" title={c.label}>{c.label.split(" (")[0]}</span>
                  {c.level ? <LevelDots level={c.level} /> : <span className="chip chip-bad">not shown</span>}
                </li>
              ))}
            </ul>
          </>)}
        </div>
      )}
      <div className="row wrap" style={{ marginTop: 10 }}>
        <button className="btn btn-primary" disabled={busy} onClick={() => onUse(r)}>Use this resume</button>
        <button className="btn btn-sm" onClick={() => setOpen(!open)}>{open ? "Hide details" : "Why this score"}</button>
      </div>
    </div>
  );
}

export default function Upload() {
  const [saved, setSaved] = useState(null);       // null = loading
  const [loadErr, setLoadErr] = useState(null);
  const [jdText, setJdText] = useState(() => { try { return sessionStorage.getItem("matcher-jd") || ""; } catch { return ""; } });
  useEffect(() => { try { sessionStorage.setItem("matcher-jd", jdText); } catch { /* private mode */ } }, [jdText]);
  // Kept in the app-wide store: ranking/starting keeps going when you switch pages, and the
  // results are still here when you come back.
  const [phase, setPhase] = useKept("new:phase", "idle");   // idle | matching | starting
  const [startedAt, setStartedAt] = useKept("new:startedAt", null);
  const [match, setMatch] = useKept("new:match", null);     // {jd, jd_text, warnings, results}
  const [sent, setSent] = useKept("new:sent", null);        // the exact text that was ranked
  const [error, setError] = useKept("new:error", null);
  const [ready_, setReadyRun] = useKept("new:readyRun", null); // run started while you were elsewhere
  const elapsed = useElapsed(phase !== "idle" ? startedAt : null);
  const resumesChanged = useChanged("resumes");

  useEffect(() => { api.resumes().then(setSaved).catch((e) => setLoadErr(e.message)); }, [resumesChanged]);

  const busy = phase !== "idle";
  const jdLen = jdText.trim().length;
  const jdShort = jdLen > 0 && jdLen < MIN_JD_CHARS;
  const ready = saved?.length > 0 && jdLen >= MIN_JD_CHARS && jdLen <= MAX_JD_CHARS;
  const edited = !!match && !sameText(sent, jdText);   // you changed the job text after ranking

  async function find() {
    if (storeGet("new:phase", "idle") !== "idle") return;
    setError(null); setPhase("matching"); setStartedAt(Date.now()); setReadyRun(null);
    try {
      const text = jdText.trim();
      const res = await track("new:match", "Ranking your resumes", "#/", () => api.matchJd(text));
      setMatch(res);
      setSent(text);
    } catch (e) {
      setError(e.message);
    } finally {
      setPhase("idle");
    }
  }

  async function use(r) {
    if (storeGet("new:phase", "idle") !== "idle") return;
    setError(null); setPhase("starting"); setStartedAt(Date.now());
    try {
      const res = await track("new:start", "Starting the run", "#/", () => api.startRun(r.resume_id, match.jd, match.jd_text));
      try { sessionStorage.removeItem("matcher-jd"); } catch { /* ignore */ }
      setMatch(null); setSent(null);
      const onNew = !window.location.hash || window.location.hash === "#/" || window.location.hash === "#";
      if (onNew) go(`run/${encodeURIComponent(res.run_id)}`);    // never pull you away from another page
      else setReadyRun({ run_id: res.run_id, title: [res.jd?.title, res.jd?.company].filter(Boolean).join(" · ") });
    } catch (e) {
      setError(e.message);
    } finally {
      setPhase("idle");
    }
  }

  if (loadErr) return <div className="alert alert-bad">{loadErr}</div>;
  if (saved === null) return <div className="muted small">Loading…</div>;

  return (
    <section className="stack">
      <div>
        <h1>Match a job</h1>
        <p className="muted">
          Paste the job description. Matcher ranks your saved resumes by how strongly each one proves what the
          job asks for. Pick one and improve only that one.
        </p>
      </div>

      {ready_ && (
        <div className="alert alert-ok row wrap">
          <span style={{ flex: 1 }}>Your run is ready{ready_.title ? `: ${ready_.title}` : ""}.</span>
          <a className="btn btn-primary btn-sm" href={`#/run/${encodeURIComponent(ready_.run_id)}`}
             onClick={() => setReadyRun(null)}>Open it</a>
        </div>
      )}

      {saved.length === 0 && (
        <div className="stack-sm">
          <div className="alert alert-info">
            <strong>First, add a resume.</strong> You don't have any saved resumes yet. Add one here (or any
            time under My info → Resumes). Later you can save every improved version too.
          </div>
          <ResumeUploader />
        </div>
      )}

      <label className="jd-box">
        <span className="drop-label">Job description</span>
        <span className="muted small">Copy the whole job posting (title, requirements, responsibilities) and paste it here.</span>
        <textarea className="editor jd-text" value={jdText} onChange={(e) => setJdText(e.target.value)}
                  placeholder="Paste the job description here…" disabled={busy} spellCheck={false} />
        <span className={`small ${jdShort || jdLen > MAX_JD_CHARS ? "err-text" : "muted"}`}>
          {jdLen === 0 ? "Nothing pasted yet"
            : jdShort ? "That looks too short. Paste the whole posting."
            : jdLen > MAX_JD_CHARS ? "That's too long (60,000 characters max)."
            : `${jdLen.toLocaleString()} characters`}
          {jdLen > 0 && !busy && (<>{" · "}<button type="button" className="linklike" onClick={() => { setJdText(""); setMatch(null); }}>clear</button></>)}
        </span>
      </label>

      {error && <div className="alert alert-bad" role="alert">{error}</div>}

      {busy ? (
        <div className="progress-card" aria-live="polite">
          <div className="spinner" aria-hidden="true" />
          <div>
            <strong>{phase === "matching" ? "Reading the job and ranking your resumes" : "Starting the run"}</strong>
            <div className="muted small">
              {phase === "matching" ? "The AI reads the job once; the ranking itself is instant. " : ""}
              ({elapsed}s so far)
            </div>
          </div>
        </div>
      ) : !match ? (
        <button className="btn btn-primary btn-big" disabled={!ready} onClick={find}>Find my best resume</button>
      ) : edited ? (
        <div className="alert alert-warn row wrap">
          <span style={{ flex: 1 }}>You changed the job description. The ranking below is for the earlier text.</span>
          <button className="btn btn-primary btn-sm" disabled={!ready} onClick={find}>Rank again</button>
        </div>
      ) : null}

      {match && (
        <div className="stack">
          <div>
            <h2>{[match.jd.title, match.jd.company].filter(Boolean).join(" · ") || "This job"}</h2>
            {match.warnings.length > 0 && (
              <div className="small muted">{match.warnings.map((w) => w.message).join(" ")}</div>
            )}
          </div>
          {match.results.length === 0 && <div className="muted">No saved resumes to rank.</div>}
          {match.results.map((r, i) => (
            <ResultCard key={r.resume_id} r={r} rank={i} onUse={use} busy={busy || edited} />
          ))}
          <p className="small muted">
            Quick match uses each resume's saved description (skill levels 1–5, proven by quotes from the
            resume). The full, strict score comes next, in the run.
          </p>
        </div>
      )}
    </section>
  );
}
