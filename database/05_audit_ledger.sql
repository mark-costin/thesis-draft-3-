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
