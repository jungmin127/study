# 이월된 운영 가시성/데이터 무결성 항목 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **워크트리를 만들지 말고 main 브랜치에서 직접 작업한다** (사용자 지시, [[upbit-v1-worktree-workflow-changed]]).

**Goal:** 스펙 `docs/superpowers/specs_v1/2026-08-10-live-trading-observability-integrity-design.md`
5건(로깅 전무, signals UNIQUE 제약 없음, skip_reason 도메인 협소, circuit_breaker_state
타임스탬프 KST/UTC 불일치, db._connect() 매호출 스키마 재실행)을 수정해 소액 실전 테스트
전 마지막 관측성/무결성 항목을 처리한다.

**Architecture:** 5건 모두 서로 다른 파일 영역이라 순서 의존성이 거의 없다(단, Task4는
Task3의 신규 함수를 소비하므로 그 뒤에 온다). `trading/db.py`를 건드리는 두 태스크
(Task1, Task2)는 같은 파일이라 순서대로 처리한다. 각 태스크는 스펙의 결정 하나에 대응하고
독립적으로 커밋 가능하다.

**Tech Stack:** Python, `pytest`(+`pytest-asyncio`, `asyncio_mode = auto`), `sqlite3`,
표준 `logging` 모듈. 새 의존성 없음.

## Global Constraints

- 로깅은 `trading/daemon.py`/`trading/order_executor.py`가 이미 쓰는 컨벤션을 그대로
  따른다 — `logging.getLogger(__name__)`, `%s` 지연 포매팅, 한국어 메시지.
- 아직 실거래 데이터가 없는 개발 단계이므로 `signals` 스키마 변경(UNIQUE 제약)은
  마이그레이션 없이 `CREATE TABLE IF NOT EXISTS`에 바로 반영한다.
- `db.insert_signal()`의 시그니처(파라미터/반환 타입)는 변경하지 않는다 — 내부 구현만
  idempotent하게 바꾼다.
- 커밋은 태스크 단위로 작게, 테스트가 통과한 뒤에만 한다.
- `trading/daemon.py`/`trading/order_executor.py`/`trading/position_manager.py`/
  `trading/reconciler.py`는 이 플랜에서 코드 변경 없음(스펙에서 이미 grep으로 확인).

---

## File Structure

- **Modify:** `trading/db.py` — `_connect()`를 경로별 1회 초기화로 변경(Task1),
  `signals`에 UNIQUE 제약 추가 + `insert_signal()`을 `INSERT OR IGNORE`로 변경(Task2).
- **Modify:** `tests/test_trading_db.py` — Task1/Task2 대응 테스트 추가.
- **Modify:** `engine/condition_tree.py` — `find_indicators_with_missing_values()` 신규
  (Task3).
- **Modify:** `tests/test_condition_tree.py` — Task3 대응 테스트 추가.
- **Modify:** `trading/signal_engine.py` — `skip_reason`에 미확보 지표명 포함(Task4).
- **Modify:** `tests/test_signal_engine.py` — Task4 대응 테스트 추가.
- **Modify:** `trading/risk_manager.py` — `tripped_at`을 UTC로 변경(Task5).
- **Modify:** `tests/test_risk_manager.py` — Task5 대응 테스트 추가.
- **Modify:** `trading/upbit_ws.py` — 재연결 로깅 추가(Task6).
- **Modify:** `tests/test_upbit_ws.py` — Task6 대응 테스트 추가.
- **Modify:** `trading/upbit_client.py` — 429 재시도/소진 로깅 추가(Task7).
- **Modify:** `tests/test_upbit_client.py` — Task7 대응 테스트 추가.

---

### Task 1: `trading/db.py` — `_connect()`가 `DB_PATH`당 스키마를 1회만 실행하게 한다

**Files:**
- Modify: `trading/db.py`
- Modify: `tests/test_trading_db.py`

**Interfaces:**
- Produces: `trading.db._initialized_paths: set[Path]`(신규 모듈 전역, 다른 태스크가
  직접 쓰지는 않지만 테스트가 검사한다). `_connect()`의 외부 시그니처/반환 타입은
  불변.

