# 라이브 트레이딩 서브플랜⑤-4c — 실시간 손절/익절(ticker 기반) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **워크트리를 만들지 말고 main 브랜치에서 직접 작업한다** (사용자 지시, [[upbit-v1-worktree-workflow-changed]]).

**Goal:** `trading/daemon.py`에 ticker 스트림 기반 실시간 손절/익절(`STOP_LOSS_PCT`/
`TAKE_PROFIT_PCT`)을 추가한다 — 캔들 마감을 기다리지 않고 매 체결가마다 즉시 반응하되,
기존 캔들 기반 신호처리·reconciler와 같은 전략에 대해 주문 실행이 절대 동시에 돌지 않게
직렬화한다.

**Architecture:** 설계 스펙 `docs/superpowers/specs_v1/2026-08-09-live-trading-daemon-realtime-risk-exit-design.md`
를 그대로 구현한다. `STOP_LOSS_PCT`/`TAKE_PROFIT_PCT` 추출/평가 로직은 `daemon.py`가
아니라 `trading/signal_engine.py`에 둔다(daemon.py는 `engine/`을 전혀 import하지 않기로
확정돼 있고, `signal_engine.py`는 이미 `engine.condition_tree`를 import하고 있다).
전략마다 개별 ticker WS 연결(`_run_risk_exit_loop`)을 새 태스크로 열고, 기존
`_run_strategy_loop`와 전략별 `asyncio.Lock`을 공유해 `order_executor.exit()` 호출이
겹치지 않게 한다.

**Tech Stack:** Python, `asyncio`, `pytest`(+`pytest-asyncio`, `asyncio_mode = auto`).
새 의존성 없음.

## Global Constraints

- `trading/daemon.py`는 여전히 `engine/`을 전혀 import하지 않는다 — 새 로직은
  `trading/signal_engine.py`를 거쳐서만 접근한다.
- `STOP_LOSS_PCT`/`TAKE_PROFIT_PCT`는 `sell_conditions_json` 트리 안의 다른 조건과의
  AND/OR 결합을 무시하고 독립 안전망으로 평가한다(설계 스펙 결정1).
- ticker 구독은 전략당 개별 WS 연결(설계 스펙 결정3), 위험조건이 없는 전략은 연결을
  아예 열지 않는다(결정7).
- `_run_strategy_loop`와 `_run_risk_exit_loop`는 전략당 `asyncio.Lock` 하나를 공유해
  주문 실행 구간을 직렬화한다(결정4).
- 신규 함수/파라미터는 전부 기존 함수의 기본 동작을 바꾸지 않는다(하위호환 — 특히
  `_run_strategy_loop`/`_run_risk_exit_loop`의 `lock` 파라미터는 기본값 `None`이면
  내부에서 새 `Lock()`을 만들어 기존 테스트가 전혀 손대지 않아도 되게 한다).
- 커밋은 태스크 단위로 작게, 테스트가 통과한 뒤에만 한다.

---

## File Structure

- **Modify:** `trading/order_executor.py` — `exit()`에 `close_reason` 파라미터 추가,
  `exit_for_risk()` 신규.
- **Modify:** `tests/test_order_executor.py` — 위 추가분 테스트.
- **Modify:** `trading/signal_engine.py` — `has_risk_exit_conditions()`,
  `matched_risk_exit_indicator()` 신규(+`engine.condition_tree`에서 `apply_operator`
  추가 import).
- **Modify:** `tests/test_signal_engine.py` — 위 추가분 테스트.
- **Modify:** `trading/daemon.py` — `_run_strategy_loop()`에 `lock` 파라미터,
  `_run_risk_exit_loop()` 신규, `_task_set_manager_loop()`가 lock+risk_exit 태스크도
  관리하도록 확장.
- **Modify:** `tests/test_daemon.py` — 위 추가분 테스트 + 기존 task_set_manager 테스트
  2개의 fake 시그니처 갱신.

---

### Task 1: `trading/order_executor.py` — `exit()`에 `close_reason` 파라미터 추가

**Files:**
- Modify: `trading/order_executor.py`
- Modify: `tests/test_order_executor.py`

**Interfaces:**
- Consumes: `trading.position_manager.close_position(position_id, exit_price, exit_qty,
  fee, close_reason)`(기존, `close_reason`은 이미 자유 TEXT 파라미터).
- Produces: `trading.order_executor.exit(strategy, position, expected_price, *,
  client=None, dry_run=False, close_reason: str = "signal") -> dict`(기존 시그니처에
  파라미터 하나 추가, 하위호환).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_order_executor.py` 파일 끝에 추가:
```python
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
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_order_executor.py -v -k close_reason`
Expected: FAIL — `TypeError: exit() got an unexpected keyword argument 'close_reason'`

- [ ] **Step 3: `trading/order_executor.py` 수정**

`exit()`의 시그니처를:
```python
async def exit(
    strategy: dict, position: dict, expected_price: float,
    *, client: httpx.AsyncClient | None = None, dry_run: bool = False,
) -> dict:
```
에서:
```python
async def exit(
    strategy: dict, position: dict, expected_price: float,
    *, client: httpx.AsyncClient | None = None, dry_run: bool = False,
    close_reason: str = "signal",
) -> dict:
```
로 교체한다.

`exit()` 안의:
```python
    close_result = position_manager.close_position(
        position["id"], result["filled_price"], result["filled_volume"], result["fee"], "signal",
    )
