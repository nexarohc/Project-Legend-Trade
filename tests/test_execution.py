"""Execution guardrails, engine and API.

These are the highest-consequence tests in the suite: everything here exists to
prove that real money cannot be reached by accident, and that an order which
should be refused *is* refused. A regression here is not a broken feature, it
is a funded mistake.

No test ever contacts a broker. The adapter is a fake that records what it was
asked to do, so "the guardrail blocked it" is proven by the fake having
received nothing.
"""
import datetime as dt

import pytest
from fastapi.testclient import TestClient

from tests.test_trading_api import StubProvider
from trading.execution import guardrails
from trading.execution.base import (
    BrokerAccount,
    BrokerAdapter,
    BrokerPosition,
    ExecutionMode,
    Order,
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
)
from trading.execution.guardrails import ExecutionContext, RiskLimits


class FakeBroker(BrokerAdapter):
    """Records every call. Never touches the network."""

    name = "fake"

    def __init__(self, mode=ExecutionMode.BROKER_PAPER, equity=100_000.0, positions=None):
        super().__init__(mode)
        self.equity = equity
        self._positions = positions or []
        self.submitted: list[OrderRequest] = []
        self.cancelled_all = 0
        self.closed_all = 0

    @property
    def configured(self) -> bool:
        return True

    def get_account(self) -> BrokerAccount:
        return BrokerAccount(
            account_id="FAKE", equity=self.equity, cash=self.equity,
            buying_power=self.equity * 2, is_paper=self.mode is not ExecutionMode.LIVE,
        )

    def get_positions(self):
        return list(self._positions)

    def get_orders(self, open_only: bool = True):
        return []

    def submit_order(self, request: OrderRequest) -> Order:
        self.submitted.append(request)
        return Order(
            broker_order_id=f"fake-{len(self.submitted)}",
            client_order_id=request.client_order_id,
            symbol=request.symbol,
            side=request.side,
            quantity=request.quantity,
            filled_quantity=0.0,
            status=OrderStatus.ACCEPTED,
            order_type=request.order_type,
            stop_price=request.stop_price,
        )

    def cancel_order(self, broker_order_id: str) -> bool:
        return True

    def cancel_all_orders(self) -> int:
        self.cancelled_all += 1
        return 3

    def close_position(self, symbol: str):
        return None

    def close_all_positions(self):
        self.closed_all += 1
        return []


def _context(**overrides) -> ExecutionContext:
    base = dict(
        mode=ExecutionMode.BROKER_PAPER,
        user_id=1,
        current_price=100.0,
        account_equity=100_000.0,
        open_position_count=0,
        existing_exposure=0.0,
        limits=RiskLimits(),
    )
    base.update(overrides)
    return ExecutionContext(**base)


def _order(**overrides) -> OrderRequest:
    base = dict(
        symbol="AAPL",
        side=OrderSide.BUY,
        quantity=10,
        order_type=OrderType.MARKET,
        stop_price=95.0,
    )
    base.update(overrides)
    return OrderRequest(**base)


# --- the mandatory-stop rule -------------------------------------------------

def test_entry_without_a_stop_is_refused():
    """The single most important guardrail."""
    decision = guardrails.evaluate(_order(stop_price=None), _context())
    assert decision.allowed is False
    assert any(r.rule == "mandatory_stop" for r in decision.refusals)


def test_stop_on_the_wrong_side_is_refused():
    # A buy with a stop above price would trigger immediately.
    decision = guardrails.evaluate(_order(side=OrderSide.BUY, stop_price=105.0), _context())
    assert decision.allowed is False
    assert any(r.rule == "stop_placement" for r in decision.refusals)

    # And the mirror case for a sell.
    decision = guardrails.evaluate(_order(side=OrderSide.SELL, stop_price=95.0), _context())
    assert decision.allowed is False


def test_absurdly_tight_stop_is_refused():
    decision = guardrails.evaluate(_order(stop_price=99.999), _context())
    assert decision.allowed is False
    assert any("noise" in r.reason for r in decision.refusals)


