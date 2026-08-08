# 라이브 트레이딩 서브플랜⑤-4a — reconciler.py Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **워크트리를 만들지 말고 main 브랜치에서 직접 작업한다** (사용자 지시, [[upbit-v1-worktree-workflow-changed]]).

**Goal:** `trading/reconciler.py`를 만들어, 데몬 시작 시 State Hydration(`hydrate_state`)과
러닝 중 주기적 수동개입 감지(`check_manual_intervention`)를 제공한다 — 내부 DB 상태와
실제 업비트 거래소 상태(잔고/미체결·종료주문)를 대조해 불일치를 self-heal하고, 설명 안
되는 불일치는 안전하게 전략을 정지시킨다.

**Architecture:** 설계 스펙 `docs/superpowers/specs/2026-08-08-live-trading-reconciler-design.md`를
그대로 구현한다. `hydrate_state`/`check_manual_intervention` 두 공개 함수가 공유
파이프라인(`_detect_external_orders` → `_reconcile_position`)을 재사용하는 구조다.
`reconciler.py`는 `trading.upbit_client`(async REST) + `trading.db` +
`trading.position_manager` + `trading.risk_manager`만 import한다 — `engine/`은 전혀
의존하지 않는다.

**Tech Stack:** Python, `httpx`(이미 사용 중), `asyncio`, `pytest`(+`pytest-asyncio`,
`asyncio_mode = auto`로 이미 설정됨). 새 의존성 없음.

## Global Constraints

- `trading/reconciler.py`는 **하나의 파일로 유지**한다(다른 `trading/` 모듈과 동일 관례).
- `engine/`은 전혀 import하지 않는다.
- `httpx`/`trading.upbit_client` 외의 새 외부 API 클라이언트 라이브러리를 추가하지 않는다.
- `upbit_client`를 직접 `monkeypatch`하는 테스트는 `trading.upbit_client` 모듈 객체의
  함수를 패치한다(`reconciler.py`가 `import trading.upbit_client as upbit_client`로
  모듈 참조를 갖고 있으므로, 어느 파일에서 import했든 같은 모듈 객체가 패치된다).
- 커밋은 태스크 단위로 작게, 테스트가 통과한 뒤에만 한다.
- 잔고 비교 허용오차는 `_QTY_EPSILON = 1e-6`로 통일한다(스펙 결정4/9).

---

## File Structure

- **Modify:** `trading/db.py` — `live_strategies`에 `baseline_qty REAL` 컬럼 추가(스키마
  직접 수정, 스펙 "DB 스키마 변경" 절 — 마이그레이션 대상 데이터 없음) +
  `update_live_strategy_baseline_qty`/`get_order_by_upbit_uuid`/`insert_external_order`/
  `insert_manual_intervention_event`/`list_wait_orders`/`adjust_position_qty` 6개 함수.
- **Modify:** `tests/test_trading_db.py` — 위 6개 함수 테스트 + 기존
  `test_live_strategies_columns`에 `baseline_qty` 반영.
- **Create:** `trading/reconciler.py` — 잔고/주문 조회 헬퍼, `_sync_pending_limit_orders`,
  `hydrate_state`/`check_manual_intervention`, `_detect_external_orders`,
  `_reconcile_position`(+ 내부 헬퍼).
- **Create:** `tests/test_reconciler.py`.

---

### Task 1: `trading/db.py` — `baseline_qty` 스키마 + `update_live_strategy_baseline_qty`

**Files:**
- Modify: `trading/db.py`
- Modify: `tests/test_trading_db.py`

**Interfaces:**
- Consumes: `trading.db._connect()`(기존).
- Produces: `live_strategies.baseline_qty`(신규 컬럼, NULL 허용),
  `trading.db.update_live_strategy_baseline_qty(live_strategy_id: str, baseline_qty: float) -> None`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_trading_db.py`의 기존 `test_live_strategies_columns`를 찾아 그 안의 `assert
columns == {...}` 블록을 교체한다:

```python
    assert columns == {
        "id", "source_run_id", "market", "timeframe", "buy_conditions_json",
        "sell_conditions_json", "risk_config_json", "current_capital", "status",
        "last_processed_candle_time", "created_at", "approved_at", "started_at",
        "stopped_at", "baseline_qty",
    }
