"""Family history and FHRS: each save recalculates the FHRS per disease."""

import uuid
from functools import partial

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from backend import audit, fhrs, services
from backend.config import settings
from backend.database import SessionLocal, set_request_identity
from backend.main import app
from backend.models import PatientFamilyHistory, PatientFhrsStratification, User
from backend.recalculate_fhrs import recalculate_all

client = TestClient(app)
PASSWORD = "correct horse battery"
E = partial(fhrs.FamilyEntry, diagnosis="Heart attack")


# --- The formula (no database) ---------------------------------------------------------------

def test_guide_worked_example_john():
    """FHRS Guide section 6: father 48 (proven), brother 38 (proven), uncle 60 (not proven),
    household smoke. Guide result 4.17, Band 3 Pronounced."""
    entries = [
        E("father", "CVD", fhrs.onset_from_age("CVD", "Heart attack", "Male", 48), "yes", True),
        E("brother", "CVD", fhrs.onset_from_age("CVD", "Heart stent", "Male", 38), "yes", False),
        E("paternal_uncle", "CVD", fhrs.onset_from_age("CVD", "Heart disease", "Male", 60), "not_sure", False),
    ]
    assert [e.onset for e in entries] == ["early", "severe_early", "typical"]
    result = fhrs.calculate("CVD", entries)
    assert result.clustering_score == 1.0
    assert result.environmental_modifier == 1.15
    assert result.calculated_fhrs == 4.17  # 3.625 x 1.15, the guide's figure
    assert result.risk_band == "Pronounced"
    assert result.affected_relatives == 3


@pytest.mark.parametrize("domain, diagnosis, sex, age, expected", [
    ("CVD", "MI", "Male", 39, "severe_early"), ("CVD", "MI", "Male", 54, "early"),
    ("CVD", "MI", "Male", 55, "typical"), ("CVD", "MI", "Male", 65, "late"),
    ("CVD", "MI", "Female", 64, "early"), ("CVD", "MI", "Female", 65, "typical"),
    ("CVD", "MI", "Female", 74, "typical"), ("CVD", "MI", "Female", 75, "late"),
    ("T2D", "Type 2 diabetes", "Male", 39, "severe_early"), ("T2D", "Type 2 diabetes", "Male", 49, "early"),
    ("T2D", "Type 2 diabetes", "Female", 64, "typical"), ("T2D", "Type 2 diabetes", "Female", 65, "late"),
    ("CRD", "Asthma", "Male", 11, "severe_early"), ("CRD", "Childhood asthma", "Male", 25, "early"),
    ("CRD", "Asthma", "Male", 40, "typical"), ("CRD", "Asthma", "Male", 41, "late"),
    ("CRD", "COPD", "Male", 39, "severe_early"), ("CRD", "Emphysema", "Male", 64, "typical"),
    ("CRD", "COPD", "Male", 65, "late"),
    # Depression: under 18 / 18-24 / 25-40 / over 40
    ("NEURO", "Depression", "Female", 17, "severe_early"), ("NEURO", "Depression", "Female", 18, "early"),
    ("NEURO", "Depression", "Female", 24, "early"), ("NEURO", "Depression", "Female", 25, "typical"),
    ("NEURO", "Depression", "Female", 40, "typical"), ("NEURO", "Depression", "Female", 41, "late"),
    # Anxiety: always typical, whatever the age
    ("NEURO", "Anxiety", "Female", 15, "typical"), ("NEURO", "Generalized anxiety disorder", "Male", 70, "typical"),
    ("NEURO", "GAD", "Female", 15, "typical"),
])
def test_onset_cutoffs_from_the_guide(domain, diagnosis, sex, age, expected):
    assert fhrs.onset_from_age(domain, diagnosis, sex, age) == expected