def test_absurdly_wide_stop_is_refused():
    decision = guardrails.evaluate(_order(stop_price=1.0), _context())
    assert decision.allowed is False
    assert any(r.rule == "stop_placement" for r in decision.refusals)


def test_a_well_formed_order_is_allowed():
    decision = guardrails.evaluate(_order(), _context())
    assert decision.allowed is True, decision.blocking_reasons


# --- live-mode gating ---------------------------------------------------------

def test_live_mode_refused_without_the_instance_flag(monkeypatch):
    monkeypatch.delenv(guardrails.LIVE_ENABLED_ENV, raising=False)
    decision = guardrails.evaluate(
        _order(), _context(mode=ExecutionMode.LIVE, live_confirmed=True)
    )
    assert decision.allowed is False
    assert any(r.rule == "live_mode_gating" for r in decision.refusals)


def test_live_mode_refused_without_typed_confirmation(monkeypatch):
    monkeypatch.setenv(guardrails.LIVE_ENABLED_ENV, "true")
    decision = guardrails.evaluate(
        _order(), _context(mode=ExecutionMode.LIVE, live_confirmed=False)
    )
    assert decision.allowed is False
    assert any(r.rule == "live_confirmation" for r in decision.refusals)


def test_live_mode_allowed_only_with_both(monkeypatch):
    monkeypatch.setenv(guardrails.LIVE_ENABLED_ENV, "true")
    decision = guardrails.evaluate(
        _order(), _context(mode=ExecutionMode.LIVE, live_confirmed=True)
    )
    assert decision.allowed is True, decision.blocking_reasons
    # Even when allowed, live mode must be flagged to the caller.
    assert any(w.rule == "live_mode" for w in decision.warnings)


def test_instance_flag_only_reads_explicit_truthy_values(monkeypatch):
    for value in ("", "false", "0", "no", "maybe", "TRUE-ish"):
        monkeypatch.setenv(guardrails.LIVE_ENABLED_ENV, value)
        assert guardrails.live_trading_enabled_on_instance() is False, value
    for value in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv(guardrails.LIVE_ENABLED_ENV, value)
        assert guardrails.live_trading_enabled_on_instance() is True, value


# --- kill switch ---------------------------------------------------------------

def test_kill_switch_blocks_everything():
    decision = guardrails.evaluate(_order(), _context(kill_switch_active=True))
    assert decision.allowed is False
    assert decision.refusals[0].rule == "kill_switch"


def test_kill_switch_short_circuits_other_checks():
    """Once the kill switch is on, nothing else needs evaluating."""
    decision = guardrails.evaluate(
        _order(stop_price=None, quantity=999999), _context(kill_switch_active=True)
    )
    assert decision.allowed is False
    assert len(decision.refusals) == 1


# --- strategy provenance --------------------------------------------------------

def test_order_from_an_unaudited_strategy_is_refused():
    decision = guardrails.evaluate(
        _order(strategy_id=5), _context(strategy_audit_verdict=None)
    )
    assert decision.allowed is False
    assert any(r.rule == "strategy_audit" for r in decision.refusals)


@pytest.mark.parametrize("verdict", ["REJECT", "REVISE"])
def test_order_from_a_non_approved_strategy_is_refused(verdict):
    decision = guardrails.evaluate(
        _order(strategy_id=5), _context(strategy_audit_verdict=verdict)
    )
    assert decision.allowed is False
    assert any(verdict in r.reason for r in decision.refusals)


def test_approved_strategy_may_trade_in_paper():
    decision = guardrails.evaluate(
        _order(strategy_id=5), _context(strategy_audit_verdict="APPROVED")
    )
    assert decision.allowed is True, decision.blocking_reasons


def test_live_requires_a_paper_trading_record(monkeypatch):
    monkeypatch.setenv(guardrails.LIVE_ENABLED_ENV, "true")
    decision = guardrails.evaluate(
        _order(strategy_id=5),
        _context(
            mode=ExecutionMode.LIVE, live_confirmed=True,
            strategy_audit_verdict="APPROVED", strategy_paper_trade_count=3,
        ),
    )
    assert decision.allowed is False
    assert any(r.rule == "paper_trading_record" for r in decision.refusals)


