# 매매일지 수수료 반영 + 그래프 리디자인 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 매수 수수료 미반영으로 과대 계상되던 실현손익을 고치고(신규 거래 + 과거 거래 소급 재계산), 매매일지 그래프를 "총자산 누적선"에서 "최근 30일 일별 실현손익 막대그래프"로 바꾼다.

**Architecture:** `trading/position_manager.py`의 손익 계산식에 `entry_fee`를 추가하고, `positions.entry_fee` 컬럼을 신설한다. 이미 청산된 거래는 `scripts/backfill_entry_fee.py`(1회성)로 소급 재계산한다. 그래프는 `backend/trading_analytics_service.py`에 새 `daily_pnl_30d` 필드(0-채움 30일 배열)를 추가하고, 프론트엔드는 새 `DailyPnlBarChart` 컴포넌트로 `recharts` `LineChart`를 `BarChart`로 교체한다.

**Tech Stack:** FastAPI + SQLite(trading.db) 백엔드, Next.js 14 + recharts 3 + Tailwind 4 프론트엔드, pytest.

## Global Constraints

- 스펙 문서: `docs/superpowers/specs_v1/2026-08-18-journal-fee-and-chart-redesign-design.md` — 모든 태스크는 이 문서의 결정을 그대로 따른다.
- 이 프로젝트는 "개발 단계 무마이그레이션 정책"(스키마 변경 시 DB 파일 재생성)을 쓰지만, `trading.db`는 지금 AWS에서 실거래 중인 프로덕션 데이터라 파일을 지울 수 없다 — `entry_fee` 컬럼만 예외적으로 실제 `ALTER TABLE`로 추가한다(다른 컬럼들의 "크게 실패시키는 assert" 패턴과 다름, 이유를 코드 주석에 남긴다).
- 프론트 타입체크는 `cd frontend && npx tsc --noEmit`로 한다. **dev 서버가 떠 있는 동안 `npm run build`를 실행하지 않는다** — 라이브 `.next`가 손상되는 알려진 이슈가 있다.
- 손익/수익률 색상은 이 앱의 기존 관례(`frontend/lib/return-rate-color.ts`)를 따른다 — **양수=빨강, 음수=파랑**(한국 증시 관례, 서구식 초록/빨강 아님).
- 마이그레이션 스크립트(`scripts/backfill_entry_fee.py`)는 기본이 드라이런이고, `--apply` 플래그를 줘야 실제로 DB를 바꾼다. `--apply` 실행 전 DB 파일을 자동 백업한다.
- 백엔드 테스트: `python -m pytest tests/ -v`. 개별 파일: `python -m pytest tests/test_xxx.py -v`.

---

### Task 1: DB 레이어 — `entry_fee` 컬럼 + CRUD 함수

**Files:**
- Modify: `trading/db.py` (스키마 30-90행 부근, `_connect()` 193-203행, `insert_position()` 266-279행, 새 함수는 `close_position_row()` 다음인 328행 부근에 추가)
- Test: `tests/test_trading_db.py`

**Interfaces:**
- Produces: `db.insert_position(live_strategy_id, market, entry_price, entry_qty, entry_fee=0.0) -> str`, `db.update_position_entry_fee(position_id, entry_fee) -> None`, `db.update_position_realized_pnl(position_id, realized_pnl, realized_pnl_pct) -> None`, `db.update_order_position_id(order_id, position_id) -> None`. `positions` 행 dict에 `entry_fee` 키가 포함됨(`get_position`/`list_closed_positions`/`get_open_position` 모두 `SELECT *`라 자동 반영).

- [ ] **Step 1: entry_fee 컬럼 자동 추가를 검증하는 실패하는 테스트 작성**

`tests/test_trading_db.py` 파일 끝에 추가:

```python
def test_connect_adds_entry_fee_column_to_existing_positions_table(monkeypatch, tmp_path):
    """entry_fee는 실거래 중인 프로덕션 DB에 적용해야 해서, 다른 컬럼들의 "크게 실패시키는
    assert" 패턴과 달리 실제 ALTER TABLE로 추가한다(무마이그레이션 정책의 유일한 예외)."""
    db = _fresh_db(monkeypatch, tmp_path)
    conn = sqlite3.connect(db.DB_PATH)
    conn.execute("""
        CREATE TABLE positions (
            id               TEXT PRIMARY KEY,
            live_strategy_id TEXT NOT NULL,
            market           TEXT NOT NULL,
            status           TEXT NOT NULL DEFAULT 'open',
            entry_price      REAL,
            entry_qty        REAL,
            entry_time       TEXT,
            exit_price       REAL,
            exit_qty         REAL,
            exit_time        TEXT,
            realized_pnl     REAL,
            realized_pnl_pct REAL,
            close_reason     TEXT,
            stale_resolved_qty      REAL NOT NULL DEFAULT 0,
            stale_resolved_proceeds REAL NOT NULL DEFAULT 0,
            stale_resolved_fee      REAL NOT NULL DEFAULT 0,
            created_at       TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("INSERT INTO positions (id, live_strategy_id, market) VALUES ('p1', 's1', 'KRW-BTC')")
    conn.commit()
    conn.close()

    db._connect()

    conn = sqlite3.connect(db.DB_PATH)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(positions)")}
        row = conn.execute("SELECT entry_fee FROM positions WHERE id = 'p1'").fetchone()
    finally:
        conn.close()
    assert "entry_fee" in columns
    assert row[0] == 0
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_trading_db.py::test_connect_adds_entry_fee_column_to_existing_positions_table -v`
Expected: FAIL (`sqlite3.OperationalError: no such column: entry_fee`)

- [ ] **Step 3: 스키마에 컬럼 추가 + `_connect()`에 자동 ALTER 로직 배선**

`trading/db.py`의 `positions` CREATE TABLE(현재 50-68행)에 `entry_fee` 컬럼 추가:

```python
CREATE TABLE IF NOT EXISTS positions (
    id               TEXT PRIMARY KEY,
    live_strategy_id TEXT NOT NULL REFERENCES live_strategies(id),
    market           TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'open',
    entry_price      REAL,
    entry_qty        REAL,
    entry_fee        REAL NOT NULL DEFAULT 0,
    entry_time       TEXT,
    exit_price       REAL,
    exit_qty         REAL,
    exit_time        TEXT,
    realized_pnl     REAL,
    realized_pnl_pct REAL,
    close_reason     TEXT,
    stale_resolved_qty      REAL NOT NULL DEFAULT 0,
    stale_resolved_proceeds REAL NOT NULL DEFAULT 0,
    stale_resolved_fee      REAL NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);
```

`_assert_live_strategies_manual_pause_column_present` 함수(170-190행) 바로 다음에 새 함수 추가:

