"""Provider registry.

`get_provider()` is the only thing the rest of the platform calls, so changing
data vendor is a config change (`MARKET_DATA_PROVIDER=polygon`) rather than a
code change. Providers are constructed lazily and cached per process.
"""
from __future__ import annotations

import logging
import os
from typing import Callable

from trading.models import AssetClass, MarketDataError
from trading.providers.base import MarketDataProvider
from trading.providers.alphavantage import AlphaVantageProvider
from trading.providers.binance import BinanceProvider
from trading.providers.coinbase import CoinbaseProvider
from trading.providers.finnhub import FinnhubProvider
from trading.providers.polygon import PolygonProvider
from trading.providers.twelvedata import TwelveDataProvider
from trading.providers.yahoo import YahooProvider

logger = logging.getLogger("legend.trading.providers")

__all__ = [
    "MarketDataProvider",
    "BinanceProvider",
    "CoinbaseProvider",
    "PolygonProvider",
    "TwelveDataProvider",
    "FinnhubProvider",
    "YahooProvider",
    "AlphaVantageProvider",
    "provider_chain_for",
    "get_provider",
    "provider_status",
    "resolve_provider_for",
    "available_providers",
    "healthy_crypto_provider",
]


def _setting(name: str, default: str = "") -> str:
    """Read config from the backend settings object, falling back to the environment.

    The backend loads `.env` through pydantic-settings, so preferring it means
    keys placed in `.env` work without also being exported into the shell.
    """
    try:
        from app.config import settings  # imported lazily: trading/ must work standalone

        value = getattr(settings, name.lower(), None)
        if value:
            return str(value)
    except Exception:  # noqa: BLE001 - backend config is optional for library use
        pass
    return os.environ.get(name.upper(), default)


_FACTORIES: dict[str, Callable[[], MarketDataProvider]] = {
    "binance": lambda: BinanceProvider(),
    "coinbase": lambda: CoinbaseProvider(),
    "polygon": lambda: PolygonProvider(_setting("POLYGON_API_KEY")),
    "twelvedata": lambda: TwelveDataProvider(_setting("TWELVEDATA_API_KEY")),
    "finnhub": lambda: FinnhubProvider(_setting("FINNHUB_API_KEY")),
    "yahoo": lambda: YahooProvider(),
    "alphavantage": lambda: AlphaVantageProvider(_setting("ALPHAVANTAGE_API_KEY")),
}

# Keyless crypto sources, in preference order. Binance has the deeper history
# and more pairs; Coinbase serves regions where Binance returns HTTP 451.
KEYLESS_CRYPTO = ("binance", "coinbase")

# Providers that need no credentials at all. Yahoo is what makes the entire
# listed universe — every US stock down to micro caps, plus indices, FX and
# futures — chartable on a fresh install with no signup.
KEYLESS = ("binance", "coinbase", "yahoo")

# Non-crypto providers in preference order. Keyed vendors come first because
# they have documented quotas and an SLA; Yahoo is the always-available floor.
NON_CRYPTO_PREFERENCE = ("polygon", "twelvedata", "finnhub", "alphavantage", "yahoo")

_instances: dict[str, MarketDataProvider] = {}
_health: dict[str, bool] = {}


def get_provider(name: str | None = None) -> MarketDataProvider:
    """Return a provider by name, or the configured default.

    The default comes from `MARKET_DATA_PROVIDER`; when that is unset we use
    the first reachable keyless crypto source, so a fresh install shows live
    charts with no credentials at all.
    """
    key = (name or _setting("MARKET_DATA_PROVIDER", "auto")).lower().strip()
    if key == "auto":
        key = healthy_crypto_provider().name
    if key not in _FACTORIES:
        raise MarketDataError(
            f"Unknown market data provider {key!r}. Available: {', '.join(sorted(_FACTORIES))}"
        )
    if key not in _instances:
        _instances[key] = _FACTORIES[key]()
    return _instances[key]


