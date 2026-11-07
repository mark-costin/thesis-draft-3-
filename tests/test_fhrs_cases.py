"""Runs ml/fhrs_cases.yaml through backend/fhrs.py. ml/test_fhrs_cases.py runs the same cases
through the generator, so the app and the training data cannot drift apart unseen."""

from pathlib import Path

import pytest
import yaml

from backend import fhrs

SHARED = yaml.safe_load((Path(__file__).parents[1] / "ml/fhrs_cases.yaml").read_text())


def entry(case, row):
    onset = "unknown" if row["age"] is None else fhrs.onset_from_age(
        case["domain"], row["diagnosis"], fhrs.sex_of(row["relative"]), row["age"])
    return fhrs.FamilyEntry(relative=row["relative"], domain=case["domain"], onset=onset,
                            confirmed="yes" if row["confirmed"] else "not_sure",
                            shared_environment=bool(case.get("exposures")), diagnosis=row["diagnosis"],
                            relative_no=row.get("relative_no", 1))


@pytest.mark.parametrize("case", SHARED["cases"], ids=lambda c: c["name"])
def test_backend_case(case):
    result = fhrs.calculate(case["domain"], [entry(case, row) for row in case["relatives"]])
    expected = case["expected"]
    assert (result.calculated_fhrs, result.risk_band, result.clustering_score, result.affected_relatives) == \
        (expected["score"], expected["band"], expected["clustering"], expected["people"])


@pytest.mark.parametrize("case", SHARED["cases"], ids=lambda c: c["name"])
def test_diagnosis_text_gives_the_generator_label(case):
    for row in case["relatives"]:
        assert fhrs.disease_of(case["domain"], row["diagnosis"]) == row["disease"]


@pytest.mark.parametrize("edge", SHARED["band_edges"], ids=lambda e: str(e["score"]))
def test_backend_band_edge(edge):
    assert fhrs.risk_band(edge["score"]) == edge["band"]
