# 라이브 트레이딩 서브플랜⑤-1 — DB CRUD + position_manager + risk_manager Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **워크트리를 만들지 말고 main 브랜치에서 직접 작업한다** (사용자 지시, [[upbit-v1-worktree-workflow-changed]]).

**Goal:** `trading/db.py`에 이 서브플랜이 실제로 쓰는 CRUD 함수(live_strategies/positions/
circuit_breaker_state/daily_performance)를 추가하고, 그 위에 순수 로직 모듈
`trading/position_manager.py`(복리 자금관리)와 `trading/risk_manager.py`(서킷브레이커+일별
성과)를 완성한다.

**Architecture:** 설계 스펙
`docs/superpowers/specs_v1/2026-08-07-live-trading-position-risk-manager-design.md`를 그대로
구현한다. 서브플랜⑤(트레이딩 엔진 코어) 전체를 4단계로 쪼갠 것 중 첫 단계다(사용자와
합의). `trading/db.py`는 스키마+CRUD를 한 파일에 두는 `engine/cache.py` 패턴을 따르고,
`position_manager.py`/`risk_manager.py`는 그 CRUD 함수만 호출하는 순수 로직 모듈이라
`engine/`도 `upbit_client`도 import하지 않는다.

**Tech Stack:** Python, `sqlite3`(표준 라이브러리), `pytest`. 새 의존성 없음.

## Global Constraints

- `trading/db.py`에 추가하는 CRUD 함수는 전부 함수 안에서 `_connect()`로 커넥션을 열고
  끝에서 닫는다(요청마다 새 커넥션, 기존 관례). SELECT류는 `conn.row_factory = sqlite3.Row`
  로 설정한 뒤 `dict(row)`로 변환해 반환한다.
- `position_manager.py`/`risk_manager.py`는 `trading/db.py` 외에 이 프로젝트의 다른
  코드를 import하지 않는다(설계 스펙 결정 1 — 계좌 잔고는 파라미터로 받는다, `engine`도
  `trading/upbit_client`도 import 금지).
- 이 서브플랜은 `signals`/`orders`/`manual_intervention_events` 테이블의 CRUD를 만들지
  않는다(YAGNI — 각각 신호평가/주문실행/reconciler 서브플랜에서).
- **설계 스펙 대비 시그니처 정정 1개:** 스펙은
  `record_trade_result(live_strategy_id, realized_pnl, realized_pnl_pct, capital_after)`로
  적었지만, `daily_performance.realized_pnl_pct`는 그날 누적 손익을 `starting_balance` 대비
  퍼센트로 매번 다시 계산해야 의미가 일관된다(스펙 원문대로 첫 거래는 호출자가 준 퍼센트를
  그대로 쓰고 이후 거래는 누적 계산을 하면, 같은 컬럼이 "이번 거래 퍼센트"와 "누적 퍼센트"를
  섞어 쓰게 돼 값이 부정확해진다 — 플랜 작성 중 발견). 그래서 `realized_pnl_pct` 파라미터를
  없애고 `record_trade_result(live_strategy_id, realized_pnl, capital_after)`로 정정한다 —
  함수 내부에서 매번 `누적_realized_pnl / starting_balance * 100`을 계산해 저장한다(아래
  Task 6 참고). `position_manager.close_position()`이 반환하는 `realized_pnl_pct`(거래 1건당
  퍼센트)는 그대로 유지 — 그건 호출자가 로그/알림 등에 쓸 수 있는 별개의 값이다.
- 커밋은 태스크 단위로 작게, 테스트가 통과한 뒤에만 한다.

---

## File Structure

- **Modify:** `trading/db.py` — 12개 CRUD 함수 추가(live_strategies 4개, positions 4개,
  circuit_breaker_state 2개, daily_performance 2개). `import sqlite3`는 이미 있음,
  `import uuid` 추가.
- **Create:** `trading/position_manager.py` — `calculate_initial_capital`/`open_position`/
  `get_open_position`/`close_position`.
- **Create:** `trading/risk_manager.py` — `today_kst`/`record_trade_result`/
  `check_circuit_breaker`.
- **Create:** `tests/trading_db_fixtures.py` — `insert_live_strategy()` 공용 테스트 헬퍼
  (FK 제약 때문에 이후 모든 태스크의 테스트가 먼저 유효한 `live_strategy_id`를 만들어야
  함).
- **Modify:** `tests/test_trading_db.py` — 이 서브플랜의 CRUD 함수 테스트 추가(기존
  스키마 테스트는 그대로 둠).
- **Create:** `tests/test_position_manager.py`, `tests/test_risk_manager.py`.

---

### Task 1: `trading/db.py` — `live_strategies` CRUD + 공용 테스트 픽스처

**Files:**
- Modify: `trading/db.py`
- Create: `tests/trading_db_fixtures.py`
- Modify: `tests/test_trading_db.py`

**Interfaces:**
- Consumes: `trading.db._connect()`(기존).
- Produces: `trading.db.get_live_strategy(live_strategy_id: str) -> dict | None`,
  `trading.db.update_live_strategy_status(live_strategy_id: str, status: str) -> None`,
  `trading.db.update_live_strategy_capital(live_strategy_id: str, current_capital: float) ->
  None`, `trading.db.update_live_strategy_last_candle(live_strategy_id: str, candle_time: str)
  -> None`, `tests.trading_db_fixtures.insert_live_strategy(db_module, **overrides) -> str`
  (이후 모든 태스크의 테스트가 재사용).

- [x] **Step 1: 공용 테스트 픽스처 작성**

