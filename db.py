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
from contextvars import ContextVar
from datetime import timedelta
from pathlib import Path
from typing import Iterator, Optional

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from models import ApiRequest, utcnow

logger = logging.getLogger("plantr.db")
request_id_var: ContextVar[Optional[str]] = ContextVar("plantr_request_id", default=None)

# Dropped only when revision 0001_initial is already applied with the
# pre-analytics shape. Production has no tables until 0.10 is deployed there,
# so this does not run against production. Do not keep using it once
# production has data; add a new revision instead.
PLANTR_TABLES = (
    "tracking_retries",
    "tracked_events",
    "email_messages",
    "pilot_surveys",
    "pilot_visits",
    "pilot_memberships",
    "pilot_applications",
    "pilot_invites",
    "pilot_cohorts",
    "billing_subscriptions",
    "campaigns",
    "stripe_events",
    "portal_accounts",
    "admin_users",
    "creator_product_links",
    "attributed_orders",
    "monthly_mrr_snapshots",
    "subscription_changes",
    "subscriptions",
    "api_requests",
    "briefs",
    "shortlist_items",
    "shortlists",
    "events",
    "products",
    "creators",
    "shops",
    "categories",
    "alembic_version",
)

ROOT = Path(__file__).resolve().parent
API_REQUEST_RETENTION_DAYS = 90
# Arbitrary constant. Session-level so it survives the commit alembic opens.
MIGRATION_LOCK_KEY = 21415001

engine: Optional[Engine] = None
SessionLocal: Optional[sessionmaker] = None

# Process-local fallback used only when DATABASE_URL is unset.
memory_creators: dict = {}
memory_creator_objects: dict = {}
memory_shops: dict = {}
memory_products: dict = {}
memory_events: list = []
memory_shortlists: list = []
memory_shortlist_items: list = []
memory_shortlist_exclusions: list = []
memory_briefs: list = []
memory_admin_users: dict = {}
memory_creator_product_links: list = []
memory_attributed_orders: list = []
memory_portal_accounts: list = []
memory_creator_stripe: dict = {}
memory_stripe_events: dict = {}
memory_campaigns: list = []
memory_cohorts: dict = {}
memory_applications: list = []
memory_invites: list = []
memory_memberships: list = []
memory_visits: list = []
memory_surveys: list = []
memory_emails: list = []
memory_billing: list = []
memory_tracked_event_ids: set = set()
memory_tracked_subjects: set = set()
memory_tracking_retries: list = []
memory_terms_acceptances: list = []
memory_reminder_sends: dict = {}
memory_listing: dict = {}
memory_listing_failures: list = []
memory_listing_rules: list = []
memory_listing_holds: list = []
memory_listing_spend: list = []
memory_samples: list = []
memory_sample_settings: dict = {}


def configured() -> bool:
    return SessionLocal is not None


def current_request_id() -> Optional[str]:
    return request_id_var.get()


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
    memory_creator_objects.clear()
    memory_shops.clear()
    memory_products.clear()
    memory_events.clear()
    memory_shortlists.clear()
    memory_shortlist_items.clear()
    memory_shortlist_exclusions.clear()
    memory_briefs.clear()
    memory_admin_users.clear()
    memory_creator_product_links.clear()
    memory_attributed_orders.clear()
    memory_portal_accounts.clear()
    memory_creator_stripe.clear()
    memory_stripe_events.clear()
    memory_campaigns.clear()
    memory_cohorts.clear()
    memory_applications.clear()
    memory_invites.clear()
    memory_memberships.clear()
    memory_visits.clear()
    memory_surveys.clear()
    memory_emails.clear()
    memory_billing.clear()
    memory_tracked_event_ids.clear()
    memory_tracked_subjects.clear()
    memory_tracking_retries.clear()
    memory_terms_acceptances.clear()
    memory_reminder_sends.clear()
    memory_listing.clear()
    memory_listing_failures.clear()
    memory_listing_rules.clear()
    memory_listing_holds.clear()
    memory_listing_spend.clear()
    memory_samples.clear()
    memory_sample_settings.clear()


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
    if drop_stale_revision_0001(url):
        logger.warning(
            "Dropped the pre-analytics 0001 schema so Alembic can recreate it. "
            "This only runs when alembic_version is 0001_initial and the old columns are present. "
            "Production is not deployed at 0.10 and is not modified."
        )
    run_migrations(url)
    engine = make_engine(url)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with session_scope() as session:
        removed = purge_api_requests(session)
    from attribution import backfill_accepted_links

    import pilots

    pilots.ensure_cohorts()
    created_links = backfill_accepted_links()
    logger.info(
        "Database configured; alembic upgrade head completed; purged %s api_requests row(s) older than %s days; "
        "accepted-link backfill created %s link(s)",
        removed,
        API_REQUEST_RETENTION_DAYS,
        created_links,
    )


