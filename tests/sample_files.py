"""Real, valid image files for upload tests. Uploads are parsed to remove hidden metadata
(backend/image_privacy.py), so a signature followed by random bytes is refused as damaged."""

import io
import struct
import zlib


def _chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def png(pixels: bytes = b"lab report pixels ") -> bytes:
    """A one-row grayscale PNG whose pixel values are the given bytes: different bytes give a
    different file (the same file twice is refused as a duplicate)."""
    header = struct.pack(">IIBBBBB", len(pixels), 1, 8, 0, 0, 0, 0)  # width, height 1, 8-bit gray
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header)
            + _chunk(b"IDAT", zlib.compress(b"\x00" + pixels)) + _chunk(b"IEND", b""))


def jpeg(size=(16, 8), colour=(200, 10, 10)) -> bytes:
    from PIL import Image  # Pillow comes with WeasyPrint

    out = io.BytesIO()
    Image.new("RGB", size, colour).save(out, "JPEG")
    return out.getvalue()
