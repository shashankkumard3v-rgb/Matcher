"""Turn extracted text into Resume / JobDescription using the LLM, then verify.

Verification is deterministic: every bullet the LLM returns must be found in the
source text (after normalizing spaces/punctuation). Bullets that aren't are flagged,
so an invented or reworded bullet never slips through silently.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from pydantic import ValidationError

from app.llm.base import LLMClient, LLMError
from app.schemas.models import JobDescription, ParseWarning, Resume


def _norm(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9%+#.]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def found_in_source(snippet: str, source_norm: str, threshold: float = 0.9) -> bool:
    """True if snippet appears in the source (exactly, or as a near match)."""
    n = _norm(snippet)
    if not n:
        return True
    if n in source_norm:
        return True
    # PDF line-wrapping can split words oddly; fall back to a fuzzy window match.
    words = n.split()
    first = words[0]
    best = 0.0
    for m in re.finditer(re.escape(first), source_norm):
        window = source_norm[m.start() : m.start() + len(n) + 20]
        best = max(best, SequenceMatcher(None, n, window[: len(n)]).ratio())
        if best >= threshold:
            return True
    return False


def verify_resume(resume: Resume, source_text: str) -> list[ParseWarning]:
    src = _norm(source_text)
    warnings: list[ParseWarning] = []
    for job in resume.experience:
        for b in job.bullets:
            if not found_in_source(b, src):
                warnings.append(ParseWarning(
                    source="resume", code="unverified_bullet",
                    message=f"Bullet under '{job.company}' not found in the PDF text: {b[:90]}",
                ))
    for fld, value in (("company", [j.company for j in resume.experience]),
                       ("title", [j.title for j in resume.experience])):
        for v in value:
            if v and not found_in_source(v, src):
                warnings.append(ParseWarning(
                    source="resume", code="unverified_field",
                    message=f"Experience {fld} '{v}' not found in the PDF text.",
                ))
    if not resume.experience:
        warnings.append(ParseWarning(
            source="resume", code="no_experience",
            message="No work experience was found in the resume.",
        ))
    return warnings


def verify_jd(jd: JobDescription) -> list[ParseWarning]:
    w: list[ParseWarning] = []
    if not jd.must_have:
        w.append(ParseWarning(source="jd", code="no_must_have",
                              message="No required skills were found in the JD."))
    if not jd.title:
        w.append(ParseWarning(source="jd", code="no_title",
                              message="No job title was found in the JD."))
    return w


async def structure_resume(llm: LLMClient, text: str) -> tuple[Resume, list[ParseWarning]]:
    data = await llm.run_skill("matcher-parse-resume", f"Resume text:\n<<<\n{text}\n>>>")
    try:
        resume = Resume.model_validate(data)
    except ValidationError as e:
        raise LLMError(f"Resume JSON did not match the schema: {e}") from e
    return resume, verify_resume(resume, text)


async def structure_jd(llm: LLMClient, text: str) -> tuple[JobDescription, list[ParseWarning]]:
    from app.library.profile import capability_vocab
    _, vocab = capability_vocab()
    caps = "\n".join(f"{k}: {v}" for k, v in vocab.items())
    data = await llm.run_skill("matcher-parse-jd",
                               f"Job description text:\n<<<\n{text}\n>>>\n\nCAPABILITIES:\n{caps}")
    try:
        jd = JobDescription.model_validate(data)
    except ValidationError as e:
        raise LLMError(f"JD JSON did not match the schema: {e}") from e
    jd.capabilities = list(dict.fromkeys(c for c in jd.capabilities if c in vocab))[:8]  # only known ids
    return jd, verify_jd(jd)
