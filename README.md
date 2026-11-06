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
6. Receive a care plan from templates matched to the risk assessment and reviewed by a physician
   (the 12 templates are drafts until the physician approves them).
7. Save the report to the database and export it.

> This project is developed as an undergraduate thesis.

## Tech Stack

| Layer           | Technology |
|-----------------|------------|
| Backend         | Python 3.11, FastAPI |
| Database        | PostgreSQL with Row-Level Security |
| ORM             | SQLAlchemy |
| Frontend        | Reflex (Python), planned for Sprint 4 |
| Packages        | uv |
| Background jobs | RQ + Redis (optional: without Redis, OCR runs in the API process) |
| OCR             | PaddleOCR-VL on a GPU (Chandra optional); PDF text layer (pypdfium2) |
| AI Model        | XGBoost with calibration (scikit-learn) and SHAP explanations; logistic regression baseline. Trained in `ml/ai_model/train_models.ipynb`; not registered yet |
| PDF reports     | WeasyPrint |

## Getting Started

1. Install the dependencies:
   ```
   uv sync
   ```
   On the GPU laptop that runs OCR, use `uv sync --inexact` (plain `uv sync` removes PaddleOCR,
   which is installed separately: see `docs/ocr.md`).
2. Copy `.env.example` to `.env` and fill in the secrets it lists.
3. Create the database `med_db`: with Docker, `docker compose -f docker-compose.dev.yml up -d`
   (PostgreSQL and Redis; the scripts in `database/` run on the first start). On Windows without
   Docker, follow `database/README.md`.
4. Create the test database `med_db_test` (asks for the `postgres` password; again after any
   change in `database/`):
   ```
   bash tests/create_test_db.sh                                     # Fedora / Linux
   powershell -ExecutionPolicy Bypass -File tests\create_test_db.ps1  # Windows
   ```
5. Run the API and open <http://localhost:8000/docs>:
   ```
   uv run uvicorn backend.main:app --reload
   ```
6. Run the tests:
   ```
   uv run pytest
   ```

OCR is off by default (`OCR_ENGINE=none`): patients type their lab values in. To read uploaded
lab results, see `docs/ocr.md`.

## Synthetic Data and Risk Models

