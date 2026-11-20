import io
import json

import pytest
from docx import Document
from fastapi.testclient import TestClient

from app.api.routes import get_llm
from app.export.builder import Look, build_docx
from app.export.export import trim_options
from app.export.fit import fit_one_page
from app.export.render import renderer_name
from app.main import app
from app.schemas.models import Contact, Experience, JobDescription, Resume
from tests.conftest import FakeLLM

needs_renderer = pytest.mark.skipif(renderer_name() is None, reason="no Word/LibreOffice to measure pages")


def big_resume(jobs=3, bullets=5):
    return Resume(
        contact=Contact(name="Priya Sharma", email="priya@example.com", phone="+91 98765 43210",
                        location="Bengaluru"),
        summary="Backend engineer building Python services with Kafka and PostgreSQL.",
        skills=["Python", "FastAPI", "Kafka", "PostgreSQL", "Docker", "AWS"],
        experience=[Experience(
            title=f"Engineer {i}", company=f"Company {chr(65 + i)}", start=f"20{20 - 2 * i}-04",
            end="present" if i == 0 else f"20{21 - 2 * i}-03",
            bullets=[f"Built payments reconciliation service {k} with Python and Kafka, reducing manual "
                     f"work by {10 + k}% for the finance operations team" for k in range(bullets)])
            for i in range(jobs)],
        achievements=["Won internal hackathon 2023"], certifications=["AWS Certified Developer"],
        languages=["English"],
    )


def test_docx_is_ats_safe_and_ordered():
    docx = build_docx(big_resume(2, 2), Look.at(0.5))
    doc = Document(io.BytesIO(docx))
    assert len(doc.tables) == 0 and len(doc.inline_shapes) == 0
    sec = doc.sections[0]
    assert round(sec.page_width.cm, 1) == 21.0 and round(sec.page_height.cm, 1) == 29.7
    texts = [p.text for p in doc.paragraphs]
    heads = [t for t in texts if t in ("SUMMARY", "SKILLS", "EXPERIENCE", "ACHIEVEMENTS", "LANGUAGES",
                                       "EDUCATION")]
    assert heads == ["SUMMARY", "SKILLS", "EXPERIENCE", "ACHIEVEMENTS"]   # no Languages
    assert texts[0] == "Priya Sharma" and "priya@example.com" in texts[1]  # contact in body
    assert "AWS Certified Developer" in texts and "Engineer 0 | Company A\tApr 2020 - Present" in texts
    assert sum(p.style.name == "List Bullet" for p in doc.paragraphs) == 4 + 2


def test_look_grows_with_scale():
    a, b = Look.at(0), Look.at(1)
    assert a.body_pt == 10 and b.body_pt > a.body_pt and a.margin_cm < b.margin_cm


@needs_renderer
def test_fit_statuses():
    assert fit_one_page(big_resume(2, 2)).status == "underfilled"
    r = fit_one_page(big_resume(3, 5))
    assert r.status == "fitted" and r.pages == 1 and 0.93 <= r.fill <= 0.99
    o = fit_one_page(big_resume(5, 9))
    assert o.status == "overflow" and o.pages > 1 and o.overflow_lines > 0


def test_trim_options_free_enough_and_keep_minimums():
    r = big_resume(3, 5)
    r.experience[2].bullets[0] = "Organised the team offsite"           # no skill, no number
    jd = JobDescription(must_have=["Kafka", "PostgreSQL"], nice_to_have=[])
    opts = trim_options(r, jd, Look.at(0).__dict__, overflow_lines=6)
    assert opts[0].text == "Organised the team offsite"               # least useful first
    chosen = [o for o in opts if o.suggested]
    assert sum(o.est_lines for o in chosen) >= 7
    for j, job in enumerate(r.experience):
        left = len(job.bullets) - sum(o.job == j for o in chosen)
        assert left >= (2 if j == 0 else 1)


