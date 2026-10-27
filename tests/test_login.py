"""Login, JWT, lockout and token_version revocation."""

import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, update

from backend.config import settings
from backend.database import SessionLocal
from backend.main import app
from backend.models import User
from backend.security import ALGORITHM
from backend.services import MAX_FAILED_ATTEMPTS

client = TestClient(app)
PASSWORD = "correct horse battery"


@pytest.fixture
def account():
    """Registers a patient through the API; the account is deleted after the test."""
    email = f"login-{uuid.uuid4().hex[:8]}@test.local"
    response = client.post("/auth/register", json={
        "email": email, "password": PASSWORD, "first_name": "Maria", "last_name": "Santos",
        "date_of_birth": "1985-03-02", "biological_sex": "Female",
        "privacy_consent": True, "consent_version": settings.privacy_notice_version,
    })
    assert response.status_code == 201
    yield email
    with SessionLocal() as session:
        session.execute(delete(User).where(func.lower(User.email) == email))
        session.commit()


def login(email, password=PASSWORD):
    return client.post("/auth/login", json={"email": email, "password": password})


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def get_user(email) -> User:
    with SessionLocal() as session:
        return session.scalars(select(User).where(User.email == email)).one()


def test_login_returns_token_that_opens_me(account):
    response = login(account)
    assert response.status_code == 200
    data = response.json()
    assert data["token_type"] == "bearer"
    assert data["expires_in"] == settings.access_token_expire_minutes * 60

    me = client.get("/auth/me", headers=bearer(data["access_token"]))
    assert me.status_code == 200
    assert me.json() == {"email": account, "first_name": "Maria", "last_name": "Santos",
                         "consent_version": settings.privacy_notice_version}

    user = get_user(account)
    assert user.last_login_at is not None and user.failed_attempts == 0


def test_token_carries_ids_but_no_personal_data(account):
    token = login(account).json()["access_token"]
    payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[ALGORITHM])
    assert set(payload) == {"sub", "patient_id", "tv", "iat", "exp"}
    assert account not in token


def test_email_is_case_insensitive(account):
    assert login("  " + account.upper()).status_code == 200


def test_wrong_password_and_unknown_email_look_the_same(account):
    wrong = login(account, "wrong password")
    unknown = login("nobody-" + account, PASSWORD)
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    assert get_user(account).failed_attempts == 1


def test_repeated_wrong_passwords_lock_the_account(account):
    for _ in range(MAX_FAILED_ATTEMPTS - 1):
        assert login(account, "wrong password").status_code == 401
    assert login(account, "wrong password").status_code == 423   # this one locks it
    assert login(account).status_code == 423                      # even the right password
    assert get_user(account).locked_until > datetime.now(timezone.utc)


def test_lock_expires(account):
    for _ in range(MAX_FAILED_ATTEMPTS):
        login(account, "wrong password")
    with SessionLocal() as session:  # pretend the lockout time has passed
        session.execute(update(User).where(User.email == account)
                        .values(locked_until=datetime.now(timezone.utc) - timedelta(seconds=1)))
        session.commit()
    assert login(account).status_code == 200
    user = get_user(account)
    assert user.locked_until is None and user.failed_attempts == 0


def test_successful_login_resets_the_count(account):
    for _ in range(MAX_FAILED_ATTEMPTS - 1):
        login(account, "wrong password")
    assert login(account).status_code == 200
    for _ in range(MAX_FAILED_ATTEMPTS - 1):
        assert login(account, "wrong password").status_code == 401   # not locked yet


def test_logout_revokes_old_tokens(account):
    token = login(account).json()["access_token"]
    assert client.get("/auth/me", headers=bearer(token)).status_code == 200
    assert client.post("/auth/logout", headers=bearer(token)).status_code == 204
    assert client.get("/auth/me", headers=bearer(token)).status_code == 401
    assert get_user(account).token_version == 1

    new_token = login(account).json()["access_token"]
    assert client.get("/auth/me", headers=bearer(new_token)).status_code == 200


def forged(account, **changes):
    user = get_user(account)
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user.user_id), "patient_id": 1, "tv": user.token_version,
               "iat": now, "exp": now + timedelta(minutes=5)}
    key = changes.pop("key", settings.jwt_secret_key)
    payload.update(changes)
    payload = {k: v for k, v in payload.items() if v is not None}
    return jwt.encode(payload, key, algorithm=ALGORITHM)


@pytest.mark.parametrize("make_headers", [
    lambda acc: {},
    lambda acc: bearer("not-a-jwt"),
    lambda acc: bearer(forged(acc, key="x" * 40)),                                     # wrong key
    lambda acc: bearer(forged(acc, exp=datetime.now(timezone.utc) - timedelta(minutes=1))),  # expired
    lambda acc: bearer(forged(acc, tv=None)),                                           # no version
    lambda acc: bearer(forged(acc, tv=99)),                                             # wrong version
    lambda acc: {"Authorization": "Basic abc"},
], ids=["no-token", "garbage", "wrong-key", "expired", "no-version", "wrong-version", "basic-auth"])
def test_bad_tokens_are_rejected(account, make_headers):
    assert client.get("/auth/me", headers=make_headers(account)).status_code == 401


def test_login_rejects_extra_fields(account):
    response = client.post("/auth/login", json={"email": account, "password": PASSWORD, "patient_id": 1})
    assert response.status_code == 422
