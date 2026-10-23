-- 13_fhrs_stratification.sql
-- Table: patient_fhrs_stratification
-- Purpose: Family History Risk Score (FHRS) per disease, calculated from the pedigree.
-- Feeds the models as a feature. One row per calculation, so changes stay traceable.

CREATE TABLE IF NOT EXISTS public.patient_fhrs_stratification (
    fhrs_id                integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_id             integer NOT NULL
                           REFERENCES public.patients(patient_id) ON DELETE CASCADE,
    disease_domain         character varying(5) NOT NULL,
    affected_relatives     smallint NOT NULL,        -- how many relatives counted
    clustering_score       numeric(6,3) NOT NULL,
    environmental_modifier numeric(4,3) NOT NULL,
    calculated_fhrs        numeric(6,3) NOT NULL,
    risk_band              character varying(10) NOT NULL,
    evaluated_at           timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fhrs_domain_check CHECK (disease_domain IN ('CVD', 'T2D', 'CRD', 'NEURO')),
    CONSTRAINT fhrs_relatives_check CHECK (affected_relatives >= 0),
    CONSTRAINT fhrs_scores_check CHECK (clustering_score >= 0 AND calculated_fhrs >= 0),
    CONSTRAINT fhrs_modifier_check CHECK (environmental_modifier BETWEEN 0 AND 5),
    CONSTRAINT fhrs_band_check CHECK (risk_band IN ('Low', 'Moderate', 'High'))
);

CREATE INDEX IF NOT EXISTS ix_fhrs_patient_domain ON public.patient_fhrs_stratification
    (patient_id, disease_domain, evaluated_at DESC);
