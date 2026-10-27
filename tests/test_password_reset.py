"""Self-service password reset: a token works once and expires after 1 hour."""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from backend import mailer
from backend.config import settings
from backend.database import SessionLocal
from backend.main import app
from backend.models import PasswordResetToken, User
from backend.services import MAX_FAILED_ATTEMPTS

client = TestClient(app)
OLD_PASSWORD = "correct horse battery"
NEW_PASSWORD = "a brand new passphrase"


@pytest.fixture
def account():
    email = f"reset-{uuid.uuid4().hex[:8]}@test.local"
    response = client.post("/auth/register", json={
        "email": email, "password": OLD_PASSWORD, "first_name": "Ana", "last_name": "Reyes",
        "date_of_birth": "1979-11-30", "biological_sex": "Female",
        "privacy_consent": True, "consent_version": settings.privacy_notice_version,
    })
    assert response.status_code == 201
    yield email
    with SessionLocal() as session:  # deleting the user also deletes its reset tokens
        session.execute(delete(User).where(func.lower(User.email) == email))
        session.commit()


@pytest.fixture
def outbox(monkeypatch):
    """Catches reset emails instead of printing them; each item is (email, token)."""
    sent = []
    monkeypatch.setattr(mailer, "send_password_reset", lambda email, token: sent.append((email, token)))
    return sent


def request_reset(email):
    return client.post("/auth/password-reset/request", json={"email": email})


def confirm(token, password=NEW_PASSWORD):
    return client.post("/auth/password-reset/confirm", json={"token": token, "new_password": password})


def login(email, password):
    return client.post("/auth/login", json={"email": email, "password": password})


def tokens_of(email):
    with SessionLocal() as session:
        user_id = session.scalar(select(User.user_id).where(User.email == email))
        return session.scalars(select(PasswordResetToken)
                               .where(PasswordResetToken.user_id == user_id)).all()


def test_request_sends_one_link_and_stores_only_a_hash(account, outbox):
    response = request_reset(account)
    assert response.status_code == 202
    assert len(outbox) == 1 and outbox[0][0] == account
    token = outbox[0][1]
    assert token not in response.text

    [row] = tokens_of(account)
    assert row.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert row.used_at is None
    assert row.expires_at - row.created_at == timedelta(hours=1)


def test_unknown_email_gets_the_same_answer_and_no_email(account, outbox):
    known = request_reset(account)
    unknown = request_reset("nobody-" + account)
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()
    assert len(outbox) == 1


def test_reset_changes_the_password(account, outbox):
    request_reset(account)
    assert confirm(outbox[0][1]).status_code == 204
    assert login(account, OLD_PASSWORD).status_code == 401
    assert login(account, NEW_PASSWORD).status_code == 200


def test_token_works_only_once(account, outbox):
    request_reset(account)
    token = outbox[0][1]
    assert confirm(token).status_code == 204
    assert confirm(token, "yet another passphrase").status_code == 400
    assert login(account, NEW_PASSWORD).status_code == 200


def test_expired_token_is_rejected(account, outbox):
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:  # made 2 hours ago, expired 90 minutes ago (30-minute life)
        user_id = session.scalar(select(User.user_id).where(User.email == account))
        session.add(PasswordResetToken(
            user_id=user_id, token_hash=hashlib.sha256(token.encode()).hexdigest(),
            created_at=now - timedelta(hours=2), expires_at=now - timedelta(minutes=90),
        ))
        session.commit()
    assert confirm(token).status_code == 400
    assert login(account, OLD_PASSWORD).status_code == 200


def test_new_request_cancels_the_older_link(account, outbox):
    request_reset(account)
    request_reset(account)
    first, second = outbox[0][1], outbox[1][1]
    assert confirm(first).status_code == 400
    assert confirm(second).status_code == 204


def test_reset_logs_out_existing_sessions(account, outbox):
    old_token = login(account, OLD_PASSWORD).json()["access_token"]
    headers = {"Authorization": f"Bearer {old_token}"}
    assert client.get("/auth/me", headers=headers).status_code == 200

    request_reset(account)
    confirm(outbox[0][1])
    assert client.get("/auth/me", headers=headers).status_code == 401


def test_reset_unlocks_a_locked_account(account, outbox):
    for _ in range(MAX_FAILED_ATTEMPTS):
        login(account, "wrong password")
    assert login(account, OLD_PASSWORD).status_code == 423

    request_reset(account)
    confirm(outbox[0][1])
    assert login(account, NEW_PASSWORD).status_code == 200


@pytest.mark.parametrize("token, password, expected", [
    ("x" * 43, NEW_PASSWORD, 400),   # well-formed but unknown
    ("short", NEW_PASSWORD, 422),
    ("x" * 43, "short", 422),        # new password follows the registration rule
], ids=["unknown-token", "short-token", "short-password"])
def test_bad_confirm_requests(token, password, expected):
    assert confirm(token, password).status_code == expected
