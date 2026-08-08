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


import json

import trading.db as db
import trading.position_manager as position_manager
from tests.trading_db_fixtures import insert_live_strategy


def _fresh_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "trading.db")
    return db


def _strategy_row(dbm, **risk_overrides):
    risk_config = {
        "order_execution_mode": "market",
        "max_position_per_market": 1_000_000.0,
        "max_slippage_pct": 0.5,
        "order_timeout_sec": 10,
    }
    risk_config.update(risk_overrides)
    strategy_id = insert_live_strategy(
        dbm, market="KRW-BTC", current_capital=1_000_000.0,
        risk_config_json=json.dumps(risk_config),
    )
    return dbm.get_live_strategy(strategy_id)


async def test_enter_dry_run_opens_position_at_requested_price(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)

    order = await order_executor.enter(strategy, 500_000.0, 50_000_000.0, dry_run=True)

    assert order["status"] == "done"
    assert order["filled_price"] == 50_000_000.0
    assert order["fee"] == 0.0
    position = position_manager.get_open_position(strategy["id"])
    assert position is not None
    assert position["entry_price"] == 50_000_000.0


async def test_enter_market_mode_places_price_order_and_records_fill(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)
    captured = {}

    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        captured.update(market=market, side=side, ord_type=ord_type, volume=volume, price=price)
        return {"uuid": "uuid-1", "state": "done"}

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        return {"state": "done", "executed_volume": "0.01", "remaining_volume": "0",
                "paid_fee": "250.0", "trades": [{"funds": "500000.0"}]}

    monkeypatch.setattr(upbit_client, "create_order", fake_create_order)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    order = await order_executor.enter(strategy, 500_000.0, 50_000_000.0)

    assert captured["ord_type"] == "price"  # 시장가 매수는 price 타입(설계 스펙 결정7)
    assert captured["price"] == "500000.0"
    assert captured["volume"] is None
    assert order["status"] == "done"
    assert order["filled_price"] == pytest.approx(500000.0 / 0.01)
    position = position_manager.get_open_position(strategy["id"])
    assert position is not None


async def test_enter_raises_when_position_already_open(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)
    position_manager.open_position(strategy["id"], "KRW-BTC", 100.0, 1.0)

    with pytest.raises(ValueError):
        await order_executor.enter(strategy, 500_000.0, 50_000_000.0, dry_run=True)


async def test_exit_market_mode_places_market_order_and_closes_position(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)
    position_manager.open_position(strategy["id"], "KRW-BTC", 49_000_000.0, 0.01)
    position = position_manager.get_open_position(strategy["id"])
    captured = {}

    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        captured.update(side=side, ord_type=ord_type, volume=volume, price=price)
        return {"uuid": "uuid-2", "state": "done"}

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        return {"state": "done", "executed_volume": "0.01", "remaining_volume": "0",
                "paid_fee": "250.0", "trades": [{"funds": "500000.0"}]}

    monkeypatch.setattr(upbit_client, "create_order", fake_create_order)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    order = await order_executor.exit(strategy, position, 50_000_000.0)

    assert captured["ord_type"] == "market"  # 시장가 매도는 market 타입(설계 스펙 결정7)
    assert captured["volume"] == "0.01"
    assert captured["price"] is None
    assert order["status"] == "done"
    assert "realized_pnl" in order
    assert position_manager.get_open_position(strategy["id"]) is None


async def test_exit_raises_when_no_open_position():
    with pytest.raises(ValueError):
        await order_executor.exit({"id": "s1", "risk_config_json": "{}", "market": "KRW-BTC"}, None, 100.0)


async def test_enter_limit_mode_leaves_order_waiting_without_opening_position(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, order_execution_mode="limit")

    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        assert ord_type == "limit"
        return {"uuid": "uuid-3", "state": "wait"}

    monkeypatch.setattr(upbit_client, "create_order", fake_create_order)

    order = await order_executor.enter(strategy, 500_000.0, 50_000_000.0)

    assert order["status"] == "wait"
    assert order["filled_price"] is None
    assert position_manager.get_open_position(strategy["id"]) is None


