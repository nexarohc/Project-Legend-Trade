"""Market data API: history, quotes, symbol search, and the live WebSocket stream.

The WebSocket endpoint is what makes the terminal live. A client connects,
sends a subscribe frame, and receives the seeded history followed by every
candle update as it happens. Multiple clients watching the same symbol share
one upstream connection via `StreamHub`.
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect

from app.dependencies import websocket_user
from database.db import SessionLocal
from trading.market import market_service, stream_hub
from trading.markets import market_catalog, resolve_symbol
from trading.models import MarketDataError, Timeframe
from trading.providers import available_providers, get_provider, provider_status

logger = logging.getLogger("legend.market")
router = APIRouter(prefix="/market", tags=["market"])


def _timeframe(raw: str) -> Timeframe:
    try:
        return Timeframe.parse(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/providers")
def providers():
    """Which data sources exist and which have credentials configured."""
    return {
        "providers": provider_status(),
        "available": available_providers(),
        "note": (
            "Binance and Coinbase need no API key. If Binance is blocked in your region "
            "(HTTP 451) the platform falls back to Coinbase automatically. Equities and FX "
            "require a Polygon, Twelve Data or Finnhub key."
        ),
    }


@router.get("/markets")
def markets():
    """Every market, exchange and asset class the platform can chart.

    Includes the accepted symbol formats, so a user can see that `NASDAQ:SERV`,
    `SERV`, `^RUT`, `ES=F` and `BTCUSDT` are all valid input.
    """
    return market_catalog()


@router.get("/resolve")
def resolve(symbol: str = Query(..., min_length=1)):
    """Show how a typed symbol is interpreted and which provider will serve it."""
    try:
        return resolve_symbol(symbol).to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/candles")
def candles(
    symbol: str = Query(..., min_length=1),
    timeframe: str = Query("1h"),
    limit: int = Query(500, ge=10, le=5000),
    provider: str | None = Query(None),
    force: bool = Query(False, description="Bypass the cache"),
):
    """OHLCV history, oldest bar first."""
    try:
        series = market_service.candles(symbol, _timeframe(timeframe), limit, provider, force)
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return series.to_dict()


@router.get("/quote")
def quote(symbol: str = Query(..., min_length=1), provider: str | None = Query(None)):
    """Latest price snapshot, including bid/ask where the provider supplies it."""
    try:
        return market_service.quote(symbol, provider).to_dict()
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/quotes")
def quotes(symbols: str = Query(..., description="Comma-separated symbols"),
           provider: str | None = Query(None)):
    """Batch quotes for a watchlist.

    A single bad symbol returns an error entry for that symbol rather than
    failing the whole request — one delisted ticker should not blank the panel.
    """
    out = []
    for symbol in [s.strip() for s in symbols.split(",") if s.strip()][:50]:
        try:
            out.append(market_service.quote(symbol, provider).to_dict())
        except MarketDataError as exc:
            out.append({"symbol": symbol.upper(), "error": str(exc)})
    return {"quotes": out}


@router.get("/search")
def search(query: str = Query(..., min_length=1), provider: str | None = Query(None),
           limit: int = Query(20, ge=1, le=50)):
    try:
        return {"results": market_service.search(query, provider, limit)}
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/multi-timeframe")
def multi_timeframe(
    symbol: str = Query(..., min_length=1),
    timeframes: str = Query("15m,1h,4h,1d"),
    limit: int = Query(300, ge=50, le=1000),
    provider: str | None = Query(None),
):
    """Several timeframes at once — the input to higher/lower timeframe bias."""
    parsed = [_timeframe(t.strip()) for t in timeframes.split(",") if t.strip()]
    try:
        result = market_service.multi_timeframe(symbol, parsed, limit, provider)
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"symbol": symbol.upper(), "series": {k: v.to_dict() for k, v in result.items()}}


@router.get("/streams")
def active_streams():
    """Diagnostics: which symbols currently have a live upstream subscription."""
    return {"rooms": stream_hub.active_rooms}


@router.websocket("/ws")
async def market_stream(websocket: WebSocket):
    """Live candle stream.

    Protocol
    --------
    Client sends: {"action": "subscribe",   "symbol": "BTCUSDT", "timeframe": "1m"}
                  {"action": "unsubscribe", "symbol": "BTCUSDT", "timeframe": "1m"}
    Server sends: {"type": "snapshot", "live": bool, ...}  seeded history on subscribe
                  {"type": "candle",   ...}                every update, forming bar included
                  {"type": "error",    ...}

    `snapshot.live` tells the client whether `candle` frames that follow come
    from a real upstream push (Binance/Coinbase for crypto, or a keyed vendor's
    socket for stocks/forex/indices once configured) or from `StreamHub`
    polling a REST endpoint every 15s and relaying it over this same socket.
    Both arrive as identical `candle` frames, so this is the only way the
    client can avoid showing "LIVE" on a feed that is actually 15 seconds
    stale.
    """
    await websocket.accept()

    # Authenticate before streaming anything. Browsers cannot set headers on a
    # WebSocket handshake, so the access token arrives as a query parameter;
    # close with 1008 (policy violation) rather than silently serving data.
    session = SessionLocal()
    try:
        user = await websocket_user(websocket, session)
    finally:
        session.close()

    if user is None:
        await websocket.send_json({
            "type": "error",
            "message": "Not authenticated. Reconnect with ?token=<access token>.",
        })
        await websocket.close(code=1008)
        return

    # The provider is part of the value, not just the key, because
    # `StreamHub._key()` includes it: unsubscribing without the provider the
    # subscription was made with looks up a *different* room, finds nothing,
    # and silently leaves the real one running forever. That is the common
    # path — browsers close tabs, they rarely send an unsubscribe frame — so
    # forgetting it here leaks an upstream connection per disconnect.
    subscriptions: dict[tuple[str, str], tuple[asyncio.Queue, asyncio.Task, str | None]] = {}

    async def pump(key: tuple[str, str], queue: asyncio.Queue) -> None:
        """Forward one room's candles to this client until cancelled."""
        symbol, timeframe = key
        try:
            while True:
                candle = await queue.get()
                await websocket.send_json({
                    "type": "candle",
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "candle": candle.to_dict(),
                })
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - client went away mid-send
            logger.debug("stream pump ended for %s: %s", key, exc)

    try:
        while True:
            message = await websocket.receive_text()
            try:
                payload = json.loads(message)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "Invalid JSON."})
                continue

            action = payload.get("action")
            symbol = (payload.get("symbol") or "").upper()
            raw_timeframe = payload.get("timeframe") or "1m"
            provider = payload.get("provider")

            if not symbol:
                await websocket.send_json({"type": "error", "message": "symbol is required."})
                continue

            try:
                timeframe = Timeframe.parse(raw_timeframe)
            except ValueError as exc:
                await websocket.send_json({"type": "error", "message": str(exc)})
                continue

            key = (symbol, timeframe.value)

            if action == "subscribe":
                if key in subscriptions:
                    continue

                # Seed the chart with history so it renders immediately rather
                # than filling in one bar at a time from the live stream.
                try:
                    history = await asyncio.to_thread(
                        market_service.candles, symbol, timeframe, 500, provider
                    )
                    # Whether what follows is a real push feed or REST polling
                    # relayed over this same socket — the client cannot tell
                    # the difference from the frames alone, and showing "LIVE"
                    # on both would misrepresent a 15-second-lagged poll as
                    # tick-by-tick. Fails closed to "not streaming" rather than
                    # raising if the provider name is ever unrecognised.
                    try:
                        is_streaming = get_provider(history.provider).supports_streaming
                    except MarketDataError:
                        is_streaming = False
                    await websocket.send_json({
                        # Echo the symbol the client subscribed with, not the
                        # provider's normalised form. Coinbase resolves
                        # BTCUSDT to BTC-USD, and a client that keys its
                        # handlers on what it asked for would never match a
                        # frame labelled with the renamed symbol.
                        "type": "snapshot",
                        "symbol": symbol,
                        "resolved_symbol": history.symbol,
                        "timeframe": timeframe.value,
                        "provider": history.provider,
                        "live": is_streaming,
                        "candles": [c.to_dict() for c in history.candles],
                    })
                except MarketDataError as exc:
                    await websocket.send_json({
                        "type": "error", "symbol": symbol, "message": str(exc),
                    })
                    continue

                queue, last = await stream_hub.subscribe(symbol, timeframe, provider)
                if last is not None:
                    await websocket.send_json({
                        "type": "candle",
                        "symbol": symbol,
                        "timeframe": timeframe.value,
                        "candle": last.to_dict(),
                    })
                task = asyncio.create_task(pump(key, queue))
                subscriptions[key] = (queue, task, provider)
                logger.info("client subscribed to %s %s", symbol, timeframe.value)

            elif action == "unsubscribe":
                entry = subscriptions.pop(key, None)
                if entry:
                    queue, task, subscribed_provider = entry
                    task.cancel()
                    await stream_hub.unsubscribe(symbol, timeframe, queue, subscribed_provider)

            elif action == "ping":
                await websocket.send_json({"type": "pong"})

            else:
                await websocket.send_json({
                    "type": "error",
                    "message": f"Unknown action {action!r}. Use subscribe, unsubscribe or ping.",
                })

    except WebSocketDisconnect:
        logger.info("market websocket disconnected")
    except Exception as exc:  # noqa: BLE001 - always clean up subscriptions
        logger.warning("market websocket error: %s", exc)
    finally:
        # Pass back the provider each subscription was made with. Omitting it
        # here silently leaked a room — and its upstream connection — on every
        # disconnect, because the hub keys rooms by provider too.
        for (symbol, timeframe_value), (queue, task, subscribed_provider) in subscriptions.items():
            task.cancel()
            try:
                await stream_hub.unsubscribe(
                    symbol, Timeframe.parse(timeframe_value), queue, subscribed_provider
                )
            except Exception:  # noqa: BLE001
                pass