```
를:
```python
    close_result = position_manager.close_position(
        position["id"], result["filled_price"], result["filled_volume"], result["fee"], close_reason,
    )
```
로 교체한다.

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_order_executor.py -v`
Expected: 전부 PASS(회귀 없음 — 기본값이 기존 동작과 동일)

- [ ] **Step 5: 커밋**

```bash
git add trading/order_executor.py tests/test_order_executor.py
git commit -m "feat: order_executor.exit()에 close_reason 파라미터 추가(감사 추적용)"
```

---

### Task 2: `trading/order_executor.py` — `exit_for_risk()` 신규

**Files:**
- Modify: `trading/order_executor.py`
- Modify: `tests/test_order_executor.py`

**Interfaces:**
- Consumes: `exit()`(Task1, `close_reason` 파라미터), `risk_manager.record_trade_result`
  (기존).
- Produces: `trading.order_executor.exit_for_risk(strategy: dict, position: dict,
  expected_price: float, reason: str, *, client=None, dry_run=False) -> dict`(반환:
  `{"action": "exited"|"pending"|"slippage_exceeded", "order_id": str}`).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_order_executor.py` 파일 끝에 추가:
```python
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
        return {"uuid": "uuid-risk-pending", "state": "wait"}

    monkeypatch.setattr(upbit_client, "create_order", fake_create_order)

    result = await order_executor.exit_for_risk(strategy, position, 50_000_000.0, "take_profit_pct")

    assert result["action"] == "pending"
    assert recorded["count"] == 0
    assert position_manager.get_open_position(strategy["id"]) is not None


async def test_exit_for_risk_marks_slippage_exceeded_on_cancel(monkeypatch, tmp_path):
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
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_order_executor.py -v -k exit_for_risk`
Expected: FAIL — `AttributeError: module 'trading.order_executor' has no attribute 'exit_for_risk'`

- [ ] **Step 3: `trading/order_executor.py`에 구현 추가**

`exit()` 함수 정의가 끝나는 지점(다음 함수 `handle_signal_result` 시작 직전)에 추가:
```python


async def exit_for_risk(
    strategy: dict, position: dict, expected_price: float, reason: str,
    *, client: httpx.AsyncClient | None = None, dry_run: bool = False,
) -> dict:
    """ticker 트리거 손절/익절 전용 진입점(⑤-4c). handle_signal_result()와 달리
    signals 테이블과 무관하다 — candle 사이클 밖에서 발생하는 이벤트라 대응되는 signal
    row가 없다. 성공 시 record_trade_result()까지 호출(handle_signal_result의 매도
    성공 분기와 동일한 부기 의무 — daemon.py의 check_circuit_breaker() 호출 전제)."""
    order = await exit(
        strategy, position, expected_price, client=client, dry_run=dry_run, close_reason=reason,
    )
    if order["status"] == "done":
        risk_manager.record_trade_result(strategy["id"], order["realized_pnl"], order["capital_after"])
        return {"action": "exited", "order_id": order["id"]}
    if order["status"] == "cancel":
        return {"action": "slippage_exceeded", "order_id": order["id"]}
    return {"action": "pending", "order_id": order["id"]}
```

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_order_executor.py -v`
Expected: 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add trading/order_executor.py tests/test_order_executor.py
git commit -m "feat: order_executor에 exit_for_risk(ticker 트리거 손절/익절 전용) 추가"
```

---

### Task 3: `trading/signal_engine.py` — 위험조건 추출/평가 함수 2개

**Files:**
- Modify: `trading/signal_engine.py`
- Modify: `tests/test_signal_engine.py`

**Interfaces:**
- Consumes: `engine.condition_tree.collect_blocks`(기존, 이미 import됨),
  `engine.condition_tree.apply_operator`(신규 import).
- Produces: `trading.signal_engine.has_risk_exit_conditions(sell_conditions: dict) ->
  bool`, `trading.signal_engine.matched_risk_exit_indicator(sell_conditions: dict,
  position_return_pct: float) -> str | None`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_signal_engine.py` 파일 끝에 추가:
