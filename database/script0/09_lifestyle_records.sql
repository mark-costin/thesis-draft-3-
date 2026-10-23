-- 09_lifestyle_records.sql
-- Table: lifestyle_records
-- Purpose: Self-reported lifestyle factors, shared by all four models.
-- Merges smoking_history (old CVD table) and tobacco_exposure (old respiratory table),
-- plus activity, sleep and diet fields from the old metric tables.

CREATE TABLE IF NOT EXISTS public.lifestyle_records (
    lifestyle_id               integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_id                 integer NOT NULL
                               REFERENCES public.patients(patient_id) ON DELETE CASCADE,
    smoking_status             character varying(10),
    pack_years                 numeric(5,1),         -- packs per day x years smoked
    physical_activity_min_week smallint,             -- moderate-to-vigorous minutes per week
    sedentary_hours_day        numeric(3,1),
    sleep_hours_day            numeric(3,1),
    sodium_mg_day              smallint,             -- estimated daily intake
    carbs_g_day                smallint,             -- estimated daily intake
    occupational_dust          boolean,              -- regular dust, fume or chemical exposure at work
    substance_use              character varying(10),
    recorded_at                timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT lifestyle_smoking_check
        CHECK (smoking_status IS NULL OR smoking_status IN ('never', 'former', 'current')),
    CONSTRAINT lifestyle_pack_years_check
        CHECK (pack_years IS NULL OR pack_years BETWEEN 0 AND 200),
    CONSTRAINT lifestyle_never_smoker_check
        CHECK (smoking_status IS DISTINCT FROM 'never' OR coalesce(pack_years, 0) = 0),
    CONSTRAINT lifestyle_activity_check
        CHECK (physical_activity_min_week IS NULL OR physical_activity_min_week BETWEEN 0 AND 10080),
    CONSTRAINT lifestyle_sedentary_check
        CHECK (sedentary_hours_day IS NULL OR sedentary_hours_day BETWEEN 0 AND 24),
    CONSTRAINT lifestyle_sleep_check
        CHECK (sleep_hours_day IS NULL OR sleep_hours_day BETWEEN 0 AND 24),
    CONSTRAINT lifestyle_sodium_check
        CHECK (sodium_mg_day IS NULL OR sodium_mg_day BETWEEN 0 AND 20000),
    CONSTRAINT lifestyle_carbs_check
        CHECK (carbs_g_day IS NULL OR carbs_g_day BETWEEN 0 AND 1500),
    CONSTRAINT lifestyle_substance_check
        CHECK (substance_use IS NULL OR substance_use IN ('none', 'past', 'current'))
);

CREATE INDEX IF NOT EXISTS ix_lifestyle_patient_recorded ON public.lifestyle_records (patient_id, recorded_at DESC);
