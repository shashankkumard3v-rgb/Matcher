"""Code checks around the AI in the improve loop: the AI only writes wording; everything that
can be counted is decided and checked here, so a wrong or made-up AI edit can't slip through.

- labels:   which bullets are the only proof of a job skill (protected), which repeat what
            other bullets already show (redundant), which over-mention a skill (stuffing).
- review:   for every suggested edit, the exact change in the rules-based points (computed,
            not guessed), and blocks edits that lose the only proof of a skill or drop a
            number a merge should keep.
- clashes:  two edits on the same bullet: the one worth more points stays.
- skills:   adding a skill your notes show to the Skills list is a code-only edit (no AI).
"""
from __future__ import annotations

import re

from app.parsing.structure import _norm
from app.schemas.models import Change, ChangeSource, JobDescription, Resume

_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in _NUM.findall(text or "")}


def jd_skills(jd: JobDescription) -> list[str]:
    from app.scoring.synonyms import canonical
    seen, out = set(), []
    for s in [*jd.must_have, *jd.nice_to_have]:
        k = canonical(s).lower()
        if k not in seen:
            seen.add(k)
            out.append(s)
    return out


def shows(skill: str, text: str) -> bool:
    from app.gate.evidence import _shows
    return _shows(skill, text) is not None


def shown_skills(resume: Resume, jd: JobDescription) -> set[str]:
    """JD skills the resume's bullets (and projects) show."""
    from app.scoring.render import evidence_text
    ev = evidence_text(resume)
    return {s for s in jd_skills(jd) if shows(s, ev)}


def code_points(resume: Resume, jd: JobDescription) -> float:
    """The rules-based part of the score (skills, title, years, format), exactly as the scorer
    computes it. The AI-judged part needs a real re-score."""
    from app.config import weights_config
    from app.scoring.deterministic import score_deterministic
    cfg = weights_config()
    dims, *_ = score_deterministic(resume, jd, cfg["weights"], cfg.get("scoring", {}), set(), None)
    return round(sum(d.points for d in dims), 2)


# ---------- labels ----------

def labels(resume: Resume, jd: JobDescription) -> dict[tuple[int, int], dict]:
    """(job, bullet) -> {"protected": [skills it alone proves], "redundant": bool, "stuffed": [skills]}"""
    from app.config import weights_config
    from app.scoring.render import evidence_text, skills_text
    from app.scoring.synonyms import alternatives, count_mentions
    max_m = int(weights_config().get("scoring", {}).get("stuffing_max_mentions", 4))
    skills = jd_skills(jd)
    ev_all, listed = evidence_text(resume), skills_text(resume)
    stuffed = {s for s in skills
               if any(count_mentions(a, ev_all) + count_mentions(a, listed) > max_m for a in alternatives(s))}
    bullets = [(j, b, t) for j, job in enumerate(resume.experience) for b, t in enumerate(job.bullets)]
    proj = "\n".join(x for p in resume.projects for x in [p.description or "", *p.bullets, *p.tech])
    out = {}
    for j, b, t in bullets:
        mine = [s for s in skills if shows(s, t)]
        others = "\n".join([x for jj, bb, x in bullets if (jj, bb) != (j, b)] + [proj])
        protected = [s for s in mine if not shows(s, others)]
        from app.loop.bullets import value_of
        duties = set(value_of(t, jd)[1]["duties"])
        other_duties = set().union(*[set(value_of(x, jd)[1]["duties"]) for jj, bb, x in bullets if (jj, bb) != (j, b)]) \
            if len(bullets) > 1 else set()
        redundant = (not protected and not _numbers(t) and bool(mine or duties)
                     and duties <= other_duties)
        out[(j, b)] = {"protected": protected, "redundant": redundant,
                       "stuffed": [s for s in mine if s in stuffed]}
    return out


# ---------- checking suggested edits ----------

def _apply(resume: Resume, changes: list[Change]) -> Resume:
    from app.loop.loop import apply_changes
    return apply_changes(resume, changes)


