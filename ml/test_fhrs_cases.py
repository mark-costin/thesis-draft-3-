"""Runs ml/fhrs_cases.yaml through the generator's fhrs() and band() (reads the notebook, changes nothing).
tests/test_fhrs_cases.py runs the same cases through backend/fhrs.py. Run: uv run pytest ml/test_fhrs_cases.py"""
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

from backend.fhrs import degree_of, sex_of

sys.path.insert(0, str(Path(__file__).parent / "investigate"))
import auroc_ceiling as ac  # noqa: E402

ML = Path(__file__).parent
SHARED = yaml.safe_load((ML / "fhrs_cases.yaml").read_text())
BANDS = ["Baseline", "Mild", "Moderate", "Pronounced", "Critical"]


@pytest.fixture(scope="module")
def gen():
    return ac.load_generator(ML / "synthetic/01_syntgen.ipynb")


def family(case):
    people = {}
    for row in case["relatives"]:
        person = people.setdefault((row["relative"], row.get("relative_no", 1)), {
            "degree": degree_of(row["relative"]), "sex": sex_of(row["relative"])[0], "conditions": []})
        person["conditions"].append({"disease": row["disease"], "year": 2000, "age": row["age"],
                                     "confirmed": row["confirmed"]})
    return list(people.values())


@pytest.mark.parametrize("case", SHARED["cases"], ids=lambda c: c["name"])
def test_generator_case(gen, case):
    exposures = dict.fromkeys(case.get("exposures", []), True)
    score = gen["fhrs"](family(case), exposures, case["domain"], date(2026, 1, 1), gen["CFG"])
    assert score == case["expected"]["score"]
    assert BANDS[gen["band"](score)] == case["expected"]["band"]


@pytest.mark.parametrize("edge", SHARED["band_edges"], ids=lambda e: str(e["score"]))
def test_generator_band_edge(gen, edge):
    assert BANDS[gen["band"](edge["score"])] == edge["band"]
