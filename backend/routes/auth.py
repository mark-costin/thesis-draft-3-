"""Account endpoints: register, login, current user, logout (Sprint 1). Password reset comes next."""

from datetime import datetime, timezone
from math import ceil

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import services
from backend.config import settings
from backend.database import get_db
from backend.dependencies import CurrentUser, get_current_user
from backend.models import Patient, User
from backend.schemas import (
    LoginRequest,
    MeResponse,
    RegisterRequest,
    RegisterResponse,
    TokenResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=RegisterResponse)
def register(body: RegisterRequest, db: Session = Depends(get_db)) -> RegisterResponse:
    try:
        services.register_patient(db, body)
    except services.EmailAlreadyRegistered:
        raise HTTPException(status.HTTP_409_CONFLICT, "This email is already registered")
    # The password and IDs are never echoed back
    return RegisterResponse(email=body.email, consent_version=body.consent_version)


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    try:
        token = services.login(db, body.email, body.password)
    except services.InvalidCredentials:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    except services.AccountLocked as locked:
        minutes = max(1, ceil((locked.locked_until - datetime.now(timezone.utc)).total_seconds() / 60))
        raise HTTPException(
            status.HTTP_423_LOCKED,
            f"Too many failed attempts. Try again in {minutes} minute(s) or reset your password.",
        )
    return TokenResponse(access_token=token, expires_in=settings.access_token_expire_minutes * 60)


@router.get("/me", response_model=MeResponse)
def me(current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> MeResponse:
    user = db.get(User, current.user_id)
    patient = db.scalars(select(Patient)).one()  # RLS returns only this patient's row
    return MeResponse(email=user.email, first_name=patient.first_name,
                      last_name=patient.last_name, consent_version=patient.consent_version)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> Response:
    """Logs out on every device by revoking all of this account's tokens."""
    services.revoke_all_tokens(db, current.user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
