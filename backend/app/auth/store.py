"""Users and sign-in sessions, stored in MongoDB (Atlas).

Collections (database MONGODB_DB, default "matcher"):
  users     {_id, google_sub (unique), email, name, picture, created_at, last_login_at}
  sessions  {token_hash (unique), user_id, created_at, expires_at}  - expired ones are removed
            automatically by MongoDB (TTL index). Only a hash of the session token is stored.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel

SESSION_DAYS = 30


class User(BaseModel):
    id: str
    google_sub: str
    email: str
    name: str = ""
    picture: str = ""


class AuthStoreError(RuntimeError):
    pass


def new_token() -> tuple[str, str]:
    """(token for the cookie, hash to store)."""
    t = secrets.token_urlsafe(32)
    return t, token_hash(t)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _explain(e: Exception) -> str:
    """A MongoDB error in plain words (never includes the password)."""
    msg = str(e)
    if "bad auth" in msg or "Authentication failed" in msg:
        return ("MongoDB refused the user name/password in MONGODB_URI. Check the database user "
                "(Atlas > Database Access) and its password.")
    if "timed out" in msg.lower() or type(e).__name__ == "ServerSelectionTimeoutError":
        return ("Couldn't reach MongoDB Atlas. In Atlas > Network Access, allow your current IP address; "
                "also check your internet or VPN.")
    if "DNS" in msg or "resolution" in msg.lower():
        return "The MongoDB cluster address in MONGODB_URI couldn't be found. Copy it again from Atlas."
    return f"Can't use MongoDB: {type(e).__name__}: {msg[:200]}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _user(doc: dict) -> User:
    return User(id=str(doc["_id"]), google_sub=doc["google_sub"], email=doc.get("email", ""),
                name=doc.get("name", ""), picture=doc.get("picture", ""))


class MongoStore:
    def __init__(self, uri: str, db: str):
        from pymongo import AsyncMongoClient
        self.client = AsyncMongoClient(uri, serverSelectionTimeoutMS=8000, tz_aware=True)
        self.db = self.client[db]
        self._ready = False

    async def _setup(self) -> None:
        if self._ready:
            return
        try:
            await self.db.users.create_index("google_sub", unique=True)
            await self.db.sessions.create_index("token_hash", unique=True)
            await self.db.sessions.create_index("expires_at", expireAfterSeconds=0)
        except Exception as e:
            raise AuthStoreError(_explain(e)) from e
        self._ready = True

    async def ping(self) -> None:
        await self._setup()

    async def count_users(self) -> int:
        await self._setup()
        return await self.db.users.count_documents({})

    async def upsert_google_user(self, sub: str, email: str, name: str, picture: str) -> tuple[User, bool]:
        """Returns (user, created)."""
        from pymongo.errors import DuplicateKeyError
        await self._setup()
        now = _now()
        fields = {"email": email, "name": name, "picture": picture, "last_login_at": now}
        doc = await self.db.users.find_one_and_update({"google_sub": sub}, {"$set": fields},
                                                      return_document=True)
        if doc:
            return _user(doc), False
        try:
            res = await self.db.users.insert_one({"google_sub": sub, "created_at": now, **fields})
        except DuplicateKeyError:  # signed in twice at the same moment
            return _user(await self.db.users.find_one({"google_sub": sub})), False
        return _user({"_id": res.inserted_id, "google_sub": sub, **fields}), True

    async def create_session(self, user_id: str) -> str:
        await self._setup()
        token, h = new_token()
        now = _now()
        await self.db.sessions.insert_one({"token_hash": h, "user_id": user_id, "created_at": now,
                                           "expires_at": now + timedelta(days=SESSION_DAYS)})
        return token

    async def user_for_token(self, token: str) -> User | None:
        await self._setup()
        s = await self.db.sessions.find_one({"token_hash": token_hash(token)})
        if not s or s["expires_at"] <= _now():
            return None
        from bson import ObjectId
        doc = await self.db.users.find_one({"_id": ObjectId(s["user_id"])})
        return _user(doc) if doc else None

    async def delete_session(self, token: str) -> None:
        await self._setup()
        await self.db.sessions.delete_one({"token_hash": token_hash(token)})


class MemoryStore:
    """Same interface, kept in memory. Used by the tests."""

    def __init__(self):
        self.users: dict[str, dict] = {}
        self.sessions: dict[str, dict] = {}

    async def ping(self) -> None:
        return None

    async def count_users(self) -> int:
        return len(self.users)

    async def upsert_google_user(self, sub, email, name, picture):
        for uid, u in self.users.items():
            if u["google_sub"] == sub:
                u.update(email=email, name=name, picture=picture)
                return _user({"_id": uid, **u}), False
        uid = secrets.token_hex(12)
        self.users[uid] = {"google_sub": sub, "email": email, "name": name, "picture": picture}
        return _user({"_id": uid, **self.users[uid]}), True

    async def create_session(self, user_id):
        token, h = new_token()
        self.sessions[h] = {"user_id": user_id, "expires_at": _now() + timedelta(days=SESSION_DAYS)}
        return token

    async def user_for_token(self, token):
        s = self.sessions.get(token_hash(token))
        if not s or s["expires_at"] <= _now() or s["user_id"] not in self.users:
            return None
        return _user({"_id": s["user_id"], **self.users[s["user_id"]]})

    async def delete_session(self, token):
        self.sessions.pop(token_hash(token), None)


_store = None


def get_store():
    """The MongoDB store from MONGODB_URI (created once). Tests replace it with set_store()."""
    global _store
    if _store is None:
        from app import config
        uri = config.mongodb_uri()
        if not uri:
            raise AuthStoreError("MONGODB_URI is not set in backend/.env.")
        try:
            _store = MongoStore(uri, config.mongodb_db())
        except ImportError as e:
            raise AuthStoreError(
                "The MongoDB package isn't installed. In the backend folder run: "
                ".venv\\Scripts\\python.exe -m pip install -r requirements.txt  (or double-click "
                "check_setup.bat), then restart the server.") from e
        except Exception as e:  # bad connection string, cluster address not found...
            raise AuthStoreError(_explain(e)) from e
    return _store


def set_store(store) -> None:
    global _store
    _store = store
