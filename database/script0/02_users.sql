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
