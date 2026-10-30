"""Request and response bodies for the API.

Rules here mirror the database CHECKs, so bad input returns 422 before it reaches PostgreSQL.
extra="forbid" rejects unknown fields, so a client can never sneak in user_id, patient_id
or token_version.
"""

import html
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal

import nh3
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from backend import questionnaires
from backend.config import settings

# Same pattern as users_email_format_check in 02_users.sql
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
MIN_AGE = 18


def age_on(born: date, today: date) -> int:
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def check_adult_birth_date(value: date) -> date:
    """Same rule at registration and on profile edits (patients_dob_check + the 18+ rule)."""
    today = date.today()
    if value > today:
        raise ValueError("Date of birth cannot be in the future")
    if value < date(1900, 1, 1):
        raise ValueError("Date of birth must be on or after 1900-01-01")
    if age_on(value, today) < MIN_AGE:
        raise ValueError(f"You must be at least {MIN_AGE} years old to use this app")
    return value


def strip_text(value):
    return value.strip() if isinstance(value, str) else value


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(max_length=254, pattern=EMAIL_PATTERN)
    # Not stripped: spaces in a password are allowed and kept
    password: str = Field(min_length=8, max_length=128)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    date_of_birth: date
    biological_sex: Literal["Female", "Male"]
    privacy_consent: bool
    consent_version: str = Field(max_length=20)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value):
        # One account per address, whatever the capitalization (matches ux_users_email_lower)
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("first_name", "last_name", mode="before")
    @classmethod
    def strip_name(cls, value):
        return strip_text(value)

    @field_validator("date_of_birth")
    @classmethod
    def check_age(cls, value: date) -> date:
        return check_adult_birth_date(value)

    @field_validator("privacy_consent")
    @classmethod
    def check_consent(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("You must accept the privacy notice to register")
        return value

    @field_validator("consent_version")
    @classmethod
    def check_consent_version(cls, value: str) -> str:
        # The patient must have seen the current notice, not an old cached one
        if value != settings.privacy_notice_version:
            raise ValueError("The privacy notice has changed; please review the current version")
        return value


class RegisterResponse(BaseModel):
    email: str
    consent_version: str


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value):
        return value.strip().lower() if isinstance(value, str) else value


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class MeResponse(BaseModel):
    email: str
    first_name: str
    last_name: str
    consent_version: str


class PasswordResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(max_length=254)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value):
        return value.strip().lower() if isinstance(value, str) else value


class PasswordResetConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=20, max_length=200)
    new_password: str = Field(min_length=8, max_length=128)  # same rule as registration


class ProfileResponse(BaseModel):
    email: str
    first_name: str
    last_name: str
    date_of_birth: date
    age: int  # computed, never stored (03_patients.sql)
    biological_sex: Literal["Female", "Male"]
    consent_version: str
    consent_at: datetime


class ProfileUpdate(BaseModel):
    """Fields a patient may change. Email, consent and IDs are not editable here."""
    model_config = ConfigDict(extra="forbid")

    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    date_of_birth: date | None = None
    biological_sex: Literal["Female", "Male"] | None = None

    @field_validator("first_name", "last_name", mode="before")
    @classmethod
    def strip_name(cls, value):
        return strip_text(value)

    @field_validator("date_of_birth")
    @classmethod
    def check_age(cls, value: date | None) -> date | None:
        return None if value is None else check_adult_birth_date(value)

    @model_validator(mode="after")
    def not_empty(self):
        if not self.model_fields_set:
            raise ValueError("Send at least one field to change")
        for name in self.model_fields_set:
            if getattr(self, name) is None:
                raise ValueError(f"{name} cannot be empty")
        return self



# ---------------------------------------------------------------------------
# Vitals (07_vitals_records.sql). Ranges are the same as the table's CHECK constraints.
# ---------------------------------------------------------------------------
MEASUREMENT_FIELDS = ("sbp", "resting_hr", "weight_kg", "height_cm", "waist_cm",
                      "blood_glucose_mgdl", "spo2")


def _one_decimal(low, high, max_digits):
    return Field(default=None, ge=low, le=high, max_digits=max_digits, decimal_places=1)