**주의:** 단순 전역 `bool` 플래그로 캐싱하면 `tests/test_trading_db.py`를 비롯해
`_fresh_db(monkeypatch, tmp_path)` 패턴(매 테스트마다 새 `DB_PATH`)을 쓰는 거의 모든
테스트 파일이 두 번째 테스트부터 깨진다 — 반드시 `DB_PATH`별로 추적해야 한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_trading_db.py`의 `test_connect_is_idempotent` 함수 다음에 추가:

```python
def test_connect_does_not_reexecute_schema_script_on_second_call(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    monkeypatch.setattr(
        db, "_SCHEMA",
        "CREATE TABLE strict_once (id INTEGER);",  # IF NOT EXISTS 없음 — 두 번째 실행되면 에러
    )

    db._connect().close()  # 첫 호출: 정상 생성
    db._connect().close()  # 캐싱이 안 되면 sqlite3.OperationalError: table strict_once already exists


def test_connect_initializes_schema_separately_for_each_db_path(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    db._connect().close()  # 첫 경로 초기화

    other_path = tmp_path / "other" / "trading.db"
    monkeypatch.setattr(db, "DB_PATH", other_path)

    conn = db._connect()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        table_names = {row[0] for row in rows}
    finally:
        conn.close()

    assert table_names == set(db.TABLE_NAMES)
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_trading_db.py -v -k does_not_reexecute_schema`
Expected: FAIL — `sqlite3.OperationalError: table strict_once already exists`
(`test_connect_initializes_schema_separately_for_each_db_path`는 지금 코드에서도
이미 통과한다 — 매 호출이 항상 스키마를 재실행하므로 새 경로도 항상 초기화되기
때문이다. 이 테스트는 회귀 방지용으로 같이 추가한다.)

- [ ] **Step 3: `trading/db.py` 수정**

`_connect()` 함수 전체를:
```python
def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    return conn
```
에서:
```python
_initialized_paths: set[Path] = set()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    if DB_PATH not in _initialized_paths:
        conn.executescript(_SCHEMA)
        _initialized_paths.add(DB_PATH)
    return conn
```
로 교체한다(`_initialized_paths` 모듈 전역 변수 선언 포함, `_connect()` 함수 정의
바로 위에 둔다).

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_trading_db.py -v`
Expected: 전부 PASS(회귀 없음)

- [ ] **Step 5: 전체 회귀 확인(이 태스크가 가장 광범위한 회귀 위험을 가짐)**

Run: `python -m pytest -q`
Expected: 전부 PASS — 특히 `_fresh_db`/`insert_live_strategy` 패턴을 쓰는 모든 테스트
파일(`tests/test_trading_db.py`, `tests/test_signal_engine.py`,
`tests/test_risk_manager.py`, `tests/test_position_manager.py`,
`tests/test_order_executor.py`, `tests/test_reconciler.py` 등)이 회귀 없이 통과해야
한다.

- [ ] **Step 6: 커밋**

```bash
git add trading/db.py tests/test_trading_db.py
git commit -m "perf: db._connect()가 DB_PATH당 스키마를 1회만 실행하도록 변경"
```

---

### Task 2: `trading/db.py` — `signals` UNIQUE 제약 + `insert_signal()` idempotent 처리

**Files:**
- Modify: `trading/db.py`
- Modify: `tests/test_trading_db.py`

**Interfaces:**
- Produces: `trading.db.insert_signal(live_strategy_id, signal_type, candle_time,
  indicator_snapshot_json, skip_reason=None) -> str`(기존 시그니처/반환 타입 불변 —
  충돌 시 새 uuid 대신 기존 행의 id를 반환하는 것만 내부 동작 변경).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_trading_db.py`의 `test_insert_signal_stores_skip_reason` 함수 다음에 추가:

```python
def test_insert_signal_is_idempotent_for_same_strategy_type_and_candle_time(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)

    first_id = db.insert_signal(
        strategy_id, "buy", "2026-08-07T10:00:00+00:00", '{"a": 1}',
    )
    second_id = db.insert_signal(
        strategy_id, "buy", "2026-08-07T10:00:00+00:00", '{"a": 2}',  # 다른 snapshot이어도 무시됨
    )

    assert second_id == first_id
    conn = db._connect()
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE live_strategy_id = ? AND signal_type = ? AND candle_time = ?",
            (strategy_id, "buy", "2026-08-07T10:00:00+00:00"),
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 1


def test_insert_signal_allows_different_signal_types_for_same_candle(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)

    buy_id = db.insert_signal(strategy_id, "buy", "2026-08-07T10:00:00+00:00", "{}")
    sell_id = db.insert_signal(strategy_id, "sell", "2026-08-07T10:00:00+00:00", "{}")

    assert buy_id != sell_id
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_trading_db.py -v -k insert_signal_is_idempotent`
Expected: FAIL — `sqlite3.IntegrityError` 또는(UNIQUE 제약이 아직 없으므로) `assert
second_id == first_id`에서 실패(서로 다른 uuid가 생성됨)

- [ ] **Step 3: `trading/db.py` 수정**

`_SCHEMA` 안의 `signals` 테이블 정의를:
```sql
CREATE TABLE IF NOT EXISTS signals (
    id                      TEXT PRIMARY KEY,
    live_strategy_id        TEXT NOT NULL REFERENCES live_strategies(id),
    signal_type             TEXT NOT NULL,
    candle_time             TEXT NOT NULL,
    indicator_snapshot_json TEXT,
    resulting_order_id      TEXT REFERENCES orders(id),
    skip_reason             TEXT,
    triggered_at            TEXT NOT NULL DEFAULT (datetime('now'))
);
```
에서:
```sql
CREATE TABLE IF NOT EXISTS signals (
    id                      TEXT PRIMARY KEY,
    live_strategy_id        TEXT NOT NULL REFERENCES live_strategies(id),
    signal_type             TEXT NOT NULL,
    candle_time             TEXT NOT NULL,
    indicator_snapshot_json TEXT,
    resulting_order_id      TEXT REFERENCES orders(id),
    skip_reason             TEXT,
    triggered_at            TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(live_strategy_id, signal_type, candle_time)
);
```
로 교체한다.

`insert_signal()` 함수 전체를:
```python
def insert_signal(
    live_strategy_id: str, signal_type: str, candle_time: str,
    indicator_snapshot_json: str, skip_reason: str | None = None,
) -> str:
    signal_id = str(uuid.uuid4())
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO signals "
            "(id, live_strategy_id, signal_type, candle_time, indicator_snapshot_json, skip_reason) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (signal_id, live_strategy_id, signal_type, candle_time, indicator_snapshot_json, skip_reason),
        )
        conn.commit()
    finally:
        conn.close()
    return signal_id
```
에서:
```python
def insert_signal(
    live_strategy_id: str, signal_type: str, candle_time: str,
    indicator_snapshot_json: str, skip_reason: str | None = None,
) -> str:
    """signals에 (live_strategy_id, signal_type, candle_time) UNIQUE 제약이 있어, 같은
    조합으로 재호출되면(daemon이 last_processed_candle_time 갱신 전에 죽었다가 재시작해
    같은 candle을 재평가하는 경우) 새 행 대신 기존 행의 id를 그대로 반환한다 — 예외
    없이 evaluate_signals()가 계속 진행되게 한다(운영 가시성/무결성 보완)."""
    signal_id = str(uuid.uuid4())
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "INSERT OR IGNORE INTO signals "
            "(id, live_strategy_id, signal_type, candle_time, indicator_snapshot_json, skip_reason) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (signal_id, live_strategy_id, signal_type, candle_time, indicator_snapshot_json, skip_reason),
        )
        if cursor.rowcount == 0:
            existing = conn.execute(
                "SELECT id FROM signals WHERE live_strategy_id = ? AND signal_type = ? AND candle_time = ?",
                (live_strategy_id, signal_type, candle_time),
            ).fetchone()
            signal_id = existing["id"]
        conn.commit()
    finally:
        conn.close()
    return signal_id
