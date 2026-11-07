"""OCR engines (backend/ocr/engines.py): pdf_text reads PDFs that contain text without OCR; paddleocr_vl
reads photos and scans. Only invented values, made here; no real reports."""

import io
import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from backend.ocr import engines, parser
from backend.config import settings
from backend.database import SessionLocal, set_request_identity
from backend.main import app
from backend.models import Document, LabResult, Patient, User

client = TestClient(app)
PASSWORD = "correct horse battery"

# Invented values (test code -> value in its standard unit)
TRUE_VALUES = {"FPG": Decimal("96.000"), "HBA1C": Decimal("5.400"), "TOTAL_CHOLESTEROL": Decimal("185.000"),
               "HDL": Decimal("52.000"), "CREATININE": Decimal("0.900")}

REPORT_HTML = """<html><body style="font-family: sans-serif">
<h2>Sample Diagnostic Laboratory</h2>
<p>Patient: TEST PATIENT &nbsp; Date: 2026-01-15</p>
<table border="1" cellpadding="6">
<tr><th>Test</th><th>Result</th><th>Unit</th><th>Reference range</th></tr>
<tr><td>Fasting Blood Sugar</td><td>96</td><td>mg/dL</td><td>70 - 100</td></tr>
<tr><td>HbA1c</td><td>5.4</td><td>%</td><td>4.0 - 5.6</td></tr>
<tr><td>Total Cholesterol</td><td>185</td><td>mg/dL</td><td>&lt; 200</td></tr>
<tr><td>HDL Cholesterol</td><td>52</td><td>mg/dL</td><td>40 - 60</td></tr>
<tr><td>Creatinine</td><td>79.6</td><td>umol/L</td><td>62 - 106</td></tr>
</table></body></html>"""


@pytest.fixture(scope="module")
def text_pdf():
    from weasyprint import HTML

    return HTML(string=REPORT_HTML).write_pdf()


def scanned_pdf(pages=1):
    """A PDF that is only a picture (like a scan): no text layer."""
    from PIL import Image

    out = io.BytesIO()
    images = [Image.new("RGB", (400, 300), "white") for _ in range(pages)]
    images[0].save(out, "PDF", save_all=True, append_images=images[1:])
    return out.getvalue()


def values_of(text):
    return {mapped.test_code: mapped.value for mapped in parser.extract(text).values}


# ---------------------------------------------------------------------------
# pdf_text
# ---------------------------------------------------------------------------
def test_a_text_pdf_is_read_without_ocr(text_pdf):
    result = engines.PdfTextEngine().read(text_pdf, "application/pdf")
    assert values_of(result.text) == TRUE_VALUES  # creatinine converted from umol/L
    assert result.confidence is None  # exact text, not an OCR guess


def test_a_scanned_pdf_has_no_text_layer():
    assert engines.pdf_text_layer(scanned_pdf()) == ""
    with pytest.raises(engines.OcrFailed, match="no text to read"):
        engines.PdfTextEngine().read(scanned_pdf(), "application/pdf")


def test_pdf_text_does_not_read_photos():
    with pytest.raises(engines.OcrFailed, match="no text to read"):
        engines.PdfTextEngine().read(b"\x89PNG\r\n\x1a\n", "image/png")


def test_a_damaged_or_very_long_pdf_is_refused_with_a_safe_message():
    with pytest.raises(engines.OcrFailed, match="could not be opened"):
        engines.PdfTextEngine().read(b"%PDF-1.4\nnot really a pdf", "application/pdf")
    with pytest.raises(engines.OcrFailed, match="more than 10 pages"):
        engines.PdfTextEngine().read(scanned_pdf(pages=11), "application/pdf")


def test_letterhead_text_alone_is_not_a_usable_text_layer():
    assert engines.has_lab_values("| Fasting Blood Sugar | 96 | mg/dL |")
    assert not engines.has_lab_values("Sample Diagnostic Laboratory\nPatient: TEST PATIENT")
    assert not engines.has_lab_values("")


# ---------------------------------------------------------------------------
# PaddleOCR-VL output -> parser rows (no paddle needed)
# ---------------------------------------------------------------------------
def test_html_tables_become_rows_the_parser_reads():
    markdown = ("## Sample Diagnostic Laboratory\n\nPatient: TEST\n\n"
                "<table><tr><td>Test</td><td>Result</td><td>Unit</td></tr>"
                "<tr><td>Fasting Blood Sugar</td><td>96</td><td>mg/dL</td></tr>"
                "<tr><td>Total<br>Cholesterol</td><td>4.78</td><td>mmol/L</td></tr>"
                "<tr><td>HbA1c</td><td>5.4 H</td><td>%</td></tr></table>\n\nNote: &lt;0.1 is not detected")
    text = engines.tables_to_rows(markdown)
    assert "| Fasting Blood Sugar | 96 | mg/dL |" in text.splitlines()
    assert "Note: <0.1 is not detected" in text.splitlines()
    assert values_of(text) == {"FPG": Decimal("96.000"), "TOTAL_CHOLESTEROL": Decimal("184.843"),
                               "HBA1C": Decimal("5.400")}


