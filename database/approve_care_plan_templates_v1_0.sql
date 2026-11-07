-- approve_care_plan_templates_v1_0.sql
-- DO NOT RUN until the two placeholders below are filled in from the signed form. As it stands
-- it stops with an error and changes nothing.
-- Purpose: Records the physician's approval of the 12 version 1.0 care plan templates and
-- switches them on. The signed form is not in this repository.
--
-- Run as postgres on med_db, AFTER seed_care_plan_templates.sql. Not numbered on purpose: the
-- test database keeps the drafts, and the tests depend on that.
-- This cannot be undone: a reviewed template is read-only, even for the owner
-- (19_care_plan_templates.sql). Check the name and date against the form BEFORE running it.
-- Safe to run twice: rows already reviewed are left alone.

DO $$
DECLARE
    approved integer;
BEGIN
    IF current_database() <> 'med_db' THEN
        RAISE EXCEPTION 'Wrong database: %. Templates are approved on med_db only.', current_database();
    END IF;

    UPDATE public.care_plan_templates
       SET reviewed_by = 'Dr. <name>, <specialty>',
           reviewed_on = DATE '<yyyy-mm-dd>',
           is_active   = true
     WHERE version = '1.0' AND reviewed_on IS NULL;

    SELECT count(*) INTO approved FROM public.care_plan_templates
     WHERE version = '1.0' AND is_active AND reviewed_on IS NOT NULL;
    IF approved <> 12 THEN  -- raising here also undoes the update above
        RAISE EXCEPTION 'Expected 12 approved v1.0 templates, found %. Run seed_care_plan_templates.sql first.', approved;
    END IF;
END;
$$;
