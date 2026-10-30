"""Assessment: what is still missing before the four models can run (Sprint 2).
The risk run itself comes in Sprint 5."""

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from backend import readiness
from backend.database import get_db
from backend.dependencies import CurrentUser, get_current_user
from backend.schemas import ReadinessResponse

router = APIRouter(prefix="/assessment", tags=["assessment"])


@router.get("/readiness", response_model=ReadinessResponse)
def check_readiness(response: Response, current: CurrentUser = Depends(get_current_user),
                    db: Session = Depends(get_db)) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return readiness.readiness(db)