def _revision_0001_is_stale(inspector, tables: set[str]) -> bool:
    """True when 0001_initial is stamped but the tables are an older edit of that revision.

    Staging may already be on a previous edit of 0001. Production has no tables.
    """
    if "creator_product_links" not in tables:
        return True
    if "shops" in tables:
        columns = {column["name"] for column in inspector.get_columns("shops")}
        if "country" not in columns or "primary_category" not in columns or "market" not in columns:
            return True
    if "creators" in tables:
        columns = {column["name"] for column in inspector.get_columns("creators")}
        if "average_engagement_rate" in columns or "metrics_source" not in columns:
            return True
    if "products" in tables:
        columns = {column["name"] for column in inspector.get_columns("products")}
        if "source" not in columns or "pet_safe" in columns or "care_level" in columns:
            return True
    if "attributed_orders" in tables:
        columns = {column["name"] for column in inspector.get_columns("attributed_orders")}
        if "amount" in columns or "country" not in columns or "market" not in columns:
            return True
    if "events" in tables:
        columns = {column["name"] for column in inspector.get_columns("events")}
        if "products_count" not in columns:
            return True
    return False


def _table_count(conn, table: str, tables: set[str]) -> int:
    if table not in tables:
        return 0
    return int(conn.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar() or 0)


def drop_stale_revision_0001(url: str) -> bool:
    """Drop Plantr tables when revision 0001_initial has the old column shape.

    Staging may already have applied the first cut of 0001 (attributed_orders.amount,
    events without products_count). The revision id did not change, so Alembic
    would skip it. Production has no tables yet and is left alone: this returns
    false unless that old shape is actually present.
    """
    probe = make_engine(url)
    try:
        with probe.connect() as conn:
            inspector = inspect(conn)
            tables = set(inspector.get_table_names())
            if "alembic_version" not in tables:
                return False
            version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            if version != "0001_initial":
                return False
            stale = _revision_0001_is_stale(inspector, tables)
            if not stale:
                return False
            shops = _table_count(conn, "shops", tables)
            creators = _table_count(conn, "creators", tables)
            if shops or creators:
                raise RuntimeError(
                    "Refusing to drop a stale 0001 schema because it already has data "
                    f"(shops={shops}, creators={creators}). Nothing was dropped."
                )
            logger.warning(
                "Dropping an empty stale 0001 schema so it can be recreated. "
                "shops and creators are empty. Production has no tables and does not hit this path."
            )
            if conn.dialect.name == "postgresql":
                conn.execute(text("DROP TABLE IF EXISTS " + ", ".join(PLANTR_TABLES) + " CASCADE"))
            else:
                conn.execute(text("PRAGMA foreign_keys=OFF"))
                for table in PLANTR_TABLES:
                    conn.execute(text(f'DROP TABLE IF EXISTS "{table}"'))
                conn.execute(text("PRAGMA foreign_keys=ON"))
            conn.commit()
            return True
    finally:
        probe.dispose()


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
    request_id: Optional[str],
    grok_tokens_prompt: Optional[int],
    grok_tokens_completion: Optional[int],
    grok_fallback: Optional[bool],
    grok_latency_ms: Optional[int],
    grok_model: Optional[str],
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
                    request_id=request_id,
                    grok_tokens_prompt=grok_tokens_prompt,
                    grok_tokens_completion=grok_tokens_completion,
                    grok_fallback=grok_fallback,
                    grok_latency_ms=grok_latency_ms,
                    grok_model=grok_model,
                )
            )
    except Exception:
        logger.exception("Failed to write api_requests row")
