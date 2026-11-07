-- 24_family_relative_no.sql
-- Purpose: Each relative is a person. relative_no tells two brothers apart, so the FHRS can sum
-- over each relative (CDSS Data Dictionary, FHRS Guide, Oct 31, 2026).
-- Run on an existing med_db as postgres, then run python -m backend.recalculate_fhrs.
-- Existing rows become relative_no 1. RLS and grants are per table, so 99_security.sql
-- does not need a rerun.

ALTER TABLE public.patient_family_history
    ADD COLUMN relative_no smallint NOT NULL DEFAULT 1,
    ADD CONSTRAINT family_relative_no_check CHECK (relative_no BETWEEN 1 AND 20),
    -- Roles that exist only once (fhrs.SINGLE_RELATIVES; a test checks they match)
    ADD CONSTRAINT family_single_relative_check CHECK (relative_no = 1 OR relative NOT IN (
        'father', 'mother',
        'paternal_grandfather', 'paternal_grandmother', 'maternal_grandfather', 'maternal_grandmother',
        'great_grandfather', 'great_grandmother'
    )),
    DROP CONSTRAINT family_unique_entry,
    ADD CONSTRAINT family_unique_entry
        UNIQUE (patient_id, relative, relative_no, disease_domain, specific_diagnosis);

COMMENT ON COLUMN public.patient_family_history.relative_no IS
    'Which person of this role: brother 1, brother 2. Rows with the same relative and relative_no are one person';
