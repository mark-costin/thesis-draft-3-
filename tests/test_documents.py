"""Document upload: files on disk are unreadable without the key."""

import hashlib
import uuid
from pathlib import Path

import pytest
from cryptography.fernet import Fernet, InvalidToken
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from backend import audit, storage
from backend.config import settings
from backend.database import SessionLocal, set_request_identity
from backend.main import app
from backend.middleware import REPLAY_HEADER
from backend.models import Document, Patient, User
from backend.schemas import DELETE_CONFIRMATION

client = TestClient(app)
PASSWORD = "correct horse battery"
PNG = b"\x89PNG\r\n\x1a\n" + b"lab result pixels " * 50
PDF = b"%PDF-1.4\n" + b"Fasting blood sugar 96 mg/dL\n" * 20
JPEG = b"\xff\xd8\xff\xe0" + b"jpeg body " * 40


@pytest.fixture(autouse=True)
def upload_folder(tmp_path, monkeypatch):
    """Each test stores its encrypted files in its own temporary folder."""
    monkeypatch.setattr(settings, "upload_dir", tmp_path)
    return tmp_path


@pytest.fixture
def make_patient():
    made = []

    def make():
        email = f"docs-{uuid.uuid4().hex[:8]}@test.local"
        made.append(email)
        assert client.post("/auth/register", json={
            "email": email, "password": PASSWORD, "first_name": "Gregoria", "last_name": "de Jesus",
            "date_of_birth": "1975-05-09", "biological_sex": "Female",
            "privacy_consent": True, "consent_version": settings.privacy_notice_version,
        }).status_code == 201
        token = client.post("/auth/login", json={"email": email, "password": PASSWORD}).json()["access_token"]
        return email, {"Authorization": f"Bearer {token}"}

    yield make
    with SessionLocal() as session:
        session.execute(delete(User).where(func.lower(User.email).in_(made)))
        session.commit()


def upload(headers, data, name="lab.png", content_type="image/png", extra=None):
    return client.post("/documents", headers=headers | (extra or {}),
                       files={"file": (name, data, content_type)})


def storage_key_of(email, document_id):
    with SessionLocal() as session:
        user_id = session.scalar(select(User.user_id).where(User.email == email))
        set_request_identity(session, user_id=user_id)
        patient_id = session.scalar(select(Patient.patient_id))
        set_request_identity(session, user_id=user_id, patient_id=patient_id)
        return session.scalar(select(Document.storage_key).where(Document.document_id == document_id))


def test_files_on_disk_are_unreadable_without_the_key(make_patient, upload_folder):
    email, headers = make_patient()
    response = upload(headers, PNG)
    assert response.status_code == 201
    doc = response.json()
    assert (doc["mime_type"], doc["status"], doc["file_size_bytes"]) == ("image/png", "pending", len(PNG))
    assert doc["file_sha256"] == hashlib.sha256(PNG).hexdigest()
    assert response.headers["Location"] == f"/documents/{doc['document_id']}"

    on_disk = (upload_folder / f"{storage_key_of(email, doc['document_id'])}.enc").read_bytes()
    assert not on_disk.startswith(b"\x89PNG") and b"lab result" not in on_disk
    with pytest.raises(InvalidToken):
        Fernet(Fernet.generate_key()).decrypt(on_disk)  # any other key fails
    assert Fernet(settings.document_encryption_key.encode()).decrypt(on_disk) == PNG


def test_download_returns_the_original_file(make_patient):
    _, headers = make_patient()
    document_id = upload(headers, PDF, "results.pdf", "application/pdf").json()["document_id"]
    response = client.get(f"/documents/{document_id}/file", headers=headers)
    assert response.status_code == 200
    assert response.content == PDF
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["Content-Disposition"] == f'attachment; filename="document-{document_id}.pdf"'
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.parametrize("data, mime", [(PNG, "image/png"), (PDF, "application/pdf"), (JPEG, "image/jpeg")])
def test_type_comes_from_the_file_not_the_name(make_patient, data, mime):
    _, headers = make_patient()
    response = upload(headers, data, name="whatever.txt", content_type="text/plain")
    assert response.status_code == 201 and response.json()["mime_type"] == mime


@pytest.mark.parametrize("data", [
    b"",
    b"just some text pretending to be an image",
    b"MZ\x90\x00 windows program",
    b"\x89PNG\r\n\x1a\n" + b"x" * (10 * 1024 * 1024),  # one byte over 10 MB
], ids=["empty", "text-named-png", "exe", "over-10mb"])
def test_bad_files_are_refused(make_patient, upload_folder, data):
    _, headers = make_patient()
    assert upload(headers, data).status_code == 422
    assert list(upload_folder.iterdir()) == []
    assert client.get("/documents", headers=headers).json() == []


