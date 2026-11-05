"""OCR engines. The worker only needs read(); which engine runs is set by OCR_ENGINE in .env.

- "pdf_text": PDFs that already contain text (lab portal PDFs) are read directly, no OCR and no
  GPU. Photos and scanned PDFs are refused with a safe message (the patient types the values in).
- "paddleocr_vl": PaddleOCR-VL for photos and scanned PDFs (GPU). A PDF with a usable text layer
  is still read directly (faster and exact); only scans go through the model. Needs the GPU
  install step in docs/ocr.md; paddle is imported only when this engine runs, so the app and the
  tests start without it.

Engines raise OcrFailed with a message that is safe to show and store. Any other exception is
caught by the worker, which logs only its type: engine error text and document text are never
logged (health data).
"""

import html.parser
import importlib.util
import threading
from dataclasses import dataclass
from typing import Protocol

from backend import ocr_parser
from backend.config import settings


@dataclass(frozen=True)
class OcrResult:
    text: str
    confidence: float | None = None  # 0-100, if the engine gives one


class OcrFailed(Exception):
    """Raised by an engine with a message that is safe to show and store (no health data)."""


class OcrEngine(Protocol):
    name: str  # stored in documents.ocr_engine (max 50 characters)

    def read(self, data: bytes, mime_type: str) -> OcrResult: ...


_NEEDS_OCR = "This file has no text to read. Please type the values in."
_PDF_DAMAGED = "This PDF could not be opened. Please upload it again or type the values in."
MAX_PDF_PAGES = 10          # lab reports are a page or two; more is refused, not processed for minutes
PDF_RENDER_DPI = 200        # scanned pages are turned into pictures at this resolution for OCR
MAX_IMAGE_SIDE = 2400       # bigger phone photos are scaled down first (GPU memory; text stays readable)


# ---------------------------------------------------------------------------
# PDF text layer (no OCR)
# ---------------------------------------------------------------------------
def _open_pdf(data: bytes):
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(data)
    except pdfium.PdfiumError:
        raise OcrFailed(_PDF_DAMAGED) from None
    if len(pdf) > MAX_PDF_PAGES:
        pdf.close()
        raise OcrFailed(f"The PDF has more than {MAX_PDF_PAGES} pages. Please upload only the lab result pages.")
    return pdf


def pdf_text_layer(data: bytes) -> str:
    """The text a PDF already contains, page by page ('' for a scan without a text layer)."""
    pdf = _open_pdf(data)
    try:
        pages = []
        for page in pdf:
            textpage = page.get_textpage()
            pages.append(textpage.get_text_bounded())
            textpage.close()
            page.close()
        return "\n".join(pages).replace("\r\n", "\n").replace("\r", "\n").strip()
    finally:
        pdf.close()


def has_lab_values(text: str) -> bool:
    """Whether a text layer is worth using: a PDF can carry a printed letterhead as text with
    the lab table itself scanned in as a picture."""
    return bool(text) and bool(ocr_parser.extract(text).values)


class PdfTextEngine:
    name = "PDF text layer"

    def read(self, data: bytes, mime_type: str) -> OcrResult:
        if mime_type != "application/pdf":
            raise OcrFailed(_NEEDS_OCR)
        text = pdf_text_layer(data)
        if not text:
            raise OcrFailed(_NEEDS_OCR)
        return OcrResult(text)  # exact text: no OCR confidence to give


