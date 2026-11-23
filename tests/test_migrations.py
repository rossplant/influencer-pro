"""Alembic boolean defaults, including a real Postgres upgrade when configured."""

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
VERSIONS = ROOT / "alembic" / "versions"


def test_boolean_defaults_are_not_integers():
    import re

    pattern = re.compile(r"Boolean\(\)(?:(?!sa\.Column\().){0,180}", re.S)
    for path in VERSIONS.glob("*.py"):
        source = path.read_text()
        for block in pattern.findall(source):
            assert 'text("0")' not in block
            assert 'text("1")' not in block
            assert re.search(r"server_default\s*=\s*[\"'][01][\"']", block) is None


def test_alembic_upgrade_head_on_postgres():
    url = os.getenv("POSTGRES_TEST_URL", "").strip()
    if not url:
        pytest.skip("POSTGRES_TEST_URL is not set")
    from alembic import command
    from alembic.config import Config

    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(cfg, "head")
    command.upgrade(cfg, "head")
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT table_name, column_name, column_default
                FROM information_schema.columns
                WHERE data_type = 'boolean' AND table_schema = 'public'
                """
            )
        ).all()
    assert rows
    for table_name, column_name, default in rows:
        if default is None:
            continue
        assert default.strip().lower() in {"false", "true"}, f"{table_name}.{column_name} default {default}"
    os.environ["DATABASE_URL"] = url
    os.environ["SESSION_SECRET"] = "test-session-secret"
    import main
    from fastapi.testclient import TestClient

    main.init_storage()
    page = TestClient(main.app).get("/founding-curators")
    assert page.status_code == 200
    assert "Find products you genuinely rate." in page.text
