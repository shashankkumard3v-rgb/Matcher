import io
import json

import pytest
from docx import Document
from fastapi.testclient import TestClient

from app.api.routes import get_llm
from app.export.builder import Look, build_docx
from app.export.export import trim_options
from app.export.fit import fit_one_page
from tests.render_check import WHY, can_render
from app.main import app
from app.schemas.models import Contact, Experience, JobDescription, Resume
from tests.conftest import FakeLLM

needs_renderer = pytest.mark.skipif(not can_render(), reason=WHY)


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



def test_skills_are_sorted_into_four_fixed_lines():
    from app.export.builder import skill_groups, skills_line_text
    lines = [skills_line_text(p) for p in skill_groups(
        ["Go", "Python", "SQL", "FastAPI", "gRPC", "Kafka", "React", "Next.js", "PostgreSQL", "Redis",
         "AWS", "Docker", "Kubernetes", "System Design", "Microservices", "Git", "Jira"])]
    assert lines == ["Languages: Go, Python, SQL",
                     "Frontend & Backend: React, Next.js, FastAPI, gRPC, Kafka, System Design, Microservices",
                     "Databases & Cloud: PostgreSQL, Redis, AWS",
                     "DevOps & Tools: Docker, Kubernetes, Git, Jira"]
    few = [skills_line_text(p) for p in skill_groups(["Python", "Django", "PostgreSQL"])]
    assert few == ["Languages: Python", "Backend: Django", "Databases: PostgreSQL"]     # labels name only what's there
    assert skill_groups(["Excel"]) == [[("", ["Excel"])]]


def test_a_long_skills_line_keeps_the_jobs_skills_and_fits_one_printed_line():
    from app.export.layout import page_skill_lines, wrap
    from app.export.builder import skills_line_text
    from app.schemas.models import Experience, Resume
    many = ["React", "Next.js", "Redux", "HTML", "CSS", "Tailwind CSS", "Node.js", "NestJS", "gRPC", "REST APIs",
            "GraphQL", "Kafka", "Fastify", "Express.js", "Microservices", "Distributed Systems", "Caching"]
    r = Resume(skills=["Go"] + many, skill_focus=["Kafka", "GraphQL", "Microservices"],
               experience=[Experience(title="SDE", company="Acme", bullets=["Built REST APIs in Node.js", "x y"])])
    lines, dropped = page_skill_lines(r, 480, 11)
    text = skills_line_text(lines[1])
    assert len(wrap(text, 480, 11)) == 1 and dropped
    assert all(x in text for x in ("Kafka", "GraphQL", "Microservices", "Node.js", "REST APIs"))   # asked for / shown



def test_profile_links_read_as_short_clickable_names():
    from app.export.builder import link_label
    assert link_label("https://www.geeksforgeeks.org/user/shashank/") == "GeeksforGeeks"
    assert link_label("auth.geeksforgeeks.org/user/shashank") == "GeeksforGeeks"
    assert link_label("leetcode.com/u/shashank") == "LeetCode"
    assert link_label("https://www.naukri.com/code360/profile/x") == "Code360"
    assert link_label("shashank.dev") == "Portfolio"           # your own site: still opens the full address


def test_the_header_has_no_address():
    import io
    from docx import Document
    from app.export.builder import Look, build_docx
    from app.schemas.models import Contact, Experience, Resume
    r = Resume(contact=Contact(name="Shashank Kumar", email="s@example.com", phone="+91 99999 00000",
                               location="Bengaluru, India", links=["https://www.geeksforgeeks.org/user/shashank/"]),
               experience=[Experience(title="SDE", company="Acme", bullets=["Built x", "Built y"])])
    text = "\n".join(p.text for p in Document(io.BytesIO(build_docx(r, Look.at(0)))).paragraphs)
    assert "Bengaluru" not in text and "s@example.com | +91 99999 00000 | GeeksforGeeks" in text


def test_skills_lines_follow_your_notes_and_know_more_names():
    from app.export.builder import known_kind
    from app.export.skillkinds import notes_kinds
    assert known_kind("Go (Golang)") == "Languages" and known_kind("Express.js") == "Backend"
    assert known_kind("Firestore") == "Databases" and known_kind("Bazel") == "DevOps" and known_kind("GCP") == "Cloud"
    nk = notes_kinds("PayPal\n* Backend: Gin, Fastify\n* Database: Firestore\n* Build Tools: Bazel\n"
                     "* Testing: Testify, Mock\n* Cloud & Infrastructure: Google Cloud Run\n"
                     "- Apache Kafka: used at the ingestion pipeline")
    assert nk["gin"] == "Backend" and nk["firestore"] == "Databases" and nk["bazel"] == "DevOps"
    assert nk["testify"] == "Engineering" and nk["google cloud run"] == "Cloud"
    assert "used at the ingestion pipeline" not in nk                  # "Apache Kafka:" isn't a category


