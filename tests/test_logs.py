"""Sprint 6: application logs contain no health data, only IDs and event names.

A patient goes through the main flow with values nobody else would type (marker words and
numbers). Everything the app writes to the log (every logger, INFO and up, including SQLAlchemy)
and to the console is collected, and none of the markers may appear in it.
SQLAlchemy's DEBUG level (it prints result rows) is a debugging tool and is never used on a server.
"""

import logging
import re
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, text
from sqlalchemy.exc import DBAPIError

from backend import mailer
from backend.config import settings
from backend.database import SessionLocal
from backend.main import app
from backend.middleware import SafeErrorMiddleware
from backend.models import User

client = TestClient(app)
PASSWORD = "Marker-Password-4471"


@pytest.fixture
def email():
    address = f"logmarker-{uuid.uuid4().hex[:8]}@test.local"
    yield address
    with SessionLocal() as session:
        session.execute(delete(User).where(func.lower(User.email) == address))
        session.commit()


@pytest.fixture
def captured(caplog, capfd, monkeypatch):
    """Returns a function that gives everything logged or printed so far."""
    monkeypatch.setattr(settings, "print_reset_links", False)  # the server setting
    caplog.set_level(logging.INFO)

    def everything() -> str:
        out, err = capfd.readouterr()
        text_ = "\n".join([r.getMessage() for r in caplog.records] + [out, err])
        # SQLAlchemy timings ("[cached since 7.83s ago]") could match a number marker by chance
        return re.sub(r"\[(generated in|cached since)[^\]]*\]", "", text_)

    return everything


def run_patient_flow(email):
    """Register, log in, and save one of each kind of health data, with marker values."""
    assert client.post("/auth/register", json={
        "email": email, "password": PASSWORD, "first_name": "Quokkafield", "last_name": "Wombatson",
        "date_of_birth": "1984-02-29", "biological_sex": "Female",
        "privacy_consent": True, "consent_version": settings.privacy_notice_version,
    }).status_code == 201
    assert client.post("/auth/login", json={"email": email, "password": "Wrong-Marker-Guess"}).status_code == 401
    token = client.post("/auth/login", json={"email": email, "password": PASSWORD}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    steps = [
        client.patch("/profile", headers=headers, json={"last_name": "Numbatley"}),
        client.post("/vitals", headers=headers, json={"sbp": 173, "dbp": 109, "spo2": 93.7}),
        client.post("/labs", headers=headers, json={"test_code": "FPG", "value": 7.83, "unit": "mmol/L"}),
        client.post("/questionnaires", headers=headers,
                    json={"instrument": "PHQ9", "answers": [3, 2, 3, 2, 3, 2, 3, 2, 1]}),
        client.post("/goals", headers=headers, json={"description": "Quokkagoal: walk to the market daily"}),
        client.get("/account/export", headers=headers),
        client.post("/auth/password-reset/request", json={"email": email}),
    ]
    assert [r.status_code for r in steps] == [200, 201, 201, 201, 201, 200, 202]
    return token


def test_the_main_flow_logs_no_health_data(email, captured):
    token = run_patient_flow(email)
    logged = captured()

    assert "Password reset email queued" in logged  # proves the log was really captured
    for marker in [email, PASSWORD, "Wrong-Marker-Guess", token, "Quokkafield", "Wombatson", "Numbatley",
                   "1984-02-29", "93.7", "7.83", "Quokkagoal"]:
        assert marker not in logged, f"{marker!r} was written to the log"


def test_reset_links_are_printed_only_when_switched_on(monkeypatch, capfd):
    monkeypatch.setattr(settings, "print_reset_links", False)
    mailer.send_password_reset("someone@test.local", "token-marker-123")
    assert "token-marker-123" not in capfd.readouterr().out

    monkeypatch.setattr(settings, "print_reset_links", True)  # development only
    mailer.send_password_reset("someone@test.local", "token-marker-123")
    assert "token-marker-123" in capfd.readouterr().out


def test_database_errors_hide_the_query_values(db):
    """hide_parameters: SQLAlchemy error messages and log lines do not show the bound values."""
    with pytest.raises(DBAPIError) as error:
        db.execute(text("SELECT 1 / :divisor"), {"divisor": 0})
    assert "parameters hidden" in str(error.value)


# ---------------------------------------------------------------------------
# Unhandled errors (SafeErrorMiddleware)
# ---------------------------------------------------------------------------
def broken_app() -> FastAPI:
    """A tiny app whose route fails the way a database error does: quoting the row."""
    tiny = FastAPI()

    @tiny.get("/boom/{record_id}")
    def boom(record_id: int):
        raise ValueError("Failing row contains (Quokkafield, quokka@test.local, 173, 7.83)")

    tiny.add_middleware(SafeErrorMiddleware)
    return tiny


def test_an_unhandled_error_logs_only_its_type_and_route(monkeypatch, caplog):
    monkeypatch.setattr(settings, "log_error_details", False)
    caplog.set_level(logging.INFO)
    response = TestClient(broken_app()).get("/boom/42?note=Quokkanote")

    assert response.status_code == 500
    assert response.json() == {"detail": "Something went wrong. Please try again."}
    # The test client (the browser side, logger "httpx" or "httpx2" depending on the version) is not
    # the server, so its lines are left out
    server_log = "\n".join(r.getMessage() for r in caplog.records if not r.name.startswith("httpx"))
    assert "Unhandled ValueError on GET /boom/42" in server_log
    for marker in ["Quokkafield", "quokka@test.local", "7.83", "Quokkanote"]:
        assert marker not in server_log and marker not in response.text


def test_full_errors_can_be_switched_on_for_debugging(monkeypatch):
    monkeypatch.setattr(settings, "log_error_details", True)
    with pytest.raises(ValueError):
        TestClient(broken_app()).get("/boom/42")


def test_the_app_uses_the_safe_error_middleware():
    assert any(m.cls is SafeErrorMiddleware for m in app.user_middleware)
