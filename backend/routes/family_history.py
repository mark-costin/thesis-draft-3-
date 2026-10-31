"""Family history (pedigree) and the Family History Risk Score (Sprint 2).

Every add, edit or delete recalculates the FHRS for all four diseases (backend/fhrs.py).
A patient with no known family history uses POST /family-history/fhrs to record Baseline.
Responses hold health data, so they carry "Cache-Control: no-store".
"""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from backend import services
from backend.database import get_db
from backend.dependencies import CurrentUser, get_current_user
from backend.schemas import FamilyEntryCreate, FamilyEntryResponse, FamilyEntryUpdate, FhrsResponse

router = APIRouter(prefix="/family-history", tags=["family history"])

NO_STORE = {"Cache-Control": "no-store"}
_NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, "Family history entry not found")
_DUPLICATE = HTTPException(status.HTTP_409_CONFLICT, "This relative is already listed with this diagnosis")


@router.get("", response_model=list[FamilyEntryResponse])
def list_entries(response: Response, current: CurrentUser = Depends(get_current_user),
                 db: Session = Depends(get_db)) -> list[FamilyEntryResponse]:
    response.headers.update(NO_STORE)
    return [FamilyEntryResponse.model_validate(row) for row in services.list_family_history(db)]


@router.post("", status_code=status.HTTP_201_CREATED, response_model=FamilyEntryResponse)
def add_entry(body: FamilyEntryCreate, response: Response, current: CurrentUser = Depends(get_current_user),
              db: Session = Depends(get_db)) -> FamilyEntryResponse:
    try:
        row = services.add_family_entry(db, current.user_id, current.patient_id, body.model_dump())
    except services.DuplicateEntry:
        raise _DUPLICATE
    response.headers.update(NO_STORE)
    response.headers["Location"] = f"/family-history/{row.record_id}"
    return FamilyEntryResponse.model_validate(row)


@router.get("/fhrs", response_model=list[FhrsResponse])
def read_fhrs(response: Response, current: CurrentUser = Depends(get_current_user),
              db: Session = Depends(get_db)) -> list[FhrsResponse]:
    """The latest score per disease. Empty until the patient adds an entry or confirms none."""
    response.headers.update(NO_STORE)
    return [FhrsResponse.model_validate(row) for row in services.latest_fhrs(db)]


@router.post("/fhrs", response_model=list[FhrsResponse])
def confirm_fhrs(response: Response, current: CurrentUser = Depends(get_current_user),
                 db: Session = Depends(get_db)) -> list[FhrsResponse]:
    """Saves the score as it stands. With no entries, every disease is recorded as Baseline."""
    rows = services.confirm_family_history(db, current.user_id, current.patient_id)
    response.headers.update(NO_STORE)
    return [FhrsResponse.model_validate(row) for row in rows]


@router.get("/{record_id}", response_model=FamilyEntryResponse)
def read_entry(record_id: int, response: Response, current: CurrentUser = Depends(get_current_user),
               db: Session = Depends(get_db)) -> FamilyEntryResponse:
    try:
        row = services.get_family_entry(db, record_id)
    except services.NotFound:
        raise _NOT_FOUND
    response.headers.update(NO_STORE)
    return FamilyEntryResponse.model_validate(row)


@router.patch("/{record_id}", response_model=FamilyEntryResponse)
def edit_entry(record_id: int, body: FamilyEntryUpdate, response: Response,
               current: CurrentUser = Depends(get_current_user),
               db: Session = Depends(get_db)) -> FamilyEntryResponse:
    try:
        row = services.update_family_entry(db, current.user_id, current.patient_id, record_id,
                                           body.model_dump(include=body.model_fields_set))
    except services.NotFound:
        raise _NOT_FOUND
    except services.DuplicateEntry:
        raise _DUPLICATE
    response.headers.update(NO_STORE)
    return FamilyEntryResponse.model_validate(row)


@router.delete("/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_entry(record_id: int, current: CurrentUser = Depends(get_current_user),
                 db: Session = Depends(get_db)) -> Response:
    try:
        services.delete_family_entry(db, current.user_id, current.patient_id, record_id)
    except services.NotFound:
        raise _NOT_FOUND
    return Response(status_code=status.HTTP_204_NO_CONTENT)
