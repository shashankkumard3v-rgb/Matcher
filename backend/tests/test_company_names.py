"""The same employer written differently is still the same job: the warning doesn't fire and your
notes for that job are used."""
from app.context import notes
from app.loop.bullets import candidates
from app.parsing.pipeline import cross_check_note_companies
from app.parsing.structure import company_matches
from app.schemas.models import Experience, JobDescription, Resume


def test_same_company_written_differently():
    same = [("Finlytics Pvt Ltd", "Finlytics Private Limited"), ("Finlytics Pvt. Ltd.", "Finlytics"),
            ("FinlyticsPvtLtd", "Finlytics Pvt Ltd"), ("Gruve AI", "Gruve.ai"), ("Gruve AI", "Gruve Inc."),
            ("Amazon Web Services", "AWS"), ("CartNest", "Cart Nest"), ("Brightloop Labs", "Brightloop"),
            ("Tata Consultancy Services", "TCS")]
    for a, b in same:
        assert company_matches(a, b), (a, b)
    for a, b in [("Finlytics", "CartNest"), ("Meta", "Metabase"), ("Google", "Goggle")]:
        assert not company_matches(a, b), (a, b)


def _resume():
    return Resume(experience=[
        Experience(title="Engineer", company="Finlytics Private Limited", start="2021-01", end="present",
                   bullets=["Built payout APIs"]),
        Experience(title="Engineer", company="CartNest", start="2019-01", end="2020-12", bullets=["Built orders"])])


def test_no_warning_for_a_differently_written_name_but_one_for_a_missing_job(monkeypatch):
    text = ("Company: Finlytics Pvt Ltd\n- Built a Kafka settlement pipeline handling 2 million events a day\n\n"
            "Company: Acme Robotics\n- Wrote firmware\n")
    monkeypatch.setattr(notes, "read_raw", lambda kind: text if kind == "experience" else "")
    warns = cross_check_note_companies(_resume())
    assert len(warns) == 1 and "Acme Robotics" in warns[0].message
    assert "Finlytics Private Limited, CartNest" in warns[0].message      # it says what IS on the resume


def test_notes_under_a_differently_written_name_are_used(monkeypatch):
    text = "Company: Finlytics Pvt Ltd\n- Built a Kafka settlement pipeline handling 2 million events a day\n"
    monkeypatch.setattr(notes, "read_raw", lambda kind: text if kind == "experience" else "")
    found = candidates(_resume(), JobDescription(must_have=["Kafka"]))
    assert [(c.job, c.text) for c in found] == [(0, "Built a Kafka settlement pipeline handling 2 million events a day")]
