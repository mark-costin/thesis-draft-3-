"""Zero Trust checks: one patient can never see or change another patient's data."""

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import ProgrammingError

from backend.database import set_request_identity
from backend.models import CarePlanTemplate, HealthReport, Patient, RiskAssessment


def as_patient(db, ids):
    user_id, patient_id = ids
    set_request_identity(db, user_id=user_id, patient_id=patient_id)


def add_assessment(db, patient_id):
    row = RiskAssessment(patient_id=patient_id, input_snapshot={})
    db.add(row)
    db.flush()
    return row.assessment_id


def test_connected_as_med_app(db):
    assert db.execute(text("SELECT current_user")).scalar() == "med_app"


def test_no_identity_sees_no_patients(db, two_patients):
    assert db.scalar(select(func.count()).select_from(Patient)) == 0


def test_patient_sees_only_own_patient_row(db, two_patients):
    as_patient(db, two_patients["A"])
    rows = db.scalars(select(Patient.patient_id)).all()
    assert rows == [two_patients["A"][1]]


def test_patient_cannot_read_other_patients_rows(db, two_patients):
    as_patient(db, two_patients["B"])
    add_assessment(db, two_patients["B"][1])
    db.commit()

    as_patient(db, two_patients["A"])
    count = db.scalar(select(func.count()).select_from(RiskAssessment)
                      .where(RiskAssessment.patient_id == two_patients["B"][1]))
    assert count == 0


def test_patient_cannot_write_rows_for_another_patient(db, two_patients):
    as_patient(db, two_patients["A"])
    with pytest.raises(ProgrammingError, match="row-level security"):
        add_assessment(db, two_patients["B"][1])


def test_identity_survives_commit(db, two_patients):
    # set_config(..., true) ends with the transaction; database.py must reapply it
    as_patient(db, two_patients["A"])
    add_assessment(db, two_patients["A"][1])
    db.commit()
    assert db.scalar(select(func.count()).select_from(RiskAssessment)) == 1


def test_health_reports_isolated(db, two_patients):
    as_patient(db, two_patients["A"])
    assessment_id = add_assessment(db, two_patients["A"][1])
    db.add(HealthReport(patient_id=two_patients["A"][1], assessment_id=assessment_id,
                        report_snapshot={"CVD": {"risk_level": "Low"}},
                        templates_used={"CVD": {"template_id": 1, "version": "1.0"}}))
    db.commit()
    assert db.scalar(select(func.count()).select_from(HealthReport)) == 1

    as_patient(db, two_patients["B"])
    assert db.scalar(select(func.count()).select_from(HealthReport)) == 0


def test_saved_report_cannot_be_edited(db, two_patients):
    as_patient(db, two_patients["A"])
    with pytest.raises(ProgrammingError, match="permission denied"):
        db.execute(update(HealthReport).values(templates_used={"edited": True}))


def test_app_can_read_but_not_write_care_plan_templates(db):
    db.scalar(select(func.count()).select_from(CarePlanTemplate))  # reading works
    with pytest.raises(ProgrammingError, match="permission denied"):
        db.add(CarePlanTemplate(disease_domain="CVD", risk_level="Low", version="9.9",
                                plan_items={"base": [], "addons": []}, guideline_source="test"))
        db.flush()


def test_app_cannot_read_audit_ledger(db):
    with pytest.raises(ProgrammingError, match="permission denied"):
        db.execute(text("SELECT count(*) FROM audit_ledger"))
