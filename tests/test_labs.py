"""Lab results: report lines map to the right test code and standard unit (backend/labs.py),
and the /labs endpoints save typed-in values for the logged-in patient only."""

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func

from backend import audit, labs
from backend.config import settings
from backend.database import SessionLocal
from backend.main import app
from backend.models import User

client = TestClient(app)
PASSWORD = "correct horse battery"


# ---------------------------------------------------------------------------
# Field mapping and unit conversion (no database)
# ---------------------------------------------------------------------------
# (label, value, unit as printed on a sample report) -> (test code, standard value, standard unit)
SAMPLE_REPORT = [
    ("FBS", "5.6", "mmol/L", "FPG", "100.890", "mg/dL"),
    ("Fasting Blood Sugar", "95", "mg/dL", "FPG", "95.000", "mg/dL"),
    ("2-Hour Glucose", "7.8", "mmol/L", "OGTT_2H", "140.525", "mg/dL"),
    ("HbA1c", "48", "mmol/mol", "HBA1C", "6.543", "%"),
    ("Hb A1c", "6.5", "%", "HBA1C", "6.500", "%"),
    ("Cholesterol", "5.2", "mmol/L", "TOTAL_CHOLESTEROL", "201.084", "mg/dL"),
    ("HDL-C", "1.3", "mmol/L", "HDL", "50.271", "mg/dL"),
    ("LDL Cholesterol", "130", "mg/dL", "LDL", "130.000", "mg/dL"),
    ("Triglycerides", "1.7", "mmol/L", "TRIGLYCERIDES", "150.569", "mg/dL"),
    ("TRIG", "150", "mg/dl", "TRIGLYCERIDES", "150.000", "mg/dL"),
    ("Crea", "88.42", "umol/L", "CREATININE", "1.000", "mg/dL"),
    ("Creatinine", "106", "µmol/L", "CREATININE", "1.199", "mg/dL"),
    ("eGFR", "90", "mL/min/1.73m2", "EGFR", "90.000", "mL/min/1.73m2"),
    ("BUA", "416", "umol/L", "URIC_ACID", "6.994", "mg/dL"),
    ("Uric Acid", "0.42", "mmol/L", "URIC_ACID", "7.060", "mg/dL"),
    ("Albumin/Creatinine Ratio", "3.4", "mg/mmol", "UACR", "30.056", "mg/g"),
    ("hs-CRP", "0.3", "mg/dL", "HS_CRP", "3.000", "mg/L"),
    ("Eosinophils Absolute", "0.4", "x10^9/L", "EOSINOPHILS", "400.000", "cells/uL"),
    ("Absolute Eosinophil Count", "350", "cells/µL", "EOSINOPHILS", "350.000", "cells/uL"),
    ("Total IgE", "150", "kU/L", "IGE", "150.000", "IU/mL"),
    ("TSH", "2.5", "uIU/mL", "TSH", "2.500", "mIU/L"),
    ("Vitamin B12", "300", "pmol/L", "VITAMIN_B12", "406.614", "pg/mL"),
    ("Folic Acid", "20", "nmol/L", "FOLATE", "8.826", "ng/mL"),
]


@pytest.mark.parametrize("label,value,unit,code,expected,standard", SAMPLE_REPORT,
                         ids=[row[0] for row in SAMPLE_REPORT])
def test_sample_report_lines_map_to_the_right_code_and_unit(label, value, unit, code, expected, standard):
    mapped = labs.map_field(label, value, unit)
    assert mapped.test_code == code
    assert mapped.unit == standard
    assert mapped.value == Decimal(expected)


def test_a_converted_value_keeps_what_the_report_said():
    mapped = labs.map_field("FBS", "5.6", "mmol/L")
    assert mapped.value_as_reported == Decimal("5.6") and mapped.unit_as_reported == "mmol/L"


def test_a_value_already_in_the_standard_unit_has_no_reported_pair():
    mapped = labs.map_field("FBS", "95", "mg/dL")
    assert mapped.value_as_reported is None and mapped.unit_as_reported is None


def test_every_test_code_has_a_standard_unit_a_range_and_labels():
    assert set(labs.STANDARD_UNIT) == set(labs.EXPECTED_RANGE) == set(labs.LABELS)


def test_a_label_is_matched_without_case_or_punctuation():
    assert labs.test_code_for("  hba1c ") == labs.test_code_for("HbA1c") == "HBA1C"
    assert labs.test_code_for("HS-CRP") == labs.test_code_for("hs crp") == "HS_CRP"


