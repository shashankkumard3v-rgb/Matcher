"""Every job keeps 2 bullets; removals explain themselves; the AI gets the job it works on;
long pastes are split; notes can be uploaded as Word or PDF."""
import io

from app.context.updater import split_long
from app.loop import guard
from app.loop.loop import _build_input
from app.parsing.pipeline import load_project_context
from app.schemas.models import Change, Experience, JobDescription, Resume, Round

JD = JobDescription(title="Backend Engineer", must_have=["Kafka", "Python"])


def _resume(n=2):
    bullets = ["Built a Kafka consumer pipeline in Python that cut lag by 40%", "Wrote billing reports",
               "Ran the team's weekly demo"][:n]
    return Resume(experience=[Experience(title="SDE", company="Acme", start="2021-01", end="present", bullets=bullets),
                              Experience(title="Intern", company="Beta", bullets=["Fixed UI bugs", "Wrote tests"])])


def test_a_job_never_drops_below_two_bullets():
    r = _resume(3)
    rm = lambda cid, b: Change(id=cid, kind="remove_bullet", job=0, bullet=b, old=r.experience[0].bullets[b],
                               job_label="SDE - Acme")
    a, b = rm("a", 1), rm("b", 2)
    guard.min_check(r, [a, b], 2)
    assert a.status == "pending" and b.status == "blocked" and "keeps at least 2" in b.truth_issues[0]
    a.status = "rejected"
    guard.min_check(r, [a, b], 2)                 # rejecting one frees the other
    assert b.status == "pending" and not b.truth_issues


def test_a_removal_says_what_it_shows_and_what_happens():
    r = _resume(3)
    c = Change(id="c", kind="remove_bullet", job=0, bullet=1, old=r.experience[0].bullets[1], job_label="SDE - Acme")
    guard.review(r, JD, [c])
    text = " ".join(c.impact)
    assert "shows none of the job's skills" in text and "no measurable result" in text
    assert "from 3 to 2 bullets" in text and "Rather keep it?" in text and "no change" in text


def test_redoing_an_edit_sends_the_whole_job_and_only_that_job(tmp_workspace):
    from app.context import notes
    notes.save("experience", "Company: Acme\n- Moved Kafka consumers to Kubernetes in 2023\nCompany: Beta\n- Fixed flaky UI tests\n")
    r = _resume(3)
    ch = Change(id="c", kind="replace_bullet", job=0, bullet=1, old="Wrote billing reports", new="x",
                feedback_history=["shorter"])
    text = _build_input(JD, r, load_project_context(), Round(n=1, base_version=0, score_before=0), {}, "use my notes", ch)
    working = text.split("WORKING ON", 1)[1]
    assert "Ran the team's weekly demo" in working and "Moved Kafka consumers to Kubernetes" in working
    assert "Fixed flaky UI tests" not in working
    assert "EARLIER FEEDBACK ON THIS EDIT:\n- shorter" in text


def test_a_long_paste_is_split_into_pieces_at_company_headings():
    long = "\n".join(["Company: Acme"] + [f"- Built service {i} handling {i} million events a day with Kafka" for i in range(60)]
                     + ["", "Company: Beta"] + [f"- Fixed bug {i} in the billing UI" for i in range(30)])
    pieces = split_long(long, 1000)
    assert len(pieces) > 3 and all(len(p) <= 1100 for p in pieces)
    assert all(p.startswith("Company:") for p in pieces)      # each piece says which job it is about
    assert "".join(pieces).count("Built service 59") == 1
    assert split_long("short answer") == ["short answer"]


def test_notes_can_be_uploaded_as_a_word_document(tmp_workspace):
    from docx import Document
    from fastapi.testclient import TestClient
    from app.main import app
    doc = Document()
    for line in ["Company: Acme", "Built a Kafka consumer pipeline that cut lag by 40%", "Led on-call for payments"]:
        doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    c = TestClient(app)
    r = c.post("/api/context/notes/experience/upload", data={"mode": "replace"},
               files={"file": ("my experience.docx", buf.getvalue(), "application/octet-stream")})
    assert r.status_code == 200, r.text
    from app.context import notes
    assert "Led on-call for payments" in notes.read_raw("experience")
    bad = c.post("/api/context/notes/experience/upload", files={"file": ("x.exe", b"MZ", "application/octet-stream")})
    assert bad.status_code == 400 and "Word" in bad.json()["detail"]
