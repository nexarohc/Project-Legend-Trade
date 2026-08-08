"""Background reconciliation: keeps broker-connected accounts' order and
position state in sync with the broker without anyone needing to call
`POST /execution/reconcile` by hand.

Only users whose execution mode actually touches a broker (`broker_paper` or
`live`) are reconciled — internal paper mode has no broker to reconcile
against, and `ExecutionEngine.reconcile()` already returns early for it, so
including paper accounts here would just be wasted work, not wasted
correctness. One user's failure (bad credentials, broker outage) is logged
and skipped; it never stops the rest of the pass or kills the loop.
"""
from __future__ import annotations

import asyncio
import logging

from database.db import SessionLocal
from database.models import ExecutionState
from trading.execution.engine import ExecutionEngine

logger = logging.getLogger("legend.execution.scheduler")


def reconcile_all_once() -> dict:
    """One synchronous pass: reconcile every user in a broker-connected mode.

    Returns a summary rather than raising, so a caller (the loop, or a test)
    always gets a result even when individual accounts fail.
    """
    session = SessionLocal()
    summary = {"attempted": 0, "reconciled": 0, "errors": []}
    try:
        user_ids = [
            row.user_id
            for row in session.query(ExecutionState)
            .filter(ExecutionState.mode != "paper")
            .all()
        ]
        for user_id in user_ids:
            summary["attempted"] += 1
            try:
                outcome = ExecutionEngine(session, user_id).reconcile()
                if outcome.get("reconciled"):
                    summary["reconciled"] += 1
                elif outcome.get("reason"):
                    logger.info("reconcile skipped for user %s: %s", user_id, outcome["reason"])
            except Exception:
                logger.exception("scheduled reconcile failed for user %s", user_id)
                summary["errors"].append(user_id)
    finally:
        session.close()
    return summary


async def reconcile_loop(interval_seconds: int) -> None:
    """Reconcile every broker-connected account on a fixed interval, forever,
    until cancelled. Started from the app's lifespan; cancellation (server
    shutdown) is expected and re-raised, not logged as an error.
    """
    logger.info("scheduled broker reconcile loop started (every %ss)", interval_seconds)
    try:
        while True:
            await asyncio.sleep(interval_seconds)
            summary = await asyncio.to_thread(reconcile_all_once)
            if summary["attempted"]:
                logger.info(
                    "scheduled reconcile: %s/%s account(s) reconciled, %s error(s)",
                    summary["reconciled"], summary["attempted"], len(summary["errors"]),
                )
    except asyncio.CancelledError:
        logger.info("scheduled broker reconcile loop stopped")
        raise
