"""Family History Risk Score (FHRS), from the CDSS Data Dictionary, FHRS Guide (Oct 31, 2026).

    BioFHRS_d      = sum over each relative of (R_i x O_i x S_i)  +  C_d
    AdjustedFHRS_d = BioFHRS_d x E_d

    R_i  relationship weight: 1st degree 0.50, 2nd degree 0.25, 3rd degree 0.125
    O_i  age-of-onset multiplier: severe early 3.0, early 2.0, typical 1.0, late 0.75
         ('unknown' is scored as typical, 21_famhistory.sql)
    S_i  verification: diagnosis confirmed by a doctor 1.0, otherwise 0.5
    C_d  clustering: +1.0, once, when 2 or more first-degree relatives have the same
         doctor-confirmed disease (see disease_of), otherwise 0
    E_d  environment: 1.0 + shared household exposure, per disease (see SHARED_ENVIRONMENT_WEIGHT)

Bands: 0 Baseline (0), Mild (below 1.76), Moderate (1.76 to below 3.51),
Pronounced (3.51 to 6.00), Critical (above 6.00).

Scores are rounded to 2 decimals, as in the generator and the bands. ml/fhrs_cases.yaml holds
the cases that this file and the generator (ml/synthetic/01_syntgen.ipynb) must both pass.

Known differences from the generator's fhrs():
  E: the generator adds 0.15 for each of the patient's own exposures: CVD secondhand smoke, high
     salt diet (E up to 1.30); T2D processed diet, low home activity (up to 1.30); CRD secondhand
     smoke, indoor mold, coal heating (up to 1.45); NEURO none. The app has no exposure fields:
     it adds 0.15 for CVD and CRD only, once, when an affected relative shared the household
     (E up to 1.15; T2D and NEURO stay 1.0). Group decision pending, separate PR.
  Two diagnoses in one domain: the generator's loop stops at the relative's first diagnosis, for
     both the term and C. The app follows the Guide: the higher term, and C sees every confirmed
     diagnosis.

Pure functions only (no database), so every rule can be tested directly.
"""

import re
from dataclasses import dataclass

DOMAINS = ("CVD", "T2D", "CRD", "NEURO")

RELATIONSHIP_WEIGHT = {1: 0.50, 2: 0.25, 3: 0.125}            # R_i
ONSET_MULTIPLIER = {"severe_early": 3.0, "early": 2.0, "typical": 1.0, "late": 0.75,
                    "unknown": 1.0}                            # O_i
VERIFICATION = {"yes": 1.0, "no": 0.5, "not_sure": 0.5}        # S_i
CLUSTER_BONUS = 1.0                                            # C_d

# E_d = 1.0 + this weight when at least one affected relative shared the patient's household
# (the table records one yes/no per relative). CDSS Data Dictionary, FHRS Guide (Oct 31, 2026):
# household secondhand smoke = +0.15 for CVD and CRD; NEURO has no environmental modifier.
SHARED_ENVIRONMENT_WEIGHT = {
    "CVD": 0.15,
    "CRD": 0.15,
    # T2D: to be set by the group. No smoke penalty yet, so E = 1.0.
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
# These roles exist only once, so relative_no stays 1 (family_single_relative_check, 24_family_relative_no.sql)
SINGLE_RELATIVES = {"father", "mother", "paternal_grandfather", "paternal_grandmother", "maternal_grandfather",
                    "maternal_grandmother", "great_grandfather", "great_grandmother"}
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


def disease_of(domain: str, diagnosis: str) -> str:
    """The disease for the clustering rule, as the generator labels it: CVD, T2D, asthma, COPD,
    depression or GAD. Every CVD diagnosis is one disease, so a heart attack and a stent match."""
    if domain == "CRD":
        return "asthma" if "asthma" in diagnosis.lower() else "COPD"
    if domain == "NEURO":
        return "GAD" if _ANXIETY.search(diagnosis) else "depression"
    return domain


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
    diagnosis: str       # specific_diagnosis, read by disease_of
    relative_no: int = 1  # with relative, identifies the person: brother 1, brother 2


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
    """Each person counts once per disease: if the same (relative, relative_no) has two diagnoses
    in the same domain (e.g. hypertension and stroke), the higher term is used, so nobody is
    double-counted."""
    best_term: dict[tuple[str, int], float] = {}
    confirmed_first_degree: dict[str, set[tuple[str, int]]] = {}
    shared = False
    for e in (e for e in entries if e.domain == domain):
        person = (e.relative, e.relative_no)
        term = RELATIONSHIP_WEIGHT[degree_of(e.relative)] * ONSET_MULTIPLIER[e.onset] * VERIFICATION[e.confirmed]
        best_term[person] = max(term, best_term.get(person, 0.0))
        if e.confirmed == "yes" and e.relative in FIRST_DEGREE:
            confirmed_first_degree.setdefault(disease_of(domain, e.diagnosis), set()).add(person)
        shared = shared or e.shared_environment

    clustering = CLUSTER_BONUS if any(len(people) >= 2 for people in confirmed_first_degree.values()) else 0.0
    bio = sum(best_term.values()) + clustering
    environment = 1.0 + (SHARED_ENVIRONMENT_WEIGHT[domain] if shared and best_term else 0.0)
    adjusted = round(bio * environment, 2)
    return FhrsResult(domain=domain, affected_relatives=len(best_term),
                      clustering_score=clustering, environmental_modifier=round(environment, 3),
                      calculated_fhrs=adjusted, risk_band=risk_band(adjusted))
