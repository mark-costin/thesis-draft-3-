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
