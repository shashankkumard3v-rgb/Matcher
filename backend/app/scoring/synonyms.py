"""Whole-word skill matching with a synonym map (config/skills_synonyms.yaml)."""
from __future__ import annotations

import re
from functools import lru_cache

import yaml

from app import config


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower().replace("-", " ")).strip()


@lru_cache
def _data() -> dict:
    path = config.CONFIG_DIR / "skills_synonyms.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


@lru_cache
def synonym_table() -> tuple[int, dict[str, str]]:
    """Returns (version, alias -> canonical) including each canonical name itself."""
    data = _data()
    table: dict[str, str] = {}
    for canonical, aliases in (data.get("synonyms") or {}).items():
        for a in [canonical, *(aliases or [])]:
            table[_norm(str(a))] = canonical
    for canonical, aliases in (_relations().get("aliases") or {}).items():   # skill_relations.yaml
        for a in aliases or []:
            table.setdefault(_norm(str(a)), canonical)
    return int(data.get("version", 0)), table


@lru_cache
def case_sensitive_words() -> dict[str, str]:
    """lowercase -> exact spelling for words that are also everyday English."""
    words = [*(_data().get("case_sensitive") or []), *(_relations().get("exact") or [])]
    return {str(w).lower(): str(w) for w in words}


# ---------- "one of" requirements: "Java or Go or C++" ----------
# A JD often accepts ANY ONE of several skills. The parser writes such a requirement as one item
# joined with " or " ("Java or Go or C++"); it is met when the resume shows any one of them.
# Vague parts ("or similar", "or equivalent") add nothing checkable and are dropped.
_ALT_SPLIT = re.compile(r"\s*,\s*(?:or\s+)?|\s+or\s+|\s+/\s+", re.IGNORECASE)
_VAGUE = re.compile(r"^(similar|equivalent|comparable|related|other|others|alike|the like|"
                    r"(a |an |any |another |other |some )?(similar|equivalent|comparable|related|other|modern)\b.*)$")


_FILLER = re.compile(r"^(?:(?:hands[- ]on |strong |solid |good |deep |working |professional )?(?:experience|"
                     r"knowledge|proficiency|expertise|familiarity|background) (?:with|in|of)|"
                     r"(?:proficient|experienced|skilled|fluent) (?:with|in)|one of|any of|either)\s+",
                     re.IGNORECASE)


_NEED = re.compile(r"^\s*(?:at least\s+|any\s+|minimum (?:of\s+)?)?(\d|two|three|four|five)\s+(?:of|from)\s*:?\s+",
                   re.IGNORECASE)
_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5}


def needed_count(skill: str) -> int:
    """'2 of Java or Go or C++' / 'at least two of ...' -> 2. Everything else -> 1 (any one)."""
    m = _NEED.match(skill or "")
    if not m:
        return 1
    v = m.group(1).lower()
    return int(v) if v.isdigit() else _WORDS[v]


def alternatives(skill: str) -> list[str]:
    """['Java', 'Go', 'C++'] for 'Java or Go or C++' (also 'Java, Go or C++', 'Java / Go').
    A normal skill gives [skill]. 'Kafka or similar' gives ['Kafka']. 'CI/CD' stays one skill."""
    s = _NEED.sub("", (skill or "").strip())
    if not re.search(r"\s+or\s+|\s+/\s+|,", s, re.IGNORECASE) or (needed_count(skill) == 1 and not re.search(r"\s+or\s+|\s+/\s+", s, re.IGNORECASE)):
        return [s]
    s = _FILLER.sub("", s)
    parts = [_FILLER.sub("", p).strip(" .;:()") for p in _ALT_SPLIT.split(s)]
    parts = [p for p in parts if p and not _VAGUE.match(_norm(p))]
    return list(dict.fromkeys(parts)) or [s]


def is_one_of(skill: str) -> bool:
    return len(alternatives(skill)) > 1


def canonical(skill: str) -> str:
    alts = alternatives(skill)
    if len(alts) > 1:
        need = needed_count(skill)
        joined = " or ".join(dict.fromkeys(canonical(a) for a in alts))
        return f"{need} of {joined}" if need > 1 else joined
    one = alts[0]
    return synonym_table()[1].get(_norm(one), one.strip())


def aliases_for(skill: str) -> list[str]:
    """Every spelling that counts as this skill (canonical + aliases + simple variants)."""
    canon = canonical(skill)
    table = synonym_table()[1]
    forms = {a for a, c in table.items() if c == canon}
    forms |= {_norm(skill), _norm(canon)}
    for f in list(forms):  # "rest apis" <-> "rest api"
        if f in case_sensitive_words():
            continue
        if f.endswith("s") and len(f) > 3:
            forms.add(f[:-1])
        else:
            forms.add(f + "s")
    return sorted(forms, key=len, reverse=True)


@lru_cache(maxsize=2048)
def _pattern(alias: str) -> re.Pattern:
    # whole word, but allow symbols that are part of names (c++, c#, .net, node.js)
    return re.compile(r"(?<![a-z0-9+#])" + re.escape(alias) + r"(?![a-z0-9+#])")


@lru_cache(maxsize=256)
def _exact_pattern(word: str) -> re.Pattern:
    return re.compile(r"(?<![A-Za-z0-9+#.])" + re.escape(word) + r"(?!\.?[A-Za-z0-9+#])")


def count_mentions(skill: str, text: str) -> int:
    """How many times the skill (any alias) appears in text. Longer aliases win overlaps.
    For a "one of" requirement ("Java or Go or C++"): mentions of any of them."""
    alts = alternatives(skill)
    if len(alts) > 1:
        return sum(_count_one(a, text) for a in alts)
    return _count_one(alts[0], text)


def best_alternative(skill: str, text: str) -> tuple[str, int]:
    """The option of a "one of" requirement that the text shows most, with its count."""
    scored = [(a, _count_one(a, text)) for a in alternatives(skill)]
    return max(scored, key=lambda x: x[1])


def _count_one(skill: str, text: str) -> int:
    t = _norm(text)
    exact = case_sensitive_words()
    taken: list[tuple[int, int]] = []
    count = 0
    for alias in aliases_for(skill):
        if alias in exact:  # e.g. "go": only "Go" in the original text counts
            count += len(_exact_pattern(exact[alias]).findall(text))
            continue
        for m in _pattern(alias).finditer(t):
            if not any(m.start() < e and s < m.end() for s, e in taken):
                taken.append((m.start(), m.end()))
    return count + len(taken)


# ---------- related skills (config/skill_relations.yaml) ----------

@lru_cache
def _relations() -> dict:
    path = config.CONFIG_DIR / "skill_relations.yaml"
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}


def shown_by(skill: str) -> list[str]:
    """Skills whose use also shows this one: JavaScript <- TypeScript, Node.js, React..."""
    want = canonical(skill).lower()
    return [k for k, v in (_relations().get("implies") or {}).items()
            if any(canonical(x).lower() == want for x in (v or []))]


def close_to(skill: str) -> list[str]:
    """Skills that are near but not the same (JD wants TypeScript, resume has JavaScript)."""
    want = canonical(skill).lower()
    for k, v in (_relations().get("close") or {}).items():
        if canonical(k).lower() == want:
            return list(v or [])
    return []


def close_credit() -> float:
    return float(_relations().get("close_credit", 0.5))


def is_language(skill: str) -> bool:
    want = canonical(skill).lower()
    return any(canonical(x).lower() == want for x in (_relations().get("languages") or []))