def review(resume: Resume, jd: JobDescription, changes: list[Change], by_you: bool = False,
           fit: bool = False) -> None:
    """Sets points_delta on each edit and blocks the harmful ones (your own edits only get a note)."""
    before_pts = code_points(resume, jd)
    before_skills = shown_skills(resume, jd)
    for c in changes:
        after = _apply(resume, [c])
        c.points_delta = round(code_points(after, jd) - before_pts, 2)
        c.impact = explain(resume, after, jd, c, before_skills)
        issues = []
        lost = sorted(before_skills - shown_skills(after, jd))
        if lost:
            issues.append("Loses the only proof of " + ", ".join(lost) + " on the resume.")
        notes_only: list[str] = []
        if c.kind in ("replace_bullet", "merge_bullets") and c.new and c.old:
            from app.loop.goals import dropped
            lost_bits = dropped(c.old, c.old2, c.new)
            if lost_bits:
                msg = ("Drops " + ", ".join(lost_bits) + " from the original. Keep them, or edit it yourself "
                       "if you really want them gone.")
                # shortening to fit the page may cut a tool name; numbers are always kept
                if fit and not any(any(ch.isdigit() for ch in b) for b in lost_bits):
                    notes_only.append("Note: " + msg)
                else:
                    issues.append(msg)
        if c.kind in ("add_bullet", "replace_bullet", "merge_bullets") and c.new:
            from app.loop.goals import duplicate_of
            twin = duplicate_of(c.new, resume, ignore=(c.old or "", c.old2 or ""))
            if twin:
                issues.append(f"Says the same as a bullet already on the resume: \"{_short(twin, 80)}\"")
        c.truth_issues += notes_only
        if not issues:
            continue
        if by_you:
            c.truth_issues += [f"Note: {i}" for i in issues]
        else:
            c.truth_issues += issues
            c.truth_ok = False
            if c.status == "pending":
                c.status = "blocked"


def _short(t: str, n: int = 60) -> str:
    return t if len(t) <= n else t[:n].rsplit(" ", 1)[0] + "…"


def explain(before: Resume, after: Resume, jd: JobDescription, c: Change, before_skills: set[str]) -> list[str]:
    """Plain facts about an edit, so you can decide: what the bullet shows, what is lost or gained,
    who else shows it, the points, and what you can do instead."""
    from app.loop.bullets import value_of
    out: list[str] = []
    after_skills = shown_skills(after, jd)
    gained, lost = sorted(after_skills - before_skills), sorted(before_skills - after_skills)
    if c.kind in ("remove_bullet", "merge_bullets") and c.old:
        mine = [s for s in jd_skills(jd) if shows(s, c.old)]
        duties = value_of(c.old, jd)[1]["duties"]
        if c.kind == "remove_bullet":
            if mine or duties:
                out.append("This bullet shows: " + ", ".join(mine + [f"duty \"{_short(x, 40)}\"" for x in duties]) + ".")
            else:
                out.append("This bullet shows none of the job's skills or duties.")
            others = [b for j, job in enumerate(after.experience) for b in job.bullets]
            kept = [s for s in mine if s not in lost]
            for s in kept[:3]:
                by = next((b for b in others if shows(s, b)), None)
                if by:
                    out.append(f"{s} stays shown by: \"{_short(by)}\"")
            out.append("It has a number (good for the impact grade); removing it loses that."
                       if _numbers(c.old) else "It has no measurable result.")
            job = before.experience[c.job] if c.job is not None and c.job < len(before.experience) else None
            if job is not None:
                out.append(f"{c.job_label or 'This job'} goes from {len(job.bullets)} to {len(job.bullets) - 1} bullets.")
            out.append("Rather keep it? Reject this, or use Revise and ask to reword it instead.")
    if gained:
        out.append("Newly shows: " + ", ".join(gained) + ".")
    if lost:
        out.append("No longer shown anywhere: " + ", ".join(lost) + ".")
    if c.kind in ("replace_bullet", "merge_bullets") and c.new:
        before_n, after_n = _numbers(c.old) | _numbers(c.old2), _numbers(c.new)
        if after_n - before_n:
            out.append("Adds result number(s) from your notes: " + ", ".join(sorted(after_n - before_n)) + ".")
    if c.points_delta:
        out.append(f"Rules-based points: {c.points_delta:+g}. The AI-judged part is re-scored after you apply.")
    elif c.kind == "remove_bullet":
        out.append("Rules-based points: no change. It frees space on the page.")
    return out


