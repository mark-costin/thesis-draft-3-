"""App state. Screens 1 to 4 (account, dashboard, vitals and lifestyle, family history) call the
CDSS API through api.py. Scan & assess, Results and Reports (Sprint 5) still show sample data;
their TODO comments mark where the API plugs in.

The login token lives in `_token`: a backend-only variable (leading underscore), so Reflex never
sends it to the browser. Passwords pass through an event handler once and are never stored.
"""
import asyncio
import base64
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

import reflex as rx

from . import api, careplan
from . import questionnaires as qn
from .labinfo import DOC_STATUS, LAB_LABEL, LAB_UNIT
from .api import GOOGLE_CLIENT_ID
from .locations import LISTS_COMPLETE, PROVINCES, barangays_for, cities_for

# Event payload types. Keep them generic (dict[str, Any]), never a bare `dict`: for a bare `dict`
# Reflex 0.10 tries to convert the payload, fails, and writes the WHOLE form (password included)
# into its console warning. With dict[str, Any] the payload is passed through untouched.
Form = dict[str, Any]
GoogleReply = dict[str, Any]

PH_MOBILE = re.compile(r"^(09|\+639)\d{9}$")  # 09171234567 or +639171234567
MIN_PASSWORD = 12  # the screens ask for 12; the API's own minimum is 8
PRIVACY_NOTICE_VERSION = "1.0"  # must equal PRIVACY_NOTICE_VERSION in the API settings
MODEL_VERSION = "sample-0.1"  # TODO (Sprint 5): the backend returns the real model version with each prediction


@dataclass
class Risk:
    code: str
    name: str
    short: str
    tier: str
    color: str
    score: int
    prob: str
    note: str


@dataclass
class VitalsRow:
    date: str
    bp: str
    hr: str
    weight: str
    bmi: str
    glucose: str
    spo2: str


@dataclass
class Check:
    label: str
    done: bool
    tag: str
    where: str = ""   # screen where the patient enters it (from the readiness check)
    href: str = ""


@dataclass
class FamilyEntry:
    record_id: int
    relative: str        # shown label, e.g. "Maternal aunt"
    disease: str         # CVD / T2D / CRD / NEURO
    diagnosis: str
    age: str             # age at diagnosis, "" if unknown
    onset: str           # shown label, e.g. "Early"
    confirmed: str       # Yes / No / Not sure
    household: bool


@dataclass
class QRow:
    instrument: str
    date: str
    score: int
    band: str
    crisis: bool


@dataclass
class Lab:
    """A lab value under review (from GET /documents/{id}/review)."""
    lab_id: int
    code: str
    label: str
    value: str
    unit: str
    reported: str     # what the report said, when the API converted the unit ("5.6 mmol/L")
    source: str       # Scanned / Typed by me
    flagged: bool     # outside the usual range: likely a misread or a wrong unit


@dataclass
class MissingLab:
    """A lab value the models still need: typed in on Scan & assess."""
    code: str
    label: str
    unit: str


@dataclass
class Chip:
    label: str
    color: str


@dataclass
class Report:
    report_id: int
    date: str
    summary: list[Chip]
    plans: str = ""


@dataclass
class Goal:
    goal_id: int
    text: str
    done: bool
    tag: str


@dataclass
class Q:
    key: str         # field name in the API (10_health_history.sql)
    question: str
    answer: str      # Yes / No / Not sure


TIER_COLOR = {"Low": "green", "Moderate": "amber", "High": "red"}  # status colours only: green / amber / red
TIER_NOTE = {"Low": "Low Risk - Routine yearly prevention", "Moderate": "Moderate Risk - Follow-up in 3 to 6 months", "High": "High Risk - See a doctor within 30 to 60 days"}
DISEASES = [("CVD", "Cardiovascular (CVD)", "Cardiovascular"), ("T2D", "Type 2 Diabetes (T2D)", "Type 2 Diabetes"),
            ("CRD", "Chronic Respiratory (CRD)", "Chronic Respiratory"), ("NEURO", "Mental Health & Neuro", "Mental Health & Neuro")]
DISEASE_LABEL = {"CVD": "Cardiovascular", "T2D": "Type 2 diabetes", "CRD": "Chronic respiratory", "NEURO": "Mental health & neuro"}

# Family history: the API's relative codes (backend/fhrs.py) and the words shown on screen
RELATIVE_GROUPS = [
    ("1st degree", [("father", "Father"), ("mother", "Mother"), ("brother", "Brother"), ("sister", "Sister"), ("son", "Son"), ("daughter", "Daughter")]),
    ("2nd degree", [("paternal_grandfather", "Grandfather (father's side)"), ("paternal_grandmother", "Grandmother (father's side)"),
                    ("maternal_grandfather", "Grandfather (mother's side)"), ("maternal_grandmother", "Grandmother (mother's side)"),
                    ("paternal_uncle", "Uncle (father's side)"), ("paternal_aunt", "Aunt (father's side)"),
                    ("maternal_uncle", "Uncle (mother's side)"), ("maternal_aunt", "Aunt (mother's side)"),
                    ("half_brother", "Half-brother"), ("half_sister", "Half-sister"), ("grandson", "Grandson"), ("granddaughter", "Granddaughter"),
                    ("nephew", "Nephew"), ("niece", "Niece")]),
    ("3rd degree", [("paternal_male_cousin", "Male cousin (father's side)"), ("paternal_female_cousin", "Female cousin (father's side)"),
                    ("maternal_male_cousin", "Male cousin (mother's side)"), ("maternal_female_cousin", "Female cousin (mother's side)"),
                    ("great_grandfather", "Great-grandfather"), ("great_grandmother", "Great-grandmother")]),
]
RELATIVE_LABEL = {code: label for _, items in RELATIVE_GROUPS for code, label in items}
RELATIVE_CODE = {label: code for code, label in RELATIVE_LABEL.items()}
ONSET_LABEL = {"severe_early": "Very early", "early": "Early", "typical": "Typical", "late": "Late", "unknown": "Unknown"}
CONFIRMED = {"Yes": "yes", "No": "no", "Not sure": "not_sure"}
CONFIRMED_LABEL = {v: k for k, v in CONFIRMED.items()}
BAND_NUMBER = {"Baseline": 0, "Mild": 1, "Moderate": 2, "Pronounced": 3, "Critical": 4}  # 21_famhistory.sql

# Lifestyle: words on screen <-> API values (09_lifestyle_records.sql)
SMOKING = {"Never smoked": "never", "Former smoker": "former", "Current smoker": "current"}
SUBSTANCE = {"None": "none", "Past": "past", "Current": "current"}
YES_NO = {"Yes": True, "No": False}
HISTORY_QUESTIONS = [("childhood_asthma", "Childhood asthma"), ("gestational_diabetes", "Gestational diabetes (diabetes during a pregnancy)"),
                     ("allergic_rhinitis", "Allergic rhinitis"), ("acanthosis_nigricans", "Acanthosis nigricans (dark, velvety skin patches)"),
                     ("tbi_history", "Past head injury that needed medical care")]


def make_risk(code: str, prob: float, tier: str | None = None) -> Risk:
    """The API sends the tier with each result; the sample data derives it from the probability."""
    _, name, short = next(d for d in DISEASES if d[0] == code)
    tier = tier or careplan.tier_for(prob)
    return Risk(code, name, short, tier, TIER_COLOR[tier], round(prob * 100), f"{prob:.2f}", TIER_NOTE[tier])




