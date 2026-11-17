"""Step 4: the interactive improve loop (text only - no documents until phase 5).

One round:
  1. diagnose  - problems ranked by points lost (ai / you / none)
  2. ask       - up to 5 questions; answers become template proposals you accept
  3. suggest   - the AI proposes sourced edits; code + AI truth checks run on each
  4. review    - accept / reject / edit / "revise with feedback" each edit
  5. apply     - accepted edits make a new version; the BLIND scorer re-scores it
  6. you pick  - next round, roll back, or finish (stop flags are advice, you decide)

The improver may see the problems list; the scorer never sees any of this.
State: runs/<id>/loop.json ; versions in runs/<id>/versions/.
"""
from __future__ import annotations

import json
from pathlib import Path

from app import runs
from app.config import weights_config
from app.context import updater
from app.loop import bullets as bullets_mod
from app.loop import guard
from app.loop import split as split_mod
from app.gate import gate as gate_mod
from app.llm.base import LLMClient
from app.loop.diagnose import diagnose, make_questions, template_text
from app.loop.truth import check_ai, check_code
from app.parsing.pipeline import load_project_context
from app.parsing.structure import _norm
from app.schemas.models import (
    Change,
    ChangeSource,
    ContextProposal,
    JobDescription,
    LoopState,
    Problem,
    ProjectContext,
    Resume,
    Round,
    ScoreResult,
    Version,
)
from app.scoring.render import render_resume
from app.scoring.scorer import score_resume

SKILL = "matcher-improve"
FIT_SKILL = "matcher-fit"
KINDS = {"replace_bullet", "add_bullet", "remove_bullet", "merge_bullets", "set_summary", "add_skill",
         "add_achievement", "remove_achievement"}


class LoopError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _cfg() -> dict:
    c = weights_config()
    return {"target": float(c.get("thresholds", {}).get("target", 85)), **c.get("loop", {})}


# ---------- storage ----------

def load_state(d: Path) -> LoopState | None:
    p = d / "loop.json"
    return LoopState.model_validate_json(p.read_text(encoding="utf-8")) if p.exists() else None


def save_state(d: Path, s: LoopState) -> None:
    (d / "loop.json").write_text(s.model_dump_json(indent=2), encoding="utf-8")


def _vdir(d: Path) -> Path:
    v = d / "versions"
    v.mkdir(exist_ok=True)
    return v


def load_version(d: Path, n: int) -> Resume:
    return Resume.model_validate_json((_vdir(d) / f"resume_v{n}.json").read_text(encoding="utf-8"))


def _load_score(d: Path, state: LoopState, n: int) -> ScoreResult:
    v = next(v for v in state.versions if v.n == n)
    return ScoreResult.model_validate_json((d / v.score_file).read_text(encoding="utf-8"))


async def _save_version(llm: LLMClient, d: Path, state: LoopState, resume: Resume, jd: JobDescription,
                        note: str, score: ScoreResult | None = None) -> Version:
    n = max((v.n for v in state.versions), default=-1) + 1
    vd = _vdir(d)
    (vd / f"resume_v{n}.json").write_text(resume.model_dump_json(indent=2), encoding="utf-8")
    (vd / f"resume_v{n}.txt").write_text(render_resume(resume), encoding="utf-8")
    # The blind judge: only this resume, the JD and the run's pins. Text only, no layout flags.
    if score is None:
        score = await score_resume(llm, resume, jd, format_flags=set(), pins=runs.load_pins(d))
    (vd / f"score_v{n}.json").write_text(score.model_dump_json(indent=2), encoding="utf-8")
    v = Version(n=n, file=f"versions/resume_v{n}.json", score=score.total,
                score_file=f"versions/score_v{n}.json", note=note)
    state.versions.append(v)
    return v


def _require(d: Path) -> LoopState:
    s = load_state(d)
    if s is None:
        raise LoopError("no_loop", "Start the loop first (POST /api/runs/{run_id}/loop/start).")
    return s


def _done(r: Round) -> bool:
    """A round is over when applied, or closed because you finished without applying."""
    return r.stage in ("applied", "closed")


def _round(s: LoopState) -> Round:
    if not s.rounds:
        raise LoopError("no_round", "No round in progress.")
    return s.rounds[-1]


