"""Middleware: CORS, SlowAPI rate limit, idempotency keys."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from backend.config import settings
from backend.database import SessionLocal
from backend.main import app
from backend.middleware import REPLAY_HEADER, limiter
from backend.models import User

client = TestClient(app)
ALLOWED_ORIGIN = settings.cors_origins[0]


@pytest.fixture
def emails():
    """Collects test emails; their accounts are deleted after the test."""
    made = []

    def new():
        made.append(f"mw-{uuid.uuid4().hex[:8]}@test.local")
        return made[-1]

    yield new
    with SessionLocal() as session:
        session.execute(delete(User).where(func.lower(User.email).in_(made)))
        session.commit()


def register_body(email):
    return {"email": email, "password": "correct horse battery", "first_name": "Andres",
            "last_name": "Bonifacio", "date_of_birth": "1988-11-30", "biological_sex": "Male",
            "privacy_consent": True, "consent_version": settings.privacy_notice_version}


def count_users(email):
    with SessionLocal() as session:
        return session.scalar(select(func.count()).select_from(User).where(User.email == email))


def key():
    return "test-" + uuid.uuid4().hex


# --- Idempotency ---------------------------------------------------------------------------

def test_same_idempotency_key_twice_creates_one_record(emails):
    email, k = emails(), key()
    first = client.post("/auth/register", json=register_body(email), headers={"Idempotency-Key": k})
    second = client.post("/auth/register", json=register_body(email), headers={"Idempotency-Key": k})
    assert first.status_code == second.status_code == 201
    assert second.json() == first.json()
    assert second.headers.get(REPLAY_HEADER) == "true"
    assert REPLAY_HEADER not in first.headers
    assert count_users(email) == 1


def test_without_a_key_the_repeat_runs_again(emails):
    email = emails()
    assert client.post("/auth/register", json=register_body(email)).status_code == 201
    assert client.post("/auth/register", json=register_body(email)).status_code == 409


def test_same_key_with_a_different_body_is_refused(emails):
    k = key()
    a, b = emails(), emails()
    assert client.post("/auth/register", json=register_body(a), headers={"Idempotency-Key": k}).status_code == 201
    assert client.post("/auth/register", json=register_body(b), headers={"Idempotency-Key": k}).status_code == 422
    assert count_users(b) == 0


def test_keys_are_scoped_to_the_caller(emails):
    k = key()
    a, b = emails(), emails()
    assert client.post("/auth/register", json=register_body(a), headers={"Idempotency-Key": k}).status_code == 201
    other_caller = {"Idempotency-Key": k, "Authorization": "Bearer someone-else"}
    response = client.post("/auth/register", json=register_body(b), headers=other_caller)
    assert response.status_code == 201 and REPLAY_HEADER not in response.headers


def test_error_responses_are_replayed_too(emails):
    k = key()
    bad = register_body(emails()) | {"privacy_consent": False}
    first = client.post("/auth/register", json=bad, headers={"Idempotency-Key": k})
    second = client.post("/auth/register", json=bad, headers={"Idempotency-Key": k})
    assert first.status_code == second.status_code == 422
    assert second.headers.get(REPLAY_HEADER) == "true"


@pytest.mark.parametrize("bad_key", ["short", "has spaces in it", "x" * 129, "semi;colon-key"])
def test_malformed_keys_are_rejected(emails, bad_key):
    email = emails()
    response = client.post("/auth/register", json=register_body(email), headers={"Idempotency-Key": bad_key})
    assert response.status_code == 400
    assert count_users(email) == 0


def test_get_requests_ignore_the_key():
    response = client.get("/health", headers={"Idempotency-Key": key()})
    assert response.status_code == 200 and REPLAY_HEADER not in response.headers


# --- Rate limiting -------------------------------------------------------------------------

@pytest.fixture
def rate_limits_on():
    limiter.reset()
    limiter.enabled = True
    yield
    limiter.enabled = False
    limiter.reset()


def test_login_is_rate_limited_per_ip(rate_limits_on):
    body = {"email": f"nobody-{uuid.uuid4().hex[:8]}@test.local", "password": "whatever"}
    codes = [client.post("/auth/login", json=body).status_code for _ in range(11)]
    assert codes[:10] == [401] * 10
    assert codes[10] == 429


def test_reset_request_is_rate_limited(rate_limits_on):
    body = {"email": f"nobody-{uuid.uuid4().hex[:8]}@test.local"}
    codes = [client.post("/auth/password-reset/request", json=body).status_code for _ in range(6)]
    assert codes[:5] == [202] * 5
    assert codes[5] == 429


# --- CORS ----------------------------------------------------------------------------------

def preflight(origin):
    return client.options("/auth/login", headers={
        "Origin": origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,idempotency-key",
    })


def test_frontend_origin_is_allowed():
    response = preflight(ALLOWED_ORIGIN)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert "idempotency-key" in response.headers["access-control-allow-headers"].lower()


def test_other_origins_are_refused():
    response = preflight("https://evil.example")
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_simple_request_gets_cors_header_only_for_allowed_origin():
    allowed = client.get("/health", headers={"Origin": ALLOWED_ORIGIN})
    other = client.get("/health", headers={"Origin": "https://evil.example"})
    assert allowed.headers.get("access-control-allow-origin") == ALLOWED_ORIGIN
    assert "access-control-allow-origin" not in other.headers
