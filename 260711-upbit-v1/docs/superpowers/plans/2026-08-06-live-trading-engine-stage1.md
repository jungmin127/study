# 라이브 트레이딩 1단계 — 기반: DB 스키마 + 조건평가 unknown 처리 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 라이브 트레이딩 엔진이 공유할 SQLite DB(`trading.db`) 스키마를 만들고,
`engine/condition_tree.py`에 값 딕셔너리 기반 조건평가 함수(`eval_group_values`)를
추가해 지표값이 없을 때("unknown") 그 조건을 스킵하는 동작까지 지원한다.

**Architecture:** 이 문서는 스펙
`docs/superpowers/specs/2026-08-04-live-trading-foundation-design.md`의 "1. 트레이딩
엔진 + 핵심 안전장치" 단계를 구현하는 여러 플랜 중 **첫 번째(기반) 플랜**이다. 이후
플랜(지표 엔진 A/B그룹, Upbit 클라이언트, 신호평가/주문/리스크 엔진, 백엔드·프론트
UX)은 전부 이 두 산출물(`trading/db.py`, `eval_group_values`) 위에서 만들어지므로
먼저 완성한다. `trading/` 패키지는 backtrader에 의존하지 않는다(결정 1) — 이 플랜의
두 파일 모두 backtrader를 import하지 않는다.

**Tech Stack:** Python, `sqlite3`(표준 라이브러리), `pytest`. 새 의존성 없음.

## Global Constraints

- DB는 SQLite, 기존 캐시 DB(`results.db`)와 완전히 분리된 새 파일 `data/trading.db`를
  쓴다(스펙 결정 4).
- `trading/` 패키지는 `engine/condition_tree.py` 외에는 `engine/`의 backtrader 관련
  코드를 import하지 않는다(스펙 결정 1, 모듈구조 절).
- 기존 `engine/condition_tree.py`의 `eval_group()`과 관련 함수들은 **수정하지 않는다**
  (회귀 위험 0, 스펙 결정 1) — 이 플랜은 새 함수만 추가한다.
- `live_strategies.status='running'`은 애플리케이션 레벨에서 **market(코인)당 최대
  1행**만 허용해야 한다(스펙 결정 6) — 이 강제 자체는 승인 API를 만드는 후속 플랜에서
  구현하지만, 스키마는 이를 방해하지 않아야 한다(DB 레벨 UNIQUE 제약을 걸지 않음 —
  `status`가 계속 바뀌는 컬럼에 부분 유니크 인덱스를 SQLite로 걸기보다 애플리케이션
  레벨 검사가 더 유연하다는 것이 이 플랜의 판단이며, 후속 플랜에서 실제로 검사 로직을
  구현한다).
- `circuit_breaker_state`/`daily_performance`는 전략별(스펙 결정 6·7 개정) 스코프다 —
  `live_strategy_id`를 포함한다.
- 커밋은 태스크 단위로 작게, 테스트가 통과한 뒤에만 한다(리포지토리 관례).

---

## File Structure

- **Create:** `trading/__init__.py` — 빈 패키지 초기화 파일(신규 `trading/` 패키지의
  첫 파일이므로 이 플랜에서 만든다).
- **Create:** `trading/db.py` — `trading.db`의 7개 테이블 스키마 정의 + 연결 헬퍼
  `_connect()`. `engine/cache.py`와 같은 패턴(모듈 전역 `DB_PATH`, `_SCHEMA` 문자열,
  `executescript` + `mkdir(parents=True, exist_ok=True)`)을 따르되, 실거래 데이터의
  참조무결성이 중요하므로 `PRAGMA foreign_keys = ON`을 추가한다(캐시 전용인
  `results.db`는 이걸 켜지 않았음 — 실수로 존재하지 않는 `live_strategy_id`를 참조하는
  포지션/주문이 생기면 조용히 남는 대신 즉시 에러가 나야 한다).
- **Modify:** `engine/condition_tree.py` — `eval_group_values()`, 관련 `__all__` 항목
  추가. 기존 함수는 한 줄도 바꾸지 않는다.
- **Test:** `tests/test_trading_db.py` (신규) — 스키마 생성, 외래키 강제, 컬럼 존재
  여부 검증.
- **Test:** `tests/test_condition_tree.py` (기존 파일에 추가) — `eval_group_values`의
  동작(파리티, unknown 드롭, 전체 unknown 시 None, 중첩 그룹 unknown 전파, position
  relative indicator는 영향 없음).

---

### Task 1: `trading/db.py` — trading.db 스키마

