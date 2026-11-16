"""Shared isolation for API tests.

DATABASE_URL is cleared so a developer shell cannot point pytest at a real
Postgres. Tests that need a database set the variable themselves and call
main.init_storage().
"""

import pytest

import grok_helper
import main
import tracking


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.delenv("API_KEYS", raising=False)
    monkeypatch.delenv("GROK_API_KEY", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("SHOPIFY_STORE", raising=False)
    monkeypatch.delenv("SHOPIFY_TOKEN", raising=False)
    for name in (
        "SHOPIFY_API_KEY",
        "SHOPIFY_API_SECRET",
        "SHOPIFY_TOKEN_KEY",
        "COMMISSION_OVERDUE_RULE",
        "COMMISSION_OVERDUE_DAYS",
        "COMMISSION_OVERDUE_GRACE_DAYS",
        "COMMISSION_EXCLUDE_PLATFORM_PAID",
        "COMMISSION_CONFIRM_WINDOW_DAYS",
        "COMMISSION_DISPUTE_RESOLVE_AFTER_DAYS",
        "PAYS_ON_TIME_VISIBILITY",
        "COMMISSION_CORRECTION_WINDOW_DAYS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("RATE_LIMIT_REQUESTS", raising=False)
    monkeypatch.delenv("RATE_LIMIT_WINDOW_SECONDS", raising=False)
    monkeypatch.delenv("MAX_BODY_BYTES", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("EMAIL_FROM", raising=False)
    monkeypatch.delenv("PILOT_START", raising=False)
    monkeypatch.delenv("PILOT_END", raising=False)
    monkeypatch.delenv("PILOT_REMINDER_AT", raising=False)
    monkeypatch.setenv("REMINDERS_LOOP", "off")
    # Pilot applications stay closed until the operator is named. Most tests need them open.
    monkeypatch.setenv("OPERATOR_LEGAL_NAME", "Test Operator Pty Ltd")
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    monkeypatch.delenv("MANUAL_REPORTING_LIVE", raising=False)
    monkeypatch.delenv("NON_SHOPIFY_CAP", raising=False)
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD_HASH", raising=False)
    monkeypatch.delenv("ADMIN_SETUP_TOKEN", raising=False)
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("STRIPE_PUBLISHABLE_KEY", raising=False)
    monkeypatch.delenv("STRIPE_PLATFORM_FEE_PERCENT", raising=False)
    monkeypatch.delenv("STRIPE_CREATOR_COMMISSION_PERCENT", raising=False)
    monkeypatch.setattr(grok_helper, "GROK_API_KEY", None)
    main.reset_runtime_state()
    tracking.reset()
    yield
    main.reset_runtime_state()
    tracking.reset()
