"""Family History Risk Score (FHRS), from the CDSS Data Dictionary's FHRS Guide.

    BioFHRS_d      = sum over relatives of (R_i x O_i x S_i)  +  C_d
    AdjustedFHRS_d = BioFHRS_d x E_d

    R_i  relationship weight: 1st degree 0.50, 2nd degree 0.25, 3rd degree 0.125
    O_i  age-of-onset multiplier: severe early 3.0, early 2.0, typical 1.0, late 0.75
         ('unknown' is scored as typical, 21_famhistory.sql)
    S_i  verification: diagnosis confirmed by a doctor 1.0, otherwise 0.5
    C_d  clustering: sum over degrees k of W_k x max(0, confirmed relatives of degree k - 1),
         W = 1.0 / 0.5 / 0.25 (cdss_spec.json; the guide's example: two confirmed
         first-degree relatives add +1.0)
    E_d  environment: 1.0 + shared household exposure, per disease (see SHARED_ENVIRONMENT_WEIGHT)

Bands: 0 Baseline (0), Mild (below 1.76), Moderate (1.76 to below 3.51),
Pronounced (3.51 to 6.00), Critical (above 6.00).

Pure functions only (no database), so every rule can be tested directly.
"""

import re
from dataclasses import dataclass

DOMAINS = ("CVD", "T2D", "CRD", "NEURO")

RELATIONSHIP_WEIGHT = {1: 0.50, 2: 0.25, 3: 0.125}            # R_i
ONSET_MULTIPLIER = {"severe_early": 3.0, "early": 2.0, "typical": 1.0, "late": 0.75,
                    "unknown": 1.0}                            # O_i
VERIFICATION = {"yes": 1.0, "no": 0.5, "not_sure": 0.5}        # S_i
CLUSTER_WEIGHT = {1: 1.0, 2: 0.5, 3: 0.25}                     # W_k

# E_d = 1.0 + this weight when at least one affected relative shared the patient's household
# (the table records one yes/no per relative). FHRS Guide (Data Dictionary, Oct 31):
# household secondhand smoke = +0.15 for CVD and CRD; NEURO has no environmental modifier.
SHARED_ENVIRONMENT_WEIGHT = {
    "CVD": 0.15,
    "CRD": 0.15,
    # T2D: to be set by the group. No smoke penalty yet, so E = 1.0. (cdss_spec.json lists
    # processed diet 0.15 and physical inactivity 0.20, which are not household exposures.)
    "T2D": 0.0,
    "NEURO": 0.0,  # always 1.0
}

# Same lists as the generated columns in 21_famhistory.sql (a test checks they match)
FIRST_DEGREE = {"father", "mother", "brother", "sister", "son", "daughter"}
SECOND_DEGREE = {"paternal_grandfather", "paternal_grandmother", "maternal_grandfather",
                 "maternal_grandmother", "paternal_uncle", "paternal_aunt", "maternal_uncle",
                 "maternal_aunt", "half_brother", "half_sister", "grandson", "granddaughter",
                 "nephew", "niece"}
THIRD_DEGREE = {"paternal_male_cousin", "paternal_female_cousin", "maternal_male_cousin",
                "maternal_female_cousin", "great_grandfather", "great_grandmother"}
RELATIVES = FIRST_DEGREE | SECOND_DEGREE | THIRD_DEGREE
MALE_RELATIVES = {"father", "brother", "son", "paternal_grandfather", "maternal_grandfather",
                  "paternal_uncle", "maternal_uncle", "half_brother", "grandson", "nephew",
                  "paternal_male_cousin", "maternal_male_cousin", "great_grandfather"}


def degree_of(relative: str) -> int:
    return 1 if relative in FIRST_DEGREE else 2 if relative in SECOND_DEGREE else 3


def sex_of(relative: str) -> str:
    return "Male" if relative in MALE_RELATIVES else "Female"


