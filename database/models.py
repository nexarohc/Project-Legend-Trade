"""Persistence schema: accounts, analyses, strategies, positions and orders."""
import datetime as dt

from sqlalchemy import Column, DateTime, Integer, String, Text, Boolean, Float
from database.db import Base


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------


class User(Base):
    """An account. Passwords are stored as scrypt hashes, never in any recoverable form."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False, index=True)
    display_name = Column(String, nullable=False, default="")
    password_hash = Column(String, nullable=False)

    is_active = Column(Boolean, nullable=False, default=True)
    is_admin = Column(Boolean, nullable=False, default=False)

    # Secret path segment for this user's TradingView webhook URL. Rotatable
    # without changing the password, because a webhook URL ends up pasted into
    # third-party alert configuration and is far more likely to leak.
    webhook_token = Column(String, unique=True, nullable=False, index=True)

    # Bumped on password change and on "sign out everywhere". Tokens issued
    # before this timestamp are refused, which is what makes logout actually
    # revoke access rather than just clearing the browser's copy.
    tokens_valid_from = Column(DateTime, nullable=False, default=dt.datetime.utcnow)

    failed_login_count = Column(Integer, nullable=False, default=0)
    locked_until = Column(DateTime, nullable=True)
    last_login_at = Column(DateTime, nullable=True)

    # TOTP secret is stored as-is, not hashed — verifying a future code requires
    # reproducing it, which is only possible in symmetric form. See the module
    # docstring in app/mfa.py for why that tradeoff is unavoidable here.
    # `mfa_secret` may be set while `mfa_enabled` is still False: /auth/mfa/setup
    # persists a pending secret that only takes effect once /auth/mfa/confirm
    # verifies a real code against it.
    mfa_enabled = Column(Boolean, nullable=False, default=False)
    mfa_secret = Column(String, nullable=True)
    mfa_confirmed_at = Column(DateTime, nullable=True)

    # Set when /auth/password/forgot issues a reset token, cleared when it is
    # used. A reset token's own signature and expiry are not enough to make it
    # single-use, so `/auth/password/reset` additionally requires this to match
    # the token's issued-at time — see the module docstring in
    # app/routers/users.py for why.
    password_reset_requested_at = Column(DateTime, nullable=True)

    # NULL until /auth/email/verify succeeds. Unlike the password-reset token,
    # a verification token needs no single-use trick — verifying twice is
    # harmless, so there's nothing here to invalidate on use, just a stamp.
    email_verified_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=dt.datetime.utcnow)


class MfaRecoveryCode(Base):
    """A one-time backup code for signing in when the authenticator device
    isn't available. Stored hashed, like a password — unlike the TOTP secret,
    a recovery code is never needed in symmetric form, only compared against."""

    __tablename__ = "user_mfa_recovery_codes"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    code_hash = Column(String, nullable=False)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=dt.datetime.utcnow)


# ---------------------------------------------------------------------------
# Trading platform tables
#
# Every table below carries `user_id`. Rows are filtered by the authenticated
# user at the query level rather than trusting an id supplied by the client,
# so one account cannot read or close another's positions.
#
# The learning engine reads these back: an analysis is scored once enough bars
# have printed to know what actually happened, which is what turns a log into
# a calibration record rather than a diary.
# ---------------------------------------------------------------------------


class AnalysisRecord(Base):
    """A stored market analysis, later scored against what price actually did."""

    __tablename__ = "trading_analyses"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True, default=1)
    symbol = Column(String, nullable=False, index=True)
    timeframe = Column(String, nullable=False)
    provider = Column(String, nullable=False, default="")
    price_at_analysis = Column(Float, nullable=False)

    trend = Column(String, nullable=False, default="")
    regime = Column(String, nullable=False, default="")
    dominant_scenario = Column(String, nullable=False, default="")
    dominant_probability = Column(Float, nullable=False, default=0.0)
    confidence = Column(Float, nullable=False, default=0.0)
    verdict = Column(String, nullable=False, default="")

    setup_direction = Column(String, nullable=True)
    setup_entry = Column(Float, nullable=True)
    setup_stop = Column(Float, nullable=True)
    setup_target = Column(Float, nullable=True)

    payload_json = Column(Text, nullable=False, default="{}")

    # Filled in later by the learning engine.
    outcome = Column(String, nullable=True)          # continuation | reversal | range | unresolved
    outcome_price = Column(Float, nullable=True)
    outcome_return = Column(Float, nullable=True)
    was_correct = Column(Boolean, nullable=True)
    scored_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=dt.datetime.utcnow, index=True)


class StrategyRecord(Base):
    """A saved strategy specification and the verdict of its most recent audit."""

    __tablename__ = "trading_strategies"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True, default=1)
    name = Column(String, nullable=False)
    symbol = Column(String, nullable=False, default="")
    timeframe = Column(String, nullable=False, default="")
    source_text = Column(Text, nullable=False, default="")
    spec_json = Column(Text, nullable=False)

    last_verdict = Column(String, nullable=True)
    last_score = Column(Float, nullable=True)
    last_net_profit_percent = Column(Float, nullable=True)
    last_trade_count = Column(Integer, nullable=True)
    last_tested_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=dt.datetime.utcnow)


class BacktestRecord(Base):
    """One backtest run, kept so the learning engine can compare across strategies."""

    __tablename__ = "trading_backtests"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True, default=1)
    strategy_id = Column(Integer, nullable=True, index=True)
    strategy_name = Column(String, nullable=False, default="")
    symbol = Column(String, nullable=False, default="")
    timeframe = Column(String, nullable=False, default="")

    trade_count = Column(Integer, nullable=False, default=0)
    win_rate = Column(Float, nullable=False, default=0.0)
    profit_factor = Column(Float, nullable=True)
    net_profit_percent = Column(Float, nullable=False, default=0.0)
    max_drawdown_percent = Column(Float, nullable=False, default=0.0)
    sharpe_ratio = Column(Float, nullable=True)
    verdict = Column(String, nullable=True)
    audit_score = Column(Float, nullable=True)

    metrics_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, default=dt.datetime.utcnow, index=True)


class PaperPosition(Base):
    """A simulated position. Paper trading is the required step before live capital."""

    __tablename__ = "trading_paper_positions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True, default=1)
    symbol = Column(String, nullable=False, index=True)
    timeframe = Column(String, nullable=False, default="")
    direction = Column(String, nullable=False)          # long | short
    quantity = Column(Float, nullable=False)
    entry_price = Column(Float, nullable=False)
    stop_price = Column(Float, nullable=False)
    target_price = Column(Float, nullable=True)

    status = Column(String, nullable=False, default="open")   # open | closed
    exit_price = Column(Float, nullable=True)
    exit_reason = Column(String, nullable=True)
    realized_pnl = Column(Float, nullable=True)
    r_multiple = Column(Float, nullable=True)

    source = Column(String, nullable=False, default="manual")  # manual | analysis | webhook
    notes = Column(Text, nullable=False, default="")

    opened_at = Column(DateTime, default=dt.datetime.utcnow)
    closed_at = Column(DateTime, nullable=True)


class PriceAlert(Base):
    """A price/condition alert evaluated against the live stream."""

    __tablename__ = "trading_alerts"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True, default=1)
    symbol = Column(String, nullable=False, index=True)
    timeframe = Column(String, nullable=False, default="1h")
    condition = Column(String, nullable=False)        # above | below | crosses
    price = Column(Float, nullable=False)
    note = Column(Text, nullable=False, default="")

    active = Column(Boolean, nullable=False, default=True)
    triggered = Column(Boolean, nullable=False, default=False)
    triggered_at = Column(DateTime, nullable=True)
    triggered_price = Column(Float, nullable=True)

    created_at = Column(DateTime, default=dt.datetime.utcnow)


class WatchlistItem(Base):
    __tablename__ = "trading_watchlist"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True, default=1)
    # Unique per user, not globally: two accounts may both watch BTCUSDT.
    symbol = Column(String, nullable=False, index=True)
    provider = Column(String, nullable=False, default="")
    asset_class = Column(String, nullable=False, default="crypto")
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=dt.datetime.utcnow)


class TradingWebhook(Base):
    """An inbound TradingView alert, stored verbatim plus its parsed form."""

    __tablename__ = "trading_webhooks"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True, default=1)
    raw_body = Column(Text, nullable=False)
    symbol = Column(String, nullable=True, index=True)
    action = Column(String, nullable=True)             # buy | sell | close
    price = Column(Float, nullable=True)
    parsed = Column(Boolean, nullable=False, default=False)
    acted_on = Column(Boolean, nullable=False, default=False)
    note = Column(Text, nullable=False, default="")
    received_at = Column(DateTime, default=dt.datetime.utcnow, index=True)


class AnalysisFeedback(Base):
    """User's own verdict on an analysis, fed back into the learning engine."""

    __tablename__ = "trading_feedback"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True, default=1)
    analysis_id = Column(Integer, nullable=True, index=True)
    rating = Column(String, nullable=False)             # useful | wrong | unclear
    comment = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, default=dt.datetime.utcnow)


