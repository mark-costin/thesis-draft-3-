# Security tests (Sprint 7)

The table shows each attack, where the defense is, and the test that checks it.
**New** means the test was added in `tests/test_security.py`. The other tests already existed.

Run them all with `uv run python -m pytest`, or just this file with `uv run python -m pytest tests/test_security.py -v`.

## 1. Login and tokens

| Attack | Defense | Test |
|---|---|---|
| Guess passwords many times | Lock for 15 min after 5 failures, even with the right password | `test_login.py::test_repeated_wrong_passwords_lock_the_account` |
| Many login tries from one IP | SlowAPI rate limit (10/min login, reset limits) | `test_middleware.py::test_login_is_rate_limited_per_ip`, `test_reset_request_is_rate_limited` |
| Find out which emails are registered | Same answer for wrong password and unknown email | `test_login.py::test_wrong_password_and_unknown_email_look_the_same`, `test_password_reset.py::test_unknown_email_gets_the_same_answer_and_no_email` |
| Token signed with a wrong key, expired, wrong version | JWT check in `security.decode_access_token` + `token_version` | `test_login.py::test_bad_tokens_are_rejected` |
| Change the `patient_id` inside a real token | Signature no longer matches | **New** `test_changing_the_patient_id_inside_a_real_token_breaks_it` |
| Token with no signature (`alg: none`) | Only HS256 is accepted | **New** `test_unsigned_token_is_refused` |
| Token signed with another algorithm | Only HS256 is accepted | **New** `test_token_signed_with_another_algorithm_is_refused` |
| Signed token with missing or wrong-type claims | `sub`, `iat`, `exp` required; `patient_id`, `tv` must be numbers | **New** `test_correctly_signed_token_with_bad_claims_is_refused` |
| Old token after logout or password reset | `token_version` goes up | `test_login.py::test_logout_revokes_old_tokens`, `test_password_reset.py::test_reset_logs_out_existing_sessions` |
| Old token after account deletion | User row is gone | `test_account.py::test_deleted_account_cannot_log_in_and_old_token_stops_working` |
| Reuse or guess a reset link | One-time, hashed, expiring token | `test_password_reset.py::test_token_works_only_once`, `test_expired_token_is_rejected`, `test_bad_confirm_requests` |
| Personal data inside the token | Token holds only IDs | `test_login.py::test_token_carries_ids_but_no_personal_data` |

## 2. One patient reading or changing another patient's data

| Attack | Defense | Test |
|---|---|---|
| Read or write another patient's rows in the database | Row-level security (RLS) | `test_isolation.py` (all tests) |
| Use another patient's record ID in the URL | Query filters on the token's `patient_id`; RLS is the second layer | `*_are_per_patient`, `test_another_patient_cannot_*` in each feature file; `test_reports.py::test_report_queries_filter_on_patient_id_even_if_rls_shows_someone_else` |
| Put another patient's ID in the URL query or a header | Identity comes only from the signed token | **New** `test_patient_id_in_the_url_or_headers_is_ignored` |
| Send `patient_id`, `user_id`, `is_admin`, consent fields in the body | `extra="forbid"` on every request schema | `test_register.py::test_extra_fields_are_rejected`, `test_profile.py::test_invalid_edits_are_rejected`, `test_goals.py::test_invalid_goals_are_rejected`, `test_login.py::test_login_rejects_extra_fields` |
| A new table with patient data but no RLS | Every table with `patient_id` must have RLS | **New** `test_every_patient_table_has_row_level_security_and_is_not_owned_by_med_app` |
| A new route that forgets the login check | Every route except the public ones must use `get_current_user` | **New** `test_every_other_route_needs_a_login` |
| Replay another patient's saved response with their Idempotency-Key | Keys are scoped to the caller's token | `test_middleware.py::test_keys_are_scoped_to_the_caller` |

## 3. Injection

| Attack | Defense | Test |
|---|---|---|
| SQL injection in the login email | SQLAlchemy bound parameters | **New** `test_sql_in_the_login_email_does_not_log_in` |
| SQL injection in a name | Bound parameters; text saved as typed | **New** `test_sql_in_a_name_is_saved_as_plain_text` |
| SQL injection in a goal | Bound parameters; text saved as typed | **New** `test_sql_in_a_goal_is_saved_as_plain_text` |
| SQL injection in a URL ID or filter | IDs must be whole numbers, filters must be allowed words | **New** `test_sql_in_the_url_is_refused` |
| Script (XSS) in a goal | nh3 removes HTML, repeated until stable | `test_goals.py::test_html_is_removed_from_goal_text`, `test_escaped_html_does_not_come_back_as_tags`, `test_tricky_html_is_cleaned` |
| Script in a name shown in the PDF report | Every value is HTML-escaped in the PDF page | `test_reports.py::test_the_pdf_page_escapes_text` |
| Script in a name read by the browser as a page | JSON content type + `X-Content-Type-Options: nosniff` | **New** `test_health_data_responses_are_json_not_html` |

