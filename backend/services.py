"""Business logic used by the routes. Routes handle HTTP; these functions handle the database."""

import hashlib
import secrets
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from cryptography.fernet import InvalidToken
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.database import set_request_identity
from backend import audit, fhrs, labs, mailer, questionnaires, storage
from backend.models import (
    Document,
    HealthReport,
    LabResult,
    LifestyleRecord,
    PasswordResetToken,
    Patient,
    PatientFamilyHistory,
    PatientFhrsStratification,
    PatientGoal,
    PatientHealthHistory,
    QuestionnaireResponse,
    RiskAssessment,
    RiskResult,
    ShapExplanation,
    User,
    VitalsRecord,
)
from backend.schemas import RegisterRequest
from backend.security import (
    burn_dummy_check,
    create_access_token,
    hash_password,
    verify_and_update_password,
    verify_password,
)

# Lockout policy: this many wrong passwords in a row locks the account for LOCKOUT_MINUTES
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15

# Reset tokens: single use, valid for 1 hour (also enforced by reset_tokens_expiry_check)
RESET_TOKEN_LIFETIME = timedelta(hours=1)


class EmailAlreadyRegistered(Exception):
    pass


class InvalidCredentials(Exception):
    """Wrong email or wrong password. One error for both, so emails cannot be probed."""


class InvalidResetToken(Exception):
    """Unknown, already used, or expired. One error for all three."""


class AccountLocked(Exception):
    def __init__(self, locked_until: datetime):
        super().__init__(locked_until)
        self.locked_until = locked_until


def register_patient(db: Session, data: RegisterRequest) -> None:
    """Creates the users row and the patients row in one transaction.

    Order matters for RLS: the users row comes first (users has no RLS), then app.user_id
    is set to the new user, because the patients policy only accepts a row whose user_id
    matches app.user_id.
    """
    exists = db.scalar(select(User.user_id).where(func.lower(User.email) == data.email))
    if exists is not None:
        raise EmailAlreadyRegistered

    user = User(email=data.email, password_hash=hash_password(data.password))
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        # Two sign-ups with the same email at the same moment: ux_users_email_lower stops the second.
        # Any other constraint error is a real bug, so it is raised as is.
        if getattr(exc.orig, "sqlstate", None) == "23505":  # unique_violation
            raise EmailAlreadyRegistered from exc
        raise

    set_request_identity(db, user_id=user.user_id)
    patient = Patient(
        user_id=user.user_id,
        first_name=data.first_name,
        last_name=data.last_name,
        date_of_birth=data.date_of_birth,
        biological_sex=data.biological_sex,
        privacy_consent=data.privacy_consent,
        consent_version=data.consent_version,
    )
    db.add(patient)
    db.flush()
    audit.log_event(db, "REGISTER", user_id=user.user_id, patient_id=patient.patient_id,
                    consent_version=data.consent_version)
    db.commit()


def login(db: Session, email: str, password: str) -> str:
    """Checks the password, applies the lockout, and returns a signed access token."""
    now = datetime.now(timezone.utc)

    # FOR UPDATE: two logins at once cannot both read the same failed_attempts count
    user = db.scalars(
        select(User).where(func.lower(User.email) == email).with_for_update()
    ).one_or_none()

    if user is None:
        burn_dummy_check(password)
        audit.log_event(db, "LOGIN_FAILED", reason="unknown_email")  # the email itself is not stored
        db.commit()
        raise InvalidCredentials

    if user.locked_until is not None and user.locked_until > now:
        audit.log_event(db, "LOGIN_FAILED", user_id=user.user_id, reason="locked")
        db.commit()
        raise AccountLocked(user.locked_until)

    matches, new_hash = verify_and_update_password(password, user.password_hash)

    if not matches:
        user.failed_attempts += 1
        if user.failed_attempts >= MAX_FAILED_ATTEMPTS:
            user.failed_attempts = 0
            user.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
            audit.log_event(db, "ACCOUNT_LOCKED", user_id=user.user_id,
                            failed_attempts=MAX_FAILED_ATTEMPTS, locked_minutes=LOCKOUT_MINUTES)
            db.commit()
            raise AccountLocked(user.locked_until)
        audit.log_event(db, "LOGIN_FAILED", user_id=user.user_id, reason="wrong_password",
                        failed_attempts=user.failed_attempts)
        db.commit()
        raise InvalidCredentials

    user.failed_attempts = 0
    user.locked_until = None
    user.last_login_at = now
    if new_hash:
        user.password_hash = new_hash

    # patients has RLS: the patient row is visible once app.user_id is this user
    set_request_identity(db, user_id=user.user_id)
    patient_id = db.scalar(select(Patient.patient_id).where(Patient.user_id == user.user_id))
    if patient_id is None:
        db.rollback()
        raise InvalidCredentials

    token = create_access_token(user.user_id, patient_id, user.token_version)
    audit.log_event(db, "LOGIN_SUCCESS", user_id=user.user_id, patient_id=patient_id)
    db.commit()
    return token