def bullet_problems(rep: "bullets_mod.BulletReport") -> list[Problem]:
    out = []
    rewordable = [b for b in rep.bullets if b.status == "rewordable"]
    richer = [b for b in rep.bullets if b.status in ("ok", "strong") and b.could_show]
    if rewordable or richer:
        parts = [f'"{b.text[:60]}" could show {", ".join(b.could_show or b.could_cover[:1])}'
                 for b in (rewordable + richer)[:5]]
        out.append(Problem(
            id="bullets:reword", dimension="experience_relevance",
            label=(f"{len(rewordable)} bullet(s) earn nothing only because of their wording"
                   if rewordable else f"{len(richer)} bullet(s) could show more JD skills"),
            points_lost=round(min(2 * len(rewordable) + len(richer), 8), 1),
            cause="Your notes about the same work mention JD skills or duties that the bullet doesn't.",
            fix_by="ai",
            detail="Reword them with the JD's terms and the facts from your notes: " + "; ".join(parts)))
    dead = [b for b in rep.bullets if b.status == "no_value"]
    if dead:
        out.append(Problem(
            id="bullets:no_value", dimension="experience_relevance",
            label=f"{len(dead)} bullet(s) earn nothing for this job",
            points_lost=round(min(1.5 * len(dead), 6), 1),
            cause="They show no skill or duty from the job description, so they use space without scoring.",
            fix_by="ai",
            detail="Replace them with relevant facts from My info, or remove them: "
                   + "; ".join(f'"{b.text[:70]}" ({b.job_label})' for b in dead[:5])))
    long_ = [b for b in rep.bullets if b.too_long]
    if long_:
        out.append(Problem(
            id="bullets:too_long", dimension="ats_format",
            label=f"{len(long_)} bullet(s) longer than {rep.word_cap} words",
            points_lost=0, cause="Long bullets take space a stronger fact could use.", fix_by="ai",
            detail="Tighten them, keeping the JD skills and the result: "
                   + "; ".join(f'"{b.text[:60]}…" ({b.words} words)' for b in long_[:4])))
    for j in rep.jobs:
        if j.count > j.cap:
            out.append(Problem(
                id=f"bullets:over_cap:{j.job}", dimension="ats_format",
                label=f"Too many bullets at {j.job_label} ({j.count} of {j.cap})",
                points_lost=0, cause="The resume must fit one page.", fix_by="ai",
                detail=f"Remove or merge the weakest {j.count - j.cap} bullet(s) there."))
    return out


CAP_HINT = "Replace a weak bullet instead, or reject another new bullet first."
LEN_HINT = "Edit or revise it to shorten."
BULLET_KINDS = ("replace_bullet", "add_bullet", "merge_bullets")


def _length_check(changes: list[Change], cfg: dict) -> None:
    """Bullets longer than the word cap are blocked (your own edits only get a warning)."""
    cap = int(cfg.get("max_words_per_bullet", 32))
    for c in changes:
        c.truth_issues = [i for i in c.truth_issues if LEN_HINT not in i]
        if c.kind not in BULLET_KINDS or not c.new:
            continue
        n = sum(1 for w in c.new.split() if any(ch.isalnum() for ch in w))
        if n > cap:
            c.truth_issues.append(f"Too long: {n} words (max {cap}). {LEN_HINT}")
            if c.status == "pending" and not c.edited_by_you:
                c.status, c.truth_ok = "blocked", False
        elif c.status == "blocked" and not c.truth_issues and c.truth_ok is False:
            c.status, c.truth_ok = "pending", True


def _cap_check(resume: Resume, changes: list[Change], cfg: dict) -> None:
    """Block adds that would push a job past its bullet limit (in the order they'd apply).
    Re-run after every decision: rejecting one new bullet can make room for another."""
    for c in changes:  # undo earlier cap blocks; they're recomputed below
        if any(CAP_HINT in i for i in c.truth_issues):
            c.truth_issues = [i for i in c.truth_issues if CAP_HINT not in i]
            if c.status == "blocked" and not c.truth_issues:
                c.status, c.truth_ok = "pending", True
    caps = bullets_mod.caps_for(len(resume.experience), cfg)
    count = [len(j.bullets) for j in resume.experience]
    for c in changes:
        if c.job is None or c.status in ("rejected",):
            continue
        if c.kind in ("remove_bullet", "merge_bullets") and c.status in ("accepted", "pending"):
            count[c.job] -= 1
        elif c.kind == "add_bullet" and c.status != "blocked":
            if count[c.job] + 1 > caps[c.job]:
                if c.status == "pending":
                    c.status = "blocked"
                    c.truth_ok = False
                msg = (f"{c.job_label} already has {count[c.job]} of {caps[c.job]} bullets. "
                       + CAP_HINT)
                if msg not in c.truth_issues:
                    c.truth_issues.append(msg)
            else:
                count[c.job] += 1


