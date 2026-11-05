# OCR check (synthetic lab reports)

Repeats the OCR check from the feat/ocr-engines pull request on any machine with the GPU
install (`docs/ocr.md`). Only invented values; no real reports.

```bash
uv run python ml/OCR/make_reports.py   # 3 images + truth.json -> ml/OCR/output/ (not committed)
uv run python ml/OCR/run_check.py      # real OCR job on each image vs the true values
```

`run_check.py` uses `med_db_test` and a throwaway user that it deletes at the end. Per image it
prints each value as true / extracted / correct, wrong or missed, the time, and the peak GPU
memory, and writes `ml/OCR/output/results.json`.

The OCR code itself is in `backend/ocr/` (engines, parser, job) and `backend/labs.py`.
