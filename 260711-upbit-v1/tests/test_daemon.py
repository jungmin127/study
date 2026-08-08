import trading.daemon as daemon


def test_poll_interval_sec_scales_with_timeframe():
    assert daemon._poll_interval_sec("minutes1") == 5.0
    assert daemon._poll_interval_sec("minutes3") == 15.0
    assert daemon._poll_interval_sec("minutes5") == 25.0
    assert daemon._poll_interval_sec("minutes15") == 60.0  # 75초 -> 60초 상한
    assert daemon._poll_interval_sec("minutes60") == 60.0  # 300초 -> 60초 상한
    assert daemon._poll_interval_sec("minutes240") == 60.0
    assert daemon._poll_interval_sec("days") == 60.0


import json

import trading.db as db
import trading.order_executor as order_executor
import trading.reconciler as reconciler
import trading.risk_manager as risk_manager
import trading.signal_engine as signal_engine
from tests.trading_db_fixtures import insert_live_strategy


def _fresh_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "trading.db")
    return db


def _no_new_candle_result():
    return {"new_candle": False, "candle_time": None, "buy_signal": None,
            "sell_signal": None, "paused": False, "resumed": False}


def _stop_after_one_tick(dbm, strategy_id):
    """fake_sleep으로 주입 — sleep이 호출되는 시점(한 틱이 끝난 시점)에 상태를
    'stopped'로 바꿔 다음 루프 최상단 체크에서 자연스럽게 종료되게 한다."""
    calls = {"count": 0}

    async def fake_sleep(seconds):
        calls["count"] += 1
        dbm.update_live_strategy_status(strategy_id, "stopped")

    return calls, fake_sleep


async def test_run_strategy_loop_hydrates_state_once_then_processes_one_tick(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm, status="running", timeframe="minutes1")
    hydrate_calls = {"count": 0}

    async def fake_hydrate_state(strategy, *, client=None):
        hydrate_calls["count"] += 1
        return {"synced_wait_orders": 0, "baseline_captured": True}

    monkeypatch.setattr(reconciler, "hydrate_state", fake_hydrate_state)
    monkeypatch.setattr(signal_engine, "evaluate_signals", lambda sid, now=None: _no_new_candle_result())
    calls, fake_sleep = _stop_after_one_tick(dbm, strategy_id)
    monkeypatch.setattr(daemon.asyncio, "sleep", fake_sleep)

    await daemon._run_strategy_loop(strategy_id)

    assert hydrate_calls["count"] == 1
    assert calls["count"] == 1


async def test_run_strategy_loop_calls_handle_signal_result_on_new_candle(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm, status="running", timeframe="minutes1")
    handle_calls = {"count": 0}

    async def fake_hydrate_state(strategy, *, client=None):
        return {"synced_wait_orders": 0, "baseline_captured": True}

    def fake_evaluate_signals(sid, now=None):
        return {"new_candle": True, "candle_time": "2026-08-08T00:00:00+00:00",
                "buy_signal": False, "sell_signal": False,
                "buy_signal_id": "b1", "sell_signal_id": "s1",
                "latest_close": 50000000.0, "paused": False, "resumed": False}

    async def fake_handle_signal_result(sid, result, *, dry_run=False):
        handle_calls["count"] += 1
        return {"buy_action": None, "sell_action": None, "buy_order_id": None, "sell_order_id": None}

    monkeypatch.setattr(reconciler, "hydrate_state", fake_hydrate_state)
    monkeypatch.setattr(signal_engine, "evaluate_signals", fake_evaluate_signals)
    monkeypatch.setattr(order_executor, "handle_signal_result", fake_handle_signal_result)
    calls, fake_sleep = _stop_after_one_tick(dbm, strategy_id)
    monkeypatch.setattr(daemon.asyncio, "sleep", fake_sleep)

    await daemon._run_strategy_loop(strategy_id)

    assert handle_calls["count"] == 1