async def test_enter_limit_timeout_fills_within_timeout_without_conversion(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, order_execution_mode="limit_timeout")

    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        assert ord_type == "limit"
        return {"uuid": "uuid-4", "state": "wait"}

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        return {"state": "done", "executed_volume": "0.01", "remaining_volume": "0",
                "paid_fee": "250.0", "trades": [{"funds": "500000.0"}]}

    async def fake_sleep(seconds):
        pass

    monkeypatch.setattr(upbit_client, "create_order", fake_create_order)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)
    monkeypatch.setattr(order_executor.asyncio, "sleep", fake_sleep)

    order = await order_executor.enter(strategy, 500_000.0, 50_000_000.0)

    assert order["status"] == "done"
    assert order["filled_price"] == pytest.approx(500000.0 / 0.01)
    assert position_manager.get_open_position(strategy["id"]) is not None


async def test_enter_limit_timeout_converts_remainder_to_market_after_timeout(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, order_execution_mode="limit_timeout")
    calls = {"create": 0, "cancel": 0}

    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        calls["create"] += 1
        if calls["create"] == 1:
            assert ord_type == "limit"
            return {"uuid": "uuid-limit", "state": "wait"}
        assert ord_type == "price"  # 잔량 매수 전환도 시장가 매수라 price 타입(결정7)
        return {"uuid": "uuid-market", "state": "done"}

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        if uuid == "uuid-limit":
            return {"state": "wait", "executed_volume": "0.004", "remaining_volume": "0.006",
                    "paid_fee": "100.0", "trades": [{"funds": "200000.0"}]}
        return {"state": "done", "executed_volume": "0.006", "remaining_volume": "0",
                "paid_fee": "150.0", "trades": [{"funds": "300000.0"}]}

    async def fake_cancel_order(*, uuid=None, identifier=None, client=None):
        calls["cancel"] += 1
        return {"uuid": uuid, "state": "cancel"}

    async def fake_sleep(seconds):
        pass

    monkeypatch.setattr(upbit_client, "create_order", fake_create_order)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)
    monkeypatch.setattr(upbit_client, "cancel_order", fake_cancel_order)
    monkeypatch.setattr(order_executor.asyncio, "sleep", fake_sleep)

    order = await order_executor.enter(strategy, 500_000.0, 50_000_000.0)

    assert calls["create"] == 2
    assert calls["cancel"] == 1
    assert order["status"] == "done"
    assert order["filled_volume"] == pytest.approx(0.01)
    assert order["filled_price"] == pytest.approx(500_000.0 / 0.01)  # (200000+300000)/0.01
    assert position_manager.get_open_position(strategy["id"]) is not None


async def test_enter_market_capped_fills_within_slippage_cap(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, order_execution_mode="market_capped", max_slippage_pct=0.5)
    captured = {}

    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        captured.update(ord_type=ord_type, price=price, time_in_force=time_in_force)
        return {"uuid": "uuid-5", "state": "done"}

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        return {"state": "done", "executed_volume": "0.01", "remaining_volume": "0",
                "paid_fee": "250.0", "trades": [{"funds": "502000.0"}]}

    monkeypatch.setattr(upbit_client, "create_order", fake_create_order)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    order = await order_executor.enter(strategy, 500_000.0, 50_000_000.0)

    assert captured["ord_type"] == "limit"
    assert captured["time_in_force"] == "fok"
    assert captured["price"] == str(order_executor.round_to_tick(50_000_000.0 * 1.005))
    assert order["status"] == "done"
    assert position_manager.get_open_position(strategy["id"]) is not None


async def test_enter_market_capped_cancels_when_fok_fails_and_position_untouched(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, order_execution_mode="market_capped", max_slippage_pct=0.1)

    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        return {"uuid": "uuid-6", "state": "cancel"}

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        return {"state": "cancel", "executed_volume": "0", "remaining_volume": "0.01",
                "paid_fee": "0", "trades": []}

    monkeypatch.setattr(upbit_client, "create_order", fake_create_order)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    order = await order_executor.enter(strategy, 500_000.0, 50_000_000.0)

    assert order["status"] == "cancel"
    assert order["filled_price"] is None
    assert position_manager.get_open_position(strategy["id"]) is None


async def test_exit_market_capped_uses_lower_bound_price(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, order_execution_mode="market_capped", max_slippage_pct=0.5)
    position_manager.open_position(strategy["id"], "KRW-BTC", 49_000_000.0, 0.01)
    position = position_manager.get_open_position(strategy["id"])
    captured = {}

    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        captured.update(price=price)
        return {"uuid": "uuid-7", "state": "done"}

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        return {"state": "done", "executed_volume": "0.01", "remaining_volume": "0",
                "paid_fee": "250.0", "trades": [{"funds": "497500.0"}]}

    monkeypatch.setattr(upbit_client, "create_order", fake_create_order)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    await order_executor.exit(strategy, position, 50_000_000.0)

    assert captured["price"] == str(order_executor.round_to_tick(50_000_000.0 * 0.995))