```python
def _ensure_positions_entry_fee_column(conn: sqlite3.Connection) -> None:
    """CREATE TABLE IF NOT EXISTS는 이미 존재하는 positions 테이블에 새 컬럼 entry_fee를
    추가하지 못한다. 다른 컬럼들은 "DB 파일을 지우고 다시 시작하라"는 assert로 크게
    실패시키지만(무마이그레이션 정책), entry_fee는 지금 AWS에서 실거래 중인 프로덕션
    DB에 적용해야 해서 파일을 지울 수 없다 — 컬럼이 없으면 실제 ALTER TABLE로 추가한다
    (기존 행은 DEFAULT 0으로 채워짐, 이후 백필 스크립트가 정확한 값으로 갱신한다)."""
    table_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='positions'"
    ).fetchone() is not None
    if not table_exists:
        return
    columns = {row[1] for row in conn.execute("PRAGMA table_info('positions')")}
    if "entry_fee" in columns:
        return
    conn.execute("ALTER TABLE positions ADD COLUMN entry_fee REAL NOT NULL DEFAULT 0")
    conn.commit()
```

`_connect()`(193-203행)에서 이 함수를 호출하도록 수정:

```python
def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    if DB_PATH not in _initialized_paths:
        conn.executescript(_SCHEMA)
        _assert_signals_unique_constraint_present(conn)
        _assert_live_strategies_manual_pause_column_present(conn)
        _ensure_positions_entry_fee_column(conn)
        _initialized_paths.add(DB_PATH)
    return conn
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_trading_db.py::test_connect_adds_entry_fee_column_to_existing_positions_table -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add trading/db.py tests/test_trading_db.py
git commit -m "feat: positions에 entry_fee 컬럼 추가(기존 DB는 ALTER TABLE로 자동 적용)"
```

- [ ] **Step 6: `insert_position`이 entry_fee를 저장하는 실패하는 테스트 작성**

`tests/test_trading_db.py`에 추가(기존 `test_insert_position_and_get_open_position` 근처):

```python
def test_insert_position_stores_entry_fee(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)

    position_id = db.insert_position(strategy_id, "KRW-BTC", 100_000_000.0, 0.01, entry_fee=500.0)

    position = db.get_position(position_id)
    assert position["entry_fee"] == 500.0


def test_insert_position_defaults_entry_fee_to_zero(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)

    position_id = db.insert_position(strategy_id, "KRW-BTC", 100_000_000.0, 0.01)

    assert db.get_position(position_id)["entry_fee"] == 0.0
```

- [ ] **Step 7: 테스트 실패 확인**

Run: `python -m pytest tests/test_trading_db.py -k entry_fee -v`
Expected: FAIL (`TypeError: insert_position() got an unexpected keyword argument 'entry_fee'`)

- [ ] **Step 8: `insert_position` 시그니처 확장**

`trading/db.py`의 `insert_position`(266-279행)을 다음으로 교체:

```python
def insert_position(
    live_strategy_id: str, market: str, entry_price: float, entry_qty: float,
    entry_fee: float = 0.0,
) -> str:
    position_id = str(uuid.uuid4())
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO positions "
            "(id, live_strategy_id, market, status, entry_price, entry_qty, entry_fee, entry_time) "
            "VALUES (?, ?, ?, 'open', ?, ?, ?, datetime('now'))",
            (position_id, live_strategy_id, market, entry_price, entry_qty, entry_fee),
        )
        conn.commit()
    finally:
        conn.close()
    return position_id
```

- [ ] **Step 9: 테스트 통과 확인**

Run: `python -m pytest tests/test_trading_db.py -k entry_fee -v`
Expected: PASS

- [ ] **Step 10: 커밋**

```bash
git add trading/db.py tests/test_trading_db.py
git commit -m "feat: insert_position이 entry_fee를 받아 저장하도록 확장"
```

- [ ] **Step 11: 백필/추적용 update 함수 3개의 실패하는 테스트 작성**

`tests/test_trading_db.py`에 추가:

```python
def test_update_position_entry_fee_updates_open_position(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    position_id = db.insert_position(strategy_id, "KRW-BTC", 100_000_000.0, 0.01)

    db.update_position_entry_fee(position_id, 777.0)

    assert db.get_position(position_id)["entry_fee"] == 777.0


def test_update_position_realized_pnl_updates_closed_position(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    position_id = db.insert_position(strategy_id, "KRW-BTC", 100_000_000.0, 0.01)
    db.close_position_row(position_id, 101_000_000.0, 0.01, 9500.0, 0.95, "signal")

    db.update_position_realized_pnl(position_id, 9000.0, 0.9)

    closed = db.get_position(position_id)
    assert closed["realized_pnl"] == 9000.0
    assert closed["realized_pnl_pct"] == 0.9


def test_update_position_realized_pnl_ignores_open_position(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    position_id = db.insert_position(strategy_id, "KRW-BTC", 100_000_000.0, 0.01)

    db.update_position_realized_pnl(position_id, 9000.0, 0.9)  # status='open'이라 아무 효과 없어야 함

    position = db.get_position(position_id)
    assert position["realized_pnl"] is None


def test_update_order_position_id_links_order_to_position(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    order_id = db.insert_order(strategy_id, None, "KRW-BTC", "bid", "market", None, None, 100_000_000.0)
    position_id = db.insert_position(strategy_id, "KRW-BTC", 100_000_000.0, 0.01)

    db.update_order_position_id(order_id, position_id)

    assert db.get_order_by_id(order_id)["position_id"] == position_id
```

- [ ] **Step 12: 테스트 실패 확인**

Run: `python -m pytest tests/test_trading_db.py -k "update_position_entry_fee or update_position_realized_pnl or update_order_position_id" -v`
Expected: FAIL (`AttributeError: module 'trading.db' has no attribute ...`)

- [ ] **Step 13: 세 함수 구현**

`trading/db.py`의 `close_position_row()` 함수(312-328행) 바로 다음에 추가:

```python
def update_position_entry_fee(position_id: str, entry_fee: float) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE positions SET entry_fee = ? WHERE id = ?",
            (entry_fee, position_id),
        )
        conn.commit()
    finally:
        conn.close()


def update_position_realized_pnl(position_id: str, realized_pnl: float, realized_pnl_pct: float) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE positions SET realized_pnl = ?, realized_pnl_pct = ? "
            "WHERE id = ? AND status = 'closed'",
            (realized_pnl, realized_pnl_pct, position_id),
        )
        conn.commit()
    finally:
        conn.close()
```

`insert_order()` 함수(447-466행) 바로 다음에 추가:

