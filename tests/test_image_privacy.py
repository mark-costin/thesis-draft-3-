"""Uploaded photos lose their hidden metadata (GPS location, camera, comments) before they are
stored; the picture itself and its orientation stay the same."""

import hashlib
import io
import struct
import uuid
import zlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func

from backend import image_privacy
from backend import worker
from backend.config import settings
from backend.database import SessionLocal
from backend.main import app
from backend.models import User

Image = pytest.importorskip("PIL.Image")  # Pillow comes with WeasyPrint
from PIL import PngImagePlugin  # noqa: E402

client = TestClient(app)
PASSWORD = "correct horse battery"
GPS_IFD = 0x8825
ORIENTATION = 0x0112


def photo_exif(orientation=6):
    exif = Image.Exif()
    exif[ORIENTATION] = orientation
    exif[0x010F] = "SecretCam"                         # camera maker
    gps = exif.get_ifd(GPS_IFD)
    gps[1], gps[2] = "N", (14.0, 35.0, 0.0)            # latitude: where the photo was taken
    return exif.tobytes()


def jpeg_with_metadata(orientation=6):
    out = io.BytesIO()
    Image.new("RGB", (40, 20), (200, 10, 10)).save(out, "JPEG", exif=photo_exif(orientation),
                                                   comment=b"patient Juan Dela Cruz")
    return out.getvalue()


def png_with_metadata():
    info = PngImagePlugin.PngInfo()
    info.add_text("Location", "14.5995, 120.9842")
    info.add_itxt("Author", "Juan Dela Cruz")
    out = io.BytesIO()
    Image.new("RGB", (40, 20), (10, 200, 10)).save(out, "PNG", pnginfo=info, exif=photo_exif())
    return out.getvalue()


def opened(data):
    image = Image.open(io.BytesIO(data))
    image.load()  # fails if the file was damaged
    return image


# ---------------------------------------------------------------------------
# Cleaning (no database)
# ---------------------------------------------------------------------------
def test_a_jpeg_loses_gps_camera_and_comments_but_keeps_its_orientation():
    original = jpeg_with_metadata()
    assert GPS_IFD in opened(original).getexif()
    cleaned = image_privacy.strip_metadata(original, "image/jpeg")
    image = opened(cleaned)
    exif = image.getexif()
    assert GPS_IFD not in exif and 0x010F not in exif
    assert exif.get(ORIENTATION) == 6               # still shown the right way up
    assert b"SecretCam" not in cleaned and b"Juan" not in cleaned
    assert image.size == (40, 20)


def test_the_jpeg_picture_data_is_not_changed():
    original = jpeg_with_metadata(orientation=1)
    cleaned = image_privacy.strip_metadata(original, "image/jpeg")
    scan = original.index(b"\xff\xda")              # start of the image data
    assert cleaned.endswith(original[scan:])
    assert opened(cleaned).tobytes() == opened(original).tobytes()


def test_a_jpeg_without_metadata_stays_byte_for_byte_the_same():
    out = io.BytesIO()
    Image.new("RGB", (8, 8)).save(out, "JPEG")
    assert image_privacy.strip_metadata(out.getvalue(), "image/jpeg") == out.getvalue()


def test_a_png_loses_its_text_and_exif_chunks():
    original = png_with_metadata()
    cleaned = image_privacy.strip_metadata(original, "image/png")
    image = opened(cleaned)
    assert image.text == {} and b"eXIf" not in cleaned
    assert b"Location" not in cleaned and b"Juan" not in cleaned
    assert image.tobytes() == opened(original).tobytes()


def test_little_and_big_endian_orientation_are_read():
    # Phones write either byte order: "II" (little-endian, most Android) or "MM" (big-endian)
    little = (b"Exif\x00\x00II*\x00" + struct.pack("<I", 8) + struct.pack("<H", 1)
              + struct.pack("<HHIHH", ORIENTATION, 3, 1, 3, 0) + struct.pack("<I", 0))
    big = image_privacy.orientation_segment(8)[4:]
    assert big[6:8] == b"MM"
    assert image_privacy.exif_orientation(little) == 3
    assert image_privacy.exif_orientation(big) == 8
    assert image_privacy.exif_orientation(b"not exif") is None