@pytest.mark.parametrize("domain, modifier", [("CVD", 1.15), ("CRD", 1.15), ("T2D", 1.0), ("NEURO", 1.0)])
def test_shared_household_modifier_per_disease(domain, modifier):
    """FHRS Guide: household smoke +0.15 for CVD and CRD only. NEURO has no modifier; T2D has
    none until the group sets one."""
    result = fhrs.calculate(domain, [E("father", domain, "typical", "yes", True)])
    assert result.environmental_modifier == modifier
    assert result.calculated_fhrs == round(0.5 * modifier, 2)


@pytest.mark.parametrize("score, band", [(0, "Baseline"), (0.047, "Mild"), (1.75, "Mild"), (1.76, "Moderate"),
                                         (3.50, "Moderate"), (3.51, "Pronounced"), (6.00, "Pronounced"),
                                         (6.01, "Critical")])
def test_risk_bands(score, band):
    assert fhrs.risk_band(score) == band


def test_same_relative_counts_once_per_disease():
    entries = [E("mother", "CVD", "late", "yes", False), E("mother", "CVD", "early", "yes", False)]
    result = fhrs.calculate("CVD", entries)
    assert result.affected_relatives == 1
    assert result.calculated_fhrs == 1.0  # 0.5 x 2.0 x 1.0, the higher term; no cluster bonus


def test_higher_term_wins_whichever_diagnosis_comes_first():
    entries = [E("mother", "CRD", "typical", "yes", False, diagnosis="Asthma"),
               E("mother", "CRD", "early", "yes", False, diagnosis="COPD")]
    assert fhrs.calculate("CRD", entries).calculated_fhrs == 1.0
    assert fhrs.calculate("CRD", entries[::-1]).calculated_fhrs == 1.0


def test_only_confirmed_relatives_form_a_cluster():
    unconfirmed = [E("father", "T2D", "typical", "no", False), E("mother", "T2D", "typical", "not_sure", False)]
    assert fhrs.calculate("T2D", unconfirmed).clustering_score == 0
    confirmed = [E("father", "T2D", "typical", "yes", False), E("mother", "T2D", "typical", "yes", False),
                 E("paternal_aunt", "T2D", "typical", "yes", False), E("maternal_uncle", "T2D", "typical", "yes", False)]
    assert fhrs.calculate("T2D", confirmed).clustering_score == 1.0  # flat; the aunt and uncle add nothing


def test_other_diseases_are_not_counted():
    assert fhrs.calculate("NEURO", [E("father", "CVD", "early", "yes", True)]).risk_band == "Baseline"


# --- The API ---------------------------------------------------------------------------------

@pytest.fixture
def make_patient():
    """Registers and logs in patients; returns auth headers. Deleted after the test."""
    made = []

    def make():
        email = f"family-{uuid.uuid4().hex[:8]}@test.local"
        made.append(email)
        assert client.post("/auth/register", json={
            "email": email, "password": PASSWORD, "first_name": "Juan", "last_name": "Luna",
            "date_of_birth": "1980-10-23", "biological_sex": "Male",
            "privacy_consent": True, "consent_version": settings.privacy_notice_version,
        }).status_code == 201
        token = client.post("/auth/login", json={"email": email, "password": PASSWORD}).json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    yield make
    with SessionLocal() as session:
        session.execute(delete(User).where(func.lower(User.email).in_(made)))
        session.commit()


def add(headers, **body):
    return client.post("/family-history", headers=headers, json={
        "disease_domain": "CVD", "specific_diagnosis": "Heart attack", "relative": "father"} | body)


def fhrs_by_domain(headers):
    return {row["disease_domain"]: row for row in client.get("/family-history/fhrs", headers=headers).json()}


def test_john_through_the_api(make_patient):
    headers = make_patient()
    add(headers, relative="father", age_at_diagnosis=48, diagnosis_confirmed="yes", shared_environment=True)
    add(headers, relative="brother", specific_diagnosis="Heart stent", age_at_diagnosis=38, diagnosis_confirmed="yes")
    add(headers, relative="paternal_uncle", specific_diagnosis="Heart disease", age_at_diagnosis=60)
    scores = fhrs_by_domain(headers)
    assert set(scores) == {"CVD", "T2D", "CRD", "NEURO"}
    assert scores["CVD"]["calculated_fhrs"] == 4.17 and scores["CVD"]["risk_band"] == "Pronounced"
    assert scores["T2D"]["risk_band"] == "Baseline"


