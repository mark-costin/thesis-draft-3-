"""Sprint 7 security tests: the app is attacked on purpose and must refuse every time.

Each section is one kind of attack. Many attacks are already tested in the feature test files
(see docs/security_tests.md for the full list); this file covers the ones that were missing.

  A. Forged or changed login tokens
  B. Identity smuggled in through the URL or headers
  C. SQL injection
  D. Routes that forget to ask for a login
  E. What the med_app database role can do if an attacker gets its connection
  F. Response headers (caching, content sniffing, framing)
"""

import base64
import json
import re
import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import DBAPIError

from backend.config import settings
from backend.database import SessionLocal
from backend.main import app
from backend.middleware import REPLAY_HEADER, SECURITY_HEADERS
from backend.models import Patient, User
from backend.security import ALGORITHM

client = TestClient(app)
PASSWORD = "correct horse battery"


@pytest.fixture
def make_patient():
    """Registers and logs in patients; returns (email, headers). Deleted after the test."""
    made = []

    def make(first_name="Andres", last_name="Bonifacio"):
        email = f"sec-{uuid.uuid4().hex[:8]}@test.local"
        made.append(email)
        assert client.post("/auth/register", json={
            "email": email, "password": PASSWORD, "first_name": first_name, "last_name": last_name,
            "date_of_birth": "1980-11-30", "biological_sex": "Male",
            "privacy_consent": True, "consent_version": settings.privacy_notice_version,
        }).status_code == 201
        token = client.post("/auth/login", json={"email": email, "password": PASSWORD}).json()["access_token"]
        return email, {"Authorization": f"Bearer {token}"}

    yield make
    with SessionLocal() as session:
        session.execute(delete(User).where(func.lower(User.email).in_(made)))
        session.commit()


def user_row(email) -> User:
    with SessionLocal() as session:
        return session.scalars(select(User).where(User.email == email)).one()


def patient_id_of(headers) -> int:
    token = headers["Authorization"].removeprefix("Bearer ")
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[ALGORITHM])["patient_id"]


def b64url(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


def b64url_decode(part: str) -> dict:
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


def valid_claims(email) -> dict:
    user = user_row(email)
    now = datetime.now(timezone.utc)
    return {"sub": str(user.user_id), "patient_id": 1, "tv": user.token_version,
            "iat": int(now.timestamp()), "exp": int((now + timedelta(minutes=5)).timestamp())}


def me(token):
    return client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})


# ---------------------------------------------------------------------------
# A. Forged or changed login tokens
# (wrong key, expired, missing/wrong token_version, deleted account: test_login.py, test_account.py)
# ---------------------------------------------------------------------------
def test_changing_the_patient_id_inside_a_real_token_breaks_it(make_patient):
    """An attacker edits the payload of their own token to point at another patient."""
    _, headers_a = make_patient()
    _, headers_b = make_patient()
    header, payload, signature = headers_a["Authorization"].removeprefix("Bearer ").split(".")
    claims = b64url_decode(payload)
    claims["patient_id"] = patient_id_of(headers_b)
    tampered = f"{header}.{b64url(claims)}.{signature}"  # old signature, new payload
    assert me(tampered).status_code == 401


def test_unsigned_token_is_refused(make_patient):
    """alg "none": a token with no signature at all."""
    email, _ = make_patient()
    unsigned = f"{b64url({'alg': 'none', 'typ': 'JWT'})}.{b64url(valid_claims(email))}."
    assert me(unsigned).status_code == 401


def test_token_signed_with_another_algorithm_is_refused(make_patient):
    """Right key, but only the one algorithm in security.ALGORITHM is accepted."""
    email, _ = make_patient()
    other = "HS512" if ALGORITHM != "HS512" else "HS384"
    token = jwt.encode(valid_claims(email), settings.jwt_secret_key, algorithm=other)
    assert me(token).status_code == 401