def test_pdfs_are_kept_as_they_are():
    data = b"%PDF-1.4\n/Author (Juan)\n"
    assert image_privacy.strip_metadata(data, "application/pdf") == data


def segment(marker, payload):
    return b"\xff" + bytes([marker]) + struct.pack(">H", len(payload) + 2) + payload


def png_chunk(kind, body):
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def plain_jpeg():
    out = io.BytesIO()
    Image.new("RGB", (40, 20), (200, 10, 10)).save(out, "JPEG")
    return out.getvalue()


def test_a_second_picture_after_the_first_loses_its_metadata_too():
    """iPhone / Samsung photos often carry a second picture (MPF: depth or gain map) after the
    first one, with its own EXIF and GPS. Everything after the end of the first picture goes."""
    out = io.BytesIO()
    Image.new("RGB", (40, 20), "red").save(out, "MPO", save_all=True, exif=photo_exif(),
                                         append_images=[Image.new("RGB", (40, 20), "blue")])
    assert out.getvalue().count(b"SecretCam") == 2
    cleaned = image_privacy.strip_metadata(out.getvalue(), "image/jpeg")
    assert b"SecretCam" not in cleaned and b"MPF\x00" not in cleaned
    image = opened(cleaned)
    assert image.format == "JPEG" and getattr(image, "n_frames", 1) == 1


def test_a_motion_photo_video_after_the_picture_is_not_kept():
    video = b"\x00\x00\x00\x18ftypmp42" + b"\xa9xyz+14.5995+120.9842/"  # MP4 with a location
    cleaned = image_privacy.strip_metadata(plain_jpeg() + video, "image/jpeg")
    assert cleaned == plain_jpeg()


def test_unknown_jpeg_blocks_are_removed():
    """A keep list, not a drop list: C2PA content credentials (APP11), FlashPix (APP2) and
    others this module does not know about can hold a location too."""
    original = plain_jpeg()
    extra = (segment(0xEB, b"JP\x00\x00jumb location 14.5995") + segment(0xE2, b"FPXR\x00 14.5995")
             + segment(0xE3, b"vendor 14.5995") + segment(0xEF, b"vendor 14.5995"))
    icc = segment(0xE2, b"ICC_PROFILE\x00\x01\x01colour")
    cleaned = image_privacy.strip_metadata(original[:2] + extra + icc + original[2:], "image/jpeg")
    assert b"14.5995" not in cleaned and icc in cleaned  # the colour profile stays
    assert cleaned == original[:2] + icc + original[2:]


def test_unknown_png_chunks_are_removed():
    original = Image.new("RGB", (40, 20), (10, 200, 10))
    out = io.BytesIO()
    original.save(out, "PNG")
    data = out.getvalue()
    extra = png_chunk(b"caBX", b"c2pa location 14.5995") + png_chunk(b"vpAg", b"14.5995")
    keep = png_chunk(b"gAMA", struct.pack(">I", 45455))
    cleaned = image_privacy.strip_metadata(data[:33] + extra + keep + data[33:], "image/png")
    assert b"14.5995" not in cleaned and keep in cleaned
    assert opened(cleaned).tobytes() == original.tobytes()


def test_a_progressive_jpeg_keeps_all_its_scans():
    out = io.BytesIO()
    Image.effect_noise((64, 48), 60).convert("RGB").save(out, "JPEG", progressive=True,
                                                          exif=photo_exif(orientation=1))
    original = out.getvalue()
    cleaned = image_privacy.strip_metadata(original, "image/jpeg")
    assert original.count(b"\xff\xda") > 1 and cleaned.count(b"\xff\xda") == original.count(b"\xff\xda")
    assert b"SecretCam" not in cleaned
    assert opened(cleaned).tobytes() == opened(original).tobytes()


def test_a_jpeg_cut_off_inside_its_picture_is_kept_as_far_as_it_goes():
    out = io.BytesIO()
    Image.effect_noise((64, 48), 60).convert("RGB").save(out, "JPEG")
    original = out.getvalue()
    cut = original[:-200]  # a partial download: stops inside the image data
    assert original.rindex(b"\xff\xda") < len(cut)
    assert image_privacy.strip_metadata(cut, "image/jpeg") == cut