async def test_run_strategy_loop_checks_circuit_breaker_after_exit(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm, status="running", timeframe="minutes1")
    cb_calls = {"count": 0, "risk_config": None}

    async def fake_hydrate_state(strategy, *, client=None):
        return {"synced_wait_orders": 0, "baseline_captured": True}

    def fake_evaluate_signals(sid, now=None):
        return {"new_candle": True, "candle_time": "2026-08-08T00:00:00+00:00",
                "buy_signal": False, "sell_signal": True,
                "buy_signal_id": "b1", "sell_signal_id": "s1",
                "latest_close": 50000000.0, "paused": False, "resumed": False}

    async def fake_handle_signal_result(sid, result, *, dry_run=False):
        return {"buy_action": None, "sell_action": "exited", "buy_order_id": None, "sell_order_id": "o1"}

    def fake_check_circuit_breaker(sid, risk_config):
        cb_calls["count"] += 1
        cb_calls["risk_config"] = risk_config
        return False

    monkeypatch.setattr(reconciler, "hydrate_state", fake_hydrate_state)
    monkeypatch.setattr(signal_engine, "evaluate_signals", fake_evaluate_signals)
    monkeypatch.setattr(order_executor, "handle_signal_result", fake_handle_signal_result)
    monkeypatch.setattr(risk_manager, "check_circuit_breaker", fake_check_circuit_breaker)
    calls, fake_sleep = _stop_after_one_tick(dbm, strategy_id)
    monkeypatch.setattr(daemon.asyncio, "sleep", fake_sleep)

    await daemon._run_strategy_loop(strategy_id)

    assert cb_calls["count"] == 1
    assert cb_calls["risk_config"] is not None


async def test_run_strategy_loop_skips_circuit_breaker_when_sell_not_exited(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm, status="running", timeframe="minutes1")
    cb_calls = {"count": 0}

    async def fake_hydrate_state(strategy, *, client=None):
        return {"synced_wait_orders": 0, "baseline_captured": True}

    monkeypatch.setattr(reconciler, "hydrate_state", fake_hydrate_state)
    monkeypatch.setattr(signal_engine, "evaluate_signals", lambda sid, now=None: _no_new_candle_result())
    monkeypatch.setattr(risk_manager, "check_circuit_breaker", lambda sid, cfg: cb_calls.__setitem__("count", cb_calls["count"] + 1))
    calls, fake_sleep = _stop_after_one_tick(dbm, strategy_id)
    monkeypatch.setattr(daemon.asyncio, "sleep", fake_sleep)

    await daemon._run_strategy_loop(strategy_id)

    assert cb_calls["count"] == 0


async def test_run_strategy_loop_reconciles_when_interval_elapsed(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm, status="running", timeframe="minutes1")
    reconcile_calls = {"manual": 0, "sync": 0}

    async def fake_hydrate_state(strategy, *, client=None):
        return {"synced_wait_orders": 0, "baseline_captured": True}

    async def fake_check_manual_intervention(strategy, *, client=None):
        reconcile_calls["manual"] += 1
        return {"balance_mismatch": False, "action": "none", "paused": False}

    async def fake_sync_pending_limit_orders(strategy, *, client=None):
        reconcile_calls["sync"] += 1
        return []

    monkeypatch.setattr(reconciler, "hydrate_state", fake_hydrate_state)
    monkeypatch.setattr(reconciler, "check_manual_intervention", fake_check_manual_intervention)
    monkeypatch.setattr(reconciler, "sync_pending_limit_orders", fake_sync_pending_limit_orders)
    monkeypatch.setattr(signal_engine, "evaluate_signals", lambda sid, now=None: _no_new_candle_result())
    # last_reconcile을 과거로 못박아 이번 틱에서 무조건 재확인 주기가 지난 것으로 만든다.
    monkeypatch.setattr(daemon.time, "monotonic", lambda: 10_000.0)
    calls, fake_sleep = _stop_after_one_tick(dbm, strategy_id)
    monkeypatch.setattr(daemon.asyncio, "sleep", fake_sleep)

    await daemon._run_strategy_loop(strategy_id)

    assert reconcile_calls["manual"] == 1
    assert reconcile_calls["sync"] == 1


async def test_run_strategy_loop_returns_immediately_when_strategy_missing(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)

    await daemon._run_strategy_loop("no-such-strategy-id")
    # 예외 없이 조용히 반환하면 성공(hydrate_state 등 아무것도 호출되지 않음)


async def test_run_strategy_loop_logs_and_continues_on_exception(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm, status="running", timeframe="minutes1")

    async def fake_hydrate_state(strategy, *, client=None):
        return {"synced_wait_orders": 0, "baseline_captured": True}

    def fake_evaluate_signals(sid, now=None):
        raise RuntimeError("네트워크 순간 장애")

    monkeypatch.setattr(reconciler, "hydrate_state", fake_hydrate_state)
    monkeypatch.setattr(signal_engine, "evaluate_signals", fake_evaluate_signals)
    calls, fake_sleep = _stop_after_one_tick(dbm, strategy_id)
    monkeypatch.setattr(daemon.asyncio, "sleep", fake_sleep)

    await daemon._run_strategy_loop(strategy_id)  # 예외가 밖으로 전파되면 테스트 실패

    assert calls["count"] == 1
