"""Truth guard for suggested edits.

1) Code checks (hard): every source quote must exist; numbers and known skills in the
   new text must appear in its sources. Failing edits are BLOCKED; you can still edit
   them yourself, and then they count as your own words.
2) AI check (soft): the matcher-truth-check skill flags inflation ("helped" -> "led").
   Flagged edits stay reviewable, with the note shown to you.
"""
from __future__ import annotations

import json
import re

from app.llm.base import LLMClient, LLMError
from app.parsing.structure import _norm, found_in_source
from app.schemas.models import Change, JobDescription
from app.scoring.synonyms import count_mentions, synonym_table

SKILL = "matcher-truth-check"
_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in _NUM.findall(text or "")}


def _known_skills(jd: JobDescription) -> list[str]:
    canon = sorted(set(synonym_table()[1].values()))
    return sorted(set(canon) | set(jd.must_have) | set(jd.nice_to_have))


def check_code(change: Change, resume_texts: list[str], tpl: dict[str, str], jd: JobDescription,
               user_texts: list[str] | None = None) -> None:
    """Verify sources and provenance. Mutates change.sources/.truth_ok/.truth_issues/.status."""
    issues: list[str] = []
    resume_norm = [_norm(t) for t in resume_texts]
    user_norm = [_norm(t) for t in (user_texts or [])]
    tpl_norm = {k: _norm(v) for k, v in tpl.items()}
    for s in change.sources:
        if s.type == "resume":
            s.verified = any(found_in_source(s.quote, t) for t in resume_norm)
        elif s.type in ("template", "notes"):
            pid = s.project_id
            if s.type == "notes" and pid and not pid.startswith("notes-"):
                pid = f"notes-{pid}"
            pool = [tpl_norm[pid]] if pid in tpl_norm else list(tpl_norm.values())
            s.verified = any(found_in_source(s.quote, t) for t in pool)
        elif s.type == "you":
            s.verified = any(found_in_source(s.quote, t) for t in user_norm)
        if not s.verified:
            issues.append(f"Source quote not found: \"{s.quote[:80]}\"")

    if change.kind != "remove_bullet" and change.new:
        support = "\n".join([s.quote for s in change.sources if s.verified]
                            + [change.old or "", change.old2 or ""])
        if not any(s.verified for s in change.sources):
            issues.append("No verified source for this edit.")
        extra = _numbers(change.new) - _numbers(support)
        if extra:
            issues.append(f"Numbers not in any source: {', '.join(sorted(extra))}.")
        for skill in _known_skills(jd):
            if count_mentions(skill, change.new) and not count_mentions(skill, support):
                issues.append(f"Mentions {skill}, which its sources don't.")

    change.truth_issues = issues
    change.truth_ok = not issues
    if issues and change.status == "pending":
        change.status = "blocked"


async def check_ai(llm: LLMClient, changes: list[Change]) -> None:
    """Soft check for inflation. Adds notes; doesn't block."""
    todo = [c for c in changes if c.kind != "remove_bullet" and c.new and c.status == "pending"]
    if not todo:
        return
    payload = [{"id": c.id, "new": c.new, "old": c.old,
                "sources": [s.quote for s in c.sources if s.verified]} for c in todo]
    try:
        data = await llm.run_skill(SKILL, "EDITS:\n" + json.dumps(payload, ensure_ascii=False, indent=1))
    except LLMError as e:
        for c in todo:
            c.truth_issues.append(f"AI truth check unavailable ({e}); review this one carefully.")
        return
    by_id = {r.get("id"): r for r in data.get("results", []) if isinstance(r, dict)}
    for c in todo:
        r = by_id.get(c.id)
        if r is None:
            c.truth_issues.append("AI truth check gave no verdict; review this one carefully.")
        elif not r.get("ok", False):
            c.truth_ok = False
            c.truth_issues += [f"Possible overstatement: {i}" for i in (r.get("issues") or [])] or [
                "Flagged as possibly unsupported."]
