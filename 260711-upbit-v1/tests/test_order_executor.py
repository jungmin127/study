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
    assert order_executor._floor_volume(0.29999999999) == 0.29999999


def test_floor_volume_does_not_shave_satoshi_on_binary_representation_error():
    """0.00998 * 1e8 == 997999.9999999999 이라 단순 floor는 1사토시를 깎아먹는다.
    이미 8자리인 값은 그대로 보존돼야 한다(exit()이 보유수량 전량을 팔 수 있도록)."""
    assert order_executor._floor_volume(0.00998) == 0.00998
    assert order_executor._floor_volume(0.1 + 0.2) == 0.3


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
    identifiers = []

    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        calls["create"] += 1
        identifiers.append(identifier)
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
    # 재시도가 같은 identifier를 재사용해야 이중주문이 안 난다(멱등성 설계의 핵심)
    assert identifiers == ["order-1", "order-1"]


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
    # 주문금액 = capital에서 매수 수수료 여유를 뺀 값(_BID_FEE_RATE), 지수표기/꼬리'.0' 없이
    assert "e" not in captured["price"]
    assert float(captured["price"]) == pytest.approx(500_000.0 / 1.0005)
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


async def test_exit_limit_mode_leaves_order_waiting_without_closing_position(monkeypatch, tmp_path):
    """미체결 지정가 매도 주문으로 포지션을 닫아버리면 실제로는 코인을 들고 있는데
    장부상 청산된 것으로 어긋난다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, order_execution_mode="limit")
    position_manager.open_position(strategy["id"], "KRW-BTC", 49_000_000.0, 0.01)
    position = position_manager.get_open_position(strategy["id"])

    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        assert ord_type == "limit"
        assert side == "ask"
        return {"uuid": "uuid-ask-limit", "state": "wait"}

    monkeypatch.setattr(upbit_client, "create_order", fake_create_order)

    order = await order_executor.exit(strategy, position, 50_000_000.0)

    assert order["status"] == "wait"
    assert order["filled_price"] is None
    assert position_manager.get_open_position(strategy["id"]) is not None


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
    orderable = capital / 1.0005  # 매수 수수료 여유(Fix 7)를 뺀 실제 주문가능금액
    # orders 행에도 실제로 낸 주문가(capped_price)가 남아야 감사추적이 맞다
    assert order["requested_price"] == pytest.approx(capped_price)
    sent_volume = float(captured["volume"])
    assert sent_volume == pytest.approx(order_executor._floor_volume(orderable / capped_price))
    assert sent_volume != pytest.approx(order_executor._floor_volume(orderable / 50_000_000.0))
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


def _http_error(status=400):
    request = httpx.Request("DELETE", "https://api.upbit.com/v1/order")
    return httpx.HTTPStatusError(
        str(status), request=request, response=httpx.Response(status, request=request),
    )


async def test_limit_timeout_uses_fast_path_when_cancel_finds_order_already_done(
    monkeypatch, tmp_path,
):
    """조회와 취소 사이에 전량 체결되면 cancel_order가 실패한다. 이때 재조회해서
    done이면 잔량 전환 없이 그대로 확정해야 한다(최종리뷰 Important #8-a)."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, order_execution_mode="limit_timeout")
    calls = {"create": 0, "get": 0}

    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        calls["create"] += 1
        return {"uuid": "uuid-limit", "state": "wait"}

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        calls["get"] += 1
        if calls["get"] == 1:  # 타임아웃 직후: 아직 부분체결
            return {"state": "wait", "executed_volume": "0.004", "remaining_volume": "0.006",
                    "paid_fee": "100.0", "trades": [{"funds": "200000.0"}]}
        # 취소 실패 후 재조회: 그 사이 전량 체결돼 있었다
        return {"state": "done", "executed_volume": "0.01", "remaining_volume": "0",
                "paid_fee": "250.0", "trades": [{"funds": "500000.0"}]}

    async def fake_cancel_order(*, uuid=None, identifier=None, client=None):
        raise _http_error()

    async def fake_sleep(seconds):
        pass

    monkeypatch.setattr(upbit_client, "create_order", fake_create_order)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)
    monkeypatch.setattr(upbit_client, "cancel_order", fake_cancel_order)
    monkeypatch.setattr(order_executor.asyncio, "sleep", fake_sleep)

    order = await order_executor.enter(strategy, 500_000.0, 50_000_000.0)

    assert calls["create"] == 1  # 잔량 시장가 주문을 내지 않았다
    assert order["status"] == "done"
    assert order["filled_volume"] == pytest.approx(0.01)
    assert order["filled_price"] == pytest.approx(500_000.0 / 0.01)
    assert position_manager.get_open_position(strategy["id"]) is not None