def healthy_crypto_provider() -> MarketDataProvider:
    """First keyless crypto provider that actually answers from this network.

    Binance returns HTTP 451 to a long list of jurisdictions, and a user in one
    of them should still get live charts rather than a stack trace. The probe
    result is cached per process so this costs one request, not one per call.
    """
    forced = _setting("MARKET_DATA_PROVIDER", "").lower().strip()
    if forced and forced != "auto" and forced in _FACTORIES:
        return get_provider(forced)

    for name in KEYLESS_CRYPTO:
        if _health.get(name) is False:
            continue
        provider = _instances.get(name) or _FACTORIES[name]()
        _instances[name] = provider
        if _health.get(name) is True:
            return provider
        try:
            provider.fetch_quote("BTCUSDT")
            _health[name] = True
            logger.info("using keyless crypto provider: %s", name)
            return provider
        except Exception as exc:  # noqa: BLE001 - probe failure just means try the next one
            _health[name] = False
            logger.warning("%s unreachable from this network (%s); trying next", name, exc)

    # Everything failed — return the preferred one so the caller sees its real error.
    _health.clear()
    return get_provider(KEYLESS_CRYPTO[0])


def resolve_provider_for(symbol: str, explicit: str | None = None) -> MarketDataProvider:
    """Pick the provider best suited to a symbol.

    An explicit name always wins. Otherwise the symbol's own shape decides:
    crypto pairs go to a streaming exchange, and everything else — stocks of any
    market cap, ETFs, indices, FX, futures, options — goes to the best
    configured equity provider, falling back to keyless Yahoo so that
    `NASDAQ:SERV` charts with no signup at all.
    """
    if explicit:
        return get_provider(explicit)

    from trading.markets import resolve_symbol

    try:
        resolved = resolve_symbol(symbol)
    except ValueError as exc:
        raise MarketDataError(str(exc)) from exc

    if resolved.asset_class is AssetClass.CRYPTO:
        return healthy_crypto_provider()

    if resolved.asset_class is AssetClass.OPTION:
        polygon = get_provider("polygon")
        if not polygon.configured:
            raise MarketDataError(
                "Option contracts need POLYGON_API_KEY — no keyless provider serves "
                "per-contract OPRA history."
            )
        return polygon

    # Prefer a keyed vendor when one is configured; otherwise Yahoo, which
    # covers the whole listed universe including small and micro caps.
    for candidate in NON_CRYPTO_PREFERENCE:
        provider = get_provider(candidate)
        if provider.configured and resolved.asset_class in provider.supported_assets:
            return provider

    return get_provider("yahoo")


def provider_chain_for(symbol: str, explicit: str | None = None) -> list[MarketDataProvider]:
    """Every provider that could serve this symbol, best first.

    Free market-data endpoints throttle and go down; a terminal that shows an
    error because its first choice returned 429 — while three other configured
    sources could have answered — is not production behaviour. Callers walk this
    chain and only surface a failure once every option has been tried.
    """
    if explicit:
        return [get_provider(explicit)]

    from trading.markets import resolve_symbol

    try:
        resolved = resolve_symbol(symbol)
    except ValueError as exc:
        raise MarketDataError(str(exc)) from exc

    chain: list[MarketDataProvider] = []

    if resolved.asset_class is AssetClass.CRYPTO:
        # Try the healthy exchange first, then the other keyless exchange, then
        # any keyed vendor that also carries crypto.
        chain.append(healthy_crypto_provider())
        for name in KEYLESS_CRYPTO + ("yahoo", "polygon", "twelvedata"):
            provider = get_provider(name)
            if provider.configured and AssetClass.CRYPTO in provider.supported_assets:
                chain.append(provider)
    elif resolved.asset_class is AssetClass.OPTION:
        chain.append(get_provider("polygon"))
    else:
        for name in NON_CRYPTO_PREFERENCE:
            provider = get_provider(name)
            if provider.configured and resolved.asset_class in provider.supported_assets:
                chain.append(provider)
        if not chain:
            chain.append(get_provider("yahoo"))

    # De-duplicate while preserving order.
    seen: set[str] = set()
    unique: list[MarketDataProvider] = []
    for provider in chain:
        if provider.name not in seen:
            seen.add(provider.name)
            unique.append(provider)
    return unique


def available_providers() -> list[str]:
    return sorted(_FACTORIES)


def provider_status() -> list[dict]:
    """Describe every provider and whether it currently has credentials.

    The settings UI renders this so a user can see at a glance which sources
    are live and which need a key.
    """
    status = []
    for name in sorted(_FACTORIES):
        try:
            provider = get_provider(name)
            status.append({
                "name": name,
                "configured": provider.configured,
                "streaming": provider.supports_streaming,
                "assets": [a.value for a in provider.supported_assets],
                "requires_key": name not in KEYLESS,
                "reachable": _health.get(name),  # None until probed
            })
        except Exception as exc:  # noqa: BLE001 - a broken provider shouldn't hide the others
            logger.warning("provider %s failed to initialise: %s", name, exc)
    return status