```python
def test_has_risk_exit_conditions_true_when_stop_loss_present():
    sell = {"type": "OR", "conditions": [
        {"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 70},
        {"indicator": "STOP_LOSS_PCT", "params": {}, "operator": "<=", "threshold": -5},
    ]}
    assert signal_engine.has_risk_exit_conditions(sell) is True


def test_has_risk_exit_conditions_false_when_absent():
    sell = {"type": "OR", "conditions": [
        {"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 70},
    ]}
    assert signal_engine.has_risk_exit_conditions(sell) is False


def test_matched_risk_exit_indicator_ignores_and_or_combination_with_other_indicators():
    # RSI 조건이 False라도(다른 지표는 이 테스트에서 아예 계산되지 않음) STOP_LOSS_PCT
    # 단독 위반만으로 매치돼야 한다 — 결정1의 핵심 계약("독립 안전망").
    sell = {"type": "AND", "conditions": [
        {"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 70},
        {"indicator": "STOP_LOSS_PCT", "params": {}, "operator": "<=", "threshold": -5},
    ]}
    assert signal_engine.matched_risk_exit_indicator(sell, -6.0) == "STOP_LOSS_PCT"


def test_matched_risk_exit_indicator_returns_take_profit_when_breached():
    sell = {"type": "OR", "conditions": [
        {"indicator": "TAKE_PROFIT_PCT", "params": {}, "operator": ">=", "threshold": 10},
    ]}
    assert signal_engine.matched_risk_exit_indicator(sell, 12.0) == "TAKE_PROFIT_PCT"


def test_matched_risk_exit_indicator_returns_none_when_within_thresholds():
    sell = {"type": "OR", "conditions": [
        {"indicator": "STOP_LOSS_PCT", "params": {}, "operator": "<=", "threshold": -5},
        {"indicator": "TAKE_PROFIT_PCT", "params": {}, "operator": ">=", "threshold": 10},
    ]}
    assert signal_engine.matched_risk_exit_indicator(sell, 0.0) is None


def test_matched_risk_exit_indicator_ignores_holding_period_bars():
    # HOLDING_PERIOD_BARS는 봉 개수 기반이라 ticker 실시간 평가 대상이 아니다(스펙 범위 밖).
    sell = {"type": "OR", "conditions": [
        {"indicator": "HOLDING_PERIOD_BARS", "params": {}, "operator": ">=", "threshold": 1},
    ]}
    assert signal_engine.matched_risk_exit_indicator(sell, -100.0) is None
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_signal_engine.py -v -k "risk_exit_conditions or matched_risk_exit"`
Expected: FAIL — `AttributeError: module 'trading.signal_engine' has no attribute 'has_risk_exit_conditions'`

- [ ] **Step 3: `trading/signal_engine.py`에 구현 추가**

import 블록을:
```python
from engine.condition_tree import (
    POSITION_RELATIVE_INDICATORS,
    collect_blocks,
    eval_group_values,
    indicator_key,
    max_required_period,
    required_aux_markets,
)
```
에서:
```python
from engine.condition_tree import (
    POSITION_RELATIVE_INDICATORS,
    apply_operator,
    collect_blocks,
    eval_group_values,
    indicator_key,
    max_required_period,
    required_aux_markets,
)
```
로 교체한다(`apply_operator` 추가).

파일 끝에 추가:
```python


_TICKER_RISK_INDICATORS = {"STOP_LOSS_PCT", "TAKE_PROFIT_PCT"}


def has_risk_exit_conditions(sell_conditions: dict) -> bool:
    """sell_conditions_json에 STOP_LOSS_PCT/TAKE_PROFIT_PCT 블록이 하나라도 있는지
    확인한다(⑤-4c: 없는 전략은 daemon.py가 ticker WS 연결 자체를 안 열기 위한
    최적화용, 설계 스펙 결정7)."""
    return any(b["indicator"] in _TICKER_RISK_INDICATORS for b in collect_blocks(sell_conditions))


def matched_risk_exit_indicator(sell_conditions: dict, position_return_pct: float) -> str | None:
    """STOP_LOSS_PCT/TAKE_PROFIT_PCT를 sell_conditions_json 안의 다른 조건과의 AND/OR
    결합과 무관하게 독립 안전망으로 평가한다(⑤-4c 설계 스펙 결정1). 위반된 블록의
    indicator 이름(트리에서 먼저 발견된 것)을 반환, 없으면 None. daemon.py가 반환값을
    order_executor.exit_for_risk()의 close_reason 기록에 그대로 쓴다."""
    for block in collect_blocks(sell_conditions):
        if block["indicator"] in _TICKER_RISK_INDICATORS:
            if apply_operator(position_return_pct, block["operator"], float(block["threshold"])):
                return block["indicator"]
    return None
```

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_signal_engine.py -v`
Expected: 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add trading/signal_engine.py tests/test_signal_engine.py
git commit -m "feat: signal_engine에 has_risk_exit_conditions/matched_risk_exit_indicator 추가"
```

---

### Task 4: `trading/daemon.py` — `_run_strategy_loop()`가 공유 lock으로 주문실행 구간을 감싼다

**Files:**
- Modify: `trading/daemon.py`
- Modify: `tests/test_daemon.py`