def revoke_all_tokens(db: Session, user_id: int, patient_id: int) -> None:
    """Logout everywhere: every token issued before this call stops working."""
    db.execute(update(User).where(User.user_id == user_id)
               .values(token_version=User.token_version + 1))
    audit.log_event(db, "LOGOUT", user_id=user_id, patient_id=patient_id, all_sessions=True)
    db.commit()


def _hash_reset_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def request_password_reset(db: Session, email: str) -> None:
    """Creates a reset token and sends the link. Does nothing visible if the email is unknown,
    so the response is the same whether or not an account exists."""
    user = db.scalars(select(User).where(func.lower(User.email) == email)).one_or_none()
    if user is None:
        db.rollback()
        return

    # Only the newest link works: drop this user's unused older tokens
    db.execute(delete(PasswordResetToken).where(
        PasswordResetToken.user_id == user.user_id,
        PasswordResetToken.used_at.is_(None),
    ))

    token = secrets.token_urlsafe(32)  # 256 bits; only its SHA-256 hash is stored
    db.add(PasswordResetToken(
        user_id=user.user_id,
        token_hash=_hash_reset_token(token),
        # Database time, so it matches created_at exactly and passes the 1-hour CHECK
        expires_at=func.now() + RESET_TOKEN_LIFETIME,
    ))
    audit.log_event(db, "PASSWORD_RESET_REQUESTED", user_id=user.user_id)
    db.commit()
    mailer.send_password_reset(user.email, token)


def confirm_password_reset(db: Session, token: str, new_password: str) -> None:
    """Uses the token once, sets the new password, unlocks the account and logs out
    every existing session."""
    row = db.scalars(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == _hash_reset_token(token),
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at > func.now(),
        ).with_for_update()
    ).one_or_none()
    if row is None:
        db.rollback()
        raise InvalidResetToken

    row.used_at = func.now()  # med_app may update only this column
    db.execute(update(User).where(User.user_id == row.user_id).values(
        password_hash=hash_password(new_password),
        password_changed_at=func.now(),
        token_version=User.token_version + 1,
        failed_attempts=0,
        locked_until=None,
    ))
    audit.log_event(db, "PASSWORD_CHANGED", user_id=row.user_id, via="reset_link",
                    sessions_revoked=True)
    db.commit()


PROFILE_FIELDS = ("first_name", "last_name", "date_of_birth", "biological_sex")


def get_profile(db: Session, user_id: int) -> tuple[User, Patient]:
    """The caller's own account and patient row. RLS on patients already limits the query
    to this patient, so no WHERE on patient_id is needed (or trusted)."""
    user = db.get(User, user_id)
    patient = db.scalars(select(Patient)).one()
    return user, patient


def update_profile(db: Session, user_id: int, patient_id: int, changes: dict) -> tuple[User, Patient]:
    """Applies only the fields the patient sent. The audit entry names the changed fields,
    never their values."""
    user, patient = get_profile(db, user_id)
    changed = sorted(name for name, value in changes.items()
                     if name in PROFILE_FIELDS and getattr(patient, name) != value)
    for name in changed:
        setattr(patient, name, changes[name])
    if changed:
        db.flush()
        audit.log_event(db, "DATA_UPDATED", user_id=user_id, patient_id=patient_id,
                        table="patients", fields=changed)
    db.commit()
    db.refresh(patient)
    return user, patient



# ---------------------------------------------------------------------------
# Vitals. RLS limits every query to the caller's patient, so a record that belongs to someone
# else looks exactly like one that does not exist (404, nothing leaked).
# ---------------------------------------------------------------------------
class NotFound(Exception):
    pass


def create_vitals(db: Session, user_id: int, patient_id: int, data: dict) -> VitalsRecord:
    values = {name: value for name, value in data.items() if value is not None}
    row = VitalsRecord(patient_id=patient_id, **values)
    db.add(row)
    db.flush()
    db.refresh(row)  # read back the BMI the database computed
    audit.log_event(db, "DATA_CREATED", user_id=user_id, patient_id=patient_id,
                    table="vitals_records", record_id=row.vitals_id, fields=sorted(values))
    db.commit()
    return row


