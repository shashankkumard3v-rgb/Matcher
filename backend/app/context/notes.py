"""Your free-form notes: experience and skills (in MongoDB when signed in, see storage.py).

Plain text you write in any style. Lines starting with a single # are comments and ignored.
A heading line starts a section and every line under it belongs to it, so you write the
company once. Headings: "Company: CartNest" (plain text), "Section: Achievements", or "## CartNest".
Matcher treats the remaining text as YOUR OWN FACTS, exactly like the project template:
the rewrite may use them, the truth check accepts quotes from them, and the gate counts
them toward your best possible score.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.context import storage

KINDS = ("experience", "skills")
MAX_BYTES = 200_000


class NotesError(ValueError):
    pass


def path(kind: str) -> Path:
    """The notes FILE (only used when nobody is signed in; signed in, notes live in MongoDB)."""
    if kind not in KINDS:
        raise NotesError(f"Notes must be one of: {', '.join(KINDS)}")
    return storage.file_path(kind)


def ensure() -> None:
    for kind in KINDS:
        storage.read_text(kind)   # creates it from the template the first time


def read_raw(kind: str) -> str:
    if kind not in KINDS:
        raise NotesError(f"Notes must be one of: {', '.join(KINDS)}")
    return storage.read_text(kind)


# "Company: Acme" or "Company - Acme" (a dash needs spaces, so "Company-wide rollout" stays a fact)
_PLAIN_HEADING = re.compile(r"^(company|employer|section)\s*(?::|\s-\s)\s*(.+)$", re.IGNORECASE)


# An unfilled "[...]" from the template: never a fact.
_PLACEHOLDER = re.compile(r"\[[^\]\n]*\]")


def _heading(line: str) -> str | None:
    s = line.strip()
    if s.startswith("##"):
        h = s.lstrip("#").strip()
        return h or None
    m = _PLAIN_HEADING.match(s)
    if m:
        return m.group(2).strip() or None
    return None


def entries(text: str) -> list[tuple[str | None, str]]:
    """(heading, line) pairs. Heading lines come back as (heading, heading); comments
    (a single #), blank lines and lines with an unfilled [placeholder] are dropped. Lines before any heading have heading None."""
    out: list[tuple[str | None, str]] = []
    current: str | None = None
    for raw in text.splitlines():
        h = _heading(raw)
        if h is not None:
            if _PLACEHOLDER.search(h):  # "Company: [Company name]" not filled in yet
                current = None
                continue
            current = h
            out.append((h, h))
            continue
        ln = raw.strip()
        if not ln or ln.startswith("#") or _PLACEHOLDER.search(ln):
            continue
        if ln.endswith(":") and len(ln) <= 40:  # a label like "What I did:"
            continue
        out.append((current, ln.lstrip("-*• ").strip() or ln))
    # a heading with nothing under it says nothing
    used = {h for h, ln in out if h != ln}
    return [(h, ln) for h, ln in out if h != ln or h in used]


def facts(text: str) -> list[str]:
    """The note's content lines, each carrying its heading ("CartNest | Built ...") so the line
    stands on its own (which job it belongs to). Comments and blank lines are removed."""
    out = []
    for h, ln in entries(text):
        out.append(ln if h is None or h == ln else f"{h} | {ln}")
    return out


def bare_lines(text: str) -> list[str]:
    """Content lines without headings and without the heading prefix."""
    return [ln for h, ln in entries(text) if h != ln]


def read_facts(kind: str) -> list[str]:
    return facts(read_raw(kind))


def all_facts() -> dict[str, list[str]]:
    return {kind: read_facts(kind) for kind in KINDS}


def all_bare_lines() -> dict[str, list[str]]:
    return {kind: bare_lines(read_raw(kind)) for kind in KINDS}


def save(kind: str, text: str) -> list[str]:
    data = text.replace("\r\n", "\n")
    if len(data.encode("utf-8")) > MAX_BYTES:
        raise NotesError("That's too long for a notes file (200 KB max).")
    if kind not in KINDS:
        raise NotesError(f"Notes must be one of: {', '.join(KINDS)}")
    storage.write_text(kind, data)   # the previous version is kept as a backup / history
    return facts(data)


def append(kind: str, text: str) -> list[str]:
    current = read_raw(kind).rstrip("\n")
    return save(kind, f"{current}\n\n{text.strip()}\n")


# ---------- adding facts in your own format ----------

_BULLET = re.compile(r"^(\s*)([-*•])\s+")


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _same_heading(a: str, b: str) -> bool:
    ka, kb = _key(a), _key(b)
    if bool(ka and kb) and (ka == kb or ka.startswith(kb + " ") or kb.startswith(ka + " ")):
        return True
    from app.parsing.structure import company_matches      # "Finlytics Pvt Ltd" = "Finlytics"
    strip = lambda h: re.sub(r"^\s*(company|employer|section)\s*[:\-]\s*", "", h, flags=re.IGNORECASE)
    return company_matches(strip(a), strip(b))


def add_bullets(kind: str, heading: str, bullets: list[str], *, prefix: str = "Company",
                intro: list[str] | None = None) -> list[str]:
    """Add bullets under an existing heading (e.g. "Company: CartNest"), in the same bullet style
    the section already uses, right after its last line. Bullets already written anywhere in the
    file are skipped. If the heading doesn't exist yet, a new block is created ("Company:" blocks go
    before the first "Section:" block). Returns the bullets actually added."""
    raw = read_raw(kind).replace("\r\n", "\n")
    lines = raw.split("\n")
    have = {_key(ln) for _, ln in entries(raw)}
    new = []
    for b in bullets:
        b = b.strip().lstrip("-*• ").strip()
        if b and _key(b) not in have and _key(b) not in {_key(x) for x in new}:
            new.append(b)
    if not new:
        return []

    start = next((i for i, ln in enumerate(lines)
                  if (h := _heading(ln)) and not _PLACEHOLDER.search(h) and _same_heading(h, heading)), None)
    if start is not None:
        end = next((i for i in range(start + 1, len(lines)) if _heading(lines[i]) is not None), len(lines))
        last = start
        style = "- "
        for i in range(start + 1, end):
            t = lines[i].strip()
            if t and not t.startswith("#"):
                last = i
            m = _BULLET.match(lines[i])
            if m:
                style = f"{m.group(1)}{m.group(2)} "
        lines[last + 1:last + 1] = [style + b for b in new]
    else:
        block = [f"{prefix}: {heading}", *(intro or []), *("- " + b for b in new)]
        while lines and not lines[-1].strip():
            lines.pop()
        first_section = next((i for i, ln in enumerate(lines)
                              if re.match(r"^\s*section\s*[:\-]", ln, re.IGNORECASE)), None)
        if prefix.lower() == "company" and first_section is not None:
            lines[first_section:first_section] = [*block, ""]
        else:
            lines += ["", *block]
    save(kind, "\n".join(lines).rstrip("\n") + "\n")
    return new