**Interfaces:**
- Consumes: `asyncio.Lock`(신규 파라미터).
- Produces: `trading.daemon._run_strategy_loop(strategy_id: str, lock: asyncio.Lock |
  None = None) -> None`(기존 시그니처에 파라미터 추가, 기본값 `None`이면 내부에서
  새 Lock을 만들어 기존 호출부/테스트는 전혀 안 바뀌어도 됨).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_daemon.py`에서 `test_run_strategy_loop_throttles_reconcile_retries_even_on_failure`
함수가 끝나는 지점(`test_task_set_manager_creates_task_for_new_strategy` 함수 시작 직전)에
추가:
```python
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
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_daemon.py -v -k serializes_signal_processing`
Expected: FAIL — `TypeError: _run_strategy_loop() takes 1 positional argument but 2 were given`

- [ ] **Step 3: `trading/daemon.py` 수정**

`_run_strategy_loop`의 시그니처와 docstring을:
```python
async def _run_strategy_loop(strategy_id: str) -> None:
    """전략 하나를 담당하는 유일한 태스크(설계 스펙 결정3). hydrate_state() 1회 →
    무한루프(새 봉 처리 → 매도체결 시 서킷브레이커 판정 → 20초마다 reconciler 2종
    호출 → 봉타임 비례 sleep). status가 running/paused가 아니게 되면 스스로 종료한다.
    신호처리와 reconcile은 서로 독립된 try/except다 — 신호처리가 매 틱 죽어도 reconcile
    워치독은 계속 돌아야 하고, 그 반대도 마찬가지다(최종 브랜치 리뷰 Important 3).
    예외는 로그만 남기고 다음 틱에 재시도(결정8)."""
    strategy = db.get_live_strategy(strategy_id)
    if strategy is None:
        return
```
에서:
```python
async def _run_strategy_loop(strategy_id: str, lock: asyncio.Lock | None = None) -> None:
    """전략 하나를 담당하는 유일한 태스크(설계 스펙 결정3). hydrate_state() 1회 →
    무한루프(새 봉 처리 → 매도체결 시 서킷브레이커 판정 → 20초마다 reconciler 2종
    호출 → 봉타임 비례 sleep). status가 running/paused가 아니게 되면 스스로 종료한다.
    신호처리와 reconcile은 서로 독립된 try/except다 — 신호처리가 매 틱 죽어도 reconcile
    워치독은 계속 돌아야 하고, 그 반대도 마찬가지다(최종 브랜치 리뷰 Important 3).
    예외는 로그만 남기고 다음 틱에 재시도(결정8). lock은 order_executor.enter()/exit()가
    실제로 실행되는 구간(신호처리의 handle_signal_result 호출 + reconcile 블록 전체)을
    감싸 _run_risk_exit_loop(⑤-4c)의 ticker 트리거 청산과 겹치지 않게 한다(⑤-4c 설계
    스펙 결정4). lock이 None이면(기존 호출부와의 하위호환) 새 Lock을 만든다 — 아무도
    공유하지 않으므로 사실상 no-op."""
    if lock is None:
        lock = asyncio.Lock()
    strategy = db.get_live_strategy(strategy_id)
    if strategy is None:
        return
```
로 교체한다.

신호처리 블록을:
```python
        try:
            result = await asyncio.to_thread(signal_engine.evaluate_signals, strategy_id)
            if result["new_candle"]:
                action_result = await order_executor.handle_signal_result(strategy_id, result)
                if action_result["sell_action"] == "exited":
                    risk_config = json.loads(strategy["risk_config_json"])
                    risk_manager.check_circuit_breaker(strategy_id, risk_config)
        except Exception:
            logger.exception("전략 신호처리 중 예외 발생: strategy_id=%s", strategy_id)
```
에서:
```python
        try:
            result = await asyncio.to_thread(signal_engine.evaluate_signals, strategy_id)
            if result["new_candle"]:
                async with lock:
                    action_result = await order_executor.handle_signal_result(strategy_id, result)
                    if action_result["sell_action"] == "exited":
                        risk_config = json.loads(strategy["risk_config_json"])
                        risk_manager.check_circuit_breaker(strategy_id, risk_config)
        except Exception:
            logger.exception("전략 신호처리 중 예외 발생: strategy_id=%s", strategy_id)
```
로 교체한다(`evaluate_signals`의 네트워크 호출은 주문/포지션을 건드리지 않으므로 lock
밖에 둔다 — ticker 태스크와 실제로 겹치면 안 되는 구간만 lock으로 좁힌다).

reconcile 블록을:
```python
        now = time.monotonic()
        if now - last_reconcile >= _RECONCILE_INTERVAL_SEC:
            # 시도 직전에 갱신한다 — 실패해도 다음 틱마다(5~60초) 재시도하지 않고
            # 20초 상한을 지키게 하기 위함이다(M3).
            last_reconcile = now
            try:
                strategy = db.get_live_strategy(strategy_id) or strategy
                synced = await reconciler.sync_pending_limit_orders(strategy)
                await reconciler.check_manual_intervention(strategy, own_fills=synced)
            except Exception:
                logger.exception("전략 reconcile 중 예외 발생: strategy_id=%s", strategy_id)
