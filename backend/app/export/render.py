"""Render a .docx to PDF so the page can be measured.

On Windows, Microsoft Word (via docx2pdf) gives the exact layout you'll see.
LibreOffice is the fallback (uses Carlito, which has the same letter widths as Calibri).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

import pdfplumber

WINDOWS_SOFFICE = [
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
]


_WORD_LOCK = threading.Lock()
# LibreOffice: one shared profile (creating a new one for every render costs seconds, a lot on a
# small server) and one render at a time (two copies can't share a profile).
_LO_LOCK = threading.Lock()
_LO_PROFILE = Path(tempfile.gettempdir()) / "matcher-libreoffice-profile"


class RenderError(RuntimeError):
    pass


@dataclass
class Measure:
    pages: int
    fill: float          # how much of page 1's usable height is used (0..1+)
    overflow_lines: int  # text lines that spilled past page 1


def _soffice() -> str | None:
    found = shutil.which("soffice") or shutil.which("libreoffice")
    if found:
        return found
    return next((p for p in WINDOWS_SOFFICE if os.path.exists(p)), None)


def _word_available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import docx2pdf  # noqa: F401
        import win32com.client  # noqa: F401
        return True
    except ImportError:
        return False


def renderer_name(choice: str = "auto") -> str | None:
    if choice in ("auto", "word") and _word_available():
        return "word"
    if choice in ("auto", "libreoffice") and _soffice():
        return "libreoffice"
    return None


def docx_to_pdf(docx: bytes, choice: str = "auto") -> tuple[bytes, str]:
    name = renderer_name(choice)
    if name is None:
        raise RenderError(
            "No way to measure the page: install Microsoft Word (plus `pip install docx2pdf pywin32`) "
            "or LibreOffice (https://www.libreoffice.org).")
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "resume.docx"
        src.write_bytes(docx)
        out = Path(tmp) / "resume.pdf"
        if name == "word":
            from docx2pdf import convert
            with _WORD_LOCK:            # one Word conversion at a time
                import pythoncom        # Word is driven over COM; each thread must initialise it
                pythoncom.CoInitialize()
                try:
                    convert(str(src), str(out))
                finally:
                    pythoncom.CoUninitialize()
        else:
            cmd = [_soffice(), f"-env:UserInstallation=file:///{_LO_PROFILE.as_posix().lstrip('/')}",
                   "--headless", "--convert-to", "pdf", "--outdir", tmp, str(src)]
            with _LO_LOCK:
                r = subprocess.run(cmd, capture_output=True, timeout=180)
            if r.returncode != 0 or not out.exists():
                raise RenderError(f"LibreOffice failed: {r.stderr.decode(errors='ignore')[:300]}")
        return out.read_bytes(), name


def count_lines(page) -> int:
    """Text lines on a page. Words within 3pt vertically are one line (bold/normal/bullet
    glyphs sit at slightly different heights on the same line)."""
    n, last = 0, None
    for t in sorted(w["top"] for w in page.extract_words()):
        if last is None or t - last > 3:
            n += 1
        last = t
    return n


def measure(pdf: bytes, margin_cm: float) -> Measure:
    import io
    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        pages = len(doc.pages)
        first = doc.pages[0]
        margin_pt = margin_cm / 2.54 * 72
        usable_top, usable_bottom = margin_pt, float(first.height) - margin_pt
        words = first.extract_words()
        bottom = max((w["bottom"] for w in words), default=usable_top)
        fill = (bottom - usable_top) / (usable_bottom - usable_top)
        overflow = sum(count_lines(p) for p in doc.pages[1:])
    return Measure(pages=pages, fill=round(fill, 3), overflow_lines=overflow)
