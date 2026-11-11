import io
import json
import sys
from pathlib import Path

import pytest
from docx import Document
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402

RESUME_LINES = [
    "Priya Sharma",
    "priya@example.com | +91 98765 43210 | Bengaluru",
    "SUMMARY",
    "Backend engineer with 4 years building Python services.",
    "EXPERIENCE",
    "Software Engineer - Company A (Apr 2021 - Present)",
    "- Built REST APIs in FastAPI serving 2M requests per day",
    "- Migrated reporting jobs from cron scripts to Airflow",
    "Junior Developer - Company B (Jun 2019 - Mar 2021)",
    "- Maintained Django admin tools for the support team",
    "SKILLS",
    "Python, FastAPI, Django, PostgreSQL, Docker",
    "EDUCATION",
    "B.Tech Computer Science - XYZ University (2015 - 2019)",
]

JD_PARAS = [
    "Senior Backend Engineer - Acme Corp",
    "We are looking for a backend engineer with 4+ years of experience.",
    "Requirements: Python, Kafka, PostgreSQL, Kubernetes",
    "Nice to have: Go, Terraform",
]


def make_pdf(lines, two_column=False) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    y = h - 60
    for line in lines:
        c.drawString(50, y, line)
        if two_column:
            c.drawString(w / 2 + 10, y, "Right column text " + line[:20])
        y -= 18
    c.save()
    return buf.getvalue()


def make_blank_pdf() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.rect(50, 50, 200, 200)  # a drawing, no text (like a scanned page)
    c.save()
    return buf.getvalue()


def make_docx(paras, table_rows=None) -> bytes:
    d = Document()
    for p in paras:
        d.add_paragraph(p)
    if table_rows:
        t = d.add_table(rows=len(table_rows), cols=len(table_rows[0]))
        for i, row in enumerate(table_rows):
            for j, val in enumerate(row):
                t.cell(i, j).text = val
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


@pytest.fixture
def resume_pdf():
    return make_pdf(RESUME_LINES)


@pytest.fixture
def jd_docx():
    return make_docx(JD_PARAS, [["Location", "Bengaluru (hybrid)"]])


@pytest.fixture(autouse=True)
def _signed_in(request, monkeypatch):
    """Most tests act as one signed-in user whose files live in the usual (temp) folders.
    Tests marked @pytest.mark.real_auth exercise the real sign-in and per-user folders."""
    if request.node.get_closest_marker("real_auth"):
        yield
        return
    from fastapi.testclient import TestClient

    from app import config
    from app.auth import store as store_mod

    class AnyTokenStore(store_mod.MemoryStore):
        async def user_for_token(self, token):
            return store_mod.User(id="test-user", google_sub="sub", email="test@example.com", name="Test")

    store_mod.set_store(AnyTokenStore())
    monkeypatch.setattr(config, "set_current_user", lambda uid: config._current_user.set(None))
    orig = TestClient.__init__

    def init(self, *a, **k):
        k.setdefault("cookies", {"matcher_session": "test"})
        orig(self, *a, **k)

    monkeypatch.setattr(TestClient, "__init__", init)
    yield
    store_mod.set_store(None)


@pytest.fixture(autouse=True)
def _single_suggest_mode(monkeypatch):
    """Most loop tests were written for the one-call improver; split-mode tests opt in."""
    monkeypatch.setenv("MATCHER_SUGGEST_MODE", "single")


@pytest.fixture
def tmp_workspace(tmp_path, monkeypatch):
    """Point runs/ and context/ at a temp folder so tests never touch real data."""
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(config, "PROJECT_CONTEXT_FILE", tmp_path / "context" / "project_context.md")
    return tmp_path


