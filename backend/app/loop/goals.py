"""What every AI writing step is told about the goal, and what every bullet must keep.

Before, each step got the job ad and its own little task, but not WHY: which gaps this round
is closing, what a finished bullet must contain, and what's already on the resume. So it
sometimes rewrote a bullet into something the resume already said, or dropped the number or
the tool that made the bullet worth anything. Now:

- goal_block():   the same short GOAL section for every writing step (target job, the gaps
                  this round is trying to close in order, the bullet recipe).
- must_keep():    for each bullet being rewritten: its numbers and the tools/skills it names.
                  The AI is told to keep them, and code checks it did (see dropped()).
- avoid_list():   bullets already on the resume, so a new one never repeats them; code checks
                  that too (see duplicate_of()).
"""
from __future__ import annotations

import re

from app.parsing.structure import _norm
from app.schemas.models import JobDescription, Resume, Round

_NUM = re.compile(r"(?<![A-Za-z0-9.])\$?\d+(?:[.,]\d+)?\s?(?:%|x|k|K|M|B|ms|s|h|hrs?|hours?|minutes?|mins?|days?|"
                  r"weeks?|months?|years?|TB|GB|MB|rps|qps|req/s|users?|requests?|events?)?(?![A-Za-z])")

RECIPE = [
    "Start with a strong past-tense verb (Built, Designed, Cut, Migrated, Led only if true).",
    "Say WHAT was built or changed, HOW (the tools/tech, by their JD names), and the SCALE or RESULT.",
    "Keep every number and every tool listed in the item's must_keep, exactly.",
    "Name the item's bring_out skills when the facts support them.",
    "Don't repeat anything in AVOID (bullets already on the resume).",
    "One sentence, no filler, at most MAX WORDS words.",
]


def numbers(text: str) -> list[str]:
    return list(dict.fromkeys(m.group().strip() for m in _NUM.finditer(text or "") if any(ch.isdigit() for ch in m.group())))


def tools(text: str) -> list[str]:
    """Known skills/tools named in the text (from the synonym table), by their usual names."""
    from app.scoring.synonyms import canonical, count_mentions, synonym_table
    names = sorted(set(synonym_table()[1].values()))
    return [n for n in names if count_mentions(n, text or "")]


def must_keep(*texts: str) -> list[str]:
    joined = "\n".join(t for t in texts if t)
    return list(dict.fromkeys(numbers(joined) + tools(joined)))


def dropped(old: str, old2: str | None, new: str) -> list[str]:
    """must_keep items of the old bullet(s) that the new one lost."""
    from app.scoring.synonyms import count_mentions
    out = []
    new_digits = {re.sub(r"\D", "", n) for n in numbers(new)}
    for n in numbers("\n".join(x for x in (old, old2) if x)):
        if re.sub(r"\D", "", n) not in new_digits:
            out.append(n)
    for t in tools("\n".join(x for x in (old, old2) if x)):
        if not count_mentions(t, new):
            out.append(t)
    return out


def duplicate_of(new: str, resume: Resume, ignore: tuple[str, ...] = ()) -> str | None:
    """An existing bullet that says the same thing as `new` (most words shared)."""
    from app.gate.ceiling import near_duplicate
    skip = {_norm(x) for x in ignore if x}
    for job in resume.experience:
        for b in job.bullets:
            if _norm(b) in skip:
                continue
            # only when the edit CREATES the repeat (a rewrite of a bullet that already looked
            # like this one isn't made worse by staying close to it)
            if near_duplicate(new, b, threshold=0.75) and not any(near_duplicate(x, b, threshold=0.75)
                                                                    for x in ignore if x):
                return b
    return None


def avoid_list(resume: Resume, jobs: set[int] | None = None) -> list[str]:
    return [b for i, j in enumerate(resume.experience) if jobs is None or i in jobs for b in j.bullets]


def goal_block(jd: JobDescription, r: Round | None) -> str:
    """The same GOAL section for every writing step."""
    target = " at ".join(x for x in (jd.title, jd.company) if x) or "this job"
    lines = [f"GOAL: make this resume score higher for {target}, using only the candidate's own facts."]
    if r is not None:
        gaps = [p for p in r.problems if p.fix_by != "none"][:6]
        if gaps:
            lines.append("THIS ROUND IS CLOSING THESE GAPS (most points first):")
            lines += [f"- {p.cause} ({p.points_lost:.1f} points)" for p in gaps]
        if r.notes:
            lines.append("THE USER ASKED FOR: " + "; ".join(r.notes))
    lines.append("EVERY BULLET MUST:")
    lines += [f"- {x}" for x in RECIPE]
    return "\n".join(lines)
