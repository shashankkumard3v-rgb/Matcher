"""Deterministic text extraction from PDF and DOCX files (no LLM)."""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber
from docx import Document

ALLOWED_JD_TYPES = {".pdf", ".docx"}
ALLOWED_RESUME_TYPES = {".pdf"}

# A real text page has hundreds of characters; scanned pages have ~0.
MIN_CHARS_PER_PAGE = 80


class ExtractionError(ValueError):
    """Raised when a file can't be used at all (wrong type, scanned, empty)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class Extracted:
    text: str
    pages: int = 1
    warnings: list[tuple[str, str]] = field(default_factory=list)  # (code, message)


def _clean(text: str) -> str:
    text = text.replace(" ", " ").replace("•", "-").replace("", "-")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _looks_two_column(page) -> bool:
    """Heuristic: many lines start near the page middle -> probably two columns."""
    words = page.extract_words()
    if len(words) < 40:
        return False
    lines: dict[int, list[dict]] = {}
    for w in words:
        lines.setdefault(round(w["top"] / 3), []).append(w)
    lo, hi = page.width * 0.35, page.width * 0.65
    split_lines = 0
    for ws in lines.values():
        ws.sort(key=lambda w: w["x0"])
        for prev, cur in zip(ws, ws[1:]):
            # a wide blank gap that ends in the middle band = a column boundary.
            # (right-aligned dates end near the right edge, so they don't count)
            if cur["x0"] - prev["x1"] > 25 and lo <= cur["x0"] <= hi:
                split_lines += 1
                break
    return split_lines >= 8 and split_lines >= 0.3 * len(lines)


def _web_links(urls) -> str:
    """Clickable links hide their web address behind the visible text ("LinkedIn"), so the text
    alone loses it. They're added at the end as one line the parser can read."""
    seen, out = set(), []
    for u in urls:
        u = (u or "").strip()
        if not u.lower().startswith(("http://", "https://")):
            continue                                   # mailto:, tel:, page jumps
        key = u.rstrip("/").lower()
        if key not in seen:
            seen.add(key)
            out.append(u)
    return ("\n\nLinks: " + " , ".join(out)) if out else ""


# Many PDFs (LaTeX/Overleaf, Canva, online resume builders) contain no space characters: words
# are just placed with a small gap. pdfplumber's default joins letters closer than 3 pt, and a
# word gap at 10 pt is only ~2.5 pt, so "Designed and built" came out as "Designedandbuilt".
# Measuring the gap relative to the letter size fixes that; each page is read both ways and the
# one that looks more like real text is kept (so PDFs that were fine before stay exactly the same).
X_TOLERANCE_RATIO = 0.12
_LONG_WORD = re.compile(r"[A-Za-z]{19,}")          # run-together words ("Designedandbuilt...")
_LONE_LETTER = re.compile(r"(?<![\w.])[B-HJ-Zb-hj-z](?![\w.])")   # split words ("Kub e rnetes")


def _oddness(text: str) -> float:
    words = max(len(text.split()), 1)
    return (len(_LONG_WORD.findall(text)) * 3 + len(_LONE_LETTER.findall(text))) / words


def page_text(page) -> str:
    """The page's text, with spaces between words even when the PDF has no space characters."""
    plain = page.extract_text(layout=False) or ""
    try:
        spaced = page.extract_text(layout=False, x_tolerance_ratio=X_TOLERANCE_RATIO) or ""
    except TypeError:          # an older pdfplumber without x_tolerance_ratio
        return plain
    return spaced if _oddness(spaced) < _oddness(plain) else plain


def extract_pdf(data: bytes) -> Extracted:
    warnings: list[tuple[str, str]] = []
    try:
        pdf = pdfplumber.open(io.BytesIO(data))
    except Exception as e:  # pdfminer raises many types
        raise ExtractionError("bad_pdf", f"Could not open PDF: {e}") from e
    with pdf:
        pages = pdf.pages
        texts = []
        two_col = False
        urls = []
        for p in pages:
            texts.append(page_text(p))
            two_col = two_col or _looks_two_column(p)
            try:
                urls += [h.get("uri") for h in p.hyperlinks]
            except Exception:  # an odd link annotation must not break reading the text
                pass
        text = _clean("\n\n".join(texts) + _web_links(urls))
        n = max(len(pages), 1)
    if len(text) < MIN_CHARS_PER_PAGE * n * 0.5:
        raise ExtractionError(
            "scanned_pdf",
            "This PDF has almost no selectable text (it looks scanned or image-only). "
            "Export it again as a text PDF from Word/Google Docs.",
        )
    if two_col:
        warnings.append((
            "two_column_layout",
            "This PDF looks like a two-column layout. Text order may be mixed up; "
            "check the parsed sections.",
        ))
    return Extracted(text=text, pages=n, warnings=warnings)


def extract_docx(data: bytes) -> Extracted:
    try:
        doc = Document(io.BytesIO(data))
    except Exception as e:
        raise ExtractionError("bad_docx", f"Could not open .docx: {e}") from e
    parts: list[str] = []
    for para in doc.paragraphs:
        if para.text.strip():
            parts.append(para.text)
    # JDs often put requirements in tables
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(dict.fromkeys(cells)))
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    urls = [r.target_ref for r in doc.part.rels.values() if r.reltype == RT.HYPERLINK and r.is_external]
    text = _clean("\n".join(parts) + _web_links(urls))
    if len(text) < 50:
        raise ExtractionError("empty_docx", "This .docx has no readable text.")
    return Extracted(text=text)


def extract_file(filename: str, data: bytes, allowed: set[str]) -> Extracted:
    ext = Path(filename or "").suffix.lower()
    if ext not in allowed:
        allowed_txt = " or ".join(sorted(allowed))
        raise ExtractionError(
            "unsupported_type", f"'{filename}' is not supported. Use {allowed_txt}."
        )
    if ext == ".pdf":
        return extract_pdf(data)
    return extract_docx(data)


MIN_JD_CHARS = 80
MAX_JD_CHARS = 60_000


def extract_text(text: str) -> Extracted:
    """A job description pasted as plain text."""
    cleaned = _clean(text or "")
    if len(cleaned) < MIN_JD_CHARS:
        raise ExtractionError("jd_too_short",
                              "The job description is too short. Paste the whole posting.")
    if len(cleaned) > MAX_JD_CHARS:
        raise ExtractionError("jd_too_long", "The job description is too long (60,000 characters max).")
    return Extracted(text=cleaned)