```python
def update_order_position_id(order_id: str, position_id: str) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE orders SET position_id = ? WHERE id = ?",
            (position_id, order_id),
        )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 14: 테스트 통과 확인**

Run: `python -m pytest tests/test_trading_db.py -k "update_position_entry_fee or update_position_realized_pnl or update_order_position_id" -v`
Expected: PASS

- [ ] **Step 15: 전체 db 테스트 회귀 확인 + 커밋**

Run: `python -m pytest tests/test_trading_db.py -v`
Expected: 전부 PASS

```bash
git add trading/db.py tests/test_trading_db.py
git commit -m "feat: 소급 재계산/주문-포지션 연결용 db 함수 3개 추가"
```

---

### Task 2: `position_manager.py` — 손익 계산에 entry_fee 반영

**Files:**
- Modify: `trading/position_manager.py` (`open_position` 31-32행, `close_position` 39-65행)
- Test: `tests/test_position_manager.py`

**Interfaces:**
- Consumes: Task 1의 `db.insert_position(..., entry_fee=0.0)`, `db.get_position` 반환 dict의 `entry_fee` 키.
- Produces: `position_manager.open_position(live_strategy_id, market, entry_price, entry_qty, entry_fee=0.0) -> str`. `close_position()`의 반환값 형태(`realized_pnl`/`realized_pnl_pct`/`capital_after` 키)는 그대로 유지.

- [ ] **Step 1: entry_fee가 손익에서 차감되는지 확인하는 실패하는 테스트 작성**

`tests/test_position_manager.py`에 추가:

```python
def test_close_position_subtracts_both_entry_and_exit_fee(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm, current_capital=100_000_000.0 * 0.01)
    position_id = open_position(strategy_id, "KRW-BTC", 100_000_000.0, 0.01, entry_fee=500.0)

    result = close_position(position_id, 101_000_000.0, 0.01, fee=505.0, close_reason="signal")

    expected_pnl = (101_000_000.0 * 0.01) - (100_000_000.0 * 0.01) - 500.0 - 505.0
    expected_pct = expected_pnl / (100_000_000.0 * 0.01) * 100

    assert result["realized_pnl"] == pytest.approx(expected_pnl)
    assert result["realized_pnl_pct"] == pytest.approx(expected_pct)
    # capital_after는 매도 체결 실수령액 기준이라 entry_fee와 무관해야 함
    assert result["capital_after"] == pytest.approx(101_000_000.0 * 0.01 - 505.0)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_position_manager.py -k subtracts_both -v`
Expected: FAIL (`TypeError: open_position() got an unexpected keyword argument 'entry_fee'`)

- [ ] **Step 3: `open_position`/`close_position` 수정**

`trading/position_manager.py`의 `open_position`(31-32행)을 교체:

```python
def open_position(
    live_strategy_id: str, market: str, entry_price: float, entry_qty: float,
    entry_fee: float = 0.0,
) -> str:
    return db.insert_position(live_strategy_id, market, entry_price, entry_qty, entry_fee)
```

`close_position`(39-65행)을 교체:

```python
def close_position(
    position_id: str, exit_price: float, exit_qty: float, fee: float, close_reason: str,
) -> dict:
    """포지션을 청산한다. realized_pnl/realized_pnl_pct를 계산해 positions 행을 갱신하고,
    live_strategies.current_capital을 (exit_price*exit_qty - fee)로 갱신한다(복리, 설계
    스펙 결정 7 — 수수료 차감 후 실현금액이 그대로 다음 진입 자금). 반환값은 호출자가
    risk_manager.record_trade_result()에 그대로 넘길 수 있는 형태다.

    realized_pnl은 진입 수수료(entry_fee, positions 행에 저장된 값)와 청산 수수료(fee
    인자)를 모두 차감한다 — capital_after는 매도 실수령액 기준이라 entry_fee와 무관하지만,
    손익 지표는 실제 매수 시 지불한 총 현금(entry_price*entry_qty + entry_fee)을 원가로
    써야 정확하다."""
    position = db.get_position(position_id)
    if position is None:
        raise ValueError(f"포지션을 찾을 수 없습니다: {position_id}")

    entry_price = position["entry_price"]
    entry_qty = position["entry_qty"]
    entry_fee = position["entry_fee"] or 0.0
    live_strategy_id = position["live_strategy_id"]

    realized_pnl = (exit_price * exit_qty) - (entry_price * entry_qty) - entry_fee - fee
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

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_position_manager.py -k subtracts_both -v`
Expected: PASS

- [ ] **Step 5: 전체 position_manager 테스트 회귀 확인(entry_fee 기본값 0이라 기존 테스트는 그대로 통과해야 함)**

Run: `python -m pytest tests/test_position_manager.py -v`
Expected: 전부 PASS

- [ ] **Step 6: 커밋**

```bash
git add trading/position_manager.py tests/test_position_manager.py
git commit -m "fix: close_position이 매수 수수료(entry_fee)도 손익에서 차감하도록 수정"
```

---

### Task 3: `order_executor.py` — `enter()`에서 entry_fee/position_id 배선

**Files:**
- Modify: `trading/order_executor.py` (`enter()` 함수, 410-414행)
- Test: `tests/test_order_executor.py`

**Interfaces:**
- Consumes: Task 2의 `position_manager.open_position(..., entry_fee=0.0)`, Task 1의 `db.update_order_position_id(order_id, position_id)`.
- Produces: 매수 체결 시 `positions.entry_fee`가 실제 체결 수수료로 채워지고, 그 매수 주문 행의 `orders.position_id`가 생성된 포지션 id로 채워짐.

- [ ] **Step 1: 매수 체결 fee가 entry_fee로 저장되고 주문에 position_id가 연결되는지 확인하는 실패하는 테스트 작성**

`tests/test_order_executor.py`의 `test_enter_market_mode_places_price_order_and_records_fill`(209-236행) 근처에 추가:

```python
async def test_enter_records_entry_fee_and_links_order_to_position(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)

    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        return {"uuid": "uuid-1", "state": "done"}

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        return {"state": "done", "executed_volume": "0.01", "remaining_volume": "0",
                "paid_fee": "250.0", "trades": [{"funds": "500000.0"}]}

    monkeypatch.setattr(upbit_client, "create_order", fake_create_order)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    order = await order_executor.enter(strategy, 500_000.0, 50_000_000.0)

    position = position_manager.get_open_position(strategy["id"])
    assert position["entry_fee"] == pytest.approx(250.0)
    linked_order = dbm.get_order_by_id(order["id"])
    assert linked_order["position_id"] == position["id"]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_order_executor.py -k records_entry_fee -v`
Expected: FAIL (`assert 0.0 == pytest.approx(250.0)` — entry_fee가 저장 안 됨)

- [ ] **Step 3: `enter()` 끝부분 수정**

`trading/order_executor.py`의 410-414행을 교체:

```python
    if result["status"] != "done":
        return db.get_order_by_id(result["order_id"])

    position_id = position_manager.open_position(
        strategy["id"], market, result["filled_price"], result["filled_volume"], result["fee"],
    )
    db.update_order_position_id(result["order_id"], position_id)
    return db.get_order_by_id(result["order_id"])
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_order_executor.py -k records_entry_fee -v`
Expected: PASS

- [ ] **Step 5: dry_run은 fee=0이라 entry_fee도 0인지 확인(기존 테스트로 커버되는지 점검)**

`test_enter_dry_run_opens_position_at_requested_price`(195-206행)는 이미 `order["fee"] == 0.0`을 검증하므로, `position["entry_fee"] == 0.0`도 자연히 성립한다. 이 assert를 그 테스트에 한 줄 추가:

`tests/test_order_executor.py:206` 다음 줄에 추가:
```python
    assert position["entry_fee"] == 0.0
