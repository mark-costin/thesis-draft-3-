"""Lifestyle entries and health history (Sprint 2). Responses hold health data, so they carry
"Cache-Control: no-store" (see routes/vitals.py)."""

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from backend import services
from backend.database import get_db
from backend.dependencies import CurrentUser, get_current_user
from backend.schemas import (
    HealthHistoryResponse,
    HealthHistoryUpdate,
    LifestyleCreate,
    LifestyleResponse,
)

router = APIRouter(tags=["lifestyle"])

NO_STORE = {"Cache-Control": "no-store"}
_NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, "Lifestyle entry not found")


@router.post("/lifestyle", status_code=status.HTTP_201_CREATED, response_model=LifestyleResponse)
def add_lifestyle(body: LifestyleCreate, response: Response, current: CurrentUser = Depends(get_current_user),
                  db: Session = Depends(get_db)) -> LifestyleResponse:
    row = services.create_lifestyle(db, current.user_id, current.patient_id, body.model_dump())
    response.headers.update(NO_STORE)
    response.headers["Location"] = f"/lifestyle/{row.lifestyle_id}"
    return LifestyleResponse.model_validate(row)


@router.get("/lifestyle", response_model=list[LifestyleResponse])
def list_lifestyle(response: Response, limit: int = Query(default=20, ge=1, le=100),
                   current: CurrentUser = Depends(get_current_user),
                   db: Session = Depends(get_db)) -> list[LifestyleResponse]:
    response.headers.update(NO_STORE)
    return [LifestyleResponse.model_validate(row) for row in services.list_lifestyle(db, limit)]


@router.get("/lifestyle/{lifestyle_id}", response_model=LifestyleResponse)
def read_lifestyle(lifestyle_id: int, response: Response, current: CurrentUser = Depends(get_current_user),
                   db: Session = Depends(get_db)) -> LifestyleResponse:
    try:
        row = services.get_lifestyle(db, lifestyle_id)
    except services.NotFound:
        raise _NOT_FOUND
    response.headers.update(NO_STORE)
    return LifestyleResponse.model_validate(row)


@router.delete("/lifestyle/{lifestyle_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_lifestyle(lifestyle_id: int, current: CurrentUser = Depends(get_current_user),
                     db: Session = Depends(get_db)) -> Response:
    try:
        services.delete_lifestyle(db, current.user_id, current.patient_id, lifestyle_id)
    except services.NotFound:
        raise _NOT_FOUND
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/health-history", response_model=HealthHistoryResponse)
def read_health_history(response: Response, current: CurrentUser = Depends(get_current_user),
                        db: Session = Depends(get_db)) -> HealthHistoryResponse:
    response.headers.update(NO_STORE)
    row = services.get_health_history(db)
    if row is None:  # not answered yet: every question is still "don't know"
        return HealthHistoryResponse(updated_at=None)
    return HealthHistoryResponse.model_validate(row)


@router.put("/health-history", response_model=HealthHistoryResponse)
def save_health_history(body: HealthHistoryUpdate, response: Response,
                        current: CurrentUser = Depends(get_current_user),
                        db: Session = Depends(get_db)) -> HealthHistoryResponse:
    try:
        row = services.save_health_history(db, current.user_id, current.patient_id, body.model_dump())
    except services.NotApplicable as exc:
        raise HTTPException(422, str(exc))
    response.headers.update(NO_STORE)
    return HealthHistoryResponse.model_validate(row)
