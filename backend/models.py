"""SQLAlchemy models for the 19 tables in med_db.

The SQL scripts in database/ are the source of truth. These models only describe the
existing tables so the app can query them: never call Base.metadata.create_all().

* Generated columns (vitals_records.bmi, patient_family_history.relationship_degree and
  relative_sex, questionnaire_responses.requires_crisis_prompt) use Computed(), so SQLAlchemy
  never sends a value for them. Read them after a flush or refresh.
* CHECK rules, RLS policies and grants live in the database, not here.
* Generated from the database schema; if a SQL script changes, regenerate or update by hand
  and run tests/test_models.py.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CHAR,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    """users (02_users.sql)."""

    # No RLS: login looks up the account by email before anyone is authenticated.
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(Integer, Identity(always=True), primary_key=True)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    failed_attempts: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"), nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    token_version: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)


class Patient(Base):
    """patients (03_patients.sql)."""

    __tablename__ = "patients"

    patient_id: Mapped[int] = mapped_column(Integer, Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    date_of_birth: Mapped[date] = mapped_column(Date, nullable=False)
    biological_sex: Mapped[str] = mapped_column(String(6), nullable=False)
    privacy_consent: Mapped[bool] = mapped_column(Boolean, nullable=False)
    consent_version: Mapped[str] = mapped_column(String(20), nullable=False)
    consent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)


class PasswordResetToken(Base):
    """password_reset_tokens (04_password_reset_tokens.sql)."""

    # med_app may only UPDATE used_at.
    __tablename__ = "password_reset_tokens"

    token_id: Mapped[int] = mapped_column(Integer, Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)


class AuditLedger(Base):
    """audit_ledger (05_audit_ledger.sql)."""

    # med_app has NO access to this table. Write entries with log_audit() only;
    # this model exists for owner-side checks (e.g. a DBeaver/admin script).
    __tablename__ = "audit_ledger"

    audit_id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    actor_user_id: Mapped[int | None] = mapped_column(Integer)
    patient_id: Mapped[int | None] = mapped_column(Integer)
    payload: Mapped[Any] = mapped_column(JSONB, server_default=text("'{}'::jsonb"), nullable=False)
    prev_seal: Mapped[str | None] = mapped_column(CHAR(64))
    entry_seal: Mapped[str] = mapped_column(CHAR(64), nullable=False)


class Document(Base):
    """documents (06_documents.sql)."""

    __tablename__ = "documents"

    document_id: Mapped[int] = mapped_column(Integer, Identity(always=True), primary_key=True)
    patient_id: Mapped[int] = mapped_column(Integer, ForeignKey("patients.patient_id", ondelete="CASCADE"), nullable=False)
    storage_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), server_default=text("gen_random_uuid()"), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(30), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    file_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    ocr_engine: Mapped[str] = mapped_column(String(50), server_default=text("'Chandra OCR'::character varying"), nullable=False)
    ocr_text: Mapped[str | None] = mapped_column(Text)
    ocr_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    status: Mapped[str] = mapped_column(String(20), server_default=text("'pending'::character varying"), nullable=False)
    error_message: Mapped[str | None] = mapped_column(String(255))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VitalsRecord(Base):
    """vitals_records (07_vitals_records.sql)."""

    __tablename__ = "vitals_records"

    vitals_id: Mapped[int] = mapped_column(Integer, Identity(always=True), primary_key=True)
    patient_id: Mapped[int] = mapped_column(Integer, ForeignKey("patients.patient_id", ondelete="CASCADE"), nullable=False)
    sbp: Mapped[int | None] = mapped_column(SmallInteger)
    dbp: Mapped[int | None] = mapped_column(SmallInteger)
    resting_hr: Mapped[int | None] = mapped_column(SmallInteger)
    weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
    height_cm: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
    bmi: Mapped[Decimal | None] = mapped_column(Numeric(4, 1), Computed("round((weight_kg / ((height_cm / (100)::numeric) * (height_cm / (100)::numeric))), 1)", persisted=True))  # computed by the database: read-only
    waist_cm: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
    blood_glucose_mgdl: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
    glucose_context: Mapped[str | None] = mapped_column(String(15))
    spo2: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)


class LabResult(Base):
    """lab_results (08_lab_results.sql)."""

    __tablename__ = "lab_results"
    __table_args__ = (
        ForeignKeyConstraint(["document_id", "patient_id"], ["documents.document_id", "documents.patient_id"], name="lab_document_fkey", ondelete="SET NULL"),
    )

    lab_result_id: Mapped[int] = mapped_column(Integer, Identity(always=True), primary_key=True)
    patient_id: Mapped[int] = mapped_column(Integer, ForeignKey("patients.patient_id", ondelete="CASCADE"), nullable=False)
    document_id: Mapped[int | None] = mapped_column(Integer)
    test_code: Mapped[str] = mapped_column(String(20), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    unit: Mapped[str] = mapped_column(String(15), nullable=False)
    value_as_reported: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    unit_as_reported: Mapped[str | None] = mapped_column(String(15))
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    is_confirmed: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    collected_on: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)


class LifestyleRecord(Base):
    """lifestyle_records (09_lifestyle_records.sql)."""

    __tablename__ = "lifestyle_records"

    lifestyle_id: Mapped[int] = mapped_column(Integer, Identity(always=True), primary_key=True)
    patient_id: Mapped[int] = mapped_column(Integer, ForeignKey("patients.patient_id", ondelete="CASCADE"), nullable=False)
    smoking_status: Mapped[str | None] = mapped_column(String(10))
    pack_years: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
    physical_activity_min_week: Mapped[int | None] = mapped_column(SmallInteger)
    sedentary_hours_day: Mapped[Decimal | None] = mapped_column(Numeric(3, 1))
    sleep_hours_day: Mapped[Decimal | None] = mapped_column(Numeric(3, 1))
    sodium_mg_day: Mapped[int | None] = mapped_column(SmallInteger)
    carbs_g_day: Mapped[int | None] = mapped_column(SmallInteger)
    occupational_dust: Mapped[bool | None] = mapped_column(Boolean)
    substance_use: Mapped[str | None] = mapped_column(String(10))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)


class PatientHealthHistory(Base):
    """patient_health_history (10_health_history.sql)."""

    __tablename__ = "patient_health_history"

    patient_id: Mapped[int] = mapped_column(Integer, ForeignKey("patients.patient_id", ondelete="CASCADE"), primary_key=True)
    gestational_diabetes: Mapped[bool | None] = mapped_column(Boolean)
    acanthosis_nigricans: Mapped[bool | None] = mapped_column(Boolean)
    allergic_rhinitis: Mapped[bool | None] = mapped_column(Boolean)
    childhood_asthma: Mapped[bool | None] = mapped_column(Boolean)
    tbi_history: Mapped[bool | None] = mapped_column(Boolean)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)


class QuestionnaireResponse(Base):
    """questionnaire_responses (11_questionnaire_responses.sql)."""

    __tablename__ = "questionnaire_responses"

    response_id: Mapped[int] = mapped_column(Integer, Identity(always=True), primary_key=True)
    patient_id: Mapped[int] = mapped_column(Integer, ForeignKey("patients.patient_id", ondelete="CASCADE"), nullable=False)
    instrument: Mapped[str] = mapped_column(String(5), nullable=False)
    item_responses: Mapped[Any] = mapped_column(JSONB, nullable=False)
    total_score: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    requires_crisis_prompt: Mapped[bool | None] = mapped_column(Boolean, Computed("(((instrument)::text = 'PHQ9'::text) AND (COALESCE(((item_responses ->> 8))::integer, 0) >= 1))", persisted=True))  # computed by the database: read-only
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)


class PatientFamilyHistory(Base):
    """patient_family_history (12_family_history.sql + 21_famhistory.sql)."""

    __tablename__ = "patient_family_history"

    record_id: Mapped[int] = mapped_column(Integer, Identity(always=True), primary_key=True)
    patient_id: Mapped[int] = mapped_column(Integer, ForeignKey("patients.patient_id", ondelete="CASCADE"), nullable=False)
    disease_domain: Mapped[str] = mapped_column(String(5), nullable=False)
    specific_diagnosis: Mapped[str] = mapped_column(String(150), nullable=False)
    relative: Mapped[str] = mapped_column(String(25), nullable=False)
    age_at_diagnosis: Mapped[int | None] = mapped_column(SmallInteger)
    onset_classification: Mapped[str] = mapped_column(String(12), server_default=text("'unknown'::character varying"), nullable=False)
    shared_environment: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    relationship_degree: Mapped[int | None] = mapped_column(SmallInteger, Computed("CASE WHEN ((relative)::text = ANY ((ARRAY['father'::character varying, 'mother'::character varying, 'brother'::character varying, 'sister'::character varying, 'son'::character varying, 'daughter'::character varying])::text[])) THEN 1 WHEN ((relative)::text = ANY ((ARRAY['paternal_grandfather'::character varying, 'paternal_grandmother'::character varying, 'maternal_grandfather'::character varying, 'maternal_grandmother'::character varying, 'paternal_uncle'::character varying, 'paternal_aunt'::character varying, 'maternal_uncle'::character varying, 'maternal_aunt'::character varying, 'half_brother'::character varying, 'half_sister'::character varying, 'grandson'::character varying, 'granddaughter'::character varying, 'nephew'::character varying, 'niece'::character varying])::text[])) THEN 2 ELSE 3 END", persisted=True))  # computed by the database: read-only
    relative_sex: Mapped[str | None] = mapped_column(String(6), Computed("CASE WHEN ((relative)::text = ANY ((ARRAY['father'::character varying, 'brother'::character varying, 'son'::character varying, 'paternal_grandfather'::character varying, 'maternal_grandfather'::character varying, 'paternal_uncle'::character varying, 'maternal_uncle'::character varying, 'half_brother'::character varying, 'grandson'::character varying, 'nephew'::character varying, 'paternal_male_cousin'::character varying, 'maternal_male_cousin'::character varying, 'great_grandfather'::character varying])::text[])) THEN 'Male'::text ELSE 'Female'::text END", persisted=True))  # computed by the database: read-only
    diagnosis_confirmed: Mapped[str] = mapped_column(String(8), server_default=text("'not_sure'::character varying"), nullable=False)


class PatientFhrsStratification(Base):
    """patient_fhrs_stratification (13_fhrs_stratification.sql + 21_famhistory.sql)."""

    __tablename__ = "patient_fhrs_stratification"

    fhrs_id: Mapped[int] = mapped_column(Integer, Identity(always=True), primary_key=True)
    patient_id: Mapped[int] = mapped_column(Integer, ForeignKey("patients.patient_id", ondelete="CASCADE"), nullable=False)
    disease_domain: Mapped[str] = mapped_column(String(5), nullable=False)
    affected_relatives: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    clustering_score: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)
    environmental_modifier: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    calculated_fhrs: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)
    risk_band: Mapped[str] = mapped_column(String(10), nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)


class ModelVersion(Base):
    """model_versions (14_model_versions.sql)."""

    # Read-only for med_app. Models are registered as the database owner.
    __tablename__ = "model_versions"

    model_version_id: Mapped[int] = mapped_column(Integer, Identity(always=True), primary_key=True)
    disease_domain: Mapped[str] = mapped_column(String(5), nullable=False)
    version: Mapped[str] = mapped_column(String(30), nullable=False)
    algorithm: Mapped[str] = mapped_column(String(30), server_default=text("'XGBoost'::character varying"), nullable=False)
    artifact_path: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    feature_list: Mapped[Any] = mapped_column(JSONB, nullable=False)
    thresholds: Mapped[Any] = mapped_column(JSONB, nullable=False)
    calibration_method: Mapped[str | None] = mapped_column(String(20))
    metrics: Mapped[Any | None] = mapped_column(JSONB)
    trained_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)


class RiskAssessment(Base):
    """risk_assessments (15_risk_assessments.sql)."""

    # Immutable for med_app (no UPDATE). Insert, read, delete only.
    __tablename__ = "risk_assessments"

    assessment_id: Mapped[int] = mapped_column(Integer, Identity(always=True), primary_key=True)
    patient_id: Mapped[int] = mapped_column(Integer, ForeignKey("patients.patient_id", ondelete="CASCADE"), nullable=False)
    input_snapshot: Mapped[Any] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)


class RiskResult(Base):
    """risk_results (16_risk_results.sql)."""

    # Immutable for med_app (no UPDATE). Insert, read, delete only.
    __tablename__ = "risk_results"
    __table_args__ = (
        ForeignKeyConstraint(["assessment_id", "patient_id"], ["risk_assessments.assessment_id", "risk_assessments.patient_id"], name="results_assessment_fkey", ondelete="CASCADE"),
        ForeignKeyConstraint(["model_version_id", "disease_domain"], ["model_versions.model_version_id", "model_versions.disease_domain"], name="results_model_fkey", ondelete="RESTRICT"),
    )

    result_id: Mapped[int] = mapped_column(Integer, Identity(always=True), primary_key=True)
    assessment_id: Mapped[int] = mapped_column(Integer, nullable=False)
    patient_id: Mapped[int] = mapped_column(Integer, nullable=False)
    model_version_id: Mapped[int] = mapped_column(Integer, nullable=False)
    disease_domain: Mapped[str] = mapped_column(String(5), nullable=False)
    risk_probability: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(10), nullable=False)
    shap_base_value: Mapped[Decimal | None] = mapped_column(Numeric(8, 5))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)


class ShapExplanation(Base):
    """shap_explanations (17_shap_explanations.sql)."""

    # Immutable for med_app (no UPDATE). Insert, read, delete only.
    __tablename__ = "shap_explanations"
    __table_args__ = (
        ForeignKeyConstraint(["result_id", "patient_id"], ["risk_results.result_id", "risk_results.patient_id"], name="shap_result_fkey", ondelete="CASCADE"),
    )

    explanation_id: Mapped[int] = mapped_column(Integer, Identity(always=True), primary_key=True)
    result_id: Mapped[int] = mapped_column(Integer, nullable=False)
    patient_id: Mapped[int] = mapped_column(Integer, nullable=False)
    feature_name: Mapped[str] = mapped_column(String(100), nullable=False)
    feature_value: Mapped[Decimal | None] = mapped_column(Numeric)
    shap_value: Mapped[Decimal] = mapped_column(Numeric(8, 5), nullable=False)
    rank: Mapped[int] = mapped_column(SmallInteger, nullable=False)


class PatientGoal(Base):
    """patient_goals (18_patient_goals.sql)."""

    __tablename__ = "patient_goals"
    __table_args__ = (
        ForeignKeyConstraint(["result_id", "patient_id"], ["risk_results.result_id", "risk_results.patient_id"], name="goals_result_fkey", ondelete="SET NULL"),
    )

    goal_id: Mapped[int] = mapped_column(Integer, Identity(always=True), primary_key=True)
    patient_id: Mapped[int] = mapped_column(Integer, ForeignKey("patients.patient_id", ondelete="CASCADE"), nullable=False)
    disease_domain: Mapped[str | None] = mapped_column(String(5))
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    target_metric: Mapped[str | None] = mapped_column(String(100))
    source: Mapped[str] = mapped_column(String(10), server_default=text("'self'::character varying"), nullable=False)
    result_id: Mapped[int | None] = mapped_column(Integer)
    is_completed: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)


class CarePlanTemplate(Base):
    """care_plan_templates (19_care_plan_templates.sql)."""

    # Read-only for med_app. Templates are loaded and approved as the database owner.
    __tablename__ = "care_plan_templates"

    template_id: Mapped[int] = mapped_column(Integer, Identity(always=True), primary_key=True)
    disease_domain: Mapped[str] = mapped_column(String(5), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(10), nullable=False)
    version: Mapped[str] = mapped_column(String(10), nullable=False)
    plan_items: Mapped[Any] = mapped_column(JSONB, nullable=False)
    guideline_source: Mapped[str] = mapped_column(Text, nullable=False)
    disclaimer: Mapped[str] = mapped_column(Text, server_default=text("'This care plan is for educational screening only and is not a medical diagnosis. Please consult a healthcare provider for medical advice.'::text"), nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(150))
    reviewed_on: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)


class HealthReport(Base):
    """health_reports (20_health_reports.sql)."""

    # Immutable for med_app (no UPDATE). Insert, read, delete only.
    __tablename__ = "health_reports"
    __table_args__ = (
        ForeignKeyConstraint(["assessment_id", "patient_id"], ["risk_assessments.assessment_id", "risk_assessments.patient_id"], name="reports_assessment_fkey", ondelete="CASCADE"),
    )

    report_id: Mapped[int] = mapped_column(Integer, Identity(always=True), primary_key=True)
    patient_id: Mapped[int] = mapped_column(Integer, nullable=False)
    assessment_id: Mapped[int] = mapped_column(Integer, nullable=False)
    report_snapshot: Mapped[Any] = mapped_column(JSONB, nullable=False)
    templates_used: Mapped[Any] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)