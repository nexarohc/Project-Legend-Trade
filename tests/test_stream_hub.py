"""StreamHub room lifecycle under load.

These exist because of a real bug that load testing found rather than
reading: the WebSocket handler subscribed *with* a provider but, on the
disconnect path, unsubscribed *without* one. `StreamHub._key()` includes the
provider, so the cleanup looked up a different room, found nothing, and left
the real one — and its upstream connection — running forever.

That path is the common one. Browsers close tabs; they rarely send a polite
unsubscribe frame. A 150-client load test against a real server leaked 50
rooms before the fix and zero after, so the regression is worth pinning down.

No network and no new test dependency: rooms are kept open with `_run`
replaced by a sleep, and async bodies run through `asyncio.run` the way
`test_execution_scheduler.py` already does.
"""
import asyncio

from trading.market import StreamHub
from trading.models import Candle, Timeframe


def _hub() -> StreamHub:
    """A hub whose rooms never open a real upstream connection."""
    hub = StreamHub()

    async def _no_upstream(key, room):
        await asyncio.sleep(3600)

    hub._run = _no_upstream
    return hub


# --- the regression that load testing found -----------------------------------

def test_unsubscribing_without_the_provider_leaves_the_room_open():
    """Pins the *mechanism* of the bug: the hub keys rooms by provider, so a
    mismatched key cannot find the room. This is why the handler has to
    remember which provider each subscription was made with."""
    async def scenario():
        hub = _hub()
        queue, _ = await hub.subscribe("BTCUSDT", Timeframe.M1, "binance")
        await hub.unsubscribe("BTCUSDT", Timeframe.M1, queue)  # provider omitted
        return hub.active_rooms

    rooms = asyncio.run(scenario())
    assert len(rooms) == 1, (
        "a provider-less unsubscribe targets a different key and cannot close "
        "the room — the handler must pass the provider it subscribed with"
    )


def test_unsubscribing_with_the_provider_closes_the_room():
    async def scenario():
        hub = _hub()
        queue, _ = await hub.subscribe("BTCUSDT", Timeframe.M1, "binance")
        await hub.unsubscribe("BTCUSDT", Timeframe.M1, queue, "binance")
        return hub.active_rooms

    assert asyncio.run(scenario()) == []


def test_no_rooms_leak_when_many_clients_disconnect():
    """The load-test scenario, shrunk: a mix of explicit and default
    providers, all leaving. Nothing may be left behind."""
    async def scenario():
        hub = _hub()
        subscriptions = []
        for i in range(60):
            symbol = f"SYM{i % 10}"
            timeframe = Timeframe.M1 if i % 2 == 0 else Timeframe.M5
            provider = ["binance", None, "coinbase"][i % 3]
            queue, _ = await hub.subscribe(symbol, timeframe, provider)
            subscriptions.append((symbol, timeframe, queue, provider))

        while_attached = len(hub.active_rooms)

        for symbol, timeframe, queue, provider in subscriptions:
            await hub.unsubscribe(symbol, timeframe, queue, provider)
        return while_attached, hub.active_rooms

    while_attached, after = asyncio.run(scenario())
    assert while_attached > 0, "rooms should exist while clients are attached"
    assert after == [], "every room must close once its last client leaves"


# --- fan-out: the property the hub exists for ---------------------------------

def test_many_clients_on_one_symbol_share_a_single_upstream():
    async def scenario():
        hub = _hub()
        queues = [
            (await hub.subscribe("BTCUSDT", Timeframe.M1, "binance"))[0]
            for _ in range(50)
        ]
        shared = list(hub.active_rooms)
        for queue in queues:
            await hub.unsubscribe("BTCUSDT", Timeframe.M1, queue, "binance")
        return shared, hub.active_rooms

    shared, after = asyncio.run(scenario())
    assert len(shared) == 1, "50 clients must share one upstream, not open 50"
    assert shared[0]["subscribers"] == 50
    assert after == []


def test_a_room_stays_open_until_its_last_client_leaves():
    async def scenario():
        hub = _hub()
        first, _ = await hub.subscribe("BTCUSDT", Timeframe.M1, "binance")
        second, _ = await hub.subscribe("BTCUSDT", Timeframe.M1, "binance")

        await hub.unsubscribe("BTCUSDT", Timeframe.M1, first, "binance")
        after_first = len(hub.active_rooms)

        await hub.unsubscribe("BTCUSDT", Timeframe.M1, second, "binance")
        return after_first, hub.active_rooms

    after_first, after_both = asyncio.run(scenario())
    assert after_first == 1, "one client is still watching"
    assert after_both == []


def test_different_timeframes_are_separate_rooms():
    async def scenario():
        hub = _hub()
        a, _ = await hub.subscribe("BTCUSDT", Timeframe.M1, "binance")
        b, _ = await hub.subscribe("BTCUSDT", Timeframe.M5, "binance")
        both = len(hub.active_rooms)

        await hub.unsubscribe("BTCUSDT", Timeframe.M1, a, "binance")
        await hub.unsubscribe("BTCUSDT", Timeframe.M5, b, "binance")
        return both, hub.active_rooms

    both, after = asyncio.run(scenario())
    assert both == 2
    assert after == []


def test_unsubscribing_an_unknown_room_is_a_no_op():
    """The disconnect path runs cleanup for everything it thinks it holds; a
    room that is already gone must not raise."""
    async def scenario():
        hub = _hub()
        queue: asyncio.Queue = asyncio.Queue()
        await hub.unsubscribe("NOTSUBSCRIBED", Timeframe.M1, queue, "binance")
        return hub.active_rooms

    assert asyncio.run(scenario()) == []


# --- the slow-client guarantee -------------------------------------------------

def test_a_full_queue_drops_its_oldest_rather_than_blocking_everyone():
    """One stalled client must not stall the feed for the rest of the room."""
    async def scenario():
        hub = _hub()
        queue, _ = await hub.subscribe("BTCUSDT", Timeframe.M1, "binance")
        room = hub._rooms[hub._key("BTCUSDT", Timeframe.M1, "binance")]

        # Overfill well past the 256-slot queue.
        for i in range(400):
            hub._publish(room, Candle(timestamp=i, open=1, high=1, low=1, close=i, volume=1))
        return queue.qsize(), room.last

    size, last = asyncio.run(scenario())
    assert size <= 256, "the queue must stay bounded rather than grow without limit"
    # The newest candle survived — the oldest were dropped, not the newest.
    assert last.close == 399
