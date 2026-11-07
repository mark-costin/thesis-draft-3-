"""One-off for dev databases: saved FHRS rows from before the FHRS Guide fix are stale.

Run once per database, after 24_family_relative_no.sql:  uv run python -m backend.recalculate_fhrs
Connects as med_app (DATABASE_URL). users has no RLS, so it can list accounts, then it acts as
each patient in turn, the same way a request does.
"""

from sqlalchemy import select

from backend import services
from backend.database import SessionLocal, set_request_identity
from backend.models import Patient, User


def recalculate_all() -> int:
    """Saves a fresh score for every patient who has one, and returns how many patients. Old rows
    stay (one row per calculation). A patient with no score keeps none: a score would tell the
    readiness check that the patient has answered the family history."""
    with SessionLocal() as db:
        user_ids = list(db.scalars(select(User.user_id)))
    patients = 0
    for user_id in user_ids:
        with SessionLocal() as db:
            set_request_identity(db, user_id=user_id)
            patient_id = db.scalar(select(Patient.patient_id).where(Patient.user_id == user_id))
            if patient_id is None:
                continue
            set_request_identity(db, user_id=user_id, patient_id=patient_id)
            if services.latest_fhrs(db, patient_id):
                services.confirm_family_history(db, user_id, patient_id)
                patients += 1
    return patients


if __name__ == "__main__":
    print(f"Recalculated the FHRS of {recalculate_all()} patients")
