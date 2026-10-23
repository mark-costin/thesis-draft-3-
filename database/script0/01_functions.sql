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