**Files:**
- Create: `trading/__init__.py`
- Create: `trading/db.py`
- Test: `tests/test_trading_db.py`

**Interfaces:**
- Consumes: 없음(이 플랜의 첫 태스크, 신규 패키지).
- Produces: `trading.db.DB_PATH: Path`, `trading.db.TABLE_NAMES: tuple[str, ...]`,
  `trading.db._connect() -> sqlite3.Connection`(외래키 활성화 + 스키마 적용된 연결을
  반환, 호출자가 `close()` 책임짐). 후속 플랜들이 이 `_connect()`을 그대로 재사용해
  `INSERT`/`SELECT` 함수를 추가한다.

- [ ] **Step 1: 빈 패키지 파일 생성**

`trading/__init__.py`:
```python
```

(빈 파일 — `engine/`, `backend/`도 같은 방식으로 빈 `__init__.py`를 쓴다.)

- [ ] **Step 2: 실패하는 테스트 작성**

`tests/test_trading_db.py`:
```python
import sqlite3

import pytest

import trading.db as db_module


def _fresh_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "trading.db")
    return db_module


def test_connect_creates_all_seven_tables(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    conn = db._connect()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        table_names = {row[0] for row in rows}
    finally:
        conn.close()

    assert table_names == set(db.TABLE_NAMES)


def test_connect_creates_file_at_db_path(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    assert not db.DB_PATH.exists()
    db._connect().close()
    assert db.DB_PATH.exists()


def test_connect_is_idempotent(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    db._connect().close()
    db._connect().close()
    db._connect().close()  # 여러 번 호출해도 에러 없어야 함


def test_foreign_keys_are_enforced(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    conn = db._connect()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO positions (id, live_strategy_id, market) VALUES (?, ?, ?)",
                ("pos-1", "nonexistent-strategy-id", "KRW-BTC"),
            )
    finally:
        conn.close()


def test_live_strategies_columns(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    conn = db._connect()
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(live_strategies)")}
    finally:
        conn.close()

    assert columns == {
        "id", "source_run_id", "market", "timeframe", "buy_conditions_json",
        "sell_conditions_json", "risk_config_json", "current_capital", "status",
        "last_processed_candle_time", "created_at", "approved_at", "started_at",
        "stopped_at",
    }


def test_circuit_breaker_state_and_daily_performance_are_per_strategy(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    conn = db._connect()
    try:
        cb_columns = {row[1] for row in conn.execute("PRAGMA table_info(circuit_breaker_state)")}
        dp_columns = {row[1] for row in conn.execute("PRAGMA table_info(daily_performance)")}
    finally:
        conn.close()

    assert "live_strategy_id" in cb_columns
    assert "live_strategy_id" in dp_columns
```

- [ ] **Step 3: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_trading_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'trading.db'` (또는 `trading`)

- [ ] **Step 4: `trading/db.py` 구현**

```python
"""
trading/db.py

라이브 트레이딩 전용 SQLite DB(trading.db). 백테스트 캐시(results.db)와 완전히
분리된 별도 파일이며(스펙 결정 4), 실거래 데이터의 참조무결성이 중요해 외래키
제약을 켠다(캐시 전용인 engine/cache.py는 켜지 않았음).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "trading.db"

TABLE_NAMES = (
    "live_strategies",
    "positions",
    "orders",
    "signals",
    "daily_performance",
    "circuit_breaker_state",
    "manual_intervention_events",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS live_strategies (
    id                  TEXT PRIMARY KEY,
    source_run_id       TEXT,
    market              TEXT NOT NULL,
    timeframe           TEXT NOT NULL,
    buy_conditions_json  TEXT NOT NULL,
    sell_conditions_json TEXT NOT NULL,
    risk_config_json    TEXT NOT NULL,
    current_capital     REAL,
    status              TEXT NOT NULL DEFAULT 'draft',
    last_processed_candle_time TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    approved_at         TEXT,
    started_at          TEXT,
    stopped_at          TEXT
);

CREATE TABLE IF NOT EXISTS positions (
    id               TEXT PRIMARY KEY,
    live_strategy_id TEXT NOT NULL REFERENCES live_strategies(id),
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
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS orders (
    id                TEXT PRIMARY KEY,
    upbit_uuid        TEXT UNIQUE,
    live_strategy_id  TEXT REFERENCES live_strategies(id),
    position_id       TEXT REFERENCES positions(id),
    replaces_order_id TEXT REFERENCES orders(id),
    market            TEXT NOT NULL,
    side              TEXT NOT NULL,
    order_type        TEXT NOT NULL,
    requested_price   REAL,
    requested_volume  REAL,
    filled_price      REAL,
    filled_volume     REAL,
    fee               REAL,
    expected_price    REAL,
    slippage_pct      REAL,
    status            TEXT NOT NULL,
    is_external       INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT
);

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

CREATE TABLE IF NOT EXISTS daily_performance (
    trading_date     TEXT NOT NULL,
    live_strategy_id TEXT NOT NULL REFERENCES live_strategies(id),
    realized_pnl     REAL NOT NULL DEFAULT 0,
    realized_pnl_pct REAL NOT NULL DEFAULT 0,
    trade_count      INTEGER NOT NULL DEFAULT 0,
    win_count        INTEGER NOT NULL DEFAULT 0,
    loss_count       INTEGER NOT NULL DEFAULT 0,
    starting_balance REAL,
    ending_balance   REAL,
    max_drawdown_pct REAL,
    PRIMARY KEY (trading_date, live_strategy_id)
);

CREATE TABLE IF NOT EXISTS circuit_breaker_state (
    live_strategy_id   TEXT PRIMARY KEY REFERENCES live_strategies(id),
    trading_date       TEXT NOT NULL,
    consecutive_losses INTEGER NOT NULL DEFAULT 0,
    tripped            INTEGER NOT NULL DEFAULT 0,
    tripped_reason     TEXT,
    tripped_at         TEXT,
    resumed_at         TEXT
);

CREATE TABLE IF NOT EXISTS manual_intervention_events (
    id             TEXT PRIMARY KEY,
    detected_at    TEXT NOT NULL DEFAULT (datetime('now')),
    market         TEXT,
    description    TEXT NOT NULL,
    action_taken   TEXT NOT NULL,
    resolved_at    TEXT
);
"""


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    return conn
```

- [ ] **Step 5: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_trading_db.py -v`
Expected: 6개 테스트 전부 PASS

