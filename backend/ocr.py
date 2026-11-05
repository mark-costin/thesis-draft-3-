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
import os
import re
import sys
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
MAX_NEW_TOKENS = 1024       # per layout block (PaddleX default 4096). A lab table needs a few hundred;
                            # the cap stops a block whose text repeats from running for minutes
PDF_RENDER_DPI = 200        # scanned pages are turned into pictures at this resolution for OCR
MAX_IMAGE_SIDE = 2000       # bigger pages are scaled down first. On a 6 GB GPU an A4 page at full
                            # 200 dpi (2339 px) spilled into system RAM and took >5 minutes; at
                            # 2000 px it takes ~3 s and reads the same values


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
        # Set before paddle is first imported (it reads these when it loads):
        # - Loading needs ~6.4 GB for a moment (model + bf16 checkpoint + fp32 copies of the vision
        #   encoder), more than a 6 GB laptop GPU; running needs ~2.8 GB. Managed memory lets that
        #   moment spill into system RAM instead of failing with "out of memory".
        # - The model files are cached in ~/.paddlex after the first run: no need to ping the
        #   model hosts every time a worker starts.
        os.environ.setdefault("FLAGS_use_cuda_managed_memory", "true")
        os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
        import paddle
        from paddleocr import PaddleOCRVL  # lazy: the app starts without paddle installed

        # Also when something imported paddle before (then the environment variable was too late);
        # it must be set before the first GPU allocation, which is the model load just below.
        paddle.set_flags({"FLAGS_use_cuda_managed_memory": True})

        # use_queues=False: run the model in this thread, not in PaddleX's worker threads. One
        # document at a time runs anyway (the lock), and an error in a worker thread could leave
        # predict() waiting forever (the document stuck in 'processing') instead of failing.
        _pipeline = PaddleOCRVL(device=settings.ocr_device, use_queues=False)
        _free_gpu_cache()
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


# PaddleOCR-VL writes some symbols as LaTeX: "\mu mol/L" or "$ \mu $mol/L" for umol/L, "$ \times
# 10^{9} $/L", "1.73 m^{2}". ocr_parser and labs.py read plain text, so it is turned back first;
# without this, every umol/L and uIU/mL line (creatinine, TSH) was skipped.
_LATEX = [
    (re.compile(r"\\mu\s*"), "µ"),
    (re.compile(r"\\times\s*"), "x "),
    (re.compile(r"\\%"), "%"),
    (re.compile(r"m\s*\^\s*\{?\s*2\s*\}?"), "m2"),         # 1.73 m^{2} -> 1.73 m2
    (re.compile(r"\^\s*\{\s*(\d+)\s*\}"), r"^\1"),           # 10^{9} -> 10^9
    (re.compile(r"\$"), ""),
]


def latex_to_text(text: str) -> str:
    for pattern, plain in _LATEX:
        text = pattern.sub(plain, text)
    return re.sub(r"[ \t]{2,}", " ", text)


def _page_text(result) -> str:
    markdown = result.markdown.get("markdown_texts", "") if hasattr(result, "markdown") else ""
    markdown = markdown if isinstance(markdown, str) else "\n".join(markdown)
    return tables_to_rows(latex_to_text(markdown))


def _dynamic_mode() -> None:
    """PaddleOCR-VL's text model needs Paddle's dynamic mode, but the layout model runs on
    Paddle's static inference engine, which can leave the global mode switched to static; the
    next document then fails with "int(Tensor) is not supported in static graph mode"."""
    paddle = sys.modules.get("paddle")
    if paddle is not None and not paddle.in_dynamic_mode():
        paddle.disable_static()


def _free_gpu_cache() -> None:
    """Give memory Paddle has cached but no longer uses back to the GPU. After loading, that is
    the ~3 GB the checkpoint needed for a moment; left cached, reading a document spills into
    system RAM (managed memory) and becomes many times slower."""
    paddle = sys.modules.get("paddle")  # only if the model was really loaded (never imports it)
    if paddle is not None and paddle.device.is_compiled_with_cuda():
        paddle.device.cuda.empty_cache()


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
            _dynamic_mode()
            try:
                texts = [_page_text(result) for page in pages
                         for result in pipeline.predict(page, max_new_tokens=MAX_NEW_TOKENS)]
            finally:
                _free_gpu_cache()
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