def test_same_file_twice_is_refused_per_patient(make_patient):
    _, headers_a = make_patient()
    _, headers_b = make_patient()
    assert upload(headers_a, PNG).status_code == 201
    assert upload(headers_a, PNG).status_code == 409
    assert upload(headers_b, PNG).status_code == 201  # another patient may upload the same file


def test_uploaded_name_is_never_used_on_disk(make_patient, upload_folder):
    _, headers = make_patient()
    assert upload(headers, PNG, name="../../../evil.png").status_code == 201
    files = list(upload_folder.iterdir())
    assert len(files) == 1 and files[0].suffix == ".enc"
    uuid.UUID(files[0].stem)  # a UUID, nothing from the uploaded name
    assert not (upload_folder.parent / "evil.png").exists()


def test_documents_are_per_patient(make_patient):
    _, headers_a = make_patient()
    _, headers_b = make_patient()
    document_id = upload(headers_a, PNG).json()["document_id"]
    assert client.get("/documents", headers=headers_b).json() == []
    for path in (f"/documents/{document_id}", f"/documents/{document_id}/file"):
        assert client.get(path, headers=headers_b).status_code == 404
    assert client.delete(f"/documents/{document_id}", headers=headers_b).status_code == 404
    assert client.get(f"/documents/{document_id}/file", headers=headers_a).content == PNG


def test_delete_removes_the_file_from_disk(make_patient, upload_folder):
    _, headers = make_patient()
    document_id = upload(headers, PNG).json()["document_id"]
    assert len(list(upload_folder.iterdir())) == 1
    assert client.delete(f"/documents/{document_id}", headers=headers).status_code == 204
    assert list(upload_folder.iterdir()) == []
    assert client.get(f"/documents/{document_id}", headers=headers).status_code == 404


def test_changed_file_on_disk_is_detected(make_patient, upload_folder):
    email, headers = make_patient()
    document_id = upload(headers, PNG).json()["document_id"]
    path = upload_folder / f"{storage_key_of(email, document_id)}.enc"
    path.write_bytes(Fernet(settings.document_encryption_key.encode()).encrypt(b"\x89PNG\r\n\x1a\nswapped"))
    assert client.get(f"/documents/{document_id}/file", headers=headers).status_code == 500
    path.write_bytes(b"garbage")
    assert client.get(f"/documents/{document_id}/file", headers=headers).status_code == 500


def test_account_deletion_removes_the_files(make_patient, upload_folder):
    _, headers = make_patient()
    upload(headers, PNG)
    upload(headers, PDF, "a.pdf", "application/pdf")
    assert len(list(upload_folder.iterdir())) == 2
    response = client.post("/account/delete", headers=headers,
                           json={"password": PASSWORD, "confirm": DELETE_CONFIRMATION})
    assert response.status_code == 204
    assert list(upload_folder.iterdir()) == []


def test_upload_is_audited_without_the_file_name(make_patient, monkeypatch):
    recorded = []
    real = audit.log_event
    monkeypatch.setattr(audit, "log_event",
                        lambda db, event_type, **kw: recorded.append((event_type, kw)) or real(db, event_type, **kw))
    _, headers = make_patient()
    upload(headers, PNG, name="juan-dela-cruz-labs.png")
    event_type, details = recorded[-1]
    assert event_type == "DOCUMENT_UPLOADED"
    assert details["mime_type"] == "image/png" and details["size_bytes"] == len(PNG)
    assert "juan" not in str(details)


def test_uploads_skip_idempotency_keys(make_patient):
    _, headers = make_patient()
    key = {"Idempotency-Key": "upload-" + uuid.uuid4().hex}
    assert upload(headers, PNG, extra=key).status_code == 201
    again = upload(headers, PNG, extra=key)
    assert again.status_code == 409 and REPLAY_HEADER not in again.headers


def test_documents_need_login():
    assert client.get("/documents").status_code == 401
    assert client.post("/documents", files={"file": ("a.png", PNG, "image/png")}).status_code == 401


def test_storage_refuses_names_that_are_not_uuids():
    with pytest.raises(ValueError):
        storage.load("../../etc/passwd")
    assert isinstance(Path(settings.upload_dir), Path)