async def test_enter_market_capped_sizes_volume_against_capped_price(monkeypatch, tmp_path):
    """market_capped는 expected_price가 아니라 슬리피지 상한가(capped_price)로 주문하므로
    수량도 capped_price 기준으로 계산해야 clamp된 capital을 넘기지 않는다(최종리뷰 Critical #2)."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, order_execution_mode="market_capped", max_slippage_pct=0.5)
    captured = {}

    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        captured.update(price=price, volume=volume)
        return {"uuid": "uuid-capped-vol", "state": "done"}

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        return {"state": "done", "executed_volume": "0.009", "remaining_volume": "0",
                "paid_fee": "250.0", "trades": [{"funds": "450000.0"}]}

    monkeypatch.setattr(upbit_client, "create_order", fake_create_order)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    capital = 500_000.0
    order = await order_executor.enter(strategy, capital, 50_000_000.0)

    capped_price = order_executor.round_to_tick(50_000_000.0 * 1.005)  # 50,250,000
    # orders 행에도 실제로 낸 주문가(capped_price)가 남아야 감사추적이 맞다
    assert order["requested_price"] == pytest.approx(capped_price)
    sent_volume = float(captured["volume"])
    assert sent_volume == pytest.approx(order_executor._floor_volume(capital / capped_price))
    assert sent_volume != pytest.approx(order_executor._floor_volume(capital / 50_000_000.0))
    assert capped_price * sent_volume <= capital  # clamp된 자본을 절대 넘지 않는다


async def test_exit_market_capped_sells_whole_position_qty(monkeypatch, tmp_path):
    """매도는 capital이 아니라 보유수량 전량을 팔므로 capped_price 재계산 대상이 아니다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, order_execution_mode="market_capped", max_slippage_pct=0.5)
    position_manager.open_position(strategy["id"], "KRW-BTC", 49_000_000.0, 0.01)
    position = position_manager.get_open_position(strategy["id"])
    captured = {}

    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        captured.update(volume=volume)
        return {"uuid": "uuid-capped-ask", "state": "done"}

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        return {"state": "done", "executed_volume": "0.01", "remaining_volume": "0",
                "paid_fee": "250.0", "trades": [{"funds": "497500.0"}]}

    monkeypatch.setattr(upbit_client, "create_order", fake_create_order)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    await order_executor.exit(strategy, position, 50_000_000.0)

    assert float(captured["volume"]) == pytest.approx(0.01)


async def test_enter_market_polls_until_order_settles(monkeypatch, tmp_path):
    """업비트는 주문을 비동기 체결하므로 create_order 직후 조회는 아직 wait일 수 있다.
    확정될 때까지 짧게 재조회해야 한다(최종리뷰 Critical #1)."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)
    calls = {"get": 0, "sleep": 0}

    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        return {"uuid": "uuid-settle", "state": "wait"}

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        calls["get"] += 1
        if calls["get"] == 1:
            return {"state": "wait", "executed_volume": "0", "remaining_volume": "0.01",
                    "paid_fee": "0", "trades": []}
        return {"state": "done", "executed_volume": "0.01", "remaining_volume": "0",
                "paid_fee": "250.0", "trades": [{"funds": "500000.0"}]}

    async def fake_sleep(seconds):
        calls["sleep"] += 1

    monkeypatch.setattr(upbit_client, "create_order", fake_create_order)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)
    monkeypatch.setattr(order_executor.asyncio, "sleep", fake_sleep)

    order = await order_executor.enter(strategy, 500_000.0, 50_000_000.0)

    assert calls["get"] == 2  # 첫 조회가 wait였으므로 한 번 더 폴링
    assert calls["sleep"] >= 1
    assert order["status"] == "done"
    assert order["filled_price"] == pytest.approx(500_000.0 / 0.01)
    assert position_manager.get_open_position(strategy["id"]) is not None


async def test_enter_market_returns_wait_when_order_never_settles(monkeypatch, tmp_path):
    """폴링 타임아웃까지 wait이면 crash도, 거짓 done도 아닌 wait으로 보고한다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)
    calls = {"get": 0}

    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        return {"uuid": "uuid-never", "state": "wait"}

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        calls["get"] += 1
        return {"state": "wait", "executed_volume": "0", "remaining_volume": "0.01",
                "paid_fee": "0", "trades": []}

    async def fake_sleep(seconds):
        pass

    monkeypatch.setattr(upbit_client, "create_order", fake_create_order)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)
    monkeypatch.setattr(order_executor.asyncio, "sleep", fake_sleep)

    order = await order_executor.enter(strategy, 500_000.0, 50_000_000.0)

    assert order["status"] == "wait"
    assert calls["get"] > 1
    assert position_manager.get_open_position(strategy["id"]) is None