class VitalsCreate(BaseModel):
    """One reading session. Send only what was measured; BMI is computed by the database
    when weight and height are in the same entry."""
    model_config = ConfigDict(extra="forbid")

    sbp: int | None = Field(default=None, ge=70, le=250)          # systolic, mmHg
    dbp: int | None = Field(default=None, ge=40, le=150)          # diastolic, mmHg
    resting_hr: int | None = Field(default=None, ge=30, le=220)   # beats per minute
    weight_kg: Decimal | None = _one_decimal(20, 300, 5)
    height_cm: Decimal | None = _one_decimal(100, 250, 5)
    waist_cm: Decimal | None = _one_decimal(40, 200, 5)
    blood_glucose_mgdl: Decimal | None = _one_decimal(20, 600, 5)
    glucose_context: Literal["fasting", "random", "post_meal_2h"] | None = None
    spo2: Decimal | None = _one_decimal(70, 100, 4)
    measured_at: AwareDatetime | None = None  # with time zone, e.g. 2026-10-30T08:00:00+08:00

    @model_validator(mode="after")
    def check_rules(self):
        if (self.sbp is None) != (self.dbp is None):
            raise ValueError("Enter both systolic and diastolic blood pressure")
        if self.sbp is not None and self.sbp <= self.dbp:
            raise ValueError("Systolic pressure must be higher than diastolic")
        if (self.blood_glucose_mgdl is None) != (self.glucose_context is None):
            raise ValueError("A blood sugar reading needs its context (fasting, random or post_meal_2h)")
        if all(getattr(self, name) is None for name in MEASUREMENT_FIELDS):
            raise ValueError("Enter at least one measurement")
        if self.measured_at is not None and self.measured_at > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise ValueError("measured_at cannot be in the future")
        return self


class VitalsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    vitals_id: int
    sbp: int | None
    dbp: int | None
    resting_hr: int | None
    weight_kg: float | None
    height_cm: float | None
    bmi: float | None  # computed by the database
    waist_cm: float | None
    blood_glucose_mgdl: float | None
    glucose_context: str | None
    spo2: float | None
    measured_at: datetime
    created_at: datetime



# ---------------------------------------------------------------------------
# Lifestyle (09_lifestyle_records.sql): a new entry each time habits are reported.
# ---------------------------------------------------------------------------
LIFESTYLE_FIELDS = ("smoking_status", "pack_years", "physical_activity_min_week", "sedentary_hours_day",
                    "sleep_hours_day", "sodium_mg_day", "carbs_g_day", "occupational_dust", "substance_use")


class LifestyleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    smoking_status: Literal["never", "former", "current"] | None = None
    pack_years: Decimal | None = _one_decimal(0, 200, 5)               # packs per day x years smoked
    physical_activity_min_week: int | None = Field(default=None, ge=0, le=10080)
    sedentary_hours_day: Decimal | None = _one_decimal(0, 24, 3)
    sleep_hours_day: Decimal | None = _one_decimal(0, 24, 3)
    sodium_mg_day: int | None = Field(default=None, ge=0, le=20000)
    carbs_g_day: int | None = Field(default=None, ge=0, le=1500)
    occupational_dust: bool | None = None
    substance_use: Literal["none", "past", "current"] | None = None

    @model_validator(mode="after")
    def check_rules(self):
        if all(getattr(self, name) is None for name in LIFESTYLE_FIELDS):
            raise ValueError("Answer at least one question")
        if self.pack_years is not None and self.smoking_status is None:
            raise ValueError("pack_years needs smoking_status")
        if self.smoking_status == "never" and self.pack_years not in (None, 0):
            raise ValueError("A never-smoker cannot have pack years")
        if (self.sedentary_hours_day is not None and self.sleep_hours_day is not None
                and self.sedentary_hours_day + self.sleep_hours_day > 24):
            raise ValueError("Sitting time plus sleep cannot be more than 24 hours")
        return self


class LifestyleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    lifestyle_id: int
    smoking_status: str | None
    pack_years: float | None
    physical_activity_min_week: int | None
    sedentary_hours_day: float | None
    sleep_hours_day: float | None
    sodium_mg_day: int | None
    carbs_g_day: int | None
    occupational_dust: bool | None
    substance_use: str | None
    recorded_at: datetime


