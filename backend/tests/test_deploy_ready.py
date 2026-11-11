"""Ready for a host that wipes its disk (Render free): runs live in the database, and only
the allowed Google accounts can sign in."""
import shutil

import pytest
from fastapi.testclient import TestClient

from app import config
from app.api.routes import get_llm
from app.data import runstore
from app.main import app
from tests.conftest import FakeLLM
from tests.test_auth import sign_in, world  # noqa: F401  (fixture)

pytestmark = pytest.mark.real_auth


@pytest.fixture(autouse=True)
def _fresh_runstore():
    runstore.reset()
    yield
    runstore.reset()


def _new_run(c, resume_pdf, jd_docx):
    r = c.post("/api/parse", files={"resume": ("cv.pdf", resume_pdf, "application/pdf"),
                                   "jd": ("jd.docx", jd_docx, "application/octet-stream")})
    assert r.status_code == 200, r.text
    return r.json()["run_id"]


def _wipe_disk_like_render(tmp):
    """What a free host does when it sleeps: every file is gone, the server starts fresh."""
    shutil.rmtree(tmp / "users", ignore_errors=True)
    runstore.reset()


def test_runs_survive_a_wiped_disk(world, resume_pdf, jd_docx):
    tmp, _ = world
    app.dependency_overrides[get_llm] = lambda: FakeLLM()
    c, _ = sign_in("asha")
    run = _new_run(c, resume_pdf, jd_docx)
    score = c.post(f"/api/runs/{run}/score").json()["score"]["total"]
    gate = c.post(f"/api/runs/{run}/gate").json()

    _wipe_disk_like_render(tmp)
    assert not (tmp / "users").exists()

    got = c.get(f"/api/runs/{run}").json()                  # restored from the database
    assert got["score"]["total"] == score
    assert [r["run_id"] for r in c.get("/api/runs").json()] == [run]
    assert c.get(f"/api/runs/{run}/gate").json()["current_score"] == gate["current_score"]


def test_only_changed_files_are_saved_again(world, resume_pdf, jd_docx):
    app.dependency_overrides[get_llm] = lambda: FakeLLM()
    c, _ = sign_in("asha")
    from app.data.store import get_data_store
    store = get_data_store()
    run = _new_run(c, resume_pdf, jd_docx)
    before = dict(store.run_files)
    c.post(f"/api/runs/{run}/score")
    changed = {k[2] for k in store.run_files if store.run_files[k] != before.get(k)}
    assert changed == {"score_0.json", "pins.json"}          # scoring wrote exactly these two


def test_deleting_a_run_deletes_it_from_the_database(world, resume_pdf, jd_docx):
    tmp, _ = world
    app.dependency_overrides[get_llm] = lambda: FakeLLM()
    c, _ = sign_in("asha")
    keep, gone = _new_run(c, resume_pdf, jd_docx), _new_run(c, resume_pdf, jd_docx)
    assert c.delete(f"/api/runs/{gone}").status_code == 200
    _wipe_disk_like_render(tmp)
    assert [r["run_id"] for r in c.get("/api/runs").json()] == [keep]   # it doesn't come back


def test_each_user_gets_only_their_own_runs_back(world, resume_pdf, jd_docx):
    tmp, _ = world
    app.dependency_overrides[get_llm] = lambda: FakeLLM()
    a, _ = sign_in("asha")
    b, _ = sign_in("bilal")
    run_a = _new_run(a, resume_pdf, jd_docx)
    _wipe_disk_like_render(tmp)
    assert b.get("/api/runs").json() == []
    assert b.get(f"/api/runs/{run_a}").status_code == 404
    assert [r["run_id"] for r in a.get("/api/runs").json()] == [run_a]


def test_runs_already_on_a_laptop_are_uploaded(world, resume_pdf, jd_docx):
    """Runs made before this change exist only as folders: the first change uploads them."""
    tmp, _ = world
    app.dependency_overrides[get_llm] = lambda: FakeLLM()
    c, user = sign_in("asha")
    from app.data.store import get_data_store
    run = _new_run(c, resume_pdf, jd_docx)
    store = get_data_store()
    store.run_files.clear()                                 # as if nothing was ever uploaded
    runstore.reset()
    c.post(f"/api/runs/{run}/score")
    assert {k[1] for k in store.run_files} == {run}
    assert any(k[2] == "resume.json" for k in store.run_files)


# ---------- who may sign in ----------

def test_only_allowed_emails_can_sign_in(world, monkeypatch):
    monkeypatch.setenv("ALLOWED_EMAILS", "Asha@Example.com, someone@else.com")
    sign_in("asha")                                         # allowed (any letter case)
    r = TestClient(app).post("/api/auth/google", json={"credential": "token-mallory"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "not_allowed"
    assert "matcher_session" not in r.cookies


def test_a_session_from_before_the_allowlist_stops_working(world, monkeypatch):
    c, _ = sign_in("mallory")
    assert c.get("/api/runs").status_code == 200
    monkeypatch.setenv("ALLOWED_EMAILS", "asha@example.com")
    r = c.get("/api/runs")
    assert r.status_code == 401 and r.json()["detail"]["code"] == "not_signed_in"


def test_no_allowlist_means_any_google_account(world, monkeypatch):
    monkeypatch.delenv("ALLOWED_EMAILS", raising=False)
    assert config.email_allowed("anyone@example.com")
    sign_in("anyone")
