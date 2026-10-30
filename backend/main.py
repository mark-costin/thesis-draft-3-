from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend import middleware
from backend.database import get_db
from backend.routes import auth, lifestyle, profile, vitals

app = FastAPI(title="CDSS API", version="0.1.0")
middleware.install(app)
app.include_router(auth.router)
app.include_router(profile.router)
app.include_router(vitals.router)
app.include_router(lifestyle.router)


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/health/db")
def health_db(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"database": "ok"}