def test_live_allowed_with_a_sufficient_paper_record(monkeypatch):
    monkeypatch.setenv(guardrails.LIVE_ENABLED_ENV, "true")
    decision = guardrails.evaluate(
        _order(strategy_id=5),
        _context(
            mode=ExecutionMode.LIVE, live_confirmed=True,
            strategy_audit_verdict="APPROVED", strategy_paper_trade_count=25,
        ),
    )
    assert decision.allowed is True, decision.blocking_reasons


# --- sizing and exposure ---------------------------------------------------------

def test_oversized_position_is_refused():
    # 10% cap on 100k equity = 10k; 200 shares at 100 = 20k.
    decision = guardrails.evaluate(_order(quantity=200), _context())
    assert decision.allowed is False
    assert any(r.rule == "position_size" for r in decision.refusals)


def test_per_order_notional_cap_is_enforced():
    limits = RiskLimits(max_order_notional=1_000.0, max_position_percent=100.0)
    decision = guardrails.evaluate(_order(quantity=50), _context(limits=limits))
    assert decision.allowed is False
    assert any("notional" in r.reason for r in decision.refusals)


def test_total_exposure_cap_is_enforced():
    decision = guardrails.evaluate(
        _order(quantity=50), _context(existing_exposure=29_000.0)
    )
    assert decision.allowed is False
    assert any(r.rule == "total_exposure" for r in decision.refusals)


def test_too_many_open_positions_is_refused():
    decision = guardrails.evaluate(_order(), _context(open_position_count=10))
    assert decision.allowed is False
    assert any(r.rule == "open_positions" for r in decision.refusals)


def test_order_rate_limit_is_enforced():
    decision = guardrails.evaluate(_order(), _context(orders_last_hour=20))
    assert decision.allowed is False
    assert any(r.rule == "order_rate" for r in decision.refusals)


def test_unknown_equity_fails_closed():
    """A size check that cannot be evaluated must refuse, not assume."""
    decision = guardrails.evaluate(
        _order(), _context(mode=ExecutionMode.BROKER_PAPER, account_equity=None)
    )
    assert decision.allowed is False
    assert any("fail-closed" in r.detail for r in decision.refusals)


def test_invalid_quantity_is_refused():
    decision = guardrails.evaluate(_order(quantity=0), _context())
    assert decision.allowed is False


# --- per-user risk limit overrides -------------------------------------------

def test_validate_limit_overrides_accepts_the_platform_defaults():
    assert guardrails.validate_limit_overrides(RiskLimits().to_dict()) is None


def test_validate_limit_overrides_accepts_tightening():
    tightened = RiskLimits(max_position_percent=5.0, max_open_positions=3).to_dict()
    assert guardrails.validate_limit_overrides(tightened) is None


def test_validate_limit_overrides_rejects_loosening_a_max_field():
    loosened = RiskLimits(max_position_percent=50.0).to_dict()
    error = guardrails.validate_limit_overrides(loosened)
    assert error is not None
    assert "max_position_percent" in error


def test_validate_limit_overrides_rejects_loosening_a_min_field():
    loosened = RiskLimits(min_stop_distance_percent=0.01).to_dict()
    error = guardrails.validate_limit_overrides(loosened)
    assert error is not None
    assert "min_stop_distance_percent" in error


def test_validate_limit_overrides_rejects_non_positive_values():
    error = guardrails.validate_limit_overrides(RiskLimits(max_open_positions=0).to_dict())
    assert error is not None


def test_validate_limit_overrides_rejects_inverted_stop_bounds():
    bad = RiskLimits(min_stop_distance_percent=20.0, max_stop_distance_percent=10.0).to_dict()
    error = guardrails.validate_limit_overrides(bad)
    assert error is not None
    assert "min_stop_distance_percent" in error


# --- the engine -------------------------------------------------------------------