def test_each_save_recalculates(make_patient):
    headers = make_patient()
    entry = add(headers, age_at_diagnosis=60).json()  # typical, not sure: 0.5 x 1.0 x 0.5 = 0.25
    assert fhrs_by_domain(headers)["CVD"]["calculated_fhrs"] == 0.25

    client.patch(f"/family-history/{entry['record_id']}", headers=headers, json={"diagnosis_confirmed": "yes"})
    assert fhrs_by_domain(headers)["CVD"]["calculated_fhrs"] == 0.5

    client.delete(f"/family-history/{entry['record_id']}", headers=headers)
    assert fhrs_by_domain(headers)["CVD"]["risk_band"] == "Baseline"


def test_entry_response_has_database_derived_fields(make_patient):
    headers = make_patient()
    response = add(headers, relative="maternal_aunt", specific_diagnosis="  <b>Hypertension</b> ")
    assert response.status_code == 201
    data = response.json()
    assert (data["relationship_degree"], data["relative_sex"]) == (2, "Female")
    assert data["specific_diagnosis"] == "Hypertension"
    assert data["onset_classification"] == "unknown" and data["diagnosis_confirmed"] == "not_sure"
    assert response.headers["Location"] == f"/family-history/{data['record_id']}"
    assert response.headers["Cache-Control"] == "no-store"


def test_python_relative_lists_match_the_database(make_patient):
    headers = make_patient()
    for relative in sorted(fhrs.RELATIVES):
        data = add(headers, relative=relative, disease_domain="T2D", specific_diagnosis="Type 2 diabetes").json()
        assert data["relationship_degree"] == fhrs.degree_of(relative), relative
        assert data["relative_sex"] == fhrs.sex_of(relative), relative


def test_age_sets_the_onset_and_overrides_a_choice(make_patient):
    headers = make_patient()
    with_age = add(headers, relative="mother", age_at_diagnosis=50, onset_classification="late").json()
    assert with_age["onset_classification"] == "early"  # female CVD 40-64
    without_age = add(headers, relative="sister", onset_classification="severe_early").json()
    assert without_age["onset_classification"] == "severe_early"
    edited = client.patch(f"/family-history/{without_age['record_id']}", headers=headers,
                          json={"age_at_diagnosis": 70}).json()
    assert edited["onset_classification"] == "typical"  # female CVD 65-74


def test_patient_with_no_family_history_records_baseline(make_patient):
    headers = make_patient()
    assert client.get("/family-history/fhrs", headers=headers).json() == []
    rows = client.post("/family-history/fhrs", headers=headers).json()
    assert {r["disease_domain"]: r["risk_band"] for r in rows} == {
        "CVD": "Baseline", "T2D": "Baseline", "CRD": "Baseline", "NEURO": "Baseline"}
    readiness = client.get("/assessment/readiness", headers=headers).json()
    assert not any(item["enter_in"] == "Family history" for item in readiness["missing_required"])


def test_duplicate_entry_is_refused(make_patient):
    headers = make_patient()
    assert add(headers).status_code == 201
    assert add(headers).status_code == 409
    assert add(headers, specific_diagnosis="Stroke").status_code == 201


def test_two_brothers_with_the_same_diagnosis(make_patient):
    headers = make_patient()
    brother = {"relative": "brother", "disease_domain": "T2D", "specific_diagnosis": "Type 2 diabetes",
               "age_at_diagnosis": 55, "diagnosis_confirmed": "yes"}
    first = add(headers, **brother)
    assert first.status_code == 201 and first.json()["relative_no"] == 1  # default
    assert add(headers, **brother).status_code == 409  # same person, same diagnosis
    second = add(headers, **brother, relative_no=2)
    assert second.status_code == 201 and second.json()["relative_no"] == 2
    score = fhrs_by_domain(headers)["T2D"]
    assert (score["calculated_fhrs"], score["clustering_score"], score["affected_relatives"]) == (2.0, 1.0, 2)