# ---------------------------------------------------------------------------
# PaddleOCR-VL
# ---------------------------------------------------------------------------
class _TableRows(html.parser.HTMLParser):
    """PaddleOCR-VL writes tables as HTML. ocr_parser reads table rows as '| a | b | c |', so
    each <tr> becomes one such line; text outside tables is kept as it is."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self._text: list[str] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._flush_text()
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag == "br":
            if self._cell is not None:
                self._cell.append(" ")
            else:
                self._text.append("\n")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._row is not None and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(self._row):
                self.lines.append("| " + " | ".join(self._row) + " |")
            self._row = None

    def handle_data(self, data):
        (self._cell if self._cell is not None else self._text).append(data)

    def _flush_text(self):
        text = "".join(self._text)
        self.lines.extend(line for line in text.splitlines() if line.strip())
        self._text = []

    def result(self) -> str:
        self.close()
        self._flush_text()
        return "\n".join(self.lines)


def tables_to_rows(markdown: str) -> str:
    rows = _TableRows()
    rows.feed(markdown)
    return rows.result()


_pipeline = None
_pipeline_lock = threading.Lock()  # one document at a time on the GPU; the model is loaded once


def _get_pipeline():
    """The PaddleOCR-VL pipeline, loaded on first use and kept for the life of the process.
    Run RQ with `--worker-class rq.SimpleWorker`: the default worker forks a new process per job,
    which would load the model again for every document."""
    global _pipeline
    if _pipeline is None:
        from paddleocr import PaddleOCRVL  # lazy: the app starts without paddle installed

        _pipeline = PaddleOCRVL(device=settings.ocr_device)
    return _pipeline


def _image_pages(data: bytes, mime_type: str) -> list:
    """The pages as BGR numpy arrays (the format Paddle reads), made in memory: the decrypted
    file is never written to disk."""
    import numpy as np
    from PIL import Image, ImageOps

    if mime_type == "application/pdf":
        pdf = _open_pdf(data)
        try:
            images = [page.render(scale=PDF_RENDER_DPI / 72).to_pil() for page in pdf]
        finally:
            pdf.close()
    else:
        import io

        try:
            image = Image.open(io.BytesIO(data))
            image.load()
        except (OSError, Image.DecompressionBombError):
            raise OcrFailed("This picture could not be opened. Please upload it again.") from None
        images = [ImageOps.exif_transpose(image)]  # the photo the right way up
    pages = []
    for image in images:
        image = image.convert("RGB")
        image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
        pages.append(np.asarray(image)[:, :, ::-1].copy())  # RGB -> BGR
    return pages


def _page_text(result) -> str:
    markdown = result.markdown.get("markdown_texts", "") if hasattr(result, "markdown") else ""
    return tables_to_rows(markdown if isinstance(markdown, str) else "\n".join(markdown))


class PaddleOcrVlEngine:
    name = "PaddleOCR-VL"

    @staticmethod
    def available() -> bool:
        return importlib.util.find_spec("paddleocr") is not None and importlib.util.find_spec("paddle") is not None

    def read(self, data: bytes, mime_type: str) -> OcrResult:
        if mime_type == "application/pdf":
            text = pdf_text_layer(data)
            if has_lab_values(text):
                return OcrResult(text)  # a lab portal PDF: its own text is exact
        pages = _image_pages(data, mime_type)
        with _pipeline_lock:
            pipeline = _get_pipeline()
            texts = [_page_text(result) for page in pages for result in pipeline.predict(page)]
        text = "\n".join(t for t in texts if t).strip()
        if not text:
            raise OcrFailed("No text was found in this file. Please type the values in.")
        return OcrResult(text)  # PaddleOCR-VL gives no overall confidence


# name in OCR_ENGINE -> factory
ENGINES: dict[str, type] = {"pdf_text": PdfTextEngine, "paddleocr_vl": PaddleOcrVlEngine}


def _unknown() -> RuntimeError:
    return RuntimeError(f"Unknown OCR_ENGINE {settings.ocr_engine!r}; use one of: none, " + ", ".join(sorted(ENGINES)))


def check_engine() -> None:
    """Called at startup (backend/main.py): a typo in OCR_ENGINE, or an engine whose packages are
    not installed, stops the app instead of leaving every upload stuck or failed."""
    if settings.ocr_engine == "none":
        return
    engine = ENGINES.get(settings.ocr_engine)
    if engine is None:
        raise _unknown()
    available = getattr(engine, "available", None)
    if available is not None and not available():
        raise RuntimeError(f"OCR_ENGINE={settings.ocr_engine} needs its packages installed "
                           "(see docs/ocr.md), or set OCR_ENGINE=none or pdf_text")


def get_engine() -> OcrEngine | None:
    """The configured engine, or None when OCR is switched off (OCR_ENGINE=none)."""
    if settings.ocr_engine == "none":
        return None
    try:
        return ENGINES[settings.ocr_engine]()
    except KeyError:
        raise _unknown() from None