@needs_renderer
def test_export_flow_underfilled_then_overflow_trim(tmp_workspace, resume_pdf, jd_docx):
    llm = FakeLLM()
    app.dependency_overrides[get_llm] = lambda: llm
    c = TestClient(app)
    run = c.post("/api/parse", files={"resume": ("cv.pdf", resume_pdf, "application/pdf"),
                                      "jd": ("jd.docx", jd_docx, "application/octet-stream")}).json()["run_id"]
    c.post(f"/api/runs/{run}/gate")
    c.post(f"/api/runs/{run}/gate/decide", json={"decision": "proceed", "reason": "test"})
    assert c.post(f"/api/runs/{run}/export").json()["detail"]["code"] == "loop_not_finished"
    c.post(f"/api/runs/{run}/loop/start")
    c.post(f"/api/runs/{run}/loop/finish", json={})

    # Small resume -> underfilled -> reopen the loop with a note about free space
    s = c.post(f"/api/runs/{run}/export").json()
    assert s["status"] == "underfilled" and s["free_lines"] > 5
    r = c.get(f"/api/runs/{run}/export/file/docx")
    assert r.status_code == 200 and r.content[:2] == b"PK"
    assert "Score by version" in c.get(f"/api/runs/{run}/export/file/report").text
    ls = c.post(f"/api/runs/{run}/export/reopen-loop").json()
    assert not ls["finished"] and "room for about" in ls["rounds"][-1]["notes"][-1]

    # Make the final version too long -> overflow -> trim suggested bullets -> new version
    run_dir = tmp_workspace / "runs" / run
    (run_dir / "versions" / "resume_v0.json").write_text(big_resume(5, 9).model_dump_json())
    c.post(f"/api/runs/{run}/loop/finish", json={"version": 0})
    s = c.post(f"/api/runs/{run}/export").json()
    assert s["status"] == "overflow" and any(o["suggested"] for o in s["trim_options"])
    picked = [o["id"] for o in s["trim_options"] if o["suggested"]]
    s2 = c.post(f"/api/runs/{run}/export/trim", json={"option_ids": picked}).json()
    assert s2["version"] == 1 and s2["history"] and "blind score" in s2["history"][0]
    assert s2["pages"] == 1 or s2["overflow_lines"] < s["overflow_lines"]
    loop = json.loads((run_dir / "loop.json").read_text())
    assert loop["final_version"] == 1 and loop["versions"][-1]["note"].startswith("Trimmed")


def test_headings_use_heading_style_and_properties_are_set():
    doc = Document(io.BytesIO(build_docx(big_resume(1, 2), Look.at(0.5))))
    heads = [p for p in doc.paragraphs if p.text in ("SUMMARY", "SKILLS", "EXPERIENCE", "ACHIEVEMENTS")]
    assert heads and all(p.style.name == "Heading 1" for p in heads)
    assert doc.core_properties.author == "Priya Sharma" and "Resume" in doc.core_properties.title


@needs_renderer
def test_ats_check_passes_on_a_clean_resume():
    from app.export import ats
    from app.export.builder import format_spec
    r = big_resume(2, 3)
    r.contact.links = ["https://linkedin.com/in/priya"]
    fit = fit_one_page(r)
    checks = {c.id: c for c in ats.check(fit.docx, fit.pdf, r, format_spec(), fit.look)}
    fails = [c for c in checks.values() if c.level == "fail"]
    assert not fails, fails
    assert checks["layout"].level == checks["headings"].level == checks["bullets"].level == "pass"
    assert checks["links"].level == "info"          # labels only: explained, not failed
    # a table breaks it
    doc = Document(io.BytesIO(fit.docx))
    doc.add_table(rows=1, cols=2)
    buf = io.BytesIO()
    doc.save(buf)
    assert {c.id: c for c in ats.check(buf.getvalue(), fit.pdf, r, format_spec(), fit.look)}["layout"].level == "fail"


