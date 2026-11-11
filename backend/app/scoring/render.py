"""Canonical plain-text rendering of a Resume.

The scorer always sees a resume through this one function, so the original upload and
every rewrite are presented in exactly the same format. Nothing here says where the
resume came from or how many times it was changed.
"""
from __future__ import annotations

from app.schemas.models import Resume


def evidence_text(resume: Resume) -> str:
    """Text that can prove a skill was USED: job bullets and project bullets/tech.

    The summary is deliberately NOT evidence: a claim in a summary isn't proof, and
    counting it would let a rewrite raise the score by stuffing the summary.
    """
    parts: list[str] = []
    for job in resume.experience:
        parts.extend(job.bullets)
    for p in resume.projects:
        if p.description:
            parts.append(p.description)
        parts.extend(p.bullets)
        parts.extend(p.tech)
    return "\n".join(parts)


def skills_text(resume: Resume) -> str:
    """Places a skill can be LISTED without proof: skills, certifications, summary."""
    return "\n".join(resume.skills + resume.certifications + resume.achievements
                     + [resume.summary or ""])


def render_resume(resume: Resume) -> str:
    c = resume.contact
    lines: list[str] = []
    if c.name:
        lines.append(c.name)
    contact = " | ".join(x for x in [c.email, c.phone, c.location, *c.links] if x)
    if contact:
        lines.append(contact)
    if resume.summary:
        lines += ["", "SUMMARY", resume.summary]
    if resume.experience:
        lines += ["", "EXPERIENCE"]
        for j in resume.experience:
            head = " - ".join(x for x in [j.title, j.company] if x)
            dates = f" ({j.start or '?'} to {j.end or '?'})" if (j.start or j.end) else ""
            lines.append(head + dates)
            lines += [f"- {b}" for b in j.bullets]
    if resume.projects:
        lines += ["", "PROJECTS"]
        for p in resume.projects:
            lines.append(p.name or "Project")
            if p.description:
                lines.append(p.description)
            lines += [f"- {b}" for b in p.bullets]
            if p.tech:
                lines.append("Tech: " + ", ".join(p.tech))
    if resume.skills:
        lines += ["", "SKILLS", ", ".join(resume.skills)]
    if resume.education:
        lines += ["", "EDUCATION"]
        for e in resume.education:
            lines.append(" - ".join(x for x in [e.degree, e.institution, e.end] if x))
    if resume.certifications:
        lines += ["", "CERTIFICATIONS", *resume.certifications]
    if resume.achievements:
        lines += ["", "ACHIEVEMENTS", *[f"- {a}" for a in resume.achievements]]
    if resume.languages:
        lines += ["", "LANGUAGES", ", ".join(resume.languages)]
    return "\n".join(lines).strip()
