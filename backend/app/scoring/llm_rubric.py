"""The 45 judgment points, graded by the matcher-score skill.

The model only ever receives: the JD (as JSON) and the resume (canonical text).
Every level above 0 must be backed by a quote that really exists in the resume.
"""
from __future__ import annotations

import json
import re
import statistics

from app.llm.base import LLMClient, LLMError
from app.parsing.structure import _norm, found_in_source
from app.schemas.models import JobDescription, ScoreDimension

SKILL = "matcher-score"
LEVELS = (0, 25, 50, 75, 100)
LABELS = {
    "experience_relevance": "Experience relevance and recency",
    "impact_quantification": "Impact and quantification",
    "seniority_scope": "Seniority and scope match",
    "domain_fit": "Domain and industry fit",
}


def build_input(jd: JobDescription, resume_text: str) -> str:
    """The judge's entire view of the world. Identical format for every resume."""
    jd_json = json.dumps(jd.model_dump(), sort_keys=True, ensure_ascii=False, indent=1)
    return f"JOB DESCRIPTION:\n{jd_json}\n\nRESUME:\n<<<\n{resume_text}\n>>>"


_WORD_LEVELS = (
    (("exceed", "excellent", "outstanding", "full", "perfect", "very strong"), 100),
    (("strong", "good", "high", "meets", "clear"), 75),
    (("partial", "moderate", "medium", "some", "average"), 50),
    (("weak", "little", "low", "slight", "limited"), 25),
)


def _snap(level) -> int:
    """75, "75", "75%", "75/100", "Level 75" -> 75."""
    try:
        v = float(level)
    except (TypeError, ValueError):
        m = re.search(r"\d+(?:\.\d+)?", str(level or ""))
        if m:
            v = float(m.group())
        else:   # a model that answers in words ("exceeds expectations") instead of a number
            w = str(level or "").lower()
            v = next((lv for words, lv in _WORD_LEVELS if any(x in w for x in words)), 0)
    return min(LEVELS, key=lambda lv: (abs(lv - v), lv))  # ties go to the lower level


def parse_reply(data: dict, resume_text: str) -> dict[str, dict]:
    """-> {key: {"level", "reason", "evidence"}} with only verified quotes kept."""
    src = _norm(resume_text)
    out: dict[str, dict] = {}
    for d in data.get("dimensions", []) if isinstance(data, dict) else []:
        key = d.get("key")
        if key not in LABELS or key in out:
            continue
        quotes = [q for q in (d.get("evidence") or []) if isinstance(q, str) and q.strip()]
        valid = [q for q in quotes if found_in_source(q, src, loose=True)]
        level = _snap(d.get("level"))
        reason = str(d.get("reason") or "").strip()
        if level > 0 and not valid:
            reason = (reason + " " if reason else "") + "[Scored 0: no evidence quote matched the resume.]"
            level = 0
        out[key] = {"level": level, "reason": reason, "evidence": valid[:3],
                    "dropped": len(quotes) - len(valid)}
    missing = set(LABELS) - set(out)
    if missing:
        raise LLMError(f"Score reply is missing dimensions: {sorted(missing)}")
    return out


async def score_llm(llm: LLMClient, jd: JobDescription, resume_text: str, weights: dict,
                    samples: int = 1, model: str | None = None
                    ) -> tuple[list[ScoreDimension], str | None, list[str]]:
    """Returns (dimensions, model used, warnings)."""
    user_input = build_input(jd, resume_text)
    runs: list[dict[str, dict]] = []
    for _ in range(max(1, samples)):
        data = await llm.run_skill(SKILL, user_input, model=model)
        model = model or llm.last_model  # every later sample uses the same model
        runs.append(parse_reply(data, resume_text))

    dims, warnings = [], []
    for key, label in LABELS.items():
        levels = sorted(r[key]["level"] for r in runs)
        level = _snap(statistics.median_low(levels))
        chosen = next(r[key] for r in runs if r[key]["level"] == level)
        if len(set(levels)) > 1:
            warnings.append(f"{label}: samples disagreed {levels}, used {level}.")
        if chosen["dropped"]:
            warnings.append(f"{label}: {chosen['dropped']} evidence quote(s) not found in the resume were ignored.")
        weight = float(weights["llm"][key])
        dims.append(ScoreDimension(
            key=key, label=label, method="llm", weight=weight, level=level,
            points=round(weight * level / 100, 2), reason=chosen["reason"],
            evidence=chosen["evidence"],
        ))
    return dims, model, warnings
