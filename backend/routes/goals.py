"""Goals: the patient's own preventive goals, optionally taken from a care plan (Sprint 2)."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from backend import services
from backend.database import get_db
from backend.dependencies import CurrentUser, get_current_user
from backend.schemas import GoalCreate, GoalResponse, GoalUpdate

router = APIRouter(prefix="/goals", tags=["goals"])

NO_STORE = {"Cache-Control": "no-store"}
_NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, "Goal not found")


@router.post("", status_code=status.HTTP_201_CREATED, response_model=GoalResponse)
def add_goal(body: GoalCreate, response: Response, current: CurrentUser = Depends(get_current_user),
             db: Session = Depends(get_db)) -> GoalResponse:
    try:
        row = services.create_goal(db, current.user_id, current.patient_id, body.model_dump())
    except services.UnknownResult:
        raise HTTPException(422, "result_id does not match one of your risk results")
    response.headers.update(NO_STORE)
    response.headers["Location"] = f"/goals/{row.goal_id}"
    return GoalResponse.model_validate(row)


@router.get("", response_model=list[GoalResponse])
def list_goals(response: Response,
               status_filter: Literal["open", "completed", "all"] = Query(default="all", alias="status"),
               current: CurrentUser = Depends(get_current_user),
               db: Session = Depends(get_db)) -> list[GoalResponse]:
    completed = {"open": False, "completed": True, "all": None}[status_filter]
    response.headers.update(NO_STORE)
    return [GoalResponse.model_validate(row) for row in services.list_goals(db, completed)]


@router.get("/{goal_id}", response_model=GoalResponse)
def read_goal(goal_id: int, response: Response, current: CurrentUser = Depends(get_current_user),
              db: Session = Depends(get_db)) -> GoalResponse:
    try:
        row = services.get_goal(db, goal_id)
    except services.NotFound:
        raise _NOT_FOUND
    response.headers.update(NO_STORE)
    return GoalResponse.model_validate(row)


@router.patch("/{goal_id}", response_model=GoalResponse)
def edit_goal(goal_id: int, body: GoalUpdate, response: Response,
              current: CurrentUser = Depends(get_current_user),
              db: Session = Depends(get_db)) -> GoalResponse:
    changes = body.model_dump(include=body.model_fields_set)
    try:
        row = services.update_goal(db, current.user_id, current.patient_id, goal_id, changes)
    except services.NotFound:
        raise _NOT_FOUND
    response.headers.update(NO_STORE)
    return GoalResponse.model_validate(row)


@router.delete("/{goal_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_goal(goal_id: int, current: CurrentUser = Depends(get_current_user),
                db: Session = Depends(get_db)) -> Response:
    try:
        services.delete_goal(db, current.user_id, current.patient_id, goal_id)
    except services.NotFound:
        raise _NOT_FOUND
    return Response(status_code=status.HTTP_204_NO_CONTENT)
