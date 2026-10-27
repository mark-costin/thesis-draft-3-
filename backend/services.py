"""Business logic used by the routes. Routes handle HTTP; these functions handle the database."""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.database import set_request_identity
from backend import mailer
from backend.models import PasswordResetToken, Patient, User
from backend.schemas import RegisterRequest
from backend.security import (
    burn_dummy_check,
    create_access_token,
    hash_password,
    verify_and_update_password,
)

# Lockout policy: this many wrong passwords in a row locks the account for LOCKOUT_MINUTES
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15

# Reset tokens: single use, valid for 1 hour (also enforced by reset_tokens_expiry_check)
RESET_TOKEN_LIFETIME = timedelta(hours=1)


class EmailAlreadyRegistered(Exception):
    pass


class InvalidCredentials(Exception):
    """Wrong email or wrong password. One error for both, so emails cannot be probed."""


class InvalidResetToken(Exception):
    """Unknown, already used, or expired. One error for all three."""


class AccountLocked(Exception):
    def __init__(self, locked_until: datetime):
        super().__init__(locked_until)
        self.locked_until = locked_until


def register_patient(db: Session, data: RegisterRequest) -> None:
    """Creates the users row and the patients row in one transaction.

    Order matters for RLS: the users row comes first (users has no RLS), then app.user_id
    is set to the new user, because the patients policy only accepts a row whose user_id
    matches app.user_id.
    """
    exists = db.scalar(select(User.user_id).where(func.lower(User.email) == data.email))
    if exists is not None:
        raise EmailAlreadyRegistered

    user = User(email=data.email, password_hash=hash_password(data.password))
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        # Two sign-ups with the same email at the same moment: ux_users_email_lower stops the second.
        # Any other constraint error is a real bug, so it is raised as is.
        if getattr(exc.orig, "sqlstate", None) == "23505":  # unique_violation
            raise EmailAlreadyRegistered from exc
        raise

    set_request_identity(db, user_id=user.user_id)
    db.add(Patient(
        user_id=user.user_id,
        first_name=data.first_name,
        last_name=data.last_name,
        date_of_birth=data.date_of_birth,
        biological_sex=data.biological_sex,
        privacy_consent=data.privacy_consent,
        consent_version=data.consent_version,
    ))
    db.commit()


def login(db: Session, email: str, password: str) -> str:
    """Checks the password, applies the lockout, and returns a signed access token."""
    now = datetime.now(timezone.utc)

    # FOR UPDATE: two logins at once cannot both read the same failed_attempts count
    user = db.scalars(
        select(User).where(func.lower(User.email) == email).with_for_update()
    ).one_or_none()

    if user is None:
        burn_dummy_check(password)
        db.rollback()
        raise InvalidCredentials

    if user.locked_until is not None and user.locked_until > now:
        db.rollback()
        raise AccountLocked(user.locked_until)

    matches, new_hash = verify_and_update_password(password, user.password_hash)

    if not matches:
        user.failed_attempts += 1
        if user.failed_attempts >= MAX_FAILED_ATTEMPTS:
            user.failed_attempts = 0
            user.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
            db.commit()
            raise AccountLocked(user.locked_until)
        db.commit()
        raise InvalidCredentials

    user.failed_attempts = 0
    user.locked_until = None
    user.last_login_at = now
    if new_hash:
        user.password_hash = new_hash

    # patients has RLS: the patient row is visible once app.user_id is this user
    set_request_identity(db, user_id=user.user_id)
    patient_id = db.scalar(select(Patient.patient_id).where(Patient.user_id == user.user_id))
    if patient_id is None:
        db.rollback()
        raise InvalidCredentials

    token = create_access_token(user.user_id, patient_id, user.token_version)
    db.commit()
    return token


def revoke_all_tokens(db: Session, user_id: int) -> None:
    """Logout everywhere: every token issued before this call stops working."""
    db.execute(update(User).where(User.user_id == user_id)
               .values(token_version=User.token_version + 1))
    db.commit()


def _hash_reset_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def request_password_reset(db: Session, email: str) -> None:
    """Creates a reset token and sends the link. Does nothing visible if the email is unknown,
    so the response is the same whether or not an account exists."""
    user = db.scalars(select(User).where(func.lower(User.email) == email)).one_or_none()
    if user is None:
        db.rollback()
        return

    # Only the newest link works: drop this user's unused older tokens
    db.execute(delete(PasswordResetToken).where(
        PasswordResetToken.user_id == user.user_id,
        PasswordResetToken.used_at.is_(None),
    ))

    token = secrets.token_urlsafe(32)  # 256 bits; only its SHA-256 hash is stored
    db.add(PasswordResetToken(
        user_id=user.user_id,
        token_hash=_hash_reset_token(token),
        # Database time, so it matches created_at exactly and passes the 1-hour CHECK
        expires_at=func.now() + RESET_TOKEN_LIFETIME,
    ))
    db.commit()
    mailer.send_password_reset(user.email, token)


def confirm_password_reset(db: Session, token: str, new_password: str) -> None:
    """Uses the token once, sets the new password, unlocks the account and logs out
    every existing session."""
    row = db.scalars(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == _hash_reset_token(token),
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at > func.now(),
        ).with_for_update()
    ).one_or_none()
    if row is None:
        db.rollback()
        raise InvalidResetToken

    row.used_at = func.now()  # med_app may update only this column
    db.execute(update(User).where(User.user_id == row.user_id).values(
        password_hash=hash_password(new_password),
        password_changed_at=func.now(),
        token_version=User.token_version + 1,
        failed_attempts=0,
        locked_until=None,
    ))
    db.commit()
