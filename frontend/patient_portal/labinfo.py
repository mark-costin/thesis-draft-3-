"""Lab tests as the screens show them. No Reflex import, so the tests can check it against the API."""

# Lab tests: the one unit the API stores (backend/labs.py STANDARD_UNIT; other units are converted there)
LAB_UNIT = {"TOTAL_CHOLESTEROL": "mg/dL", "HDL": "mg/dL", "LDL": "mg/dL", "TRIGLYCERIDES": "mg/dL", "FPG": "mg/dL", "OGTT_2H": "mg/dL",
            "HBA1C": "%", "CREATININE": "mg/dL", "EGFR": "mL/min/1.73m2", "URIC_ACID": "mg/dL", "UACR": "mg/g", "HS_CRP": "mg/L",
            "EOSINOPHILS": "cells/uL", "IGE": "IU/mL", "TSH": "mIU/L", "VITAMIN_B12": "pg/mL", "FOLATE": "ng/mL"}
LAB_LABEL = {"TOTAL_CHOLESTEROL": "Total cholesterol", "HDL": "HDL cholesterol", "LDL": "LDL cholesterol", "TRIGLYCERIDES": "Triglycerides",
             "FPG": "Fasting blood sugar (FBS)", "OGTT_2H": "OGTT 2-hour glucose", "HBA1C": "HbA1c", "CREATININE": "Creatinine",
             "EGFR": "eGFR", "URIC_ACID": "Uric acid", "UACR": "Urine albumin-creatinine ratio", "HS_CRP": "hs-CRP",
             "EOSINOPHILS": "Eosinophils", "IGE": "Total IgE", "TSH": "TSH", "VITAMIN_B12": "Vitamin B12", "FOLATE": "Folate"}
DOC_STATUS = {"pending": "Waiting for OCR", "processing": "Reading the document", "ready_for_review": "Ready for review",
              "verified": "Confirmed", "rejected": "Rejected", "failed": "Could not be read"}