def _found(d: Path, resume: Resume) -> list:
    """Requirements your notes show (found at the gate) that this version still doesn't."""
    from app.gate import evidence
    return evidence.still_needed(evidence.load(d), resume)


def _open_round(d: Path, s: LoopState) -> None:
    resume = load_version(d, s.current_version)
    score = _load_score(d, s, s.current_version)
    ctx = load_project_context()
    found = _found(d, resume)
    problems = diagnose(score, resume, runs.load_jd(d), ctx, found=found)
    problems += bullet_problems(bullets_mod.analyze(resume, runs.load_jd(d), score, _cfg(), found=found))
    asked = {q.id for r in s.rounds for q in r.questions if q.status in ("answered", "skipped")}
    qs = make_questions(problems, resume, runs.load_jd(d), ctx, asked,
                        int(_cfg().get("max_questions_per_round", 5)))
    s.rounds.append(Round(n=len(s.rounds) + 1, base_version=s.current_version,
                          score_before=score.total, problems=problems, questions=qs))
    s.flags, s.recommendation = [], ""


# ---------- start ----------

TARGETS = (80, 85, 90, 95)


def _check_target(target: float) -> float:
    t = float(target)
    if not 50 <= t <= 100:
        raise LoopError("bad_target", "Target must be between 50 and 100.")
    return t


def set_target(d: Path, target: float) -> LoopState:
    """Change the score you're aiming for (e.g. 80, 85, 90 or 95), any time."""
    s = _require(d)
    s.target = _check_target(target)
    r = s.rounds[-1] if s.rounds else None
    if r is not None and r.score_after is not None:
        _flags(s, _cfg())
    save_state(d, s)
    return s


async def start(llm: LLMClient, d: Path, target: float | None = None) -> LoopState:
    existing = load_state(d)
    if existing:
        return set_target(d, target) if target is not None else existing
    g = gate_mod.load_state(d)
    if g is None or g.decision != "proceed":
        raise LoopError("gate_not_passed", "The apply gate must end with 'proceed' before improving.")
    s = LoopState(run_id=d.name, target=_check_target(target) if target is not None else _cfg()["target"])
    # v0 is your resume exactly as it was scored on the run page: same score, no second opinion
    await _save_version(llm, d, s, runs.load_resume(d), runs.load_jd(d), "Your resume as uploaded",
                        score=runs.latest_score(d))
    s.current_version = 0
    _open_round(d, s)
    save_state(d, s)
    return s


# ---------- questions & your context ----------

def _question(r: Round, qid: str):
    q = next((x for x in r.questions if x.id == qid), None)
    if q is None:
        raise LoopError("no_question", f"No question '{qid}' in this round.")
    return q


async def answer(llm: LLMClient, d: Path, qid: str, text: str) -> LoopState:
    s = _require(d)
    r = _round(s)
    q = _question(r, qid)
    q.answer, q.status = text, "answered"
    prop = await updater.propose(llm, text, load_version(d, s.current_version),
                                 question=q.text, source=f"loop:{qid}")
    if prop.action != "none":
        q.proposal_id = prop.id
        s.proposals.append(prop)
        r.proposal_ids.append(prop.id)
    save_state(d, s)
    return s


def skip(d: Path, qid: str) -> LoopState:
    s = _require(d)
    _question(_round(s), qid).status = "skipped"
    save_state(d, s)
    return s


async def add_context(llm: LLMClient, d: Path, text: str) -> LoopState:
    s = _require(d)
    prop = await updater.propose(llm, text, load_version(d, s.current_version), source="loop:free_text")
    if prop.action == "none":
        raise LoopError("nothing_to_add", prop.summary or "I couldn't find a fact to add in that.")
    s.proposals.append(prop)
    _round(s).proposal_ids.append(prop.id)
    save_state(d, s)
    return s


