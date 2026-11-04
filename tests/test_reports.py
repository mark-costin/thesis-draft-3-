"""Saved reports: a frozen snapshot of one assessment that never changes, and a PDF built only
from that snapshot."""

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from backend import audit, reports
from backend.config import settings
from backend.database import SessionLocal, set_request_identity
from backend.models import Patient, User
from risk_helpers import (  # noqa: F401  (fixtures are used by name)
    MODELS, client, fill_required, make_patient, models_on, placeholder_ids,
)


def assessed(make_patient):
    email, headers = make_patient()
    fill_required(headers)
    response = client.post("/assessment/run", headers=headers)
    assert response.status_code == 201, response.text
    return email, headers, response.json()


def save(headers, assessment_id):
    return client.post("/reports", headers=headers, json={"assessment_id": assessment_id})


# ---------------------------------------------------------------------------
# Saving
# ---------------------------------------------------------------------------
def test_saving_freezes_the_results_and_care_plans(make_patient, models_on):
    _, headers, assessment = assessed(make_patient)
    response = save(headers, assessment["assessment_id"])
    assert response.status_code == 201, response.text
    assert response.headers["Cache-Control"] == "no-store"
    report = response.json()
    assert response.headers["Location"] == f"/reports/{report['report_id']}"
    snapshot = report["report_snapshot"]
    assert snapshot["results"] == assessment["results"]
    assert snapshot["patient"]["first_name"] == "Marcelo" and snapshot["patient"]["biological_sex"] == "Male"
    assert snapshot["patient"]["age"] >= 18 and snapshot["disclaimer"] == assessment["disclaimer"]
    assert set(report["templates_used"]) == set(MODELS)
    for disease, used in report["templates_used"].items():
        plan = next(r["care_plan"] for r in snapshot["results"] if r["disease"] == disease)
        assert used == {"template_id": plan["template_id"], "version": plan["version"]}


def test_a_saved_report_does_not_change_when_the_data_changes(make_patient, models_on):
    _, headers, assessment = assessed(make_patient)
    report = save(headers, assessment["assessment_id"]).json()
    # The patient quits smoking: the live view drops the smoker add-on, the saved report keeps it
    client.post("/lifestyle", headers=headers, json={"smoking_status": "former", "pack_years": 15.0})
    live = client.get(f"/assessment/{assessment['assessment_id']}", headers=headers).json()
    assert "A1" not in [a["code"] for a in live["results"][0]["care_plan"]["addons"]]
    saved = client.get(f"/reports/{report['report_id']}", headers=headers).json()
    assert saved == report
    assert "A1" in [a["code"] for a in saved["report_snapshot"]["results"][0]["care_plan"]["addons"]]


def test_the_app_cannot_edit_a_saved_report(make_patient, models_on):
    """health_reports has no UPDATE for med_app (20_health_reports.sql, 99_security.sql)."""
    email, headers, assessment = assessed(make_patient)
    report_id = save(headers, assessment["assessment_id"]).json()["report_id"]
    with SessionLocal() as session:
        user_id = session.scalar(select(User.user_id).where(User.email == email))
        set_request_identity(session, user_id=user_id)
        patient_id = session.scalar(select(Patient.patient_id))
        set_request_identity(session, user_id=user_id, patient_id=patient_id)
        with pytest.raises(DBAPIError, match="permission denied"):
            session.execute(text("UPDATE health_reports SET templates_used = '{\"x\": 1}' "
                                 "WHERE report_id = :id"), {"id": report_id})


def test_an_assessment_is_saved_only_once(make_patient, models_on):
    _, headers, assessment = assessed(make_patient)
    first = save(headers, assessment["assessment_id"]).json()
    again = save(headers, assessment["assessment_id"])
    assert again.status_code == 409 and again.json()["detail"]["report_id"] == first["report_id"]
    assert len(client.get("/reports", headers=headers).json()) == 1


def test_without_approved_care_plans_nothing_is_saved(make_patient, models_on, monkeypatch):
    _, headers, assessment = assessed(make_patient)
    monkeypatch.setattr(settings, "show_draft_care_plans", False)  # templates are still drafts
    response = save(headers, assessment["assessment_id"])
    assert response.status_code == 409 and "not approved" in response.json()["detail"]
    assert client.get("/reports", headers=headers).json() == []


def test_unknown_or_other_patients_assessment_is_not_found(make_patient, models_on):
    _, owner, assessment = assessed(make_patient)
    _, other = make_patient()
    assert save(other, assessment["assessment_id"]).status_code == 404
    assert save(owner, 999999999).status_code == 404
    assert client.post("/reports", headers=owner, json={"assessment_id": 0}).status_code == 422
    assert client.post("/reports", headers=owner,
                       json={"assessment_id": assessment["assessment_id"], "patient_id": 1}).status_code == 422


