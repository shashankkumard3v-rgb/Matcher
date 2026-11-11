"""Check a Google sign-in (an ID token from the "Sign in with Google" button)."""
from __future__ import annotations

import asyncio


class GoogleAuthError(ValueError):
    pass


class GoogleUnavailable(RuntimeError):
    """Google's signing keys couldn't be downloaded (no internet, proxy, firewall)."""


def _verify_sync(credential: str, client_id: str) -> dict:
    try:
        from google.auth.exceptions import TransportError
        from google.auth.transport import requests as g_requests
        from google.oauth2 import id_token
    except ImportError as e:
        raise GoogleUnavailable(
            "The Google sign-in package isn't installed. In the backend folder run: "
            ".venv\\Scripts\\python.exe -m pip install -r requirements.txt  (or double-click "
            "check_setup.bat), then restart the server.") from e
    try:
        claims = id_token.verify_oauth2_token(credential, g_requests.Request(), client_id)
    except TransportError as e:
        raise GoogleUnavailable(f"Couldn't reach Google to check the sign-in: {e}") from e
    except ValueError as e:  # bad signature, wrong audience, expired...
        raise GoogleAuthError(f"Google sign-in couldn't be verified: {e}") from e
    if claims.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        raise GoogleAuthError("This sign-in didn't come from Google.")
    if not claims.get("email") or not claims.get("email_verified"):
        raise GoogleAuthError("Your Google account's email isn't verified.")
    return claims


async def verify(credential: str, client_id: str) -> dict:
    """Signature, audience (our client id), issuer and expiry are all checked."""
    return await asyncio.to_thread(_verify_sync, credential, client_id)
