-- 08_lab_results.sql
-- Table: lab_results
-- Purpose: One row per lab value, from OCR or manual entry.
-- Patient-in-the-loop: the models only use rows where is_confirmed = true.
-- Replaces the NOT NULL lab columns of the old metric tables, so patients without
-- lab results can still use the app (XGBoost handles missing values).
-- Removed (clinic or hospital-only tests): LVEF, troponin, NT-proBNP, arterial blood gas,
-- FeNO, spirometry, fasting insulin, C-peptide.

CREATE TABLE IF NOT EXISTS public.lab_results (
    lab_result_id     integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_id        integer NOT NULL
                      REFERENCES public.patients(patient_id) ON DELETE CASCADE,
    document_id       integer,                       -- NULL = manual entry
    test_code         character varying(20) NOT NULL,
    value             numeric(10,3) NOT NULL,        -- in the standard unit below
    unit              character varying(15) NOT NULL,
    value_as_reported numeric(10,3),                 -- what the document said, before conversion
    unit_as_reported  character varying(15),         -- e.g. mmol/L on many Philippine lab reports
    source            character varying(10) NOT NULL,
    is_confirmed      boolean NOT NULL DEFAULT false,
    confirmed_at      timestamptz,
    collected_on      date,                          -- date the sample was taken
    created_at        timestamptz NOT NULL DEFAULT now(),
    -- Composite FK: the document must belong to the same patient (FK checks skip RLS,
    -- so a plain document_id FK would let a patient link another patient's document)
    CONSTRAINT lab_document_fkey FOREIGN KEY (document_id, patient_id)
        REFERENCES public.documents (document_id, patient_id) ON DELETE SET NULL (document_id),
    CONSTRAINT lab_test_code_check CHECK (test_code IN (
        'TOTAL_CHOLESTEROL', 'HDL', 'LDL', 'TRIGLYCERIDES',   -- lipid panel
        'FPG', 'HBA1C', 'OGTT_2H',                            -- glycemic
        'CREATININE', 'EGFR', 'URIC_ACID', 'UACR',            -- kidney / metabolic
        'HS_CRP',                                             -- inflammation
        'EOSINOPHILS', 'IGE',                                 -- allergy / respiratory
        'TSH', 'VITAMIN_B12', 'FOLATE'                        -- neuro / mental health
    )),
    -- Each test is stored in one fixed unit, so the models always get comparable values
    CONSTRAINT lab_unit_check CHECK (unit = CASE test_code
        WHEN 'TOTAL_CHOLESTEROL' THEN 'mg/dL'  WHEN 'HDL'        THEN 'mg/dL'
        WHEN 'LDL'               THEN 'mg/dL'  WHEN 'TRIGLYCERIDES' THEN 'mg/dL'
        WHEN 'FPG'               THEN 'mg/dL'  WHEN 'OGTT_2H'    THEN 'mg/dL'
        WHEN 'HBA1C'             THEN '%'      WHEN 'CREATININE' THEN 'mg/dL'
        WHEN 'EGFR'              THEN 'mL/min/1.73m2'
        WHEN 'URIC_ACID'         THEN 'mg/dL'  WHEN 'UACR'       THEN 'mg/g'
        WHEN 'HS_CRP'            THEN 'mg/L'   WHEN 'EOSINOPHILS' THEN 'cells/uL'
        WHEN 'IGE'               THEN 'IU/mL'  WHEN 'TSH'        THEN 'mIU/L'
        WHEN 'VITAMIN_B12'       THEN 'pg/mL'  WHEN 'FOLATE'     THEN 'ng/mL'
    END),
    CONSTRAINT lab_value_check CHECK (value > 0 AND value < 100000),
    CONSTRAINT lab_source_check CHECK (source IN ('ocr', 'manual')),
    CONSTRAINT lab_confirmed_check CHECK (is_confirmed = (confirmed_at IS NOT NULL)),
    CONSTRAINT lab_reported_pair_check CHECK ((value_as_reported IS NULL) = (unit_as_reported IS NULL))
);

CREATE INDEX IF NOT EXISTS ix_lab_patient_test ON public.lab_results (patient_id, test_code, collected_on DESC);
CREATE INDEX IF NOT EXISTS ix_lab_document ON public.lab_results (document_id);
