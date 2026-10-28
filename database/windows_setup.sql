-- windows_setup.sql
-- Purpose: Sets up med_db on Windows without Docker (DBeaver or psql).
-- Replaces 00_roles.sh (bash, Docker only), then runs 01-21, 99_security and the draft
-- care plan templates (seed_care_plan_templates.sql), in the same order as tests/create_test_db.ps1.
-- Run ONCE on a NEW, EMPTY database named med_db, connected as postgres.
-- Keep in sync: when a script in database/ is added or changed, update this file in the same PR.
-- Dev password for med_app = CHANGE_ME (matches .env.example). Change both before any real deployment.

DO $$
BEGIN
    IF current_database() <> 'med_db' THEN
        RAISE EXCEPTION 'Wrong database: %. Connect to the empty med_db first.', current_database();
    END IF;
    IF EXISTS (SELECT 1 FROM pg_tables WHERE schemaname = 'public') THEN
        RAISE EXCEPTION 'med_db already has tables. This script is for a new, empty med_db only.';
    END IF;
END
$$;

-- ============ 00_roles (converted from 00_roles.sh) ============
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'med_app') THEN
        CREATE ROLE med_app LOGIN PASSWORD 'CHANGE_ME'
            NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
    END IF;
END
$$;

REVOKE CONNECT ON DATABASE med_db FROM PUBLIC;
GRANT CONNECT ON DATABASE med_db TO med_app;

REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO med_app;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO med_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO med_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

-- ============ 01_functions.sql ============
-- 01_functions.sql
-- Purpose: Shared helper functions used by the tables and security policies.

-- Keeps updated_at current on every UPDATE
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

-- Patient id of the current request.
-- FastAPI runs  SET LOCAL app.patient_id = '<id from JWT>'  at the start of each transaction.
-- Missing or empty setting returns NULL, so RLS policies match no rows (deny by default).
CREATE OR REPLACE FUNCTION public.current_patient_id()
RETURNS integer
LANGUAGE sql
STABLE
AS $$
    SELECT NULLIF(current_setting('app.patient_id', true), '')::integer;
$$;

-- User id of the current request (set the same way as app.user_id).
-- Used during registration, before a patient row exists.
CREATE OR REPLACE FUNCTION public.current_app_user_id()
RETURNS integer
LANGUAGE sql
STABLE
AS $$
    SELECT NULLIF(current_setting('app.user_id', true), '')::integer;
$$;

-- Blocks UPDATE, DELETE and TRUNCATE on append-only tables
CREATE OR REPLACE FUNCTION public.prevent_modification()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION '% is append-only: % is not allowed', TG_TABLE_NAME, TG_OP;
END;
$$;

-- ============ 02_users.sql ============
-- 02_users.sql
-- Table: users
-- Purpose: Login credentials, lockout tracking and token revocation.
-- Health data lives in separate tables (patients and below); this table holds no PHI.

CREATE TABLE IF NOT EXISTS public.users (
    user_id             integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email               character varying(254) NOT NULL,
    password_hash       text NOT NULL,                       -- Argon2id hash (pwdlib), never plaintext
    failed_attempts     smallint NOT NULL DEFAULT 0,
    locked_until        timestamptz,                         -- timed lockout after repeated failures
    token_version       integer NOT NULL DEFAULT 0,          -- increment to revoke all existing JWTs
    last_login_at       timestamptz,
    password_changed_at timestamptz NOT NULL DEFAULT now(),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT users_email_format_check CHECK (email ~* '^[^@\s]+@[^@\s]+\.[^@\s]+$'),
    CONSTRAINT users_failed_attempts_check CHECK (failed_attempts >= 0),
    CONSTRAINT users_password_hash_check CHECK (password_hash LIKE '$argon2id$%')
);

-- Case-insensitive unique email (Juan@Mail.com = juan@mail.com)
CREATE UNIQUE INDEX IF NOT EXISTS ux_users_email_lower ON public.users (lower(email));

CREATE TRIGGER trg_users_updated_at
    BEFORE UPDATE ON public.users
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- ============ 03_patients.sql ============
-- 03_patients.sql
-- Table: patients
-- Purpose: Minimal patient profile and RA 10173 consent record.
-- Data minimization: only fields the models or the PDF report need.
-- Removed from the old version: religion, gender, address, contact number,
-- emergency contacts and a duplicate email (already in users).

CREATE TABLE IF NOT EXISTS public.patients (
    patient_id      integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id         integer NOT NULL UNIQUE
                    REFERENCES public.users(user_id) ON DELETE CASCADE,   -- right to erasure
    first_name      character varying(100) NOT NULL,                       -- shown on the PDF report
    last_name       character varying(100) NOT NULL,
    date_of_birth   date NOT NULL,                                         -- age is computed, never stored
    biological_sex  character varying(6) NOT NULL,
    privacy_consent boolean NOT NULL,                                      -- RA 10173 consent
    consent_version character varying(20) NOT NULL,                        -- which privacy notice was accepted
    consent_at      timestamptz NOT NULL DEFAULT now(),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT patients_biological_sex_check CHECK (biological_sex IN ('Female', 'Male')),
    CONSTRAINT patients_consent_required_check CHECK (privacy_consent = true),
    CONSTRAINT patients_dob_check CHECK (date_of_birth >= DATE '1900-01-01')
    -- The app also validates that the patient is 18+ and the date is not in the future.
);

CREATE TRIGGER trg_patients_updated_at
    BEFORE UPDATE ON public.patients
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- ============ 04_password_reset_tokens.sql ============
-- 04_password_reset_tokens.sql
-- Table: password_reset_tokens
-- Purpose: Self-service password reset. Replaces the old doctor-issued invites table.
-- Only a SHA-256 hash of the token is stored; tokens are single-use and expire.

CREATE TABLE IF NOT EXISTS public.password_reset_tokens (
    token_id    integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     integer NOT NULL
                REFERENCES public.users(user_id) ON DELETE CASCADE,
    token_hash  character(64) NOT NULL UNIQUE,      -- sha256 hex of the token sent to the user
    expires_at  timestamptz NOT NULL,
    used_at     timestamptz,                         -- set once; a used token can never be reused
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT reset_tokens_hash_format_check CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT reset_tokens_expiry_check CHECK (expires_at > created_at
                                                AND expires_at <= created_at + interval '1 hour')
);

CREATE INDEX IF NOT EXISTS ix_reset_tokens_user ON public.password_reset_tokens (user_id);

-- ============ 05_audit_ledger.sql ============
-- 05_audit_ledger.sql
-- Table: audit_ledger
-- Purpose: Tamper-evident, append-only security log (logins, data changes, exports, deletions).
--
-- Zero Trust design:
--   * Hash chain: each entry_seal = sha256(previous seal + this entry), computed inside the
--     database, so a deleted or edited row breaks the chain and is detectable.
--   * The app (med_app) has NO direct access to this table. It can only call log_audit().
--   * UPDATE, DELETE and TRUNCATE are blocked by triggers, even for the owner.
--   * No foreign keys on user/patient ids: the history survives account deletion.
--   * payload holds ids and event details only - never health values.

CREATE TABLE IF NOT EXISTS public.audit_ledger (
    audit_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    occurred_at   timestamptz NOT NULL DEFAULT now(),
    event_type    character varying(40) NOT NULL,
    actor_user_id integer,                         -- no FK on purpose
    patient_id    integer,                         -- no FK on purpose
    payload       jsonb NOT NULL DEFAULT '{}'::jsonb,
    prev_seal     character(64),                   -- NULL only for the first entry
    entry_seal    character(64) NOT NULL UNIQUE,
    CONSTRAINT audit_event_type_check CHECK (event_type IN (
        'REGISTER', 'LOGIN_SUCCESS', 'LOGIN_FAILED', 'LOGOUT', 'ACCOUNT_LOCKED',
        'PASSWORD_CHANGED', 'PASSWORD_RESET_REQUESTED', 'TOKENS_REVOKED',
        'DATA_CREATED', 'DATA_UPDATED', 'DATA_DELETED',
        'DOCUMENT_UPLOADED', 'DOCUMENT_VERIFIED', 'DOCUMENT_REJECTED',
        'RISK_ASSESSMENT_RUN', 'REPORT_EXPORTED', 'DATA_EXPORTED', 'ACCOUNT_DELETED'
    )),
    CONSTRAINT audit_payload_object_check CHECK (jsonb_typeof(payload) = 'object')
);

CREATE INDEX IF NOT EXISTS ix_audit_occurred_at ON public.audit_ledger (occurred_at);
CREATE INDEX IF NOT EXISTS ix_audit_patient ON public.audit_ledger (patient_id, occurred_at);

-- Append-only enforcement
CREATE TRIGGER trg_audit_no_update_delete
    BEFORE UPDATE OR DELETE ON public.audit_ledger
    FOR EACH ROW EXECUTE FUNCTION public.prevent_modification();

CREATE TRIGGER trg_audit_no_truncate
    BEFORE TRUNCATE ON public.audit_ledger
    FOR EACH STATEMENT EXECUTE FUNCTION public.prevent_modification();

-- The text that gets hashed for one entry (UTC timestamp so the result never depends
-- on the session time zone; jsonb::text is already in a canonical form)
CREATE OR REPLACE FUNCTION public.audit_seal_input(
    p_prev_seal text, p_event_type text, p_actor integer, p_patient integer,
    p_payload jsonb, p_occurred_at timestamptz)