def list_vitals(db: Session, limit: int) -> list[VitalsRecord]:
    return list(db.scalars(select(VitalsRecord)
                           .order_by(VitalsRecord.measured_at.desc(), VitalsRecord.vitals_id.desc())
                           .limit(limit)))


def get_vitals(db: Session, vitals_id: int) -> VitalsRecord:
    row = db.scalars(select(VitalsRecord).where(VitalsRecord.vitals_id == vitals_id)).one_or_none()
    if row is None:
        raise NotFound
    return row


def delete_vitals(db: Session, user_id: int, patient_id: int, vitals_id: int) -> None:
    deleted = db.scalar(delete(VitalsRecord).where(VitalsRecord.vitals_id == vitals_id)
                        .returning(VitalsRecord.vitals_id))
    if deleted is None:
        db.rollback()
        raise NotFound
    audit.log_event(db, "DATA_DELETED", user_id=user_id, patient_id=patient_id,
                    table="vitals_records", record_id=vitals_id)
    db.commit()



# ---------------------------------------------------------------------------
# Lifestyle
# ---------------------------------------------------------------------------
def create_lifestyle(db: Session, user_id: int, patient_id: int, data: dict) -> LifestyleRecord:
    values = {name: value for name, value in data.items() if value is not None}
    row = LifestyleRecord(patient_id=patient_id, **values)
    db.add(row)
    db.flush()
    db.refresh(row)
    audit.log_event(db, "DATA_CREATED", user_id=user_id, patient_id=patient_id,
                    table="lifestyle_records", record_id=row.lifestyle_id, fields=sorted(values))
    db.commit()
    return row


def list_lifestyle(db: Session, limit: int) -> list[LifestyleRecord]:
    return list(db.scalars(select(LifestyleRecord)
                           .order_by(LifestyleRecord.recorded_at.desc(), LifestyleRecord.lifestyle_id.desc())
                           .limit(limit)))


def get_lifestyle(db: Session, lifestyle_id: int) -> LifestyleRecord:
    row = db.scalars(select(LifestyleRecord).where(LifestyleRecord.lifestyle_id == lifestyle_id)).one_or_none()
    if row is None:
        raise NotFound
    return row


def delete_lifestyle(db: Session, user_id: int, patient_id: int, lifestyle_id: int) -> None:
    deleted = db.scalar(delete(LifestyleRecord).where(LifestyleRecord.lifestyle_id == lifestyle_id)
                        .returning(LifestyleRecord.lifestyle_id))
    if deleted is None:
        db.rollback()
        raise NotFound
    audit.log_event(db, "DATA_DELETED", user_id=user_id, patient_id=patient_id,
                    table="lifestyle_records", record_id=lifestyle_id)
    db.commit()


# ---------------------------------------------------------------------------
# Health history (one row per patient)
# ---------------------------------------------------------------------------
class NotApplicable(Exception):
    """A question that does not apply to this patient, e.g. gestational diabetes for a male patient."""


def get_health_history(db: Session) -> PatientHealthHistory | None:
    return db.scalars(select(PatientHealthHistory)).one_or_none()


def save_health_history(db: Session, user_id: int, patient_id: int, data: dict) -> PatientHealthHistory:
    sex = db.scalar(select(Patient.biological_sex))  # RLS: the caller's own row
    if sex != "Female" and data.get("gestational_diabetes") is not None:
        raise NotApplicable("gestational_diabetes applies to female patients only")

    existed = get_health_history(db) is not None
    values = {name: data.get(name) for name in (
        "gestational_diabetes", "acanthosis_nigricans", "allergic_rhinitis", "childhood_asthma", "tbi_history")}
    db.execute(pg_insert(PatientHealthHistory)
               .values(patient_id=patient_id, **values)
               .on_conflict_do_update(index_elements=[PatientHealthHistory.patient_id], set_=values))
    audit.log_event(db, "DATA_UPDATED" if existed else "DATA_CREATED", user_id=user_id,
                    patient_id=patient_id, table="patient_health_history",
                    fields=sorted(name for name, value in values.items() if value is not None))
    db.commit()
    return get_health_history(db)



