"""Database engine, Alembic startup, and the in-memory fallback.

DATABASE_URL is optional. When it is unset the API keeps process-local storage
and logs a warning so a deploy can boot before Postgres is attached. Railway's
postgres:// URLs are rewritten to postgresql+psycopg://.

Startup runs `alembic upgrade head` behind a Postgres advisory lock. That is
idempotent and safe for the single API replica this service runs today; a
second replica waits for the lock instead of racing the migration. No Railway
pre-deploy command is required.

The database URL is never written to the logs.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Iterator, Optional

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from models import ApiRequest, utcnow

logger = logging.getLogger("plantr.db")

ROOT = Path(__file__).resolve().parent
API_REQUEST_RETENTION_DAYS = 90
# Arbitrary constant. Session-level so it survives the commit alembic opens.
MIGRATION_LOCK_KEY = 21415001

engine: Optional[Engine] = None
SessionLocal: Optional[sessionmaker] = None

# Process-local fallback used only when DATABASE_URL is unset.
memory_creators: dict = {}
memory_shops: dict = {}
memory_products: dict = {}
memory_events: list = []
memory_shortlists: list = []
memory_shortlist_items: list = []
memory_briefs: list = []


def configured() -> bool:
    return SessionLocal is not None


def normalise_database_url(url: str) -> str:
    """Rewrite Railway's postgres:// scheme to the psycopg v3 SQLAlchemy URL."""
    cleaned = url.strip()
    if cleaned.startswith("postgres://"):
        return "postgresql+psycopg://" + cleaned[len("postgres://") :]
    if cleaned.startswith("postgresql://"):
        return "postgresql+psycopg://" + cleaned[len("postgresql://") :]
    return cleaned


def reset_memory() -> None:
    memory_creators.clear()
    memory_shops.clear()
    memory_products.clear()
    memory_events.clear()
    memory_shortlists.clear()
    memory_shortlist_items.clear()
    memory_briefs.clear()


def close() -> None:
    global engine, SessionLocal
    if engine is not None:
        engine.dispose()
    engine = None
    SessionLocal = None


def make_engine(url: str) -> Engine:
    connect_args = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    new_engine = create_engine(url, pool_pre_ping=True, connect_args=connect_args)
    if new_engine.dialect.name == "sqlite":

        @event.listens_for(new_engine, "connect")
        def _sqlite_foreign_keys(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return new_engine


def init_storage() -> None:
    """Connect and migrate, or fall back to memory when DATABASE_URL is unset."""
    global engine, SessionLocal
    close()
    raw = os.getenv("DATABASE_URL", "").strip()
    if not raw:
        logger.warning(
            "DATABASE_URL is unset; using in-memory storage. "
            "Profiles and usage will be lost on restart. Attach Postgres to persist."
        )
        return

    url = normalise_database_url(raw)
    run_migrations(url)
    engine = make_engine(url)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with session_scope() as session:
        removed = purge_api_requests(session)
    logger.info(
        "Database configured; alembic upgrade head completed; purged %s api_requests row(s) older than %s days",
        removed,
        API_REQUEST_RETENTION_DAYS,
    )


def run_migrations(url: str) -> None:
    """Apply Alembic head. Holds a Postgres advisory lock for the duration."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    # ConfigParser treats % as interpolation.
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))

    locking_engine = None
    lock_conn = None
    try:
        if url.startswith("postgresql"):
            locking_engine = make_engine(url)
            lock_conn = locking_engine.connect()
            lock_conn.execute(text("SELECT pg_advisory_lock(:key)"), {"key": MIGRATION_LOCK_KEY})
            lock_conn.commit()
        command.upgrade(cfg, "head")
    finally:
        if lock_conn is not None:
            lock_conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": MIGRATION_LOCK_KEY})
            lock_conn.commit()
            lock_conn.close()
        if locking_engine is not None:
            locking_engine.dispose()


@contextmanager
def session_scope() -> Iterator[Session]:
    if SessionLocal is None:
        raise RuntimeError("Database is not configured")
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def purge_api_requests(session: Session) -> int:
    """Delete api_requests rows older than 90 days. Returns the number removed."""
    from sqlalchemy import delete

    cutoff = utcnow() - timedelta(days=API_REQUEST_RETENTION_DAYS)
    result = session.execute(delete(ApiRequest).where(ApiRequest.ts < cutoff))
    return int(result.rowcount or 0)


def record_api_request(
    *,
    ts,
    route: str,
    method: str,
    status: int,
    latency_ms: int,
    api_key_id: Optional[str],
    grok_tokens_prompt: Optional[int],
    grok_tokens_completion: Optional[int],
    grok_fallback: Optional[bool],
) -> None:
    """Insert one api_requests row. No-op when the database is not configured.

    The signature has no body fields. Callers cannot persist a request or
    response body through this function.
    """
    if not configured():
        return
    import uuid

    try:
        with session_scope() as session:
            session.add(
                ApiRequest(
                    id=str(uuid.uuid4()),
                    ts=ts,
                    route=route[:512],
                    method=method[:16],
                    status=status,
                    latency_ms=latency_ms,
                    api_key_id=api_key_id,
                    grok_tokens_prompt=grok_tokens_prompt,
                    grok_tokens_completion=grok_tokens_completion,
                    grok_fallback=grok_fallback,
                )
            )
    except Exception:
        logger.exception("Failed to write api_requests row")
