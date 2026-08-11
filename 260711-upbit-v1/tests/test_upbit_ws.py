import asyncio
import json
import logging
import time

import pytest
import websockets

import trading.upbit_ws as upbit_ws
from trading.upbit_ws import stream_ticker


async def test_stream_ticker_sends_subscribe_message_and_yields_parsed_json():
    received_subscribe = asyncio.Future()

    async def handler(ws):
        raw = await ws.recv()
        received_subscribe.set_result(json.loads(raw))
        await ws.send(json.dumps({"type": "ticker", "code": "KRW-BTC", "trade_price": 50000000}))
        await ws.wait_closed()

    async with websockets.serve(handler, "localhost", 0) as server:
        port = server.sockets[0].getsockname()[1]
        url = f"ws://localhost:{port}"

        gen = stream_ticker(["KRW-BTC"], url=url)
        first_message = await anext(gen)
        await gen.aclose()

    subscribe_msg = await received_subscribe
    assert subscribe_msg[1] == {"type": "ticker", "codes": ["KRW-BTC"]}
    assert "ticket" in subscribe_msg[0]
    assert first_message == {"type": "ticker", "code": "KRW-BTC", "trade_price": 50000000}


async def test_stream_ticker_decodes_binary_frames():
    async def handler(ws):
        await ws.recv()
        await ws.send(json.dumps({"type": "ticker", "trade_price": 1}).encode("utf-8"))
        await ws.wait_closed()

    async with websockets.serve(handler, "localhost", 0) as server:
        port = server.sockets[0].getsockname()[1]
        gen = stream_ticker(["KRW-BTC"], url=f"ws://localhost:{port}")
        message = await anext(gen)
        await gen.aclose()

    assert message == {"type": "ticker", "trade_price": 1}


async def test_stream_ticker_reconnects_after_connection_drop(monkeypatch):
    monkeypatch.setattr(upbit_ws, "RECONNECT_BASE_DELAY_SECONDS", 0.01)
    connection_count = {"n": 0}

    async def handler(ws):
        connection_count["n"] += 1
        await ws.recv()  # subscribe message
        if connection_count["n"] == 1:
            await ws.send(json.dumps({"seq": 1}))
            await ws.close()  # 첫 연결은 끊어서 재연결을 유도
        else:
            await ws.send(json.dumps({"seq": 2}))
            await ws.wait_closed()

    async with websockets.serve(handler, "localhost", 0) as server:
        port = server.sockets[0].getsockname()[1]
        gen = stream_ticker(["KRW-BTC"], url=f"ws://localhost:{port}")
        first = await anext(gen)
        second = await anext(gen)
        await gen.aclose()

    assert first == {"seq": 1}
    assert second == {"seq": 2}
    assert connection_count["n"] == 2


async def test_stream_ticker_logs_warning_on_reconnect(monkeypatch, caplog):
    monkeypatch.setattr(upbit_ws, "RECONNECT_BASE_DELAY_SECONDS", 0.01)
    connection_count = {"n": 0}

    async def handler(ws):
        connection_count["n"] += 1
        await ws.recv()  # subscribe message
        if connection_count["n"] == 1:
            await ws.send(json.dumps({"seq": 1}))
            await ws.close()  # 첫 연결은 끊어서 재연결을 유도
        else:
            await ws.send(json.dumps({"seq": 2}))
            await ws.wait_closed()

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        gen = stream_ticker(["KRW-BTC"], url=f"ws://127.0.0.1:{port}")
        with caplog.at_level(logging.WARNING, logger="trading.upbit_ws"):
            await anext(gen)
            await anext(gen)
        await gen.aclose()

    assert any("재연결" in record.message for record in caplog.records)


async def test_stream_ticker_applies_backoff_delay_on_clean_close(monkeypatch):
    monkeypatch.setattr(upbit_ws, "RECONNECT_BASE_DELAY_SECONDS", 0.2)
    received_at: list[float] = []

    async def handler(ws):
        await ws.recv()  # subscribe message
        await ws.send(json.dumps({"seq": 1}))
        received_at.append(time.monotonic())
        await ws.close()  # clean close (code 1000) — the scenario that was broken

    # NOTE: uses 127.0.0.1 rather than "localhost" — on this dev machine, "localhost"
    # intermittently added ~2s of pure TCP/DNS dual-stack resolution overhead per fresh
    # connection (verified via a standalone repro script; identical overhead occurred on
    # the very first connection too, before any backoff logic ran), which is unrelated to
    # the backoff behavior under test and made the timing assertion below flaky.
    # 127.0.0.1 avoids the ambiguous localhost resolution and was stable across repeated runs.
    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        gen = stream_ticker(["KRW-BTC"], url=f"ws://127.0.0.1:{port}")
        await anext(gen)  # first message, connection 1
        start = time.monotonic()
        await anext(gen)  # should only arrive after the backoff delay elapses
        elapsed = time.monotonic() - start
        await gen.aclose()

    assert elapsed >= 0.15  # genuinely waited close to the 0.2s delay (small tolerance for scheduling jitter)
    assert elapsed < 2.0  # sanity bound — didn't hang or apply some much larger delay
