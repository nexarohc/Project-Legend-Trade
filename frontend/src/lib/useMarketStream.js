import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { wsUrl } from "./tradingApi.js";

/**
 * Live market data over a single shared WebSocket.
 *
 * One socket serves every symbol the UI is watching; subscriptions are
 * multiplexed over it. The socket reconnects with backoff and re-subscribes to
 * whatever was active, so a laptop waking from sleep recovers on its own
 * rather than leaving a silently frozen chart on screen — which is worse than
 * showing no chart at all.
 */
export function useMarketStream() {
  const socketRef = useRef(null);
  const attemptRef = useRef(0);
  const reconnectRef = useRef(null);
  const subscriptionsRef = useRef(new Map()); // key -> {symbol, timeframe, provider}
  const handlersRef = useRef(new Map());      // key -> Set<handler>

  const [status, setStatus] = useState("connecting");

  const keyOf = (symbol, timeframe) => `${symbol.toUpperCase()}:${timeframe}`;

  const send = useCallback((message) => {
    const socket = socketRef.current;
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify(message));
      return true;
    }
    return false;
  }, []);

  const connect = useCallback(() => {
    if (socketRef.current?.readyState === WebSocket.OPEN) return;

    setStatus(attemptRef.current === 0 ? "connecting" : "reconnecting");
    // Built per connection so a refreshed access token is picked up on reconnect.
    const socket = new WebSocket(wsUrl());
    socketRef.current = socket;

    socket.onopen = () => {
      attemptRef.current = 0;
      setStatus("live");
      // Restore every subscription the UI still believes is active.
      subscriptionsRef.current.forEach((sub) => {
        socket.send(JSON.stringify({ action: "subscribe", ...sub }));
      });
    };

    socket.onmessage = (event) => {
      let message;
      try {
        message = JSON.parse(event.data);
      } catch {
        return;
      }
      if (message.type === "pong") return;

      const deliver = (handlers) => {
        if (!handlers) return;
        handlers.forEach((handler) => {
          try {
            handler(message);
          } catch (error) {
            console.error("market stream handler failed", error);
          }
        });
      };

      const key = keyOf(message.symbol ?? "", message.timeframe ?? "");
      const exact = handlersRef.current.get(key);
      if (exact) {
        deliver(exact);
        return;
      }

      // A frame that names a symbol but no timeframe cannot be routed by the
      // exact key, and dropping it is the worst option available: the panel
      // waiting on that symbol keeps showing "Loading…" indefinitely with no
      // indication anything went wrong. Errors in particular arrived this way,
      // so they were invisible precisely when they mattered most.
      //
      // The server now stamps a timeframe on its error frames, so the exact
      // path above handles them. This stays as the floor: an unroutable error
      // reaches every subscriber for that symbol rather than nobody, which is
      // the right way to be wrong.
      if (message.type === "error" && message.symbol && !message.timeframe) {
        const prefix = `${String(message.symbol).toUpperCase()}:`;
        handlersRef.current.forEach((handlers, registered) => {
          if (registered.startsWith(prefix)) deliver(handlers);
        });
      }
    };

    socket.onclose = () => {
      setStatus("disconnected");
      // Exponential backoff, capped, so a backend that is down does not get
      // hammered by every open tab.
      const delay = Math.min(1000 * 2 ** attemptRef.current, 15000);
      attemptRef.current += 1;
      reconnectRef.current = setTimeout(connect, delay);
    };

    socket.onerror = () => socket.close();
  }, []);

  useEffect(() => {
    connect();
    return () => {
      clearTimeout(reconnectRef.current);
      const socket = socketRef.current;
      socketRef.current = null;
      // Detach handlers before closing so the reconnect timer isn't armed by
      // the unmount-triggered close.
      if (socket) {
        socket.onclose = null;
        socket.close();
      }
    };
  }, [connect]);

  /** Subscribe to a symbol/timeframe. Returns an unsubscribe function. */
  const subscribe = useCallback((symbol, timeframe, handler, provider) => {
    if (!symbol || !timeframe) return () => {};
    const key = keyOf(symbol, timeframe);

    if (!handlersRef.current.has(key)) handlersRef.current.set(key, new Set());
    handlersRef.current.get(key).add(handler);

    const isNew = !subscriptionsRef.current.has(key);
    subscriptionsRef.current.set(key, { symbol: symbol.toUpperCase(), timeframe, provider });
    if (isNew) send({ action: "subscribe", symbol: symbol.toUpperCase(), timeframe, provider });

    return () => {
      const handlers = handlersRef.current.get(key);
      if (!handlers) return;
      handlers.delete(handler);
      if (handlers.size === 0) {
        handlersRef.current.delete(key);
        subscriptionsRef.current.delete(key);
        send({ action: "unsubscribe", symbol: symbol.toUpperCase(), timeframe });
      }
    };
  }, [send]);

  // Memoised so consumers get a stable object identity. Returning a fresh
  // literal each render would make every effect keyed on `stream` tear down and
  // re-subscribe on any unrelated parent render — visible as a redundant
  // unsubscribe/subscribe round trip and a duplicated history snapshot.
  return useMemo(() => ({ status, subscribe }), [status, subscribe]);
}

/**
 * Live candles for one symbol/timeframe, ready to hand to a chart.
 *
 * Holds the series in a ref and mirrors it into state, because the chart needs
 * a stable array identity for incremental updates while React needs a new one
 * to re-render the header.
 */
export function useCandles(stream, symbol, timeframe, provider) {
  const [snapshot, setSnapshot] = useState(null);
  const [lastCandle, setLastCandle] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!symbol || !timeframe) return undefined;

    setLoading(true);
    setError(null);
    setSnapshot(null);
    setLastCandle(null);

    const unsubscribe = stream.subscribe(
      symbol,
      timeframe,
      (message) => {
        if (message.type === "snapshot") {
          setSnapshot({
            candles: message.candles,
            provider: message.provider,
            symbol: message.symbol,
            live: message.live,
          });
          setLoading(false);
        } else if (message.type === "candle") {
          setLastCandle(message.candle);
          setLoading(false);
        } else if (message.type === "error") {
          setError(message.message);
          setLoading(false);
        }
      },
      provider,
    );

    return unsubscribe;
  }, [stream, symbol, timeframe, provider]);

  return { snapshot, lastCandle, error, loading };
}
