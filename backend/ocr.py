"""OCR engines. The worker only needs read(); which engine runs is set by OCR_ENGINE in .env.

The engine can be changed without touching the worker: Chandra OCR needs a GPU with about
11 GB of memory (bf16), so a lighter engine (e.g. Tesseract) can run on laptops and on the
DigitalOcean droplet, and Chandra where a strong GPU is available.
"""

from dataclasses import dataclass
from typing import Protocol

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


# name in OCR_ENGINE -> factory. Real engines are added here once the group picks them.
ENGINES: dict[str, type] = {}


def get_engine() -> OcrEngine | None:
    """The configured engine, or None when OCR is switched off (OCR_ENGINE=none)."""
    if settings.ocr_engine == "none":
        return None
    try:
        return ENGINES[settings.ocr_engine]()
    except KeyError:
        raise RuntimeError(f"Unknown OCR_ENGINE {settings.ocr_engine!r}; use one of: none, "
                           + ", ".join(sorted(ENGINES))) from None
