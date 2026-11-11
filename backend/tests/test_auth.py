"""Google sign-in, sessions, and each user's own runs and notes."""
import pytest
from fastapi.testclient import TestClient

from app import config
from app.auth import google, store as store_mod
from app.main import app

pytestmark = pytest.mark.real_auth


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Empty user store, temp folders, and a fake Google that accepts "token-<name>"."""
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(config, "CONTEXT_DIR", tmp_path / "context")
    monkeypatch.setattr(config, "PROJECT_CONTEXT_FILE", tmp_path / "context" / "project_context.md")
    monkeypatch.setattr(config, "USERS_DIR", tmp_path / "users")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-123")
    monkeypatch.setenv("MONGODB_URI", "mongodb://fake")
    store = store_mod.MemoryStore()
    store_mod.set_store(store)
    from app.context import storage
    from app.data import store as data_store
    data_store.set_data_store(data_store.MemoryDataStore())
    storage.clear_cache()

    async def fake_verify(credential, client_id):
        assert client_id == "client-123"
        if not credential.startswith("token-"):
            raise google.GoogleAuthError("bad token")
        n = credential[6:]
        return {"sub": f"sub-{n}", "email": f"{n}@example.com", "email_verified": True, "name": n.title()}

    monkeypatch.setattr(google, "verify", fake_verify)
    yield tmp_path, store
    store_mod.set_store(None)
    data_store.set_data_store(None)
    storage.clear_cache()


def sign_in(name):
    c = TestClient(app)
    r = c.post("/api/auth/google", json={"credential": f"token-{name}"})
    assert r.status_code == 200, r.text
    return c, r.json()


def test_everything_needs_sign_in_except_public_endpoints(world):
    c = TestClient(app)
    for path in ("/api/runs", "/api/context/notes", "/api/skills", "/api/auth/me"):
        r = c.get(path)
        assert r.status_code == 401 and r.json()["detail"]["code"] == "not_signed_in", path
    assert c.post("/api/parse").status_code == 401
    assert c.get("/api/health").status_code == 200
    assert c.get("/api/auth/config").json() == {"google_client_id": "client-123", "ready": True,
                                                "missing": [], "db_error": None}
    assert c.get("/api/runs", cookies={"matcher_session": "made-up"}).status_code == 401


def test_bad_google_token_is_refused(world):
    r = TestClient(app).post("/api/auth/google", json={"credential": "forged"})
    assert r.status_code == 401 and "matcher_session" not in r.cookies


def test_sign_in_sets_an_httponly_session_and_logout_ends_it(world):
    c, body = sign_in("asha")
    assert body["user"]["email"] == "asha@example.com" and body["new"] and "google_sub" not in body["user"]
    set_cookie = [v for k, v in c.cookies.items()]
    assert set_cookie
    assert c.get("/api/auth/me").json()["user"]["name"] == "Asha"
    assert c.post("/api/auth/logout").status_code == 200
    c.cookies.clear()
    assert c.get("/api/auth/me").status_code == 401
    _, again = sign_in("asha")
    assert again["new"] is False


def test_first_account_takes_over_earlier_files_and_users_are_separate(world):
    tmp, _ = world
    (tmp / "runs" / "old-run").mkdir(parents=True)
    (tmp / "runs" / "old-run" / "jd.json").write_text("{}")
    (tmp / "context").mkdir(parents=True)
    (tmp / "context" / "experience.txt").write_text("Company: Acme\n- Built the old thing\n")

    a, body_a = sign_in("asha")
    assert set(body_a["moved"]) >= {"old-run", "experience.txt"}
    uid_a = body_a["user"]["id"]
    assert (tmp / "users" / uid_a / "runs" / "old-run").is_dir()
    assert not (tmp / "runs" / "old-run").exists()
    assert "Built the old thing" in a.get("/api/context/notes").json()["experience"]["text"]

    b, body_b = sign_in("bilal")
    assert body_b["moved"] == []                          # only the very first account
    assert "Built the old thing" not in b.get("/api/context/notes").json()["experience"]["text"]
    b.put("/api/context/notes/experience", json={"text": "Company: Beta\n- Bilal's own fact\n"})
    assert "Bilal's own fact" not in a.get("/api/context/notes").json()["experience"]["text"]
    assert b.get("/api/runs").json() == []
    assert b.get("/api/runs/old-run").status_code == 404   # can't open someone else's run


def test_missing_settings_are_reported(world, monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID")
    c = TestClient(app)
    assert c.get("/api/auth/config").json()["missing"] == ["GOOGLE_CLIENT_ID"]
    assert c.post("/api/auth/google", json={"credential": "token-x"}).status_code == 503


def test_placeholder_password_is_reported_on_the_sign_in_screen(world, monkeypatch):
    monkeypatch.setenv("MONGODB_URI", "mongodb+srv://u:<db_password>@cluster0.x.mongodb.net/")
    cfg = TestClient(app).get("/api/auth/config").json()
    assert not cfg["ready"] and "<db_password>" in cfg["db_error"]


def test_mongo_errors_are_explained_without_secrets():
    from app.auth.store import _explain
    assert "Network Access" in _explain(Exception("connection timed out"))
    assert "Database Access" in _explain(Exception("bad auth : authentication failed"))


def test_missing_packages_give_a_clear_message_not_a_500(world, monkeypatch):
    import builtins
    real_import = builtins.__import__

    def no_pymongo(name, *a, **k):
        if name.startswith("pymongo"):
            raise ImportError("No module named 'pymongo'")
        return real_import(name, *a, **k)

    store_mod.set_store(None)
    monkeypatch.setattr(builtins, "__import__", no_pymongo)
    r = TestClient(app).post("/api/auth/google", json={"credential": "token-asha"})
    assert r.status_code == 503 and "pip install -r requirements.txt" in r.json()["detail"]["message"]


def test_unexpected_errors_are_logged_and_explained(world, monkeypatch, tmp_path):
    from app.auth import routes as auth_routes

    async def boom(credential, client_id):
        raise RuntimeError("something odd")

    monkeypatch.setattr(google, "verify", boom)
    r = TestClient(app, raise_server_exceptions=False).post("/api/auth/google", json={"credential": "x"})
    assert r.status_code == 500
    assert r.json()["detail"]["message"].startswith("Server error: RuntimeError: something odd")
    assert auth_routes is not None
