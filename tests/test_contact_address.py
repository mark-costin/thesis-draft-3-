"""Contact number and home address on the profile (22_contact_and_google.sql)."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, text
from sqlalchemy.exc import IntegrityError

from backend import audit
from backend.config import settings
from backend.database import SessionLocal, set_request_identity
from backend.main import app
from backend.models import User

client = TestClient(app)
PASSWORD = "correct horse battery"
ADDRESS = {"province": "Bulacan", "city_municipality": "Marilao", "barangay": "Abangan Norte"}


@pytest.fixture
def emails():
    made = []

    def new():
        made.append(f"addr-{uuid.uuid4().hex[:8]}@test.local")
        return made[-1]

    yield new
    with SessionLocal() as session:
        session.execute(delete(User).where(func.lower(User.email).in_(made)))
        session.commit()


def body(email, **changes):
    return {"email": email, "password": PASSWORD, "first_name": "Andres", "last_name": "Bonifacio",
            "date_of_birth": "1980-11-30", "biological_sex": "Male",
            "privacy_consent": True, "consent_version": settings.privacy_notice_version} | changes


def register_and_login(emails, **changes):
    email = emails()
    assert client.post("/auth/register", json=body(email, **changes)).status_code == 201
    token = client.post("/auth/login", json={"email": email, "password": PASSWORD}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_register_saves_the_contact_number_and_address(emails):
    headers = register_and_login(emails, contact_number="0917 123 4567", street="  12 Rizal St. ", **ADDRESS)
    profile = client.get("/profile", headers=headers).json()
    assert profile["contact_number"] == "09171234567"  # spaces removed
    assert {k: profile[k] for k in ADDRESS} == ADDRESS
    assert profile["street"] == "12 Rizal St."
    assert profile["sign_in"] == "email"


def test_contact_and_address_are_optional(emails):
    profile = client.get("/profile", headers=register_and_login(emails)).json()
    assert profile["contact_number"] is None and profile["province"] is None and profile["street"] is None


@pytest.mark.parametrize("changes", [
    {"contact_number": "12345"},
    {"contact_number": "0817 123 4567"},
    {"contact_number": "+63917123456"},
    {"contact_number": "0917123456a"},
    {"province": "Bulacan"},
    {"province": "Bulacan", "city_municipality": "Marilao"},
    {"province": "Bulacan", "city_municipality": "Marilao", "barangay": ""},
    {"province": "x" * 101, "city_municipality": "Marilao", "barangay": "Abangan Norte"},
    {"street": "x" * 151},
], ids=["too-short", "not-09", "plus63-too-short", "letter", "province-only", "no-barangay",
        "empty-barangay", "province-too-long", "street-too-long"])
def test_bad_contact_or_address_is_refused(emails, changes):
    assert client.post("/auth/register", json=body(emails(), **changes)).status_code == 422


def test_plus63_numbers_are_accepted(emails):
    headers = register_and_login(emails, contact_number="+639171234567")
    assert client.get("/profile", headers=headers).json()["contact_number"] == "+639171234567"


def test_html_in_the_address_is_removed(emails):
    headers = register_and_login(emails, province="<b>Bulacan</b>", city_municipality="Marilao",
                                 barangay="Abangan Norte<script>alert(1)</script>")
    profile = client.get("/profile", headers=headers).json()
    assert profile["province"] == "Bulacan" and profile["barangay"] == "Abangan Norte"


def test_profile_edits_the_contact_and_address(emails):
    headers = register_and_login(emails, contact_number="09171234567", street="12 Rizal St.", **ADDRESS)
    moved = {"province": "Metro Manila", "city_municipality": "City of Manila", "barangay": "Barangay 1"}
    response = client.patch("/profile", headers=headers, json=moved | {"street": None, "contact_number": None})
    assert response.status_code == 200
    data = response.json()
    assert {k: data[k] for k in moved} == moved
    assert data["street"] is None and data["contact_number"] is None  # both may be cleared


@pytest.mark.parametrize("edit", [
    {"barangay": "Lambakin"},                                   # alone: city and province must come too
    {"province": None, "city_municipality": None, "barangay": None},
    {"contact_number": "1234"},
], ids=["barangay-alone", "clear-address", "bad-number"])
def test_bad_profile_edits_are_refused(emails, edit):
    headers = register_and_login(emails, **ADDRESS)
    assert client.patch("/profile", headers=headers, json=edit).status_code == 422
    assert client.get("/profile", headers=headers).json()["barangay"] == "Abangan Norte"


def test_address_changes_are_audited_with_field_names_only(emails, monkeypatch):
    headers = register_and_login(emails, **ADDRESS)
    recorded = []
    real = audit.log_event

    def recording(db, event_type, **kwargs):
        recorded.append((event_type, kwargs))
        return real(db, event_type, **kwargs)

    monkeypatch.setattr(audit, "log_event", recording)
    client.patch("/profile", headers=headers, json={"contact_number": "09998887777"})
    event_type, details = recorded[-1]
    assert event_type == "DATA_UPDATED" and details["fields"] == ["contact_number"]
    assert "09998887777" not in str(details)


def test_the_export_includes_the_contact_and_address(emails):
    headers = register_and_login(emails, contact_number="09171234567", **ADDRESS)
    profile = client.get("/account/export", headers=headers).json()["profile"]
    assert profile["contact_number"] == "09171234567" and profile["barangay"] == "Abangan Norte"


def test_the_database_checks_the_number_and_the_address_order(db, two_patients):
    """Even if the API were bypassed, the database refuses a bad number or a barangay with no city."""
    user_id, patient_id = two_patients["A"]
    set_request_identity(db, user_id=user_id, patient_id=patient_id)
    for sql in ("UPDATE patients SET contact_number = '12345' WHERE patient_id = :p",
                "UPDATE patients SET barangay = 'Lambakin' WHERE patient_id = :p"):
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.execute(text(sql), {"p": patient_id})
