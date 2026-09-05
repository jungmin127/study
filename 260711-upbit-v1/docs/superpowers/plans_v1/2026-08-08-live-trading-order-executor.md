# 라이브 트레이딩 서브플랜⑤-3 — order_executor.py Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **워크트리를 만들지 말고 main 브랜치에서 직접 작업한다** (사용자 지시, [[upbit-v1-worktree-workflow-changed]]).

**Goal:** `trading/order_executor.py`를 만들어, 서브플랜⑤-2(`signal_engine.py`)의 신호평가
결과를 받아 실제 업비트 주문(매수/매도, 4가지 실행모드)을 내고 `positions`/서킷브레이커
상태까지 갱신한다.

**Architecture:** 설계 스펙 `docs/superpowers/specs_v1/2026-08-08-live-trading-order-executor-design.md`를
그대로 구현한다. 서브플랜⑤(트레이딩 엔진 코어) 4단계 중 세 번째다. `order_executor.py`는
`trading.upbit_client`(async REST) + `trading.position_manager` + `trading.risk_manager` +
`trading.db`만 import한다 — `engine/`은 전혀 의존하지 않는다(신호 결과의 True/False/id만
다루므로 조건평가 로직 자체와 무관).

**Tech Stack:** Python, `httpx`(이미 사용 중), `asyncio`, `pytest`(+`pytest-asyncio`,
`asyncio_mode = auto`로 이미 설정됨). 새 의존성 없음.

## Global Constraints

- `trading/order_executor.py`는 **하나의 파일로 유지**한다(다른 `trading/` 모듈과 동일 관례).
- `engine/`은 전혀 import하지 않는다(⑤-2의 `signal_engine.py`와 달리 `engine.condition_tree`도
  필요 없음 — 이 모듈은 이미 계산된 신호 결과만 다룬다).
- `httpx`/`trading.upbit_client` 외의 새 외부 API 클라이언트 라이브러리를 추가하지 않는다.
- `upbit_client`를 직접 `monkeypatch`하는 테스트는 `trading.upbit_client` 모듈 객체의
  함수를 패치한다(`order_executor.py`가 `import trading.upbit_client as upbit_client`로
  모듈 참조를 갖고 있으므로, 어느 파일에서 import했든 같은 모듈 객체가 패치된다).
- 커밋은 태스크 단위로 작게, 테스트가 통과한 뒤에만 한다.

---

## File Structure

- **Modify:** `trading/db.py` — `orders` CRUD(`insert_order`/`update_order_filled`/
  `get_order_by_id`) + `signals.resulting_order_id` 갱신 함수(`update_signal_result`) 추가.
- **Modify:** `tests/test_trading_db.py` — 위 CRUD 테스트 추가.
- **Modify:** `trading/signal_engine.py` — `evaluate_signals()` 반환값에
  `buy_signal_id`/`sell_signal_id`/`latest_close` 추가.
- **Modify:** `tests/test_signal_engine.py` — 반환값 확장에 대한 회귀 테스트 추가(기존
  11개는 그대로 유지).
- **Create:** `trading/order_executor.py` — 틱사이즈 라운딩, 네트워크 헬퍼, 4가지 실행모드,
  `enter()`/`exit()`/`handle_signal_result()`.
- **Create:** `tests/test_order_executor.py`.

---

### Task 1: `trading/db.py` — `orders` CRUD + `update_signal_result`

**Files:**
- Modify: `trading/db.py`
- Modify: `tests/test_trading_db.py`

**Interfaces:**
- Consumes: `trading.db._connect()`(기존), `tests.trading_db_fixtures.insert_live_strategy`(⑤-1).
- Produces: `trading.db.insert_order(live_strategy_id, position_id, market, side, order_type,
  requested_price, requested_volume, expected_price, *, replaces_order_id=None) -> str`,
  `trading.db.update_order_filled(order_id, upbit_uuid, filled_price, filled_volume, fee,
  slippage_pct, status) -> None`, `trading.db.get_order_by_id(order_id) -> dict | None`,
  `trading.db.update_signal_result(signal_id, resulting_order_id, skip_reason) -> None`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_trading_db.py` 파일 끝에 추가:
```python
def test_insert_order_creates_wait_row(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)

    order_id = db.insert_order(
        strategy_id, None, "KRW-BTC", "bid", "market", 50000000.0, 0.01, 50000000.0,
    )

    order = db.get_order_by_id(order_id)
    assert order["live_strategy_id"] == strategy_id
    assert order["position_id"] is None
    assert order["market"] == "KRW-BTC"
    assert order["side"] == "bid"
    assert order["order_type"] == "market"
    assert order["requested_price"] == 50000000.0
    assert order["requested_volume"] == 0.01
    assert order["expected_price"] == 50000000.0
    assert order["status"] == "wait"
    assert order["replaces_order_id"] is None


