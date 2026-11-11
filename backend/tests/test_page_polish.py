"""The page looks finished: consistent bullet endings, no broken links, short resumes fill the page."""
import io

from docx import Document

from app.export.builder import Look, build_docx, bullet_ends, format_spec, is_web_address, merge_links
from app.parsing.extract import extract_docx
from app.schemas.models import Contact, Experience, Resume


def test_bullet_endings_follow_the_majority():
    assert bullet_ends(["Cut cost by 40%.", "Built X", "Led Y."]) == ["Cut cost by 40%.", "Built X.", "Led Y."]
    assert bullet_ends(["Built X.", "Led Y", "Ran Z"]) == ["Built X", "Led Y", "Ran Z"]
    assert bullet_ends(["Built X.", "Led Y"]) == ["Built X", "Led Y"]          # a tie: no full stops
    assert bullet_ends(["Wait..."], "none") == ["Wait..."]                     # ellipses are left alone


def test_label_only_links_are_never_broken_links():
    assert not is_web_address("LinkedIn") and is_web_address("linkedin.com/in/x")
    assert merge_links(["LinkedIn", "github.com/a"]) == ["https://github.com/a"]


def _resume(links):
    return Resume(contact=Contact(name="A B", email="a@b.co", links=links),
                  experience=[Experience(title="Engineer", company="Acme", start="2020-01", end="present",
                                  bullets=["Built the payments API.", "Cut latency by 40%", "Led 3 reviews"])])


def test_built_page_uses_consistent_endings_and_skips_label_links():
    docx = build_docx(_resume(["LinkedIn", "github.com/ab"]), Look.at(1.0))
    doc = Document(io.BytesIO(docx))
    bullets = [p.text for p in doc.paragraphs if p.style.name == "List Bullet"]
    assert bullets == ["Built the payments API", "Cut latency by 40%", "Led 3 reviews"]
    targets = [r.target_ref for r in doc.part.rels.values() if "hyperlink" in r.reltype]
    assert targets == ["https://github.com/ab"]                                # no https://LinkedIn


def test_link_addresses_survive_reading_an_exported_file():
    docx = build_docx(_resume(["linkedin.com/in/ab", "leetcode.com/u/ab"]), Look.at(1.0))
    text = extract_docx(docx).text
    assert "https://linkedin.com/in/ab" in text and "https://leetcode.com/u/ab" in text


def test_stretch_only_grows_spacing():
    spec = format_spec()
    roomy, stretched = Look.at(1.0, spec), Look.at(2.0, spec)
    assert (stretched.body_pt, stretched.name_pt, stretched.margin_cm) == (roomy.body_pt, roomy.name_pt, roomy.margin_cm)
    assert stretched.section_before_pt > roomy.section_before_pt and stretched.line_spacing > roomy.line_spacing
    assert Look.at(1.5, spec).bullet_after_pt < stretched.bullet_after_pt