`tests/trading_db_fixtures.py`(신규 파일):
```python
"""trading/db.py의 CRUD 함수를 테스트할 때 FK 제약을 만족시키기 위한 공용 픽스처."""
from __future__ import annotations

import uuid


def insert_live_strategy(db_module, **overrides) -> str:
    """유효한 live_strategies 행을 만들고 id를 반환한다. positions/circuit_breaker_state/
    daily_performance는 전부 live_strategy_id를 외래키로 참조하므로, 이 헬퍼 없이는
    그 테이블들의 CRUD 테스트를 작성할 수 없다."""
    defaults = {
        "id": str(uuid.uuid4()),
        "market": "KRW-BTC",
        "timeframe": "minutes60",
        "buy_conditions_json": "{}",
        "sell_conditions_json": "{}",
        "risk_config_json": "{}",
        "current_capital": 100000.0,
        "status": "running",
    }
    defaults.update(overrides)

    conn = db_module._connect()
    try:
        conn.execute(
            "INSERT INTO live_strategies "
            "(id, market, timeframe, buy_conditions_json, sell_conditions_json, "
            "risk_config_json, current_capital, status) "
            "VALUES (:id, :market, :timeframe, :buy_conditions_json, :sell_conditions_json, "
            ":risk_config_json, :current_capital, :status)",
            defaults,
        )
        conn.commit()
    finally:
        conn.close()
    return defaults["id"]
```

- [x] **Step 2: 실패하는 테스트 작성**

`tests/test_trading_db.py` 파일 끝에 추가:
```python
from tests.trading_db_fixtures import insert_live_strategy


def test_get_live_strategy_returns_row_as_dict(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, market="KRW-ETH")

    result = db.get_live_strategy(strategy_id)

    assert result["id"] == strategy_id
    assert result["market"] == "KRW-ETH"
    assert result["status"] == "running"


def test_get_live_strategy_returns_none_when_not_found(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    assert db.get_live_strategy("nonexistent-id") is None


def test_update_live_strategy_status(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="running")

    db.update_live_strategy_status(strategy_id, "paused")

    assert db.get_live_strategy(strategy_id)["status"] == "paused"


def test_update_live_strategy_capital(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, current_capital=100000.0)

    db.update_live_strategy_capital(strategy_id, 105320.5)

    assert db.get_live_strategy(strategy_id)["current_capital"] == 105320.5


def test_update_live_strategy_last_candle(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)

    db.update_live_strategy_last_candle(strategy_id, "2026-08-07T10:00:00+00:00")

    assert db.get_live_strategy(strategy_id)["last_processed_candle_time"] == "2026-08-07T10:00:00+00:00"
```

- [x] **Step 3: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_trading_db.py -v -k live_strategy`
Expected: FAIL — `AttributeError: module 'trading.db' has no attribute 'get_live_strategy'`

- [x] **Step 4: `trading/db.py`에 구현 추가**

파일 맨 위 import를 아래로 교체:
```python
from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
```

`def _connect() -> sqlite3.Connection:` 함수 뒤(파일 끝)에 추가:
```python


def get_live_strategy(live_strategy_id: str) -> dict | None:
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM live_strategies WHERE id = ?", (live_strategy_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_live_strategy_status(live_strategy_id: str, status: str) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE live_strategies SET status = ? WHERE id = ?", (status, live_strategy_id)
        )
        conn.commit()
    finally:
        conn.close()


def update_live_strategy_capital(live_strategy_id: str, current_capital: float) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE live_strategies SET current_capital = ? WHERE id = ?",
            (current_capital, live_strategy_id),
        )
        conn.commit()
    finally:
        conn.close()


def update_live_strategy_last_candle(live_strategy_id: str, candle_time: str) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE live_strategies SET last_processed_candle_time = ? WHERE id = ?",
            (candle_time, live_strategy_id),
        )
        conn.commit()
    finally:
        conn.close()
```

- [x] **Step 5: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_trading_db.py -v`
Expected: 기존 6개 + 신규 5개 = 11개 전부 PASS

- [x] **Step 6: 커밋**

```bash
git add trading/db.py tests/trading_db_fixtures.py tests/test_trading_db.py
git commit -m "feat: trading/db.py에 live_strategies CRUD 추가 + 공용 테스트 픽스처"
```

---

### Task 2: `trading/db.py` — `positions` CRUD

**Files:**
- Modify: `trading/db.py`
- Modify: `tests/test_trading_db.py`

**Interfaces:**
- Consumes: Task 1의 `insert_live_strategy` 픽스처.
- Produces: `trading.db.insert_position(live_strategy_id: str, market: str, entry_price:
  float, entry_qty: float) -> str`, `trading.db.get_position(position_id: str) -> dict |
  None`, `trading.db.close_position_row(position_id: str, exit_price: float, exit_qty: float,
  realized_pnl: float, realized_pnl_pct: float, close_reason: str) -> None`,
  `trading.db.get_open_position(live_strategy_id: str) -> dict | None`.

`get_position(position_id)`은 설계 스펙의 함수 목록엔 없었지만, Task 5의
`position_manager.close_position()`이 청산 전에 `entry_price`/`entry_qty`/`live_strategy_id`를
읽어야 해서 이 태스크에서 함께 추가한다(`get_open_position`과 거의 같은 쿼리지만 status
필터 없이 id로 직접 조회) — 플랜 작성 중 발견된, 스펙 취지를 벗어나지 않는 필요한 보강.

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_trading_db.py` 파일 끝에 추가:
```python
def test_insert_position_and_get_open_position(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)

    position_id = db.insert_position(strategy_id, "KRW-BTC", 100_000_000.0, 0.01)

    open_position = db.get_open_position(strategy_id)
    assert open_position["id"] == position_id
    assert open_position["status"] == "open"
    assert open_position["entry_price"] == 100_000_000.0
    assert open_position["entry_qty"] == 0.01
    assert open_position["entry_time"] is not None


