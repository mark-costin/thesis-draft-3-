-- seed_test_models.sql
-- TEST DATABASE ONLY (med_db_test). Never load into med_db.
-- Four placeholder rows in model_versions so the risk endpoint tests can save results
-- (risk_results.model_version_id must point to a registered model). They are NOT active and
-- point to no real file: the tests load their own throw-away models and use these ids.
-- Run as postgres (med_app cannot write model_versions). Loaded by tests/create_test_db.sh,
-- tests/create_test_db.ps1 and database/setup_windows.ps1 -Test. Safe to rerun.

INSERT INTO public.model_versions
    (disease_domain, version, algorithm, artifact_path, artifact_sha256, feature_list, thresholds, is_active)
SELECT d, 'test-only', 'XGBoost', 'ml/artifacts/test-only-' || lower(d) || '.json', repeat('0', 64),
       '["test"]'::jsonb, '{"moderate": 0.2, "high": 0.5}'::jsonb, false
FROM unnest(ARRAY['CVD', 'T2D', 'CRD', 'NEURO']) AS d
ON CONFLICT (disease_domain, version) DO NOTHING;
