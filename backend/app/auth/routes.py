"""Sign in with Google -> a session cookie. Every other /api call needs that cookie."""
from __future__ import annotations

import logging
import shutil

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from app import config
from app.auth import google
from app.auth.store import AuthStoreError, get_store

logger = logging.getLogger(__name__)
router = APIRouter()

COOKIE = "matcher_session"


class GoogleBody(BaseModel):
    credential: str


@router.get("/auth/config")
async def auth_config():
    """What the sign-in screen needs (the Google client id is public by design), and whether
    the database can be reached right now."""
    import asyncio
    missing = [k for k, v in (("GOOGLE_CLIENT_ID", config.google_client_id()),
                              ("MONGODB_URI", config.mongodb_uri())) if not v]
    db_error = None
    if config.mongodb_uri():
        uri = config.mongodb_uri()
        if "<" in uri or "db_password" in uri:
            db_error = "MONGODB_URI still contains <db_password>: put your database user's password there."
        else:
            try:
                await asyncio.wait_for(get_store().ping(), timeout=10)
            except AuthStoreError as e:
                db_error = str(e)
            except asyncio.TimeoutError:
                db_error = "MongoDB didn't answer in 10 seconds. Check Atlas > Network Access and your internet."
    return {"google_client_id": config.google_client_id(),
            "ready": not missing and db_error is None,
            "missing": missing, "db_error": db_error}


def _claim_legacy_data(user_id: str) -> list[str]:
    """The very first account takes over the runs and notes made before sign-in existed.
    Files are moved, never overwritten."""
    moved = []
    dest = config.user_dir(user_id)
    pairs = [(config.RUNS_DIR, dest / "runs")]
    for name in ("project_context.md", "experience.txt", "skills.txt",
                 "project_context.md.bak", "experience.txt.bak", "skills.txt.bak"):
        pairs.append((config.CONTEXT_DIR / name, dest / "context" / name))
    for src, dst in pairs:
        if not src.exists():
            continue
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
            for item in src.iterdir():
                target = dst / item.name
                if not target.exists():
                    shutil.move(str(item), str(target))
                    moved.append(item.name)
        elif not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            moved.append(src.name)
    return moved


def _set_cookie(response: Response, request: Request, token: str) -> None:
    from app.auth.store import SESSION_DAYS
    response.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax",
                        secure=request.url.scheme == "https", path="/")


@router.post("/auth/google")
async def sign_in_google(body: GoogleBody, request: Request, response: Response):
    client_id = config.google_client_id()
    if not client_id:
        raise HTTPException(503, {"code": "not_configured", "message": "GOOGLE_CLIENT_ID is not set in backend/.env."})
    try:
        claims = await google.verify(body.credential, client_id)
    except google.GoogleAuthError as e:
        raise HTTPException(401, {"code": "google_rejected", "message": str(e)}) from e
    except google.GoogleUnavailable as e:
        msg = str(e) if "isn't installed" in str(e) else \
            "Couldn't reach Google to check your sign-in. Check your internet and try again."
        raise HTTPException(503, {"code": "google_unreachable", "message": msg}) from e
    if not claims.get("email_verified", False) or not config.email_allowed(claims.get("email")):
        logger.warning("Sign-in refused for %s (not in ALLOWED_EMAILS)", claims.get("email"))
        raise HTTPException(403, {"code": "not_allowed", "message":
            f"{claims.get('email')} isn't allowed to use this Matcher. The owner can add it to "
            "ALLOWED_EMAILS in the server settings."})
    try:
        store = get_store()
        first = await store.count_users() == 0
        user, created = await store.upsert_google_user(
            claims["sub"], claims["email"], claims.get("name", ""), claims.get("picture", ""))
        token = await store.create_session(user.id)
    except AuthStoreError as e:
        raise HTTPException(503, {"code": "db_unavailable", "message": str(e)}) from e
    moved = _claim_legacy_data(user.id) if (first and created) else []
    if moved:
        logger.info("Moved %d earlier item(s) into %s's account", len(moved), user.email)
    _set_cookie(response, request, token)
    return {"user": user.model_dump(exclude={"google_sub"}), "new": created, "moved": moved}


@router.get("/auth/me")
async def me(request: Request):
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(401, {"code": "not_signed_in", "message": "Sign in with Google."})
    return {"user": user.model_dump(exclude={"google_sub"})}


@router.post("/auth/logout")
async def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE)
    if token:
        try:
            await get_store().delete_session(token)
        except AuthStoreError:
            pass
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}
