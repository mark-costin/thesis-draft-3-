"""Request and response bodies for the API.

Rules here mirror the database CHECKs, so bad input returns 422 before it reaches PostgreSQL.
extra="forbid" rejects unknown fields, so a client can never sneak in user_id, patient_id
or token_version.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.config import settings

# Same pattern as users_email_format_check in 02_users.sql
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
MIN_AGE = 18


def age_on(born: date, today: date) -> int:
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


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
        return value.strip() if isinstance(value, str) else value

    @field_validator("date_of_birth")
    @classmethod
    def check_age(cls, value: date) -> date:
        today = date.today()
        if value > today:
            raise ValueError("Date of birth cannot be in the future")
        if value < date(1900, 1, 1):
            raise ValueError("Date of birth must be on or after 1900-01-01")
        if age_on(value, today) < MIN_AGE:
            raise ValueError(f"You must be at least {MIN_AGE} years old to register")
        return value

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
