-- 10_health_history.sql
-- Table: patient_health_history
-- Purpose: The patient's own past conditions that rarely change (one row per patient).
-- Collects the yes/no history flags from the old T2D, respiratory and neuro tables.
-- NULL = the patient doesn't know or hasn't answered.
-- Removed as leakage (they describe existing disease, not risk): annual_exacerbations,
-- seizure_frequency.

CREATE TABLE IF NOT EXISTS public.patient_health_history (
    patient_id           integer PRIMARY KEY
                         REFERENCES public.patients(patient_id) ON DELETE CASCADE,
    gestational_diabetes boolean,     -- female patients only (validated in the app)
    acanthosis_nigricans boolean,     -- dark, velvety skin patches (shown with a picture in the app)
    allergic_rhinitis    boolean,
    childhood_asthma     boolean,
    tbi_history          boolean,     -- past traumatic brain injury
    updated_at           timestamptz NOT NULL DEFAULT now()
);

CREATE TRIGGER trg_health_history_updated_at
    BEFORE UPDATE ON public.patient_health_history
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