# ---------------------------------------------------------------------------
# Questionnaires
# ---------------------------------------------------------------------------
def submit_questionnaire(db: Session, user_id: int, patient_id: int, instrument: str,
                         answers: list[int]) -> QuestionnaireResponse:
    row = QuestionnaireResponse(patient_id=patient_id, instrument=instrument,
                                item_responses=answers, total_score=questionnaires.total_score(answers))
    db.add(row)
    db.flush()
    db.refresh(row)  # read back requires_crisis_prompt, computed by the database
    # The instrument name only: never the score, the answers or the crisis flag
    audit.log_event(db, "DATA_CREATED", user_id=user_id, patient_id=patient_id,
                    table="questionnaire_responses", record_id=row.response_id, instrument=instrument)
    db.commit()
    return row


def list_questionnaires(db: Session, instrument: str | None, limit: int) -> list[QuestionnaireResponse]:
    query = select(QuestionnaireResponse)
    if instrument is not None:
        query = query.where(QuestionnaireResponse.instrument == instrument)
    return list(db.scalars(query.order_by(QuestionnaireResponse.completed_at.desc(),
                                          QuestionnaireResponse.response_id.desc()).limit(limit)))


def get_questionnaire(db: Session, response_id: int) -> QuestionnaireResponse:
    row = db.scalars(select(QuestionnaireResponse)
                     .where(QuestionnaireResponse.response_id == response_id)).one_or_none()
    if row is None:
        raise NotFound
    return row


def delete_questionnaire(db: Session, user_id: int, patient_id: int, response_id: int) -> None:
    deleted = db.scalar(delete(QuestionnaireResponse)
                        .where(QuestionnaireResponse.response_id == response_id)
                        .returning(QuestionnaireResponse.response_id))
    if deleted is None:
        db.rollback()
        raise NotFound
    audit.log_event(db, "DATA_DELETED", user_id=user_id, patient_id=patient_id,
                    table="questionnaire_responses", record_id=response_id)
    db.commit()



# ---------------------------------------------------------------------------
# Goals
# ---------------------------------------------------------------------------
class UnknownResult(Exception):
    """result_id does not exist or belongs to another patient (goals_result_fkey)."""


def create_goal(db: Session, user_id: int, patient_id: int, data: dict) -> PatientGoal:
    values = {name: value for name, value in data.items() if value is not None}
    row = PatientGoal(patient_id=patient_id, source="suggested" if "result_id" in values else "self", **values)
    db.add(row)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        if getattr(exc.orig, "sqlstate", None) == "23503":  # foreign_key_violation
            raise UnknownResult from exc
        raise
    db.refresh(row)
    audit.log_event(db, "DATA_CREATED", user_id=user_id, patient_id=patient_id,
                    table="patient_goals", record_id=row.goal_id, fields=sorted(values))
    db.commit()
    return row


def list_goals(db: Session, completed: bool | None) -> list[PatientGoal]:
    query = select(PatientGoal)
    if completed is not None:
        query = query.where(PatientGoal.is_completed == completed)
    # Open goals first, then newest first
    return list(db.scalars(query.order_by(PatientGoal.is_completed, PatientGoal.created_at.desc(),
                                          PatientGoal.goal_id.desc())))


def get_goal(db: Session, goal_id: int) -> PatientGoal:
    row = db.scalars(select(PatientGoal).where(PatientGoal.goal_id == goal_id)).one_or_none()
    if row is None:
        raise NotFound
    return row


def update_goal(db: Session, user_id: int, patient_id: int, goal_id: int, changes: dict) -> PatientGoal:
    row = get_goal(db, goal_id)
    changed = []
    if "is_completed" in changes and changes["is_completed"] != row.is_completed:
        row.is_completed = changes["is_completed"]
        row.completed_at = func.now() if row.is_completed else None  # goals_completed_check
        changed.append("is_completed")
    for name in ("description", "target_metric"):
        if name in changes and changes[name] != getattr(row, name):
            setattr(row, name, changes[name] or None)
            changed.append(name)
    if changed:
        db.flush()
        audit.log_event(db, "DATA_UPDATED", user_id=user_id, patient_id=patient_id,
                        table="patient_goals", record_id=goal_id, fields=sorted(changed))
    db.commit()
    db.refresh(row)
    return row


def delete_goal(db: Session, user_id: int, patient_id: int, goal_id: int) -> None:
    deleted = db.scalar(delete(PatientGoal).where(PatientGoal.goal_id == goal_id)
                        .returning(PatientGoal.goal_id))
    if deleted is None:
        db.rollback()
        raise NotFound
    audit.log_event(db, "DATA_DELETED", user_id=user_id, patient_id=patient_id,
                    table="patient_goals", record_id=goal_id)
    db.commit()