@pytest.mark.parametrize("data, mime", [
    (b"\x89PNG\r\n\x1a\n" + b"damaged " * 20, "image/png"),
    (b"\xff\xd8\xff\xe0" + b"damaged " * 20, "image/jpeg"),
    (b"\xff\xd8" + segment(0xE0, b"JFIF\x00") + b"junk" + jpeg_with_metadata()[2:], "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", b"x" * 13), "image/png"),
], ids=["damaged-png", "damaged-jpeg", "junk-before-exif", "png-without-end"])
def test_damaged_images_are_refused(data, mime):
    """What follows the damage cannot be checked for metadata, and no viewer could show it."""
    with pytest.raises(image_privacy.DamagedImage):
        image_privacy.strip_metadata(data, mime)


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def upload_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", tmp_path)
    return tmp_path


@pytest.fixture
def headers():
    email = f"exif-{uuid.uuid4().hex[:8]}@test.local"
    assert client.post("/auth/register", json={
        "email": email, "password": PASSWORD, "first_name": "Josefa", "last_name": "Llanes",
        "date_of_birth": "1984-09-09", "biological_sex": "Female",
        "privacy_consent": True, "consent_version": settings.privacy_notice_version,
    }).status_code == 201
    token = client.post("/auth/login", json={"email": email, "password": PASSWORD}).json()["access_token"]
    yield {"Authorization": f"Bearer {token}"}
    with SessionLocal() as session:
        session.execute(delete(User).where(func.lower(User.email) == email))
        session.commit()


@pytest.mark.parametrize("make, mime, name", [
    (jpeg_with_metadata, "image/jpeg", "lab.jpg"),
    (png_with_metadata, "image/png", "lab.png"),
], ids=["jpeg", "png"])
def test_uploaded_photos_are_stored_without_metadata(headers, make, mime, name):
    original = make()
    response = client.post("/documents", headers=headers, files={"file": (name, original, mime)})
    assert response.status_code in (201, 202), response.text
    document = response.json()
    stored = client.get(f"/documents/{document['document_id']}/file", headers=headers).content
    assert b"Juan" not in stored and GPS_IFD not in opened(stored).getexif()
    assert document["file_sha256"] == hashlib.sha256(stored).hexdigest()  # checksum of what is kept
    assert document["file_size_bytes"] == len(stored) < len(original)


def test_the_same_photo_with_different_metadata_counts_as_the_same_file(headers):
    """The duplicate check uses the cleaned file, so re-uploading the same picture is refused."""
    first = client.post("/documents", headers=headers,
                        files={"file": ("a.jpg", jpeg_with_metadata(orientation=1), "image/jpeg")})
    assert first.status_code in (201, 202)
    out = io.BytesIO()
    exif = Image.Exif()
    exif[0x010F] = "OtherCam"
    Image.new("RGB", (40, 20), (200, 10, 10)).save(out, "JPEG", exif=exif.tobytes())
    again = client.post("/documents", headers=headers, files={"file": ("b.jpg", out.getvalue(), "image/jpeg")})
    assert again.status_code == 409


def test_a_retried_upload_of_a_photo_with_metadata_finds_the_pending_document(headers, monkeypatch):
    """With OCR on, the same file again while it is still pending is a retry (Redis was down). The
    stored checksum is of the cleaned file, so the retry must be compared after cleaning too."""
    monkeypatch.setattr(settings, "ocr_engine", "fake")
    monkeypatch.setattr(worker, "start", lambda *args: None)  # OCR never starts: stays pending
    photo = jpeg_with_metadata()
    first = client.post("/documents", headers=headers, files={"file": ("a.jpg", photo, "image/jpeg")})
    again = client.post("/documents", headers=headers, files={"file": ("a.jpg", photo, "image/jpeg")})
    assert first.status_code == again.status_code == 202
    assert again.json()["document_id"] == first.json()["document_id"]


def test_a_damaged_photo_upload_is_refused(headers):
    response = client.post("/documents", headers=headers,
                           files={"file": ("a.jpg", b"\xff\xd8\xff\xe0damaged", "image/jpeg")})
    assert response.status_code == 422 and "damaged" in response.json()["detail"]
