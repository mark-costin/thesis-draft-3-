-- 23_contact_and_google.sql
-- Purpose: (1) contact number and home address on the patient's profile, as collected on the
-- Create account and Account settings screens; (2) sign in with Google.
-- Run on an existing med_db as postgres, then run 99_security.sql again (it grants the app
-- UPDATE on the new profile columns). New databases run it in order like the other scripts.
-- Safe to run twice.
--
-- RA 10173 (data minimization): the address is kept at barangay level plus an optional street
-- line, the contact number is one mobile number, and none of it is a model input, an audit
-- payload or part of a saved report. It is in the account export and is deleted with the account.

-- ---------------------------------------------------------------------------
-- 1. Contact number and home address (patients)
-- ---------------------------------------------------------------------------
ALTER TABLE public.patients
    ADD COLUMN IF NOT EXISTS contact_number    character varying(13),   -- 09171234567 or +639171234567
    ADD COLUMN IF NOT EXISTS province          character varying(100),
    ADD COLUMN IF NOT EXISTS city_municipality character varying(100),
    ADD COLUMN IF NOT EXISTS barangay          character varying(100),
    ADD COLUMN IF NOT EXISTS street            character varying(150);  -- optional house no. / street

ALTER TABLE public.patients
    DROP CONSTRAINT IF EXISTS patients_contact_number_check,
    DROP CONSTRAINT IF EXISTS patients_address_order_check,
    ADD CONSTRAINT patients_contact_number_check
        CHECK (contact_number IS NULL OR contact_number ~ '^(09[0-9]{9}|\+639[0-9]{9})$'),
    -- A barangay needs its city, and a city needs its province (the screens pick them in that order)
    ADD CONSTRAINT patients_address_order_check
        CHECK ((barangay IS NULL OR city_municipality IS NOT NULL)
           AND (city_municipality IS NULL OR province IS NOT NULL));

-- ---------------------------------------------------------------------------
-- 2. Sign in with Google (users)
-- ---------------------------------------------------------------------------
-- A Google account has no password: password_hash becomes optional and google_sub (Google's
-- permanent account id from the verified ID token) identifies it. Every account has one or both.
ALTER TABLE public.users
    ALTER COLUMN password_hash DROP NOT NULL,
    ADD COLUMN IF NOT EXISTS google_sub character varying(255);

ALTER TABLE public.users
    DROP CONSTRAINT IF EXISTS users_has_a_sign_in_check,
    ADD CONSTRAINT users_has_a_sign_in_check CHECK (password_hash IS NOT NULL OR google_sub IS NOT NULL);

CREATE UNIQUE INDEX IF NOT EXISTS ux_users_google_sub ON public.users (google_sub)
    WHERE google_sub IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 3. Accepting a new privacy notice
-- ---------------------------------------------------------------------------
-- med_app has no UPDATE right on the consent columns (99_security.sql). This is the only way it can
-- change them, and only on the patient of the current request. The old version stays in the audit log.
CREATE OR REPLACE FUNCTION public.accept_privacy_notice(p_version character varying)
RETURNS void
LANGUAGE sql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
    UPDATE public.patients
       SET consent_version = p_version, consent_at = now()
     WHERE patient_id = public.current_patient_id();
$$;
