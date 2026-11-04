"""Saved health reports (Sprint 5): a frozen copy of one assessment's results, top factors and
care plans, plus the template versions used, so an old report always shows the advice the patient
actually received (Decision 4: saved reports are read-only).

The care plans come from the templates recorded when the assessment was RUN
(input_snapshot.templates_used), not today's active ones, and the values the model capped are
copied in (input_snapshot.capped). The PDF is built ONLY from the saved snapshot (never from live
data), so it always matches it. Every query filters on the patient_id from the verified JWT; RLS is
the second layer.
"""

import html
from fastapi.encoders import jsonable_encoder
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend import audit, care_plan, risk
from backend.config import settings
from backend.models import HealthReport, Patient, RiskAssessment
from backend.schemas import age_on

SNAPSHOT_VERSION = 1


class NotFound(Exception):
    pass


class AlreadySaved(Exception):
    def __init__(self, report_id: int):
        super().__init__(report_id)
        self.report_id = report_id


class CarePlansNotReady(Exception):
    pass


class PdfUnavailable(Exception):
    pass


# ---------------------------------------------------------------------------
# Save, read, delete
# ---------------------------------------------------------------------------
def save_report(db: Session, user_id: int, patient_id: int, assessment_id: int) -> HealthReport:
    existing = db.scalar(select(HealthReport.report_id).where(HealthReport.assessment_id == assessment_id,
                                                              HealthReport.patient_id == patient_id))
    if existing is not None:
        raise AlreadySaved(existing)
    try:
        view = risk.view(db, patient_id, assessment_id)  # only the caller's own assessment
    except risk.NotFound:
        raise NotFound from None
    run_snapshot = db.scalar(select(RiskAssessment.input_snapshot)
                             .where(RiskAssessment.assessment_id == assessment_id,
                                    RiskAssessment.patient_id == patient_id))
    tiers = {r["disease"]: r["risk_level"] for r in view["results"]}
    if not tiers:
        raise CarePlansNotReady
    # The templates recorded at run time, so the report keeps the advice that applied then
    try:
        plans = care_plan.build_recorded_care_plans(db, patient_id, tiers,
                                                    run_snapshot.get("templates_used") or {})
    except care_plan.TemplateMissing:
        raise CarePlansNotReady from None
    # A draft (not yet approved) template never goes into a patient's report unless drafts are on
    if not settings.show_draft_care_plans and any(plan["is_draft"] for plan in plans.values()):
        raise CarePlansNotReady
    for result in view["results"]:
        result["care_plan"] = plans[result["disease"]]

    patient = db.scalars(select(Patient).where(Patient.patient_id == patient_id)).one()
    assessed_on = view["created_at"].date()
    snapshot = jsonable_encoder({
        "snapshot_version": SNAPSHOT_VERSION,
        "patient": {"first_name": patient.first_name, "last_name": patient.last_name,
                    "age": age_on(patient.date_of_birth, assessed_on), "biological_sex": patient.biological_sex},
        "assessment_id": assessment_id,
        "assessed_at": view["created_at"],
        "disclaimer": view["disclaimer"],
        "results": view["results"],
        # what the patient entered where the model used a capped value: {disease: {feature: {entered, used}}}
        "capped": run_snapshot.get("capped", {}),
    })
    report = HealthReport(patient_id=patient_id, assessment_id=assessment_id, report_snapshot=snapshot,
                          templates_used=care_plan.templates_used(plans))
    db.add(report)
    try:
        db.flush()
    except IntegrityError as exc:  # two clicks at once: reports_assessment_key
        db.rollback()
        if getattr(exc.orig, "sqlstate", None) == "23505":
            raise AlreadySaved(db.scalar(select(HealthReport.report_id)
                                         .where(HealthReport.assessment_id == assessment_id))) from exc
        raise
    audit.log_event(db, "DATA_CREATED", user_id=user_id, patient_id=patient_id, table="health_reports",
                    record_id=report.report_id, assessment_id=assessment_id)
    db.commit()
    db.refresh(report)
    return report


def list_reports(db: Session, patient_id: int) -> list[HealthReport]:
    return list(db.scalars(select(HealthReport).where(HealthReport.patient_id == patient_id)
                           .order_by(HealthReport.created_at.desc(), HealthReport.report_id.desc())))


def get_report(db: Session, patient_id: int, report_id: int) -> HealthReport:
    report = db.scalars(select(HealthReport).where(HealthReport.report_id == report_id,
                                                   HealthReport.patient_id == patient_id)).one_or_none()
    if report is None:
        raise NotFound
    return report


