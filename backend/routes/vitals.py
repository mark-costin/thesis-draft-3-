"""Vitals: blood pressure, heart rate, weight, height, waist, blood sugar, SpO2 (Sprint 2).

Every response here holds health data, so it carries "Cache-Control: no-store": browsers and
proxies do not keep a copy, and the idempotency store keeps only the status and Location.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from backend import services
from backend.database import get_db
from backend.dependencies import CurrentUser, get_current_user
from backend.schemas import VitalsCreate, VitalsResponse

router = APIRouter(prefix="/vitals", tags=["vitals"])

NO_STORE = {"Cache-Control": "no-store"}
_NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, "Vitals entry not found")


@router.post("", status_code=status.HTTP_201_CREATED, response_model=VitalsResponse)
def add_vitals(body: VitalsCreate, response: Response, current: CurrentUser = Depends(get_current_user),
               db: Session = Depends(get_db)) -> VitalsResponse:
    row = services.create_vitals(db, current.user_id, current.patient_id, body.model_dump())
    response.headers.update(NO_STORE)
    response.headers["Location"] = f"/vitals/{row.vitals_id}"
    return VitalsResponse.model_validate(row)


@router.get("", response_model=list[VitalsResponse])
def list_vitals(response: Response, limit: int = Query(default=20, ge=1, le=100),
                current: CurrentUser = Depends(get_current_user),
                db: Session = Depends(get_db)) -> list[VitalsResponse]:
    response.headers.update(NO_STORE)
    return [VitalsResponse.model_validate(row) for row in services.list_vitals(db, limit)]


@router.get("/{vitals_id}", response_model=VitalsResponse)
def read_vitals(vitals_id: int, response: Response, current: CurrentUser = Depends(get_current_user),
                db: Session = Depends(get_db)) -> VitalsResponse:
    try:
        row = services.get_vitals(db, vitals_id)
    except services.NotFound:
        raise _NOT_FOUND
    response.headers.update(NO_STORE)
    return VitalsResponse.model_validate(row)


@router.delete("/{vitals_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_vitals(vitals_id: int, current: CurrentUser = Depends(get_current_user),
                  db: Session = Depends(get_db)) -> Response:
    try:
        services.delete_vitals(db, current.user_id, current.patient_id, vitals_id)
    except services.NotFound:
        raise _NOT_FOUND
    return Response(status_code=status.HTTP_204_NO_CONTENT)
