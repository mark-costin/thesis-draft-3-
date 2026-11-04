from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend import middleware, readiness
from backend.database import get_db
from backend.routes import account, assessment, auth, documents, family_history, goals, lifestyle, profile, questionnaires, vitals

app = FastAPI(title="CDSS API", version="0.1.0")
readiness.load_features()  # fail at startup if ml/features.yaml names a column that does not exist
middleware.install(app)
app.include_router(auth.router)
app.include_router(profile.router)
app.include_router(vitals.router)
app.include_router(lifestyle.router)
app.include_router(questionnaires.router)
app.include_router(goals.router)
app.include_router(family_history.router)
app.include_router(account.router)
app.include_router(documents.router)
app.include_router(assessment.router)


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/health/db")
def health_db(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"database": "ok"}