def resolve_proposal(d: Path, pid: str, accept: bool, edited: ContextProposal | None = None) -> LoopState:
    s = _require(d)
    prop = next((p for p in s.proposals if p.id == pid), None)
    if prop is None:
        raise LoopError("no_proposal", f"No proposal '{pid}'.")
    if prop.status != "pending":
        raise LoopError("not_pending", f"Proposal is already {prop.status}.")
    if accept:
        if edited is not None:
            prop.project, prop.profile = edited.project, edited.profile
        ctx = updater.apply(prop)
        (d / "projects.json").write_text(ctx.model_dump_json(indent=2), encoding="utf-8")
        prop.status = "accepted"
    else:
        prop.status = "rejected"
    save_state(d, s)
    return s


def add_note(d: Path, text: str) -> LoopState:
    """Style input for the rewrite (tone, length, emphasis). Not used as facts."""
    s = _require(d)
    _round(s).notes.append(text.strip())
    save_state(d, s)
    return s


# ---------- suggestions ----------

def _resume_view(r: Resume) -> dict:
    return {
        "summary": r.summary,
        "experience": [{"job": i, "title": j.title, "company": j.company, "start": j.start, "end": j.end,
                        "bullets": [{"bullet": k, "text": b} for k, b in enumerate(j.bullets)]}
                       for i, j in enumerate(r.experience)],
        "skills": r.skills,
        "achievements": [{"index": i, "text": a} for i, a in enumerate(r.achievements)],
    }


def _template_view(ctx: ProjectContext) -> dict:
    from app.context import notes
    return {"projects": [p.model_dump() for p in ctx.projects], "profile": ctx.profile.model_dump(),
            "notes": notes.all_facts()}


def _build_input(jd: JobDescription, resume: Resume, ctx: ProjectContext, r: Round, cfg: dict,
                 feedback: str | None = None, change: Change | None = None, found: list | None = None) -> str:
    problems = [p.model_dump(include={"id", "label", "points_lost", "cause", "fix_by", "detail"})
                for p in r.problems if p.fix_by != "none"]
    rep = bullets_mod.analyze(resume, jd, None, cfg, found=found)
    parts = [
        "JD:\n" + json.dumps(jd.model_dump(), ensure_ascii=False, indent=1),
        "RESUME:\n" + json.dumps(_resume_view(resume), ensure_ascii=False, indent=1),
        "TEMPLATE:\n" + json.dumps(_template_view(ctx), ensure_ascii=False, indent=1),
        "PROBLEMS:\n" + json.dumps(problems, ensure_ascii=False, indent=1),
        "USER NOTES:\n" + ("\n".join(f"- {n}" for n in r.notes) or "(none)"),
        "BULLET LIMITS (job index -> max bullets):\n" + json.dumps(
            {i: c for i, c in enumerate(bullets_mod.caps_for(len(resume.experience), cfg))}),
        f"MAX WORDS PER BULLET: {int(cfg.get('max_words_per_bullet', 32))}",
        "BULLET VALUES (what each current bullet earns for this JD; no_value = shows no JD skill "
        "or duty; rewordable = earns nothing as worded, but related_notes about the same work "
        "show could_show/could_cover):\n" + json.dumps(
            [b.model_dump(include={"job", "bullet", "value", "status", "shows", "words",
                                   "could_show", "could_cover", "related_notes"})
             for b in rep.bullets], ensure_ascii=False),
        "CANDIDATE FACTS (lines from the user's experience notes for each job, not on the resume "
        "yet, most valuable for this JD first):\n" + json.dumps(
            [c.model_dump(include={"job", "text", "value", "shows"}) for c in rep.candidates],
            ensure_ascii=False),
        f"MAX CHANGES: {1 if change else int(cfg.get('max_changes_per_round', 10))}",
    ]
    if r.purpose == "fit":
        parts.append("LAYOUT:\n" + json.dumps(r.layout, ensure_ascii=False, indent=1))
        parts.append("GOAL: " + r.layout.get("goal", ""))
    if change is not None:
        parts.append("EDIT TO REDO:\n" + change.model_dump_json(
            include={"kind", "job", "bullet", "old", "new", "fixes", "sources"}, indent=1))
        parts.append(f"FEEDBACK: {feedback}")
    return "\n\n".join(parts)