@pytest.fixture()
def engine(session):
    from trading.execution import ExecutionEngine

    broker = FakeBroker()
    eng = ExecutionEngine(session, user_id=1, adapter_factory=lambda b, m: broker)
    eng.fake_broker = broker
    return eng


def test_engine_defaults_to_internal_paper(engine):
    assert engine.state().mode == ExecutionMode.PAPER.value


def test_refused_order_never_reaches_the_broker(engine, monkeypatch):
    monkeypatch.setattr(engine, "_build_context", lambda r, s, m: _context(
        mode=ExecutionMode.BROKER_PAPER, kill_switch_active=True))
    engine.set_mode("broker_paper")

    result = engine.submit(_order())

    assert result["submitted"] is False
    assert engine.fake_broker.submitted == [], "a refused order was transmitted"


def test_refused_orders_are_still_recorded(engine, monkeypatch):
    monkeypatch.setattr(engine, "_build_context", lambda r, s, m: _context(
        mode=ExecutionMode.BROKER_PAPER, kill_switch_active=True))
    engine.set_mode("broker_paper")
    engine.submit(_order())

    orders = engine.orders()
    assert len(orders) == 1
    assert orders[0]["allowed"] is False
    assert orders[0]["status"] == "rejected_by_guardrail"
    assert orders[0]["reason"]


def test_allowed_order_reaches_the_broker(engine, monkeypatch):
    monkeypatch.setattr(engine, "_build_context", lambda r, s, m: _context(
        mode=ExecutionMode.BROKER_PAPER))
    engine.set_mode("broker_paper")

    result = engine.submit(_order())

    assert result["submitted"] is True
    assert len(engine.fake_broker.submitted) == 1
    assert engine.fake_broker.submitted[0].stop_price == 95.0


def test_kill_switch_flattens_and_blocks(engine):
    engine.set_mode("broker_paper")
    result = engine.engage_kill_switch("panic")

    assert result["kill_switch"] == "engaged"
    assert engine.fake_broker.cancelled_all == 1
    assert engine.fake_broker.closed_all == 1
    assert engine.state().kill_switch_active is True

    # And no new order gets through afterwards.
    followup = engine.submit(_order())
    assert followup["submitted"] is False
    assert engine.fake_broker.submitted == []


def test_kill_switch_disarms_live_confirmation(engine, monkeypatch):
    monkeypatch.setenv(guardrails.LIVE_ENABLED_ENV, "true")
    engine.confirm_live(guardrails.LIVE_CONFIRMATION_PHRASE)
    assert engine.state().live_confirmed_until is not None

    engine.engage_kill_switch("panic")
    assert engine.state().live_confirmed_until is None


def test_kill_switch_survives_a_new_engine_instance(session, engine):
    """A kill switch that forgets on restart is worse than none."""
    from trading.execution import ExecutionEngine

    engine.engage_kill_switch("panic")
    fresh = ExecutionEngine(session, user_id=1, adapter_factory=lambda b, m: FakeBroker())
    assert fresh.state().kill_switch_active is True


def test_disengaging_the_kill_switch_restores_trading(engine):
    engine.engage_kill_switch("panic")
    engine.disengage_kill_switch()
    assert engine.state().kill_switch_active is False


def test_live_confirmation_requires_the_exact_phrase(engine, monkeypatch):
    monkeypatch.setenv(guardrails.LIVE_ENABLED_ENV, "true")

    for wrong in ["trade live money", "TRADE LIVE MONEY ", "yes", ""]:
        assert engine.confirm_live(wrong)["confirmed"] is False

    assert engine.confirm_live(guardrails.LIVE_CONFIRMATION_PHRASE)["confirmed"] is True


def test_live_confirmation_refused_without_the_instance_flag(engine, monkeypatch):
    monkeypatch.delenv(guardrails.LIVE_ENABLED_ENV, raising=False)
    result = engine.confirm_live(guardrails.LIVE_CONFIRMATION_PHRASE)
    assert result["confirmed"] is False