```
에서:
```python
        now = time.monotonic()
        if now - last_reconcile >= _RECONCILE_INTERVAL_SEC:
            # 시도 직전에 갱신한다 — 실패해도 다음 틱마다(5~60초) 재시도하지 않고
            # 20초 상한을 지키게 하기 위함이다(M3).
            last_reconcile = now
            try:
                async with lock:
                    strategy = db.get_live_strategy(strategy_id) or strategy
                    synced = await reconciler.sync_pending_limit_orders(strategy)
                    await reconciler.check_manual_intervention(strategy, own_fills=synced)
            except Exception:
                logger.exception("전략 reconcile 중 예외 발생: strategy_id=%s", strategy_id)
```
로 교체한다(reconciler의 REST 조회 전체가 주문실행과 겹치면 안 되는 구간이라 블록
전체를 lock으로 감싼다).

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_daemon.py -v`
Expected: 전부 PASS(기존 19개 + 신규 1개 = 20개, 회귀 없음 — `lock=None` 기본값 덕분에
기존 테스트는 코드 변경 없이 그대로 통과)

- [ ] **Step 5: 커밋**

```bash
git add trading/daemon.py tests/test_daemon.py
git commit -m "feat: _run_strategy_loop가 공유 lock으로 주문실행 구간을 감싸도록 확장"
```

---

### Task 5: `trading/daemon.py` — `_run_risk_exit_loop()` 신규

**Files:**
- Modify: `trading/daemon.py`
- Modify: `tests/test_daemon.py`

**Interfaces:**
- Consumes: `trading.signal_engine.has_risk_exit_conditions`/`matched_risk_exit_indicator`
  (Task3), `trading.order_executor.exit_for_risk`(Task2), `trading.position_manager.
  get_open_position`(기존), `trading.upbit_ws.stream_ticker`(기존),
  `trading.risk_manager.check_circuit_breaker`(기존).
- Produces: `trading.daemon._run_risk_exit_loop(strategy_id: str, lock: asyncio.Lock |
  None = None) -> None`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_daemon.py` 상단 import 블록(`import trading.signal_engine as signal_engine`
가 있는 곳 근처)에 추가:
```python
import trading.position_manager as position_manager
import trading.upbit_ws as upbit_ws
```

파일 끝(`test_ntp_check_loop_survives_exception` 다음)에 추가:
```python
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
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(
        dbm, status="running", market="KRW-BTC",
        sell_conditions_json=json.dumps({"type": "OR", "conditions": [
            {"indicator": "STOP_LOSS_PCT", "params": {}, "operator": "<=", "threshold": -5},
        ]}),
    )
    position_manager.open_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)
    processed = {"n": 0}

    async def fake_stream_ticker(markets):
        yield {"type": "ticker", "code": "KRW-BTC", "trade_price": 47_000_000.0}
        yield {"type": "ticker", "code": "KRW-BTC", "trade_price": 47_000_000.0}

    async def failing_exit_for_risk(strategy, position, price, reason, **kwargs):
        processed["n"] += 1
        raise RuntimeError("네트워크 순간 장애")

    monkeypatch.setattr(upbit_ws, "stream_ticker", fake_stream_ticker)
    monkeypatch.setattr(order_executor, "exit_for_risk", failing_exit_for_risk)

    await daemon._run_risk_exit_loop(strategy_id)  # 예외가 밖으로 전파되면 테스트 실패

    assert processed["n"] == 2  # 첫 tick 실패 후에도 두 번째 tick을 계속 처리


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
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_daemon.py -v -k run_risk_exit_loop`
Expected: FAIL — `AttributeError: module 'trading.daemon' has no attribute '_run_risk_exit_loop'`

- [ ] **Step 3: `trading/daemon.py`에 구현 추가**

import 블록을:
```python
import trading.db as db
import trading.order_executor as order_executor
import trading.reconciler as reconciler
import trading.risk_manager as risk_manager
import trading.signal_engine as signal_engine
import upbit_data_service
```
에서:
```python
import trading.db as db
import trading.order_executor as order_executor
import trading.position_manager as position_manager
import trading.reconciler as reconciler
import trading.risk_manager as risk_manager
import trading.signal_engine as signal_engine
import trading.upbit_ws as upbit_ws
import upbit_data_service
```
로 교체한다.

모듈 docstring의 마지막 문장을:
```
결정3). 실시간 손절/익절(ticker 기반)은 ⑤-4c 몫이라 여기 없다. trading.db +
trading.signal_engine + trading.order_executor + trading.reconciler +
trading.risk_manager + upbit_data_service만 의존. engine/ 미의존.
```
에서:
```
결정3). 실시간 손절/익절(ticker 기반, ⑤-4c)은 전략별 개별 ticker WS 연결
(_run_risk_exit_loop)로 처리하며, _run_strategy_loop와 전략별 asyncio.Lock을 공유해
주문실행이 겹치지 않게 한다. trading.db + trading.signal_engine +
trading.order_executor + trading.position_manager + trading.reconciler +
trading.risk_manager + trading.upbit_ws + upbit_data_service만 의존. engine/ 미의존.
```
로 교체한다.

파일 끝(`_task_set_manager_loop` 함수 앞)에 추가:
```python
async def _run_risk_exit_loop(strategy_id: str, lock: asyncio.Lock | None = None) -> None:
    """전략 하나의 ticker 기반 실시간 손절/익절 전용 태스크(⑤-4c 설계 스펙). 시작 시
    sell_conditions_json에 STOP_LOSS_PCT/TAKE_PROFIT_PCT가 없으면 WS 연결 없이 즉시
    반환한다(결정7 — 위험조건 없는 전략까지 연결을 열 이유가 없음). 있으면 해당 마켓의
    ticker를 구독해(결정3) 매 tick마다 position_return_pct를 계산하고 독립 안전망으로
    평가(결정1) — 위반 시 lock을 잡고(결정4) exit_for_risk() 호출(결정5), 청산 성공
    시에만 check_circuit_breaker()까지 호출(결정7 재사용). 예외는 로그만 남기고 다음
    tick에 계속(⑤-4b 결정8과 동일 원칙)."""
    if lock is None:
        lock = asyncio.Lock()
    strategy = db.get_live_strategy(strategy_id)
    if strategy is None:
        return
    sell_conditions = json.loads(strategy["sell_conditions_json"])
    if not signal_engine.has_risk_exit_conditions(sell_conditions):
        return

    market = strategy["market"]
    async for tick in upbit_ws.stream_ticker([market]):
        try:
            trade_price = tick["trade_price"]
            async with lock:
                position = position_manager.get_open_position(strategy_id)
                if position is None:
                    continue
                position_return_pct = (
                    (trade_price - position["entry_price"]) / position["entry_price"] * 100
                )
                matched = signal_engine.matched_risk_exit_indicator(sell_conditions, position_return_pct)
                if matched is None:
                    continue
                fresh_strategy = db.get_live_strategy(strategy_id)
                if fresh_strategy is None:
                    continue
                result = await order_executor.exit_for_risk(
                    fresh_strategy, position, trade_price, matched.lower(),
                )
                if result["action"] == "exited":
                    risk_config = json.loads(fresh_strategy["risk_config_json"])
                    risk_manager.check_circuit_breaker(strategy_id, risk_config)
        except Exception:
            logger.exception("실시간 손절/익절 처리 중 예외 발생: strategy_id=%s", strategy_id)