def _to_change(raw: dict, cid: str, resume: Resume) -> Change | None:
    kind = raw.get("kind")
    if kind not in KINDS:
        return None
    try:
        sources = [ChangeSource(type=str(x.get("type")), project_id=x.get("project_id"),
                                quote=str(x.get("quote") or "")) for x in raw.get("sources") or []]
    except AttributeError:
        sources = []
    c = Change(id=cid, kind=kind, job=raw.get("job"), bullet=raw.get("bullet"),
               old=raw.get("old"), new=raw.get("new"), fixes=[str(x) for x in raw.get("fixes") or []],
               why=str(raw.get("why") or ""), sources=sources)
    # structural checks against the current version
    if kind == "add_achievement":
        c.job = c.bullet = c.old = None
        c.job_label = "Achievements"
        return c if c.new else None
    if kind == "remove_achievement":
        match = next((a for a in resume.achievements if _norm(a) == _norm(c.old or "")), None)
        if match is None:
            return None
        c.old, c.new, c.job, c.bullet, c.job_label = match, None, None, None, "Achievements"
        return c
    if kind == "set_summary":
        c.old, c.job, c.bullet = resume.summary, None, None
        return c if c.new else None
    if kind == "add_skill":
        c.old, c.job, c.bullet, c.job_label = None, None, None, "Skills"
        return c if c.new and len(c.new) <= 40 else None
    if not isinstance(c.job, int) or not (0 <= c.job < len(resume.experience)):
        return None
    job = resume.experience[c.job]
    c.job_label = " - ".join(x for x in (job.title, job.company) if x)
    if kind == "add_bullet":
        c.old, c.bullet = None, None
        return c if c.new else None
    if not isinstance(c.bullet, int) or not (0 <= c.bullet < len(job.bullets)):
        return None
    c.old = job.bullets[c.bullet]  # always the real current text
    if kind == "merge_bullets":
        b2 = raw.get("bullet2")
        if not isinstance(b2, int) or not (0 <= b2 < len(job.bullets)) or b2 == c.bullet:
            return None
        c.bullet2, c.old2 = b2, job.bullets[b2]
        return c if c.new else None
    if kind == "remove_bullet":
        c.new = None
        return c
    return c if c.new else None


def _suggest_mode(cfg: dict) -> str:
    """"split" (plan, then reword / write in small calls) or "single" (one big call).
    MATCHER_SUGGEST_MODE in the environment overrides config/weights.yaml (loop.suggest_mode)."""
    import os
    return (os.getenv("MATCHER_SUGGEST_MODE") or cfg.get("suggest_mode") or "split").lower()


def _skill_for(r: Round) -> str:
    return FIT_SKILL if r.purpose == "fit" else SKILL


def _lines_delta(r: Round, changes: list[Change]) -> None:
    """Fit rounds: how many lines each edit adds (+) or saves (-) at the round's look."""
    if r.purpose != "fit":
        return
    from app.export.builder import A4_W_CM, Look
    from app.export.layout import BULLET_INDENT_PT, wrap
    look = Look.at(float(r.layout.get("scale", 0.0)))
    full = (A4_W_CM - 2 * look.margin_cm) / 2.54 * 72
    bullet_w = full - BULLET_INDENT_PT

    def n(text: str | None, width: float) -> int:
        return len(wrap(text, width, look.body_pt)) if text else 0

    for c in changes:
        width = full if c.kind == "set_summary" else bullet_w
        c.lines_delta = n(c.new, width) - n(c.old, width)


async def _checks(llm: LLMClient, d: Path, s: LoopState, changes: list[Change], jd: JobDescription,
                  ctx: ProjectContext) -> None:
    resume_texts = [render_resume(load_version(d, 0)), render_resume(load_version(d, s.current_version))]
    tpl = template_text(ctx)
    from app.gate import evidence
    confirmed = evidence.load(d)
    for c in changes:
        check_code(c, resume_texts, tpl, jd, confirmed=confirmed)
    await check_ai(llm, changes)


