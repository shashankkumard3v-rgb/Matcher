"""Step 3: the interactive apply gate.

Flow (all state lives in runs/<run_id>/gate.json):
  compute  -> verdict, knockouts, questions, explanation
  answer   -> your answer becomes a proposed template change (nothing saved yet)
  accept   -> the change is written to your template; gate needs a recompute
  context  -> tell it anything else about your work, same as answering
  override -> switch off a knockout, or the whole verdict, with an optional reason
  decide   -> proceed to improving, or stop

Every verdict can be overridden. Everything you answer, accept or override is logged.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app import runs
from app.config import weights_config
from app.context import updater
from app.gate.ceiling import build_ceiling_resume
from app.llm.base import LLMClient
from app.parsing.pipeline import load_project_context
from app.parsing.structure import _norm
from app.schemas.models import (
    ContextProposal,
    GateExplanation,
    GateQuestion,
    GateState,
    JobDescription,
    Knockout,
    LossLine,
    Override,
    ProjectContext,
    Resume,
    ScoreResult,
)
from app.scoring.scorer import score_resume
from app.scoring.synonyms import alternatives, needed_count

APPLY, RISK, DONT, OVERRIDDEN = "APPLY", "APPLY_WITH_RISK", "DONT_APPLY", "OVERRIDDEN"
NEXT_LEVEL = {0: 25, 25: 50, 50: 75, 75: 100}


class GateError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _gate_cfg() -> dict:
    cfg = weights_config()
    return {"thresholds": cfg.get("thresholds", {"floor": 65, "target": 85}), **cfg.get("gate", {})}


def load_state(d: Path) -> GateState | None:
    p = d / "gate.json"
    return GateState.model_validate_json(p.read_text(encoding="utf-8")) if p.exists() else None


def save_state(d: Path, state: GateState) -> None:
    (d / "gate.json").write_text(state.model_dump_json(indent=2), encoding="utf-8")


# ---------- pure parts (no AI) ----------

def find_knockouts(ceiling: ScoreResult, resume: Resume, jd: JobDescription,
                   ctx: ProjectContext, cfg: dict) -> list[Knockout]:
    out: list[Knockout] = []
    missing = [h.skill for h in ceiling.must_have if h.status == "missing"]
    if len(missing) > int(cfg.get("max_missing_must_haves", 1)):
        out.append(Knockout(id="missing_must_haves", message=(
            f"{len(missing)} required skills appear nowhere in your resume or your My info notes: "
            + ", ".join(missing) + ".")))
    if jd.years_required and ceiling.years_found is not None:
        ratio = ceiling.years_found / jd.years_required
        if ratio < float(cfg.get("min_years_ratio", 0.7)):
            out.append(Knockout(id="years_short", message=(
                f"The JD asks for {jd.years_required:g}+ years; your resume dates add up to "
                f"{ceiling.years_found} ({ratio:.0%}).")))
    have = [_norm(x) for x in resume.languages + ctx.profile.languages]
    for lang in jd.languages:
        if not any(_norm(lang) in h for h in have):
            out.append(Knockout(id=f"language:{_norm(lang)}", message=(
                f"The JD requires {lang}, and your resume doesn't mention it.")))
    return out


def make_questions(current: ScoreResult, ceiling: ScoreResult, jd: JobDescription,
                   knockouts: list[Knockout], weights: dict) -> list[GateQuestion]:
    qs: list[GateQuestion] = []
    n_must = max(len(ceiling.must_have), 1)
    per_skill = float(weights["deterministic"]["must_have_coverage"]) / n_must
    ko_ids = {k.id for k in knockouts}
    for h in ceiling.must_have:
        if h.status == "missing":
            opts = alternatives(h.skill)
            text = (f"The JD requires {h.skill}, and neither your resume nor your My info notes "
                    f"mentions it. Have you used it? If yes, where and what did you do?")
            if len(opts) > 1:      # "Java or Go or C++": one is enough, never ask for all of them
                text = (f"The JD needs ONE of these: {', '.join(opts[:-1])} or {opts[-1]} (any one is "
                        "enough, you don't need all). Neither your resume nor your My info notes shows "
                        "any of them. Have you used one? If yes, which one, where and what did you do?")
            qs.append(GateQuestion(
                id=f"skill:{_norm(h.skill)}", kind="missing_skill",
                text=text,
                why=f"Worth up to {per_skill:.1f} points"
                    + (" and it's part of a knockout." if "missing_must_haves" in ko_ids else "."),
                points_at_stake=round(per_skill + (100 if "missing_must_haves" in ko_ids else 0), 2)))
        elif h.status == "close":
            gain = per_skill * (1 - h.credit)
            asked = alternatives(h.skill)
            want = asked[0] if len(asked) == 1 else " or ".join(asked)
            qs.append(GateQuestion(
                id=f"close:{_norm(h.skill)}", kind="missing_skill",
                text=(f"The JD asks for {want}; your resume shows {h.matched or 'something close'}, which is "
                      f"related but not the same. Have you used {want} itself? If yes, where and what did you do?"),
                why=f"Showing {want} itself is worth about {gain:.1f} more points.",
                points_at_stake=round(gain, 2)))
        elif h.status == "partial":
            gain = per_skill * (1 - h.credit)
            opts = alternatives(h.skill)
            qs.append(GateQuestion(
                id=f"partial:{_norm(h.skill)}", kind="missing_skill",
                text=(f"The JD wants {needed_count(h.skill)} of these: {', '.join(opts)}. Your resume shows "
                      f"{h.matched or 'fewer'}. Have you used another one of them? If yes, which, where and what did you do?"),
                why=f"Worth about {gain:.1f} more points.",
                points_at_stake=round(gain, 2)))
        elif h.status == "listed_only":
            gain = per_skill * (1 - h.credit)
            shown = h.matched or h.skill   # "Java or Go or C++": name the one you have, not the list
            qs.append(GateQuestion(
                id=f"listed:{_norm(h.skill)}", kind="listed_only",
                text=(f"{shown} is only listed, and no bullet shows you using it. What did you "
                      f"build or do with {shown}, and at which job?"),
                why=f"Showing it in a bullet is worth about {gain:.1f} more points.",
                points_at_stake=round(gain, 2)))
    if "years_short" in ko_ids or (jd.years_required and (ceiling.years_found or 0) < jd.years_required):
        years_dim = next(d for d in ceiling.dimensions if d.key == "years_fit")
        qs.append(GateQuestion(
            id="years", kind="years_short",
            text=(f"The JD asks for {jd.years_required:g} years; I count {ceiling.years_found} from "
                  "your resume dates. Do you have other experience that isn't on the resume "
                  "(freelance, internships, part-time, other jobs)?"),
            why=(f"{years_dim.lost:.1f} points lost"
                 + (" and it's a knockout." if "years_short" in ko_ids else ".")),
            points_at_stake=round(years_dim.lost + (100 if "years_short" in ko_ids else 0), 2)))
    for k in knockouts:
        if k.id.startswith("language:"):
            lang = next((x for x in jd.languages if _norm(x) == k.id.split(":", 1)[1]), k.id[9:])
            qs.append(GateQuestion(
                id=k.id, kind="missing_language",
                text=f"This is required and your resume doesn't mention it. Do you want to add {lang}? "
                     "If yes, at what level (native, fluent, professional, basic)?",
                why="Required by the JD; it's a knockout until answered or overridden.",
                points_at_stake=100))
    return sorted(qs, key=lambda q: (-q.points_at_stake, q.id))


def _to_gain(d, ceiling: ScoreResult, jd: JobDescription) -> str:
    if d.key in ("must_have_coverage", "nice_to_have_coverage"):
        hits = ceiling.must_have if d.key == "must_have_coverage" else ceiling.nice_to_have
        missing = [h.skill for h in hits if h.status == "missing"]
        listed = [h.skill for h in hits if h.status == "listed_only"]
        missing += [h.skill for h in hits if h.status in ("close", "partial")]
        parts = []
        if missing:
            parts.append("real experience with " + ", ".join(missing))
        if listed:
            parts.append("a bullet showing you used " + ", ".join(listed))
        return "Needs " + " and ".join(parts) + "." if parts else ""
    if d.key == "years_fit" and jd.years_required:
        return f"Needs {max(jd.years_required - (ceiling.years_found or 0), 0):.1f} more years of dated experience."
    if d.key == "title_alignment":
        return f"Needs a recent job title closer to '{jd.title}'."
    if d.key == "ats_format":
        return "Fixed automatically when the final Word file is built."
    if d.level is not None and d.level in NEXT_LEVEL:
        nxt = NEXT_LEVEL[d.level]
        return f"Reaching level {nxt} would add {d.weight * (nxt - d.level) / 100:.1f} points."
    return ""


def best_of(current: ScoreResult, ceiling: ScoreResult) -> ScoreResult:
    """The best version keeps the better result of each part: adding your notes may raise skills
    coverage while the AI judge, reading a longer page, grades one part a notch lower. The
    improve loop can add the new facts AND keep what already scored well, so each part counts
    at its better value (before, a lower total threw away every gain and showed now = best)."""
    cur = {x.key: x for x in current.dimensions}
    dims = [x if x.points >= cur.get(x.key, x).points else cur[x.key] for x in ceiling.dimensions]
    det = round(sum(x.points for x in dims if x.method != "llm"), 2)
    llm_pts = round(sum(x.points for x in dims if x.method == "llm"), 2)
    by_skill = {h.skill: h for h in current.must_have + current.nice_to_have}
    pick = lambda hs: [h if h.credit >= by_skill.get(h.skill, h).credit else by_skill[h.skill] for h in hs]
    top = sorted((x for x in dims if x.lost > 0), key=lambda x: (-x.lost, x.key))
    return ceiling.model_copy(update={
        "dimensions": dims, "deterministic_points": det, "llm_points": llm_pts,
        "total": max(round(det + llm_pts, 1), current.total),
        "must_have": pick(ceiling.must_have), "nice_to_have": pick(ceiling.nice_to_have),
        "top_losses": [x.key for x in top[:5]]})


def explain(current: ScoreResult, ceiling: ScoreResult, jd: JobDescription, floor: float,
            found: list | None = None) -> GateExplanation:
    lines = []
    for key in ceiling.top_losses:
        d = next(x for x in ceiling.dimensions if x.key == key)
        lines.append(LossLine(key=d.key, label=d.label, lost=d.lost, reason=d.reason,
                              evidence=d.evidence, to_gain=_to_gain(d, ceiling, jd)))
    return GateExplanation(
        how_reached=(
            f"Your resume as uploaded scores {current.total}/100 "
            f"({current.deterministic_points} from rules, {current.llm_points} from AI judgment). "
            f"Adding everything in your My info notes, the best it can reach is {ceiling.total}/100."
            + (f" Your notes already show {len(found)} requirement(s) the resume is missing: "
               + ", ".join(f.skill for f in found) + "." if found else "")),
        top_losses=lines,
        missing_must_haves=[h.skill for h in ceiling.must_have if h.status == "missing"],
        points_below_floor=round(max(floor - ceiling.total, 0), 1),
    )


def base_verdict(ceiling_total: float, knockouts: list[Knockout], thresholds: dict) -> str:
    if any(k.status == "active" for k in knockouts):
        return DONT
    if ceiling_total >= thresholds["target"]:
        return APPLY
    if ceiling_total >= thresholds["floor"]:
        return RISK
    return DONT


def effective_verdict(state: GateState) -> str:
    if any(o.target == "verdict" for o in state.overrides):
        return OVERRIDDEN
    return base_verdict(state.ceiling_score, state.knockouts, state.thresholds)


def _info_notes(jd: JobDescription) -> list[str]:
    notes = []
    if jd.location:
        notes.append(f"Location: {jd.location}. Not checked; make sure it works for you.")
    if jd.work_auth:
        notes.append(f"Work authorization: {jd.work_auth}. Not checked.")
    return notes


# ---------- steps that use the AI ----------

async def compute(llm: LLMClient, d: Path) -> GateState:
    resume, jd = runs.load_resume(d), runs.load_jd(d)
    pins = runs.load_pins(d)
    current = runs.latest_score(d)       # the score you see on the run page, not a new one
    if current is None:
        current = await score_resume(llm, resume, jd, format_flags=runs.original_format_flags(d), pins=pins)
        if pins is None:
            pins = current.pins
            runs.save_pins(d, pins)
        runs.save_score(d, current)

    # Fresh copy of your template (you may have added things since parsing)
    ctx = load_project_context()
    (d / "projects.json").write_text(ctx.model_dump_json(indent=2), encoding="utf-8")
    from app.loop.loop import _cfg as loop_cfg
    # Requirements your notes already show in other words (AI finds, code checks; cached)
    from app.gate import evidence
    found = await evidence.find(llm, d, resume, jd, current, ctx,
                                int(loop_cfg().get("max_words_per_bullet", 32)))
    ceiling_resume = build_ceiling_resume(resume, ctx, jd, loop_cfg(), found=found)
    ceiling = best_of(current, await score_resume(llm, ceiling_resume, jd, format_flags=set(), pins=pins))
    (d / "ceiling_resume.json").write_text(ceiling_resume.model_dump_json(indent=2), encoding="utf-8")
    (d / "ceiling_score.json").write_text(ceiling.model_dump_json(indent=2), encoding="utf-8")

    cfg = _gate_cfg()
    weights = weights_config()["weights"]
    old = load_state(d)
    overridden = {o.target for o in (old.overrides if old else [])}
    knockouts = find_knockouts(ceiling, resume, jd, ctx, cfg)
    for k in knockouts:
        if k.id in overridden:
            k.status = "overridden"

    fresh = make_questions(current, ceiling, jd, knockouts, weights)
    covered = {f"{kind}:{_norm(f.skill)}" for f in found for kind in ("skill", "close", "partial", "listed")}
    fresh = [q for q in fresh if q.id not in covered]   # your notes already answer these
    previous = {q.id: q for q in (old.questions if old else [])}
    questions = []
    for q in fresh:
        prev = previous.get(q.id)
        if prev and prev.status in ("answered", "skipped"):
            q.status, q.answer, q.proposal_id = prev.status, prev.answer, prev.proposal_id
        questions.append(q)
    for qid, prev in previous.items():  # gaps your answers closed
        if qid not in {q.id for q in fresh} and prev.status != "resolved":
            prev.status = "resolved"
            questions.append(prev)
    open_qs = [q for q in questions if q.status == "open"][: int(cfg.get("max_questions", 5))]
    shown = open_qs + [q for q in questions if q.status != "open"]

    state = GateState(
        run_id=d.name, current_score=current.total, ceiling_score=ceiling.total,
        thresholds=cfg["thresholds"],
        base_verdict=base_verdict(ceiling.total, knockouts, cfg["thresholds"]),
        verdict=DONT, knockouts=knockouts, questions=shown,
        proposals=old.proposals if old else [], overrides=old.overrides if old else [],
        explanation=explain(current, ceiling, jd, float(cfg["thresholds"]["floor"]), found),
        found_in_notes=found,
        notes=_info_notes(jd), needs_recompute=False,
        decision=None, decision_reason=None,
    )
    state.verdict = effective_verdict(state)
    save_state(d, state)
    return state


def _require(d: Path) -> GateState:
    state = load_state(d)
    if state is None:
        raise GateError("no_gate", "Run the gate first (POST /api/runs/{run_id}/gate).")
    return state


async def answer(llm: LLMClient, d: Path, question_id: str, text: str) -> GateState:
    state = _require(d)
    q = next((x for x in state.questions if x.id == question_id), None)
    if q is None:
        raise GateError("no_question", f"No question '{question_id}'.")
    q.answer, q.status = text, "answered"
    prop = await updater.propose(llm, text, runs.load_resume(d), question=q.text,
                                 source=f"question:{q.id}")
    q.proposal_id = prop.id if prop.action != "none" else None
    if prop.action != "none":
        state.proposals.append(prop)
    save_state(d, state)
    return state


def skip(d: Path, question_id: str) -> GateState:
    state = _require(d)
    q = next((x for x in state.questions if x.id == question_id), None)
    if q is None:
        raise GateError("no_question", f"No question '{question_id}'.")
    q.status = "skipped"
    save_state(d, state)
    return state


async def add_context(llm: LLMClient, d: Path, text: str) -> GateState:
    state = _require(d)
    prop = await updater.propose(llm, text, runs.load_resume(d), source="free_text")
    if prop.action == "none":
        raise GateError("nothing_to_add", prop.summary or "I couldn't find a fact to add in that.")
    state.proposals.append(prop)
    save_state(d, state)
    return state


def resolve_proposal(d: Path, proposal_id: str, accept: bool,
                     edited: ContextProposal | None = None) -> GateState:
    state = _require(d)
    i = next((i for i, p in enumerate(state.proposals) if p.id == proposal_id), None)
    if i is None:
        raise GateError("no_proposal", f"No proposal '{proposal_id}'.")
    prop = state.proposals[i]
    if prop.status != "pending":
        raise GateError("not_pending", f"Proposal is already {prop.status}.")
    if accept:
        if edited is not None:  # you changed the wording before accepting
            prop.project, prop.profile = edited.project, edited.profile
        ctx = updater.apply(prop)
        (d / "projects.json").write_text(ctx.model_dump_json(indent=2), encoding="utf-8")
        prop.status = "accepted"
        state.needs_recompute = True
    else:
        prop.status = "rejected"
    save_state(d, state)
    return state


def override(d: Path, target: str, reason: str = "") -> GateState:
    state = _require(d)
    valid = {"verdict"} | {k.id for k in state.knockouts}
    if target not in valid:
        raise GateError("bad_target", f"Can only override: {', '.join(sorted(valid))}.")
    state.overrides.append(Override(target=target, reason=reason, at=_now()))
    for k in state.knockouts:
        if k.id == target:
            k.status = "overridden"
    state.base_verdict = base_verdict(state.ceiling_score, state.knockouts, state.thresholds)
    state.verdict = effective_verdict(state)
    save_state(d, state)
    return state


def decide(d: Path, decision: str, reason: str = "") -> GateState:
    state = _require(d)
    if decision not in ("proceed", "stop"):
        raise GateError("bad_decision", "decision must be 'proceed' or 'stop'.")
    if decision == "proceed":
        if state.needs_recompute:
            raise GateError("recompute_first",
                            "Your My info notes changed. Re-run the gate so the verdict uses it.")
        if state.verdict == DONT:
            if not reason:
                raise GateError("needs_override", (
                    "The verdict is DON'T APPLY. To improve it anyway, send a reason "
                    "(it's recorded as an override)."))
            override(d, "verdict", reason)
            state = load_state(d)
    state.decision, state.decision_reason = decision, reason or None
    save_state(d, state)
    return state