```

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_daemon.py -v`
Expected: 전부 PASS(기존 20개 + 신규 8개 = 28개)

- [ ] **Step 5: 커밋**

```bash
git add trading/daemon.py tests/test_daemon.py
git commit -m "feat: daemon에 _run_risk_exit_loop(ticker 기반 실시간 손절/익절) 추가"
```

---

### Task 6: `trading/daemon.py` — `_task_set_manager_loop()`가 lock+risk_exit 태스크도 관리

**Files:**
- Modify: `trading/daemon.py`
- Modify: `tests/test_daemon.py`

**Interfaces:**
- Consumes: `_run_risk_exit_loop`(Task5), `asyncio.Lock`.
- Produces: `_task_set_manager_loop()`가 전략당 `asyncio.Lock`을 생성해
  `_run_strategy_loop`/`_run_risk_exit_loop` 양쪽에 동일 객체로 전달하고, 두 태스크를
  같은 생명주기(활성 전략 집합 기준 생성/취소)로 관리한다.

- [ ] **Step 1: 기존 테스트 2개를 새 시그니처 기준으로 갱신 + 신규 테스트 작성**

`tests/test_daemon.py`의 `test_task_set_manager_creates_task_for_new_strategy` 안의:
```python
    async def fake_run_strategy_loop(sid):
        started["ids"].append(sid)
        await asyncio.sleep(3600)  # 태스크가 살아있는 채로 유지(취소되기 전까지)

    monkeypatch.setattr(daemon, "_run_strategy_loop", fake_run_strategy_loop)
```
를:
```python
    async def fake_run_strategy_loop(sid, lock=None):
        started["ids"].append(sid)
        await asyncio.sleep(3600)  # 태스크가 살아있는 채로 유지(취소되기 전까지)

    async def fake_run_risk_exit_loop(sid, lock=None):
        await asyncio.sleep(3600)

    monkeypatch.setattr(daemon, "_run_strategy_loop", fake_run_strategy_loop)
    monkeypatch.setattr(daemon, "_run_risk_exit_loop", fake_run_risk_exit_loop)
```
로 교체한다(`_task_set_manager_loop`가 이제 `_run_strategy_loop(strategy_id,
locks[strategy_id])`처럼 2개 인자로 부르므로 fake도 `lock` 파라미터를 받아야 하고,
`_run_risk_exit_loop`도 실제 함수가 그대로 도는 걸 막기 위해 같이 monkeypatch한다).

`test_task_set_manager_cancels_task_for_removed_strategy` 안의:
```python
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
```
를:
```python
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
```
로 교체한다.

파일 끝(`test_ntp_check_loop_survives_exception` 다음, Task5에서 추가한 테스트들
다음)에 추가:
```python
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
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_daemon.py -v -k task_set_manager`
Expected: 새 테스트 3개 FAIL — `AssertionError`(risk_exit 태스크가 아직 안 생겨서
`started["ids"]`가 빈 리스트, 또는 `captured`에 `risk_exit_lock` 키가 없어서 KeyError)