- [ ] **Step 6: 커밋**

```bash
git add trading/__init__.py trading/db.py tests/test_trading_db.py
git commit -m "feat: trading.db 스키마 추가 (7개 테이블, 외래키 강제)"
```

---

### Task 2: `engine/condition_tree.py` — `eval_group_values()` (unknown 처리 포함)

**Files:**
- Modify: `engine/condition_tree.py`
- Test: `tests/test_condition_tree.py`

**Interfaces:**
- Consumes: `engine.condition_tree.indicator_key(indicator: str, params: dict) -> str`,
  `engine.condition_tree.apply_operator(value: float, operator: str, threshold: float) -> bool`,
  `engine.condition_tree.POSITION_RELATIVE_INDICATORS: set[str]`(모두 기존 함수/상수,
  변경 없이 재사용).
- Produces: `engine.condition_tree.eval_group_values(group: dict, values: dict[str,
  float | None], position_return_pct: float | None = None, position_holding_bars:
  int | None = None) -> bool | None`. 반환값 `None`은 "이 그룹을 판단할 수 있는 알려진
  지표가 하나도 없었다"는 뜻(스펙 결정 8) — 신호평가 엔진을 만드는 후속 플랜은 최상위
  매수/매도 조건 그룹 평가가 `None`이면 그 캔들에서는 판단 불가로 처리하고, 그게 매
  캔들 반복되면(= B그룹 지표에만 의존하는 전략) 해당 `live_strategy`를
  `status='paused'`로 전환한다(이 로직 자체는 daemon/signal_engine 플랜에서 구현).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_condition_tree.py`에 추가(파일 맨 아래):
```python
from engine.condition_tree import eval_group_values