def test_insert_order_with_replaces_order_id(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    original_id = db.insert_order(strategy_id, None, "KRW-BTC", "bid", "limit", 100.0, 1.0, 100.0)

    child_id = db.insert_order(
        strategy_id, None, "KRW-BTC", "bid", "market", None, 0.5, 100.0,
        replaces_order_id=original_id,
    )

    order = db.get_order_by_id(child_id)
    assert order["replaces_order_id"] == original_id


def test_update_order_filled_sets_fill_fields_and_updated_at(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    order_id = db.insert_order(strategy_id, None, "KRW-BTC", "bid", "market", 100.0, 1.0, 100.0)

    db.update_order_filled(order_id, "upbit-uuid-1", 101.0, 1.0, 0.05, 1.0, "done")

    order = db.get_order_by_id(order_id)
    assert order["upbit_uuid"] == "upbit-uuid-1"
    assert order["filled_price"] == 101.0
    assert order["filled_volume"] == 1.0
    assert order["fee"] == 0.05
    assert order["slippage_pct"] == 1.0
    assert order["status"] == "done"
    assert order["updated_at"] is not None


def test_get_order_by_id_returns_none_when_missing(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    assert db.get_order_by_id("nonexistent") is None


def test_update_signal_result_sets_resulting_order_id(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    signal_id = db.insert_signal(strategy_id, "buy", "2026-08-08T10:00:00+00:00", "{}")
    order_id = db.insert_order(strategy_id, None, "KRW-BTC", "bid", "market", 100.0, 1.0, 100.0)

    db.update_signal_result(signal_id, order_id, None)

    conn = db._connect()
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
    finally:
        conn.close()
    assert row["resulting_order_id"] == order_id
    assert row["skip_reason"] is None


def test_update_signal_result_sets_skip_reason_without_order(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    signal_id = db.insert_signal(strategy_id, "buy", "2026-08-08T10:00:00+00:00", "{}")

    db.update_signal_result(signal_id, None, "circuit_breaker_tripped")

    conn = db._connect()
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
    finally:
        conn.close()
    assert row["resulting_order_id"] is None
    assert row["skip_reason"] == "circuit_breaker_tripped"
```

(`sqlite3`와 `insert_live_strategy`는 이 파일 상단에 이미 import돼 있다 — 새로 추가할 필요
없음.)

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_trading_db.py -v -k "insert_order or update_order_filled or get_order_by_id or update_signal_result"`
Expected: FAIL — `AttributeError: module 'trading.db' has no attribute 'insert_order'`

- [ ] **Step 3: `trading/db.py`에 구현 추가**

파일 끝(`insert_signal` 함수 뒤)에 추가:
```python


def insert_order(
    live_strategy_id: str, position_id: str | None, market: str, side: str, order_type: str,
    requested_price: float | None, requested_volume: float | None, expected_price: float | None,
    *, replaces_order_id: str | None = None,
) -> str:
    order_id = str(uuid.uuid4())
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO orders "
            "(id, live_strategy_id, position_id, replaces_order_id, market, side, order_type, "
            "requested_price, requested_volume, expected_price, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'wait')",
            (order_id, live_strategy_id, position_id, replaces_order_id, market, side, order_type,
             requested_price, requested_volume, expected_price),
        )
        conn.commit()
    finally:
        conn.close()
    return order_id


def update_order_filled(
    order_id: str, upbit_uuid: str | None, filled_price: float | None,
    filled_volume: float | None, fee: float | None, slippage_pct: float | None, status: str,
) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE orders SET upbit_uuid=?, filled_price=?, filled_volume=?, fee=?, "
            "slippage_pct=?, status=?, updated_at=datetime('now') WHERE id=?",
            (upbit_uuid, filled_price, filled_volume, fee, slippage_pct, status, order_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_order_by_id(order_id: str) -> dict | None:
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_signal_result(
    signal_id: str, resulting_order_id: str | None, skip_reason: str | None,
) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE signals SET resulting_order_id=?, skip_reason=? WHERE id=?",
            (resulting_order_id, skip_reason, signal_id),
        )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_trading_db.py -v`
Expected: 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add trading/db.py tests/test_trading_db.py
git commit -m "feat: trading/db.py에 orders CRUD + update_signal_result 추가"
```

---

### Task 2: `trading/signal_engine.py` — `evaluate_signals()` 반환값 확장

**Files:**
- Modify: `trading/signal_engine.py`
- Modify: `tests/test_signal_engine.py`

**Interfaces:**
- Consumes: `trading.db.insert_signal`(기존, 반환값을 이제 사용).
- Produces: `evaluate_signals()` 반환 dict에 `buy_signal_id: str`, `sell_signal_id: str`,
  `latest_close: float` 3개 키 추가(기존 키는 전부 유지).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_signal_engine.py` 파일 끝에 추가:
```python
def test_evaluate_signals_returns_signal_ids_and_latest_close(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    df = make_oscillating_df()
    buy_json, sell_json = _strategy_conditions()
    strategy_id = insert_live_strategy(
        dbm, buy_conditions_json=buy_json, sell_conditions_json=sell_json,
    )
    monkeypatch.setattr(signal_engine, "get_candles", lambda market, timeframe, start, end: df)

    result = signal_engine.evaluate_signals(strategy_id, now=datetime.now(timezone.utc))

    assert result["latest_close"] == pytest.approx(df["close"].iloc[-1])

    conn = dbm._connect()
    try:
        conn.row_factory = __import__("sqlite3").Row
        buy_row = conn.execute(
            "SELECT * FROM signals WHERE id = ?", (result["buy_signal_id"],)
        ).fetchone()
        sell_row = conn.execute(
            "SELECT * FROM signals WHERE id = ?", (result["sell_signal_id"],)
        ).fetchone()
    finally:
        conn.close()
    assert buy_row["signal_type"] == "buy"
    assert sell_row["signal_type"] == "sell"
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_signal_engine.py -v -k signal_ids_and_latest_close`
Expected: FAIL — `KeyError: 'latest_close'`

- [ ] **Step 3: `trading/signal_engine.py` 수정**

`evaluate_signals()` 안의 다음 블록을:
```python
    db.insert_signal(
        live_strategy_id, "buy", candle_time_str, snapshot_json,
        skip_reason="unknown" if buy_result is None else None,
    )
    db.insert_signal(
        live_strategy_id, "sell", candle_time_str, snapshot_json,
        skip_reason="unknown" if sell_result is None else None,
    )
```
로:
```python
    buy_signal_id = db.insert_signal(
        live_strategy_id, "buy", candle_time_str, snapshot_json,
        skip_reason="unknown" if buy_result is None else None,
    )
    sell_signal_id = db.insert_signal(
        live_strategy_id, "sell", candle_time_str, snapshot_json,
        skip_reason="unknown" if sell_result is None else None,
    )
```
로 교체하고, 이어지는 반환문:
```python
    return {
        "new_candle": True,
        "candle_time": candle_time_str,
        "buy_signal": buy_result,
        "sell_signal": sell_result,
        "paused": paused,
        "resumed": resumed,
    }
```
을:
```python
    return {
        "new_candle": True,
        "candle_time": candle_time_str,
        "buy_signal": buy_result,
        "sell_signal": sell_result,
        "buy_signal_id": buy_signal_id,
        "sell_signal_id": sell_signal_id,
        "latest_close": float(latest_close),
        "paused": paused,
        "resumed": resumed,
    }
```
로 교체한다.

- [ ] **Step 4: 테스트 실행해서 통과 확인 + 전체 회귀**

Run: `python -m pytest tests/test_signal_engine.py -v`
Expected: 기존 16개 + 신규 1개 = 17개 전부 PASS

Run: `python -m pytest -q`
Expected: 전부 PASS(회귀 없음 — 반환 dict에 키만 추가했고 기존 테스트는 특정 키만
인덱싱하므로 깨지지 않는다)

- [ ] **Step 5: 커밋**

```bash
git add trading/signal_engine.py tests/test_signal_engine.py
git commit -m "feat: evaluate_signals 반환값에 buy_signal_id/sell_signal_id/latest_close 추가"
```

---

### Task 3: `trading/order_executor.py` — 틱사이즈 테이블 + `round_to_tick()` + `_floor_volume()`

**Files:**
- Create: `trading/order_executor.py`
- Create: `tests/test_order_executor.py`

**Interfaces:**
- Consumes: 없음(순수 함수).
- Produces: `trading.order_executor.round_to_tick(price: float) -> float`,
  `trading.order_executor._floor_volume(volume: float) -> float`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_order_executor.py`(신규 파일):
```python
import trading.order_executor as order_executor


def test_round_to_tick_boundaries():
    assert order_executor.round_to_tick(2_500_000) == 2_500_000  # 1,000,000원 이상 → 1,000원 단위
    assert order_executor.round_to_tick(2_500_400) == 2_500_000
    assert order_executor.round_to_tick(999_760) == 999_500  # 500,000~1,000,000 → 500원 단위이므로 반올림 값 확인
    assert order_executor.round_to_tick(150_030) == 150_000  # 100,000~500,000 → 100원 단위
    assert order_executor.round_to_tick(9_998) == 10_000  # 5,000~10,000 → 5원 단위, 반올림
    assert order_executor.round_to_tick(4_500) == 4_500  # 1,000~5,000 → 1원 단위
    assert order_executor.round_to_tick(55) == 55.0  # 10~100 → 0.1원 단위
    assert order_executor.round_to_tick(5.678) == 5.68  # 1~10 → 0.01원 단위


def test_floor_volume_truncates_to_eight_decimals():
    assert order_executor._floor_volume(0.123456789) == 0.12345678
    assert order_executor._floor_volume(1.0) == 1.0
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_order_executor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'trading.order_executor'`

- [ ] **Step 3: `trading/order_executor.py` 구현**

```python
"""
trading/order_executor.py

signal_engine.evaluate_signals() 결과를 받아 실제 업비트 주문(매수/매도)을 실행한다.
market/limit/limit_timeout/market_capped 4가지 실행모드를 지원하고, 서킷브레이커 확인 →
enter()/exit() 호출 → signals.resulting_order_id 갱신까지 handle_signal_result()가 한 번에
처리한다(설계 스펙 결정3). trading.upbit_client(async REST) + trading.position_manager +
trading.risk_manager를 엮는 이 서브플랜의 유일한 모듈. engine/ 미의존.
"""
from __future__ import annotations

import math

# 업비트 원화마켓 주문가격단위(2026-08 기준, docs.upbit.com/kr/docs/krw-market-info).
# orders/chance 응답의 price_unit은 deprecated라 쓰지 않는다(설계 스펙 결정1) — 업비트가
# 이 표를 바꾸면(2023/2024년 실제 변경 이력 있음) 수동으로 갱신해야 한다.
_TICK_TABLE: list[tuple[float, float]] = [
    (1_000_000, 1000),
    (500_000, 500),
    (100_000, 100),
    (50_000, 50),
    (10_000, 10),
    (5_000, 5),
    (100, 1),
    (10, 0.1),
    (1, 0.01),
    (0.1, 0.001),
    (0.01, 0.0001),
    (0.001, 0.00001),
    (0.0001, 0.000001),
    (0.00001, 0.0000001),
    (0, 0.00000001),
]


def round_to_tick(price: float) -> float:
    for threshold, tick in _TICK_TABLE:
        if price >= threshold:
            return round(round(price / tick) * tick, 8)
    return price


def _floor_volume(volume: float) -> float:
    return math.floor(volume * 1e8) / 1e8
```

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_order_executor.py -v`
Expected: 2개 테스트 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add trading/order_executor.py tests/test_order_executor.py
git commit -m "feat: order_executor에 틱사이즈 라운딩(round_to_tick) + 수량절삭 추가"
```

---

### Task 4: `trading/order_executor.py` — 네트워크 헬퍼(`_fetch_fill`, `_create_order_with_retry`)

**Files:**
- Modify: `trading/order_executor.py`
- Modify: `tests/test_order_executor.py`

**Interfaces:**
- Consumes: `trading.upbit_client.create_order`/`get_order`(async, 기존).
- Produces: `trading.order_executor._fetch_fill(upbit_uuid, *, client=None) -> dict`(키:
  `state`/`executed_volume`/`remaining_volume`/`filled_price`/`fee`),
  `trading.order_executor._create_order_with_retry(market, side, ord_type, *, order_id,
  volume=None, price=None, time_in_force=None, client=None) -> dict`,
  `trading.order_executor._slippage_pct(filled_price, expected_price) -> float`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_order_executor.py` 파일 끝에 추가:
```python
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
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_order_executor.py -v -k "fetch_fill or slippage_pct or create_order_with_retry"`
Expected: FAIL — `AttributeError: module 'trading.order_executor' has no attribute '_fetch_fill'`

- [ ] **Step 3: `trading/order_executor.py`에 구현 추가**

import 블록을:
```python
from __future__ import annotations

import math
```
에서:
```python
from __future__ import annotations

import math

import httpx

import trading.upbit_client as upbit_client
```
로 교체한다.

파일 끝(`_floor_volume` 함수 뒤)에 추가:
```python


async def _create_order_with_retry(
    market: str, side: str, ord_type: str, *, order_id: str,
    volume: str | None = None, price: str | None = None, time_in_force: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict:
    """create_order()가 네트워크 에러/타임아웃으로 응답을 못 받으면 identifier로 재조회해
    실제로 주문이 들어갔는지 확인한 뒤에만 1회 재시도한다(설계 스펙 결정5, 이중주문 방지)."""
    try:
        return await upbit_client.create_order(
            market, side, ord_type, volume=volume, price=price,
            time_in_force=time_in_force, identifier=order_id, client=client,
        )
    except (httpx.TransportError, httpx.TimeoutException):
        try:
            return await upbit_client.get_order(identifier=order_id, client=client)
        except httpx.HTTPStatusError:
            return await upbit_client.create_order(
                market, side, ord_type, volume=volume, price=price,
                time_in_force=time_in_force, identifier=order_id, client=client,
            )


async def _fetch_fill(upbit_uuid: str, *, client: httpx.AsyncClient | None = None) -> dict:
    """get_order()로 체결 결과를 조회한다. 평균체결가는 trades[].funds 합계 ÷
    executed_volume으로 계산한다(업비트 공식 문서 기준)."""
    resp = await upbit_client.get_order(uuid=upbit_uuid, client=client)
    executed_volume = float(resp["executed_volume"])
    filled_price = (
        sum(float(t["funds"]) for t in resp["trades"]) / executed_volume
        if executed_volume > 0 else None
    )
    return {
        "state": resp["state"],
        "executed_volume": executed_volume,
        "remaining_volume": float(resp["remaining_volume"]),
        "filled_price": filled_price,
        "fee": float(resp["paid_fee"]),
    }


def _slippage_pct(filled_price: float, expected_price: float) -> float:
    return (filled_price - expected_price) / expected_price * 100
```

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_order_executor.py -v`
Expected: 전부 PASS(2 + 7 = 9개)

- [ ] **Step 5: 커밋**

```bash
git add trading/order_executor.py tests/test_order_executor.py
git commit -m "feat: order_executor에 네트워크 헬퍼(_fetch_fill, _create_order_with_retry) 추가"
```

---

### Task 5: `trading/order_executor.py` — `enter()`/`exit()` 뼈대 + `market` 모드 + `dry_run`

**Files:**
- Modify: `trading/order_executor.py`
- Modify: `tests/test_order_executor.py`

**Interfaces:**
- Consumes: Task1의 `db.insert_order`/`update_order_filled`/`get_order_by_id`, Task3~4의
  헬퍼, `trading.position_manager.get_open_position`/`open_position`/`close_position`(⑤-1).
- Produces: `trading.order_executor.enter(strategy, capital, expected_price, *, client=None,
  dry_run=False) -> dict`, `trading.order_executor.exit(strategy, position, expected_price,
  *, client=None, dry_run=False) -> dict`. 이 태스크에서는 `order_execution_mode`가
  `"market"`이거나 `dry_run=True`인 경우만 지원 — 다른 모드는 `ValueError`(Task6~8에서
  분기 추가).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_order_executor.py` 파일 끝에 추가:
```python
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
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_order_executor.py -v -k "test_enter or test_exit"`
Expected: FAIL — `AttributeError: module 'trading.order_executor' has no attribute 'enter'`

- [ ] **Step 3: `trading/order_executor.py`에 구현 추가**

import 블록을:
```python
from __future__ import annotations

import math

import httpx

import trading.upbit_client as upbit_client
```
에서:
```python
from __future__ import annotations

import json
import math

import httpx

import trading.db as db
import trading.position_manager as position_manager
import trading.upbit_client as upbit_client
```
로 교체한다.

파일 끝(`_slippage_pct` 함수 뒤)에 추가:
```python


async def _run_market(
    order_id: str, market: str, side: str, capital: float | None, volume: float,
    expected_price: float, *, client: httpx.AsyncClient | None = None,
) -> dict:
    if side == "bid":
        resp = await _create_order_with_retry(
            market, "bid", "price", order_id=order_id, price=str(capital), client=client,
        )
    else:
        resp = await _create_order_with_retry(
            market, "ask", "market", order_id=order_id, volume=str(volume), client=client,
        )
    fill = await _fetch_fill(resp["uuid"], client=client)
    db.update_order_filled(
        order_id, resp["uuid"], fill["filled_price"], fill["executed_volume"], fill["fee"],
        _slippage_pct(fill["filled_price"], expected_price), "done",
    )
    return {"order_id": order_id, "status": "done", "filled_price": fill["filled_price"],
            "filled_volume": fill["executed_volume"], "fee": fill["fee"]}


async def enter(
    strategy: dict, capital: float, expected_price: float,
    *, client: httpx.AsyncClient | None = None, dry_run: bool = False,
) -> dict:
    if position_manager.get_open_position(strategy["id"]) is not None:
        raise ValueError(f"이미 오픈 포지션이 있습니다: {strategy['id']}")

    risk_config = json.loads(strategy["risk_config_json"])
    mode = risk_config["order_execution_mode"]
    market = strategy["market"]
    price = round_to_tick(expected_price)
    volume = _floor_volume(capital / price)

    order_id = db.insert_order(strategy["id"], None, market, "bid", mode, price, volume, expected_price)

    if dry_run:
        db.update_order_filled(order_id, None, price, volume, 0.0, 0.0, "done")
        result = {"order_id": order_id, "status": "done", "filled_price": price,
                   "filled_volume": volume, "fee": 0.0}
    elif mode == "market":
        result = await _run_market(order_id, market, "bid", capital, volume, expected_price, client=client)
    else:
        raise ValueError(f"지원하지 않는 order_execution_mode: {mode}")

    if result["status"] != "done":
        return db.get_order_by_id(result["order_id"])

    position_manager.open_position(strategy["id"], market, result["filled_price"], result["filled_volume"])
    return db.get_order_by_id(result["order_id"])


async def exit(
    strategy: dict, position: dict, expected_price: float,
    *, client: httpx.AsyncClient | None = None, dry_run: bool = False,
) -> dict:
    if position is None:
        raise ValueError("오픈 포지션이 없습니다")

    risk_config = json.loads(strategy["risk_config_json"])
    mode = risk_config["order_execution_mode"]
    market = strategy["market"]
    price = round_to_tick(expected_price)
    volume = position["entry_qty"]

    order_id = db.insert_order(strategy["id"], position["id"], market, "ask", mode, price, volume, expected_price)

    if dry_run:
        db.update_order_filled(order_id, None, price, volume, 0.0, 0.0, "done")
        result = {"order_id": order_id, "status": "done", "filled_price": price,
                   "filled_volume": volume, "fee": 0.0}
    elif mode == "market":
        result = await _run_market(order_id, market, "ask", None, volume, expected_price, client=client)
    else:
        raise ValueError(f"지원하지 않는 order_execution_mode: {mode}")

    if result["status"] != "done":
        return db.get_order_by_id(result["order_id"])

    close_result = position_manager.close_position(
        position["id"], result["filled_price"], result["filled_volume"], result["fee"], "signal",
    )
    order = db.get_order_by_id(result["order_id"])
    order.update(close_result)
    return order
```

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_order_executor.py -v`
Expected: 전부 PASS(9 + 5 = 14개)

- [ ] **Step 5: 커밋**

```bash
git add trading/order_executor.py tests/test_order_executor.py
git commit -m "feat: order_executor에 enter()/exit() 뼈대 + market 모드 + dry_run 추가"
```

---

### Task 6: `trading/order_executor.py` — `limit` 모드(타임아웃 없음)

**Files:**
- Modify: `trading/order_executor.py`
- Modify: `tests/test_order_executor.py`

**Interfaces:**
- Consumes: Task4의 `_create_order_with_retry`.
- Produces: `trading.order_executor._run_limit(order_id, market, side, price, volume, *,
  client=None) -> dict`. `enter()`/`exit()`가 `order_execution_mode == "limit"`을 지원.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_order_executor.py` 파일 끝에 추가:
```python
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
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_order_executor.py -v -k limit_mode_leaves`
Expected: FAIL — `ValueError: 지원하지 않는 order_execution_mode: limit`

- [ ] **Step 3: `trading/order_executor.py`에 구현 추가**

`_run_market` 함수 바로 뒤에 추가:
```python


async def _run_limit(
    order_id: str, market: str, side: str, price: float, volume: float,
    *, client: httpx.AsyncClient | None = None,
) -> dict:
    resp = await _create_order_with_retry(
        market, side, "limit", order_id=order_id, price=str(price), volume=str(volume), client=client,
    )
    db.update_order_filled(order_id, resp["uuid"], None, None, None, None, "wait")
    return {"order_id": order_id, "status": "wait", "filled_price": None, "filled_volume": None, "fee": None}
```

`enter()` 안의 다음 블록을:
```python
    elif mode == "market":
        result = await _run_market(order_id, market, "bid", capital, volume, expected_price, client=client)
    else:
        raise ValueError(f"지원하지 않는 order_execution_mode: {mode}")
```
로:
```python
    elif mode == "market":
        result = await _run_market(order_id, market, "bid", capital, volume, expected_price, client=client)
    elif mode == "limit":
        result = await _run_limit(order_id, market, "bid", price, volume, client=client)
    else:
        raise ValueError(f"지원하지 않는 order_execution_mode: {mode}")
```
로 교체한다.

`exit()` 안의 다음 블록을:
```python
    elif mode == "market":
        result = await _run_market(order_id, market, "ask", None, volume, expected_price, client=client)
    else:
        raise ValueError(f"지원하지 않는 order_execution_mode: {mode}")
```
로:
```python
    elif mode == "market":
        result = await _run_market(order_id, market, "ask", None, volume, expected_price, client=client)
    elif mode == "limit":
        result = await _run_limit(order_id, market, "ask", price, volume, client=client)
    else:
        raise ValueError(f"지원하지 않는 order_execution_mode: {mode}")
```
로 교체한다.

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_order_executor.py -v`
Expected: 전부 PASS(14 + 1 = 15개)

- [ ] **Step 5: 커밋**

```bash
git add trading/order_executor.py tests/test_order_executor.py
git commit -m "feat: order_executor에 limit 모드(타임아웃 없음) 추가"
```

---

### Task 7: `trading/order_executor.py` — `limit_timeout` 모드

**Files:**
- Modify: `trading/order_executor.py`
- Modify: `tests/test_order_executor.py`

**Interfaces:**
- Consumes: Task4의 `_create_order_with_retry`/`_fetch_fill`, `trading.upbit_client.cancel_order`.
- Produces: `trading.order_executor._run_limit_timeout(order_id, live_strategy_id,
  position_id, market, side, price, volume, expected_price, timeout_sec, *, client=None) ->
  dict`. `enter()`/`exit()`가 `order_execution_mode == "limit_timeout"`을 지원.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_order_executor.py` 파일 끝에 추가:
```python
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
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_order_executor.py -v -k limit_timeout`
Expected: FAIL — `ValueError: 지원하지 않는 order_execution_mode: limit_timeout`

- [ ] **Step 3: `trading/order_executor.py`에 구현 추가**

import 블록에 `import math` 다음 줄로 `import asyncio`를 추가한다(알파벳 순서 유지):
```python
from __future__ import annotations

import asyncio
import json
import math
```

`_run_limit` 함수 바로 뒤에 추가:
```python


async def _run_limit_timeout(
    order_id: str, live_strategy_id: str, position_id: str | None, market: str, side: str,
    price: float, volume: float, expected_price: float, timeout_sec: float,
    *, client: httpx.AsyncClient | None = None,
) -> dict:
    resp = await _create_order_with_retry(
        market, side, "limit", order_id=order_id, price=str(price), volume=str(volume), client=client,
    )
    await asyncio.sleep(timeout_sec)
    fill = await _fetch_fill(resp["uuid"], client=client)

    if fill["state"] == "done":
        db.update_order_filled(
            order_id, resp["uuid"], fill["filled_price"], fill["executed_volume"], fill["fee"],
            _slippage_pct(fill["filled_price"], expected_price), "done",
        )
        return {"order_id": order_id, "status": "done", "filled_price": fill["filled_price"],
                "filled_volume": fill["executed_volume"], "fee": fill["fee"]}

    await upbit_client.cancel_order(uuid=resp["uuid"], client=client)
    first_volume = fill["executed_volume"]
    first_funds = fill["filled_price"] * first_volume if first_volume else 0.0
    first_fee = fill["fee"]
    db.update_order_filled(order_id, resp["uuid"], fill["filled_price"], first_volume, first_fee, None, "cancel")

    remaining_volume = fill["remaining_volume"]
    market_order_id = db.insert_order(
        live_strategy_id, position_id, market, side, "market", None, remaining_volume, expected_price,
        replaces_order_id=order_id,
    )
    if side == "bid":
        market_resp = await _create_order_with_retry(
            market, "bid", "price", order_id=market_order_id,
            price=str(round_to_tick(expected_price) * remaining_volume), client=client,
        )
    else:
        market_resp = await _create_order_with_retry(
            market, "ask", "market", order_id=market_order_id, volume=str(remaining_volume), client=client,
        )
    second_fill = await _fetch_fill(market_resp["uuid"], client=client)

    total_volume = first_volume + second_fill["executed_volume"]
    total_funds = first_funds + second_fill["filled_price"] * second_fill["executed_volume"]
    total_fee = first_fee + second_fill["fee"]
    avg_price = total_funds / total_volume
    db.update_order_filled(
        market_order_id, market_resp["uuid"], avg_price, total_volume, total_fee,
        _slippage_pct(avg_price, expected_price), "done",
    )
    return {"order_id": market_order_id, "status": "done", "filled_price": avg_price,
            "filled_volume": total_volume, "fee": total_fee}
```

`enter()` 안의 `elif mode == "limit":` 줄이 있는 블록을:
```python
    elif mode == "limit":
        result = await _run_limit(order_id, market, "bid", price, volume, client=client)
    else:
        raise ValueError(f"지원하지 않는 order_execution_mode: {mode}")
```
에서:
```python
    elif mode == "limit":
        result = await _run_limit(order_id, market, "bid", price, volume, client=client)
    elif mode == "limit_timeout":
        timeout_sec = risk_config.get("order_timeout_sec", 10)
        result = await _run_limit_timeout(
            order_id, strategy["id"], None, market, "bid", price, volume, expected_price,
            timeout_sec, client=client,
        )
    else:
        raise ValueError(f"지원하지 않는 order_execution_mode: {mode}")
```
로 교체한다.

`exit()` 안의 동일한 형태 블록도 같은 방식으로:
```python
    elif mode == "limit":
        result = await _run_limit(order_id, market, "ask", price, volume, client=client)
    elif mode == "limit_timeout":
        timeout_sec = risk_config.get("order_timeout_sec", 10)
        result = await _run_limit_timeout(
            order_id, strategy["id"], position["id"], market, "ask", price, volume, expected_price,
            timeout_sec, client=client,
        )
    else:
        raise ValueError(f"지원하지 않는 order_execution_mode: {mode}")
```
로 교체한다.

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_order_executor.py -v`
Expected: 전부 PASS(15 + 2 = 17개)

- [ ] **Step 5: 커밋**

```bash
git add trading/order_executor.py tests/test_order_executor.py
git commit -m "feat: order_executor에 limit_timeout 모드(타임아웃 후 잔량 시장가 전환) 추가"
```

---

### Task 8: `trading/order_executor.py` — `market_capped` 모드(슬리피지 상한 + FOK)

**Files:**
- Modify: `trading/order_executor.py`
- Modify: `tests/test_order_executor.py`

**Interfaces:**
- Consumes: Task4의 `_create_order_with_retry`/`_fetch_fill`.
- Produces: `trading.order_executor._run_market_capped(order_id, market, side,
  expected_price, volume, max_slippage_pct, *, client=None) -> dict`. `enter()`/`exit()`가
  `order_execution_mode == "market_capped"`를 지원.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_order_executor.py` 파일 끝에 추가:
```python
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
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_order_executor.py -v -k market_capped`
Expected: FAIL — `ValueError: 지원하지 않는 order_execution_mode: market_capped`

- [ ] **Step 3: `trading/order_executor.py`에 구현 추가**

`_run_limit_timeout` 함수 바로 뒤에 추가:
```python


async def _run_market_capped(
    order_id: str, market: str, side: str, expected_price: float, volume: float,
    max_slippage_pct: float, *, client: httpx.AsyncClient | None = None,
) -> dict:
    sign = 1 if side == "bid" else -1
    capped_price = round_to_tick(expected_price * (1 + sign * max_slippage_pct / 100))
    resp = await _create_order_with_retry(
        market, side, "limit", order_id=order_id,
        price=str(capped_price), volume=str(volume), time_in_force="fok", client=client,
    )
    fill = await _fetch_fill(resp["uuid"], client=client)
    if fill["state"] != "done" or fill["executed_volume"] == 0:
        db.update_order_filled(order_id, resp["uuid"], None, None, None, None, "cancel")
        return {"order_id": order_id, "status": "cancel", "filled_price": None,
                "filled_volume": None, "fee": None}

    db.update_order_filled(
        order_id, resp["uuid"], fill["filled_price"], fill["executed_volume"], fill["fee"],
        _slippage_pct(fill["filled_price"], expected_price), "done",
    )
    return {"order_id": order_id, "status": "done", "filled_price": fill["filled_price"],
            "filled_volume": fill["executed_volume"], "fee": fill["fee"]}
```

`enter()` 안의(`"bid"`/`None`으로 식별 — `exit()`의 같은 모양 블록과 구분됨) 다음 블록을:
```python
    elif mode == "limit_timeout":
        timeout_sec = risk_config.get("order_timeout_sec", 10)
        result = await _run_limit_timeout(
            order_id, strategy["id"], None, market, "bid", price, volume, expected_price,
            timeout_sec, client=client,
        )
    else:
        raise ValueError(f"지원하지 않는 order_execution_mode: {mode}")
```
로:
```python
    elif mode == "limit_timeout":
        timeout_sec = risk_config.get("order_timeout_sec", 10)
        result = await _run_limit_timeout(
            order_id, strategy["id"], None, market, "bid", price, volume, expected_price,
            timeout_sec, client=client,
        )
    elif mode == "market_capped":
        result = await _run_market_capped(
            order_id, market, "bid", expected_price, volume, risk_config["max_slippage_pct"], client=client,
        )
    else:
        raise ValueError(f"지원하지 않는 order_execution_mode: {mode}")
```
로 교체한다.

`exit()` 안의(`"ask"`/`position["id"]`로 식별) 다음 블록을:
```python
    elif mode == "limit_timeout":
        timeout_sec = risk_config.get("order_timeout_sec", 10)
        result = await _run_limit_timeout(
            order_id, strategy["id"], position["id"], market, "ask", price, volume, expected_price,
            timeout_sec, client=client,
        )
    else:
        raise ValueError(f"지원하지 않는 order_execution_mode: {mode}")
```
로:
```python
    elif mode == "limit_timeout":
        timeout_sec = risk_config.get("order_timeout_sec", 10)
        result = await _run_limit_timeout(
            order_id, strategy["id"], position["id"], market, "ask", price, volume, expected_price,
            timeout_sec, client=client,
        )
    elif mode == "market_capped":
        result = await _run_market_capped(
            order_id, market, "ask", expected_price, volume, risk_config["max_slippage_pct"], client=client,
        )
    else:
        raise ValueError(f"지원하지 않는 order_execution_mode: {mode}")
```
로 교체한다.

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_order_executor.py -v`
Expected: 전부 PASS(17 + 3 = 20개)

- [ ] **Step 5: 커밋**

```bash
git add trading/order_executor.py tests/test_order_executor.py
git commit -m "feat: order_executor에 market_capped 모드(슬리피지상한+FOK) 추가"
```

---

### Task 9: `trading/order_executor.py` — `handle_signal_result()`

**Files:**
- Modify: `trading/order_executor.py`
- Modify: `tests/test_order_executor.py`

**Interfaces:**
- Consumes: Task2의 `evaluate_signals()` 반환 형태, Task5~8의 `enter`/`exit`,
  `trading.risk_manager.is_circuit_tripped_today`/`record_trade_result`(⑤-1),
  `trading.db.get_live_strategy`/`update_signal_result`.
- Produces: `trading.order_executor.handle_signal_result(strategy_id, signal_result, *,
  dry_run=False) -> dict`(키: `buy_action`/`sell_action`/`buy_order_id`/`sell_order_id`).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_order_executor.py` 파일 끝에 추가:
```python
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
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_order_executor.py -v -k handle_signal_result`
Expected: FAIL — `AttributeError: module 'trading.order_executor' has no attribute
'handle_signal_result'`

- [ ] **Step 3: `trading/order_executor.py`에 구현 추가**

import 블록에 `import trading.position_manager as position_manager` 다음 줄로 추가:
```python
import trading.risk_manager as risk_manager
```

파일 끝(`exit` 함수 뒤)에 추가:
```python


async def handle_signal_result(
    strategy_id: str, signal_result: dict, *, dry_run: bool = False,
) -> dict:
    result = {"buy_action": None, "sell_action": None, "buy_order_id": None, "sell_order_id": None}

    if signal_result["paused"]:
        return result

    strategy = db.get_live_strategy(strategy_id)
    risk_config = json.loads(strategy["risk_config_json"])
    position = position_manager.get_open_position(strategy_id)
    expected_price = signal_result["latest_close"]

    if signal_result["buy_signal"] is True and position is None:
        if risk_manager.is_circuit_tripped_today(strategy_id):
            db.update_signal_result(signal_result["buy_signal_id"], None, "circuit_breaker_tripped")
            result["buy_action"] = "skipped_circuit_breaker"
        else:
            capital = min(strategy["current_capital"], risk_config["max_position_per_market"])
            order = await enter(strategy, capital, expected_price, dry_run=dry_run)
            result["buy_order_id"] = order["id"]
            if order["status"] == "done":
                db.update_signal_result(signal_result["buy_signal_id"], order["id"], None)
                result["buy_action"] = "entered"
            elif order["status"] == "cancel":
                db.update_signal_result(signal_result["buy_signal_id"], order["id"], "slippage_exceeded")
                result["buy_action"] = "slippage_exceeded"
            else:
                db.update_signal_result(signal_result["buy_signal_id"], order["id"], None)
                result["buy_action"] = "pending"

    if signal_result["sell_signal"] is True and position is not None:
        order = await exit(strategy, position, expected_price, dry_run=dry_run)
        result["sell_order_id"] = order["id"]
        if order["status"] == "done":
            db.update_signal_result(signal_result["sell_signal_id"], order["id"], None)
            result["sell_action"] = "exited"
            risk_manager.record_trade_result(strategy_id, order["realized_pnl"], order["capital_after"])
        elif order["status"] == "cancel":
            db.update_signal_result(signal_result["sell_signal_id"], order["id"], "slippage_exceeded")
            result["sell_action"] = "slippage_exceeded"
        else:
            db.update_signal_result(signal_result["sell_signal_id"], order["id"], None)
            result["sell_action"] = "pending"

    return result
```

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_order_executor.py -v`
Expected: 전부 PASS(20 + 6 = 26개)

- [ ] **Step 5: 커밋**

```bash
git add trading/order_executor.py tests/test_order_executor.py
git commit -m "feat: order_executor에 handle_signal_result(신호→주문 연결 로직) 추가"
```

---

### Task 10: 최종 통합 확인 + engine/ 미의존 검증 + 전체 회귀

**Files:**
- Modify: `trading/order_executor.py`(문서화만, 필요 시)

**Interfaces:**
- Consumes: 이 플랜의 모든 이전 태스크 산출물.
- Produces: 없음(검증 전용 태스크).

- [ ] **Step 1: `engine/` 미의존 확인**

Run:
```bash
python -c "
import ast
tree = ast.parse(open('trading/order_executor.py', encoding='utf-8').read())
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
Expected: `engine` 관련 import 없음, 에러 없이 통과.

- [ ] **Step 2: signal_engine → order_executor 엔드투엔드 흐름 최종 확인**

Run:
```bash
python -c "
import asyncio
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import trading.db as db
db.DB_PATH = Path(tempfile.mkdtemp()) / 'trading.db'

from tests.trading_db_fixtures import insert_live_strategy
from tests.signal_fixtures import make_oscillating_df
import trading.signal_engine as signal_engine
import trading.order_executor as order_executor

df = make_oscillating_df()
signal_engine.get_candles = lambda market, timeframe, start, end: df

buy_json = json.dumps({'type': 'AND', 'conditions': [
    {'indicator': 'RSI', 'params': {'period': 14}, 'operator': '>', 'threshold': -1},
]})
sell_json = json.dumps({'type': 'AND', 'conditions': [
    {'indicator': 'RSI', 'params': {'period': 14}, 'operator': '>', 'threshold': 101},
]})
risk_config = json.dumps({
    'order_execution_mode': 'market', 'max_position_per_market': 1000000.0,
    'max_slippage_pct': 0.5, 'order_timeout_sec': 10,
})
strategy_id = insert_live_strategy(
    db, market='KRW-BTC', current_capital=500000.0,
    buy_conditions_json=buy_json, sell_conditions_json=sell_json, risk_config_json=risk_config,
)

signal_result = signal_engine.evaluate_signals(strategy_id, now=datetime.now(timezone.utc))
assert signal_result['buy_signal'] is True

action_result = asyncio.run(
    order_executor.handle_signal_result(strategy_id, signal_result, dry_run=True)
)
print(action_result)
assert action_result['buy_action'] == 'entered'

import trading.position_manager as position_manager
position = position_manager.get_open_position(strategy_id)
assert position is not None
print('OK: signal_engine -> order_executor 엔드투엔드 흐름 정상 확인')
"
```
Expected: 에러 없이 `OK: signal_engine -> order_executor 엔드투엔드 흐름 정상 확인` 출력.

- [ ] **Step 3: 전체 테스트 스위트 실행(회귀 확인)**

Run: `python -m pytest -q`
Expected: 전부 PASS(⑤-2까지의 기존 테스트 전부 + 이 플랜의 신규 테스트 전부 포함, 회귀 없음).

- [ ] **Step 4: 커밋**

이 태스크는 검증 전용이라 코드 변경이 없으면 커밋할 게 없다 — Step 1~3이 전부 통과하면
빈 diff이므로 커밋을 생략한다. 검증 중 실제 코드 수정이 필요했다면 그 수정을 커밋한다:
```bash
git add trading/order_executor.py
git commit -m "fix: order_executor 최종 통합 검증에서 발견된 문제 수정"
```

---

## Self-Review

**스펙 커버리지:**
- 결정1(틱사이즈 하드코딩 테이블) → Task3의 `round_to_tick`/`_TICK_TABLE`.
- 결정2(async 경계) → Task5~9 전체, `enter`/`exit`/`handle_signal_result`가 전부 `async def`.
- 결정3(`handle_signal_result`가 연결 로직 담당) → Task9.
- 결정4(`limit_timeout` 블로킹 처리) → Task7의 `_run_limit_timeout`.
- 결정5(identifier 재조회 후 재시도) → Task4의 `_create_order_with_retry`.
- 결정6(진입마다 `max_position_per_market` 클램프) → Task9의
  `capital = min(strategy["current_capital"], risk_config["max_position_per_market"])`.
- 결정7(시장가 매수/매도 `ord_type` 비대칭) → Task5의 `_run_market`(bid="price", ask="market")과
  테스트로 양쪽 다 검증.
- 결정8(`dry_run` 즉시 전량체결) → Task5의 `enter`/`exit` 안 `if dry_run:` 분기.
- 결정9(`market_capped` 슬리피지상한+FOK) → Task8의 `_run_market_capped`.
- "plain limit 모드는 'pending'" 보완(스펙 addendum) → Task6(엔진 쪽 'wait' 반환) + Task9
  (`handle_signal_result`의 `else: ... "pending"` 분기), 각각 테스트로 검증.

**플레이스홀더 스캔:** 없음 — 모든 스텝에 완전한 코드가 있다.

**타입 일관성:** `_run_market`/`_run_limit`/`_run_limit_timeout`/`_run_market_capped` 4개
헬퍼가 전부 `{"order_id":, "status":, "filled_price":, "filled_volume":, "fee":}` 동일한
키 집합을 반환하므로 `enter()`/`exit()`의 후처리 로직(`if result["status"] != "done":`)이
모드에 관계없이 하나로 통일된다. `handle_signal_result()`가 기대하는
`signal_result`(Task2가 만든 `evaluate_signals()` 반환 형태)의 키(`buy_signal_id`/
`sell_signal_id`/`latest_close`/`paused`)와 Task9의 실제 사용이 정확히 일치한다.