async def test_limit_timeout_propagates_cancel_error_when_order_still_not_done(
    monkeypatch, tmp_path,
):
    """취소가 실패했는데 재조회해도 done이 아니면 진짜 장애이므로 삼키면 안 된다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, order_execution_mode="limit_timeout")

    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        return {"uuid": "uuid-limit", "state": "wait"}

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        return {"state": "wait", "executed_volume": "0.004", "remaining_volume": "0.006",
                "paid_fee": "100.0", "trades": [{"funds": "200000.0"}]}

    async def fake_cancel_order(*, uuid=None, identifier=None, client=None):
        raise _http_error(500)

    async def fake_sleep(seconds):
        pass

    monkeypatch.setattr(upbit_client, "create_order", fake_create_order)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)
    monkeypatch.setattr(upbit_client, "cancel_order", fake_cancel_order)
    monkeypatch.setattr(order_executor.asyncio, "sleep", fake_sleep)

    with pytest.raises(httpx.HTTPStatusError):
        await order_executor.enter(strategy, 500_000.0, 50_000_000.0)


async def test_limit_timeout_skips_remainder_below_min_order_amount(monkeypatch, tmp_path):
    """잔량 환산금액이 업비트 최소주문금액(5,000원) 미만이면 전환 주문 자체가 거부되므로
    1차 체결만으로 확정한다(최종리뷰 Important #8-b)."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, order_execution_mode="limit_timeout")
    calls = {"create": 0, "cancel": 0}

    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        calls["create"] += 1
        return {"uuid": "uuid-limit", "state": "wait"}

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        # 잔량 0.00002 × 5천만원 = 1,000원 < 5,000원
        return {"state": "wait", "executed_volume": "0.00998", "remaining_volume": "0.00002",
                "paid_fee": "249.0", "trades": [{"funds": "499000.0"}]}

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

    assert calls["create"] == 1  # 잔량 전환 주문을 내지 않았다
    assert calls["cancel"] == 1
    assert order["status"] == "done"
    assert order["filled_volume"] == pytest.approx(0.00998)
    assert position_manager.get_open_position(strategy["id"]) is not None


async def test_limit_timeout_prices_remainder_from_first_leg_fill(monkeypatch, tmp_path):
    """잔량 매수금액 기준가는 timeout_sec 전의 expected_price가 아니라, 방금 체결된
    1차 체결가를 써야 한다(최종리뷰 Important #8-c)."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, order_execution_mode="limit_timeout")
    calls = {"create": 0}
    captured = {}

    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        calls["create"] += 1
        if calls["create"] == 1:
            return {"uuid": "uuid-limit", "state": "wait"}
        captured.update(price=price)
        return {"uuid": "uuid-market", "state": "done"}

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        if uuid == "uuid-limit":
            # 1차 체결가 5,100만원 (expected_price 5,000만원과 다르다)
            return {"state": "wait", "executed_volume": "0.004", "remaining_volume": "0.006",
                    "paid_fee": "100.0", "trades": [{"funds": "204000.0"}]}
        return {"state": "done", "executed_volume": "0.006", "remaining_volume": "0",
                "paid_fee": "150.0", "trades": [{"funds": "306000.0"}]}

    async def fake_cancel_order(*, uuid=None, identifier=None, client=None):
        return {"uuid": uuid, "state": "cancel"}

    async def fake_sleep(seconds):
        pass

    monkeypatch.setattr(upbit_client, "create_order", fake_create_order)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)
    monkeypatch.setattr(upbit_client, "cancel_order", fake_cancel_order)
    monkeypatch.setattr(order_executor.asyncio, "sleep", fake_sleep)

    await order_executor.enter(strategy, 500_000.0, 50_000_000.0)

    assert float(captured["price"]) == pytest.approx(51_000_000.0 * 0.006)  # 1차 체결가 기준
    assert float(captured["price"]) != pytest.approx(50_000_000.0 * 0.006)  # expected_price 아님


async def test_limit_timeout_keeps_first_leg_when_remainder_order_fails(monkeypatch, tmp_path):
    """잔량 전환 주문이 실패해도 이미 실제로 체결된 1차 부분체결(=보유 코인)을
    통째로 날려선 안 된다(최종리뷰 Important #8-d)."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, order_execution_mode="limit_timeout")
    calls = {"create": 0}

    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        calls["create"] += 1
        if calls["create"] == 1:
            return {"uuid": "uuid-limit", "state": "wait"}
        raise _http_error(400)

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        return {"state": "wait", "executed_volume": "0.004", "remaining_volume": "0.006",
                "paid_fee": "100.0", "trades": [{"funds": "200000.0"}]}

    async def fake_cancel_order(*, uuid=None, identifier=None, client=None):
        return {"uuid": uuid, "state": "cancel"}

    async def fake_sleep(seconds):
        pass

    monkeypatch.setattr(upbit_client, "create_order", fake_create_order)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)
    monkeypatch.setattr(upbit_client, "cancel_order", fake_cancel_order)
    monkeypatch.setattr(order_executor.asyncio, "sleep", fake_sleep)

    order = await order_executor.enter(strategy, 500_000.0, 50_000_000.0)

    assert calls["create"] == 2
    assert order["status"] == "done"
    assert order["filled_volume"] == pytest.approx(0.004)  # 1차 부분체결이 보존된다
    assert order["filled_price"] == pytest.approx(200_000.0 / 0.004)
    position = position_manager.get_open_position(strategy["id"])
    assert position is not None
    assert position["entry_qty"] == pytest.approx(0.004)


async def test_limit_timeout_keeps_first_leg_when_remainder_settles_unfilled(monkeypatch, tmp_path):
    """잔량 시장가 주문이 체결 없이 취소로 확정되면 second_fill["filled_price"]가 None이라
    total_funds 계산에서 None * 0.0 TypeError가 난다(최종리뷰 Critical #1이 지목한 크래시).
    이 경우도 1차 체결만으로 확정해야 한다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, order_execution_mode="limit_timeout")
    calls = {"create": 0}

    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        calls["create"] += 1
        return {"uuid": "uuid-limit" if calls["create"] == 1 else "uuid-market", "state": "wait"}

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        if uuid == "uuid-limit":
            return {"state": "wait", "executed_volume": "0.004", "remaining_volume": "0.006",
                    "paid_fee": "100.0", "trades": [{"funds": "200000.0"}]}
        return {"state": "cancel", "executed_volume": "0", "remaining_volume": "0.006",
                "paid_fee": "0", "trades": []}

    async def fake_cancel_order(*, uuid=None, identifier=None, client=None):
        return {"uuid": uuid, "state": "cancel"}

    async def fake_sleep(seconds):
        pass

    monkeypatch.setattr(upbit_client, "create_order", fake_create_order)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)
    monkeypatch.setattr(upbit_client, "cancel_order", fake_cancel_order)
    monkeypatch.setattr(order_executor.asyncio, "sleep", fake_sleep)

    order = await order_executor.enter(strategy, 500_000.0, 50_000_000.0)

    assert order["status"] == "done"
    assert order["filled_volume"] == pytest.approx(0.004)
    assert position_manager.get_open_position(strategy["id"]) is not None


async def test_limit_timeout_propagates_remainder_failure_when_nothing_filled(
    monkeypatch, tmp_path,
):
    """1차에서 한 주도 안 체결됐다면 보호할 부분체결이 없으므로 에러를 그대로 올린다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, order_execution_mode="limit_timeout")
    calls = {"create": 0}

    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        calls["create"] += 1
        if calls["create"] == 1:
            return {"uuid": "uuid-limit", "state": "wait"}
        raise _http_error(400)

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        return {"state": "wait", "executed_volume": "0", "remaining_volume": "0.01",
                "paid_fee": "0", "trades": []}

    async def fake_cancel_order(*, uuid=None, identifier=None, client=None):
        return {"uuid": uuid, "state": "cancel"}

    async def fake_sleep(seconds):
        pass

    monkeypatch.setattr(upbit_client, "create_order", fake_create_order)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)
    monkeypatch.setattr(upbit_client, "cancel_order", fake_cancel_order)
    monkeypatch.setattr(order_executor.asyncio, "sleep", fake_sleep)

    with pytest.raises(httpx.HTTPStatusError):
        await order_executor.enter(strategy, 500_000.0, 50_000_000.0)


