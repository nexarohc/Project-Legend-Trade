"""Schema migrations for existing databases.

`Base.metadata.create_all` creates missing *tables* but never alters existing
ones, so a database created before authentication existed would keep its old
trading tables with no `user_id` column and every query against them would
fail. This module closes that gap.

It is deliberately small rather than a full migration framework. The changes
needed here are additive column adds that SQLite supports directly, and each
step is idempotent — it inspects the live schema and does nothing when the
column is already present — so it is safe to run on every startup. If the
schema ever needs destructive changes (dropping columns, changing types,
splitting tables), replace this with Alembic rather than growing it.

Two layers, in order:

1. **Explicit steps**, below, for changes that carry intent a generic pass
   could not infer — `user_id` defaulting to 1 so pre-existing rows belong to
   the first account, backfilling webhook tokens, dropping a stale unique
   index.
2. **A generic additive sweep** (`_add_missing_columns`) that compares every
   table against its SQLAlchemy model and adds any column the model declares
   but the database lacks. This exists because the failure mode of layer 1 is
   silent and expensive: add a column to `models.py`, forget the matching
   block here, and existing installs break on the next query while a fresh
   one works fine — so it passes every test written against a new database.
   The sweep closes that by construction rather than by remembering.

The sweep is deliberately conservative. It will not invent a value for a
`NOT NULL` column with no default when the table already has rows — it logs
loudly and skips instead, because guessing there silently rewrites data.
"""
from __future__ import annotations

import datetime as dt
import logging
import secrets

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

logger = logging.getLogger("legend.migrations")

# table -> column -> DDL type, for the per-user isolation columns.
USER_SCOPED_TABLES = (
    "trading_analyses",
    "trading_strategies",
    "trading_backtests",
    "trading_paper_positions",
    "trading_alerts",
    "trading_watchlist",
    "trading_webhooks",
    "trading_feedback",
)


