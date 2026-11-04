"""Risk endpoint: the four models run on the patient's saved data, and the assessment, results and
SHAP values are saved together. Throw-away models stand in for the trained ones (risk_helpers.py)."""

import math

import pytest

from backend import audit, model_registry
from backend.config import settings
from risk_helpers import (  # noqa: F401  (fixtures are used by name)
    FEATURES, MODELS, client, fill_required, make_patient, models_on, placeholder_ids, saved_rows,
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_models_off_returns_503(make_patient, monkeypatch):
    monkeypatch.setattr(model_registry, "ACTIVE", {})
    _, headers = make_patient()
    fill_required(headers)
    assert client.post("/assessment/run", headers=headers).status_code == 503


def test_missing_required_inputs_return_the_checklist(make_patient, models_on):
    email, headers = make_patient()
    response = client.post("/assessment/run", headers=headers)
    assert response.status_code == 409
    missing = [item["name"] for item in response.json()["detail"]["missing_required"]]
    assert "sbp" in missing and "phq9" in missing
    assert saved_rows(email) == ([], [], [])


def test_a_run_saves_the_assessment_results_and_every_shap_value(make_patient, models_on):
    email, headers = make_patient()
    fill_required(headers)
    response = client.post("/assessment/run", headers=headers)
    assert response.status_code == 201, response.text
    assert response.headers["Cache-Control"] == "no-store"
    data = response.json()
    assert response.headers["Location"] == f"/assessment/{data['assessment_id']}"
    assert [r["disease"] for r in data["results"]] == list(MODELS)
    assessments, results, shap = saved_rows(email)
    assert len(assessments) == 1 and len(results) == 4
    assert len(shap) == sum(len(names) for names in FEATURES.values())  # every input of every model
    for result in results:
        ranks = sorted(row.rank for row in shap if row.result_id == result.result_id)
        assert ranks == list(range(1, len(FEATURES[result.disease_domain]) + 1))
    assert set(assessments[0].input_snapshot) == set(MODELS) | {"capped", "templates_used"}


def test_the_same_inputs_always_give_the_same_saved_result(make_patient, models_on):
    email, headers = make_patient()
    fill_required(headers)
    first = client.post("/assessment/run", headers=headers).json()
    second = client.post("/assessment/run", headers=headers).json()
    assert first["assessment_id"] != second["assessment_id"]
    strip = lambda d: [{k: v for k, v in r.items() if k not in ("care_plan", "result_id")} for r in d["results"]]
    assert strip(first) == strip(second)
    assessments, results, shap = saved_rows(email)
    by_assessment = {}
    for row in shap:
        result = next(r for r in results if r.result_id == row.result_id)
        by_assessment.setdefault(result.assessment_id, set()).add(
            (result.disease_domain, row.feature_name, row.feature_value, row.shap_value, row.rank))
    a, b = by_assessment.values()
    assert a == b


def test_probabilities_and_tiers_match_the_models(make_patient, models_on):
    _, headers = make_patient()
    fill_required(headers)
    data = client.post("/assessment/run", headers=headers).json()
    for result in data["results"]:
        assert 0.0 <= result["probability"] <= 1.0
        model = models_on[result["disease"]]
        assert result["risk_level"] == model.tier(result["probability"])
        assert result["model_version"] == "test-only"
        assert result["consult_recommended"] == (result["risk_level"] == "High")
        assert (result["consult_message"] is not None) == result["consult_recommended"]


def test_shap_values_add_up_to_the_model_score(make_patient, models_on):
    """For a plain XGBoost model, base value + SHAP values = the model's log-odds (TreeSHAP)."""
    email, headers = make_patient()
    fill_required(headers)
    data = client.post("/assessment/run", headers=headers).json()
    assessments, results, shap = saved_rows(email)
    cvd = next(r for r in results if r.disease_domain == "CVD")
    total = float(cvd.shap_base_value) + sum(float(row.shap_value) for row in shap if row.result_id == cvd.result_id)
    probability = next(r["probability"] for r in data["results"] if r["disease"] == "CVD")
    assert abs(1 / (1 + math.exp(-total)) - probability) < 1e-3


def test_saved_feature_values_are_the_numbers_the_model_got(make_patient, models_on):
    email, headers = make_patient()
    fill_required(headers)
    client.post("/assessment/run", headers=headers)
    _, results, shap = saved_rows(email)
    neuro = next(r for r in results if r.disease_domain == "NEURO")
    values = {row.feature_name: row.feature_value for row in shap if row.result_id == neuro.result_id}
    assert values["sex"] == 1          # Male, from the encoding in ml/features.yaml
    assert values["tsh"] is None       # optional lab not entered
    assert values["phq9"] == 7


def test_results_explain_the_top_factors_in_plain_language(make_patient, models_on):
    _, headers = make_patient()
    fill_required(headers)
    data = client.post("/assessment/run", headers=headers).json()
    for result in data["results"]:
        factors = result["top_factors"]
        assert [f["rank"] for f in factors] == [1, 2, 3]
        assert all(f["sentence"].endswith(".") for f in factors)
        assert abs(factors[0]["shap_value"]) >= abs(factors[1]["shap_value"]) >= abs(factors[2]["shap_value"])
    assert data["disclaimer"].startswith("This is an educational screening estimate")


def test_each_result_comes_with_the_care_plan_for_its_tier(make_patient, models_on):
    _, headers = make_patient()
    fill_required(headers)
    data = client.post("/assessment/run", headers=headers).json()
    for result in data["results"]:
        plan = result["care_plan"]
        assert plan["disease_domain"] == result["disease"] and plan["risk_level"] == result["risk_level"]
        assert plan["base"] and plan["is_draft"] is True
    cvd_plan = data["results"][0]["care_plan"]
    assert "A1" in [a["code"] for a in cvd_plan["addons"]]  # current smoker


def test_without_approved_templates_the_care_plan_is_empty(make_patient, models_on, monkeypatch):
    monkeypatch.setattr(settings, "show_draft_care_plans", False)
    _, headers = make_patient()
    fill_required(headers)
    data = client.post("/assessment/run", headers=headers).json()
    assert all(result["care_plan"] is None for result in data["results"])


def test_saved_assessments_can_be_read_back(make_patient, models_on):
    _, headers = make_patient()
    fill_required(headers)
    created = client.post("/assessment/run", headers=headers).json()
    again = client.get(f"/assessment/{created['assessment_id']}", headers=headers)
    assert again.status_code == 200 and again.json() == created
    listed = client.get("/assessment", headers=headers).json()
    assert [row["assessment_id"] for row in listed] == [created["assessment_id"]]
    assert set(listed[0]["risk_levels"]) == set(MODELS)


def test_another_patient_cannot_read_an_assessment(make_patient, models_on):
    _, owner = make_patient()
    fill_required(owner)
    assessment_id = client.post("/assessment/run", headers=owner).json()["assessment_id"]
    _, other = make_patient()
    assert client.get(f"/assessment/{assessment_id}", headers=other).status_code == 404
    assert client.get("/assessment", headers=other).json() == []


def test_a_failure_part_way_saves_nothing(make_patient, models_on, monkeypatch):
    """One transaction: if the third model fails, the first two results are not kept either."""
    email, headers = make_patient()
    fill_required(headers)

    def broken(inputs):
        raise RuntimeError("model crashed")

    monkeypatch.setattr(models_on["CRD"], "contributions", broken)
    with pytest.raises(RuntimeError):
        client.post("/assessment/run", headers=headers)
    assert saved_rows(email) == ([], [], [])


def test_a_run_is_audited_without_results(make_patient, models_on, monkeypatch):
    recorded = []
    real = audit.log_event

    def recording(db, event_type, **kwargs):
        recorded.append((event_type, kwargs))
        return real(db, event_type, **kwargs)

    monkeypatch.setattr(audit, "log_event", recording)
    _, headers = make_patient()
    fill_required(headers)
    data = client.post("/assessment/run", headers=headers).json()
    event_type, details = recorded[-1]
    assert event_type == "RISK_ASSESSMENT_RUN" and details["record_id"] == data["assessment_id"]
    assert details["model_versions"] == {d: "test-only" for d in MODELS}
    text = str(details)
    assert "High" not in text and "Low" not in text and "probability" not in text


def test_assessments_need_a_login():
    assert client.post("/assessment/run").status_code == 401
    assert client.get("/assessment").status_code == 401


# ---------------------------------------------------------------------------
# Review fixes: capped values, templates used, result_id, patient_id filters
# ---------------------------------------------------------------------------
def test_a_capped_value_is_saved_and_explained_with_what_was_entered(make_patient, models_on, monkeypatch):
    email, headers = make_patient()
    fill_required(headers)
    send(headers, "/vitals", {"sbp": 240, "dbp": 90})  # features.yaml sbp range: 70-220
    monkeypatch.setattr(risk, "TOP_FACTORS", 100)      # show every factor, so sbp is listed
    body = client.post("/assessment/run", headers=headers).json()
    assessments, _, _ = saved_rows(email)
    snapshot = assessments[0].input_snapshot
    assert snapshot["CVD"]["sbp"] == 220
    assert snapshot["capped"]["CVD"]["sbp"] == {"entered": 240, "used": 220}
    cvd = next(r for r in body["results"] if r["disease"] == "CVD")
    sbp = next(f for f in cvd["top_factors"] if f["feature"] == "sbp")
    assert "of 240 mmHg (counted as 220, the highest value the model uses)" in sbp["sentence"]


def test_the_care_plan_templates_used_are_saved_with_the_assessment(make_patient, models_on):
    email, headers = make_patient()
    fill_required(headers)
    body = client.post("/assessment/run", headers=headers).json()
    used = saved_rows(email)[0][0].input_snapshot["templates_used"]
    assert set(used) == set(MODELS)
    for result in body["results"]:
        plan = result["care_plan"]
        assert used[result["disease"]] == {"template_id": plan["template_id"], "version": plan["version"]}


def test_each_result_returns_its_result_id(make_patient, models_on):
    email, headers = make_patient()
    fill_required(headers)
    body = client.post("/assessment/run", headers=headers).json()
    _, results, _ = saved_rows(email)
    saved = {r.disease_domain: r.result_id for r in results}
    assert {r["disease"]: r["result_id"] for r in body["results"]} == saved
    again = client.get(f"/assessment/{body['assessment_id']}", headers=headers).json()
    assert [r["result_id"] for r in again["results"]] == [r["result_id"] for r in body["results"]]


def test_view_and_list_filter_on_patient_id_even_if_rls_shows_someone_else(make_patient, models_on):
    """Second layer: the session's RLS identity is the owner, but the view is asked for as another
    patient. It must not return the owner's assessment."""
    owner_email, owner = make_patient()
    fill_required(owner)
    assessment_id = client.post("/assessment/run", headers=owner).json()["assessment_id"]
    other_email, _ = make_patient()
    with SessionLocal() as session:
        ids = {}
        for email in (owner_email, other_email):
            user_id = session.scalar(select(User.user_id).where(User.email == email))
            set_request_identity(session, user_id=user_id)
            ids[email] = (user_id, session.scalar(select(Patient.patient_id)))
        set_request_identity(session, user_id=ids[owner_email][0], patient_id=ids[owner_email][1])
        assert risk.view(session, ids[owner_email][1], assessment_id)["assessment_id"] == assessment_id
        with pytest.raises(risk.NotFound):
            risk.view(session, ids[other_email][1], assessment_id)
        assert risk.list_assessments(session, ids[other_email][1], 20) == []