def delete_report(db: Session, user_id: int, patient_id: int, report_id: int) -> None:
    deleted = db.scalar(delete(HealthReport).where(HealthReport.report_id == report_id,
                                                   HealthReport.patient_id == patient_id)
                        .returning(HealthReport.report_id))
    if deleted is None:
        db.rollback()
        raise NotFound
    audit.log_event(db, "DATA_DELETED", user_id=user_id, patient_id=patient_id,
                    table="health_reports", record_id=report_id)
    db.commit()


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
_CSS = """
@page { size: A4; margin: 18mm 16mm; @bottom-center { content: "Page " counter(page) " of " counter(pages);
        font-size: 8pt; color: #666; } }
body { font-family: "DejaVu Sans", Arial, sans-serif; font-size: 10pt; color: #1a1a1a; line-height: 1.4; }
h1 { font-size: 17pt; margin: 0 0 4pt; } h2 { font-size: 13pt; margin: 16pt 0 4pt; border-bottom: 1px solid #ccc; }
h3 { font-size: 10.5pt; margin: 10pt 0 3pt; }
.meta, .small { color: #555; font-size: 8.5pt; }
.box { border: 1px solid #ccc; padding: 6pt 8pt; margin: 6pt 0; }
.consult { border-color: #b00020; color: #b00020; font-weight: bold; }
.draft { border-color: #b26a00; color: #b26a00; font-weight: bold; }
table { width: 100%; border-collapse: collapse; margin: 4pt 0; }
td, th { border: 1px solid #ddd; padding: 3pt 5pt; vertical-align: top; text-align: left; }
th { background: #f2f2f2; } .level { font-weight: bold; }
"""


def _e(value) -> str:
    return html.escape("" if value is None else str(value))


def _items_table(items: list[dict]) -> str:
    rows = "".join(
        f"<tr><td>{_e(item.get('category') or item.get('trigger'))}</td><td>{_e(item.get('action'))}"
        f"<div class='small'>{_e(item.get('why'))}</div></td><td class='small'>{_e(item.get('source'))}</td></tr>"
        for item in items)
    return f"<table><tr><th>Area</th><th>What to do</th><th>Source</th></tr>{rows}</table>"


def render_html(snapshot: dict, report_id: int, saved_at: str) -> str:
    """The report as HTML, built only from the saved snapshot."""
    patient = snapshot["patient"]
    parts = [
        "<h1>Chronic Disease Risk Screening Report</h1>",
        f"<div class='meta'>Report #{report_id} &middot; Assessment #{_e(snapshot['assessment_id'])} &middot; "
        f"Assessed {_e(str(snapshot['assessed_at'])[:10])} &middot; Saved {_e(saved_at[:10])}</div>",
        f"<p><b>{_e(patient['first_name'])} {_e(patient['last_name'])}</b>, {_e(patient['age'])} years, "
        f"{_e(patient['biological_sex'])}</p>",
        f"<div class='box small'>{_e(snapshot['disclaimer'])}</div>",
    ]
    for result in snapshot["results"]:
        plan = result.get("care_plan") or {}
        parts.append(f"<h2>{_e(result['disease_name'].capitalize())}</h2>")
        parts.append(f"<p>Risk level: <span class='level'>{_e(result['risk_level'])}</span> "
                     f"(estimated {round(float(result['probability']) * 100, 1)}%)</p>")
        if result.get("consult_message"):
            parts.append(f"<div class='box consult'>{_e(result['consult_message'])}</div>")
        if result.get("top_factors"):
            parts.append("<h3>What affected this result the most</h3><ol>"
                         + "".join(f"<li>{_e(f['sentence'])}</li>" for f in result["top_factors"]) + "</ol>")
        if plan:
            parts.append(f"<h3>Your care plan ({_e(result['risk_level'])} risk)</h3>")
            if plan.get("is_draft"):
                parts.append("<div class='box draft'>DRAFT: this care plan has not been reviewed by a "
                             "physician yet.</div>")
            parts.append(_items_table(plan.get("base", [])))
            if plan.get("addons"):
                parts.append("<h3>Added for you, based on your answers</h3>" + _items_table(plan["addons"]))
            reviewer = (f"Reviewed by {_e(plan.get('reviewed_by'))} on {_e(plan.get('reviewed_on'))}"
                        if plan.get("reviewed_by") else "Not yet reviewed by a physician")
            parts.append(f"<p class='small'>Care plan template v{_e(plan.get('version'))}. {reviewer}. "
                         f"Guidelines: {_e(plan.get('guideline_source'))}</p>")
            parts.append(f"<p class='small'>{_e(plan.get('disclaimer'))}</p>")
    body = "\n".join(parts)
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>Health report {report_id}</title>"
            f"<style>{_CSS}</style></head><body>{body}</body></html>")


def render_pdf(report: HealthReport) -> bytes:
    """PDF bytes. WeasyPrint needs the Pango libraries (built into the Docker image; on Windows
    they come with the GTK runtime), so a missing library gives PdfUnavailable, not a crash."""
    try:
        from weasyprint import HTML
    except (ImportError, OSError) as exc:
        raise PdfUnavailable from exc
    html_text = render_html(report.report_snapshot, report.report_id, report.created_at.isoformat())
    return HTML(string=html_text).write_pdf()


def export_pdf(db: Session, user_id: int, patient_id: int, report_id: int) -> tuple[HealthReport, bytes]:
    report = get_report(db, patient_id, report_id)
    data = render_pdf(report)
    audit.log_event(db, "REPORT_EXPORTED", user_id=user_id, patient_id=patient_id,
                    record_id=report_id, format="pdf")
    db.commit()
    return report, data
