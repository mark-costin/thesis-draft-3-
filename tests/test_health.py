"""main.py and config.py: the API starts, reads .env and reaches the database."""

from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from backend.config import settings
from backend.main import app

client = TestClient(app)


def test_config_uses_med_app():
    assert make_url(settings.database_url).username == "med_app"


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_db():
    response = client.get("/health/db")
    assert response.status_code == 200
    assert response.json() == {"database": "ok"}


def test_identity_cannot_come_from_query_string():
    # The old get_db() turned user_id/patient_id into query parameters; now they are ignored
    response = client.get("/health/db", params={"user_id": 1, "patient_id": 1})
    assert response.status_code == 200