def test_eval_group_values_matches_apply_operator_when_all_known():
    tree = {
        "type": "AND",
        "conditions": [
            {"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 31},
            {"indicator": "CCI", "params": {}, "operator": "<", "threshold": -120},
        ],
    }
    values = {
        indicator_key("RSI", {"period": 14}): 25.0,
        indicator_key("CCI", {}): -150.0,
    }
    assert eval_group_values(tree, values) is True

    values[indicator_key("CCI", {})] = -50.0
    assert eval_group_values(tree, values) is False


def test_eval_group_values_drops_unknown_leaf_in_and():
    tree = {
        "type": "AND",
        "conditions": [
            {"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 31},
            {"indicator": "FUNDING_RATE", "params": {}, "operator": "<", "threshold": -0.01},
        ],
    }
    # FUNDING_RATE 키가 아예 없음 = unknown -> RSI 조건만으로 판단
    values = {indicator_key("RSI", {"period": 14}): 25.0}
    assert eval_group_values(tree, values) is True

    values = {indicator_key("RSI", {"period": 14}): 40.0}
    assert eval_group_values(tree, values) is False


def test_eval_group_values_drops_unknown_leaf_in_or():
    tree = {
        "type": "OR",
        "conditions": [
            {"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 54},
            {"indicator": "FUNDING_RATE", "params": {}, "operator": ">", "threshold": 0.01},
        ],
    }
    values = {indicator_key("RSI", {"period": 14}): 60.0}
    assert eval_group_values(tree, values) is True

    values = {indicator_key("RSI", {"period": 14}): 10.0}
    assert eval_group_values(tree, values) is False


def test_eval_group_values_none_value_is_treated_as_unknown():
    tree = {
        "type": "AND",
        "conditions": [{"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 31}],
    }
    # 키는 있지만 값이 None(지표 계산 실패) -> unknown -> 결과도 None
    values = {indicator_key("RSI", {"period": 14}): None}
    assert eval_group_values(tree, values) is None


def test_eval_group_values_returns_none_when_all_leaves_unknown():
    tree = {
        "type": "AND",
        "conditions": [
            {"indicator": "FUNDING_RATE", "params": {}, "operator": "<", "threshold": -0.01},
            {"indicator": "KOREA_PREMIUM", "params": {}, "operator": "<", "threshold": 2.0},
        ],
    }
    assert eval_group_values(tree, {}) is None


def test_eval_group_values_propagates_unknown_nested_group():
    tree = {
        "type": "AND",
        "conditions": [
            {"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 31},
            {
                "type": "OR",
                "conditions": [
                    {"indicator": "FUNDING_RATE", "params": {}, "operator": "<", "threshold": -0.01},
                    {"indicator": "KOREA_PREMIUM", "params": {}, "operator": "<", "threshold": 2.0},
                ],
            },
        ],
    }
    values = {indicator_key("RSI", {"period": 14}): 25.0}
    # 중첩 OR 그룹 전체가 unknown -> 상위 AND에서 제외 -> RSI만으로 판단
    assert eval_group_values(tree, values) is True


def test_eval_group_values_position_relative_indicators_unaffected_by_unknown_handling():
    tree = {
        "type": "AND",
        "conditions": [{"indicator": "STOP_LOSS_PCT", "params": {}, "operator": "<", "threshold": -5.0}],
    }
    # 포지션 없음(position_return_pct=None) -> False (unknown이 아니라 기존 eval_group과 동일한 "false")
    assert eval_group_values(tree, {}, position_return_pct=None) is False
    assert eval_group_values(tree, {}, position_return_pct=-6.0) is True


def test_eval_group_values_empty_conditions_returns_false_not_none():
    assert eval_group_values({"type": "AND", "conditions": []}, {}) is False
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_condition_tree.py -v -k eval_group_values`
Expected: FAIL — `ImportError: cannot import name 'eval_group_values'`

- [ ] **Step 3: `eval_group_values()` 구현**

`engine/condition_tree.py`의 `eval_group()` 함수 바로 아래에 추가:

```python
def eval_group_values(
    group: dict,
    values: dict[str, float | None],
    position_return_pct: float | None = None,
    position_holding_bars: int | None = None,
) -> bool | None:
    """ConditionGroup을 재귀적으로 평가해 bool 또는 None(unknown)을 반환. eval_group()과
    로직은 같지만 bt.Indicator 대신 이미 계산된 값 딕셔너리(indicator_key -> float | None)를
    직접 읽는다(라이브 트레이딩용, 스펙 결정 1).

    values[key]가 없거나 None이면 그 리프는 "unknown"으로 취급되어 AND/OR 평가에서
    제외된다(스펙 결정 8 — 외부데이터 지표가 지연/실패해도 나머지 조건만으로 매매를
    이어가기 위함). 한 그룹의 자식이 전부 unknown이면 그 그룹 자체도 None을 반환하고,
    그 None은 상위 그룹에서도 다시 unknown 리프처럼 제외된다. 최상위 그룹까지 None이
    전파되면 "이 조건 전체를 지금 판단할 수 없다"는 뜻이다."""
    group_type = group.get("type", "AND")
    conditions = group.get("conditions", [])

    if not conditions:
        return False

    results: list[bool] = []
    for item in conditions:
        if "indicator" in item:
            if item["indicator"] == "HOLDING_PERIOD_BARS":
                if position_holding_bars is None:
                    results.append(False)
                else:
                    results.append(
                        apply_operator(position_holding_bars, item["operator"], float(item["threshold"]))
                    )
                continue
            if item["indicator"] in POSITION_RELATIVE_INDICATORS:
                if position_return_pct is None:
                    results.append(False)
                else:
                    results.append(apply_operator(position_return_pct, item["operator"], float(item["threshold"])))
                continue
            key = indicator_key(item["indicator"], item.get("params", {}))
            value = values.get(key)
            if value is None:
                continue  # unknown 리프는 이 그룹 평가에서 제외
            results.append(apply_operator(value, item["operator"], float(item["threshold"])))
        elif "type" in item:
            child = eval_group_values(item, values, position_return_pct, position_holding_bars)
            if child is None:
                continue  # 하위 그룹 전체가 unknown -> 이 그룹 평가에서도 제외
            results.append(child)

    if not results:
        return None  # 판단 가능한 자식이 하나도 없음 -> 이 그룹도 unknown

    return all(results) if group_type == "AND" else any(results)
```

`__all__` 목록에 추가:
```python
__all__ = [
    "POSITION_RELATIVE_INDICATORS",
    "indicator_key",
    "collect_blocks",
    "get_indicator_value",
    "apply_operator",
    "eval_group",
    "eval_group_values",
    "find_unknown_indicators",
    "is_empty",
    "max_required_period",
    "AUX_MARKET_INDICATORS",
    "required_aux_markets",
]
```

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_condition_tree.py -v`
Expected: 기존 테스트 전부 PASS + 새로 추가한 8개 테스트 PASS (회귀 없음)

- [ ] **Step 5: 전체 테스트 스위트 실행(회귀 확인)**

Run: `python -m pytest -q`
Expected: 전부 PASS (기존 백테스트/그리드서치 관련 테스트가 이 변경으로 깨지지 않아야
함 — `eval_group()`을 건드리지 않았으므로 회귀가 없어야 정상)

- [ ] **Step 6: 커밋**

```bash
git add engine/condition_tree.py tests/test_condition_tree.py
git commit -m "feat: eval_group_values 추가 (라이브 트레이딩용, unknown 지표 스킵 처리)"
```

---

## Self-Review

**스펙 커버리지:**
- 결정 4(SQLite, `trading.db` 분리) → Task 1.
- 결정 1(backtrader 미의존, `eval_group()` 불변, `eval_group_values` 신규 추가) → Task 2.
- 결정 6(전략별 독립 서킷브레이커/일일성과 스키마) → Task 1의 `circuit_breaker_state`/
  `daily_performance` 스키마.
- 결정 7(`current_capital` 컬럼) → Task 1의 `live_strategies` 스키마.
- 결정 8(B그룹 지표 unknown 처리) → Task 2.
- 이 플랜은 "기반"만 다룬다 — 지표 엔진(A/B그룹 포팅), Upbit REST/WS 클라이언트,
  signal_engine/order_executor/position_manager/risk_manager/reconciler/daemon,
  백엔드 API, 프론트 "라이브 전략 관리" 페이지는 **의도적으로 이 플랜에 없다** — 각각
  후속 플랜에서 이 플랜의 `trading/db.py`와 `eval_group_values()`를 그대로 재사용해
  만든다.

**플레이스홀더 스캔:** 없음 — 모든 스텝에 완전한 코드가 있다.

**타입 일관성:** `eval_group_values`의 반환 타입은 `bool | None`으로 Task 2 전체에서
일관되게 사용했고, `values: dict[str, float | None]`는 Task 1의 스키마와는 독립적인
런타임 딕셔너리라 DB 스키마와 이름이 겹치지 않는다.

---

## 다음 플랜(이 문서 이후, 순서대로 작성 예정)

1. **지표 엔진 A그룹** — `trading/live_indicators.py`에 대상 마켓 OHLCV만으로 계산되는
   30개 지표를 pandas로 포팅 + `engine/indicators/*.py`(backtrader)와의 골든테스트.
2. **지표 엔진 B그룹 + 장애정책** — 외부데이터 6개 지표의 실시간 수집 + 지연/실패 시
   `None` 반환(결정 8과 Task 2가 여기서 만나 실제로 동작 검증).
3. **Upbit 연동** — `trading/upbit_client.py`(REST/JWT/Throttle), `trading/upbit_ws.py`.
4. **트레이딩 엔진 코어** — `signal_engine.py`, `order_executor.py`,
   `position_manager.py`(복리 자금관리), `risk_manager.py`(전략별 서킷브레이커),
   `reconciler.py`, `daemon.py` 메인루프, State Hydration.
5. **UX** — `backend/main.py` 라이브 전략 관리 API + 프론트 "라이브 전략 관리" 페이지.
