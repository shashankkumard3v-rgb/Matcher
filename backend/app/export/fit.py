"""Find the look that fills exactly one A4 page.

Every setting (font sizes, spacing, margins) grows with one 'scale' number between the
compact and roomy limits in config/format_spec.yaml. Taller is monotonic in scale, so a
binary search finds the largest scale that still fits on one page.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from app.export.builder import Look, build_docx, format_spec
from app.export.render import Measure, docx_to_pdf, measure
from app.schemas.models import Resume

FITTED, OVERFLOW, UNDERFILLED = "fitted", "overflow", "underfilled"


@dataclass
class FitResult:
    status: str            # fitted | overflow | underfilled
    scale: float
    look: dict
    pages: int
    fill: float
    overflow_lines: int
    renderer: str
    renders: int
    docx: bytes
    pdf: bytes


def _try(resume: Resume, scale: float, spec: dict, renderer: str) -> tuple[Measure, bytes, bytes, str, Look]:
    look = Look.at(scale, spec)
    docx = build_docx(resume, look, spec)
    pdf, used = docx_to_pdf(docx, renderer)
    return measure(pdf, look.margin_cm), docx, pdf, used, look


def fit_one_page(resume: Resume, steps: int = 7) -> FitResult:
    spec = format_spec()
    renderer = spec.get("renderer", "auto")
    lo_fill = float(spec["fit"]["target_fill_min"])
    hi_fill = float(spec["fit"]["target_fill_max"])
    renders = 0

    def result(status, scale, m, docx, pdf, used, look):
        return FitResult(status=status, scale=round(scale, 3), look=asdict(look), pages=m.pages,
                         fill=m.fill, overflow_lines=m.overflow_lines, renderer=used,
                         renders=renders, docx=docx, pdf=pdf)

    # Roomiest look first: if it fits on one page, nothing needs shrinking.
    m, docx, pdf, used, look = _try(resume, 1.0, spec, renderer)
    renders += 1
    if m.pages == 1:
        if m.fill >= lo_fill:
            return result(FITTED, 1.0, m, docx, pdf, used, look)
        # Short resume: keep the roomy fonts and margins and spread the spacing (scale 1..2), so
        # the page fills evenly instead of leaving a big empty space at the bottom.
        best = (1.0, m, docx, pdf, used, look)
        m2, docx2, pdf2, used2, look2 = _try(resume, 2.0, spec, renderer)    # fully stretched
        renders += 1
        if m2.pages == 1 and m2.fill <= hi_fill:
            best = (2.0, m2, docx2, pdf2, used2, look2)
        else:                                       # too much: largest stretch that still fits
            lo, hi = 1.0, 2.0
            for _ in range(steps):
                mid = (lo + hi) / 2
                m2, docx2, pdf2, used2, look2 = _try(resume, mid, spec, renderer)
                renders += 1
                if m2.pages == 1 and m2.fill <= hi_fill:
                    best, lo = (mid, m2, docx2, pdf2, used2, look2), mid
                else:
                    hi = mid
        scale, m, docx, pdf, used, look = best
        return result(FITTED if m.fill >= lo_fill else UNDERFILLED, scale, m, docx, pdf, used, look)

    # Most compact look: if even this spills over, content must be cut.
    m0, docx0, pdf0, used0, look0 = _try(resume, 0.0, spec, renderer)
    renders += 1
    if m0.pages > 1:
        return result(OVERFLOW, 0.0, m0, docx0, pdf0, used0, look0)

    # Largest scale that still fits on one page.
    best = (0.0, m0, docx0, pdf0, used0, look0)
    lo, hi = 0.0, 1.0
    for _ in range(steps):
        mid = (lo + hi) / 2
        m, docx, pdf, used, look = _try(resume, mid, spec, renderer)
        renders += 1
        if m.pages == 1 and m.fill <= hi_fill:
            best, lo = (mid, m, docx, pdf, used, look), mid   # fits: try roomier
        else:
            hi = mid                                           # spills or too tight: shrink
    scale, m, docx, pdf, used, look = best
    status = FITTED if m.fill >= lo_fill else UNDERFILLED
    return result(status, scale, m, docx, pdf, used, look)
