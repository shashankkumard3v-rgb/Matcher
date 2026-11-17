"""The 55 deterministic points. Pure functions: same resume + JD -> same points, always."""
from __future__ import annotations

import re
from datetime import date

from app.schemas.models import JobDescription, Resume, ScoreDimension, SkillHit
from app.scoring.dates import total_years
from app.scoring.render import evidence_text, skills_text
from app.scoring.synonyms import alternatives, canonical, close_credit, close_to, count_mentions, needed_count, shown_by

SENIORITY_WORDS = {
    "senior", "sr", "junior", "jr", "lead", "principal", "staff", "associate", "intern",
    "trainee", "mid", "level", "i", "ii", "iii", "iv", "v", "head", "chief", "entry",
    "1", "2", "3", "4", "5", "l1", "l2", "l3", "l4", "l5", "grade",
}
TITLE_SYNONYMS = {
    "developer": "engineer", "dev": "engineer", "programmer": "engineer", "sde": "software engineer",
    "swe": "software engineer", "software development engineer": "software engineer",
    "software development engineering": "software engineer", "software engineering": "software engineer",
    "member of technical staff": "software engineer", "mts": "software engineer",
    "smts": "software engineer", "sdet": "software test engineer", "back end": "backend", "back-end": "backend",
    "front end": "frontend", "front-end": "frontend", "full stack": "fullstack",
    "full-stack": "fullstack", "ml": "machine learning", "ai": "machine learning",
}
STOP = {"and", "of", "the", "for", "to", "in", "a", "an", "&", "-", "/", "with"}


def _round(x: float) -> float:
    return round(x + 1e-9, 2)


# ---------- skills coverage ----------

def _option(skill: str, ev: str, listed: str, list_only: float) -> tuple[float, str, int, str | None]:
    """How well the resume shows ONE skill -> (credit, status, mentions, through which skill).
    In a bullet > shown through a skill that implies it (TypeScript shows JavaScript) > only in
    the Skills list > a close skill (JavaScript when TypeScript is asked) > missing."""
    in_ev, in_list = count_mentions(skill, ev), count_mentions(skill, listed)
    if in_ev:
        return 1.0, "evidenced", in_ev + in_list, None
    for other in shown_by(skill):
        if count_mentions(other, ev):
            return 1.0, "evidenced", count_mentions(other, ev), other
    if in_list:
        return list_only, "listed_only", in_list, None
    for other in shown_by(skill):
        if count_mentions(other, listed):
            return list_only, "listed_only", count_mentions(other, listed), other
    for other in close_to(skill):
        n = count_mentions(other, ev) + count_mentions(other, listed)
        if n:
            return close_credit() * (1.0 if count_mentions(other, ev) else list_only), "close", n, other
    return 0.0, "missing", 0, None


def skill_hits(skills: list[str], resume: Resume, cfg: dict) -> list[SkillHit]:
    """One result per JD requirement. A requirement is a single skill ("Go": only Go counts),
    or "any one of" ("Java or Go or C++"), or "N of" ("2 of Java or Go or C++"). Needing ALL of
    several skills is written as separate requirements, each scored on its own."""
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
        opts = alternatives(name)
        need = min(needed_count(name), len(opts))
        scored = sorted(((*_option(o, ev, listed, list_only), o) for o in opts), key=lambda x: -x[0])
        top = scored[:need]
        credit = sum(x[0] for x in top) / need
        mentions = max(x[2] for x in top)                  # stuffing is judged per skill
        shown = [x[4] if not x[3] else f"{x[4]} (through {x[3]})" for x in top if x[0] > 0]
        if credit >= 1.0:
            status = "evidenced"
        elif credit == 0:
            status = "missing"
        elif need == 1:
            status = top[0][1]                            # listed_only | close
        else:
            status = "partial"

        note = None
        best = top[0]
        if len(opts) == 1:
            if status == "listed_only":
                note = "Only in the Skills list; no bullet shows it being used."
                if best[3]:
                    note = f"Only {best[3]} is listed (it counts as {name}); no bullet shows it."
            elif status == "evidenced" and best[3]:
                note = f"Shown through {best[3]} (using {best[3]} shows {name})."
            elif status == "close":
                note = (f"Not on your resume; {best[3]} is close but not the same, so it earns part "
                        f"of the credit.")
        elif need == 1:
            if status == "missing":
                note = "The job accepts any ONE of these. You don't need all of them."
            else:
                note = f"Any one of these is enough; your resume shows {shown[0]}."
                if status == "listed_only":
                    note += " It's only in the Skills list."
                elif status == "close":
                    note += " That's close, not the same, so it earns part of the credit."
        else:
            have = ", ".join(shown) if shown else "none of them"
            note = f"The job wants {need} of these; your resume shows {have}."
        if mentions > max_mentions:
            credit *= stuffed_credit
            note = f"Mentioned {mentions} times (more than {max_mentions}): looks like keyword stuffing."
        if len(opts) == 1:
            matched = best[3] if best[0] > 0 else None        # the related skill that counted, if any
        elif need == 1:
            matched = (best[3] or best[4]) if best[0] > 0 else None
        else:
            matched = " + ".join(x[4] for x in top if x[0] > 0) or None
        hits.append(SkillHit(skill=name, status=status, credit=_round(credit), mentions=mentions, note=note,
                             matched=matched))
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
    # "SDE2" / "SDE-II" / "SWE3": the level stuck to the role -> "sde 2", "sde ii"
    t = re.sub(r"\b(sde|swe|sdet|mts|smts|engineer|developer)[-_ ]?(i{1,3}|iv|v|\d)\b", r"\1 \2", t)
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
