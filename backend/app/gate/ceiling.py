"""Build the 'best possible' resume: your resume + the best facts from your notes and template
that fit on one page. It is never allowed to lose what your resume already proves.

Template facts are added word for word (no rewriting), so the ceiling is what the
blind scorer gives when nothing you know is left out. Real rewrites in phase 4 can
only use these same facts, so they can't honestly go higher than this - except for
better wording, which is why the ceiling is an estimate, not a cap.
"""
from __future__ import annotations

import re

from app.parsing.structure import _norm
from app.schemas.models import JobDescription, ProjectContext, Resume, ResumeProject


def _same_company(a: str | None, b: str | None) -> bool:
    from app.parsing.structure import company_matches
    return company_matches(a, b)


_LABEL = re.compile(r"^(role|dates?|location|team|product)\s*:", re.IGNORECASE)
# skills-notes entries ("Python | Finlytics | APIs | strong", "Python: used at X ... Level: Strong")
# describe a skill, they aren't resume bullets
_SKILL_ENTRY = re.compile(r"\||\blevel\s*:|^[^:]{1,40}:\s*used\b", re.IGNORECASE)
_TOKEN = re.compile(r"[a-z0-9][a-z0-9+#.]*")


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN.findall(_norm(text)) if len(t) > 2}


def _note_is_for(company: str, head: str | None, line: str) -> bool:
    """A notes line belongs to a job when it sits under that company's heading or names it."""
    from app.parsing.structure import company_matches, mentions_company
    if head and head != line:
        h = re.sub(r"^\s*(company|employer|section)\s*[:\-]\s*", "", head, flags=re.IGNORECASE)
        if company_matches(company, h):
            return True
    return mentions_company(company, line)


def near_duplicate(a: str, b: str, threshold: float = 0.6) -> bool:
    """Two bullets saying the same thing (most words of the shorter one are in the other)."""
    x, y = _tokens(a), _tokens(b)
    if not x or not y:
        return _norm(a) == _norm(b)
    return len(x & y) / min(len(x), len(y)) >= threshold


def _dedupe(cands: list[str], jd: JobDescription | None) -> list[str]:
    """Keeps the stronger of two near-duplicates (earlier one wins a tie: your resume comes first)."""
    from app.loop.bullets import value_of
    out: list[str] = []
    for c in cands:
        twin = next((i for i, o in enumerate(out) if near_duplicate(c, o)), None)
        if twin is None:
            out.append(c)
        elif jd is not None and value_of(c, jd)[0] > value_of(out[twin], jd)[0]:
            out[twin] = c
    return out


def _jd_skills(jd: JobDescription) -> list[str]:
    from app.scoring.synonyms import canonical
    seen, out = set(), []
    for s in [*jd.must_have, *jd.nice_to_have]:
        k = canonical(s).lower()
        if k not in seen:
            seen.add(k)
            out.append(s)
    return out


def _over_limit(r: Resume, jd: JobDescription, max_mentions: int) -> set[str]:
    """JD skills mentioned more often than the scorer allows (keyword stuffing)."""
    from app.scoring.render import evidence_text, skills_text
    from app.scoring.synonyms import count_mentions
    from app.scoring.synonyms import alternatives
    ev, listed = evidence_text(r), skills_text(r)
    # "Java or Go or C++": each option is judged on its own (Java 3x + Go 2x isn't stuffing)
    return {s for s in _jd_skills(jd)
            if any(count_mentions(a, ev) + count_mentions(a, listed) > max_mentions for a in alternatives(s))}


def _select(r: Resume, pools: list[list[str]], caps: list[int], jd: JobDescription,
            max_mentions: int) -> None:
    """Fills each job with at most `cap` bullets:
    1. every JD skill (must-haves first) that some bullet shows gets its best bullet,
    2. then the most valuable bullets for this JD,
    never adding a bullet that would push a skill into keyword stuffing."""
    from app.loop.bullets import value_of
    from app.scoring.synonyms import count_mentions
    chosen: list[list[str]] = [[] for _ in pools]
    value = {b: value_of(b, jd)[0] for pool in pools for b in pool}

    def apply() -> None:
        for job, picked, pool in zip(r.experience, chosen, pools):
            job.bullets = [b for b in pool if b in picked]   # keep the pool's order (resume first)

    def try_add(i: int, b: str) -> bool:
        if b in chosen[i] or len(chosen[i]) >= caps[i]:
            return False
        before = _over_limit(r, jd, max_mentions)
        chosen[i].append(b)
        apply()
        if _over_limit(r, jd, max_mentions) - before:
            chosen[i].remove(b)
            apply()
            return False
        return True

    apply()
    # what this job asks for, most important first: must-have skills, duties, nice-to-have skills
    from app.scoring.synonyms import canonical
    duties = {b: set(value_of(b, jd)[1]["duties"]) for pool in pools for b in pool}
    must = {canonical(x).lower() for x in jd.must_have}
    wants = ([("skill", x) for x in _jd_skills(jd) if canonical(x).lower() in must]
             + [("duty", d) for d in jd.responsibilities]
             + [("skill", x) for x in _jd_skills(jd) if canonical(x).lower() not in must])

    def shows(kind: str, want: str, b: str) -> bool:
        return bool(count_mentions(want, b)) if kind == "skill" else want in duties[b]

    for kind, want in wants:
        if any(shows(kind, want, b) for picked in chosen for b in picked):
            continue
        options = sorted(((value[b], -i, i, b) for i, pool in enumerate(pools) for b in pool
                          if shows(kind, want, b)), reverse=True)
        for _, _, i, b in options:
            if try_add(i, b):
                break
    for i, pool in enumerate(pools):
        for b in sorted(pool, key=lambda x: -value[x]):
            if len(chosen[i]) >= caps[i]:
                break
            try_add(i, b)
    apply()


