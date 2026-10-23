-- 07_vitals_records.sql
-- Table: vitals_records
-- Purpose: Self-reported vitals, entered once and shared by all four models.
-- Replaces the sbp/dbp/bmi/resting_hr/spo2 columns that were duplicated across the
-- old CVD, T2D and respiratory tables.

CREATE TABLE IF NOT EXISTS public.vitals_records (
    vitals_id          integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_id         integer NOT NULL
                       REFERENCES public.patients(patient_id) ON DELETE CASCADE,
    sbp                smallint,                 -- systolic BP, mmHg
    dbp                smallint,                 -- diastolic BP, mmHg
    resting_hr         smallint,                 -- beats per minute
    weight_kg          numeric(5,1),
    height_cm          numeric(5,1),
    bmi                numeric(4,1) GENERATED ALWAYS AS
                       (round(weight_kg / ((height_cm / 100) * (height_cm / 100)), 1)) STORED,
    waist_cm           numeric(5,1),
    blood_glucose_mgdl numeric(5,1),             -- home glucometer reading
    glucose_context    character varying(15),    -- when the glucose reading was taken
    spo2               numeric(4,1),             -- %, home pulse oximeter
    measured_at        timestamptz NOT NULL DEFAULT now(),
    created_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT vitals_sbp_check        CHECK (sbp IS NULL OR sbp BETWEEN 70 AND 250),
    CONSTRAINT vitals_dbp_check        CHECK (dbp IS NULL OR dbp BETWEEN 40 AND 150),
    CONSTRAINT vitals_bp_pair_check    CHECK ((sbp IS NULL) = (dbp IS NULL)),
    CONSTRAINT vitals_bp_order_check   CHECK (sbp IS NULL OR sbp > dbp),
    CONSTRAINT vitals_hr_check         CHECK (resting_hr IS NULL OR resting_hr BETWEEN 30 AND 220),
    CONSTRAINT vitals_weight_check     CHECK (weight_kg IS NULL OR weight_kg BETWEEN 20 AND 300),
    CONSTRAINT vitals_height_check     CHECK (height_cm IS NULL OR height_cm BETWEEN 100 AND 250),
    CONSTRAINT vitals_waist_check      CHECK (waist_cm IS NULL OR waist_cm BETWEEN 40 AND 200),
    CONSTRAINT vitals_glucose_check    CHECK (blood_glucose_mgdl IS NULL OR blood_glucose_mgdl BETWEEN 20 AND 600),
    CONSTRAINT vitals_glucose_context_check
        CHECK (glucose_context IS NULL OR glucose_context IN ('fasting', 'random', 'post_meal_2h')),
    CONSTRAINT vitals_glucose_pair_check CHECK ((blood_glucose_mgdl IS NULL) = (glucose_context IS NULL)),
    CONSTRAINT vitals_spo2_check       CHECK (spo2 IS NULL OR spo2 BETWEEN 70 AND 100),
    CONSTRAINT vitals_not_empty_check  CHECK (num_nonnulls(sbp, resting_hr, weight_kg, height_cm,
                                                           waist_cm, blood_glucose_mgdl, spo2) >= 1)
);

CREATE INDEX IF NOT EXISTS ix_vitals_patient_measured ON public.vitals_records (patient_id, measured_at DESC);
