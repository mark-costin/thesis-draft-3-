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
