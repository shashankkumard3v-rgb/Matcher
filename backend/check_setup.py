r"""Checks backend/.env and the connections Matcher needs. Never prints passwords or keys.

Run:  .venv\Scripts\python.exe check_setup.py     (or double-click check_setup.bat)
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OK, BAD, WARN = "[ OK ]", "[FAIL]", "[WARN]"
problems = 0


def say(mark: str, text: str) -> None:
    global problems
    problems += mark == BAD
    print(f"  {mark} {text}")


def main() -> int:
    print("\nMatcher setup check (nothing secret is shown)\n")
    env = HERE / ".env"
    if not env.exists():
        say(BAD, ".env not found in the backend folder. Copy .env.example to .env and fill it in.")
        return 1
    from dotenv import load_dotenv
    load_dotenv(env, override=True)

    # --- packages
    missing = []
    for mod in ("pymongo", "google.auth", "requests", "fastapi", "dotenv"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        say(BAD, f"Packages not installed: {', '.join(missing)}. Close the server window and run run.bat.")
    else:
        say(OK, "Packages installed")

    # --- MongoDB
    uri = os.getenv("MONGODB_URI", "").strip()
    db = os.getenv("MONGODB_DB", "").strip() or "matcher"
    if not uri:
        say(BAD, "MONGODB_URI is empty.")
    elif "<" in uri or ">" in uri or "db_password" in uri:
        say(BAD, "MONGODB_URI still contains <db_password>: put your database user's password there (no < >).")
    elif not uri.startswith(("mongodb+srv://", "mongodb://")):
        say(BAD, "MONGODB_URI should start with mongodb+srv://")
    else:
        m = re.match(r"mongodb(?:\+srv)?://([^:@/]+)(?::([^@]*))?@([^/?]+)", uri)
        if not m:
            say(BAD, "MONGODB_URI doesn't look like mongodb+srv://USER:PASSWORD@HOST/")
        else:
            user, pw, host = m.groups()
            if not pw:
                say(BAD, "MONGODB_URI has no password after the user name.")
            elif re.search(r"[@:/?#&]", pw.replace("%40", "").replace("%3A", "")):
                say(WARN, "The password contains special characters; if connecting fails, change it to letters and numbers.")
            say(OK, f"MONGODB_URI is set (user '{user}', host '{host}', database '{db}')")
            if not missing:
                try:
                    from pymongo import MongoClient
                    c = MongoClient(uri, serverSelectionTimeoutMS=8000)
                    c.admin.command("ping")
                    c[db].list_collection_names()
                    say(OK, f"Connected to MongoDB Atlas; database '{db}' is usable")
                except Exception as e:  # noqa: BLE001
                    msg = str(e)
                    if "bad auth" in msg or "Authentication failed" in msg:
                        say(BAD, "MongoDB refused the user/password. Check the database user and password "
                                 "(Atlas > Database Access), not your Atlas login email.")
                    elif "timed out" in msg.lower() or "ServerSelectionTimeoutError" in type(e).__name__:
                        say(BAD, "Couldn't reach MongoDB in time. Atlas > Network Access: add your current IP "
                                 "(or 0.0.0.0/0 while testing), and check your internet/VPN.")
                    elif "DNS" in msg or "resolution" in msg.lower():
                        say(BAD, "The cluster address couldn't be found. Copy the connection string again from Atlas.")
                    else:
                        say(BAD, f"MongoDB error: {type(e).__name__}: {msg[:200]}")

    # --- Google sign-in
    gid = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    if not gid:
        say(BAD, "GOOGLE_CLIENT_ID is empty.")
    elif gid.startswith("123456789012-abc"):
        say(BAD, "GOOGLE_CLIENT_ID is still the example value from .env.example.")
    elif not gid.endswith(".apps.googleusercontent.com"):
        say(BAD, "GOOGLE_CLIENT_ID should end with .apps.googleusercontent.com (use the Client ID, not the secret).")
    else:
        say(OK, f"GOOGLE_CLIENT_ID looks right ({gid[:8]}...{gid[-28:]})")
        say(WARN, "In Google Cloud the client's Authorized JavaScript origins must be http://localhost:8000 "
                  "and http://localhost, and you open Matcher at http://localhost:8000")

    # --- AI keys
    keys = [k for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY")
            if os.getenv(k, "").strip() and "example" not in os.getenv(k, "")]
    if keys:
        say(OK, "AI key(s) set: " + ", ".join(keys))
    else:
        say(BAD, "No AI key set (or still the example). Add at least one, e.g. GEMINI_API_KEY.")

    print("\n" + ("All good. Start run.bat and open http://localhost:8000" if not problems
                  else f"{problems} problem(s) to fix above, then run this check again.") + "\n")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