async def suggest(llm: LLMClient, d: Path) -> LoopState:
    s = _require(d)
    r = _round(s)
    if _done(r):
        raise LoopError("round_done", "This round is over. Start the next round first.")
    cfg = _cfg()
    resume, jd, ctx = load_version(d, s.current_version), runs.load_jd(d), load_project_context()
    if _suggest_mode(cfg) == "split" and r.purpose != "fit":
        # plan first (keep / reword / merge / remove / add), then reword and write in small calls
        raws, r.plan = await split_mod.suggest(llm, resume, jd, _load_score(d, s, s.current_version), r, cfg,
                                               found=_found(d, resume))
        limit = len(raws)
    else:
        data = await llm.run_skill(_skill_for(r), _build_input(jd, resume, ctx, r, cfg, found=_found(d, resume)))
        raws = data.get("changes", []) if isinstance(data, dict) else []
        limit = int(cfg.get("max_changes_per_round", 10))
    kept = [c for c in r.changes if c.status in ("accepted", "rejected")]
    kept_keys = {(c.kind, c.job, _norm(c.old or ""), _norm(c.new or "")) for c in kept}
    start_n = len(r.changes) + 1
    new = [c for i, raw in enumerate(raws[:limit])
           if isinstance(raw, dict) and (c := _to_change(raw, f"r{r.n}c{start_n + i}", resume))
           and (c.kind, c.job, _norm(c.old or ""), _norm(c.new or "")) not in kept_keys]
    if r.purpose != "fit":
        new += [c for c in guard.skill_edits(resume, jd, _found(d, resume), start_n + len(raws[:limit]),
                                               f"r{r.n}c", planned=new + kept)
                if (c.kind, c.job, _norm(c.old or ""), _norm(c.new or "")) not in kept_keys]
    if not new and not kept:
        raise LoopError("no_suggestions", "The AI had no safe edits to suggest. Add context or answer questions first.")
    await _checks(llm, d, s, new, jd, ctx)
    guard.review(resume, jd, new)
    guard.clashes(new)
    for c in new:
        c.from_notes = next((x for x in (bullets_mod._notes_section(src.quote) for src in c.sources
                                          if src.type in ("notes", "template") and src.quote) if x), None)
    _lines_delta(r, new)
    r.changes = kept + new
    _length_check(r.changes, cfg)
    if r.purpose != "fit":
        _cap_check(resume, r.changes, cfg)
    r.stage = "review"
    save_state(d, s)
    return s


def _change(r: Round, cid: str) -> Change:
    c = next((x for x in r.changes if x.id == cid), None)
    if c is None:
        raise LoopError("no_change", f"No suggestion '{cid}' in this round.")
    return c


async def change_action(llm: LLMClient, d: Path, cid: str, action: str, text: str | None = None,
                        feedback: str | None = None) -> LoopState:
    s = _require(d)
    r = _round(s)
    if r.stage != "review":
        raise LoopError("not_reviewing", "There are no suggestions to review right now.")
    c = _change(r, cid)
    if action == "accept":
        if c.status == "blocked":
            raise LoopError("blocked", "This edit failed the truth check. Edit it yourself or ask for a revision.")
        c.status = "accepted"
    elif action == "reject":
        c.status = "rejected"
    elif action == "edit":
        if not text or not text.strip():
            raise LoopError("empty_edit", "Send the text you want.")
        c.new, c.edited_by_you = text.strip(), True
        probe = c.model_copy(deep=True)
        probe.status = "pending"
        check_code(probe, [render_resume(load_version(d, 0)), render_resume(load_version(d, s.current_version))],
                   template_text(load_project_context()), runs.load_jd(d),
                   confirmed=__import__("app.gate.evidence", fromlist=["load"]).load(d))
        c.sources.append(ChangeSource(type="you", quote=c.new, verified=True))
        c.truth_ok = True
        c.truth_issues = ([f"Your own wording. Note: {i}" for i in probe.truth_issues]
                          if probe.truth_issues else [])
        guard.review(load_version(d, s.current_version), runs.load_jd(d), [c], by_you=True)
        c.status = "accepted"
    elif action == "revise":
        if not feedback or not feedback.strip():
            raise LoopError("empty_feedback", "Say what to change about this suggestion.")
        resume, jd, ctx = load_version(d, s.current_version), runs.load_jd(d), load_project_context()
        data = await llm.run_skill(_skill_for(r), _build_input(jd, resume, ctx, r, _cfg(), feedback, c))
        raw = (data.get("changes") or [None])[0] if isinstance(data, dict) else None
        redo = _to_change(raw, c.id, resume) if isinstance(raw, dict) else None
        if redo is None:
            raise LoopError("revise_failed", "The AI couldn't produce a valid revision. Try different feedback.")
        redo.feedback_history = c.feedback_history + [feedback.strip()]
        await _checks(llm, d, s, [redo], jd, ctx)
        guard.review(resume, jd, [redo])
        redo.from_notes = next((x for x in (bullets_mod._notes_section(src.quote) for src in redo.sources
                                             if src.type in ("notes", "template") and src.quote) if x), None)
        _lines_delta(r, [redo])
        r.changes[r.changes.index(c)] = redo
    else:
        raise LoopError("bad_action", "action must be accept, reject, edit or revise.")
    _length_check(r.changes, _cfg())
    if r.purpose != "fit":
        _cap_check(load_version(d, s.current_version), r.changes, _cfg())
    save_state(d, s)
    return s