# ---------------------------------------------------------------------------
# Family history and FHRS. Every change recalculates the FHRS for all four diseases in the
# same transaction, so the score always matches the saved pedigree.
# ---------------------------------------------------------------------------
class DuplicateEntry(Exception):
    """The same relative is already listed with this diagnosis (family_unique_entry)."""


def _set_onset(row: PatientFamilyHistory, chosen: str | None = None) -> None:
    """A known age decides the onset level; without an age the patient's choice is kept."""
    if row.age_at_diagnosis is not None:
        row.onset_classification = fhrs.onset_from_age(
            row.disease_domain, row.specific_diagnosis, fhrs.sex_of(row.relative), row.age_at_diagnosis)
    elif chosen is not None:
        row.onset_classification = chosen


def recalculate_fhrs(db: Session, patient_id: int) -> list[PatientFhrsStratification]:
    entries = [fhrs.FamilyEntry(relative=r.relative, domain=r.disease_domain, onset=r.onset_classification,
                                confirmed=r.diagnosis_confirmed, shared_environment=r.shared_environment)
               for r in db.scalars(select(PatientFamilyHistory))]
    rows = []
    for domain in fhrs.DOMAINS:
        result = fhrs.calculate(domain, entries)
        row = PatientFhrsStratification(
            patient_id=patient_id, disease_domain=domain, affected_relatives=result.affected_relatives,
            clustering_score=result.clustering_score, environmental_modifier=result.environmental_modifier,
            calculated_fhrs=result.calculated_fhrs, risk_band=result.risk_band)
        db.add(row)
        rows.append(row)
    db.flush()
    return rows


def latest_fhrs(db: Session) -> list[PatientFhrsStratification]:
    rows = db.scalars(select(PatientFhrsStratification)
                      .distinct(PatientFhrsStratification.disease_domain)
                      .order_by(PatientFhrsStratification.disease_domain,
                                PatientFhrsStratification.evaluated_at.desc(),
                                PatientFhrsStratification.fhrs_id.desc()))
    return list(rows)


def list_family_history(db: Session) -> list[PatientFamilyHistory]:
    return list(db.scalars(select(PatientFamilyHistory).order_by(
        PatientFamilyHistory.disease_domain, PatientFamilyHistory.record_id)))


def get_family_entry(db: Session, record_id: int) -> PatientFamilyHistory:
    row = db.scalars(select(PatientFamilyHistory)
                     .where(PatientFamilyHistory.record_id == record_id)).one_or_none()
    if row is None:
        raise NotFound
    return row


def _flush_entry(db: Session) -> None:
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        if getattr(exc.orig, "sqlstate", None) == "23505":
            raise DuplicateEntry from exc
        raise


def add_family_entry(db: Session, user_id: int, patient_id: int, data: dict) -> PatientFamilyHistory:
    chosen = data.pop("onset_classification")
    row = PatientFamilyHistory(patient_id=patient_id, **data)
    _set_onset(row, chosen)
    db.add(row)
    _flush_entry(db)
    recalculate_fhrs(db, patient_id)
    audit.log_event(db, "DATA_CREATED", user_id=user_id, patient_id=patient_id,
                    table="patient_family_history", record_id=row.record_id)
    db.commit()
    db.refresh(row)
    return row


def update_family_entry(db: Session, user_id: int, patient_id: int, record_id: int,
                        changes: dict) -> PatientFamilyHistory:
    row = get_family_entry(db, record_id)
    chosen = changes.pop("onset_classification", None)
    for name, value in changes.items():
        setattr(row, name, value)
    _set_onset(row, chosen)
    _flush_entry(db)
    recalculate_fhrs(db, patient_id)
    audit.log_event(db, "DATA_UPDATED", user_id=user_id, patient_id=patient_id,
                    table="patient_family_history", record_id=record_id,
                    fields=sorted(set(changes) | ({"onset_classification"} if chosen else set())))
    db.commit()
    db.refresh(row)
    return row


def delete_family_entry(db: Session, user_id: int, patient_id: int, record_id: int) -> None:
    deleted = db.scalar(delete(PatientFamilyHistory).where(PatientFamilyHistory.record_id == record_id)
                        .returning(PatientFamilyHistory.record_id))
    if deleted is None:
        db.rollback()
        raise NotFound
    recalculate_fhrs(db, patient_id)
    audit.log_event(db, "DATA_DELETED", user_id=user_id, patient_id=patient_id,
                    table="patient_family_history", record_id=record_id)
    db.commit()


