import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import { go, skillLabel } from "./nav.js";
import { bump, get as storeGet, track, useChanged, useKept } from "./store.js";
import { confirmDelete } from "./RunsList.jsx";
import Improve from "./Improve.jsx";

const WARNING_TEXT = {
  unverified_bullet: "Possibly not in your PDF",
  unverified_field: "Possibly not in your PDF",
  two_column_layout: "Two-column layout",
  no_experience: "No experience found",
  no_must_have: "No required skills found",
  no_title: "No job title found",
  employer_not_on_resume: "Company isn't on your resume",
  no_projects: "My info is empty",
};

function Chip({ children, kind }) {
  return <span className={`chip ${kind ? `chip-${kind}` : ""}`}>{children}</span>;
}

function Warnings({ warnings }) {
  if (!warnings?.length) return null;
  const groups = { resume: "Resume", jd: "Job description", projects: "My info" };
  return (
    <div className="card">
      <h2>Check these ({warnings.length})</h2>
      {Object.entries(groups).map(([src, title]) => {
        const items = warnings.filter((w) => w.source === src);
        if (!items.length) return null;
        return (
          <div key={src} className="warn-group">
            <div className="small strong">{title}</div>
            <ul className="warn-list">
              {items.map((w, i) => (
                <li key={i}>
                  <span className="warn-code">{WARNING_TEXT[w.code] || w.code.replaceAll("_", " ")}</span>
                  {w.message}
                </li>
              ))}
            </ul>
          </div>
        );
      })}
    </div>
  );
}

function JdCard({ jd }) {
  return (
    <div className="card">
      <h2>The job</h2>
      <div className="kv">
        {jd.seniority && <span>Level: <strong>{jd.seniority}</strong></span>}
        {jd.years_required != null && <span>Experience: <strong>{jd.years_required}+ yrs</strong></span>}
        {jd.location && <span>Location: <strong>{jd.location}</strong></span>}
      </div>
      <div className="small strong">Required</div>
      <div className="chips">
        {jd.must_have.length ? jd.must_have.map((s) => <Chip key={s}>{skillLabel(s)}</Chip>) : <span className="muted">none found</span>}
      </div>
      {jd.nice_to_have.length > 0 && (
        <>
          <div className="small strong">Nice to have</div>
          <div className="chips">{jd.nice_to_have.map((s) => <Chip key={s} kind="soft">{skillLabel(s)}</Chip>)}</div>
        </>
      )}
      {jd.languages?.length > 0 && (
        <>
          <div className="small strong">Languages</div>
          <div className="chips">{jd.languages.map((s) => <Chip key={s} kind="soft">{s}</Chip>)}</div>
        </>
      )}
    </div>
  );
}

function ResumeCard({ resume }) {
  const bullets = resume.experience.reduce((n, j) => n + j.bullets.length, 0);
  return (
    <div className="card">
      <h2>Your resume</h2>
      <div className="kv">
        <span><strong>{resume.experience.length}</strong> jobs</span>
        <span><strong>{bullets}</strong> bullets</span>
        <span><strong>{resume.skills.length}</strong> skills</span>
      </div>
      {resume.experience.map((j, i) => (
        <details key={i} className="job">
          <summary>
            <span className="strong">{j.title || "Role"}</span>
            {j.company && <span className="muted"> · {j.company}</span>}
            <span className="muted small"> · {j.start || "?"} – {j.end || "?"}</span>
          </summary>
          <ul>
            {j.bullets.map((b, k) => <li key={k}>{b}</li>)}
          </ul>
        </details>
      ))}
    </div>
  );
}

function ScoreRing({ value, floor, target }) {
  const pct = Math.max(0, Math.min(100, value));
  const color = value >= target ? "var(--good)" : value >= floor ? "var(--warn)" : "var(--bad)";
  return (
    <div className="ring" style={{ "--pct": pct, "--ring": color }}>
      <div className="ring-inner">
        <div className="ring-num">{Math.round(value)}</div>
        <div className="small muted">/ 100</div>
      </div>
    </div>
  );
}

function hitKind(status) {
  // close = a related skill instead (JavaScript for TypeScript); partial = some of "N of"
  return status === "evidenced" ? "good" : ["listed_only", "close", "partial"].includes(status) ? "warn" : "bad";
}

const r1 = (x) => Math.round(x * 10) / 10;

function ScoreAgain({ history, improving, busy, error, onAgain }) {
  const earlier = (history || []).slice(0, -1);
  return (
    <div className="score-again">
      {earlier.length > 0 && (
        <div className="small muted">
          Earlier scores of this same resume: {earlier.map((h) => `${r1(h.total)} (AI ${r1(h.ai)})`).join(" → ")}.
          {" "}The newest one counts everywhere.
        </div>
      )}
      {improving ? (
        <div className="small muted">
          You're improving this resume: each version is scored in Step 2, and Step 3 has a fresh re-check.
        </div>
      ) : (
        <>
          <div className="small muted">
            The 55 rule points never change for the same resume. The 45 AI points can differ by a few points
            between reads. Score again reads it 3 times and uses the middle answer for each part.
          </div>
          {error && <div className="alert alert-bad small">{error}</div>}
          <div>
            <button className="btn" disabled={busy} onClick={onAgain}>
              {busy ? "Re-checking… (30–90 s)" : "↻ Score again"}
            </button>
          </div>
        </>
      )}
    </div>
  );
}

