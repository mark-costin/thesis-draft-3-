"""Sign in with Google (POST /auth/google), Google-only accounts, and change password.

Google is not called: _verify_with_google is replaced by a stand-in that returns the claims a
real verified ID token would carry. The checks done on those claims (issuer, verified email,
audience through the client id) are the app's own and are tested here.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from backend import google_auth
from backend.config import settings
from backend.database import SessionLocal
from backend.main import app
from backend.models import User

client = TestClient(app)
PASSWORD = "correct horse battery"


def signup_details(**changes):
    return {"first_name": "Gabriela", "last_name": "Silang", "date_of_birth": "1990-05-04",
            "biological_sex": "Female", "privacy_consent": True,
            "consent_version": settings.privacy_notice_version} | changes


@pytest.fixture
def google(monkeypatch):
    """A fake Google: credential "good-<name>..." is a verified token for <name>@gmail.test."""
    monkeypatch.setattr(settings, "google_client_id", "test-client.apps.googleusercontent.com")
    accounts = {}

    def fake_verify(credential, client_id):
        assert client_id == settings.google_client_id  # the audience is our client id
        if credential not in accounts:
            raise ValueError("Token signature or audience is wrong")
        return accounts[credential]

    monkeypatch.setattr(google_auth, "_verify_with_google", fake_verify)

    def account(**claims):
        tag = uuid.uuid4().hex[:8]
        credential = f"good-credential-{tag}-0123456789"
        accounts[credential] = {"iss": "https://accounts.google.com", "sub": f"google-{tag}",
                                "email": f"g-{tag}@gmail.test", "email_verified": True,
                                "given_name": "Gabriela", "family_name": "Silang"} | claims
        return credential, accounts[credential]

    yield account
    emails = [claims["email"].lower() for claims in accounts.values()]
    with SessionLocal() as session:
        session.execute(delete(User).where(func.lower(User.email).in_(emails)))
        session.commit()


def sign_in(credential, signup=None):
    body = {"credential": credential} | ({"signup": signup} if signup else {})
    return client.post("/auth/google", json=body)


def bearer(response):
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def user_of(email) -> User:
    with SessionLocal() as session:
        return session.scalars(select(User).where(User.email == email)).one()


# ---------------------------------------------------------------------------
# Sign up and sign in
# ---------------------------------------------------------------------------
def test_new_google_account_is_asked_for_the_missing_details(google):
    credential, claims = google()
    response = sign_in(credential)
    assert response.status_code == 404
    assert response.json()["code"] == "signup_required"
    assert (response.json()["first_name"], response.json()["last_name"]) == ("Gabriela", "Silang")
    with SessionLocal() as session:  # nothing was created yet
        assert session.scalar(select(func.count()).select_from(User).where(User.email == claims["email"])) == 0


def test_google_sign_up_creates_an_account_without_a_password(google):
    credential, claims = google()
    response = sign_in(credential, signup_details(contact_number="09171234567"))
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    profile = client.get("/profile", headers=bearer(response)).json()
    assert profile["email"] == claims["email"] and profile["sign_in"] == "google"
    assert profile["contact_number"] == "09171234567"
    user = user_of(claims["email"])
    assert user.password_hash is None and user.google_sub == claims["sub"]


def test_the_same_google_account_signs_in_again(google):
    credential, _ = google()
    first = sign_in(credential, signup_details())
    again = sign_in(credential)  # no signup details needed the second time
    assert again.status_code == 200
    assert client.get("/profile", headers=bearer(again)).json() == client.get("/profile", headers=bearer(first)).json()


@pytest.mark.parametrize("claims", [
    {"email_verified": False},
    {"iss": "https://evil.example"},
    {"email": ""},
], ids=["email-not-verified", "wrong-issuer", "no-email"])
def test_unsafe_google_tokens_are_refused(google, claims):
    credential, _ = google(**claims)
    assert sign_in(credential, signup_details()).status_code == 401


def test_a_forged_or_foreign_token_is_refused(google):
    google()
    assert sign_in("forged-credential-not-from-google-123", signup_details()).status_code == 401


def test_google_sign_in_is_off_without_a_client_id(google, monkeypatch):
    credential, _ = google()
    monkeypatch.setattr(settings, "google_client_id", "")
    assert sign_in(credential).status_code == 503


def test_a_google_email_with_a_password_account_is_not_taken_over(google):
    """Someone who controls a Google account with the same email must not get into an
    existing password account; the owner logs in with the password."""
    credential, claims = google()
    assert client.post("/auth/register", json=signup_details(email=claims["email"], password=PASSWORD)).status_code == 201
    response = sign_in(credential, signup_details())
    assert response.status_code == 409
    assert user_of(claims["email"]).google_sub is None


@pytest.mark.parametrize("changes", [
    {"privacy_consent": False},
    {"consent_version": "0.1"},
    {"date_of_birth": "2015-01-01"},
    {"biological_sex": "Other"},
    {"province": "Bulacan"},  # address must come as province + city + barangay
], ids=["no-consent", "old-notice", "under-18", "bad-sex", "address-incomplete"])
def test_google_sign_up_follows_the_same_rules_as_password_sign_up(google, changes):
    credential, claims = google()
    assert sign_in(credential, signup_details(**changes)).status_code == 422
    assert sign_in(credential).status_code == 404  # still no account


def test_a_google_only_account_cannot_log_in_with_a_password(google):
    credential, claims = google()
    sign_in(credential, signup_details())
    assert client.post("/auth/login", json={"email": claims["email"], "password": "anything at all"}).status_code == 401


def test_google_sign_up_is_audited_without_the_email(google, monkeypatch):
    from backend import audit
    recorded = []
    real = audit.log_event

    def recording(db, event_type, **kwargs):
        recorded.append((event_type, kwargs))
        return real(db, event_type, **kwargs)

    monkeypatch.setattr(audit, "log_event", recording)
    credential, claims = google()
    sign_in(credential, signup_details())
    assert [e for e, _ in recorded] == ["REGISTER", "LOGIN_SUCCESS"]
    assert recorded[0][1]["method"] == "google" and recorded[1][1]["method"] == "google"
    assert claims["email"] not in str(recorded) and claims["sub"] not in str(recorded)


# ---------------------------------------------------------------------------
# Change password
# ---------------------------------------------------------------------------
@pytest.fixture
def password_account():
    email = f"chg-{uuid.uuid4().hex[:8]}@test.local"
    assert client.post("/auth/register", json=signup_details(email=email, password=PASSWORD)).status_code == 201
    token = client.post("/auth/login", json={"email": email, "password": PASSWORD}).json()["access_token"]
    yield email, {"Authorization": f"Bearer {token}"}
    with SessionLocal() as session:
        session.execute(delete(User).where(func.lower(User.email) == email))
        session.commit()


def change(headers, current, new):
    return client.post("/auth/change-password", headers=headers,
                       json={"current_password": current, "new_password": new})


def test_change_password_logs_out_other_sessions_but_keeps_this_one(password_account):
    email, headers = password_account
    response = change(headers, PASSWORD, "a brand new passphrase")
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    assert client.get("/auth/me", headers=headers).status_code == 401           # old token revoked
    assert client.get("/auth/me", headers=bearer(response)).status_code == 200   # new token works
    assert client.post("/auth/login", json={"email": email, "password": "a brand new passphrase"}).status_code == 200
    assert client.post("/auth/login", json={"email": email, "password": PASSWORD}).status_code == 401


@pytest.mark.parametrize("current, new, expected", [
    ("wrong password", "a brand new passphrase", 403),
    (PASSWORD, "short", 422),
    (PASSWORD, PASSWORD, 422),
], ids=["wrong-current", "new-too-short", "same-as-current"])
def test_bad_password_changes_are_refused(password_account, current, new, expected):
    email, headers = password_account
    assert change(headers, current, new).status_code == expected
    assert client.post("/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200


def test_wrong_current_passwords_lock_the_account_like_login(password_account):
    from backend.services import MAX_FAILED_ATTEMPTS
    _, headers = password_account
    for _ in range(MAX_FAILED_ATTEMPTS - 1):
        assert change(headers, "wrong password", "a brand new passphrase").status_code == 403
    assert change(headers, "wrong password", "a brand new passphrase").status_code == 423
    assert change(headers, PASSWORD, "a brand new passphrase").status_code == 423  # even the right one


def test_change_password_needs_login():
    assert client.post("/auth/change-password",
                       json={"current_password": "x", "new_password": "a brand new passphrase"}).status_code == 401


def test_a_google_only_account_has_no_password_to_change(google):
    credential, _ = google()
    headers = bearer(sign_in(credential, signup_details()))
    assert change(headers, "anything", "a brand new passphrase").status_code == 400


# ---------------------------------------------------------------------------
# Deleting a Google-only account
# ---------------------------------------------------------------------------
def delete_body(**proof):
    return {"confirm": "DELETE MY ACCOUNT"} | proof


def test_a_google_only_account_is_deleted_with_a_fresh_google_sign_in(google):
    credential, claims = google()
    headers = bearer(sign_in(credential, signup_details()))
    assert client.post("/account/delete", headers=headers, json=delete_body(password="guess")).status_code == 403
    assert client.post("/account/delete", headers=headers, json=delete_body(google_credential=credential)).status_code == 204
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(User).where(User.email == claims["email"])) == 0


def test_another_google_account_cannot_delete_mine(google):
    mine, _ = google()
    theirs, _ = google()
    headers = bearer(sign_in(mine, signup_details()))
    sign_in(theirs, signup_details())
    assert client.post("/account/delete", headers=headers, json=delete_body(google_credential=theirs)).status_code == 403
    assert client.get("/profile", headers=headers).status_code == 200


@pytest.mark.parametrize("proof", [{}, {"password": PASSWORD, "google_credential": "x" * 30}],
                         ids=["no-proof", "both"])
def test_delete_needs_exactly_one_proof(password_account, proof):
    _, headers = password_account
    assert client.post("/account/delete", headers=headers, json=delete_body(**proof)).status_code == 422
