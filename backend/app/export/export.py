"""Step 5: turn the final version into a one-page A4 Word resume (interactive).

fitted      -> done; download the .docx
overflow    -> Matcher suggests the least useful bullets to cut; you pick which.
               The trimmed resume becomes a new version and is blind re-scored.
underfilled -> Matcher says how many lines are free and lists template facts not used yet.
               Reopen the loop to add content, or keep the file as it is.
"""
from __future__ import annotations

import asyncio
import math
import re
from pathlib import Path

from app import runs
from app.export.builder import build_docx, format_spec  # noqa: F401  (re-exported for tests)
from app.export.fit import FITTED, OVERFLOW, UNDERFILLED, fit_one_page
from app.export.report import write_report
from app.llm.base import LLMClient
from app.loop import loop as loop_mod
from app.parsing.pipeline import load_project_context
from app.loop.bullets import min_bullets
from app.parsing.structure import _norm
from app.schemas.models import (
    ExportState,
    FreshScore,
    JobDescription,
    LoopQuestion,
    Problem,
    Resume,
    TrimOption,
)
from app.scoring.synonyms import count_mentions

_DIGIT = re.compile(r"\d")


class ExportError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _edir(d: Path) -> Path:
    e = d / "export"
    e.mkdir(exist_ok=True)
    return e


def load_state(d: Path) -> ExportState | None:
    p = d / "export" / "export.json"
    if not p.exists():
        return None
    st = ExportState.model_validate_json(p.read_text(encoding="utf-8"))
    from app.export.edit import load_chat
    st.chat = load_chat(d)
    return st


def _save(d: Path, s: ExportState) -> None:
    (_edir(d) / "export.json").write_text(s.model_dump_json(indent=2), encoding="utf-8")


def _safe(s: str | None) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s or "").strip("_")[:40]


def _chars_per_line(look: dict) -> int:
    usable_pt = (21.0 - 2 * look["margin_cm"]) / 2.54 * 72 - 18  # minus bullet indent
    return max(int(usable_pt / (look["body_pt"] * 0.5)), 40)       # ~0.5em average char width


def trim_options(resume: Resume, jd: JobDescription, look: dict, overflow_lines: int) -> list[TrimOption]:
    cpl = _chars_per_line(look)
    opts: list[TrimOption] = []
    for j, job in enumerate(resume.experience):
        keep_min = min_bullets()
        if len(job.bullets) <= keep_min:
            continue
        for b, text in enumerate(job.bullets):
            must = [s for s in jd.must_have if count_mentions(s, text)]
            nice = [s for s in jd.nice_to_have if count_mentions(s, text)]
            value = 3 * len(must) + 1.5 * len(nice) + (1 if _DIGIT.search(text) else 0) + max(2 - j, 0)
            why = []
            if must or nice:
                why.append("shows " + ", ".join(must + nice))
            else:
                why.append("mentions no JD skill")
            why.append("has a number" if _DIGIT.search(text) else "no measurable result")
            if j >= 2:
                why.append("older job")
            opts.append(TrimOption(
                id=f"j{j}b{b}", job=j, job_label=" - ".join(x for x in (job.title, job.company) if x),
                text=text, value=round(value, 2), est_lines=max(math.ceil(len(text) / cpl), 1),
                reason="; ".join(why)))
    opts.sort(key=lambda o: (o.value, -o.job, -o.est_lines))
    # pre-select the cheapest set that frees enough lines, respecting per-job minimums
    need, freed, per_job = overflow_lines + 1, 0, {}
    for o in opts:
        if freed >= need:
            break
        left = len(resume.experience[o.job].bullets) - per_job.get(o.job, 0)
        if left - 1 < min_bullets():
            continue
        o.suggested = True
        per_job[o.job] = per_job.get(o.job, 0) + 1
        freed += o.est_lines
    return opts


def unused_facts(resume: Resume) -> list[str]:
    ctx = load_project_context()
    text = _norm("\n".join(b for j in resume.experience for b in j.bullets) + "\n" + (resume.summary or ""))
    out = []
    for p in ctx.projects:
        for item in p.what_i_did + p.results:
            if _norm(item) and _norm(item) not in text:
                out.append(f"{p.name} ({p.employer or 'no employer'}): {item}")
    from app.context import notes
    for kind, lines in notes.all_bare_lines().items():
        for line in lines:
            if _norm(line) and _norm(line) not in text:
                out.append(f"{kind}.txt: {line}")
    return out


