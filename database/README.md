# Database Scripts (patient-only, Zero Trust)

PostgreSQL 16 schema for the self-hosted patient web app. Docker runs every file in this
folder in alphabetical order the first time the database volume is created.

## Run order

| File | Creates | Purpose |
| --- | --- | --- |
| `00_roles.sh` | `med_app` role | Least-privilege login role for the app (never connect as `postgres`) |
| `01_functions.sql` | helper functions | `updated_at` trigger, current patient/user id for RLS, append-only guard |
| `02_users.sql` | `users` | Credentials, lockout, token revocation (no health data) |
| `03_patients.sql` | `patients` | Minimal profile + RA 10173 consent |
| `04_password_reset_tokens.sql` | `password_reset_tokens` | Hashed, single-use, 1-hour reset tokens |
| `05_audit_ledger.sql` | `audit_ledger`, `log_audit()`, `verify_audit_chain()` | Hash-chained, append-only security log |
| `06_documents.sql` | `documents` | Uploaded lab files + OCR status |
| `07_vitals_records.sql` | `vitals_records` | Self-reported BP, heart rate, weight, height, BMI, glucose, SpO2 |
| `08_lab_results.sql` | `lab_results` | One lab value per row; models use only confirmed values |
| `09_lifestyle_records.sql` | `lifestyle_records` | Smoking, activity, sleep, diet, occupational dust |
| `10_health_history.sql` | `patient_health_history` | Patient's own past conditions (yes/no) |
| `11_questionnaire_responses.sql` | `questionnaire_responses` | PHQ-9, GAD-7, PSQI, mMRC, CAT, ACT |
| `12_family_history.sql` | `patient_family_history` | Pedigree: relative x condition |
| `13_fhrs_stratification.sql` | `patient_fhrs_stratification` | Family History Risk Score per disease |
| `14_model_versions.sql` | `model_versions` | Registry of allowed model files + thresholds |
| `15_risk_assessments.sql` | `risk_assessments` | One row per screening run + input snapshot |
| `16_risk_results.sql` | `risk_results` | Four disease probabilities and tiers |
| `17_shap_explanations.sql` | `shap_explanations` | Per-feature SHAP values, ranked |
| `18_patient_goals.sql` | `patient_goals` | Patient's own preventive goals |
| `99_security.sql` | RLS policies, grants | Zero Trust enforcement (runs last) |

## Windows setup (no Docker)

`00_roles.sh` only runs inside Docker. On Windows, use `windows_setup.sql`, which creates the
`med_app` role and runs every script in the right order.

1. In DBeaver, connected as `postgres`, create an empty database named `med_db`.
2. Open `database/windows_setup.sql`, set the active database to `med_db` (toolbar shows `public@med_db`),
   and run the whole script (Alt+X).
3. Refresh `med_db`: Schemas -> public -> Tables should list 17 tables.
4. Copy `.env.example` to `.env` (the dev password `CHANGE_ME` already matches the script).
5. Check the connection:

   ```
   uv run python backend/check_db.py
   ```

   Expected: `connected as: ('med_db', 'med_app')`, `tables: 17`, `patients visible: 0`
   (0 is correct: RLS hides every patient row until a patient id is set).

If step 5 fails with `password authentication failed for user "med_app"`, the role already existed
with another password. Run `ALTER ROLE med_app WITH LOGIN PASSWORD 'CHANGE_ME';` as `postgres`.

When a script in this folder is added or changed, update `windows_setup.sql` in the same pull request.

## Zero Trust principles applied

- **Least privilege:** the app connects as `med_app`, which cannot bypass RLS, create tables,
  read the audit log, or change the model registry.
- **Deny by default:** Row-Level Security on every patient table. With no patient id set,
  queries return nothing.
- **Never trust the app's input:** composite foreign keys stop a patient from linking their
  rows to another patient's document, assessment or result (foreign-key checks skip RLS).
- **Tamper evidence:** the audit log is hash-chained inside the database, append-only even for
  the owner, and checkable with `SELECT verify_audit_chain();`.
