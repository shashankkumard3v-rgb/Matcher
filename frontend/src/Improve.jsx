// Steps 3-5 in the app: apply gate -> improve loop (with a target) -> one-page Word export.
// You can download any version at any time, then keep improving if you like.
import { useEffect, useRef, useState } from "react";
import { api, maybe } from "./api.js";
import { bump, get as storeGet, track, useChanged, useKept } from "./store.js";

const TARGETS = [80, 85, 90, 95];

const VERDICT = {
  APPLY: { label: "Apply: a strong match is reachable", kind: "good" },
  APPLY_WITH_RISK: { label: "Apply, with some risk", kind: "warn" },
  DONT_APPLY: { label: "Not recommended", kind: "bad" },
  OVERRIDDEN: { label: "You chose to go ahead", kind: "warn" },
};

const FIX_BY = {
  ai: { label: "AI can fix", kind: "good" },
  you: { label: "needs your input", kind: "warn" },
  none: { label: "can't fix by rewording", kind: "soft" },
};

const r1 = (x) => (x == null ? "–" : Math.round(x * 10) / 10);

// Runs an async action with a busy flag and an error message.
// Busy/error live in the app-wide store (keyed by run + card), so a step keeps going and stays
// visible when you switch pages and come back. If it finishes while this card isn't on screen,
// the run page is told to reload its state from the server.
function useAction(runId, part) {
  const key = `${runId}:${part}`;
  const [busy, setBusy] = useKept(`busy:${key}`, null);
  const [error, setError] = useKept(`err:${key}`, null);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  async function run(label, fn) {
    if (storeGet(`busy:${key}`)) return undefined;   // one step at a time per card
    setBusy(label);
    setError(null);
    try {
      return await track(key, label, `#/run/${encodeURIComponent(runId)}`, fn);
    } catch (e) {
      setError(e.message);
      return undefined;
    } finally {
      setBusy(null);
      if (!mounted.current) bump(`run:${runId}`);
    }
  }
  return { busy, error, run, setError };
}

function Busy({ label }) {
  if (!label) return null;
  return (
    <div className="row small muted">
      <div className="spinner spinner-sm" /> {label}
    </div>
  );
}

function Err({ msg }) {
  return msg ? <div className="alert alert-bad">{msg}</div> : null;
}

/* ---------- shared pieces ---------- */

function TargetPicker({ value, onChange, best, disabled }) {
  return (
    <div className="stack-sm">
      <div className="targets" role="radiogroup" aria-label="Target score">
        {TARGETS.map((t) => {
          const reach = best == null || best >= t;
          return (
            <button
              key={t}
              type="button"
              role="radio"
              aria-checked={value === t}
              disabled={disabled}
              className={`target ${value === t ? "target-on" : ""}`}
              onClick={() => onChange(t)}
            >
              <span className="target-num">{t}+</span>
              <span className="target-hint">{best == null ? "" : reach ? "reachable" : "stretch"}</span>
            </button>
          );
        })}
      </div>
      {best != null && value > best && (
        <div className="small muted">
          The best Matcher can reach with your current facts is about {r1(best)}. Answering questions and adding
          experience to My info can raise it.
        </div>
      )}
    </div>
  );
}