# ---------------------------------------------------------------------------
# Reading, deleting, isolation
# ---------------------------------------------------------------------------
def test_list_read_and_delete(make_patient, models_on):
    _, headers, assessment = assessed(make_patient)
    report = save(headers, assessment["assessment_id"]).json()
    listed = client.get("/reports", headers=headers).json()
    assert [(r["report_id"], r["assessment_id"]) for r in listed] == [(report["report_id"], assessment["assessment_id"])]
    assert client.delete(f"/reports/{report['report_id']}", headers=headers).status_code == 204
    assert client.get(f"/reports/{report['report_id']}", headers=headers).status_code == 404
    assert client.get("/reports", headers=headers).json() == []
    # the assessment itself stays, and can be saved again
    assert save(headers, assessment["assessment_id"]).status_code == 201


def test_another_patient_cannot_read_download_or_delete_a_report(make_patient, models_on):
    _, owner, assessment = assessed(make_patient)
    report_id = save(owner, assessment["assessment_id"]).json()["report_id"]
    _, other = make_patient()
    assert client.get(f"/reports/{report_id}", headers=other).status_code == 404
    assert client.get(f"/reports/{report_id}/pdf", headers=other).status_code == 404
    assert client.delete(f"/reports/{report_id}", headers=other).status_code == 404
    assert client.get("/reports", headers=other).json() == []
    assert client.get(f"/reports/{report_id}", headers=owner).status_code == 200


def test_reports_need_a_login():
    assert client.get("/reports").status_code == 401
    assert client.post("/reports", json={"assessment_id": 1}).status_code == 401


def test_saving_and_deleting_are_audited_without_health_data(make_patient, models_on, monkeypatch):
    recorded = []
    real = audit.log_event

    def recording(db, event_type, **kwargs):
        recorded.append((event_type, kwargs))
        return real(db, event_type, **kwargs)

    monkeypatch.setattr(audit, "log_event", recording)
    _, headers, assessment = assessed(make_patient)
    report_id = save(headers, assessment["assessment_id"]).json()["report_id"]
    client.delete(f"/reports/{report_id}", headers=headers)
    created, deleted = recorded[-2], recorded[-1]
    assert created[0] == "DATA_CREATED" and created[1]["table"] == "health_reports"
    assert created[1]["record_id"] == report_id and created[1]["assessment_id"] == assessment["assessment_id"]
    assert deleted[0] == "DATA_DELETED" and deleted[1]["record_id"] == report_id
    assert "Marcelo" not in str(recorded[-2:]) and "High" not in str(recorded[-2:])


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
def test_the_pdf_page_is_built_only_from_the_snapshot(make_patient, models_on):
    _, headers, assessment = assessed(make_patient)
    report = save(headers, assessment["assessment_id"]).json()
    snapshot = report["report_snapshot"]
    page = reports.render_html(snapshot, report["report_id"], report["created_at"])
    assert page == reports.render_html(snapshot, report["report_id"], report["created_at"])  # same every time
    assert "Marcelo del Pilar" in page
    for result in snapshot["results"]:
        for factor in result["top_factors"]:
            assert reports._e(factor["sentence"]) in page
        for item in result["care_plan"]["base"]:
            assert reports._e(item["action"]) in page
    assert "DRAFT: this care plan has not been reviewed" in page   # drafts are marked
    assert "Not yet reviewed by a physician" in page
    assert snapshot["disclaimer"] in page


def test_the_pdf_page_escapes_text():
    snapshot = {"patient": {"first_name": "<script>x</script>", "last_name": "A&B", "age": 40,
                            "biological_sex": "Male"},
                "assessment_id": 1, "assessed_at": "2026-11-03T10:00:00+08:00", "disclaimer": "d", "results": []}
    page = reports.render_html(snapshot, 1, "2026-11-03T10:00:00+08:00")
    assert "<script>" not in page and "&lt;script&gt;" in page and "A&amp;B" in page


def test_pdf_download(make_patient, models_on):
    try:
        from weasyprint import HTML  # noqa: F401  (needs the Pango/GTK libraries)
    except (ImportError, OSError):  # on Windows without GTK, the import itself fails with OSError
        pytest.skip("WeasyPrint or its Pango/GTK libraries are not installed on this computer")
    _, headers, assessment = assessed(make_patient)
    report_id = save(headers, assessment["assessment_id"]).json()["report_id"]
    response = client.get(f"/reports/{report_id}/pdf", headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
    assert response.headers["Cache-Control"] == "no-store"
    assert f'health-report-{report_id}.pdf' in response.headers["Content-Disposition"]


def test_without_the_pdf_library_the_download_says_so(make_patient, models_on, monkeypatch):
    def unavailable(report):
        raise reports.PdfUnavailable

    monkeypatch.setattr(reports, "render_pdf", unavailable)
    _, headers, assessment = assessed(make_patient)
    report_id = save(headers, assessment["assessment_id"]).json()["report_id"]
    response = client.get(f"/reports/{report_id}/pdf", headers=headers)
    assert response.status_code == 503 and "JSON" in response.json()["detail"]