- **Data minimization (RA 10173):** no religion, gender, address or emergency contacts; OCR
  text is cleared after verification.
- **Right to erasure:** deleting a user cascades to all their data; the audit log keeps only ids.

## How the app must connect

Every request runs in a transaction that starts with the identity from the JWT:

```python
# backend/database.py (SQLAlchemy)
from sqlalchemy import text

def set_request_identity(session, user_id: int, patient_id: int | None):
    session.execute(text("SELECT set_config('app.user_id', :u, true)"), {"u": str(user_id)})
    if patient_id is not None:
        session.execute(text("SELECT set_config('app.patient_id', :p, true)"), {"p": str(patient_id)})
```

`set_config(..., true)` is the same as `SET LOCAL`: it ends with the transaction, so one
request's identity never carries over to the next pooled connection.

Write audit entries through the function, never the table:

```python
session.execute(text("SELECT log_audit(:e, :u, :p, CAST(:payload AS jsonb))"),
                {"e": "LOGIN_SUCCESS", "u": user_id, "p": patient_id, "payload": '{"ip": "10.0.0.2"}'})
```

## docker-compose.yml

```yaml
postgres:
  image: postgres:16
  environment:
    POSTGRES_DB: med_db
    POSTGRES_USER: ${DB_ADMIN_USER}
    POSTGRES_PASSWORD: ${DB_ADMIN_PASSWORD}
    APP_DB_PASSWORD: ${APP_DB_PASSWORD}        # read by 00_roles.sh
  volumes:
    - ./backend/sql:/docker-entrypoint-initdb.d:ro
    - pgdata:/var/lib/postgresql/data
```

The app's `DATABASE_URL` uses `med_app` and `APP_DB_PASSWORD`, never the admin user.
Scripts only run on an empty volume; to rebuild from scratch: `docker compose down -v && docker compose up`
(this deletes all data).

## Where the old columns went

| Old table | New location |
| --- | --- |
| `doctors`, `doctor_patients`, `consultations`, `queue_tickets` | Removed (no doctor role) |
| `invites` | `password_reset_tokens` |
| `idempotency_cache` | Removed (Redis with per-user keys) |
| `care_plan_tasks` | `patient_goals` |
| sbp, dbp, bmi, resting_hr, spo2, waist (CVD/T2D/CRD tables) | `vitals_records` (entered once) |
| cholesterol, HDL, LDL, TG, FPG, HbA1c, creatinine, eGFR, hs-CRP, uric acid, UACR, eosinophils, IgE, TSH, B12, folate | `lab_results` (optional, patient-confirmed) |
| smoking_history, tobacco_exposure, physical_activity, daily_sodium, daily_carbs, sedentary_duration, daily_sleep, occupational_dust, substance_abuse | `lifestyle_records` |
| gestational_diabetes, acanthosis_nigricans, allergic_rhinitis, childhood_asthma, tbi_history | `patient_health_history` |
| phq9, gad7, psqi, mmrc_score, cat_score, act_score | `questionnaire_responses` (with item answers) |
| family_history_* flags | Derived from `patient_family_history` / `patient_fhrs_stratification` |
| age, biological_sex copies | Computed from `patients` |
| **Dropped** - clinic or hospital only: LVEF, troponin, NT-proBNP, PaO2, PaCO2, FeNO, spirometry, fasting insulin, C-peptide, HOMA-IR, cortisol, HRV | |
| **Dropped** - clinician-rated: MMSE, MoCA, HAM-D, UPDRS III, tremor rating, FAQ | |
| **Dropped** - leakage (describe existing disease): annual_exacerbations, seizure_frequency | |

`feature_builder.py` assembles each model's inputs from these tables and saves them in
`risk_assessments.input_snapshot`.

## Safety note

`questionnaire_responses.requires_crisis_prompt` is set automatically when PHQ-9 item 9
(thoughts of self-harm) is answered above 0. The app must show crisis resources right away,
e.g. the NCMH Crisis Hotline (1553).