def _touches(c: Change) -> set[tuple]:
    """The bullets an edit changes (job + text: two jobs may have the same sentence)."""
    if c.kind in ("add_bullet", "add_skill", "add_achievement"):
        return set()
    return {(c.job, _norm(x)) for x in (c.old, c.old2) if x}


def clashes(changes: list[Change]) -> None:
    """Two pending edits on the same bullet can't both apply: the one worth more points stays."""
    live = [c for c in changes if c.status == "pending" and _touches(c)]
    live.sort(key=lambda c: -(c.points_delta or 0))
    taken: dict[tuple, str] = {}
    for c in live:
        hit = next((taken[t] for t in _touches(c) if t in taken), None)
        if hit:
            c.status = "blocked"
            c.truth_issues.append(f"Changes the same bullet as {hit}, which is worth more. Reject that one to use this.")
            continue
        for t in _touches(c):
            taken[t] = c.id


# ---------- Skills list (code only) ----------

def skill_edits(resume: Resume, jd: JobDescription, found: list, start: int, prefix: str,
                planned: list[Change] | None = None) -> list[Change]:
    """A JD skill the resume doesn't list or show, that your notes name (or the gate found in
    your notes): adding it to Skills earns the listed share right away, at no bullet space."""
    from app.context import notes
    from app.scoring.render import evidence_text, skills_text
    from app.scoring.synonyms import alternatives, canonical, count_mentions
    # skills this round's other edits already bring in don't need a Skills entry too
    have = "\n".join([evidence_text(resume), skills_text(resume)]
                     + [c.new or "" for c in planned or [] if c.status != "blocked"])
    note_lines = [ln for kind in ("experience", "skills")
                  for h, ln in notes.entries(notes.read_raw(kind)) if h != ln]
    out: list[Change] = []
    for s in jd_skills(jd):
        if any(count_mentions(a, have) for a in alternatives(s)):
            continue
        for opt in alternatives(s):
            quote = next((ln for ln in note_lines if count_mentions(opt, ln)), None)
            if quote is None:
                ev = next((f for f in found or [] if canonical(f.skill).lower() == canonical(s).lower()), None)
                quote = ev.quotes[0] if ev and ev.quotes else None
            if quote:
                out.append(Change(
                    id=f"{prefix}{start + len(out)}", kind="add_skill", new=canonical(opt), job_label="Skills",
                    why=f"Your notes show {canonical(opt)}; listing it earns part of its points right away. "
                        "A bullet showing it earns the rest.",
                    fixes=[f"skill:{_norm(s)}"],
                    sources=[ChangeSource(type="notes", project_id=None, quote=quote)]))
                break
    return out


# ---------- every job keeps at least 2 bullets ----------

MIN_HINT = "every job keeps at least"


def min_check(resume: Resume, changes: list[Change], floor: int) -> None:
    """Block removals/merges that would leave a job with fewer than `floor` bullets (in the order
    they'd apply). Re-run after every decision: rejecting one removal can unblock another."""
    for c in changes:          # undo earlier blocks of this kind; recomputed below
        if any(MIN_HINT in i for i in c.truth_issues):
            c.truth_issues = [i for i in c.truth_issues if MIN_HINT not in i]
            if c.status == "blocked" and not c.truth_issues:
                c.status, c.truth_ok = "pending", True
    count = [len(j.bullets) for j in resume.experience]
    for c in changes:
        if c.job is None or c.status in ("rejected", "blocked") or c.kind not in ("remove_bullet", "merge_bullets"):
            continue
        if count[c.job] - 1 < floor:
            msg = (f"{c.job_label or 'This job'} would be left with {count[c.job] - 1} bullet(s); "
                   f"{MIN_HINT} {floor}. Reword it instead, or reject another removal there first.")
            if c.status == "pending":
                c.status, c.truth_ok = "blocked", False
                c.truth_issues.append(msg)
            continue
        count[c.job] -= 1
