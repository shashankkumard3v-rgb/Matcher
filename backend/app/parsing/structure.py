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


# ---------- company names ----------
# The same employer gets written many ways: "Finlytics Pvt Ltd" / "Finlytics Private Limited" /
# "Finlytics", "Gruve AI" / "Gruve.ai" / "Gruve Inc.", "Amazon Web Services" / "AWS", and
# "FinlyticsPvtLtd" when a PDF lost its spaces. All of these must count as one company, or your
# notes for a job are silently ignored.
_LEGAL = {"pvt", "private", "ltd", "limited", "inc", "incorporated", "llc", "llp", "plc", "corp",
          "corporation", "co", "company", "gmbh", "ag", "sa", "bv", "pte", "pty", "the", "and"}
_LEGAL_TAIL = ("corporation", "incorporated", "limited", "private", "gmbh", "corp", "ltd", "pvt", "llc",
               "llp", "inc", "plc", "pte", "pty")


def _company_tokens(name: str) -> list[str]:
    t = re.sub(r"[^a-z0-9]+", " ", (name or "").lower().replace("&", " and "))
    return [x for x in t.split() if x not in _LEGAL]


def company_key(name: str | None) -> str:
    """'Finlytics Pvt. Ltd.' -> 'finlytics'; 'Gruve.ai' and 'Gruve AI' -> 'gruveai'."""
    toks = _company_tokens(name or "")
    key = "".join(toks)
    if len(toks) == 1:                       # words joined together: "finlyticspvtltd"
        changed = True
        while changed:
            changed = False
            for suf in _LEGAL_TAIL:
                if key.endswith(suf) and len(key) - len(suf) >= 3:
                    key, changed = key[: -len(suf)], True
    return key


def company_matches(a: str | None, b: str | None) -> bool:
    """True when two names are the same employer written differently."""
    ka, kb = company_key(a), company_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    ta, tb = _company_tokens(a or ""), _company_tokens(b or "")
    short_t, long_t = sorted((ta, tb), key=len)
    if short_t and long_t[: len(short_t)] == short_t and len("".join(short_t)) >= 3:
        return True                                         # whole words: "Finlytics" / "Finlytics Payments"
    short = min((ka, kb), key=len)
    for x, y in ((a, kb), (b, ka)):                           # "Amazon Web Services" / "AWS"
        toks = _company_tokens(x or "")
        if len(toks) >= 2 and "".join(t[0] for t in toks) == y:
            return True
    import difflib
    return len(short) >= 5 and difflib.SequenceMatcher(None, ka, kb).ratio() >= 0.88   # a typo


def mentions_company(company: str | None, text: str) -> bool:
    """Whether a notes line names this employer ("Built X at Finlytics" for "Finlytics Pvt Ltd")."""
    key = company_key(company)
    if len(key) < 3:
        return False
    compact = re.sub(r"[^a-z0-9]+", "", (text or "").lower())
    if len(key) >= 5:
        return key in compact
    return bool(re.search(r"(?<![a-z0-9])" + re.escape(key) + r"(?![a-z0-9])", (text or "").lower()))


_ELLIPSIS = re.compile(r"\.{3,}|\u2026|\[\s*\.*\s*\]")


def _close_wording(words: list[str], src_words: list[str], need: float = 0.8) -> bool:
    """The quote's words appear in one place of the source, nearly all and in order (a quote
    with "the" added or "using" for "with" still counts; numbers must all be there)."""
    if len(words) < 5:
        return False
    nums = {w for w in words if any(c.isdigit() for c in w)}
    starts = set(words[:3])
    span = int(len(words) * 1.4) + 2
    for i, w in enumerate(src_words):
        if w not in starts:
            continue
        window = src_words[i:i + span]
        if not nums <= set(window):
            continue
        hit = sum(b.size for b in SequenceMatcher(None, words, window, autojunk=False).get_matching_blocks())
        if hit / len(words) >= need:
            return True
    return False


def found_in_source(snippet: str, source_norm: str, threshold: float = 0.9, loose: bool = False) -> bool:
    """True if snippet appears in the source (exactly, or as a near match).
    Also true when the source lost its spaces ("Designedandbuilt...") and for a quote shortened
    with "..." when every piece is found. loose=True also accepts a lightly reworded quote."""
    parts = [p for p in _ELLIPSIS.split(snippet or "") if _norm(p)]
    if len(parts) > 1:
        return all(found_in_source(p, source_norm, threshold, loose) for p in parts)
    n = _norm(snippet)
    if not n:
        return True
    if n in source_norm:
        return True
    tight = n.replace(" ", "")
    if len(tight) >= 12 and tight in source_norm.replace(" ", ""):
        return True
    # Near matches must not change a number ("25 microservices" when the resume says 14).
    if not set(re.findall(r"\d+", n)) <= set(re.findall(r"\d+", source_norm)):
        return False
    # PDF line-wrapping can split words oddly; fall back to a fuzzy window match.
    words = n.split()
    first = words[0]
    best = 0.0
    for m in re.finditer(re.escape(first), source_norm):
        window = source_norm[m.start() : m.start() + len(n) + 20]
        best = max(best, SequenceMatcher(None, n, window[: len(n)]).ratio())
        if best >= threshold:
            return True
    return loose and _close_wording(words, source_norm.split())


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
    jd.must_have = normalize_one_of(jd.must_have)
    jd.nice_to_have = normalize_one_of(jd.nice_to_have)
    return jd, verify_jd(jd)


def normalize_one_of(skills: list[str]) -> list[str]:
    """Writes every "any one of" requirement the same way ("Java or Go or C++") so scoring
    counts it as met by any one option. "Java/Go/C++" (slashes) counts too when every part is a
    known skill and the whole isn't one name itself (CI/CD, TCP/IP stay as they are)."""
    from app.scoring.synonyms import alternatives, canonical, is_language, needed_count, synonym_table
    known = synonym_table()[1]
    out = []
    for raw in skills:
        s = (raw or "").strip()
        parts = [p for p in s.split("/") if p]
        if len(parts) > 1 and " " not in s and _norm(s) not in known and all(_norm(p) in known for p in parts):
            if all(is_language(p) for p in parts):
                s = " or ".join(parts)               # "Java/Go/C++": any one language
            else:
                out += parts                         # "Node.js/TypeScript": both
                continue
        need = needed_count(s)
        alts = alternatives(s)
        if len(alts) > 1:
            out.append((f"{need} of " if need > 1 else "") + " or ".join(alts))
        elif "," in s and not re.search(r"\bor\b", s, re.IGNORECASE):
            out += [p.strip() for p in s.split(",") if p.strip()]     # "Python, SQL": both required
        else:
            out.append(alts[0])
    seen, unique = set(), []
    for s in out:
        k = canonical(s).lower()
        if s and k not in seen:
            seen.add(k)
            unique.append(s)
    return unique