```
로 교체한다.

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_trading_db.py -v`
Expected: 전부 PASS(기존 `test_insert_signal_creates_row_with_null_resulting_order_id`/
`test_insert_signal_stores_skip_reason` 포함 회귀 없음)

- [ ] **Step 5: 커밋**

```bash
git add trading/db.py tests/test_trading_db.py
git commit -m "fix: signals에 UNIQUE 제약 추가 + insert_signal이 크래시 재시작 시 idempotent하게 동작하도록 수정"
```

---

### Task 3: `engine/condition_tree.py` — `find_indicators_with_missing_values()` 신규

**Files:**
- Modify: `engine/condition_tree.py`
- Modify: `tests/test_condition_tree.py`

**Interfaces:**
- Produces: `engine.condition_tree.find_indicators_with_missing_values(group: dict,
  values: dict[str, float | None]) -> list[str]`(신규, Task4가 소비한다).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_condition_tree.py`의 `test_find_unknown_indicators_allows_holding_period_bars`
함수 다음에 추가:

```python
def test_find_indicators_with_missing_values_returns_leaves_with_none_or_nan():
    tree = {
        "type": "AND",
        "conditions": [
            {"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30},
            {"indicator": "FUNDING_RATE", "params": {}, "operator": "<", "threshold": 0},
        ],
    }
    values = {
        indicator_key("RSI", {"period": 14}): 25.0,
        indicator_key("FUNDING_RATE", {}): None,
    }
    assert find_indicators_with_missing_values(tree, values) == ["FUNDING_RATE"]


def test_find_indicators_with_missing_values_treats_nan_as_missing():
    tree = {
        "type": "AND",
        "conditions": [{"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30}],
    }
    values = {indicator_key("RSI", {"period": 14}): float("nan")}
    assert find_indicators_with_missing_values(tree, values) == ["RSI"]


def test_find_indicators_with_missing_values_excludes_position_relative_indicators():
    tree = {
        "type": "OR",
        "conditions": [
            {"indicator": "STOP_LOSS_PCT", "params": {}, "operator": "<=", "threshold": -5},
            {"indicator": "HOLDING_PERIOD_BARS", "params": {}, "operator": ">=", "threshold": 5},
        ],
    }
    assert find_indicators_with_missing_values(tree, {}) == []


def test_find_indicators_with_missing_values_returns_empty_when_all_present():
    tree = {
        "type": "AND",
        "conditions": [{"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30}],
    }
    values = {indicator_key("RSI", {"period": 14}): 25.0}
    assert find_indicators_with_missing_values(tree, values) == []
```

`tests/test_condition_tree.py`의 import 블록(파일 최상단, `from engine.condition_tree
import (` 로 시작)에 `find_indicators_with_missing_values`를 추가한다(다른 이름들과
함께 알파벳 순 유지가 기존 스타일이면 그 자리에, 아니면 목록 끝에 추가).

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_condition_tree.py -v -k find_indicators_with_missing_values`
Expected: FAIL — `ImportError: cannot import name 'find_indicators_with_missing_values'`

- [ ] **Step 3: `engine/condition_tree.py` 수정**

`find_unknown_indicators()` 함수(아래 내용) 바로 다음, `is_empty()` 함수 바로 위에
새 함수를 추가한다:

```python
def find_unknown_indicators(group: dict) -> list[str]:
    """INDICATOR_FACTORY와 POSITION_RELATIVE_INDICATORS 어디에도 없는 지표 키를 찾아 반환(중복 제거, 정렬)."""
    unknown = {
        b["indicator"]
        for b in collect_blocks(group)
        if b["indicator"] not in INDICATOR_FACTORY and b["indicator"] not in POSITION_RELATIVE_INDICATORS
    }
    return sorted(unknown)


def find_indicators_with_missing_values(group: dict, values: dict[str, float | None]) -> list[str]:
    """조건 트리의 리프 지표 중 values에 값이 없거나 NaN인 지표명을 수집한다(중복 제거,
    정렬). eval_group_values()가 왜 None(unknown)을 반환했는지 운영자가 signals.skip_reason
    만 보고 파악할 수 있게 한다(운영 가시성 보완). HOLDING_PERIOD_BARS/
    POSITION_RELATIVE_INDICATORS는 values를 안 쓰므로(eval_group_values()도 이들은
    position_return_pct/position_holding_bars로 별도 평가) 대상에서 제외한다."""
    missing = set()
    for block in collect_blocks(group):
        name = block["indicator"]
        if name in POSITION_RELATIVE_INDICATORS:
            continue
        key = indicator_key(name, block.get("params", {}))
        value = values.get(key)
        if value is None or value != value:  # None 또는 NaN
            missing.add(name)
    return sorted(missing)


def is_empty(group: dict) -> bool:
    return len(group.get("conditions", [])) == 0
```

(즉, `find_unknown_indicators`와 `is_empty` 사이에 새 함수를 끼워 넣는다.)

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_condition_tree.py -v`
Expected: 전부 PASS(회귀 없음)

- [ ] **Step 5: 커밋**

```bash
git add engine/condition_tree.py tests/test_condition_tree.py
git commit -m "feat: find_indicators_with_missing_values 추가(조건 트리의 미확보 지표 탐지)"
```

---

### Task 4: `trading/signal_engine.py` — `skip_reason`에 미확보 지표명 포함

**Files:**
- Modify: `trading/signal_engine.py`
- Modify: `tests/test_signal_engine.py`

**Interfaces:**
- Consumes: `engine.condition_tree.find_indicators_with_missing_values(group, values) ->
  list[str]`(Task3).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_signal_engine.py`의 `test_evaluate_signals_pauses_strategy_when_condition_unknown`
함수 다음에 추가:

```python
def test_evaluate_signals_records_missing_indicator_name_in_skip_reason(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    df = make_oscillating_df()
    buy_json = json.dumps({"type": "AND", "conditions": [
        {"indicator": "FUNDING_RATE", "params": {}, "operator": "<", "threshold": -0.01},
    ]})
    _, sell_json = _strategy_conditions()
    strategy_id = insert_live_strategy(
        dbm, status="running", buy_conditions_json=buy_json, sell_conditions_json=sell_json,
    )
    monkeypatch.setattr(signal_engine, "get_candles", lambda market, timeframe, start, end: df)
    monkeypatch.setattr(signal_engine, "fetch_live_funding_rate_value", lambda market, now=None: None)

    result = signal_engine.evaluate_signals(strategy_id, now=datetime.now(timezone.utc))

    conn = dbm._connect()
    try:
        row = conn.execute(
            "SELECT skip_reason FROM signals WHERE id = ?", (result["buy_signal_id"],)
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "unknown:FUNDING_RATE"
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_signal_engine.py -v -k records_missing_indicator_name`
Expected: FAIL — `assert 'unknown' == 'unknown:FUNDING_RATE'`

- [ ] **Step 3: `trading/signal_engine.py` 수정**

import 블록을:
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
에서:
```python
from engine.condition_tree import (
    POSITION_RELATIVE_INDICATORS,
    apply_operator,
    collect_blocks,
    eval_group_values,
    find_indicators_with_missing_values,
    indicator_key,
    max_required_period,
    required_aux_markets,
)
```
로 교체한다.

`evaluate_signals()` 안의:
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
을:
```python
    buy_missing = find_indicators_with_missing_values(buy_conditions, values)
    sell_missing = find_indicators_with_missing_values(sell_conditions, values)

    buy_signal_id = db.insert_signal(
        live_strategy_id, "buy", candle_time_str, snapshot_json,
        skip_reason=f"unknown:{','.join(buy_missing)}" if buy_result is None else None,
    )
    sell_signal_id = db.insert_signal(
        live_strategy_id, "sell", candle_time_str, snapshot_json,
        skip_reason=f"unknown:{','.join(sell_missing)}" if sell_result is None else None,
    )
```
로 교체한다.

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_signal_engine.py -v`
Expected: 전부 PASS(회귀 없음 — `test_evaluate_signals_pauses_strategy_when_condition_unknown`은
`skip_reason` 값을 검사하지 않고 `buy_signal is None`/`paused is True`만 검사하므로
영향 없음)

- [ ] **Step 5: 커밋**

```bash
git add trading/signal_engine.py tests/test_signal_engine.py
git commit -m "feat: skip_reason에 미확보 지표명을 포함하도록 수정(unknown:FUNDING_RATE 형태)"
```

---

### Task 5: `trading/risk_manager.py` — `tripped_at`을 UTC로 통일

**Files:**
- Modify: `trading/risk_manager.py`
- Modify: `tests/test_risk_manager.py`

**Interfaces:** 없음(내부 타임스탬프 생성 방식만 변경, 함수 시그니처 불변).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_risk_manager.py`의 `test_check_circuit_breaker_trips_on_consecutive_loss_limit`
함수 다음에 추가:

```python
def test_check_circuit_breaker_records_tripped_at_in_utc(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm, status="running")
    risk_config = {"daily_loss_limit_pct": -5.0}

    record_trade_result(strategy_id, realized_pnl=-1000.0, capital_after=94_000.0)

    assert check_circuit_breaker(strategy_id, risk_config) is True
    tripped_at = dbm.get_circuit_breaker_state(strategy_id)["tripped_at"]
    assert tripped_at.endswith("+00:00")
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_risk_manager.py -v -k tripped_at_in_utc`
Expected: FAIL — `tripped_at`이 `+09:00`으로 끝남(KST)

- [ ] **Step 3: `trading/risk_manager.py` 수정**

`check_circuit_breaker()` 안의:
```python
    db.upsert_circuit_breaker_state(
        live_strategy_id, trading_date, consecutive_losses, 1, tripped_reason,
        datetime.now(_KST).isoformat(),
    )
```
을:
```python
    db.upsert_circuit_breaker_state(
        live_strategy_id, trading_date, consecutive_losses, 1, tripped_reason,
        datetime.now(timezone.utc).isoformat(),
    )
```
로 교체한다(`trading_date`/`today_kst()`는 영업일 경계 목적이라 그대로 KST 유지 —
이 파일의 다른 부분은 손대지 않는다).

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_risk_manager.py -v`
Expected: 전부 PASS(회귀 없음 — 기존 테스트는 `tripped_at is not None`만 검사하거나
테스트가 직접 값을 넣는 경우뿐이라 포맷 변경과 무관)

- [ ] **Step 5: 커밋**

```bash
git add trading/risk_manager.py tests/test_risk_manager.py
git commit -m "fix: circuit_breaker_state.tripped_at을 UTC로 통일(resumed_at과의 포맷 불일치 해소)"
```

---

### Task 6: `trading/upbit_ws.py` — 재연결 로깅 추가

**Files:**
- Modify: `trading/upbit_ws.py`
- Modify: `tests/test_upbit_ws.py`

**Interfaces:** 없음(로깅만 추가, `stream_ticker()`의 시그니처/동작/타이밍 불변).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_upbit_ws.py` 최상단 import에 `import logging`을 추가하고,
`test_stream_ticker_reconnects_after_connection_drop` 함수 다음에 추가:

```python
async def test_stream_ticker_logs_warning_on_reconnect(monkeypatch, caplog):
    monkeypatch.setattr(upbit_ws, "RECONNECT_BASE_DELAY_SECONDS", 0.01)
    connection_count = {"n": 0}

    async def handler(ws):
        connection_count["n"] += 1
        await ws.recv()  # subscribe message
        if connection_count["n"] == 1:
            await ws.send(json.dumps({"seq": 1}))
            await ws.close()  # 첫 연결은 끊어서 재연결을 유도
        else:
            await ws.send(json.dumps({"seq": 2}))
            await ws.wait_closed()

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        gen = stream_ticker(["KRW-BTC"], url=f"ws://127.0.0.1:{port}")
        with caplog.at_level(logging.WARNING, logger="trading.upbit_ws"):
            await anext(gen)
            await anext(gen)
        await gen.aclose()

    assert any("재연결" in record.message for record in caplog.records)
```

(127.0.0.1을 쓰는 이유는 기존
`test_stream_ticker_applies_backoff_delay_on_clean_close`의 주석 참고 — "localhost"는
이 개발 환경에서 DNS 해석 오버헤드로 타이밍이 흔들릴 수 있다.)

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_upbit_ws.py -v -k logs_warning_on_reconnect`
Expected: FAIL — `assert any(...) `가 빈 `caplog.records`에 대해 실패

- [ ] **Step 3: `trading/upbit_ws.py` 수정**

파일 상단의:
```python
from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator

import websockets

UPBIT_WS_URL = "wss://api.upbit.com/websocket/v1"
```
을:
```python
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator

import websockets

logger = logging.getLogger(__name__)

UPBIT_WS_URL = "wss://api.upbit.com/websocket/v1"
```
로 교체한다.

`stream_ticker()`의 while 루프를:
```python
    while True:
        try:
            async with websockets.connect(url) as ws:
                await ws.send(subscribe_msg)
                delay = RECONNECT_BASE_DELAY_SECONDS
                async for raw in ws:
                    data = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                    yield json.loads(data)
        except (websockets.exceptions.WebSocketException, OSError, json.JSONDecodeError):
            pass
        await asyncio.sleep(delay)
        delay = min(delay * 2, RECONNECT_MAX_DELAY_SECONDS)
```
에서:
```python
    while True:
        disconnect_reason: BaseException | str = "정상 종료"
        try:
            async with websockets.connect(url) as ws:
                await ws.send(subscribe_msg)
                delay = RECONNECT_BASE_DELAY_SECONDS
                async for raw in ws:
                    data = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                    yield json.loads(data)
        except (websockets.exceptions.WebSocketException, OSError, json.JSONDecodeError) as exc:
            disconnect_reason = exc
        logger.warning("ticker WS 연결 끊김(%s), %.1f초 후 재연결", disconnect_reason, delay)
        await asyncio.sleep(delay)
        delay = min(delay * 2, RECONNECT_MAX_DELAY_SECONDS)
```
로 교체한다(정상 종료·예외 종료 둘 다 재연결 직전에 한 줄 남긴다 — 매 tick이 아니라
연결이 끊길 때만이라 스팸이 안 된다).

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_upbit_ws.py -v`
Expected: 전부 PASS(회귀 없음 — 특히 `test_stream_ticker_applies_backoff_delay_on_clean_close`의
타이밍 검증이 로그 추가로 영향받지 않는지 확인)

- [ ] **Step 5: 커밋**

```bash
git add trading/upbit_ws.py tests/test_upbit_ws.py
git commit -m "feat: upbit_ws 재연결 시 로깅 추가(사유 불문 매 재연결 1줄)"
```

---

### Task 7: `trading/upbit_client.py` — 429 재시도/소진 로깅 추가

**Files:**
- Modify: `trading/upbit_client.py`
- Modify: `tests/test_upbit_client.py`

**Interfaces:** 없음(로깅만 추가, `_request()`/공개 함수들의 시그니처/동작 불변).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_upbit_client.py` 최상단 import에 `import logging`을 추가하고,
`test_get_accounts_raises_after_exhausting_retries` 함수 다음에 추가:

```python
async def test_get_accounts_logs_warning_on_429_retry(monkeypatch, caplog):
    monkeypatch.setenv("UPBIT_ACCESS_KEY", "test-access")
    monkeypatch.setenv("UPBIT_SECRET_KEY", "test-secret")
    monkeypatch.setattr(upbit_client, "RATE_LIMIT_BACKOFF_SECONDS", 0.0)
    calls = {"count": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(429)
        return httpx.Response(200, json=[])

    async with _mock_async_client(handler) as client:
        with caplog.at_level(logging.WARNING, logger="trading.upbit_client"):
            await get_accounts(client=client)

    assert any("429" in record.message for record in caplog.records)


async def test_get_accounts_logs_error_when_retries_exhausted(monkeypatch, caplog):
    monkeypatch.setenv("UPBIT_ACCESS_KEY", "test-access")
    monkeypatch.setenv("UPBIT_SECRET_KEY", "test-secret")
    monkeypatch.setattr(upbit_client, "RATE_LIMIT_BACKOFF_SECONDS", 0.0)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    async with _mock_async_client(handler) as client:
        with caplog.at_level(logging.WARNING, logger="trading.upbit_client"):
            with pytest.raises(RuntimeError):
                await get_accounts(client=client)

    assert any(record.levelno == logging.ERROR for record in caplog.records)
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_upbit_client.py -v -k "logs_warning_on_429_retry or logs_error_when_retries_exhausted"`
Expected: FAIL — 둘 다 `assert any(...)`가 빈 `caplog.records`에 대해 실패

- [ ] **Step 3: `trading/upbit_client.py` 수정**

파일 상단의:
```python
from __future__ import annotations

import asyncio
import hashlib
import os
import time
import uuid
from urllib.parse import unquote, urlencode

import httpx
import jwt
from dotenv import load_dotenv

load_dotenv()

UPBIT_BASE_URL = "https://api.upbit.com/v1"
```
을:
```python
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
import uuid
from urllib.parse import unquote, urlencode

import httpx
import jwt
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

UPBIT_BASE_URL = "https://api.upbit.com/v1"
```
로 교체한다.

`_request()` 안의:
```python
            if resp.status_code == 429:
                await asyncio.sleep(RATE_LIMIT_BACKOFF_SECONDS * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.json()
        raise UpbitRateLimitError(f"업비트 API 호출 실패 (429 재시도 소진): {method} {path}")
```
을:
```python
            if resp.status_code == 429:
                logger.warning(
                    "업비트 429 재시도 %d/%d: %s %s", attempt + 1, RETRY_ATTEMPTS, method, path,
                )
                await asyncio.sleep(RATE_LIMIT_BACKOFF_SECONDS * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.json()
        logger.error("업비트 API 호출 실패(429 재시도 소진): %s %s", method, path)
        raise UpbitRateLimitError(f"업비트 API 호출 실패 (429 재시도 소진): {method} {path}")
```
로 교체한다. 성공 요청은 로그하지 않는다(기본 30req/s·주문 8req/s 트래픽에서 매
요청 로그는 노이즈가 된다).

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_upbit_client.py -v`
Expected: 전부 PASS(회귀 없음)

- [ ] **Step 5: 커밋**

```bash
git add trading/upbit_client.py tests/test_upbit_client.py
git commit -m "feat: upbit_client 429 재시도/소진 로깅 추가"
```

---

### Task 8: 전체 회귀 확인

**Files:** 없음(검증 전용, 코드 변경 없음).

**Interfaces:** 없음.

- [ ] **Step 1: 전체 테스트 스위트 실행**

Run: `python -m pytest -q`
Expected: 전부 PASS(Task1~7의 신규 테스트 포함, 기존 스위트 회귀 없음)

- [ ] **Step 2: 스펙의 "범위 경계" 재확인**

Run: `grep -rln "insert_signal\|tripped_at\|_connect\b" trading/order_executor.py trading/daemon.py trading/reconciler.py trading/position_manager.py`
Expected: 빈 결과(이 4개 파일은 이 플랜에서 변경되지 않았어야 하고, 실제로 이 심볼들을
직접 참조하지도 않는다 — 스펙 자기검토에서 확인한 내용의 최종 재확인)

커밋 없음(이 태스크는 검증만 수행, 각 파일 변경은 이미 Task1~7에서 커밋됨).

---

## Self-Review

**스펙 커버리지:**
- 결정1(로깅) → Task6(upbit_ws) + Task7(upbit_client).
- 결정2(signals UNIQUE + idempotent) → Task2.
- 결정3(skip_reason 미확보 지표명) → Task3(헬퍼) + Task4(signal_engine 소비부).
- 결정4(tripped_at UTC 통일) → Task5.
- 결정5(db._connect 경로별 1회 초기화) → Task1.

**플레이스홀더 스캔:** 없음 — 모든 스텝에 완전한 코드가 있다.

**타입 일관성:** `find_indicators_with_missing_values(group: dict, values: dict[str,
float | None]) -> list[str]`이 Task3(정의)과 Task4(호출부, `find_indicators_with_missing_values(buy_conditions,
values)`) 양쪽에서 동일한 파라미터 순서(`group` 먼저, `values` 나중)로 일치함을
확인했다. `db.insert_signal()`의 시그니처는 Task2 전후로 완전히 동일(반환 타입
`str` 그대로).

**태스크 순서 재확인:** Task1과 Task2는 둘 다 `trading/db.py`를 건드리지만 서로 다른
함수(`_connect()` vs `insert_signal()`/스키마의 `signals` 테이블)라 어느 순서로
구현해도 무방하다 — 이 플랜은 회귀 위험이 더 큰 Task1을 먼저 배치했다. Task4는
Task3의 신규 함수를 import하므로 반드시 Task3 이후에 온다(순서 고정).

**기존 테스트 영향 재확인:** `tests/test_trading_db.py`의 `_fresh_db` 패턴을 쓰는
전체 스위트가 Task1(스키마 캐싱)의 유일한 회귀 위험 지점이다 — Task1 Step5에서 전체
회귀를 별도로 한 번 더 돌리는 이유. 나머지 태스크는 각자 파일에 로컬하고, 기존
테스트가 검사하지 않는 새 동작(로그 출력, `skip_reason`의 구체적인 문자열, `tripped_at`의
오프셋)만 추가하므로 회귀 위험이 낮다.
