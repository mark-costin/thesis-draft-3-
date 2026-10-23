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