```

- [ ] **Step 6: 전체 order_executor 테스트 회귀 확인**

Run: `python -m pytest tests/test_order_executor.py -v`
Expected: 전부 PASS

- [ ] **Step 7: 커밋**

```bash
git add trading/order_executor.py tests/test_order_executor.py
git commit -m "feat: enter()가 매수 체결 수수료를 entry_fee로 저장하고 주문-포지션을 연결"
```

---

### Task 4: 소급 재계산 마이그레이션 스크립트

**Files:**
- Create: `scripts/backfill_entry_fee.py`
- Test: `tests/test_backfill_entry_fee.py`

**Interfaces:**
- Consumes: Task 1의 `db.update_position_entry_fee`, `db.update_position_realized_pnl`, `db.upsert_daily_performance`(기존), `db.list_closed_positions`, `db.get_open_position`, `db.list_orders_for_strategy`, `db.list_live_strategies`, `db.get_daily_performance`.
- Produces: `run(apply: bool) -> None` — 커맨드라인에서 `python scripts/backfill_entry_fee.py [--apply]`로 실행.

- [ ] **Step 1: 정상 매칭 케이스(청산된 포지션 1건)의 실패하는 테스트 작성**

`tests/test_backfill_entry_fee.py` 새로 작성:

```python
import sqlite3

import trading.db as db
from tests.trading_db_fixtures import insert_live_strategy
from scripts import backfill_entry_fee as bf


def _fresh_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "trading.db")
    return db


def _seed_closed_trade(dbm, strategy_id, *, entry_fee_on_order=500.0, exit_time_kst_date="2026-08-10"):
    order_id = dbm.insert_order(
        strategy_id, None, "KRW-BTC", "bid", "market", None, None, 100_000_000.0,
    )
    dbm.update_order_filled(order_id, "uuid-1", 100_000_000.0, 0.01, entry_fee_on_order, 0.0, "done")

    position_id = dbm.insert_position(strategy_id, "KRW-BTC", 100_000_000.0, 0.01)
    # entry_fee 없이(구버전) 계산된 realized_pnl을 그대로 흉내낸다: exit-fee만 차감된 값
    old_realized_pnl = (101_000_000.0 * 0.01) - (100_000_000.0 * 0.01) - 505.0
    old_pct = old_realized_pnl / (100_000_000.0 * 0.01) * 100
    dbm.close_position_row(position_id, 101_000_000.0, 0.01, old_realized_pnl, old_pct, "signal")

    # exit_time을 UTC로 직접 덮어써 KST 날짜 변환이 결정적이도록 고정(2026-08-10 09:00 UTC = 18:00 KST)
    conn = sqlite3.connect(dbm.DB_PATH)
    conn.execute(
        "UPDATE positions SET exit_time = ? WHERE id = ?",
        (f"{exit_time_kst_date} 09:00:00", position_id),
    )
    conn.commit()
    conn.close()

    dbm.upsert_daily_performance(
        strategy_id, exit_time_kst_date, old_realized_pnl, old_pct, 1, 1, 0,
        100_000_000.0 * 0.01, 101_000_000.0 * 0.01 - 505.0,
    )
    return position_id, old_realized_pnl


def test_backfill_apply_corrects_realized_pnl_and_daily_performance(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm)
    position_id, old_pnl = _seed_closed_trade(dbm, strategy_id)

    bf.run(apply=True)

    position = dbm.get_position(position_id)
    assert position["entry_fee"] == 500.0
    assert position["realized_pnl"] == old_pnl - 500.0

    daily = dbm.get_daily_performance(strategy_id, "2026-08-10")
    assert daily["realized_pnl"] == old_pnl - 500.0


def test_backfill_dry_run_does_not_modify_db(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm)
    position_id, old_pnl = _seed_closed_trade(dbm, strategy_id)

    bf.run(apply=False)

    position = dbm.get_position(position_id)
    assert position["entry_fee"] == 0.0
    assert position["realized_pnl"] == old_pnl


def test_backfill_skips_strategy_when_order_and_position_counts_mismatch(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm)
    position_id, old_pnl = _seed_closed_trade(dbm, strategy_id)
    # 매수 주문을 하나 더 추가해 개수 불일치를 만든다(매칭 불확실 케이스)
    extra_order_id = dbm.insert_order(
        strategy_id, None, "KRW-BTC", "bid", "market", None, None, 100_000_000.0,
    )
    dbm.update_order_filled(extra_order_id, "uuid-2", 100_000_000.0, 0.01, 500.0, 0.0, "done")

    bf.run(apply=True)

    position = dbm.get_position(position_id)
    assert position["entry_fee"] == 0.0
    assert position["realized_pnl"] == old_pnl