@needs_renderer
def test_preview_and_fresh_score(tmp_workspace, resume_pdf, jd_docx):
    llm = FakeLLM()
    app.dependency_overrides[get_llm] = lambda: llm
    c = TestClient(app)
    run = c.post("/api/parse", files={"resume": ("cv.pdf", resume_pdf, "application/pdf"),
                                      "jd": ("jd.docx", jd_docx, "application/octet-stream")}).json()["run_id"]
    c.post(f"/api/runs/{run}/gate")
    c.post(f"/api/runs/{run}/gate/decide", json={"decision": "proceed", "reason": "test"})
    assert c.post(f"/api/runs/{run}/export/fresh-score").status_code == 404
    c.post(f"/api/runs/{run}/loop/start")
    c.post(f"/api/runs/{run}/loop/finish", json={})
    s = c.post(f"/api/runs/{run}/export").json()
    assert s["ats"] and {x["id"] for x in s["ats"]} >= {"layout", "contact", "headings", "jobs"}
    png = c.get(f"/api/runs/{run}/export/preview.png")
    assert png.status_code == 200 and png.content[:4] == b"\x89PNG"
    pdf = c.get(f"/api/runs/{run}/export/file/pdf?inline=true")
    assert pdf.headers["content-disposition"].startswith("inline")

    n_before = len(llm.inputs)
    f = c.post(f"/api/runs/{run}/export/fresh-score").json()
    assert f["n"] == 1 and f["score"]["total"] > 0 and f["read_back"]["jobs"] >= 1
    # the fresh check read the exported FILE: the parser got raw text, the judge got no history
    new_inputs = llm.inputs[n_before:]
    assert any("Priya Sharma" in i for i in new_inputs)
    assert not any("score_v" in i or "Round" in i or "loop" in i.lower() for i in new_inputs)
    assert len(c.get(f"/api/runs/{run}/export/fresh-scores").json()) == 1


@needs_renderer
def test_ats_check_has_no_false_alarms_on_our_own_output():
    from app.export import ats
    from app.export.builder import format_spec
    r = big_resume(2, 3)
    fit = fit_one_page(r)
    levels = {c.id: c.level for c in ats.check(fit.docx, fit.pdf, r, format_spec(), fit.look)}
    assert levels["fonts"] == "pass" and levels["symbols"] == "pass", levels


def test_the_resume_has_an_education_section():
    import io
    from docx import Document
    from app.export.builder import build_docx
    from app.export.layout import Look
    from app.schemas.models import Education, Experience, Resume
    r = Resume(experience=[Experience(title="SDE 2", company="Gruve AI", start="2022-01", end="present",
                                      bullets=["Built Kafka pipelines", "Cut p99 latency to 120ms"])],
               education=[Education(degree="B.Tech Computer Science", institution="XYZ University",
                                    start="2015", end="2019", details="CGPA 8.6/10")])
    text = "\n".join(p.text for p in Document(io.BytesIO(build_docx(r, Look.at(0)))).paragraphs)
    assert "EDUCATION" in text and "B.Tech Computer Science\t2015 - 2019" in text   # dates on the degree line
    assert "XYZ University | CGPA 8.6/10" in text
    assert text.index("EXPERIENCE") < text.index("EDUCATION")



def test_skills_are_sorted_by_kind_on_at_most_four_lines():
    from app.export.builder import skill_groups, skills_line_text
    lines = skill_groups(["Go", "Python", "SQL", "FastAPI", "gRPC", "Kafka", "React", "Next.js", "PostgreSQL", "Redis",
                          "AWS", "Docker", "Kubernetes", "System Design", "Microservices", "Git", "Jira",
                          "Mentoring", "Communication"])
    text = [skills_line_text(p) for p in lines]
    assert len(lines) == 4
    assert text[0] == "Languages: Go, Python, SQL"
    assert text[1] == "Backend: FastAPI, gRPC, Kafka | Frontend: React, Next.js"
    assert text[2] == "Databases: PostgreSQL, Redis | Cloud & DevOps: AWS, Docker, Kubernetes"
    assert text[3] == "Engineering: System Design, Microservices, Git, Jira | Soft Skills: Mentoring, Communication"
    few = [skills_line_text(p) for p in skill_groups(["Python", "Django", "PostgreSQL", "AWS"])]
    assert few == ["Languages: Python", "Backend: Django", "Databases: PostgreSQL", "Cloud & DevOps: AWS"]   # no sharing needed
    assert skill_groups(["Excel"]) == [[("", ["Excel"])]]