import trading.risk_manager as risk_manager


def _signal_result(**overrides):
    base = {
        "new_candle": True, "candle_time": "2026-08-08T10:00:00+00:00",
        "buy_signal": False, "sell_signal": False,
        "buy_signal_id": "buy-sig-1", "sell_signal_id": "sell-sig-1",
        "latest_close": 50_000_000.0, "paused": False, "resumed": False,
    }
    base.update(overrides)
    return base


async def test_handle_signal_result_does_nothing_when_paused(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)

    result = await order_executor.handle_signal_result(
        strategy["id"], _signal_result(buy_signal=True, paused=True), dry_run=True,
    )

    assert result == {"buy_action": None, "sell_action": None, "buy_order_id": None, "sell_order_id": None}
    assert position_manager.get_open_position(strategy["id"]) is None


async def test_handle_signal_result_returns_early_when_no_new_candle(monkeypatch, tmp_path):
    """새 봉이 없을 때가 실제로 가장 흔한 호출인데, 그 결과 dict에는 latest_close가 아예
    없어서 KeyError로 터졌다(최종리뷰 Important #3). 전략 행조차 만들지 않고 호출해
    조기 반환(=DB 조회 없음)을 검증한다."""
    import trading.signal_engine as signal_engine

    _fresh_db(monkeypatch, tmp_path)

    result = await order_executor.handle_signal_result(
        "no-such-strategy", signal_engine._no_new_candle_result(), dry_run=True,
    )

    assert result == {"buy_action": None, "sell_action": None,
                      "buy_order_id": None, "sell_order_id": None}


async def test_handle_signal_result_enters_on_buy_signal(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)

    result = await order_executor.handle_signal_result(
        strategy["id"], _signal_result(buy_signal=True), dry_run=True,
    )

    assert result["buy_action"] == "entered"
    assert result["buy_order_id"] is not None
    assert position_manager.get_open_position(strategy["id"]) is not None


async def test_handle_signal_result_skips_buy_when_circuit_tripped(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)
    monkeypatch.setattr(risk_manager, "is_circuit_tripped_today", lambda sid: True)

    result = await order_executor.handle_signal_result(
        strategy["id"], _signal_result(buy_signal=True), dry_run=True,
    )

    assert result["buy_action"] == "skipped_circuit_breaker"
    assert position_manager.get_open_position(strategy["id"]) is None


async def test_handle_signal_result_exits_on_sell_signal_and_records_trade(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)
    position_manager.open_position(strategy["id"], "KRW-BTC", 49_000_000.0, 0.01)
    recorded = {}
    monkeypatch.setattr(
        risk_manager, "record_trade_result",
        lambda sid, pnl, capital_after: recorded.update(sid=sid, pnl=pnl, capital_after=capital_after),
    )

    result = await order_executor.handle_signal_result(
        strategy["id"], _signal_result(sell_signal=True), dry_run=True,
    )

    assert result["sell_action"] == "exited"
    assert position_manager.get_open_position(strategy["id"]) is None
    assert recorded["sid"] == strategy["id"]


async def test_handle_signal_result_marks_pending_for_plain_limit_mode(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, order_execution_mode="limit")

    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        return {"uuid": "uuid-8", "state": "wait"}

    monkeypatch.setattr(upbit_client, "create_order", fake_create_order)

    result = await order_executor.handle_signal_result(strategy["id"], _signal_result(buy_signal=True))

    assert result["buy_action"] == "pending"
    assert position_manager.get_open_position(strategy["id"]) is None


async def test_handle_signal_result_records_slippage_exceeded_on_fok_cancel(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, order_execution_mode="market_capped", max_slippage_pct=0.1)

    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        return {"uuid": "uuid-9", "state": "cancel"}

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        return {"state": "cancel", "executed_volume": "0", "remaining_volume": "0.01",
                "paid_fee": "0", "trades": []}

    monkeypatch.setattr(upbit_client, "create_order", fake_create_order)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    result = await order_executor.handle_signal_result(strategy["id"], _signal_result(buy_signal=True))

    assert result["buy_action"] == "slippage_exceeded"
    assert position_manager.get_open_position(strategy["id"]) is None