async def build(d: Path, history: list[str] | None = None) -> ExportState:
    ls = loop_mod.load_state(d)
    if ls is None or not ls.finished or ls.final_version is None:
        raise ExportError("loop_not_finished", "Finish the improve loop first (POST .../loop/finish).")
    resume = loop_mod.load_version(d, ls.final_version)
    jd = runs.load_jd(d)
    from app.export.builder import merge_links
    resume.contact.links = merge_links(resume.contact.links, load_project_context().profile.links)
    fit = await asyncio.to_thread(fit_one_page, resume)   # rendering takes seconds; keep the server responsive
    e = _edir(d)
    base = "_".join(x for x in (_safe(resume.contact.name), _safe(jd.company)) if x) or "Resume"
    docx_name, pdf_name = f"{base}_Resume.docx", f"{base}_Resume_preview.pdf"
    (e / docx_name).write_bytes(fit.docx)
    (e / pdf_name).write_bytes(fit.pdf)

    state = ExportState(
        run_id=d.name, version=ls.final_version, status=fit.status, pages=fit.pages, fill=fit.fill,
        overflow_lines=fit.overflow_lines, renderer=fit.renderer, look=fit.look,
        history=(history or []),
        files={"docx": docx_name, "pdf": pdf_name, "report": "report.md"},
    )
    from app.export.edit import load_chat
    state.chat = load_chat(d)
    line_pt = fit.look["body_pt"] * fit.look["line_spacing"] * 1.2
    usable_pt = (29.7 - 2 * fit.look["margin_cm"]) / 2.54 * 72
    if fit.status == FITTED:
        state.message = f"One full page ({fit.fill:.0%} filled). Ready to download."
    elif fit.status == OVERFLOW:
        state.trim_options = trim_options(resume, jd, fit.look, fit.overflow_lines)
        state.message = (f"Even at the most compact look, about {fit.overflow_lines} lines spill onto "
                         "page 2. Pick bullets to cut (suggestions are pre-selected).")
    elif fit.status == UNDERFILLED:
        state.free_lines = max(int((1 - fit.fill) * usable_pt / line_pt), 1)
        state.unused_facts = unused_facts(resume)
        state.message = (f"The page is only {fit.fill:.0%} full (about {state.free_lines} free lines) "
                         "even at the roomiest look. Reopen the loop to add content"
                         + (" from your My info notes" if state.unused_facts else "")
                         + ", or keep it as it is.")
    from app.export import ats
    state.ats = ats.check(fit.docx, fit.pdf, resume, format_spec(), fit.look,
                          int(loop_mod._cfg().get("max_words_per_bullet", 32)))
    write_report(d, state)
    _save(d, state)
    return state


# ---------- see it before downloading ----------

def preview_png(d: Path) -> Path:
    """Page 1 of the exported resume as a picture (works on phones, where PDFs don't show inline)."""
    import pdfplumber
    state = load_state(d)
    if state is None or "pdf" not in state.files:
        raise ExportError("no_export", "Export first.")
    pdf = _edir(d) / state.files["pdf"]
    png = _edir(d) / f"preview_{int(pdf.stat().st_mtime_ns)}.png"
    if not png.exists():
        for old in _edir(d).glob("preview_*.png"):
            old.unlink(missing_ok=True)
        with pdfplumber.open(pdf) as doc:
            doc.pages[0].to_image(resolution=130).original.save(png, format="PNG")
    return png


# ---------- check the score again, from scratch ----------

def fresh_scores(d: Path) -> list[FreshScore]:
    return [FreshScore.model_validate_json(p.read_text(encoding="utf-8"))
            for p in sorted(_edir(d).glob("fresh_score_*.json"), key=lambda p: int(p.stem.rsplit("_", 1)[1]))]


