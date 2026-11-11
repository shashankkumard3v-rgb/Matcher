"""The 55 deterministic points. Pure functions: same resume + JD -> same points, always."""
from __future__ import annotations

import re
from datetime import date

from app.schemas.models import JobDescription, Resume, ScoreDimension, SkillHit
from app.scoring.dates import total_years
from app.scoring.render import evidence_text, skills_text
from app.scoring.synonyms import canonical, count_mentions

SENIORITY_WORDS = {
    "senior", "sr", "junior", "jr", "lead", "principal", "staff", "associate", "intern",
    "trainee", "mid", "level", "i", "ii", "iii", "iv", "head", "chief", "entry",
}
TITLE_SYNONYMS = {
    "developer": "engineer", "dev": "engineer", "programmer": "engineer", "sde": "software engineer",
    "swe": "software engineer", "back end": "backend", "back-end": "backend",
    "front end": "frontend", "front-end": "frontend", "full stack": "fullstack",
    "full-stack": "fullstack", "ml": "machine learning", "ai": "machine learning",
}
STOP = {"and", "of", "the", "for", "to", "in", "a", "an", "&", "-", "/", "with"}


def _round(x: float) -> float:
    return round(x + 1e-9, 2)


# ---------- skills coverage ----------

def skill_hits(skills: list[str], resume: Resume, cfg: dict) -> list[SkillHit]:
    ev, listed = evidence_text(resume), skills_text(resume)
    list_only = float(cfg.get("list_only_credit", 0.5))
    max_mentions = int(cfg.get("stuffing_max_mentions", 4))
    stuffed_credit = float(cfg.get("stuffing_credit", 0.75))
    hits, seen = [], set()
    for raw in skills:
        name = canonical(raw)
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        in_ev, in_list = count_mentions(name, ev), count_mentions(name, listed)
        if in_ev:
            credit, status, note = 1.0, "evidenced", None
        elif in_list:
            credit, status = list_only, "listed_only"
            note = "Only in the Skills list; no bullet shows it being used."
        else:
            credit, status, note = 0.0, "missing", None
        total = in_ev + in_list
        if total > max_mentions:
            credit *= stuffed_credit
            note = f"Mentioned {total} times (more than {max_mentions}): looks like keyword stuffing."
        hits.append(SkillHit(skill=name, status=status, credit=_round(credit), mentions=total, note=note))
    return hits


def coverage_dimension(key: str, label: str, weight: float, hits: list[SkillHit]) -> ScoreDimension:
    if not hits:
        return ScoreDimension(key=key, label=label, method="deterministic", weight=weight,
                              points=weight, reason="The JD lists none, so full points.")
    share = sum(h.credit for h in hits) / len(hits)
    missing = [h.skill for h in hits if h.status == "missing"]
    listed = [h.skill for h in hits if h.status == "listed_only"]
    parts = [f"{sum(h.status == 'evidenced' for h in hits)}/{len(hits)} shown in bullets"]
    if listed:
        parts.append("only listed: " + ", ".join(listed))
    if missing:
        parts.append("missing: " + ", ".join(missing))
    return ScoreDimension(key=key, label=label, method="deterministic", weight=weight,
                          points=_round(weight * share), reason="; ".join(parts))


# ---------- title ----------

def _title_tokens(title: str) -> set[str]:
    t = title.lower()
    for a, b in sorted(TITLE_SYNONYMS.items(), key=lambda kv: -len(kv[0])):
        t = re.sub(rf"(?<![a-z]){re.escape(a)}(?![a-z])", b, t)
    words = re.findall(r"[a-z0-9+#.]+", t)
    return {w for w in words if w not in SENIORITY_WORDS and w not in STOP}


def title_dimension(resume: Resume, jd: JobDescription, weight: float) -> ScoreDimension:
    dim = dict(key="title_alignment", label="Job title alignment", method="deterministic", weight=weight)
    if not jd.title:
        return ScoreDimension(**dim, points=weight, reason="JD has no title, so full points.")
    target = _title_tokens(jd.title)
    recent = [j.title for j in resume.experience[:2] if j.title]
    if not target or not recent:
        return ScoreDimension(**dim, points=0, reason="No job titles found on the resume.")
    best_title, best = max(
        ((t, len(target & _title_tokens(t)) / len(target)) for t in recent), key=lambda x: x[1])
    return ScoreDimension(**dim, points=_round(weight * best),
                          reason=f"Closest recent title '{best_title}' covers {best:.0%} of '{jd.title}'.")


# ---------- years ----------

def years_dimension(resume: Resume, jd: JobDescription, weight: float, today: date | None
                    ) -> tuple[ScoreDimension, float, int]:
    years, unreadable = total_years([(j.start, j.end) for j in resume.experience], today=today)
    dim = dict(key="years_fit", label="Years of experience fit", method="deterministic", weight=weight)
    if not jd.years_required:
        return ScoreDimension(**dim, points=weight, reason=f"JD sets no minimum; you have {years} years."), years, unreadable
    ratio = min(years / jd.years_required, 1.0)
    return (ScoreDimension(**dim, points=_round(weight * ratio),
                           reason=f"{years} years found vs {jd.years_required:g} required."),
            years, unreadable)


# ---------- ATS format ----------

def ats_dimension(resume: Resume, weight: float, unreadable_dates: int, format_flags: set[str]
                  ) -> ScoreDimension:
    checks = {
        "email and phone present": bool(resume.contact.email and resume.contact.phone),
        "work experience with readable dates": bool(resume.experience) and unreadable_dates == 0,
        "skills section present": bool(resume.skills),
        "single-column layout": "two_column_layout" not in format_flags,
        "no unplaced text": len(resume.unparsed) <= 2,
    }
    passed = sum(checks.values())
    failed = [k for k, ok in checks.items() if not ok]
    return ScoreDimension(
        key="ats_format", label="ATS format and parseability", method="deterministic",
        weight=weight, points=_round(weight * passed / len(checks)),
        reason=f"{passed}/{len(checks)} checks pass" + (f"; failing: {', '.join(failed)}" if failed else ""),
    )


def score_deterministic(resume: Resume, jd: JobDescription, weights: dict, cfg: dict,
                        format_flags: set[str] | None = None, today: date | None = None):
    w = weights["deterministic"]
    must = skill_hits(jd.must_have, resume, cfg)
    nice = skill_hits(jd.nice_to_have, resume, cfg)
    years_dim, years, unreadable = years_dimension(resume, jd, w["years_fit"], today)
    dims = [
        coverage_dimension("must_have_coverage", "Must-have skills coverage", w["must_have_coverage"], must),
        coverage_dimension("nice_to_have_coverage", "Nice-to-have skills coverage", w["nice_to_have_coverage"], nice),
        title_dimension(resume, jd, w["title_alignment"]),
        years_dim,
        ats_dimension(resume, w["ats_format"], unreadable, format_flags or set()),
    ]
    warnings = []
    if unreadable:
        warnings.append(f"{unreadable} job(s) have dates that couldn't be read; years may be undercounted.")
    return dims, must, nice, years, warnings
