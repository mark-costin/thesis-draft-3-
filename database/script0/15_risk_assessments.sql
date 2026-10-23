-- 15_risk_assessments.sql
-- Table: risk_assessments
-- Purpose: One row per screening run (the patient presses "Run assessment").
-- Groups the four disease results and keeps an exact copy of the inputs used,
-- so every result can be reproduced and explained later.
-- Replaces the old consultations table as the parent of risk_results.

CREATE TABLE IF NOT EXISTS public.risk_assessments (
    assessment_id  integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_id     integer NOT NULL
                   REFERENCES public.patients(patient_id) ON DELETE CASCADE,
    input_snapshot jsonb NOT NULL,     -- feature values per model, e.g. {"CVD": {...}, "T2D": {...}}
    created_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT assessment_snapshot_check CHECK (jsonb_typeof(input_snapshot) = 'object'),
    CONSTRAINT assessment_id_patient_key UNIQUE (assessment_id, patient_id)  -- target of 16_risk_results FK
);

CREATE INDEX IF NOT EXISTS ix_assessments_patient_created ON public.risk_assessments (patient_id, created_at DESC);
