"""Saved health reports: save one assessment as a frozen report, list, read, PDF export, delete.

A saved report never changes (health_reports has no UPDATE for med_app): it keeps the results and
the care plan text and template versions of the day it was saved. Everything here is health
data, so responses are Cache-Control: no-store.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from backend import reports
from backend.database import get_db
from backend.dependencies import CurrentUser, get_current_user
from backend.middleware import limiter
from backend.schemas import ReportCreate, ReportResponse, ReportSummary

router = APIRouter(prefix="/reports", tags=["reports"])

NO_STORE = {"Cache-Control": "no-store"}
_NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ReportResponse)
def save(body: ReportCreate, response: Response, current: CurrentUser = Depends(get_current_user),
         db: Session = Depends(get_db)) -> ReportResponse:
    try:
        report = reports.save_report(db, current.user_id, current.patient_id, body.assessment_id)
    except reports.NotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Assessment not found")
    except reports.AlreadySaved as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, {"message": "This assessment is already saved as a report.",
                                                       "report_id": exc.report_id})
    except reports.CarePlansNotReady:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "The care plans for this result are not approved yet, so the report cannot be saved.")
    response.headers.update(NO_STORE)
    response.headers["Location"] = f"/reports/{report.report_id}"
    return ReportResponse.model_validate(report)


@router.get("", response_model=list[ReportSummary])
def list_reports(response: Response, current: CurrentUser = Depends(get_current_user),
                 db: Session = Depends(get_db)) -> list[ReportSummary]:
    response.headers.update(NO_STORE)
    return [ReportSummary.model_validate(row) for row in reports.list_reports(db)]


@router.get("/{report_id}", response_model=ReportResponse)
def read_report(report_id: int, response: Response, current: CurrentUser = Depends(get_current_user),
                db: Session = Depends(get_db)) -> ReportResponse:
    try:
        report = reports.get_report(db, report_id)
    except reports.NotFound:
        raise _NOT_FOUND
    response.headers.update(NO_STORE)
    return ReportResponse.model_validate(report)


@router.get("/{report_id}/pdf")
@limiter.limit("10/minute")  # building a PDF takes work
def download_pdf(request: Request, report_id: int, current: CurrentUser = Depends(get_current_user),
                 db: Session = Depends(get_db)) -> Response:
    try:
        report, data = reports.export_pdf(db, current.user_id, current.patient_id, report_id)
    except reports.NotFound:
        raise _NOT_FOUND
    except reports.PdfUnavailable:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "PDF export is not available on this server yet. Please download the JSON report.")
    return Response(content=data, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="health-report-{report.report_id}.pdf"',
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    })


@router.delete("/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_report(report_id: int, current: CurrentUser = Depends(get_current_user),
                  db: Session = Depends(get_db)) -> Response:
    try:
        reports.delete_report(db, current.user_id, current.patient_id, report_id)
    except reports.NotFound:
        raise _NOT_FOUND
    return Response(status_code=status.HTTP_204_NO_CONTENT)
