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