- [ ] **Step 3: `trading/daemon.py` 수정**

`_task_set_manager_loop`를:
```python
async def _task_set_manager_loop() -> None:
    """20초마다 db.list_active_strategies()를 다시 조회해 태스크 집합을 갱신한다
    (설계 스펙 결정2). 새 전략 -> create_task(_run_strategy_loop), 더 이상 대상
    아님 -> task.cancel(). 재시작 없이 새로 승인된 전략을 자동으로 픽업한다."""
    tasks: dict[str, asyncio.Task] = {}
    while True:
        try:
            active_ids = {s["id"] for s in db.list_active_strategies()}

            for strategy_id in active_ids:
                if strategy_id not in tasks or tasks[strategy_id].done():
                    tasks[strategy_id] = asyncio.create_task(_run_strategy_loop(strategy_id))

            for strategy_id in list(tasks):
                if strategy_id not in active_ids:
                    tasks[strategy_id].cancel()
                    del tasks[strategy_id]
        except Exception:
            # 이 루프가 죽으면 새 전략을 영영 못 집는다(설계 스펙 '에러 처리' 절) —
            # 로그만 남기고 다음 스캔 주기에 재시도한다(코드 리뷰 지적).
            logger.exception("태스크셋 스캔 중 예외 발생")

        await asyncio.sleep(_TASK_REFRESH_INTERVAL_SEC)
```
에서:
```python
async def _task_set_manager_loop() -> None:
    """20초마다 db.list_active_strategies()를 다시 조회해 태스크 집합을 갱신한다
    (설계 스펙 결정2). 새 전략 -> create_task(_run_strategy_loop) +
    create_task(_run_risk_exit_loop)(⑤-4c), 더 이상 대상 아님 -> 두 태스크 다
    task.cancel(). 전략당 asyncio.Lock을 하나 만들어 두 태스크에 동일 객체로 넘겨
    주문실행을 직렬화한다(⑤-4c 설계 스펙 결정4). 재시작 없이 새로 승인된 전략을
    자동으로 픽업한다."""
    tasks: dict[str, asyncio.Task] = {}
    risk_tasks: dict[str, asyncio.Task] = {}
    locks: dict[str, asyncio.Lock] = {}
    while True:
        try:
            active_ids = {s["id"] for s in db.list_active_strategies()}

            for strategy_id in active_ids:
                if strategy_id not in locks:
                    locks[strategy_id] = asyncio.Lock()
                if strategy_id not in tasks or tasks[strategy_id].done():
                    tasks[strategy_id] = asyncio.create_task(
                        _run_strategy_loop(strategy_id, locks[strategy_id])
                    )
                if strategy_id not in risk_tasks or risk_tasks[strategy_id].done():
                    risk_tasks[strategy_id] = asyncio.create_task(
                        _run_risk_exit_loop(strategy_id, locks[strategy_id])
                    )

            for strategy_id in list(tasks):
                if strategy_id not in active_ids:
                    tasks[strategy_id].cancel()
                    del tasks[strategy_id]
            for strategy_id in list(risk_tasks):
                if strategy_id not in active_ids:
                    risk_tasks[strategy_id].cancel()
                    del risk_tasks[strategy_id]
            for strategy_id in list(locks):
                if strategy_id not in active_ids:
                    del locks[strategy_id]
        except Exception:
            # 이 루프가 죽으면 새 전략을 영영 못 집는다(설계 스펙 '에러 처리' 절) —
            # 로그만 남기고 다음 스캔 주기에 재시도한다(코드 리뷰 지적).
            logger.exception("태스크셋 스캔 중 예외 발생")

        await asyncio.sleep(_TASK_REFRESH_INTERVAL_SEC)
```
로 교체한다.

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_daemon.py -v`
Expected: 전부 PASS(기존 28개 + 신규 3개 = 31개)

- [ ] **Step 5: 커밋**

```bash
git add trading/daemon.py tests/test_daemon.py
git commit -m "feat: _task_set_manager_loop가 전략당 lock+risk_exit 태스크를 같이 관리하도록 확장"
```

---

### Task 7: 최종 통합 확인 — 동시성 레이스 재현 + `engine/` 미의존 검증 + 전체 회귀

**Files:**
- Modify: `tests/test_daemon.py`(신규 통합 테스트 1개)
- Modify: `trading/daemon.py`(검증 중 발견된 문제가 있을 때만)

**Interfaces:**
- Consumes: 이 플랜의 모든 이전 태스크 산출물.
- Produces: 없음(검증 전용 태스크).

- [ ] **Step 1: 실제 asyncio.Lock 하나로 두 루프를 동시에 돌리는 통합 테스트 작성**

`tests/test_daemon.py` 파일 끝에 추가:
```python
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
```

- [ ] **Step 2: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_daemon.py -v -k serialize_order_execution_via_shared_lock`
Expected: PASS(Task4/6이 이미 lock을 올바르게 배선했다면 별도 수정 없이 통과해야 한다 —
실패하면 lock 배선에 구멍이 있다는 뜻이므로 Task4/6 diff를 재검토할 것)

