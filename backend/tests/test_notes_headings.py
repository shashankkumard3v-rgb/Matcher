from app.context import notes

TEXT = """# a comment
## CartNest | Software Engineer | 2019-07 to 2021-03
- Built Django services for order management
Integrated 3 shipping partner APIs

## Achievements
LeetCode: 450+ problems solved
"""


def test_headings_carry_to_lines():
    f = notes.facts(TEXT)
    assert f[0] == "CartNest | Software Engineer | 2019-07 to 2021-03"
    assert "CartNest | Software Engineer | 2019-07 to 2021-03 | Built Django services for order management" in f
    assert "Achievements | LeetCode: 450+ problems solved" in f
    assert not any("comment" in x for x in f)
    assert notes.bare_lines(TEXT) == ["Built Django services for order management",
                                     "Integrated 3 shipping partner APIs", "LeetCode: 450+ problems solved"]


def test_old_style_lines_still_work():
    assert notes.facts("CartNest | Built X\n# note\n") == ["CartNest | Built X"]


def test_ceiling_puts_heading_lines_under_the_job(tmp_workspace, monkeypatch):
    from app.gate import ceiling
    from app.schemas.models import Experience, ProjectContext, Resume
    notes.save("experience", TEXT)
    notes.save("skills", "")
    r = Resume(experience=[Experience(company="CartNest", title="SWE", bullets=["Old bullet"])])
    out = ceiling.build_ceiling_resume(r, ProjectContext())
    bullets = out.experience[0].bullets
    assert "Integrated 3 shipping partner APIs" in bullets
    assert not any("Software Engineer | 2019" in b for b in bullets)
    assert any(p.name == "From your notes" and "LeetCode: 450+ problems solved" in p.bullets for p in out.projects)


PLAIN = """Company: Finlytics Pvt Ltd
Role: Software Engineer II
What I did:
- Built a Kafka consumer service

Section: Achievements
LeetCode 450+ problems
"""


def test_plain_text_headings():
    f = notes.facts(PLAIN)
    assert "Finlytics Pvt Ltd | Role: Software Engineer II" in f
    assert "Finlytics Pvt Ltd | Built a Kafka consumer service" in f
    assert "Achievements | LeetCode 450+ problems" in f
    assert notes.bare_lines(PLAIN)[0] == "Role: Software Engineer II"


def test_unfilled_placeholders_are_not_facts():
    from app import config
    for k in ("experience", "skills"):
        tpl = (config.TEMPLATES_DIR / f"{k}_template.txt").read_text(encoding="utf-8")
        assert notes.facts(tpl) == [], k
    f = notes.facts("Company: [Company name]\n- Built [thing] using Python\nCompany: Acme\n- Built a CLI\n")
    assert f == ["Acme", "Acme | Built a CLI"]


MINE = """Company: Acme Corp
Role: SWE
What I did:
- Built the billing API
- Cut costs by 20%

Company: Beta Ltd
What I did:
* Wrote scripts

Section: Achievements
- LeetCode 400+
"""

SKILLS = """Section: Languages
- Python: used at Acme Corp. Level: Strong
"""


def _prop(**kw):
    from app.schemas.models import ContextProposal, Project
    return ContextProposal(id="p1", source="free_text", user_text="x", action="add_project",
                           project=Project(id="x", name="Kafka work", **kw))


def test_answer_goes_under_the_existing_company_in_the_same_style(tmp_workspace):
    from app.context import updater
    notes.save("experience", MINE)
    notes.save("skills", SKILLS)
    updater.apply(_prop(employer="Acme", what_i_did=["Built Kafka consumers", "Built the billing API"],
                        results=["2M events/day"], tech=["Kafka", "Python"]))
    raw = notes.read_raw("experience")
    assert raw.count("Company: Acme Corp") == 1 and "Added by Matcher" not in raw
    acme = raw.split("Company: Beta Ltd")[0]
    # the short result fragment stays with its bullet instead of becoming a bullet of its own
    assert acme.rstrip().endswith("- Cut costs by 20%\n- Built Kafka consumers, result: 2M events/day")
    assert raw.count("Built the billing API") == 1          # duplicates skipped
    skills = notes.read_raw("skills")
    assert "Section: Other skills\n- Kafka: used at Acme" in skills and skills.count("Python") == 1


def test_bullet_style_is_copied_and_new_company_goes_before_sections(tmp_workspace):
    from app.context import updater
    notes.save("experience", MINE)
    updater.apply(_prop(employer="Beta Ltd", what_i_did=["Automated reports"]))
    assert "* Wrote scripts\n* Automated reports" in notes.read_raw("experience")
    updater.apply(_prop(employer="Gamma Inc", role="Intern", what_i_did=["Built a CLI"]))
    raw = notes.read_raw("experience")
    assert raw.index("Company: Gamma Inc\nRole: Intern\nWhat I did:\n- Built a CLI") < raw.index("Section: Achievements")


def test_profile_facts_go_to_achievements(tmp_workspace):
    from app.context import updater
    from app.schemas.models import ContextProposal, Profile
    notes.save("experience", MINE)
    updater.apply(ContextProposal(id="p", source="x", user_text="x", action="add_profile",
                                  profile=Profile(facts=["AWS Certified, 2024"], links=["https://github.com/me"])))
    raw = notes.read_raw("experience")
    assert "- LeetCode 400+\n- AWS Certified, 2024" in raw and "Section: Links\n- https://github.com/me" in raw