async def fresh_score(llm: LLMClient, d: Path) -> FreshScore:
    """Score the exported file as if it were a brand-new upload: read the text out of the file
    (like an ATS), let the AI read that text into a resume, and let the blind judge score it.
    Nothing from the loop goes in: no earlier scores, versions, edits or notes. Only the scoring
    settings are the same as the rest of the run, so the numbers are comparable."""
    from datetime import datetime

    from app.parsing.extract import extract_pdf
    from app.parsing.structure import structure_resume
    from app.scoring.scorer import score_resume

    state = load_state(d)
    if state is None or "pdf" not in state.files:
        raise ExportError("no_export", "Export first.")
    data = (_edir(d) / state.files["pdf"]).read_bytes()
    ex = extract_pdf(data)
    parsed, _ = await structure_resume(llm, ex.text)
    jd = runs.load_jd(d)
    score = await score_resume(llm, parsed, jd, format_flags={c for c, _ in ex.warnings},
                               pins=runs.load_pins(d))

    exported = loop_mod.load_version(d, state.version)
    read = {"name": parsed.contact.name, "email": parsed.contact.email, "phone": parsed.contact.phone,
            "jobs": len(parsed.experience), "bullets": sum(len(j.bullets) for j in parsed.experience),
            "skills": len(parsed.skills), "summary": bool(parsed.summary)}
    diffs = []
    for k, want in (("name", exported.contact.name), ("email", exported.contact.email)):
        if want and _norm(read[k] or "") != _norm(want):
            diffs.append(f"{k}: read \"{read[k] or 'nothing'}\" instead of \"{want}\"")
    for k, want in (("jobs", len(exported.experience)),
                    ("bullets", sum(len(j.bullets) for j in exported.experience)),
                    ("skills", len(exported.skills))):
        if read[k] != want:
            diffs.append(f"{k}: read {read[k]}, the resume has {want}")
    v = next((x for x in loop_mod.load_state(d).versions if x.n == state.version), None)
    n = len(fresh_scores(d)) + 1
    out = FreshScore(n=n, at=datetime.now().isoformat(timespec="seconds"), version=state.version,
                     score=score, read_back=read, differences=diffs, loop_score=v.score if v else None)
    (_edir(d) / f"fresh_score_{n}.json").write_text(out.model_dump_json(indent=2), encoding="utf-8")
    return out


async def trim(llm: LLMClient, d: Path, option_ids: list[str]) -> ExportState:
    state = load_state(d)
    if state is None:
        raise ExportError("no_export", "Export first.")
    chosen = [o for o in state.trim_options if o.id in set(option_ids)]
    if not chosen:
        raise ExportError("nothing_chosen", "Choose at least one bullet to cut.")
    resume = loop_mod.load_version(d, state.version)
    for j, job in enumerate(resume.experience):
        cut = sum(1 for o in chosen if o.job == j)
        if cut and len(job.bullets) - cut < min_bullets():
            raise ExportError("too_few_bullets", f"{' - '.join(x for x in (job.title, job.company) if x)} would be "
                              f"left with {len(job.bullets) - cut} bullet(s); every job keeps at least {min_bullets()}.")
    v = await loop_mod.trim_version(llm, d, [(o.job, o.text) for o in chosen],
                                    f"Trimmed {len(chosen)} bullet(s) to fit one page")
    msg = f"Cut {len(chosen)} bullet(s) -> v{v.n}, blind score {v.score}."
    return await build(d, history=state.history + [msg])


def reopen_loop(d: Path):
    state = load_state(d)
    if state is None:
        raise ExportError("no_export", "Export first.")
    if state.status == UNDERFILLED:
        note = (f"The one-page resume has room for about {state.free_lines} more lines. "
                "Add the most JD-relevant facts from the notes; don't pad.")
    elif state.status == OVERFLOW:
        note = (f"The resume is about {state.overflow_lines} lines too long for one page. "
                "Prefer tightening or replacing bullets over adding.")
    else:
        note = ""   # it already fits: no extra instruction for the AI
    return loop_mod.reopen(d, note)



# ---------- the fit agent (agentic one-page rounds) ----------