- [ ] **Step 3: `engine/` 미의존 확인**

Run:
```bash
python -c "
import ast
tree = ast.parse(open('trading/daemon.py', encoding='utf-8').read())
names = set()
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        names.update(a.name for a in node.names)
    elif isinstance(node, ast.ImportFrom) and node.module:
        names.add(node.module)
engine_imports = {n for n in names if n == 'engine' or n.startswith('engine.')}
assert not engine_imports, f'engine 의존 발견: {engine_imports}'
print('OK:', sorted(names))
"
```
Expected: `engine` 관련 import 없음. `sorted(names)`에 `['__future__', 'asyncio',
'json', 'logging', 'time', 'trading.db', 'trading.order_executor',
'trading.position_manager', 'trading.reconciler', 'trading.risk_manager',
'trading.signal_engine', 'trading.upbit_ws', 'upbit_data_service']`가 출력됨(Task5에서
추가한 `trading.position_manager`/`trading.upbit_ws` 포함).

- [ ] **Step 4: 전체 테스트 스위트 실행(회귀 확인)**

Run: `python -m pytest -q`
Expected: 전부 PASS(⑤-4b까지의 기존 688개 + 이 플랜의 신규 테스트 전부 포함, 회귀 없음).

- [ ] **Step 5: 커밋**

Step 1~4가 전부 통과하면(검증 테스트 1개 추가 외 코드 변경이 없으면) 그 테스트만
커밋한다:
```bash
git add tests/test_daemon.py
git commit -m "test: strategy_loop-risk_exit_loop 간 lock 직렬화를 진짜 asyncio.Lock으로 재현 검증"
```
검증 중 실제 코드 수정이 필요했다면(예: lock 배선 버그 발견) 그 수정도 함께 커밋한다:
```bash
git add trading/daemon.py tests/test_daemon.py
git commit -m "fix: risk_exit_loop 최종 통합 검증에서 발견된 lock 배선 문제 수정"
```

---

## Self-Review

**스펙 커버리지:**
- 결정1(독립 안전망 평가) → Task3 `matched_risk_exit_indicator`(AND/OR 무시하고
  STOP_LOSS_PCT/TAKE_PROFIT_PCT만 매치).
- 결정2(engine/ 미의존, signal_engine.py에 로직 배치) → Task3이 daemon.py가 아니라
  `trading/signal_engine.py`에 함수를 추가, Task7 Step3의 AST 검사로 재확인.
- 결정3(전략당 개별 WS 연결) → Task5 `_run_risk_exit_loop`가 `stream_ticker([market])`
  하나만 구독.
- 결정4(전략당 Lock 공유) → Task4(`_run_strategy_loop`)+Task5(`_run_risk_exit_loop`)가
  같은 lock 파라미터를 받고, Task6(`_task_set_manager_loop`)이 동일 객체를 양쪽에
  전달, Task7이 진짜 Lock으로 레이스 재현 검증.
- 결정5(`exit_for_risk` 신규, `handle_signal_result` 미재사용) → Task2.
- 결정6(`close_reason` 파라미터) → Task1.
- 결정7(위험조건 없으면 WS 미개방, no-op 재생성 감내) → Task5의 조기 반환 +
  Task6에서 `risk_tasks[strategy_id].done()` 체크로 자연스럽게 재생성(추가 코드 불필요,
  기존 `_task_set_manager_loop`의 done() 체크 패턴을 그대로 재사용).
- 결정8(포지션 fresh 재조회) → Task5의 `position_manager.get_open_position`(매 tick)
  + `db.get_live_strategy`(트리거 직전).

**플레이스홀더 스캔:** 없음 — 모든 스텝에 완전한 코드가 있다.

**타입 일관성:** `_run_strategy_loop`/`_run_risk_exit_loop` 둘 다
`(strategy_id: str, lock: asyncio.Lock | None = None) -> None` 시그니처로 통일.
`exit_for_risk()`의 반환 `{"action": ..., "order_id": ...}`를 `_run_risk_exit_loop`가
`result["action"] == "exited"`로 정확히 소비. `matched_risk_exit_indicator()`가 반환하는
indicator 이름(`"STOP_LOSS_PCT"`/`"TAKE_PROFIT_PCT"`)을 `_run_risk_exit_loop`가
`.lower()`로 변환해 `exit_for_risk()`의 `reason` 인자와 `close_reason` 컬럼에 그대로
흘려보낸다 — Task3/Task5/Task1의 문자열 계약이 끊기지 않고 이어진다.

**스코프 경계:** `HOLDING_PERIOD_BARS`는 어느 태스크도 건드리지 않음(Task3 테스트로
명시적 확인). 승인/제어 UI(⑥)는 이 플랜과 무관 — daemon.py는 여전히 `status` 컬럼만
읽는다.
