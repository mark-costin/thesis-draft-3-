"""Account endpoints: register (Sprint 1). Login and password reset are added next."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend import services
from backend.database import get_db
from backend.schemas import RegisterRequest, RegisterResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=RegisterResponse)
def register(body: RegisterRequest, db: Session = Depends(get_db)) -> RegisterResponse:
    try:
        services.register_patient(db, body)
    except services.EmailAlreadyRegistered:
        raise HTTPException(status.HTTP_409_CONFLICT, "This email is already registered")
    # The password and IDs are never echoed back
    return RegisterResponse(email=body.email, consent_version=body.consent_version)