def test_get_open_position_returns_none_when_no_open_position(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    assert db.get_open_position(strategy_id) is None


def test_get_position_by_id(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    position_id = db.insert_position(strategy_id, "KRW-BTC", 100_000_000.0, 0.01)

    result = db.get_position(position_id)

    assert result["id"] == position_id
    assert result["live_strategy_id"] == strategy_id


def test_close_position_row_updates_fields_and_leaves_open_position_none(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    position_id = db.insert_position(strategy_id, "KRW-BTC", 100_000_000.0, 0.01)

    db.close_position_row(position_id, 101_000_000.0, 0.01, 9500.0, 0.95, "signal")

    closed = db.get_position(position_id)
    assert closed["status"] == "closed"
    assert closed["exit_price"] == 101_000_000.0
    assert closed["exit_qty"] == 0.01
    assert closed["realized_pnl"] == 9500.0
    assert closed["realized_pnl_pct"] == 0.95
    assert closed["close_reason"] == "signal"
    assert closed["exit_time"] is not None
    assert db.get_open_position(strategy_id) is None


def test_close_position_row_raises_when_position_not_found(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        db.close_position_row("nonexistent-id", 1.0, 1.0, 0.0, 0.0, "signal")
```

- [x] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_trading_db.py -v -k position`
Expected: FAIL — `AttributeError: module 'trading.db' has no attribute 'insert_position'`

- [x] **Step 3: `trading/db.py`에 구현 추가**

파일 끝에 추가:
```python


def insert_position(live_strategy_id: str, market: str, entry_price: float, entry_qty: float) -> str:
    position_id = str(uuid.uuid4())
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO positions "
            "(id, live_strategy_id, market, status, entry_price, entry_qty, entry_time) "
            "VALUES (?, ?, ?, 'open', ?, ?, datetime('now'))",
            (position_id, live_strategy_id, market, entry_price, entry_qty),
        )
        conn.commit()
    finally:
        conn.close()
    return position_id


def get_position(position_id: str) -> dict | None:
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM positions WHERE id = ?", (position_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def close_position_row(
    position_id: str, exit_price: float, exit_qty: float,
    realized_pnl: float, realized_pnl_pct: float, close_reason: str,
) -> None:
    conn = _connect()
    try:
        cursor = conn.execute(
            "UPDATE positions SET status='closed', exit_price=?, exit_qty=?, "
            "exit_time=datetime('now'), realized_pnl=?, realized_pnl_pct=?, close_reason=? "
            "WHERE id=?",
            (exit_price, exit_qty, realized_pnl, realized_pnl_pct, close_reason, position_id),
        )
        if cursor.rowcount == 0:
            raise ValueError(f"포지션을 찾을 수 없습니다: {position_id}")
        conn.commit()
    finally:
        conn.close()


def get_open_position(live_strategy_id: str) -> dict | None:
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM positions WHERE live_strategy_id = ? AND status = 'open'",
            (live_strategy_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
```

- [x] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_trading_db.py -v`
Expected: 전부 PASS(Task 1의 11개 + 이 태스크의 5개 = 16개)

- [x] **Step 5: 커밋**

```bash
git add trading/db.py tests/test_trading_db.py
git commit -m "feat: trading/db.py에 positions CRUD 추가"
```

---

### Task 3: `trading/db.py` — `circuit_breaker_state` + `daily_performance` CRUD

**Files:**
- Modify: `trading/db.py`
- Modify: `tests/test_trading_db.py`

**Interfaces:**
- Consumes: Task 1의 `insert_live_strategy` 픽스처.
- Produces: `trading.db.get_circuit_breaker_state(live_strategy_id: str) -> dict | None`,
  `trading.db.upsert_circuit_breaker_state(live_strategy_id: str, trading_date: str,
  consecutive_losses: int, tripped: int, tripped_reason: str | None = None, tripped_at: str |
  None = None, resumed_at: str | None = None) -> None`,
  `trading.db.get_daily_performance(live_strategy_id: str, trading_date: str) -> dict | None`,
  `trading.db.upsert_daily_performance(live_strategy_id: str, trading_date: str, realized_pnl:
  float, realized_pnl_pct: float, trade_count: int, win_count: int, loss_count: int,
  starting_balance: float, ending_balance: float) -> None`.

`upsert_daily_performance`는 재호출 시 `starting_balance`를 덮어쓰지 않는다(그날 첫 거래
때 확정된 값을 유지해야 함) — `ON CONFLICT ... DO UPDATE SET`에 `starting_balance`를
포함하지 않는 것으로 구현한다.

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_trading_db.py` 파일 끝에 추가:
```python
def test_circuit_breaker_state_upsert_then_get(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)

    db.upsert_circuit_breaker_state(strategy_id, "2026-08-07", 2, 0)
    result = db.get_circuit_breaker_state(strategy_id)

    assert result["trading_date"] == "2026-08-07"
    assert result["consecutive_losses"] == 2
    assert result["tripped"] == 0
    assert result["tripped_reason"] is None


def test_circuit_breaker_state_upsert_overwrites_existing_row(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)

    db.upsert_circuit_breaker_state(strategy_id, "2026-08-07", 1, 0)
    db.upsert_circuit_breaker_state(
        strategy_id, "2026-08-07", 3, 1, "consecutive_loss_limit", "2026-08-07T12:00:00+00:00",
    )

    result = db.get_circuit_breaker_state(strategy_id)
    assert result["consecutive_losses"] == 3
    assert result["tripped"] == 1
    assert result["tripped_reason"] == "consecutive_loss_limit"


def test_get_circuit_breaker_state_returns_none_when_not_found(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    assert db.get_circuit_breaker_state(strategy_id) is None


def test_daily_performance_upsert_then_get(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)

    db.upsert_daily_performance(
        strategy_id, "2026-08-07", 5000.0, 5.0, 1, 1, 0, 100_000.0, 105_000.0,
    )
    result = db.get_daily_performance(strategy_id, "2026-08-07")

    assert result["realized_pnl"] == 5000.0
    assert result["realized_pnl_pct"] == 5.0
    assert result["trade_count"] == 1
    assert result["win_count"] == 1
    assert result["loss_count"] == 0
    assert result["starting_balance"] == 100_000.0
    assert result["ending_balance"] == 105_000.0


def test_daily_performance_upsert_preserves_starting_balance_on_second_call(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)

    db.upsert_daily_performance(strategy_id, "2026-08-07", 5000.0, 5.0, 1, 1, 0, 100_000.0, 105_000.0)
    db.upsert_daily_performance(strategy_id, "2026-08-07", 3000.0, 3.0, 2, 1, 1, 999_999.0, 103_000.0)

    result = db.get_daily_performance(strategy_id, "2026-08-07")
    assert result["starting_balance"] == 100_000.0  # 두 번째 호출의 999_999.0으로 덮어써지지 않음
    assert result["ending_balance"] == 103_000.0
    assert result["trade_count"] == 2


def test_get_daily_performance_returns_none_when_not_found(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    assert db.get_daily_performance(strategy_id, "2026-08-07") is None
```

- [x] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_trading_db.py -v -k "circuit_breaker_state_upsert or daily_performance"`
Expected: FAIL — `AttributeError: module 'trading.db' has no attribute 'upsert_circuit_breaker_state'`

- [x] **Step 3: `trading/db.py`에 구현 추가**

파일 끝에 추가:
```python


def get_circuit_breaker_state(live_strategy_id: str) -> dict | None:
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM circuit_breaker_state WHERE live_strategy_id = ?",
            (live_strategy_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def upsert_circuit_breaker_state(
    live_strategy_id: str, trading_date: str, consecutive_losses: int, tripped: int,
    tripped_reason: str | None = None, tripped_at: str | None = None, resumed_at: str | None = None,
) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO circuit_breaker_state "
            "(live_strategy_id, trading_date, consecutive_losses, tripped, tripped_reason, "
            "tripped_at, resumed_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(live_strategy_id) DO UPDATE SET "
            "trading_date=excluded.trading_date, "
            "consecutive_losses=excluded.consecutive_losses, "
            "tripped=excluded.tripped, tripped_reason=excluded.tripped_reason, "
            "tripped_at=excluded.tripped_at, resumed_at=excluded.resumed_at",
            (live_strategy_id, trading_date, consecutive_losses, tripped, tripped_reason,
             tripped_at, resumed_at),
        )
        conn.commit()
    finally:
        conn.close()


def get_daily_performance(live_strategy_id: str, trading_date: str) -> dict | None:
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM daily_performance WHERE live_strategy_id = ? AND trading_date = ?",
            (live_strategy_id, trading_date),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def upsert_daily_performance(
    live_strategy_id: str, trading_date: str, realized_pnl: float, realized_pnl_pct: float,
    trade_count: int, win_count: int, loss_count: int,
    starting_balance: float, ending_balance: float,
) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO daily_performance "
            "(trading_date, live_strategy_id, realized_pnl, realized_pnl_pct, trade_count, "
            "win_count, loss_count, starting_balance, ending_balance) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(trading_date, live_strategy_id) DO UPDATE SET "
            "realized_pnl=excluded.realized_pnl, realized_pnl_pct=excluded.realized_pnl_pct, "
            "trade_count=excluded.trade_count, win_count=excluded.win_count, "
            "loss_count=excluded.loss_count, ending_balance=excluded.ending_balance",
            (trading_date, live_strategy_id, realized_pnl, realized_pnl_pct, trade_count,
             win_count, loss_count, starting_balance, ending_balance),
        )
        conn.commit()
    finally:
        conn.close()
```

- [x] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_trading_db.py -v`
Expected: 전부 PASS(16 + 6 = 22개)

- [x] **Step 5: 커밋**

```bash
git add trading/db.py tests/test_trading_db.py
git commit -m "feat: trading/db.py에 circuit_breaker_state/daily_performance CRUD 추가"
```

---

### Task 4: `trading/position_manager.py` — `calculate_initial_capital` + `open_position` + `get_open_position`

**Files:**
- Create: `trading/position_manager.py`
- Create: `tests/test_position_manager.py`

**Interfaces:**
- Consumes: Task 1~3의 `trading.db` CRUD 함수, `tests.trading_db_fixtures.insert_live_strategy`.
- Produces: `trading.position_manager.calculate_initial_capital(risk_config: dict,
  available_balance: float) -> float`, `trading.position_manager.open_position(live_strategy_id:
  str, market: str, entry_price: float, entry_qty: float) -> str`,
  `trading.position_manager.get_open_position(live_strategy_id: str) -> dict | None`.

`percent` 모드의 `position_sizing_value`는 퍼센트 숫자로 정의한다(예: `10`은 `10%`) —
설계 스펙 예시(`risk_config_json` 필드 구성)에 fixed 모드 예시(`100000`)만 있고 percent
모드 값의 단위가 명시되지 않아 이 태스크에서 확정한다.

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_position_manager.py`(신규 파일):
```python
import trading.db as db
from tests.trading_db_fixtures import insert_live_strategy
from trading.position_manager import calculate_initial_capital, get_open_position, open_position


def _fresh_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "trading.db")
    return db


def test_calculate_initial_capital_fixed_mode():
    risk_config = {"position_sizing_mode": "fixed", "position_sizing_value": 100_000}
    assert calculate_initial_capital(risk_config, available_balance=5_000_000.0) == 100_000.0


def test_calculate_initial_capital_percent_mode():
    risk_config = {"position_sizing_mode": "percent", "position_sizing_value": 10}
    assert calculate_initial_capital(risk_config, available_balance=1_000_000.0) == 100_000.0


def test_calculate_initial_capital_clamps_to_max_position_per_market():
    risk_config = {
        "position_sizing_mode": "percent", "position_sizing_value": 50,
        "max_position_per_market": 300_000,
    }
    assert calculate_initial_capital(risk_config, available_balance=1_000_000.0) == 300_000.0


def test_calculate_initial_capital_raises_on_unknown_mode():
    risk_config = {"position_sizing_mode": "unknown", "position_sizing_value": 1}
    try:
        calculate_initial_capital(risk_config, available_balance=1.0)
        assert False, "ValueError가 발생해야 함"
    except ValueError:
        pass


def test_open_position_and_get_open_position(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm)

    position_id = open_position(strategy_id, "KRW-BTC", 100_000_000.0, 0.01)

    result = get_open_position(strategy_id)
    assert result["id"] == position_id
    assert result["market"] == "KRW-BTC"


def test_get_open_position_returns_none_when_no_position(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm)
    assert get_open_position(strategy_id) is None
```

- [x] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_position_manager.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'trading.position_manager'`

- [x] **Step 3: `trading/position_manager.py` 구현**

```python
"""
trading/position_manager.py

전략별 포지션 추적 + 복리 자금관리(스펙 결정 7). trading/db.py의 CRUD 함수만 사용하는
순수 모듈 — 계좌 잔고 조회(업비트 API)는 호출자(승인 API, daemon)의 몫이라 이 모듈은
잔고를 파라미터로 받는다(서브플랜⑤-1 설계 스펙 결정 1). engine/upbit_client 미의존.
"""
from __future__ import annotations

import trading.db as db


def calculate_initial_capital(risk_config: dict, available_balance: float) -> float:
    """position_sizing_mode('fixed'|'percent')에 따라 최초 진입 자금을 계산하고,
    max_position_per_market 상한으로 클램프한다(설계 스펙 결정 7 — 승인 시 1회만 호출).
    percent 모드의 position_sizing_value는 퍼센트 숫자(예: 10 = 10%)다."""
    mode = risk_config["position_sizing_mode"]
    if mode == "fixed":
        capital = float(risk_config["position_sizing_value"])
    elif mode == "percent":
        capital = available_balance * float(risk_config["position_sizing_value"]) / 100
    else:
        raise ValueError(f"지원하지 않는 position_sizing_mode: {mode}")

    max_position = risk_config.get("max_position_per_market")
    if max_position is not None:
        capital = min(capital, float(max_position))
    return capital


def open_position(live_strategy_id: str, market: str, entry_price: float, entry_qty: float) -> str:
    return db.insert_position(live_strategy_id, market, entry_price, entry_qty)


def get_open_position(live_strategy_id: str) -> dict | None:
    return db.get_open_position(live_strategy_id)
```

- [x] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_position_manager.py -v`
Expected: 6개 테스트 전부 PASS

- [x] **Step 5: 커밋**

```bash
git add trading/position_manager.py tests/test_position_manager.py
git commit -m "feat: position_manager에 calculate_initial_capital/open_position/get_open_position 추가"
```

---

### Task 5: `trading/position_manager.py` — `close_position`

**Files:**
- Modify: `trading/position_manager.py`
- Modify: `tests/test_position_manager.py`

**Interfaces:**
- Consumes: Task 2의 `trading.db.get_position`/`close_position_row`, Task 1의
  `trading.db.update_live_strategy_capital`.
- Produces: `trading.position_manager.close_position(position_id: str, exit_price: float,
  exit_qty: float, fee: float, close_reason: str) -> dict`(반환값
  `{"realized_pnl": float, "realized_pnl_pct": float, "capital_after": float}`).

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_position_manager.py` 파일 끝에 추가:
```python
import pytest

from trading.position_manager import close_position


def test_close_position_computes_pnl_and_updates_capital(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm, current_capital=100_000_000.0 * 0.01)
    position_id = open_position(strategy_id, "KRW-BTC", 100_000_000.0, 0.01)

    result = close_position(position_id, 101_000_000.0, 0.01, fee=500.0, close_reason="signal")

    expected_pnl = (101_000_000.0 * 0.01) - (100_000_000.0 * 0.01) - 500.0
    expected_pct = expected_pnl / (100_000_000.0 * 0.01) * 100
    expected_capital_after = 101_000_000.0 * 0.01 - 500.0

    assert result["realized_pnl"] == pytest.approx(expected_pnl)
    assert result["realized_pnl_pct"] == pytest.approx(expected_pct)
    assert result["capital_after"] == pytest.approx(expected_capital_after)

    assert dbm.get_live_strategy(strategy_id)["current_capital"] == pytest.approx(expected_capital_after)
    assert get_open_position(strategy_id) is None


def test_close_position_raises_when_position_not_found(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        close_position("nonexistent-id", 1.0, 1.0, fee=0.0, close_reason="signal")


def test_close_position_handles_loss(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm)
    position_id = open_position(strategy_id, "KRW-BTC", 100_000_000.0, 0.01)

    result = close_position(position_id, 95_000_000.0, 0.01, fee=475.0, close_reason="signal")

    assert result["realized_pnl"] < 0
    assert result["realized_pnl_pct"] < 0
```

- [x] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_position_manager.py -v -k close_position`
Expected: FAIL — `ImportError: cannot import name 'close_position'`

- [x] **Step 3: `trading/position_manager.py`에 구현 추가**

`def get_open_position(...)` 함수 바로 뒤에 추가:
```python


def close_position(
    position_id: str, exit_price: float, exit_qty: float, fee: float, close_reason: str,
) -> dict:
    """포지션을 청산한다. realized_pnl/realized_pnl_pct를 계산해 positions 행을 갱신하고,
    live_strategies.current_capital을 (exit_price*exit_qty - fee)로 갱신한다(복리, 설계
    스펙 결정 7 — 수수료 차감 후 실현금액이 그대로 다음 진입 자금). 반환값은 호출자가
    risk_manager.record_trade_result()에 그대로 넘길 수 있는 형태다."""
    position = db.get_position(position_id)
    if position is None:
        raise ValueError(f"포지션을 찾을 수 없습니다: {position_id}")

    entry_price = position["entry_price"]
    entry_qty = position["entry_qty"]
    live_strategy_id = position["live_strategy_id"]

    realized_pnl = (exit_price * exit_qty) - (entry_price * entry_qty) - fee
    realized_pnl_pct = realized_pnl / (entry_price * entry_qty) * 100
    capital_after = exit_price * exit_qty - fee

    db.close_position_row(position_id, exit_price, exit_qty, realized_pnl, realized_pnl_pct, close_reason)
    db.update_live_strategy_capital(live_strategy_id, capital_after)

    return {
        "realized_pnl": realized_pnl,
        "realized_pnl_pct": realized_pnl_pct,
        "capital_after": capital_after,
    }
```

- [x] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_position_manager.py -v`
Expected: 9개 테스트 전부 PASS

- [x] **Step 5: 커밋**

```bash
git add trading/position_manager.py tests/test_position_manager.py
git commit -m "feat: position_manager에 close_position 추가"
```

---

### Task 6: `trading/risk_manager.py` — `today_kst` + `record_trade_result`

**Files:**
- Create: `trading/risk_manager.py`
- Create: `tests/test_risk_manager.py`

**Interfaces:**
- Consumes: Task 1·3의 `trading.db` CRUD 함수, `tests.trading_db_fixtures.insert_live_strategy`.
- Produces: `trading.risk_manager.today_kst() -> str`,
  `trading.risk_manager.record_trade_result(live_strategy_id: str, realized_pnl: float,
  capital_after: float) -> None`(Global Constraints의 시그니처 정정 반영 — `realized_pnl_pct`
  파라미터 없음).

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_risk_manager.py`(신규 파일):
```python
from datetime import datetime, timedelta, timezone

import trading.db as db
from tests.trading_db_fixtures import insert_live_strategy
from trading.risk_manager import record_trade_result, today_kst


def _fresh_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "trading.db")
    return db


def test_today_kst_matches_manual_kst_calculation():
    kst = timezone(timedelta(hours=9))
    expected = datetime.now(kst).strftime("%Y-%m-%d")
    assert today_kst() == expected


def test_record_trade_result_creates_daily_performance_row_on_first_trade(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm)

    record_trade_result(strategy_id, realized_pnl=5000.0, capital_after=105_000.0)

    row = dbm.get_daily_performance(strategy_id, today_kst())
    assert row["starting_balance"] == 100_000.0  # 105_000 - 5_000 역산
    assert row["ending_balance"] == 105_000.0
    assert row["realized_pnl"] == 5000.0
    assert row["realized_pnl_pct"] == 5.0
    assert row["trade_count"] == 1
    assert row["win_count"] == 1
    assert row["loss_count"] == 0


def test_record_trade_result_accumulates_on_second_trade_same_day(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm)

    record_trade_result(strategy_id, realized_pnl=5000.0, capital_after=105_000.0)
    record_trade_result(strategy_id, realized_pnl=-2000.0, capital_after=103_000.0)

    row = dbm.get_daily_performance(strategy_id, today_kst())
    assert row["starting_balance"] == 100_000.0  # 첫 거래 값 유지
    assert row["ending_balance"] == 103_000.0
    assert row["realized_pnl"] == 3000.0  # 5000 + (-2000) 누적
    assert row["realized_pnl_pct"] == 3.0  # 3000 / 100_000 * 100
    assert row["trade_count"] == 2
    assert row["win_count"] == 1
    assert row["loss_count"] == 1


def test_record_trade_result_increments_consecutive_losses_on_loss(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm)

    record_trade_result(strategy_id, realized_pnl=-1000.0, capital_after=99_000.0)
    record_trade_result(strategy_id, realized_pnl=-500.0, capital_after=98_500.0)

    cb = dbm.get_circuit_breaker_state(strategy_id)
    assert cb["consecutive_losses"] == 2


def test_record_trade_result_resets_consecutive_losses_on_win(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm)

    record_trade_result(strategy_id, realized_pnl=-1000.0, capital_after=99_000.0)
    record_trade_result(strategy_id, realized_pnl=2000.0, capital_after=101_000.0)

    cb = dbm.get_circuit_breaker_state(strategy_id)
    assert cb["consecutive_losses"] == 0
```

- [x] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_risk_manager.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'trading.risk_manager'`

- [x] **Step 3: `trading/risk_manager.py` 구현**

```python
"""
trading/risk_manager.py

전략별 서킷브레이커(일일손실/연속손실) + 일별 성과 집계. trading/db.py의 CRUD 함수만
사용하는 순수 모듈. engine/upbit_client 미의존(서브플랜⑤-1 설계 스펙 결정 1).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import trading.db as db

_KST = timezone(timedelta(hours=9))


def today_kst() -> str:
    return datetime.now(_KST).strftime("%Y-%m-%d")


def record_trade_result(live_strategy_id: str, realized_pnl: float, capital_after: float) -> None:
    """포지션 청산마다 호출. daily_performance를 오늘 날짜(KST) 기준으로 upsert하고
    circuit_breaker_state.consecutive_losses를 갱신한다(이번 거래가 손실이면 +1, 아니면
    0으로 리셋 — 설계 스펙 결정 4). trading_date가 바뀌었으면 연속손실/트립 상태를 먼저
    리셋한다."""
    trading_date = today_kst()

    existing = db.get_daily_performance(live_strategy_id, trading_date)
    if existing is None:
        starting_balance = capital_after - realized_pnl
        cumulative_pnl = realized_pnl
        trade_count = 1
        win_count = 1 if realized_pnl >= 0 else 0
        loss_count = 1 if realized_pnl < 0 else 0
    else:
        starting_balance = existing["starting_balance"]
        cumulative_pnl = existing["realized_pnl"] + realized_pnl
        trade_count = existing["trade_count"] + 1
        win_count = existing["win_count"] + (1 if realized_pnl >= 0 else 0)
        loss_count = existing["loss_count"] + (1 if realized_pnl < 0 else 0)

    cumulative_pnl_pct = (cumulative_pnl / starting_balance * 100) if starting_balance else 0.0

    db.upsert_daily_performance(
        live_strategy_id, trading_date, cumulative_pnl, cumulative_pnl_pct,
        trade_count, win_count, loss_count, starting_balance, capital_after,
    )

    cb_state = db.get_circuit_breaker_state(live_strategy_id)
    if cb_state is None or cb_state["trading_date"] != trading_date:
        tripped = 0
        tripped_reason = None
        tripped_at = None
    else:
        tripped = cb_state["tripped"]
        tripped_reason = cb_state["tripped_reason"]
        tripped_at = cb_state["tripped_at"]

    prior_consecutive_losses = (
        cb_state["consecutive_losses"]
        if cb_state is not None and cb_state["trading_date"] == trading_date
        else 0
    )
    consecutive_losses = prior_consecutive_losses + 1 if realized_pnl < 0 else 0

    db.upsert_circuit_breaker_state(
        live_strategy_id, trading_date, consecutive_losses, tripped, tripped_reason, tripped_at,
    )
```

- [x] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_risk_manager.py -v`
Expected: 5개 테스트 전부 PASS

- [x] **Step 5: 커밋**

```bash
git add trading/risk_manager.py tests/test_risk_manager.py
git commit -m "feat: risk_manager에 today_kst/record_trade_result 추가"
```

---

### Task 7: `trading/risk_manager.py` — `check_circuit_breaker`

**Files:**
- Modify: `trading/risk_manager.py`
- Modify: `tests/test_risk_manager.py`

**Interfaces:**
- Consumes: Task 6의 `record_trade_result`/`today_kst`, Task 1·3의 `trading.db` CRUD 함수.
- Produces: `trading.risk_manager.check_circuit_breaker(live_strategy_id: str, risk_config:
  dict) -> bool`.

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_risk_manager.py` 파일 끝에 추가:
```python
from trading.risk_manager import check_circuit_breaker


def test_check_circuit_breaker_returns_false_when_within_limits(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm)
    risk_config = {"daily_loss_limit_pct": -5.0, "consecutive_loss_limit": 3}

    record_trade_result(strategy_id, realized_pnl=1000.0, capital_after=101_000.0)

    assert check_circuit_breaker(strategy_id, risk_config) is False
    assert dbm.get_live_strategy(strategy_id)["status"] != "paused"


def test_check_circuit_breaker_trips_on_daily_loss_limit(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm, status="running")
    risk_config = {"daily_loss_limit_pct": -5.0, "consecutive_loss_limit": 10}

    record_trade_result(strategy_id, realized_pnl=-6000.0, capital_after=94_000.0)

    assert check_circuit_breaker(strategy_id, risk_config) is True
    cb = dbm.get_circuit_breaker_state(strategy_id)
    assert cb["tripped"] == 1
    assert cb["tripped_reason"] == "daily_loss_limit"
    assert cb["tripped_at"] is not None
    assert dbm.get_live_strategy(strategy_id)["status"] == "paused"


def test_check_circuit_breaker_trips_on_consecutive_loss_limit(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm, status="running")
    risk_config = {"daily_loss_limit_pct": -50.0, "consecutive_loss_limit": 2}

    record_trade_result(strategy_id, realized_pnl=-100.0, capital_after=99_900.0)
    record_trade_result(strategy_id, realized_pnl=-100.0, capital_after=99_800.0)

    assert check_circuit_breaker(strategy_id, risk_config) is True
    cb = dbm.get_circuit_breaker_state(strategy_id)
    assert cb["tripped_reason"] == "consecutive_loss_limit"
    assert dbm.get_live_strategy(strategy_id)["status"] == "paused"


def test_check_circuit_breaker_returns_true_immediately_when_already_tripped(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm, status="paused")
    risk_config = {"daily_loss_limit_pct": -5.0, "consecutive_loss_limit": 3}
    dbm.upsert_circuit_breaker_state(strategy_id, today_kst(), 0, 1, "daily_loss_limit", "2026-08-07T00:00:00+00:00")

    assert check_circuit_breaker(strategy_id, risk_config) is True


def test_check_circuit_breaker_ignores_missing_limits(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm)
    risk_config: dict = {}  # 한도 미설정

    record_trade_result(strategy_id, realized_pnl=-999_999.0, capital_after=1.0)

    assert check_circuit_breaker(strategy_id, risk_config) is False
```

- [x] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_risk_manager.py -v -k check_circuit_breaker`
Expected: FAIL — `ImportError: cannot import name 'check_circuit_breaker'`

- [x] **Step 3: `trading/risk_manager.py`에 구현 추가**

파일 끝에 추가:
```python


def check_circuit_breaker(live_strategy_id: str, risk_config: dict) -> bool:
    """오늘(KST)의 daily_performance.realized_pnl_pct와
    circuit_breaker_state.consecutive_losses를 risk_config의 한도와 비교한다. 이미
    tripped=1이면 즉시 True. 새로 한도를 넘었으면 circuit_breaker_state.tripped=1 +
    tripped_reason + tripped_at을 기록하고 live_strategies.status를 'paused'로 바꾼 뒤
    True를 반환한다(설계 스펙 결정 3 — 판정과 반응을 하나의 함수 안에서 원자적으로 처리).
    한도 안이면 False."""
    trading_date = today_kst()
    cb_state = db.get_circuit_breaker_state(live_strategy_id)
    is_today = cb_state is not None and cb_state["trading_date"] == trading_date

    if is_today and cb_state["tripped"]:
        return True

    daily = db.get_daily_performance(live_strategy_id, trading_date)
    consecutive_losses = cb_state["consecutive_losses"] if is_today else 0

    daily_loss_limit_pct = risk_config.get("daily_loss_limit_pct")
    consecutive_loss_limit = risk_config.get("consecutive_loss_limit")

    tripped_reason = None
    if (
        daily is not None
        and daily_loss_limit_pct is not None
        and daily["realized_pnl_pct"] <= daily_loss_limit_pct
    ):
        tripped_reason = "daily_loss_limit"
    elif consecutive_loss_limit is not None and consecutive_losses >= consecutive_loss_limit:
        tripped_reason = "consecutive_loss_limit"

    if tripped_reason is None:
        return False

    db.upsert_circuit_breaker_state(
        live_strategy_id, trading_date, consecutive_losses, 1, tripped_reason,
        datetime.now(_KST).isoformat(),
    )
    db.update_live_strategy_status(live_strategy_id, "paused")
    return True
```

- [x] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_risk_manager.py -v`
Expected: 10개 테스트 전부 PASS

- [x] **Step 5: 커밋**

```bash
git add trading/risk_manager.py tests/test_risk_manager.py
git commit -m "feat: risk_manager에 check_circuit_breaker 추가"
```

---

### Task 8: 최종 통합 확인 + 전체 회귀

**Files:**
- Modify: `trading/db.py`(문서화만, 필요 시)

**Interfaces:**
- Consumes: 이 플랜의 모든 이전 태스크 산출물.
- Produces: 없음(검증 전용 태스크).

- [x] **Step 1: `position_manager.py`/`risk_manager.py`가 `engine`/`upbit_client`를 안 쓰는지 확인**

Run:
```bash
python -c "
import ast
for path in ['trading/position_manager.py', 'trading/risk_manager.py']:
    tree = ast.parse(open(path, encoding='utf-8').read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split('.')[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split('.')[0])
    assert 'engine' not in names, f'{path}가 engine을 import함: {names}'
    assert 'httpx' not in names, f'{path}가 httpx를 import함(네트워크 의존): {names}'
    print(path, '-> engine/httpx 미의존 확인, imports:', sorted(names))
"
```
Expected: 두 파일 모두 `engine`/`httpx` 미포함 출력, 에러 없이 통과.

- [x] **Step 2: `position_manager.close_position()`과 `risk_manager.record_trade_result()`를
  연결한 통합 시나리오 수동 확인**

Run:
```bash
python -c "
import tempfile
from pathlib import Path

import trading.db as db
db.DB_PATH = Path(tempfile.mkdtemp()) / 'trading.db'

from tests.trading_db_fixtures import insert_live_strategy
from trading.position_manager import close_position, open_position
from trading.risk_manager import check_circuit_breaker, record_trade_result, today_kst

strategy_id = insert_live_strategy(db, current_capital=100000.0)
position_id = open_position(strategy_id, 'KRW-BTC', 100_000_000.0, 0.001)
result = close_position(position_id, 90_000_000.0, 0.001, fee=90.0, close_reason='stop_loss')
record_trade_result(strategy_id, result['realized_pnl'], result['capital_after'])
tripped = check_circuit_breaker(strategy_id, {'daily_loss_limit_pct': -5.0, 'consecutive_loss_limit': 3})

print('realized_pnl:', result['realized_pnl'])
print('capital_after:', result['capital_after'])
print('daily_performance:', db.get_daily_performance(strategy_id, today_kst()))
print('circuit_breaker tripped:', tripped)
assert db.get_live_strategy(strategy_id)['current_capital'] == result['capital_after']
print('OK: position_manager -> risk_manager 흐름 정상 연결 확인')
"
```
Expected: 에러 없이 `OK: position_manager -> risk_manager 흐름 정상 연결 확인` 출력. 10%
손실(100_000_000*0.001 -> 90_000_000*0.001, 수수료 90원)이므로 `daily_loss_limit_pct=-5.0`
한도를 넘어 `tripped: True`가 나와야 한다(수동으로 값 확인).

- [x] **Step 3: 전체 테스트 스위트 실행(회귀 확인)**

Run: `python -m pytest -q`
Expected: 전부 PASS(서브플랜④까지의 기존 467개 + 이 플랜의 신규 테스트 전부 포함, 정확한
합계는 실행 결과로 확인).

- [x] **Step 4: 커밋**

이 태스크는 검증 전용이라 코드 변경이 없으면 커밋할 게 없다 — Step 1~3이 전부 통과하면
빈 diff이므로 커밋을 생략한다. 검증 중 실제 코드 수정이 필요했다면 그 수정을 커밋한다:
```bash
git add trading/position_manager.py trading/risk_manager.py
git commit -m "fix: position_manager/risk_manager 최종 통합 검증에서 발견된 문제 수정"
```

---

## Self-Review

**스펙 커버리지:**
- 설계 스펙의 DB CRUD 함수 목록(live_strategies 4개, positions 3개+보강 1개,
  circuit_breaker_state 2개, daily_performance 2개) → Task 1~3에서 전부 구현.
- 결정 1(position_manager는 순수 함수, 잔고는 파라미터) → `calculate_initial_capital`이
  `available_balance`를 파라미터로 받고 `trading.db` 외 아무것도 import하지 않음(Task 4·8에서
  검증).
- 결정 2(CRUD는 db.py에) → 전부 Task 1~3에서 `trading/db.py`에 구현, `position_manager.py`/
  `risk_manager.py`는 그 함수들만 호출.
- 결정 3(record_trade_result와 check_circuit_breaker 분리, 트립 시 원자적 기록) → Task 6·7에서
  정확히 그렇게 구현.
- 결정 4(연속손실 = 손실이면 +1, 아니면 리셋, 날짜 바뀌면 리셋) → Task 6에서 구현·테스트됨.
- Global Constraints에 명시한 스펙 대비 시그니처 정정(`record_trade_result`에서
  `realized_pnl_pct` 파라미터 제거) → Task 6에서 반영, 이유를 문서화함.
- 이 플랜은 `position_manager`/`risk_manager`를 실제로 엮어 포지션 진입/청산을 트리거하는
  로직(주문 체결 후 처리)은 다루지 않는다 — `order_executor.py`(⑤-3)로 명확히 넘김. Task 8의
  Step 2는 "두 모듈이 서로 호출 가능한 조합으로 잘 맞물리는지"만 수동 확인하는 것이지,
  실제 daemon 통합이 아니다.

**플레이스홀더 스캔:** 없음 — 모든 스텝에 완전한 코드가 있다.

**타입 일관성:** `trading/db.py`의 모든 조회 함수는 `-> dict | None`, 쓰기 함수는
`-> None`(insert류만 `-> str`로 생성된 id 반환)으로 일관된다. `position_manager.close_position()`과
`risk_manager.record_trade_result()`의 파라미터 이름(`realized_pnl`/`capital_after`)이
정확히 일치해 호출자가 앞의 반환값을 뒤의 인자로 그대로 넘길 수 있다(Task 8 Step 2에서
실제로 그렇게 사용해 검증).

---

## 다음 서브플랜 (이 문서 이후)

⑤-2 **신호평가 엔진** — `signal_engine.py`가 서브플랜②·③의 `live_indicators.py`
(`LIVE_INDICATOR_FACTORY` 39개 + `fetch_live_*` 3개) + 서브플랜①의
`eval_group_values()`를 결합하고, `upbit_data_service.get_candles()`를 REST로 폴링해 새
봉 마감을 감지, `signals` 테이블에 판단을 기록한다(이 서브플랜에서 다루지 않은
`signals` CRUD가 여기서 추가됨). ⑤-3 **주문실행** — `order_executor.py`가 이 플랜의
`position_manager`와 서브플랜④의 `upbit_client`를 엮어 시장가/지정가/지정가+타임아웃
3모드를 구현(`orders` CRUD도 여기서 추가). ⑤-4 **reconciler + daemon 메인루프** —
`reconciler.py`(수동개입 감지, `manual_intervention_events` CRUD 추가) +
`daemon.py`(State Hydration, 위 4개 모듈 전부 결합).
