import asyncio

import pytest

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
import trading.position_manager as position_manager
import trading.reconciler as reconciler
import trading.risk_manager as risk_manager
import trading.signal_engine as signal_engine
import trading.upbit_ws as upbit_ws
import upbit_data_service
from tests.trading_db_fixtures import insert_live_strategy


@pytest.fixture(autouse=True)
def _default_reconciler_mocks(monkeypatch):
    """I2 — last_reconcile=float('-inf') 초기화 때문에 이 파일의 거의 모든 테스트가 첫
    틱에서 reconciler.check_manual_intervention/sync_pending_limit_orders를 실제로
    호출한다. 이 sandbox엔 API 키가 없어 UpbitCredentialsError가 나서(루프의 예외
    흡수 덕에) 눈에 띄지 않았을 뿐, 실제 키가 설정된 머신에서는 이 파일을 돌리는 것만으로
    진짜 업비트 계정에 인증된 호출이 나간다. 안전한 기본 mock을 autouse로 깔아둔다 —
    개별 테스트가 이후 같은 함수에 monkeypatch.setattr을 다시 하면 그쪽이 이긴다."""
    async def fake_check_manual_intervention(strategy, *, own_fills=(), client=None):
        return {"balance_mismatch": False, "action": "none", "paused": False}

    async def fake_sync_pending_limit_orders(strategy, *, client=None):
        return []

    monkeypatch.setattr(reconciler, "check_manual_intervention", fake_check_manual_intervention)
    monkeypatch.setattr(reconciler, "sync_pending_limit_orders", fake_sync_pending_limit_orders)


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
    reconcile_calls = {"manual": 0, "sync": 0, "own_fills": "not-called"}
    sync_result = [{"id": "order-1", "side": "bid", "filled_volume": 0.01}]

    async def fake_hydrate_state(strategy, *, client=None):
        return {"synced_wait_orders": 0, "baseline_captured": True}

    async def fake_check_manual_intervention(strategy, *, own_fills=(), client=None):
        reconcile_calls["manual"] += 1
        reconcile_calls["own_fills"] = own_fills
        return {"balance_mismatch": False, "action": "none", "paused": False}

    async def fake_sync_pending_limit_orders(strategy, *, client=None):
        reconcile_calls["sync"] += 1
        return sync_result

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
    # C1 — sync_pending_limit_orders()의 결과가 check_manual_intervention의 own_fills로
    # 전달돼야 한다. 그렇지 않으면 자체 체결이 수동개입으로 오인된다.
    assert reconcile_calls["own_fills"] == sync_result


async def test_run_strategy_loop_forwards_synced_orders_as_own_fills(monkeypatch, tmp_path):
    """C1 — sync_pending_limit_orders()가 먼저 실행돼 그 반환값이 check_manual_intervention에
    own_fills로 전달돼야 한다. 현재 코드는 두 함수를 독립적으로 호출하고 sync 결과를
    버리므로, 오프라인 중 체결된 plain limit 주문이 다음 사이클에 "설명 안 되는 잔고
    변화"로 오인돼 포지션이 잘못 강제조정되고 스퓨리어스 정지가 걸린다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm, status="running", timeframe="minutes1")
    captured = {"own_fills": "not-called"}
    synced_orders = [{"id": "order-1", "side": "bid", "filled_volume": 0.01}]

    async def fake_hydrate_state(strategy, *, client=None):
        return {"synced_wait_orders": 0, "baseline_captured": True}

    async def fake_sync_pending_limit_orders(strategy, *, client=None):
        return synced_orders

    async def fake_check_manual_intervention(strategy, *, own_fills=(), client=None):
        captured["own_fills"] = own_fills
        return {"balance_mismatch": False, "action": "none", "paused": False}

    monkeypatch.setattr(reconciler, "hydrate_state", fake_hydrate_state)
    monkeypatch.setattr(reconciler, "sync_pending_limit_orders", fake_sync_pending_limit_orders)
    monkeypatch.setattr(reconciler, "check_manual_intervention", fake_check_manual_intervention)
    monkeypatch.setattr(signal_engine, "evaluate_signals", lambda sid, now=None: _no_new_candle_result())
    monkeypatch.setattr(daemon.time, "monotonic", lambda: 10_000.0)
    calls, fake_sleep = _stop_after_one_tick(dbm, strategy_id)
    monkeypatch.setattr(daemon.asyncio, "sleep", fake_sleep)

    await daemon._run_strategy_loop(strategy_id)

    assert captured["own_fills"] == synced_orders


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


async def test_run_strategy_loop_returns_when_hydrate_state_fails(monkeypatch, tmp_path, caplog):
    """I1 — hydrate_state()가 try/except 밖에 있으면 일시적 네트워크 장애가 태스크 전체를
    로그 한 줄 없이 죽인다. 실패 시 예외를 흡수하고 로그를 남긴 뒤 조용히 반환해야 한다
    (다음 태스크셋 매니저 스캔 주기에 재시도됨)."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm, status="running", timeframe="minutes1")

    async def failing_hydrate_state(strategy, *, client=None):
        raise RuntimeError("network hiccup")

    monkeypatch.setattr(reconciler, "hydrate_state", failing_hydrate_state)
    evaluate_calls = {"count": 0}

    def fake_evaluate_signals(sid, now=None):
        evaluate_calls["count"] += 1
        return _no_new_candle_result()

    monkeypatch.setattr(signal_engine, "evaluate_signals", fake_evaluate_signals)

    with caplog.at_level("ERROR", logger="trading.daemon"):
        await daemon._run_strategy_loop(strategy_id)  # 예외가 밖으로 전파되면 테스트 실패

    assert evaluate_calls["count"] == 0  # hydrate_state 실패 시 루프에 진입조차 하지 않음
    assert any("hydrate_state" in record.message or "hydrate" in record.message.lower()
               or strategy_id in record.message for record in caplog.records)