# ---------- apply + blind re-score ----------

def apply_changes(resume: Resume, changes: list[Change]) -> Resume:
    out = resume.model_copy(deep=True)
    for c in changes:
        if c.kind == "set_summary":
            out.summary = c.new
            continue
        if c.kind == "add_skill":
            if c.new and _norm(c.new) not in {_norm(x) for x in out.skills}:
                out.skills.append(c.new)
            continue
        if c.kind == "add_achievement":
            out.achievements.append(c.new)
            continue
        if c.kind == "remove_achievement":
            out.achievements = [a for a in out.achievements if _norm(a) != _norm(c.old or "")]
            continue
        job = out.experience[c.job]
        if c.kind == "add_bullet":
            job.bullets.append(c.new)
            continue
        idx = next((i for i, b in enumerate(job.bullets) if _norm(b) == _norm(c.old or "")), None)
        if idx is None:
            continue  # bullet already changed by an earlier edit in this batch
        if c.kind == "replace_bullet":
            job.bullets[idx] = c.new
        elif c.kind == "remove_bullet":
            job.bullets.pop(idx)
        elif c.kind == "merge_bullets":
            job.bullets[idx] = c.new
            idx2 = next((i for i, b in enumerate(job.bullets) if _norm(b) == _norm(c.old2 or "")), None)
            if idx2 is not None and idx2 != idx:
                job.bullets.pop(idx2)
    return out


def _flags(s: LoopState, cfg: dict) -> None:
    r = s.rounds[-1]
    flags, rec = [], ""
    noise = float(cfg.get("score_noise", 1.0))   # the AI judge's grade varies a little between reads
    if r.score_after is not None and r.score_after < r.score_before - noise:
        flags.append("score_dropped")
        rec = f"Score went down ({r.score_before} -> {r.score_after}). Rolling back to v{r.base_version} is recommended."
    elif r.score_after is not None and abs(r.score_after - r.score_before) <= noise and r.result_version is not None:
        flags.append("no_real_change")
        rec = (f"{r.score_before} -> {r.score_after} is within the AI judge's normal variation (±{noise:g}), "
               "so this round didn't really change the score. Use \"Score again\" (3 reads) to be sure, "
               "or try different edits.")
    if r.score_after is not None and r.score_after >= s.target:
        flags.append("target_reached")
        rec = rec or f"Target reached ({r.score_after}). You can finish, or keep polishing."
    if len(s.rounds) >= int(cfg.get("max_iterations", 5)):
        flags.append("max_rounds")
        rec = rec or "That's the planned number of rounds. Finishing with the best version is recommended."
    k = int(cfg.get("plateau_rounds", 2))
    gains = [(x.score_after or x.score_before) - x.score_before for x in s.rounds[-k:]]
    if len(gains) == k and all(g < float(cfg.get("plateau_min_gain", 2)) for g in gains):
        flags.append("plateau")
        rec = rec or "Progress has stalled. Answering open questions or adding context may help; or finish."
    s.flags, s.recommendation = flags, rec or "Start the next round when you're ready."


