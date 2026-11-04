"""Assessment: what is still missing (Sprint 2) and the risk run itself (Sprint 5).

POST /assessment/run runs the four models and saves everything in one transaction. Results are
health data, so every response here is Cache-Control: no-store.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from backend import feature_builder, readiness, risk
from backend.database import get_db
from backend.dependencies import CurrentUser, get_current_user
from backend.middleware import limiter
from backend.schemas import AssessmentResponse, AssessmentSummary, ReadinessResponse

router = APIRouter(prefix="/assessment", tags=["assessment"])

NO_STORE = {"Cache-Control": "no-store"}


@router.get("/readiness", response_model=ReadinessResponse)
def check_readiness(response: Response, current: CurrentUser = Depends(get_current_user),
                    db: Session = Depends(get_db)) -> dict:
    response.headers.update(NO_STORE)
    return readiness.readiness(db, current.patient_id)


@router.post("/run", status_code=status.HTTP_201_CREATED, response_model=AssessmentResponse)
@limiter.limit("10/minute")
def run(request: Request, response: Response, current: CurrentUser = Depends(get_current_user),
        db: Session = Depends(get_db)) -> dict:
    try:
        assessment_id = risk.run_assessment(db, current.user_id, current.patient_id)
    except risk.ModelsOff:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "The risk models are not available yet. Please try again later.")
    except feature_builder.MissingRequired:
        db.rollback()
        missing = readiness.readiness(db, current.patient_id)["missing_required"]
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "message": "Some required information is still missing. Please enter it first.",
            "missing_required": missing,
        })
    response.headers.update(NO_STORE)
    response.headers["Location"] = f"/assessment/{assessment_id}"
    return risk.view(db, current.patient_id, assessment_id)


@router.get("", response_model=list[AssessmentSummary])
def list_assessments(response: Response, limit: int = Query(default=20, ge=1, le=100),
                     current: CurrentUser = Depends(get_current_user),
                     db: Session = Depends(get_db)) -> list[dict]:
    response.headers.update(NO_STORE)
    return risk.list_assessments(db, current.patient_id, limit)


@router.get("/{assessment_id}", response_model=AssessmentResponse)
def read_assessment(assessment_id: int, response: Response, current: CurrentUser = Depends(get_current_user),
                    db: Session = Depends(get_db)) -> dict:
    try:
        result = risk.view(db, current.patient_id, assessment_id)
    except risk.NotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Assessment not found")
    response.headers.update(NO_STORE)
    return result
