"""Password hashing (Argon2id) and login tokens (JWT)."""

from datetime import datetime, timedelta, timezone

import jwt
from pwdlib import PasswordHash

from backend.config import settings

# Argon2id with pwdlib's recommended settings
password_hash = PasswordHash.recommended()

ALGORITHM = "HS256"

# Checked against when the email does not exist, so a wrong email takes as long as a wrong
# password and response time does not reveal which accounts exist.
_DUMMY_HASH = password_hash.hash("dummy-password-for-timing")


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return password_hash.verify(plain_password, hashed_password)


def verify_and_update_password(plain_password: str, hashed_password: str) -> tuple[bool, str | None]:
    """Returns (matches, new_hash). new_hash is set when the stored hash uses old settings."""
    return password_hash.verify_and_update(plain_password, hashed_password)


def burn_dummy_check(plain_password: str) -> None:
    password_hash.verify(plain_password, _DUMMY_HASH)


def create_access_token(user_id: int, patient_id: int, token_version: int) -> str:
    """The token carries the IDs the database uses for Row-Level Security.

    token_version must still match users.token_version when the token is used;
    raising it in the database revokes every token issued before (logout everywhere).
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "patient_id": patient_id,
        "tv": token_version,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Checks the signature and expiry. Raises ValueError for any bad token."""
    try:
        payload = jwt.decode(
            token, settings.jwt_secret_key, algorithms=[ALGORITHM],
            options={"require": ["exp", "iat", "sub"]},
        )
        payload["sub"] = int(payload["sub"])
        if not isinstance(payload.get("patient_id"), int) or not isinstance(payload.get("tv"), int):
            raise ValueError("Invalid token")
        return payload
    except jwt.ExpiredSignatureError as exc:
        raise ValueError("Token has expired") from exc
    except (jwt.InvalidTokenError, KeyError, TypeError) as exc:
        raise ValueError("Invalid token") from exc
