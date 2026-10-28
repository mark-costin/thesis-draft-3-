"""Shared test setup.

Every test runs against med_db_test (TEST_DATABASE_URL in .env), connected as med_app,
so RLS and the grants are tested exactly as the app will hit them. Never med_db.
"""

import uuid
from datetime import date

import pytest
from sqlalchemy import create_engine, delete
from sqlalchemy.engine import make_url

from backend.config import settings
from backend.database import SessionLocal, set_request_identity
from backend.middleware import limiter
from backend.models import Patient, User

# ---------------------------------------------------------------------------
# Safety checks: refuse to run against the real database or as postgres
# ---------------------------------------------------------------------------
if not settings.test_database_url:
    raise RuntimeError("TEST_DATABASE_URL is missing from .env (see .env.example)")

_test_url = make_url(settings.test_database_url)
if _test_url.database == make_url(settings.database_url).database:
    raise RuntimeError("TEST_DATABASE_URL points to the real database; use med_db_test")
if _test_url.username != "med_app":
    raise RuntimeError("Tests must connect as med_app, never postgres")

test_engine = create_engine(settings.test_database_url, pool_pre_ping=True)

# The auth tests log in many times from the same test client, which would hit the per-IP
# rate limits. They are switched off here and switched on only in tests/test_middleware.py.
limiter.enabled = False

# Point the app's session factory at the test database. The RLS identity hook in
# database.py is attached to SessionLocal, so it keeps working.
SessionLocal.configure(bind=test_engine)


@pytest.fixture
def db():
    """A fresh session per test; anything left open is rolled back."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _register(email_tag: str) -> tuple[int, int]:
    """Registers a patient the same way the app will: users row, then app.user_id, then patients row."""
    with SessionLocal() as session:
        user = User(email=f"{email_tag}-{uuid.uuid4().hex[:8]}@test.local", password_hash="$argon2id$test")
        session.add(user)
        session.flush()

        set_request_identity(session, user_id=user.user_id)  # patients RLS needs app.user_id first
        patient = Patient(
            user_id=user.user_id, first_name="Test", last_name=email_tag,
            date_of_birth=date(1990, 1, 1), biological_sex="Male",
            privacy_consent=True, consent_version="test",
        )
        session.add(patient)
        session.flush()
        ids = (user.user_id, patient.patient_id)
        session.commit()
    return ids


@pytest.fixture
def engine():
    return test_engine


@pytest.fixture
def two_patients(db):
    """Creates patient A and patient B, then deletes both accounts after the test."""
    a = _register("patient-a")
    b = _register("patient-b")
    yield {"A": a, "B": b}
    db.rollback()  # release the test's locks first, or the delete below would wait on them
    with SessionLocal() as session:
        # users has no RLS; deleting the user cascades to the patient and all their rows
        session.execute(delete(User).where(User.user_id.in_([a[0], b[0]])))
        session.commit()
