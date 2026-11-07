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


def pdf_text(data: bytes) -> str:
    """All the text in a PDF, with line breaks and repeated spaces made single spaces."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(data)
    try:
        pages = []
        for page in pdf:
            textpage = page.get_textpage()
            pages.append(textpage.get_text_bounded())
            textpage.close()
            page.close()
    finally:
        pdf.close()
    return " ".join(" ".join(pages).split())


def test_every_disease_in_the_pdf_has_its_care_plan_disclaimer(make_patient, models_on):
    """The care-plan disclaimer must be printed inside each disease's own section of the PDF, not
    only once somewhere in the file (reports.render_html adds it after each care plan)."""
    try:
        from weasyprint import HTML  # noqa: F401  (needs the Pango/GTK libraries)
    except (ImportError, OSError):
        pytest.skip("WeasyPrint or its Pango/GTK libraries are not installed on this computer")
    _, headers, assessment = assessed(make_patient)
    report = save(headers, assessment["assessment_id"]).json()
    response = client.get(f"/reports/{report['report_id']}/pdf", headers=headers)
    assert response.status_code == 200
    text = pdf_text(response.content)

    results = report["report_snapshot"]["results"]
    assert sorted(result["disease"] for result in results) == sorted(MODELS)  # all four diseases
    # Each disease's section: from its heading to the next disease's heading (or the end)
    starts, position = [], 0
    for result in results:
        position = text.index(result["disease_name"].capitalize(), position)
        starts.append(position)
    for index, result in enumerate(results):
        section = text[starts[index]:starts[index + 1] if index + 1 < len(results) else len(text)]
        disclaimer = " ".join(result["care_plan"]["disclaimer"].split())
        assert disclaimer.startswith("This care plan is for educational screening only"), result["disease"]
        assert "not a medical diagnosis" in disclaimer
        assert disclaimer in section, f"{result['disease']}: care-plan disclaimer missing from its PDF section"


def test_without_the_pdf_library_the_download_says_so(make_patient, models_on, monkeypatch):
    def unavailable(report):
        raise reports.PdfUnavailable

    monkeypatch.setattr(reports, "render_pdf", unavailable)
    _, headers, assessment = assessed(make_patient)
    report_id = save(headers, assessment["assessment_id"]).json()["report_id"]
    response = client.get(f"/reports/{report_id}/pdf", headers=headers)
    assert response.status_code == 503 and "JSON" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Review fixes: run-time templates, capped values, patient_id filters
# ---------------------------------------------------------------------------
def ids(email):
    with SessionLocal() as session:
        user_id = session.scalar(select(User.user_id).where(User.email == email))
        set_request_identity(session, user_id=user_id)
        return user_id, session.scalar(select(Patient.patient_id))


def run_snapshot(email, assessment_id):
    from backend.models import RiskAssessment
    user_id, patient_id = ids(email)
    with SessionLocal() as session:
        set_request_identity(session, user_id=user_id, patient_id=patient_id)
        return session.scalar(select(RiskAssessment.input_snapshot)
                              .where(RiskAssessment.assessment_id == assessment_id))


def test_the_report_keeps_the_templates_recorded_when_the_assessment_was_run(make_patient, models_on, monkeypatch):
    """If a different template would be picked today (e.g. a newer version was approved since the
    run), the report still uses the one recorded at run time."""
    from backend import care_plan
    from backend.models import CarePlanTemplate
    email, headers, assessment = assessed(make_patient)
    recorded = run_snapshot(email, assessment["assessment_id"])["templates_used"]
    with SessionLocal() as session:  # some other template, standing in for "today's" version
        other = session.scalars(select(CarePlanTemplate)
                                .where(CarePlanTemplate.template_id.not_in(
                                    [u["template_id"] for u in recorded.values()]))).first()
        session.expunge(other)
    monkeypatch.setattr(care_plan, "get_template", lambda *a, **k: other)
    response = save(headers, assessment["assessment_id"])
    assert response.status_code == 201, response.text
    report = client.get(f"/reports/{response.json()['report_id']}", headers=headers).json()
    assert report["templates_used"] == recorded
    for result in report["report_snapshot"]["results"]:
        assert result["care_plan"]["template_id"] == recorded[result["disease"]]["template_id"]


def test_a_draft_recorded_at_run_time_is_not_saved_once_drafts_are_off(make_patient, models_on, monkeypatch):
    _, headers, assessment = assessed(make_patient)  # run with drafts on (templates are still drafts)
    monkeypatch.setattr(settings, "show_draft_care_plans", False)
    assert save(headers, assessment["assessment_id"]).status_code == 409


def test_the_report_keeps_what_the_patient_entered_for_a_capped_value(make_patient, models_on):
    from risk_helpers import send
    email, headers = make_patient()
    fill_required(headers)
    send(headers, "/vitals", {"sbp": 240, "dbp": 90})  # model range 70-220
    assessment = client.post("/assessment/run", headers=headers).json()
    report_id = save(headers, assessment["assessment_id"]).json()["report_id"]
    snapshot = client.get(f"/reports/{report_id}", headers=headers).json()["report_snapshot"]
    assert snapshot["capped"]["CVD"]["sbp"] == {"entered": 240, "used": 220}


def test_report_queries_filter_on_patient_id_even_if_rls_shows_someone_else(make_patient, models_on):
    owner_email, owner, assessment = assessed(make_patient)
    report_id = save(owner, assessment["assessment_id"]).json()["report_id"]
    other_email, _ = make_patient()
    owner_user, owner_patient = ids(owner_email)
    _, other_patient = ids(other_email)
    with SessionLocal() as session:
        set_request_identity(session, user_id=owner_user, patient_id=owner_patient)
        assert reports.get_report(session, owner_patient, report_id).report_id == report_id
        with pytest.raises(reports.NotFound):
            reports.get_report(session, other_patient, report_id)
        assert reports.list_reports(session, other_patient) == []
        with pytest.raises(reports.NotFound):
            reports.delete_report(session, owner_user, other_patient, report_id)
    assert client.get(f"/reports/{report_id}", headers=owner).status_code == 200  # still there
