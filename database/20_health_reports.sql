-- 20_health_reports.sql
-- Table: health_reports
-- Purpose: A saved health report: one frozen snapshot of an assessment's results, top SHAP
-- factors and the care plan items the patient was shown. The PDF and JSON exports are built
-- from this row, so an export always matches what the patient saw.
--
-- Zero Trust:
--   * Patient-owned: Row-Level Security limits each request to its own patient (99_security.sql).
--   * Read-only once saved: UPDATE is revoked from the app (99_security.sql). A report may only
--     be deleted, by the patient or on account deletion.
--   * The composite foreign key means a report can only point to the same patient's assessment.
--
-- report_snapshot example:
--   {"CVD": {"risk_probability": 0.31, "risk_level": "Moderate",
--            "top_factors": ["Your family history of heart disease raised your risk the most", ...],
--            "care_plan": {"base": [...], "addons": [...]}, "disclaimer": "..."},
--    "T2D": {...}, "CRD": {...}, "NEURO": {...}}
-- templates_used example (proves which advice version was shown):
--   {"CVD": {"template_id": 2, "version": "1.0"}, "T2D": {...}, "CRD": {...}, "NEURO": {...}}

CREATE TABLE IF NOT EXISTS public.health_reports (
    report_id       integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_id      integer NOT NULL,
    assessment_id   integer NOT NULL,
    report_snapshot jsonb NOT NULL,
    templates_used  jsonb NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    -- Composite FK: the assessment must belong to the same patient
    CONSTRAINT reports_assessment_fkey FOREIGN KEY (assessment_id, patient_id)
        REFERENCES public.risk_assessments (assessment_id, patient_id) ON DELETE CASCADE,
    CONSTRAINT reports_snapshot_check CHECK (jsonb_typeof(report_snapshot) = 'object'
                                             AND report_snapshot <> '{}'::jsonb),
    CONSTRAINT reports_templates_check CHECK (jsonb_typeof(templates_used) = 'object'
                                              AND templates_used <> '{}'::jsonb),
    -- One saved report per assessment, so a double click cannot save it twice
    CONSTRAINT reports_assessment_key UNIQUE (assessment_id)
);

CREATE INDEX IF NOT EXISTS ix_reports_patient_created ON public.health_reports (patient_id, created_at DESC);
