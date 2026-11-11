"""Turn a score into a ranked list of problems, each tagged with who can fix it.

ai   -> the facts already exist (resume or template); a rewrite can fix it
you  -> the facts are missing; Matcher asks you
none -> rewriting can't fix it (job titles, real years); shown so you know
"""
from __future__ import annotations

import re

from app.parsing.structure import _norm
from app.schemas.models import (
    JobDescription,
    LoopQuestion,
    Problem,
    ProjectContext,
    Resume,
    ScoreResult,
)
from app.scoring.render import evidence_text
from app.scoring.synonyms import count_mentions

_DIGIT = re.compile(r"\d")
_WORD = re.compile(r"[a-z][a-z0-9+#.]{3,}")
STOPWORDS = {"with", "from", "that", "this", "your", "will", "have", "work", "team", "using",
             "build", "design", "develop", "ensure", "across", "other", "their", "about"}


def template_text(ctx: ProjectContext) -> dict[str, str]:
    """source id -> text you wrote: each template project, 'profile', and your notes files
    ('notes-experience', 'notes-skills')."""
    from app.context import notes

    out = {}
    for p in ctx.projects:
        out[p.id] = "\n".join(
            [p.name, p.problem or "", p.notes or "", *p.what_i_did, *p.tech, *p.results])
    out["profile"] = "\n".join(ctx.profile.languages + ctx.profile.facts)
    for kind, lines in notes.all_facts().items():
        out[f"notes-{kind}"] = "\n".join(lines)
    return out


def _template_has(skill: str, tpl: dict[str, str]) -> str | None:
    for pid, text in tpl.items():
        if count_mentions(skill, text):
            return pid
    return None


def _uncovered_responsibilities(jd: JobDescription, resume: Resume, tpl: dict[str, str]) -> list[str]:
    known = _norm(evidence_text(resume) + "\n" + "\n".join(tpl.values()))
    out = []
    for r in jd.responsibilities:
        words = {w for w in _WORD.findall(_norm(r)) if w not in STOPWORDS}
        if words and sum(w in known for w in words) / len(words) < 0.5:
            out.append(r)
    return out


def diagnose(score: ScoreResult, resume: Resume, jd: JobDescription, ctx: ProjectContext
             ) -> list[Problem]:
    dims = {d.key: d for d in score.dimensions}
    tpl = template_text(ctx)
    problems: list[Problem] = []

    for key, hits in (("must_have_coverage", score.must_have), ("nice_to_have_coverage", score.nice_to_have)):
        d = dims[key]
        per = d.weight / max(len(hits), 1)
        tag = "must" if key.startswith("must") else "nice"
        for h in hits:
            lost = round(per * (1 - h.credit), 2)
            if lost <= 0:
                continue
            pid = _template_has(h.skill, tpl)
            if h.status == "missing":
                cause = f"{h.skill} is required" if tag == "must" else f"{h.skill} is a plus"
                cause += " and doesn't appear in your resume."
            elif h.status == "listed_only":
                cause = f"{h.skill} is only listed; no bullet shows you using it."
            else:
                cause = h.note or f"{h.skill} is over-mentioned."
            if h.status == "evidenced":  # stuffing: only wording can fix it
                fix, detail = "ai", "Reduce repetition of this keyword."
            elif pid:
                where = (f"your {pid.split('-', 1)[1]}.txt notes" if pid.startswith("notes-")
                         else "your My info notes")
                fix, detail = "ai", f"{where[0].upper() + where[1:]} mention it, so a bullet can show it."
            else:
                fix, detail = "you", "No source shows it yet."
            problems.append(Problem(id=f"{tag}:{_norm(h.skill)}", dimension=key, label=d.label,
                                    points_lost=lost, cause=cause, fix_by=fix, detail=detail,
                                    subject=h.skill))

    for key, pid, fix, why in (
        ("experience_relevance", "relevance", "you", "Recent work doesn't show enough of the JD's core duties."),
        ("impact_quantification", "impact", "you", "Bullets show duties more than measurable results."),
        ("seniority_scope", "seniority", "you", "Ownership, scale or leadership is thinner than the JD asks."),
        ("domain_fit", "domain", "you", "Little overlap with the JD's industry or problem space."),
    ):
        d = dims.get(key)
        if d and d.lost > 0:
            problems.append(Problem(id=pid, dimension=key, label=d.label, points_lost=d.lost,
                                    cause=d.reason or why, fix_by=fix, detail=why))

    for key, pid, detail in (
        ("title_alignment", "title", "Job titles stay as they are; they can't be rewritten."),
        ("years_fit", "years", "Years come from your real job dates."),
        ("ats_format", "format", "Layout is handled when the final Word file is built."),
    ):
        d = dims.get(key)
        if d and d.lost > 0:
            problems.append(Problem(id=pid, dimension=key, label=d.label, points_lost=d.lost,
                                    cause=d.reason, fix_by="none", detail=detail))

    return sorted(problems, key=lambda p: (-p.points_lost, p.id))


def make_questions(problems: list[Problem], resume: Resume, jd: JobDescription,
                   ctx: ProjectContext, already_asked: set[str], limit: int) -> list[LoopQuestion]:
    tpl = template_text(ctx)
    qs: list[LoopQuestion] = []
    for p in problems:
        if p.fix_by != "you":
            continue
        skill = p.subject
        if p.id.startswith(("must:", "nice:")) and "only listed" in p.cause:
            text = f"{skill} is only listed. What did you build or do with {skill}, and at which job?"
        elif p.id.startswith(("must:", "nice:")):
            text = (f"The JD wants {skill}, and nothing on your resume or My info notes shows it. Have you "
                    f"used it? If yes: where, what did you do, and what came of it?")
        elif p.id == "impact":
            plain = [b for j in resume.experience[:2] for b in j.bullets if not _DIGIT.search(b)][:2]
            examples = "; ".join(f"\"{b}\"" for b in plain) or "your recent bullets"
            text = (f"Do any of these have a measurable result: {examples}? For example time saved, "
                    "users, requests, money, % improvement. Only give numbers you're sure of.")
        elif p.id == "relevance":
            gaps = _uncovered_responsibilities(jd, resume, tpl)[:2]
            if gaps:
                text = ("The JD expects: " + "; ".join(f"\"{g}\"" for g in gaps)
                        + ". Have you done any of this? Where and how?")
            else:
                text = "Which of your projects is closest to this JD's main work? What exactly did you do?"
        elif p.id == "seniority":
            text = ("Did you lead people, own a system end to end, mentor anyone, or make key design "
                    "decisions? Give team size or scope if you can.")
        elif p.id == "domain":
            area = jd.domain or "this company's industry"
            text = f"Have you worked on anything related to {area}? What was it?"
        else:
            continue
        qid = f"q:{p.id}"
        if qid in already_asked:
            continue
        qs.append(LoopQuestion(id=qid, problem_id=p.id, text=text,
                               why=f"{p.label}: {p.points_lost:.1f} points lost.",
                               points_at_stake=p.points_lost))
        if len(qs) >= limit:
            break
    return qs
