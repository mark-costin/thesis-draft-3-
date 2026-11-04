"""Removes hidden metadata (EXIF, GPS location, camera details, comments) from uploaded photos
before they are stored. A phone photo of a lab result can carry where and when it was taken;
the app does not need that, so it is never kept (RA 10173: data minimization).

Lossless: the image data itself is copied byte for byte; only metadata blocks are dropped.
- JPEG: drops APP1 (EXIF, XMP), APP12, APP13 (IPTC/Photoshop) and COM (comments). Keeps APP0
  (JFIF), APP2 (colour profile) and APP14 (Adobe colour), which are needed to show the image
  correctly. The EXIF orientation is kept in a tiny new EXIF block, so a photo taken sideways is
  still shown (and read by OCR) the right way up.
- PNG: drops eXIf, tEXt, zTXt, iTXt and tIME chunks.
- PDF: unchanged (scanned reports rarely carry GPS; PDF metadata cleaning would need a PDF library).

If a file is damaged part way, everything up to the damage is cleaned and the rest is kept as it
is, so a strange file never crashes the upload.
"""

import struct

_JPEG_DROP = {0xE1, 0xEC, 0xED, 0xFE}           # APP1, APP12, APP13, COM
_PNG_DROP = {b"eXIf", b"tEXt", b"zTXt", b"iTXt", b"tIME"}
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_ORIENTATION_TAG = 0x0112


def strip_metadata(data: bytes, mime_type: str) -> bytes:
    if mime_type == "image/jpeg":
        return _strip_jpeg(data)
    if mime_type == "image/png":
        return _strip_png(data)
    return data


# ---------------------------------------------------------------------------
# JPEG
# ---------------------------------------------------------------------------
def exif_orientation(exif: bytes) -> int | None:
    """The Orientation value (1-8) from an APP1 'Exif' payload, or None."""
    if not exif.startswith(b"Exif\x00\x00") or len(exif) < 14:
        return None
    tiff = exif[6:]
    order = {b"II": "<", b"MM": ">"}.get(tiff[:2])
    if order is None:
        return None
    try:
        (ifd_offset,) = struct.unpack(order + "I", tiff[4:8])
        (count,) = struct.unpack(order + "H", tiff[ifd_offset:ifd_offset + 2])
        for i in range(count):
            start = ifd_offset + 2 + 12 * i
            tag, kind, _, value = struct.unpack(order + "HHI4s", tiff[start:start + 12])
            if tag == _ORIENTATION_TAG and kind == 3:  # SHORT
                (orientation,) = struct.unpack(order + "H", value[:2])
                return orientation if 1 <= orientation <= 8 else None
    except struct.error:
        return None
    return None


def orientation_segment(orientation: int) -> bytes:
    """A minimal APP1 EXIF segment that holds only the orientation."""
    tiff = (b"MM\x00\x2a" + struct.pack(">I", 8)                       # header, IFD0 at 8
            + struct.pack(">H", 1)                                      # one entry
            + struct.pack(">HHIHH", _ORIENTATION_TAG, 3, 1, orientation, 0)
            + struct.pack(">I", 0))                                     # no next IFD
    payload = b"Exif\x00\x00" + tiff
    return b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload


def _strip_jpeg(data: bytes) -> bytes:
    if not data.startswith(b"\xff\xd8"):
        return data
    out = bytearray(b"\xff\xd8")
    position = 2
    orientation = None
    inserted = False
    while position < len(data):
        if data[position] != 0xFF or position + 1 >= len(data):
            break  # damaged: keep the rest as is
        marker = data[position + 1]
        if marker == 0xFF:  # fill byte
            position += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:  # markers without a length
            out += data[position:position + 2]
            position += 2
            continue
        if marker == 0xD9:  # end of image
            out += data[position:position + 2]
            position += 2
            break
        if position + 4 > len(data):
            break
        (length,) = struct.unpack(">H", data[position + 2:position + 4])
        end = position + 2 + length
        if length < 2 or end > len(data):
            break
        payload = data[position + 4:end]
        if marker == 0xDA:  # start of scan: the image data follows; copy everything after it
            if orientation and orientation != 1 and not inserted:
                out += orientation_segment(orientation)
                inserted = True
            out += data[position:]
            return bytes(out)
        if marker in _JPEG_DROP:
            if marker == 0xE1 and orientation is None:
                orientation = exif_orientation(payload)
        else:
            if marker != 0xE0 and orientation and orientation != 1 and not inserted:
                out += orientation_segment(orientation)  # right after APP0, before the rest
                inserted = True
            out += data[position:end]
        position = end
    out += data[position:]
    return bytes(out)


# ---------------------------------------------------------------------------
# PNG
# ---------------------------------------------------------------------------
def _strip_png(data: bytes) -> bytes:
    if not data.startswith(_PNG_SIGNATURE):
        return data
    out = bytearray(_PNG_SIGNATURE)
    position = len(_PNG_SIGNATURE)
    while position + 8 <= len(data):
        (length,) = struct.unpack(">I", data[position:position + 4])
        kind = data[position + 4:position + 8]
        end = position + 12 + length  # length, type, data, CRC
        if end > len(data) or not kind.isalpha():
            break  # damaged: keep the rest as is
        if kind not in _PNG_DROP:
            out += data[position:end]
        position = end
        if kind == b"IEND":
            return bytes(out)
    out += data[position:]
    return bytes(out)
