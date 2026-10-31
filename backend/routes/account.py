"""Account: export all my data, permanently delete my account (Sprint 3, RA 10173)."""

import json
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from backend import services, storage
from backend.database import get_db
from backend.dependencies import CurrentUser, get_current_user
from backend.middleware import limiter
from backend.schemas import AccountDeleteRequest

router = APIRouter(prefix="/account", tags=["account"])


@router.get("/export")
def export_my_data(current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> Response:
    """Right to access: one JSON file with everything the app holds about the patient."""
    data = services.export_account(db, current.user_id, current.patient_id)
    filename = f"my-health-data-{date.today().isoformat()}.json"
    return Response(
        content=json.dumps(data, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
    )


@router.post("/delete", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("5/minute")  # it checks the password
def delete_my_account(request: Request, body: AccountDeleteRequest,
                      current: CurrentUser = Depends(get_current_user),
                      db: Session = Depends(get_db)) -> Response:
    """Right to erasure: permanent, cannot be undone. Needs the password and the phrase
    DELETE MY ACCOUNT. The audit log keeps an ACCOUNT_DELETED entry with IDs only."""
    try:
        storage_keys = services.delete_account(db, current.user_id, current.patient_id, body.password)
    except services.WrongPassword:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Incorrect password. Nothing was deleted.")
    storage.remove_many(storage_keys)  # the encrypted lab files go too
    return Response(status_code=status.HTTP_204_NO_CONTENT)