class FakeLLM:
    """Returns canned JSON; can be told to invent a bullet to test verification."""

    def __init__(self, invent_bullet=False, score_reply=None, jd_languages=None,
                 invent_number=False):
        self.invent_bullet = invent_bullet
        self.jd_languages = jd_languages or []
        self.invent_number = invent_number
        self.score_reply = score_reply
        self.calls = []
        self.inputs = []
        self.models = []

    mode = "fake"
    last_model = "fake-model"

    async def run_skill(self, skill, user_input, model=None):
        self.calls.append(skill)
        self.inputs.append(user_input)
        self.models.append(model)
        if skill == "matcher-score":
            return self.score_reply or DEFAULT_SCORE_REPLY
        if skill == "matcher-update-context":
            return self._update_context(user_input)
        if skill == "matcher-improve":
            return self._improve(user_input)
        if skill == "matcher-fit":
            return self._fit(user_input)
        if skill == "matcher-profile-resume":
            return self._profile(user_input)
        if skill == "matcher-truth-check":
            items = json.loads(user_input.split("EDITS:\n", 1)[1])
            return {"results": [
                {"id": i["id"], "ok": "spearheaded" not in (i["new"] or "").lower(),
                 "issues": [] if "spearheaded" not in (i["new"] or "").lower()
                 else ["\"Spearheaded\" overstates the source."]} for i in items]}
        if skill == "matcher-parse-resume":
            bullets = [
                "Built REST APIs in FastAPI serving 2M requests per day",
                "Migrated reporting jobs from cron scripts to Airflow",
            ]
            if self.invent_bullet:
                bullets.append("Led a team of 12 engineers to rebuild the payments platform")
            return {
                "contact": {"name": "Priya Sharma", "email": "priya@example.com",
                            "phone": "+91 98765 43210", "location": "Bengaluru", "links": []},
                "summary": "Backend engineer with 4 years building Python services.",
                "experience": [
                    {"title": "Software Engineer", "company": "Company A", "location": None,
                     "start": "2021-04", "end": "present", "bullets": bullets},
                    {"title": "Junior Developer", "company": "Company B", "location": None,
                     "start": "2019-06", "end": "2021-03",
                     "bullets": ["Maintained Django admin tools for the support team"]},
                ],
                "skills": ["Python", "FastAPI", "Django", "PostgreSQL", "Docker"],
                "education": [{"degree": "B.Tech Computer Science", "institution": "XYZ University",
                               "start": "2015", "end": "2019", "details": None}],
                "certifications": [], "projects": [], "unparsed": [],
            }
        return {
            "languages": self.jd_languages,
            "title": "Senior Backend Engineer", "company": "Acme Corp", "seniority": "senior",
            "must_have": ["Python", "Kafka", "PostgreSQL", "Kubernetes"],
            "nice_to_have": ["Go", "Terraform"], "years_required": 4,
            "location": "Bengaluru (hybrid)", "work_auth": None, "domain": None,
            "responsibilities": [],
        }


    def _fit(self, user_input):
        """Stand-in for matcher-fit: shorten wordy bullets / cut, or add template facts."""
        layout = json.loads(user_input.split("LAYOUT:\n", 1)[1].split("\n\nGOAL:", 1)[0])
        resume = json.loads(user_input.split("RESUME:\n", 1)[1].split("\n\nTEMPLATE:", 1)[0])
        tpl = json.loads(user_input.split("TEMPLATE:\n", 1)[1].split("\n\nPROBLEMS:", 1)[0])
        changes = []
        if layout["status"] == "overflow":
            for job in resume["experience"]:
                for b in job["bullets"]:
                    short = b["text"].replace(" for the finance operations team", "")
                    if short != b["text"] and len(changes) < 10:
                        changes.append({"kind": "replace_bullet", "job": job["job"], "bullet": b["bullet"],
                                        "old": b["text"], "new": short, "fixes": ["fit:overflow"],
                                        "why": "Same facts, fewer words.",
                                        "sources": [{"type": "resume", "quote": b["text"]}]})
        else:
            for fact in tpl["profile"]["facts"]:
                changes.append({"kind": "add_achievement", "new": fact, "fixes": ["fit:underfill"],
                                "why": "Uses free space with a real achievement.",
                                "sources": [{"type": "template", "project_id": "profile", "quote": fact}]})
        return {"changes": changes}

    def _improve(self, user_input):
        """Stand-in for matcher-improve: good edits, one dishonest, one inflated."""
        if "FEEDBACK:" in user_input:
            return {"changes": [{
                "kind": "replace_bullet", "job": 0, "bullet": 0, "old": "x",
                "new": "Built FastAPI REST APIs serving 2M requests per day",
                "fixes": ["impact"], "why": "Shorter, per your feedback.",
                "sources": [{"type": "resume", "quote": "Built REST APIs in FastAPI serving 2M requests per day"}]}]}
        tpl = json.loads(user_input.split("TEMPLATE:\n", 1)[1].split("\n\nPROBLEMS:", 1)[0])
        changes = [
            {"kind": "replace_bullet", "job": 0, "bullet": 0, "old": "Built REST APIs in FastAPI serving 2M requests per day",
             "new": "Built REST APIs in FastAPI serving 2M requests per day for the order platform",
             "fixes": ["relevance"], "why": "Names what the APIs were for.",
             "sources": [{"type": "resume", "quote": "Built REST APIs in FastAPI serving 2M requests per day"}]},
            {"kind": "add_bullet", "job": 0, "new": "Led a team of 12 engineers to migrate services to Kubernetes",
             "fixes": ["must:kubernetes"], "why": "Shows Kubernetes.",
             "sources": [{"type": "resume", "quote": "Migrated reporting jobs from cron scripts to Airflow"}]},
            {"kind": "set_summary", "new": "Backend engineer building Python services with FastAPI and Airflow",
             "fixes": ["relevance"], "why": "Leads with the JD's stack.",
             "sources": [{"type": "resume", "quote": "Backend engineer with 4 years building Python services."},
                         {"type": "resume", "quote": "Built REST APIs in FastAPI"},
                         {"type": "resume", "quote": "Migrated reporting jobs from cron scripts to Airflow"}]},
            {"kind": "replace_bullet", "job": 0, "bullet": 1, "old": "Migrated reporting jobs from cron scripts to Airflow",
             "new": "Spearheaded migration of reporting jobs from cron scripts to Airflow",
             "fixes": ["seniority"], "why": "Stronger verb.",
             "sources": [{"type": "resume", "quote": "Migrated reporting jobs from cron scripts to Airflow"}]},
        ]
        for p in tpl["projects"]:
            if "Kafka" in p["tech"]:
                changes.insert(1, {
                    "kind": "add_bullet", "job": 0, "new": p["what_i_did"][0].replace("Yes, at Company A I built", "Built"),
                    "fixes": ["must:kafka"], "why": "Shows Kafka from your template.",
                    "sources": [{"type": "template", "project_id": p["id"], "quote": p["what_i_did"][0]}]})
        return {"changes": changes}

    def _profile(self, user_input):
        """Stand-in for the strict profile: rates what's in the text, plus one invented skill."""
        skills, caps = [], []
        for line in user_input.splitlines():
            b = line[2:] if line.startswith("- ") else line
            if "FastAPI" in b and b.startswith("Built"):
                skills.append({"name": "FastAPI", "level": 3, "quote": b})
                caps.append({"id": "backend_apis", "level": 3, "quote": b})
            if "Airflow" in b and b.startswith("Migrated"):
                skills.append({"name": "Airflow", "level": 3, "quote": b})
                caps.append({"id": "data_pipelines", "level": 3, "quote": b})
            if "Django" in b and b.startswith("Maintained"):
                skills.append({"name": "Django", "level": 2, "quote": b})
        skills.append({"name": "Rust", "level": 5, "quote": "Rewrote everything in Rust, 10x faster"})  # invented
        return {"skills": skills, "capabilities": caps}

    def _update_context(self, user_input):
        """Tiny stand-in for the update-context skill."""
        answer = user_input.split("ANSWER: ", 1)[1].split("\n", 1)[0]
        question = user_input.split("QUESTION: ", 1)[1].split("\n", 1)[0] if "QUESTION: " in user_input else ""
        if answer.lower().startswith("no"):
            return {"action": "none", "summary": "Nothing to add.", "missing_info": []}
        if "achievements to add" in question:
            return {"action": "add_profile", "profile": {"facts": [answer]},
                    "summary": "Adds an achievement.", "missing_info": []}
        if "Do you want to add" in question:
            lang = question.split("Do you want to add ", 1)[1].split("?", 1)[0]
            return {"action": "add_profile", "profile": {"languages": [f"{lang} ({answer.split()[-1]})"]},
                    "summary": f"Adds {lang}.", "missing_info": []}
        tech = [t for t in ("Kafka", "Kubernetes", "PostgreSQL") if t.lower() in answer.lower()]
        results = ["Cut consumer lag by 40%"] if "40%" in answer else []
        if self.invent_number:
            results.append("Saved $1000000 per year")
        return {"action": "add_project", "target_project_id": None,
                "project": {"name": f"{tech[0] if tech else 'Other'} work", "employer": "Company A",
                            "role": "Software Engineer", "start": "2023-01", "end": "2023-06",
                            "team_size": None, "problem": None, "what_i_did": [answer],
                            "tech": tech, "results": results, "notes": None},
                "summary": "Adds a project under Company A.", "missing_info": ["How many messages per day?"]}


DEFAULT_SCORE_REPLY = {"dimensions": [
    {"key": "experience_relevance", "level": 75, "reason": "Backend Python work.",
     "evidence": ["serving 2M requests per day"]},
    {"key": "impact_quantification", "level": 50, "reason": "One number.",
     "evidence": ["serving 2M requests per day"]},
    {"key": "seniority_scope", "level": 50, "reason": "Mid level.",
     "evidence": ["Software Engineer"]},
    {"key": "domain_fit", "level": 25, "reason": "Different domain.",
     "evidence": ["Maintained Django admin tools for the support team"]},
]}


@pytest.fixture
def fake_llm():
    return FakeLLM()


def dump(obj):
    return json.dumps(obj, indent=2)