```

파일 끝에 추가:
```python
def test_update_live_strategy_baseline_qty_sets_value(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    assert db.get_live_strategy(strategy_id)["baseline_qty"] is None

    db.update_live_strategy_baseline_qty(strategy_id, 0.05)

    assert db.get_live_strategy(strategy_id)["baseline_qty"] == 0.05
```

(`insert_live_strategy`는 이 파일 상단에 이미 import돼 있다.)

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_trading_db.py -v -k "baseline_qty"`
Expected: FAIL — `test_live_strategies_columns`는 컬럼 불일치로, `test_update_live_strategy_baseline_qty_sets_value`는 `AttributeError`로 실패.

- [ ] **Step 3: `trading/db.py`에 구현 추가**

`_SCHEMA` 문자열의 `live_strategies` 테이블 정의에서:
```python
    stopped_at          TEXT
);
```
를(첫 번째 등장 — `live_strategies` 테이블):
```python
    stopped_at          TEXT,
    baseline_qty        REAL
);
```
로 교체한다.

파일 끝(`update_signal_result` 함수 뒤)에 추가:
```python


def update_live_strategy_baseline_qty(live_strategy_id: str, baseline_qty: float) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE live_strategies SET baseline_qty = ? WHERE id = ?",
            (baseline_qty, live_strategy_id),
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
git commit -m "feat: live_strategies에 baseline_qty 컬럼 추가(설계 스펙 결정9)"
```

---

### Task 2: `trading/db.py` — 외부주문/수동개입/미체결조회/수량보정 CRUD

**Files:**
- Modify: `trading/db.py`
- Modify: `tests/test_trading_db.py`

**Interfaces:**
- Consumes: `trading.db._connect()`(기존).
- Produces: `trading.db.get_order_by_upbit_uuid(upbit_uuid: str) -> dict | None`,
  `trading.db.insert_external_order(live_strategy_id, position_id, market, side, order_type,
  upbit_uuid, filled_price, filled_volume, fee, status) -> str`,
  `trading.db.insert_manual_intervention_event(market, description, action_taken) -> str`,
  `trading.db.list_wait_orders(live_strategy_id, order_type=None) -> list[dict]`,
  `trading.db.adjust_position_qty(position_id, new_qty) -> None`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_trading_db.py` 파일 끝에 추가:
```python
def test_get_order_by_upbit_uuid_returns_none_when_missing(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    assert db.get_order_by_upbit_uuid("nonexistent-uuid") is None


def test_insert_external_order_is_findable_by_upbit_uuid(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)

    order_id = db.insert_external_order(
        strategy_id, None, "KRW-BTC", "bid", "limit", "upbit-ext-1",
        50_000_000.0, 0.01, 500.0, "done",
    )

    found = db.get_order_by_upbit_uuid("upbit-ext-1")
    assert found["id"] == order_id
    assert found["is_external"] == 1
    assert found["status"] == "done"
    assert found["filled_price"] == 50_000_000.0
    assert found["live_strategy_id"] == strategy_id


def test_insert_manual_intervention_event_creates_row(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)

    event_id = db.insert_manual_intervention_event(
        "KRW-BTC", "설명 안 되는 잔고 변화", "all_stop",
    )

    conn = db._connect()
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM manual_intervention_events WHERE id = ?", (event_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row["market"] == "KRW-BTC"
    assert row["description"] == "설명 안 되는 잔고 변화"
    assert row["action_taken"] == "all_stop"
    assert row["detected_at"] is not None


def test_list_wait_orders_filters_by_status_and_optional_type(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    wait_limit_id = db.insert_order(strategy_id, None, "KRW-BTC", "bid", "limit", 100.0, 1.0, 100.0)
    wait_market_id = db.insert_order(strategy_id, None, "KRW-BTC", "bid", "market", 100.0, 1.0, 100.0)
    done_id = db.insert_order(strategy_id, None, "KRW-BTC", "bid", "limit", 100.0, 1.0, 100.0)
    db.update_order_filled(done_id, "uuid-done", 100.0, 1.0, 0.0, 0.0, "done")

    all_wait = db.list_wait_orders(strategy_id)
    limit_only = db.list_wait_orders(strategy_id, order_type="limit")

    assert {o["id"] for o in all_wait} == {wait_limit_id, wait_market_id}
    assert {o["id"] for o in limit_only} == {wait_limit_id}


def test_adjust_position_qty_updates_open_position_only(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    position_id = db.insert_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)

    db.adjust_position_qty(position_id, 0.006)

    assert db.get_position(position_id)["entry_qty"] == 0.006
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_trading_db.py -v -k "upbit_uuid or external_order or manual_intervention_event or list_wait_orders or adjust_position_qty"`
Expected: FAIL — `AttributeError: module 'trading.db' has no attribute 'get_order_by_upbit_uuid'`

- [ ] **Step 3: `trading/db.py`에 구현 추가**

파일 끝에 추가:
```python


def get_order_by_upbit_uuid(upbit_uuid: str) -> dict | None:
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM orders WHERE upbit_uuid = ?", (upbit_uuid,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def insert_external_order(
    live_strategy_id: str, position_id: str | None, market: str, side: str,
    order_type: str, upbit_uuid: str, filled_price: float | None,
    filled_volume: float | None, fee: float | None, status: str,
) -> str:
    order_id = str(uuid.uuid4())
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO orders "
            "(id, upbit_uuid, live_strategy_id, position_id, market, side, order_type, "
            "filled_price, filled_volume, fee, status, is_external, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, datetime('now'))",
            (order_id, upbit_uuid, live_strategy_id, position_id, market, side, order_type,
             filled_price, filled_volume, fee, status),
        )
        conn.commit()
    finally:
        conn.close()
    return order_id


def insert_manual_intervention_event(market: str, description: str, action_taken: str) -> str:
    event_id = str(uuid.uuid4())
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO manual_intervention_events (id, market, description, action_taken) "
            "VALUES (?, ?, ?, ?)",
            (event_id, market, description, action_taken),
        )
        conn.commit()
    finally:
        conn.close()
    return event_id


def list_wait_orders(live_strategy_id: str, order_type: str | None = None) -> list[dict]:
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        if order_type is not None:
            rows = conn.execute(
                "SELECT * FROM orders WHERE live_strategy_id = ? AND status = 'wait' "
                "AND order_type = ?",
                (live_strategy_id, order_type),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM orders WHERE live_strategy_id = ? AND status = 'wait'",
                (live_strategy_id,),
            ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def adjust_position_qty(position_id: str, new_qty: float) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE positions SET entry_qty = ? WHERE id = ? AND status = 'open'",
            (new_qty, position_id),
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
git commit -m "feat: db.py에 외부주문/수동개입이벤트/미체결조회/포지션수량보정 CRUD 추가"
```

---

### Task 3: `trading/reconciler.py` — 모듈 뼈대 + 잔고 조회 헬퍼

**Files:**
- Create: `trading/reconciler.py`
- Create: `tests/test_reconciler.py`

**Interfaces:**
- Consumes: `trading.upbit_client.get_accounts`(기존).
- Produces: `trading.reconciler._coin_currency(market: str) -> str`,
  `trading.reconciler._get_coin_account(market: str, *, client=None) -> dict | None`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_reconciler.py`(신규 파일):
```python
import trading.reconciler as reconciler
import trading.upbit_client as upbit_client


def test_coin_currency_extracts_ticker_from_market():
    assert reconciler._coin_currency("KRW-BTC") == "BTC"
    assert reconciler._coin_currency("KRW-ETH") == "ETH"


async def test_get_coin_account_returns_matching_currency(monkeypatch):
    async def fake_get_accounts(*, client=None):
        return [
            {"currency": "KRW", "balance": "100000.0", "locked": "0", "avg_buy_price": "0"},
            {"currency": "BTC", "balance": "0.01", "locked": "0.002", "avg_buy_price": "49000000"},
        ]

    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    account = await reconciler._get_coin_account("KRW-BTC")

    assert account["currency"] == "BTC"
    assert account["balance"] == "0.01"


async def test_get_coin_account_returns_none_when_not_held(monkeypatch):
    async def fake_get_accounts(*, client=None):
        return [{"currency": "KRW", "balance": "100000.0", "locked": "0", "avg_buy_price": "0"}]

    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    assert await reconciler._get_coin_account("KRW-BTC") is None
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_reconciler.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'trading.reconciler'`

- [ ] **Step 3: `trading/reconciler.py` 구현**

```python
"""
trading/reconciler.py

거래소 실제 상태(잔고/미체결·종료주문)와 내부 DB를 대조해 외부(수동) 개입을 감지하고
자동으로 self-heal한다. 데몬 시작 시 1회(hydrate_state) + 러닝 중 주기적으로
(check_manual_intervention) 호출되는 공유 파이프라인 구조(설계 스펙 결정1). 스스로
타이머/루프를 갖지 않는다 — 언제 호출할지는 daemon.py(⑤-4b)의 몫이다. trading.upbit_client
+ trading.db + trading.position_manager + trading.risk_manager만 의존. engine/ 미의존.
"""
from __future__ import annotations

import httpx

import trading.db as db
import trading.position_manager as position_manager
import trading.risk_manager as risk_manager
import trading.upbit_client as upbit_client

_QTY_EPSILON = 1e-6


def _coin_currency(market: str) -> str:
    return market.split("-", 1)[1]


async def _get_coin_account(market: str, *, client: httpx.AsyncClient | None = None) -> dict | None:
    accounts = await upbit_client.get_accounts(client=client)
    currency = _coin_currency(market)
    for account in accounts:
        if account["currency"] == currency:
            return account
    return None
```

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_reconciler.py -v`
Expected: 3개 테스트 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add trading/reconciler.py tests/test_reconciler.py
git commit -m "feat: reconciler.py 뼈대 + 코인잔고 조회 헬퍼 추가"
```

---

### Task 4: `trading/reconciler.py` — `_sync_pending_limit_orders`(재시작 시 catch-up)

**Files:**
- Modify: `trading/reconciler.py`
- Modify: `tests/test_reconciler.py`

**Interfaces:**
- Consumes: `trading.db.list_wait_orders`/`update_order_filled`(Task1~2),
  `trading.upbit_client.get_order`(기존).
- Produces: `trading.reconciler._sync_pending_limit_orders(strategy: dict, *, client=None) -> int`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_reconciler.py` 파일 끝에 추가:
```python
import json

import trading.db as db
from tests.trading_db_fixtures import insert_live_strategy


def _fresh_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "trading.db")
    return db