async def test_enter_reserves_fee_headroom_from_capital(monkeypatch, tmp_path):
    """업비트는 매수 시 주문금액+수수료를 묶으므로 capital 전액을 주문에 쓰면
    완전복리 전략이 늘 잔고보다 조금 더 주문하게 된다(최종리뷰 Important #7)."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)
    captured = {}

    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        captured.update(price=price)
        return {"uuid": "uuid-fee", "state": "done"}

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        return {"state": "done", "executed_volume": "0.01", "remaining_volume": "0",
                "paid_fee": "250.0", "trades": [{"funds": "499750.0"}]}

    monkeypatch.setattr(upbit_client, "create_order", fake_create_order)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    await order_executor.enter(strategy, 500_000.0, 50_000_000.0)

    assert float(captured["price"]) == pytest.approx(500_000.0 / 1.0005, rel=1e-9)


async def test_exit_does_not_apply_fee_headroom(monkeypatch, tmp_path):
    """매도는 capital 기반 사이징이 아니라 보유수량 전량이므로 수수료 여유를 빼지 않는다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)
    position_manager.open_position(strategy["id"], "KRW-BTC", 49_000_000.0, 0.01)
    position = position_manager.get_open_position(strategy["id"])
    captured = {}

    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        captured.update(volume=volume)
        return {"uuid": "uuid-fee-ask", "state": "done"}

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        return {"state": "done", "executed_volume": "0.01", "remaining_volume": "0",
                "paid_fee": "250.0", "trades": [{"funds": "500000.0"}]}

    monkeypatch.setattr(upbit_client, "create_order", fake_create_order)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    await order_executor.exit(strategy, position, 50_000_000.0)

    assert float(captured["volume"]) == pytest.approx(0.01)


