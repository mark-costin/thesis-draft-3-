# Thesis Draft 3: Chronic Disease Risk Decision Support System

## Introduction

This is a web-based clinical decision support system that estimates a patient's risk of chronic disease from their **family health history** and **lab results**. It screens four disease groups at the same time:

- Cardiovascular Disease (CVD)
- Type 2 Diabetes (T2D)
- Chronic Respiratory Disease (CRD)
- Neurological Disorders (NEURO)

**How it works for the patient:**

1. Log in to the system.
2. Upload a lab result. OCR reads its values.
3. Type in any details OCR missed.
4. The AI risk model analyzes the data.
5. View the risk results.
6. Receive a care plan based on doctor-validated templates matched to the risk assessment.
7. Save the report to the database and export it.

> This project is developed as an undergraduate thesis.

## Tech Stack

| Layer      | Technology            |
|------------|-----------------------|
| Backend    | Python, FastAPI       |
| Database   | PostgreSQL            |
| ORM        | SQLAlchemy            |
| Frontend   | Reflex (Python)       |
| Packages   | uv                    |
| OCR        | _TODO_                |
| AI Model   | _TODO_                |

## Project Folder

The tree below matches `main`. Items marked _(coming in …)_ are in an open pull request.

```
thesis-d3/
├── backend/                       # FastAPI app (Python)
│   ├── routes/                    # API endpoints, one file per feature
│   │   ├── __init__.py
│   │   ├── account.py             # Export all my data, delete my account (RA 10173)
│   │   ├── assessment.py          # Missing-inputs check and the risk run (/assessment)
│   │   ├── auth.py                # Register, login, logout, password reset
│   │   ├── documents.py           # Lab document upload, list, download, OCR review
│   │   ├── family_history.py      # Pedigree and the Family History Risk Score
│   │   ├── goals.py               # The patient's own preventive goals
│   │   ├── labs.py                # Lab values: manual entry, fix after OCR, delete
│   │   ├── lifestyle.py           # Lifestyle entries and health history
│   │   ├── profile.py             # The patient's own profile
│   │   ├── questionnaires.py      # PHQ-9, GAD-7, PSQI, mMRC, CAT, ACT
│   │   ├── reports.py             # Saved health reports and PDF export
│   │   ├── risk.py                # Empty placeholder (the risk endpoints are in assessment.py)
│   │   └── vitals.py              # Blood pressure, heart rate, weight, height, SpO2, ...
│   ├── uploads/                   # Encrypted uploaded files (only .gitkeep is committed)
│   ├── __init__.py
│   ├── audit.py                   # Security audit log (hash-chained in the database)
│   ├── care_plan.py               # Care plans from physician-reviewed templates + add-ons
│   ├── check_db.py                # Checks the database connection
│   ├── config.py                  # Settings read from .env
│   ├── database.py                # Database engine, sessions, RLS identity per request
│   ├── dependencies.py            # Shared FastAPI dependencies (current user)
│   ├── explanations.py            # Plain-language sentences for SHAP factors
│   ├── feature_builder.py         # Builds each model's inputs from the patient's saved data
│   ├── fhrs.py                    # Family History Risk Score (Data Dictionary FHRS Guide)
│   ├── image_privacy.py           # Removes GPS/camera metadata from uploaded photos
│   ├── labs.py                    # Lab test names and unit conversion (OCR and manual entry)
│   ├── mailer.py                  # Account emails (password reset)
│   ├── main.py                    # FastAPI entry point
│   ├── middleware.py              # CORS, rate limits, idempotency keys
│   ├── model_registry.py          # Loads the registered risk models, refuses changed files
│   ├── models.py                  # SQLAlchemy models for the 19 tables
│   ├── ocr.py                     # OCR engines (chosen by OCR_ENGINE in .env)
│   ├── ocr_parser.py              # OCR text -> lab values
│   ├── questionnaires.py          # Questionnaire scoring
│   ├── readiness.py               # What each model still needs from the patient
│   ├── reports.py                 # Frozen report snapshots
│   ├── risk.py                    # Runs the four models and saves results and SHAP values
│   ├── schemas.py                 # Request/response schemas (Pydantic)
│   ├── security.py                # Password hashing (Argon2id) and JWT tokens
│   ├── services.py                # Business logic used by the routes
│   ├── storage.py                 # Encrypted file storage (Fernet)
│   └── worker.py                  # The OCR job (RQ worker, or in-process when Redis is off)
├── database/                      # PostgreSQL schema, run in number order
│   ├── 00_roles.sh                # Creates the med_app role (Docker only)
│   ├── 01_functions.sql           # Shared SQL functions (RLS identity, triggers)
│   ├── 02_users.sql … 20_health_reports.sql   # One script per table (see below)
│   ├── 21_famhistory.sql          # Changes to the family history tables (FHRS guide)
│   ├── 22_ocr_engine_default.sql  # documents.ocr_engine default 'none' (coming in feat/ocr-engines)
│   ├── 99_security.sql            # Row-level security and permissions (runs last)
│   ├── seed_care_plan_templates.sql   # The 12 draft care plan templates (run by hand)
│   ├── setup_windows.ps1          # One-step database setup on Windows
│   ├── windows_setup.sql          # All scripts in one file, for pgAdmin on Windows
│   └── README.md                  # Database setup guide
├── docs/                          # (coming in feat/ocr-engines)
│   └── ocr.md                     # OCR engines and the PaddleOCR-VL GPU install
├── frontend/                      # Planned, Sprint 4 (empty)
│   ├── patient_app/               # Planned, Sprint 4 (empty files)
│   │   ├── __init__.py
│   │   ├── patient_app.py         # App entry point and page routes
│   │   ├── state.py               # App state and backend API calls
│   │   ├── components.py          # Shared UI components
│   │   ├── login.py               # Login and registration
│   │   ├── dashboard.py           # Health dashboard
│   │   ├── vitals.py              # Vitals entry
│   │   ├── family_history.py      # Family history entry
│   │   ├── documents.py           # Lab result upload and OCR review
│   │   ├── risk.py                # Risk results
│   │   └── report.py              # Care plan and saved reports
│   └── rxconfig.py                # Reflex configuration
├── ml/
│   ├── ai_model/
│   │   ├── __init__.py
│   │   └── train_models.ipynb     # Trains the 4 risk models (coming in #35)
│   ├── artifacts/                 # Trained model files (*.joblib: not committed)
│   │   └── __init__.py
│   ├── synthetic/                 # Synthetic patient data
│   │   ├── 01_syntgen.ipynb       # Notebook 1: generates the synthetic patients
│   │   ├── 02_synthval.ipynb      # Notebook 2: checks the synthetic data
│   │   ├── data/                  # Source tables (GBD 2023, PSA): rates, deaths, population
│   │   ├── output/                # Generated CSVs (patients, visits, relatives, training,
│   │   │                          #   doctor_review), committed so everyone trains on the same data
│   │   └── __init__.py
│   ├── features.yaml              # Each model's inputs, ranges and encodings (read by the app too)
│   └── __init__.py
├── tests/                         # pytest tests (+ create_test_db.sh / .ps1 for med_db_test)
├── .env.example                   # Template for .env (copy and fill in)
├── .gitignore
├── .python-version
├── docker-compose.dev.yml         # Local development containers
├── docs-requirement.md            # Documentation requirements
├── pyproject.toml                 # Dependencies (uv)
├── uv.lock
└── README.md
```

