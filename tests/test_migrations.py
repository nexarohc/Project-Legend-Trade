"""Schema migrations for existing databases.

The failure mode these guard against is quiet and expensive: add a column to
`models.py`, forget the matching step in `migrations.py`, and a *fresh*
install works perfectly while every *existing* one starts erroring on the
next query. Tests written against a new database would all pass.

So the tests here deliberately do the opposite of the rest of the suite —
they build deliberately out-of-date databases and prove migration catches
them up.
"""
import datetime as dt
import secrets

import pytest
from sqlalchemy import Boolean, Column, DateTime, Integer, String, create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.migrations import run_migrations
from database.models import User


@pytest.fixture()
def legacy_engine(tmp_path):
    """A database at the current schema, ready to be aged backwards."""
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


def _seed_user(engine, email="existing@example.com"):
    session = sessionmaker(bind=engine)()
    session.add(User(
        email=email, display_name="Existing", password_hash="x",
        webhook_token=secrets.token_urlsafe(8), tokens_valid_from=dt.datetime.utcnow(),
    ))
    session.commit()
    session.close()


def _columns(engine, table):
    return {c["name"] for c in inspect(engine).get_columns(table)}


@pytest.fixture()
def restore_metadata():
    """Undo columns a test bolts onto the shared model metadata.

    `Base.metadata` is module-global, so a test that appends a column would
    leak it into every later test in the session if it did not clean up.
    """
    added: list[tuple[str, str]] = []

    def track(table_name: str, column: Column):
        Base.metadata.tables[table_name].append_column(column)
        added.append((table_name, column.name))

    yield track

    for table_name, column_name in added:
        table = Base.metadata.tables[table_name]
        table._columns.remove(table.c[column_name])


# --- the generic sweep --------------------------------------------------------

def test_adds_a_model_column_that_has_no_explicit_migration(legacy_engine, restore_metadata):
    """The whole point: forgetting to hand-write a migration must not break
    an existing database."""
    _seed_user(legacy_engine)
    restore_metadata("users", Column("theme_preference", String, nullable=True))

    applied = run_migrations(legacy_engine)["applied"]

    assert "theme_preference" in _columns(legacy_engine, "users")
    assert any("theme_preference" in step for step in applied)


def test_adds_a_not_null_column_with_a_default_to_a_table_with_rows(legacy_engine, restore_metadata):
    """SQLite refuses a NOT NULL add without a default once rows exist, so
    the sweep has to supply one."""
    _seed_user(legacy_engine)
    restore_metadata("users", Column("newsletter_optin", Boolean, nullable=False, default=False))

    run_migrations(legacy_engine)

    with legacy_engine.connect() as connection:
        value = connection.execute(text("SELECT newsletter_optin FROM users")).scalar()
    assert value == 0  # the existing row got the default, not a NULL or an error


def test_existing_rows_survive_the_sweep_intact(legacy_engine, restore_metadata):
    _seed_user(legacy_engine, email="survivor@example.com")
    restore_metadata("users", Column("login_streak", Integer, nullable=False, default=0))

    run_migrations(legacy_engine)

    with legacy_engine.connect() as connection:
        row = connection.execute(
            text("SELECT email, login_streak FROM users")
        ).first()
    assert row[0] == "survivor@example.com"
    assert row[1] == 0


def test_the_sweep_is_idempotent(legacy_engine, restore_metadata):
    _seed_user(legacy_engine)
    restore_metadata("users", Column("run_twice", String, nullable=True))

    first = run_migrations(legacy_engine)["applied"]
    second = run_migrations(legacy_engine)["applied"]

    assert any("run_twice" in step for step in first)
    assert second == [], "a second run should find nothing left to do"


def test_a_not_null_column_with_no_usable_default_is_refused_not_guessed(
    legacy_engine, restore_metadata, caplog
):
    """Inventing a timestamp for every existing row would silently write data
    that was never true. Refusing loudly is the correct outcome."""
    _seed_user(legacy_engine)
    restore_metadata("users", Column("must_be_set_at", DateTime, nullable=False))

    with caplog.at_level("ERROR", logger="dex.migrations"):
        run_migrations(legacy_engine)

    assert "must_be_set_at" not in _columns(legacy_engine, "users")
    assert any("refusing to guess" in r.message for r in caplog.records)


def test_the_sweep_leaves_an_already_current_database_alone(legacy_engine):
    _seed_user(legacy_engine)
    assert run_migrations(legacy_engine)["applied"] == []


def test_tables_absent_from_the_database_are_skipped(tmp_path):
    """An empty database is create_all's job, not the sweep's — it must not
    try to ALTER tables that do not exist yet."""
    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    assert run_migrations(engine)["applied"] == []
    engine.dispose()


# --- the explicit steps, on a genuinely old schema ----------------------------

def test_a_pre_auth_database_gains_every_column_the_model_expects(tmp_path):
    """Recreates the users table as it existed before MFA, password reset and
    email verification, then proves migration catches it fully up."""
    engine = create_engine(f"sqlite:///{tmp_path / 'preauth.db'}")
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                email VARCHAR NOT NULL,
                display_name VARCHAR,
                password_hash VARCHAR NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT 1,
                is_admin BOOLEAN NOT NULL DEFAULT 0,
                webhook_token VARCHAR,
                tokens_valid_from DATETIME,
                failed_login_count INTEGER NOT NULL DEFAULT 0,
                locked_until DATETIME,
                last_login_at DATETIME,
                created_at DATETIME
            )
        """))
        connection.execute(text(
            "INSERT INTO users (email, password_hash) VALUES ('old@example.com', 'x')"
        ))

    run_migrations(engine)
    Base.metadata.create_all(bind=engine)

    expected = {c.name for c in Base.metadata.tables["users"].columns}
    assert expected <= _columns(engine, "users")
    engine.dispose()


def test_a_pre_auth_database_gets_user_id_on_trading_tables(tmp_path):
    """The original reason this module exists: trading tables created before
    accounts had no user_id, and every query against them failed."""
    engine = create_engine(f"sqlite:///{tmp_path / 'nouserid.db'}")
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE trading_watchlist (
                id INTEGER PRIMARY KEY,
                symbol VARCHAR NOT NULL,
                provider VARCHAR,
                asset_class VARCHAR,
                sort_order INTEGER,
                created_at DATETIME
            )
        """))
        connection.execute(text("INSERT INTO trading_watchlist (symbol) VALUES ('BTCUSDT')"))

    run_migrations(engine)

    assert "user_id" in _columns(engine, "trading_watchlist")
    with engine.connect() as connection:
        # Pre-existing rows belong to the first account, not to nobody.
        assert connection.execute(text("SELECT user_id FROM trading_watchlist")).scalar() == 1
    engine.dispose()


def test_empty_webhook_tokens_are_backfilled(tmp_path):
    """An empty secret in a URL is not a secret."""
    engine = create_engine(f"sqlite:///{tmp_path / 'tokens.db'}")
    Base.metadata.create_all(bind=engine)
    # Seed through the ORM so every NOT NULL column is populated, then blank
    # the token to recreate the pre-webhook-token state. Hand-writing the
    # INSERT would need updating every time a column is added — the same
    # brittleness the generic sweep exists to remove.
    _seed_user(engine, email="t@example.com")
    with engine.begin() as connection:
        connection.execute(text("UPDATE users SET webhook_token = ''"))

    run_migrations(engine)

    with engine.connect() as connection:
        token = connection.execute(text("SELECT webhook_token FROM users")).scalar()
    assert token, "an empty webhook token must be replaced with a real one"
    assert len(token) > 20
    engine.dispose()
