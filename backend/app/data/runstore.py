"""Runs live in MongoDB; the run folder on disk is only a working copy.

All the steps (scoring, gate, improve loop, export) keep reading and writing files in
backend/users/<you>/runs/<run>/ exactly as before. This module keeps that folder in step with
the database, so a host that wipes its disk (Render free sleeps and restarts) loses nothing:

- restore: before a request that needs runs, any run that's in the database but missing
  on disk is written back to disk (once per user per server start, plus on demand);
- sync: after a request that may have changed something, every file that changed since the
  last sync is saved to the database (only the changed ones: compared by content hash);
- delete: deleting a run removes it from the database too.

On your laptop the first sync uploads the runs you already have, so nothing is lost there either.
Only signed-in users are synced (tests without a user keep using plain folders).
"""
from __future__ import annotations

import hashlib
import logging
import threading
from pathlib import Path

from app import config

log = logging.getLogger("matcher.runstore")

MAX_FILE_BYTES = 15 * 1024 * 1024   # a MongoDB document holds up to 16 MB; run files are far smaller

_lock = threading.Lock()
# what the database holds, per user: {run_id: {relative path: sha1}}
_manifest: dict[str, dict[str, dict[str, str]]] = {}
_restored: set[str] = set()
# files already compared, per user: {absolute path: (size, mtime_ns)}; unchanged ones aren't re-read
_seen: dict[str, dict[str, tuple[int, int]]] = {}


def _sha(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def _store():
    from app.data.store import get_data_store
    return get_data_store()


def _run_dirs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return [p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")]


def _local_files(run_dir: Path) -> dict[str, Path]:
    out = {}
    for p in run_dir.rglob("*"):
        if p.is_file() and not any(part.startswith(".") for part in p.relative_to(run_dir).parts):
            out[p.relative_to(run_dir).as_posix()] = p
    return out


def _manifest_for(user_id: str) -> dict[str, dict[str, str]]:
    if user_id not in _manifest:
        _manifest[user_id] = _store().run_manifest(user_id)
    return _manifest[user_id]


def enabled() -> str | None:
    """The signed-in user's id when runs should be kept in the database."""
    return config.current_user_id()


def restore(force: bool = False) -> int:
    """Writes runs that are in the database but not on disk. Returns how many were restored."""
    uid = enabled()
    if not uid:
        return 0
    with _lock:
        if uid in _restored and not force:
            return 0
        manifest = _manifest_for(uid)
        root = config.runs_dir()
        have = {p.name for p in _run_dirs(root)}
        n = 0
        for run_id in manifest:
            if run_id in have:
                continue
            d = root / run_id
            for rel, data in _store().get_run_files(uid, run_id):
                target = (d / rel).resolve()
                if d.resolve() not in target.parents:      # never write outside the run folder
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                st = target.stat()
                _seen.setdefault(uid, {})[str(target)] = (st.st_size, st.st_mtime_ns)
            n += 1
        _restored.add(uid)
        if n:
            log.info("Restored %d run(s) from the database", n)
        return n


def sync() -> int:
    """Saves every changed file of every run on disk. Returns how many files were written."""
    uid = enabled()
    if not uid:
        return 0
    with _lock:
        manifest = _manifest_for(uid)
        written = 0
        for d in _run_dirs(config.runs_dir()):
            known = manifest.setdefault(d.name, {})
            files = _local_files(d)
            seen = _seen.setdefault(uid, {})
            for rel, p in files.items():
                try:
                    st = p.stat()
                    stamp = (st.st_size, st.st_mtime_ns)
                    if rel in known and seen.get(str(p)) == stamp:
                        continue                 # untouched since the last sync
                    data = p.read_bytes()
                except OSError:
                    continue                     # e.g. a file still being written; next sync gets it
                if len(data) > MAX_FILE_BYTES:
                    log.warning("Not saving %s/%s to the database: %d bytes is too big", d.name, rel, len(data))
                    continue
                h = _sha(data)
                if known.get(rel) != h:
                    _store().put_run_file(uid, d.name, rel, data, h)
                    known[rel] = h
                    written += 1
                seen[str(p)] = stamp
            for rel in [r for r in known if r not in files]:   # removed on disk (e.g. a trimmed export)
                _store().delete_run_file(uid, d.name, rel)
                known.pop(rel)
        return written


def delete(run_id: str) -> None:
    uid = enabled()
    if not uid:
        return
    with _lock:
        _store().delete_run(uid, run_id)
        _manifest_for(uid).pop(run_id, None)


def reset() -> None:
    """Forget what's cached (tests, or after the database changes underneath)."""
    with _lock:
        _manifest.clear()
        _restored.clear()
        _seen.clear()
