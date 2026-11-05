"""The OCR job: read an uploaded document, save the lab values found, and hand it to the patient
for review (status ready_for_review). Values are saved unconfirmed (source = ocr); nothing is
used by the models until the patient confirms them (POST /documents/{id}/confirm).

Runs in an RQ worker when REDIS_ENABLED=true (Linux / Docker:
`rq worker --worker-class rq.SimpleWorker ocr`; the default worker forks per job and would load
the OCR model again for every document), or right after
the upload response in the API process when Redis is off (development; RQ does not run on
Windows).
"""

import hashlib
import logging
from datetime import datetime, timedelta, timezone

from cryptography.fernet import InvalidToken
from sqlalchemy import update

from backend import audit, storage
from backend.ocr import engines, parser
from backend.config import settings
from backend.database import SessionLocal, set_request_identity
from backend.models import Document, LabResult

log = logging.getLogger(__name__)
QUEUE_NAME = "ocr"
JOB_TIMEOUT_SECONDS = 600
# A document still 'processing' this long after its job claimed it is stuck (crashed worker, lost
# job). Longer than JOB_TIMEOUT_SECONDS, so a job that is still running is never reset.
STUCK_AFTER = timedelta(minutes=15)
_GENERIC_ERROR = "OCR could not read this file. Please type the values in."


def enqueue(document_id: int, patient_id: int, background_tasks) -> bool:
    """Starts OCR for a new upload. Returns False when OCR is off (the document stays pending)."""
    if settings.ocr_engine == "none":
        return False
    if settings.redis_enabled:
        from redis import Redis
        from rq import Queue
        Queue(QUEUE_NAME, connection=Redis.from_url(settings.redis_url)).enqueue(
            process_document, document_id, patient_id, job_timeout=JOB_TIMEOUT_SECONDS)
    else:
        background_tasks.add_task(process_document, document_id, patient_id)
    return True


def start(document_id: int, patient_id: int, background_tasks) -> bool:
    """enqueue() that never fails the request: if the queue cannot be reached (Redis down), the
    document stays 'pending' and is queued again when the patient uploads the same file or
    polls a stuck one. Only the error type is logged."""
    try:
        return enqueue(document_id, patient_id, background_tasks)
    except Exception as exc:
        log.warning("Could not queue OCR for document %s: %s", document_id, type(exc).__name__)
        return False


def reset_stuck(session, patient_id: int) -> list[int]:
    """This patient's documents claimed more than STUCK_AFTER ago and still 'processing' go back
    to 'pending'. Per patient, because RLS lets a session see only one patient's documents.
    processed_at holds the claim time while a job runs (see process_document)."""
    return list(session.scalars(update(Document)
                                .where(Document.patient_id == patient_id, Document.status == "processing",
                                       Document.processed_at < datetime.now(timezone.utc) - STUCK_AFTER)
                                .values(status="pending", processed_at=None)
                                .returning(Document.document_id)))


def requeue_stuck(session, patient_id: int, background_tasks) -> list[int]:
    """reset_stuck() and queue each reset document again. Called when the patient polls."""
    ids = reset_stuck(session, patient_id)
    session.commit()
    for document_id in ids:
        start(document_id, patient_id, background_tasks)
    return ids


def _set_failed(session, document_id: int, patient_id: int, message: str) -> None:
    session.execute(update(Document)
                    .where(Document.document_id == document_id, Document.patient_id == patient_id,
                           Document.status == "processing")
                    .values(status="failed", error_message=message[:255],
                            processed_at=datetime.now(timezone.utc)))
    session.commit()


def process_document(document_id: int, patient_id: int) -> str:
    """Returns the final status. patient_id comes from the uploader's verified token, so the job
    runs under the same Row-Level Security as the request that queued it, and every query also
    filters on it (RLS is the second layer)."""
    # Before claiming: a wrong OCR_ENGINE raises here and the document stays 'pending'
    engine = engines.get_engine()
    with SessionLocal() as session:
        set_request_identity(session, patient_id=patient_id)
        reset_stuck(session, patient_id)  # this patient's crashed jobs go back to 'pending'

        # Claim the document: only a pending one, so a job that runs twice does nothing twice.
        # processed_at holds the claim time until the job finishes (used by reset_stuck).
        claimed = session.execute(update(Document)
                                  .where(Document.document_id == document_id, Document.patient_id == patient_id,
                                         Document.status == "pending")
                                  .values(status="processing", processed_at=datetime.now(timezone.utc))
                                  .returning(Document.storage_key, Document.mime_type, Document.file_sha256)).one_or_none()
        session.commit()
        if claimed is None:
            return "skipped"
        storage_key, mime_type, sha256 = claimed

        if engine is None:
            _set_failed(session, document_id, patient_id, "OCR is switched off. Please type the values in.")
            return "failed"
        try:
            data = storage.load(storage_key)
            if hashlib.sha256(data).hexdigest() != sha256:
                raise InvalidToken
            result = engine.read(data, mime_type)
        except (InvalidToken, FileNotFoundError):
            _set_failed(session, document_id, patient_id,
                        "The stored file failed its integrity check. Please upload it again.")
            return "failed"
        except engines.OcrFailed as exc:
            _set_failed(session, document_id, patient_id, str(exc) or _GENERIC_ERROR)
            return "failed"
        except Exception as exc:  # engine crash: log the type only, never the text (health data)
            log.error("OCR engine %s failed on document %s: %s", engine.name, document_id, type(exc).__name__)
            _set_failed(session, document_id, patient_id, _GENERIC_ERROR)
            return "failed"

        found = parser.extract(result.text)
        for mapped in found.values:
            session.add(LabResult(patient_id=patient_id, document_id=document_id, test_code=mapped.test_code,
                                  value=mapped.value, unit=mapped.unit, value_as_reported=mapped.value_as_reported,
                                  unit_as_reported=mapped.unit_as_reported, source="ocr"))
        confidence = None if result.confidence is None else round(max(0.0, min(100.0, result.confidence)), 2)
        # Even when nothing was found: the patient reviews it and types every value in.
        # Only while still ours: if the document was reset meanwhile, nothing is saved.
        finished = session.scalar(update(Document)
                                  .where(Document.document_id == document_id, Document.patient_id == patient_id,
                                         Document.status == "processing")
                                  .values(status="ready_for_review", ocr_engine=engine.name[:50],
                                          ocr_text=result.text, ocr_confidence=confidence, error_message=None,
                                          processed_at=datetime.now(timezone.utc))
                                  .returning(Document.document_id))
        if finished is None:
            session.rollback()
            return "skipped"
        audit.log_event(session, "DATA_CREATED", patient_id=patient_id, table="lab_results",
                        document_id=document_id, source="ocr", values_found=len(found.values),
                        skipped_lines=found.skipped_lines)
        session.commit()
        return "ready_for_review"