async def test_run_strategy_loop_reconciles_even_when_signal_processing_raises(monkeypatch, tmp_path):
    """I3 — 신호처리(evaluate_signals/handle_signal_result)가 매 틱 예외를 던지는 상황에서도
    reconcile 워치독은 별도 try/except여야 계속 돌아간다. 지금은 한 try 안에 다 있어서
    신호처리가 죽으면 reconcile도 같이 건너뛴다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm, status="running", timeframe="minutes1")
    reconcile_calls = {"count": 0}

    async def fake_hydrate_state(strategy, *, client=None):
        return {"synced_wait_orders": 0, "baseline_captured": True}

    def failing_evaluate_signals(sid, now=None):
        raise RuntimeError("신호처리 깨짐")

    async def fake_check_manual_intervention(strategy, *, own_fills=(), client=None):
        reconcile_calls["count"] += 1
        return {"balance_mismatch": False, "action": "none", "paused": False}

    async def fake_sync_pending_limit_orders(strategy, *, client=None):
        return []

    monkeypatch.setattr(reconciler, "hydrate_state", fake_hydrate_state)
    monkeypatch.setattr(signal_engine, "evaluate_signals", failing_evaluate_signals)
    monkeypatch.setattr(reconciler, "check_manual_intervention", fake_check_manual_intervention)
    monkeypatch.setattr(reconciler, "sync_pending_limit_orders", fake_sync_pending_limit_orders)
    monkeypatch.setattr(daemon.time, "monotonic", lambda: 10_000.0)
    calls, fake_sleep = _stop_after_one_tick(dbm, strategy_id)
    monkeypatch.setattr(daemon.asyncio, "sleep", fake_sleep)

    await daemon._run_strategy_loop(strategy_id)  # 예외가 밖으로 전파되면 테스트 실패

    assert reconcile_calls["count"] == 1


async def test_run_strategy_loop_throttles_reconcile_retries_even_on_failure(monkeypatch, tmp_path):
    """M3 — reconcile 호출 자체가 실패해도 last_reconcile은 시도 직전에 갱신돼야 한다.
    그렇지 않으면 고장난 reconciler가 20초 상한 대신 매 틱(5~60초)마다 재시도된다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm, status="running", timeframe="minutes1")
    reconcile_calls = {"count": 0}

    async def fake_hydrate_state(strategy, *, client=None):
        return {"synced_wait_orders": 0, "baseline_captured": True}

    async def failing_sync_pending_limit_orders(strategy, *, client=None):
        reconcile_calls["count"] += 1
        raise RuntimeError("reconcile 실패")

    monkeypatch.setattr(reconciler, "hydrate_state", fake_hydrate_state)
    monkeypatch.setattr(signal_engine, "evaluate_signals", lambda sid, now=None: _no_new_candle_result())
    monkeypatch.setattr(reconciler, "sync_pending_limit_orders", failing_sync_pending_limit_orders)
    # time.monotonic()을 상수로 고정 — 실제 시간 흐름과 무관하게 두 번째 틱에서도
    # "20초가 지났는지"를 last_reconcile 갱신 여부만으로 판별하게 만든다.
    monkeypatch.setattr(daemon.time, "monotonic", lambda: 10_000.0)

    tick_count = {"n": 0}

    async def fake_sleep(seconds):
        tick_count["n"] += 1
        if tick_count["n"] >= 2:
            dbm.update_live_strategy_status(strategy_id, "stopped")

    monkeypatch.setattr(daemon.asyncio, "sleep", fake_sleep)

    await daemon._run_strategy_loop(strategy_id)

    assert tick_count["n"] == 2
    # 두 번째 틱에서는 last_reconcile이 이미 갱신돼 있어 재시도하지 않아야 한다.
    assert reconcile_calls["count"] == 1


async def test_run_strategy_loop_survives_get_live_strategy_exception_mid_loop(monkeypatch, tmp_path, caplog):
    """루프 최상단(while True: 바로 아래)의 db.get_live_strategy() 재조회는 신호처리/
    reconcile 블록과 달리 try/except 밖에 있었다 — 락 경합 같은 일시적 DB 오류가 나면
    로그 한 줄 없이 태스크 전체가 죽는다(코드 리뷰 지적). 실패 시 이전 strategy 값으로
    이번 틱을 마저 진행하고 다음 틱에 다시 시도해야 한다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm, status="running", timeframe="minutes1")

    real_get_live_strategy = db.get_live_strategy
    call_count = {"n": 0}

    def flaky_get_live_strategy(sid):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("일시적 DB 락 경합")
        return real_get_live_strategy(sid)

    monkeypatch.setattr(db, "get_live_strategy", flaky_get_live_strategy)

    async def fake_hydrate_state(strategy, *, client=None):
        return {"synced_wait_orders": 0, "baseline_captured": True}

    async def fake_check_manual_intervention(strategy, *, own_fills=(), client=None):
        return {"balance_mismatch": False, "action": "none", "paused": False}

    async def fake_sync_pending_limit_orders(strategy, *, client=None):
        return []

    monkeypatch.setattr(reconciler, "hydrate_state", fake_hydrate_state)
    monkeypatch.setattr(reconciler, "check_manual_intervention", fake_check_manual_intervention)
    monkeypatch.setattr(reconciler, "sync_pending_limit_orders", fake_sync_pending_limit_orders)
    monkeypatch.setattr(signal_engine, "evaluate_signals", lambda sid, now=None: _no_new_candle_result())
    calls, fake_sleep = _stop_after_one_tick(dbm, strategy_id)
    monkeypatch.setattr(daemon.asyncio, "sleep", fake_sleep)

    with caplog.at_level("ERROR", logger="trading.daemon"):
        await daemon._run_strategy_loop(strategy_id)  # 예외가 밖으로 전파되면 테스트 실패

    assert calls["count"] == 1  # 예외에도 불구하고 이번 틱을 마저 처리하고 sleep까지 도달
    assert any(strategy_id in record.message for record in caplog.records)


async def test_run_strategy_loop_serializes_signal_processing_through_shared_lock(monkeypatch, tmp_path):
    """전략별 lock을 다른 코루틴이 이미 쥐고 있으면, _run_strategy_loop의
    handle_signal_result 호출은 그 lock이 풀릴 때까지 기다려야 한다(⑤-4c 설계 스펙
    결정4 — _run_risk_exit_loop와 주문실행이 겹치지 않게 하는 핵심 계약)."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm, status="running", timeframe="minutes1")
    lock = asyncio.Lock()
    events = []
    real_sleep = asyncio.sleep  # daemon.asyncio.sleep을 아래서 monkeypatch하기 전에 붙잡아둔다

    async def fake_hydrate_state(strategy, *, client=None):
        return {"synced_wait_orders": 0, "baseline_captured": True}

    def fake_evaluate_signals(sid, now=None):
        return {"new_candle": True, "candle_time": "2026-08-08T00:00:00+00:00",
                "buy_signal": False, "sell_signal": False,
                "buy_signal_id": "b1", "sell_signal_id": "s1",
                "latest_close": 50000000.0, "paused": False, "resumed": False}

    async def fake_handle_signal_result(sid, result, *, dry_run=False):
        events.append("handle_signal_result")
        return {"buy_action": None, "sell_action": None, "buy_order_id": None, "sell_order_id": None}

    async def fake_check_manual_intervention(strategy, *, own_fills=(), client=None):
        return {"balance_mismatch": False, "action": "none", "paused": False}

    async def fake_sync_pending_limit_orders(strategy, *, client=None):
        return []

    monkeypatch.setattr(reconciler, "hydrate_state", fake_hydrate_state)
    monkeypatch.setattr(signal_engine, "evaluate_signals", fake_evaluate_signals)
    monkeypatch.setattr(order_executor, "handle_signal_result", fake_handle_signal_result)
    monkeypatch.setattr(reconciler, "check_manual_intervention", fake_check_manual_intervention)
    monkeypatch.setattr(reconciler, "sync_pending_limit_orders", fake_sync_pending_limit_orders)
    calls, fake_sleep = _stop_after_one_tick(dbm, strategy_id)
    monkeypatch.setattr(daemon.asyncio, "sleep", fake_sleep)

    async with lock:
        events.append("lock_held_by_other")
        loop_task = asyncio.create_task(daemon._run_strategy_loop(strategy_id, lock))
        # _run_strategy_loop가 lock 획득을 시도하다 블록되게 한 틱 양보한다. daemon.asyncio.sleep은
        # 위에서 fake_sleep으로 전역 monkeypatch됐으므로(daemon.asyncio는 top-level import asyncio와
        # 동일한 모듈 객체), 여기서 bare asyncio.sleep(0)을 쓰면 그 fake_sleep이 호출돼 전략 상태가
        # 여기서 조기에 'stopped'로 바뀌어버린다 — 반드시 monkeypatch 전에 붙잡아둔 real_sleep을 쓴다.
        await real_sleep(0)
        assert "handle_signal_result" not in events
        events.append("lock_released_by_other")

    await loop_task

    assert events == ["lock_held_by_other", "lock_released_by_other", "handle_signal_result"]


