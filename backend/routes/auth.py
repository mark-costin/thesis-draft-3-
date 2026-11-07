"""Account endpoints: register, login, current user, logout and password reset (Sprint 1)."""

from datetime import datetime, timezone
from math import ceil

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import google_auth, services
from backend.config import settings
from backend.database import get_db
from backend.dependencies import CurrentUser, get_current_user
from backend.middleware import limiter
from backend.models import Patient, User
from backend.schemas import (
    ChangePasswordRequest,
    GoogleSignInRequest,
    LoginRequest,
    MeResponse,
    NoticeAcceptance,
    PasswordResetConfirm,
    PasswordResetRequest,
    RegisterRequest,
    RegisterResponse,
    TokenResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])
# The login token and the patient's name must not stay in a browser or proxy cache. On login this
# also keeps the token out of the idempotency store (middleware.py saves no body for no-store).
NO_STORE = {"Cache-Control": "no-store"}


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
def login(request: Request, response: Response, body: LoginRequest,
          db: Session = Depends(get_db)) -> TokenResponse:
    response.headers.update(NO_STORE)
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
def me(response: Response, current: CurrentUser = Depends(get_current_user),
       db: Session = Depends(get_db)) -> MeResponse:
    response.headers.update(NO_STORE)
    user = db.get(User, current.user_id)
    # Filtered on the token's patient_id; RLS is the second layer
    patient = db.scalars(select(Patient).where(Patient.patient_id == current.patient_id)).one()
    return MeResponse(email=user.email, first_name=patient.first_name,
                      last_name=patient.last_name, consent_version=patient.consent_version,
                      consent_required=patient.consent_version != settings.privacy_notice_version)


@router.post("/accept-privacy-notice", status_code=status.HTTP_204_NO_CONTENT)
def accept_privacy_notice(body: NoticeAcceptance, current: CurrentUser = Depends(get_current_user),
                          db: Session = Depends(get_db)) -> Response:
    """The patient accepts the current notice after /auth/me said consent_required."""
    services.accept_privacy_notice(db, current.user_id, current.patient_id, body.consent_version)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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


def _locked(locked: services.AccountLocked) -> HTTPException:
    minutes = max(1, ceil((locked.locked_until - datetime.now(timezone.utc)).total_seconds() / 60))
    return HTTPException(status.HTTP_423_LOCKED,
                         f"Too many failed attempts. Try again in {minutes} minute(s) or reset your password.")


@router.post("/google", response_model=TokenResponse)
@limiter.limit("10/minute")
def google_sign_in(request: Request, response: Response, body: GoogleSignInRequest,
                   db: Session = Depends(get_db)):
    """Sign in (or sign up) with Google. The browser sends the ID token from the Google button.

    - Known Google account: 200 with a token.
    - New Google account without `signup`: 404 with code "signup_required" and the names Google
      gave, so the screen can ask for birth date, sex and consent, then call again with `signup`.
    - New Google account with `signup`: the account is created (no password), 200 with a token.
    - The Google email already has a password account: 409 (it is not linked automatically).
    """
    response.headers["Cache-Control"] = "no-store"  # the token must not stay in a cache
    try:
        token, _ = services.google_sign_in(db, body.credential, body.signup)
    except google_auth.GoogleSignInOff:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Sign in with Google is not set up")
    except google_auth.InvalidGoogleToken:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Google sign-in failed. Please try again.")
    except services.EmailUsesPassword:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "This email already has an account. Log in with your email and password.")
    except services.SignUpNeeded as needed:
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, headers={"Cache-Control": "no-store"}, content={
            "detail": "No account yet. Add your birth date and sex and accept the privacy notice to create one.",
            "code": "signup_required",
            "first_name": needed.identity.given_name,
            "last_name": needed.identity.family_name,
        })
    return TokenResponse(access_token=token, expires_in=settings.access_token_expire_minutes * 60)


@router.post("/change-password", response_model=TokenResponse)
@limiter.limit("5/minute")  # it checks the password
def change_password(request: Request, response: Response, body: ChangePasswordRequest,
                    current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Checks the current password, sets the new one and logs out every other device.
    Returns a new token, so this session keeps working."""
    response.headers["Cache-Control"] = "no-store"
    try:
        token = services.change_password(db, current.user_id, current.patient_id,
                                         body.current_password, body.new_password)
    except services.NoPassword:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "This account signs in with Google and has no password. "
                            "Use Forgot password to add one.")
    except services.WrongPassword:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "The current password is incorrect.")
    except services.AccountLocked as locked:
        raise _locked(locked)
    return TokenResponse(access_token=token, expires_in=settings.access_token_expire_minutes * 60)
