-- 19_care_plan_templates.sql
-- Table: care_plan_templates
-- Purpose: The 12 physician-reviewed care plan templates (4 diseases x 3 risk tiers).
-- After each assessment the app picks the one active template for each disease and tier.
-- The AI never writes or changes the advice.
--
-- Zero Trust:
--   * The app can only READ this table (see 99_security.sql). Templates are loaded as the
--     database owner (seed_care_plan_templates.sql), never through the app.
--   * Only a reviewed template can be active (reviewer name and review date required).
--   * A reviewed template is read-only, even for the owner: a change is saved as a new
--     version and reviewed again. Old versions stay, so saved reports can still point to them.
--
-- plan_items shape (one JSON object per template):
--   {"base":   [{"no": 1, "category": "...", "action": "...", "why": "...",
--                "source": "...", "basis": "...", "goal": true}, ...],
--    "addons": [{"code": "A1", "trigger_key": "current_smoker", "trigger": "Current smoker",
--                "action": "...", "why": "...", "source": "...", "basis": "...", "goal": true}, ...]}
-- The app shows an add-on only when its trigger_key is true in the patient's saved data.

-- Blocks edits and deletion of a template once it has been reviewed.
-- is_active may still change, so an old version can be switched off when a new one is approved.
CREATE OR REPLACE FUNCTION public.protect_reviewed_template()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.reviewed_on IS NOT NULL THEN
            RAISE EXCEPTION 'Reviewed care plan template % % v% cannot be deleted; set is_active = false instead',
                OLD.disease_domain, OLD.risk_level, OLD.version;
        END IF;
        RETURN OLD;
    END IF;

    IF OLD.reviewed_on IS NOT NULL
       AND (NEW.disease_domain, NEW.risk_level, NEW.version, NEW.plan_items,
            NEW.guideline_source, NEW.disclaimer, NEW.reviewed_by, NEW.reviewed_on)
           IS DISTINCT FROM
           (OLD.disease_domain, OLD.risk_level, OLD.version, OLD.plan_items,
            OLD.guideline_source, OLD.disclaimer, OLD.reviewed_by, OLD.reviewed_on) THEN
        RAISE EXCEPTION 'Reviewed care plan template % % v% is read-only; save the change as a new version',
            OLD.disease_domain, OLD.risk_level, OLD.version;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TABLE IF NOT EXISTS public.care_plan_templates (
    template_id      integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    disease_domain   character varying(5) NOT NULL,
    risk_level       character varying(10) NOT NULL,
    version          character varying(10) NOT NULL,       -- e.g. '1.0', '1.1'
    plan_items       jsonb NOT NULL,                        -- base and add-on items, in display order
    guideline_source text NOT NULL,                         -- main guidelines the template follows
    disclaimer       text NOT NULL DEFAULT
        'This care plan is for educational screening only and is not a medical diagnosis. '
        'Please consult a healthcare provider for medical advice.',
    reviewed_by      character varying(150),                -- physician name and specialty
    reviewed_on      date,
    is_active        boolean NOT NULL DEFAULT false,
    created_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT template_domain_check CHECK (disease_domain IN ('CVD', 'T2D', 'CRD', 'NEURO')),
    CONSTRAINT template_level_check CHECK (risk_level IN ('Low', 'Moderate', 'High')),  -- same as risk_results
    CONSTRAINT template_version_check CHECK (version ~ '^[0-9]+\.[0-9]+$'),
    -- Reviewer name and date are recorded together
    CONSTRAINT template_review_pair_check CHECK ((reviewed_by IS NULL) = (reviewed_on IS NULL)),
    -- "Doctor-validated": an unreviewed draft can never be shown to patients
    CONSTRAINT template_active_reviewed_check CHECK (NOT is_active OR reviewed_on IS NOT NULL),
    CONSTRAINT template_items_shape_check CHECK (
        jsonb_typeof(plan_items) = 'object'
        AND jsonb_typeof(plan_items -> 'base') = 'array'
        AND jsonb_array_length(plan_items -> 'base') > 0
        AND jsonb_typeof(plan_items -> 'addons') = 'array'),
    -- Rule from the care plan: every template lists urgent warning signs
    CONSTRAINT template_warning_signs_check CHECK (
        plan_items -> 'base' @> '[{"category": "Urgent warning signs"}]'),
    -- Rule from the care plan: every Mental Health template carries the NCMH Crisis Hotline (1553)
    CONSTRAINT template_neuro_crisis_check CHECK (
        disease_domain <> 'NEURO'
        OR (plan_items -> 'base' @> '[{"category": "Safety"}]'
            AND position('1553' IN (plan_items -> 'base')::text) > 0)),
    CONSTRAINT template_domain_level_version_key UNIQUE (disease_domain, risk_level, version)
);

-- Exactly one active template per disease and tier
CREATE UNIQUE INDEX IF NOT EXISTS ux_template_one_active
    ON public.care_plan_templates (disease_domain, risk_level) WHERE is_active;

CREATE OR REPLACE TRIGGER trg_template_protect_reviewed
    BEFORE UPDATE OR DELETE ON public.care_plan_templates
    FOR EACH ROW EXECUTE FUNCTION public.protect_reviewed_template();