def test_fmt_avoids_scientific_notation_and_float_noise():
    """bare str()은 작은 값에서 과학적 표기법(업비트가 거부)을, 부동소수점 오차에서는
    불필요한 자리수를 만든다(최종리뷰 Important #5)."""
    assert "e" not in order_executor._fmt(0.00006)
    assert float(order_executor._fmt(0.00006)) == pytest.approx(0.00006)
    assert "e" not in order_executor._fmt(6.66e-05)
    assert float(order_executor._fmt(6.66e-05)) == pytest.approx(6.66e-05)
    assert order_executor._fmt(0.1 + 0.2) == "0.3"
    assert order_executor._fmt(50_000_000.0) == "50000000"  # 큰 정수도 지수표기/.0 없이
    assert order_executor._fmt(0.00000001) == "0.00000001"  # 업비트 최소 단위(1e-8)


async def test_exit_market_formats_small_volume_without_scientific_notation(monkeypatch, tmp_path):
    """str(6.66e-05) == '6.66e-05' 라 업비트가 거부한다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)
    position_manager.open_position(strategy["id"], "KRW-BTC", 49_000_000.0, 6.66e-05)
    position = position_manager.get_open_position(strategy["id"])
    captured = {}

    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        captured.update(volume=volume)
        return {"uuid": "uuid-sci", "state": "done"}

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        return {"state": "done", "executed_volume": "0.0000666", "remaining_volume": "0",
                "paid_fee": "1.0", "trades": [{"funds": "3330.0"}]}

    monkeypatch.setattr(upbit_client, "create_order", fake_create_order)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    await order_executor.exit(strategy, position, 50_000_000.0)

    assert "e" not in captured["volume"]
    assert float(captured["volume"]) == pytest.approx(6.66e-05)


async def test_exit_floors_position_qty_to_eight_decimals(monkeypatch, tmp_path):
    """entry_qty를 그대로 쓰면 8자리 초과 정밀도로 주문이 거부된다(최종리뷰 Important #6)."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)
    position_manager.open_position(strategy["id"], "KRW-BTC", 49_000_000.0, 0.1234567891)
    position = position_manager.get_open_position(strategy["id"])
    captured = {}

    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        captured.update(volume=volume)
        return {"uuid": "uuid-floor", "state": "done"}

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        return {"state": "done", "executed_volume": "0.12345678", "remaining_volume": "0",
                "paid_fee": "250.0", "trades": [{"funds": "6172839.0"}]}

    monkeypatch.setattr(upbit_client, "create_order", fake_create_order)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    await order_executor.exit(strategy, position, 50_000_000.0)

    assert captured["volume"] == "0.12345678"


async def test_limit_timeout_floors_total_volume(monkeypatch, tmp_path):
    """1차+2차 체결수량 단순 합은 0.30000000000000004 같은 값이 된다(최종리뷰 Important #6)."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, order_execution_mode="limit_timeout")
    calls = {"create": 0}

    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        calls["create"] += 1
        return {"uuid": "uuid-limit" if calls["create"] == 1 else "uuid-market", "state": "wait"}

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        if uuid == "uuid-limit":
            return {"state": "wait", "executed_volume": "0.1", "remaining_volume": "0.2",
                    "paid_fee": "100.0", "trades": [{"funds": "5000000.0"}]}
        return {"state": "done", "executed_volume": "0.2", "remaining_volume": "0",
                "paid_fee": "200.0", "trades": [{"funds": "10000000.0"}]}

    async def fake_cancel_order(*, uuid=None, identifier=None, client=None):
        return {"uuid": uuid, "state": "cancel"}

    async def fake_sleep(seconds):
        pass

    monkeypatch.setattr(upbit_client, "create_order", fake_create_order)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)
    monkeypatch.setattr(upbit_client, "cancel_order", fake_cancel_order)
    monkeypatch.setattr(order_executor.asyncio, "sleep", fake_sleep)

    order = await order_executor.enter(strategy, 500_000.0, 50_000_000.0)

    assert order["filled_volume"] == 0.3  # 0.30000000000000004가 아니라 정확히 0.3
    assert order["filled_price"] == pytest.approx(15_000_000.0 / 0.3)


def _order_count(dbm):
    conn = dbm._connect()
    try:
        return conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    finally:
        conn.close()


async def test_enter_rejects_unsupported_mode_before_inserting_order(monkeypatch, tmp_path):
    """모드 검증이 insert_order 뒤에 있으면 잘못 설정된 전략이 status='wait' 고아 행을
    영구히 남긴다. dry_run이어도 검증돼야 한다(최종리뷰 Important #4)."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, order_execution_mode="twap")
    before = _order_count(dbm)

    with pytest.raises(ValueError, match="지원하지 않는 order_execution_mode"):
        await order_executor.enter(strategy, 500_000.0, 50_000_000.0, dry_run=True)

    assert _order_count(dbm) == before


async def test_exit_rejects_unsupported_mode_before_inserting_order(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, order_execution_mode="twap")
    position_manager.open_position(strategy["id"], "KRW-BTC", 49_000_000.0, 0.01)
    position = position_manager.get_open_position(strategy["id"])
    before = _order_count(dbm)

    with pytest.raises(ValueError, match="지원하지 않는 order_execution_mode"):
        await order_executor.exit(strategy, position, 50_000_000.0, dry_run=True)

    assert _order_count(dbm) == before


async def test_enter_rejects_market_capped_without_max_slippage_pct(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(
        dbm, market="KRW-BTC", current_capital=1_000_000.0,
        risk_config_json=json.dumps({
            "order_execution_mode": "market_capped", "max_position_per_market": 1_000_000.0,
        }),
    )
    strategy = dbm.get_live_strategy(strategy_id)
    before = _order_count(dbm)

    with pytest.raises(ValueError, match="max_slippage_pct"):
        await order_executor.enter(strategy, 500_000.0, 50_000_000.0, dry_run=True)

    assert _order_count(dbm) == before


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


async def test_exit_records_signal_as_default_close_reason(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)
    position_manager.open_position(strategy["id"], "KRW-BTC", 49_000_000.0, 0.01)
    position = position_manager.get_open_position(strategy["id"])

    await order_executor.exit(strategy, position, 50_000_000.0, dry_run=True)

    assert dbm.get_position(position["id"])["close_reason"] == "signal"


async def test_exit_records_custom_close_reason(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)
    position_manager.open_position(strategy["id"], "KRW-BTC", 49_000_000.0, 0.01)
    position = position_manager.get_open_position(strategy["id"])

    await order_executor.exit(
        strategy, position, 50_000_000.0, dry_run=True, close_reason="stop_loss_pct",
    )

    assert dbm.get_position(position["id"])["close_reason"] == "stop_loss_pct"


async def test_exit_for_risk_records_trade_result_and_close_reason_on_success(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)
    position_manager.open_position(strategy["id"], "KRW-BTC", 49_000_000.0, 0.01)
    position = position_manager.get_open_position(strategy["id"])
    recorded = {}
    monkeypatch.setattr(
        risk_manager, "record_trade_result",
        lambda sid, pnl, capital_after: recorded.update(sid=sid, pnl=pnl, capital_after=capital_after),
    )

    result = await order_executor.exit_for_risk(
        strategy, position, 50_000_000.0, "stop_loss_pct", dry_run=True,
    )

    assert result["action"] == "exited"
    assert recorded["sid"] == strategy["id"]
    assert dbm.get_position(position["id"])["close_reason"] == "stop_loss_pct"


async def test_exit_for_risk_marks_pending_without_recording_trade_when_not_filled(monkeypatch, tmp_path):
    """order_execution_mode='limit'로 설정된 전략이라도 exit_for_risk()는 항상 market을
    강제한다(4라운드 구조적 수정) — 그래서 이 시나리오는 이제 _run_limit()이 아니라
    _run_market()의 3초 정산 폴링이 타임아웃까지 미확정으로 남는 경로로 "pending"이
    된다. fake_get_order가 계속 wait을 반환해 폴링이 타임아웃까지 소진되게 하고,
    fake_sleep으로 그 3초의 실제 대기를 건너뛴다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, order_execution_mode="limit")
    position_manager.open_position(strategy["id"], "KRW-BTC", 49_000_000.0, 0.01)
    position = position_manager.get_open_position(strategy["id"])
    recorded = {"count": 0}
    monkeypatch.setattr(
        risk_manager, "record_trade_result",
        lambda *a: recorded.__setitem__("count", recorded["count"] + 1),
    )

    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        assert ord_type == "market"  # order_execution_mode='limit'을 무시하고 강제됐다
        return {"uuid": "uuid-risk-pending", "state": "wait"}

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        return {"state": "wait", "executed_volume": "0", "remaining_volume": "0.01",
                "paid_fee": "0", "trades": []}

    async def fake_sleep(seconds):
        pass

    monkeypatch.setattr(upbit_client, "create_order", fake_create_order)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)
    monkeypatch.setattr(order_executor.asyncio, "sleep", fake_sleep)

    result = await order_executor.exit_for_risk(strategy, position, 50_000_000.0, "take_profit_pct")

    assert result["action"] == "pending"
    assert recorded["count"] == 0
    assert position_manager.get_open_position(strategy["id"]) is not None


async def test_exit_for_risk_marks_slippage_exceeded_on_cancel(monkeypatch, tmp_path):
    """order_execution_mode='market_capped'로 설정돼 있어도 exit_for_risk()는 market을
    강제하므로(4라운드 구조적 수정) 실제로는 FOK 취소가 아니라 market 주문이 거래소에서
    체결 없이 cancel로 확정되는 경로를 탄다 — 반환 action 이름("slippage_exceeded")은
    exit_for_risk()가 status=='cancel'인 모든 경우에 공통으로 쓰는 라벨이라 그대로다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, order_execution_mode="market_capped", max_slippage_pct=0.1)
    position_manager.open_position(strategy["id"], "KRW-BTC", 49_000_000.0, 0.01)
    position = position_manager.get_open_position(strategy["id"])
    recorded = {"count": 0}
    monkeypatch.setattr(
        risk_manager, "record_trade_result",
        lambda *a: recorded.__setitem__("count", recorded["count"] + 1),
    )

    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        assert ord_type == "market"  # order_execution_mode='market_capped'을 무시하고 강제됐다
        return {"uuid": "uuid-risk-cancel", "state": "cancel"}

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        return {"state": "cancel", "executed_volume": "0", "remaining_volume": "0.01",
                "paid_fee": "0", "trades": []}

    monkeypatch.setattr(upbit_client, "create_order", fake_create_order)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    result = await order_executor.exit_for_risk(strategy, position, 50_000_000.0, "stop_loss_pct")

    assert result["action"] == "slippage_exceeded"
    assert recorded["count"] == 0
    assert position_manager.get_open_position(strategy["id"]) is not None


@pytest.mark.parametrize("configured_mode,extra_risk_config", [
    ("limit", {}),
    ("limit_timeout", {"order_timeout_sec": 10}),
    ("market_capped", {"max_slippage_pct": 0.1}),
])
async def test_exit_for_risk_always_uses_market_regardless_of_configured_mode(
    monkeypatch, tmp_path, configured_mode, extra_risk_config,
):
    """4라운드 구조적 수정의 핵심 불변조건 — exit_for_risk()는 strategy에 설정된
    order_execution_mode가 무엇이든(limit/limit_timeout/market_capped 전부) 항상
    market으로 강제해야 한다. 실제 order_executor.exit_for_risk() -> exit() 경로를
    그대로 태우고(모드별 분기를 mock으로 우회하지 않음), 거래소에 실제로 나간
    ord_type만 확인한다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, order_execution_mode=configured_mode, **extra_risk_config)
    position_manager.open_position(strategy["id"], "KRW-BTC", 49_000_000.0, 0.01)
    position = position_manager.get_open_position(strategy["id"])
    captured = {}

    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        captured["ord_type"] = ord_type
        captured["time_in_force"] = time_in_force
        return {"uuid": "uuid-forced-market", "state": "done", "executed_volume": "0.01",
                "remaining_volume": "0", "paid_fee": "100.0",
                "trades": [{"funds": "500000.0"}]}

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        return {"state": "done", "executed_volume": "0.01", "remaining_volume": "0",
                "paid_fee": "100.0", "trades": [{"funds": "500000.0"}]}

    monkeypatch.setattr(upbit_client, "create_order", fake_create_order)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    result = await order_executor.exit_for_risk(strategy, position, 50_000_000.0, "stop_loss_pct")

    # order_executor._run_market()은 매도(side='ask')를 "market" ord_type으로 낸다 —
    # configured_mode가 limit이든 limit_timeout이든 market_capped(FOK "limit"+time_in_force)
    # 든 상관없이 이 값이어야 강제가 실제로 적용된 것이다.
    assert captured["ord_type"] == "market"
    assert captured["time_in_force"] is None  # market_capped였다면 "fok"가 찍혔을 값
    assert result["action"] == "exited"


async def test_exit_for_risk_cancels_stale_ask_wait_order_before_placing_market_exit(monkeypatch, tmp_path):
    """5라운드 핵심 수정 — exit_for_risk()는 market 청산 주문을 내기 전에, 같은 전략의
    남아있는 ask wait 행(실제 upbit_uuid가 있는)을 먼저 취소해야 한다. 순서 자체가
    중요하므로(취소가 먼저, 시장가 주문이 나중) 두 fake가 공유하는 call_order 리스트로
    호출 순서를 직접 검증한다 — 둘 다 호출됐다는 것만으로는 부족하다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)
    position_manager.open_position(strategy["id"], "KRW-BTC", 49_000_000.0, 0.01)
    position = position_manager.get_open_position(strategy["id"])

    stale_order_id = dbm.insert_order(
        strategy["id"], None, "KRW-BTC", "ask", "limit", 51_000_000.0, 0.01, 51_000_000.0,
    )
    dbm.update_order_filled(stale_order_id, "stale-ask-uuid", None, None, None, None, "wait")

    call_order = []

    async def fake_cancel_order(*, uuid=None, identifier=None, client=None):
        call_order.append(("cancel", uuid))
        return {"uuid": uuid, "state": "cancel"}

    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        call_order.append(("create", ord_type))
        return {"uuid": "uuid-exit", "state": "done", "executed_volume": "0.01",
                "remaining_volume": "0", "paid_fee": "100.0", "trades": [{"funds": "500000.0"}]}

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        return {"state": "done", "executed_volume": "0.01", "remaining_volume": "0",
                "paid_fee": "100.0", "trades": [{"funds": "500000.0"}]}

    monkeypatch.setattr(upbit_client, "cancel_order", fake_cancel_order)
    monkeypatch.setattr(upbit_client, "create_order", fake_create_order)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    result = await order_executor.exit_for_risk(strategy, position, 50_000_000.0, "stop_loss_pct")

    assert call_order == [("cancel", "stale-ask-uuid"), ("create", "market")]
    assert result["action"] == "exited"


async def test_exit_for_risk_skips_cancel_for_wait_order_without_upbit_uuid(monkeypatch, tmp_path):
    """upbit_uuid가 None인 wait 행은 거래소에 아무 것도 걸려있지 않은 내부 부기용 고아
    행이다 — 취소할 대상이 없으므로 cancel_order를 호출하지 않고 건너뛰어야 하고,
    청산 자체는 정상적으로 진행돼야 한다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)
    position_manager.open_position(strategy["id"], "KRW-BTC", 49_000_000.0, 0.01)
    position = position_manager.get_open_position(strategy["id"])

    # upbit_uuid를 채우지 않은 채 그대로 둔다 — insert_order() 직후 상태와 동일(status='wait',
    # upbit_uuid=NULL).
    dbm.insert_order(strategy["id"], None, "KRW-BTC", "ask", "limit", 51_000_000.0, 0.01, 51_000_000.0)

    cancel_calls = {"n": 0}

    async def fake_cancel_order(*, uuid=None, identifier=None, client=None):
        cancel_calls["n"] += 1
        return {"uuid": uuid, "state": "cancel"}

    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        return {"uuid": "uuid-exit", "state": "done", "executed_volume": "0.01",
                "remaining_volume": "0", "paid_fee": "100.0", "trades": [{"funds": "500000.0"}]}

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        return {"state": "done", "executed_volume": "0.01", "remaining_volume": "0",
                "paid_fee": "100.0", "trades": [{"funds": "500000.0"}]}

    monkeypatch.setattr(upbit_client, "cancel_order", fake_cancel_order)
    monkeypatch.setattr(upbit_client, "create_order", fake_create_order)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    result = await order_executor.exit_for_risk(strategy, position, 50_000_000.0, "stop_loss_pct")

    assert cancel_calls["n"] == 0
    assert result["action"] == "exited"


async def test_exit_for_risk_proceeds_when_stale_cancel_fails(monkeypatch, tmp_path):
    """취소 시점엔 이미 그 주문이 체결/취소돼 있을 수 있다 — Upbit는 이럴 때 취소
    요청 자체를 거부한다(httpx.HTTPStatusError로 나타남, _run_limit_timeout이 이미
    겪은 것과 동일한 경쟁조건). 이 실패가 뒤따르는 실제 청산 시도를 막으면 안 된다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)
    position_manager.open_position(strategy["id"], "KRW-BTC", 49_000_000.0, 0.01)
    position = position_manager.get_open_position(strategy["id"])

    stale_order_id = dbm.insert_order(
        strategy["id"], None, "KRW-BTC", "ask", "limit", 51_000_000.0, 0.01, 51_000_000.0,
    )
    dbm.update_order_filled(stale_order_id, "already-resolved-uuid", None, None, None, None, "wait")

    async def fake_cancel_order(*, uuid=None, identifier=None, client=None):
        request = httpx.Request("DELETE", "https://api.upbit.com/v1/order")
        response = httpx.Response(400, request=request, json={"error": {"message": "order not found"}})
        raise httpx.HTTPStatusError("order not found", request=request, response=response)

    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        return {"uuid": "uuid-exit", "state": "done", "executed_volume": "0.01",
                "remaining_volume": "0", "paid_fee": "100.0", "trades": [{"funds": "500000.0"}]}

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        return {"state": "done", "executed_volume": "0.01", "remaining_volume": "0",
                "paid_fee": "100.0", "trades": [{"funds": "500000.0"}]}

    monkeypatch.setattr(upbit_client, "cancel_order", fake_cancel_order)
    monkeypatch.setattr(upbit_client, "create_order", fake_create_order)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    result = await order_executor.exit_for_risk(strategy, position, 50_000_000.0, "stop_loss_pct")

    assert result["action"] == "exited"