def test_two_great_grandmothers_both_count(make_patient):
    headers = make_patient()
    great_grandmother = {"relative": "great_grandmother", "disease_domain": "T2D",
                         "specific_diagnosis": "Type 2 diabetes", "age_at_diagnosis": 55, "diagnosis_confirmed": "yes"}
    assert add(headers, **great_grandmother).status_code == 201
    assert add(headers, **great_grandmother, relative_no=2).status_code == 201
    score = fhrs_by_domain(headers)["T2D"]
    assert (score["calculated_fhrs"], score["clustering_score"], score["affected_relatives"]) == (0.25, 0.0, 2)


@pytest.mark.parametrize("relative", sorted(fhrs.GREAT_GRANDPARENTS))
def test_great_grandparents_stop_at_four(make_patient, relative):
    headers = make_patient()
    assert add(headers, relative=relative, relative_no=4).status_code == 201
    assert add(headers, relative=relative, relative_no=5).status_code == 422


@pytest.mark.parametrize("relative", sorted(fhrs.SINGLE_RELATIVES))
def test_single_roles_cannot_repeat(make_patient, relative):
    headers = make_patient()
    assert add(headers, relative=relative, relative_no=2).status_code == 422
    assert client.get("/family-history", headers=headers).json() == []


def test_database_agrees_on_which_roles_repeat(db, two_patients):
    """Past the API, the CHECK must refuse the same roles, and only those."""
    user_id, patient_id = two_patients["A"]
    set_request_identity(db, user_id=user_id, patient_id=patient_id)
    for relative in sorted(fhrs.RELATIVES):
        db.add(PatientFamilyHistory(patient_id=patient_id, disease_domain="T2D", specific_diagnosis="x",
                                    relative=relative, relative_no=2))
        if relative in fhrs.SINGLE_RELATIVES:
            with pytest.raises(IntegrityError):
                db.flush()
        else:
            db.flush()
        db.rollback()


@pytest.mark.parametrize("relative", sorted(fhrs.GREAT_GRANDPARENTS))
def test_database_stops_great_grandparents_at_four(db, two_patients, relative):
    user_id, patient_id = two_patients["A"]
    set_request_identity(db, user_id=user_id, patient_id=patient_id)
    for number in (4, 5):
        db.add(PatientFamilyHistory(patient_id=patient_id, disease_domain="T2D", specific_diagnosis="x",
                                    relative=relative, relative_no=number))
    with pytest.raises(IntegrityError):
        db.flush()


def test_recalculate_all_replaces_stale_scores_only_for_patients_who_have_one(db, two_patients):
    user_a, patient_a = two_patients["A"]
    user_b, patient_b = two_patients["B"]
    set_request_identity(db, user_id=user_a, patient_id=patient_a)
    for number in (1, 2):
        db.add(PatientFamilyHistory(patient_id=patient_a, disease_domain="T2D", specific_diagnosis="Type 2 diabetes",
                                    relative="brother", relative_no=number, onset_classification="typical",
                                    diagnosis_confirmed="yes"))
    db.add(PatientFhrsStratification(patient_id=patient_a, disease_domain="T2D", affected_relatives=1,
                                     clustering_score=0, environmental_modifier=1, calculated_fhrs=0.5,
                                     risk_band="Mild"))  # what the old code saved
    db.commit()

    assert recalculate_all() >= 1

    latest = {row.disease_domain: row for row in services.latest_fhrs(db, patient_a)}
    assert float(latest["T2D"].calculated_fhrs) == 2.0 and latest["T2D"].risk_band == "Moderate"
    assert db.scalar(select(func.count()).select_from(PatientFhrsStratification)) == 5  # old row kept, 4 new
    set_request_identity(db, user_id=user_b, patient_id=patient_b)
    assert services.latest_fhrs(db, patient_b) == []  # never answered: stays unanswered


