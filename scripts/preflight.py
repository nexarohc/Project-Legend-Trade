#!/usr/bin/env python3
"""Check everything standing between you and your first broker order.

The information is already available piecemeal — credentials in the
environment, connectivity from the adapter's `health()`, guardrail rules from
`/execution/status` — but it is spread across a UI you have to be signed in to
reach, and the first thing an operator wants to know is simply *"will this
work, and if not, what do I fix?"*

So this runs the whole chain in one pass, from the command line, before any
order exists:

    1. Credentials     — present? (never printed)
    2. Connectivity    — does the broker answer, and what does it say?
    3. Account state   — equity, buying power, trading blocked?
    4. Guardrails      — a representative order run through the real rules,
                         reporting exactly which would block it

Step 4 is the part worth having. Every guardrail is unit-tested, but "which
rules would refuse the order I am about to place" is otherwise only discoverable
by placing it and reading the refusal. This asks the same code the same question
without sending anything.

**This never transmits an order.** It calls read-only broker endpoints and
evaluates guardrails in memory. Placing the real first order is a deliberate act
and stays in your hands.

Usage:
    python scripts/preflight.py                      # alpaca, broker_paper
    python scripts/preflight.py --broker tradier
    python scripts/preflight.py --mode live          # read-only even here
    python scripts/preflight.py --symbol AAPL --quantity 10

Exit status is 0 only when the chain is ready end to end, so this is usable as
a deployment gate.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# Run from anywhere: the repo root has to be importable for `trading.*`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trading.env import DEFAULT_ENV_FILE, load_env_file  # noqa: E402
from trading.execution import guardrails  # noqa: E402
from trading.execution.base import (  # noqa: E402
    ExecutionMode,
    OrderRequest,
    OrderSide,
    OrderType,
)
from trading.execution.engine import _default_adapter_factory  # noqa: E402

TICK = "  ok  "
CROSS = " FAIL "
WARN = " warn "


def line(status: str, message: str) -> None:
    print(f"[{status}] {message}")


def check_credentials(broker: str, mode: ExecutionMode, adapter, loaded: list[str]) -> bool:
    """Credentials present? Report *which* variables, never their values."""
    print("\n1. Credentials")
    # Say where they came from. "MISSING" printed while a correctly filled-in
    # .env sits in the repo root is the most confusing output this script could
    # produce, so the loader's result is reported either way.
    if loaded:
        print(f"       loaded {len(loaded)} variable(s) from {DEFAULT_ENV_FILE.name}")
    elif DEFAULT_ENV_FILE.is_file():
        print(f"       {DEFAULT_ENV_FILE.name} was read, but every name in it "
              "was already set in the environment")
    else:
        print(f"       no .env at {DEFAULT_ENV_FILE.parent} — reading the environment only")

    if adapter is None:
        line(CROSS, f"no adapter for broker {broker!r}")
        return False

    if adapter.configured:
        line(TICK, f"{broker} credentials found for {mode.value} mode")
        return True

    line(CROSS, f"{broker} has no credentials for {mode.value} mode")
    # The adapter knows which variable names it wants; asking it beats keeping a
    # duplicate list here that drifts the moment a broker is added.
    hint = getattr(adapter, "credential_env_names", None)
    if callable(hint):
        for name in hint():
            state = "set" if os.environ.get(name) else "MISSING"
            line(WARN if state == "MISSING" else TICK, f"  {name}: {state}")
    return False


def check_connectivity(adapter) -> tuple[bool, dict]:
    print("\n2. Connectivity")
    health = adapter.health()
    if not health.get("connected"):
        line(CROSS, f"not connected: {health.get('reason', 'unknown reason')}")
        return False, health
    line(TICK, f"connected to {health.get('broker')} ({health.get('mode')})")
    return True, health


def check_account(health: dict, mode: ExecutionMode) -> tuple[bool, float]:
    """Report account state. Returns (usable, equity)."""
    print("\n3. Account")
    account = health.get("account") or {}
    equity = float(account.get("equity") or 0.0)
    is_paper = account.get("is_paper")

    line(TICK, f"account id      {account.get('account_id', '?')}")
    line(TICK, f"equity          {equity:,.2f} {account.get('currency', 'USD')}")
    line(TICK, f"buying power    {float(account.get('buying_power') or 0.0):,.2f}")
    line(TICK, f"paper account   {is_paper}")

    usable = True

    # The accident this whole script exists to prevent: live credentials pasted
    # into the paper slot. Adapters read separate variables per mode precisely so
    # that cannot happen quietly, but nothing stops someone copying the wrong key
    # pair into the right-looking name.
    #
    # The broker's own answer is the authority here — not the variable it came
    # from, and not the shape of the key. If we asked for paper and the account
    # says it is real, refuse and say so loudly, because the next step after a
    # green preflight is placing an order.
    if mode is ExecutionMode.BROKER_PAPER and is_paper is False:
        line(CROSS, "these credentials belong to a REAL-MONEY account, not a paper one")
        line(CROSS, "  the broker reported is_paper=false while running in broker_paper mode")
        line(CROSS, "  check which key pair went into the paper environment variables")
        usable = False

    # The mirror case is worth a warning rather than a failure: paper keys in
    # live mode cannot lose money, so it is a configuration mistake, not a
    # hazard — but it does mean "live" is not live.
    if mode is ExecutionMode.LIVE and is_paper is True:
        line(WARN, "live mode is pointed at a PAPER account — no real order will be placed")

    if account.get("trading_blocked"):
        # Brokers halt accounts for margin and PDT breaches. An order will fail
        # for a reason invisible in the app, so surface it here.
        line(CROSS, "trading is BLOCKED on this account — the broker will reject orders")
        usable = False
    if account.get("pattern_day_trader"):
        line(WARN, "flagged as a pattern day trader — day-trade limits apply")
    if equity <= 0:
        line(CROSS, "equity is zero — guardrails size positions off equity and will refuse")
        usable = False

    return usable, equity


def check_guardrails(mode: ExecutionMode, equity: float, symbol: str,
                     quantity: float, price: float) -> bool:
    """Run a representative order through the real rules without sending it."""
    print("\n4. Guardrails (evaluated, not sent)")

    # A protective stop 2% below entry: inside the platform's default distance
    # band, so a refusal here is about the *account*, not about a stop the
    # sample deliberately got wrong.
    stop = round(price * 0.98, 4)
    request = OrderRequest(
        symbol=symbol,
        side=OrderSide.BUY,
        quantity=quantity,
        order_type=OrderType.MARKET,
        stop_price=stop,
        reason="preflight sample order — never transmitted",
    )
    context = guardrails.ExecutionContext(
        mode=mode,
        user_id=0,
        account_equity=equity,
        current_price=price,
        open_position_count=0,
        existing_exposure=0.0,
        orders_last_hour=0,
    )

    decision = guardrails.evaluate(request, context)

    notional = quantity * price
    line(TICK, f"sample: BUY {quantity} {symbol} @ ~{price:,.2f} "
               f"(notional {notional:,.2f}, stop {stop:,.4f})")

    for refusal in decision.refusals:
        line(CROSS, f"{refusal.rule}: {refusal.reason}")
    for warning in decision.warnings:
        line(WARN, f"{warning.rule}: {warning.reason}")

    if decision.allowed:
        line(TICK, f"{len(decision.checks_run)} checks run, none blocking")
        return True

    # Live mode refusing here is correct and expected, not a fault to fix: the
    # confirmation phrase is typed in the UI and is deliberately not something a
    # script can supply.
    if mode is ExecutionMode.LIVE:
        line(WARN, "live mode refuses until armed in the UI — expected, not a fault")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--broker", default="alpaca", help="alpaca | tradier | ibkr")
    parser.add_argument(
        "--mode",
        default="broker_paper",
        choices=["broker_paper", "live"],
        help="`paper` is internal simulation and needs no broker, so it is not offered here",
    )
    parser.add_argument("--symbol", default="AAPL")
    parser.add_argument("--quantity", type=float, default=1.0)
    parser.add_argument(
        "--price",
        type=float,
        default=100.0,
        help="assumed price for the sample order; no market data is fetched",
    )
    args = parser.parse_args()

    # The guardrail layer logs every refusal, which is right in a server and
    # wrong here: this script prints its own itemised report, so the log line
    # arrives as an unformatted duplicate in the middle of it.
    logging.getLogger("legend.execution.guardrails").setLevel(logging.CRITICAL)

    mode = ExecutionMode(args.mode)
    print(f"Legend Trade preflight — broker={args.broker} mode={mode.value}")
    print("No order is transmitted by this script.")

    # Adapters read credentials straight from os.environ, and pydantic-settings
    # populates a Settings object rather than the environment — so without this
    # the documented `.env` is invisible to everything below.
    loaded = load_env_file()

    try:
        adapter = _default_adapter_factory(args.broker, mode)
    except Exception as exc:  # noqa: BLE001 - report, don't traceback at an operator
        line(CROSS, f"could not construct the {args.broker} adapter: {exc}")
        return 1

    if not check_credentials(args.broker, mode, adapter, loaded):
        print("\nNOT READY — add credentials, then run this again.")
        return 1

    connected, health = check_connectivity(adapter)
    if not connected:
        print("\nNOT READY — credentials are present but the broker did not accept them.")
        return 1

    account_ok, equity = check_account(health, mode)
    guardrails_ok = check_guardrails(mode, equity, args.symbol, args.quantity, args.price)

    if mode is ExecutionMode.LIVE and not guardrails.live_trading_enabled_on_instance():
        print()
        line(WARN, f"{guardrails.LIVE_ENABLED_ENV} is not set — live mode is unreachable")

    print()
    if account_ok and guardrails_ok:
        print("READY — the chain is clear. Place the first order from the Execute panel.")
        return 0
    print("NOT READY — see the FAIL lines above.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