def confirm_family_history(db: Session, user_id: int, patient_id: int) -> list[PatientFhrsStratification]:
    """Saves the FHRS as it stands now. A patient with no known family history calls this to
    record Baseline for every disease (the readiness check needs an FHRS for each model)."""
    rows = recalculate_fhrs(db, patient_id)
    audit.log_event(db, "DATA_CREATED", user_id=user_id, patient_id=patient_id,
                    table="patient_fhrs_stratification", entries=len(list_family_history(db)))
    db.commit()
    for row in rows:
        db.refresh(row)
    return rows



# ---------------------------------------------------------------------------
# Account export and permanent deletion (RA 10173)
# ---------------------------------------------------------------------------
# Every patient-owned table, with columns left out of the export: internal keys and storage
# names that mean nothing to the patient. RLS limits every query to the caller's rows.
EXPORT_TABLES = (
    ("vitals", VitalsRecord, ()),
    ("lab_results", LabResult, ()),
    ("lifestyle", LifestyleRecord, ()),
    ("health_history", PatientHealthHistory, ()),
    ("questionnaires", QuestionnaireResponse, ()),
    ("family_history", PatientFamilyHistory, ()),
    ("family_history_scores", PatientFhrsStratification, ()),
    ("goals", PatientGoal, ()),
    ("documents", Document, ("storage_key",)),
    ("risk_assessments", RiskAssessment, ()),
    ("risk_results", RiskResult, ()),
    ("risk_explanations", ShapExplanation, ()),
    ("health_reports", HealthReport, ()),
)


class WrongPassword(Exception):
    pass


def _plain(value):
    """Database values as plain JSON (numbers, text, ISO dates)."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value


def _rows(db: Session, model, skip=()) -> list[dict]:
    columns = [c for c in model.__table__.columns if c.name not in skip and c.name != "patient_id"]
    return [{c.name: _plain(getattr(row, c.key)) for c in columns}
            for row in db.scalars(select(model))]


def export_account(db: Session, user_id: int, patient_id: int) -> dict:
    """Everything the app holds about the patient, as one JSON document. The audit log is not
    included: it is the system's security record and holds IDs and event names only."""
    user = db.get(User, user_id)
    patient = db.scalars(select(Patient)).one()
    data = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "account": {"email": user.email, "created_at": _plain(user.created_at),
                    "last_login_at": _plain(user.last_login_at),
                    "password_changed_at": _plain(user.password_changed_at)},
        "profile": {c.name: _plain(getattr(patient, c.key)) for c in Patient.__table__.columns
                    if c.name not in ("patient_id", "user_id")},
    }
    for name, model, skip in EXPORT_TABLES:
        data[name] = _rows(db, model, skip)
    audit.log_event(db, "DATA_EXPORTED", user_id=user_id, patient_id=patient_id, format="json")
    db.commit()
    return data


def delete_account(db: Session, user_id: int, patient_id: int, password: str) -> list[str]:
    """Permanently deletes the account and every patient row. Deleting the users row cascades to
    patients and from there to every patient-owned table (ON DELETE CASCADE). The audit
    ledger has no foreign keys, so the ACCOUNT_DELETED entry stays.
    Returns the storage keys of uploaded files, which the caller removes from disk."""
    user = db.get(User, user_id)
    if user is None or not verify_password(password, user.password_hash):
        db.rollback()
        raise WrongPassword
    storage_keys = [str(k) for k in db.scalars(select(Document.storage_key))]
    audit.log_event(db, "ACCOUNT_DELETED", user_id=user_id, patient_id=patient_id, files=len(storage_keys))
    db.execute(delete(User).where(User.user_id == user_id))
    db.commit()
    return storage_keys



# ---------------------------------------------------------------------------
# Documents: upload, list, download, delete. The file is encrypted on disk (backend/storage.py).
# ---------------------------------------------------------------------------
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # documents_file_size_check

# The real type comes from the file's first bytes, never from the name or the browser's claim
_SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"%PDF-", "application/pdf"),
)


class UnsupportedFile(Exception):
    pass


class DuplicateFile(Exception):
    pass


def detect_mime(data: bytes) -> str:
    for signature, mime in _SIGNATURES:
        if data.startswith(signature):
            return mime
    raise UnsupportedFile("Only JPEG, PNG or PDF files are accepted")