def test_live_confirmation_expires(engine, monkeypatch):
    monkeypatch.setenv(guardrails.LIVE_ENABLED_ENV, "true")
    engine.confirm_live(guardrails.LIVE_CONFIRMATION_PHRASE, minutes=30)

    state = engine.state()
    assert engine._live_confirmed(state) is True

    # Wind the expiry into the past.
    state.live_confirmed_until = dt.datetime.utcnow() - dt.timedelta(minutes=1)
    assert engine._live_confirmed(state) is False


def test_cannot_switch_to_live_without_the_instance_flag(engine, monkeypatch):
    monkeypatch.delenv(guardrails.LIVE_ENABLED_ENV, raising=False)
    result = engine.set_mode("live")
    assert "error" in result
    assert engine.state().mode != "live"


def test_unknown_mode_is_rejected(engine):
    assert "error" in engine.set_mode("yolo")


def test_engine_defaults_to_alpaca(engine):
    assert engine.state().broker == "alpaca"


def test_set_broker_switches_which_adapter_is_used(engine):
    result = engine.set_broker("tradier")
    assert result == {"broker": "tradier"}
    assert engine.state().broker == "tradier"


def test_set_broker_rejects_an_unknown_broker(engine):
    result = engine.set_broker("robinhood")
    assert "error" in result
    assert engine.state().broker == "alpaca"  # unchanged


def test_set_broker_clears_an_active_live_confirmation(engine, monkeypatch):
    monkeypatch.setenv(guardrails.LIVE_ENABLED_ENV, "true")
    engine.confirm_live(guardrails.LIVE_CONFIRMATION_PHRASE)
    assert engine._live_confirmed(engine.state()) is True

    engine.set_broker("tradier")

    assert engine._live_confirmed(engine.state()) is False


# --- per-user risk limit overrides (engine) -----------------------------------

def test_new_engine_has_platform_default_limits(engine):
    assert engine.limits().to_dict() == RiskLimits().to_dict()


def test_set_limits_tightens_and_persists(engine):
    result = engine.set_limits({"max_position_percent": 5.0})
    assert result["max_position_percent"] == 5.0
    assert engine.limits().max_position_percent == 5.0
    # Untouched fields stay at the platform default.
    assert engine.limits().max_open_positions == RiskLimits().max_open_positions


def test_set_limits_rejects_loosening_beyond_the_platform_default(engine):
    result = engine.set_limits({"max_position_percent": 99.0})
    assert "error" in result
    # Nothing was written.
    assert engine.limits().max_position_percent == RiskLimits().max_position_percent


def test_set_limits_rejects_an_unknown_field(engine):
    result = engine.set_limits({"max_position_percent_typo": 1.0})
    assert "error" in result


def test_set_limits_merges_with_previously_set_overrides(engine):
    engine.set_limits({"max_position_percent": 5.0})
    engine.set_limits({"max_open_positions": 2})

    limits = engine.limits()
    assert limits.max_position_percent == 5.0
    assert limits.max_open_positions == 2


def test_set_limits_survives_a_new_engine_instance(session, engine):
    from trading.execution import ExecutionEngine

    engine.set_limits({"max_position_percent": 5.0})
    fresh = ExecutionEngine(session, user_id=1, adapter_factory=lambda b, m: FakeBroker())
    assert fresh.limits().max_position_percent == 5.0


def test_reset_limits_reverts_to_platform_defaults(engine):
    engine.set_limits({"max_position_percent": 5.0, "max_open_positions": 2})
    result = engine.reset_limits()
    assert result == RiskLimits().to_dict()
    assert engine.limits().to_dict() == RiskLimits().to_dict()


