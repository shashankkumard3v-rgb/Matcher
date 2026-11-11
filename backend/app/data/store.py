"""Per-user data in MongoDB: My info notes and the resume library.

Collections (database MONGODB_DB):
  notes          {user_id, kind, text, updated_at}            kind: experience | skills | template
  notes_history  {user_id, kind, text, saved_at}               the last few versions (backups)
  resumes        {user_id, name, notes, created_at, updated_at, source, resume, text, profile,
                  profile_version, file_id}
  GridFS "files" the original uploaded PDFs

Synchronous on purpose: the notes are read in many places of the (synchronous) scoring and loop
code. Reads are cached per user in memory and the cache is updated on every write, so a page
load doesn't hit the database again and again.
"""
from __future__ import annotations

import secrets
import threading
from datetime import datetime, timezone

HISTORY_KEEP = 10


class DataStoreError(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MongoDataStore:
    def __init__(self, uri: str, db: str):
        import gridfs
        from pymongo import ASCENDING, MongoClient
        self.client = MongoClient(uri, serverSelectionTimeoutMS=8000, tz_aware=True)
        self.db = self.client[db]
        self.fs = gridfs.GridFS(self.db, collection="files")
        self._asc = ASCENDING
        self._ready = False
        self._lock = threading.Lock()

    def _setup(self) -> None:
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            try:
                self.db.notes.create_index([("user_id", self._asc), ("kind", self._asc)], unique=True)
                self.db.notes_history.create_index([("user_id", self._asc), ("kind", self._asc),
                                                    ("saved_at", self._asc)])
                self.db.resumes.create_index([("user_id", self._asc), ("created_at", self._asc)])
            except Exception as e:
                from app.auth.store import _explain
                raise DataStoreError(_explain(e)) from e
            self._ready = True

    # ----- notes -----
    def get_note(self, user_id: str, kind: str) -> str | None:
        self._setup()
        d = self.db.notes.find_one({"user_id": user_id, "kind": kind})
        return d["text"] if d else None

    def put_note(self, user_id: str, kind: str, text: str) -> None:
        self._setup()
        old = self.db.notes.find_one({"user_id": user_id, "kind": kind})
        if old is not None and old["text"] != text:
            self.db.notes_history.insert_one({"user_id": user_id, "kind": kind, "text": old["text"],
                                              "saved_at": _now()})
            extra = list(self.db.notes_history.find({"user_id": user_id, "kind": kind}, {"_id": 1})
                         .sort("saved_at", -1).skip(HISTORY_KEEP))
            if extra:
                self.db.notes_history.delete_many({"_id": {"$in": [x["_id"] for x in extra]}})
        self.db.notes.update_one({"user_id": user_id, "kind": kind},
                                 {"$set": {"text": text, "updated_at": _now()}}, upsert=True)

    # ----- resumes -----
    def list_resumes(self, user_id: str) -> list[dict]:
        self._setup()
        return [self._out(d) for d in self.db.resumes.find({"user_id": user_id}).sort("created_at", -1)]

    def get_resume(self, user_id: str, rid: str) -> dict | None:
        self._setup()
        from bson import ObjectId
        from bson.errors import InvalidId
        try:
            d = self.db.resumes.find_one({"_id": ObjectId(rid), "user_id": user_id})
        except InvalidId:
            return None
        return self._out(d) if d else None

    def insert_resume(self, user_id: str, doc: dict) -> str:
        self._setup()
        now = _now()
        res = self.db.resumes.insert_one({**doc, "user_id": user_id, "created_at": now, "updated_at": now})
        return str(res.inserted_id)

    def update_resume(self, user_id: str, rid: str, fields: dict) -> bool:
        self._setup()
        from bson import ObjectId
        r = self.db.resumes.update_one({"_id": ObjectId(rid), "user_id": user_id},
                                       {"$set": {**fields, "updated_at": _now()}})
        return r.matched_count == 1

    def delete_resume(self, user_id: str, rid: str) -> bool:
        self._setup()
        from bson import ObjectId
        d = self.db.resumes.find_one_and_delete({"_id": ObjectId(rid), "user_id": user_id})
        if d and d.get("file_id"):
            self.delete_file(d["file_id"])
        return d is not None

    # ----- files -----
    def put_file(self, user_id: str, name: str, data: bytes, content_type: str) -> str:
        self._setup()
        return str(self.fs.put(data, filename=name, user_id=user_id, metadata={"content_type": content_type}))

    def get_file(self, user_id: str, file_id: str) -> tuple[str, bytes, str] | None:
        self._setup()
        from bson import ObjectId
        try:
            f = self.fs.get(ObjectId(file_id))
        except Exception:
            return None
        if getattr(f, "user_id", None) != user_id:
            return None
        return f.filename, f.read(), (f.metadata or {}).get("content_type", "application/octet-stream")

    def delete_file(self, file_id: str) -> None:
        from bson import ObjectId
        try:
            self.fs.delete(ObjectId(file_id))
        except Exception:
            pass

    @staticmethod
    def _out(d: dict) -> dict:
        d = dict(d)
        d["id"] = str(d.pop("_id"))
        if d.get("file_id") is not None:
            d["file_id"] = str(d["file_id"])
        return d


class MemoryDataStore:
    """Same interface in memory (tests)."""

    def __init__(self):
        self.notes: dict[tuple[str, str], str] = {}
        self.history: list[dict] = []
        self.resumes: dict[str, dict] = {}
        self.files: dict[str, tuple[str, str, bytes, str]] = {}

    def get_note(self, user_id, kind):
        return self.notes.get((user_id, kind))

    def put_note(self, user_id, kind, text):
        old = self.notes.get((user_id, kind))
        if old is not None and old != text:
            self.history.append({"user_id": user_id, "kind": kind, "text": old})
        self.notes[(user_id, kind)] = text

    def list_resumes(self, user_id):
        out = [dict(v, id=k) for k, v in self.resumes.items() if v["user_id"] == user_id]
        return sorted(out, key=lambda d: d["created_at"], reverse=True)

    def get_resume(self, user_id, rid):
        d = self.resumes.get(rid)
        return dict(d, id=rid) if d and d["user_id"] == user_id else None

    def insert_resume(self, user_id, doc):
        rid = secrets.token_hex(12)
        now = _now()
        self.resumes[rid] = {**doc, "user_id": user_id, "created_at": now, "updated_at": now}
        return rid

    def update_resume(self, user_id, rid, fields):
        d = self.resumes.get(rid)
        if not d or d["user_id"] != user_id:
            return False
        d.update(fields, updated_at=_now())
        return True

    def delete_resume(self, user_id, rid):
        d = self.resumes.get(rid)
        if not d or d["user_id"] != user_id:
            return False
        if d.get("file_id"):
            self.files.pop(d["file_id"], None)
        del self.resumes[rid]
        return True

    def put_file(self, user_id, name, data, content_type):
        fid = secrets.token_hex(12)
        self.files[fid] = (user_id, name, data, content_type)
        return fid

    def get_file(self, user_id, file_id):
        f = self.files.get(file_id)
        return (f[1], f[2], f[3]) if f and f[0] == user_id else None

    def delete_file(self, file_id):
        self.files.pop(file_id, None)


_store = None


def get_data_store():
    global _store
    if _store is None:
        from app import config
        uri = config.mongodb_uri()
        if not uri:
            raise DataStoreError("MONGODB_URI is not set in backend/.env.")
        try:
            _store = MongoDataStore(uri, config.mongodb_db())
        except ImportError as e:
            raise DataStoreError("The MongoDB package isn't installed: run pip install -r requirements.txt.") from e
        except Exception as e:
            from app.auth.store import _explain
            raise DataStoreError(_explain(e)) from e
    return _store


def set_data_store(store) -> None:
    global _store
    _store = store