@pytest.mark.parametrize("label,value,unit", [
    ("Hemoglobin", "13.5", "g/dL"),        # not a test this app uses
    ("FBS", "5.6", "furlongs"),            # unknown unit
    ("Eosinophils", "5", "%"),             # a percent cannot become an absolute count
    ("HbA1c", "6.5", "mg/dL"),             # known unit, wrong test
    ("FBS", "abc", "mg/dL"),               # not a number
    ("FBS", "5,6", "mmol/L"),              # decimal comma: 5.6 or 56? refused
    ("FBS", "0", "mg/dL"),                 # lab_value_check: value > 0
    ("FBS", "-5", "mg/dL"),
    ("FBS", "NaN", "mg/dL"),
    ("FBS", "Infinity", "mg/dL"),
    ("FBS", "200000", "mg/dL"),            # lab_value_check: value < 100000
])
def test_lines_that_cannot_be_mapped_are_refused(label, value, unit):
    with pytest.raises(labs.MappingError):
        labs.map_field(label, value, unit)


def test_a_thousands_separator_is_accepted():
    assert labs.map_field("Eosinophils Absolute", "1,250", "cells/uL").value == Decimal("1250.000")


def test_values_outside_the_expected_range_are_kept_but_flagged():
    mapped = labs.map_field("FBS", "30", "mmol/L")  # 540 mg/dL: likely a misread or wrong unit
    assert mapped.value == Decimal("540.480") and mapped.out_of_expected_range
    assert not labs.map_field("FBS", "95", "mg/dL").out_of_expected_range


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@pytest.fixture
def make_patient():
    made = []

    def make():
        email = f"labs-{uuid.uuid4().hex[:8]}@test.local"
        made.append(email)
        assert client.post("/auth/register", json={
            "email": email, "password": PASSWORD, "first_name": "Gabriela", "last_name": "Silang",
            "date_of_birth": "1980-03-19", "biological_sex": "Female",
            "privacy_consent": True, "consent_version": settings.privacy_notice_version,
        }).status_code == 201
        token = client.post("/auth/login", json={"email": email, "password": PASSWORD}).json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    yield make
    with SessionLocal() as session:  # deleting the user cascades to the lab results
        session.execute(delete(User).where(func.lower(User.email).in_(made)))
        session.commit()


def test_a_typed_in_value_is_converted_and_confirmed(make_patient):
    headers = make_patient()
    response = client.post("/labs", headers=headers, json={
        "test_code": "FPG", "value": 5.6, "unit": "mmol/L", "collected_on": "2026-10-01"})
    assert response.status_code == 201
    data = response.json()
    assert data["value"] == 100.89 and data["unit"] == "mg/dL"
    assert data["value_as_reported"] == 5.6 and data["unit_as_reported"] == "mmol/L"
    assert data["source"] == "manual" and data["document_id"] is None
    assert data["is_confirmed"] is True and data["confirmed_at"] is not None
    assert data["collected_on"] == "2026-10-01" and data["out_of_expected_range"] is False
    assert response.headers["Location"] == f"/labs/{data['lab_result_id']}"
    assert response.headers["Cache-Control"] == "no-store"


def test_a_value_in_the_standard_unit_is_saved_as_is(make_patient):
    headers = make_patient()
    data = client.post("/labs", headers=headers, json={"test_code": "HBA1C", "value": 6.1, "unit": "%"}).json()
    assert data["value"] == 6.1 and data["value_as_reported"] is None and data["unit_as_reported"] is None


@pytest.mark.parametrize("body", [
    {"test_code": "HEMOGLOBIN", "value": 13, "unit": "g/dL"},           # not in lab_test_code_check
    {"test_code": "FPG", "value": 0, "unit": "mg/dL"},
    {"test_code": "FPG", "value": 5.6789, "unit": "mmol/L"},           # more than 3 decimals
    {"test_code": "FPG", "value": 95, "unit": ""},
    {"test_code": "FPG", "value": 95, "unit": "mg/dL", "collected_on": (date.today() + timedelta(days=1)).isoformat()},
    {"test_code": "FPG", "value": 95, "unit": "mg/dL", "source": "ocr"},  # extra field
    {"test_code": "FPG", "value": 95, "unit": "mg/dL", "is_confirmed": False},
], ids=["unknown-test", "zero", "four-decimals", "no-unit", "future-date", "source", "is-confirmed"])
def test_bad_entries_are_rejected(make_patient, body):
    headers = make_patient()
    assert client.post("/labs", headers=headers, json=body).status_code == 422


@pytest.mark.parametrize("unit", ["furlongs", "%", "mg/dL"])
def test_a_unit_that_does_not_fit_the_test_is_rejected(make_patient, unit):
    headers = make_patient()
    response = client.post("/labs", headers=headers, json={"test_code": "EOSINOPHILS", "value": 5, "unit": unit})
    assert response.status_code == 422


