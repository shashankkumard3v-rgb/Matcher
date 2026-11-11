import pytest

from app.parsing.extract import (
    ALLOWED_JD_TYPES,
    ALLOWED_RESUME_TYPES,
    ExtractionError,
    extract_file,
)
from tests.conftest import RESUME_LINES, make_blank_pdf, make_docx, make_pdf


def test_pdf_text_extracted(resume_pdf):
    x = extract_file("cv.pdf", resume_pdf, ALLOWED_RESUME_TYPES)
    assert "FastAPI serving 2M requests per day" in x.text
    assert not any(code == "two_column_layout" for code, _ in x.warnings)


def test_scanned_pdf_rejected():
    with pytest.raises(ExtractionError) as e:
        extract_file("scan.pdf", make_blank_pdf(), ALLOWED_RESUME_TYPES)
    assert e.value.code == "scanned_pdf"


def test_two_column_pdf_warns():
    x = extract_file("cv.pdf", make_pdf(RESUME_LINES * 3, two_column=True), ALLOWED_RESUME_TYPES)
    assert any(code == "two_column_layout" for code, _ in x.warnings)


def test_docx_jd_includes_tables(jd_docx):
    x = extract_file("jd.docx", jd_docx, ALLOWED_JD_TYPES)
    assert "Kafka" in x.text and "Bengaluru (hybrid)" in x.text


def test_resume_must_be_pdf(jd_docx):
    with pytest.raises(ExtractionError) as e:
        extract_file("cv.docx", jd_docx, ALLOWED_RESUME_TYPES)
    assert e.value.code == "unsupported_type"


def test_jd_rejects_other_types():
    with pytest.raises(ExtractionError) as e:
        extract_file("jd.txt", b"some text", ALLOWED_JD_TYPES)
    assert e.value.code == "unsupported_type"


def test_empty_docx_rejected():
    with pytest.raises(ExtractionError) as e:
        extract_file("jd.docx", make_docx(["hi"]), ALLOWED_JD_TYPES)
    assert e.value.code == "empty_docx"


def test_right_aligned_dates_are_not_two_columns():
    import io
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    y = h - 60
    for i in range(30):
        c.drawString(50, y, f"Software Engineer at Company {i} building things with Python and APIs")
        c.drawRightString(w - 50, y, "Apr 2021 - Present")
        y -= 18
    c.save()
    x = extract_file("cv.pdf", buf.getvalue(), ALLOWED_RESUME_TYPES)
    assert not any(code == "two_column_layout" for code, _ in x.warnings)
