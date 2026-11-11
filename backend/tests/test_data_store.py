"""My info notes and the resume library in the database."""
import pytest

from app import config
from app.context import notes, storage
from app.data import store as ds


@pytest.fixture
def user_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "USERS_DIR", tmp_path / "users")
    mem = ds.MemoryDataStore()
    ds.set_data_store(mem)
    storage.clear_cache()
    yield mem
    ds.set_data_store(None)
    storage.clear_cache()


def test_signed_in_notes_live_in_the_database_and_old_files_move_in(user_db, tmp_path):
    old = tmp_path / "users" / "u1" / "context"
    old.mkdir(parents=True)
    (old / "experience.txt").write_text("Company: Acme\n- Built the old thing\n")
    with config.as_user("u1"):
        assert "Built the old thing" in notes.read_raw("experience")     # brought over once
        notes.save("experience", "Company: Acme\n- Built a new thing\n")
        assert "new thing" in notes.read_raw("experience")
        assert notes.read_raw("skills") == storage.default_text("skills")   # starts from the template
    assert user_db.notes[("u1", "experience")].endswith("Built a new thing\n")
    assert user_db.history[-1]["text"].startswith("Company: Acme\n- Built the old thing")
    assert (old / "experience.txt").exists()                               # old file kept as backup
    with config.as_user("u2"):
        assert notes.read_raw("experience") == storage.default_text("experience")  # not u1's notes


def test_project_template_is_per_user_too(user_db):
    from app.parsing.pipeline import load_project_context
    with config.as_user("u1"):
        storage.write_text("template", "# Project Context\n\n## Project: Pipe\n- Employer: Acme\n- Role: SWE\n"
                                       "- Start: 2023-01\n- End: 2023-02\n- What I did:\n  - Built it\n")
        assert [p.name for p in load_project_context().projects] == ["Pipe"]
    with config.as_user("u2"):
        assert load_project_context().projects == []


def test_mongo_data_store_against_a_stand_in_database():
    mongomock = pytest.importorskip("mongomock")
    import mongomock.gridfs
    mongomock.gridfs.enable_gridfs_integration()
    store = ds.MongoDataStore.__new__(ds.MongoDataStore)
    import gridfs
    import threading
    from pymongo import ASCENDING
    store.client = mongomock.MongoClient(tz_aware=True)
    store.db = store.client["matcher"]
    store.fs = gridfs.GridFS(store.db, collection="files")
    store._asc, store._ready, store._lock = ASCENDING, False, threading.Lock()

    assert store.get_note("u", "experience") is None
    store.put_note("u", "experience", "one")
    store.put_note("u", "experience", "two")
    assert store.get_note("u", "experience") == "two"
    assert store.db.notes_history.count_documents({"user_id": "u"}) == 1

    fid = store.put_file("u", "cv.pdf", b"%PDF-1", "application/pdf")
    rid = store.insert_resume("u", {"name": "Backend", "file_id": fid, "profile": {}})
    assert [r["name"] for r in store.list_resumes("u")] == ["Backend"] and store.list_resumes("x") == []
    assert store.get_file("u", fid)[1] == b"%PDF-1" and store.get_file("x", fid) is None
    assert store.update_resume("u", rid, {"name": "Backend v2"}) and store.get_resume("u", rid)["name"] == "Backend v2"
    assert store.get_resume("x", rid) is None and store.get_resume("u", "not-an-id") is None
    assert store.delete_resume("u", rid) and store.get_file("u", fid) is None
