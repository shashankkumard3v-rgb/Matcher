"""Where My info lives: the experience notes, skills notes and project template.

Signed in -> MongoDB, per user (collection "notes"). The first time a user's note is read and
the database has none, it is filled from that user's old file (if any) or the starting template.
Not signed in (tests, scripts) -> the files in backend/context/, as before.
"""
from __future__ import annotations

import threading
from pathlib import Path

from app import config

KINDS = ("experience", "skills", "template")
FILES = {"experience": "experience.txt", "skills": "skills.txt", "template": "project_context.md"}

_cache: dict[tuple[str, str], str] = {}
_lock = threading.Lock()


class StorageError(ValueError):
    pass


def _check(kind: str) -> None:
    if kind not in KINDS:
        raise StorageError(f"Unknown notes kind '{kind}'.")


def default_text(kind: str) -> str:
    _check(kind)
    t = config.PROJECT_CONTEXT_TEMPLATE if kind == "template" else config.TEMPLATES_DIR / f"{kind}_template.txt"
    return t.read_text(encoding="utf-8")


def file_path(kind: str) -> Path:
    """The file for this kind (the signed-in user's old file, or the shared one)."""
    _check(kind)
    ctx = config.context_file()
    return ctx if kind == "template" else ctx.parent / FILES[kind]


def read_text(kind: str) -> str:
    _check(kind)
    uid = config.current_user_id()
    if uid is None:
        p = file_path(kind)
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(default_text(kind), encoding="utf-8")
        return p.read_text(encoding="utf-8")
    key = (uid, kind)
    if key in _cache:
        return _cache[key]
    from app.data.store import get_data_store
    store = get_data_store()
    with _lock:
        text = store.get_note(uid, kind)
        if text is None:  # first time: bring over the user's old file, or start from the template
            p = file_path(kind)
            text = p.read_text(encoding="utf-8") if p.exists() else default_text(kind)
            store.put_note(uid, kind, text)
        _cache[key] = text
    return text


def write_text(kind: str, text: str) -> None:
    _check(kind)
    uid = config.current_user_id()
    if uid is None:
        p = file_path(kind)
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            bak = p.with_name(p.name + ".bak")
            bak.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
        p.write_text(text, encoding="utf-8")
        return
    from app.data.store import get_data_store
    with _lock:
        get_data_store().put_note(uid, kind, text)   # keeps the previous versions as history
        _cache[(uid, kind)] = text


def clear_cache() -> None:
    _cache.clear()