RETURNS bytea
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT convert_to(
        coalesce(p_prev_seal, 'GENESIS') || '|' ||
        p_event_type || '|' ||
        coalesce(p_actor::text, '') || '|' ||
        coalesce(p_patient::text, '') || '|' ||
        p_payload::text || '|' ||
        to_char(p_occurred_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'),
        'UTF8');
$$;

-- The only way the app can write an audit entry.
-- SECURITY DEFINER: runs with the owner's rights, so med_app needs no table privileges.
CREATE OR REPLACE FUNCTION public.log_audit(
    p_event_type text,
    p_actor_user_id integer,
    p_patient_id integer,
    p_payload jsonb DEFAULT '{}'::jsonb)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_prev  character(64);
    v_now   timestamptz := clock_timestamp();
    v_id    bigint;
BEGIN
    -- Serialize writers so the chain never forks
    PERFORM pg_advisory_xact_lock(hashtext('public.audit_ledger'));

    SELECT entry_seal INTO v_prev
    FROM public.audit_ledger
    ORDER BY audit_id DESC
    LIMIT 1;

    INSERT INTO public.audit_ledger
        (occurred_at, event_type, actor_user_id, patient_id, payload, prev_seal, entry_seal)
    VALUES
        (v_now, p_event_type, p_actor_user_id, p_patient_id, coalesce(p_payload, '{}'::jsonb), v_prev,
         encode(sha256(public.audit_seal_input(v_prev, p_event_type, p_actor_user_id,
                                               p_patient_id, coalesce(p_payload, '{}'::jsonb), v_now)), 'hex'))
    RETURNING audit_id INTO v_id;

    RETURN v_id;
END;
$$;

-- Admin check: returns the first audit_id where the chain is broken, or NULL if intact.
-- Not granted to med_app; run as the database owner:  SELECT public.verify_audit_chain();
CREATE OR REPLACE FUNCTION public.verify_audit_chain()
RETURNS bigint
LANGUAGE plpgsql
STABLE
SET search_path = public, pg_temp
AS $$
DECLARE
    r       record;
    v_prev  character(64) := NULL;
BEGIN
    FOR r IN SELECT * FROM public.audit_ledger ORDER BY audit_id LOOP
        IF r.prev_seal IS DISTINCT FROM v_prev
           OR r.entry_seal <> encode(sha256(public.audit_seal_input(
                  r.prev_seal, r.event_type, r.actor_user_id, r.patient_id,
                  r.payload, r.occurred_at)), 'hex') THEN
            RETURN r.audit_id;
        END IF;
        v_prev := r.entry_seal;
    END LOOP;
    RETURN NULL;
END;
$$;

-- ============ 06_documents.sql ============
-- 06_documents.sql
-- Table: documents
-- Purpose: Uploaded lab documents and their Chandra OCR job status.
-- The file itself is Fernet-encrypted on the Docker volume; only its storage key is stored here.

CREATE TABLE IF NOT EXISTS public.documents (
    document_id     integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_id      integer NOT NULL
                    REFERENCES public.patients(patient_id) ON DELETE CASCADE,
    storage_key     uuid NOT NULL UNIQUE DEFAULT gen_random_uuid(),  -- server-generated file name
                                                                     -- (never the uploaded name: blocks path traversal)
    mime_type       character varying(30) NOT NULL,
    file_size_bytes integer NOT NULL,
    file_sha256     character(64) NOT NULL,                          -- integrity check of the original file
    ocr_engine      character varying(50) NOT NULL DEFAULT 'Chandra OCR',
    ocr_text        text,                                            -- cleared after verification (data minimization)
    ocr_confidence  numeric(5,2),
    status          character varying(20) NOT NULL DEFAULT 'pending',
    error_message   character varying(255),                          -- OCR failure reason, no PHI
    uploaded_at     timestamptz NOT NULL DEFAULT now(),
    processed_at    timestamptz,
    verified_at     timestamptz,
    CONSTRAINT documents_mime_type_check
        CHECK (mime_type IN ('image/jpeg', 'image/png', 'application/pdf')),
    CONSTRAINT documents_file_size_check
        CHECK (file_size_bytes > 0 AND file_size_bytes <= 10485760),     -- 10 MB
    CONSTRAINT documents_sha256_format_check CHECK (file_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT documents_ocr_confidence_check
        CHECK (ocr_confidence IS NULL OR ocr_confidence BETWEEN 0 AND 100),
    CONSTRAINT documents_status_check
        CHECK (status IN ('pending', 'processing', 'ready_for_review', 'verified', 'rejected', 'failed')),
    CONSTRAINT documents_verified_check
        CHECK (status <> 'verified' OR verified_at IS NOT NULL),
    -- The same file uploaded twice by the same patient is rejected
    CONSTRAINT documents_patient_file_unique UNIQUE (patient_id, file_sha256),
    CONSTRAINT documents_id_patient_key UNIQUE (document_id, patient_id)  -- target of 08_lab_results FK
);

CREATE INDEX IF NOT EXISTS ix_documents_patient_uploaded ON public.documents (patient_id, uploaded_at DESC);
CREATE INDEX IF NOT EXISTS ix_documents_status ON public.documents (status)
    WHERE status IN ('pending', 'processing');                       -- the OCR worker's queue view

-- ============ 07_vitals_records.sql ============
-- 07_vitals_records.sql
-- Table: vitals_records
-- Purpose: Self-reported vitals, entered once and shared by all four models.
-- Replaces the sbp/dbp/bmi/resting_hr/spo2 columns that were duplicated across the
-- old CVD, T2D and respiratory tables.

CREATE TABLE IF NOT EXISTS public.vitals_records (
    vitals_id          integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_id         integer NOT NULL
                       REFERENCES public.patients(patient_id) ON DELETE CASCADE,
    sbp                smallint,                 -- systolic BP, mmHg
    dbp                smallint,                 -- diastolic BP, mmHg
    resting_hr         smallint,                 -- beats per minute
    weight_kg          numeric(5,1),
    height_cm          numeric(5,1),
    bmi                numeric(4,1) GENERATED ALWAYS AS
                       (round(weight_kg / ((height_cm / 100) * (height_cm / 100)), 1)) STORED,
    waist_cm           numeric(5,1),
    blood_glucose_mgdl numeric(5,1),             -- home glucometer reading
    glucose_context    character varying(15),    -- when the glucose reading was taken
    spo2               numeric(4,1),             -- %, home pulse oximeter
    measured_at        timestamptz NOT NULL DEFAULT now(),
    created_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT vitals_sbp_check        CHECK (sbp IS NULL OR sbp BETWEEN 70 AND 250),
    CONSTRAINT vitals_dbp_check        CHECK (dbp IS NULL OR dbp BETWEEN 40 AND 150),
    CONSTRAINT vitals_bp_pair_check    CHECK ((sbp IS NULL) = (dbp IS NULL)),
    CONSTRAINT vitals_bp_order_check   CHECK (sbp IS NULL OR sbp > dbp),
    CONSTRAINT vitals_hr_check         CHECK (resting_hr IS NULL OR resting_hr BETWEEN 30 AND 220),
    CONSTRAINT vitals_weight_check     CHECK (weight_kg IS NULL OR weight_kg BETWEEN 20 AND 300),
    CONSTRAINT vitals_height_check     CHECK (height_cm IS NULL OR height_cm BETWEEN 100 AND 250),
    CONSTRAINT vitals_waist_check      CHECK (waist_cm IS NULL OR waist_cm BETWEEN 40 AND 200),
    CONSTRAINT vitals_glucose_check    CHECK (blood_glucose_mgdl IS NULL OR blood_glucose_mgdl BETWEEN 20 AND 600),
    CONSTRAINT vitals_glucose_context_check
        CHECK (glucose_context IS NULL OR glucose_context IN ('fasting', 'random', 'post_meal_2h')),
    CONSTRAINT vitals_glucose_pair_check CHECK ((blood_glucose_mgdl IS NULL) = (glucose_context IS NULL)),
    CONSTRAINT vitals_spo2_check       CHECK (spo2 IS NULL OR spo2 BETWEEN 70 AND 100),
    CONSTRAINT vitals_not_empty_check  CHECK (num_nonnulls(sbp, resting_hr, weight_kg, height_cm,
                                                           waist_cm, blood_glucose_mgdl, spo2) >= 1)
);

CREATE INDEX IF NOT EXISTS ix_vitals_patient_measured ON public.vitals_records (patient_id, measured_at DESC);

-- ============ 08_lab_results.sql ============
-- 08_lab_results.sql
-- Table: lab_results
-- Purpose: One row per lab value, from OCR or manual entry.
-- Patient-in-the-loop: the models only use rows where is_confirmed = true.
-- Replaces the NOT NULL lab columns of the old metric tables, so patients without
-- lab results can still use the app (XGBoost handles missing values).
-- Removed (clinic or hospital-only tests): LVEF, troponin, NT-proBNP, arterial blood gas,
-- FeNO, spirometry, fasting insulin, C-peptide.

CREATE TABLE IF NOT EXISTS public.lab_results (
    lab_result_id     integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_id        integer NOT NULL
                      REFERENCES public.patients(patient_id) ON DELETE CASCADE,
    document_id       integer,                       -- NULL = manual entry
    test_code         character varying(20) NOT NULL,
    value             numeric(10,3) NOT NULL,        -- in the standard unit below
    unit              character varying(15) NOT NULL,
    value_as_reported numeric(10,3),                 -- what the document said, before conversion
    unit_as_reported  character varying(15),         -- e.g. mmol/L on many Philippine lab reports
    source            character varying(10) NOT NULL,
    is_confirmed      boolean NOT NULL DEFAULT false,
    confirmed_at      timestamptz,
    collected_on      date,                          -- date the sample was taken
    created_at        timestamptz NOT NULL DEFAULT now(),
    -- Composite FK: the document must belong to the same patient (FK checks skip RLS,
    -- so a plain document_id FK would let a patient link another patient's document)
    CONSTRAINT lab_document_fkey FOREIGN KEY (document_id, patient_id)
        REFERENCES public.documents (document_id, patient_id) ON DELETE SET NULL (document_id),
    CONSTRAINT lab_test_code_check CHECK (test_code IN (
        'TOTAL_CHOLESTEROL', 'HDL', 'LDL', 'TRIGLYCERIDES',   -- lipid panel
        'FPG', 'HBA1C', 'OGTT_2H',                            -- glycemic
        'CREATININE', 'EGFR', 'URIC_ACID', 'UACR',            -- kidney / metabolic
        'HS_CRP',                                             -- inflammation
        'EOSINOPHILS', 'IGE',                                 -- allergy / respiratory
        'TSH', 'VITAMIN_B12', 'FOLATE'                        -- neuro / mental health
    )),
    -- Each test is stored in one fixed unit, so the models always get comparable values
    CONSTRAINT lab_unit_check CHECK (unit = CASE test_code
        WHEN 'TOTAL_CHOLESTEROL' THEN 'mg/dL'  WHEN 'HDL'        THEN 'mg/dL'
        WHEN 'LDL'               THEN 'mg/dL'  WHEN 'TRIGLYCERIDES' THEN 'mg/dL'
        WHEN 'FPG'               THEN 'mg/dL'  WHEN 'OGTT_2H'    THEN 'mg/dL'
        WHEN 'HBA1C'             THEN '%'      WHEN 'CREATININE' THEN 'mg/dL'
        WHEN 'EGFR'              THEN 'mL/min/1.73m2'
        WHEN 'URIC_ACID'         THEN 'mg/dL'  WHEN 'UACR'       THEN 'mg/g'
        WHEN 'HS_CRP'            THEN 'mg/L'   WHEN 'EOSINOPHILS' THEN 'cells/uL'
        WHEN 'IGE'               THEN 'IU/mL'  WHEN 'TSH'        THEN 'mIU/L'
        WHEN 'VITAMIN_B12'       THEN 'pg/mL'  WHEN 'FOLATE'     THEN 'ng/mL'
    END),
    CONSTRAINT lab_value_check CHECK (value > 0 AND value < 100000),
    CONSTRAINT lab_source_check CHECK (source IN ('ocr', 'manual')),
    CONSTRAINT lab_confirmed_check CHECK (is_confirmed = (confirmed_at IS NOT NULL)),
    CONSTRAINT lab_reported_pair_check CHECK ((value_as_reported IS NULL) = (unit_as_reported IS NULL))
);

CREATE INDEX IF NOT EXISTS ix_lab_patient_test ON public.lab_results (patient_id, test_code, collected_on DESC);
CREATE INDEX IF NOT EXISTS ix_lab_document ON public.lab_results (document_id);

-- ============ 09_lifestyle_records.sql ============
-- 09_lifestyle_records.sql
-- Table: lifestyle_records
-- Purpose: Self-reported lifestyle factors, shared by all four models.
-- Merges smoking_history (old CVD table) and tobacco_exposure (old respiratory table),
-- plus activity, sleep and diet fields from the old metric tables.

CREATE TABLE IF NOT EXISTS public.lifestyle_records (
    lifestyle_id               integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_id                 integer NOT NULL
                               REFERENCES public.patients(patient_id) ON DELETE CASCADE,
    smoking_status             character varying(10),
    pack_years                 numeric(5,1),         -- packs per day x years smoked
    physical_activity_min_week smallint,             -- moderate-to-vigorous minutes per week
    sedentary_hours_day        numeric(3,1),
    sleep_hours_day            numeric(3,1),
    sodium_mg_day              smallint,             -- estimated daily intake
    carbs_g_day                smallint,             -- estimated daily intake
    occupational_dust          boolean,              -- regular dust, fume or chemical exposure at work
    substance_use              character varying(10),
    recorded_at                timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT lifestyle_smoking_check
        CHECK (smoking_status IS NULL OR smoking_status IN ('never', 'former', 'current')),
    CONSTRAINT lifestyle_pack_years_check
        CHECK (pack_years IS NULL OR pack_years BETWEEN 0 AND 200),
    CONSTRAINT lifestyle_never_smoker_check
        CHECK (smoking_status IS DISTINCT FROM 'never' OR coalesce(pack_years, 0) = 0),
    CONSTRAINT lifestyle_activity_check
        CHECK (physical_activity_min_week IS NULL OR physical_activity_min_week BETWEEN 0 AND 10080),
    CONSTRAINT lifestyle_sedentary_check
        CHECK (sedentary_hours_day IS NULL OR sedentary_hours_day BETWEEN 0 AND 24),
    CONSTRAINT lifestyle_sleep_check
        CHECK (sleep_hours_day IS NULL OR sleep_hours_day BETWEEN 0 AND 24),
    CONSTRAINT lifestyle_sodium_check
        CHECK (sodium_mg_day IS NULL OR sodium_mg_day BETWEEN 0 AND 20000),
    CONSTRAINT lifestyle_carbs_check
        CHECK (carbs_g_day IS NULL OR carbs_g_day BETWEEN 0 AND 1500),
    CONSTRAINT lifestyle_substance_check
        CHECK (substance_use IS NULL OR substance_use IN ('none', 'past', 'current'))
);

CREATE INDEX IF NOT EXISTS ix_lifestyle_patient_recorded ON public.lifestyle_records (patient_id, recorded_at DESC);

-- ============ 10_health_history.sql ============
-- 10_health_history.sql
-- Table: patient_health_history
-- Purpose: The patient's own past conditions that rarely change (one row per patient).
-- Collects the yes/no history flags from the old T2D, respiratory and neuro tables.
-- NULL = the patient doesn't know or hasn't answered.
-- Removed as leakage (they describe existing disease, not risk): annual_exacerbations,
-- seizure_frequency.

CREATE TABLE IF NOT EXISTS public.patient_health_history (
    patient_id           integer PRIMARY KEY
                         REFERENCES public.patients(patient_id) ON DELETE CASCADE,
    gestational_diabetes boolean,     -- female patients only (validated in the app)
    acanthosis_nigricans boolean,     -- dark, velvety skin patches (shown with a picture in the app)
    allergic_rhinitis    boolean,
    childhood_asthma     boolean,
    tbi_history          boolean,     -- past traumatic brain injury
    updated_at           timestamptz NOT NULL DEFAULT now()
);

CREATE TRIGGER trg_health_history_updated_at
    BEFORE UPDATE ON public.patient_health_history
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- ============ 11_questionnaire_responses.sql ============
-- 11_questionnaire_responses.sql
-- Table: questionnaire_responses
-- Purpose: Self-administered screening questionnaires.
--   PHQ-9 (depression), GAD-7 (anxiety), PSQI (sleep quality)       -> NEURO model
--   mMRC (breathlessness), CAT (COPD symptoms), ACT (asthma control) -> CRD model
-- Removed (clinician-administered, not possible in a patient-only app):
-- MMSE, MoCA, HAM-D, UPDRS part III, tremor rating, FAQ.
--
-- SAFETY: PHQ-9 item 9 asks about thoughts of self-harm. requires_crisis_prompt is set
-- automatically when item 9 is answered above 0. The app must then immediately show crisis
-- resources (e.g., NCMH Crisis Hotline 1553), because no clinician reviews the results.

CREATE TABLE IF NOT EXISTS public.questionnaire_responses (
    response_id           integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_id            integer NOT NULL
                          REFERENCES public.patients(patient_id) ON DELETE CASCADE,
    instrument            character varying(5) NOT NULL,
    item_responses        jsonb NOT NULL,          -- array of item answers, in question order
    total_score           smallint NOT NULL,
    requires_crisis_prompt boolean GENERATED ALWAYS AS (
        instrument = 'PHQ9' AND coalesce((item_responses ->> 8)::integer, 0) >= 1
    ) STORED,
    completed_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT questionnaire_instrument_check
        CHECK (instrument IN ('PHQ9', 'GAD7', 'PSQI', 'MMRC', 'CAT', 'ACT')),
    CONSTRAINT questionnaire_items_array_check
        CHECK (jsonb_typeof(item_responses) = 'array'),
    -- Valid score range (and item count where fixed) for each instrument
    CONSTRAINT questionnaire_score_check CHECK (CASE instrument
        WHEN 'PHQ9' THEN total_score BETWEEN 0 AND 27 AND jsonb_array_length(item_responses) = 9
        WHEN 'GAD7' THEN total_score BETWEEN 0 AND 21 AND jsonb_array_length(item_responses) = 7
        WHEN 'PSQI' THEN total_score BETWEEN 0 AND 21
        WHEN 'MMRC' THEN total_score BETWEEN 0 AND 4  AND jsonb_array_length(item_responses) = 1
        WHEN 'CAT'  THEN total_score BETWEEN 0 AND 40 AND jsonb_array_length(item_responses) = 8
        WHEN 'ACT'  THEN total_score BETWEEN 5 AND 25 AND jsonb_array_length(item_responses) = 5
    END)
);

CREATE INDEX IF NOT EXISTS ix_questionnaire_patient ON public.questionnaire_responses
    (patient_id, instrument, completed_at DESC);

-- ============ 12_family_history.sql ============
-- 12_family_history.sql
-- Table: patient_family_history
-- Purpose: Family pedigree - one row per relative x condition.
-- relationship_degree is derived from the relative automatically, so it can never be wrong.
-- Relationship and onset weights are applied in the FHRS calculation (13_fhrs_stratification).

CREATE TABLE IF NOT EXISTS public.patient_family_history (
    record_id            integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_id           integer NOT NULL
                         REFERENCES public.patients(patient_id) ON DELETE CASCADE,
    disease_domain       character varying(5) NOT NULL,
    specific_diagnosis   character varying(150) NOT NULL,    -- e.g. 'Hypertension', 'Type 2 diabetes'
    relative             character varying(25) NOT NULL,
    relationship_degree  smallint GENERATED ALWAYS AS (CASE
        WHEN relative IN ('father', 'mother', 'brother', 'sister', 'son', 'daughter') THEN 1
        WHEN relative IN ('paternal_grandfather', 'paternal_grandmother',
                          'maternal_grandfather', 'maternal_grandmother',
                          'paternal_uncle', 'paternal_aunt', 'maternal_uncle', 'maternal_aunt',
                          'half_brother', 'half_sister') THEN 2
        ELSE 3                                                -- cousins
    END) STORED,
    age_at_diagnosis     smallint,
    onset_classification character varying(10) NOT NULL DEFAULT 'unknown',
    shared_environment   boolean NOT NULL DEFAULT false,     -- lived in the same household
    recorded_at          timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT family_domain_check CHECK (disease_domain IN ('CVD', 'T2D', 'CRD', 'NEURO')),
    CONSTRAINT family_relative_check CHECK (relative IN (
        'father', 'mother', 'brother', 'sister', 'son', 'daughter',
        'paternal_grandfather', 'paternal_grandmother', 'maternal_grandfather', 'maternal_grandmother',
        'paternal_uncle', 'paternal_aunt', 'maternal_uncle', 'maternal_aunt',
        'half_brother', 'half_sister', 'paternal_cousin', 'maternal_cousin'
    )),
    CONSTRAINT family_age_check CHECK (age_at_diagnosis IS NULL OR age_at_diagnosis BETWEEN 0 AND 120),
    CONSTRAINT family_onset_check CHECK (onset_classification IN ('early', 'late', 'unknown')),
    -- The same relative can't be entered twice for the same diagnosis
    CONSTRAINT family_unique_entry UNIQUE (patient_id, relative, disease_domain, specific_diagnosis)
);

CREATE INDEX IF NOT EXISTS ix_family_patient_domain ON public.patient_family_history (patient_id, disease_domain);

CREATE TRIGGER trg_family_history_updated_at
    BEFORE UPDATE ON public.patient_family_history
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- ============ 13_fhrs_stratification.sql ============
-- 13_fhrs_stratification.sql
-- Table: patient_fhrs_stratification
-- Purpose: Family History Risk Score (FHRS) per disease, calculated from the pedigree.
-- Feeds the models as a feature. One row per calculation, so changes stay traceable.

CREATE TABLE IF NOT EXISTS public.patient_fhrs_stratification (
    fhrs_id                integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_id             integer NOT NULL
                           REFERENCES public.patients(patient_id) ON DELETE CASCADE,
    disease_domain         character varying(5) NOT NULL,
    affected_relatives     smallint NOT NULL,        -- how many relatives counted
    clustering_score       numeric(6,3) NOT NULL,
    environmental_modifier numeric(4,3) NOT NULL,
    calculated_fhrs        numeric(6,3) NOT NULL,
    risk_band              character varying(10) NOT NULL,
    evaluated_at           timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fhrs_domain_check CHECK (disease_domain IN ('CVD', 'T2D', 'CRD', 'NEURO')),
    CONSTRAINT fhrs_relatives_check CHECK (affected_relatives >= 0),
    CONSTRAINT fhrs_scores_check CHECK (clustering_score >= 0 AND calculated_fhrs >= 0),
    CONSTRAINT fhrs_modifier_check CHECK (environmental_modifier BETWEEN 0 AND 5),
    CONSTRAINT fhrs_band_check CHECK (risk_band IN ('Low', 'Moderate', 'High'))
);

CREATE INDEX IF NOT EXISTS ix_fhrs_patient_domain ON public.patient_fhrs_stratification
    (patient_id, disease_domain, evaluated_at DESC);

-- ============ 14_model_versions.sql ============
-- 14_model_versions.sql
-- Table: model_versions
-- Purpose: Registry of the XGBoost model files the app is allowed to load.
-- Zero Trust: the app can only READ this table (see 99_security.sql), so it can never swap
-- in a different model. The app must recompute the file's sha256 at load time and refuse
-- to start if it doesn't match artifact_sha256.
-- Register new models as the database owner, never through the app.

CREATE TABLE IF NOT EXISTS public.model_versions (
    model_version_id   integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    disease_domain     character varying(5) NOT NULL,
    version            character varying(30) NOT NULL,
    algorithm          character varying(30) NOT NULL DEFAULT 'XGBoost',
    artifact_path      text NOT NULL,                  -- e.g. ml/artifacts/cvd_v1.json
    artifact_sha256    character(64) NOT NULL,
    feature_list       jsonb NOT NULL,                 -- ordered feature names the model expects
    thresholds         jsonb NOT NULL,                 -- risk tier cutoffs, e.g. {"moderate":0.10,"high":0.20}
    calibration_method character varying(20),          -- e.g. 'isotonic', 'sigmoid'
    metrics            jsonb,                          -- AUROC, AUPRC, Brier, ECE from Chapter 4
    trained_at         timestamptz,
    is_active          boolean NOT NULL DEFAULT false,
    created_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT model_domain_check CHECK (disease_domain IN ('CVD', 'T2D', 'CRD', 'NEURO')),
    CONSTRAINT model_sha256_format_check CHECK (artifact_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT model_feature_list_check CHECK (jsonb_typeof(feature_list) = 'array'
                                               AND jsonb_array_length(feature_list) > 0),
    CONSTRAINT model_thresholds_check CHECK (
        thresholds ? 'moderate' AND thresholds ? 'high'
        AND (thresholds ->> 'moderate')::numeric > 0
        AND (thresholds ->> 'moderate')::numeric < (thresholds ->> 'high')::numeric
        AND (thresholds ->> 'high')::numeric < 1),
    CONSTRAINT model_domain_version_key UNIQUE (disease_domain, version),
    CONSTRAINT model_id_domain_key UNIQUE (model_version_id, disease_domain)  -- target of 16_risk_results FK
);

-- Exactly one active model per disease
CREATE UNIQUE INDEX IF NOT EXISTS ux_model_one_active ON public.model_versions (disease_domain) WHERE is_active;

-- ============ 15_risk_assessments.sql ============
-- 15_risk_assessments.sql
-- Table: risk_assessments
-- Purpose: One row per screening run (the patient presses "Run assessment").
-- Groups the four disease results and keeps an exact copy of the inputs used,
-- so every result can be reproduced and explained later.
-- Replaces the old consultations table as the parent of risk_results.

CREATE TABLE IF NOT EXISTS public.risk_assessments (
    assessment_id  integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_id     integer NOT NULL
                   REFERENCES public.patients(patient_id) ON DELETE CASCADE,
    input_snapshot jsonb NOT NULL,     -- feature values per model, e.g. {"CVD": {...}, "T2D": {...}}
    created_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT assessment_snapshot_check CHECK (jsonb_typeof(input_snapshot) = 'object'),
    CONSTRAINT assessment_id_patient_key UNIQUE (assessment_id, patient_id)  -- target of 16_risk_results FK
);

CREATE INDEX IF NOT EXISTS ix_assessments_patient_created ON public.risk_assessments (patient_id, created_at DESC);

-- ============ 16_risk_results.sql ============
-- 16_risk_results.sql
-- Table: risk_results
-- Purpose: The four disease risk probabilities and tiers for each assessment.
-- Results are immutable once written (UPDATE revoked in 99_security.sql).
-- Composite foreign keys guarantee that:
--   * patient_id always matches the assessment's patient (no cross-patient mix-ups)
--   * the model used belongs to the same disease as the result
-- Removed: is_released (no doctor releases results; the patient sees them directly).

CREATE TABLE IF NOT EXISTS public.risk_results (
    result_id        integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    assessment_id    integer NOT NULL,
    patient_id       integer NOT NULL,
    model_version_id integer NOT NULL,
    disease_domain   character varying(5) NOT NULL,
    risk_probability numeric(5,4) NOT NULL,
    risk_level       character varying(10) NOT NULL,
    shap_base_value  numeric(8,5),                 -- SHAP expected value (log-odds)
    created_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT results_assessment_fkey FOREIGN KEY (assessment_id, patient_id)
        REFERENCES public.risk_assessments (assessment_id, patient_id) ON DELETE CASCADE,
    CONSTRAINT results_model_fkey FOREIGN KEY (model_version_id, disease_domain)
        REFERENCES public.model_versions (model_version_id, disease_domain) ON DELETE RESTRICT,
    CONSTRAINT results_domain_check CHECK (disease_domain IN ('CVD', 'T2D', 'CRD', 'NEURO')),
    CONSTRAINT results_level_check CHECK (risk_level IN ('Low', 'Moderate', 'High')),
    CONSTRAINT results_probability_check CHECK (risk_probability BETWEEN 0 AND 1),
    CONSTRAINT results_assessment_domain_key UNIQUE (assessment_id, disease_domain),
    CONSTRAINT results_id_patient_key UNIQUE (result_id, patient_id)  -- target of 17_shap_explanations FK
);

CREATE INDEX IF NOT EXISTS ix_results_patient ON public.risk_results (patient_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_results_model ON public.risk_results (model_version_id);

-- ============ 17_shap_explanations.sql ============
-- 17_shap_explanations.sql
-- Table: shap_explanations
-- Purpose: Per-feature SHAP contributions for each risk result, ranked by impact.
-- The app turns the top ranks into plain-language sentences for the patient
-- (e.g., "Your family history of diabetes increased your risk the most").

CREATE TABLE IF NOT EXISTS public.shap_explanations (
    explanation_id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    result_id      integer NOT NULL,
    patient_id     integer NOT NULL,
    feature_name   character varying(100) NOT NULL,
    feature_value  numeric,                   -- NULL when the input was missing
    shap_value     numeric(8,5) NOT NULL,     -- positive = raised risk, negative = lowered risk
    rank           smallint NOT NULL,         -- 1 = largest absolute impact
    CONSTRAINT shap_result_fkey FOREIGN KEY (result_id, patient_id)
        REFERENCES public.risk_results (result_id, patient_id) ON DELETE CASCADE,
    CONSTRAINT shap_result_feature_key UNIQUE (result_id, feature_name),
    CONSTRAINT shap_result_rank_key UNIQUE (result_id, rank),
    CONSTRAINT shap_rank_check CHECK (rank >= 1)
);

-- ============ 18_patient_goals.sql ============
-- 18_patient_goals.sql
-- Table: patient_goals
-- Purpose: Preventive goals the patient sets for themselves, or accepts from the app's
-- suggestions after an assessment (e.g., "Walk 150 minutes a week").
-- Replaces the old doctor-prescribed care_plan_tasks.

CREATE TABLE IF NOT EXISTS public.patient_goals (
    goal_id        integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_id     integer NOT NULL
                   REFERENCES public.patients(patient_id) ON DELETE CASCADE,
    disease_domain character varying(5),               -- NULL = general wellness goal
    description    character varying(255) NOT NULL,
    target_metric  character varying(100),             -- e.g. 'physical_activity_min_week >= 150'
    source         character varying(10) NOT NULL DEFAULT 'self',
    result_id      integer,                            -- the result that prompted it
    is_completed   boolean NOT NULL DEFAULT false,
    completed_at   timestamptz,
    created_at     timestamptz NOT NULL DEFAULT now(),
    -- Composite FK: the result must belong to the same patient
    CONSTRAINT goals_result_fkey FOREIGN KEY (result_id, patient_id)
        REFERENCES public.risk_results (result_id, patient_id) ON DELETE SET NULL (result_id),
    CONSTRAINT goals_domain_check
        CHECK (disease_domain IS NULL OR disease_domain IN ('CVD', 'T2D', 'CRD', 'NEURO')),
    CONSTRAINT goals_source_check CHECK (source IN ('self', 'suggested')),
    CONSTRAINT goals_completed_check CHECK (is_completed = (completed_at IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS ix_goals_patient ON public.patient_goals (patient_id, is_completed);

-- ============ 19_care_plan_templates.sql ============
-- 19_care_plan_templates.sql
-- Table: care_plan_templates
-- Purpose: The 12 physician-reviewed care plan templates (4 diseases x 3 risk tiers).
-- After each assessment the app picks the one active template for each disease and tier.
-- The AI never writes or changes the advice.
--
-- Zero Trust:
--   * The app can only READ this table (see 99_security.sql). Templates are loaded as the
--     database owner (seed_care_plan_templates.sql), never through the app.
--   * Only a reviewed template can be active (reviewer name and review date required).
--   * A reviewed template is read-only, even for the owner: a change is saved as a new
--     version and reviewed again. Old versions stay, so saved reports can still point to them.
--
-- plan_items shape (one JSON object per template):
--   {"base":   [{"no": 1, "category": "...", "action": "...", "why": "...",
--                "source": "...", "basis": "...", "goal": true}, ...],
--    "addons": [{"code": "A1", "trigger_key": "current_smoker", "trigger": "Current smoker",
--                "action": "...", "why": "...", "source": "...", "basis": "...", "goal": true}, ...]}
-- The app shows an add-on only when its trigger_key is true in the patient's saved data.

-- Blocks edits and deletion of a template once it has been reviewed.
-- is_active may still change, so an old version can be switched off when a new one is approved.
CREATE OR REPLACE FUNCTION public.protect_reviewed_template()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.reviewed_on IS NOT NULL THEN
            RAISE EXCEPTION 'Reviewed care plan template % % v% cannot be deleted; set is_active = false instead',
                OLD.disease_domain, OLD.risk_level, OLD.version;
        END IF;
        RETURN OLD;
    END IF;

    IF OLD.reviewed_on IS NOT NULL
       AND (NEW.disease_domain, NEW.risk_level, NEW.version, NEW.plan_items,
            NEW.guideline_source, NEW.disclaimer, NEW.reviewed_by, NEW.reviewed_on)
           IS DISTINCT FROM
           (OLD.disease_domain, OLD.risk_level, OLD.version, OLD.plan_items,
            OLD.guideline_source, OLD.disclaimer, OLD.reviewed_by, OLD.reviewed_on) THEN
        RAISE EXCEPTION 'Reviewed care plan template % % v% is read-only; save the change as a new version',
            OLD.disease_domain, OLD.risk_level, OLD.version;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TABLE IF NOT EXISTS public.care_plan_templates (
    template_id      integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    disease_domain   character varying(5) NOT NULL,
    risk_level       character varying(10) NOT NULL,
    version          character varying(10) NOT NULL,       -- e.g. '1.0', '1.1'
    plan_items       jsonb NOT NULL,                        -- base and add-on items, in display order
    guideline_source text NOT NULL,                         -- main guidelines the template follows
    disclaimer       text NOT NULL DEFAULT
        'This care plan is for educational screening only and is not a medical diagnosis. '
        'Please consult a healthcare provider for medical advice.',
    reviewed_by      character varying(150),                -- physician name and specialty
    reviewed_on      date,
    is_active        boolean NOT NULL DEFAULT false,
    created_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT template_domain_check CHECK (disease_domain IN ('CVD', 'T2D', 'CRD', 'NEURO')),
    CONSTRAINT template_level_check CHECK (risk_level IN ('Low', 'Moderate', 'High')),  -- same as risk_results
    CONSTRAINT template_version_check CHECK (version ~ '^[0-9]+\.[0-9]+$'),
    -- Reviewer name and date are recorded together
    CONSTRAINT template_review_pair_check CHECK ((reviewed_by IS NULL) = (reviewed_on IS NULL)),
    -- "Doctor-validated": an unreviewed draft can never be shown to patients
    CONSTRAINT template_active_reviewed_check CHECK (NOT is_active OR reviewed_on IS NOT NULL),
    CONSTRAINT template_items_shape_check CHECK (
        jsonb_typeof(plan_items) = 'object'
        AND jsonb_typeof(plan_items -> 'base') = 'array'
        AND jsonb_array_length(plan_items -> 'base') > 0
        AND jsonb_typeof(plan_items -> 'addons') = 'array'),
    -- Rule from the care plan: every template lists urgent warning signs
    CONSTRAINT template_warning_signs_check CHECK (
        plan_items -> 'base' @> '[{"category": "Urgent warning signs"}]'),
    -- Rule from the care plan: every Mental Health template carries the NCMH Crisis Hotline (1553)
    CONSTRAINT template_neuro_crisis_check CHECK (
        disease_domain <> 'NEURO'
        OR (plan_items -> 'base' @> '[{"category": "Safety"}]'
            AND position('1553' IN (plan_items -> 'base')::text) > 0)),
    CONSTRAINT template_domain_level_version_key UNIQUE (disease_domain, risk_level, version)
);

-- Exactly one active template per disease and tier
CREATE UNIQUE INDEX IF NOT EXISTS ux_template_one_active
    ON public.care_plan_templates (disease_domain, risk_level) WHERE is_active;

CREATE OR REPLACE TRIGGER trg_template_protect_reviewed
    BEFORE UPDATE OR DELETE ON public.care_plan_templates
    FOR EACH ROW EXECUTE FUNCTION public.protect_reviewed_template();

-- ============ 20_health_reports.sql ============
-- 20_health_reports.sql
-- Table: health_reports
-- Purpose: A saved health report: one frozen snapshot of an assessment's results, top SHAP
-- factors and the care plan items the patient was shown. The PDF and JSON exports are built
-- from this row, so an export always matches what the patient saw.
--
-- Zero Trust:
--   * Patient-owned: Row-Level Security limits each request to its own patient (99_security.sql).
--   * Read-only once saved: UPDATE is revoked from the app (99_security.sql). A report may only
--     be deleted, by the patient or on account deletion.
--   * The composite foreign key means a report can only point to the same patient's assessment.
--
-- report_snapshot example:
--   {"CVD": {"risk_probability": 0.31, "risk_level": "Moderate",
--            "top_factors": ["Your family history of heart disease raised your risk the most", ...],
--            "care_plan": {"base": [...], "addons": [...]}, "disclaimer": "..."},
--    "T2D": {...}, "CRD": {...}, "NEURO": {...}}
-- templates_used example (proves which advice version was shown):
--   {"CVD": {"template_id": 2, "version": "1.0"}, "T2D": {...}, "CRD": {...}, "NEURO": {...}}

CREATE TABLE IF NOT EXISTS public.health_reports (
    report_id       integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_id      integer NOT NULL,
    assessment_id   integer NOT NULL,
    report_snapshot jsonb NOT NULL,
    templates_used  jsonb NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    -- Composite FK: the assessment must belong to the same patient
    CONSTRAINT reports_assessment_fkey FOREIGN KEY (assessment_id, patient_id)
        REFERENCES public.risk_assessments (assessment_id, patient_id) ON DELETE CASCADE,
    CONSTRAINT reports_snapshot_check CHECK (jsonb_typeof(report_snapshot) = 'object'
                                             AND report_snapshot <> '{}'::jsonb),
    CONSTRAINT reports_templates_check CHECK (jsonb_typeof(templates_used) = 'object'
                                              AND templates_used <> '{}'::jsonb),
    -- One saved report per assessment, so a double click cannot save it twice
    CONSTRAINT reports_assessment_key UNIQUE (assessment_id)
);

CREATE INDEX IF NOT EXISTS ix_reports_patient_created ON public.health_reports (patient_id, created_at DESC);

-- ============ 21_famhistory.sql ============
-- 21_family_history_fhrs.sql
-- Purpose: Aligns the family history tables with the FHRS guide.
-- Run on an existing med_db as postgres. RLS from 99_security.sql still applies:
-- these statements only change columns and constraints, not the policies.
--
-- Sources:
--   Relative degrees: Thompson & Thompson, Ch. 7 (Pedigrees, Fig. 7.3) - second degree includes
--     grandchildren, nephews and nieces, and half-sibs; degree depends on the number of
--     steps in the pedigree (so great-grandparents are third degree).
--   CVD onset is sex-specific: Harrison's, premature IHD = <55 in male and <65 in female
--     first-degree relatives. This is why every relative now has a sex.

-- ---------------------------------------------------------------------------
-- 1. Relatives: add grandchildren, nieces, nephews, great-grandparents;
--    split cousins by sex
-- ---------------------------------------------------------------------------
ALTER TABLE public.patient_family_history
    DROP CONSTRAINT family_relative_check,
    DROP CONSTRAINT family_unique_entry,
    DROP COLUMN relationship_degree;

ALTER TABLE public.patient_family_history
    ADD CONSTRAINT family_relative_check CHECK (relative IN (
        -- 1st degree
        'father', 'mother', 'brother', 'sister', 'son', 'daughter',
        -- 2nd degree
        'paternal_grandfather', 'paternal_grandmother', 'maternal_grandfather', 'maternal_grandmother',
        'paternal_uncle', 'paternal_aunt', 'maternal_uncle', 'maternal_aunt',
        'half_brother', 'half_sister', 'grandson', 'granddaughter', 'nephew', 'niece',
        -- 3rd degree
        'paternal_male_cousin', 'paternal_female_cousin', 'maternal_male_cousin', 'maternal_female_cousin',
        'great_grandfather', 'great_grandmother'
    )),
    -- Relationship degree, derived automatically (Ri = 0.50 / 0.25 / 0.125 in the FHRS service)
    ADD COLUMN relationship_degree smallint GENERATED ALWAYS AS (CASE
        WHEN relative IN ('father', 'mother', 'brother', 'sister', 'son', 'daughter') THEN 1
        WHEN relative IN ('paternal_grandfather', 'paternal_grandmother',
                          'maternal_grandfather', 'maternal_grandmother',
                          'paternal_uncle', 'paternal_aunt', 'maternal_uncle', 'maternal_aunt',
                          'half_brother', 'half_sister', 'grandson', 'granddaughter',
                          'nephew', 'niece') THEN 2
        ELSE 3
    END) STORED,
    -- Relative's sex, derived automatically (needed for the sex-specific CVD onset cutoffs)
    ADD COLUMN relative_sex character varying(6) GENERATED ALWAYS AS (CASE
        WHEN relative IN ('father', 'brother', 'son', 'paternal_grandfather', 'maternal_grandfather',
                          'paternal_uncle', 'maternal_uncle', 'half_brother', 'grandson', 'nephew',
                          'paternal_male_cousin', 'maternal_male_cousin', 'great_grandfather') THEN 'Male'
        ELSE 'Female'
    END) STORED,
    ADD CONSTRAINT family_unique_entry UNIQUE (patient_id, relative, disease_domain, specific_diagnosis);

-- ---------------------------------------------------------------------------
-- 2. Onset: four levels instead of two (Oi = 3.0 / 2.0 / 1.0 / 0.75)
--    When age_at_diagnosis is known, the FHRS service sets the level from the onset
--    cutoffs in the data dictionary. When it is unknown, the patient may choose a level,
--    or leave it as 'unknown' (scored as typical onset, Oi = 1.0).
-- ---------------------------------------------------------------------------
ALTER TABLE public.patient_family_history
    DROP CONSTRAINT family_onset_check,
    ALTER COLUMN onset_classification TYPE character varying(12),
    ADD CONSTRAINT family_onset_check
        CHECK (onset_classification IN ('severe_early', 'early', 'typical', 'late', 'unknown'));

-- ---------------------------------------------------------------------------
-- 3. Verification factor (Si): "Was this diagnosis confirmed by a doctor?"
--    yes = 1.0; no or not_sure = 0.5 (author-defined weights; not stated in the sources)
-- ---------------------------------------------------------------------------
ALTER TABLE public.patient_family_history
    ADD COLUMN diagnosis_confirmed character varying(8) NOT NULL DEFAULT 'not_sure',
    ADD CONSTRAINT family_confirmed_check CHECK (diagnosis_confirmed IN ('yes', 'no', 'not_sure'));

-- ---------------------------------------------------------------------------
-- 4. FHRS results: five risk bands instead of three; environmental multiplier
--    Ed = 1.0 + sum of shared household exposures, so it can never be below 1.0
-- ---------------------------------------------------------------------------
ALTER TABLE public.patient_fhrs_stratification
    DROP CONSTRAINT fhrs_band_check,
    DROP CONSTRAINT fhrs_modifier_check,
    ADD CONSTRAINT fhrs_band_check
        CHECK (risk_band IN ('Baseline', 'Mild', 'Moderate', 'Pronounced', 'Critical')),
    ADD CONSTRAINT fhrs_modifier_check CHECK (environmental_modifier BETWEEN 1.0 AND 5.0);

COMMENT ON COLUMN public.patient_fhrs_stratification.risk_band IS
    'Band 0 Baseline (0.0), 1 Mild (0.10-1.75), 2 Moderate (1.76-3.50), 3 Pronounced (3.51-6.00), 4 Critical (>6.00)';
-- ============ 99_security.sql ============
-- 99_security.sql
-- Purpose: Zero Trust enforcement inside the database. Runs last.
--   1. Row-Level Security: each request sees only its own patient's rows (deny by default)
--   2. Least privilege: narrows med_app's default table rights per table
--   3. Function access: med_app may call only log_audit()
--
-- How the app uses this - at the start of every request transaction:
--     SET LOCAL app.user_id    = '<user_id from JWT>';
--     SET LOCAL app.patient_id = '<patient_id from JWT>';
-- SET LOCAL ends with the transaction, so one request's identity never leaks into the next.
--
-- Safe to run again: policies are dropped and recreated, and grants/revokes repeat cleanly.
-- After adding a new table script, rerun this whole file on the existing database.

SET client_min_messages = warning;  -- hide "policy does not exist, skipping" notices

-- ---------------------------------------------------------------------------
-- 1. Row-Level Security on every patient-owned table
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'documents', 'vitals_records', 'lab_results', 'lifestyle_records',
        'patient_health_history', 'questionnaire_responses', 'patient_family_history',
        'patient_fhrs_stratification', 'risk_assessments', 'risk_results',
        'shap_explanations', 'patient_goals', 'health_reports'
    ] LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS patient_isolation ON public.%I', t);
        EXECUTE format(
            'CREATE POLICY patient_isolation ON public.%I
                 USING (patient_id = public.current_patient_id())
                 WITH CHECK (patient_id = public.current_patient_id())', t);
    END LOOP;
END;
$$;

-- patients: visible to its own patient id, or to its own user during registration/login
-- (before the app knows the patient id)
ALTER TABLE public.patients ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS patient_self ON public.patients;
CREATE POLICY patient_self ON public.patients
    USING (patient_id = public.current_patient_id()
           OR user_id = public.current_app_user_id())
    WITH CHECK (user_id = public.current_app_user_id());

-- users has no RLS: login must look up an account by email before anyone is authenticated.
-- It holds credentials only (no health data); password hashes are Argon2id.

-- ---------------------------------------------------------------------------
-- 2. Least privilege per table
-- ---------------------------------------------------------------------------

-- Audit log: no direct access at all; writes go through log_audit() only
REVOKE ALL ON public.audit_ledger FROM med_app;

-- Model registry: read-only for the app
REVOKE INSERT, UPDATE, DELETE ON public.model_versions FROM med_app;

-- Care plan templates: read-only for the app (loaded and approved by the owner only).
-- Not patient-owned, so no RLS; every patient may read the active templates.
REVOKE INSERT, UPDATE, DELETE ON public.care_plan_templates FROM med_app;

-- Assessment results and saved reports are immutable
-- (they may only be deleted, e.g. on account deletion)
REVOKE UPDATE ON public.risk_assessments, public.risk_results, public.shap_explanations,
                 public.health_reports FROM med_app;

-- Reset tokens are never edited except to mark them used
REVOKE UPDATE ON public.password_reset_tokens FROM med_app;
GRANT UPDATE (used_at) ON public.password_reset_tokens TO med_app;

-- ---------------------------------------------------------------------------
-- 3. Functions
-- ---------------------------------------------------------------------------
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC;

-- Helpers needed by RLS policies and triggers while med_app is running queries
GRANT EXECUTE ON FUNCTION public.current_patient_id()  TO med_app;
GRANT EXECUTE ON FUNCTION public.current_app_user_id() TO med_app;
GRANT EXECUTE ON FUNCTION public.set_updated_at()      TO med_app;
GRANT EXECUTE ON FUNCTION public.prevent_modification() TO med_app;

-- The only way to write audit entries
GRANT EXECUTE ON FUNCTION public.log_audit(text, integer, integer, jsonb) TO med_app;

-- verify_audit_chain() and audit_seal_input() stay owner-only (admin checks).
-- protect_reviewed_template() (19_care_plan_templates.sql) stays owner-only: it only fires
-- on UPDATE or DELETE of templates, which med_app is not allowed to do.

-- ============ seed_care_plan_templates.sql ============
-- seed_care_plan_templates.sql
-- Purpose: Loads the 12 care plan templates from careplan_oct27.docx (version 1.0) as DRAFTS.
-- Generated from the Word file; edit the Word file and regenerate rather than editing by hand.
--
-- Run as postgres AFTER 19_care_plan_templates.sql. Not numbered on purpose, so Docker
-- does not load unreviewed advice automatically.
-- Drafts load with reviewed_by/reviewed_on NULL and is_active = false, so the app shows none
-- of them yet. After the physician signs a template (Section 7 of the care plan):
--     UPDATE public.care_plan_templates
--        SET reviewed_by = 'Dr. <name>, <specialty>', reviewed_on = DATE '<yyyy-mm-dd>', is_active = true
--      WHERE disease_domain = 'CVD' AND risk_level = 'Low' AND version = '1.0';
-- From then on that row is read-only; any change is a new version (e.g. '1.1').

BEGIN;

-- CVD-LOW: 7 base items, 3 add-ons
INSERT INTO public.care_plan_templates (disease_domain, risk_level, version, plan_items, guideline_source)
VALUES ('CVD', 'Low', '1.0', $plan${
  "base": [
    {
      "no": 1,
      "category": "Check-up and tests",
      "action": "Have your blood pressure checked in a clinic at least once a year.",
      "why": "Regular checks find high blood pressure before it causes harm.",
      "source": "PHEx 2021, Sec. 3.4",
      "basis": "Sourced / System rule",
      "goal": true
    },
    {
      "no": 2,
      "category": "Lifestyle",
      "action": "Eat a DASH-style diet and reduce salt added to meals.",
      "why": "Less salt helps keep blood pressure in a healthy range.",
      "source": "ISH 2020, Table 8",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 3,
      "category": "Lifestyle",
      "action": "Keep a healthy weight and a waist-to-height ratio below 0.5.",
      "why": "A healthy body size lowers long-term strain on your heart.",
      "source": "ISH 2020, Table 8",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 4,
      "category": "Lifestyle",
      "action": "Do 30 minutes of moderate aerobic exercise on 5 to 7 days a week.",
      "why": "Regular activity supports blood flow and heart fitness.",
      "source": "ISH 2020, Table 8",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 5,
      "category": "Lifestyle",
      "action": "Limit alcohol to 2 standard drinks a day for men, or 1.5 for women.",
      "why": "Too much alcohol raises blood pressure.",
      "source": "ISH 2020, Table 8",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 6,
      "category": "Lifestyle",
      "action": "Reduce daily stress and get 7 to 9 hours of sleep each night.",
      "why": "Good sleep and less stress support healthy blood vessels.",
      "source": "ISH 2020, Table 8 and Fig. 6",
      "basis": "Sourced (verify sleep hours)",
      "goal": true
    },
    {
      "no": 7,
      "category": "Urgent warning signs",
      "action": "Get urgent care now for chest pain, severe breathlessness, or blood pressure of 180/110 or higher with headache, chest pain, or blurred vision.",
      "why": "These can be signs of a heart or blood pressure emergency.",
      "source": "ISH 2020, Sec. 3 and 9",
      "basis": "Sourced",
      "goal": false
    }
  ],
  "addons": [
    {
      "code": "A1",
      "trigger_key": "current_smoker",
      "trigger": "Current smoker",
      "action": "Stop smoking completely and ask your healthcare provider about quit support.",
      "why": "Quitting protects the lining of your blood vessels.",
      "source": "ISH 2020, Table 8",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A2",
      "trigger_key": "fhrs_cvd_band_3_or_4",
      "trigger": "CVD family history band 3 or 4",
      "action": "Ask your doctor about getting a lipid profile test.",
      "why": "Heart disease in your family makes checking blood fats more important.",
      "source": "PHEx 2021, Sec. 3.5",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A3",
      "trigger_key": "sodium_above_2300mg",
      "trigger": "Daily sodium above 2,300 mg",
      "action": "Avoid processed, canned, and instant foods to lower your salt intake.",
      "why": "Processed foods hide a lot of salt.",
      "source": "ISH 2020, Table 8",
      "basis": "Sourced",
      "goal": true
    }
  ]
}$plan$::jsonb,
        '2020 ISH Global Hypertension Practice Guidelines (main); 2021 Philippine Guidelines on Periodic Health Examination (PHEx)')
ON CONFLICT (disease_domain, risk_level, version) DO NOTHING;

-- CVD-MODERATE: 8 base items, 3 add-ons
INSERT INTO public.care_plan_templates (disease_domain, risk_level, version, plan_items, guideline_source)
VALUES ('CVD', 'Moderate', '1.0', $plan${
  "base": [
    {
      "no": 1,
      "category": "See a doctor",
      "action": "Schedule a follow-up visit in 3 to 6 months to review your heart health.",
      "why": "A review checks whether your lifestyle changes are working.",
      "source": "CDSS Data Dictionary",
      "basis": "System rule",
      "goal": true
    },
    {
      "no": 2,
      "category": "Check-up and tests",
      "action": "Check your blood pressure at home for 3 to 7 days before your visit.",
      "why": "Home readings show your usual blood pressure outside the clinic.",
      "source": "ISH 2020, Sec. 4 and Fig. 6",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 3,
      "category": "Lifestyle",
      "action": "Eat a DASH-style diet and reduce salt added to meals.",
      "why": "Less salt helps keep blood pressure in a healthy range.",
      "source": "ISH 2020, Table 8",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 4,
      "category": "Lifestyle",
      "action": "Keep a healthy weight and a waist-to-height ratio below 0.5.",
      "why": "A healthy body size lowers long-term strain on your heart.",
      "source": "ISH 2020, Table 8",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 5,
      "category": "Lifestyle",
      "action": "Do 30 minutes of moderate aerobic exercise on 5 to 7 days a week.",
      "why": "Regular activity supports blood flow and heart fitness.",
      "source": "ISH 2020, Table 8",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 6,
      "category": "Lifestyle",
      "action": "Limit alcohol to 2 standard drinks a day for men, or 1.5 for women.",
      "why": "Too much alcohol raises blood pressure.",
      "source": "ISH 2020, Table 8",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 7,
      "category": "Lifestyle",
      "action": "Reduce daily stress and get 7 to 9 hours of sleep each night.",
      "why": "Good sleep and less stress support healthy blood vessels.",
      "source": "ISH 2020, Table 8 and Fig. 6",
      "basis": "Sourced (verify sleep hours)",
      "goal": true
    },
    {
      "no": 8,
      "category": "Urgent warning signs",
      "action": "Get urgent care now for chest pain, severe breathlessness, or blood pressure of 180/110 or higher with headache, chest pain, or blurred vision.",
      "why": "These can be signs of a heart or blood pressure emergency.",
      "source": "ISH 2020, Sec. 3 and 9",
      "basis": "Sourced",
      "goal": false
    }
  ],
  "addons": [
    {
      "code": "A1",
      "trigger_key": "current_smoker",
      "trigger": "Current smoker",
      "action": "Stop smoking completely and ask your healthcare provider about quit support.",
      "why": "Quitting protects the lining of your blood vessels.",
      "source": "ISH 2020, Table 8",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A2",
      "trigger_key": "fhrs_cvd_band_3_or_4",
      "trigger": "CVD family history band 3 or 4",
      "action": "Have a yearly blood pressure check and ask your doctor about a lipid profile.",
      "why": "Heart disease in your family calls for closer tracking.",
      "source": "PHEx 2021, Sec. 3.4 and 3.5",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A3",
      "trigger_key": "sodium_above_2300mg",
      "trigger": "Daily sodium above 2,300 mg",
      "action": "Avoid processed, canned, and instant foods to lower your salt intake.",
      "why": "Processed foods hide a lot of salt.",
      "source": "ISH 2020, Table 8",
      "basis": "Sourced",
      "goal": true
    }
  ]
}$plan$::jsonb,
        '2020 ISH Global Hypertension Practice Guidelines (main); 2021 Philippine Guidelines on Periodic Health Examination (PHEx)')
ON CONFLICT (disease_domain, risk_level, version) DO NOTHING;

-- CVD-HIGH: 8 base items, 3 add-ons
INSERT INTO public.care_plan_templates (disease_domain, risk_level, version, plan_items, guideline_source)
VALUES ('CVD', 'High', '1.0', $plan${
  "base": [
    {
      "no": 1,
      "category": "See a doctor",
      "action": "See a doctor within 30 to 60 days to confirm your blood pressure and check your lipid profile and fasting blood sugar.",
      "why": "Your results suggest a higher risk of heart disease that needs a medical check.",
      "source": "ISH 2020, Sec. 4; PHEx 2021, Sec. 3.4 and 3.5",
      "basis": "Sourced / System rule",
      "goal": true
    },
    {
      "no": 2,
      "category": "Check-up and tests",
      "action": "Record home blood pressure readings for 7 days before your visit.",
      "why": "Repeat readings help your doctor see if your pressure is high most of the time.",
      "source": "ISH 2020, Sec. 4 and Table 5",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 3,
      "category": "Lifestyle",
      "action": "Eat a DASH-style diet and reduce salt added to meals.",
      "why": "Less salt helps keep blood pressure in a healthy range.",
      "source": "ISH 2020, Table 8",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 4,
      "category": "Lifestyle",
      "action": "Keep a healthy weight and a waist-to-height ratio below 0.5.",
      "why": "A healthy body size lowers long-term strain on your heart.",
      "source": "ISH 2020, Table 8",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 5,
      "category": "Lifestyle",
      "action": "Do 30 minutes of moderate aerobic exercise on 5 to 7 days a week.",
      "why": "Regular activity supports blood flow and heart fitness.",
      "source": "ISH 2020, Table 8",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 6,
      "category": "Lifestyle",
      "action": "Limit alcohol to 2 standard drinks a day for men, or 1.5 for women.",
      "why": "Too much alcohol raises blood pressure.",
      "source": "ISH 2020, Table 8",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 7,
      "category": "Lifestyle",
      "action": "Reduce daily stress and get 7 to 9 hours of sleep each night.",
      "why": "Managing stress and rest helps protect your heart.",
      "source": "ISH 2020, Table 8 and Fig. 6",
      "basis": "Sourced (verify sleep hours)",
      "goal": true
    },
    {
      "no": 8,
      "category": "Urgent warning signs",
      "action": "Get urgent care now for chest pain, severe breathlessness, or blood pressure of 180/110 or higher with headache, chest pain, or blurred vision.",
      "why": "These can be signs of a heart or blood pressure emergency.",
      "source": "ISH 2020, Sec. 3 and 9",
      "basis": "Sourced",
      "goal": false
    }
  ],
  "addons": [
    {
      "code": "A1",
      "trigger_key": "current_smoker",
      "trigger": "Current smoker",
      "action": "Stop smoking completely and ask your healthcare provider about quit support.",
      "why": "Quitting protects the lining of your blood vessels.",
      "source": "ISH 2020, Table 8",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A2",
      "trigger_key": "fhrs_cvd_band_3_or_4",
      "trigger": "CVD family history band 3 or 4",
      "action": "Have a yearly blood pressure check after your doctor visit and keep your lipid profile up to date.",
      "why": "Heart disease in your family calls for closer tracking.",
      "source": "PHEx 2021, Sec. 3.4 and 3.5",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A3",
      "trigger_key": "sodium_above_2300mg",
      "trigger": "Daily sodium above 2,300 mg",
      "action": "Avoid processed, canned, and instant foods to lower your salt intake.",
      "why": "Processed foods hide a lot of salt.",
      "source": "ISH 2020, Table 8",
      "basis": "Sourced",
      "goal": true
    }
  ]
}$plan$::jsonb,
        '2020 ISH Global Hypertension Practice Guidelines (main); 2021 Philippine Guidelines on Periodic Health Examination (PHEx)')
ON CONFLICT (disease_domain, risk_level, version) DO NOTHING;

-- T2D-LOW: 5 base items, 6 add-ons
INSERT INTO public.care_plan_templates (disease_domain, risk_level, version, plan_items, guideline_source)
VALUES ('T2D', 'Low', '1.0', $plan${
  "base": [
    {
      "no": 1,
      "category": "Check-up and tests",
      "action": "Have your fasting blood sugar or HbA1c checked at your yearly health visit.",
      "why": "Early testing finds blood sugar changes before symptoms appear.",
      "source": "PHEx 2021, Sec. 3.3; ADA 2019, Sec. 2",
      "basis": "Sourced / System rule",
      "goal": true
    },
    {
      "no": 2,
      "category": "Lifestyle",
      "action": "Choose whole grains, vegetables, and lean proteins, and watch your portion sizes.",
      "why": "Healthy foods and portions help keep blood sugar steady.",
      "source": "ADA 2019, Sec. 5",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 3,
      "category": "Lifestyle",
      "action": "Drink water instead of sugar-sweetened drinks.",
      "why": "Fewer sugary drinks lower your daily sugar and calories.",
      "source": "ADA 2019, Rec. 5.23",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 4,
      "category": "Lifestyle",
      "action": "Do 30 minutes of moderate activity on at least 5 days a week (150 minutes).",
      "why": "Regular activity helps your body use insulin better.",
      "source": "ADA 2019, Rec. 3.2",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 5,
      "category": "Urgent warning signs",
      "action": "Get urgent medical care if you have extreme thirst and frequent urination with vomiting, confusion, or drowsiness.",
      "why": "These can signal a dangerous rise in blood sugar.",
      "source": "ADA 2019, Sec. 2",
      "basis": "Sourced",
      "goal": false
    }
  ],
  "addons": [
    {
      "code": "A1",
      "trigger_key": "fhrs_t2d_band_3_or_4",
      "trigger": "T2D family history band 3 or 4",
      "action": "Ask your doctor about earlier fasting blood sugar screening because of diabetes in your family.",
      "why": "Diabetes in your family raises your lifetime risk.",
      "source": "ADA 2019, Table 2.3",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A2",
      "trigger_key": "history_gestational_diabetes",
      "trigger": "History of gestational diabetes",
      "action": "Get tested for diabetes at least every 3 years.",
      "why": "Diabetes during pregnancy raises your risk for life.",
      "source": "ADA 2019, Table 2.3 and Rec. 2.10",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A3",
      "trigger_key": "acanthosis_nigricans",
      "trigger": "Acanthosis nigricans present",
      "action": "Ask your doctor for a blood sugar test.",
      "why": "Dark, velvety skin patches [acanthosis nigricans] can be a sign of insulin resistance.",
      "source": "ADA 2019, Table 2.3",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A4",
      "trigger_key": "current_smoker",
      "trigger": "Current smoker",
      "action": "Stop smoking completely and ask your healthcare provider about quit support.",
      "why": "Quitting lowers your risk of diabetes and its complications.",
      "source": "ADA 2019, Sec. 5",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A5",
      "trigger_key": "sedentary_8h_or_more",
      "trigger": "Sedentary time 8 hours a day or more",
      "action": "Break up long periods of sitting by standing or walking every 30 minutes.",
      "why": "Short movement breaks help your muscles use blood sugar.",
      "source": "ADA 2019, Rec. 5.26",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A6",
      "trigger_key": "bmi_25_or_more",
      "trigger": "BMI 25 or more (see open question on 23)",
      "action": "Aim to lose 7% of your body weight and keep it off.",
      "why": "Losing 7% of body weight lowers the chance of developing diabetes.",
      "source": "ADA 2019, Rec. 3.2",
      "basis": "Sourced",
      "goal": true
    }
  ]
}$plan$::jsonb,
        '2019 ADA Standards of Medical Care in Diabetes, Sections 2, 3 and 5 (main); 2021 PHEx')
ON CONFLICT (disease_domain, risk_level, version) DO NOTHING;

-- T2D-MODERATE: 6 base items, 5 add-ons
INSERT INTO public.care_plan_templates (disease_domain, risk_level, version, plan_items, guideline_source)
VALUES ('T2D', 'Moderate', '1.0', $plan${
  "base": [
    {
      "no": 1,
      "category": "See a doctor",
      "action": "Schedule a follow-up in 3 to 6 months to review your blood sugar risk.",
      "why": "A review checks whether your diet and activity changes are working.",
      "source": "CDSS Data Dictionary",
      "basis": "System rule",
      "goal": true
    },
    {
      "no": 2,
      "category": "Check-up and tests",
      "action": "Ask your doctor about testing your fasting blood sugar or HbA1c.",
      "why": "Tests show whether your blood sugar is moving toward prediabetes.",
      "source": "ADA 2019, Tables 2.2 and 2.5",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 3,
      "category": "Lifestyle",
      "action": "Choose whole grains, vegetables, and lean proteins, and watch your portion sizes.",
      "why": "Healthy foods and portions help keep blood sugar steady.",
      "source": "ADA 2019, Sec. 5",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 4,
      "category": "Lifestyle",
      "action": "Drink water instead of sugar-sweetened drinks.",
      "why": "Fewer sugary drinks lower your daily sugar and calories.",
      "source": "ADA 2019, Rec. 5.23",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 5,
      "category": "Lifestyle",
      "action": "Do 30 minutes of moderate activity on at least 5 days a week (150 minutes).",
      "why": "Regular activity helps your body use insulin better.",
      "source": "ADA 2019, Rec. 3.2",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 6,
      "category": "Urgent warning signs",
      "action": "Get urgent medical care if you have extreme thirst and frequent urination with vomiting, confusion, or drowsiness.",
      "why": "These can signal a dangerous rise in blood sugar.",
      "source": "ADA 2019, Sec. 2",
      "basis": "Sourced",
      "goal": false
    }
  ],
  "addons": [
    {
      "code": "A2",
      "trigger_key": "history_gestational_diabetes",
      "trigger": "History of gestational diabetes",
      "action": "Get tested for diabetes at least every 3 years.",
      "why": "Diabetes during pregnancy raises your risk for life.",
      "source": "ADA 2019, Table 2.3 and Rec. 2.10",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A3",
      "trigger_key": "acanthosis_nigricans",
      "trigger": "Acanthosis nigricans present",
      "action": "Ask your doctor for a blood sugar test.",
      "why": "Dark, velvety skin patches [acanthosis nigricans] can be a sign of insulin resistance.",
      "source": "ADA 2019, Table 2.3",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A4",
      "trigger_key": "current_smoker",
      "trigger": "Current smoker",
      "action": "Stop smoking completely and ask your healthcare provider about quit support.",
      "why": "Quitting lowers your risk of diabetes and its complications.",
      "source": "ADA 2019, Sec. 5",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A5",
      "trigger_key": "sedentary_8h_or_more",
      "trigger": "Sedentary time 8 hours a day or more",
      "action": "Break up long periods of sitting by standing or walking every 30 minutes.",
      "why": "Short movement breaks help your muscles use blood sugar.",
      "source": "ADA 2019, Rec. 5.26",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A6",
      "trigger_key": "bmi_25_or_more",
      "trigger": "BMI 25 or more (see open question on 23)",
      "action": "Aim to lose 7% of your body weight and keep it off.",
      "why": "Losing 7% of body weight lowers the chance of developing diabetes.",
      "source": "ADA 2019, Rec. 3.2",
      "basis": "Sourced",
      "goal": true
    }
  ]
}$plan$::jsonb,
        '2019 ADA Standards of Medical Care in Diabetes, Sections 2, 3 and 5 (main); 2021 PHEx')
ON CONFLICT (disease_domain, risk_level, version) DO NOTHING;

-- T2D-HIGH: 6 base items, 5 add-ons
INSERT INTO public.care_plan_templates (disease_domain, risk_level, version, plan_items, guideline_source)
VALUES ('T2D', 'High', '1.0', $plan${
  "base": [
    {
      "no": 1,
      "category": "See a doctor",
      "action": "See a doctor within 30 to 60 days for a fasting blood sugar or HbA1c test.",
      "why": "Your results suggest a higher risk of diabetes that needs a lab check.",
      "source": "ADA 2019, Table 2.2",
      "basis": "Sourced / System rule",
      "goal": true
    },
    {
      "no": 2,
      "category": "Check-up and tests",
      "action": "If the result is high, expect a second test to confirm it.",
      "why": "A second test makes the result reliable.",
      "source": "ADA 2019, Table 2.2",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 3,
      "category": "Lifestyle",
      "action": "Choose whole grains, vegetables, and lean proteins, and watch your portion sizes.",
      "why": "Healthy foods and portions help keep blood sugar steady.",
      "source": "ADA 2019, Sec. 5",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 4,
      "category": "Lifestyle",
      "action": "Drink water instead of sugar-sweetened drinks.",
      "why": "Fewer sugary drinks lower your daily sugar and calories.",
      "source": "ADA 2019, Rec. 5.23",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 5,
      "category": "Lifestyle",
      "action": "Do 30 minutes of moderate activity on at least 5 days a week (150 minutes).",
      "why": "Regular activity helps your body use insulin better.",
      "source": "ADA 2019, Rec. 3.2",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 6,
      "category": "Urgent warning signs",
      "action": "Get urgent medical care if you have extreme thirst and frequent urination with vomiting, confusion, or drowsiness.",
      "why": "These can signal a dangerous rise in blood sugar.",
      "source": "ADA 2019, Sec. 2",
      "basis": "Sourced",
      "goal": false
    }
  ],
  "addons": [
    {
      "code": "A2",
      "trigger_key": "history_gestational_diabetes",
      "trigger": "History of gestational diabetes",
      "action": "Get tested for diabetes at least every 3 years.",
      "why": "Diabetes during pregnancy raises your risk for life.",
      "source": "ADA 2019, Table 2.3 and Rec. 2.10",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A3",
      "trigger_key": "acanthosis_nigricans",
      "trigger": "Acanthosis nigricans present",
      "action": "Ask your doctor for a blood sugar test.",
      "why": "Dark, velvety skin patches [acanthosis nigricans] can be a sign of insulin resistance.",
      "source": "ADA 2019, Table 2.3",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A4",
      "trigger_key": "current_smoker",
      "trigger": "Current smoker",
      "action": "Stop smoking completely and ask your healthcare provider about quit support.",
      "why": "Quitting lowers your risk of diabetes and its complications.",
      "source": "ADA 2019, Sec. 5",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A5",
      "trigger_key": "sedentary_8h_or_more",
      "trigger": "Sedentary time 8 hours a day or more",
      "action": "Break up long periods of sitting by standing or walking every 30 minutes.",
      "why": "Short movement breaks help your muscles use blood sugar.",
      "source": "ADA 2019, Rec. 5.26",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A6",
      "trigger_key": "bmi_25_or_more",
      "trigger": "BMI 25 or more (see open question on 23)",
      "action": "Aim to lose 7% of your body weight and keep it off.",
      "why": "Losing 7% of body weight lowers the chance of developing diabetes.",
      "source": "ADA 2019, Rec. 3.2",
      "basis": "Sourced",
      "goal": true
    }
  ]
}$plan$::jsonb,
        '2019 ADA Standards of Medical Care in Diabetes, Sections 2, 3 and 5 (main); 2021 PHEx')
ON CONFLICT (disease_domain, risk_level, version) DO NOTHING;

-- CRD-LOW: 8 base items, 3 add-ons
INSERT INTO public.care_plan_templates (disease_domain, risk_level, version, plan_items, guideline_source)
VALUES ('CRD', 'Low', '1.0', $plan${
  "base": [
    {
      "no": 1,
      "category": "Check-up and tests",
      "action": "Have your breathing checked at your yearly doctor visit.",
      "why": "Regular checks find breathing changes early.",
      "source": "CDSS Data Dictionary",
      "basis": "System rule",
      "goal": true
    },
    {
      "no": 2,
      "category": "Lifestyle",
      "action": "Do not smoke, and stay away from secondhand smoke and e-cigarette vapor.",
      "why": "Smoke irritates and slowly damages your airways.",
      "source": "GOLD 2020, Ch. 3; GINA 2026",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 3,
      "category": "Lifestyle",
      "action": "Avoid wood smoke, cooking fumes, dust, and heavy air pollution.",
      "why": "Breathing in smoke and fumes can inflame your airways.",
      "source": "GOLD 2020, Ch. 1 and 3",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 4,
      "category": "Lifestyle",
      "action": "Do 30 minutes of moderate activity on at least 5 days a week (150 minutes).",
      "why": "Regular activity keeps your breathing muscles and stamina strong.",
      "source": "ISH 2020, Table 8; ADA 2019, Rec. 3.2",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 5,
      "category": "Check-up and tests",
      "action": "Ask your doctor whether you should get yearly flu and pneumococcal vaccines.",
      "why": "Vaccines protect your lungs from serious infections.",
      "source": "GOLD 2020, Ch. 3; GINA 2026",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 6,
      "category": "Check-up and tests",
      "action": "Watch for breathing symptoms such as long-lasting cough, wheezing, or shortness of breath.",
      "why": "Noticing changes early helps you get advice in time.",
      "source": "GOLD 2020, Ch. 1 and 2",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 7,
      "category": "Lifestyle",
      "action": "Avoid asthma triggers such as cold air and allergens, and note if you wheeze with exercise or at night.",
      "why": "Knowing your triggers can point to sensitive airways.",
      "source": "GINA 2026",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 8,
      "category": "Urgent warning signs",
      "action": "Get emergency care now for severe breathlessness, trouble speaking in sentences, blue lips, or drowsiness or confusion.",
      "why": "These are signs of a breathing emergency.",
      "source": "GINA 2026; GOLD 2020",
      "basis": "Sourced",
      "goal": false
    }
  ],
  "addons": [
    {
      "code": "A1",
      "trigger_key": "current_smoker",
      "trigger": "Current smoker",
      "action": "Stop smoking completely and ask your healthcare provider about quit programs.",
      "why": "Quitting is the most effective way to protect your lungs.",
      "source": "GOLD 2020, Ch. 3",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A2",
      "trigger_key": "occupational_dust_or_fumes",
      "trigger": "Occupational dust or fume exposure",
      "action": "Use a proper mask and good ventilation at work.",
      "why": "Less exposure prevents lung damage from building up.",
      "source": "GOLD 2020, Ch. 3",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A3",
      "trigger_key": "allergic_rhinitis_or_childhood_asthma_or_fhrs_crd_band_3_or_4",
      "trigger": "Allergic rhinitis, childhood asthma history, or CRD family history band 3 or 4 (any)",
      "action": "Ask your doctor whether you need a lung function test.",
      "why": "Nasal allergies, childhood asthma, and lung disease in your family raise your risk.",
      "source": "GINA 2026; GOLD 2020, Ch. 1; CDSS Data Dictionary (FHRS)",
      "basis": "Sourced",
      "goal": true
    }
  ]
}$plan$::jsonb,
        'GINA 2026 Summary Guide (asthma, main); GOLD 2020 Pocket Guide (COPD, main); activity target: ISH 2020 and ADA 2019')
ON CONFLICT (disease_domain, risk_level, version) DO NOTHING;

-- CRD-MODERATE: 8 base items, 3 add-ons
INSERT INTO public.care_plan_templates (disease_domain, risk_level, version, plan_items, guideline_source)
VALUES ('CRD', 'Moderate', '1.0', $plan${
  "base": [
    {
      "no": 1,
      "category": "See a doctor",
      "action": "Schedule a doctor visit in 3 to 6 months to review your breathing.",
      "why": "A review catches new breathing symptoms early.",
      "source": "CDSS Data Dictionary",
      "basis": "System rule",
      "goal": true
    },
    {
      "no": 2,
      "category": "Lifestyle",
      "action": "Do not smoke, and stay away from secondhand smoke and e-cigarette vapor.",
      "why": "Smoke irritates and slowly damages your airways.",
      "source": "GOLD 2020, Ch. 3; GINA 2026",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 3,
      "category": "Lifestyle",
      "action": "Avoid wood smoke, cooking fumes, dust, and heavy air pollution.",
      "why": "Breathing in smoke and fumes can inflame your airways.",
      "source": "GOLD 2020, Ch. 1 and 3",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 4,
      "category": "Lifestyle",
      "action": "Do 30 minutes of moderate activity on at least 5 days a week (150 minutes).",
      "why": "Regular activity keeps your breathing muscles and stamina strong.",
      "source": "ISH 2020, Table 8; ADA 2019, Rec. 3.2",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 5,
      "category": "Check-up and tests",
      "action": "Ask your doctor whether you should get yearly flu and pneumococcal vaccines.",
      "why": "Vaccines protect your lungs from serious infections.",
      "source": "GOLD 2020, Ch. 3; GINA 2026",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 6,
      "category": "Check-up and tests",
      "action": "Keep track of any lasting cough, phlegm, or wheezing and tell your doctor at your visit.",
      "why": "A symptom record helps your doctor decide if tests are needed.",
      "source": "GOLD 2020, Ch. 2",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 7,
      "category": "Lifestyle",
      "action": "Avoid asthma triggers such as cold air and allergens, and note if you wheeze with exercise or at night.",
      "why": "Knowing your triggers can point to sensitive airways.",
      "source": "GINA 2026",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 8,
      "category": "Urgent warning signs",
      "action": "Get emergency care now for severe breathlessness, trouble speaking in sentences, blue lips, or drowsiness or confusion.",
      "why": "These are signs of a breathing emergency.",
      "source": "GINA 2026; GOLD 2020",
      "basis": "Sourced",
      "goal": false
    }
  ],
  "addons": [
    {
      "code": "A1",
      "trigger_key": "current_smoker",
      "trigger": "Current smoker",
      "action": "Stop smoking completely and ask your healthcare provider about quit programs.",
      "why": "Quitting is the most effective way to protect your lungs.",
      "source": "GOLD 2020, Ch. 3",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A2",
      "trigger_key": "occupational_dust_or_fumes",
      "trigger": "Occupational dust or fume exposure",
      "action": "Use a proper mask and good ventilation at work.",
      "why": "Less exposure prevents lung damage from building up.",
      "source": "GOLD 2020, Ch. 3",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A3",
      "trigger_key": "allergic_rhinitis_or_childhood_asthma_or_fhrs_crd_band_3_or_4",
      "trigger": "Allergic rhinitis, childhood asthma history, or CRD family history band 3 or 4 (any)",
      "action": "Ask your doctor whether you need a lung function test.",
      "why": "Nasal allergies, childhood asthma, and lung disease in your family raise your risk.",
      "source": "GINA 2026; GOLD 2020, Ch. 1; CDSS Data Dictionary (FHRS)",
      "basis": "Sourced",
      "goal": true
    }
  ]
}$plan$::jsonb,
        'GINA 2026 Summary Guide (asthma, main); GOLD 2020 Pocket Guide (COPD, main); activity target: ISH 2020 and ADA 2019')
ON CONFLICT (disease_domain, risk_level, version) DO NOTHING;

-- CRD-HIGH: 8 base items, 2 add-ons
INSERT INTO public.care_plan_templates (disease_domain, risk_level, version, plan_items, guideline_source)
VALUES ('CRD', 'High', '1.0', $plan${
  "base": [
    {
      "no": 1,
      "category": "See a doctor",
      "action": "See a doctor within 30 to 60 days and ask about a lung function test [spirometry] to check for asthma or COPD.",
      "why": "Your results suggest a higher risk of lung disease that needs testing.",
      "source": "GOLD 2020, Ch. 2",
      "basis": "Sourced / System rule",
      "goal": true
    },
    {
      "no": 2,
      "category": "Lifestyle",
      "action": "Do not smoke, and stay away from secondhand smoke and e-cigarette vapor.",
      "why": "Smoke irritates and slowly damages your airways.",
      "source": "GOLD 2020, Ch. 3; GINA 2026",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 3,
      "category": "Lifestyle",
      "action": "Avoid wood smoke, cooking fumes, dust, and heavy air pollution.",
      "why": "Breathing in smoke and fumes can inflame your airways.",
      "source": "GOLD 2020, Ch. 1 and 3",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 4,
      "category": "Lifestyle",
      "action": "Do 30 minutes of moderate activity on at least 5 days a week (150 minutes).",
      "why": "Regular activity keeps your breathing muscles and stamina strong.",
      "source": "ISH 2020, Table 8; ADA 2019, Rec. 3.2",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 5,
      "category": "Check-up and tests",
      "action": "Ask your doctor whether you should get yearly flu and pneumococcal vaccines.",
      "why": "Vaccines protect your lungs from serious infections.",
      "source": "GOLD 2020, Ch. 3; GINA 2026",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 6,
      "category": "Check-up and tests",
      "action": "Track your breathing symptoms and note patterns of cough or breathlessness.",
      "why": "A symptom record helps your doctor read your test results.",
      "source": "GOLD 2020, Ch. 2",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 7,
      "category": "Lifestyle",
      "action": "Avoid asthma triggers such as cold air and allergens, and note if you wheeze with exercise or at night.",
      "why": "Knowing your triggers can point to sensitive airways.",
      "source": "GINA 2026",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 8,
      "category": "Urgent warning signs",
      "action": "Get emergency care now for severe breathlessness, trouble speaking in sentences, blue lips, or drowsiness or confusion.",
      "why": "These are signs of a breathing emergency.",
      "source": "GINA 2026; GOLD 2020",
      "basis": "Sourced",
      "goal": false
    }
  ],
  "addons": [
    {
      "code": "A1",
      "trigger_key": "current_smoker",
      "trigger": "Current smoker",
      "action": "Stop smoking completely and ask your healthcare provider about quit programs.",
      "why": "Quitting is the most effective way to protect your lungs.",
      "source": "GOLD 2020, Ch. 3",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A2",
      "trigger_key": "occupational_dust_or_fumes",
      "trigger": "Occupational dust or fume exposure",
      "action": "Use a proper mask and good ventilation at work.",
      "why": "Less exposure prevents lung damage from building up.",
      "source": "GOLD 2020, Ch. 3",
      "basis": "Sourced",
      "goal": true
    }
  ]
}$plan$::jsonb,
        'GINA 2026 Summary Guide (asthma, main); GOLD 2020 Pocket Guide (COPD, main); activity target: ISH 2020 and ADA 2019')
ON CONFLICT (disease_domain, risk_level, version) DO NOTHING;

-- NEURO-LOW: 8 base items, 4 add-ons
INSERT INTO public.care_plan_templates (disease_domain, risk_level, version, plan_items, guideline_source)
VALUES ('NEURO', 'Low', '1.0', $plan${
  "base": [
    {
      "no": 1,
      "category": "Safety",
      "action": "If you have thoughts of harming yourself, call the NCMH Crisis Hotline at 1553 or go to the nearest emergency room now.",
      "why": "Immediate support keeps you safe during a crisis.",
      "source": "System rule",
      "basis": "System rule",
      "goal": false
    },
    {
      "no": 2,
      "category": "Check-up and tests",
      "action": "Retake the PHQ-9 (mood) and GAD-7 (anxiety) questionnaires at your yearly check-up.",
      "why": "Regular tracking shows changes in mood and anxiety over time.",
      "source": "PHEx 2021, Sec. 3.15 (PHQ-9)",
      "basis": "Sourced (PHQ-9) / System rule (GAD-7, yearly)",
      "goal": true
    },
    {
      "no": 3,
      "category": "Lifestyle",
      "action": "Learn about anxiety and how it differs from everyday worry.",
      "why": "Understanding anxiety helps you notice it early and manage it.",
      "source": "NICE CG113, Sec. 1.2",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 4,
      "category": "Lifestyle",
      "action": "Do 30 minutes of moderate activity on at least 5 days a week (150 minutes).",
      "why": "Regular exercise supports a better mood.",
      "source": "NICE NG222, Sec. 1.4 (exercise); ISH 2020, Table 8; ADA 2019, Rec. 3.2 (minutes)",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 5,
      "category": "Lifestyle",
      "action": "Keep a daily routine and stay connected with family, friends, and others who support you.",
      "why": "Routines and social contact give emotional stability.",
      "source": "NICE NG222, Sec. 1.1; WHO Module 6",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 6,
      "category": "Lifestyle",
      "action": "Keep regular sleep and wake times.",
      "why": "Good sleep supports emotional health.",
      "source": "NICE NG222, Sec. 1.4; WHO Module 6",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 7,
      "category": "Lifestyle",
      "action": "Limit alcohol to 2 standard drinks a day for men, or 1.5 for women.",
      "why": "Too much alcohol can worsen mood, anxiety, and sleep.",
      "source": "ISH 2020, Table 8",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 8,
      "category": "Urgent warning signs",
      "action": "Get urgent medical care now if you are unable to eat, sleep, or care for yourself.",
      "why": "Being unable to manage daily needs calls for prompt help.",
      "source": "NICE CG113, Sec. 1.2 (self-neglect)",
      "basis": "Sourced (verify wording)",
      "goal": false
    }
  ],
  "addons": [
    {
      "code": "A1",
      "trigger_key": "sleep_below_7_or_above_9h",
      "trigger": "Daily sleep below 7 or above 9 hours",
      "action": "Aim for 7 to 9 hours of sleep each night with consistent bedtimes.",
      "why": "Too little or too much sleep can upset your mood.",
      "source": "WHO Module 6; CDSS Data Dictionary (Harrison's, 7 to 9 hours)",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A2",
      "trigger_key": "current_alcohol_use",
      "trigger": "Alcohol use: current",
      "action": "Cut down or stop drinking alcohol.",
      "why": "Alcohol can disturb your mood and sleep.",
      "source": "WHO Module 6",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A3",
      "trigger_key": "psqi_above_5",
      "trigger": "PSQI above 5",
      "action": "Relax before bed and cut down screen time at bedtime.",
      "why": "Better sleep habits improve sleep quality.",
      "source": "NICE NG222, Sec. 1.4",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A4",
      "trigger_key": "tsh_or_b12_out_of_range",
      "trigger": "TSH outside 0.34 to 4.25 mIU/L, or vitamin B12 below 200 pg/mL",
      "action": "Show your TSH or vitamin B12 result to your doctor.",
      "why": "Thyroid and vitamin levels can affect energy and mood.",
      "source": "CDSS Data Dictionary (Harrison's)",
      "basis": "Sourced",
      "goal": true
    }
  ]
}$plan$::jsonb,
        'NICE NG222 Depression in Adults (main); NICE CG113 Generalised Anxiety Disorder, GAD sections only (main); 2009 WHO Lifestyle-related Diseases Manual, Module 6; 2021 PHEx (PHQ-9); activity and alcohol: ISH 2020 and ADA 2019')
ON CONFLICT (disease_domain, risk_level, version) DO NOTHING;

-- NEURO-MODERATE: 9 base items, 4 add-ons
INSERT INTO public.care_plan_templates (disease_domain, risk_level, version, plan_items, guideline_source)
VALUES ('NEURO', 'Moderate', '1.0', $plan${
  "base": [
    {
      "no": 1,
      "category": "Safety",
      "action": "If you have thoughts of harming yourself, call the NCMH Crisis Hotline at 1553 or go to the nearest emergency room now.",
      "why": "Immediate support keeps you safe during a crisis.",
      "source": "System rule",
      "basis": "System rule",
      "goal": false
    },
    {
      "no": 2,
      "category": "See a doctor",
      "action": "Schedule a follow-up in 3 to 6 months to review your emotional well-being.",
      "why": "A review checks whether self-care is helping your mood and anxiety.",
      "source": "CDSS Data Dictionary",
      "basis": "System rule",
      "goal": true
    },
    {
      "no": 3,
      "category": "Check-up and tests",
      "action": "Retake the PHQ-9 and GAD-7 questionnaires in 3 to 6 months.",
      "why": "Re-screening shows whether mood and anxiety are getting better or worse.",
      "source": "PHEx 2021, Sec. 3.15 (PHQ-9)",
      "basis": "Sourced (PHQ-9) / System rule (GAD-7, interval)",
      "goal": true
    },
    {
      "no": 4,
      "category": "See a doctor",
      "action": "Ask your healthcare provider about guided self-help, a psychoeducational group, or talking therapy for low mood or anxiety.",
      "why": "Structured support gives practical tools for coping with low mood and worry.",
      "source": "NICE NG222; NICE CG113, Sec. 1.2",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 5,
      "category": "Lifestyle",
      "action": "Do 30 minutes of moderate activity on at least 5 days a week (150 minutes).",
      "why": "Regular exercise supports a better mood.",
      "source": "NICE NG222, Sec. 1.4 (exercise); ISH 2020, Table 8; ADA 2019, Rec. 3.2 (minutes)",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 6,
      "category": "Lifestyle",
      "action": "Keep a daily routine and stay connected with family, friends, and others who support you.",
      "why": "Routines and social contact give emotional stability.",
      "source": "NICE NG222, Sec. 1.1; WHO Module 6",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 7,
      "category": "Lifestyle",
      "action": "Keep regular sleep and wake times.",
      "why": "Good sleep supports emotional health.",
      "source": "NICE NG222, Sec. 1.4; WHO Module 6",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 8,
      "category": "Lifestyle",
      "action": "Limit alcohol to 2 standard drinks a day for men, or 1.5 for women.",
      "why": "Too much alcohol can worsen mood, anxiety, and sleep.",
      "source": "ISH 2020, Table 8",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 9,
      "category": "Urgent warning signs",
      "action": "Get urgent medical care now if you are unable to eat, sleep, or care for yourself.",
      "why": "Being unable to manage daily needs calls for prompt help.",
      "source": "NICE CG113, Sec. 1.2 (self-neglect)",
      "basis": "Sourced (verify wording)",
      "goal": false
    }
  ],
  "addons": [
    {
      "code": "A1",
      "trigger_key": "sleep_below_7_or_above_9h",
      "trigger": "Daily sleep below 7 or above 9 hours",
      "action": "Aim for 7 to 9 hours of sleep each night with consistent bedtimes.",
      "why": "Too little or too much sleep can upset your mood.",
      "source": "WHO Module 6; CDSS Data Dictionary (Harrison's, 7 to 9 hours)",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A2",
      "trigger_key": "current_alcohol_use",
      "trigger": "Alcohol use: current",
      "action": "Cut down or stop drinking alcohol.",
      "why": "Alcohol can disturb your mood and sleep.",
      "source": "WHO Module 6",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A3",
      "trigger_key": "psqi_above_5",
      "trigger": "PSQI above 5",
      "action": "Relax before bed and cut down screen time at bedtime.",
      "why": "Better sleep habits improve sleep quality.",
      "source": "NICE NG222, Sec. 1.4",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A4",
      "trigger_key": "tsh_or_b12_out_of_range",
      "trigger": "TSH outside 0.34 to 4.25 mIU/L, or vitamin B12 below 200 pg/mL",
      "action": "Show your TSH or vitamin B12 result to your doctor.",
      "why": "Thyroid and vitamin levels can affect energy and mood.",
      "source": "CDSS Data Dictionary (Harrison's)",
      "basis": "Sourced",
      "goal": true
    }
  ]
}$plan$::jsonb,
        'NICE NG222 Depression in Adults (main); NICE CG113 Generalised Anxiety Disorder, GAD sections only (main); 2009 WHO Lifestyle-related Diseases Manual, Module 6; 2021 PHEx (PHQ-9); activity and alcohol: ISH 2020 and ADA 2019')
ON CONFLICT (disease_domain, risk_level, version) DO NOTHING;

-- NEURO-HIGH: 8 base items, 4 add-ons
INSERT INTO public.care_plan_templates (disease_domain, risk_level, version, plan_items, guideline_source)
VALUES ('NEURO', 'High', '1.0', $plan${
  "base": [
    {
      "no": 1,
      "category": "Safety",
      "action": "If you have thoughts of harming yourself, call the NCMH Crisis Hotline at 1553 or go to the nearest emergency room now.",
      "why": "Immediate support keeps you safe during a crisis.",
      "source": "System rule",
      "basis": "System rule",
      "goal": false
    },
    {
      "no": 2,
      "category": "See a doctor",
      "action": "See a doctor within 30 to 60 days for a depression and anxiety assessment, and retake the PHQ-9 and GAD-7 before the visit.",
      "why": "Your results suggest a higher risk that needs a clinical check.",
      "source": "PHEx 2021, Sec. 3.15; NICE NG222; NICE CG113, Sec. 1.2",
      "basis": "Sourced (assessment) / System rule (timeframe, GAD-7)",
      "goal": true
    },
    {
      "no": 3,
      "category": "See a doctor",
      "action": "Ask your healthcare provider about talking therapy, such as CBT for low mood or anxiety, or applied relaxation for anxiety.",
      "why": "Talking therapy gives practical tools to manage low mood and worry.",
      "source": "NICE NG222; NICE CG113, Sec. 1.2",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 4,
      "category": "Lifestyle",
      "action": "Do 30 minutes of moderate activity on at least 5 days a week (150 minutes).",
      "why": "Regular exercise supports a better mood.",
      "source": "NICE NG222, Sec. 1.4 (exercise); ISH 2020, Table 8; ADA 2019, Rec. 3.2 (minutes)",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 5,
      "category": "Lifestyle",
      "action": "Keep a daily routine and stay connected with family, friends, and others who support you.",
      "why": "Routines and social contact give emotional stability.",
      "source": "NICE NG222, Sec. 1.1; WHO Module 6",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 6,
      "category": "Lifestyle",
      "action": "Keep regular sleep and wake times.",
      "why": "Good sleep supports emotional health.",
      "source": "NICE NG222, Sec. 1.4; WHO Module 6",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 7,
      "category": "Lifestyle",
      "action": "Limit alcohol to 2 standard drinks a day for men, or 1.5 for women.",
      "why": "Too much alcohol can worsen mood, anxiety, and sleep.",
      "source": "ISH 2020, Table 8",
      "basis": "Sourced",
      "goal": true
    },
    {
      "no": 8,
      "category": "Urgent warning signs",
      "action": "Get urgent medical care now if you are unable to eat, sleep, or care for yourself.",
      "why": "Being unable to manage daily needs calls for prompt help.",
      "source": "NICE CG113, Sec. 1.2 (self-neglect)",
      "basis": "Sourced (verify wording)",
      "goal": false
    }
  ],
  "addons": [
    {
      "code": "A1",
      "trigger_key": "sleep_below_7_or_above_9h",
      "trigger": "Daily sleep below 7 or above 9 hours",
      "action": "Aim for 7 to 9 hours of sleep each night with consistent bedtimes.",
      "why": "Too little or too much sleep can upset your mood.",
      "source": "WHO Module 6; CDSS Data Dictionary (Harrison's, 7 to 9 hours)",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A2",
      "trigger_key": "current_alcohol_use",
      "trigger": "Alcohol use: current",
      "action": "Cut down or stop drinking alcohol.",
      "why": "Alcohol can disturb your mood and sleep.",
      "source": "WHO Module 6",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A3",
      "trigger_key": "psqi_above_5",
      "trigger": "PSQI above 5",
      "action": "Relax before bed and cut down screen time at bedtime.",
      "why": "Better sleep habits improve sleep quality.",
      "source": "NICE NG222, Sec. 1.4",
      "basis": "Sourced",
      "goal": true
    },
    {
      "code": "A4",
      "trigger_key": "tsh_or_b12_out_of_range",
      "trigger": "TSH outside 0.34 to 4.25 mIU/L, or vitamin B12 below 200 pg/mL",
      "action": "Show your TSH or vitamin B12 result to your doctor.",
      "why": "Thyroid and vitamin levels can affect energy and mood.",
      "source": "CDSS Data Dictionary (Harrison's)",
      "basis": "Sourced",
      "goal": true
    }
  ]
}$plan$::jsonb,
        'NICE NG222 Depression in Adults (main); NICE CG113 Generalised Anxiety Disorder, GAD sections only (main); 2009 WHO Lifestyle-related Diseases Manual, Module 6; 2021 PHEx (PHQ-9); activity and alcohol: ISH 2020 and ADA 2019')
ON CONFLICT (disease_domain, risk_level, version) DO NOTHING;

COMMIT;