def _strategy_row(dbm, *, baseline_qty=None, manual_intervention_policy="all_stop", **overrides):
    risk_config = {
        "order_execution_mode": "market",
        "max_position_per_market": 1_000_000.0,
        "manual_intervention_policy": manual_intervention_policy,
    }
    strategy_id = insert_live_strategy(
        dbm, market="KRW-BTC", current_capital=1_000_000.0,
        risk_config_json=json.dumps(risk_config), **overrides,
    )
    if baseline_qty is not None:
        dbm.update_live_strategy_baseline_qty(strategy_id, baseline_qty)
    return dbm.get_live_strategy(strategy_id)


async def test_sync_pending_limit_orders_updates_filled_order(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)
    order_id = dbm.insert_order(strategy["id"], None, "KRW-BTC", "bid", "limit", 100.0, 1.0, 100.0)
    dbm.update_order_filled(order_id, "uuid-limit-1", None, None, None, None, "wait")

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        return {"state": "done", "executed_volume": "1.0", "remaining_volume": "0",
                "paid_fee": "50.0", "trades": [{"funds": "100.0"}]}

    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    synced = await reconciler._sync_pending_limit_orders(strategy)

    assert synced == 1
    order = dbm.get_order_by_id(order_id)
    assert order["status"] == "done"
    assert order["filled_price"] == 100.0
    assert order["filled_volume"] == 1.0


async def test_sync_pending_limit_orders_skips_orders_still_waiting(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)
    order_id = dbm.insert_order(strategy["id"], None, "KRW-BTC", "bid", "limit", 100.0, 1.0, 100.0)
    dbm.update_order_filled(order_id, "uuid-limit-2", None, None, None, None, "wait")

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        return {"state": "wait", "executed_volume": "0", "remaining_volume": "1.0",
                "paid_fee": "0", "trades": []}

    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    synced = await reconciler._sync_pending_limit_orders(strategy)

    assert synced == 0
    assert dbm.get_order_by_id(order_id)["status"] == "wait"


async def test_sync_pending_limit_orders_ignores_non_limit_wait_orders(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)
    dbm.insert_order(strategy["id"], None, "KRW-BTC", "bid", "market", 100.0, 1.0, 100.0)
    calls = {"count": 0}

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        calls["count"] += 1
        return {"state": "done", "executed_volume": "1.0", "remaining_volume": "0",
                "paid_fee": "0", "trades": [{"funds": "100.0"}]}

    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    synced = await reconciler._sync_pending_limit_orders(strategy)

    assert synced == 0
    assert calls["count"] == 0
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_reconciler.py -v -k sync_pending_limit_orders`
Expected: FAIL — `AttributeError: module 'trading.reconciler' has no attribute '_sync_pending_limit_orders'`

- [ ] **Step 3: `trading/reconciler.py`에 구현 추가**

파일 끝에 추가:
```python


async def _sync_pending_limit_orders(
    strategy: dict, *, client: httpx.AsyncClient | None = None,
) -> int:
    """내부 status='wait', order_type='limit' 주문(오프라인 동안 결과를 못 받은 사용자
    선택 방치 주문, 설계 스펙 결정6)을 재조회해 조용히 동기화한다. 우리가 낸 주문이므로
    수동개입으로 기록하지 않는다."""
    wait_orders = db.list_wait_orders(strategy["id"], order_type="limit")
    synced = 0
    for order in wait_orders:
        if not order["upbit_uuid"]:
            continue
        resp = await upbit_client.get_order(uuid=order["upbit_uuid"], client=client)
        if resp["state"] == "wait":
            continue

        executed_volume = float(resp["executed_volume"])
        filled_price = (
            sum(float(t["funds"]) for t in resp["trades"]) / executed_volume
            if executed_volume > 0 else None
        )
        status = "done" if resp["state"] == "done" else "cancel"
        db.update_order_filled(
            order["id"], order["upbit_uuid"], filled_price, executed_volume,
            float(resp["paid_fee"]), None, status,
        )
        synced += 1
    return synced
```

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_reconciler.py -v`
Expected: 전부 PASS(3 + 3 = 6개)

- [ ] **Step 5: 커밋**

```bash
git add trading/reconciler.py tests/test_reconciler.py
git commit -m "feat: reconciler에 _sync_pending_limit_orders(재시작 catch-up) 추가"
```

