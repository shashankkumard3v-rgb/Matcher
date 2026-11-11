"""Deterministic parser for context/project_context.md (no LLM).

Format (see templates/project_context_template.md):

    ## Project: <name>
    - Employer: ...
    - What I did:
      - item
      - item
"""
from __future__ import annotations

import re

from app.schemas.models import Profile, Project, ProjectContext, ProjectIssue

SCALAR_FIELDS = {
    "employer": "employer",
    "role": "role",
    "start": "start",
    "end": "end",
    "team size": "team_size",
    "problem": "problem",
    "notes": "notes",
}
LIST_FIELDS = {
    "what i did": "what_i_did",
    "tech": "tech",
    "results": "results",
}
REQUIRED = ["employer", "role", "start", "end"]
DATE_RE = re.compile(r"^(\d{4}-\d{2}|present)$", re.IGNORECASE)

_PROJECT_HDR = re.compile(r"^##\s*Project:\s*(.+?)\s*$", re.IGNORECASE)
_PROFILE_HDR = re.compile(r"^##\s*Profile\s*$", re.IGNORECASE)
_ANY_H2 = re.compile(r"^##\s")
PROFILE_LISTS = {"languages": "languages", "other facts": "facts", "links": "links"}
_FIELD = re.compile(r"^-\s*([A-Za-z ]+?)\s*:\s*(.*)$")
_SUBITEM = re.compile(r"^\s{2,}[-*]\s+(.*)$")


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:48] or "project"


def _strip_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)


def parse_project_context(text: str) -> ProjectContext:
    text = _strip_comments(text)
    projects: list[Project] = []
    profile = Profile()
    current: dict | None = None
    current_list: str | None = None
    in_profile = False
    seen_ids: dict[str, int] = {}

    def flush():
        if current is None:
            return
        if current["name"].lower().startswith("example"):
            return  # the template's sample block is never real data
        base = _slug(current["name"])
        seen_ids[base] = seen_ids.get(base, 0) + 1
        pid = base if seen_ids[base] == 1 else f"{base}-{seen_ids[base]}"
        projects.append(Project(id=pid, **current))

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        m = _PROJECT_HDR.match(line)
        if m:
            flush()
            current = {"name": m.group(1)}
            current_list = None
            in_profile = False
            continue
        if _PROFILE_HDR.match(line):
            flush()
            current, current_list, in_profile = None, None, True
            continue
        if _ANY_H2.match(line):
            flush()
            current, current_list, in_profile = None, None, False
            continue
        if in_profile:
            sub = _SUBITEM.match(line)
            if sub and current_list:
                if sub.group(1).strip():
                    getattr(profile, current_list).append(sub.group(1).strip())
                continue
            f = _FIELD.match(line.strip())
            if f:
                current_list = PROFILE_LISTS.get(f.group(1).strip().lower())
                if current_list and f.group(2).strip():
                    getattr(profile, current_list).extend(
                        v.strip() for v in f.group(2).split(",") if v.strip())
            continue
        if current is None:
            continue
        sub = _SUBITEM.match(line)
        if sub and current_list:
            item = sub.group(1).strip()
            if item:
                current.setdefault(current_list, []).append(item)
            continue
        f = _FIELD.match(line.strip())
        if f:
            key = f.group(1).strip().lower()
            value = f.group(2).strip()
            if key in LIST_FIELDS:
                current_list = LIST_FIELDS[key]
                current.setdefault(current_list, [])
                if value:  # allow comma-separated on one line too
                    current[current_list].extend(
                        v.strip() for v in value.split(",") if v.strip()
                    )
            elif key in SCALAR_FIELDS:
                current_list = None
                if value:
                    current[SCALAR_FIELDS[key]] = value
            else:
                current_list = None
    flush()

    return ProjectContext(projects=projects, profile=profile, issues=validate_projects(projects))


def validate_projects(projects: list[Project]) -> list[ProjectIssue]:
    issues: list[ProjectIssue] = []
    for p in projects:
        for fld in REQUIRED:
            if not getattr(p, fld):
                issues.append(ProjectIssue(
                    project_id=p.id, field=fld,
                    message=f"'{p.name}' is missing {fld}.",
                ))
        for fld in ("start", "end"):
            v = getattr(p, fld)
            if v and not DATE_RE.match(v):
                issues.append(ProjectIssue(
                    project_id=p.id, field=fld,
                    message=f"'{p.name}' {fld} '{v}' should be YYYY-MM or 'present'.",
                ))
        if not p.what_i_did:
            issues.append(ProjectIssue(
                project_id=p.id, field="what_i_did",
                message=f"'{p.name}' has nothing under 'What I did', so no bullets can be written from it.",
            ))
    return issues


# ---------- writing (used when you accept a proposed change) ----------

def render_project_block(p: Project) -> str:
    lines = [f"## Project: {p.name}"]
    for label, attr in (("Employer", "employer"), ("Role", "role"), ("Start", "start"),
                        ("End", "end"), ("Team size", "team_size"), ("Problem", "problem")):
        lines.append(f"- {label}: {getattr(p, attr) or ''}".rstrip())
    for label, attr in (("What I did", "what_i_did"), ("Tech", "tech"), ("Results", "results")):
        lines.append(f"- {label}:")
        lines += [f"  - {item}" for item in getattr(p, attr)]
    if p.notes:
        lines.append(f"- Notes: {p.notes}")
    return "\n".join(lines)


def render_profile_block(profile: Profile) -> str:
    lines = ["## Profile", "- Languages:"]
    lines += [f"  - {x}" for x in profile.languages]
    lines.append("- Other facts:")
    lines += [f"  - {x}" for x in profile.facts]
    lines.append("- Links:")
    lines += [f"  - {x}" for x in profile.links]
    return "\n".join(lines)


def _block_span(text: str, header: re.Pattern, name: str | None = None) -> tuple[int, int] | None:
    """Character span of a '## ...' block (outside comments) whose header matches."""
    lines = text.splitlines(keepends=True)
    pos, start, in_comment = 0, None, False
    for line in lines:
        stripped = line.strip()
        if "<!--" in stripped and "-->" not in stripped:
            in_comment = True
        if not in_comment:
            if start is not None and _ANY_H2.match(stripped):
                return start, pos
            m = header.match(stripped)
            if m and start is None and (name is None or _slug(m.group(1)) == _slug(name)):
                start = pos
        if "-->" in stripped:
            in_comment = False
        pos += len(line)
    return (start, len(text)) if start is not None else None


def upsert_project(text: str, project: Project) -> str:
    """Replace the project's block if it exists (matched by name), else append it."""
    block = render_project_block(project) + "\n\n"
    span = _block_span(text, _PROJECT_HDR, project.name)
    if span:
        return text[: span[0]] + block + text[span[1]:].lstrip("\n")
    return text.rstrip() + "\n\n" + block


def upsert_profile(text: str, profile: Profile) -> str:
    block = render_profile_block(profile) + "\n\n"
    span = _block_span(text, _PROFILE_HDR)
    if span:
        return text[: span[0]] + block + text[span[1]:].lstrip("\n")
    return text.rstrip() + "\n\n" + block