async def test_unknown_skills_are_asked_once_and_remembered(tmp_path):
    from app.export.skillkinds import effective
    from app.schemas.models import Resume

    class Sorter:
        calls = 0
        async def run_skill(self, skill, user_input, model=None):
            Sorter.calls += 1
            assert skill == "matcher-sort-skills" and "Zorblax" in user_input and "Kubernetes" not in user_input
            return {"kinds": [{"skill": "Zorblax", "kind": "Backend"}]}

    r = Resume(skills=["Kubernetes", "Zorblax"])
    assert (await effective(r, tmp_path, Sorter()))["Zorblax"] == "Backend"
    assert (await effective(r, tmp_path, Sorter()))["Zorblax"] == "Backend" and Sorter.calls == 1


def test_ai_tools_share_the_devops_and_tools_line():
    from app.export.builder import skill_groups, skills_line_text
    lines = [skills_line_text(p) for p in skill_groups(["Go", "Docker", "Git", "Cursor", "ChatGPT"])]
    assert lines == ["Languages: Go", "DevOps, Tools & AI: Docker, Git, Cursor, ChatGPT"]


def test_ai_tools_show_only_when_the_job_asks_for_ai():
    from app.export.skillkinds import jd_wants_ai, page_skills
    from app.schemas.models import JobDescription, Resume
    r = Resume(skills=["Go", "Kafka", "Cursor", "ChatGPT", "Claude"])
    plain = JobDescription(title="Backend Engineer", must_have=["Go", "Kafka"], responsibilities=["Build payment APIs"])
    ai = JobDescription(title="Backend Engineer", must_have=["Go"], nice_to_have=["Experience with AI coding tools like Copilot"])
    assert not jd_wants_ai(plain) and jd_wants_ai(ai)
    assert page_skills(r, {}, plain) == ["Go", "Kafka"]
    assert page_skills(r, {}, ai) == ["Go", "Kafka", "Cursor", "ChatGPT", "Claude"]
    assert not jd_wants_ai(JobDescription(title="Engineer", responsibilities=["Maintain the email service"]))   # "email" isn't AI



def test_soft_skills_are_always_on_the_page_with_at_least_two_kept():
    from app.export.builder import skills_line_text
    from app.export.layout import page_skill_lines
    from app.export.skillkinds import page_skills
    from app.schemas.models import JobDescription, Resume
    r = Resume(skills=["Docker", "Kubernetes", "Helm", "Terraform", "Jenkins", "Bazel", "Git", "Jira", "Jest",
                       "Playwright", "Postman", "ESLint", "Mentoring", "Communication", "Leadership"])
    r.skills = page_skills(r, {}, JobDescription(title="Backend Engineer", must_have=["Go"]))
    assert "Mentoring" in r.skills
    lines, dropped = page_skill_lines(r, 420, 11)
    texts = [skills_line_text(p) for p in lines]
    assert texts[-1] == "Soft Skills: Mentoring, Communication, Leadership"      # their own line
    assert texts[-2].startswith("DevOps & Tools:") and dropped                   # the long one was trimmed


def test_skills_are_ranked_for_the_job_even_when_it_lists_few():
    from app.export.skillkinds import rank
    from app.schemas.models import Experience, JobDescription, Resume
    r = Resume(skills=["PostgreSQL", "MySQL", "Firestore", "Redux", "Bazel"],
               experience=[Experience(title="SDE", company="A", bullets=["Built Go services on Firestore", "x y z"])])
    jd = JobDescription(title="Backend Engineer", must_have=["PostgreSQL", "Go"])
    s = rank(r, jd)
    assert s["PostgreSQL"] > s["MySQL"] > s["Redux"]          # asked > related (same kind of database) > unrelated
    assert s["Firestore"] > s["Bazel"]                         # your bullets show Firestore
    assert s["MySQL"] >= 60 and s["Redux"] < 20               # a backend role doesn't favour frontend