@pytest.mark.parametrize("change", [
    {"sub": "1 OR 1=1"},
    {"sub": None},
    {"patient_id": "1"},
    {"patient_id": None},
    {"tv": "0"},
    {"iat": None},
    {"exp": None},
], ids=["sub-not-a-number", "no-sub", "patient-id-as-text", "no-patient-id", "tv-as-text", "no-iat", "no-exp"])
def test_correctly_signed_token_with_bad_claims_is_refused(make_patient, change):
    """Even with the real key, a token must have every claim, with the right type."""
    email, _ = make_patient()
    claims = {k: v for k, v in (valid_claims(email) | change).items() if v is not None}
    token = jwt.encode(claims, settings.jwt_secret_key, algorithm=ALGORITHM)
    assert me(token).status_code == 401


# ---------------------------------------------------------------------------
# B. Identity smuggled in through the URL or headers
# ---------------------------------------------------------------------------
def test_patient_id_in_the_url_or_headers_is_ignored(make_patient):
    """Only the signed token says who the caller is. Extra IDs in the request change nothing."""
    email_a, headers_a = make_patient(first_name="Ana")
    _, headers_b = make_patient(first_name="Berto")
    b_id = patient_id_of(headers_b)
    assert client.post("/goals", headers=headers_b, json={"description": "B's private goal"}).status_code == 201

    smuggled = headers_a | {"X-Patient-Id": str(b_id), "X-User-Id": str(b_id), "X-Forwarded-User": str(b_id)}
    profile = client.get(f"/profile?patient_id={b_id}&user_id={b_id}", headers=smuggled)
    assert profile.status_code == 200 and profile.json()["email"] == email_a

    goals = client.get(f"/goals?patient_id={b_id}", headers=smuggled)
    assert goals.status_code == 200 and goals.json() == []


# ---------------------------------------------------------------------------
# C. SQL injection
# Every query uses SQLAlchemy with bound parameters, so input is data, never SQL.
# These tests prove it: the text is refused or stored exactly as typed, and the tables survive.
# ---------------------------------------------------------------------------
SQL_PAYLOADS = [
    "' OR '1'='1",
    "' OR 1=1 --",
    "x'; DROP TABLE patients; --",
    "1; UPDATE users SET failed_attempts = 99; --",
    "' UNION SELECT password_hash FROM users --",
]
SQL_IDS = ["or-true", "or-comment", "drop-table", "stacked-update", "union-select"]


def tables_still_work():
    with SessionLocal() as session:
        session.execute(text("SELECT 1 FROM users LIMIT 1"))
        assert session.scalar(text("SELECT to_regclass('public.patients')")) is not None
        assert session.scalar(text("SELECT to_regclass('public.patient_goals')")) is not None


@pytest.mark.parametrize("payload", SQL_PAYLOADS, ids=SQL_IDS)
def test_sql_in_the_login_email_does_not_log_in(make_patient, payload):
    make_patient()
    for email in (payload, payload + "@test.local"):
        response = client.post("/auth/login", json={"email": email, "password": payload})
        assert response.status_code == 401
        assert "access_token" not in response.text
    tables_still_work()


@pytest.mark.parametrize("payload", SQL_PAYLOADS, ids=SQL_IDS)
def test_sql_in_a_name_is_saved_as_plain_text(make_patient, payload):
    email, headers = make_patient()
    assert client.patch("/profile", headers=headers, json={"last_name": payload}).status_code == 200
    assert client.get("/profile", headers=headers).json()["last_name"] == payload
    tables_still_work()
    assert user_row(email).failed_attempts == 0  # the stacked UPDATE never ran


@pytest.mark.parametrize("payload", SQL_PAYLOADS, ids=SQL_IDS)
def test_sql_in_a_goal_is_saved_as_plain_text(make_patient, payload):
    _, headers = make_patient()
    created = client.post("/goals", headers=headers, json={"description": payload})
    assert created.status_code == 201 and created.json()["description"] == payload
    assert len(client.get("/goals", headers=headers).json()) == 1  # "OR 1=1" did not list more rows
    tables_still_work()


