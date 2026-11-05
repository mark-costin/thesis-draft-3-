# Sprint 6 integration checks

Two parts:

1. **Automatic:** `uv run python -m pytest tests/test_end_to_end.py -v` runs the whole flow
   through the API, checks the rows each step saves, the audit events, and two patients side by side.
2. **By hand:** the steps below, with the real app (API + Reflex) in two browsers and DBeaver.
   Write the result (Pass / Fail + note) in the last column and copy it into the Chapter 4 test cases.

Start the app first (three terminals: API on 8000, OCR worker if your PC runs OCR, Reflex on 3000 or 3001).

## A. Two patients, two browsers

Use **Chrome** for patient A and an **Edge (or Chrome Incognito)** window for patient B, so they do not share a login.

| # | Step | Expected | Result |
|---|---|---|---|
| A1 | Create patient A in Chrome, patient B in Edge | Both reach the dashboard with their own first name | |
| A2 | A: save vitals, lifestyle, one family entry, PHQ-9, one typed lab | Each screen says it is saved | |
| A3 | B: open Dashboard, Vitals, Family history, Questionnaires, Scan, Reports | All empty: nothing of A's shows | |
| A4 | A: run an assessment and save the report (needs the models from Phase 0) | Results and the saved report show for A | |
| A5 | B: open Results and Reports | "No assessment yet" / no reports | |
| A6 | B: type A's report address in the URL bar (`/results`, `/reports`) after logging in | Still only B's own data | |
| A7 | Log out A in Chrome, press Back | Goes to the login page, no data shown | |

## B. Database rows after each screen (DBeaver, connected as `postgres` to `med_db`)

Replace `:pid` with patient A's `patient_id` (first query).

```sql
SELECT p.patient_id, u.email FROM patients p JOIN users u USING (user_id) ORDER BY p.patient_id DESC LIMIT 5;

SELECT 'vitals' t, count(*) FROM vitals_records WHERE patient_id = :pid
UNION ALL SELECT 'lifestyle', count(*) FROM lifestyle_records WHERE patient_id = :pid
UNION ALL SELECT 'health_history', count(*) FROM patient_health_history WHERE patient_id = :pid
UNION ALL SELECT 'questionnaires', count(*) FROM questionnaire_responses WHERE patient_id = :pid
UNION ALL SELECT 'family_history', count(*) FROM patient_family_history WHERE patient_id = :pid
UNION ALL SELECT 'fhrs', count(*) FROM patient_fhrs_stratification WHERE patient_id = :pid
UNION ALL SELECT 'documents', count(*) FROM documents WHERE patient_id = :pid
UNION ALL SELECT 'lab_results', count(*) FROM lab_results WHERE patient_id = :pid
UNION ALL SELECT 'risk_assessments', count(*) FROM risk_assessments WHERE patient_id = :pid
UNION ALL SELECT 'risk_results', count(*) FROM risk_results WHERE patient_id = :pid
UNION ALL SELECT 'shap', count(*) FROM shap_explanations WHERE patient_id = :pid
UNION ALL SELECT 'goals', count(*) FROM patient_goals WHERE patient_id = :pid
UNION ALL SELECT 'health_reports', count(*) FROM health_reports WHERE patient_id = :pid;
```

| Screen | Table that must get a new row |
|---|---|
| Create account | `users`, `patients` |
| Vitals | `vitals_records` |
| Lifestyle | `lifestyle_records` (TSH / B12 typed here go to `lab_results`) |
| Health history | `patient_health_history` (one row, updated on each save) |
| Family history | `patient_family_history`, then `patient_fhrs_stratification` |
| Questionnaires | `questionnaire_responses` (one per questionnaire saved) |
| Scan & assess: upload | `documents` (and `lab_results` with `is_confirmed = false` after OCR) |
| Scan & assess: confirm / typed labs | `lab_results` with `is_confirmed = true` |
| Run assessment | `risk_assessments` (1), `risk_results` (4), `shap_explanations` |
| Add as goal / new goal | `patient_goals` |
| Save report | `health_reports` |

## C. Audit log (DBeaver as `postgres`)

```sql
SELECT occurred_at, event_type, actor_user_id, patient_id, payload
FROM audit_ledger WHERE patient_id = :pid ORDER BY audit_id;

SELECT public.verify_audit_chain();   -- must be NULL (the chain is intact)
```

Check that these appear after the flow: `REGISTER`, `LOGIN_SUCCESS`, `DATA_CREATED` (each table above),
`DOCUMENT_UPLOADED`, `DOCUMENT_VERIFIED`, `RISK_ASSESSMENT_RUN`, `REPORT_EXPORTED` (PDF download),
`DATA_EXPORTED` (Download my data), `LOGOUT`. The `payload` column must hold IDs, table and field names only:
no values, names or emails.

## D. Failure cases

| # | What to do | Expected | Covered by test |
|---|---|---|---|
| D1 | Stop the API (Ctrl+C in its terminal), then click Save on Vitals | "Cannot reach the server…"; nothing saved; start the API and Save works | frontend PR: `test_network_and_rate_limit_messages`, `test_the_key_is_kept_for_a_retry_but_not_shared` |
| D2 | Double-click Save vitals (or Save report) fast | One new row only (check part B) | frontend PR: `test_a_double_click_saves_one_record`; `tests/test_middleware.py` |
| D3 | PHQ-9 with item 9 above 0, double-click Save | Crisis dialog still shows; one row | frontend PR: `test_a_double_submit_keeps_the_crisis_prompt` |
| D4 | Upload with OCR off (this laptop) | "OCR is not running…" after a few checks; type the values by hand | `test_with_ocr_off_the_upload_stays_pending` |
| D5 | OCR fails (engine error) | Document "failed" with a safe message; type values by hand | `test_an_engine_error_marks_the_document_failed_with_its_safe_message` |
| D6 | OCR finds nothing | Goes to review with no values; Reject, then type values | `test_a_report_with_no_values_found_still_goes_to_review` |
| D7 | Upload the same file twice | "You already uploaded this file" | `test_same_file_twice_is_refused_per_patient` |
| D8 | Upload a .txt renamed to .png | Refused | `test_bad_files_are_refused` |
| D9 | Run assessment with something missing | Button stays off; the missing list shows what to enter | `tests/test_readiness.py` |
| D10 | Download PDF on Windows without GTK | "PDF export is not available…"; JSON still works | `test_without_the_pdf_library_the_download_says_so` |
| D11 | Wrong password 5 times | Account locked message | `tests/test_login.py` |

## E. Bugs

Put every Fail from A–D in GitHub Issues (label `bug`, and `critical` when data is lost, wrong, or shown to
the wrong patient). Sprint 6 is done when there are no open `critical` issues.
