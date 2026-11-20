import { useEffect, useRef, useState } from "react";
import { api, uploadNotes } from "./api.js";
import MyResumes from "./MyResumes.jsx";

const TABS = [
  { id: "resumes", label: "Resumes" },
  { id: "experience", label: "Experience", file: "experience.txt" },
  { id: "skills", label: "Skills", file: "skills.txt" },
  { id: "answers", label: "Saved answers" },
];

// Answers you gave during runs, kept as projects. A project is only used for a resume that has
// its company; here you can see them all and remove ones you don't want (e.g. test data).
function SavedAnswers() {
  const [ctx, setCtx] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(null);
  useEffect(() => { api.context().then((r) => setCtx(r.parsed)).catch((e) => setError(e.message)); }, []);

  async function remove(p) {
    if (!window.confirm(`Remove "${p.name}"${p.employer ? ` (${p.employer})` : ""}? Matcher won't use it again.`)) return;
    setBusy(p.id); setError(null);
    try { setCtx((await api.deleteSavedAnswer(p.id)).parsed); }
    catch (e) { setError(e.message); }
    finally { setBusy(null); }
  }

  if (error && !ctx) return <div className="alert alert-bad">{error}</div>;
  if (!ctx) return <div className="muted small">Loading…</div>;
  return (
    <div className="stack-sm">
      <p className="small muted">
        Answers you gave the AI during runs, saved as projects. Each one is used only for a resume that has the
        same company. Remove any you don't want Matcher to use.
      </p>
      {error && <div className="alert alert-bad small">{error}</div>}
      {ctx.projects.length === 0 && <div className="muted small">No saved answers.</div>}
      {ctx.projects.map((p) => (
        <div key={p.id} className="card card-flat stack-sm">
          <div className="strong">{p.name}</div>
          <div className="small muted">
            {[p.employer, p.role, [p.start, p.end].filter(Boolean).join(" – ")].filter(Boolean).join(" · ") || "No company given"}
          </div>
          {p.what_i_did?.length > 0 && (
            <ul className="small">{p.what_i_did.slice(0, 4).map((x, i) => <li key={i}>{x}</li>)}</ul>
          )}
          <div>
            <button className="btn btn-sm btn-danger" disabled={!!busy} onClick={() => remove(p)}>
              {busy === p.id ? "Removing…" : "Remove"}
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

const HELP = {
  experience:
    "Write your experience in plain text, in as much detail as you like: what you built, owned and improved, " +
    "with numbers you can defend. Start each job with a 'Company: <name>' line; everything under it belongs " +
    "to that job. Lines still showing [brackets] are ignored until you fill them in.",
  skills:
    "Write your skills in plain text: each skill, where you used it and what you did with it. " +
    "Name the company in each line. A skill tied to real work can become a full resume bullet; one that's " +
    "only listed earns half credit. Lines still showing [brackets] are ignored.",
};

// Ctrl+S / Cmd+S saves while you're typing.
function useSaveShortcut(onSave) {
  const ref = useRef(onSave);
  ref.current = onSave;
  useEffect(() => {
    const h = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        ref.current();
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);
}

// Roughly the server's rule: skip comments (#), unfilled [placeholders], labels like "What I did:"
// and "Company:" / "Section:" heading lines.
const factLines = (text) =>
  text.split("\n").filter((l) => {
    const t = l.trim();
    if (!t || (t.startsWith("#") && !t.startsWith("##"))) return false;
    if (/\[[^\]]*\]/.test(t)) return false;
    if (t.endsWith(":") && t.length <= 40) return false;
    return !/^(company|employer|section)\s*[:-]/i.test(t) && !t.startsWith("##");
  });

function NotesEditor({ kind }) {
  const [saved, setSaved] = useState(null);
  const [text, setText] = useState("");
  const [status, setStatus] = useState(null); // {kind: ok|bad, msg}
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState("append");
  const fileInput = useRef(null);

  useEffect(() => {
    api.notes().then((all) => {
      setSaved(all[kind].text);
      setText(all[kind].text);
    }).catch((e) => setStatus({ kind: "bad", msg: e.message }));
  }, [kind]);

  const dirty = text !== (saved ?? "");
  const n = factLines(text).length;
  useSaveShortcut(() => !busy && save());

  async function save() {
    setBusy(true);
    try {
      const res = await api.saveNotes(kind, text);
      setSaved(res.text);
      setText(res.text);
      setStatus({ kind: "ok", msg: `Saved. Matcher will use ${res.facts.length} line(s) from ${kind}.txt.` });
    } catch (e) {
      setStatus({ kind: "bad", msg: e.message });
    } finally {
      setBusy(false);
    }
  }

  async function upload(file) {
    if (!file) return;
    if (dirty && !window.confirm("You have unsaved edits here. Uploading will discard them. Continue?")) return;
    setBusy(true);
    try {
      const res = await uploadNotes(kind, file, mode);
      setSaved(res.text);
      setText(res.text);
      setStatus({
        kind: "ok",
        msg: `${mode === "append" ? "Added" : "Replaced with"} ${file.name}. ${res.facts.length} line(s) in use.`,
      });
    } catch (e) {
      setStatus({ kind: "bad", msg: e.message });
    } finally {
      setBusy(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  if (saved === null && !status) return <div className="muted">Loading…</div>;
  return (
    <div className="stack-sm">
      <textarea
        className="editor editor-tall"
        value={text}
        onChange={(e) => setText(e.target.value)}
        spellCheck
        aria-label={`${kind} notes`}
      />
      <div className="savebar row wrap">
        <button className="btn btn-primary" onClick={save} disabled={busy}>
          {busy ? "Saving…" : "Save"}
        </button>
        <span className="small muted">
          {dirty ? "Unsaved changes" : "All saved"} · {n} fact line(s) · Ctrl+S saves
        </span>
      </div>
      <div className="row wrap">
        <span className="upload-group">
          <button className="btn" onClick={() => fileInput.current?.click()} disabled={busy}>
            Upload a file
          </button>
          <select value={mode} onChange={(e) => setMode(e.target.value)} aria-label="Upload mode" disabled={busy}>
            <option value="append">add to the end</option>
            <option value="replace">replace everything</option>
          </select>
          <input ref={fileInput} type="file" accept=".docx,.pdf,.txt,.md,text/plain,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                 hidden onChange={(e) => { upload(e.target.files[0]); e.target.value = ""; }} />
        </span>
        <span className="small muted">Word (.docx), PDF or text. Long documents are fine.</span>
      </div>
      {status && <div className={`alert ${status.kind === "ok" ? "alert-ok" : "alert-bad"}`}>{status.msg}</div>}
    </div>
  );
}

export default function MyInfo({ tab }) {
  const current = TABS.find((t) => t.id === tab) || TABS[0];
  return (
    <section className="stack">
      <div>
        <h1>My info</h1>
        <p className="muted">
          Everything Matcher may use to improve your resume, besides the resume itself. It never adds a fact
          that isn't here or on your resume. Answers you give the AI during a run are added to your
          experience notes too. Everything is saved to your account.
        </p>
      </div>
      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <a key={t.id} role="tab" aria-selected={t.id === current.id}
             className={`tab ${t.id === current.id ? "tab-on" : ""}`} href={`#/me/${t.id}`}>
            {t.label}
          </a>
        ))}
      </div>
      {current.id === "resumes" ? <MyResumes /> : current.id === "answers" ? (
        <div className="card"><SavedAnswers /></div>
      ) : (
        <div className="card">
          <p className="small">{HELP[current.id]}</p>
          <NotesEditor key={current.id} kind={current.id} />
        </div>
      )}
    </section>
  );
}
