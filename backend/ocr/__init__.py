"""OCR for uploaded lab documents (docs/ocr.md).

- engines.py: the engines ("pdf_text", "paddleocr_vl"), chosen by OCR_ENGINE in .env
- parser.py:  the text an engine read -> lab values (test names and units: backend/labs.py)
- job.py:     the OCR job (RQ worker, or in the API process when Redis is off)

Import the module you need (from backend.ocr import engines); nothing is re-exported here, so
importing backend.ocr never loads the job's database code or an engine.
"""