function Questions({ questions, onAnswer, onSkip }) {
  const [drafts, setDrafts] = useState({});
  const [sending, setSending] = useState({}); // question id -> "answer" | "skip"
  const chain = useRef(Promise.resolve());
  const open = questions.filter((q) => q.status === "open");
  const done = questions.filter((q) => q.status !== "open");
  if (!questions.length) return null;

  // Only the button you pressed reacts; answers sent quickly one after another wait their turn.
  function send(qid, kind, fn) {
    setSending((s) => ({ ...s, [qid]: kind }));
    chain.current = chain.current
      .then(fn)
      .catch(() => {})
      .finally(() => setSending((s) => {
        const n = { ...s };
        delete n[qid];
        return n;
      }));
  }

  return (
    <div className="stack-sm">
      <div className="small strong">Questions ({open.length} open)</div>
      {open.map((q) => {
        const st = sending[q.id];
        const text = (drafts[q.id] || "").trim();
        return (
          <div key={q.id} className="qa">
            <div className="strong">{q.text}</div>
            <div className="small muted">
              {q.why}
              {q.points_at_stake > 0 && q.points_at_stake <= 100 && !/point/i.test(q.why || "") &&
                <> · up to {r1(q.points_at_stake)} points</>}
            </div>
            <textarea
              className="editor qa-input"
              placeholder="Your answer, in your own words (company, what you did, numbers you can defend)…"
              value={drafts[q.id] || ""}
              disabled={!!st}
              onChange={(e) => setDrafts({ ...drafts, [q.id]: e.target.value })}
            />
            <div className="row wrap">
              <button className="btn btn-primary" disabled={!!st || !text}
                      onClick={() => send(q.id, "answer", () => onAnswer(q.id, text))}>
                {st === "answer" ? "Sending…" : "Answer"}
              </button>
              <button className="btn" disabled={!!st} onClick={() => send(q.id, "skip", () => onSkip(q.id))}>
                {st === "skip" ? "Skipping…" : "Skip"}
              </button>
            </div>
          </div>
        );
      })}
      {done.length > 0 && (
        <details className="small">
          <summary className="muted">{done.length} answered or skipped</summary>
          <ul className="warn-list">
            {done.map((q) => (
              <li key={q.id}>
                {q.text} <span className="muted">→ {q.status === "skipped" ? "skipped" : q.answer || q.status}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function Proposals({ proposals, onResolve }) {
  const [sending, setSending] = useState({});
  const chain = useRef(Promise.resolve());
  const pending = proposals.filter((p) => p.status === "pending");
  if (!pending.length) return null;
  function send(pid, ok) {
    setSending((s) => ({ ...s, [pid]: ok ? "save" : "drop" }));
    chain.current = chain.current.then(() => onResolve(pid, ok)).catch(() => {})
      .finally(() => setSending((s) => { const n = { ...s }; delete n[pid]; return n; }));
  }
  return (
    <div className="stack-sm">
      <div className="small strong">Save to your experience notes?</div>
      {pending.map((p) => (
        <div key={p.id} className="qa">
          <div>{p.summary || "New facts from your answer"}</div>
          <div className="small dest">
            → My info ›{" "}
            {p.project
              ? (p.project.employer ? `Experience notes › Company: ${p.project.employer}` : "Experience notes › Personal projects")
              : "Experience notes › Achievements / Links"}
          </div>
          {p.project && (
            <ul className="evidence small">
              {[...(p.project.what_i_did || []), ...(p.project.results || [])].map((x, i) => <li key={i}>{x}</li>)}
              {p.project.employer && <li className="muted">at {p.project.employer}</li>}
            </ul>
          )}
          {p.profile && (
            <ul className="evidence small">
              {[...(p.profile.facts || []), ...(p.profile.links || [])].map((x, i) => <li key={i}>{x}</li>)}
            </ul>
          )}
          {p.warnings?.map((w, i) => <div key={i} className="small warn-text">{w}</div>)}
          {p.missing_info?.length > 0 && (
            <div className="small muted">Would help to know: {p.missing_info.join(", ")}</div>
          )}
          <div className="row wrap">
            <button className="btn btn-primary" disabled={!!sending[p.id]} onClick={() => send(p.id, true)}>
              {sending[p.id] === "save" ? "Saving…" : "Save"}
            </button>
            <button className="btn" disabled={!!sending[p.id]} onClick={() => send(p.id, false)}>Don't save</button>
          </div>
        </div>
      ))}
    </div>
  );
}

function AddContext({ onAdd, busy, label = "Tell Matcher something else about your work" }) {
  const [text, setText] = useState("");
  return (
    <details className="small">
      <summary className="strong">{label}</summary>
      <div className="stack-sm" style={{ marginTop: 8 }}>
        <textarea className="editor qa-input" value={text} onChange={(e) => setText(e.target.value)}
                  placeholder="e.g. At Finlytics I also led the migration to Kubernetes for 6 services." />
        <div>
          <button className="btn" disabled={!!busy || !text.trim()}
                  onClick={async () => { if (await onAdd(text.trim())) setText(""); }}>
            Add
          </button>
        </div>
      </div>
    </details>
  );
}

/* ---------- step 3: gate ---------- */

function GateCard({ runId, gate, setGate, loop, onStarted }) {
  const a = useAction(runId, "gate");
  const [reason, setReason] = useState("");
  const [override, setOverride] = useState({});
  const best = gate?.ceiling_score;
  const defaultTarget = best == null ? 85 : [...TARGETS].reverse().find((t) => best >= t) || 80;
  const [target, setTarget] = useState(defaultTarget);
  useEffect(() => setTarget(defaultTarget), [gate?.ceiling_score]); // eslint-disable-line

  if (!gate) {
    return (
      <div className="card">
        <h2>Step 1 · Is it worth applying?</h2>
        <p className="muted small">
          Matcher works out the best score your resume could reach using everything in My info, checks for deal
          breakers, and asks you about gaps.
        </p>
        <Err msg={a.error} />
        <Busy label={a.busy} />
        <button className="btn btn-primary" disabled={!!a.busy}
                onClick={() => a.run("Checking… (20–60 s)", async () => setGate(await api.gateCompute(runId)))}>
          Check my chances
        </button>
      </div>
    );
  }

  const v = VERDICT[gate.verdict] || { label: gate.verdict, kind: "soft" };
  const started = !!loop;
  const set = (p) => p.then((g) => g && setGate(g));
  const activeKo = gate.knockouts.filter((k) => k.status === "active");
  const dont = gate.verdict === "DONT_APPLY";

  async function improve() {
    await a.run("Starting… (scoring your resume, 10–40 s)", async () => {
      if (gate.decision !== "proceed") setGate(await api.gateDecide(runId, "proceed", reason.trim()));
      onStarted(await api.loopStart(runId, target));
    });
  }

  return (
    <div className="card">
      <h2>Step 1 · Is it worth applying?</h2>
      <div className="score-pair">
        <div><div className="score-num">{r1(gate.current_score)}</div><div className="small muted">now</div></div>
        <div className="muted">→</div>
        <div><div className="score-num">{r1(gate.ceiling_score)}</div><div className="small muted">best possible</div></div>
        <span className={`chip chip-${v.kind}`}>{v.label}</span>
      </div>
      <p className="small">{gate.explanation.how_reached}</p>

      {gate.explanation.top_losses?.length > 0 && (
        <details className="small">
          <summary className="strong">Where the points go</summary>
          <ul className="warn-list">
            {gate.explanation.top_losses.map((l) => (
              <li key={l.key}>
                <strong>{l.label}</strong> −{r1(l.lost)}: {l.reason}
                {l.to_gain && <div className="muted">To gain: {l.to_gain}</div>}
              </li>
            ))}
          </ul>
        </details>
      )}

      {activeKo.length > 0 && (
        <div className="stack-sm">
          <div className="small strong">Deal breakers</div>
          {activeKo.map((k) => (
            <div key={k.id} className="qa">
              <div>{k.message}</div>
              <div className="row wrap">
                <input className="input" placeholder="Why it's fine (optional)" value={override[k.id] || ""}
                       onChange={(e) => setOverride({ ...override, [k.id]: e.target.value })} />
                <button className="btn" disabled={!!a.busy}
                        onClick={() => a.run("Saving…", () => set(api.gateOverride(runId, k.id, override[k.id] || "")))}>
                  Ignore this
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {gate.notes?.length > 0 && <ul className="small muted">{gate.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>}

      {!started && (
        <>
          <Questions
            questions={gate.questions}
            onAnswer={(qid, text) => a.run("Reading your answer…", () => set(api.gateAnswer(runId, qid, text)))}
            onSkip={(qid) => a.run("Skipping…", () => set(api.gateSkip(runId, qid)))}
          />
          <Proposals proposals={gate.proposals}
                     onResolve={(pid, ok) => a.run("Saving…", () => set(api.gateProposal(runId, pid, ok)))} />
          <AddContext busy={a.busy}
                      onAdd={(text) => a.run("Reading…", () => set(api.gateContext(runId, text)).then(() => true))} />
          {gate.needs_recompute && (
            <div className="alert alert-warn row wrap">
              Something changed since this was worked out (your notes or your score). Recalculate to update it.
              <button className="btn" disabled={!!a.busy}
                      onClick={() => a.run("Recalculating… (20–60 s)", async () => setGate(await api.gateCompute(runId)))}>
                Recalculate
              </button>
            </div>
          )}

          <div className="decide">
            <div className="strong">Improve it to</div>
            <TargetPicker value={target} onChange={setTarget} best={best} disabled={!!a.busy} />
            {dont && (
              <input className="input" placeholder="Matcher advises against this one. Why go ahead? (required)"
                     value={reason} onChange={(e) => setReason(e.target.value)} />
            )}
            <Err msg={a.error} />
            <Busy label={a.busy} />
            <div className="row wrap">
              <button className="btn btn-primary" disabled={!!a.busy || gate.needs_recompute || (dont && !reason.trim())}
                      onClick={improve}>
                Improve my resume to {target}+
              </button>
              {gate.decision !== "stop" && (
                <button className="btn" disabled={!!a.busy}
                        onClick={() => a.run("Saving…", () => set(api.gateDecide(runId, "stop", "Not applying")))}>
                  Don't apply
                </button>
              )}
            </div>
            {gate.decision === "stop" && <div className="small muted">You decided not to apply. You can still change your mind.</div>}
          </div>
        </>
      )}
      {started && <div className="small muted">You chose to improve this resume. Steps below.</div>}
    </div>
  );
}

/* ---------- step 4: improve loop ---------- */

const words = (t) => (t || "").trim().split(/\s+/).filter((w) => /[A-Za-z0-9]/.test(w)).length;

function Change({ c, busy, onAction, wordCap = 32 }) {
  const [mode, setMode] = useState(null); // edit | revise
  const [text, setText] = useState(c.new || "");
  const kind = { replace_bullet: "Rewrite", add_bullet: "New bullet", remove_bullet: "Remove",
                 merge_bullets: "Merge two bullets", set_summary: "Summary" }[c.kind] || c.kind;
  const isBullet = ["replace_bullet", "add_bullet", "merge_bullets"].includes(c.kind);
  const n = words(text);
  const st = { accepted: "good", rejected: "soft", blocked: "bad", pending: "warn" }[c.status];
  return (
    <div className={`change change-${c.status}`}>
      <div className="row wrap small">
        <span className="strong">{kind}</span>
        {c.job_label && <span className="muted">{c.job_label}</span>}
        <span className={`chip chip-${st}`}>{c.status}</span>
        {c.lines_delta != null && <span className="muted">{c.lines_delta > 0 ? "+" : ""}{c.lines_delta} line(s)</span>}
      </div>
      <div className="small dest">
        → {c.kind === "set_summary" ? "Resume › Summary" : c.job_label === "Achievements" ? "Resume › Achievements"
          : `Resume › ${c.job_label}`}
        {c.from_notes && <> · from My info › {c.from_notes.replace(" > ", " › ")}</>}
      </div>
      {c.old && <div className="old">{c.old}</div>}
      {c.old2 && <div className="old">{c.old2}</div>}
      {c.new && (
        <div className="new">
          {c.new}
          {isBullet && (
            <span className={`small wc ${words(c.new) > wordCap ? "wc-bad" : ""}`}> · {words(c.new)}/{wordCap} words</span>
          )}
        </div>
      )}
      {c.why && <div className="small muted">{c.why}</div>}
      {c.sources?.length > 0 && (
        <details className="small">
          <summary className="muted">Based on</summary>
          <ul className="evidence">{c.sources.map((s, i) => <li key={i}>{s.type}: “{s.quote}”</li>)}</ul>
        </details>
      )}
      {c.truth_issues?.map((t, i) => <div key={i} className="small warn-text">{t}</div>)}
      {mode && (
        <div className="stack-sm">
          {mode === "edit" && (c.old || c.old2) && (
            <div className="row wrap small">
              <span className="muted">Start from:</span>
              <button className="linklike" onClick={() => setText(c.new || "")}>suggestion</button>
              <button className="linklike" onClick={() => setText(c.old || "")}>original</button>
              <button className="linklike"
                      onClick={() => setText([c.old, c.old2, c.new].filter(Boolean).join(" | "))}>
                both (then cut what you don't need)
              </button>
            </div>
          )}
          <textarea className="editor qa-input" value={text} onChange={(e) => setText(e.target.value)}
                    placeholder={mode === "revise" ? "What should change? e.g. keep the first half of the original, add the latency result" : ""} />
          {mode === "edit" && isBullet && (
            <div className={`small wc ${n > wordCap ? "wc-bad" : ""}`}>
              {n}/{wordCap} words{n > wordCap ? " — too long for one line pair; trim it" : ""}
            </div>
          )}
          <div className="row wrap">
            <button className="btn btn-primary" disabled={!!busy || !text.trim()}
                    onClick={async () => {
                      await onAction(c.id, mode, mode === "edit" ? { text } : { feedback: text });
                      setMode(null);
                    }}>
              {mode === "edit" ? "Use my wording" : "Ask AI to revise"}
            </button>
            <button className="btn" onClick={() => setMode(null)}>Cancel</button>
          </div>
        </div>
      )}
      {!mode && (
        <div className="row wrap">
          <button className="btn btn-sm btn-primary" disabled={!!busy || c.status === "blocked" || c.status === "accepted"}
                  onClick={() => onAction(c.id, "accept")}>Accept</button>
          <button className="btn btn-sm" disabled={!!busy || c.status === "rejected"} onClick={() => onAction(c.id, "reject")}>Reject</button>
          {c.kind !== "remove_bullet" && (
            <button className="btn btn-sm" disabled={!!busy} onClick={() => { setText(c.new || ""); setMode("edit"); }}>Edit</button>
          )}
          <button className="btn btn-sm" disabled={!!busy} onClick={() => { setText(""); setMode("revise"); }}>Revise</button>
        </div>
      )}
    </div>
  );
}

function Versions({ runId, loop, busy, onRollback, onDownload }) {
  const [view, setView] = useState(null);
  const [text, setText] = useState("");
  async function show(n) {
    if (view === n) return setView(null);
    setView(n);
    setText("Loading…");
    try {
      setText((await api.loopVersion(runId, n)).text);
    } catch (e) {
      setText(e.message);
    }
  }
  const best = Math.max(...loop.versions.map((v) => v.score));
  return (
    <details className="small">
      <summary className="strong">All versions ({loop.versions.length})</summary>
      <div className="stack-sm" style={{ marginTop: 8 }}>
        {loop.versions.map((v) => (
          <div key={v.n} className="version">
            <div className="row wrap">
              <span className="strong">v{v.n}</span>
              <span className={`chip ${v.score >= loop.target ? "chip-good" : ""}`}>{r1(v.score)}</span>
              {v.score === best && loop.versions.length > 1 && <span className="chip chip-good">best</span>}
              {v.n === loop.current_version && <span className="chip">current</span>}
              <span className="muted">{v.note}</span>
            </div>
            <div className="row wrap">
              <button className="linklike" onClick={() => show(v.n)}>{view === v.n ? "Hide" : "View"}</button>
              <button className="linklike" disabled={!!busy} onClick={() => onDownload(v.n)}>Download this one</button>
              {v.n !== loop.current_version && (
                <button className="linklike" disabled={!!busy} onClick={() => onRollback(v.n)}>Continue from this one</button>
              )}
            </div>
            {view === v.n && <pre className="version-text">{text}</pre>}
          </div>
        ))}
      </div>
    </details>
  );
}

const VALUE = {
  strong: { label: "strong", kind: "good" },
  ok: { label: "helps a little", kind: "warn" },
  rewordable: { label: "earns nothing as worded", kind: "warn" },
  no_value: { label: "earns nothing", kind: "bad" },
};

function ResumeMap({ rep, err }) {
  if (err) return <div className="small muted">{err}</div>;
  if (!rep) return <div className="small muted">Loading the resume map…</div>;
  const dead = rep.bullets.filter((b) => b.status === "no_value").length;
  const reword = rep.bullets.filter((b) => b.status === "rewordable").length;
  return (
    <details className="small resume-map">
      <summary className="strong">
        Resume map (v{rep.version}){reword > 0 && <span className="chip chip-warn"> {reword} need better wording</span>}
        {dead > 0 && <span className="chip chip-bad"> {dead} earn nothing</span>}
      </summary>
      <div className="muted" style={{ margin: "6px 0" }}>
        What each bullet earns for this job and where it came from. Bullets that earn nothing are the first
        to replace with something relevant from My info. Each job has a bullet limit and each bullet a word
        limit, so the resume fits one page; the AI combines the strongest facts into the space.
      </div>
      {rep.jobs.map((j) => (
        <div key={j.job} className="map-job">
          <div className="row wrap">
            <span className="strong">{j.job_label}</span>
            <span className={`chip ${j.count > j.cap ? "chip-bad" : j.count === j.cap ? "chip-warn" : ""}`}>
              {j.count} of {j.cap} bullets
            </span>
          </div>
          <ul className="map-list">
            {rep.bullets.filter((b) => b.job === j.job).map((b) => (
              <li key={b.bullet} className={`map-${b.status}`}>
                <div>{b.text}</div>
                <div className="row wrap map-tags">
                  <span className={`chip chip-${VALUE[b.status].kind}`}>{VALUE[b.status].label} · {b.value}</span>
                  {b.shows.map((x) => <span key={x} className="chip chip-soft">{x}</span>)}
                  {b.judge_quoted && <span className="chip chip-soft">quoted by the judge</span>}
                  <span className={`chip ${b.too_long ? "chip-bad" : "chip-soft"}`}>{b.words}/{rep.word_cap} words</span>
                  <span className="muted">{b.origin_label}</span>
                </div>
                {(b.could_show.length > 0 || (b.status === "rewordable" && b.could_cover.length > 0)) && (
                  <div className="could">
                    Better wording could show{" "}
                    {b.could_show.length ? <strong>{b.could_show.join(", ")}</strong> : <>the duty “{b.could_cover[0]}”</>}
                    {" "}using your notes:
                    <ul>{b.related_notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
                  </div>
                )}
              </li>
            ))}
          </ul>
          {rep.candidates.filter((c) => c.job === j.job).length > 0 && (
            <details className="map-cands">
              <summary className="muted">
                {rep.candidates.filter((c) => c.job === j.job).length} fact(s) in My info for this job not on the resume
                (best first)
              </summary>
              <ul className="map-list">
                {rep.candidates.filter((c) => c.job === j.job).map((c, i) => (
                  <li key={i}>
                    <div>{c.text}</div>
                    <div className="row wrap map-tags">
                      <span className="chip chip-soft">worth {c.value}</span>
                      {c.shows.map((x) => <span key={x} className="chip chip-soft">{x}</span>)}
                    </div>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      ))}
    </details>
  );
}

function LoopCard({ runId, loop, setLoop, setExport }) {
  const a = useAction(runId, "loop");
  const [note, setNote] = useState("");
  const [rep, setRep] = useState(null);
  const [repErr, setRepErr] = useState(null);
  useEffect(() => {
    setRep(null);
    setRepErr(null);
    api.loopBullets(runId, loop.current_version).then(setRep).catch((e) => setRepErr(e.message));
  }, [runId, loop.current_version, loop.rounds.length]);
  const r = loop.rounds[loop.rounds.length - 1];
  const cur = loop.versions.find((v) => v.n === loop.current_version);
  const score = cur ? cur.score : r?.score_before;
  const set = (p) => p.then((s) => s && setLoop(s));
  const fit = r?.purpose === "fit";
  const pending = r ? r.changes.filter((c) => c.status === "pending").length : 0;
  const accepted = r ? r.changes.filter((c) => c.status === "accepted").length : 0;

  async function download(version) {
    await a.run("Building your one-page Word resume… (10–60 s)", async () => {
      setLoop(await api.loopFinish(runId, version ?? loop.current_version));
      setExport(await api.exportBuild(runId));
    });
  }

  return (
    <div className="card">
      <h2>Step 2 · Improve {fit ? "(fitting one page)" : ""}</h2>
      <div className="score-head">
        <div className="score-pair">
          <div><div className="score-num">{r1(score)}</div><div className="small muted">current (v{loop.current_version})</div></div>
          <div className="muted">/</div>
          <div><div className="score-num">{loop.target}+</div><div className="small muted">target</div></div>
        </div>
      </div>
      <span className="bar"><span className="bar-fill" style={{ width: `${Math.min(100, (score / loop.target) * 100)}%`, background: score >= loop.target ? "var(--good)" : undefined }} /></span>

      <details className="small" style={{ marginTop: 10 }}>
        <summary className="strong">Change target</summary>
        <div style={{ marginTop: 8 }}>
          <TargetPicker value={loop.target} best={null} disabled={!!a.busy}
                        onChange={(t) => a.run("Saving…", () => set(api.loopTarget(runId, t)))} />
        </div>
      </details>

      {loop.recommendation && (
        <div className={`alert ${loop.flags.includes("score_dropped") ? "alert-bad" : loop.flags.includes("target_reached") ? "alert-ok" : "alert-info"}`}>
          {loop.recommendation}
        </div>
      )}

      {r && !loop.finished && (
        <div className="stack-sm">
          <div className="small muted">Round {r.n} · started at {r1(r.score_before)}</div>

          {r.stage === "questions" && (
            <>
              {r.problems.length > 0 && (
                <details className="small" open>
                  <summary className="strong">What's costing points</summary>
                  <ul className="warn-list">
                    {r.problems.map((p) => (
                      <li key={p.id}>
                        <strong>{p.label}</strong> −{r1(p.points_lost)}{" "}
                        <span className={`chip chip-${(FIX_BY[p.fix_by] || {}).kind || "soft"}`}>{(FIX_BY[p.fix_by] || {}).label || p.fix_by}</span>
                        <div className="muted">{p.detail || p.cause}</div>
                      </li>
                    ))}
                  </ul>
                </details>
              )}
              <Questions
                questions={r.questions}
                onAnswer={(qid, text) => a.run("Reading your answer…", () => set(api.loopAnswer(runId, qid, text)))}
                onSkip={(qid) => a.run("Skipping…", () => set(api.loopSkip(runId, qid)))}
              />
              <Proposals proposals={loop.proposals.filter((p) => r.proposal_ids.includes(p.id))}
                         onResolve={(pid, ok) => a.run("Saving…", () => set(api.loopProposal(runId, pid, ok)))} />
              <AddContext busy={a.busy}
                          onAdd={(text) => a.run("Reading…", () => set(api.loopContext(runId, text)).then(() => true))} />
              <details className="small">
                <summary className="strong">Style instructions for this round{r.notes.length ? ` (${r.notes.length})` : ""}</summary>
                <div className="stack-sm" style={{ marginTop: 8 }}>
                  {r.notes.map((n, i) => <div key={i} className="muted">• {n}</div>)}
                  <div className="row wrap">
                    <input className="input" value={note} onChange={(e) => setNote(e.target.value)}
                           placeholder="e.g. keep bullets under 2 lines, lead with Kafka work" />
                    <button className="btn" disabled={!!a.busy || !note.trim()}
                            onClick={() => a.run("Saving…", () => set(api.loopNotes(runId, note.trim())).then(() => setNote("")))}>
                      Add
                    </button>
                  </div>
                </div>
              </details>
              <button className="btn btn-primary btn-big" disabled={!!a.busy}
                      onClick={() => a.run("Planning what to keep, reword, remove and add, then writing and checking each edit… (20–90 s)", () => set(api.loopSuggest(runId)))}>
                Suggest improvements
              </button>
            </>
          )}

          {r.stage === "review" && (
            <>
              {r.plan?.bullets && (() => {
                const acts = Object.values(r.plan.bullets).map((x) => x.action);
                const n = (a) => acts.filter((x) => x === a).length;
                const parts = [
                  n("reword") && `reword ${n("reword")}`,
                  n("merge") && `merge ${n("merge")} pair(s)`,
                  n("remove") && `remove ${n("remove")}`,
                  r.plan.add?.length && `add ${r.plan.add.length} from My info`,
                  r.plan.summary === "rewrite" && "rewrite the summary",
                ].filter(Boolean);
                return (
                  <div className="alert alert-info small">
                    <strong>Plan:</strong> {parts.length ? parts.join(" · ") : "nothing to change"}
                    {r.plan.used_rules && <span className="muted"> (made by rules: the AI planning step didn't answer)</span>}
                  </div>
                );
              })()}
              <div className="small strong">
                Review each suggestion ({r.changes.length}) · {pending} left to decide
              </div>
              {r.changes.map((c) => (
                <Change key={c.id} c={c} busy={a.busy} wordCap={rep?.word_cap || 32}
                        onAction={(cid, action, extra) =>
                          a.run(action === "revise" ? "Revising…" : "Saving…", () => set(api.loopChange(runId, cid, action, extra)))} />
              ))}
              <div className="row wrap">
                <button className="btn btn-primary btn-big" disabled={!!a.busy || pending > 0}
                        onClick={() => a.run(`Applying ${accepted} edit(s) and re-scoring blind… (10–40 s)`, async () => {
                          if (fit) {
                            const out = await api.fitApply(runId);
                            setLoop(out.loop);
                            setExport(out.export);
                          } else setLoop(await api.loopApply(runId));
                        })}>
                  {pending > 0 ? `Decide on ${pending} more` : `Apply ${accepted} edit(s) and re-score`}
                </button>
                <button className="btn" disabled={!!a.busy}
                        onClick={() => a.run("Getting more suggestions…", () => set(api.loopSuggest(runId)))}>
                  More suggestions
                </button>
              </div>
            </>
          )}

          {(r.stage === "applied" || r.stage === "closed") && (
            <>
              <div className="alert alert-info">
                Round {r.n}: {r1(r.score_before)} → <strong>{r1(r.score_after)}</strong>
              </div>
              <button className="btn btn-primary btn-big" disabled={!!a.busy}
                      onClick={() => a.run("Finding what to improve next… (5–20 s)", () => set(api.loopNext(runId)))}>
                Next round
              </button>
            </>
          )}
        </div>
      )}

      <Err msg={a.error} />
      <Busy label={a.busy} />

      <div className="download-bar">
        <button className="btn btn-primary" disabled={!!a.busy} onClick={() => download()}>
          ⬇ Download this version ({r1(score)})
        </button>
        <span className="small muted">Good enough? Download any time. You can keep improving afterwards.</span>
      </div>

      <ResumeMap rep={rep} err={repErr} />

      <Versions runId={runId} loop={loop} busy={a.busy}
                onRollback={(n) => a.run("Switching…", () => set(api.loopRollback(runId, n)))}
                onDownload={download} />
    </div>
  );
}

/* ---------- step 5: export ---------- */

const ATS_LEVEL = { pass: ["good", "✓"], info: ["soft", "i"], warn: ["warn", "!"], fail: ["bad", "✗"] };

function AtsList({ checks }) {
  if (!checks.length) return null;
  const count = (l) => checks.filter((c) => c.level === l).length;
  const bad = count("fail");
  return (
    <details className="small ats" open={bad > 0}>
      <summary className="strong">
        ATS check: {count("pass")} passed
        {count("warn") > 0 && <span className="chip chip-warn"> {count("warn")} to improve</span>}
        {bad > 0 && <span className="chip chip-bad"> {bad} problem(s)</span>}
        {count("info") > 0 && <span className="chip chip-soft"> {count("info")} note(s)</span>}
      </summary>
      <ul className="ats-list">
        {checks.map((c) => (
          <li key={c.id} className={`ats-${c.level}`}>
            <span className={`ats-mark chip chip-${ATS_LEVEL[c.level][0]}`}>{ATS_LEVEL[c.level][1]}</span>
            <div>
              <div>{c.label}</div>
              {c.detail && <div className="muted">{c.detail}</div>}
            </div>
          </li>
        ))}
      </ul>
    </details>
  );
}

function FreshScore({ runId, exp }) {
  const a = useAction(runId, "fresh");
  const [list, setList] = useState([]);
  useEffect(() => {
    api.freshScores(runId).then(setList).catch(() => {});
  }, [runId]);
  const last = list[list.length - 1];
  const stale = last && last.version !== exp.version;
  return (
    <div className="fresh">
      <div className="row wrap">
        <button className="btn" disabled={!!a.busy}
                onClick={() => a.run("Reading your file like an ATS, then scoring it blind… (20–60 s)",
                                     async () => { const f = await api.freshScore(runId); setList((l) => [...l, f]); })}>
          ↻ Check the score again (fresh)
        </button>
        <span className="small muted">
          Reads the exported file from scratch, like an ATS, and a blind judge scores it. It never sees earlier
          scores, versions or edits.
        </span>
      </div>
      <Err msg={a.error} />
      <Busy label={a.busy} />
      {last && (
        <div className="fresh-result">
          <div className="score-pair">
            <div><div className="score-num">{r1(last.score.total)}</div><div className="small muted">fresh score (v{last.version})</div></div>
            {last.loop_score != null && (
              <div><div className="score-num muted">{r1(last.loop_score)}</div><div className="small muted">during improving</div></div>
            )}
            {stale && <span className="chip chip-warn">from an older version; check again</span>}
          </div>
          <div className="small">
            The ATS read: {last.read_back.name || "no name"} · {last.read_back.email || "no email"} ·{" "}
            {last.read_back.jobs} job(s) · {last.read_back.bullets} bullet(s) · {last.read_back.skills} skill(s)
          </div>
          {last.differences.length > 0 ? (
            <ul className="small warn-list">{last.differences.map((d, i) => <li key={i} className="warn-text">{d}</li>)}</ul>
          ) : (
            <div className="small" style={{ color: "var(--good)" }}>Everything on your resume was read back correctly.</div>
          )}
          <details className="small">
            <summary className="muted">Score breakdown</summary>
            <ul className="warn-list">
              {last.score.dimensions.map((d) => (
                <li key={d.key}>{d.label}: {r1(d.points)}/{d.weight}{d.reason ? ` · ${d.reason}` : ""}</li>
              ))}
            </ul>
          </details>
          {list.length > 1 && (
            <div className="small muted">Earlier fresh checks: {list.slice(0, -1).map((f) => `${r1(f.score.total)} (v${f.version})`).join(", ")}</div>
          )}
        </div>
      )}
    </div>
  );
}

function SaveToLibrary({ runId, version }) {
  const k = `${runId}:save:v${version}`;            // kept across pages, per version
  const [open, setOpen] = useKept(`${k}:open`, false);
  const [name, setName] = useKept(`${k}:name`, "");
  const [notes, setNotes] = useKept(`${k}:notes`, "");
  const [busy, setBusy] = useKept(`${k}:busy`, false);
  const [error, setError] = useKept(`${k}:error`, null);
  const [saved, setSaved] = useKept(`${k}:saved`, null);
  async function save() {
    setBusy(true); setError(null);
    try {
      const r = await track(k, "Saving to My resumes", `#/run/${encodeURIComponent(runId)}`,
        () => api.saveResume(runId, { version, name: name.trim() || null, notes }));
      setSaved(r);
      setOpen(false);
      bump("resumes");
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  if (saved) return (
    <div className="alert alert-ok small">
      Saved v{version} to My resumes as <strong>{saved.name}</strong>. <a href="#/me/resumes">See it</a>
    </div>
  );
  return (
    <div className="card card-flat stack-sm">
      {!open ? (
        <div className="row wrap">
          <button className="btn" onClick={() => setOpen(true)}>💾 Save to my resumes</button>
          <span className="small muted">Keep this version so future jobs can start from it.</span>
        </div>
      ) : (<>
        <input className="input" placeholder="Name (empty = job title · company)" value={name} maxLength={80}
               onChange={(e) => setName(e.target.value)} disabled={busy} />
        <input className="input" placeholder="Notes (optional), e.g. good for data platform roles" value={notes}
               onChange={(e) => setNotes(e.target.value)} disabled={busy} />
        {busy && <div className="small muted">Saving and rating every skill strictly… 20–60 s</div>}
        {error && <div className="alert alert-bad small">{error}</div>}
        <div className="row wrap">
          <button className="btn btn-primary" disabled={busy} onClick={save}>Save v{version}</button>
          <button className="btn" disabled={busy} onClick={() => setOpen(false)}>Cancel</button>
        </div>
      </>)}
    </div>
  );
}

function ExportCard({ runId, exp, setExport, setLoop }) {
  const a = useAction(runId, "export");
  const [show, setShow] = useState(false);
  const [picked, setPicked] = useState(() => new Set(exp.trim_options.filter((t) => t.suggested).map((t) => t.id)));
  useEffect(() => setPicked(new Set(exp.trim_options.filter((t) => t.suggested).map((t) => t.id))), [exp]);
  const ok = exp.status === "fitted";
  return (
    <div className="card">
      <h2>Step 3 · Your one-page resume (v{exp.version})</h2>
      <div className={`alert ${ok ? "alert-ok" : "alert-warn"}`}>
        {ok ? `Fits exactly one A4 page (${Math.round(exp.fill * 100)}% filled).` : exp.message || exp.status}
      </div>
      <div className="row wrap">
        <button className="btn" onClick={() => setShow(!show)}>{show ? "Hide preview" : "👁 View resume"}</button>
        <a className="btn btn-primary" href={api.fileUrl(runId, "docx")}>⬇ Word (.docx)</a>
        <a className="btn" href={`${api.fileUrl(runId, "pdf")}?inline=true`} target="_blank" rel="noreferrer">Open PDF</a>
        <a className="btn" href={api.fileUrl(runId, "report")}>Report</a>
      </div>
      {show && (
        <div className="preview">
          <img src={api.previewUrl(runId, `${exp.version}-${exp.history.length}-${exp.fill}`)}
               alt="Your one-page resume" />
        </div>
      )}

      <AtsList checks={exp.ats || []} />
      <FreshScore runId={runId} exp={exp} />

      {exp.status === "overflow" && exp.trim_options.length > 0 && (
        <div className="stack-sm">
          <div className="small strong">Too long by about {exp.overflow_lines} line(s). Pick bullets to cut:</div>
          {exp.trim_options.map((t) => (
            <label key={t.id} className="trim">
              <input type="checkbox" checked={picked.has(t.id)}
                     onChange={(e) => {
                       const n = new Set(picked);
                       e.target.checked ? n.add(t.id) : n.delete(t.id);
                       setPicked(n);
                     }} />
              <span>
                {t.text} <span className="muted small">· {t.job_label} · {t.reason}</span>
              </span>
            </label>
          ))}
          <div>
            <button className="btn btn-primary" disabled={!!a.busy || picked.size === 0}
                    onClick={() => a.run("Cutting and re-scoring…", async () => setExport(await api.exportTrim(runId, [...picked])))}>
              Cut {picked.size} bullet(s)
            </button>
          </div>
        </div>
      )}

      {!ok && (
        <div className="row wrap">
          <button className="btn" disabled={!!a.busy}
                  onClick={() => a.run("Looking at the page…", async () => setLoop(await api.fitStart(runId)))}>
            Let AI fit it to one page
          </button>
        </div>
      )}

      {exp.unused_facts?.length > 0 && (
        <details className="small">
          <summary className="muted">{exp.unused_facts.length} fact(s) from My info not on this page</summary>
          <ul className="evidence">{exp.unused_facts.map((f, i) => <li key={i}>{f}</li>)}</ul>
        </details>
      )}

      <SaveToLibrary runId={runId} version={exp.version} />

      <Err msg={a.error} />
      <Busy label={a.busy} />
      <div className="download-bar">
        <button className="btn" disabled={!!a.busy}
                onClick={() => a.run("Reopening…", async () => setLoop(await api.exportReopen(runId)))}>
          Keep improving
        </button>
        <span className="small muted">Not happy with the score? Go back for more rounds and download again later.</span>
      </div>
    </div>
  );
}

/* ---------- the whole flow ---------- */

export default function Improve({ runId }) {
  const [state, setState] = useState(null); // {gate, loop, exp}
  const [error, setError] = useState(null);

  const changed = useChanged(`run:${runId}`);   // a step finished while you were on another page
  useEffect(() => {
    Promise.all([maybe(api.gate(runId)), maybe(api.loop(runId)), maybe(api.exportGet(runId))])
      .then(([gate, loop, exp]) => setState({ gate, loop, exp }))
      .catch((e) => setError(e.message));
  }, [runId, changed]);

  if (error) return <div className="alert alert-bad">{error}</div>;
  if (!state) return <div className="muted small">Loading…</div>;
  const { gate, loop, exp } = state;
  const patch = (k) => (v) => setState((s) => ({ ...s, [k]: v }));

  return (
    <>
      <GateCard runId={runId} gate={gate} setGate={patch("gate")} loop={loop} onStarted={patch("loop")} />
      {loop && <LoopCard runId={runId} loop={loop} setLoop={patch("loop")} setExport={patch("exp")} />}
      {loop?.finished && exp && (
        <ExportCard runId={runId} exp={exp} setExport={patch("exp")} setLoop={patch("loop")} />
      )}
    </>
  );
}