def upload_document(db: Session, user_id: int, patient_id: int, data: bytes) -> Document:
    if not data:
        raise UnsupportedFile("The file is empty")
    if len(data) > MAX_UPLOAD_BYTES:
        raise UnsupportedFile("The file is larger than 10 MB")
    mime = detect_mime(data)
    digest = hashlib.sha256(data).hexdigest()

    key = uuid.uuid4()
    row = Document(patient_id=patient_id, storage_key=key, mime_type=mime,
                   file_size_bytes=len(data), file_sha256=digest)
    db.add(row)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        if getattr(exc.orig, "sqlstate", None) == "23505":  # documents_patient_file_unique
            raise DuplicateFile from exc
        raise

    storage.save(key, data)
    try:
        audit.log_event(db, "DOCUMENT_UPLOADED", user_id=user_id, patient_id=patient_id,
                        record_id=row.document_id, mime_type=mime, size_bytes=len(data))
        db.commit()
    except Exception:
        db.rollback()
        storage.remove(key)  # no row, no file
        raise
    db.refresh(row)
    return row


def list_documents(db: Session) -> list[Document]:
    return list(db.scalars(select(Document).order_by(Document.uploaded_at.desc(), Document.document_id.desc())))


def get_document(db: Session, document_id: int) -> Document:
    row = db.scalars(select(Document).where(Document.document_id == document_id)).one_or_none()
    if row is None:
        raise NotFound
    return row


class TamperedFile(Exception):
    pass


def read_document_file(db: Session, user_id: int, patient_id: int, document_id: int) -> tuple[Document, bytes]:
    row = get_document(db, document_id)
    try:
        data = storage.load(row.storage_key)
    except (InvalidToken, FileNotFoundError) as exc:  # wrong key, edited or missing file
        raise TamperedFile from exc
    if hashlib.sha256(data).hexdigest() != row.file_sha256:  # integrity check of the original
        raise TamperedFile
    audit.log_event(db, "DATA_EXPORTED", user_id=user_id, patient_id=patient_id,
                    table="documents", record_id=document_id)
    db.commit()
    return row, data


def delete_document(db: Session, user_id: int, patient_id: int, document_id: int) -> None:
    # Values not yet confirmed go with the document. Confirmed ones stay (document_id becomes
    # NULL through lab_document_fkey): the patient already checked them against the report.
    db.execute(delete(LabResult).where(LabResult.document_id == document_id, LabResult.is_confirmed.is_(False)))
    key = db.scalar(delete(Document).where(Document.document_id == document_id).returning(Document.storage_key))
    if key is None:
        db.rollback()
        raise NotFound
    audit.log_event(db, "DATA_DELETED", user_id=user_id, patient_id=patient_id,
                    table="documents", record_id=document_id)
    db.commit()
    storage.remove(key)


# ---------------------------------------------------------------------------
# Lab results (manual entry). OCR rows will be added by the OCR worker and confirmed on review.
# ---------------------------------------------------------------------------
def create_lab(db: Session, user_id: int, patient_id: int, mapped: labs.MappedLab,
               collected_on: date | None) -> LabResult:
    """A typed-in value is confirmed at once: the patient read it off the report themselves."""
    row = LabResult(patient_id=patient_id, test_code=mapped.test_code, value=mapped.value, unit=mapped.unit,
                    value_as_reported=mapped.value_as_reported, unit_as_reported=mapped.unit_as_reported,
                    source="manual", is_confirmed=True, confirmed_at=datetime.now(timezone.utc),
                    collected_on=collected_on)
    db.add(row)
    db.flush()
    db.refresh(row)
    # Field names only; the test and its value stay out of the audit ledger
    audit.log_event(db, "DATA_CREATED", user_id=user_id, patient_id=patient_id, table="lab_results",
                    record_id=row.lab_result_id, fields=["collected_on", "test_code", "unit", "value"],
                    converted=mapped.unit_as_reported is not None)
    db.commit()
    return row


def list_labs(db: Session, test_code: str | None, limit: int) -> list[LabResult]:
    query = select(LabResult)
    if test_code is not None:
        query = query.where(LabResult.test_code == test_code)
    return list(db.scalars(query.order_by(LabResult.collected_on.desc().nulls_last(),
                                          LabResult.created_at.desc(), LabResult.lab_result_id.desc())
                          .limit(limit)))


def get_lab(db: Session, lab_result_id: int) -> LabResult:
    row = db.scalars(select(LabResult).where(LabResult.lab_result_id == lab_result_id)).one_or_none()
    if row is None:
        raise NotFound
    return row


