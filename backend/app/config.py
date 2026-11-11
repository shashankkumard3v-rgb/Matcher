"""Paths and settings. Everything is relative to the backend folder."""
from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BACKEND_DIR / "config"
CONTEXT_DIR = BACKEND_DIR / "context"
TEMPLATES_DIR = BACKEND_DIR / "templates"
RUNS_DIR = BACKEND_DIR / "runs"
SKILLS_DIR = BACKEND_DIR / "skills"
# Remembers which Claude skill id/version matches each local SKILL.md
SKILL_CACHE_FILE = BACKEND_DIR / ".claude_skills.json"
# The built web app (frontend/dist), served at http://localhost:8000/
FRONTEND_DIST = BACKEND_DIR.parent / "frontend" / "dist"

PROJECT_CONTEXT_FILE = CONTEXT_DIR / "project_context.md"
PROJECT_CONTEXT_TEMPLATE = TEMPLATES_DIR / "project_context_template.md"
# Each signed-in user's own runs and notes: users/<user id>/runs and users/<user id>/context
USERS_DIR = BACKEND_DIR / "users"

# Who the current request is for (set after sign-in). None = no user (tests, scripts).
_current_user: ContextVar[str | None] = ContextVar("matcher_user", default=None)


def current_user_id() -> str | None:
    return _current_user.get()


def set_current_user(user_id: str | None):
    return _current_user.set(user_id)


@contextmanager
def as_user(user_id: str | None):
    token = _current_user.set(user_id)
    try:
        yield
    finally:
        _current_user.reset(token)


def user_dir(user_id: str) -> Path:
    return USERS_DIR / user_id


def runs_dir() -> Path:
    """The signed-in user's run folders (or the shared folder when nobody is signed in)."""
    uid = _current_user.get()
    return user_dir(uid) / "runs" if uid else RUNS_DIR


def context_file() -> Path:
    """The signed-in user's project_context.md; experience.txt / skills.txt sit next to it."""
    uid = _current_user.get()
    return user_dir(uid) / "context" / "project_context.md" if uid else PROJECT_CONTEXT_FILE

load_dotenv(BACKEND_DIR / ".env")


def google_client_id() -> str | None:
    return os.getenv("GOOGLE_CLIENT_ID") or None


def mongodb_uri() -> str | None:
    return os.getenv("MONGODB_URI") or None


def mongodb_db() -> str:
    return os.getenv("MONGODB_DB") or "matcher"


def openrouter_api_key() -> str | None:
    return os.getenv("OPENROUTER_API_KEY") or None


def anthropic_api_key() -> str | None:
    return os.getenv("ANTHROPIC_API_KEY") or None


@lru_cache
def models_config() -> dict:
    with open(CONFIG_DIR / "models.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


@lru_cache
def weights_config() -> dict:
    with open(CONFIG_DIR / "weights.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def ensure_dirs() -> None:
    runs_dir().mkdir(parents=True, exist_ok=True)
    if _current_user.get() is None:   # signed in, My info lives in the database instead
        from app.context import notes, storage
        storage.read_text("template")
        notes.ensure()