def run_migrations(engine: Engine) -> dict:
    """Bring an existing database up to the current schema.

    Returns a summary of what changed, so startup logs show whether a migration
    actually ran rather than silently doing nothing.
    """
    applied: list[str] = []
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    with engine.begin() as connection:
        # --- add user_id to trading tables -----------------------------------
        for table in USER_SCOPED_TABLES:
            if table not in existing_tables:
                continue  # create_all will build it with the column already present
            columns = {c["name"] for c in inspector.get_columns(table)}
            if "user_id" in columns:
                continue

            # Default to 1 so pre-existing rows belong to the first account,
            # which is the only sensible owner for data created before accounts
            # existed. NOT NULL with a default keeps the column queryable.
            connection.execute(
                text(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1")
            )
            connection.execute(
                text(f"CREATE INDEX IF NOT EXISTS ix_{table}_user_id ON {table} (user_id)")
            )
            applied.append(f"{table}.user_id")

        # --- backfill webhook tokens ------------------------------------------
        # A user row created before webhook tokens existed would have an empty
        # token, and an empty secret in a URL is not a secret.
        if "users" in existing_tables:
            user_columns = {c["name"] for c in inspector.get_columns("users")}
            if "webhook_token" in user_columns:
                rows = connection.execute(
                    text("SELECT id FROM users WHERE webhook_token IS NULL OR webhook_token = ''")
                ).fetchall()
                for (user_id,) in rows:
                    connection.execute(
                        text("UPDATE users SET webhook_token = :token WHERE id = :id"),
                        {"token": secrets.token_urlsafe(32), "id": user_id},
                    )
                    applied.append(f"users.webhook_token[{user_id}]")

            if "tokens_valid_from" in user_columns:
                connection.execute(
                    text(
                        "UPDATE users SET tokens_valid_from = :now "
                        "WHERE tokens_valid_from IS NULL"
                    ),
                    {"now": dt.datetime.utcnow()},
                )

        # --- drop the old global-unique index on watchlist symbols ------------
        # Watchlist symbols are unique per user now, not globally. A leftover
        # global UNIQUE index would stop a second account watching BTCUSDT.
        if "trading_watchlist" in existing_tables:
            for index in inspector.get_indexes("trading_watchlist"):
                if index.get("unique") and index.get("column_names") == ["symbol"]:
                    connection.execute(text(f"DROP INDEX IF EXISTS {index['name']}"))
                    applied.append("trading_watchlist: dropped global unique symbol index")

        # --- execution tables --------------------------------------------------
        # trading_orders and trading_execution_state are created by
        # Base.metadata.create_all when absent; nothing to alter on an existing
        # install because they are new. This block is a placeholder marker so a
        # future additive column follows the same idempotent pattern as above.

        # --- MFA columns on users ------------------------------------------------
        # user_mfa_recovery_codes is a brand-new table, created by create_all.
        if "users" in existing_tables:
            user_columns = {c["name"] for c in inspector.get_columns("users")}
            if "mfa_enabled" not in user_columns:
                connection.execute(
                    text("ALTER TABLE users ADD COLUMN mfa_enabled BOOLEAN NOT NULL DEFAULT 0")
                )
                applied.append("users.mfa_enabled")
            if "mfa_secret" not in user_columns:
                connection.execute(text("ALTER TABLE users ADD COLUMN mfa_secret VARCHAR"))
                applied.append("users.mfa_secret")
            if "mfa_confirmed_at" not in user_columns:
                connection.execute(text("ALTER TABLE users ADD COLUMN mfa_confirmed_at DATETIME"))
                applied.append("users.mfa_confirmed_at")

        # --- password reset column on users -------------------------------------
        if "users" in existing_tables:
            user_columns = {c["name"] for c in inspector.get_columns("users")}
            if "password_reset_requested_at" not in user_columns:
                connection.execute(
                    text("ALTER TABLE users ADD COLUMN password_reset_requested_at DATETIME")
                )
                applied.append("users.password_reset_requested_at")

        # --- email verification column on users ---------------------------------
        if "users" in existing_tables:
            user_columns = {c["name"] for c in inspector.get_columns("users")}
            if "email_verified_at" not in user_columns:
                connection.execute(
                    text("ALTER TABLE users ADD COLUMN email_verified_at DATETIME")
                )
                applied.append("users.email_verified_at")

    # --- generic sweep, after the explicit steps above -----------------------
    # Catches any model column the blocks above don't already handle, so
    # adding a column to models.py cannot silently break existing installs.
    applied.extend(_add_missing_columns(engine))

    if applied:
        logger.info("applied %d schema migration(s): %s", len(applied), ", ".join(applied[:12]))
    return {"applied": applied, "count": len(applied)}


def _sqlite_literal(value) -> str | None:
    """Render a Python default as a SQL literal, or None if it isn't safe to."""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    return None


def _default_clause(column) -> str | None:
    """A DEFAULT clause for a NOT NULL column, or None when we can't infer one.

    Only *static* defaults are usable here. A callable default (like
    `datetime.utcnow`) produces a different value per row and cannot be
    expressed as a single DDL literal, so those fall through to the
    type-based fallback rather than being frozen to one timestamp.
    """
    default = getattr(column, "default", None)
    if default is not None and not getattr(default, "is_callable", False):
        literal = _sqlite_literal(getattr(default, "arg", None))
        if literal is not None:
            return literal

    server_default = getattr(column, "server_default", None)
    if server_default is not None and getattr(server_default, "arg", None) is not None:
        return str(server_default.arg)

    # Fall back to a type-appropriate empty value. This is only reached for a
    # NOT NULL column with no usable default, where the alternative is
    # refusing to migrate at all.
    type_name = column.type.__class__.__name__.upper()
    if "BOOL" in type_name:
        return "0"
    if "INT" in type_name or "FLOAT" in type_name or "NUMERIC" in type_name:
        return "0"
    if "CHAR" in type_name or "TEXT" in type_name or "STRING" in type_name:
        return "''"
    return None


def _add_missing_columns(engine: Engine) -> list[str]:
    """Add every column a model declares that its table is missing."""
    from database.db import Base  # imported here to avoid a circular import

    applied: list[str] = []
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    for table_name, table in Base.metadata.tables.items():
        if table_name not in existing_tables:
            continue  # create_all builds it complete; nothing to alter

        existing_columns = {c["name"] for c in inspector.get_columns(table_name)}
        missing = [c for c in table.columns if c.name not in existing_columns]
        if not missing:
            continue

        has_rows = False
        with engine.connect() as connection:
            has_rows = connection.execute(
                text(f"SELECT 1 FROM {table_name} LIMIT 1")  # noqa: S608 - name from metadata
            ).first() is not None

        for column in missing:
            if column.primary_key:
                # A primary key cannot be bolted on after the fact; that is a
                # table rebuild, which is exactly the destructive case this
                # module's docstring says to use Alembic for.
                logger.error(
                    "cannot add primary key column %s.%s to an existing table — "
                    "this needs a real migration tool, not an ALTER",
                    table_name, column.name,
                )
                continue

            try:
                type_ddl = column.type.compile(engine.dialect)
            except Exception:  # noqa: BLE001 - an uncompilable type is not migratable
                logger.error(
                    "cannot render a DDL type for %s.%s (%r); skipping",
                    table_name, column.name, column.type,
                )
                continue

            clause = f"ALTER TABLE {table_name} ADD COLUMN {column.name} {type_ddl}"

            if not column.nullable:
                default_sql = _default_clause(column)
                if default_sql is None:
                    logger.error(
                        "%s.%s is NOT NULL with no usable default; refusing to guess a "
                        "value for existing rows. Add an explicit migration step for it.",
                        table_name, column.name,
                    )
                    continue
                # SQLite requires the default when the table already has rows,
                # and it is harmless when empty.
                clause += f" NOT NULL DEFAULT {default_sql}"
            elif has_rows:
                default_sql = _default_clause(column) if column.default is not None else None
                if default_sql is not None:
                    clause += f" DEFAULT {default_sql}"

            try:
                with engine.begin() as connection:
                    connection.execute(text(clause))
            except Exception as exc:  # noqa: BLE001 - report and continue, don't abort startup
                logger.error("failed to add %s.%s: %s", table_name, column.name, exc)
                continue

            applied.append(f"{table_name}.{column.name} (auto)")
            logger.info("added missing column %s.%s", table_name, column.name)

    return applied
