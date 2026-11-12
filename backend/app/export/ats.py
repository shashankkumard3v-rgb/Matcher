"""ATS check of the finished resume: reads the Word file AND the text a parser would pull out
of it, and checks the rules applicant tracking systems care about.

Levels: pass | warn (works, but could cost you with some ATS) | fail (likely to break parsing)
         | info (a choice you made, explained).
"""
from __future__ import annotations

import io
import re

import pdfplumber
from docx import Document
from docx.oxml.ns import qn
from app.export.builder import fmt_date
from app.parsing.structure import _norm, found_in_source
from app.schemas.models import AtsCheck, Resume

SAFE_FONTS = {"calibri", "carlito", "arial", "helvetica", "liberation sans", "georgia", "cambria",
              "caladea", "times new roman", "garamond", "verdana", "tahoma", "aptos"}
_DATE = re.compile(r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{4}$|^Present$")
_ODD = re.compile(r"[←-⇿☀-➿\U0001f300-\U0001faff■-◿-]")
_BULLET_GLYPHS = {"\uf0b7", "\uf0a7", "\uf076", "\uf0d8", "\uf0fc"}  # how Word's own bullets come out of a PDF
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _ok(i, label, detail=""):
    return AtsCheck(id=i, label=label, level="pass", detail=detail)


def pdf_text(pdf: bytes) -> tuple[str, int]:
    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        from app.parsing.extract import page_text      # same reader as uploads (keeps word spaces)
        return "\n".join(page_text(p) for p in doc.pages), len(doc.pages)


def check(docx_bytes: bytes, pdf_bytes: bytes, resume: Resume, spec: dict, look: dict,
          word_cap: int = 32) -> list[AtsCheck]:
    out: list[AtsCheck] = []
    doc = Document(io.BytesIO(docx_bytes))
    body = doc.element.body
    xml = body.xml

    # 1. layout: one column, no tables / text boxes / images
    problems = []
    if doc.tables:
        problems.append(f"{len(doc.tables)} table(s)")
    if "txbxContent" in xml:
        problems.append("text boxes")
    if "<w:drawing" in xml or "<w:pict" in xml:
        problems.append("images or shapes")
    cols = [c.get(qn("w:num")) for c in body.iter(qn("w:cols"))]
    if any(c and int(c) > 1 for c in cols):
        problems.append("multiple columns")
    out.append(_ok("layout", "One column, no tables, text boxes or images") if not problems else
               AtsCheck(id="layout", label="Simple one-column layout", level="fail",
                        detail="Found " + ", ".join(problems) + ". Many ATS scramble or skip these."))

    # 2. nothing important in the page header/footer
    hf = " ".join(p.text for s in doc.sections for part in (s.header, s.footer) for p in part.paragraphs).strip()
    out.append(_ok("header_footer", "Contact details in the page body, not the header/footer") if not hf else
               AtsCheck(id="header_footer", label="Page header/footer is empty", level="fail",
                        detail=f"Header/footer contains \"{hf[:60]}\". Many ATS ignore headers and footers."))

    # 3. what a parser actually reads
    text, pages = pdf_text(pdf_bytes)
    norm = _norm(text)
    out.append(_ok("pages", "Exactly one page") if pages == 1 else
               AtsCheck(id="pages", label="Exactly one page", level="warn", detail=f"{pages} pages."))
    if "(cid:" in text or len(norm) < 200:
        out.append(AtsCheck(id="text", label="Text can be extracted", level="fail",
                            detail="The text came out garbled or empty, so an ATS would read nothing."))
    else:
        out.append(_ok("text", "Text extracts cleanly", f"{len(text.split())} words read back from the file"))

    # 4. contact
    c = resume.contact
    head = _norm(" ".join(text.splitlines()[:4]))
    miss = [k for k, v in (("name", c.name), ("email", c.email), ("phone", c.phone)) if v and _norm(v) not in head]
    if not c.email or not _EMAIL.search(text):
        miss.append("a readable email")
    out.append(_ok("contact", "Name, email and phone at the top, as plain text") if not miss else
               AtsCheck(id="contact", label="Contact details readable at the top", level="fail",
                        detail="Not found at the top: " + ", ".join(miss) + "."))

    # 5. standard section headings, in order
    heads = spec.get("headings", {})
    wanted = [heads.get(s, s.upper()) for s in spec.get("sections", [])
              if (s == "summary" and resume.summary) or (s == "skills" and resume.skills)
              or (s == "experience" and resume.experience) or s == "achievements"
              and (resume.achievements or resume.certifications) or (s == "education" and resume.education)]
    lines = [ln.strip() for ln in text.splitlines()]
    pos = [lines.index(h) if h in lines else -1 for h in wanted]
    if -1 in pos:
        out.append(AtsCheck(id="headings", label="Standard section headings", level="fail",
                            detail="Missing as its own line: " + ", ".join(h for h, p in zip(wanted, pos) if p < 0)))
    elif pos != sorted(pos):
        out.append(AtsCheck(id="headings", label="Standard section headings", level="warn",
                            detail="Headings are read back out of order."))
    else:
        out.append(_ok("headings", "Standard section headings (" + ", ".join(wanted) + ")"))
    if "education" not in spec.get("sections", []) and resume.education:
        out.append(AtsCheck(id="education", label="Education section", level="info",
                            detail="Your format has no EDUCATION heading (degrees go under Achievements). "
                                   "Some ATS filter on degree and look for that heading; add \"education\" to "
                                   "sections in config/format_spec.yaml if a job asks for a degree."))

    # 6. jobs: title, company and dates read back; consistent dates
    lost, bad_dates = [], []
    for j in resume.experience:
        for v in (j.title, j.company):
            if v and _norm(v) not in norm:
                lost.append(v)
        for d in (fmt_date(j.start), fmt_date(j.end)):
            if not d or not _DATE.match(d):
                bad_dates.append(f"{j.company or j.title}: '{d or 'missing'}'")
    out.append(_ok("jobs", "Every job title and company is readable") if not lost else
               AtsCheck(id="jobs", label="Job titles and companies readable", level="fail",
                        detail="Not read back: " + ", ".join(lost)))
    out.append(_ok("dates", "Dates in one format (e.g. Apr 2021 - Present)") if not bad_dates else
               AtsCheck(id="dates", label="Consistent job dates", level="warn",
                        detail="ATS compute your years of experience from these: " + "; ".join(bad_dates)))

    # 7. bullets: real Word bullets, all read back, not too long
    bullets = [b for j in resume.experience for b in j.bullets]
    unread = [b for b in bullets if not found_in_source(b, norm)]
    styles = {p.style.name for p in doc.paragraphs if p.text.strip() in set(bullets)}
    if unread:
        out.append(AtsCheck(id="bullets", label="All bullets readable", level="fail",
                            detail=f"{len(unread)} bullet(s) not read back, e.g. \"{unread[0][:60]}\""))
    elif styles and styles != {"List Bullet"}:
        out.append(AtsCheck(id="bullets", label="Real Word bullets", level="warn",
                            detail="Some bullets aren't Word list bullets."))
    else:
        out.append(_ok("bullets", f"All {len(bullets)} bullets are real Word bullets and read back"))
    long_ = [b for b in bullets if len(b.split()) > word_cap]
    if long_:
        out.append(AtsCheck(id="bullet_length", label="Bullets short enough to scan", level="warn",
                            detail=f"{len(long_)} bullet(s) over {word_cap} words."))

    # 8. fonts and sizes
    fonts = set()
    for para in doc.paragraphs:  # only styles actually used (Word's template has many unused ones)
        st = para.style
        while st is not None:
            if st.font is not None and st.font.name:
                fonts.add(st.font.name.lower())
            st = st.base_style
    for r in body.iter(qn("w:rFonts")):
        v = r.get(qn("w:ascii"))
        if v:
            fonts.add(v.lower())
    odd_fonts = sorted(f for f in fonts if f not in SAFE_FONTS)
    small = look.get("body_pt", 11) < 10
    out.append(_ok("fonts", f"Standard font, {look.get('body_pt')} pt body text") if not odd_fonts and not small else
               AtsCheck(id="fonts", label="Standard fonts, 10 pt or more", level="warn",
                        detail=("Unusual fonts: " + ", ".join(odd_fonts) + ". ") * bool(odd_fonts)
                        + ("Body text under 10 pt." if small else "")))

    # 9. symbols that parsers mangle
    odd = sorted(set(_ODD.findall(text)) - _BULLET_GLYPHS)
    out.append(_ok("symbols", "No icons, emoji or special symbols") if not odd else
               AtsCheck(id="symbols", label="No icons or special symbols", level="warn",
                        detail="Found: " + " ".join(odd)))

    # 10. links
    style = spec.get("link_text", "label")
    from app.export.builder import is_web_address
    no_address = [x for x in c.links if not is_web_address(x)]
    if no_address:
        out.append(AtsCheck(id="links_missing", label="Profile links have a web address", level="warn",
                            detail=f"Left off the page because only the name was saved, not the address: "
                                   f"{', '.join(no_address)}. This happens when a resume is read from a PDF whose "
                                   "links were only clickable text. Add the resume again under My info > Resumes "
                                   "(Matcher now reads link addresses) to get them back."))
    if c.links and style == "label" and len(no_address) < len(c.links):
        out.append(AtsCheck(id="links", label="Profile links", level="info",
                            detail="Links show as labels (\"LinkedIn\") and are clickable. Some ATS keep only the "
                                   "visible text, so the address may be lost. Set link_text: label_url in "
                                   "config/format_spec.yaml to show \"LinkedIn: linkedin.com/in/...\" instead."))
    elif c.links and len(no_address) < len(c.links):
        out.append(_ok("links", "Profile links are clickable and show the address"))

    # 11. file properties
    p = doc.core_properties
    out.append(_ok("properties", "File title and author set to your name") if p.author and p.author != "python-docx" else
               AtsCheck(id="properties", label="File properties", level="warn",
                        detail="The file's author isn't your name."))
    return out