@pytest.mark.parametrize("url", [
    "/goals/1 OR 1=1",
    "/goals/1;DROP TABLE patient_goals",
    "/goals/-1 UNION SELECT 1",
    "/goals?limit=1;DROP TABLE patient_goals",
    "/goals?status=all' OR '1'='1",
], ids=["path-or", "path-stacked", "path-union", "query-limit", "query-status"])
def test_sql_in_the_url_is_refused(make_patient, url):
    """Path IDs must be whole numbers and filters must be one of the allowed words."""
    _, headers = make_patient()
    assert client.get(url, headers=headers).status_code == 422
    tables_still_work()


# ---------------------------------------------------------------------------
# D. Routes that forget to ask for a login
# A new route without get_current_user would leak data. This test finds it automatically.
# ---------------------------------------------------------------------------
PUBLIC_ROUTES = {
    ("POST", "/auth/register"),
    ("POST", "/auth/login"),
    ("POST", "/auth/password-reset/request"),
    ("POST", "/auth/password-reset/confirm"),
    ("GET", "/health"),
    ("GET", "/health/db"),
}


def api_operations():
    """Every (method, path, operation) from the OpenAPI schema. The schema is used because newer
    FastAPI versions keep included routers nested, so app.routes no longer lists every route."""
    for path, operations in app.openapi()["paths"].items():
        for method, operation in operations.items():
            yield method.upper(), path, operation


def test_the_public_list_is_up_to_date():
    """If a public route is renamed or removed, this list must change with it."""
    existing = {(method, path) for method, path, _ in api_operations()}
    assert PUBLIC_ROUTES <= existing
    assert len(existing) > 40  # the schema really lists the whole app


@pytest.mark.parametrize("method, path, operation",
                         [op for op in api_operations() if op[:2] not in PUBLIC_ROUTES],
                         ids=lambda value: value if isinstance(value, str) else "")
def test_every_other_route_needs_a_login(method, path, operation):
    # get_current_user uses HTTPBearer, so the schema marks the route as needing a token
    assert operation.get("security"), f"{method} {path} does not use get_current_user"
    url = re.sub(r"\{[^}]+\}", "1", path)
    response = client.request(method, url)
    assert response.status_code == 401, f"{method} {url} answered {response.status_code} without a login"


# ---------------------------------------------------------------------------
# E. What med_app can do if an attacker gets its connection
# (audit_ledger, consent, templates, saved reports: test_audit.py, test_isolation.py, test_profile.py)
# 42501 is PostgreSQL's "insufficient privilege" error code.
# ---------------------------------------------------------------------------
def test_med_app_is_an_ordinary_role(db):
    row = db.execute(text(
        "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb FROM pg_roles WHERE rolname = current_user"
    )).one()
    assert tuple(row) == (False, False, False, False)


def test_every_patient_table_has_row_level_security_and_is_not_owned_by_med_app(db):
    """A table med_app can read, with a patient_id column and no RLS, would show every patient's rows."""
    rows = db.execute(text("""
        SELECT c.relname, c.relrowsecurity, pg_get_userbyid(c.relowner) AS owner
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r'
          AND has_table_privilege(current_user, c.oid, 'SELECT')  -- audit_ledger: no access at all
          AND EXISTS (SELECT 1 FROM pg_attribute a
                      WHERE a.attrelid = c.oid AND a.attname = 'patient_id' AND NOT a.attisdropped)
    """)).all()
    assert len(rows) >= 10  # the query itself found the patient tables
    assert [r.relname for r in rows if not r.relrowsecurity] == []
    assert [r.relname for r in rows if r.owner == "med_app"] == []  # an owner could switch RLS off


