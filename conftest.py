"""Shared isolation for API tests.

DATABASE_URL is cleared so a developer shell cannot point pytest at a real
Postgres. Tests that need a database set the variable themselves and call
main.init_storage().
"""

import pytest

import grok_helper
import main


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.delenv("API_KEYS", raising=False)
    monkeypatch.delenv("GROK_API_KEY", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("SHOPIFY_STORE", raising=False)
    monkeypatch.delenv("SHOPIFY_TOKEN", raising=False)
    monkeypatch.delenv("RATE_LIMIT_REQUESTS", raising=False)
    monkeypatch.delenv("RATE_LIMIT_WINDOW_SECONDS", raising=False)
    monkeypatch.delenv("MAX_BODY_BYTES", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(grok_helper, "GROK_API_KEY", None)
    main.reset_runtime_state()
    yield
    main.reset_runtime_state()