**Synthetic patients.** `ml/synthetic/01_syntgen.ipynb` generates 20,000 patients with seed
20261101 into `ml/synthetic/output/` (committed, so everyone trains on the same data) and 20,000
with seed 20261102 into `ml/synthetic/output_seed2/`, the external check for the models. The
seed-2 CSVs are not committed: Notebook 1 rebuilds them exactly (see "Regenerating the second
seed" in `docs/synthetic_data_changes.md`); its Notebook 2 check,
`output_seed2/02_synthval_seed2.ipynb`, is committed. `output/doctor_review.csv` holds the 20
patients the physician rated and is never overwritten; the new sample is `doctor_review_v2.csv`.

**What changed in the generator and why:** `docs/synthetic_data_changes.md` records every change
after the Synthetic Data System Design (Nov 3, 2026): the label-leak fix, the physician-approved
label rules, BMI and flag calibration, the NEURO risk factors, the source of every new setting
(each marked "author-defined, not yet physician-reviewed"), and the open questions for the
physician.

**Model inputs** are listed in `ml/features.yaml` (read by the training notebook and by the app).
Added in November 2026: age and sex for T2D and CRD, smoking status (never / former / current) for
CVD, T2D and CRD, and the CRD family history score (`fhrs_crd`). Because the readiness check reads
`features.yaml`, the CRD model now waits for the patient's family history, like the other three.

**Why some AUROCs are low:** `ml/investigate/auroc_ceiling.py` compares each model with the best
score the synthetic data allows (the generator's own risk for each patient), lists which onset
risk factors are model inputs, and counts the readings available before a later index visit.

### Running the notebooks

Run them from the terminal with `nbconvert --execute`, in this order. Each notebook runs its own
tests (`%%ipytest` cells), and a failing test stops the run, so a finished run means the tests
passed.

```
cd ml/synthetic
uv run --group ml jupyter nbconvert --to notebook --execute --inplace 01_syntgen.ipynb      # about 8 min
uv run --group ml jupyter nbconvert --to notebook --execute --inplace 02_synthval.ipynb
SYNTH_DIR=output_seed2 uv run --group ml jupyter nbconvert --to notebook --execute 02_synthval.ipynb \
    --output-dir output_seed2 --output 02_synthval_seed2.ipynb
cd ../ai_model
uv run --group ml jupyter nbconvert --to notebook --execute --inplace train_models.ipynb   # writes ml/artifacts/
```

For a quick test run of Notebook 1, put `SYNTH_N=5000` in front of its command (5,000 patients
per seed). Training does not register any model: `ml/artifacts/register_models.sql` is run by hand.

`uv run pytest` runs the app tests in `tests/`. The ML tests run separately:

```
uv run --group ml python -m pytest ml/ai_model/test_model_inputs.py ml/investigate/test_auroc_ceiling.py
```

## Project Folder

The tree below matches `main`.

```
thesis-d3/
├── backend/                       # FastAPI app (Python)
│   ├── ocr/                       # OCR for uploaded lab documents (docs/ocr.md)
│   │   ├── __init__.py
│   │   ├── engines.py             # Engines: pdf_text, paddleocr_vl (chosen by OCR_ENGINE in .env)
│   │   ├── job.py                 # The OCR job (RQ worker, or in-process when Redis is off)
│   │   └── parser.py              # OCR text -> lab values
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
│   ├── questionnaires.py          # Questionnaire scoring
│   ├── readiness.py               # What each model still needs from the patient
│   ├── reports.py                 # Frozen report snapshots
│   ├── risk.py                    # Runs the four models and saves results and SHAP values
│   ├── schemas.py                 # Request/response schemas (Pydantic)
│   ├── security.py                # Password hashing (Argon2id) and JWT tokens
│   ├── services.py                # Business logic used by the routes
│   └── storage.py                 # Encrypted file storage (Fernet)
├── database/                      # PostgreSQL schema, run in number order
│   ├── 00_roles.sh                # Creates the med_app role (Docker only)
│   ├── 01_functions.sql           # Shared SQL functions (RLS identity, triggers)
│   ├── 02_users.sql … 20_health_reports.sql   # One script per table (see below)
│   ├── 21_famhistory.sql          # Changes to the family history tables (FHRS guide)
│   ├── 22_ocr_engine_default.sql  # documents.ocr_engine default 'none'
│   ├── 99_security.sql            # Row-level security and permissions (runs last)
│   ├── seed_care_plan_templates.sql   # The 12 draft care plan templates (run by hand)
│   ├── setup_windows.ps1          # One-step database setup on Windows
│   ├── windows_setup.sql          # All scripts in one file, for pgAdmin on Windows
│   └── README.md                  # Database setup guide
├── docs/
│   ├── ocr.md                     # OCR engines and the PaddleOCR-VL GPU install
│   └── synthetic_data_changes.md  # Every change to the synthetic data generator, with sources
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
│   │   ├── test_model_inputs.py   # Tests: the model inputs in features.yaml and the training data
│   │   └── train_models.ipynb     # Trains the 4 risk models
│   ├── artifacts/                 # Trained model files and run reports (not committed)
│   │   └── __init__.py
│   ├── investigate/               # Why some AUROCs are low (read only)
│   │   ├── auroc_ceiling.py       # Best possible AUROC, risk factor coverage, earlier visits
│   │   └── test_auroc_ceiling.py
│   ├── OCR/                       # OCR check on 3 synthetic lab reports (README.md)
│   │   ├── __init__.py
│   │   ├── make_reports.py        # Makes the 3 report images + true values -> output/
│   │   ├── run_check.py           # Real OCR job on each: true vs extracted, time, GPU memory
│   │   └── README.md
│   ├── synthetic/                 # Synthetic patient data (docs/synthetic_data_changes.md)
│   │   ├── 01_syntgen.ipynb       # Notebook 1: generates the synthetic patients (2 seeds)
│   │   ├── 02_synthval.ipynb      # Notebook 2: checks the synthetic data
│   │   ├── data/                  # Source tables (GBD 2023, PSA): rates, deaths, population
│   │   ├── output/                # 20,000 patients, seed 20261101 (patients, visits, relatives,
│   │   │                          #   training, doctor_review, doctor_review_v2), committed
│   │   └── output_seed2/          # Seed 20261102: CSVs rebuilt by Notebook 1 (not committed)
│   │       └── 02_synthval_seed2.ipynb   # Notebook 2 run on seed 2
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

**Where OCR lives:** `backend/ocr/` (`engines.py`, `parser.py`, `job.py`) and `backend/labs.py`
(test names and units). Install and settings: `docs/ocr.md`. Checking it: `ml/OCR/`.

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
| `22_ocr_engine_default.sql` | change to `documents` (`ocr_engine` default `'none'`) |
| `99_security.sql` | RLS policies and grants (runs last) |

Generated folders (`.venv/`, `.pytest_cache/`, `__pycache__/`, `ml/mlruns/`, `ml/OCR/output/`), the seed-2 CSVs (`ml/synthetic/output_seed2/*.csv`), uploaded files and `.env` are left out because Git ignores them.