async def test_exit_for_risk_does_not_cancel_bid_side_wait_orders(monkeypatch, tmp_path):
    """매수(bid) wait 행은 원화를 묶어둘 뿐 지금 팔려는 코인 잔고와 무관하다 — 청산 전
    사전 취소 대상이 아니다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)
    position_manager.open_position(strategy["id"], "KRW-BTC", 49_000_000.0, 0.01)
    position = position_manager.get_open_position(strategy["id"])

    bid_order_id = dbm.insert_order(
        strategy["id"], None, "KRW-BTC", "bid", "limit", 49_000_000.0, 0.01, 49_000_000.0,
    )
    dbm.update_order_filled(bid_order_id, "bid-uuid", None, None, None, None, "wait")

    cancel_calls = {"n": 0}

    async def fake_cancel_order(*, uuid=None, identifier=None, client=None):
        cancel_calls["n"] += 1
        return {"uuid": uuid, "state": "cancel"}

    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        return {"uuid": "uuid-exit", "state": "done", "executed_volume": "0.01",
                "remaining_volume": "0", "paid_fee": "100.0", "trades": [{"funds": "500000.0"}]}

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        return {"state": "done", "executed_volume": "0.01", "remaining_volume": "0",
                "paid_fee": "100.0", "trades": [{"funds": "500000.0"}]}

    monkeypatch.setattr(upbit_client, "cancel_order", fake_cancel_order)
    monkeypatch.setattr(upbit_client, "create_order", fake_create_order)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    result = await order_executor.exit_for_risk(strategy, position, 50_000_000.0, "stop_loss_pct")

    assert cancel_calls["n"] == 0
    assert result["action"] == "exited"