def delete_lab(db: Session, user_id: int, patient_id: int, lab_result_id: int) -> None:
    deleted = db.scalar(delete(LabResult).where(LabResult.lab_result_id == lab_result_id)
                        .returning(LabResult.lab_result_id))
    if deleted is None:
        db.rollback()
        raise NotFound
    audit.log_event(db, "DATA_DELETED", user_id=user_id, patient_id=patient_id,
                    table="lab_results", record_id=lab_result_id)
    db.commit()


# ---------------------------------------------------------------------------
# OCR review: the patient checks what OCR read before any of it is used (patient-in-the-loop).
# The OCR worker (separate task) saves unconfirmed lab rows and sets the document to ready_for_review.
# ---------------------------------------------------------------------------
class WrongStatus(Exception):
    def __init__(self, status: str):
        super().__init__(status)
        self.status = status


class NothingToConfirm(Exception):
    pass


class AlreadyConfirmed(Exception):
    pass


def review_document(db: Session, document_id: int) -> tuple[Document, list[LabResult]]:
    document = get_document(db, document_id)
    rows = list(db.scalars(select(LabResult).where(LabResult.document_id == document_id)
                           .order_by(LabResult.lab_result_id)))
    return document, rows


def update_lab(db: Session, user_id: int, patient_id: int, lab_result_id: int,
               mapped: labs.MappedLab, collected_on: date | None, collected_on_sent: bool) -> LabResult:
    """Fixes an OCR value before it is confirmed. Confirmed values are not edited: delete and re-enter."""
    row = get_lab(db, lab_result_id)
    if row.is_confirmed:
        raise AlreadyConfirmed
    row.test_code, row.value, row.unit = mapped.test_code, mapped.value, mapped.unit
    row.value_as_reported, row.unit_as_reported = mapped.value_as_reported, mapped.unit_as_reported
    if collected_on_sent:
        row.collected_on = collected_on
    db.flush()
    audit.log_event(db, "DATA_UPDATED", user_id=user_id, patient_id=patient_id, table="lab_results",
                    record_id=lab_result_id, fields=["test_code", "unit", "value"]
                    + (["collected_on"] if collected_on_sent else []))
    db.commit()
    db.refresh(row)
    return row


def _finish_review(db: Session, document_id: int, new_status: str, **extra) -> Document:
    """Moves a document out of ready_for_review in one UPDATE, so two clicks cannot both succeed."""
    document = get_document(db, document_id)
    changed = db.scalar(update(Document)
                        .where(Document.document_id == document_id, Document.status == "ready_for_review")
                        .values(status=new_status, ocr_text=None, **extra)  # OCR text no longer needed
                        .returning(Document.document_id))
    if changed is None:
        db.rollback()
        raise WrongStatus(document.status)
    return document


def confirm_document(db: Session, user_id: int, patient_id: int, document_id: int,
                     collected_on: date | None) -> tuple[Document, list[LabResult]]:
    """The patient says the (possibly corrected) values match the report: they now count."""
    now = datetime.now(timezone.utc)
    _finish_review(db, document_id, "verified", verified_at=now)
    pending = LabResult.document_id == document_id, LabResult.is_confirmed.is_(False)
    if collected_on is not None:  # the date on the report, for rows OCR could not date
        db.execute(update(LabResult).where(*pending, LabResult.collected_on.is_(None))
                   .values(collected_on=collected_on))
    confirmed = db.scalars(update(LabResult).where(*pending)
                           .values(is_confirmed=True, confirmed_at=now)
                           .returning(LabResult.lab_result_id)).all()
    if not confirmed:
        db.rollback()
        raise NothingToConfirm
    audit.log_event(db, "DOCUMENT_VERIFIED", user_id=user_id, patient_id=patient_id,
                    record_id=document_id, confirmed_values=len(confirmed))
    db.commit()
    return review_document(db, document_id)


def reject_document(db: Session, user_id: int, patient_id: int, document_id: int) -> Document:
    """OCR read the report wrong: its unconfirmed values are dropped. The file stays; the patient
    can type the values in with POST /labs instead."""
    _finish_review(db, document_id, "rejected")
    dropped = db.scalars(delete(LabResult)
                         .where(LabResult.document_id == document_id, LabResult.is_confirmed.is_(False))
                         .returning(LabResult.lab_result_id)).all()
    audit.log_event(db, "DOCUMENT_REJECTED", user_id=user_id, patient_id=patient_id,
                    record_id=document_id, dropped_values=len(dropped))
    db.commit()
    return get_document(db, document_id)
