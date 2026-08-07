import asyncio
import json

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
