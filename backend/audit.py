"""Security audit log.

Every entry goes through public.log_audit() (05_audit_ledger.sql). med_app cannot read,
change or delete audit_ledger; the function adds each entry to a SHA-256 hash chain, so an
edited or deleted row is caught by verify_audit_chain() (run as the database owner).

The entry is written in the same transaction as the action it records, so an action that
is rolled back leaves no entry, and a saved action always has one.

Payloads hold IDs and event details only: never emails, passwords, tokens or health values.
"""

import json

from sqlalchemy import text
from sqlalchemy.orm import Session

# Must match audit_event_type_check in 05_audit_ledger.sql
EVENT_TYPES = frozenset({
    "REGISTER", "LOGIN_SUCCESS", "LOGIN_FAILED", "LOGOUT", "ACCOUNT_LOCKED",
    "PASSWORD_CHANGED", "PASSWORD_RESET_REQUESTED", "TOKENS_REVOKED",
    "DATA_CREATED", "DATA_UPDATED", "DATA_DELETED",
    "DOCUMENT_UPLOADED", "DOCUMENT_VERIFIED", "DOCUMENT_REJECTED",
    "RISK_ASSESSMENT_RUN", "REPORT_EXPORTED", "DATA_EXPORTED", "ACCOUNT_DELETED",
})

_FORBIDDEN_KEYS = {"email", "password", "new_password", "token", "password_hash"}

_LOG_AUDIT = text(
    "SELECT public.log_audit(:event_type, CAST(:user_id AS integer), "
    "CAST(:patient_id AS integer), CAST(:payload AS jsonb))"
)


def log_event(db: Session, event_type: str, *, user_id: int | None = None,
              patient_id: int | None = None, **details) -> int:
    """Adds one entry to the audit chain and returns its audit_id. Does not commit."""
    if event_type not in EVENT_TYPES:
        raise ValueError(f"Unknown audit event type: {event_type}")
    leaked = _FORBIDDEN_KEYS & details.keys()
    if leaked:
        raise ValueError(f"Audit payload must not contain {sorted(leaked)}")
    return db.scalar(_LOG_AUDIT, {
        "event_type": event_type,
        "user_id": user_id,
        "patient_id": patient_id,
        "payload": json.dumps(details, sort_keys=True),
    })