def build_ceiling_resume(resume: Resume, ctx: ProjectContext, jd: JobDescription | None = None,
                         cfg: dict | None = None) -> Resume:
    """Your resume + the facts from your notes and template, as one honest page.

    With a JD, each job keeps at most its one-page number of bullets (cfg max_bullets_per_job),
    chosen so nothing your resume already proves is lost (a bullet that is the only proof of a JD
    skill is always kept), near-duplicates count once and no skill gets stuffed."""
    from app.context import notes
    r = resume.model_copy(deep=True)
    pools: list[list[str]] = [list(j.bullets) for j in r.experience]
    leftover: list[str] = []

    for p in ctx.projects:
        lines = list(p.what_i_did) + list(p.results)
        i = next((k for k, j in enumerate(r.experience) if _same_company(j.company, p.employer)), None)
        if i is not None:
            pools[i] += lines
        else:  # employer not on the resume: still counts as shown work, as a project
            r.projects.append(ResumeProject(
                name=p.name, description=p.problem, bullets=list(p.what_i_did) + list(p.results),
                tech=list(p.tech)))

    # Experience notes: a line under one of your companies goes to that job; the rest becomes a
    # "From your notes" project. Words are kept as written (no rewriting here).
    for head, line in notes.entries(notes.read_raw("experience")):
        if head == line or _LABEL.match(line) or _SKILL_ENTRY.search(line):
            continue
        where = f"{head} | {line}" if head else line
        i = next((k for k, j in enumerate(r.experience) if j.company and _note_is_for(j.company, head, line)), None)
        if i is not None:
            pools[i].append(line)
        else:
            leftover.append(line)

    # Skills notes describe skills: a JD skill you wrote about goes into the Skills list (it is
    # still only "listed" until a bullet shows it), never in as a fake bullet.
    if jd is not None:
        from app.scoring.synonyms import canonical, count_mentions
        skills_raw = notes.read_raw("skills")
        from app.scoring.render import evidence_text, skills_text
        have = {canonical(x).lower() for x in r.skills}
        shown = evidence_text(r) + "\n" + skills_text(r)
        from app.scoring.synonyms import alternatives
        lines = [line for h, line in notes.entries(skills_raw) if h != line]
        for s in _jd_skills(jd):
            if count_mentions(s, shown):
                continue
            # "Java or Go or C++": add only the option your notes actually mention (one is enough)
            for opt in alternatives(s):
                if canonical(opt).lower() not in have and any(count_mentions(opt, line) for line in lines):
                    r.skills.append(canonical(opt))
                    have.add(canonical(opt).lower())
                    break

    pools = [_dedupe(pool, jd) for pool in pools]
    seen = [b for pool in pools for b in pool]
    leftover = [b for b in _dedupe(leftover, jd) if not any(near_duplicate(b, x) for x in seen)]
    for job, pool in zip(r.experience, pools):
        job.bullets = list(pool)

    if jd is not None:
        from app.loop.bullets import caps_for, value_of
        from app.config import weights_config
        max_mentions = int(weights_config().get("scoring", {}).get("stuffing_max_mentions", 4))
        _select(r, pools, caps_for(len(r.experience), cfg or {}), jd, max_mentions)
        kept = []
        for b in sorted(leftover, key=lambda x: -value_of(x, jd)[0]):
            if len(kept) >= 4:
                break
            before = _over_limit(r, jd, max_mentions)
            r.projects.append(ResumeProject(name="From your notes", bullets=kept + [b]))
            if not (_over_limit(r, jd, max_mentions) - before):
                kept.append(b)
            r.projects.pop()
        leftover = kept
    if leftover:
        r.projects.append(ResumeProject(name="From your notes", bullets=leftover))

    have_lang = {_norm(x) for x in r.languages}
    r.languages += [x for x in ctx.profile.languages if _norm(x) not in have_lang]
    have_cert = {_norm(x) for x in r.certifications}
    r.certifications += [x for x in ctx.profile.facts if _norm(x) not in have_cert]
    return r
