"""Lab results: manual entry, list, read, delete, and a mapping preview (Sprint 3).

Values are stored in one standard unit per test (lab_unit_check). A value typed in mmol/L or
umol/L is converted in backend/labs.py, and the original is kept in value_as_reported.
"""

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from backend import labs, services
from backend.database import get_db
from backend.dependencies import CurrentUser, get_current_user
from backend.models import LabResult
from backend.schemas import LabCreate, LabMapRequest, LabMapResponse, LabResponse, LabTestCode

router = APIRouter(prefix="/labs", tags=["labs"])

NO_STORE = {"Cache-Control": "no-store"}
_NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, "Lab result not found")


def _out(row: LabResult) -> LabResponse:
    low, high = labs.EXPECTED_RANGE[row.test_code]
    data = LabResponse.model_validate(row)
    data.out_of_expected_range = not (Decimal(str(low)) <= row.value <= Decimal(str(high)))
    return data


@router.post("/map", response_model=LabMapResponse)
def map_lab_line(body: LabMapRequest, response: Response,
                 current: CurrentUser = Depends(get_current_user)) -> LabMapResponse:
    """Shows how a report line would be saved (test code, converted value). Saves nothing."""
    try:
        mapped = labs.map_field(body.label, body.value, body.unit)
    except labs.MappingError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    response.headers.update(NO_STORE)
    return LabMapResponse(**mapped.__dict__)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=LabResponse)
def add_lab(body: LabCreate, response: Response, current: CurrentUser = Depends(get_current_user),
            db: Session = Depends(get_db)) -> LabResponse:
    try:
        mapped = labs.convert(body.test_code, body.value, body.unit)
    except labs.MappingError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    row = services.create_lab(db, current.user_id, current.patient_id, mapped, body.collected_on)
    response.headers.update(NO_STORE)
    response.headers["Location"] = f"/labs/{row.lab_result_id}"
    return _out(row)


@router.get("", response_model=list[LabResponse])
def list_labs(response: Response, test_code: LabTestCode | None = None,
              limit: int = Query(default=50, ge=1, le=200),
              current: CurrentUser = Depends(get_current_user),
              db: Session = Depends(get_db)) -> list[LabResponse]:
    response.headers.update(NO_STORE)
    return [_out(row) for row in services.list_labs(db, test_code, limit)]


@router.get("/{lab_result_id}", response_model=LabResponse)
def read_lab(lab_result_id: int, response: Response, current: CurrentUser = Depends(get_current_user),
             db: Session = Depends(get_db)) -> LabResponse:
    try:
        row = services.get_lab(db, lab_result_id)
    except services.NotFound:
        raise _NOT_FOUND
    response.headers.update(NO_STORE)
    return _out(row)


@router.delete("/{lab_result_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_lab(lab_result_id: int, current: CurrentUser = Depends(get_current_user),
               db: Session = Depends(get_db)) -> Response:
    try:
        services.delete_lab(db, current.user_id, current.patient_id, lab_result_id)
    except services.NotFound:
        raise _NOT_FOUND
    return Response(status_code=status.HTTP_204_NO_CONTENT)
