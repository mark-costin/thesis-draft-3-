"""Sprint 6 integration: the whole patient flow through the API, the final row counts,
the audit events it writes, and two patients side by side.

    log in -> enter data -> upload a lab report -> OCR -> confirm -> assess -> results
           -> care plan goal -> save report -> PDF -> export -> log out

OCR uses a fake engine and the models are throw-away ones (tests/risk_helpers.py), so this runs
on any laptop. The by-hand version (two browsers, DBeaver) is in docs/integration_checks.md.
"""

import uuid

import pytest
from sqlalchemy import func, select

from backend import audit
from backend.config import settings
from backend.database import SessionLocal, set_request_identity
from backend.models import (
    Document, HealthReport, LabResult, LifestyleRecord, Patient, PatientFamilyHistory, PatientGoal,
    PatientFhrsStratification, PatientHealthHistory, QuestionnaireResponse, RiskAssessment, RiskResult, ShapExplanation, User,
    VitalsRecord,
)
from backend.ocr import engines
from risk_helpers import (  # noqa: F401  (fixtures are used by name)
    MODELS, client, fill_required, make_patient, models_on, placeholder_ids, send,
)
from sample_files import png

LAB_REPORT = "LIPID PROFILE\nLDL Cholesterol 160 mg/dL\n"


class FakeEngine:
    name = "Fake OCR"
    text = LAB_REPORT
    confidence = 90.0

    def read(self, data, mime_type):
        return engines.OcrResult(self.text, self.confidence)


@pytest.fixture(autouse=True)
def upload_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    (tmp_path / "uploads").mkdir()


@pytest.fixture
def ocr_on(monkeypatch):
    fake = type("ConfiguredFake", (FakeEngine,), {})
    monkeypatch.setattr(settings, "ocr_engine", "fake")
    monkeypatch.setitem(engines.ENGINES, "fake", fake)
    return fake


@pytest.fixture
def events(monkeypatch):
    """Every audit entry written during the test (med_app cannot read audit_ledger itself)."""
    recorded = []
    real_log_event = audit.log_event

    def recording(db, event_type, *, user_id=None, patient_id=None, **details):
        audit_id = real_log_event(db, event_type, user_id=user_id, patient_id=patient_id, **details)
        recorded.append((event_type, user_id, patient_id, details))
        return audit_id

    monkeypatch.setattr(audit, "log_event", recording)
    return recorded


def ids_of(email):
    with SessionLocal() as session:
        user_id = session.scalar(select(User.user_id).where(User.email == email))
        set_request_identity(session, user_id=user_id)
        return user_id, session.scalar(select(Patient.patient_id))


def row_counts(email):
    """Rows the patient has in each table, read as med_app with the patient's own identity."""
    user_id, patient_id = ids_of(email)
    tables = (VitalsRecord, LifestyleRecord, PatientHealthHistory, QuestionnaireResponse, PatientFamilyHistory,
              PatientFhrsStratification, Document, LabResult, RiskAssessment, RiskResult, ShapExplanation, PatientGoal, HealthReport)
    with SessionLocal() as session:
        set_request_identity(session, user_id=user_id, patient_id=patient_id)
        return {model.__tablename__: session.scalar(select(func.count()).select_from(model)) for model in tables}


def upload_lab(headers):
    response = client.post("/documents", headers=headers,
                           files={"file": ("lab.png", png(uuid.uuid4().bytes), "image/png")})
    assert response.status_code == 202, response.text
    return response.json()["document_id"]


def missing_ldl(headers):
    cvd = client.get("/assessment/readiness", headers=headers).json()["models"]["CVD"]
    return "ldl" in [item["name"] for item in cvd["missing_required"] + cvd["missing_optional"]]