def test_tools_keep_two_places_and_soft_skills_have_their_own_line():
    from app.export.builder import skills_line_text
    from app.export.layout import page_skill_lines
    from app.schemas.models import Resume
    r = Resume(skills=["Docker", "Kubernetes", "Helm", "Terraform", "Jenkins", "Bazel", "Ansible", "Git", "Jira",
                       "Jest", "Mentoring", "Communication"],
               skill_rank={"Docker": 90, "Kubernetes": 90, "Helm": 80, "Terraform": 80, "Jenkins": 80, "Bazel": 80,
                           "Ansible": 80, "Git": 1, "Jira": 1, "Jest": 0, "Mentoring": 0, "Communication": 0})
    lines = [skills_line_text(p) for p in page_skill_lines(r, 300, 11)[0]]
    assert sum(x in lines[-2] for x in ("Git", "Jira", "Jest")) >= 2             # Tools keeps 2 places
    assert lines[-1] == "Soft Skills: Mentoring, Communication"                  # Soft Skills: own line


def _sel_resume():
    from app.schemas.models import Experience, Resume
    return Resume(
        skills=["TypeScript", "Go (Golang)", "PHP", "React.js", "Redux", "Tailwind CSS", "Node.js", "NestJS",
                "gRPC", "Kafka", "PostgreSQL", "MySQL", "AWS", "Docker", "Kubernetes", "Git", "Jira", "Jest",
                "Mentoring", "Communication", "Leadership"],
        experience=[Experience(title="SDE", company="A", bullets=["Built Go (Golang) gRPC services on PostgreSQL", "Ran Docker"]),
                    Experience(title="SDE", company="B", bullets=["Built NestJS APIs with Kafka", "Built React.js pages in TypeScript"])])


def test_skills_the_job_does_not_need_are_left_off_even_with_room():
    from app.export.skillkinds import select
    from app.schemas.models import JobDescription
    jd = JobDescription(title="Backend Engineer", must_have=["Go", "PostgreSQL", "Kubernetes", "AWS"])
    shown, off = select(_sel_resume(), jd)
    assert {"Go (Golang)", "PostgreSQL", "Kubernetes", "AWS"} <= set(shown)        # named
    assert {"MySQL", "gRPC", "NestJS", "Kafka"} <= set(shown)                      # related / proven, fits role
    assert "PHP" in off and "Tailwind CSS" in off                                 # unrelated, unproven
    assert "React.js" in shown and "Redux" in off                                 # off-role: only proven ones
    assert sum(s in shown for s in ("Git", "Jira", "Jest")) == 2                  # Tools keep exactly their 2 best
    assert sum(s in shown for s in ("Mentoring", "Communication", "Leadership")) == 2   # Soft Skills too


def test_the_same_skills_for_a_frontend_job_keep_frontend():
    from app.export.skillkinds import select
    from app.schemas.models import JobDescription
    jd = JobDescription(title="Frontend Engineer", must_have=["React", "TypeScript"])
    shown, off = select(_sel_resume(), jd)
    assert {"React.js", "Redux", "Tailwind CSS", "TypeScript"} <= set(shown)
    assert "PHP" in off


def test_a_skill_you_ask_for_in_the_chat_stays_on_the_page():
    from app.export.edit import apply
    from app.export.skillkinds import select
    from app.schemas.models import JobDescription
    jd = JobDescription(title="Backend Engineer", must_have=["Go", "PostgreSQL"])
    r = _sel_resume()
    assert "PHP" in select(r, jd)[1]
    r, done, refused, layout = apply(r, [{"type": "add_skill", "skill": "php"}], "add PHP back", "")
    assert r.skill_keep == ["PHP"] and layout and "PHP will show on the page." in done
    assert "PHP" in select(r, jd)[0]


def test_word_dropping_the_connection_is_retried_then_explained(monkeypatch):
    from app.export import render
    calls = []

    def flaky(src, out):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("(-2147023174, 'The RPC server is unavailable.')")
        out.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(render, "renderer_name", lambda choice="auto": "word")
    monkeypatch.setattr(render, "_word_convert", flaky)
    assert render.docx_to_pdf(b"x") == (b"%PDF-1.4", "word") and len(calls) == 2   # second try works

    def dead(src, out):
        raise OSError("RPC server is unavailable")
    monkeypatch.setattr(render, "_word_convert", dead)
    monkeypatch.setattr(render, "_soffice", lambda: None)
    with pytest.raises(render.RenderError, match="Word didn't answer"):
        render.docx_to_pdf(b"x")
