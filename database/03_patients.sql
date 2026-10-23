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
