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
| Frontend   | _TODO_                |
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
└── README.md
```

Generated folders (`.venv/`, `.pytest_cache/`, `__pycache__/`) are left out because Git ignores them.
