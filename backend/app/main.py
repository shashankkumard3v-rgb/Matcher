import asyncio
import logging
from logging.handlers import RotatingFileHandler
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app import config
from app.api.routes import router
from app.auth.routes import COOKIE
from app.auth.routes import router as auth_router
from app.library.routes import router as library_router
from app.auth.store import AuthStoreError, get_store
from app.data.store import DataStoreError
from app.config import ensure_dirs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
# Errors and warnings also go to backend/logs/server.log, so they can be read after the
# terminal window is closed. (logs/ isn't watched by auto-reload.)
_LOG_DIR = config.BACKEND_DIR / "logs"
_LOG_DIR.mkdir(exist_ok=True)
if not any(isinstance(h, RotatingFileHandler) for h in logging.getLogger().handlers):
    _fh = RotatingFileHandler(_LOG_DIR / "server.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    _fh.setLevel(logging.WARNING)
    _fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.getLogger().addHandler(_fh)
log = logging.getLogger("matcher")


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_dirs()
    yield


app = FastAPI(title="Matcher API", version="0.1.0", lifespan=lifespan)

# The app is served by this same server, so no cross-origin access is needed. Only the Vite
# dev server (npm run dev, port 5173) may call the API from another origin. Allowing "*" would
# let any website you visit read your resume and notes from this server.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# One change at a time per run: every step reads the run's files, may wait for the AI, then
# writes them back. Two at once (say, skipping a question while an answer is being read)
# would let the second overwrite the first.
_RUN_PATH = re.compile(r"^/api/runs/([A-Za-z0-9_-]+)(?:/|$)")   # deleting a run waits for a running step too
_run_locks: dict[str, asyncio.Lock] = {}


@app.middleware("http")
async def one_change_per_run(request: Request, call_next):
    m = _RUN_PATH.match(request.url.path)
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and m:
        lock = _run_locks.setdefault(m.group(1), asyncio.Lock())
        async with lock:
            return await call_next(request)
    return await call_next(request)

# Sign-in: every /api call except these needs a valid session cookie (Google sign-in).
_PUBLIC = {"/api/health", "/api/app-version", "/api/auth/config", "/api/auth/google", "/api/auth/logout"}


@app.middleware("http")
async def require_sign_in(request: Request, call_next):
    """Runs before everything else: finds who you are and points file storage at your folder."""
    path = request.url.path
    request.state.user = None
    token = request.cookies.get(COOKIE)
    if token and path.startswith("/api/"):
        try:
            request.state.user = await get_store().user_for_token(token)
        except AuthStoreError as e:
            if path not in _PUBLIC:
                return JSONResponse({"detail": {"code": "db_unavailable", "message": str(e)}}, status_code=503)
    if path.startswith("/api/") and path not in _PUBLIC and request.state.user is None:
        return JSONResponse({"detail": {"code": "not_signed_in", "message": "Sign in with Google."}},
                            status_code=401)
    user = request.state.user
    reset = config.set_current_user(user.id if user else None)
    try:
        return await call_next(request)
    finally:
        config._current_user.reset(reset)


app.include_router(router, prefix="/api")
app.include_router(auth_router, prefix="/api")
app.include_router(library_router, prefix="/api")


@app.exception_handler(DataStoreError)
async def database_error(request: Request, exc: DataStoreError):
    log.warning("Database problem on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(status_code=503, content={"detail": {"code": "db_unavailable", "message": str(exc)}})


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    """Anything not handled elsewhere: log the full error, and tell the app what went wrong
    instead of a bare "HTTP 500"."""
    log.exception("Unexpected error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": {
        "code": "server_error",
        "message": f"Server error: {type(exc).__name__}: {str(exc)[:300]} (details in backend/logs/server.log)"}})


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": app.version}


# ---------- the web app (frontend/dist), if it has been built ----------

@app.api_route("/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"],
               include_in_schema=False)
async def web_app(path: str, request: Request):
    """Serve the phone/desktop app. Unknown paths get index.html (single-page app).
    Anything else (unknown API calls, non-GET requests) is a plain 404."""
    dist = config.FRONTEND_DIST.resolve()
    if request.method not in ("GET", "HEAD") or path.startswith("api/"):
        raise HTTPException(404, {"code": "unknown_endpoint", "message":
            f"The server doesn't know {request.method} /{path}. It's probably still running older code: "
            "close the server window and start backend\\run.bat again."})
    if not (dist / "index.html").exists():
        raise HTTPException(404, "Not found. The web app isn't built: see frontend/README.md")
    target = (dist / path).resolve()
    if path and target.is_file() and dist in target.parents:
        headers = {"Cache-Control": "public, max-age=31536000, immutable"} if path.startswith("assets/") else {}
        return FileResponse(target, headers=headers)
    return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})
