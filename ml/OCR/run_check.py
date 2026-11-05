"""OCR check: runs the real OCR job (PaddleOCR-VL, backend/ocr/job.py) on the 3 synthetic reports
from make_reports.py and compares what was stored with the true values. Prints time and peak GPU
memory per image and writes results.json next to the images.

Needs the GPU install (docs/ocr.md) and med_db_test. Uses the TEST database and a throwaway user
(deleted at the end). Run from the project folder:

    uv run python ml/OCR/make_reports.py
    uv run python ml/OCR/run_check.py            # images from ml/OCR/output/
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from decimal import Decimal
from pathlib import Path

from dotenv import dotenv_values

PROJECT = Path.cwd()
sys.path.insert(0, str(PROJECT))
env = dotenv_values(PROJECT / ".env")
os.environ.update(DATABASE_URL=env["TEST_DATABASE_URL"], OCR_ENGINE="paddleocr_vl", REDIS_ENABLED="false",
                  UPLOAD_DIR=tempfile.mkdtemp(prefix="ocrcheck-"))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import delete, func, select  # noqa: E402

from backend.ocr import engines, job  # noqa: E402
from backend.config import settings  # noqa: E402
from backend.database import SessionLocal, set_request_identity  # noqa: E402
from backend.main import app  # noqa: E402
from backend.middleware import limiter  # noqa: E402
from backend.models import Document, LabResult, Patient, User  # noqa: E402

assert "med_db_test" in settings.database_url
limiter.enabled = False
images = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "output"
truth = {k: Decimal(v) for k, v in json.loads((images / "truth.json").read_text()).items()}


def gpu_mib():
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True).stdout
    return int(out.strip().splitlines()[0])


class Peak:
    def __enter__(self):
        self.peak, self._stop = gpu_mib(), threading.Event()
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()
        return self

    def _watch(self):
        while not self._stop.is_set():
            self.peak = max(self.peak, gpu_mib())
            time.sleep(0.1)

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join()


client = TestClient(app)
email, password = f"ocrcheck-{uuid.uuid4().hex[:8]}@test.local", "correct horse battery"
assert client.post("/auth/register", json={
    "email": email, "password": password, "first_name": "Test", "last_name": "Sample",
    "date_of_birth": "1981-01-01", "biological_sex": "Female",
    "privacy_consent": True, "consent_version": settings.privacy_notice_version}).status_code == 201
token = client.post("/auth/login", json={"email": email, "password": password}).json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}
job.start = lambda *args: False  # upload only; the job is run (and timed) below

print(f"GPU memory before model load: {gpu_mib()} MiB")
with Peak() as load_peak:
    started = time.perf_counter()
    engines._get_pipeline()
    load_seconds = time.perf_counter() - started
print(f"Model load (incl. first download): {load_seconds:.1f} s, GPU peak {load_peak.peak} MiB, "
      f"after load {gpu_mib()} MiB\n")

report = {"model_load_seconds": round(load_seconds, 1), "model_load_peak_mib": load_peak.peak, "images": {}}
try:
    for name, mime in [("clean.png", "image/png"), ("photo.jpg", "image/jpeg"), ("handwritten.jpg", "image/jpeg")]:
        response = client.post("/documents", headers=headers, files={"file": (name, (images / name).read_bytes(), mime)})
        assert response.status_code == 202, response.text
        document_id = response.json()["document_id"]
        with SessionLocal() as session:
            user_id = session.scalar(select(User.user_id).where(User.email == email))
            set_request_identity(session, user_id=user_id)
            patient_id = session.scalar(select(Patient.patient_id))
        with Peak() as peak:
            started = time.perf_counter()
            status = job.process_document(document_id, patient_id)
            seconds = time.perf_counter() - started
        with SessionLocal() as session:
            set_request_identity(session, user_id=user_id, patient_id=patient_id)
            document = session.get(Document, document_id)
            found = {row.test_code: row.value for row in session.scalars(
                select(LabResult).where(LabResult.document_id == document_id))}
            text, error, engine_name = document.ocr_text, document.error_message, document.ocr_engine
        rows = []
        for code, true_value in truth.items():
            got = found.get(code)
            verdict = "missed" if got is None else ("correct" if got == true_value else "wrong")
            rows.append({"test": code, "true": str(true_value), "extracted": None if got is None else str(got),
                         "result": verdict})
        extra = sorted(set(found) - set(truth))
        correct = sum(r["result"] == "correct" for r in rows)
        report["images"][name] = {"status": status, "engine": engine_name, "seconds": round(seconds, 1),
                                  "gpu_peak_mib": peak.peak, "correct": f"{correct}/{len(truth)}", "rows": rows,
                                  "unexpected": extra, "error": error, "ocr_text": text}
        print(f"=== {name}: {status} ({engine_name}), {seconds:.1f} s, GPU peak {peak.peak} MiB, "
              f"{correct}/{len(truth)} correct")
        for r in rows:
            print(f"  {r['result']:<8} {r['test']:<18} true {r['true']:>9}  got {r['extracted']}")
        if extra or error:
            print("  unexpected:", extra, "error:", error)
        print("  --- OCR text ---\n  " + (text or "").replace("\n", "\n  ") + "\n")
finally:
    with SessionLocal() as session:
        session.execute(delete(User).where(func.lower(User.email) == email))
        session.commit()
(images / "results.json").write_text(json.dumps(report, indent=2))
