"""Profile: a patient sees and edits only their own profile (Sprint 2)."""

from datetime import date

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from backend import services
from backend.database import get_db
from backend.dependencies import CurrentUser, get_current_user
from backend.models import Patient, User
from backend.schemas import ProfileResponse, ProfileUpdate, age_on

router = APIRouter(prefix="/profile", tags=["profile"])
NO_STORE = {"Cache-Control": "no-store"}  # name, birth date and sex must not stay in a cache


def _response(user: User, patient: Patient) -> ProfileResponse:
    return ProfileResponse(
        email=user.email,
        first_name=patient.first_name,
        last_name=patient.last_name,
        date_of_birth=patient.date_of_birth,
        age=age_on(patient.date_of_birth, date.today()),
        biological_sex=patient.biological_sex,
        consent_version=patient.consent_version,
        consent_at=patient.consent_at,
    )


@router.get("", response_model=ProfileResponse)
def read_profile(response: Response, current: CurrentUser = Depends(get_current_user),
                 db: Session = Depends(get_db)) -> ProfileResponse:
    response.headers.update(NO_STORE)
    return _response(*services.get_profile(db, current.user_id))


@router.patch("", response_model=ProfileResponse)
def edit_profile(body: ProfileUpdate, response: Response, current: CurrentUser = Depends(get_current_user),
                 db: Session = Depends(get_db)) -> ProfileResponse:
    response.headers.update(NO_STORE)
    changes = body.model_dump(include=body.model_fields_set)
    return _response(*services.update_profile(db, current.user_id, current.patient_id, changes))
