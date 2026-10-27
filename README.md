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
├── backend/
│   ├── app/
│   │   ├── main.py          # FastAPI entry point
│   │   ├── models/          # SQLAlchemy models
│   │   ├── schemas/         # Request/response schemas
│   │   ├── routers/         # API endpoints
│   │   └── services/        # OCR, risk model, care plans
│   └── requirements.txt
├── frontend/
├── docs/
└── README.md
```
