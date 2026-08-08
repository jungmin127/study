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
import trading.reconciler as reconciler
import trading.risk_manager as risk_manager
import trading.signal_engine as signal_engine
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


async def test_task_set_manager_creates_task_for_new_strategy(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm, status="running")
    started = {"ids": []}
    # daemon.asyncio는 top-level `import asyncio`와 동일한 모듈 객체라서
    # monkeypatch.setattr(daemon.asyncio, "sleep", ...)는 asyncio.sleep 자체를
    # 전역으로 바꿔버린다. 아래에서 실제 스케줄링 양보용으로 쓸 원본 sleep을
    # 패치 전에 미리 붙잡아둔다.
    real_sleep = asyncio.sleep

    async def fake_run_strategy_loop(sid):
        started["ids"].append(sid)
        await asyncio.sleep(3600)  # 태스크가 살아있는 채로 유지(취소되기 전까지)

    monkeypatch.setattr(daemon, "_run_strategy_loop", fake_run_strategy_loop)

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

    async def fake_run_strategy_loop(sid):
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

    monkeypatch.setattr(daemon, "_run_strategy_loop", fake_run_strategy_loop)

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