def _by_age(age: int, severe_below: int, early_below: int, typical_below: int) -> str:
    if age < severe_below:
        return "severe_early"
    if age < early_below:
        return "early"
    if age < typical_below:
        return "typical"
    return "late"


_ANXIETY = re.compile(r"\b(anxiety|gad)\b", re.IGNORECASE)


def fixed_onset(domain: str, diagnosis: str) -> str | None:
    """Diagnoses whose onset level does not depend on age. FHRS Guide: anxiety is always
    'typical' (no early-onset multiplier), whatever the age or the patient's choice."""
    if domain == "NEURO" and _ANXIETY.search(diagnosis):
        return "typical"
    return None


def onset_from_age(domain: str, diagnosis: str, relative_sex: str, age: int) -> str:
    """Onset level from the age at diagnosis (FHRS Guide, "When is a disease considered early?")."""
    fixed = fixed_onset(domain, diagnosis)
    if fixed is not None:
        return fixed
    text = diagnosis.lower()
    if domain == "CVD":  # sex-specific (Harrison's: premature IHD <55 men, <65 women)
        return _by_age(age, 40, 55, 65) if relative_sex == "Male" else _by_age(age, 40, 65, 75)
    if domain == "T2D":
        return _by_age(age, 40, 50, 65)
    if domain == "CRD":
        if "asthma" in text:  # under 12 / 12-25 / 26-40 / over 40
            return _by_age(age, 12, 26, 41)
        return _by_age(age, 40, 50, 65)  # COPD and other chronic lung disease
    if domain == "NEURO":
        # Depression row of the guide: under 18 / 18-24 / 25-40 / over 40. Anxiety is handled
        # by fixed_onset above. Alzheimer's is outside NEURO in this version.
        return _by_age(age, 18, 25, 41)
    raise ValueError(domain)


@dataclass(frozen=True)
class FamilyEntry:
    relative: str
    domain: str
    onset: str           # severe_early | early | typical | late | unknown
    confirmed: str       # yes | no | not_sure
    shared_environment: bool


@dataclass(frozen=True)
class FhrsResult:
    domain: str
    affected_relatives: int
    clustering_score: float
    environmental_modifier: float
    calculated_fhrs: float
    risk_band: str


def risk_band(score: float) -> str:
    if score <= 0:
        return "Baseline"
    if score < 1.76:
        return "Mild"
    if score < 3.51:
        return "Moderate"
    if score <= 6.00:
        return "Pronounced"
    return "Critical"


def calculate(domain: str, entries: list[FamilyEntry]) -> FhrsResult:
    """One relative counts once per disease: if the same relative has two diagnoses in the
    same domain (e.g. hypertension and stroke), the higher term is used, so nobody is
    double-counted."""
    best_term: dict[str, float] = {}
    confirmed_relatives: set[str] = set()
    shared = False
    for e in (e for e in entries if e.domain == domain):
        term = RELATIONSHIP_WEIGHT[degree_of(e.relative)] * ONSET_MULTIPLIER[e.onset] * VERIFICATION[e.confirmed]
        best_term[e.relative] = max(term, best_term.get(e.relative, 0.0))
        if e.confirmed == "yes":
            confirmed_relatives.add(e.relative)
        shared = shared or e.shared_environment

    confirmed_per_degree = {k: 0 for k in CLUSTER_WEIGHT}
    for relative in confirmed_relatives:
        confirmed_per_degree[degree_of(relative)] += 1
    clustering = sum(CLUSTER_WEIGHT[k] * max(0, n - 1) for k, n in confirmed_per_degree.items())

    bio = sum(best_term.values()) + clustering
    environment = 1.0 + (SHARED_ENVIRONMENT_WEIGHT[domain] if shared and best_term else 0.0)
    adjusted = round(bio * environment, 3)
    return FhrsResult(domain=domain, affected_relatives=len(best_term),
                      clustering_score=round(clustering, 3), environmental_modifier=round(environment, 3),
                      calculated_fhrs=adjusted, risk_band=risk_band(adjusted))