@pytest.mark.parametrize("statement", [
    "DROP TABLE vitals_records",
    "TRUNCATE patients",
    "ALTER TABLE patients DISABLE ROW LEVEL SECURITY",
    "DROP POLICY patient_self ON patients",
    "CREATE TABLE public.attacker (x int)",
    "CREATE FUNCTION public.attacker() RETURNS int LANGUAGE sql AS 'SELECT 1'",
    "CREATE ROLE attacker",
    "SET ROLE postgres",
    "UPDATE risk_results SET risk_probability = 0",
    "UPDATE risk_assessments SET input_snapshot = '{}'",
    "UPDATE shap_explanations SET shap_value = 0",
    "UPDATE model_versions SET is_active = true",
    "INSERT INTO model_versions DEFAULT VALUES",
    "SELECT rolpassword FROM pg_authid",
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT lo_import('/etc/passwd')",
    "COPY patients TO PROGRAM 'whoami'",
], ids=["drop-table", "truncate", "disable-rls", "drop-policy", "create-table", "create-function",
        "create-role", "become-postgres", "edit-risk-result", "edit-assessment", "edit-shap",
        "activate-model", "add-model", "read-role-passwords",
        "read-server-file", "import-server-file", "run-server-program"])
def test_med_app_cannot(db, statement):
    with pytest.raises(DBAPIError) as error:
        db.execute(text(statement))
    assert error.value.orig.sqlstate == "42501", error.value.orig


# ---------------------------------------------------------------------------
# F. Response headers
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("method, url", [
    ("GET", "/health"),
    ("GET", "/profile"),          # 401
    ("GET", "/no-such-page"),     # 404
    ("OPTIONS", "/auth/login"),   # CORS preflight
], ids=["ok", "unauthorized", "not-found", "preflight"])
def test_security_headers_are_on_every_response(method, url):
    headers = {"Origin": settings.cors_origins[0], "Access-Control-Request-Method": "POST"} \
        if method == "OPTIONS" else {}
    response = client.request(method, url, headers=headers)
    for name, value in SECURITY_HEADERS.items():
        assert response.headers.get(name) == value, name


def test_health_data_responses_are_json_not_html(make_patient):
    """A name with a <script> is returned as JSON; with nosniff the browser never runs it as a page."""
    _, headers = make_patient()
    client.patch("/profile", headers=headers, json={"first_name": "<script>alert(1)</script>"})
    response = client.get("/profile", headers=headers)
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.parametrize("method, url, body", [
    ("GET", "/auth/me", None),
    ("GET", "/profile", None),
    ("PATCH", "/profile", {"last_name": "Rizal"}),
], ids=["me", "profile", "profile-edit"])
def test_personal_data_is_not_cached(make_patient, method, url, body):
    _, headers = make_patient()
    response = client.request(method, url, headers=headers, json=body)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"


def test_login_token_is_not_cached_or_kept_for_replay(make_patient):
    """The token must not stay in a cache, and the idempotency store must not keep a copy."""
    email, _ = make_patient()
    key = {"Idempotency-Key": "login-" + uuid.uuid4().hex}
    body = {"email": email, "password": PASSWORD}
    first = client.post("/auth/login", json=body, headers=key)
    assert first.status_code == 200 and first.headers["Cache-Control"] == "no-store"
    assert "access_token" in first.json()

    again = client.post("/auth/login", json=body, headers=key)
    assert again.headers.get(REPLAY_HEADER) == "true"
    assert "access_token" not in again.text  # the saved copy has no token in it


def test_security_headers_are_on_an_unhandled_error_500(monkeypatch):
    """The plain 500 from SafeErrorMiddleware (Sprint 6) must carry the security headers too, so
    SecurityHeadersMiddleware has to be the outermost middleware in install()."""
    from fastapi import FastAPI
    from backend import middleware

    monkeypatch.setattr(settings, "log_error_details", False)
    tiny = FastAPI()

    @tiny.get("/boom")
    def boom():
        raise ValueError("Failing row contains (secret)")

    middleware.install(tiny)
    response = TestClient(tiny).get("/boom")
    assert response.status_code == 500 and "secret" not in response.text
    for name, value in SECURITY_HEADERS.items():
        assert response.headers.get(name) == value, name
