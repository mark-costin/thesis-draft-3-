import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from backend.config import settings

# Bind to the test database
test_engine = create_engine(settings.test_database_url)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

@pytest.fixture
def db_patient_1():
    db = TestingSessionLocal()
    db.execute(text("SELECT set_config('app.user_id', '1', true)"))
    db.execute(text("SELECT set_config('app.patient_id', '1', true)"))
    yield db
    db.close()

@pytest.fixture
def db_patient_2():
    db = TestingSessionLocal()
    db.execute(text("SELECT set_config('app.user_id', '2', true)"))
    db.execute(text("SELECT set_config('app.patient_id', '2', true)"))
    yield db
    db.close()

def test_rls_isolation(db_patient_1, db_patient_2):
    # Setup: Insert a dummy vitals record for patient 1
    db_patient_1.execute(
        text("INSERT INTO vitals_records (patient_id, heart_rate) VALUES (1, 75)")
    )
    db_patient_1.commit()

    # Patient 1 should see their own record
    count_1 = db_patient_1.execute(
        text("SELECT COUNT(*) FROM vitals_records WHERE patient_id = 1")
    ).scalar()
    assert count_1 == 1

    # Patient 2 should see 0 records when querying Patient 1's data
    count_2 = db_patient_2.execute(
        text("SELECT COUNT(*) FROM vitals_records WHERE patient_id = 1")
    ).scalar()
    assert count_2 == 0

    # Teardown
    db_patient_1.execute(text("DELETE FROM vitals_records WHERE patient_id = 1"))
    db_patient_1.commit()