async def test_task_set_manager_creates_task_for_new_strategy(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm, status="running")
    started = {"ids": []}
    # daemon.asyncio는 top-level `import asyncio`와 동일한 모듈 객체라서
    # monkeypatch.setattr(daemon.asyncio, "sleep", ...)는 asyncio.sleep 자체를
    # 전역으로 바꿔버린다. 아래에서 실제 스케줄링 양보용으로 쓸 원본 sleep을
    # 패치 전에 미리 붙잡아둔다.
    real_sleep = asyncio.sleep

    async def fake_run_strategy_loop(sid, lock=None):
        started["ids"].append(sid)
        await asyncio.sleep(3600)  # 태스크가 살아있는 채로 유지(취소되기 전까지)

    async def fake_run_risk_exit_loop(sid, lock=None):
        await asyncio.sleep(3600)

    monkeypatch.setattr(daemon, "_run_strategy_loop", fake_run_strategy_loop)
    monkeypatch.setattr(daemon, "_run_risk_exit_loop", fake_run_risk_exit_loop)

    async def stop_after_one_scan(seconds):
        raise asyncio.CancelledError()

    monkeypatch.setattr(daemon.asyncio, "sleep", stop_after_one_scan)

    with pytest.raises(asyncio.CancelledError):
        await daemon._task_set_manager_loop()
    await real_sleep(0)  # 생성된 태스크가 실제로 스케줄되게 한 틱 양보

    assert started["ids"] == [strategy_id]