## 4. Files and models

| Attack | Defense | Test |
|---|---|---|
| Upload a file pretending to be another type | Type is read from the file bytes, not the name | `test_documents.py::test_type_comes_from_the_file_not_the_name`, `test_bad_files_are_refused` |
| Path tricks in the file name | Stored under a random UUID only | `test_documents.py::test_uploaded_name_is_never_used_on_disk`, `test_storage_refuses_names_that_are_not_uuids` |
| Steal files from the disk | Files are encrypted at rest | `test_documents.py::test_files_on_disk_are_unreadable_without_the_key` |
| Change a stored file | SHA-256 is checked on download | `test_documents.py::test_changed_file_on_disk_is_detected` |
| GPS location left inside a photo | EXIF and other metadata are removed on upload | `test_image_privacy.py` (PR #34) |
| Swap a model file | SHA-256 checked before loading; only `ml/artifacts` allowed | `test_model_registry.py::test_a_changed_file_is_refused`, `test_files_outside_the_model_folder_are_refused`, `test_a_file_swapped_after_the_check_is_never_loaded` |

## 5. If an attacker gets the app's database login (med_app)

| Attack | Defense | Test |
|---|---|---|
| Turn off RLS, drop tables or policies | med_app owns no table | **New** `test_med_app_cannot[drop-table, truncate, disable-rls, drop-policy]` |
| Become a stronger role | med_app is not superuser, cannot create roles, cannot `SET ROLE postgres` | **New** `test_med_app_is_an_ordinary_role`, `test_med_app_cannot[create-role, become-postgres]` |
| Add own tables or functions | No CREATE on schema `public` | **New** `test_med_app_cannot[create-table, create-function]` |
| Change saved risk results or SHAP values | No UPDATE grant: results are append-only | **New** `test_med_app_cannot[edit-risk-result, edit-assessment, edit-shap]` |
| Turn on or add a model | `model_versions` is read-only | **New** `test_med_app_cannot[activate-model, add-model]`, `test_model_registry.py::test_the_app_cannot_register_a_model` |
| Read or change the audit trail | No grant on `audit_ledger`; writes only through `log_audit` | `test_audit.py::test_app_cannot_touch_the_ledger_directly`, `test_app_cannot_run_the_admin_chain_check`, `test_isolation.py::test_app_cannot_read_audit_ledger` |
| Change consent, care plan templates, saved reports | No UPDATE grant | `test_profile.py::test_med_app_cannot_update_consent`, `test_isolation.py::test_app_can_read_but_not_write_care_plan_templates`, `test_saved_report_cannot_be_edited` |
| Read server files, run programs, read role passwords | Not superuser | **New** `test_med_app_cannot[read-server-file, import-server-file, run-server-program, read-role-passwords]` |

## 6. Browser and network

| Attack | Defense | Test |
|---|---|---|
| Another website calls the API from a user's browser | CORS allows only the frontend origin | `test_middleware.py::test_other_origins_are_refused` |
| Show the API inside a frame (clickjacking), guess content types, leak URLs | `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: no-referrer` on every response | **New** `test_security_headers_are_on_every_response` |
| Health or personal data kept in a browser or proxy cache | `Cache-Control: no-store` | **New** `test_personal_data_is_not_cached`; the health data routes already test it in their own files |
| Login token kept in a cache or in the idempotency store | `Cache-Control: no-store` on login, so the store keeps no body | **New** `test_login_token_is_not_cached_or_kept_for_replay` |
| Double submit creates two records | Idempotency-Key | `test_middleware.py::test_same_idempotency_key_twice_creates_one_record` |

## Not covered by automated tests (deployment, Sprint 8)

- HTTPS and HSTS: set on the reverse proxy (nginx or Caddy), not in FastAPI.
- Secrets: `.env` is never committed. The Postgres password in `docker-compose.dev.yml` should move to `.env`.
- Dependency scanning: run `uvx pip-audit` before the defense.
