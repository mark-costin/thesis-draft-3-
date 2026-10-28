"""FastAPI dependencies shared by the routes."""

from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from backend.database import get_db, set_request_identity
from backend.models import User
from backend.security import decode_access_token

_bearer = HTTPBearer(auto_error=False)

_UNAUTHORIZED = HTTPException(
    status.HTTP_401_UNAUTHORIZED,
    "Not logged in or session expired",
    headers={"WWW-Authenticate": "Bearer"},
)


@dataclass(frozen=True)
class CurrentUser:
    user_id: int
    patient_id: int


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> CurrentUser:
    """Verifies the JWT, checks it was not revoked, and sets the RLS identity for this request.

    The identity comes only from the signed token, never from the URL, body or other headers.
    FastAPI gives the route the same db session, so every query after this runs as this patient.
    """
    if credentials is None:
        raise _UNAUTHORIZED
    try:
        payload = decode_access_token(credentials.credentials)
    except ValueError:
        raise _UNAUTHORIZED

    user = db.get(User, payload["sub"])
    # A raised token_version (logout everywhere, password reset) makes older tokens invalid
    if user is None or user.token_version != payload["tv"]:
        raise _UNAUTHORIZED

    set_request_identity(db, user_id=user.user_id, patient_id=payload["patient_id"])
    return CurrentUser(user_id=user.user_id, patient_id=payload["patient_id"])