def test_backfill_fills_entry_fee_only_for_open_position(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm)
    order_id = dbm.insert_order(
        strategy_id, None, "KRW-BTC", "bid", "market", None, None, 100_000_000.0,
    )
    dbm.update_order_filled(order_id, "uuid-1", 100_000_000.0, 0.01, 500.0, 0.0, "done")
    position_id = dbm.insert_position(strategy_id, "KRW-BTC", 100_000_000.0, 0.01)

    bf.run(apply=True)

    position = dbm.get_position(position_id)
    assert position["entry_fee"] == 500.0
    assert position["status"] == "open"
    assert position["realized_pnl"] is None
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_backfill_entry_fee.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'scripts.backfill_entry_fee'`)

- [ ] **Step 3: 스크립트 구현**

`scripts/backfill_entry_fee.py` 새로 작성:

```python
"""
scripts/backfill_entry_fee.py

1회성 마이그레이션: 매수 수수료(entry_fee) 미반영 버그 수정 이전에 생성된 포지션들을
소급 보정한다. 실행 전 trading.db를 자동 백업한다(--apply일 때만). 기본은 드라이런(무엇을
바꿀지만 출력)이고, --apply를 줘야 실제로 DB를 변경한다.

전략별로 side='bid' AND status='done' 주문을 created_at 순으로, 그 전략의 모든
포지션(열린 것 포함, 전략당 열린 포지션은 최대 1개)을 entry_time 순으로 정렬해 1:1로
매칭한다. 개수가 안 맞으면(주문 유실/재시도 등으로 매칭이 불확실하면) 그 전략은 건드리지
않고 건너뛴다.

청산된 포지션은 entry_fee를 채우고 realized_pnl/realized_pnl_pct를 재계산한다. 아직 열린
포지션은 entry_fee만 채운다(청산 전이라 손익 재계산 대상 아님) — 그래야 코드 수정 이전에
진입해 이후 청산되는 포지션도 정확한 entry_fee로 마감된다.

daily_performance는 영향받은 전략의 청산일별로 재계산한다(realized_pnl 합, win/loss
카운트). starting_balance/ending_balance는 실제 현금 흐름이라 entry_fee 보정과 무관하므로
그대로 둔다.

사용법:
    python scripts/backfill_entry_fee.py            # 드라이런
    python scripts/backfill_entry_fee.py --apply     # 실제 적용
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import trading.db as db


def _exit_kst_date(exit_time: str) -> str:
    utc_dt = datetime.strptime(exit_time, "%Y-%m-%d %H:%M:%S")
    return (utc_dt + timedelta(hours=9)).strftime("%Y-%m-%d")


def _match_positions_to_bid_orders(strategy_id: str) -> list[tuple[dict, dict]] | None:
    closed = sorted(db.list_closed_positions(strategy_id), key=lambda p: p["entry_time"])
    open_pos = db.get_open_position(strategy_id)
    positions = closed + ([open_pos] if open_pos else [])
    positions.sort(key=lambda p: p["entry_time"])

    bid_orders = sorted(
        (o for o in db.list_orders_for_strategy(strategy_id)
         if o["side"] == "bid" and o["status"] == "done"),
        key=lambda o: o["created_at"],
    )

    if len(bid_orders) != len(positions):
        return None
    return list(zip(positions, bid_orders))


def _recompute_daily_performance(strategy_id: str, apply: bool) -> None:
    closed = db.list_closed_positions(strategy_id)
    by_date: dict[str, list[dict]] = {}
    for p in closed:
        by_date.setdefault(_exit_kst_date(p["exit_time"]), []).append(p)

    for trading_date, positions in by_date.items():
        existing = db.get_daily_performance(strategy_id, trading_date)
        if existing is None:
            print(f"  경고: daily_performance 행 없음 strategy={strategy_id} date={trading_date} — 건너뜀")
            continue
        realized_pnl = sum(p["realized_pnl"] for p in positions)
        win_count = sum(1 for p in positions if p["realized_pnl"] >= 0)
        loss_count = len(positions) - win_count
        starting_balance = existing["starting_balance"]
        pct = (realized_pnl / starting_balance * 100.0) if starting_balance else 0.0
        print(
            f"  daily_performance {trading_date}: realized_pnl {existing['realized_pnl']:.2f} -> "
            f"{realized_pnl:.2f}"
        )
        if apply:
            db.upsert_daily_performance(
                strategy_id, trading_date, realized_pnl, pct,
                len(positions), win_count, loss_count, starting_balance, existing["ending_balance"],
            )


def run(apply: bool) -> None:
    if apply:
        backup_path = db.DB_PATH.with_name(
            f"{db.DB_PATH.name}.bak-{datetime.now().strftime('%Y%m%dT%H%M%S')}"
        )
        shutil.copy2(db.DB_PATH, backup_path)
        print(f"백업 완료: {backup_path}")

    strategies = db.list_live_strategies()
    matched_count = 0
    skipped_count = 0
    touched_strategy_ids: list[str] = []

    for strategy in strategies:
        pairs = _match_positions_to_bid_orders(strategy["id"])
        if pairs is None:
            skipped_count += 1
            print(f"건너뜀 (주문/포지션 개수 불일치): live_strategy_id={strategy['id']}")
            continue
        if not pairs:
            continue

        touched_strategy_ids.append(strategy["id"])
        for position, order in pairs:
            entry_fee = order["fee"] or 0.0
            if position["status"] == "closed":
                new_realized_pnl = position["realized_pnl"] - entry_fee
                denom = position["entry_price"] * position["entry_qty"]
                new_pct = (new_realized_pnl / denom * 100.0) if denom else 0.0
                print(
                    f"  포지션 {position['id']}: entry_fee={entry_fee:.2f}, "
                    f"realized_pnl {position['realized_pnl']:.2f} -> {new_realized_pnl:.2f}"
                )
                if apply:
                    db.update_position_entry_fee(position["id"], entry_fee)
                    db.update_position_realized_pnl(position["id"], new_realized_pnl, new_pct)
            else:
                print(f"  열린 포지션 {position['id']}: entry_fee만 {entry_fee:.2f}로 백필")
                if apply:
                    db.update_position_entry_fee(position["id"], entry_fee)
            matched_count += 1

    for strategy_id in touched_strategy_ids:
        print(f"daily_performance 재계산: live_strategy_id={strategy_id}")
        _recompute_daily_performance(strategy_id, apply)

    print(f"\n완료: 포지션 {matched_count}건 처리, 전략 {skipped_count}건 건너뜀.")
    if not apply:
        print("드라이런입니다. 실제로 적용하려면 --apply를 붙여 다시 실행하세요.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="실제로 DB를 변경한다(기본은 드라이런)")
    args = parser.parse_args()
    run(args.apply)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_backfill_entry_fee.py -v`
Expected: 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add scripts/backfill_entry_fee.py tests/test_backfill_entry_fee.py
git commit -m "feat: 매수 수수료 소급 재계산 1회성 마이그레이션 스크립트 추가"
```

---

### Task 5: 백엔드 — `daily_pnl_30d` 집계

**Files:**
- Modify: `backend/trading_analytics_service.py` (import 13-19행, `get_journal_summary` 101-152행, `_market_metrics` 196-241행, `get_market_journal` 244-301행)
- Test: `tests/test_trading_analytics_service.py`

**Interfaces:**
- Produces: `_zero_filled_last_30_days(pnl_by_date: dict[str, float], *, today: str | None = None) -> list[dict]` (각 항목 `{"date": "YYYY-MM-DD", "pnl": float}`, 오늘 포함 최근 30일, 오래된 날짜부터 정렬). `get_journal_summary()`/`get_market_journal()` 반환 dict에 `daily_pnl_30d` 키 추가(기존 `equity_curve`/`daily`는 그대로 유지).

- [ ] **Step 1: `_zero_filled_last_30_days` 헬퍼의 실패하는 단위 테스트 작성**

`tests/test_trading_analytics_service.py`에 추가:

```python
def test_zero_filled_last_30_days_fills_missing_dates_and_keeps_known_values():
    pnl_by_date = {"2026-08-10": 1500.0, "2026-08-05": -300.0}

    result = svc._zero_filled_last_30_days(pnl_by_date, today="2026-08-10")

    assert len(result) == 30
    assert result[-1] == {"date": "2026-08-10", "pnl": 1500.0}
    assert result[0]["date"] == "2026-07-12"  # 29일 전
    by_date = {d["date"]: d["pnl"] for d in result}
    assert by_date["2026-08-05"] == -300.0
    assert by_date["2026-08-01"] == 0.0  # 거래 없는 날은 0
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_trading_analytics_service.py -k zero_filled -v`
Expected: FAIL (`AttributeError: module 'backend.trading_analytics_service' has no attribute '_zero_filled_last_30_days'`)

- [ ] **Step 3: 헬퍼 구현 + import 추가**

`backend/trading_analytics_service.py` 상단 import(13-19행)를 교체:

```python
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

import trading.db as trading_db
import trading.risk_manager as risk_manager
from engine.cache import load_result
from engine.metrics import calculate_metrics

MIN_SAMPLE_SIZE = 10
```

`_mdd_pct` 함수(24-32행) 바로 다음에 새 함수 추가:

```python
def _zero_filled_last_30_days(
    pnl_by_date: dict[str, float], *, today: str | None = None,
) -> list[dict]:
    """pnl_by_date(YYYY-MM-DD 키)를 오늘(KST) 포함 최근 30일로 0-채움한 배열로 바꾼다.
    그래프가 청산 없는 날도 막대(0)로 표시할 수 있도록 daily_performance에 행이 없는
    날짜도 항목을 만든다. today를 넘기면 그 날짜를 기준으로 30일 창을 만든다(테스트용,
    기본은 실제 오늘)."""
    anchor = datetime.strptime(today or risk_manager.today_kst(), "%Y-%m-%d").date()
    return [
        {
            "date": (anchor - timedelta(days=offset)).isoformat(),
            "pnl": round(pnl_by_date.get((anchor - timedelta(days=offset)).isoformat(), 0.0), 4),
        }
        for offset in range(29, -1, -1)
    ]
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_trading_analytics_service.py -k zero_filled -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add backend/trading_analytics_service.py tests/test_trading_analytics_service.py
git commit -m "feat: 최근 30일 0-채움 일별손익 헬퍼(_zero_filled_last_30_days) 추가"
```

- [ ] **Step 6: `get_journal_summary`/`get_market_journal`에 `daily_pnl_30d` 배선하는 실패하는 테스트 작성**

`tests/test_trading_analytics_service.py`의 기존 `test_journal_summary_empty_when_no_approved_strategies`(20-30행)에 assert 한 줄 추가:

```python
def test_journal_summary_empty_when_no_approved_strategies(monkeypatch, tmp_path):
    db = _fresh(monkeypatch, tmp_path)
    insert_live_strategy(db, status="draft")

    summary = svc.get_journal_summary()

    assert summary["strategies"] == []
    assert summary["equity_curve"] == []
    assert summary["cumulative_pnl"] == 0.0
    assert summary["mdd_pct"] == 0.0
    assert summary["win_rate_pct"] == 0.0
    assert len(summary["daily_pnl_30d"]) == 30
    assert all(d["pnl"] == 0.0 for d in summary["daily_pnl_30d"])
```

파일 끝에 새 테스트 추가:

```python
def test_journal_summary_includes_daily_pnl_30d(monkeypatch, tmp_path):
    db = _fresh(monkeypatch, tmp_path)
    s1 = insert_live_strategy(db, status="draft")
    _approve(db, s1, 100_000.0)

    summary = svc.get_journal_summary()

    assert len(summary["daily_pnl_30d"]) == 30


def test_market_journal_includes_daily_pnl_30d(monkeypatch, tmp_path):
    db = _fresh(monkeypatch, tmp_path)
    s1 = insert_live_strategy(db, status="draft", market="KRW-DOGE")
    _approve(db, s1, 100_000.0)

    detail = svc.get_market_journal("KRW-DOGE")

    assert len(detail["daily_pnl_30d"]) == 30
```

- [ ] **Step 7: 테스트 실패 확인**

Run: `python -m pytest tests/test_trading_analytics_service.py -k daily_pnl_30d -v`
Expected: FAIL (`KeyError: 'daily_pnl_30d'`)

- [ ] **Step 8: `get_journal_summary` 배선**

`backend/trading_analytics_service.py`의 `get_journal_summary`(현재 101-152행)를 교체:

```python
def get_journal_summary() -> dict:
    strategies = [s for s in trading_db.list_live_strategies() if s["approved_at"] is not None]

    if not strategies:
        return {
            "cumulative_pnl": 0.0, "cumulative_pnl_pct": 0.0, "mdd_pct": 0.0,
            "win_rate_pct": 0.0, "equity_curve": [], "strategies": [],
            "daily_pnl_30d": _zero_filled_last_30_days({}),
        }

    strategy_cards = []
    pnl_by_date: dict[str, float] = {}
    total_baseline = 0.0
    weighted_pct_sum = 0.0
    all_closed: list[dict] = []

    for strategy in strategies:
        m = _strategy_metrics(strategy)
        total_baseline += m["baseline"]
        weighted_pct_sum += m["cumulative_pnl_pct"] * m["baseline"]
        all_closed.extend(m["closed_positions"])
        for row in m["daily_rows"]:
            pnl_by_date[row["trading_date"]] = (
                pnl_by_date.get(row["trading_date"], 0.0) + row["realized_pnl"]
            )
        strategy_cards.append({
            "id": strategy["id"],
            "market": strategy["market"],
            "timeframe": strategy["timeframe"],
            "status": strategy["status"],
            "cumulative_pnl": round(m["cumulative_pnl"], 4),
            "cumulative_pnl_pct": round(m["cumulative_pnl_pct"], 4),
            "trade_count": len(m["closed_positions"]),
        })

    equity_curve = []
    running = total_baseline
    for trading_date in sorted(pnl_by_date):
        running += pnl_by_date[trading_date]
        equity_curve.append({"trading_date": trading_date, "value": round(running, 4)})

    cumulative_pnl = sum(p["realized_pnl"] for p in all_closed)
    cumulative_pnl_pct = (weighted_pct_sum / total_baseline) if total_baseline else 0.0
    mdd_series = [total_baseline] + [e["value"] for e in equity_curve]

    return {
        "cumulative_pnl": round(cumulative_pnl, 4),
        "cumulative_pnl_pct": round(cumulative_pnl_pct, 4),
        "mdd_pct": round(_mdd_pct(mdd_series), 4),
        "win_rate_pct": round(_win_rate_pct(all_closed), 4),
        "equity_curve": equity_curve,
        "strategies": strategy_cards,
        "daily_pnl_30d": _zero_filled_last_30_days(pnl_by_date),
    }
```

- [ ] **Step 9: `_market_metrics`/`get_market_journal` 배선**

`_market_metrics`(196-241행)를 교체:

```python
def _market_metrics(strategies: list[dict]) -> dict:
    """여러 live_strategy 행(같은 market, 서로 다른 timeframe·세대 포함)을 하나로 합친
    지표. 코인 단위 매매일지(달력/그래프 포함)를 위해 _strategy_metrics를 전략별로 구해
    날짜별 realized_pnl을 합산한 뒤, baseline부터 날짜순으로 누적하며 그날의 수익률(%)까지
    함께 계산한다 — daily_performance에 이미 저장된 realized_pnl_pct는 전략 단위 기준이라
    코인 합산 관점에서는 재계산이 필요하다.
    cumulative_pnl_pct는 각 전략의 TWR 보정된 cumulative_pnl_pct를 baseline으로
    가중평균한다 — 자본 조정 이력이 없는 흔한 경우엔 sum(pnl)/sum(baseline)과
    대수적으로 동일하다(가중치가 전부 baseline이고 pct_i == pnl_i/baseline_i*100일 때)."""
    total_baseline = 0.0
    weighted_pct_sum = 0.0
    all_closed: list[dict] = []
    pnl_by_date: dict[str, float] = {}
    for strategy in strategies:
        m = _strategy_metrics(strategy)
        total_baseline += m["baseline"]
        weighted_pct_sum += m["cumulative_pnl_pct"] * m["baseline"]
        all_closed.extend(m["closed_positions"])
        for row in m["daily_rows"]:
            pnl_by_date[row["trading_date"]] = pnl_by_date.get(row["trading_date"], 0.0) + row["realized_pnl"]

    daily: list[dict] = []
    running = total_baseline
    for trading_date in sorted(pnl_by_date):
        day_pnl = pnl_by_date[trading_date]
        day_pct = (day_pnl / running * 100.0) if running else 0.0
        running += day_pnl
        daily.append({
            "trading_date": trading_date,
            "pnl": round(day_pnl, 4),
            "pnl_pct": round(day_pct, 4),
            "cumulative": round(running, 4),
        })

    cumulative_pnl = sum(p["realized_pnl"] for p in all_closed)
    cumulative_pnl_pct = (weighted_pct_sum / total_baseline) if total_baseline else 0.0
    mdd_series = [total_baseline] + [d["cumulative"] for d in daily]

    return {
        "closed_positions": all_closed,
        "cumulative_pnl": cumulative_pnl,
        "cumulative_pnl_pct": cumulative_pnl_pct,
        "mdd_pct": _mdd_pct(mdd_series),
        "win_rate_pct": _win_rate_pct(all_closed),
        "daily": daily,
        "daily_pnl_30d": _zero_filled_last_30_days(pnl_by_date),
    }
```

`get_market_journal`(244-301행)의 반환 dict(287-301행)에 `daily_pnl_30d` 한 줄 추가:

```python
    return {
        "market": market,
        "timeframes": sorted({s["timeframe"] for s in strategies}),
        "statuses": sorted({s["status"] for s in strategies}),
        "cumulative_pnl": round(m["cumulative_pnl"], 4),
        "cumulative_pnl_pct": round(m["cumulative_pnl_pct"], 4),
        "mdd_pct": round(m["mdd_pct"], 4),
        "win_rate_pct": round(m["win_rate_pct"], 4),
        "avg_slippage_pct": avg_slippage_pct,
        "max_slippage_pct": max_slippage_pct,
        "trade_count": len(m["closed_positions"]),
        "backtest_comparison": _backtest_comparison(latest_strategy, m),
        "trade_log": trade_log,
        "daily": m["daily"],
        "daily_pnl_30d": m["daily_pnl_30d"],
    }
```

- [ ] **Step 10: 테스트 통과 확인**

Run: `python -m pytest tests/test_trading_analytics_service.py -v`
Expected: 전부 PASS

- [ ] **Step 11: journal 엔드포인트 테스트 회귀 확인**

Run: `python -m pytest tests/test_journal_endpoints.py -v`
Expected: 전부 PASS (기존 assert들은 `daily_pnl_30d` 존재 여부를 체크하지 않으므로 그대로 통과해야 함)

- [ ] **Step 12: 커밋**

```bash
git add backend/trading_analytics_service.py tests/test_trading_analytics_service.py
git commit -m "feat: journal summary/market API에 daily_pnl_30d(최근 30일 일별 실현손익) 추가"
```

---

### Task 6: 프론트엔드 — 타입 + `DailyPnlBarChart` 컴포넌트

**Files:**
- Modify: `frontend/lib/types/journal.ts`
- Create: `frontend/components/DailyPnlBarChart.tsx`

**Interfaces:**
- Consumes: Task 5의 API 응답 `daily_pnl_30d: [{date, pnl}]`.
- Produces: `JournalDailyPnlPoint` 타입, `<DailyPnlBarChart data={...} heightPx={...} />` 컴포넌트(막대그래프, 콤마 포맷 y축, 상시 값 라벨, 툴팁 없음, 색상은 `return-rate-color.ts`와 동일 관례).

- [ ] **Step 1: 타입 추가**

`frontend/lib/types/journal.ts`에 `JournalEquityPoint` 인터페이스(3-6행) 바로 다음에 추가:

```ts
export interface JournalDailyPnlPoint {
  date: string;
  pnl: number;
}
```

`JournalSummary`(18-25행)에 필드 추가:

```ts
export interface JournalSummary {
  cumulative_pnl: number;
  cumulative_pnl_pct: number;
  mdd_pct: number;
  win_rate_pct: number;
  equity_curve: JournalEquityPoint[];
  daily_pnl_30d: JournalDailyPnlPoint[];
  strategies: JournalStrategyCard[];
}
```

`JournalMarketDetail`(60-74행)에 필드 추가:

```ts
export interface JournalMarketDetail {
  market: string;
  timeframes: string[];
  statuses: LiveStrategyStatus[];
  cumulative_pnl: number;
  cumulative_pnl_pct: number;
  mdd_pct: number;
  win_rate_pct: number;
  avg_slippage_pct: number | null;
  max_slippage_pct: number | null;
  trade_count: number;
  backtest_comparison: JournalBacktestComparison | null;
  trade_log: JournalTradeLogEntry[];
  daily: JournalDailyCell[];
  daily_pnl_30d: JournalDailyPnlPoint[];
}
```

- [ ] **Step 2: 타입체크로 아직 컴포넌트가 없어 에러 없는지만 확인(변경분 자체는 타입 추가라 즉시 안전)**

Run: `cd frontend && npx tsc --noEmit`
Expected: PASS (기존 코드는 아직 새 필드를 참조하지 않으므로 에러 없음)

- [ ] **Step 3: 커밋**

```bash
git add frontend/lib/types/journal.ts
git commit -m "feat: JournalSummary/JournalMarketDetail에 daily_pnl_30d 타입 추가"
```

- [ ] **Step 4: `DailyPnlBarChart` 컴포넌트 작성**

`frontend/components/DailyPnlBarChart.tsx` 새로 작성:

```tsx
'use client';

import { Bar, BarChart, Cell, LabelList, ResponsiveContainer, XAxis, YAxis } from 'recharts';
import type { JournalDailyPnlPoint } from '@/lib/types/journal';

// 이 앱의 손익 색상 관례(frontend/lib/return-rate-color.ts)와 동일: 양수=빨강, 음수=파랑
// (한국 증시 관례, 서구식 초록/빨강 아님).
function barColorClass(pnl: number): string {
  if (pnl > 0) return 'fill-red-600 dark:fill-red-400';
  if (pnl < 0) return 'fill-blue-600 dark:fill-blue-400';
  return 'fill-muted-foreground/20';
}

function fmtTick(date: string): string {
  return date.slice(5).replace('-', '/');
}

function fmtLabel(value: number): string {
  if (value === 0) return '';
  return Math.round(value).toLocaleString();
}

export default function DailyPnlBarChart({
  data, heightPx,
}: {
  data: JournalDailyPnlPoint[];
  heightPx: number;
}) {
  return (
    <ResponsiveContainer width="100%" height={heightPx}>
      <BarChart data={data} margin={{ top: 16 }}>
        <XAxis dataKey="date" tick={{ fontSize: 11 }} tickFormatter={fmtTick} interval={4} />
        <YAxis tick={{ fontSize: 11 }} tickFormatter={(v: number) => v.toLocaleString()} />
        <Bar dataKey="pnl">
          {data.map((entry) => (
            <Cell key={entry.date} className={barColorClass(entry.pnl)} />
          ))}
          <LabelList
            dataKey="pnl"
            position="top"
            className="fill-foreground"
            fontSize={10}
            formatter={fmtLabel}
          />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
```

- [ ] **Step 5: 타입체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: PASS

- [ ] **Step 6: 커밋**

```bash
git add frontend/components/DailyPnlBarChart.tsx
git commit -m "feat: 일별 실현손익 막대그래프 컴포넌트(DailyPnlBarChart) 추가"
```

---

### Task 7: 프론트엔드 — `JournalPage.tsx`를 막대그래프로 교체

**Files:**
- Modify: `frontend/components/JournalPage.tsx`

**Interfaces:**
- Consumes: Task 6의 `<DailyPnlBarChart data={...} heightPx={...} />`, `summary.daily_pnl_30d`, `detail.daily_pnl_30d`.

- [ ] **Step 1: import 교체**

`frontend/components/JournalPage.tsx` 1-14행을 교체:

```tsx
'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { ApiError } from '@/lib/api/client';
import { getJournalSummary, getMarketJournal } from '@/lib/api/journal';
import type { JournalMarketDetail, JournalSummary } from '@/lib/types/journal';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { formatTimeframe } from '@/lib/format';
import DailyPnlBarChart from '@/components/DailyPnlBarChart';
import JournalCalendar from '@/components/JournalCalendar';
import JournalMarketDetailView from '@/components/JournalMarketDetail';
```

- [ ] **Step 2: 계좌 전체 그래프 블록 교체**

현재 127-147행(`{summary.equity_curve.length === 0 ? (...) : (<ResponsiveContainer>...</ResponsiveContainer>)}`)을 교체:

```tsx
          {summary.daily_pnl_30d.every((d) => d.pnl === 0) ? (
            <p className="text-sm text-muted-foreground">
              최근 30일간 청산된 거래가 없어 그래프를 표시할 수 없습니다.
            </p>
          ) : (
            <DailyPnlBarChart data={summary.daily_pnl_30d} heightPx={280} />
          )}
```

- [ ] **Step 3: 코인별 그래프 블록 교체**

현재 174-194행(`{detail.daily.length === 0 ? (...) : (<ResponsiveContainer>...</ResponsiveContainer>)}`)을 교체:

```tsx
              {detail.daily_pnl_30d.every((d) => d.pnl === 0) ? (
                <p className="text-sm text-muted-foreground">
                  최근 30일간 청산된 거래가 없어 그래프를 표시할 수 없습니다.
                </p>
              ) : (
                <DailyPnlBarChart data={detail.daily_pnl_30d} heightPx={240} />
              )}
```

- [ ] **Step 4: 타입체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add frontend/components/JournalPage.tsx
git commit -m "feat: 매매일지 그래프를 총자산 누적선 대신 최근 30일 일별손익 막대그래프로 교체"
```

---

### Task 8: 브라우저 수동 검증

**Files:** 없음 (검증만).

**Interfaces:** 없음.

- [ ] **Step 1: 전체 백엔드 테스트 회귀 확인**

Run: `python -m pytest tests/ -v`
Expected: 전부 PASS

- [ ] **Step 2: 마이그레이션 스크립트 로컬 드라이런**

`data/trading.db`가 로컬에 있다면(있을 때만):

Run: `python scripts/backfill_entry_fee.py`
Expected: 처리/건너뜀 건수가 출력됨(드라이런이라 DB는 안 바뀜). 출력 내용을 확인해 이상한 스킵이 많지 않은지 눈으로 검토.

로컬에 `data/trading.db`가 없으면(라이브 트레이딩을 로컬에서 돌린 적 없으면) 이 단계는 건너뛴다 — 실제 적용은 AWS 서버에서 사용자가 별도로 진행한다.

- [ ] **Step 3: 개발 서버 기동**

`run` 스킬을 사용하거나, 수동으로:
- 백엔드: `python -m uvicorn backend.main:app --reload --port 8000`
- 프론트엔드: `cd frontend && npm run dev`

dev 서버가 떠 있는 동안 `npm run build`를 실행하지 않는다(라이브 `.next` 손상 이슈).

- [ ] **Step 4: 매매일지 탭 확인(webapp-testing/Playwright로 브라우저 조작)**

확인 항목:
- 계좌 전체 요약, 코인별 그래프 모두 막대그래프로 보이는지.
- y축/막대 라벨에 천단위 콤마가 있는지.
- 양수 막대가 빨강, 음수 막대가 파랑, 0인 날은 거의 안 보이는 회색인지.
- 막대를 클릭/탭해도 툴팁/팝업이 뜨지 않는지.
- 모바일 뷰포트(예: 390x844)로 리사이즈해 레이아웃이 깨지지 않는지, 탭 시에도 팝업이 없는지.
- 청산 거래가 없는 코인을 선택했을 때 "최근 30일간 청산된 거래가 없어..." 문구가 뜨는지.
- 카드의 "누적손익" 숫자가 그래프와 별개로 정상 표시되는지(수수료 반영된 값).
- 코인별 매매일지의 달력 뷰(`JournalCalendar`)가 이번 변경과 무관하게 그대로 정상 동작하는지(회귀 확인 — 이번 스코프에서 손대지 않은 컴포넌트).

- [ ] **Step 5: 문제 없으면 최종 커밋 없이 종료(모든 변경은 이미 태스크별로 커밋됨)**