def test_family_history_is_per_patient(make_patient):
    headers_a, headers_b = make_patient(), make_patient()
    record_id = add(headers_a, diagnosis_confirmed="yes").json()["record_id"]
    fhrs_a = fhrs_by_domain(headers_a)
    assert client.get("/family-history", headers=headers_b).json() == []
    assert client.get("/family-history/fhrs", headers=headers_b).json() == []
    assert client.get(f"/family-history/{record_id}", headers=headers_b).status_code == 404
    assert client.patch(f"/family-history/{record_id}", headers=headers_b,
                        json={"diagnosis_confirmed": "no"}).status_code == 404
    assert client.delete(f"/family-history/{record_id}", headers=headers_b).status_code == 404
    assert len(client.get("/family-history", headers=headers_a).json()) == 1
    # B's own saves recalculate only B's scores
    add(headers_b, relative="mother", diagnosis_confirmed="yes")
    assert client.post("/family-history/fhrs", headers=headers_b).status_code in (200, 201)
    assert fhrs_by_domain(headers_a) == fhrs_a
    assert client.get(f"/family-history/{record_id}", headers=headers_a).json()["diagnosis_confirmed"] == "yes"


def test_anxiety_onset_is_typical_through_the_api(make_patient):
    headers = make_patient()
    with_age = add(headers, disease_domain="NEURO", specific_diagnosis="Anxiety", relative="mother",
                   age_at_diagnosis=15)
    assert with_age.status_code == 201 and with_age.json()["onset_classification"] == "typical"
    chosen = add(headers, disease_domain="NEURO", specific_diagnosis="GAD", relative="sister",
                 onset_classification="early")
    assert chosen.status_code == 201 and chosen.json()["onset_classification"] == "typical"


def test_neuro_and_t2d_ignore_shared_household(make_patient):
    headers = make_patient()
    for domain, diagnosis in (("CVD", "Heart attack"), ("CRD", "COPD"), ("T2D", "Type 2 diabetes"),
                              ("NEURO", "Depression")):
        assert add(headers, disease_domain=domain, specific_diagnosis=diagnosis,
                   shared_environment=True).status_code == 201
    scores = fhrs_by_domain(headers)
    assert {d: scores[d]["environmental_modifier"] for d in scores} == \
        {"CVD": 1.15, "CRD": 1.15, "T2D": 1.0, "NEURO": 1.0}


@pytest.mark.parametrize("body", [
    {"relative": "neighbor"},
    {"disease_domain": "CANCER"},
    {"age_at_diagnosis": 130},
    {"specific_diagnosis": ""},
    {"specific_diagnosis": "<script>x</script>"},
    {"onset_classification": "very_early"},
    {"diagnosis_confirmed": "maybe"},
    {"relationship_degree": 1},
    {"patient_id": 1},
], ids=["bad-relative", "bad-domain", "age-high", "empty-diagnosis", "html-only", "bad-onset",
        "bad-confirmed", "degree-sent", "patient-id-sent"])
def test_invalid_entries_are_rejected(make_patient, body):
    headers = make_patient()
    assert add(headers, **body).status_code == 422
    assert client.get("/family-history", headers=headers).json() == []


def test_family_history_needs_login():
    assert client.get("/family-history").status_code == 401
    assert client.post("/family-history/fhrs").status_code == 401


def test_audit_holds_no_diagnosis_or_relative(make_patient, monkeypatch):
    recorded = []
    real = audit.log_event

    def recording(db, event_type, **kwargs):
        recorded.append((event_type, kwargs))
        return real(db, event_type, **kwargs)

    monkeypatch.setattr(audit, "log_event", recording)
    headers = make_patient()
    record_id = add(headers, specific_diagnosis="Heart failure", relative="mother").json()["record_id"]
    client.patch(f"/family-history/{record_id}", headers=headers, json={"diagnosis_confirmed": "yes"})
    created, updated = recorded[-2:]
    assert created[0] == "DATA_CREATED" and updated[0] == "DATA_UPDATED"
    assert updated[1]["fields"] == ["diagnosis_confirmed"]
    assert "Heart" not in str(recorded[-2:]) and "mother" not in str(recorded[-2:])