def full_flow(headers):
    """Every screen in order. Returns the IDs the flow made."""
    fill_required(headers)
    ready = client.get("/assessment/readiness", headers=headers).json()
    assert ready["ready"] and ready["missing_required"] == []

    # Scan & assess: OCR reads LDL; it counts only after the patient confirms it
    typed_ldl = client.get("/labs", headers=headers).json()[0]["lab_result_id"]
    assert client.delete(f"/labs/{typed_ldl}", headers=headers).status_code == 204
    assert missing_ldl(headers)
    document_id = upload_lab(headers)
    assert client.get(f"/documents/{document_id}", headers=headers).json()["status"] == "ready_for_review"
    review = client.get(f"/documents/{document_id}/review", headers=headers).json()
    assert [row["test_code"] for row in review["lab_results"]] == ["LDL"]
    assert missing_ldl(headers)  # read by OCR, not confirmed yet
    assert client.post(f"/documents/{document_id}/confirm", headers=headers).status_code == 200
    assert not missing_ldl(headers)

    # Results with care plans
    run = client.post("/assessment/run", headers=headers)
    assert run.status_code == 201, run.text
    assessment = run.json()
    assert {r["disease"] for r in assessment["results"]} == set(MODELS)
    assert all(r["care_plan"] for r in assessment["results"])
    saved = client.get(f"/assessment/{assessment['assessment_id']}", headers=headers).json()
    assert [r["risk_level"] for r in saved["results"]] == [r["risk_level"] for r in assessment["results"]]

    # A care plan item becomes a goal; then the report is saved, downloaded and exported
    result = assessment["results"][0]
    goal = client.post("/goals", headers=headers, json={"description": "Walk 30 minutes a day",
                                                        "result_id": result["result_id"]})
    assert goal.status_code == 201, goal.text
    assert client.patch(f"/goals/{goal.json()['goal_id']}", headers=headers,
                        json={"is_completed": True}).json()["is_completed"] is True
    report = client.post("/reports", headers=headers, json={"assessment_id": assessment["assessment_id"]})
    assert report.status_code == 201, report.text
    pdf = client.get(f"/reports/{report.json()['report_id']}/pdf", headers=headers)
    assert pdf.status_code in (200, 503)  # 503 = no GTK/Pango on this PC (Windows); Docker has it
    export = client.get("/account/export", headers=headers)
    assert export.status_code == 200 and export.json()["health_reports"][0]["report_id"] == report.json()["report_id"]
    return {"document_id": document_id, "assessment_id": assessment["assessment_id"],
            "result_id": result["result_id"], "goal_id": goal.json()["goal_id"],
            "report_id": report.json()["report_id"], "pdf_ok": pdf.status_code == 200}


# ---------------------------------------------------------------------------
# The whole flow, the rows and the audit log
# ---------------------------------------------------------------------------
def test_the_whole_flow_saves_every_screen_to_its_table(make_patient, models_on, ocr_on):
    email, headers = make_patient()
    full_flow(headers)
    counts = row_counts(email)
    assert counts == {
        "vitals_records": 1, "lifestyle_records": 1, "patient_health_history": 1,
        "questionnaire_responses": 3, "patient_family_history": 1,
        "patient_fhrs_stratification": len(MODELS),  # one per disease for the one family history save
        "documents": 1,
        "lab_results": 1,  # the typed LDL was deleted; the confirmed OCR value is the one left
        "risk_assessments": 1, "risk_results": len(MODELS),
        "shap_explanations": sum(len(m.features) for m in models_on.values()),
        "patient_goals": 1, "health_reports": 1,
    }
    with SessionLocal() as session:
        user_id, patient_id = ids_of(email)
        set_request_identity(session, user_id=user_id, patient_id=patient_id)
        lab = session.scalars(select(LabResult)).one()
        assert (lab.source, lab.is_confirmed, lab.document_id) == ("ocr", True, session.scalar(select(Document.document_id)))


def test_every_step_is_audited_for_the_right_patient(make_patient, models_on, ocr_on, events):
    email, headers = make_patient()
    ids = full_flow(headers)
    assert client.post("/auth/logout", headers=headers).status_code == 204
    user_id, patient_id = ids_of(email)

    kinds = {event for event, *_ in events}
    expected = {"REGISTER", "LOGIN_SUCCESS", "DATA_CREATED", "DATA_UPDATED", "DATA_DELETED", "DOCUMENT_UPLOADED",
                "DOCUMENT_VERIFIED", "RISK_ASSESSMENT_RUN", "DATA_EXPORTED", "LOGOUT"}
    if ids["pdf_ok"]:
        expected.add("REPORT_EXPORTED")
    assert expected <= kinds, expected - kinds
    created = {details.get("table") for event, _, _, details in events if event == "DATA_CREATED"}
    assert {"vitals_records", "lifestyle_records", "questionnaire_responses", "patient_family_history",
            "lab_results", "patient_goals", "health_reports"} <= created, created
    # Every entry belongs to this patient (the OCR job has no user: it runs in the background)
    assert {u for _, u, _, _ in events} <= {user_id, None}
    assert {p for _, _, p, _ in events} == {patient_id}
    # IDs and event names only: no names, emails or diagnoses in the details (numbers are not
    # checked here: an ID can contain any digits; the log tests in PR #38 cover values)
    text = repr([details for *_, details in events])
    for secret in (email, "Marcelo", "del Pilar", "Heart attack", "Walk 30 minutes"):
        assert secret not in text, secret


