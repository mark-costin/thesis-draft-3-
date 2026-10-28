"""Register with privacy consent: creates users + patients rows with the consent version."""

import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from backend.config import settings
from backend.database import SessionLocal, set_request_identity
from backend.main import app
from backend.models import Patient, User
from backend.security import verify_password

client = TestClient(app)
PASSWORD = "correct horse battery"


@pytest.fixture
def new_email():
    """A unique email; any account made with it is deleted after the test."""
    email = f"reg-{uuid.uuid4().hex[:8]}@test.local"
    yield email
    with SessionLocal() as session:
        session.execute(delete(User).where(func.lower(User.email) == email))
        session.commit()


def body(email, /, **changes):
    data = {
        "email": email,
        "password": PASSWORD,
        "first_name": "Juan",
        "last_name": "Dela Cruz",
        "date_of_birth": "1990-05-17",
        "biological_sex": "Male",
        "privacy_consent": True,
        "consent_version": settings.privacy_notice_version,
    }
    data.update(changes)
    return data


def years_ago(years: int, extra_days: int = 0) -> str:
    today = date.today()
    try:
        born = today.replace(year=today.year - years)
    except ValueError:  # 29 February
        born = today.replace(year=today.year - years, day=28)
    return date.fromordinal(born.toordinal() + extra_days).isoformat()


def test_register_creates_user_and_patient_with_consent(db, new_email):
    response = client.post("/auth/register", json=body(new_email))
    assert response.status_code == 201
    assert response.json() == {"email": new_email, "consent_version": settings.privacy_notice_version}

    user = db.scalars(select(User).where(User.email == new_email)).one()
    assert user.password_hash.startswith("$argon2id$")
    assert PASSWORD not in user.password_hash
    assert verify_password(PASSWORD, user.password_hash)
    assert user.failed_attempts == 0 and user.token_version == 0

    set_request_identity(db, user_id=user.user_id)
    patient = db.scalars(select(Patient)).one()  # RLS: only this user's patient row is visible
    assert patient.user_id == user.user_id
    assert patient.privacy_consent is True
    assert patient.consent_version == settings.privacy_notice_version
    assert patient.consent_at is not None


def test_response_never_returns_password_or_ids(new_email):
    response = client.post("/auth/register", json=body(new_email))
    text = response.text
    assert PASSWORD not in text and "argon2" not in text
    assert "user_id" not in text and "patient_id" not in text


def test_email_is_case_insensitive_and_unique(new_email):
    assert client.post("/auth/register", json=body(new_email)).status_code == 201
    again = client.post("/auth/register", json=body("  " + new_email.upper() + " "))
    assert again.status_code == 409


def test_failed_registration_leaves_no_rows(db, new_email):
    client.post("/auth/register", json=body(new_email, privacy_consent=False))
    assert db.scalar(select(func.count()).select_from(User).where(User.email == new_email)) == 0


@pytest.mark.parametrize("changes", [
    {"privacy_consent": False},
    {"consent_version": "0.9"},
    {"password": "short"},
    {"email": "not-an-email"},
    {"biological_sex": "Other"},
    {"first_name": "   "},
    {"date_of_birth": "1899-12-31"},
    {"date_of_birth": years_ago(0, extra_days=1)},    # tomorrow
    {"date_of_birth": years_ago(18, extra_days=1)},   # turns 18 tomorrow
], ids=["no-consent", "old-notice", "short-password", "bad-email", "bad-sex",
        "blank-name", "before-1900", "future", "under-18"])
def test_invalid_input_is_rejected(new_email, changes):
    assert client.post("/auth/register", json=body(new_email, **changes)).status_code == 422


def test_exactly_18_today_is_accepted(new_email):
    response = client.post("/auth/register", json=body(new_email, date_of_birth=years_ago(18)))
    assert response.status_code == 201


@pytest.mark.parametrize("field", ["user_id", "patient_id", "token_version", "is_admin"])
def test_extra_fields_are_rejected(new_email, field):
    assert client.post("/auth/register", json=body(new_email, **{field: 1})).status_code == 422
