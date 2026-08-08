"""Scheduled broker reconciliation: the background loop that keeps
broker-connected accounts synced without a manual POST /execution/reconcile.

No test starts the real asyncio loop or sleeps — `reconcile_all_once` is the
synchronous unit under test, and the loop wrapper is exercised only for one
iteration with a stubbed sleep so the test suite stays fast and deterministic.
"""
import asyncio

import pytest

from database.models import ExecutionState
from trading.execution.scheduler import reconcile_all_once, reconcile_loop


def _state(session, user_id: int, mode: str) -> ExecutionState:
    row = ExecutionState(user_id=user_id, mode=mode)
    session.add(row)
    session.commit()
    return row


def test_reconcile_all_once_skips_paper_accounts(session, monkeypatch):
    _state(session, user_id=1, mode="paper")

    calls = []
    monkeypatch.setattr(
        "trading.execution.scheduler.ExecutionEngine",
        lambda s, uid: calls.append(uid) or _StubEngine(),
    )

    summary = reconcile_all_once()
    assert calls == []
    assert summary == {"attempted": 0, "reconciled": 0, "errors": []}


class _StubEngine:
    def __init__(self, outcome=None, raises=False):
        self._outcome = outcome or {"reconciled": True}
        self._raises = raises

    def reconcile(self):
        if self._raises:
            raise RuntimeError("broker unreachable")
        return self._outcome


def test_reconcile_all_once_reconciles_broker_connected_accounts(session, monkeypatch):
    _state(session, user_id=1, mode="paper")
    _state(session, user_id=2, mode="broker_paper")
    _state(session, user_id=3, mode="live")

    seen = []

    def fake_engine(s, uid):
        seen.append(uid)
        return _StubEngine(outcome={"reconciled": True})

    monkeypatch.setattr("trading.execution.scheduler.ExecutionEngine", fake_engine)

    summary = reconcile_all_once()
    assert sorted(seen) == [2, 3]
    assert summary == {"attempted": 2, "reconciled": 2, "errors": []}


def test_reconcile_all_once_records_reconciled_false_without_erroring(session, monkeypatch):
    _state(session, user_id=2, mode="broker_paper")
    monkeypatch.setattr(
        "trading.execution.scheduler.ExecutionEngine",
        lambda s, uid: _StubEngine(outcome={"reconciled": False, "reason": "no credentials"}),
    )

    summary = reconcile_all_once()
    assert summary == {"attempted": 1, "reconciled": 0, "errors": []}


def test_reconcile_all_once_isolates_a_failing_account(session, monkeypatch):
    _state(session, user_id=2, mode="broker_paper")
    _state(session, user_id=3, mode="broker_paper")

    def fake_engine(s, uid):
        return _StubEngine(raises=(uid == 2))

    monkeypatch.setattr("trading.execution.scheduler.ExecutionEngine", fake_engine)

    summary = reconcile_all_once()
    assert summary["attempted"] == 2
    assert summary["reconciled"] == 1
    assert summary["errors"] == [2]


def test_reconcile_loop_runs_reconcile_all_once_each_tick(monkeypatch):
    reconcile_calls = {"count": 0}

    def fake_reconcile_all_once():
        reconcile_calls["count"] += 1
        return {"attempted": 0, "reconciled": 0, "errors": []}

    monkeypatch.setattr("trading.execution.scheduler.reconcile_all_once", fake_reconcile_all_once)

    sleep_calls = {"count": 0}

    async def fake_sleep(_seconds):
        sleep_calls["count"] += 1
        if sleep_calls["count"] >= 3:
            raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(reconcile_loop(interval_seconds=0))

    # The loop awaits sleep before each reconcile pass, so it reconciled
    # twice before the third sleep raised the cancellation.
    assert reconcile_calls["count"] == 2
