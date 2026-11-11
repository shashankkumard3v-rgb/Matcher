"""Turn what you say about your work into a proposed template change.

Nothing is written until you accept the proposal. Proposals are applied against the
template as it is at accept time, so edits you made by hand in between are kept.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import date

from app import config
from app.llm.base import LLMClient, LLMError
from app.parsing.projects import (
    _slug,
    parse_project_context,
    render_profile_block,
    render_project_block,
    upsert_profile,
    upsert_project,
)
from app.schemas.models import ContextProposal, Profile, Project, ProjectContext, Resume

SKILL = "matcher-update-context"
_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def _read_template() -> str:
    from app.context import storage
    return storage.read_text("template")


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in _NUM.findall(text or "")}


def _drop_unsaid_numbers(items: list[str], allowed: set[str], warnings: list[str]) -> list[str]:
    """Truth guard: an item containing a number the user never said is removed."""
    kept = []
    for item in items:
        extra = _numbers(item) - allowed
        if extra:
            warnings.append(f"Removed \"{item}\": it has a number you didn't say ({', '.join(sorted(extra))}).")
        else:
            kept.append(item)
    return kept


def _merge_project(existing: Project | None, new: Project) -> Project:
    if existing is None:
        return new
    merged = existing.model_copy(deep=True)
    for f in ("employer", "role", "start", "end", "team_size", "problem", "notes"):
        if not getattr(merged, f) and getattr(new, f):
            setattr(merged, f, getattr(new, f))  # fill gaps, never overwrite your words
    for f in ("what_i_did", "tech", "results"):
        have = {x.lower() for x in getattr(merged, f)}
        getattr(merged, f).extend(x for x in getattr(new, f) if x.lower() not in have)
    return merged


def _merge_profile(existing: Profile, new: Profile) -> Profile:
    out = existing.model_copy(deep=True)
    for f in ("languages", "facts", "links"):
        have = {x.lower() for x in getattr(out, f)}
        getattr(out, f).extend(x for x in getattr(new, f) if x.lower() not in have)
    return out


def _find(ctx: ProjectContext, project_id: str | None, name: str | None) -> Project | None:
    for p in ctx.projects:
        if (project_id and p.id == project_id) or (name and p.id == _slug(name)):
            return p
    return None


def build_input(answer: str, question: str | None, resume: Resume, ctx: ProjectContext,
                today: date | None = None) -> str:
    jobs = [{"title": j.title, "company": j.company, "start": j.start, "end": j.end}
            for j in resume.experience]
    projects = [{"id": p.id, "name": p.name, "employer": p.employer} for p in ctx.projects]
    return (
        (f"QUESTION: {question}\n" if question else "")
        + f"ANSWER: {answer}\n"
        + f"RESUME JOBS: {json.dumps(jobs, ensure_ascii=False)}\n"
        + f"EXISTING PROJECTS: {json.dumps(projects, ensure_ascii=False)}\n"
        + f"TODAY: {(today or date.today()).isoformat()}"
    )


async def propose(llm: LLMClient, answer: str, resume: Resume, *, question: str | None = None,
                  source: str = "free_text") -> ContextProposal:
    ctx = parse_project_context(_read_template())
    data = await llm.run_skill(SKILL, build_input(answer, question, resume, ctx))
    if not isinstance(data, dict):
        raise LLMError("update-context reply was not a JSON object")
    action = data.get("action") or "none"
    warnings: list[str] = []
    prop = ContextProposal(
        id=uuid.uuid4().hex[:10], source=source, user_text=answer, action=action,
        summary=str(data.get("summary") or ""),
        missing_info=[str(x) for x in (data.get("missing_info") or [])][:3],
    )

    if action in ("add_project", "update_project") and data.get("project"):
        raw = dict(data["project"])
        # models sometimes send numbers ("team_size": 4, "start": 2023); the schema wants text
        for k in ("name", "employer", "role", "start", "end", "team_size", "problem", "notes"):
            if raw.get(k) is not None and not isinstance(raw[k], str):
                raw[k] = str(raw[k])
        raw["tech"] = [str(x) for x in raw.get("tech") or [] if x is not None]
        raw.setdefault("name", "Untitled project")
        raw["name"] = raw["name"] or "Untitled project"
        existing = _find(ctx, data.get("target_project_id"), raw.get("name"))
        allowed = _numbers(answer) | _numbers(json.dumps(existing.model_dump() if existing else {}))
        for f in ("what_i_did", "results"):
            raw[f] = _drop_unsaid_numbers([str(x) for x in raw.get(f) or []], allowed, warnings)
        if raw.get("team_size") and _numbers(str(raw["team_size"])) - allowed:
            warnings.append(f"Removed team size \"{raw['team_size']}\": you didn't say it.")
            raw["team_size"] = None
        new = Project(id=existing.id if existing else _slug(raw["name"]), **{
            k: v for k, v in raw.items() if k in Project.model_fields and k != "id"})
        merged = _merge_project(existing, new)
        prop.action = "update_project" if existing else "add_project"
        prop.project = merged
        prop.before = render_project_block(existing) if existing else ""
        prop.after = render_project_block(merged)
        issues = [i.message for i in parse_project_context(prop.after).issues]
        prop.warnings = warnings + issues
    elif action == "add_profile" and data.get("profile"):
        new = Profile(**{k: [str(x) for x in v] for k, v in data["profile"].items()
                         if k in ("languages", "facts", "links") and isinstance(v, list)})
        merged = _merge_profile(ctx.profile, new)
        prop.profile = merged
        prop.before = render_profile_block(ctx.profile)
        prop.after = render_profile_block(merged)
        prop.warnings = warnings
    else:
        prop.action = "none"
        prop.status = "rejected"  # nothing to apply
        prop.warnings = warnings
    return prop


def apply(prop: ContextProposal) -> ProjectContext:
    """Write an accepted proposal into the template (a .bak of the old file is kept)."""
    text = _read_template()
    if prop.project is not None:
        ctx = parse_project_context(text)
        current = _find(ctx, prop.project.id, prop.project.name)
        new_text = upsert_project(text, _merge_project(current, prop.project))
        delta = _only_new_project(current, prop.project)
        delta_profile = None
    elif prop.profile is not None:
        current = parse_project_context(text).profile
        new_text = upsert_profile(text, _merge_profile(current, prop.profile))
        delta, delta_profile = None, _only_new_profile(current, prop.profile)
    else:
        return parse_project_context(text)
    from app.context import storage
    storage.write_text("template", new_text)   # keeps the previous version as a backup
    _copy_to_notes(prop.model_copy(update={"project": delta, "profile": delta_profile}))
    return parse_project_context(new_text)


def _only_new_project(existing: Project | None, p: Project) -> Project:
    """The facts this proposal adds (an update proposal also carries the project's old facts)."""
    if existing is None:
        return p
    out = p.model_copy(deep=True)
    for f in ("what_i_did", "tech", "results"):
        have = {x.lower() for x in getattr(existing, f)}
        setattr(out, f, [x for x in getattr(p, f) if x.lower() not in have])
    return out


def _only_new_profile(existing: Profile, p: Profile) -> Profile:
    out = p.model_copy(deep=True)
    for f in ("languages", "facts", "links"):
        have = {x.lower() for x in getattr(existing, f)}
        setattr(out, f, [x for x in getattr(p, f) if x.lower() not in have])
    return out


def _copy_to_notes(prop: ContextProposal) -> None:
    """Also write the new facts into your notes, in the format they already use: bullets go under
    the matching "Company:" block in experience.txt, new skills go into skills.txt, profile facts
    under "Section: Achievements" and links under "Section: Links"."""
    from app.context import notes

    if prop.project is not None:
        p = prop.project
        have = {notes._key(ln) for _, ln in notes.entries(notes.read_raw("experience"))}
        bullets = [x.strip() for x in p.what_i_did if x.strip() and notes._key(x) not in have]
        # A result is only written separately when no bullet already says it; a short
        # fragment ("2M events/day") is attached to the last bullet so it keeps its context.
        for res in (x.strip() for x in p.results if x.strip()):
            if any(_key_text(res).strip() in _key_text(b) for b in bullets):
                continue
            if bullets and len(res.split()) < 6:
                bullets[-1] = bullets[-1].rstrip(". ") + f", result: {res}"
            else:
                bullets.append(res)
        if not bullets and not p.what_i_did and not p.results and p.problem:  # nothing else to say
            bullets = [f"{p.name}: {p.problem}" if p.name and p.problem else (p.problem or p.name)]
        if p.employer:
            intro = [f"Role: {p.role}"] if p.role else []
            intro.append("What I did:")
            notes.add_bullets("experience", p.employer, bullets, prefix="Company", intro=intro)
        else:
            notes.add_bullets("experience", "Personal projects", bullets, prefix="Section")
        known = _key_text(" ".join(notes.read_facts("skills")))
        where = p.employer or (p.name and f"the {p.name} project") or "a project"
        new_skills = [f"{t}: used at {where}" for t in p.tech
                      if t.strip() and _key_text(t) not in known]  # " kafka " in " ... kafka ... "
        if new_skills:
            notes.add_bullets("skills", "Other skills", new_skills, prefix="Section")
    elif prop.profile is not None:
        if prop.profile.facts:
            notes.add_bullets("experience", "Achievements", prop.profile.facts, prefix="Section")
        if prop.profile.links:
            notes.add_bullets("experience", "Links", prop.profile.links, prefix="Section")


def _key_text(text: str) -> str:
    import re
    return " " + re.sub(r"[^a-z0-9+#.]+", " ", text.lower()).strip() + " "
