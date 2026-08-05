"""The credential-name contract every broker adapter must honour.

`scripts/preflight.py` tells an operator which environment variables to set when
a broker is not configured. It gets those names from the adapter, because the
adapter is the only thing that knows them and every mode reads a different set —
any duplicate list elsewhere drifts the moment a broker or a mode is added.

That makes `credential_env_names()` a contract rather than a convenience, and
these tests are what stop a new adapter from silently returning nothing and
leaving the operator with an unactionable "not configured".
"""
import os

import pytest

from trading.execution.base import ExecutionMode
from trading.execution.engine import KNOWN_BROKERS, _default_adapter_factory

BROKER_MODES = [
    (broker, mode)
    for broker in KNOWN_BROKERS
    for mode in (ExecutionMode.BROKER_PAPER, ExecutionMode.LIVE)
]


@pytest.mark.parametrize("broker,mode", BROKER_MODES)
def test_every_adapter_names_the_variables_it_reads(broker, mode):
    adapter = _default_adapter_factory(broker, mode)
    names = adapter.credential_env_names()

    assert names, f"{broker}/{mode.value} reports no credential variables"
    assert all(isinstance(name, str) and name for name in names)


@pytest.mark.parametrize("broker,mode", BROKER_MODES)
def test_names_are_names_and_not_values(broker, mode, monkeypatch):
    """This output is printed. A leaked secret here goes into logs and terminals.

    Setting every variable to a recognisable secret and asserting it appears
    nowhere in the output catches the obvious slip of returning the resolved
    value rather than the name.
    """
    adapter = _default_adapter_factory(broker, mode)
    sentinel = "SUPER-SECRET-VALUE-DO-NOT-PRINT"
    for name in adapter.credential_env_names():
        monkeypatch.setenv(name, sentinel)

    # Rebuild: the adapters read the environment in __init__.
    reloaded = _default_adapter_factory(broker, mode)
    assert sentinel not in " ".join(reloaded.credential_env_names())


@pytest.mark.parametrize("broker,mode", BROKER_MODES)
def test_the_named_variables_are_the_ones_that_actually_configure_it(
    broker, mode, monkeypatch
):
    """The names must be true, not merely present.

    A plausible-looking but wrong name is worse than none: the operator sets it,
    nothing changes, and they have no way to tell the doc is lying. Setting
    exactly what the adapter names must be enough to make it report configured.

    IBKR is excluded and that exclusion is the point — it has no API key at all.
    Authentication lives in a separately-running, browser-authenticated Client
    Portal Gateway, so no environment variable can make it `configured`. See
    decision 30 in PROJECT_STATE.md.
    """
    if broker == "ibkr":
        pytest.skip("IBKR authenticates via a running gateway, not via env vars")

    adapter = _default_adapter_factory(broker, mode)
    assert not adapter.configured, "test environment already has credentials set"

    for name in adapter.credential_env_names():
        monkeypatch.setenv(name, "placeholder")

    assert _default_adapter_factory(broker, mode).configured


def test_paper_and_live_never_share_a_variable():
    """Sharing one pair makes it far too easy to point live keys at the sandbox —
    or worse, sandbox keys at live. Separate names are the safeguard."""
    for broker in KNOWN_BROKERS:
        paper = set(_default_adapter_factory(broker, ExecutionMode.BROKER_PAPER)
                    .credential_env_names())
        live = set(_default_adapter_factory(broker, ExecutionMode.LIVE)
                   .credential_env_names())
        shared = paper & live
        # IBKR legitimately shares the gateway URL: it is an address, not a
        # credential, and one gateway serves both account types.
        shared.discard("IBKR_GATEWAY_URL")
        assert not shared, f"{broker} shares {shared} between paper and live"


def test_no_broker_is_configured_in_the_test_environment():
    """A guard on the tests themselves.

    If real credentials leak into CI, the tests above start exercising a
    different path and the assertions above quietly stop meaning what they say.
    """
    leaked = [
        name
        for broker, mode in BROKER_MODES
        for name in _default_adapter_factory(broker, mode).credential_env_names()
        if os.environ.get(name)
    ]
    assert not leaked, f"broker credentials present in the environment: {leaked}"
