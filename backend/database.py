from collections.abc import Iterator

from sqlalchemy import Connection, create_engine, event, text
from sqlalchemy.orm import Session, SessionTransaction, sessionmaker

from backend.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False)

_IDENTITY_KEY = "rls_identity"


def _apply_identity(connection: Connection, identity: dict[str, int | None]) -> None:
    # set_config(..., true) lasts only for the current transaction.
    # An empty string makes current_patient_id() / current_app_user_id() return NULL.
    for name, value in identity.items():
        connection.execute(
            text("SELECT set_config(:name, :value, true)"),
            {"name": name, "value": "" if value is None else str(value)},
        )


@event.listens_for(SessionLocal, "after_begin")
def _reapply_identity(session: Session, transaction: SessionTransaction, connection: Connection) -> None:
    # Every new transaction (e.g. after db.commit()) gets the identity again,
    # so RLS never runs with an empty identity mid-request.
    identity = session.info.get(_IDENTITY_KEY)
    if identity:
        _apply_identity(connection, identity)


def set_request_identity(db: Session, user_id: int | None = None, patient_id: int | None = None) -> None:
    """Bind the RLS identity to this session.

    Only pass IDs taken from a verified JWT (or from the row you just
    created during registration). Never pass values from the request body,
    query string or headers.
    """
    identity = {"app.user_id": user_id, "app.patient_id": patient_id}
    db.info[_IDENTITY_KEY] = identity
    if db.in_transaction():
        _apply_identity(db.connection(), identity)
    # Otherwise the after_begin hook applies it when the next query starts.


def get_db() -> Iterator[Session]:
    """FastAPI dependency. Takes no parameters on purpose: identity is set by the
    auth dependency (Week 2) through set_request_identity()."""
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()