"""Build the ATS-safe, single-column A4 Word resume.

ATS-safe means: one column, no tables, no text boxes, no images or icons, contact details
in the body (not the page header), standard section headings, real Word bullets.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from functools import lru_cache

import yaml
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from app import config
from app.scoring.dates import MONTHS
from app.schemas.models import Resume

A4_W_CM, A4_H_CM = 21.0, 29.7
LINK_LABELS = {
    "leetcode.com": "LeetCode", "linkedin.com": "LinkedIn", "github.com": "GitHub",
    "gitlab.com": "GitLab", "kaggle.com": "Kaggle", "medium.com": "Medium",
    "stackoverflow.com": "Stack Overflow", "codeforces.com": "Codeforces",
    "hackerrank.com": "HackerRank", "codechef.com": "CodeChef", "behance.net": "Behance",
    "dribbble.com": "Dribbble",
}


def normalize_url(url: str) -> str:
    u = url.strip()
    return u if u.lower().startswith(("http://", "https://", "mailto:")) else "https://" + u


def link_label(url: str) -> str:
    host = normalize_url(url).split("://", 1)[1].split("/", 1)[0].lower()
    host = host[4:] if host.startswith("www.") else host
    for domain, label in LINK_LABELS.items():
        if host == domain or host.endswith("." + domain):
            return label
    return host  # e.g. "priya.dev" for a personal site


def is_web_address(url: str) -> bool:
    """'linkedin.com/in/x' or 'https://...' yes; a bare label like 'LinkedIn' no (it would become
    a broken link to https://LinkedIn)."""
    host = normalize_url(url).split("://", 1)[1].split("/", 1)[0]
    return "." in host and " " not in host


def merge_links(*groups: list[str]) -> list[str]:
    seen, out = set(), []
    for group in groups:
        for url in group:
            if not is_web_address(url):
                continue
            key = normalize_url(url).split("://", 1)[1].rstrip("/").lower().removeprefix("www.")
            if key not in seen:
                seen.add(key)
                out.append(normalize_url(url))
    return out


def _add_hyperlink(paragraph, url: str, text: str, size_pt: float) -> None:
    """A real clickable link: blue, underlined text that opens url."""
    r_id = paragraph.part.relate_to(url, RT.HYPERLINK, is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), r_id)
    run = OxmlElement("w:r")
    r_pr = OxmlElement("w:rPr")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "0563C1")
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    size = OxmlElement("w:sz")
    size.set(qn("w:val"), str(int(round(size_pt * 2))))
    r_pr.extend([color, underline, size])
    run.append(r_pr)
    t = OxmlElement("w:t")
    t.text = text
    t.set(qn("xml:space"), "preserve")
    run.append(t)
    link.append(run)
    paragraph._p.append(link)
MONTH_NAMES = {v: k.capitalize() for k, v in MONTHS.items()}

# Spacing a short resume may grow to (see "stretch" in config/format_spec.yaml)
DEFAULT_STRETCH = {"line_spacing": 1.3, "para_after_pt": 6, "bullet_after_pt": 5, "section_before_pt": 18}


def stretch_limits(spec: dict) -> dict:
    """Only spacing may stretch; a limit below the roomy value is ignored."""
    roomy = spec["fit"]["roomy"]
    want = {**DEFAULT_STRETCH, **(spec["fit"].get("stretch") or {})}
    return {k: max(float(v), float(roomy[k])) for k, v in want.items() if k in DEFAULT_STRETCH and k in roomy}


def bullet_ends(bullets: list[str], mode: str = "auto") -> list[str]:
    """Every bullet ends the same way: with a full stop or without. 'auto' follows the majority
    (a tie means no full stop, the usual resume style). Other endings (?, !, ...) are kept."""
    items = [b.rstrip() for b in bullets]
    if mode not in ("none", "period"):
        with_dot = sum(b.endswith(".") and not b.endswith("..") for b in items)
        mode = "period" if with_dot > len(items) - with_dot else "none"
    out = []
    for b in items:
        ends_with_dot = b.endswith(".") and not b.endswith("..")
        if mode == "none" and ends_with_dot:
            b = b[:-1].rstrip()
        elif mode == "period" and b and (b[-1].isalnum() or b[-1] in ")%"):
            b += "."
        out.append(b)
    return out