**Where OCR lives:** `backend/ocr.py` (the engines), `backend/ocr_parser.py` (text to lab values),
`backend/worker.py` (the job that runs it) and `backend/labs.py` (test names and units).

### Database scripts

| Script | Table |
|--------|-------|
| `00_roles.sh` | role `med_app` |
| `01_functions.sql` | helper functions |
| `02_users.sql` | `users` |
| `03_patients.sql` | `patients` |
| `04_password_reset_tokens.sql` | `password_reset_tokens` |
| `05_audit_ledger.sql` | `audit_ledger` |
| `06_documents.sql` | `documents` |
| `07_vitals_records.sql` | `vitals_records` |
| `08_lab_results.sql` | `lab_results` |
| `09_lifestyle_records.sql` | `lifestyle_records` |
| `10_health_history.sql` | `patient_health_history` |
| `11_questionnaire_responses.sql` | `questionnaire_responses` |
| `12_family_history.sql` | `patient_family_history` |
| `13_fhrs_stratification.sql` | `patient_fhrs_stratification` |
| `14_model_versions.sql` | `model_versions` |
| `15_risk_assessments.sql` | `risk_assessments` |
| `16_risk_results.sql` | `risk_results` |
| `17_shap_explanations.sql` | `shap_explanations` |
| `18_patient_goals.sql` | `patient_goals` |
| `19_care_plan_templates.sql` | `care_plan_templates` |
| `20_health_reports.sql` | `health_reports` |
| `21_famhistory.sql` | changes to `patient_family_history`, `patient_fhrs_stratification` |
| `22_ocr_engine_default.sql` | change to `documents` (coming in feat/ocr-engines) |
| `99_security.sql` | RLS policies and grants (runs last) |

Generated folders (`.venv/`, `.pytest_cache/`, `__pycache__/`, `ml/mlruns/`), uploaded files and `.env` are left out because Git ignores them.