def test_paddleocr_vl_latex_units_are_read_as_plain_text():
    """PaddleOCR-VL writes the micro sign and powers as LaTeX. Without turning them back, every
    umol/L and uIU/mL line (creatinine, TSH) was skipped on the step 4 check."""
    text = engines.latex_to_text(
        "| Creatinine | 88 | \\mu mol/L | 62 - 106 |  |\n"
        "| TSH | 2.15 | $ \\mu $IU/mL | 0.34 - 4.25 |  |\n"
        "| Eosinophils | 0.3 | $ \\times 10^{9} $/L |  |\n"
        "| eGFR | 95 | mL/min/1.73 m^{2} |  |\n"
        "| HbA1c | 5.9 | \\% |  |")
    assert "\\" not in text and "$" not in text
    assert values_of(text) == {"CREATININE": Decimal("0.995"), "TSH": Decimal("2.150"),
                               "EOSINOPHILS": Decimal("300.000"), "EGFR": Decimal("95.000"),
                               "HBA1C": Decimal("5.900")}


def test_paddleocr_vl_output_goes_through_the_latex_step(fake_pipeline, monkeypatch):
    monkeypatch.setattr(fake_pipeline, "predict", lambda page, **kwargs: [FakeResult(
        "<table><tr><td>Creatinine</td><td>88</td><td>\\mu mol/L</td></tr></table>")])
    result = engines.PaddleOcrVlEngine().read(scanned_pdf(), "application/pdf")
    assert values_of(result.text) == {"CREATININE": Decimal("0.995")}


class FakeResult:
    def __init__(self, text):
        self.markdown = {"markdown_texts": text}


class FakePipeline:
    def __init__(self):
        self.pages, self.kwargs = [], []

    def predict(self, page, **kwargs):
        self.pages.append(page)
        self.kwargs.append(kwargs)
        return [FakeResult("<table><tr><td>FBS</td><td>101</td><td>mg/dL</td></tr></table>")]


@pytest.fixture
def fake_pipeline(monkeypatch):
    pipeline = FakePipeline()
    monkeypatch.setattr(engines, "_get_pipeline", lambda: pipeline)
    return pipeline


def test_paddleocr_vl_reads_a_text_pdf_directly(text_pdf, fake_pipeline):
    result = engines.PaddleOcrVlEngine().read(text_pdf, "application/pdf")
    assert values_of(result.text) == TRUE_VALUES and fake_pipeline.pages == []  # model not used


def test_paddleocr_vl_reads_scans_and_photos_as_pictures(fake_pipeline):
    from PIL import Image

    result = engines.PaddleOcrVlEngine().read(scanned_pdf(pages=2), "application/pdf")
    assert values_of(result.text) == {"FPG": Decimal("101.000")}
    assert len(fake_pipeline.pages) == 2
    assert fake_pipeline.kwargs[0] == {"max_new_tokens": engines.MAX_NEW_TOKENS}  # runaway text is capped
    page = fake_pipeline.pages[0]
    assert page.shape == (834, 1112, 3)  # a 400 x 300 pt page at 200 dpi, colour

    photo = io.BytesIO()
    Image.new("RGB", (4000, 3000), (255, 0, 0)).save(photo, "JPEG")
    engines.PaddleOcrVlEngine().read(photo.getvalue(), "image/jpeg")
    page = fake_pipeline.pages[-1]
    assert max(page.shape[:2]) == engines.MAX_IMAGE_SIDE  # scaled down for the GPU
    blue, green, red = page[0, 0]
    assert red > 200 and blue < 50 and green < 50  # a red photo, in Paddle's BGR order


def test_paddleocr_vl_turns_a_photo_the_right_way_up(fake_pipeline):
    from PIL import Image

    exif = Image.Exif()
    exif[0x0112] = 6  # taken sideways
    photo = io.BytesIO()
    Image.new("RGB", (300, 100), "white").save(photo, "JPEG", exif=exif.tobytes())
    engines.PaddleOcrVlEngine().read(photo.getvalue(), "image/jpeg")
    assert fake_pipeline.pages[-1].shape[:2] == (300, 100)


