"""ml/synthetic/seed_demo_patients.py: the demo patients load through the API and are ready to assess."""

import importlib.util
import sys
from pathlib import Path

import pytest
from sqlalchemy import delete, func

from backend.database import SessionLocal
from backend.models import User
from risk_helpers import MODELS, client, models_on, placeholder_ids  # noqa: F401  (fixtures are used by name)

SCRIPT = Path(__file__).resolve().parents[1] / "ml" / "synthetic" / "seed_demo_patients.py"
spec = importlib.util.spec_from_file_location("seed_demo_patients", SCRIPT)
seed_demo = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = seed_demo  # dataclasses look the module up by name while it loads
spec.loader.exec_module(seed_demo)

PASSWORD = "demo patients only 123"
EMAILS = [p.email for p in seed_demo.PATIENTS]


@pytest.fixture(autouse=True)
def clean():
    def remove():
        with SessionLocal() as session:
            session.execute(delete(User).where(func.lower(User.email).in_(EMAILS)))
            session.commit()
    remove()
    yield
    remove()


def test_every_demo_patient_is_ready_to_assess():
    results = seed_demo.seed(client, PASSWORD)
    assert [r["email"] for r in results] == EMAILS
    for r in results:
        assert r["status"] == "loaded" and r["ready"], r


def test_running_it_again_skips_patients_already_there():
    seed_demo.seed(client, PASSWORD, patients=seed_demo.PATIENTS[:1])
    again = seed_demo.seed(client, PASSWORD, patients=seed_demo.PATIENTS[:1])
    assert again[0]["status"] == "already registered, skipped"


def test_with_run_each_patient_gets_results_and_a_saved_report(models_on):
    results = seed_demo.seed(client, PASSWORD, run=True)
    for r in results:
        assert set(r["tiers"]) == set(MODELS), r
        assert set(r["tiers"].values()) <= {"Low", "Moderate", "High"}
    token = client.post("/auth/login", json={"email": EMAILS[0], "password": PASSWORD}).json()["access_token"]
    assert len(client.get("/reports", headers={"Authorization": f"Bearer {token}"}).json()) == 1


def test_the_script_says_so_when_the_api_is_not_running(capsys):
    assert seed_demo.main(["--password", PASSWORD, "--api", "http://127.0.0.1:9"]) == 1
    assert "Cannot reach the API" in capsys.readouterr().out
