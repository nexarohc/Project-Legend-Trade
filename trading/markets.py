"""The tradable universe: markets, exchanges, asset classes and symbol resolution.

Two jobs:

1. **Describe what the platform can chart** — every asset class and exchange,
   with real example symbols including small and micro caps, so a user can see
   coverage rather than guess at it.

2. **Resolve whatever the user types** into a ticker the chosen provider
   understands. Traders paste symbols in TradingView form (`NASDAQ:SERV`),
   broker form (`BRK.B`), or plain (`serve robotics`), and all three should
   land on the same instrument.

There is no hard-coded list of tradable stocks here on purpose. The US listed
universe is ~6,000 names and changes weekly with IPOs, delistings and ticker
changes; shipping a snapshot of it would be wrong within days. Symbol lookup
goes to the provider's live search endpoint instead, which is why a brand-new
listing is chartable the day it starts trading.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from trading.models import AssetClass


@dataclass(frozen=True)
class MarketInfo:
    key: str
    name: str
    asset_class: AssetClass
    exchanges: tuple[str, ...]
    examples: tuple[str, ...]
    providers: tuple[str, ...]
    notes: str

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "asset_class": self.asset_class.value,
            "exchanges": list(self.exchanges),
            "examples": list(self.examples),
            "providers": list(self.providers),
            "notes": self.notes,
        }


# Providers are listed best-first. "yahoo" needs no key and covers the whole
# listed universe, which is what makes small caps work out of the box.
MARKETS: tuple[MarketInfo, ...] = (
    MarketInfo(
        key="us_stocks",
        name="US Stocks — large, mid, small and micro cap",
        asset_class=AssetClass.STOCK,
        exchanges=("NASDAQ", "NYSE", "NYSE American (AMEX)", "OTC"),
        examples=(
            "AAPL", "MSFT", "NVDA", "TSLA",            # mega cap
            "RIVN", "PLTR", "SOFI",                    # mid cap
            "SERV", "IONQ", "RGTI", "LUNR", "ACHR",    # small / micro cap
            "BRK.B",                                   # class shares
        ),
        providers=("yahoo", "polygon", "finnhub", "twelvedata"),
        notes=(
            "Every US-listed ticker is chartable, including recent IPOs and micro caps. "
            "Type the plain ticker (SERV) or the TradingView form (NASDAQ:SERV) — both resolve. "
            "Class shares work in either notation (BRK.B or BRK-B)."
        ),
    ),
    MarketInfo(
        key="etfs",
        name="ETFs and funds",
        asset_class=AssetClass.ETF,
        exchanges=("NYSE Arca", "NASDAQ", "BATS"),
        examples=("SPY", "QQQ", "IWM", "ARKK", "XLE", "GLD", "TLT", "SOXL"),
        providers=("yahoo", "polygon", "twelvedata"),
        notes="Includes leveraged and sector ETFs. IWM and ARKK are the usual small-cap proxies.",
    ),
    MarketInfo(
        key="indices",
        name="Indices",
        asset_class=AssetClass.INDEX,
        exchanges=("NASDAQ", "NYSE", "CBOE", "global"),
        examples=("^IXIC", "^GSPC", "^DJI", "^RUT", "^VIX", "^FTSE", "^N225", "^GDAXI"),
        providers=("yahoo", "polygon", "twelvedata"),
        notes=(
            "Yahoo-style index tickers are prefixed with ^. ^IXIC is the Nasdaq Composite, "
            "^RUT the Russell 2000 (the small-cap benchmark), ^VIX the volatility index."
        ),
    ),
    MarketInfo(
        key="crypto",
        name="Cryptocurrency",
        asset_class=AssetClass.CRYPTO,
        exchanges=("Binance", "Coinbase", "and any pair those list"),
        examples=("BTCUSDT", "ETHUSDT", "SOLUSDT", "BTC-USD", "ETH-USD", "DOGE-USD"),
        providers=("binance", "coinbase", "yahoo", "polygon"),
        notes=(
            "The only market with true tick-by-tick streaming in this platform, via the "
            "exchange WebSocket feeds. Binance and Coinbase both work without an API key."
        ),
    ),
    MarketInfo(
        key="forex",
        name="Foreign exchange",
        asset_class=AssetClass.FOREX,
        exchanges=("OTC interbank",),
        examples=("EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "EURJPY"),
        providers=("yahoo", "twelvedata", "polygon", "finnhub"),
        notes=(
            "Spot FX carries no consolidated volume, so volume-profile and delta analysis "
            "are reported as unavailable rather than fabricated from a single venue's ticks."
        ),
    ),
    MarketInfo(
        key="commodities",
        name="Commodities",
        asset_class=AssetClass.COMMODITY,
        exchanges=("COMEX", "NYMEX", "CBOT", "ICE"),
        examples=("GC=F", "SI=F", "CL=F", "NG=F", "ZC=F", "XAUUSD"),
        providers=("yahoo", "twelvedata"),
        notes="Gold GC=F, silver SI=F, WTI crude CL=F, natural gas NG=F, corn ZC=F.",
    ),
    MarketInfo(
        key="futures",
        name="Futures",
        asset_class=AssetClass.FUTURE,
        exchanges=("CME", "CBOT", "NYMEX", "COMEX", "ICE"),
        examples=("ES=F", "NQ=F", "RTY=F", "YM=F", "CL=F", "GC=F", "ZB=F"),
        providers=("yahoo", "polygon"),
        notes=(
            "Continuous front-month contracts. ES=F is the S&P 500 e-mini, NQ=F the Nasdaq 100, "
            "RTY=F the Russell 2000 small-cap contract. Roll gaps are visible in the data and "
            "the gap-risk check will flag them."
        ),
    ),
    MarketInfo(
        key="options",
        name="Options",
        asset_class=AssetClass.OPTION,
        exchanges=("OPRA", "CBOE"),
        examples=("O:SPY241220C00500000",),
        providers=("polygon",),
        notes=(
            "Option contract charting requires a Polygon key — no keyless source serves "
            "per-contract OPRA history. The analysis engines run on the contract's own OHLCV; "
            "they do not model greeks, implied volatility or assignment risk."
        ),
    ),
    MarketInfo(
        key="international",
        name="International equities",
        asset_class=AssetClass.STOCK,
        exchanges=("LSE", "TSX", "XETRA", "Euronext", "TSE", "HKEX", "ASX", "NSE"),
        examples=("SHOP.TO", "BARC.L", "SAP.DE", "7203.T", "0700.HK", "BHP.AX", "RELIANCE.NS"),
        providers=("yahoo", "twelvedata"),
        notes=(
            "Non-US listings use Yahoo's exchange suffix: .L London, .TO Toronto, .DE XETRA, "
            ".T Tokyo, .HK Hong Kong, .AX Australia, .NS India."
        ),
    ),
)


# TradingView-style exchange prefixes users routinely paste in.
EXCHANGE_PREFIXES = {
    "NASDAQ", "NYSE", "AMEX", "ARCA", "BATS", "OTC", "CBOE", "CME", "COMEX",
    "NYMEX", "CBOT", "ICE", "LSE", "TSX", "TSXV", "XETR", "FWB", "EURONEXT",
    "TSE", "HKEX", "ASX", "NSE", "BSE", "BINANCE", "COINBASE", "BYBIT",
    "KRAKEN", "BITSTAMP", "OANDA", "FX", "FOREXCOM", "CAPITALCOM", "SP", "DJ",
}

# Suffixes that already identify a Yahoo-shaped instrument.
_YAHOO_SUFFIXES = ("=X", "=F")


@dataclass(frozen=True)
class ResolvedSymbol:
    raw: str
    ticker: str
    exchange: str | None
    asset_class: AssetClass
    suggested_provider: str
    note: str

    def to_dict(self) -> dict:
        return {
            "raw": self.raw,
            "ticker": self.ticker,
            "exchange": self.exchange,
            "asset_class": self.asset_class.value,
            "suggested_provider": self.suggested_provider,
            "note": self.note,
        }


def resolve_symbol(raw: str) -> ResolvedSymbol:
    """Turn user input into a ticker plus the provider best suited to serve it.

    Handles the exchange prefix, whitespace, and the crypto/FX/index/futures
    shapes. Company-name lookups ("serve robotics") are not resolved here —
    they go through `/market/search`, which queries the provider's live index.
    """
    text = (raw or "").strip()
    if not text:
        raise ValueError("Symbol cannot be empty.")

    exchange: str | None = None
    if ":" in text:
        head, _, tail = text.partition(":")
        head = head.strip().upper()
        if head in EXCHANGE_PREFIXES:
            exchange = head
            text = tail.strip()

    ticker = text.upper().replace(" ", "")

    # Crypto, by exchange prefix or stablecoin quote.
    if exchange in {"BINANCE", "COINBASE", "BYBIT", "KRAKEN", "BITSTAMP"} or \
            re.search(r"(USDT|USDC|BUSD)$", ticker):
        return ResolvedSymbol(
            raw=raw, ticker=ticker, exchange=exchange, asset_class=AssetClass.CRYPTO,
            suggested_provider="binance",
            note="Crypto pair — streams tick-by-tick from the exchange WebSocket.",
        )

    if ticker.startswith("^"):
        return ResolvedSymbol(
            raw=raw, ticker=ticker, exchange=exchange, asset_class=AssetClass.INDEX,
            suggested_provider="yahoo", note="Index.",
        )

    if ticker.endswith("=F"):
        return ResolvedSymbol(
            raw=raw, ticker=ticker, exchange=exchange, asset_class=AssetClass.FUTURE,
            suggested_provider="yahoo", note="Continuous front-month futures contract.",
        )

    if ticker.endswith("=X") or (len(ticker) == 6 and ticker.isalpha() and ticker[3:] in {
        "USD", "EUR", "JPY", "GBP", "CHF", "CAD", "AUD", "NZD"
    }):
        return ResolvedSymbol(
            raw=raw, ticker=ticker if ticker.endswith("=X") else f"{ticker}=X",
            exchange=exchange, asset_class=AssetClass.FOREX,
            suggested_provider="yahoo",
            note="FX pair — spot FX has no consolidated volume, so volume analysis is skipped.",
        )

    if ticker.startswith("O:"):
        return ResolvedSymbol(
            raw=raw, ticker=ticker, exchange=exchange, asset_class=AssetClass.OPTION,
            suggested_provider="polygon",
            note="Option contract — requires POLYGON_API_KEY.",
        )

    # Anything else is treated as an equity or ETF listing.
    normalized = ticker.replace(".", "-") if "." in ticker and not ticker.endswith(
        (".L", ".TO", ".DE", ".T", ".HK", ".AX", ".NS", ".PA", ".MI", ".SW")
    ) else ticker

    return ResolvedSymbol(
        raw=raw,
        ticker=normalized,
        exchange=exchange,
        asset_class=AssetClass.STOCK,
        suggested_provider="yahoo",
        note=(
            f"Listed equity or ETF{f' on {exchange}' if exchange else ''}. "
            f"Served keyless by Yahoo Finance; set POLYGON_API_KEY or TWELVEDATA_API_KEY "
            f"for a rate-limited production feed."
        ),
    )


def market_catalog() -> dict:
    """Everything the platform can chart, for the docs and the /market/markets endpoint."""
    return {
        "markets": [m.to_dict() for m in MARKETS],
        "asset_classes": [a.value for a in AssetClass],
        "symbol_formats": {
            "plain_ticker": "SERV — a US listing, any market cap",
            "exchange_prefixed": "NASDAQ:SERV — TradingView style, the prefix is stripped",
            "class_shares": "BRK.B or BRK-B — both resolve",
            "index": "^IXIC, ^RUT — caret prefix",
            "futures": "ES=F, CL=F — =F suffix",
            "forex": "EURUSD or EURUSD=X",
            "crypto": "BTCUSDT (exchange pair) or BTC-USD",
            "international": "SHOP.TO, BARC.L, 7203.T — Yahoo exchange suffix",
            "option": "O:SPY241220C00500000 — requires a Polygon key",
        },
        "notes": [
            "There is no fixed list of supported tickers. Symbol search queries the provider's "
            "live index, so newly listed companies are chartable on their first trading day.",
            "Small and micro caps are fully supported — they are ordinary listings to every "
            "provider here. Expect wider spreads and thinner volume, which the liquidity and "
            "gap-risk checks will flag in the analysis.",
            "Only crypto streams tick-by-tick. Equities, FX and futures are polled on a timer "
            "because no keyless provider offers a public socket for them.",
        ],
    }
