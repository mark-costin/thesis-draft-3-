"""Plain-language SHAP sentences: every model input has one, and each reads as a full sentence
with the value, its unit and the direction of the effect."""

import pytest

from backend import explanations
from backend.readiness import MODELS, load_features

ALL = [(f.model, f.name) for f in load_features()]


def test_every_model_input_has_a_sentence():
    assert explanations.check_coverage(f.name for f in load_features()) == []


def test_no_sentence_is_left_unused():
    """A sentence for a feature that no model uses is probably a typo in a feature name."""
    assert set(explanations.FEATURES) == {f.name for f in load_features()}


def sample_value(name):
    entry = explanations.FEATURES[name]
    if isinstance(entry, explanations.YesNo):
        return 1
    if isinstance(entry, explanations.Sex):
        return "Male"
    return 12.5


@pytest.mark.parametrize("model, name", ALL, ids=[f"{m}-{n}" for m, n in ALL])
def test_each_input_reads_as_a_sentence(model, name):
    for value in (sample_value(name), None):
        for shap in (0.4, -0.4, 0.0):
            text = explanations.sentence(name, value, shap, model)
            assert text[0].isupper() and text.endswith(".")
            assert "None" not in text and "{" not in text and "  " not in text
            if value is not None:
                assert explanations.DISEASE_NAMES[model] in text


@pytest.mark.parametrize("value, shap, expected", [
    (145, 0.42, "Your systolic (upper) blood pressure of 145 mmHg raised your risk of heart disease and stroke."),
    (110, -0.3, "Your systolic (upper) blood pressure of 110 mmHg lowered your risk of heart disease and stroke."),
    (120, 0.0004, "Your systolic (upper) blood pressure of 120 mmHg had almost no effect on your risk of heart disease and stroke."),
    (None, 0.2, "Your systolic (upper) blood pressure was not entered yet. Adding it can make this result more accurate."),
])
def test_a_measurement_sentence(value, shap, expected):
    assert explanations.sentence("sbp", value, shap, "CVD") == expected


def test_numbers_are_shown_simply():
    assert "of 22.86 kg/m² " in explanations.sentence("bmi", 22.857, 0.1, "T2D")
    assert "of 6.5% " in explanations.sentence("hba1c", 6.50, 0.1, "T2D")
    assert "(score 0.25)" in explanations.sentence("fhrs_cvd", 0.25, 0.1, "CVD")
    assert "(score 12)" in explanations.sentence("phq9", 12.0, 0.1, "NEURO")


def test_yes_and_no_inputs_read_differently():
    yes = explanations.sentence("occupational_dust", 1, 0.3, "CRD")
    no = explanations.sentence("occupational_dust", 0, -0.1, "CRD")
    assert yes == "Being exposed to dust or fumes at work raised your risk of chronic lung disease."
    assert no == "Not being exposed to dust or fumes at work lowered your risk of chronic lung disease."


@pytest.mark.parametrize("value, word", [("Male", "male"), ("Female", "female"), (1, "male"), (0, "female")])
def test_sex_reads_as_a_word(value, word):
    assert explanations.sentence("sex", value, 0.2, "NEURO") == f"Being {word} raised your risk of depression and anxiety."


def test_unknown_feature_or_disease_is_refused():
    with pytest.raises(ValueError):
        explanations.sentence("shoe_size", 42, 0.1, "CVD")
    with pytest.raises(ValueError):
        explanations.sentence("sbp", 120, 0.1, "CANCER")


def test_factors_are_ranked_by_size_of_effect():
    shap = {"sbp": 0.10, "fhrs_cvd": 0.55, "hdl": -0.30, "ldl": 0.02}
    inputs = {"sbp": 128, "fhrs_cvd": 2.5, "hdl": 60, "ldl": None}
    rows = explanations.ranked_factors("CVD", shap, inputs)
    assert [(r["rank"], r["feature_name"]) for r in rows] == [(1, "fhrs_cvd"), (2, "hdl"), (3, "sbp")]
    assert rows[1]["sentence"] == "Your HDL (good) cholesterol of 60 mg/dL lowered your risk of heart disease and stroke."
    assert rows[0]["feature_value"] == 2.5 and rows[0]["shap_value"] == 0.55
    everything = explanations.ranked_factors("CVD", shap, inputs, limit=None)
    assert [r["rank"] for r in everything] == [1, 2, 3, 4]
    assert "not entered yet" in everything[3]["sentence"]


def test_every_disease_has_a_plain_name():
    assert set(explanations.DISEASE_NAMES) == set(MODELS)