def test_paddleocr_vl_with_nothing_found_asks_the_patient_to_type(monkeypatch):
    class Empty:
        def predict(self, page, **kwargs):
            return [FakeResult("")]

    monkeypatch.setattr(engines, "_get_pipeline", lambda: Empty())
    with pytest.raises(engines.OcrFailed, match="type the values in"):
        engines.PaddleOcrVlEngine().read(scanned_pdf(), "application/pdf")
    with pytest.raises(engines.OcrFailed, match="could not be opened"):
        engines.PaddleOcrVlEngine().read(b"\xff\xd8\xff\xe0broken", "image/jpeg")


def test_paddleocr_vl_without_paddle_stops_the_app_at_startup(monkeypatch):
    monkeypatch.setattr(settings, "ocr_engine", "paddleocr_vl")
    monkeypatch.setattr(engines.PaddleOcrVlEngine, "available", staticmethod(lambda: False))
    with pytest.raises(RuntimeError, match="needs its packages installed"):
        engines.check_engine()
    monkeypatch.setattr(settings, "ocr_engine", "pdf_text")
    engines.check_engine()  # nothing extra to install


# ---------------------------------------------------------------------------
# Worker with pdf_text, end to end
# ---------------------------------------------------------------------------
@pytest.fixture
def headers(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", tmp_path)
    email = f"ocr-eng-{uuid.uuid4().hex[:8]}@test.local"
    assert client.post("/auth/register", json={
        "email": email, "password": PASSWORD, "first_name": "Melchora", "last_name": "Aquino",
        "date_of_birth": "1980-01-06", "biological_sex": "Female",
        "privacy_consent": True, "consent_version": settings.privacy_notice_version,
    }).status_code == 201
    token = client.post("/auth/login", json={"email": email, "password": PASSWORD}).json()["access_token"]
    yield email, {"Authorization": f"Bearer {token}"}
    with SessionLocal() as session:
        session.execute(delete(User).where(func.lower(User.email) == email))
        session.commit()


def test_an_uploaded_portal_pdf_is_read_by_the_worker(headers, text_pdf, monkeypatch):
    email, auth = headers
    monkeypatch.setattr(settings, "ocr_engine", "pdf_text")
    response = client.post("/documents", headers=auth, files={"file": ("lab.pdf", text_pdf, "application/pdf")})
    assert response.status_code == 202
    document_id = response.json()["document_id"]
    with SessionLocal() as session:
        user_id = session.scalar(select(User.user_id).where(User.email == email))
        set_request_identity(session, user_id=user_id)
        patient_id = session.scalar(select(Patient.patient_id))
        set_request_identity(session, user_id=user_id, patient_id=patient_id)
        document = session.get(Document, document_id)
        assert (document.status, document.ocr_engine) == ("ready_for_review", "PDF text layer")
        saved = {row.test_code: row.value for row in session.scalars(
            select(LabResult).where(LabResult.document_id == document_id))}
    assert saved == TRUE_VALUES


def test_a_document_no_engine_has_read_says_none(headers):
    """database/22_ocr_engine_default.sql: not 'Chandra OCR', which never ran here."""
    email, auth = headers
    response = client.post("/documents", headers=auth, files={"file": ("lab.pdf", scanned_pdf(), "application/pdf")})
    assert response.status_code == 202  # OCR_ENGINE=none in tests: stays pending
    with SessionLocal() as session:
        user_id = session.scalar(select(User.user_id).where(User.email == email))
        set_request_identity(session, user_id=user_id)
        patient_id = session.scalar(select(Patient.patient_id))
        set_request_identity(session, user_id=user_id, patient_id=patient_id)
        assert session.get(Document, response.json()["document_id"]).ocr_engine == "none"


# ---------------------------------------------------------------------------
# The real PaddleOCR-VL model (only where it is installed: GPU machine, docs/ocr.md)
# ---------------------------------------------------------------------------
def test_the_real_paddleocr_vl_reads_a_rendered_report(text_pdf):
    # find_spec, not importorskip: importing paddle here, before the engine sets its memory flags,
    # would load the model without them (out of memory on a 6 GB GPU)
    if not engines.PaddleOcrVlEngine.available():
        pytest.skip("PaddleOCR-VL is not installed (docs/ocr.md)")
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(text_pdf)
    picture = io.BytesIO()
    pdf[0].render(scale=200 / 72).to_pil().save(picture, "PNG")  # the report as a screenshot
    pdf.close()
    result = engines.PaddleOcrVlEngine().read(picture.getvalue(), "image/png")
    assert values_of(result.text) == TRUE_VALUES