def _fit_round_inputs(d: Path, state: ExportState) -> tuple[dict, list[Problem], list[LoopQuestion]]:
    from app.export.builder import Look, merge_links
    from app.export.layout import analyze

    ls = loop_mod.load_state(d)
    version = ls.final_version if ls.final_version is not None else ls.current_version
    resume = loop_mod.load_version(d, version)
    resume.contact.links = merge_links(resume.contact.links, load_project_context().profile.links)
    jd = runs.load_jd(d)
    # Overflow is judged at the most compact look; underfill at the roomiest.
    scale = 0.0 if state.status == OVERFLOW else 1.0
    lay = analyze(resume, Look.at(scale))
    problems: list[Problem] = []
    questions: list[LoopQuestion] = []

    if state.status == OVERFLOW:
        need = state.overflow_lines + 1
        goal = (f"Save at least {need} lines so everything fits on one page. Prefer trimming orphan "
                "words and tightening wording; cut bullets only if needed.")
        for b in lay.orphans():
            problems.append(Problem(
                id=f"fit:orphan:{b.id}", dimension="layout", label="One-page fit", points_lost=0,
                fix_by="ai", subject=b.id,
                cause=f"\"{b.text[:70]}...\" puts only {b.last_line_words} word(s) on its last line.",
                detail=f"Trimming about {b.chars_to_save_line} characters saves 1 line, losing no facts."))
        for o in trim_options(resume, jd, state.look, state.overflow_lines):
            if o.suggested:
                problems.append(Problem(
                    id=f"fit:cut:{o.id}", dimension="layout", label="One-page fit", points_lost=0,
                    fix_by="ai", subject=o.id, cause=f"Low value for this JD: {o.reason}.",
                    detail=f"Removing it saves about {o.est_lines} line(s)."))
        problems.append(Problem(id="fit:overflow", dimension="layout", label="One-page fit",
                                points_lost=0, fix_by="ai",
                                cause=f"About {state.overflow_lines} line(s) spill onto page 2.",
                                detail=goal))
    else:
        free = state.free_lines or 1
        goal = (f"Add about {free} lines of real, JD-relevant content so the page is full. "
                "Use template facts not on the resume yet; never pad.")
        facts = unused_facts(resume)
        for i, f in enumerate(facts[:10]):
            problems.append(Problem(id=f"fit:fact:{i}", dimension="layout", label="One-page fit",
                                    points_lost=0, fix_by="ai", cause=f"Not on the resume yet: {f}",
                                    detail="Could become a bullet under the matching job."))
        problems.append(Problem(id="fit:underfill", dimension="layout", label="One-page fit",
                                points_lost=0, fix_by="ai" if facts else "you",
                                cause=f"About {free} line(s) of the page are empty.", detail=goal))
        if len(facts) < 3:  # not enough known facts: ask
            gaps = [h for h in jd.must_have + jd.nice_to_have][:3]
            for j, job in enumerate(resume.experience[:3]):
                questions.append(LoopQuestion(
                    id=f"q:fit:more:j{j}", problem_id="fit:underfill",
                    text=(f"The page has room for about {free} more lines. Is there more work at "
                          f"{job.company or 'this job'} worth adding"
                          + (f", especially with {', '.join(gaps)}" if gaps else "")
                          + "? What did you do and what came of it?"),
                    why="Real content fills the page better than bigger fonts.",
                    points_at_stake=0))
            questions.append(LoopQuestion(
                id="q:fit:achievements", problem_id="fit:underfill",
                text=("Any achievements to add? For example awards, hackathons, LeetCode or contest "
                      "ratings, certifications, open-source work."),
                why="The Achievements section can use the free space.", points_at_stake=0))
            questions = questions[:5]

    layout = {
        "status": state.status, "scale": scale, "goal": goal,
        "lines_over": state.overflow_lines, "lines_free": state.free_lines,
        "bullets": [{"id": b.id, "job": b.job, "bullet": b.bullet, "lines": b.lines,
                     "last_line_words": b.last_line_words, "chars_to_save_line": b.chars_to_save_line}
                    for b in lay.bullets],
    }
    return layout, problems, questions


def fit_start(d: Path):
    """Open an agentic fit round: the AI looks at the page, asks, and suggests edits."""
    state = load_state(d)
    if state is None:
        raise ExportError("no_export", "Export first.")
    if state.status == FITTED:
        raise ExportError("already_fits", "The resume already fills exactly one page.")
    layout, problems, questions = _fit_round_inputs(d, state)
    return loop_mod.open_fit_round(d, layout, problems, questions)


async def fit_apply(llm: LLMClient, d: Path) -> dict:
    """Apply the fit round's accepted edits -> new version (blind re-scored) -> rebuild + measure."""
    ls = loop_mod.load_state(d)
    if ls is None or not ls.rounds or ls.rounds[-1].purpose != "fit":
        raise ExportError("no_fit_round", "Start a fit round first (POST .../export/fit/start).")
    old = load_state(d)
    ls = await loop_mod.apply(llm, d)
    r = ls.rounds[-1]
    loop_mod.finish(d, ls.current_version)
    msg = (f"Fit round {r.n}: {sum(c.status == 'accepted' for c in r.changes)} edit(s), "
           f"score {r.score_before} -> {r.score_after}")
    state = await build(d, history=(old.history if old else []) + [msg])
    if state.status != FITTED:
        state.message += " Start another fit round to keep going."
        _save(d, state)
    return {"loop": loop_mod.load_state(d), "export": state}