def when_label(iso: str) -> str:
    """'2026-10-20T07:42:00+08:00' -> 'Oct 20, 7:42 AM' (in the time zone the API sent)."""
    try:
        d = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return iso or ""
    return f"{d:%b} {d.day}, {d.hour % 12 or 12}:{d:%M} {'AM' if d.hour < 12 else 'PM'}"


def _num(value: Any) -> str:
    """API number -> text for the screen ('' when missing, no trailing .0)."""
    if value is None:
        return ""
    return str(int(value)) if float(value).is_integer() else str(value)


def _clean(form: dict, key: str) -> str:
    return str(form.get(key) or "").strip()


def _number(text: str, kind=float):
    """Text from a form box -> number for the API, or None when blank."""
    text = (text or "").strip()
    if not text:
        return None
    return kind(float(text)) if kind is int else float(text)


class State(rx.State):
    # ---- account (from GET /profile) ----
    _token: str = ""                 # backend only: never sent to the browser
    _google_credential: str = ""     # backend only: a Google ID token waiting for sign-up details
    _body_last: dict[str, str] = {}  # backend only: weight, height, waist as last saved (not re-sent unchanged)
    is_logged_in: bool = False
    submitting: bool = False
    consent: bool = False
    first_name: str = ""
    last_name: str = ""
    auth_provider: str = "password"  # "password" or "google" (from the profile's sign_in)
    email: str = ""
    picture: str = ""
    dob: str = ""
    sex: str = ""
    contact: str = ""
    street: str = ""
    province: str = ""
    city: str = ""
    barangay: str = ""
    google_pending: bool = False     # Google said who you are; birth date, sex and consent still needed

    # ---- dashboard ----
    probs: dict[str, float] = {}     # latest assessment; empty until the first one
    tiers: dict[str, str] = {}
    checklist: list[Check] = []
    ready: bool = False
    bp_trend: list[dict[str, int | str]] = []
    bmi_trend: list[dict[str, float | str]] = []

    # ---- vitals, lifestyle, history ----
    vitals_history: list[VitalsRow] = []
    weight: str = ""
    height: str = ""
    waist: str = ""
    smoking: str = ""
    pack_years: str = ""
    activity: str = ""        # minutes of moderate activity per week
    sleep: str = ""
    carbs: str = ""
    dust: str = ""            # Yes / No
    alcohol: str = ""         # alcohol or other substance use: None / Past / Current
    sedentary: str = ""
    sodium: str = ""
    psqi: str = ""            # TODO (Sprint 5): from the PSQI questionnaire (Screen 5)
    tsh: str = ""
    b12: str = ""
    history: list[Q] = [Q(key, text, "Not sure") for key, text in HISTORY_QUESTIONS]

    # ---- family history ----
    family: list[FamilyEntry] = []
    bands: dict[str, str] = {}       # disease -> Baseline | Mild | Moderate | Pronounced | Critical
    fam_relative: str = ""
    fam_disease: str = ""

    # ---- questionnaires (Screen 5) ----
    q_instrument: str = "PHQ9"
    q_answers: list[int] = [-1] * 9            # -1 = not answered yet
    q_history: list[QRow] = []
    q_saving: bool = False
    show_crisis: bool = False
    crisis_message: str = ""
    crisis_hotlines: list[dict[str, str]] = []

    # ---- scan and verify ----
    doc_id: int = 0
    doc_name: str = ""
    doc_status: str = ""           # API status: pending, processing, ready_for_review, verified, failed, rejected
    doc_error: str = ""
    doc_preview: str = ""          # data: URL for a photo (decrypted by the API, shown only to this patient)
    doc_is_pdf: bool = False
    labs: list[Lab] = []           # values under review
    missing_labs: list[MissingLab] = []
    other_missing: list[Check] = []  # non-lab inputs still needed, with links
    uploading: bool = False
    running: bool = False
    assessment_id: int = 0

    # ---- results, reports, goals (Sprint 5: sample data) ----
    selected: str = "CVD"
    api_results: list[dict[str, Any]] = []   # DiseaseResult rows of the assessment on screen (GET /assessment/{id})
    result_disclaimer: str = ""
    reports: list[Report] = []
    goals: list[Goal] = []

    # ================= computed values =================
    @rx.var
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @rx.var
    def initials(self) -> str:
        return (self.first_name[:1] + self.last_name[:1]).upper() or "P"

    @rx.var
    def account_line(self) -> str:
        return self.email

    @rx.var
    def lists_incomplete(self) -> bool:
        return not LISTS_COMPLETE

    @rx.var
    def provinces(self) -> list[str]:
        return PROVINCES

    @rx.var
    def city_options(self) -> list[str]:
        return cities_for(self.province)

    @rx.var
    def barangay_options(self) -> list[str]:
        return barangays_for(self.province, self.city)

    @rx.var
    def bmi(self) -> str:
        try:
            return f"{float(self.weight) / (float(self.height) / 100) ** 2:.1f}"
        except (ValueError, ZeroDivisionError):
            return "--"

    @rx.var
    def has_results(self) -> bool:
        return bool(self.probs)

    @rx.var
    def risks(self) -> list[Risk]:
        return [make_risk(code, self.probs[code], self.tiers.get(code)) for code, _, _ in DISEASES if code in self.probs]

    @rx.var
    def current(self) -> Risk:
        found = [r for r in self.risks if r.code == self.selected]
        return found[0] if found else make_risk(self.selected, 0.0)

    def _result(self) -> dict[str, Any]:
        return next((r for r in self.api_results if r["disease"] == self.selected), {})

    @rx.var
    def reasons(self) -> list[dict[str, Any]]:
        """Top factors (SHAP) of the selected disease, from the API, with plain-language sentences."""
        return [{"name": f["feature"].replace("_", " ").capitalize(), "sentence": f["sentence"],
                 "raises": max(f["shap_value"], 0.0), "lowers": min(f["shap_value"], 0.0)}
                for f in self._result().get("top_factors", [])]

    @rx.var
    def consult_message(self) -> str:
        return self._result().get("consult_message") or ""

    @rx.var
    def has_plan(self) -> bool:
        return bool(self._result().get("care_plan"))

    @rx.var
    def has_vitals(self) -> bool:
        return len(self.vitals_history) > 0

    @rx.var
    def latest_vitals(self) -> VitalsRow:
        return self.vitals_history[0] if self.vitals_history else VitalsRow("", "--", "--", "--", "--", "--", "--")

    @rx.var
    def fam_bands(self) -> dict[str, int]:
        """FHRS band per disease as a number (0 Baseline to 4 Critical), computed by the API."""
        return {code: BAND_NUMBER.get(self.bands.get(code, "Baseline"), 0) for code, _, _ in DISEASES}

    @rx.var
    def band_chips(self) -> list[dict[str, str]]:
        colors = {"Baseline": "gray", "Mild": "green", "Moderate": "amber", "Pronounced": "orange", "Critical": "red"}
        return [{"code": code, "band": self.bands.get(code, "Not scored yet"), "color": colors.get(self.bands.get(code, ""), "gray")}
                for code, _, _ in DISEASES]

    @rx.var
    def relative_options(self) -> list[str]:
        return [label for _, items in RELATIVE_GROUPS for _, label in items]

    @rx.var
    def disease_options(self) -> list[str]:
        return list(DISEASE_LABEL.values())

    @rx.var
    def plan(self) -> dict[str, Any]:
        """The care plan the API picked for this result: the template for the disease and tier,
        plus the add-ons whose trigger is true in the patient's saved data (backend/care_plan.py)."""
        cp = self._result().get("care_plan") or {}
        in_goals = {g.text for g in self.goals}
        rows = []
        for item, addon in [(i, False) for i in cp.get("base", [])] + [(a, True) for a in cp.get("addons", [])]:
            category = item.get("category") or ("Based on your answers" if addon else "")
            text = item.get("action", "")
            rows.append({"text": text, "why": item.get("why", ""), "source": item.get("source", ""), "basis": item.get("basis", ""),
                         "basis_color": "amber" if "Plan default" in item.get("basis", "") else "gray",
                         "category": category, "label": category or "Care plan", "addon": addon,
                         "goal": bool(item.get("goal")), "in_goals": text[:255] in in_goals})
        tier = cp.get("risk_level", "")
        return {"id": f"{cp.get('disease_domain', '')}-{tier.upper()}" if cp else "", "version": cp.get("version", ""),
                "reviewed_by": cp.get("reviewed_by") or "", "reviewed_on": str(cp.get("reviewed_on") or ""), "is_draft": bool(cp.get("is_draft")),
                "actions": [r for r in rows if r["category"] not in careplan.ALERT_CATEGORIES],
                "alerts": [r for r in rows if r["category"] in careplan.ALERT_CATEGORIES]}

    @rx.var
    def plan_title(self) -> str:
        return f"{self.current.short} care plan"

    @rx.var
    def plan_actions(self) -> list[dict[str, Any]]:
        return self.plan["actions"]

    @rx.var
    def plan_alerts(self) -> list[dict[str, Any]]:
        return self.plan["alerts"]

    @rx.var
    def plan_footer(self) -> str:
        p = self.plan
        who = p["reviewed_by"] or "[physician name]"
        return f"Template {p['id']} v{p['version']} | Reviewed by: {who} | Review date: {p['reviewed_on'] or '[date]'}"

    @rx.var
    def plan_pending(self) -> bool:
        return self.plan["is_draft"] or not self.plan["reviewed_by"]

    @rx.var
    def disclaimer(self) -> str:
        return self.result_disclaimer or careplan.DISCLAIMER

    @rx.var
    def is_high(self) -> bool:
        return self.current.tier == "High"

    @rx.var
    def doc_status_label(self) -> str:
        return DOC_STATUS.get(self.doc_status, "")

    @rx.var
    def in_review(self) -> bool:
        return self.doc_status == "ready_for_review"

    @rx.var
    def missing_for_run(self) -> list[str]:
        """What still blocks the assessment, from the API's readiness check."""
        return [c.label.lower() for c in self.checklist if not c.done]

    @rx.var
    def run_hint(self) -> str:
        return ("Still needed: " + ", ".join(self.missing_for_run) + ".") if self.missing_for_run else "Everything is filled in. You can run your assessment."

    @rx.var
    def can_run(self) -> bool:
        return self.ready

    @rx.var
    def q_title(self) -> str:
        return qn.INSTRUMENTS[self.q_instrument]["title"]

    @rx.var
    def q_intro(self) -> str:
        return qn.INSTRUMENTS[self.q_instrument]["intro"]

    @rx.var
    def q_options(self) -> list[str]:
        return qn.INSTRUMENTS[self.q_instrument]["options"]

    @rx.var
    def q_numeric(self) -> bool:
        """Number scales (PSQI, CAT, ACT) fit in one row; word answers are listed."""
        return self.q_instrument in ("PSQI", "CAT", "ACT")

    @rx.var
    def q_items(self) -> list[dict[str, Any]]:
        spec = qn.INSTRUMENTS[self.q_instrument]
        options, low = spec["options"], spec["low"]
        return [{"i": i, "number": i + 1, "text": text,
                 "answer": options[self.q_answers[i] - low] if i < len(self.q_answers) and self.q_answers[i] >= low else ""}
                for i, text in enumerate(spec["items"])]

    @rx.var
    def q_unanswered(self) -> int:
        return sum(1 for a in self.q_answers if a < qn.INSTRUMENTS[self.q_instrument]["low"])

    @rx.var
    def q_tabs(self) -> list[dict[str, str]]:
        """One tab per questionnaire; 'needed' when the readiness check still lists it."""
        needed = {c.href.rsplit("=", 1)[-1] for c in self.checklist if "/questionnaires" in c.href}
        return [{"code": code, "title": qn.INSTRUMENTS[code]["title"], "needed": "yes" if code in needed else ""} for code in qn.ORDER]

    # ================= helpers =================
    async def _api(self, method: str, path: str, **kwargs) -> api.Reply:
        """API call with this patient's token. A 401 means the session ended: sign out."""
        reply = await api.call(method, path, self._token or None, **kwargs)
        if reply.status == 401 and self._token:
            self._sign_out()
        return reply

    def _sign_out(self):
        self.reset()  # clears every value, the token included, so the next person sees nothing

    def _signed_in(self, token: str):
        self._token, self.is_logged_in = token, True

    async def _load_profile(self) -> bool:
        reply = await self._api("GET", "/profile")
        if not reply.ok:
            return False
        p = reply.data
        self.email, self.first_name, self.last_name = p["email"], p["first_name"], p["last_name"]
        self.dob, self.sex = p["date_of_birth"], p["biological_sex"]
        self.contact, self.street = p.get("contact_number") or "", p.get("street") or ""
        self.province, self.city, self.barangay = p.get("province") or "", p.get("city_municipality") or "", p.get("barangay") or ""
        self.auth_provider = "google" if p.get("sign_in") == "google" else "password"
        return True

    def _contact_and_address(self, form: Form) -> dict:
        contact = _clean(form, "contact").replace(" ", "").replace("-", "")
        body = {"contact_number": contact or None, "street": _clean(form, "street") or None}
        if self.province and self.city and self.barangay:
            body |= {"province": self.province, "city_municipality": self.city, "barangay": self.barangay}
        return body

    def _check_names(self, *names: str):
        """Returns an error message, or None when every name is letters only (spaces, dots, hyphens and apostrophes allowed)."""
        for n in names:
            if not n.strip() or any(not (ch.isalpha() or ch in " .'’-") for ch in n):
                return "Names can only have letters. Please remove any numbers or symbols."
        return None

    def _check_contact(self, contact: str):
        """Returns an error message, or None when the number looks like a Philippine mobile number."""
        if not PH_MOBILE.match(contact.replace(" ", "").replace("-", "")):
            return "Enter a mobile number with digits only, like 09171234567 or +639171234567."
        return None

    # ================= sign in / sign out =================
    @rx.event
    def check_login(self):
        if not self.is_logged_in:
            return rx.redirect("/")

    @rx.event
    def redirect_if_logged_in(self):
        """Someone who is already signed in does not need the sign-in screen."""
        if self.is_logged_in:
            return rx.redirect("/dashboard")

    @rx.event
    def set_consent(self, value: bool):
        self.consent = value

    @rx.event
    def set_contact(self, value: str):
        self.contact = value

    @rx.event
    def set_street(self, value: str):
        self.street = value

    @rx.event
    def set_province(self, value: str):
        self.province, self.city, self.barangay = value, "", ""  # a new province clears the city and barangay

    @rx.event
    def set_city(self, value: str):
        self.city, self.barangay = value, ""

    @rx.event
    def set_barangay(self, value: str):
        self.barangay = value

    @rx.event
    def set_dob(self, value: str):
        self.dob = value

    @rx.event
    def set_sex(self, value: str):
        self.sex = value

    @rx.event
    async def register(self, form: Form):
        password, confirm = form.get("password", ""), form.get("confirm_password", "")
        if password != confirm:
            yield rx.toast.error("The two passwords do not match.")
            return
        if len(password) < MIN_PASSWORD:
            yield rx.toast.error(f"Your password needs at least {MIN_PASSWORD} characters.")
            return
        problem = self._check_names(_clean(form, "first_name"), _clean(form, "last_name")) or self._check_contact(_clean(form, "contact"))
        if problem:
            yield rx.toast.error(problem)
            return
        if not (self.province and self.city and self.barangay):
            yield rx.toast.error("Please complete your home address: province, city and barangay.")
            return
        if not self.consent:
            yield rx.toast.error("Please accept the Privacy Notice first.")
            return
        self.submitting = True
        yield
        email = _clean(form, "email").lower()
        body = {"email": email, "password": password, "first_name": _clean(form, "first_name"), "last_name": _clean(form, "last_name"),
                "date_of_birth": self.dob, "biological_sex": self.sex, "privacy_consent": True,
                "consent_version": PRIVACY_NOTICE_VERSION} | self._contact_and_address(form)
        reply = await api.call("POST", "/auth/register", json=body, idempotent=True)
        if not reply.ok:
            self.submitting = False
            yield rx.toast.error(reply.message("Your account could not be created."))
            return
        login = await api.call("POST", "/auth/login", json={"email": email, "password": password})
        self.submitting = False
        if not login.ok:
            yield rx.toast.success("Your account is ready. Please log in.")
            yield rx.redirect("/")
            return
        self._signed_in(login.data["access_token"])
        await self._load_profile()
        yield rx.toast.success("Your account is ready.")
        yield rx.redirect("/dashboard")

    @rx.event
    async def login(self, form: Form):
        self.submitting = True
        yield
        reply = await api.call("POST", "/auth/login", json={"email": _clean(form, "email").lower(), "password": form.get("password", "")})
        self.submitting = False
        if not reply.ok:
            yield rx.toast.error(reply.message("Incorrect email or password."))
            return
        self._signed_in(reply.data["access_token"])
        await self._load_profile()
        yield rx.redirect("/dashboard")

    @rx.event
    async def google_signed_in(self, response: GoogleReply):
        """Google button (ID-token flow). The API checks the token with Google; nobody is signed in here."""
        credential = (response or {}).get("credential", "")
        signup = None
        if self.google_pending or self.consent:  # on Create account: send the details Google does not have
            if not (self.consent and self.dob and self.sex):
                return rx.toast.error("Fill in your date of birth and biological sex, and accept the Privacy Notice, then continue with Google.")
            signup = {"first_name": self.first_name or "Patient", "last_name": self.last_name or "Patient",
                      "date_of_birth": self.dob, "biological_sex": self.sex, "privacy_consent": True,
                      "consent_version": PRIVACY_NOTICE_VERSION} | self._contact_and_address({"contact": self.contact, "street": self.street})
        reply = await api.call("POST", "/auth/google", json={"credential": credential} | ({"signup": signup} if signup else {}))
        if reply.status == 404 and isinstance(reply.data, dict) and reply.data.get("code") == "signup_required":
            # New here: keep Google's names, ask for the rest on Create account
            self.first_name, self.last_name = reply.data.get("first_name") or "", reply.data.get("last_name") or ""
            self._google_credential, self.google_pending = credential, True
            return [rx.redirect("/register"),
                    rx.toast.info("Almost done: add your date of birth and biological sex, accept the Privacy Notice, then continue with Google again.")]
        if not reply.ok:
            return rx.toast.error(reply.message("Google sign-in did not work. Please try again."))
        self.google_pending, self._google_credential = False, ""
        self._signed_in(reply.data["access_token"])
        await self._load_profile()
        return rx.redirect("/dashboard")

    @rx.event
    async def logout(self):
        await self._api("POST", "/auth/logout")  # revokes the token on the server too
        self._sign_out()
        return [rx.redirect("/"), rx.toast.info("You have been logged out.")]

    @rx.event
    async def send_reset_email(self, form: Form | None = None):
        """Same message whether or not the address has an account, so the form cannot be used to find out who is registered."""
        address = ((form or {}).get("email") or self.email or "").strip().lower()
        if not address:
            return rx.toast.error("Enter the email address of your account.")
        reply = await api.call("POST", "/auth/password-reset/request", json={"email": address})
        if reply.status in (0, 429):
            return rx.toast.error(reply.message())
        return rx.toast.success("If that address has an account, a reset link is on its way. It works for one hour.")

    @rx.event
    async def confirm_reset(self, form: Form):
        token = self.router.url.query_parameters.get("token", "")  # from the emailed link
        new, confirm = form.get("password", ""), form.get("confirm_password", "")  # names from new_password_fields()
        if len(new) < MIN_PASSWORD:
            return rx.toast.error(f"Your new password needs at least {MIN_PASSWORD} characters.")
        if new != confirm:
            return rx.toast.error("The new password and its confirmation do not match.")
        reply = await api.call("POST", "/auth/password-reset/confirm", json={"token": token, "new_password": new})
        if not reply.ok:
            return rx.toast.error(reply.message("This reset link is invalid, already used, or expired. Request a new one."))
        return [rx.toast.success("Your password was changed. Please log in."), rx.redirect("/")]

    # ================= account settings =================
    @rx.event
    async def save_profile(self, form: Form):
        problem = self._check_names(_clean(form, "first_name"), _clean(form, "last_name"))
        if not problem and _clean(form, "contact"):
            problem = self._check_contact(_clean(form, "contact"))
        if problem:
            return rx.toast.error(problem)
        if self.province and not (self.city and self.barangay):
            return rx.toast.error("Please complete your home address: province, city and barangay.")
        body = {"first_name": _clean(form, "first_name"), "last_name": _clean(form, "last_name"),
                "date_of_birth": _clean(form, "dob") or self.dob, "biological_sex": form.get("sex") or self.sex} | self._contact_and_address(form)
        reply = await self._api("PATCH", "/profile", json=body)
        if not reply.ok:
            return rx.toast.error(reply.message("Your details could not be saved."))
        await self._load_profile()
        return rx.toast.success("Your personal details were saved.")

    @rx.event
    async def change_password(self, form: Form):
        current, new, confirm = form.get("current_password", ""), form.get("new_password", ""), form.get("confirm_password", "")
        if not current:
            return rx.toast.error("Enter your current password.")
        if len(new) < MIN_PASSWORD:
            return rx.toast.error(f"Your new password needs at least {MIN_PASSWORD} characters.")
        if new != confirm:
            return rx.toast.error("The new password and its confirmation do not match.")
        reply = await self._api("POST", "/auth/change-password", json={"current_password": current, "new_password": new})
        if not reply.ok:
            return rx.toast.error(reply.message("Your password could not be changed."))
        self._token = reply.data["access_token"]  # other devices are logged out; this one keeps working
        return [rx.toast.success("Your password was changed. Other devices were logged out."),
                rx.set_value("current_password", ""), rx.set_value("new_password", ""), rx.set_value("confirm_password", "")]

    @rx.event
    async def export_data(self):
        reply = await self._api("GET", "/account/export")
        if not reply.ok:
            return rx.toast.error(reply.message("Your data could not be exported."))
        return rx.download(data=json.dumps(reply.data, ensure_ascii=False, indent=2), filename=f"my-health-data-{datetime.now():%Y-%m-%d}.json")

    @rx.event
    async def delete_account(self, form: Form):
        """Needs the password (or a fresh Google sign-in for a Google account) and the exact phrase."""
        body = {"confirm": form.get("confirm", "")}
        if self.auth_provider == "google":
            if not self._google_credential:
                return rx.toast.error("Sign in with Google again on this page to confirm it is you.")
            body["google_credential"] = self._google_credential
        else:
            body["password"] = form.get("password", "")
        reply = await self._api("POST", "/account/delete", json=body)
        self._google_credential = ""
        if not reply.ok:
            return rx.toast.error(reply.message("Nothing was deleted."))
        self._sign_out()
        return [rx.toast.success("Your account and records were deleted."), rx.redirect("/")]

    @rx.event
    def google_confirm_identity(self, response: GoogleReply):
        """Google button on the delete dialog: keeps the fresh ID token for the delete request."""
        self._google_credential = (response or {}).get("credential", "")
        return rx.toast.info("Google sign-in confirmed. You can delete your account now.")

    # ================= dashboard =================
    @rx.event
    async def load_dashboard(self):
        if not self.is_logged_in:
            return rx.redirect("/")
        await self._load_vitals()
        await self._load_readiness()
        await self._load_latest_assessment()

    async def _load_readiness(self):
        reply = await self._api("GET", "/assessment/readiness")
        if not reply.ok:
            return
        self.ready = bool(reply.data.get("ready"))
        self.checklist = [Check(item["label"], False, "Missing", item.get("enter_in", ""), _screen_for(item.get("enter_in", ""), item.get("instrument")))
                          for item in reply.data.get("missing_required", [])]

    async def _load_latest_assessment(self):
        listed = await self._api("GET", "/assessment", params={"limit": 1})
        if not listed.ok or not listed.data:
            self.probs, self.tiers = {}, {}
            return
        latest = await self._api("GET", f"/assessment/{listed.data[0]['assessment_id']}")
        if latest.ok:
            self.probs = {r["disease"]: float(r["probability"]) for r in latest.data["results"]}
            self.tiers = {r["disease"]: r["risk_level"] for r in latest.data["results"]}

    # ================= vitals, lifestyle, history =================
    @rx.event
    async def load_vitals_page(self):
        if not self.is_logged_in:
            return rx.redirect("/")
        await self._load_vitals()
        await self._load_lifestyle()
        await self._load_history()

    async def _load_vitals(self):
        reply = await self._api("GET", "/vitals", params={"limit": 50})
        if not reply.ok:
            return
        rows = reply.data
        self.vitals_history = [VitalsRow(when_label(r["measured_at"]), f"{r['sbp']}/{r['dbp']}" if r.get("sbp") else "--", _num(r.get("resting_hr")) or "--",
                                         _num(r.get("weight_kg")) or "--", _num(r.get("bmi")) or "--", _num(r.get("blood_glucose_mgdl")) or "--",
                                         _num(r.get("spo2")) or "--") for r in rows]
        # Charts: oldest to newest, only entries that have the value
        self.bp_trend = [{"day": when_label(r["measured_at"]).split(",")[0], "systolic": r["sbp"], "diastolic": r["dbp"]}
                         for r in reversed(rows) if r.get("sbp")][-10:]
        self.bmi_trend = [{"day": when_label(r["measured_at"]).split(",")[0], "bmi": r["bmi"]} for r in reversed(rows) if r.get("bmi")][-10:]
        latest = lambda key: next((r[key] for r in rows if r.get(key) is not None), None)  # newest value of each column
        self.weight, self.height, self.waist = _num(latest("weight_kg")), _num(latest("height_cm")), _num(latest("waist_cm"))
        self._body_last = {"weight_kg": self.weight, "height_cm": self.height, "waist_cm": self.waist}

    async def _load_lifestyle(self):
        reply = await self._api("GET", "/lifestyle", params={"limit": 20})
        if not reply.ok:
            return
        rows = reply.data
        latest = lambda key: next((r[key] for r in rows if r.get(key) is not None), None)
        smoking = latest("smoking_status")
        self.smoking = next((k for k, v in SMOKING.items() if v == smoking), "")
        self.pack_years, self.activity = _num(latest("pack_years")), _num(latest("physical_activity_min_week"))
        self.sleep, self.sedentary = _num(latest("sleep_hours_day")), _num(latest("sedentary_hours_day"))
        self.sodium, self.carbs = _num(latest("sodium_mg_day")), _num(latest("carbs_g_day"))
        dust = latest("occupational_dust")
        self.dust = "" if dust is None else ("Yes" if dust else "No")
        substance = latest("substance_use")
        self.alcohol = next((k for k, v in SUBSTANCE.items() if v == substance), "")

    async def _load_history(self):
        reply = await self._api("GET", "/health-history")
        if not reply.ok:
            return
        answer = lambda v: "Not sure" if v is None else ("Yes" if v else "No")
        # Gestational diabetes is asked of female patients only (the API refuses it for males)
        self.history = [Q(key, text, answer(reply.data.get(key))) for key, text in HISTORY_QUESTIONS
                        if key != "gestational_diabetes" or self.sex == "Female"]

    @rx.event
    def set_weight(self, value: str):
        self.weight = value

    @rx.event
    def set_height(self, value: str):
        self.height = value

    @rx.event
    def set_waist(self, value: str):
        self.waist = value

    @rx.event
    def set_smoking(self, value: str):
        self.smoking = value

    @rx.event
    async def save_vitals(self, form: Form):
        try:
            body = {"sbp": _number(form.get("systolic"), int), "dbp": _number(form.get("diastolic"), int),
                    "resting_hr": _number(form.get("hr"), int), "spo2": _number(form.get("spo2")),
                    "weight_kg": _number(self.weight), "height_cm": _number(self.height), "waist_cm": _number(form.get("waist")),
                    "blood_glucose_mgdl": _number(form.get("glucose"))}
        except ValueError:
            return rx.toast.error("Please enter numbers only.")
        if body["blood_glucose_mgdl"] is not None:
            body["glucose_context"] = {"Fasting": "fasting", "Random": "random", "2 hours after a meal": "post_meal_2h"}.get(form.get("glucose_context", ""))
            if not body["glucose_context"]:
                return rx.toast.error("Tell us when the blood sugar was taken: fasting, random, or 2 hours after a meal.")
        # Weight, height and waist are shown pre-filled from the last entry: send them only when changed,
        # so an old weight is not saved again as today's measurement
        for key, shown in (("weight_kg", self.weight), ("height_cm", self.height), ("waist_cm", form.get("waist") or self.waist)):
            if str(shown or "").strip() == self._body_last.get(key, ""):
                body[key] = None
        changed_size = body["weight_kg"] is not None or body["height_cm"] is not None
        if changed_size and body["weight_kg"] is not None and body["height_cm"] is None:
            body["height_cm"] = _number(self.height)  # BMI needs both in the same entry
        if changed_size and body["height_cm"] is not None and body["weight_kg"] is None:
            body["weight_kg"] = _number(self.weight)
        body = {k: v for k, v in body.items() if v is not None}
        if not body:
            return rx.toast.error("Enter at least one new measurement.")
        reply = await self._api("POST", "/vitals", json=body, idempotent=True)
        if not reply.ok:
            return rx.toast.error(reply.message("Your vitals could not be saved."))
        await self._load_vitals()
        return rx.toast.success("Vitals saved. BMI was calculated automatically.")

    @rx.event
    async def save_lifestyle(self, form: Form):
        try:
            body = {"smoking_status": SMOKING.get(form.get("smoking") or self.smoking),
                    "pack_years": _number(form.get("pack_years")),
                    "physical_activity_min_week": _number(form.get("activity"), int),
                    "sleep_hours_day": _number(form.get("sleep")), "sedentary_hours_day": _number(form.get("sedentary")),
                    "sodium_mg_day": _number(form.get("sodium"), int), "carbs_g_day": _number(form.get("carbs"), int),
                    "occupational_dust": YES_NO.get(form.get("dust", "")), "substance_use": SUBSTANCE.get(form.get("alcohol", ""))}
            labs = [("TSH", _number(form.get("tsh")), "mIU/L"), ("VITAMIN_B12", _number(form.get("b12")), "pg/mL")]
        except ValueError:
            return rx.toast.error("Please enter numbers only.")
        if body["smoking_status"] == "never":
            body["pack_years"] = None
        body = {k: v for k, v in body.items() if v is not None}
        if body:
            reply = await self._api("POST", "/lifestyle", json=body, idempotent=True)
            if not reply.ok:
                return rx.toast.error(reply.message("Your lifestyle answers could not be saved."))
        for code, value, unit in labs:  # optional lab results are saved as typed-in lab values
            if value is not None:
                reply = await self._api("POST", "/labs", json={"test_code": code, "value": value, "unit": unit}, idempotent=True)
                if not reply.ok:
                    return rx.toast.error(reply.message(f"The {code} value could not be saved."))
        if not body and all(v is None for _, v, _ in labs):
            return rx.toast.error("Answer at least one question.")
        await self._load_lifestyle()
        return rx.toast.success("Lifestyle saved.")

    @rx.event
    def set_history(self, i: int, value: str):
        self.history = [replace(q, answer=value) if k == i else q for k, q in enumerate(self.history)]

    @rx.event
    async def save_history(self):
        body = {q.key: YES_NO.get(q.answer) for q in self.history}  # "Not sure" is sent as null
        reply = await self._api("PUT", "/health-history", json=body)
        if not reply.ok:
            return rx.toast.error(reply.message("Your health history could not be saved."))
        return rx.toast.success("Health history saved.")

    # ================= family history =================
    @rx.event
    async def load_family_page(self):
        if not self.is_logged_in:
            return rx.redirect("/")
        await self._load_family()

    async def _load_family(self):
        entries = await self._api("GET", "/family-history")
        if entries.ok:
            self.family = [FamilyEntry(e["record_id"], RELATIVE_LABEL.get(e["relative"], e["relative"]), e["disease_domain"], e["specific_diagnosis"],
                                       _num(e.get("age_at_diagnosis")), ONSET_LABEL.get(e.get("onset_classification", "unknown"), "Unknown"),
                                       CONFIRMED_LABEL.get(e.get("diagnosis_confirmed", "not_sure"), "Not sure"), bool(e.get("shared_environment")))
                           for e in entries.data]
        scores = await self._api("GET", "/family-history/fhrs")
        if scores.ok:
            self.bands = {s["disease_domain"]: s["risk_band"] for s in scores.data}

    @rx.event
    def set_fam_relative(self, value: str):
        self.fam_relative = value

    @rx.event
    def set_fam_disease(self, value: str):
        self.fam_disease = value

    @rx.event
    async def add_family_entry(self, form: Form):
        relative = RELATIVE_CODE.get(self.fam_relative)
        disease = next((code for code, label in DISEASE_LABEL.items() if label == self.fam_disease), None)
        if not relative or not disease:
            return rx.toast.error("Choose the relative and the condition group.")
        diagnosis = _clean(form, "diagnosis")
        if not diagnosis:
            return rx.toast.error("Type the diagnosis, for example Hypertension or Asthma.")
        try:
            age = _number(form.get("age"), int)
        except ValueError:
            return rx.toast.error("Age at diagnosis must be a number.")
        body = {"relative": relative, "disease_domain": disease, "specific_diagnosis": diagnosis,
                "diagnosis_confirmed": CONFIRMED.get(form.get("confirmed", ""), "not_sure"),
                "shared_environment": form.get("household") in ("on", True, "true")}
        if age is not None:
            body["age_at_diagnosis"] = age
        reply = await self._api("POST", "/family-history", json=body, idempotent=True)
        if not reply.ok:
            return rx.toast.error(reply.message("This relative could not be saved."))
        await self._load_family()
        return [rx.toast.success("Saved. Your family history score was updated."), rx.set_value("fam_diagnosis", ""), rx.set_value("fam_age", "")]

    @rx.event
    async def delete_family_entry(self, record_id: int):
        reply = await self._api("DELETE", f"/family-history/{record_id}")
        if not reply.ok:
            return rx.toast.error(reply.message("This entry could not be removed."))
        await self._load_family()
        return rx.toast.success("Removed. Your family history score was updated.")

    @rx.event
    async def no_family_history(self):
        """Nobody in the family has these conditions: records Baseline for all four diseases."""
        reply = await self._api("POST", "/family-history/fhrs")
        if not reply.ok:
            return rx.toast.error(reply.message())
        await self._load_family()
        return rx.toast.success("Saved: no known family history.")

    # ================= questionnaires (Screen 5) =================
    @rx.event
    async def load_questionnaires_page(self):
        if not self.is_logged_in:
            return rx.redirect("/")
        wanted = self.router.url.query_parameters.get("i", "")
        if wanted in qn.INSTRUMENTS:
            self._start(wanted)
        await self._load_readiness()
        await self._load_q_history()

    async def _load_q_history(self):
        reply = await self._api("GET", "/questionnaires", params={"limit": 50})
        if reply.ok:
            self.q_history = [QRow(qn.INSTRUMENTS.get(r["instrument"], {}).get("title", r["instrument"]), when_label(r["completed_at"]),
                                   r["total_score"], r["band"], bool(r["requires_crisis_prompt"])) for r in reply.data]

    def _start(self, code: str):
        self.q_instrument = code
        self.q_answers = [-1] * len(qn.INSTRUMENTS[code]["items"])

    @rx.event
    def set_q_instrument(self, code: str):
        self._start(code)

    @rx.event
    def set_q_answer(self, i: int, label: str):
        spec = qn.INSTRUMENTS[self.q_instrument]
        score = spec["options"].index(label) + spec["low"]
        answers = list(self.q_answers)
        answers[i] = score
        self.q_answers = answers
        # Safety: PHQ-9 item 9 above 0 shows the crisis resources at once, before anything is saved
        if self.q_instrument == "PHQ9" and i == qn.PHQ9_SELF_HARM_ITEM and score > 0:
            self.crisis_message, self.crisis_hotlines, self.show_crisis = qn.CRISIS_MESSAGE, qn.CRISIS_HOTLINES, True

    @rx.event
    def close_crisis(self):
        self.show_crisis = False

    @rx.event
    async def submit_questionnaire(self):
        left = sum(1 for a in self.q_answers if a < qn.INSTRUMENTS[self.q_instrument]["low"])
        if left:
            yield rx.toast.error(f"Please answer every question ({left} left).")
            return
        self.q_saving = True
        yield
        reply = await self._api("POST", "/questionnaires", json={"instrument": self.q_instrument, "answers": self.q_answers}, idempotent=True)
        self.q_saving = False
        if not reply.ok:
            yield rx.toast.error(reply.message("Your answers could not be saved."))
            return
        data = reply.data
        if data.get("requires_crisis_prompt"):  # the API's own text and numbers
            resources = data.get("crisis_resources") or {}
            self.crisis_message = resources.get("message", qn.CRISIS_MESSAGE)
            self.crisis_hotlines = resources.get("hotlines", qn.CRISIS_HOTLINES)
            self.show_crisis = True
        title = qn.INSTRUMENTS[self.q_instrument]["title"]
        self._start(self.q_instrument)
        await self._load_q_history()
        await self._load_readiness()
        yield rx.toast.success(f"{title} saved. Score {data['total_score']} ({data['band']}). This is a screening result, not a diagnosis.")

    # ================= scan and verify =================
    @rx.event
    async def load_scan_page(self):
        if not self.is_logged_in:
            return rx.redirect("/")
        await self._load_readiness()
        await self._load_scan_missing()
        if not self.doc_id:  # pick up the newest document still waiting for review
            docs = await self._api("GET", "/documents")
            if docs.ok and docs.data:
                newest = docs.data[0]
                if newest["status"] in ("pending", "processing", "ready_for_review"):
                    await self._load_document(newest["document_id"])

    async def _load_scan_missing(self):
        """Splits the readiness list: lab values (typed in here) and everything else (links)."""
        reply = await self._api("GET", "/assessment/readiness")
        if not reply.ok:
            return
        other = [Check(item["label"], False, "Missing", item.get("enter_in", ""), _screen_for(item.get("enter_in", ""), item.get("instrument")))
                 for item in reply.data.get("missing_required", []) if item["name"].upper() not in LAB_UNIT]
        # Lab values are optional model inputs (ml/features.yaml): list every one a model could still use
        codes = []
        for model in reply.data.get("models", {}).values():
            for item in model.get("missing_required", []) + model.get("missing_optional", []):
                code = item["name"].upper()
                if code in LAB_UNIT and code not in codes:
                    codes.append(code)
        order = list(LAB_UNIT)
        self.missing_labs = [MissingLab(code, LAB_LABEL[code], LAB_UNIT[code]) for code in sorted(codes, key=order.index)]
        self.other_missing = other

    async def _load_document(self, document_id: int):
        reply = await self._api("GET", f"/documents/{document_id}/review")
        if not reply.ok:
            return
        doc = reply.data["document"]
        self.doc_id, self.doc_status, self.doc_error = doc["document_id"], doc["status"], doc.get("error_message") or ""
        self.doc_is_pdf = doc["mime_type"] == "application/pdf"
        self.labs = [Lab(r["lab_result_id"], r["test_code"], LAB_LABEL.get(r["test_code"], r["test_code"]), _num(r["value"]), r["unit"],
                         f"{_num(r['value_as_reported'])} {r['unit_as_reported']}" if r.get("unit_as_reported") else "",
                         "Scanned" if r["source"] == "ocr" else "Typed by me", bool(r.get("out_of_expected_range")))
                     for r in reply.data["lab_results"]]
        if not self.doc_is_pdf and not self.doc_preview:  # show the photo beside the values
            file = await self._api("GET", f"/documents/{document_id}/file")
            if file.ok:
                self.doc_preview = f"data:{doc['mime_type']};base64,{base64.b64encode(file.content).decode()}"

    @rx.event
    async def handle_upload(self, files: list[rx.UploadFile]):
        if not files:
            return
        f = files[0]
        data = await f.read()
        self.uploading, self.doc_preview, self.labs = True, "", []
        self.doc_name = Path(getattr(f, "filename", None) or getattr(f, "name", "upload")).name
        yield
        reply = await self._api("POST", "/documents", files={"file": (self.doc_name, data, "application/octet-stream")})
        if not reply.ok:
            self.uploading = False
            yield rx.toast.error(reply.message("The file could not be uploaded."))
            return
        self.doc_id, self.doc_status = reply.data["document_id"], reply.data["status"]
        yield rx.toast.success("Document received. Reading the values...")
        # Wait for the OCR worker. A document that is still 'pending' after a few seconds means no
        # OCR engine runs here (OCR_ENGINE=none): stop waiting at once. Once OCR has started
        # ('processing'), wait up to about 40 seconds for it to finish.
        started = False
        for check in range(20):
            await self._load_document(self.doc_id)
            started = started or self.doc_status == "processing"
            if self.doc_status not in ("pending", "processing") or (not started and check >= 2):
                break
            yield
            await asyncio.sleep(2)
        self.uploading = False
        await self._load_scan_missing()
        if self.doc_status == "ready_for_review":
            yield rx.toast.success("Check each value below, fix any mistake, then confirm.")
        elif self.doc_status == "pending" and not started:
            self.doc_error = "Your file is saved, but automatic reading (OCR) is not running on this computer. Please type the values from your report below."
            yield rx.toast.info("Please type the values from your report below.")
        else:
            yield rx.toast.info(self.doc_error or "The document could not be read yet. Please type the values in below.")

    @rx.event
    async def fix_lab(self, lab_id: int, form: Form):
        """PATCH /labs/{id}: the value and unit exactly as printed on the report."""
        lab = next((l for l in self.labs if l.lab_id == lab_id), None)
        if lab is None:
            return
        try:
            value = _number(form.get("value"))
        except ValueError:
            return rx.toast.error("Please enter a number.")
        if value is None:
            return rx.toast.error("Please enter the value, or remove the line.")
        reply = await self._api("PATCH", f"/labs/{lab_id}", json={"test_code": lab.code, "value": value, "unit": _clean(form, "unit") or lab.unit})
        if not reply.ok:
            return rx.toast.error(reply.message("The value could not be changed."))
        await self._load_document(self.doc_id)
        return rx.toast.success(f"{lab.label} updated.")

    @rx.event
    async def remove_lab(self, lab_id: int):
        reply = await self._api("DELETE", f"/labs/{lab_id}")
        if not reply.ok:
            return rx.toast.error(reply.message("This line could not be removed."))
        await self._load_document(self.doc_id)
        await self._load_scan_missing()
        return rx.toast.success("Line removed.")

    def _typed_values(self, form: Form) -> list[dict[str, Any]]:
        """Lab values typed into the 'missing' boxes (field names lab_<CODE>)."""
        typed = []
        for lab in self.missing_labs:
            value = _number(form.get(f"lab_{lab.code}"))
            if value is not None:
                typed.append({"test_code": lab.code, "value": value, "unit": lab.unit})
        return typed

    @rx.event
    async def confirm_document(self, form: Form):
        """POST /documents/{id}/confirm: every reviewed value plus what was typed for what OCR missed."""
        try:
            body = {"typed_values": self._typed_values(form)}
        except ValueError:
            return rx.toast.error("Please enter numbers only.")
        if _clean(form, "collected_on"):
            body["collected_on"] = _clean(form, "collected_on")
        reply = await self._api("POST", f"/documents/{self.doc_id}/confirm", json=body)
        if not reply.ok:
            return rx.toast.error(reply.message("The values could not be confirmed."))
        await self._load_document(self.doc_id)
        await self._load_scan_missing()
        await self._load_readiness()
        return rx.toast.success("Values confirmed. Only confirmed values are used for your assessment.")

    @rx.event
    async def reject_document(self):
        reply = await self._api("POST", f"/documents/{self.doc_id}/reject")
        if not reply.ok:
            return rx.toast.error(reply.message())
        self.doc_id, self.doc_status, self.labs, self.doc_preview = 0, "", [], ""
        await self._load_scan_missing()
        return rx.toast.info("The scanned values were dropped. The file is kept; you can type the values in instead.")

    @rx.event
    async def save_typed_labs(self, form: Form):
        """No document (or OCR is off): each value is saved as a typed-in lab result (POST /labs)."""
        try:
            typed = self._typed_values(form)
        except ValueError:
            return rx.toast.error("Please enter numbers only.")
        if not typed:
            return rx.toast.error("Type at least one value.")
        collected = _clean(form, "collected_on")
        for value in typed:
            reply = await self._api("POST", "/labs", json=value | ({"collected_on": collected} if collected else {}), idempotent=True)
            if not reply.ok:
                return rx.toast.error(reply.message(f"{LAB_LABEL.get(value['test_code'], value['test_code'])} could not be saved."))
        await self._load_scan_missing()
        await self._load_readiness()
        return [rx.toast.success(f"{len(typed)} lab value(s) saved."), rx.set_value("lab_collected_on", "")]

    @rx.event
    async def run_assessment(self):
        self.running = True
        yield
        reply = await self._api("POST", "/assessment/run", idempotent=True)
        self.running = False
        if reply.status == 503:
            yield rx.toast.info("The risk models are not switched on yet: they are still being trained and checked. Your data is saved and ready.")
            return
        if reply.status == 409:
            await self._load_readiness()
            await self._load_scan_missing()
            yield rx.toast.error("Some required information is still missing. See the list on this page.")
            return
        if not reply.ok:
            yield rx.toast.error(reply.message("The assessment could not be run."))
            return
        self.assessment_id = reply.data["assessment_id"]
        self.probs = {r["disease"]: float(r["probability"]) for r in reply.data["results"]}
        self.tiers = {r["disease"]: r["risk_level"] for r in reply.data["results"]}
        yield rx.redirect("/results")

    # ================= results =================
    @rx.event
    async def load_results_page(self):
        if not self.is_logged_in:
            return rx.redirect("/")
        if not self.assessment_id:
            listed = await self._api("GET", "/assessment", params={"limit": 1})
            if listed.ok and listed.data:
                self.assessment_id = listed.data[0]["assessment_id"]
        if self.assessment_id:
            await self._load_assessment(self.assessment_id)
        await self._load_goals()

    async def _load_assessment(self, assessment_id: int):
        reply = await self._api("GET", f"/assessment/{assessment_id}")
        if not reply.ok:
            return
        self.api_results, self.result_disclaimer = reply.data["results"], reply.data.get("disclaimer", "")
        self.probs = {r["disease"]: float(r["probability"]) for r in self.api_results}
        self.tiers = {r["disease"]: r["risk_level"] for r in self.api_results}
        if self.selected not in self.probs and self.api_results:
            self.selected = self.api_results[0]["disease"]

    @rx.event
    def select_disease(self, code: str):
        self.selected = code

    @rx.event
    async def save_report(self):
        if not self.assessment_id:
            return rx.toast.error("Run an assessment first.")
        reply = await self._api("POST", "/reports", json={"assessment_id": self.assessment_id}, idempotent=True)
        if reply.status == 409:  # already saved, or the care plans are not approved yet: the API says which
            return rx.toast.info(reply.message("This assessment could not be saved as a report."))
        if not reply.ok:
            return rx.toast.error(reply.message("The report could not be saved."))
        return [rx.toast.success("Report saved. It will not change, even if your data changes later."), rx.redirect("/reports")]

    # ================= reports and goals =================
    @rx.event
    async def load_reports_page(self):
        if not self.is_logged_in:
            return rx.redirect("/")
        await self._load_reports()
        await self._load_goals()

    async def _load_reports(self):
        reply = await self._api("GET", "/reports")
        if not reply.ok:
            return
        runs = await self._api("GET", "/assessment", params={"limit": 100})
        levels = {a["assessment_id"]: a["risk_levels"] for a in (runs.data if runs.ok else [])}
        self.reports = [Report(r["report_id"], when_label(r["created_at"]).split(",")[0] + f", {r['created_at'][:4]}",
                               [Chip(f"{code} {tier}", TIER_COLOR.get(tier, "gray")) for code, tier in levels.get(r["assessment_id"], {}).items()],
                               f"Assessment #{r['assessment_id']}")
                        for r in reply.data]

    @rx.event
    async def download_report(self, report_id: int):
        reply = await self._api("GET", f"/reports/{report_id}/pdf")
        if reply.status == 503:
            return rx.toast.info("PDF export needs the GTK libraries on this computer (they are included in Docker). The saved report itself is safe.")
        if not reply.ok:
            return rx.toast.error(reply.message("The PDF could not be made."))
        return rx.download(data=reply.content, filename=f"health-report-{report_id}.pdf")

    async def _load_goals(self):
        reply = await self._api("GET", "/goals", params={"limit": 200})
        if reply.ok:
            self.goals = [Goal(g["goal_id"], g["description"], g["is_completed"],
                               DISEASE_LABEL.get(g["disease_domain"], "My goal") if g.get("disease_domain") else "My goal")
                          for g in reply.data]

    @rx.event
    async def add_goal(self, text: str, category: str):
        """'Add as goal' on a care plan item: linked to this result (saved as 'suggested')."""
        if any(g.text == text[:255] for g in self.goals):
            return rx.toast.info("This is already in your goals.")
        result_id = self._result().get("result_id")
        body = {"description": text[:255]} | ({"result_id": result_id} if result_id else {})
        reply = await self._api("POST", "/goals", json=body, idempotent=True)
        if not reply.ok:
            return rx.toast.error(reply.message("The goal could not be added."))
        await self._load_goals()
        return rx.toast.success("Added to your goals.")

    @rx.event
    async def toggle_goal(self, goal_id: int, value: bool):
        reply = await self._api("PATCH", f"/goals/{goal_id}", json={"is_completed": value})
        if not reply.ok:
            return rx.toast.error(reply.message())
        await self._load_goals()

    @rx.event
    async def new_goal(self, form: Form):
        text = _clean(form, "goal")
        if not text:
            return
        reply = await self._api("POST", "/goals", json={"description": text[:255]}, idempotent=True)
        if not reply.ok:
            return rx.toast.error(reply.message("The goal could not be added."))
        await self._load_goals()


def _screen_for(enter_in: str, instrument: str | None = None) -> str:
    """Readiness 'enter_in' (the screen named by the API) -> page link."""
    text = enter_in.lower()
    if "questionnaire" in text:
        return f"/questionnaires?i={instrument}" if instrument else "/questionnaires"
    if "family" in text:
        return "/family"
    if "lab" in text or "document" in text or "scan" in text:
        return "/scan"
    if "profile" in text or "account" in text:
        return "/account"
    return "/vitals"
