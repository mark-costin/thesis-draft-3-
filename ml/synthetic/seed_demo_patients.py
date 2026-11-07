"""Load demo patients into med_db through the running API (for the demo and the defense).

Four made-up patients, one per kind of result, each with everything the four models need:

    demo.low@cdss.test       Liza Reyes, 26, healthy                       -> expected Low
    demo.moderate@cdss.test  Ramon Cruz, 54, overweight, high blood sugar  -> expected Moderate (T2D, CVD)
    demo.high@cdss.test      Carlos Mendoza, 68, smoker, BP 160/100        -> expected High (CVD)
    demo.neuro@cdss.test     Andrea Santos, 36, low mood, poor sleep       -> mental health inputs high

The expected tiers depend on the models that are registered; with other models they can differ.

Everything goes through the API (register, log in, POST each screen's data), so the rows are
the same as a patient typing them: audit log, RLS and checks all apply, and no database password
is needed. A patient whose email is already registered is skipped (delete that account in the
app first to load it again).

    uv run python ml/synthetic/seed_demo_patients.py --password "<a demo password>"
    uv run python ml/synthetic/seed_demo_patients.py --password "<...>" --run   # also assess + save a report

--run needs RISK_MODELS_ENABLED=true and registered models on the API. The password is not
stored anywhere by this script; use one only for these demo accounts.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import date

import httpx

DEFAULT_API = "http://127.0.0.1:8000"
CONSENT_VERSION = "1.0"  # backend/config.py privacy_notice_version


@dataclass
class DemoPatient:
    email: str
    first_name: str
    last_name: str
    date_of_birth: str
    biological_sex: str
    contact: dict
    vitals: dict
    lifestyle: dict
    history: dict
    questionnaires: list[tuple[str, list[int]]]
    family: list[dict]
    labs: list[tuple[str, float]]
    expected: str
    notes: list[str] = field(default_factory=list)


UNIT = {"HDL": "mg/dL", "LDL": "mg/dL", "TOTAL_CHOLESTEROL": "mg/dL", "TRIGLYCERIDES": "mg/dL", "FPG": "mg/dL",
        "OGTT_2H": "mg/dL", "HBA1C": "%", "EGFR": "mL/min/1.73m2", "HS_CRP": "mg/L", "EOSINOPHILS": "cells/uL",
        "TSH": "mIU/L", "VITAMIN_B12": "pg/mL"}


def relative(domain, diagnosis, who, age=None, confirmed="yes", shared=False):
    entry = {"disease_domain": domain, "specific_diagnosis": diagnosis, "relative": who,
             "diagnosis_confirmed": confirmed, "shared_environment": shared}
    return entry | ({"age_at_diagnosis": age} if age is not None else {})


PATIENTS = [
    DemoPatient(
        "demo.low@cdss.test", "Liza", "Reyes", "2000-03-14", "Female",
        {"contact_number": "09171234501", "province": "Metro Manila", "city_municipality": "Quezon City",
         "barangay": "Batasan Hills"},
        {"sbp": 112, "dbp": 72, "resting_hr": 68, "weight_kg": 54.0, "height_cm": 158.0, "waist_cm": 70.0, "spo2": 99.0},
        {"smoking_status": "never", "physical_activity_min_week": 240, "sedentary_hours_day": 6.0,
         "sleep_hours_day": 8.0, "sodium_mg_day": 1800, "carbs_g_day": 220, "occupational_dust": False,
         "substance_use": "none"},
        {"gestational_diabetes": False, "acanthosis_nigricans": False, "allergic_rhinitis": False,
         "childhood_asthma": False, "tbi_history": False},
        [("PHQ9", [0, 0, 0, 0, 0, 0, 0, 0, 0]), ("GAD7", [0, 0, 0, 0, 0, 0, 0]), ("MMRC", [0])],
        [relative("CVD", "Heart attack", "paternal_grandfather", 78, confirmed="not_sure")],
        [("HDL", 62), ("LDL", 95), ("FPG", 86), ("HBA1C", 5.1)],
        "Low in all four"),
    DemoPatient(
        "demo.moderate@cdss.test", "Ramon", "Cruz", "1972-07-02", "Male",
        {"contact_number": "09171234502", "province": "Cebu", "city_municipality": "Cebu City", "barangay": "Lahug"},
        {"sbp": 142, "dbp": 90, "resting_hr": 82, "weight_kg": 86.0, "height_cm": 167.0, "waist_cm": 102.0,
         "blood_glucose_mgdl": 118.0, "glucose_context": "fasting", "spo2": 97.0},
        {"smoking_status": "former", "pack_years": 10.0, "physical_activity_min_week": 60, "sedentary_hours_day": 10.0,
         "sleep_hours_day": 6.0, "sodium_mg_day": 3400, "carbs_g_day": 360, "occupational_dust": False,
         "substance_use": "past"},
        {"acanthosis_nigricans": True, "allergic_rhinitis": False, "childhood_asthma": False, "tbi_history": False},
        [("PHQ9", [1, 1, 0, 1, 1, 0, 0, 0, 0]), ("GAD7", [1, 0, 1, 0, 0, 0, 0]), ("MMRC", [1])],
        [relative("T2D", "Type 2 diabetes", "mother", 52), relative("T2D", "Type 2 diabetes", "brother", 49),
         relative("CVD", "Heart attack", "father", 61, confirmed="not_sure")],
        [("HDL", 40), ("LDL", 150), ("TRIGLYCERIDES", 210), ("FPG", 118), ("HBA1C", 6.2), ("OGTT_2H", 170)],
        "Moderate for T2D and CVD"),
    DemoPatient(
        "demo.high@cdss.test", "Carlos", "Mendoza", "1958-06-01", "Male",
        {"contact_number": "09171234503", "province": "Davao del Sur", "city_municipality": "Davao City",
         "barangay": "Buhangin"},
        {"sbp": 160, "dbp": 100, "resting_hr": 90, "weight_kg": 95.0, "height_cm": 165.0, "waist_cm": 110.0,
         "blood_glucose_mgdl": 150.0, "glucose_context": "fasting", "spo2": 93.0},
        {"smoking_status": "current", "pack_years": 30.0, "physical_activity_min_week": 20, "sedentary_hours_day": 12.0,
         "sleep_hours_day": 5.0, "sodium_mg_day": 4500, "carbs_g_day": 400, "occupational_dust": True,
         "substance_use": "current"},
        {"acanthosis_nigricans": False, "allergic_rhinitis": True, "childhood_asthma": True, "tbi_history": False},
        [("PHQ9", [2, 2, 2, 1, 2, 1, 1, 1, 0]), ("GAD7", [2, 2, 1, 2, 1, 1, 1]), ("MMRC", [3])],
        [relative("CVD", "Heart attack", "father", 50, shared=True), relative("CVD", "Heart attack", "brother", 48),
         relative("T2D", "Type 2 diabetes", "mother", 45), relative("CRD", "Asthma", "sister", 20)],
        [("HDL", 30), ("LDL", 190), ("TOTAL_CHOLESTEROL", 270), ("FPG", 140), ("HBA1C", 7.5), ("OGTT_2H", 220),
         ("HS_CRP", 6.0), ("EOSINOPHILS", 600)],
        "High for CVD; raised for T2D and CRD"),
    DemoPatient(
        "demo.neuro@cdss.test", "Andrea", "Santos", "1990-11-20", "Female",
        {"contact_number": "09171234504", "province": "Laguna", "city_municipality": "City of Calamba",
         "barangay": "Real"},
        {"sbp": 118, "dbp": 76, "resting_hr": 88, "weight_kg": 58.0, "height_cm": 160.0, "spo2": 98.0},
        {"smoking_status": "never", "physical_activity_min_week": 30, "sedentary_hours_day": 11.0,
         "sleep_hours_day": 4.5, "sodium_mg_day": 2200, "carbs_g_day": 260, "occupational_dust": False,
         "substance_use": "past"},
        {"gestational_diabetes": False, "acanthosis_nigricans": False, "allergic_rhinitis": False,
         "childhood_asthma": False, "tbi_history": True},
        # PHQ-9 item 9 is 0 on purpose: the demo does not trigger the crisis dialog by itself
        [("PHQ9", [2, 2, 3, 2, 2, 2, 2, 1, 0]), ("GAD7", [2, 3, 2, 2, 2, 2, 2]), ("MMRC", [0])],
        [relative("NEURO", "Depression", "mother", 35), relative("NEURO", "Depression", "sister", 24)],
        [("HDL", 55), ("LDL", 110), ("TSH", 2.4), ("VITAMIN_B12", 380)],
        "Mental health inputs high (PHQ-9 16, GAD-7 15); others Low"),
]


class SeedError(Exception):
    pass


_SEND_CONTACT = True  # turned off once if the API does not know contact_number


def _check(response: httpx.Response, what: str) -> httpx.Response:
    if response.status_code >= 400:
        raise SeedError(f"{what}: {response.status_code} {response.text[:300]}")
    return response


def register(client: httpx.Client, p: DemoPatient, password: str) -> bool:
    """True when made now, False when the email was already registered."""
    body = {"email": p.email, "password": password, "first_name": p.first_name, "last_name": p.last_name,
            "date_of_birth": p.date_of_birth, "biological_sex": p.biological_sex,
            "privacy_consent": True, "consent_version": CONSENT_VERSION}
    global _SEND_CONTACT
    response = client.post("/auth/register", json=body | p.contact if _SEND_CONTACT else body)
    if _SEND_CONTACT and response.status_code == 422 and "contact_number" in response.text:
        _SEND_CONTACT = False  # an API without contact/address fields (before #39): stop sending them
        response = client.post("/auth/register", json=body)
    if response.status_code == 429:
        raise SeedError("too many sign-ups in a minute (limit 5); wait a minute and run it again")
    if response.status_code == 409:
        return False
    _check(response, f"register {p.email}")
    return True


def load(client: httpx.Client, p: DemoPatient, password: str, run: bool) -> dict:
    """Register (skip if there), log in, enter every screen's data; with run=True also assess and
    save a report. Returns what happened, for the summary."""
    if not register(client, p, password):
        return {"email": p.email, "status": "already registered, skipped"}
    token = _check(client.post("/auth/login", json={"email": p.email, "password": password}), "log in").json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    _check(client.post("/vitals", headers=h, json=p.vitals), "vitals")
    _check(client.post("/lifestyle", headers=h, json=p.lifestyle), "lifestyle")
    _check(client.put("/health-history", headers=h, json=p.history), "health history")
    for instrument, answers in p.questionnaires:
        _check(client.post("/questionnaires", headers=h, json={"instrument": instrument, "answers": answers}), instrument)
    for entry in p.family:
        _check(client.post("/family-history", headers=h, json=entry), f"family {entry['relative']}")
    today = date.today().isoformat()
    for code, value in p.labs:
        _check(client.post("/labs", headers=h, json={"test_code": code, "value": value, "unit": UNIT[code],
                                                     "collected_on": today}), f"lab {code}")
    ready = _check(client.get("/assessment/readiness", headers=h), "readiness").json()
    result = {"email": p.email, "status": "loaded", "ready": ready["ready"],
              "missing": [m.get("label", m.get("name")) for m in ready["missing_required"]], "expected": p.expected}
    if run and ready["ready"]:
        assessed = client.post("/assessment/run", headers=h)
        if assessed.status_code == 503:
            result["tiers"] = "models are off on the API (RISK_MODELS_ENABLED=false)"
        else:
            data = _check(assessed, "run assessment").json()
            result["tiers"] = {r["disease"]: r["risk_level"] for r in data["results"]}
            _check(client.post("/reports", headers=h, json={"assessment_id": data["assessment_id"]}), "save report")
    client.post("/auth/logout", headers=h)
    return result


def seed(client: httpx.Client, password: str, run: bool = False, patients=PATIENTS) -> list[dict]:
    return [load(client, p, password, run) for p in patients]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--password", required=True, help="password for the demo accounts (8 or more characters)")
    parser.add_argument("--api", default=DEFAULT_API, help=f"API address (default {DEFAULT_API})")
    parser.add_argument("--run", action="store_true", help="also run the assessment and save a report")
    args = parser.parse_args(argv)
    if len(args.password) < 8:
        parser.error("--password needs 8 or more characters")
    try:
        with httpx.Client(base_url=args.api, timeout=60) as client:
            results = seed(client, args.password, args.run)
    except httpx.ConnectError:
        print(f"Cannot reach the API at {args.api}. Start it first (uvicorn backend.main:app --port 8000).")
        return 1
    except SeedError as error:
        print(f"Stopped: {error}")
        return 1
    for r in results:
        print(f"- {r['email']}: {r['status']}")
        if r["status"] == "loaded":
            print(f"    ready for assessment: {r['ready']}" + (f" (missing: {', '.join(r['missing'])})" if r["missing"] else ""))
            print(f"    expected: {r['expected']}")
            if "tiers" in r:
                print(f"    result: {r['tiers']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
