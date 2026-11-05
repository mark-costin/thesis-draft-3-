"""Lab documents: upload, list, download, delete (Sprint 3).

Upload checks the real file type from its first bytes (JPEG, PNG, PDF), the size (10 MB), and
the SHA-256 (the same file twice is refused), then stores it encrypted under a UUID name.
Upload answers 202: OCR runs after the response (backend/ocr/job.py) and moves the document from
'pending' to 'ready_for_review' (or 'failed'). With OCR_ENGINE=none it stays 'pending'.

Review: when OCR is done (status ready_for_review) the patient sees the values it read and the
required inputs still missing, fixes values with PATCH /labs/{id} or removes them with
DELETE /labs/{id}, then confirms (adding any lab values OCR missed) or rejects the document.
Only confirmed values are used by the models.
"""

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Request, Response, UploadFile, status
from sqlalchemy.orm import Session

from backend import labs, readiness, services
from backend.config import settings
from backend.database import get_db
from backend.dependencies import CurrentUser, get_current_user
from backend.middleware import limiter
from backend.ocr import job
from backend.routes.labs import lab_out
from backend.schemas import DocumentConfirm, DocumentResponse, DocumentReviewResponse

router = APIRouter(prefix="/documents", tags=["documents"])

NO_STORE = {"Cache-Control": "no-store"}
_NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
_EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "application/pdf": "pdf"}


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=DocumentResponse)
@limiter.limit("10/minute")
def upload(request: Request, response: Response, background_tasks: BackgroundTasks, file: UploadFile = File(...),
           current: CurrentUser = Depends(get_current_user),
           db: Session = Depends(get_db)) -> DocumentResponse:
    # The uploaded file name and the browser's content type are ignored on purpose
    data = file.file.read(services.MAX_UPLOAD_BYTES + 1)  # one byte more tells us it is too big
    try:
        row = services.upload_document(db, current.user_id, current.patient_id, data)
    except services.UnsupportedFile as exc:
        raise HTTPException(422, str(exc))
    except services.DuplicateFile:
        # The same file again: if OCR is on but never started for it (e.g. Redis was down), this
        # is a retry. With OCR off, 'pending' is the normal state, so it stays a duplicate.
        row = services.pending_duplicate(db, current.patient_id, data) if settings.ocr_engine != "none" else None
        if row is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "You already uploaded this file")
    job.start(row.document_id, current.patient_id, background_tasks)  # never fails the upload
    response.headers.update(NO_STORE)
    response.headers["Location"] = f"/documents/{row.document_id}"  # poll it for the OCR status
    return DocumentResponse.model_validate(row)


@router.get("", response_model=list[DocumentResponse])
def list_documents(response: Response, background_tasks: BackgroundTasks,
                   current: CurrentUser = Depends(get_current_user),
                   db: Session = Depends(get_db)) -> list[DocumentResponse]:
    job.requeue_stuck(db, current.patient_id, background_tasks)  # crashed OCR jobs start again
    response.headers.update(NO_STORE)
    return [DocumentResponse.model_validate(row) for row in services.list_documents(db, current.patient_id)]


@router.get("/{document_id}", response_model=DocumentResponse)
def read_document(document_id: int, response: Response, background_tasks: BackgroundTasks,
                  current: CurrentUser = Depends(get_current_user),
                  db: Session = Depends(get_db)) -> DocumentResponse:
    job.requeue_stuck(db, current.patient_id, background_tasks)  # the patient polls this one
    try:
        row = services.get_document(db, current.patient_id, document_id)
    except services.NotFound:
        raise _NOT_FOUND
    response.headers.update(NO_STORE)
    return DocumentResponse.model_validate(row)


@router.get("/{document_id}/file")
def download_document(document_id: int, current: CurrentUser = Depends(get_current_user),
                      db: Session = Depends(get_db)) -> Response:
    try:
        row, data = services.read_document_file(db, current.user_id, current.patient_id, document_id)
    except services.NotFound:
        raise _NOT_FOUND
    except services.TamperedFile:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                            "The stored file failed its integrity check. Please upload it again.")
    filename = f"document-{document_id}.{_EXTENSIONS[row.mime_type]}"
    return Response(content=data, media_type=row.mime_type, headers={
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    })


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_document(document_id: int, current: CurrentUser = Depends(get_current_user),
                    db: Session = Depends(get_db)) -> Response:
    try:
        services.delete_document(db, current.user_id, current.patient_id, document_id)
    except services.NotFound:
        raise _NOT_FOUND
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _review_response(db: Session, patient_id: int, document, rows) -> DocumentReviewResponse:
    # Values still under review count as given, so the list shows only what OCR did not find
    found = frozenset(readiness.lab_key(row.test_code) for row in rows)
    missing = readiness.readiness(db, patient_id, assume_present=found)["missing_required"]
    return DocumentReviewResponse(document=DocumentResponse.model_validate(document),
                                  lab_results=[lab_out(row) for row in rows], missing_required=missing)


def _wrong_status(exc: services.WrongStatus) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT,
                         f"This document is '{exc.status}'. Only documents that are ready for review "
                         "can be confirmed or rejected.")


@router.get("/{document_id}/review", response_model=DocumentReviewResponse)
def review(document_id: int, response: Response, current: CurrentUser = Depends(get_current_user),
           db: Session = Depends(get_db)) -> DocumentReviewResponse:
    """The values OCR read from this document, with the unit conversion and range flags."""
    try:
        document, rows = services.review_document(db, current.patient_id, document_id)
    except services.NotFound:
        raise _NOT_FOUND
    response.headers.update(NO_STORE)
    return _review_response(db, current.patient_id, document, rows)


@router.post("/{document_id}/confirm", response_model=DocumentReviewResponse)
def confirm(document_id: int, response: Response, body: DocumentConfirm | None = None,
            current: CurrentUser = Depends(get_current_user),
            db: Session = Depends(get_db)) -> DocumentReviewResponse:
    body = body or DocumentConfirm()
    typed = []
    for number, value in enumerate(body.typed_values, start=1):
        try:
            typed.append((labs.convert(value.test_code, value.value, value.unit), value.collected_on))
        except labs.MappingError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Typed value {number}: {exc}")
    try:
        document, rows = services.confirm_document(db, current.user_id, current.patient_id,
                                                   document_id, body.collected_on, typed)
    except services.NotFound:
        raise _NOT_FOUND
    except services.WrongStatus as exc:
        raise _wrong_status(exc)
    except services.NothingToConfirm:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "There are no values left to confirm. Reject the document instead.")
    response.headers.update(NO_STORE)
    return _review_response(db, current.patient_id, document, rows)


@router.post("/{document_id}/reject", response_model=DocumentResponse)
def reject(document_id: int, response: Response, current: CurrentUser = Depends(get_current_user),
           db: Session = Depends(get_db)) -> DocumentResponse:
    try:
        document = services.reject_document(db, current.user_id, current.patient_id, document_id)
    except services.NotFound:
        raise _NOT_FOUND
    except services.WrongStatus as exc:
        raise _wrong_status(exc)
    response.headers.update(NO_STORE)
    return DocumentResponse.model_validate(document)