def test_list_is_newest_sample_first_and_filters_by_test(make_patient):
    headers = make_patient()
    for code, value, unit, when in [("FPG", 90, "mg/dL", "2026-01-10"), ("FPG", 99, "mg/dL", "2026-06-10"),
                                    ("HDL", 50, "mg/dL", "2026-03-10")]:
        assert client.post("/labs", headers=headers, json={
            "test_code": code, "value": value, "unit": unit, "collected_on": when}).status_code == 201
    everything = client.get("/labs", headers=headers).json()
    assert [row["collected_on"] for row in everything] == ["2026-06-10", "2026-03-10", "2026-01-10"]
    fpg = client.get("/labs", headers=headers, params={"test_code": "FPG"}).json()
    assert [row["value"] for row in fpg] == [99.0, 90.0]
    assert client.get("/labs", headers=headers, params={"test_code": "NOPE"}).status_code == 422


def test_read_and_delete_one_result(make_patient):
    headers = make_patient()
    lab_id = client.post("/labs", headers=headers, json={"test_code": "TSH", "value": 2.5, "unit": "mIU/L"}).json()["lab_result_id"]
    assert client.get(f"/labs/{lab_id}", headers=headers).json()["test_code"] == "TSH"
    assert client.delete(f"/labs/{lab_id}", headers=headers).status_code == 204
    assert client.get(f"/labs/{lab_id}", headers=headers).status_code == 404
    assert client.delete(f"/labs/{lab_id}", headers=headers).status_code == 404


def test_a_patient_cannot_see_or_delete_another_patients_result(make_patient):
    owner, other = make_patient(), make_patient()
    lab_id = client.post("/labs", headers=owner, json={"test_code": "LDL", "value": 130, "unit": "mg/dL"}).json()["lab_result_id"]
    assert client.get(f"/labs/{lab_id}", headers=other).status_code == 404
    assert client.delete(f"/labs/{lab_id}", headers=other).status_code == 404
    assert client.get("/labs", headers=other).json() == []
    assert client.get(f"/labs/{lab_id}", headers=owner).status_code == 200


def test_labs_need_a_login():
    assert client.get("/labs").status_code in (401, 403)
    assert client.post("/labs", json={"test_code": "FPG", "value": 95, "unit": "mg/dL"}).status_code in (401, 403)
    assert client.post("/labs/map", json={"label": "FBS", "value": "95", "unit": "mg/dL"}).status_code in (401, 403)


def test_map_previews_a_report_line_without_saving(make_patient):
    headers = make_patient()
    response = client.post("/labs/map", headers=headers, json={"label": "Cholesterol", "value": "5.2", "unit": "mmol/L"})
    assert response.status_code == 200
    assert response.json() == {"test_code": "TOTAL_CHOLESTEROL", "value": 201.084, "unit": "mg/dL",
                               "value_as_reported": 5.2, "unit_as_reported": "mmol/L",
                               "out_of_expected_range": False}
    assert client.get("/labs", headers=headers).json() == []
    bad = client.post("/labs/map", headers=headers, json={"label": "Hemoglobin", "value": "13", "unit": "g/dL"})
    assert bad.status_code == 422 and "Hemoglobin" in bad.json()["detail"]


def test_a_typed_in_lab_counts_for_readiness(make_patient):
    """HbA1c is an optional T2D input (ml/features.yaml); once typed in, it is no longer missing."""
    headers = make_patient()

    def missing_labs():
        t2d = client.get("/assessment/readiness", headers=headers).json()["models"]["T2D"]
        return [item for item in t2d["missing_optional"] if item["source"] == "lab_ocr"]

    before = missing_labs()
    assert any(item["path"] == "/labs" for item in before)  # the checklist points to the labs screen
    assert client.post("/labs", headers=headers, json={"test_code": "HBA1C", "value": 6.1, "unit": "%"}).status_code == 201
    assert len(missing_labs()) == len(before) - 1


def test_create_and_delete_are_audited_without_the_value(make_patient, monkeypatch):
    recorded = []
    real = audit.log_event

    def recording(db, event_type, **kwargs):
        recorded.append((event_type, kwargs))
        return real(db, event_type, **kwargs)

    monkeypatch.setattr(audit, "log_event", recording)
    headers = make_patient()
    lab_id = client.post("/labs", headers=headers, json={"test_code": "FPG", "value": 123.456, "unit": "mg/dL"}).json()["lab_result_id"]
    client.delete(f"/labs/{lab_id}", headers=headers)

    created, deleted = recorded[-2], recorded[-1]
    assert created[0] == "DATA_CREATED" and created[1]["table"] == "lab_results"
    assert created[1]["record_id"] == lab_id and created[1]["converted"] is False
    assert "123.456" not in str(created) and "FPG" not in str(created)
    assert deleted[0] == "DATA_DELETED" and deleted[1]["record_id"] == lab_id
