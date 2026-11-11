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
    return int(data.get("version", 0)), table


@lru_cache
def case_sensitive_words() -> dict[str, str]:
    """lowercase -> exact spelling for words that are also everyday English."""
    return {str(w).lower(): str(w) for w in (_data().get("case_sensitive") or [])}


def canonical(skill: str) -> str:
    return synonym_table()[1].get(_norm(skill), skill.strip())


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
    """How many times the skill (any alias) appears in text. Longer aliases win overlaps."""
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
