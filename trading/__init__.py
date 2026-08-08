"""Legend Trade — institutional-grade market analysis, strategy and backtesting.

Layered so each level depends only on the ones beneath it:

    providers/   real market data (Binance, Polygon, Twelve Data, Finnhub)
    market       caching + live stream fan-out
    indicators / structure / patterns / volume / smc / levels / regime / wyckoff
                 deterministic analysis computed from OHLCV
    probability / setups / risk
                 scenario odds, trade plans, sizing
    analysis     the ordered pipeline that produces the full report
    strategy/    natural-language strategies, backtest, Monte Carlo, audit
    pine/        Pine Script v6 generation
    narrative    optional LLM explanation layer over the deterministic output

Nothing in the analysis layers reaches for the network: they take a `Series`
and return findings, which is what makes them testable and what keeps the
backtester honest.
"""
from trading.models import (
    AssetClass,
    Candle,
    MarketDataError,
    Quote,
    Series,
    SymbolInfo,
    Timeframe,
)

__all__ = [
    "AssetClass",
    "Candle",
    "MarketDataError",
    "Quote",
    "Series",
    "SymbolInfo",
    "Timeframe",
]

__version__ = "1.0.0"