async def test_task_set_manager_cancels_task_for_removed_strategy(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm, status="running")
    cancelled = {"count": 0}
    # 위 테스트와 동일한 이유로 패치 전 원본 sleep을 붙잡아둔다.
    real_sleep = asyncio.sleep

    async def fake_run_strategy_loop(sid, lock=None):
        # asyncio.sleep(3600)이 아니라 절대 set되지 않는 Event를 기다리게 한다.
        # daemon.asyncio.sleep은 아래에서 scan 카운팅용으로 전역 monkeypatch되므로,
        # 이 서브 태스크가 asyncio.sleep을 쓰면 매니저 루프의 sleep 호출과 같은
        # 공유 카운터를 건드리게 되어 실제 .cancel() 호출 없이도 자기 자신의
        # CancelledError를 유발할 수 있다(공유 mock의 부작용). Event().wait()는
        # asyncio.sleep을 전혀 경유하지 않으므로, 이 태스크를 깨우는 유일한 방법은
        # 진짜 task.cancel() 호출뿐이다.
        never_set = asyncio.Event()
        try:
            await never_set.wait()
        except asyncio.CancelledError:
            cancelled["count"] += 1
            raise

    async def fake_run_risk_exit_loop(sid, lock=None):
        never_set = asyncio.Event()
        await never_set.wait()

    monkeypatch.setattr(daemon, "_run_strategy_loop", fake_run_strategy_loop)
    monkeypatch.setattr(daemon, "_run_risk_exit_loop", fake_run_risk_exit_loop)

    scan_count = {"n": 0}

    async def fake_sleep(seconds):
        # 이제 이 mock은 매니저 루프 자신의 sleep(_TASK_REFRESH_INTERVAL_SEC) 호출만
        # 카운팅한다(서브 태스크는 Event.wait()를 쓰므로 여기 안 걸림).
        scan_count["n"] += 1
        if scan_count["n"] == 1:
            # scan 1: 전략을 로드해 태스크를 생성한 직후. 여기서 상태를 stopped로
            # 바꾸고, 방금 생성된 태스크가 실제로 스케줄되어 never_set.wait()에서
            # 대기 상태에 들어가도록 한 틱 양보한다(그래야 다음 scan에서 걸리는
            # 진짜 .cancel() 호출이 의미 있는 대상을 취소하게 된다).
            dbm.update_live_strategy_status(strategy_id, "stopped")
            await real_sleep(0)
        else:
            # scan 2 진입 전: 매니저 루프가 이미 tasks[strategy_id].cancel()을
            # 호출한 뒤 여기 도달한다. 매니저 루프 자체를 멈추기 위해 여기서
            # CancelledError를 던진다.
            raise asyncio.CancelledError()

    monkeypatch.setattr(daemon.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await daemon._task_set_manager_loop()
    # 위에서 걸린 진짜 task.cancel() 호출이 never_set.wait() 내부까지 전파되어
    # except CancelledError 블록이 실행되게 한 틱 더 양보한다.
    await real_sleep(0)

    assert cancelled["count"] == 1


async def test_task_set_manager_survives_exception_from_scan(monkeypatch, tmp_path, caplog):
    """스캔 본문(db.list_active_strategies() 조회 + 태스크 생성/취소)이 try/except 밖에
    있었다 — 일시적 DB 오류가 나면 이 루프 자체가 죽고, main()의 asyncio.gather() 밖으로
    전파되어 데몬 프로세스 전체가 죽는다(코드 리뷰 지적, 설계 스펙 '에러 처리' 절 위반).
    실패해도 로그만 남기고 다음 스캔 주기(20초)에 재시도해야 한다."""
    _fresh_db(monkeypatch, tmp_path)

    def failing_list_active_strategies():
        raise RuntimeError("일시적 DB 오류")

    monkeypatch.setattr(db, "list_active_strategies", failing_list_active_strategies)

    async def stop_after_one_scan(seconds):
        raise asyncio.CancelledError()

    monkeypatch.setattr(daemon.asyncio, "sleep", stop_after_one_scan)

    with caplog.at_level("ERROR", logger="trading.daemon"), pytest.raises(asyncio.CancelledError):
        await daemon._task_set_manager_loop()  # RuntimeError가 밖으로 새면 테스트 실패

    assert len(caplog.records) == 1


async def test_ntp_check_loop_logs_warning_when_drift_exceeds_threshold(monkeypatch, caplog):
    monkeypatch.setattr(upbit_data_service, "get_server_time_offset_sec", lambda: 1.2)

    async def stop_after_one_check(seconds):
        raise asyncio.CancelledError()

    monkeypatch.setattr(daemon.asyncio, "sleep", stop_after_one_check)

    with caplog.at_level("WARNING"), pytest.raises(asyncio.CancelledError):
        await daemon._run_ntp_check_loop()

    assert any("1.2" in r.message or "1.20" in r.message for r in caplog.records)


async def test_ntp_check_loop_silent_when_drift_within_threshold(monkeypatch, caplog):
    monkeypatch.setattr(upbit_data_service, "get_server_time_offset_sec", lambda: 0.05)

    async def stop_after_one_check(seconds):
        raise asyncio.CancelledError()

    monkeypatch.setattr(daemon.asyncio, "sleep", stop_after_one_check)

    with caplog.at_level("WARNING"), pytest.raises(asyncio.CancelledError):
        await daemon._run_ntp_check_loop()

    assert len(caplog.records) == 0


async def test_ntp_check_loop_survives_exception(monkeypatch):
    def fake_offset():
        raise RuntimeError("네트워크 순간 장애")

    monkeypatch.setattr(upbit_data_service, "get_server_time_offset_sec", fake_offset)

    async def stop_after_one_check(seconds):
        raise asyncio.CancelledError()

    monkeypatch.setattr(daemon.asyncio, "sleep", stop_after_one_check)

    with pytest.raises(asyncio.CancelledError):
        await daemon._run_ntp_check_loop()  # RuntimeError가 밖으로 새면 테스트 실패


async def test_run_risk_exit_loop_skips_ws_subscription_without_risk_conditions(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(
        dbm, status="running",
        sell_conditions_json=json.dumps({"type": "OR", "conditions": [
            {"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 70},
        ]}),
    )
    calls = {"n": 0}

    def fake_stream_ticker(markets):
        # 위험조건 없는 전략은 이 함수가 아예 호출되면 안 된다(호출되면 결정7 위반).
        # 호출됐을 때 억지로 예외를 던지는 대신 카운트만 남긴다 — async for가 이 반환값을
        # 바로 순회하려 들면 그 자체로 TypeError가 나서 테스트가 실패하므로 충분하다.
        calls["n"] += 1

    monkeypatch.setattr(upbit_ws, "stream_ticker", fake_stream_ticker)

    await daemon._run_risk_exit_loop(strategy_id)

    assert calls["n"] == 0


async def test_run_risk_exit_loop_returns_immediately_when_strategy_missing(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)

    await daemon._run_risk_exit_loop("no-such-strategy-id")
    # 예외 없이 조용히 반환하면 성공


async def test_run_risk_exit_loop_skips_tick_without_open_position(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(
        dbm, status="running", market="KRW-BTC",
        sell_conditions_json=json.dumps({"type": "OR", "conditions": [
            {"indicator": "STOP_LOSS_PCT", "params": {}, "operator": "<=", "threshold": -5},
        ]}),
    )
    exit_calls = {"n": 0}

    async def fake_stream_ticker(markets):
        assert markets == ["KRW-BTC"]
        yield {"type": "ticker", "code": "KRW-BTC", "trade_price": 47_000_000.0}

    async def fake_exit_for_risk(strategy, position, price, reason, **kwargs):
        exit_calls["n"] += 1
        return {"action": "exited", "order_id": "o1"}

    monkeypatch.setattr(upbit_ws, "stream_ticker", fake_stream_ticker)
    monkeypatch.setattr(order_executor, "exit_for_risk", fake_exit_for_risk)

    await daemon._run_risk_exit_loop(strategy_id)

    assert exit_calls["n"] == 0


async def test_run_risk_exit_loop_skips_tick_within_thresholds(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(
        dbm, status="running", market="KRW-BTC",
        sell_conditions_json=json.dumps({"type": "OR", "conditions": [
            {"indicator": "STOP_LOSS_PCT", "params": {}, "operator": "<=", "threshold": -5},
        ]}),
    )
    position_manager.open_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)
    exit_calls = {"n": 0}

    async def fake_stream_ticker(markets):
        yield {"type": "ticker", "code": "KRW-BTC", "trade_price": 49_000_000.0}  # -2%, 손절선(-5%) 안 뚫림

    async def fake_exit_for_risk(strategy, position, price, reason, **kwargs):
        exit_calls["n"] += 1
        return {"action": "exited", "order_id": "o1"}

    monkeypatch.setattr(upbit_ws, "stream_ticker", fake_stream_ticker)
    monkeypatch.setattr(order_executor, "exit_for_risk", fake_exit_for_risk)

    await daemon._run_risk_exit_loop(strategy_id)

    assert exit_calls["n"] == 0


async def test_run_risk_exit_loop_triggers_exit_for_risk_when_stop_loss_breached(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(
        dbm, status="running", market="KRW-BTC",
        sell_conditions_json=json.dumps({"type": "OR", "conditions": [
            {"indicator": "STOP_LOSS_PCT", "params": {}, "operator": "<=", "threshold": -5},
        ]}),
    )
    position_manager.open_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)
    captured = {}
    cb_calls = {"n": 0}

    async def fake_stream_ticker(markets):
        yield {"type": "ticker", "code": "KRW-BTC", "trade_price": 47_000_000.0}  # -6%, 손절선(-5%) 뚫림

    async def fake_exit_for_risk(strategy, position, price, reason, **kwargs):
        captured.update(strategy_id=strategy["id"], price=price, reason=reason)
        return {"action": "exited", "order_id": "o1"}

    monkeypatch.setattr(upbit_ws, "stream_ticker", fake_stream_ticker)
    monkeypatch.setattr(order_executor, "exit_for_risk", fake_exit_for_risk)
    monkeypatch.setattr(
        risk_manager, "check_circuit_breaker",
        lambda sid, cfg: cb_calls.__setitem__("n", cb_calls["n"] + 1),
    )

    await daemon._run_risk_exit_loop(strategy_id)

    assert captured["strategy_id"] == strategy_id
    assert captured["price"] == 47_000_000.0
    assert captured["reason"] == "stop_loss_pct"  # matched_risk_exit_indicator 반환값을 소문자로
    assert cb_calls["n"] == 1  # action=="exited"이면 서킷브레이커 판정도 호출돼야 한다


async def test_run_risk_exit_loop_skips_circuit_breaker_when_exit_pending(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(
        dbm, status="running", market="KRW-BTC",
        sell_conditions_json=json.dumps({"type": "OR", "conditions": [
            {"indicator": "TAKE_PROFIT_PCT", "params": {}, "operator": ">=", "threshold": 10},
        ]}),
    )
    position_manager.open_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)
    cb_calls = {"n": 0}

    async def fake_stream_ticker(markets):
        yield {"type": "ticker", "code": "KRW-BTC", "trade_price": 56_000_000.0}  # +12%, 익절선(10%) 뚫림

    async def fake_exit_for_risk(strategy, position, price, reason, **kwargs):
        return {"action": "pending", "order_id": "o1"}

    monkeypatch.setattr(upbit_ws, "stream_ticker", fake_stream_ticker)
    monkeypatch.setattr(order_executor, "exit_for_risk", fake_exit_for_risk)
    monkeypatch.setattr(
        risk_manager, "check_circuit_breaker",
        lambda sid, cfg: cb_calls.__setitem__("n", cb_calls["n"] + 1),
    )

    await daemon._run_risk_exit_loop(strategy_id)

    assert cb_calls["n"] == 0


async def test_run_risk_exit_loop_logs_and_continues_on_exception(monkeypatch, tmp_path):
    """C1의 쿨다운 가드는 exit_for_risk()가 예외를 던진 경우에도 last_risk_exit_attempt를
    갱신한다(실패해도 즉시 재시도하지 않기 위함) — 그래서 이 테스트는 두 번째 tick이
    쿨다운 밖에서 벌어지도록 daemon.time.monotonic()을 patch한다. 그러지 않으면 real
    time으로는 두 tick 사이에 30초가 흐르지 않아 두 번째 시도 자체가 쿨다운에 막혀버려,
    이 테스트가 원래 검증하려는 것(신호처리 예외 후에도 루프가 죽지 않고 계속 tick을
    처리한다)을 볼 수 없다.

    time.monotonic은 daemon.py가 `import time`으로 가져온 것과 동일한 모듈 객체라서,
    이걸 monkeypatch하면 이 프로세스 전체 — 이 테스트를 실행 중인 asyncio 이벤트
    루프 자신의 내부 스케줄링(sleep(0) 처리 등)까지 포함해서 — 가 그 함수를 쓰게 된다.
    고정 개수의 iter([...])를 쓰면 daemon 코드가 아닌 asyncio 내부 호출만으로도
    StopIteration이 나 버린다(실제로 재현됨) — 그래서 "소비되면 고갈되는" 값 대신
    "명시적으로 올릴 때만 바뀌는" 가변 클록을 쓴다. tick 2개 사이에 fake_stream_ticker가
    직접 클록을 100초 앞으로 밀어 쿨다운을 피하게 한다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(
        dbm, status="running", market="KRW-BTC",
        sell_conditions_json=json.dumps({"type": "OR", "conditions": [
            {"indicator": "STOP_LOSS_PCT", "params": {}, "operator": "<=", "threshold": -5},
        ]}),
    )
    position_manager.open_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)
    processed = {"n": 0}
    clock = {"value": 0.0}
    monkeypatch.setattr(daemon.time, "monotonic", lambda: clock["value"])

    async def fake_stream_ticker(markets):
        yield {"type": "ticker", "code": "KRW-BTC", "trade_price": 47_000_000.0}
        clock["value"] += 100.0  # 쿨다운(30초)보다 긴 시간이 흐른 것으로 만든다.
        yield {"type": "ticker", "code": "KRW-BTC", "trade_price": 47_000_000.0}

    async def failing_exit_for_risk(strategy, position, price, reason, **kwargs):
        processed["n"] += 1
        raise RuntimeError("네트워크 순간 장애")

    monkeypatch.setattr(upbit_ws, "stream_ticker", fake_stream_ticker)
    monkeypatch.setattr(order_executor, "exit_for_risk", failing_exit_for_risk)

    await daemon._run_risk_exit_loop(strategy_id)  # 예외가 밖으로 전파되면 테스트 실패

    assert processed["n"] == 2  # 첫 tick 실패 후에도 두 번째 tick을 계속 처리


async def test_run_risk_exit_loop_stops_retrying_once_wait_order_exists(monkeypatch, tmp_path):
    """C1 가드 ① — exit_for_risk()가 "pending"(리밋 주문이 아직 열려있음)을 반환하면
    포지션은 여전히 breach 상태로 남아 다음 tick도 그대로 같은 트리거 조건을 만족한다.
    list_wait_orders()로 진행 중인 청산 시도를 감지해 두 번째 이후 tick은 실주문을
    내지 않고 건너뛰어야 한다. 쿨다운 가드가 개입해 이 테스트를 오염시키지 않도록
    time.monotonic()을 매 호출 충분히 벌려 쿨다운은 항상 통과시킨다 — 이 테스트는
    순수하게 list_wait_orders 가드만 검증한다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(
        dbm, status="running", market="KRW-BTC",
        sell_conditions_json=json.dumps({"type": "OR", "conditions": [
            {"indicator": "STOP_LOSS_PCT", "params": {}, "operator": "<=", "threshold": -5},
        ]}),
    )
    position_manager.open_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)
    exit_calls = {"n": 0}
    clock = {"value": 0.0}
    # 쿨다운 가드가 개입해 이 테스트를 오염시키지 않도록 daemon.time.monotonic()을
    # 매 tick 전에 넉넉히 앞당긴다 — 이 테스트는 순수하게 list_wait_orders 가드만
    # 검증한다. iter([...]) 같은 "소비하면 고갈되는" 값은 쓰지 않는다 — daemon.time은
    # 프로세스 전역 time 모듈과 동일 객체라서, 이 이벤트 루프 자신의 내부 스케줄링도
    # 같은 함수를 거치므로 고정 개수 iterator는 daemon 코드가 부르기도 전에 고갈된다
    # (직접 재현됨).
    monkeypatch.setattr(daemon.time, "monotonic", lambda: clock["value"])

    async def fake_stream_ticker(markets):
        for _ in range(5):
            clock["value"] += 100.0
            yield {"type": "ticker", "code": "KRW-BTC", "trade_price": 47_000_000.0}

    async def fake_exit_for_risk(strategy, position, price, reason, **kwargs):
        exit_calls["n"] += 1
        return {"action": "pending", "order_id": "o1"}

    def fake_list_wait_orders(live_strategy_id, order_type=None):
        # 첫 시도 전에는 대기 주문이 없다가, 그 이후엔 pending 리밋 주문이 계속
        # 남아있는 것처럼 흉내낸다.
        return [{"id": "o1", "status": "wait"}] if exit_calls["n"] > 0 else []

    monkeypatch.setattr(upbit_ws, "stream_ticker", fake_stream_ticker)
    monkeypatch.setattr(order_executor, "exit_for_risk", fake_exit_for_risk)
    monkeypatch.setattr(db, "list_wait_orders", fake_list_wait_orders)

    await daemon._run_risk_exit_loop(strategy_id)

    assert exit_calls["n"] == 1


async def test_run_risk_exit_loop_cooldown_blocks_retry_after_slippage_exceeded(monkeypatch, tmp_path):
    """C1 가드 ② — exit_for_risk()가 "slippage_exceeded"(FOK 즉시 취소, 대기 주문을
    남기지 않음)를 반환하면 가드 ①(list_wait_orders)은 무력하다. 쿨다운으로 재시도
    간격을 강제해야 한다. list_wait_orders는 실제 빈 DB 결과를 그대로 쓴다 — 이 테스트는
    순수하게 쿨다운 가드만 검증한다. time.monotonic()은 patch하지 않는다 — 세 tick이
    실제 시간으로 사실상 동시에(수 밀리초 안에) 흘러가므로 real time만으로도 쿨다운
    윈도(30초) 안에 확실히 머문다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(
        dbm, status="running", market="KRW-BTC",
        sell_conditions_json=json.dumps({"type": "OR", "conditions": [
            {"indicator": "STOP_LOSS_PCT", "params": {}, "operator": "<=", "threshold": -5},
        ]}),
    )
    position_manager.open_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)
    exit_calls = {"n": 0}

    async def fake_stream_ticker(markets):
        for _ in range(3):
            yield {"type": "ticker", "code": "KRW-BTC", "trade_price": 47_000_000.0}

    async def fake_exit_for_risk(strategy, position, price, reason, **kwargs):
        exit_calls["n"] += 1
        return {"action": "slippage_exceeded", "order_id": None}

    monkeypatch.setattr(upbit_ws, "stream_ticker", fake_stream_ticker)
    monkeypatch.setattr(order_executor, "exit_for_risk", fake_exit_for_risk)

    await daemon._run_risk_exit_loop(strategy_id)

    assert exit_calls["n"] == 1


async def test_run_risk_exit_loop_skips_trigger_when_strategy_paused(monkeypatch, tmp_path):
    """I3(사용자 결정) — paused는 reconciler가 잔고 불일치를 발견해 데몬이 자신의
    포지션 기록(entry_price/entry_qty)을 신뢰할 수 없다고 판단했을 때 세팅된다. 그
    상태에서 실시간 손절/익절을 발사하는 건 아무 것도 안 하는 것보다 더 위험하므로,
    ticker 트리거는 paused 상태에서 exit_for_risk()를 호출하면 안 된다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(
        dbm, status="paused", market="KRW-BTC",
        sell_conditions_json=json.dumps({"type": "OR", "conditions": [
            {"indicator": "STOP_LOSS_PCT", "params": {}, "operator": "<=", "threshold": -5},
        ]}),
    )
    position_manager.open_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)
    exit_calls = {"n": 0}

    async def fake_stream_ticker(markets):
        yield {"type": "ticker", "code": "KRW-BTC", "trade_price": 47_000_000.0}

    async def fake_exit_for_risk(strategy, position, price, reason, **kwargs):
        exit_calls["n"] += 1
        return {"action": "exited", "order_id": "o1"}

    monkeypatch.setattr(upbit_ws, "stream_ticker", fake_stream_ticker)
    monkeypatch.setattr(order_executor, "exit_for_risk", fake_exit_for_risk)

    await daemon._run_risk_exit_loop(strategy_id)

    assert exit_calls["n"] == 0


async def test_run_risk_exit_loop_skips_non_ticker_frame(monkeypatch, tmp_path):
    """트리비얼 지적사항 — ticker 채널에서 type != "ticker"이거나 trade_price가 없는
    프레임이 오면 KeyError로 튀지 않고 조용히 건너뛰어야 한다. 그 다음 진짜 ticker
    프레임은 정상적으로 처리돼야 한다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(
        dbm, status="running", market="KRW-BTC",
        sell_conditions_json=json.dumps({"type": "OR", "conditions": [
            {"indicator": "STOP_LOSS_PCT", "params": {}, "operator": "<=", "threshold": -5},
        ]}),
    )
    position_manager.open_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)
    exit_calls = {"n": 0}

    async def fake_stream_ticker(markets):
        yield {"type": "trade", "code": "KRW-BTC"}  # 다른 타입의 프레임 — trade_price 없음
        yield {"type": "ticker", "code": "KRW-BTC", "trade_price": None}  # 방어적 케이스
        yield {"type": "ticker", "code": "KRW-BTC", "trade_price": 47_000_000.0}

    async def fake_exit_for_risk(strategy, position, price, reason, **kwargs):
        exit_calls["n"] += 1
        return {"action": "exited", "order_id": "o1"}

    monkeypatch.setattr(upbit_ws, "stream_ticker", fake_stream_ticker)
    monkeypatch.setattr(order_executor, "exit_for_risk", fake_exit_for_risk)
    monkeypatch.setattr(risk_manager, "check_circuit_breaker", lambda sid, cfg: None)

    await daemon._run_risk_exit_loop(strategy_id)  # KeyError가 나면 여기서 예외로 실패

    assert exit_calls["n"] == 1


async def test_run_risk_exit_loop_survives_stream_ticker_generator_raising(monkeypatch, tmp_path, caplog):
    """I5 — stream_ticker()가 tick을 만들어내는 도중(async generator 본체 자체)에서
    예외를 던지면(예: 핸드셰이크 TimeoutError), 그 예외는 tick 단위 try/except 안이
    아니라 async for 문 자체에서 발생한다. 지금은 이걸 흡수하는 코드가 없어 태스크
    전체가 로그 한 줄 없이 죽는다. 바깥 try/except로 흡수하고 로그를 남겨야 한다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(
        dbm, status="running", market="KRW-BTC",
        sell_conditions_json=json.dumps({"type": "OR", "conditions": [
            {"indicator": "STOP_LOSS_PCT", "params": {}, "operator": "<=", "threshold": -5},
        ]}),
    )

    async def failing_stream_ticker(markets):
        raise TimeoutError("핸드셰이크 타임아웃")
        yield  # pragma: no cover - 도달하지 않음, async generator로 만들기 위한 장치

    monkeypatch.setattr(upbit_ws, "stream_ticker", failing_stream_ticker)

    with caplog.at_level("ERROR", logger="trading.daemon"):
        await daemon._run_risk_exit_loop(strategy_id)  # 예외가 밖으로 전파되면 테스트 실패

    assert any(strategy_id in record.message for record in caplog.records)


async def test_run_risk_exit_loop_waits_for_lock_before_exiting(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(
        dbm, status="running", market="KRW-BTC",
        sell_conditions_json=json.dumps({"type": "OR", "conditions": [
            {"indicator": "STOP_LOSS_PCT", "params": {}, "operator": "<=", "threshold": -5},
        ]}),
    )
    position_manager.open_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)
    lock = asyncio.Lock()
    events = []

    async def fake_stream_ticker(markets):
        yield {"type": "ticker", "code": "KRW-BTC", "trade_price": 47_000_000.0}

    async def fake_exit_for_risk(strategy, position, price, reason, **kwargs):
        events.append("exit_for_risk")
        return {"action": "exited", "order_id": "o1"}

    monkeypatch.setattr(upbit_ws, "stream_ticker", fake_stream_ticker)
    monkeypatch.setattr(order_executor, "exit_for_risk", fake_exit_for_risk)
    monkeypatch.setattr(risk_manager, "check_circuit_breaker", lambda sid, cfg: None)

    async with lock:
        events.append("lock_held_by_other")
        loop_task = asyncio.create_task(daemon._run_risk_exit_loop(strategy_id, lock))
        await asyncio.sleep(0)
        assert "exit_for_risk" not in events
        events.append("lock_released_by_other")

    await loop_task

    assert events == ["lock_held_by_other", "lock_released_by_other", "exit_for_risk"]


async def test_task_set_manager_creates_risk_exit_task_for_new_strategy(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm, status="running")
    started = {"ids": []}
    real_sleep = asyncio.sleep

    async def fake_run_strategy_loop(sid, lock=None):
        await asyncio.sleep(3600)

    async def fake_run_risk_exit_loop(sid, lock=None):
        started["ids"].append(sid)
        await asyncio.sleep(3600)

    monkeypatch.setattr(daemon, "_run_strategy_loop", fake_run_strategy_loop)
    monkeypatch.setattr(daemon, "_run_risk_exit_loop", fake_run_risk_exit_loop)

    async def stop_after_one_scan(seconds):
        raise asyncio.CancelledError()

    monkeypatch.setattr(daemon.asyncio, "sleep", stop_after_one_scan)

    with pytest.raises(asyncio.CancelledError):
        await daemon._task_set_manager_loop()
    await real_sleep(0)

    assert started["ids"] == [strategy_id]


async def test_task_set_manager_cancels_risk_exit_task_for_removed_strategy(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm, status="running")
    cancelled = {"count": 0}
    real_sleep = asyncio.sleep

    async def fake_run_strategy_loop(sid, lock=None):
        never_set = asyncio.Event()
        await never_set.wait()

    async def fake_run_risk_exit_loop(sid, lock=None):
        never_set = asyncio.Event()
        try:
            await never_set.wait()
        except asyncio.CancelledError:
            cancelled["count"] += 1
            raise

    monkeypatch.setattr(daemon, "_run_strategy_loop", fake_run_strategy_loop)
    monkeypatch.setattr(daemon, "_run_risk_exit_loop", fake_run_risk_exit_loop)

    scan_count = {"n": 0}

    async def fake_sleep(seconds):
        scan_count["n"] += 1
        if scan_count["n"] == 1:
            dbm.update_live_strategy_status(strategy_id, "stopped")
            await real_sleep(0)
        else:
            raise asyncio.CancelledError()

    monkeypatch.setattr(daemon.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await daemon._task_set_manager_loop()
    await real_sleep(0)

    assert cancelled["count"] == 1


async def test_task_set_manager_shares_same_lock_between_strategy_and_risk_exit_loop(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm, status="running")
    captured = {}
    real_sleep = asyncio.sleep

    async def fake_run_strategy_loop(sid, lock=None):
        captured["strategy_lock"] = lock
        await asyncio.sleep(3600)

    async def fake_run_risk_exit_loop(sid, lock=None):
        captured["risk_exit_lock"] = lock
        await asyncio.sleep(3600)

    monkeypatch.setattr(daemon, "_run_strategy_loop", fake_run_strategy_loop)
    monkeypatch.setattr(daemon, "_run_risk_exit_loop", fake_run_risk_exit_loop)

    async def stop_after_one_scan(seconds):
        raise asyncio.CancelledError()

    monkeypatch.setattr(daemon.asyncio, "sleep", stop_after_one_scan)

    with pytest.raises(asyncio.CancelledError):
        await daemon._task_set_manager_loop()
    await real_sleep(0)

    assert captured["strategy_lock"] is captured["risk_exit_lock"]
    assert isinstance(captured["strategy_lock"], asyncio.Lock)


async def test_strategy_loop_and_risk_exit_loop_serialize_order_execution_via_shared_lock(monkeypatch, tmp_path):
    """⑤-4c 설계 스펙 결정4의 핵심 계약: 진짜 asyncio.Lock 하나를 공유하는
    _run_strategy_loop와 _run_risk_exit_loop를 asyncio.gather로 동시에 돌려도,
    신호처리(handle_signal_result)와 ticker 트리거 청산(exit_for_risk)이 서로 겹치지
    않고 완전히 순차적으로만 실행돼야 한다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(
        dbm, status="running", timeframe="minutes1", market="KRW-BTC",
        sell_conditions_json=json.dumps({"type": "OR", "conditions": [
            {"indicator": "STOP_LOSS_PCT", "params": {}, "operator": "<=", "threshold": -5},
        ]}),
    )
    position_manager.open_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)
    lock = asyncio.Lock()
    events = []
    real_sleep = asyncio.sleep  # daemon.asyncio.sleep을 아래서 monkeypatch하기 전에 붙잡아둔다

    async def fake_hydrate_state(strategy, *, client=None):
        return {"synced_wait_orders": 0, "baseline_captured": True}

    def fake_evaluate_signals(sid, now=None):
        return {"new_candle": True, "candle_time": "2026-08-08T00:00:00+00:00",
                "buy_signal": False, "sell_signal": False,
                "buy_signal_id": "b1", "sell_signal_id": "s1",
                "latest_close": 50000000.0, "paused": False, "resumed": False}

    async def fake_handle_signal_result(sid, result, *, dry_run=False):
        events.append("handle_signal_result:start")
        await real_sleep(0)  # 이 구간이 lock 없이는 다른 태스크에 끼어들 여지를 실제로 준다
        events.append("handle_signal_result:end")
        return {"buy_action": None, "sell_action": None, "buy_order_id": None, "sell_order_id": None}

    async def fake_check_manual_intervention(strategy, *, own_fills=(), client=None):
        return {"balance_mismatch": False, "action": "none", "paused": False}

    async def fake_sync_pending_limit_orders(strategy, *, client=None):
        return []

    async def fake_stream_ticker(markets):
        yield {"type": "ticker", "code": "KRW-BTC", "trade_price": 47_000_000.0}

    async def fake_exit_for_risk(strategy, position, price, reason, **kwargs):
        events.append("exit_for_risk:start")
        await real_sleep(0)
        events.append("exit_for_risk:end")
        return {"action": "exited", "order_id": "o1"}

    monkeypatch.setattr(reconciler, "hydrate_state", fake_hydrate_state)
    monkeypatch.setattr(signal_engine, "evaluate_signals", fake_evaluate_signals)
    monkeypatch.setattr(order_executor, "handle_signal_result", fake_handle_signal_result)
    monkeypatch.setattr(reconciler, "check_manual_intervention", fake_check_manual_intervention)
    monkeypatch.setattr(reconciler, "sync_pending_limit_orders", fake_sync_pending_limit_orders)
    monkeypatch.setattr(upbit_ws, "stream_ticker", fake_stream_ticker)
    monkeypatch.setattr(order_executor, "exit_for_risk", fake_exit_for_risk)
    monkeypatch.setattr(risk_manager, "check_circuit_breaker", lambda sid, cfg: None)
    calls, fake_sleep = _stop_after_one_tick(dbm, strategy_id)
    monkeypatch.setattr(daemon.asyncio, "sleep", fake_sleep)

    async def fake_to_thread(func, *args):
        # _run_strategy_loop는 evaluate_signals를 asyncio.to_thread로 실행하는데,
        # 이건 실제 OS 스레드풀을 거치는 지연이라 _run_risk_exit_loop의 in-loop
        # sleep(0)보다 훨씬 느리고 비결정적이다 — 그 결과 두 태스크가 실제로 같은
        # 시점에 lock 획득을 시도하지 못하고(스케줄링 우연으로 절대 안 겹침), lock이
        # 공유되지 않아도(심지어 lock 자체가 없어도) 이 테스트가 우연히 통과해버린다
        # (리뷰에서 실증됨). to_thread를 in-loop sleep(0)로 대체해 두 태스크의 lock
        # 시도 타이밍을 동질화하면, 진짜 lock 경합이 일어나 broken lock을 실제로
        # 탐지할 수 있다.
        await real_sleep(0)
        return func(*args)

    monkeypatch.setattr(daemon.asyncio, "to_thread", fake_to_thread)

    await asyncio.gather(
        daemon._run_strategy_loop(strategy_id, lock),
        daemon._run_risk_exit_loop(strategy_id, lock),
    )

    assert len(events) == 4
    # 두 구간이 절대 인터리빙되지 않아야 한다: 하나의 start~end 쌍이 완전히 끝난 뒤에야
    # 다른 쪽의 start가 나와야 한다(직렬화 계약 — lock이 없으면 이 assert가 깨진다).
    for i in range(0, len(events), 2):
        name_start, phase_start = events[i].split(":")
        name_end, phase_end = events[i + 1].split(":")
        assert phase_start == "start" and phase_end == "end"
        assert name_start == name_end
