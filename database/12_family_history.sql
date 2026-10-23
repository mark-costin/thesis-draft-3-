-- 12_family_history.sql
-- Table: patient_family_history
-- Purpose: Family pedigree - one row per relative x condition.
-- relationship_degree is derived from the relative automatically, so it can never be wrong.
-- Relationship and onset weights are applied in the FHRS calculation (13_fhrs_stratification).

CREATE TABLE IF NOT EXISTS public.patient_family_history (
    record_id            integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_id           integer NOT NULL
                         REFERENCES public.patients(patient_id) ON DELETE CASCADE,
    disease_domain       character varying(5) NOT NULL,
    specific_diagnosis   character varying(150) NOT NULL,    -- e.g. 'Hypertension', 'Type 2 diabetes'
    relative             character varying(25) NOT NULL,
    relationship_degree  smallint GENERATED ALWAYS AS (CASE
        WHEN relative IN ('father', 'mother', 'brother', 'sister', 'son', 'daughter') THEN 1
        WHEN relative IN ('paternal_grandfather', 'paternal_grandmother',
                          'maternal_grandfather', 'maternal_grandmother',
                          'paternal_uncle', 'paternal_aunt', 'maternal_uncle', 'maternal_aunt',
                          'half_brother', 'half_sister') THEN 2
        ELSE 3                                                -- cousins
    END) STORED,
    age_at_diagnosis     smallint,
    onset_classification character varying(10) NOT NULL DEFAULT 'unknown',
    shared_environment   boolean NOT NULL DEFAULT false,     -- lived in the same household
    recorded_at          timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT family_domain_check CHECK (disease_domain IN ('CVD', 'T2D', 'CRD', 'NEURO')),
    CONSTRAINT family_relative_check CHECK (relative IN (
        'father', 'mother', 'brother', 'sister', 'son', 'daughter',
        'paternal_grandfather', 'paternal_grandmother', 'maternal_grandfather', 'maternal_grandmother',
        'paternal_uncle', 'paternal_aunt', 'maternal_uncle', 'maternal_aunt',
        'half_brother', 'half_sister', 'paternal_cousin', 'maternal_cousin'
    )),
    CONSTRAINT family_age_check CHECK (age_at_diagnosis IS NULL OR age_at_diagnosis BETWEEN 0 AND 120),
    CONSTRAINT family_onset_check CHECK (onset_classification IN ('early', 'late', 'unknown')),
    -- The same relative can't be entered twice for the same diagnosis
    CONSTRAINT family_unique_entry UNIQUE (patient_id, relative, disease_domain, specific_diagnosis)
);

CREATE INDEX IF NOT EXISTS ix_family_patient_domain ON public.patient_family_history (patient_id, disease_domain);

CREATE TRIGGER trg_family_history_updated_at
    BEFORE UPDATE ON public.patient_family_history
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