@lru_cache
def format_spec() -> dict:
    return yaml.safe_load((config.CONFIG_DIR / "format_spec.yaml").read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Look:
    body_pt: float
    name_pt: float
    heading_pt: float
    margin_cm: float
    line_spacing: float
    para_after_pt: float
    bullet_after_pt: float
    section_before_pt: float

    @staticmethod
    def at(scale: float, spec: dict | None = None) -> "Look":
        """scale 0 = most compact allowed, 1 = roomiest allowed: everything grows with scale.
        1..2 = stretch for short resumes: fonts and margins stay roomy, only spacing grows."""
        spec = spec or format_spec()
        lo, hi = spec["fit"]["compact"], spec["fit"]["roomy"]
        if scale > 1.0:
            top = {**hi, **{k: v for k, v in stretch_limits(spec).items() if k in hi}}
            s = min(scale, 2.0) - 1.0
            vals = {k: round(hi[k] + (top[k] - hi[k]) * s, 3) for k in hi}
        else:
            s = max(scale, 0.0)
            vals = {k: round(lo[k] + (hi[k] - lo[k]) * s, 3) for k in lo}
        # snap font sizes to half points so Word shows clean sizes
        for k in ("body_pt", "name_pt", "heading_pt"):
            vals[k] = round(vals[k] * 2) / 2
        return Look(**vals)


def fmt_date(v: str | None) -> str:
    if not v:
        return ""
    v = v.strip()
    if v.lower() in ("present", "current", "now"):
        return "Present"
    parts = v.split("-")
    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
        return f"{MONTH_NAMES.get(int(parts[1]), parts[1])} {parts[0]}"
    return v


def _bottom_border(paragraph) -> None:
    p_pr = paragraph._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    for k, v in (("w:val", "single"), ("w:sz", "6"), ("w:space", "1"), ("w:color", "808080")):
        bottom.set(qn(k), v)
    borders.append(bottom)
    p_pr.append(borders)


def _set_font(style, family: str, size_pt: float, bold: bool | None = None) -> None:
    style.font.name = family
    style.font.size = Pt(size_pt)
    if bold is not None:
        style.font.bold = bold
    r_pr = style.element.get_or_add_rPr()
    fonts = r_pr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        r_pr.append(fonts)
    for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        fonts.set(qn(attr), family)


def _para_format(p, look: Look, after: float, before: float = 0) -> None:
    f = p.paragraph_format
    f.space_before = Pt(before)
    f.space_after = Pt(after)
    f.line_spacing = look.line_spacing


def achievements_of(resume: Resume, spec: dict) -> list[str]:
    items = list(resume.achievements)
    if spec.get("achievements_include_certifications", True):
        have = {x.lower() for x in items}
        items += [c for c in resume.certifications if c.lower() not in have]
    return items


def build_docx(resume: Resume, look: Look, spec: dict | None = None) -> bytes:
    spec = spec or format_spec()
    family = spec.get("font", {}).get("family", "Calibri")
    heads = spec.get("headings", {})
    doc = Document()

    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(A4_W_CM), Cm(A4_H_CM)
    for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(sec, side, Cm(look.margin_cm))
    usable_cm = A4_W_CM - 2 * look.margin_cm

    _set_font(doc.styles["Normal"], family, look.body_pt)
    _set_font(doc.styles["List Bullet"], family, look.body_pt)
    doc.styles["Normal"].paragraph_format.space_after = Pt(0)

    # Section headings use Word's real "Heading 1" style (parsers and screen readers use it to
    # find sections), restyled to plain black text so it looks the same as before.
    h1 = doc.styles["Heading 1"]
    _set_font(h1, family, look.heading_pt, bold=True)
    h1.font.color.rgb = RGBColor(0, 0, 0)
    h1.font.italic = False
    h1.paragraph_format.keep_with_next = True
    r_pr = h1.element.get_or_add_rPr()
    for el in r_pr.findall(qn("w:rFonts")):
        for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
            el.attrib.pop(qn(attr), None)

    def heading(text: str) -> None:
        p = doc.add_paragraph(style="Heading 1")
        _para_format(p, look, after=2, before=look.section_before_pt)
        run = p.add_run(text)
        run.bold, run.font.size = True, Pt(look.heading_pt)
        run.font.color.rgb = RGBColor(0, 0, 0)
        _bottom_border(p)

    every = [b for j in resume.experience for b in j.bullets] + achievements_of(resume, spec)
    if "certifications" in spec.get("sections", []):
        every += resume.certifications
    ending = dict(zip(every, bullet_ends(every, spec.get("bullet_end", "auto"))))

    def bullet(text: str) -> None:
        p = doc.add_paragraph(ending.get(text, text), style="List Bullet")
        _para_format(p, look, after=look.bullet_after_pt)

    # Document properties (what Word shows under File > Info; python-docx's defaults say "python-docx")
    c = resume.contact
    props = doc.core_properties
    props.title = f"{c.name} - Resume" if c.name else "Resume"
    props.author = props.last_modified_by = c.name or ""
    props.subject, props.keywords, props.comments, props.category = "Resume", "", "", ""

    # Contact block (in the body, not the page header: ATS can read it)
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _para_format(p, look, after=1)
    run = p.add_run(c.name or "")
    run.bold, run.font.size = True, Pt(look.name_pt)
    plain = [x for x in [c.email, c.phone, c.location] if x]
    links = merge_links(c.links)
    if plain or links:
        p = doc.add_paragraph(" | ".join(plain))
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _para_format(p, look, after=look.para_after_pt)
        style = spec.get("link_text", "label")
        for i, url in enumerate(links):  # "LeetCode", "LinkedIn"... each clickable
            if plain or i:
                p.add_run(" | ")
            short = url.split("://", 1)[1].removeprefix("www.").rstrip("/")
            text = {"url": short, "label_url": f"{link_label(url)}: {short}"}.get(style, link_label(url))
            _add_hyperlink(p, url, text, look.body_pt)

    for section in spec.get("sections", ["summary", "skills", "experience", "achievements"]):
        if section == "summary" and resume.summary:
            heading(heads.get("summary", "SUMMARY"))
            p = doc.add_paragraph(resume.summary)
            _para_format(p, look, after=look.para_after_pt)
        elif section == "skills" and resume.skills:
            heading(heads.get("skills", "SKILLS"))
            p = doc.add_paragraph(", ".join(resume.skills))
            _para_format(p, look, after=look.para_after_pt)
        elif section == "experience" and resume.experience:
            heading(heads.get("experience", "EXPERIENCE"))
            for i, job in enumerate(resume.experience):
                p = doc.add_paragraph()
                _para_format(p, look, after=1, before=0 if i == 0 else look.para_after_pt + 2)
                p.paragraph_format.tab_stops.add_tab_stop(Cm(usable_cm), WD_TAB_ALIGNMENT.RIGHT)
                p.add_run(job.title or "").bold = True
                if job.company:
                    p.add_run(f" | {job.company}")
                dates = " - ".join(x for x in (fmt_date(job.start), fmt_date(job.end)) if x)
                if dates:
                    p.add_run("\t" + dates)
                for b in job.bullets:
                    bullet(b)
        elif section == "achievements":
            items = achievements_of(resume, spec)
            if items:
                heading(heads.get("achievements", "ACHIEVEMENTS"))
                for a in items:
                    bullet(a)
        elif section == "education" and resume.education:
            heading(heads.get("education", "EDUCATION"))
            for e in resume.education:
                p = doc.add_paragraph()
                _para_format(p, look, after=look.para_after_pt)
                p.paragraph_format.tab_stops.add_tab_stop(Cm(usable_cm), WD_TAB_ALIGNMENT.RIGHT)
                p.add_run(" - ".join(x for x in (e.degree, e.institution) if x)).bold = True
                if e.end:
                    p.add_run("\t" + fmt_date(e.end))
        elif section == "certifications" and resume.certifications:
            heading(heads.get("certifications", "CERTIFICATIONS"))
            for x in resume.certifications:
                bullet(x)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
