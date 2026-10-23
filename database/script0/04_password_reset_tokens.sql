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