# ---------------------------------------------------------------------------
# Two patients
# ---------------------------------------------------------------------------
def test_two_patients_never_see_each_other(make_patient, models_on, ocr_on):
    email_a, a = make_patient()
    ids = full_flow(a)
    before = row_counts(email_a)
    _, b = make_patient()

    def first_id(path, key):
        return client.get(path, headers=a).json()[0][key]

    others = {
        "vitals": first_id("/vitals", "vitals_id"), "lifestyle": first_id("/lifestyle", "lifestyle_id"),
        "questionnaire": first_id("/questionnaires", "response_id"),
        "family": first_id("/family-history", "record_id"), "lab": first_id("/labs", "lab_result_id"),
    }
    # B tries every one of A's records by ID: always 404, as if it did not exist
    attempts = [
        ("GET", f"/vitals/{others['vitals']}", None), ("DELETE", f"/vitals/{others['vitals']}", None),
        ("GET", f"/lifestyle/{others['lifestyle']}", None), ("DELETE", f"/lifestyle/{others['lifestyle']}", None),
        ("GET", f"/questionnaires/{others['questionnaire']}", None),
        ("DELETE", f"/questionnaires/{others['questionnaire']}", None),
        ("GET", f"/family-history/{others['family']}", None), ("DELETE", f"/family-history/{others['family']}", None),
        ("PATCH", f"/family-history/{others['family']}", {"age_at_diagnosis": 50}),
        ("GET", f"/labs/{others['lab']}", None),
        ("PATCH", f"/labs/{others['lab']}", {"test_code": "LDL", "value": 90, "unit": "mg/dL"}),
        ("DELETE", f"/labs/{others['lab']}", None),
        ("GET", f"/documents/{ids['document_id']}", None), ("GET", f"/documents/{ids['document_id']}/file", None),
        ("GET", f"/documents/{ids['document_id']}/review", None),
        ("POST", f"/documents/{ids['document_id']}/confirm", None),
        ("POST", f"/documents/{ids['document_id']}/reject", None),
        ("DELETE", f"/documents/{ids['document_id']}", None),
        ("GET", f"/assessment/{ids['assessment_id']}", None),
        ("POST", "/reports", {"assessment_id": ids["assessment_id"]}),
        ("GET", f"/reports/{ids['report_id']}", None), ("GET", f"/reports/{ids['report_id']}/pdf", None),
        ("DELETE", f"/reports/{ids['report_id']}", None),
        ("GET", f"/goals/{ids['goal_id']}", None), ("PATCH", f"/goals/{ids['goal_id']}", {"is_completed": False}),
        ("DELETE", f"/goals/{ids['goal_id']}", None),
    ]
    for method, path, body in attempts:
        response = client.request(method, path, headers=b, json=body)
        assert response.status_code == 404, (method, path, response.status_code)
    # Linking a goal to A's risk result is refused too
    assert client.post("/goals", headers=b, json={"description": "x", "result_id": ids["result_id"]}).status_code == 422

    # B's own lists are empty, and A still has everything
    for path in ("/vitals", "/lifestyle", "/questionnaires", "/family-history", "/labs", "/documents",
                 "/assessment", "/reports", "/goals"):
        assert client.get(path, headers=b).json() == [], path
    export = client.get("/account/export", headers=b).json()
    assert all(export[name] == [] for name in ("vitals", "lab_results", "documents", "risk_results", "health_reports"))
    assert row_counts(email_a) == before
