-- 21_family_history_fhrs.sql
-- Purpose: Aligns the family history tables with the FHRS guide.
-- Run on an existing med_db as postgres. RLS from 99_security.sql still applies:
-- these statements only change columns and constraints, not the policies.
--
-- Sources:
--   Relative degrees: Thompson & Thompson, Ch. 7 (Pedigrees, Fig. 7.3) - second degree includes
--     grandchildren, nephews and nieces, and half-sibs; degree depends on the number of
--     steps in the pedigree (so great-grandparents are third degree).
--   CVD onset is sex-specific: Harrison's, premature IHD = <55 in male and <65 in female
--     first-degree relatives. This is why every relative now has a sex.

-- ---------------------------------------------------------------------------
-- 1. Relatives: add grandchildren, nieces, nephews, great-grandparents;
--    split cousins by sex
-- ---------------------------------------------------------------------------
ALTER TABLE public.patient_family_history
    DROP CONSTRAINT family_relative_check,
    DROP CONSTRAINT family_unique_entry,
    DROP COLUMN relationship_degree;

ALTER TABLE public.patient_family_history
    ADD CONSTRAINT family_relative_check CHECK (relative IN (
        -- 1st degree
        'father', 'mother', 'brother', 'sister', 'son', 'daughter',
        -- 2nd degree
        'paternal_grandfather', 'paternal_grandmother', 'maternal_grandfather', 'maternal_grandmother',
        'paternal_uncle', 'paternal_aunt', 'maternal_uncle', 'maternal_aunt',
        'half_brother', 'half_sister', 'grandson', 'granddaughter', 'nephew', 'niece',
        -- 3rd degree
        'paternal_male_cousin', 'paternal_female_cousin', 'maternal_male_cousin', 'maternal_female_cousin',
        'great_grandfather', 'great_grandmother'
    )),
    -- Relationship degree, derived automatically (Ri = 0.50 / 0.25 / 0.125 in the FHRS service)
    ADD COLUMN relationship_degree smallint GENERATED ALWAYS AS (CASE
        WHEN relative IN ('father', 'mother', 'brother', 'sister', 'son', 'daughter') THEN 1
        WHEN relative IN ('paternal_grandfather', 'paternal_grandmother',
                          'maternal_grandfather', 'maternal_grandmother',
                          'paternal_uncle', 'paternal_aunt', 'maternal_uncle', 'maternal_aunt',
                          'half_brother', 'half_sister', 'grandson', 'granddaughter',
                          'nephew', 'niece') THEN 2
        ELSE 3
    END) STORED,
    -- Relative's sex, derived automatically (needed for the sex-specific CVD onset cutoffs)
    ADD COLUMN relative_sex character varying(6) GENERATED ALWAYS AS (CASE
        WHEN relative IN ('father', 'brother', 'son', 'paternal_grandfather', 'maternal_grandfather',
                          'paternal_uncle', 'maternal_uncle', 'half_brother', 'grandson', 'nephew',
                          'paternal_male_cousin', 'maternal_male_cousin', 'great_grandfather') THEN 'Male'
        ELSE 'Female'
    END) STORED,
    ADD CONSTRAINT family_unique_entry UNIQUE (patient_id, relative, disease_domain, specific_diagnosis);

-- ---------------------------------------------------------------------------
-- 2. Onset: four levels instead of two (Oi = 3.0 / 2.0 / 1.0 / 0.75)
--    When age_at_diagnosis is known, the FHRS service sets the level from the onset
--    cutoffs in the data dictionary. When it is unknown, the patient may choose a level,
--    or leave it as 'unknown' (scored as typical onset, Oi = 1.0).
-- ---------------------------------------------------------------------------
ALTER TABLE public.patient_family_history
    DROP CONSTRAINT family_onset_check,
    ALTER COLUMN onset_classification TYPE character varying(12),
    ADD CONSTRAINT family_onset_check
        CHECK (onset_classification IN ('severe_early', 'early', 'typical', 'late', 'unknown'));

-- ---------------------------------------------------------------------------
-- 3. Verification factor (Si): "Was this diagnosis confirmed by a doctor?"
--    yes = 1.0; no or not_sure = 0.5 (author-defined weights; not stated in the sources)
-- ---------------------------------------------------------------------------
ALTER TABLE public.patient_family_history
    ADD COLUMN diagnosis_confirmed character varying(8) NOT NULL DEFAULT 'not_sure',
    ADD CONSTRAINT family_confirmed_check CHECK (diagnosis_confirmed IN ('yes', 'no', 'not_sure'));

-- ---------------------------------------------------------------------------
-- 4. FHRS results: five risk bands instead of three; environmental multiplier
--    Ed = 1.0 + sum of shared household exposures, so it can never be below 1.0
-- ---------------------------------------------------------------------------
ALTER TABLE public.patient_fhrs_stratification
    DROP CONSTRAINT fhrs_band_check,
    DROP CONSTRAINT fhrs_modifier_check,
    ADD CONSTRAINT fhrs_band_check
        CHECK (risk_band IN ('Baseline', 'Mild', 'Moderate', 'Pronounced', 'Critical')),
    ADD CONSTRAINT fhrs_modifier_check CHECK (environmental_modifier BETWEEN 1.0 AND 5.0);

COMMENT ON COLUMN public.patient_fhrs_stratification.risk_band IS
    'Band 0 Baseline (0.0), 1 Mild (0.10-1.75), 2 Moderate (1.76-3.50), 3 Pronounced (3.51-6.00), 4 Critical (>6.00)';