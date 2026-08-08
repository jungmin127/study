import trading.order_executor as order_executor


def test_round_to_tick_boundaries():
    assert order_executor.round_to_tick(2_500_000) == 2_500_000  # 1,000,000원 이상 → 1,000원 단위
    assert order_executor.round_to_tick(2_500_400) == 2_500_000
    assert order_executor.round_to_tick(999_760) == 1_000_000  # 500,000~1,000,000 → 500원 단위이므로 반올림 값 확인
    assert order_executor.round_to_tick(150_030) == 150_000  # 100,000~500,000 → 100원 단위
    assert order_executor.round_to_tick(9_998) == 10_000  # 5,000~10,000 → 5원 단위, 반올림
    assert order_executor.round_to_tick(4_500) == 4_500  # 1,000~5,000 → 1원 단위
    assert order_executor.round_to_tick(55) == 55.0  # 10~100 → 0.1원 단위
    assert order_executor.round_to_tick(5.678) == 5.68  # 1~10 → 0.01원 단위


def test_floor_volume_truncates_to_eight_decimals():
    assert order_executor._floor_volume(0.123456789) == 0.12345678
    assert order_executor._floor_volume(1.0) == 1.0


import httpx
import pytest

import trading.upbit_client as upbit_client


async def test_fetch_fill_computes_weighted_average_price_from_trades(monkeypatch):
    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        return {
            "state": "done",
            "executed_volume": "0.02",
            "remaining_volume": "0",
            "paid_fee": "500.0",
            "trades": [
                {"funds": "500000.0", "volume": "0.01"},
                {"funds": "510000.0", "volume": "0.01"},
            ],
        }

    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    fill = await order_executor._fetch_fill("uuid-1")

    assert fill["state"] == "done"
    assert fill["executed_volume"] == pytest.approx(0.02)
    assert fill["remaining_volume"] == pytest.approx(0.0)
    assert fill["filled_price"] == pytest.approx(1_010_000.0 / 0.02)
    assert fill["fee"] == pytest.approx(500.0)


async def test_fetch_fill_returns_none_price_when_nothing_executed(monkeypatch):
    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        return {"state": "wait", "executed_volume": "0", "remaining_volume": "1.0",
                "paid_fee": "0", "trades": []}

    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    fill = await order_executor._fetch_fill("uuid-2")

    assert fill["filled_price"] is None


def test_slippage_pct_computes_percentage_deviation():
    assert order_executor._slippage_pct(101.0, 100.0) == pytest.approx(1.0)
    assert order_executor._slippage_pct(99.0, 100.0) == pytest.approx(-1.0)


async def test_create_order_with_retry_returns_response_on_success(monkeypatch):
    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        return {"uuid": "uuid-1", "state": "wait"}

    monkeypatch.setattr(upbit_client, "create_order", fake_create_order)

    resp = await order_executor._create_order_with_retry(
        "KRW-BTC", "bid", "limit", order_id="order-1", price="100", volume="1",
    )

    assert resp["uuid"] == "uuid-1"


async def test_create_order_with_retry_reuses_existing_order_after_network_error(monkeypatch):
    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        raise httpx.TimeoutException("timed out")

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        assert identifier == "order-1"
        return {"uuid": "uuid-recovered", "state": "wait"}

    monkeypatch.setattr(upbit_client, "create_order", fake_create_order)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    resp = await order_executor._create_order_with_retry(
        "KRW-BTC", "bid", "limit", order_id="order-1", price="100", volume="1",
    )

    assert resp["uuid"] == "uuid-recovered"


async def test_create_order_with_retry_retries_when_confirmation_finds_nothing(monkeypatch):
    calls = {"create": 0}

    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        calls["create"] += 1
        if calls["create"] == 1:
            raise httpx.TimeoutException("timed out")
        return {"uuid": "uuid-2", "state": "wait"}

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        request = httpx.Request("GET", "https://api.upbit.com/v1/order")
        raise httpx.HTTPStatusError("404", request=request, response=httpx.Response(404, request=request))

    monkeypatch.setattr(upbit_client, "create_order", fake_create_order)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    resp = await order_executor._create_order_with_retry(
        "KRW-BTC", "bid", "limit", order_id="order-1", price="100", volume="1",
    )

    assert resp["uuid"] == "uuid-2"
    assert calls["create"] == 2
