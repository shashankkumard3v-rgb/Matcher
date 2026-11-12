"""Quick match: how well each saved resume fits a job description, from the saved profiles.

Code only (no AI at match time), so it's instant and gives the same answer every time.
Points (100):
  required skills  55  each JD must-have: best matching skill's level/5, less if not used lately
  nice-to-have     10  same, for nice-to-have skills
  capabilities     20  each kind of work the JD asks for: the resume's level/5 for it
  years             8  the resume's total years vs the JD's minimum
  title             7  words shared by the resume's latest job title and the JD title
A JD part that is empty (e.g. no nice-to-haves) gives its points to required skills.
"""
from __future__ import annotations

import re
from datetime import date

from pydantic import BaseModel, Field

from app.library.profile import ResumeProfile, capability_vocab
from app.schemas.models import JobDescription, Resume
from app.scoring.dates import to_month_index, total_years
from app.scoring.synonyms import (alternatives, canonical, close_credit, close_to, count_mentions, needed_count,
                                   shown_by)

WEIGHTS = {"required": 55, "nice": 10, "capabilities": 20, "years": 8, "title": 7}
_TITLE_STOP = {"senior", "sr", "junior", "jr", "lead", "principal", "staff", "ii", "iii", "iv", "i",
               "the", "and", "of", "for", "-", "&", "associate", "intern", "head", "chief"}


class SkillFit(BaseModel):
    jd_skill: str
    matched: str | None = None
    level: int = 0
    credit: float = 0.0
    note: str = ""


class MatchResult(BaseModel):
    resume_id: str = ""
    name: str = ""
    score: float
    parts: dict[str, float]
    required: list[SkillFit] = Field(default_factory=list)
    nice: list[SkillFit] = Field(default_factory=list)
    capabilities: list[dict] = Field(default_factory=list)  # {id, label, level}
    summary: str = ""


def _recency(last_used: str | None, today: date) -> float:
    if not last_used or last_used == "present":
        return 1.0
    m = to_month_index(last_used, today=today)
    if m is None:
        return 1.0
    years_ago = (today.year * 12 + today.month - m) / 12
    return 1.0 if years_ago <= 1 else 0.9 if years_ago <= 2 else 0.75 if years_ago <= 4 else 0.6


def _fit_one(skill: str, profile: ResumeProfile, today: date) -> SkillFit:
    """The best saved-resume skill for ONE skill: the same skill, one that shows it
    (TypeScript shows JavaScript), or a close one (Java for Kotlin) at part of the credit."""
    best = SkillFit(jd_skill=skill)
    want = canonical(skill).lower()
    via = {canonical(x).lower() for x in shown_by(skill)}
    near = {canonical(x).lower() for x in close_to(skill)}
    for s in profile.skills:
        n = s.name.lower()
        if n == want or n in via or count_mentions(s.name, skill) or count_mentions(skill, s.name):
            factor, how = 1.0, ""
        elif n in near:
            factor, how = close_credit(), "close, not the same; "
        else:
            continue
        credit = s.level / 5 * _recency(s.last_used, today) * factor
        if credit > best.credit:
            note = how + ("only listed" if s.level == 1 else
                          f"level {s.level}/5" + ("" if s.last_used in (None, "present") else f", last used {s.last_used}"))
            best = SkillFit(jd_skill=skill, matched=s.name, level=s.level, credit=round(credit, 3), note=note)
    if best.matched is None:
        best.note = "missing"
    return best


def _fit(jd_skill: str, profile: ResumeProfile, today: date) -> SkillFit:
    """A JD requirement: one skill, ANY ONE of several ("Java or Go or C++"), or N of them
    ("2 of Java or Go or C++": the best two count, averaged)."""
    opts = alternatives(jd_skill)
    if len(opts) == 1:
        f = _fit_one(opts[0], profile, today)
        f.jd_skill = jd_skill
        return f
    need = min(needed_count(jd_skill), len(opts))
    fits = sorted((_fit_one(o, profile, today) for o in opts), key=lambda f: -f.credit)[:need]
    shown = [f for f in fits if f.matched]
    if not shown:
        return SkillFit(jd_skill=jd_skill, note="missing (any one is enough)" if need == 1 else f"missing ({need} needed)")
    credit = round(sum(f.credit for f in fits) / need, 3)
    note = fits[0].note if need == 1 else f"{len(shown)} of {need} needed: " + ", ".join(
        f"{f.matched} {f.level}/5" for f in shown)
    return SkillFit(jd_skill=jd_skill, matched=" + ".join(f.matched for f in shown), level=min(f.level for f in shown),
                    credit=credit, note=note)


def _resume_years(resume: Resume, today: date) -> float:
    y, _ = total_years([(j.start, j.end) for j in resume.experience], today=today)
    return y


def _title_fit(resume: Resume, jd: JobDescription) -> float:
    if not jd.title or not resume.experience or not resume.experience[0].title:
        return 0.5
    words = lambda t: {w for w in re.findall(r"[a-z]+", t.lower()) if w not in _TITLE_STOP}  # noqa: E731
    want, have = words(jd.title), words(resume.experience[0].title)
    return len(want & have) / len(want) if want else 0.5


def match(profile: ResumeProfile, resume: Resume, jd: JobDescription, today: date | None = None) -> MatchResult:
    today = today or date.today()
    _, vocab = capability_vocab()
    req = [_fit(s, profile, today) for s in jd.must_have]
    nice = [_fit(s, profile, today) for s in jd.nice_to_have]
    have_caps = {c.id: c.level for c in profile.capabilities}
    caps = [{"id": c, "label": vocab.get(c, c), "level": have_caps.get(c, 0)} for c in jd.capabilities]

    w = dict(WEIGHTS)
    if not nice:
        w["required"] += w.pop("nice")
    if not caps:
        w["required"] += w.pop("capabilities")
    if not req:  # nothing required: spread over what's left
        extra = w.pop("required")
        for k in w:
            w[k] += extra / len(w)
    parts = {}
    if "required" in w:
        parts["required"] = w["required"] * (sum(f.credit for f in req) / len(req))
    if "nice" in w:
        parts["nice"] = w["nice"] * (sum(f.credit for f in nice) / len(nice))
    if "capabilities" in w:
        parts["capabilities"] = w["capabilities"] * (sum(c["level"] for c in caps) / (5 * len(caps)))
    years = _resume_years(resume, today)
    parts["years"] = w["years"] * (min(years / jd.years_required, 1.0) if jd.years_required else 1.0)
    parts["title"] = w["title"] * _title_fit(resume, jd)
    parts = {k: round(v, 1) for k, v in parts.items()}

    strong = [f.jd_skill for f in req if f.level >= 3]
    weak = [f.jd_skill for f in req if 0 < f.level < 3]
    missing = [f.jd_skill for f in req if f.level == 0]
    bits = [f"shows {len(strong)}/{len(req)} required skills" if req else "no required skills listed"]
    if weak:
        bits.append("weak: " + ", ".join(weak))
    if missing:
        bits.append("missing: " + ", ".join(missing))
    return MatchResult(score=round(sum(parts.values()), 1), parts=parts, required=req, nice=nice,
                       capabilities=caps, summary="; ".join(bits))
