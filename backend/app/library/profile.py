"""The saved description of a resume: how strongly it proves each skill and capability.

The AI (skill matcher-profile-resume) reads ONLY the resume and gives each skill/capability a
0-5 evidence level with an exact quote. Code then makes it strict and reliable:
- a rating whose quote isn't in the resume is dropped (no invented skills);
- the level is capped by where the quote is: skills-list only -> 1; no number in it -> max 4;
  the skill must actually appear in the quote;
- skill names are standardised (PostgreSQL = Postgres) and duplicates merged (highest level);
- "last used" and "years" come from the dates of the jobs whose bullets show the skill.
"""
from __future__ import annotations

import re
from datetime import date
from functools import lru_cache

import yaml
from pydantic import BaseModel, Field

from app import config
from app.llm.base import LLMClient, LLMError
from app.parsing.structure import _norm, found_in_source
from app.schemas.models import Resume
from app.scoring.dates import to_month_index, total_years
from app.scoring.synonyms import canonical, count_mentions

SKILL = "matcher-profile-resume"
PROFILE_VERSION = 1
_DIGIT = re.compile(r"\d")
_STRONG = re.compile(r"\b(led|lead|owned|own|owning|designed|architected|headed|drove|spearheaded)\b", re.I)


class SkillLevel(BaseModel):
    name: str
    level: int                      # 1..5
    last_used: str | None = None    # "present" or "YYYY-MM"
    years: float = 0.0              # years of jobs whose bullets show it
    quotes: list[str] = Field(default_factory=list)


class CapabilityLevel(BaseModel):
    id: str
    label: str
    level: int
    quotes: list[str] = Field(default_factory=list)


class ResumeProfile(BaseModel):
    version: int = PROFILE_VERSION
    capabilities_version: int = 0
    skills: list[SkillLevel] = Field(default_factory=list)
    capabilities: list[CapabilityLevel] = Field(default_factory=list)
    dropped: int = 0                # ratings thrown away (quote not found / skill not in quote)
    model: str | None = None


@lru_cache
def capability_vocab() -> tuple[int, dict[str, str]]:
    data = yaml.safe_load((config.CONFIG_DIR / "capabilities.yaml").read_text(encoding="utf-8")) or {}
    return int(data.get("version", 0)), {str(k): str(v) for k, v in (data.get("capabilities") or {}).items()}


def build_input(resume: Resume) -> str:
    lines = []
    if resume.summary:
        lines += ["SUMMARY", resume.summary, ""]
    if resume.experience:
        lines.append("EXPERIENCE")
        for j in resume.experience:
            lines.append(" - ".join(x for x in (j.title, j.company) if x)
                         + f" ({j.start or '?'} to {j.end or '?'})")
            lines += [f"- {b}" for b in j.bullets]
        lines.append("")
    for p in resume.projects:
        lines.append(f"PROJECT {p.name or ''}".strip())
        lines += [f"- {b}" for b in p.bullets]
        if p.tech:
            lines.append("Tech: " + ", ".join(p.tech))
    if resume.skills:
        lines += ["", "SKILLS", ", ".join(resume.skills)]
    if resume.achievements or resume.certifications:
        lines += ["", "ACHIEVEMENTS", *[f"- {a}" for a in resume.achievements + resume.certifications]]
    _, vocab = capability_vocab()
    caps = "\n".join(f"{k}: {v}" for k, v in vocab.items())
    return "RESUME:\n" + "\n".join(lines) + "\n\nCAPABILITIES:\n" + caps


def _where(quote: str, resume: Resume) -> tuple[str, list[int]]:
    """'bullet' (with the job indexes whose bullets contain it), 'summary', 'list' or 'none'."""
    q = _norm(quote)
    jobs = [i for i, j in enumerate(resume.experience) if any(found_in_source(quote, _norm(b)) for b in j.bullets)]
    if jobs:
        return "bullet", jobs
    if any(found_in_source(quote, _norm(b)) for p in resume.projects for b in p.bullets + [p.description or ""]):
        return "bullet", []
    if resume.summary and found_in_source(quote, _norm(resume.summary)):
        return "summary", []
    rest = _norm(" ".join(resume.skills + resume.achievements + resume.certifications
                          + [t for p in resume.projects for t in p.tech]))
    if q and found_in_source(quote, rest):
        return "list", []
    return "none", []