---

### Task 5: `trading/reconciler.py` — `hydrate_state`의 `baseline_qty` 첫 캡처 경로

**Files:**
- Modify: `trading/reconciler.py`
- Modify: `tests/test_reconciler.py`

**Interfaces:**
- Consumes: Task3~4의 `_get_coin_account`/`_sync_pending_limit_orders`, Task1의
  `db.update_live_strategy_baseline_qty`.
- Produces: `trading.reconciler.hydrate_state(strategy: dict, *, client=None) -> dict`
  (이 태스크에서는 `baseline_qty`가 이미 있는 경우 빈 파이프라인 호출은 Task8에서 채움 —
  여기서는 "첫 호출" 분기만 완성).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_reconciler.py` 파일 끝에 추가:
```python
async def test_hydrate_state_captures_baseline_on_first_call(monkeypatch, tmp_path):
    """설계 스펙 결정9 — 전략 시작 전부터 보유하던 코인(사용자 사례: 기존 BTC 보유)을
    첫 hydrate_state 호출에서 baseline으로 격리하고, 그 호출은 불일치 검사를 건너뛴다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)
    assert strategy["baseline_qty"] is None

    async def fake_get_accounts(*, client=None):
        return [{"currency": "BTC", "balance": "0.05", "locked": "0", "avg_buy_price": "50000000"}]

    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    result = await reconciler.hydrate_state(strategy)

    assert result["baseline_captured"] is True
    assert result["synced_wait_orders"] == 0
    assert dbm.get_live_strategy(strategy["id"])["baseline_qty"] == 0.05
    assert dbm.get_live_strategy(strategy["id"])["status"] != "paused"
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_reconciler.py -v -k captures_baseline`
Expected: FAIL — `AttributeError: module 'trading.reconciler' has no attribute 'hydrate_state'`

- [ ] **Step 3: `trading/reconciler.py`에 구현 추가**

파일 끝에 추가(이 태스크에서는 `baseline_qty`가 이미 설정된 경우의 파이프라인 호출은
`_run_reconcile_pipeline`이 아직 없으므로 Task8에서 채운다 — 지금은 `NotImplementedError`
플레이스홀더 없이, 그 분기를 그대로 두면 이후 태스크가 안전하게 대체할 수 있도록 함수
전체를 여기서 정의하되 "baseline 있음" 분기만 Task8에서 본문을 교체한다는 걸 명시):

```python


async def hydrate_state(strategy: dict, *, client: httpx.AsyncClient | None = None) -> dict:
    """데몬 시작 시 전략 1개당 1회 호출. 내부 wait limit 주문을 먼저 동기화(결정6)한 뒤,
    strategy['baseline_qty']가 None이면(결정9, 이 전략의 첫 호출) 그 시점 실제 코인 잔고를
    baseline으로 저장하고 불일치 검사 없이 반환한다. 이미 baseline이 있으면
    _run_reconcile_pipeline()을 수행한다(Task8에서 연결)."""
    synced = await _sync_pending_limit_orders(strategy, client=client)

    if strategy["baseline_qty"] is None:
        account = await _get_coin_account(strategy["market"], client=client)
        baseline = (float(account["balance"]) + float(account["locked"])) if account else 0.0
        db.update_live_strategy_baseline_qty(strategy["id"], baseline)
        return {"synced_wait_orders": synced, "baseline_captured": True}

    result = await _run_reconcile_pipeline(strategy, client=client)
    return {"synced_wait_orders": synced, "baseline_captured": False, **result}
```

이 시점에는 `_run_reconcile_pipeline`이 아직 정의되지 않았으므로, `baseline_qty`가 이미
있는 전략에 대해 `hydrate_state`를 호출하는 테스트는 이 태스크에 없다(Task8에서 추가).

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_reconciler.py -v -k captures_baseline`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add trading/reconciler.py tests/test_reconciler.py
git commit -m "feat: hydrate_state의 baseline_qty 첫 캡처 경로 추가(결정9)"
```

---

### Task 6: `trading/reconciler.py` — `_detect_external_orders`

**Files:**
- Modify: `trading/reconciler.py`
- Modify: `tests/test_reconciler.py`

**Interfaces:**
- Consumes: `trading.upbit_client.list_open_orders`/`list_closed_orders`/`get_order`(기존),
  `trading.db.get_order_by_upbit_uuid`/`insert_external_order`/
  `insert_manual_intervention_event`/`update_live_strategy_status`(Task1~2, 기존).
- Produces: `trading.reconciler._detect_external_orders(strategy: dict, *, client=None) -> list[dict]`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_reconciler.py` 파일 끝에 추가:
```python
async def test_detect_external_orders_finds_new_order_all_stop(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, baseline_qty=0.0, manual_intervention_policy="all_stop")

    async def fake_list_open_orders(*, market=None, states=None, client=None):
        return [{"uuid": "ext-uuid-1"}]

    async def fake_list_closed_orders(*, market=None, states=None, client=None):
        return []

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        return {"state": "wait", "side": "bid", "ord_type": "limit",
                "executed_volume": "0", "remaining_volume": "1.0",
                "paid_fee": "0", "trades": []}

    monkeypatch.setattr(upbit_client, "list_open_orders", fake_list_open_orders)
    monkeypatch.setattr(upbit_client, "list_closed_orders", fake_list_closed_orders)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    found = await reconciler._detect_external_orders(strategy)

    assert len(found) == 1
    assert found[0]["upbit_uuid"] == "ext-uuid-1"
    assert found[0]["is_external"] == 1
    assert dbm.get_live_strategy(strategy["id"])["status"] == "paused"


async def test_detect_external_orders_acknowledge_and_continue_keeps_running(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(
        dbm, baseline_qty=0.0, manual_intervention_policy="acknowledge_and_continue",
    )

    async def fake_list_open_orders(*, market=None, states=None, client=None):
        return [{"uuid": "ext-uuid-2"}]

    async def fake_list_closed_orders(*, market=None, states=None, client=None):
        return []

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        return {"state": "wait", "side": "bid", "ord_type": "limit",
                "executed_volume": "0", "remaining_volume": "1.0",
                "paid_fee": "0", "trades": []}

    monkeypatch.setattr(upbit_client, "list_open_orders", fake_list_open_orders)
    monkeypatch.setattr(upbit_client, "list_closed_orders", fake_list_closed_orders)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    found = await reconciler._detect_external_orders(strategy)

    assert len(found) == 1
    assert dbm.get_live_strategy(strategy["id"])["status"] == "running"


async def test_detect_external_orders_ignores_already_known_uuid(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, baseline_qty=0.0)
    dbm.insert_external_order(
        strategy["id"], None, "KRW-BTC", "bid", "limit", "known-uuid",
        100.0, 1.0, 0.0, "done",
    )
    calls = {"get_order": 0}

    async def fake_list_open_orders(*, market=None, states=None, client=None):
        return []

    async def fake_list_closed_orders(*, market=None, states=None, client=None):
        return [{"uuid": "known-uuid"}]

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        calls["get_order"] += 1
        return {"state": "done", "side": "bid", "ord_type": "limit",
                "executed_volume": "1.0", "remaining_volume": "0",
                "paid_fee": "0", "trades": [{"funds": "100.0"}]}

    monkeypatch.setattr(upbit_client, "list_open_orders", fake_list_open_orders)
    monkeypatch.setattr(upbit_client, "list_closed_orders", fake_list_closed_orders)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    found = await reconciler._detect_external_orders(strategy)

    assert found == []
    assert calls["get_order"] == 0
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_reconciler.py -v -k detect_external_orders`
Expected: FAIL — `AttributeError: module 'trading.reconciler' has no attribute '_detect_external_orders'`

- [ ] **Step 3: `trading/reconciler.py`에 구현 추가**

import 블록을:
```python
from __future__ import annotations

import httpx
```
에서:
```python
from __future__ import annotations

import json

import httpx
```
로 교체한다.

파일 끝에 추가:
```python


async def _detect_external_orders(
    strategy: dict, *, client: httpx.AsyncClient | None = None,
) -> list[dict]:
    """그 마켓의 미체결+최근 종료 주문을 조회해 내부 DB에 없는 uuid만 골라 기록한다
    (설계 스펙 결정7 준비 — 여기서 찾은 주문들을 _reconcile_position이 재사용)."""
    market = strategy["market"]
    open_orders = await upbit_client.list_open_orders(market=market, client=client)
    closed_orders = await upbit_client.list_closed_orders(
        market=market, states=["done", "cancel"], client=client,
    )

    risk_config = json.loads(strategy["risk_config_json"])
    policy = risk_config.get("manual_intervention_policy", "all_stop")

    found: list[dict] = []
    for raw in open_orders + closed_orders:
        upbit_uuid = raw["uuid"]
        if db.get_order_by_upbit_uuid(upbit_uuid) is not None:
            continue

        detail = await upbit_client.get_order(uuid=upbit_uuid, client=client)
        executed_volume = float(detail["executed_volume"])
        filled_price = (
            sum(float(t["funds"]) for t in detail["trades"]) / executed_volume
            if executed_volume > 0 else None
        )
        status = "wait" if detail["state"] == "wait" else (
            "done" if detail["state"] == "done" else "cancel"
        )

        order_id = db.insert_external_order(
            strategy["id"], None, market, detail["side"], detail["ord_type"], upbit_uuid,
            filled_price, executed_volume if executed_volume > 0 else None,
            float(detail["paid_fee"]), status,
        )
        found.append(db.get_order_by_id(order_id))

        action_taken = "all_stop" if policy == "all_stop" else "acknowledged_and_continued"
        db.insert_manual_intervention_event(
            market,
            f"내부에 없는 외부주문 발견: uuid={upbit_uuid}, side={detail['side']}, "
            f"state={detail['state']}",
            action_taken,
        )
        if policy == "all_stop":
            db.update_live_strategy_status(strategy["id"], "paused")

    return found
```

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_reconciler.py -v`
Expected: 전부 PASS(6 + 4 = 10개)

- [ ] **Step 5: 커밋**

```bash
git add trading/reconciler.py tests/test_reconciler.py
git commit -m "feat: reconciler에 _detect_external_orders(외부주문 감지) 추가"
```

---

### Task 7: `trading/reconciler.py` — `_reconcile_position`(잔고 대조 + self-heal)

**Files:**
- Modify: `trading/reconciler.py`
- Modify: `tests/test_reconciler.py`

**Interfaces:**
- Consumes: Task3의 `_get_coin_account`, Task6이 반환하는 외부주문 리스트,
  `trading.position_manager.get_open_position`/`open_position`/`close_position`(기존),
  `trading.risk_manager.record_trade_result`(기존), `trading.db.adjust_position_qty`/
  `close_position_row`/`insert_manual_intervention_event`/`update_live_strategy_status`
  (Task1~2, 기존).
- Produces: `trading.reconciler._reconcile_position(strategy: dict,
  external_orders: list[dict], *, client=None) -> dict`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_reconciler.py` 파일 끝에 추가:
```python
import trading.position_manager as position_manager


def _account(balance, locked="0", avg_buy_price="0"):
    return {"currency": "BTC", "balance": str(balance), "locked": locked,
            "avg_buy_price": avg_buy_price}


async def test_reconcile_position_no_mismatch_returns_none_action(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, baseline_qty=0.0)

    async def fake_get_accounts(*, client=None):
        return [_account(0)]

    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    result = await reconciler._reconcile_position(strategy, [])

    assert result == {"balance_mismatch": False, "action": "none", "paused": False}


async def test_reconcile_position_opens_from_matched_external_buy(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, baseline_qty=0.0, manual_intervention_policy="acknowledge_and_continue")

    async def fake_get_accounts(*, client=None):
        return [_account(0.01)]

    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    external_orders = [{"side": "bid", "filled_volume": 0.01, "filled_price": 50_000_000.0, "fee": 500.0}]
    result = await reconciler._reconcile_position(strategy, external_orders)

    assert result == {"balance_mismatch": True, "action": "opened", "paused": False}
    position = position_manager.get_open_position(strategy["id"])
    assert position["entry_qty"] == 0.01
    assert position["entry_price"] == 50_000_000.0


async def test_reconcile_position_closes_from_matched_external_sell(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, baseline_qty=0.0, manual_intervention_policy="all_stop")
    position_manager.open_position(strategy["id"], "KRW-BTC", 49_000_000.0, 0.01)

    async def fake_get_accounts(*, client=None):
        return [_account(0)]

    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    external_orders = [{"side": "ask", "filled_volume": 0.01, "filled_price": 50_000_000.0, "fee": 500.0}]
    result = await reconciler._reconcile_position(strategy, external_orders)

    assert result == {"balance_mismatch": True, "action": "closed", "paused": True}
    assert position_manager.get_open_position(strategy["id"]) is None
    assert dbm.get_live_strategy(strategy["id"])["status"] == "paused"


async def test_reconcile_position_adjusts_qty_on_partial_external_sell(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, baseline_qty=0.0, manual_intervention_policy="acknowledge_and_continue")
    position_manager.open_position(strategy["id"], "KRW-BTC", 49_000_000.0, 0.01)
    capital_before = dbm.get_live_strategy(strategy["id"])["current_capital"]

    async def fake_get_accounts(*, client=None):
        return [_account(0.006)]

    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    external_orders = [{"side": "ask", "filled_volume": 0.004, "filled_price": 50_000_000.0, "fee": 200.0}]
    result = await reconciler._reconcile_position(strategy, external_orders)

    assert result["action"] == "adjusted"
    position = position_manager.get_open_position(strategy["id"])
    assert position["entry_qty"] == pytest.approx(0.006)
    assert dbm.get_live_strategy(strategy["id"])["current_capital"] == capital_before


async def test_reconcile_position_unexplained_forces_paused_regardless_of_policy(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, baseline_qty=0.0, manual_intervention_policy="acknowledge_and_continue")
    position_manager.open_position(strategy["id"], "KRW-BTC", 49_000_000.0, 0.01)

    async def fake_get_accounts(*, client=None):
        return [_account(0.005)]  # 절반만 남았는데 설명할 외부주문이 없음

    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    result = await reconciler._reconcile_position(strategy, [])

    assert result == {"balance_mismatch": True, "action": "unexplained", "paused": True}
    assert dbm.get_live_strategy(strategy["id"])["status"] == "paused"
    position = position_manager.get_open_position(strategy["id"])
    assert position["entry_qty"] == pytest.approx(0.005)


async def test_reconcile_position_unexplained_open_uses_avg_buy_price(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, baseline_qty=0.0)

    async def fake_get_accounts(*, client=None):
        return [_account(0.02, avg_buy_price="48000000")]

    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    result = await reconciler._reconcile_position(strategy, [])

    assert result["action"] == "unexplained"
    position = position_manager.get_open_position(strategy["id"])
    assert position["entry_price"] == 48_000_000.0
    assert position["entry_qty"] == pytest.approx(0.02)
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_reconciler.py -v -k reconcile_position`
Expected: FAIL — `AttributeError: module 'trading.reconciler' has no attribute '_reconcile_position'`

- [ ] **Step 3: `trading/reconciler.py`에 구현 추가**

파일 끝에 추가:
```python


def _weighted_fill(orders: list[dict]) -> tuple[float, float, float]:
    """반환: (총체결수량, 가중평균체결가, 총수수료). 빈 리스트면 (0, 0, 0)."""
    total_volume = sum(o["filled_volume"] for o in orders)
    total_funds = sum(o["filled_price"] * o["filled_volume"] for o in orders)
    total_fee = sum(o["fee"] or 0.0 for o in orders)
    avg_price = total_funds / total_volume if total_volume > 0 else 0.0
    return total_volume, avg_price, total_fee


def _apply_explained_change(
    strategy: dict, position: dict | None, actual_qty: float,
    buy_volume: float, buy_price: float, sell_price: float, sell_fee: float,
) -> str:
    """설계 스펙 결정4/7 — 매칭된 외부주문의 실제 체결가로 정밀하게 self-heal한다."""
    if position is None:
        position_manager.open_position(strategy["id"], strategy["market"], buy_price, actual_qty)
        return "opened"

    if actual_qty <= _QTY_EPSILON:
        close_result = position_manager.close_position(
            position["id"], sell_price, position["entry_qty"], sell_fee, "manual",
        )
        risk_manager.record_trade_result(
            strategy["id"], close_result["realized_pnl"], close_result["capital_after"],
        )
        return "closed"

    db.adjust_position_qty(position["id"], actual_qty)
    return "adjusted"


def _self_heal_unexplained(strategy: dict, position: dict | None, actual_qty: float, avg_buy_price: float) -> None:
    """설계 스펙 결정5 — 가격 근거가 없으므로 PnL/current_capital은 건드리지 않고
    수량만 실제 잔고에 맞춘다. 신규 포지션은 업비트가 자체 관리하는 avg_buy_price를
    근사 원가로 쓴다(정확한 매도가는 알 수 없어도, 향후 정상 청산 시 PnL 계산의 기준점은
    있어야 한다)."""
    if position is None:
        if actual_qty > _QTY_EPSILON:
            position_manager.open_position(strategy["id"], strategy["market"], avg_buy_price, actual_qty)
        return

    if actual_qty <= _QTY_EPSILON:
        db.close_position_row(position["id"], None, position["entry_qty"], None, None, "manual_unexplained")
        return

    db.adjust_position_qty(position["id"], actual_qty)


async def _reconcile_position(
    strategy: dict, external_orders: list[dict], *, client: httpx.AsyncClient | None = None,
) -> dict:
    market = strategy["market"]
    risk_config = json.loads(strategy["risk_config_json"])
    policy = risk_config.get("manual_intervention_policy", "all_stop")

    account = await _get_coin_account(market, client=client)
    raw_balance = (float(account["balance"]) + float(account["locked"])) if account else 0.0
    avg_buy_price = float(account["avg_buy_price"]) if account and account.get("avg_buy_price") else 0.0
    baseline_qty = strategy["baseline_qty"] or 0.0
    actual_qty = raw_balance - baseline_qty

    position = position_manager.get_open_position(strategy["id"])
    internal_qty = position["entry_qty"] if position else 0.0

    diff = actual_qty - internal_qty
    if abs(diff) <= _QTY_EPSILON:
        return {"balance_mismatch": False, "action": "none", "paused": False}

    done_buys = [o for o in external_orders if o["side"] == "bid" and o["filled_volume"]]
    done_sells = [o for o in external_orders if o["side"] == "ask" and o["filled_volume"]]
    buy_volume, buy_price, _buy_fee = _weighted_fill(done_buys)
    sell_volume, sell_price, sell_fee = _weighted_fill(done_sells)
    explained_diff = buy_volume - sell_volume

    if (buy_volume > 0 or sell_volume > 0) and abs(diff - explained_diff) <= _QTY_EPSILON:
        action = _apply_explained_change(
            strategy, position, actual_qty, buy_volume, buy_price, sell_price, sell_fee,
        )
        paused = policy == "all_stop"
        if paused:
            db.update_live_strategy_status(strategy["id"], "paused")
        return {"balance_mismatch": True, "action": action, "paused": paused}

    _self_heal_unexplained(strategy, position, actual_qty, avg_buy_price)
    db.insert_manual_intervention_event(
        market,
        f"설명 안 되는 잔고 변화: 기대수량={internal_qty}, 실제수량={actual_qty}",
        "all_stop",
    )
    db.update_live_strategy_status(strategy["id"], "paused")
    return {"balance_mismatch": True, "action": "unexplained", "paused": True}
```

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_reconciler.py -v`
Expected: 전부 PASS(10 + 6 = 16개)

- [ ] **Step 5: 커밋**

```bash
git add trading/reconciler.py tests/test_reconciler.py
git commit -m "feat: reconciler에 _reconcile_position(잔고 대조 + self-heal) 추가"
```

---

### Task 8: `trading/reconciler.py` — `_run_reconcile_pipeline` + `check_manual_intervention` + `hydrate_state` 완성

**Files:**
- Modify: `trading/reconciler.py`
- Modify: `tests/test_reconciler.py`

**Interfaces:**
- Consumes: Task6~7의 `_detect_external_orders`/`_reconcile_position`.
- Produces: `trading.reconciler._run_reconcile_pipeline(strategy: dict, *, client=None) -> dict`,
  `trading.reconciler.check_manual_intervention(strategy: dict, *, client=None) -> dict`.
  `hydrate_state`가 `baseline_qty`가 이미 있는 전략에 대해 파이프라인까지 수행하도록 완성.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_reconciler.py` 파일 끝에 추가:
```python
async def test_run_reconcile_pipeline_absorbs_api_errors(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, baseline_qty=0.0)

    async def failing_list_open_orders(*, market=None, states=None, client=None):
        raise httpx.ConnectError("network down")

    monkeypatch.setattr(upbit_client, "list_open_orders", failing_list_open_orders)

    result = await reconciler._run_reconcile_pipeline(strategy)

    assert "error" in result
    assert dbm.get_live_strategy(strategy["id"])["status"] == "running"


async def test_check_manual_intervention_runs_pipeline_directly(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, baseline_qty=0.0)

    async def fake_list_open_orders(*, market=None, states=None, client=None):
        return []

    async def fake_list_closed_orders(*, market=None, states=None, client=None):
        return []

    async def fake_get_accounts(*, client=None):
        return [_account(0)]

    monkeypatch.setattr(upbit_client, "list_open_orders", fake_list_open_orders)
    monkeypatch.setattr(upbit_client, "list_closed_orders", fake_list_closed_orders)
    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    result = await reconciler.check_manual_intervention(strategy)

    assert result == {"balance_mismatch": False, "action": "none", "paused": False}


async def test_hydrate_state_runs_pipeline_when_baseline_already_set(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, baseline_qty=0.0)

    async def fake_list_open_orders(*, market=None, states=None, client=None):
        return []

    async def fake_list_closed_orders(*, market=None, states=None, client=None):
        return []

    async def fake_get_accounts(*, client=None):
        return [_account(0)]

    monkeypatch.setattr(upbit_client, "list_open_orders", fake_list_open_orders)
    monkeypatch.setattr(upbit_client, "list_closed_orders", fake_list_closed_orders)
    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    result = await reconciler.hydrate_state(strategy)

    assert result["baseline_captured"] is False
    assert result["balance_mismatch"] is False
    assert result["synced_wait_orders"] == 0
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_reconciler.py -v -k "run_reconcile_pipeline or check_manual_intervention or baseline_already_set"`
Expected: FAIL — `AttributeError: module 'trading.reconciler' has no attribute '_run_reconcile_pipeline'`(뒤 두 테스트는 `hydrate_state`가 이미 `_run_reconcile_pipeline`을 호출하도록 Task5에서 작성돼 있어 같은 에러로 실패)

- [ ] **Step 3: `trading/reconciler.py`에 구현 추가**

파일 끝에 추가:
```python


async def _run_reconcile_pipeline(strategy: dict, *, client: httpx.AsyncClient | None = None) -> dict:
    """_detect_external_orders() → _reconcile_position() 순서로 실행한다. 업비트 API 실패는
    여기서 흡수하고(설계 스펙 결정8) 매매를 막지 않는다 — reconciler는 감시자이지
    트레이더가 아니다."""
    try:
        external_orders = await _detect_external_orders(strategy, client=client)
        return await _reconcile_position(strategy, external_orders, client=client)
    except (httpx.HTTPError, upbit_client.UpbitRateLimitError) as exc:
        return {"error": str(exc)}


async def check_manual_intervention(strategy: dict, *, client: httpx.AsyncClient | None = None) -> dict:
    """러닝 중 데몬이 주기적으로(15~30초, 스케줄링은 daemon.py 몫) 호출한다."""
    return await _run_reconcile_pipeline(strategy, client=client)
```

`__all__`은 이 모듈에 없으므로 별도 export 목록 갱신은 필요 없다.

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_reconciler.py -v`
Expected: 전부 PASS(16 + 3 = 19개)

- [ ] **Step 5: 커밋**

```bash
git add trading/reconciler.py tests/test_reconciler.py
git commit -m "feat: reconciler에 _run_reconcile_pipeline + check_manual_intervention 추가, hydrate_state 완성"
```

---

### Task 9: 최종 통합 확인 + `engine/` 미의존 검증 + 전체 회귀

**Files:**
- Modify: `trading/reconciler.py`(문서화만, 필요 시)

**Interfaces:**
- Consumes: 이 플랜의 모든 이전 태스크 산출물.
- Produces: 없음(검증 전용 태스크).

- [ ] **Step 1: `engine/` 미의존 확인**

Run:
```bash
python -c "
import ast
tree = ast.parse(open('trading/reconciler.py', encoding='utf-8').read())
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
Expected: `engine` 관련 import 없음, 에러 없이 통과. `sorted(names)`에
`['__future__', 'httpx', 'json', 'trading.db', 'trading.position_manager',
'trading.risk_manager', 'trading.upbit_client']`가 출력됨.

- [ ] **Step 2: 재시작 시나리오 엔드투엔드 확인**(사용자가 발견한 "기존 보유 BTC" 케이스가
  실제로 첫 재시작에서 안전하게 처리되는지, 두 번째 재시작부터 정상적으로 수동개입을
  감지하는지 순서대로 확인)

Run:
```bash
python -c "
import asyncio
import json
import tempfile
from pathlib import Path

import trading.db as db
db.DB_PATH = Path(tempfile.mkdtemp()) / 'trading.db'

from tests.trading_db_fixtures import insert_live_strategy
import trading.upbit_client as upbit_client
import trading.reconciler as reconciler
import trading.position_manager as position_manager

risk_config = json.dumps({
    'order_execution_mode': 'market', 'max_position_per_market': 1000000.0,
    'manual_intervention_policy': 'all_stop',
})
strategy_id = insert_live_strategy(
    db, market='KRW-BTC', current_capital=500000.0, risk_config_json=risk_config,
)
strategy = db.get_live_strategy(strategy_id)

# 사용자가 승인 전부터 BTC 0.05를 들고 있던 상황을 흉내낸다.
async def fake_get_accounts(*, client=None):
    return [{'currency': 'BTC', 'balance': '0.05', 'locked': '0', 'avg_buy_price': '50000000'}]
async def fake_list_open_orders(*, market=None, states=None, client=None):
    return []
async def fake_list_closed_orders(*, market=None, states=None, client=None):
    return []

upbit_client.get_accounts = fake_get_accounts
upbit_client.list_open_orders = fake_list_open_orders
upbit_client.list_closed_orders = fake_list_closed_orders

first = asyncio.run(reconciler.hydrate_state(strategy))
assert first['baseline_captured'] is True, first
assert db.get_live_strategy(strategy_id)['status'] == 'running'
assert position_manager.get_open_position(strategy_id) is None
print('OK: 1차 재시작 — 기존 보유 BTC가 baseline으로 격리되고 정상 running 유지')

strategy = db.get_live_strategy(strategy_id)
second = asyncio.run(reconciler.hydrate_state(strategy))
assert second['baseline_captured'] is False, second
assert second['balance_mismatch'] is False, second
print('OK: 2차 재시작 — baseline 기준 잔고 변화 없음으로 정상 판정')
"
```
Expected: 에러 없이 두 `OK:` 줄이 순서대로 출력됨.

- [ ] **Step 3: 전체 테스트 스위트 실행(회귀 확인)**

Run: `python -m pytest -q`
Expected: 전부 PASS(⑤-3까지의 기존 622개 + 이 플랜의 신규 테스트 전부 포함, 회귀 없음).

- [ ] **Step 4: 커밋**

이 태스크는 검증 전용이라 코드 변경이 없으면 커밋할 게 없다 — Step 1~3이 전부 통과하면
빈 diff이므로 커밋을 생략한다. 검증 중 실제 코드 수정이 필요했다면 그 수정을 커밋한다:
```bash
git add trading/reconciler.py
git commit -m "fix: reconciler 최종 통합 검증에서 발견된 문제 수정"
```

---

## Self-Review

**스펙 커버리지:**
- 결정1(공유 파이프라인 + 얇은 진입점 2개) → Task8의 `_run_reconcile_pipeline`을
  `hydrate_state`(Task5)/`check_manual_intervention`(Task8)이 공유.
- 결정2(재시작 중 발견된 수동개입도 동일 정책) → Task6의 `_detect_external_orders`가
  `hydrate_state`/`check_manual_intervention` 양쪽에서 동일하게 호출됨(분기 없음).
- 결정3(self-heal) → Task7의 `_apply_explained_change`/`_self_heal_unexplained`.
- 결정4(정밀 체결가 추적, 완전히 설명될 때만) → Task7 `_reconcile_position`의
  `abs(diff - explained_diff) <= _QTY_EPSILON` 판정.
- 결정5(설명 안 되면 강제 all_stop) → Task7의 else 분기, 정책 값 무시하고 항상 paused.
- 결정6(hydrate_state의 wait limit 동기화는 catch-up) → Task4.
- 결정7(여러 외부주문 가중평균) → Task7의 `_weighted_fill`.
- 결정8(API 실패 조용히 스킵) → Task8의 `_run_reconcile_pipeline` try/except.
- 결정9(baseline_qty로 승인 전 보유코인 격리) → Task1(스키마) + Task5(첫 호출 캡처) +
  Task7(`raw_balance - baseline_qty`로 대조).

**플레이스홀더 스캔:** 없음 — 모든 스텝에 완전한 코드가 있다. Task5가 `hydrate_state`를
"부분 완성" 상태로 커밋하는 것처럼 보일 수 있으나, 그 시점에 존재하지 않는
`_run_reconcile_pipeline` 호출은 실제로 도달 불가능한 코드 경로가 아니라 Task8에서 함수
자체가 새로 정의되는 것이므로 `NameError`가 아니라 정상적으로 이어진다(Python은 함수
본문 안의 이름을 호출 시점에 해석하므로, `hydrate_state` 정의 시점에는
`_run_reconcile_pipeline`이 없어도 에러가 나지 않고, Task8이 그 함수를 추가한 뒤에야
실제로 호출되는 테스트가 통과한다) — Task5의 Step4가 `baseline_qty`가 없는 경로만
테스트하는 이유가 이것이다.

**타입 일관성:** `_apply_explained_change`/`_self_heal_unexplained`가 반환하는 action
문자열(`"opened"`/`"closed"`/`"adjusted"`/`"unexplained"`)이 `_reconcile_position`의
반환 dict `"action"` 키와 스펙의 반환 형태 설명(`"none"|"opened"|"closed"|"adjusted"|
"unexplained"`)과 정확히 일치한다. `hydrate_state`/`check_manual_intervention`/
`_run_reconcile_pipeline`이 전부 `strategy: dict, *, client=None` 시그니처로 통일돼
daemon.py(⑤-4b)가 어느 함수를 호출하든 같은 방식으로 다룰 수 있다.

**스코프 경계:** daemon.py의 스케줄링, 손절/익절 실시간 평가, `limit` 모드 장기 감시
루프는 이 플랜에서 다루지 않고 ⑤-4b로 넘겼다(설계 스펙 "범위" 절과 동일).