# ---------------------------------------------------------------------------
# Health history (10_health_history.sql): one row per patient, replaced as a whole.
# null = "I don't know" or not answered.
# ---------------------------------------------------------------------------
HISTORY_FIELDS = ("gestational_diabetes", "acanthosis_nigricans", "allergic_rhinitis",
                  "childhood_asthma", "tbi_history")


class HealthHistoryUpdate(BaseModel):
    """PUT body: every field is set to what is sent; a field left out becomes null."""
    model_config = ConfigDict(extra="forbid")

    gestational_diabetes: bool | None = None   # female patients only
    acanthosis_nigricans: bool | None = None
    allergic_rhinitis: bool | None = None
    childhood_asthma: bool | None = None
    tbi_history: bool | None = None


class HealthHistoryResponse(HealthHistoryUpdate):
    model_config = ConfigDict(from_attributes=True)

    updated_at: datetime | None  # null until the patient answers for the first time



# ---------------------------------------------------------------------------
# Questionnaires (11_questionnaire_responses.sql); scoring in backend/questionnaires.py
# ---------------------------------------------------------------------------
class QuestionnaireSubmit(BaseModel):
    """The item answers in question order. The total is computed by the server."""
    model_config = ConfigDict(extra="forbid")

    instrument: Literal["PHQ9", "GAD7", "PSQI", "MMRC", "CAT", "ACT"]
    answers: list[int]

    @model_validator(mode="after")
    def check_answers(self):
        questionnaires.check_answers(self.instrument, self.answers)
        return self


class CrisisHotline(BaseModel):
    name: str
    number: str
    hours: str


class CrisisResources(BaseModel):
    message: str
    hotlines: list[CrisisHotline]


class QuestionnaireResponseOut(BaseModel):
    response_id: int
    instrument: str
    answers: list[int]
    total_score: int
    band: str                       # screening band from the published cut-offs, not a diagnosis
    requires_crisis_prompt: bool    # PHQ-9 item 9 above 0: show crisis resources at once
    crisis_resources: CrisisResources | None
    completed_at: datetime



# ---------------------------------------------------------------------------
# Goals (18_patient_goals.sql)
# ---------------------------------------------------------------------------
def clean_text(value):
    """Trims and removes any HTML, so a goal can never carry a script into a page."""
    if not isinstance(value, str):
        return value
    # nh3 drops tags (and whole <script>/<style> blocks) and escapes the rest; unescape turns
    # "&amp;" back into "&" so normal text like "walk & stretch" is kept as typed
    return html.unescape(nh3.clean(value, tags=set())).strip()


class GoalCreate(BaseModel):
    """A goal the patient writes, or one taken from a care plan item. When result_id is given
    (the risk result whose care plan suggested it), the goal is saved as 'suggested'."""
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=255)
    disease_domain: Literal["CVD", "T2D", "CRD", "NEURO"] | None = None  # null = general wellness
    target_metric: str | None = Field(default=None, min_length=1, max_length=100)
    result_id: int | None = Field(default=None, ge=1)

    @field_validator("description", "target_metric", mode="before")
    @classmethod
    def clean(cls, value):
        return clean_text(value)


class GoalUpdate(BaseModel):
    """Tick or untick a goal, or reword it. Only the fields sent are changed."""
    model_config = ConfigDict(extra="forbid")

    is_completed: bool | None = None
    description: str | None = Field(default=None, min_length=1, max_length=255)
    target_metric: str | None = Field(default=None, max_length=100)

    @field_validator("description", "target_metric", mode="before")
    @classmethod
    def clean(cls, value):
        return clean_text(value)

    @model_validator(mode="after")
    def not_empty(self):
        if not self.model_fields_set:
            raise ValueError("Send at least one field to change")
        if "is_completed" in self.model_fields_set and self.is_completed is None:
            raise ValueError("is_completed must be true or false")
        if "description" in self.model_fields_set and self.description is None:
            raise ValueError("description cannot be empty")
        return self


class GoalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    goal_id: int
    description: str
    disease_domain: str | None
    target_metric: str | None
    source: str
    result_id: int | None
    is_completed: bool
    completed_at: datetime | None
    created_at: datetime
