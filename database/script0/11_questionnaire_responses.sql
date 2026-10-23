-- 11_questionnaire_responses.sql
-- Table: questionnaire_responses
-- Purpose: Self-administered screening questionnaires.
--   PHQ-9 (depression), GAD-7 (anxiety), PSQI (sleep quality)       -> NEURO model
--   mMRC (breathlessness), CAT (COPD symptoms), ACT (asthma control) -> CRD model
-- Removed (clinician-administered, not possible in a patient-only app):
-- MMSE, MoCA, HAM-D, UPDRS part III, tremor rating, FAQ.
--
-- SAFETY: PHQ-9 item 9 asks about thoughts of self-harm. requires_crisis_prompt is set
-- automatically when item 9 is answered above 0. The app must then immediately show crisis
-- resources (e.g., NCMH Crisis Hotline 1553), because no clinician reviews the results.

CREATE TABLE IF NOT EXISTS public.questionnaire_responses (
    response_id           integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_id            integer NOT NULL
                          REFERENCES public.patients(patient_id) ON DELETE CASCADE,
    instrument            character varying(5) NOT NULL,
    item_responses        jsonb NOT NULL,          -- array of item answers, in question order
    total_score           smallint NOT NULL,
    requires_crisis_prompt boolean GENERATED ALWAYS AS (
        instrument = 'PHQ9' AND coalesce((item_responses ->> 8)::integer, 0) >= 1
    ) STORED,
    completed_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT questionnaire_instrument_check
        CHECK (instrument IN ('PHQ9', 'GAD7', 'PSQI', 'MMRC', 'CAT', 'ACT')),
    CONSTRAINT questionnaire_items_array_check
        CHECK (jsonb_typeof(item_responses) = 'array'),
    -- Valid score range (and item count where fixed) for each instrument
    CONSTRAINT questionnaire_score_check CHECK (CASE instrument
        WHEN 'PHQ9' THEN total_score BETWEEN 0 AND 27 AND jsonb_array_length(item_responses) = 9
        WHEN 'GAD7' THEN total_score BETWEEN 0 AND 21 AND jsonb_array_length(item_responses) = 7
        WHEN 'PSQI' THEN total_score BETWEEN 0 AND 21
        WHEN 'MMRC' THEN total_score BETWEEN 0 AND 4  AND jsonb_array_length(item_responses) = 1
        WHEN 'CAT'  THEN total_score BETWEEN 0 AND 40 AND jsonb_array_length(item_responses) = 8
        WHEN 'ACT'  THEN total_score BETWEEN 5 AND 25 AND jsonb_array_length(item_responses) = 5
    END)
);

CREATE INDEX IF NOT EXISTS ix_questionnaire_patient ON public.questionnaire_responses
    (patient_id, instrument, completed_at DESC);
