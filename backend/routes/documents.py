"""Lab documents: upload, list, download, delete (Sprint 3).

Upload checks the real file type from its first bytes (JPEG, PNG, PDF), the size (10 MB), and
the SHA-256 (the same file twice is refused), then stores it encrypted under a UUID name.
OCR is a separate task: new documents stay 'pending' until the worker picks them up.
"""

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile, status
from sqlalchemy.orm import Session

from backend import services
from backend.database import get_db
from backend.dependencies import CurrentUser, get_current_user
from backend.middleware import limiter
from backend.schemas import DocumentResponse

router = APIRouter(prefix="/documents", tags=["documents"])

NO_STORE = {"Cache-Control": "no-store"}
_NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
_EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "application/pdf": "pdf"}


@router.post("", status_code=status.HTTP_201_CREATED, response_model=DocumentResponse)
@limiter.limit("10/minute")
def upload(request: Request, response: Response, file: UploadFile = File(...),
           current: CurrentUser = Depends(get_current_user),
           db: Session = Depends(get_db)) -> DocumentResponse:
    # The uploaded file name and the browser's content type are ignored on purpose
    data = file.file.read(services.MAX_UPLOAD_BYTES + 1)  # one byte more tells us it is too big
    try:
        row = services.upload_document(db, current.user_id, current.patient_id, data)
    except services.UnsupportedFile as exc:
        raise HTTPException(422, str(exc))
    except services.DuplicateFile:
        raise HTTPException(status.HTTP_409_CONFLICT, "You already uploaded this file")
    response.headers.update(NO_STORE)
    response.headers["Location"] = f"/documents/{row.document_id}"
    return DocumentResponse.model_validate(row)


@router.get("", response_model=list[DocumentResponse])
def list_documents(response: Response, current: CurrentUser = Depends(get_current_user),
                   db: Session = Depends(get_db)) -> list[DocumentResponse]:
    response.headers.update(NO_STORE)
    return [DocumentResponse.model_validate(row) for row in services.list_documents(db)]


@router.get("/{document_id}", response_model=DocumentResponse)
def read_document(document_id: int, response: Response, current: CurrentUser = Depends(get_current_user),
                  db: Session = Depends(get_db)) -> DocumentResponse:
    try:
        row = services.get_document(db, document_id)
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
