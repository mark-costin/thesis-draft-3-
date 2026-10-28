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

```
thesis-d3/
├── assets/                  # Static files (images, icons)
├── backend/
│   ├── routes/              # API endpoints, grouped by feature
│   ├── uploads/             # Uploaded lab result files (not committed)
│   ├── __init__.py
│   ├── audit.py             # Audit logging
│   ├── check_db.py          # Checks the database connection
│   ├── config.py            # Settings read from .env
│   ├── database.py          # Database engine and sessions
│   ├── dependencies.py      # Shared FastAPI dependencies (e.g., current user)
│   ├── mailer.py            # Sends emails (e.g., password reset)
│   ├── main.py              # FastAPI entry point
│   ├── models.py            # SQLAlchemy models
│   ├── ocr_worker.py        # Background worker that runs OCR on uploads
│   ├── schemas.py           # Request/response schemas (Pydantic)
│   ├── security.py          # Password hashing and JWT tokens
│   └── services.py          # Business logic (risk model, care plans)
├── database/                # PostgreSQL schema, run in number order
│   ├── 00_roles.sh          # Creates the database roles
│   ├── 01_functions.sql     # Shared SQL functions
│   ├── 02_users.sql … 21_famhistory.sql   # One script per table
│   ├── 99_security.sql      # Row-level security and permissions
│   ├── seed_care_plan_templates.sql       # Loads the care plan templates
│   ├── setup_windows.ps1    # One-step database setup on Windows
│   └── README.md            # Database setup guide
├── frontend/
│   ├── patient_app/
│   │   ├── __init__.py
│   │   ├── patient_app.py   # App entry point and page routes
│   │   ├── state.py         # App state and backend API calls
│   │   ├── components.py    # Shared UI components
│   │   ├── login.py         # Login and registration
│   │   ├── dashboard.py     # Health dashboard
│   │   ├── vitals.py        # Vitals entry
│   │   ├── family_history.py  # Family history entry
│   │   ├── documents.py     # Lab result upload and OCR review
│   │   ├── risk.py          # Risk results
│   │   └── report.py        # Care plan and saved reports
│   └── rxconfig.py          # Reflex configuration
├── ml/
│   ├── ai_model/            # Risk model training and prediction code
│   ├── artifacts/           # Trained model files and checksums
│   ├── OCR/                 # Lab result OCR and field mapping
│   ├── synthetic/           # Synthetic patient data generator
│   └── __init__.py
├── tests/                   # pytest tests
├── .env.example             # Template for .env (copy and fill in)
├── .gitignore
├── .python-version
├── docker-compose.dev.yml   # Local development containers
├── docs-requirement.md      # Documentation requirements
├── pyproject.toml           # Dependencies (uv)
├── uv.lock
└── README.md
```

### Database scripts

| Script | Table |
|--------|-------|
| `02_users.sql` | `users` |
| `03_patients.sql` | `patients` |
| `04_password_reset_tokens.sql` | `password_reset_tokens` |
| `05_audit_ledger.sql` | `audit_ledger` |
| `06_documents.sql` | `documents` |
| `07_vitals_records.sql` | `vitals_records` |
| `08_lab_results.sql` | `lab_results` |
| `09_lifestyle_records.sql` | `lifestyle_records` |
| `10_health_history.sql` | `health_history` |
| `11_questionnaire_responses.sql` | `questionnaire_responses` |
| `12_family_history.sql` | `family_history` |
| `13_fhrs_stratification.sql` | `fhrs_stratification` |
| `14_model_versions.sql` | `model_versions` |
| `15_risk_assessments.sql` | `risk_assessments` |
| `16_risk_results.sql` | `risk_results` |
| `17_shap_explanations.sql` | `shap_explanations` |
| `18_patient_goals.sql` | `patient_goals` |
| `19_care_plan_templates.sql` | `care_plan_templates` |
| `20_health_reports.sql` | `health_reports` |
| `21_famhistory.sql` | _TODO_ |

Generated folders (`.venv/`, `.pytest_cache/`, `__pycache__/`) and `.env` are left out because Git ignores them.
