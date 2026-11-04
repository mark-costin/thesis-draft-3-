"""Encrypted file storage for uploaded lab documents.

Each file is encrypted with Fernet (AES-128-CBC + HMAC-SHA256) before it touches the disk and
saved under a server-made UUID name, never the uploaded name (blocks path traversal).
Without DOCUMENT_ENCRYPTION_KEY the files on disk cannot be read.
"""

import os
import uuid
from pathlib import Path

from cryptography.fernet import Fernet

from backend.config import settings


def _fernet() -> Fernet:
    return Fernet(settings.document_encryption_key.encode())


def _path(storage_key: uuid.UUID | str) -> Path:
    key = uuid.UUID(str(storage_key))  # anything that is not a UUID is refused here
    return Path(settings.upload_dir) / f"{key}.enc"


def save(storage_key: uuid.UUID, data: bytes) -> None:
    folder = Path(settings.upload_dir)
    folder.mkdir(parents=True, exist_ok=True)
    final = _path(storage_key)
    temp = final.with_suffix(".tmp")
    temp.write_bytes(_fernet().encrypt(data))
    os.replace(temp, final)  # the file appears complete or not at all


def load(storage_key: uuid.UUID | str) -> bytes:
    return _fernet().decrypt(_path(storage_key).read_bytes())


def remove(storage_key: uuid.UUID | str) -> None:
    _path(storage_key).unlink(missing_ok=True)


def remove_many(storage_keys) -> None:
    for key in storage_keys:
        remove(key)
