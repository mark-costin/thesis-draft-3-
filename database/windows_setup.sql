-- windows_setup.sql
-- Purpose: Sets up med_db on Windows without Docker (DBeaver or psql).
-- Replaces 00_roles.sh (bash, Docker only), then runs 01-18, 99_security and 21_famhistory in order.
-- Run ONCE on a NEW, EMPTY database named med_db, connected as postgres.
-- Keep in sync: when a script in database/ is added or changed, update this file too.
-- Dev password for med_app = CHANGE_ME (matches .env.example). Change both before any real deployment.

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
        'shap_explanations', 'patient_goals'
    ] LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
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

-- Assessment results are immutable (they may only be deleted, e.g. on account deletion)
REVOKE UPDATE ON public.risk_assessments, public.risk_results, public.shap_explanations FROM med_app;

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