def _cap_level(level: int, quote: str, where: str) -> int:
    if where == "list":
        return 1
    if where == "summary":
        level = min(level, 2)
    if not _DIGIT.search(quote):
        level = min(level, 4)
    if level >= 4 and not _STRONG.search(quote):
        level = min(level, 3)   # owned/designed must be said, not guessed
    return level


def _job_dates(resume: Resume, skill: str, jobs_hint: list[int], today: date | None) -> tuple[str | None, float]:
    jobs = sorted(set(jobs_hint) | {i for i, j in enumerate(resume.experience)
                                    if any(count_mentions(skill, b) for b in j.bullets)})
    if not jobs:
        return None, 0.0
    spans = [(resume.experience[i].start, resume.experience[i].end) for i in jobs]
    years, _ = total_years(spans, today=today)
    ends = []
    for i in jobs:
        e = resume.experience[i].end
        if e and e.strip().lower() in ("present", "current", "now"):
            return "present", round(years, 1)
        m = to_month_index(e, today=today, is_end=True)
        if m:
            ends.append(m)
    if not ends:
        return None, round(years, 1)
    last = max(ends)
    y, mo = divmod(last - 1, 12)
    return f"{y:04d}-{mo + 1:02d}", round(years, 1)


def normalize(raw: dict, resume: Resume, today: date | None = None) -> ResumeProfile:
    cap_version, vocab = capability_vocab()
    out = ResumeProfile(capabilities_version=cap_version)
    skills: dict[str, SkillLevel] = {}
    for item in (raw.get("skills") or []) if isinstance(raw, dict) else []:
        try:
            name, level, quote = str(item["name"]).strip(), int(item["level"]), str(item.get("quote") or "")
        except (KeyError, TypeError, ValueError):
            out.dropped += 1
            continue
        if not name or level < 1 or not quote.strip():
            out.dropped += 1
            continue
        where, jobs = _where(quote, resume)
        if where == "none" or not count_mentions(name, quote):
            out.dropped += 1          # quote not in the resume, or the skill isn't in its own quote
            continue
        level = _cap_level(min(level, 5), quote, where)
        canon = canonical(name)
        last, years = _job_dates(resume, canon, jobs, today)
        s = skills.get(canon.lower())
        if s is None:
            skills[canon.lower()] = SkillLevel(name=canon, level=level, last_used=last, years=years, quotes=[quote])
        else:
            s.level = max(s.level, level)
            s.quotes.append(quote)
            s.years = max(s.years, years)
            s.last_used = s.last_used or last
    # a skill listed but never rated by the AI still counts as "listed"
    for name in resume.skills:
        canon = canonical(name)
        if canon.lower() not in skills:
            skills[canon.lower()] = SkillLevel(name=canon, level=1, quotes=[name])
    out.skills = sorted(skills.values(), key=lambda s: (-s.level, -s.years, s.name.lower()))

    caps: dict[str, CapabilityLevel] = {}
    for item in (raw.get("capabilities") or []) if isinstance(raw, dict) else []:
        try:
            cid, level, quote = str(item["id"]).strip(), int(item["level"]), str(item.get("quote") or "")
        except (KeyError, TypeError, ValueError):
            out.dropped += 1
            continue
        if cid not in vocab or level < 1 or not quote.strip():
            out.dropped += 1
            continue
        where, _ = _where(quote, resume)
        if where in ("none", "list"):
            out.dropped += 1
            continue
        level = _cap_level(min(level, 5), quote, where)
        c = caps.get(cid)
        if c is None:
            caps[cid] = CapabilityLevel(id=cid, label=vocab[cid], level=level, quotes=[quote])
        else:
            c.level = max(c.level, level)
            c.quotes.append(quote)
    out.capabilities = sorted(caps.values(), key=lambda c: (-c.level, c.id))
    return out


async def build_profile(llm: LLMClient, resume: Resume, today: date | None = None) -> ResumeProfile:
    data = await llm.run_skill(SKILL, build_input(resume))
    if not isinstance(data, dict):
        raise LLMError("The resume profile reply was not a JSON object.")
    p = normalize(data, resume, today)
    p.model = getattr(llm, "last_model", None)
    return p
