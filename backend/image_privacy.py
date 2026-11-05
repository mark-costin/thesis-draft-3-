"""Removes hidden metadata (EXIF, GPS location, camera details, comments) from uploaded photos
before they are stored. A phone photo of a lab result can carry where and when it was taken;
the app does not need that, so it is never kept (RA 10173: data minimization).

Only what is needed to show the picture is kept (a keep list, not a drop list, so metadata
blocks this module has never heard of are removed too):
- JPEG: keeps APP0 (JFIF), APP2 ICC_PROFILE (colour profile), APP14 Adobe (colour transform) and
  the image segments. Everything else goes: APP1 (EXIF, XMP), APP2 MPF / FlashPix, APP3-APP13,
  APP11 (C2PA / JUMBF), APP15, comments, and anything after the end of the image (a second MPF
  picture, the video of a "motion photo"). The EXIF orientation is kept in a tiny new EXIF
  block, so a photo taken sideways is still shown (and read by OCR) the right way up.
- PNG: keeps the critical chunks and the ones that change how it looks (transparency, gamma,
  colour, pixel size); eXIf, tEXt, zTXt, iTXt, tIME, caBX (C2PA) and the rest go.
- PDF: unchanged (scanned reports rarely carry GPS; PDF metadata cleaning would need a PDF library).

Lossless: the image data itself is copied byte for byte; only metadata blocks are dropped.
A file whose structure is broken before the image data is refused (DamagedImage): no viewer
could show it, and what follows the damage cannot be checked for metadata. A JPEG cut off inside
its image data (a partial download) is kept as far as it goes.
"""

import struct

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# Ancillary PNG chunks that change how the picture looks. Critical chunks (IHDR, PLTE, IDAT,
# IEND: upper-case first letter) are always kept.
_PNG_KEEP = {b"tRNS", b"gAMA", b"cHRM", b"sRGB", b"iCCP", b"sBIT", b"bKGD", b"pHYs", b"hIST",
             b"cICP", b"mDCV", b"cLLI"}
_ORIENTATION_TAG = 0x0112
_SOS, _EOI = 0xDA, 0xD9


class DamagedImage(ValueError):
    pass


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


def _keep_jpeg_segment(marker: int, payload: bytes) -> bool:
    if marker == 0xE0:                                   # APP0 JFIF / JFXX
        return True
    if marker == 0xE2:                                   # APP2: only the colour profile
        return payload.startswith(b"ICC_PROFILE\x00")
    if marker == 0xEE:                                   # APP14 Adobe colour transform
        return payload.startswith(b"Adobe")
    return not (0xE0 <= marker <= 0xEF or marker == 0xFE)  # other APPn and COM: metadata


def _end_of_scan(data: bytes, position: int) -> int:
    """Where the entropy-coded image data that starts at position ends: the next marker that is
    not a stuffed byte (FF 00) or a restart marker (FF D0-D7), or the end of the file."""
    while True:
        position = data.find(b"\xff", position)
        if position < 0 or position + 1 >= len(data):
            return len(data)
        following = data[position + 1]
        if following == 0x00 or 0xD0 <= following <= 0xD7:
            position += 2
            continue
        return position


def _strip_jpeg(data: bytes) -> bytes:
    if not data.startswith(b"\xff\xd8"):
        return data
    out = bytearray(b"\xff\xd8")
    position = 2
    orientation = None
    inserted = False
    in_image = False  # True after the first start of scan

    def add_orientation():
        nonlocal inserted
        if orientation and orientation != 1 and not inserted:
            out.extend(orientation_segment(orientation))
            inserted = True

    while position < len(data):
        if data[position] != 0xFF or position + 1 >= len(data):
            if not in_image:
                raise DamagedImage("The image file is damaged")
            return bytes(out)  # junk after the picture: not kept
        marker = data[position + 1]
        if marker == 0xFF:  # fill byte
            position += 1
            continue
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:  # markers without a length
            out += data[position:position + 2]
            position += 2
            continue
        if marker == _EOI:
            out += data[position:position + 2]
            return bytes(out)  # anything after the picture (MPF images, motion-photo video) goes
        if marker == 0xD8:
            raise DamagedImage("The image file is damaged")
        length = struct.unpack(">H", data[position + 2:position + 4])[0] if position + 4 <= len(data) else 0
        end = position + 2 + length
        if length < 2 or end > len(data):
            if not in_image:
                raise DamagedImage("The image file is damaged")
            return bytes(out)
        payload = data[position + 4:end]
        if marker == _SOS:
            add_orientation()
            in_image = True
            end = _end_of_scan(data, end)  # the scan header plus its image data
            out += data[position:end]
        elif _keep_jpeg_segment(marker, payload):
            if marker != 0xE0:
                add_orientation()  # right after APP0, before the rest
            out += data[position:end]
        elif marker == 0xE1 and orientation is None:
            orientation = exif_orientation(payload)
        position = end
    if not in_image:
        raise DamagedImage("The image file is damaged")
    return bytes(out)  # cut off inside the image data: kept as far as it goes


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
            raise DamagedImage("The image file is damaged")
        if kind[:1].isupper() or kind in _PNG_KEEP:
            out += data[position:end]
        position = end
        if kind == b"IEND":
            return bytes(out)  # anything after the picture goes
    raise DamagedImage("The image file is damaged")  # no IEND: cut off