def test_tightened_limits_are_actually_enforced_by_the_guardrails(engine, monkeypatch):
    """Not just stored — a tightened per-user limit must change what the
    guardrails allow, since a limit nobody enforces is decoration."""
    monkeypatch.setattr(engine, "_build_context", lambda r, s, m: _context(
        mode=ExecutionMode.BROKER_PAPER, account_equity=100_000.0,
        limits=engine.limits(),
    ))
    engine.set_mode("broker_paper")
    engine.set_limits({"max_position_percent": 1.0})  # 1% of 100k = $1,000

    # 50 shares at the context's $100 price = $5,000 notional, 5% of equity —
    # above the tightened 1% cap.
    result = engine.submit(_order(quantity=50))
    assert result["submitted"] is False
    assert any(r["rule"] == "position_size" for r in result["guardrails"]["refusals"])


# --- API ---------------------------------------------------------------------------

@pytest.fixture()
def client(monkeypatch, session):
    stub = StubProvider()
    import trading.market as market_module
    import trading.providers as providers_module

    monkeypatch.setattr(providers_module, "get_provider", lambda name=None: stub)
    monkeypatch.setattr(providers_module, "resolve_provider_for", lambda s, explicit=None: stub)
    monkeypatch.setattr(market_module, "get_provider", lambda name=None: stub)
    monkeypatch.setattr(market_module, "resolve_provider_for", lambda s, explicit=None: stub)
    market_module.market_service.invalidate()

    from app.ratelimit import compute_limiter

    compute_limiter.reset()

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
    market_module.market_service.invalidate()


def test_status_endpoint_reports_paper_by_default(client):
    payload = client.get("/execution/status").json()
    assert payload["mode"] == "paper"
    assert payload["kill_switch_active"] is False
    assert payload["live_enabled_on_instance"] in (True, False)


def test_rules_endpoint_lists_the_blocking_rules(client):
    payload = client.get("/execution/rules").json()
    assert any("protective stop" in rule for rule in payload["always_blocking"])
    assert "limits" in payload


def test_brokers_endpoint_lists_known_brokers(client):
    payload = client.get("/execution/brokers").json()
    assert set(payload["brokers"]) == {"alpaca", "tradier", "ibkr"}


def test_set_broker_endpoint_switches_the_broker(client):
    response = client.post("/execution/broker", json={"broker": "tradier"})
    assert response.status_code == 200
    assert response.json()["broker"] == "tradier"
    assert client.get("/execution/status").json()["broker"] == "tradier"


def test_set_broker_endpoint_rejects_an_unknown_broker(client):
    response = client.post("/execution/broker", json={"broker": "robinhood"})
    assert response.status_code == 422  # fails the pattern validator