# ---------------------------------------------------------------------------
# Live execution
#
# These records are a log of what the platform *asked* the broker to do and
# what it observed back. They are never treated as authoritative — the broker
# is the source of truth, and reconciliation overwrites these from broker state
# rather than the reverse.
# ---------------------------------------------------------------------------


class OrderRecord(Base):
    """Every order attempt, including the ones the guardrails refused.

    Refused attempts are stored on purpose: a blocked order is exactly the
    event worth being able to review later, and dropping it would hide how
    often a guardrail actually fired.
    """

    __tablename__ = "trading_orders"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True, default=1)

    mode = Column(String, nullable=False)              # paper | broker_paper | live
    broker = Column(String, nullable=False, default="")
    broker_order_id = Column(String, nullable=True, index=True)
    client_order_id = Column(String, nullable=True, index=True)

    symbol = Column(String, nullable=False, index=True)
    side = Column(String, nullable=False)              # buy | sell
    quantity = Column(Float, nullable=False)
    order_type = Column(String, nullable=False, default="market")
    limit_price = Column(Float, nullable=True)
    stop_price = Column(Float, nullable=True)
    take_profit_price = Column(Float, nullable=True)

    # accepted | rejected_by_guardrail | filled | cancelled | ... — our view,
    # refreshed from the broker on reconciliation.
    status = Column(String, nullable=False, default="pending", index=True)
    filled_quantity = Column(Float, nullable=False, default=0.0)
    average_fill_price = Column(Float, nullable=True)

    # Why it was allowed or refused.
    allowed = Column(Boolean, nullable=False, default=False)
    guardrail_json = Column(Text, nullable=False, default="{}")
    reason = Column(Text, nullable=False, default="")

    strategy_id = Column(Integer, nullable=True, index=True)
    analysis_id = Column(Integer, nullable=True)

    raw_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, default=dt.datetime.utcnow, index=True)
    reconciled_at = Column(DateTime, nullable=True)


class ExecutionState(Base):
    """Per-user execution settings: mode, kill switch, live confirmation.

    A single row per user. The kill switch and the live-confirmation flag live
    here so they survive a restart — a kill switch that forgets it was engaged
    when the process bounces is worse than none.
    """

    __tablename__ = "trading_execution_state"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, unique=True, index=True)

    mode = Column(String, nullable=False, default="paper")
    broker = Column(String, nullable=False, default="alpaca")

    kill_switch_active = Column(Boolean, nullable=False, default=False)
    kill_switch_reason = Column(Text, nullable=False, default="")
    kill_switch_engaged_at = Column(DateTime, nullable=True)

    # Live confirmation is intentionally not persisted as "on forever": it
    # carries an expiry so arming live mode does not stay armed indefinitely.
    live_confirmed_until = Column(DateTime, nullable=True)

    # JSON-encoded RiskLimits overrides; empty means use the defaults.
    limits_json = Column(Text, nullable=False, default="{}")

    updated_at = Column(DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow)
