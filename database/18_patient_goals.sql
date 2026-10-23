-- 18_patient_goals.sql
-- Table: patient_goals
-- Purpose: Preventive goals the patient sets for themselves, or accepts from the app's
-- suggestions after an assessment (e.g., "Walk 150 minutes a week").
-- Replaces the old doctor-prescribed care_plan_tasks.

CREATE TABLE IF NOT EXISTS public.patient_goals (
    goal_id        integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_id     integer NOT NULL
                   REFERENCES public.patients(patient_id) ON DELETE CASCADE,
    disease_domain character varying(5),               -- NULL = general wellness goal
    description    character varying(255) NOT NULL,
    target_metric  character varying(100),             -- e.g. 'physical_activity_min_week >= 150'
    source         character varying(10) NOT NULL DEFAULT 'self',
    result_id      integer,                            -- the result that prompted it
    is_completed   boolean NOT NULL DEFAULT false,
    completed_at   timestamptz,
    created_at     timestamptz NOT NULL DEFAULT now(),
    -- Composite FK: the result must belong to the same patient
    CONSTRAINT goals_result_fkey FOREIGN KEY (result_id, patient_id)
        REFERENCES public.risk_results (result_id, patient_id) ON DELETE SET NULL (result_id),
    CONSTRAINT goals_domain_check
        CHECK (disease_domain IS NULL OR disease_domain IN ('CVD', 'T2D', 'CRD', 'NEURO')),
    CONSTRAINT goals_source_check CHECK (source IN ('self', 'suggested')),
    CONSTRAINT goals_completed_check CHECK (is_completed = (completed_at IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS ix_goals_patient ON public.patient_goals (patient_id, is_completed);