def test_api_refuses_an_order_without_a_stop(client):
    response = client.post("/execution/orders", json={
        "symbol": "AAPL", "side": "buy", "quantity": 1,
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload["submitted"] is False
    assert any(r["rule"] == "mandatory_stop" for r in payload["guardrails"]["refusals"])


def test_api_cannot_switch_to_live_without_the_flag(client, monkeypatch):
    monkeypatch.delenv(guardrails.LIVE_ENABLED_ENV, raising=False)
    response = client.post("/execution/mode", json={"mode": "live"})
    assert response.status_code == 400


def test_api_kill_switch_round_trip(client):
    engaged = client.post("/execution/kill-switch", json={"reason": "test"}).json()
    assert engaged["kill_switch"] == "engaged"
    assert client.get("/execution/status").json()["kill_switch_active"] is True

    client.delete("/execution/kill-switch")
    assert client.get("/execution/status").json()["kill_switch_active"] is False


def test_api_rejects_a_bad_confirmation_phrase(client):
    response = client.post("/execution/confirm-live", json={"phrase": "please"})
    assert response.status_code == 400


def test_order_history_is_returned(client):
    client.post("/execution/orders", json={"symbol": "AAPL", "side": "buy", "quantity": 1})
    orders = client.get("/execution/orders").json()["orders"]
    assert len(orders) >= 1
    assert orders[0]["symbol"] == "AAPL"


# --- per-user risk limit overrides (API) --------------------------------------

def test_get_limits_returns_platform_defaults_initially(client):
    payload = client.get("/execution/limits").json()
    assert payload["effective"] == RiskLimits().to_dict()
    assert payload["platform_defaults"] == RiskLimits().to_dict()


def test_post_limits_tightens_a_field(client):
    response = client.post("/execution/limits", json={"max_position_percent": 4.0})
    assert response.status_code == 200
    assert response.json()["max_position_percent"] == 4.0
    assert client.get("/execution/limits").json()["effective"]["max_position_percent"] == 4.0


def test_post_limits_rejects_loosening_beyond_the_default(client):
    response = client.post("/execution/limits", json={"max_position_percent": 99.0})
    assert response.status_code == 400
    assert "platform default" in response.json()["detail"]


def test_post_limits_rejects_an_empty_body(client):
    response = client.post("/execution/limits", json={})
    assert response.status_code == 400


def test_post_limits_rejects_a_non_positive_value(client):
    response = client.post("/execution/limits", json={"max_open_positions": 0})
    assert response.status_code == 422  # fails the Pydantic ge=1 constraint


def test_delete_limits_resets_to_defaults(client):
    client.post("/execution/limits", json={"max_position_percent": 4.0})
    response = client.delete("/execution/limits")
    assert response.status_code == 200
    assert response.json() == RiskLimits().to_dict()
    assert client.get("/execution/limits").json()["effective"] == RiskLimits().to_dict()


# --- broker-connected modes are admin-only ------------------------------------
#
# Broker credentials are shared across the whole instance (see the module
# docstrings in trading/execution/alpaca.py and backend/app/routers/execution.py),
# so once real strangers can register, only the instance admin — the first
# account — may reach a mode that touches the shared broker connection.

from tests.test_auth import STRONG_PASSWORD, auth_client  # noqa: F401,E402 - shared fixture


def _register_for_execution(client, email: str) -> dict:
    response = client.post("/auth/register", json={"email": email, "password": STRONG_PASSWORD})
    assert response.status_code == 201, response.text
    return response.json()


def _auth_headers(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def test_first_account_is_admin_and_can_use_broker_connected_modes(auth_client):
    tokens = _register_for_execution(auth_client, "first@example.com")
    headers = _auth_headers(tokens)

    status = auth_client.get("/execution/status", headers=headers).json()
    assert status["user_is_admin"] is True

    response = auth_client.post("/execution/mode", json={"mode": "broker_paper"}, headers=headers)
    assert response.status_code == 200


def test_second_account_is_not_admin_and_is_refused_broker_paper_mode(auth_client):
    _register_for_execution(auth_client, "admin@example.com")
    tokens = _register_for_execution(auth_client, "member@example.com")
    headers = _auth_headers(tokens)

    status = auth_client.get("/execution/status", headers=headers).json()
    assert status["user_is_admin"] is False

    response = auth_client.post("/execution/mode", json={"mode": "broker_paper"}, headers=headers)
    assert response.status_code == 403
    assert "administrator" in response.json()["detail"]


def test_non_admin_can_still_use_internal_paper_mode(auth_client):
    _register_for_execution(auth_client, "admin2@example.com")
    tokens = _register_for_execution(auth_client, "member2@example.com")
    headers = _auth_headers(tokens)

    response = auth_client.post("/execution/mode", json={"mode": "paper"}, headers=headers)
    assert response.status_code == 200


def test_non_admin_cannot_switch_the_shared_broker(auth_client):
    _register_for_execution(auth_client, "admin3@example.com")
    tokens = _register_for_execution(auth_client, "member3@example.com")
    headers = _auth_headers(tokens)

    response = auth_client.post("/execution/broker", json={"broker": "tradier"}, headers=headers)
    assert response.status_code == 403


def test_non_admin_cannot_confirm_live(auth_client, monkeypatch):
    monkeypatch.setenv(guardrails.LIVE_ENABLED_ENV, "true")
    _register_for_execution(auth_client, "admin4@example.com")
    tokens = _register_for_execution(auth_client, "member4@example.com")
    headers = _auth_headers(tokens)

    response = auth_client.post(
        "/execution/confirm-live",
        json={"phrase": guardrails.LIVE_CONFIRMATION_PHRASE},
        headers=headers,
    )
    assert response.status_code == 403
