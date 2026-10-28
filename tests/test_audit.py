"""Audit logging through log_audit().

med_app cannot read audit_ledger (by design), so these tests record each call the app makes
while still letting it reach the database. A failing log_audit() call therefore still fails
the test. The hash chain itself is checked by the owner in DBeaver:
    SELECT public.verify_audit_chain();   -- NULL means the chain is intact
"""

import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, text
from sqlalchemy.exc import ProgrammingError

from backend import audit, mailer
from backend.config import settings
from backend.database import SessionLocal
from backend.main import app
from backend.models import User
from backend.services import MAX_FAILED_ATTEMPTS

client = TestClient(app)
PASSWORD = "correct horse battery"


@pytest.fixture
def events(monkeypatch):
    """Every audit entry the app writes during the test, as (event_type, user_id, patient_id, details)."""
    recorded = []
    real_log_event = audit.log_event

    def recording(db, event_type, *, user_id=None, patient_id=None, **details):
        audit_id = real_log_event(db, event_type, user_id=user_id, patient_id=patient_id, **details)
        assert isinstance(audit_id, int)
        recorded.append((event_type, user_id, patient_id, details))
        return audit_id

    monkeypatch.setattr(audit, "log_event", recording)
    return recorded


@pytest.fixture
def email():
    address = f"audit-{uuid.uuid4().hex[:8]}@test.local"
    yield address
    with SessionLocal() as session:
        session.execute(delete(User).where(func.lower(User.email) == address))
        session.commit()


def register(email):
    response = client.post("/auth/register", json={
        "email": email, "password": PASSWORD, "first_name": "Jose", "last_name": "Rizal",
        "date_of_birth": "1990-06-19", "biological_sex": "Male",
        "privacy_consent": True, "consent_version": settings.privacy_notice_version,
    })
    assert response.status_code == 201


def login(email, password=PASSWORD):
    return client.post("/auth/login", json={"email": email, "password": password})


def types(events):
    return [e[0] for e in events]


def test_register_is_logged_with_both_ids(events, email):
    register(email)
    [(event_type, user_id, patient_id, details)] = events
    assert event_type == "REGISTER"
    assert isinstance(user_id, int) and isinstance(patient_id, int)
    assert details == {"consent_version": settings.privacy_notice_version}


def test_login_success_and_failure_are_logged(events, email):
    register(email)
    login(email, "wrong password")
    login(email)
    assert types(events) == ["REGISTER", "LOGIN_FAILED", "LOGIN_SUCCESS"]
    assert events[1][3] == {"reason": "wrong_password", "failed_attempts": 1}
    assert events[2][2] is not None  # LOGIN_SUCCESS carries the patient_id


def test_unknown_email_is_logged_without_the_email(events, email):
    login("nobody-" + email)
    [(event_type, user_id, patient_id, details)] = events
    assert (event_type, user_id, patient_id) == ("LOGIN_FAILED", None, None)
    assert details == {"reason": "unknown_email"}


def test_lockout_is_logged(events, email):
    register(email)
    for _ in range(MAX_FAILED_ATTEMPTS):
        login(email, "wrong password")
    login(email)  # right password while locked
    assert types(events) == (["REGISTER"] + ["LOGIN_FAILED"] * (MAX_FAILED_ATTEMPTS - 1)
                             + ["ACCOUNT_LOCKED", "LOGIN_FAILED"])
    assert events[-1][3] == {"reason": "locked"}


def test_logout_is_logged(events, email):
    register(email)
    token = login(email).json()["access_token"]
    client.post("/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert types(events)[-1] == "LOGOUT"


def test_password_reset_is_logged(events, email, monkeypatch):
    outbox = []
    monkeypatch.setattr(mailer, "send_password_reset", lambda to, token: outbox.append(token))
    register(email)
    client.post("/auth/password-reset/request", json={"email": email})
    client.post("/auth/password-reset/confirm", json={"token": outbox[0], "new_password": "new passphrase 1"})
    assert types(events)[-2:] == ["PASSWORD_RESET_REQUESTED", "PASSWORD_CHANGED"]
    assert events[-1][3] == {"via": "reset_link", "sessions_revoked": True}


def test_rejected_registration_writes_no_entry(events, email):
    register(email)
    client.post("/auth/register", json={"email": email})  # invalid body, 422
    assert types(events) == ["REGISTER"]


def test_payloads_never_hold_secrets(events, email, monkeypatch):
    outbox = []
    monkeypatch.setattr(mailer, "send_password_reset", lambda to, token: outbox.append(token))
    register(email)
    login(email, "wrong password")
    login(email)
    client.post("/auth/password-reset/request", json={"email": email})
    dumped = json.dumps([e[3] for e in events])
    assert email not in dumped and PASSWORD not in dumped and outbox[0] not in dumped


def test_log_event_refuses_unknown_types_and_secret_keys(db):
    with pytest.raises(ValueError):
        audit.log_event(db, "SOMETHING_ELSE")
    with pytest.raises(ValueError):
        audit.log_event(db, "LOGIN_FAILED", email="x@test.local")


@pytest.mark.parametrize("sql", [
    "UPDATE audit_ledger SET payload = '{}'::jsonb",
    "DELETE FROM audit_ledger",
    "INSERT INTO audit_ledger (event_type, entry_seal) VALUES ('LOGIN_FAILED', repeat('0', 64))",
], ids=["update", "delete", "direct-insert"])
def test_app_cannot_touch_the_ledger_directly(db, sql):
    with pytest.raises(ProgrammingError, match="permission denied"):
        db.execute(text(sql))


def test_app_cannot_run_the_admin_chain_check(db):
    with pytest.raises(ProgrammingError, match="permission denied"):
        db.execute(text("SELECT public.verify_audit_chain()"))