function ScoreCard({ score, children }) {
  const floor = 65, target = 85;
  const verdict =
    score.total >= target ? "Strong match" : score.total >= floor ? "Possible match" : "Weak match as uploaded";
  return (
    <div className="card">
      <h2>Score</h2>
      <div className="score-head">
        <ScoreRing value={score.total} floor={floor} target={target} />
        <div>
          <div className="strong">{verdict}</div>
          <div className="muted small">
            {score.deterministic_points} from fixed rules (out of 55) · {score.llm_points} from AI judgment (out of 45)
          </div>
          <div className="muted small">Target 85 · below 65 Matcher advises not to apply</div>
        </div>
      </div>

      <div className="small strong">Required skills</div>
      <div className="chips">
        {score.must_have.map((h) => (
          <Chip key={h.skill} kind={hitKind(h.status)}>{skillLabel(h.skill, h.matched)}</Chip>
        ))}
      </div>
      <div className="legend small muted">
        <Chip kind="good">shown in a bullet</Chip> <Chip kind="warn">only listed / close / partly</Chip> <Chip kind="bad">missing</Chip>
      </div>

      <div className="dims">
        {score.dimensions.map((d) => (
          <details key={d.key} className="dim">
            <summary>
              <span className="dim-label">{d.label}</span>
              <span className="dim-pts">{d.points}/{d.weight}</span>
              <span className="bar bar-thin">
                <span className="bar-fill" style={{ width: `${(d.points / d.weight) * 100}%` }} />
              </span>
            </summary>
            <div className="small">
              <span className="muted">{d.method === "llm" ? "AI judgment" : "Fixed rule"}: </span>
              {d.reason || "—"}
            </div>
            {d.evidence?.length > 0 && (
              <ul className="evidence small">
                {d.evidence.map((q, i) => <li key={i}>“{q}”</li>)}
              </ul>
            )}
          </details>
        ))}
      </div>
      {score.warnings?.length > 0 && (
        <ul className="small muted">{score.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
      )}
      {children}
    </div>
  );
}

export default function RunView({ runId }) {
  const [run, setRun] = useState(null);
  const [error, setError] = useState(null);
  const [scoring, setScoring] = useKept(`busy:${runId}:score`, false);    // kept when you switch pages
  const [scoreError, setScoreError] = useKept(`err:${runId}:score`, null);
  const [deleting, setDeleting] = useState(false);
  const changed = useChanged(`run:${runId}`);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  useEffect(() => {
    api.run(runId).then(setRun).catch((e) => setError(e.message));
  }, [runId, changed]);

  async function score(again = false) {
    if (storeGet(`busy:${runId}:score`)) return;
    setScoring(true);
    setScoreError(null);
    try {
      const res = await track(`${runId}:score`, again ? "Scoring again" : "Scoring",
        `#/run/${encodeURIComponent(runId)}`, () => api.score(runId, again));
      setRun((r) => r && ({ ...r, score: res.score, score_history: res.history }));
    } catch (e) {
      setScoreError(e.message);
    } finally {
      setScoring(false);
      if (!mounted.current) bump(`run:${runId}`);   // finished while you were elsewhere: reload on return
    }
  }

  if (error) return <div className="alert alert-bad">{error}</div>;
  if (!run) return <div className="muted">Loading…</div>;

  return (
    <section className="stack">
      <div>
        <a className="small" href="#/runs">← All runs</a>
        <h1>{run.job_title || "Untitled role"}</h1>
        <div className="muted">{run.company || "Unknown company"}{run.candidate ? ` · ${run.candidate}` : ""}</div>
      </div>

      {run.score ? (
        <ScoreCard score={run.score}>
          <ScoreAgain history={run.score_history} improving={run.improving} busy={scoring}
                      error={scoreError} onAgain={() => score(true)} />
        </ScoreCard>
      ) : (
        <div className="card">
          <h2>Score this match</h2>
          <p className="muted small">
            A blind judge scores your resume as it is: 55 points from fixed rules, 45 from AI judgment backed by
            quotes from your resume.
          </p>
          {scoreError && <div className="alert alert-bad">{scoreError}</div>}
          <button className="btn btn-primary" disabled={scoring} onClick={() => score(false)}>
            {scoring ? "Scoring… (10–40 s)" : "Score it"}
          </button>
        </div>
      )}

      <Warnings warnings={run.warnings} />
      <JdCard jd={run.jd} />
      <ResumeCard resume={run.resume} />

      {/* a new score re-opens Step 1 so it shows "recalculate" */}
      <Improve key={(run.score_history || []).length} runId={run.run_id} />

      <div className="card card-flat row wrap">
        <span className="small muted" style={{ flex: 1 }}>Done with this job? Deleting removes the run for good.</span>
        <button className="btn btn-sm btn-danger" disabled={deleting} onClick={async () => {
          if (!confirmDelete(run)) return;
          setDeleting(true);
          try { await api.deleteRun(run.run_id); go("runs"); }
          catch (e) { window.alert(e.message); setDeleting(false); }
        }}>{deleting ? "Deleting…" : "Delete this run"}</button>
      </div>
    </section>
  );
}