async def apply(llm: LLMClient, d: Path) -> LoopState:
    s = _require(d)
    r = _round(s)
    if r.stage != "review":
        raise LoopError("not_reviewing", "Get suggestions first.")
    pending = [c.id for c in r.changes if c.status == "pending"]
    if pending:
        raise LoopError("undecided", f"Decide on these suggestions first: {', '.join(pending)}.")
    accepted = [c for c in r.changes if c.status == "accepted"]
    if accepted:
        new_resume = apply_changes(load_version(d, s.current_version), accepted)
        if r.purpose != "fit":
            caps = bullets_mod.caps_for(len(new_resume.experience), _cfg())
            before = load_version(d, s.current_version)
            over = [f"{' - '.join(x for x in (j.title, j.company) if x)} ({len(j.bullets)} of {caps[i]})"
                    for i, j in enumerate(new_resume.experience)
                    if len(j.bullets) > caps[i] and len(j.bullets) > len(before.experience[i].bullets)]
            if over:
                raise LoopError("over_cap", "Too many bullets for one page at: " + ", ".join(over)
                                + ". Reject a new bullet or accept a removal there first.")
        v = await _save_version(llm, d, s, new_resume, runs.load_jd(d),
                                f"Round {r.n}: {len(accepted)} edit(s)")
        r.result_version, r.score_after = v.n, v.score
        s.current_version = v.n
    else:
        r.score_after = r.score_before
    r.stage = "applied"
    _flags(s, _cfg())
    save_state(d, s)
    return s


# ---------- your decisions ----------

def next_round(d: Path) -> LoopState:
    s = _require(d)
    if s.finished:
        raise LoopError("finished", "The loop is finished.")
    if not _done(_round(s)):
        raise LoopError("round_open", "Finish (apply) the current round first.")
    _open_round(d, s)
    save_state(d, s)
    return s


def rollback(d: Path, version: int) -> LoopState:
    s = _require(d)
    if not any(v.n == version for v in s.versions):
        raise LoopError("no_version", f"No version v{version}.")
    if s.rounds and not _done(_round(s)):
        raise LoopError("round_open", "Apply or finish reviewing the current round first.")
    s.current_version = version
    s.flags, s.recommendation = [], f"Rolled back to v{version}. Start the next round when ready."
    save_state(d, s)
    return s


def finish(d: Path, version: int | None = None) -> LoopState:
    s = _require(d)
    v = s.current_version if version is None else version
    if not any(x.n == v for x in s.versions):
        raise LoopError("no_version", f"No version v{v}.")
    if s.rounds and not _done(_round(s)):
        r = _round(s)
        r.closed_from, r.stage = r.stage, "closed"  # paused; "Keep improving" brings it back
    s.finished, s.final_version = True, v
    save_state(d, s)
    return s


def reopen(d: Path, note: str) -> LoopState:
    """Go back to improving (e.g. the page has free space). Adds your note to the round."""
    s = _require(d)
    s.finished, s.final_version = False, None
    last = s.rounds[-1] if s.rounds else None
    if (last is not None and last.stage == "closed" and last.closed_from in ("questions", "review")
            and last.purpose == "improve" and last.base_version == s.current_version):
        last.stage, last.closed_from = last.closed_from, None   # pick up where you left off
    elif not s.rounds or _done(_round(s)):
        _open_round(d, s)
    if note:
        _round(s).notes.append(note)
    save_state(d, s)
    return s


async def trim_version(llm: LLMClient, d: Path, removals: list[tuple[int, str]], note: str) -> Version:
    """Remove bullets (job index, exact text) from the final version -> new, blind-scored version."""
    s = _require(d)
    base = s.final_version if s.final_version is not None else s.current_version
    changes = [Change(id=f"trim{i}", kind="remove_bullet", job=j, old=t, status="accepted")
               for i, (j, t) in enumerate(removals)]
    new_resume = apply_changes(load_version(d, base), changes)
    v = await _save_version(llm, d, s, new_resume, runs.load_jd(d), note)
    s.current_version = s.final_version = v.n
    save_state(d, s)
    return v


def open_fit_round(d: Path, layout: dict, problems: list, questions: list) -> LoopState:
    """Start a one-page fit round (called by the export step)."""
    s = _require(d)
    if s.rounds and not _done(_round(s)):
        raise LoopError("round_open", "Finish (apply) the current round first.")
    if s.final_version is not None:
        s.current_version = s.final_version
    s.finished, s.final_version = False, None
    score = _load_score(d, s, s.current_version)
    s.rounds.append(Round(n=len(s.rounds) + 1, purpose="fit", layout=layout,
                          base_version=s.current_version, score_before=score.total,
                          problems=problems, questions=questions))
    s.flags, s.recommendation = [], ""
    save_state(d, s)
    return s
