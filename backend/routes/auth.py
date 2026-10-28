"""Account endpoints: register, login, current user, logout and password reset (Sprint 1)."""

from datetime import datetime, timezone
from math import ceil

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import services
from backend.config import settings
from backend.database import get_db
from backend.dependencies import CurrentUser, get_current_user
from backend.middleware import limiter
from backend.models import Patient, User
from backend.schemas import (
    LoginRequest,
    MeResponse,
    PasswordResetConfirm,
    PasswordResetRequest,
    RegisterRequest,
    RegisterResponse,
    TokenResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=RegisterResponse)
@limiter.limit("5/minute")
def register(request: Request, body: RegisterRequest, db: Session = Depends(get_db)) -> RegisterResponse:
    try:
        services.register_patient(db, body)
    except services.EmailAlreadyRegistered:
        raise HTTPException(status.HTTP_409_CONFLICT, "This email is already registered")
    # The password and IDs are never echoed back
    return RegisterResponse(email=body.email, consent_version=body.consent_version)


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")  # per IP; the per-account lockout is in services.login
def login(request: Request, body: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
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
    services.revoke_all_tokens(db, current.user_id, current.patient_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/password-reset/request", status_code=status.HTTP_202_ACCEPTED)
@limiter.limit("5/minute")  # each call can send an email
def password_reset_request(request: Request, body: PasswordResetRequest,
                           db: Session = Depends(get_db)) -> dict:
    services.request_password_reset(db, body.email)
    # Same answer for every email, so this cannot be used to find out who has an account
    return {"detail": "If that email is registered, a reset link has been sent. It expires in 1 hour."}


@router.post("/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("10/minute")
def password_reset_confirm(request: Request, body: PasswordResetConfirm,
                           db: Session = Depends(get_db)) -> Response:
    try:
        services.confirm_password_reset(db, body.token, body.new_password)
    except services.InvalidResetToken:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "This reset link is invalid, already used, or expired. Request a new one.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
