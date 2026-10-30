"""Questionnaires: PHQ-9, GAD-7, PSQI, mMRC, CAT, ACT (Sprint 2).

SAFETY: when PHQ-9 item 9 (thoughts of self-harm) is above 0, the response says
requires_crisis_prompt = true and carries the crisis hotline, so the screen can show it at once.
Responses hold health data, so they carry "Cache-Control: no-store".
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from backend import questionnaires, services
from backend.database import get_db
from backend.dependencies import CurrentUser, get_current_user
from backend.models import QuestionnaireResponse
from backend.schemas import CrisisResources, QuestionnaireResponseOut, QuestionnaireSubmit

router = APIRouter(prefix="/questionnaires", tags=["questionnaires"])

NO_STORE = {"Cache-Control": "no-store"}
_NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, "Questionnaire response not found")


def _out(row: QuestionnaireResponse) -> QuestionnaireResponseOut:
    crisis = bool(row.requires_crisis_prompt)
    return QuestionnaireResponseOut(
        response_id=row.response_id,
        instrument=row.instrument,
        answers=row.item_responses,
        total_score=row.total_score,
        band=questionnaires.band(row.instrument, row.total_score),
        requires_crisis_prompt=crisis,
        crisis_resources=CrisisResources(**questionnaires.CRISIS_RESOURCES) if crisis else None,
        completed_at=row.completed_at,
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=QuestionnaireResponseOut)
def submit(body: QuestionnaireSubmit, response: Response, current: CurrentUser = Depends(get_current_user),
           db: Session = Depends(get_db)) -> QuestionnaireResponseOut:
    row = services.submit_questionnaire(db, current.user_id, current.patient_id, body.instrument, body.answers)
    response.headers.update(NO_STORE)
    response.headers["Location"] = f"/questionnaires/{row.response_id}"
    return _out(row)


@router.get("", response_model=list[QuestionnaireResponseOut])
def list_responses(response: Response,
                   instrument: Literal["PHQ9", "GAD7", "PSQI", "MMRC", "CAT", "ACT"] | None = None,
                   limit: int = Query(default=20, ge=1, le=100),
                   current: CurrentUser = Depends(get_current_user),
                   db: Session = Depends(get_db)) -> list[QuestionnaireResponseOut]:
    response.headers.update(NO_STORE)
    return [_out(row) for row in services.list_questionnaires(db, instrument, limit)]


@router.get("/{response_id}", response_model=QuestionnaireResponseOut)
def read_response(response_id: int, response: Response, current: CurrentUser = Depends(get_current_user),
                  db: Session = Depends(get_db)) -> QuestionnaireResponseOut:
    try:
        row = services.get_questionnaire(db, response_id)
    except services.NotFound:
        raise _NOT_FOUND
    response.headers.update(NO_STORE)
    return _out(row)


@router.delete("/{response_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_response(response_id: int, current: CurrentUser = Depends(get_current_user),
                    db: Session = Depends(get_db)) -> Response:
    try:
        services.delete_questionnaire(db, current.user_id, current.patient_id, response_id)
    except services.NotFound:
        raise _NOT_FOUND
    return Response(status_code=status.HTTP_204_NO_CONTENT)
