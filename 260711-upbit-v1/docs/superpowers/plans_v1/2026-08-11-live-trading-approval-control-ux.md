# 라이브 전략 승인/제어 UX (⑥) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** DB에 직접 SQL을 치지 않고, 백테스트 상세 페이지 → draft 생성 → 승인(자금검증) →
일시정지/재개/중지까지 라이브 전략을 웹에서 제어할 수 있게 한다.

**Architecture:** `backend/main.py`에 `trading/db.py`를 통해서만 `live_strategies` 테이블을
읽고 쓰는 REST 엔드포인트 6개를 추가하고, `frontend/app`에 그 엔드포인트를 소비하는 페이지
2개(`live-strategies/new`, `live-strategies`)와 백테스트 상세 페이지의 진입 버튼 1개를
추가한다. `trading/daemon.py`는 전혀 수정하지 않는다 — `status` 컬럼 값이 daemon이 이미
아는 값(`running`/`paused`)으로만 바뀌므로 그대로 픽업된다.

**Tech Stack:** FastAPI(Pydantic) + SQLite(`trading/db.py`) 백엔드, Next.js App
Router(TypeScript) 프론트엔드. 기존 grid-search/backtests 기능의 파일 구조·컴포넌트
컨벤션을 그대로 따른다.

## Global Constraints

- `trading/daemon.py`, `trading/order_executor.py`, `trading/signal_engine.py`,
  `trading/risk_manager.py`, `trading/reconciler.py`는 이 플랜에서 **절대 수정하지 않는다**
  — daemon은 `live_strategies.status`만 읽는 기존 계약을 그대로 유지해야 한다.
- `backend/main.py`는 원칙적으로 `trading/`의 실행 로직을 호출하지 않는다. 유일한 예외는
  승인(approve) 엔드포인트가 호출하는 `trading.upbit_client.get_accounts()`(조회)와
  `trading.position_manager.calculate_initial_capital()`(순수 계산) — 둘 다 주문을 내지
  않는다(설계 스펙 결정 2).
- 새 라이브 전략(draft)은 백테스트 상세 페이지의 "이 전략으로 실매매 시작" 진입점을 통해서만
  생성한다 — 라이브 전략 관리 페이지 자체에는 조건을 직접 입력하는 생성 UI를 두지 않는다
  (설계 스펙 결정 3).
- 승인은 `draft`→`running` 1단계로 수행한다. `approved` 상태값은 애플리케이션에서 쓰지
  않는다(설계 스펙 결정 1 — daemon이 `approved`를 인식하지 않기 때문).
- 실행 중 전략의 조건/리스크설정을 수정하는 플로우는 이 플랜의 범위가 아니다(설계 스펙
  결정 4) — 승인/일시정지/재개/중지 4개 액션만 구현한다.
- "중지"는 해당 전략에 열린 포지션(`positions.status='open'`)이 있으면 무조건 거부한다
  (설계 스펙 결정 5).
- 설계 스펙 전체: `docs/superpowers/specs_v1/2026-08-11-live-trading-approval-control-ux-design.md`

---

## Task 1: `trading/db.py` — 라이브 전략 생성/목록 조회

**Files:**
- Modify: `trading/db.py`
- Test: `tests/test_trading_db.py`

**Interfaces:**
- Consumes: 기존 `_connect()`, `TABLE_NAMES`, `sqlite3.Row` 패턴(파일 내 기존 함수들과 동일)
- Produces:
  - `insert_live_strategy(source_run_id: str | None, market: str, timeframe: str, buy_conditions_json: str, sell_conditions_json: str, risk_config_json: str) -> str` — 생성된 행의 `id` 반환, `status='draft'`(스키마 기본값)
  - `list_live_strategies() -> list[dict]` — 전체 status, `created_at DESC, rowid DESC` 정렬

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_trading_db.py` 맨 아래(파일 끝, `test_accumulate_stale_resolution_does_not_affect_other_positions` 다음)에 추가:

```python
def test_insert_live_strategy_creates_draft_row(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = db.insert_live_strategy(
        source_run_id="run-1", market="KRW-BTC", timeframe="minutes60",
        buy_conditions_json="{}", sell_conditions_json="{}", risk_config_json="{}",
    )

    strategy = db.get_live_strategy(strategy_id)
    assert strategy["status"] == "draft"
    assert strategy["source_run_id"] == "run-1"
    assert strategy["market"] == "KRW-BTC"
    assert strategy["timeframe"] == "minutes60"
    assert strategy["current_capital"] is None
    assert strategy["approved_at"] is None


def test_insert_live_strategy_allows_null_source_run_id(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = db.insert_live_strategy(
        source_run_id=None, market="KRW-ETH", timeframe="days",
        buy_conditions_json="{}", sell_conditions_json="{}", risk_config_json="{}",
    )

    strategy = db.get_live_strategy(strategy_id)
    assert strategy["source_run_id"] is None


def test_list_live_strategies_returns_all_statuses_newest_first(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    older_id = insert_live_strategy(db, status="stopped")
    newer_id = db.insert_live_strategy(
        source_run_id=None, market="KRW-BTC", timeframe="minutes60",
        buy_conditions_json="{}", sell_conditions_json="{}", risk_config_json="{}",
    )

    rows = db.list_live_strategies()

    assert [r["id"] for r in rows] == [newer_id, older_id]


def test_list_live_strategies_returns_empty_list_when_none(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    assert db.list_live_strategies() == []
```

- [x] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_trading_db.py -k "insert_live_strategy or list_live_strategies" -v`
Expected: FAIL — `AttributeError: module 'trading.db' has no attribute 'insert_live_strategy'`

- [x] **Step 3: 최소 구현 작성**

`trading/db.py`의 `list_active_strategies()` 함수(파일 맨 끝) 바로 앞에 추가:

```python
def insert_live_strategy(
    source_run_id: str | None, market: str, timeframe: str,
    buy_conditions_json: str, sell_conditions_json: str, risk_config_json: str,
) -> str:
    live_strategy_id = str(uuid.uuid4())
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO live_strategies "
            "(id, source_run_id, market, timeframe, buy_conditions_json, sell_conditions_json, risk_config_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (live_strategy_id, source_run_id, market, timeframe,
             buy_conditions_json, sell_conditions_json, risk_config_json),
        )
        conn.commit()
    finally:
        conn.close()
    return live_strategy_id


def list_live_strategies() -> list[dict]:
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM live_strategies ORDER BY created_at DESC, rowid DESC"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
```

- [x] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_trading_db.py -k "insert_live_strategy or list_live_strategies" -v`
Expected: PASS (4 passed)

- [x] **Step 5: 전체 trading db 테스트 회귀 확인**

Run: `python -m pytest tests/test_trading_db.py -v`
Expected: 전부 PASS (기존 테스트 포함, 총 40개 이상)

- [x] **Step 6: 커밋**

```bash
git add trading/db.py tests/test_trading_db.py
git commit -m "feat: 라이브 전략 생성/목록 조회 DB 함수 추가"
```

---

## Task 2: `trading/db.py` — 승인/전환/중지 가드 함수

**Files:**
- Modify: `trading/db.py`
- Test: `tests/test_trading_db.py`

**Interfaces:**
- Consumes: Task 1의 `insert_live_strategy`(테스트에서 픽스처 대신 직접 검증용으로 사용
  가능하나, 아래 테스트는 기존 `tests/trading_db_fixtures.insert_live_strategy` 픽스처를
  그대로 사용한다), 기존 `db.insert_position`, `db.close_position_row`, `db.get_live_strategy`
- Produces:
  - `approve_live_strategy(live_strategy_id: str, current_capital: float) -> bool` —
    `status='draft'`일 때만 `running`으로 전환 + `current_capital`/`approved_at`/`started_at`
    설정, 성공 시 True
  - `transition_live_strategy_status(live_strategy_id: str, from_status: str, to_status: str) -> bool` —
    `status=from_status`일 때만 `to_status`로 전환, 성공 시 True (pause/resume 공용)
  - `stop_live_strategy_if_no_open_position(live_strategy_id: str) -> bool` — 열린 포지션이
    없을 때만 `status='stopped'`+`stopped_at` 설정, 성공 시 True. 열린 포지션이 있으면 아무
    것도 바꾸지 않고 False.

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_trading_db.py` 맨 아래에 추가:

```python
def test_approve_live_strategy_transitions_draft_to_running(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="draft", current_capital=None)

    result = db.approve_live_strategy(strategy_id, 150000.0)

    assert result is True
    strategy = db.get_live_strategy(strategy_id)
    assert strategy["status"] == "running"
    assert strategy["current_capital"] == 150000.0
    assert strategy["approved_at"] is not None
    assert strategy["started_at"] is not None


def test_approve_live_strategy_returns_false_when_not_draft(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="running", current_capital=100000.0)

    result = db.approve_live_strategy(strategy_id, 150000.0)

    assert result is False
    strategy = db.get_live_strategy(strategy_id)
    assert strategy["current_capital"] == 100000.0
    assert strategy["approved_at"] is None


def test_transition_live_strategy_status_applies_when_status_matches(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="running")

    result = db.transition_live_strategy_status(strategy_id, "running", "paused")

    assert result is True
    assert db.get_live_strategy(strategy_id)["status"] == "paused"


def test_transition_live_strategy_status_returns_false_when_status_mismatches(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="draft")

    result = db.transition_live_strategy_status(strategy_id, "running", "paused")

    assert result is False
    assert db.get_live_strategy(strategy_id)["status"] == "draft"


def test_stop_live_strategy_if_no_open_position_stops_when_no_position(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="running")

    result = db.stop_live_strategy_if_no_open_position(strategy_id)

    assert result is True
    strategy = db.get_live_strategy(strategy_id)
    assert strategy["status"] == "stopped"
    assert strategy["stopped_at"] is not None


def test_stop_live_strategy_if_no_open_position_refuses_when_position_open(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="running")
    db.insert_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)

    result = db.stop_live_strategy_if_no_open_position(strategy_id)

    assert result is False
    assert db.get_live_strategy(strategy_id)["status"] == "running"


def test_stop_live_strategy_if_no_open_position_allows_stopping_after_position_closed(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="running")
    position_id = db.insert_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)
    db.close_position_row(position_id, 51_000_000.0, 0.01, 10000.0, 2.0, "signal")

    result = db.stop_live_strategy_if_no_open_position(strategy_id)

    assert result is True
    assert db.get_live_strategy(strategy_id)["status"] == "stopped"
```

- [x] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_trading_db.py -k "approve_live_strategy or transition_live_strategy_status or stop_live_strategy_if_no_open_position" -v`
Expected: FAIL — `AttributeError`

- [x] **Step 3: 최소 구현 작성**

같은 위치(`list_active_strategies()` 바로 앞, Task 1에서 추가한 두 함수 다음)에 추가:

```python
def approve_live_strategy(live_strategy_id: str, current_capital: float) -> bool:
    conn = _connect()
    try:
        cursor = conn.execute(
            "UPDATE live_strategies SET status='running', current_capital=?, "
            "approved_at=datetime('now'), started_at=datetime('now') "
            "WHERE id=? AND status='draft'",
            (current_capital, live_strategy_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def transition_live_strategy_status(live_strategy_id: str, from_status: str, to_status: str) -> bool:
    conn = _connect()
    try:
        cursor = conn.execute(
            "UPDATE live_strategies SET status=? WHERE id=? AND status=?",
            (to_status, live_strategy_id, from_status),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def stop_live_strategy_if_no_open_position(live_strategy_id: str) -> bool:
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        open_position = conn.execute(
            "SELECT id FROM positions WHERE live_strategy_id = ? AND status = 'open'",
            (live_strategy_id,),
        ).fetchone()
        if open_position is not None:
            return False
        cursor = conn.execute(
            "UPDATE live_strategies SET status='stopped', stopped_at=datetime('now') "
            "WHERE id=? AND status IN ('draft','running','paused')",
            (live_strategy_id,),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()
```

- [x] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_trading_db.py -k "approve_live_strategy or transition_live_strategy_status or stop_live_strategy_if_no_open_position" -v`
Expected: PASS (7 passed)

- [x] **Step 5: 전체 trading db 테스트 회귀 확인**

Run: `python -m pytest tests/test_trading_db.py -v`
Expected: 전부 PASS

- [x] **Step 6: 커밋**

```bash
git add trading/db.py tests/test_trading_db.py
git commit -m "feat: 라이브 전략 승인/상태전환/중지 가드 DB 함수 추가"
```

---

## Task 3: `backend/main.py` — 백테스트 조건 프리필 엔드포인트

라이브 전략 생성 폼이 백테스트의 market/timeframe/buy_conditions/sell_conditions를
다시 읽어올 수 있는 엔드포인트가 필요하다. 기존 `GET /api/v1/backtests/{run_id}`는
이 필드들을 응답에 포함하지 않는다(성과 지표/차트 전용) — `engine.cache.get_run_config()`가
이미 필요한 데이터를 갖고 있지만 지금은 내부 refresh 엔드포인트에서만 쓰인다.

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_backend.py`

**Interfaces:**
- Consumes: 이미 import된 `get_run_config`(engine.cache), `save_result`, `_client`,
  `_patch_get_candles`, `_run_request`, `_VALID_BUY`, `_VALID_SELL`(모두 기존 테스트 헬퍼)
- Produces: `GET /api/v1/backtests/{run_id}/config` → `{"market": str, "timeframe": str, "buy_conditions": dict, "sell_conditions": dict}`, 없으면 404

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_backend.py`의 `test_refresh_backtest_preserves_title_and_description` 함수
바로 다음(193번째 줄 근처, `test_get_signals_returns_registered_signal_keys` 앞)에 추가:

```python
def test_backtest_config_returns_404_for_missing_run(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    resp = client.get("/api/v1/backtests/does-not-exist/config")
    assert resp.status_code == 404


def test_backtest_config_returns_market_timeframe_and_conditions(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch)

    create_resp = client.post("/api/v1/backtests/run", json=_run_request())
    run_id = create_resp.json()["run_id"]

    resp = client.get(f"/api/v1/backtests/{run_id}/config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["market"] == "KRW-BTC"
    assert body["timeframe"] == "days"
    assert body["buy_conditions"] == _VALID_BUY
    assert body["sell_conditions"] == _VALID_SELL
```

- [x] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_backend.py -k "backtest_config" -v`
Expected: FAIL — 404 Not Found (라우트 없음, `test_backtest_config_returns_market_timeframe_and_conditions`가 실패)

- [x] **Step 3: 최소 구현 작성**

`backend/main.py`의 `get_backtest_detail` 함수(582번째 줄 근처, `return {...}` 다음) 바로
뒤에 추가:

```python
@app.get("/api/v1/backtests/{run_id}/config")
def get_backtest_config_endpoint(run_id: str) -> dict:
    config = get_run_config(run_id)
    if config is None:
        raise HTTPException(status_code=404, detail="해당 run_id의 백테스트 설정을 찾을 수 없습니다")
    return {
        "market": config["market"],
        "timeframe": config["timeframe"],
        "buy_conditions": config["buy_conditions"],
        "sell_conditions": config["sell_conditions"],
    }
```

- [x] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_backend.py -k "backtest_config" -v`
Expected: PASS (2 passed)

- [x] **Step 5: 전체 backend 테스트 회귀 확인**

Run: `python -m pytest tests/test_backend.py -v`
Expected: 전부 PASS

- [x] **Step 6: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "feat: 백테스트 조건 프리필용 config 엔드포인트 추가"
```

---

## Task 4: `backend/main.py` — 라이브 전략 draft 생성 API

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_backend.py`

**Interfaces:**
- Consumes: Task 1의 `trading.db.insert_live_strategy`, `trading.db.get_live_strategy`,
  기존 `ConditionGroupRequest`(637번째 줄), `VALID_TIMEFRAMES`(engine.metrics, 이미 import됨),
  `get_krw_markets`(이미 import됨)
- Produces:
  - `CreateLiveStrategyRiskConfig`, `CreateLiveStrategyRequest` (Pydantic 모델)
  - `POST /api/v1/live-strategies` → 생성된 전략 dict(`status='draft'`) 반환, 검증 실패 시 400
  - 이후 Task 5·6이 재사용할 `_live_strategy_response(strategy, position, current_price)` /
    `_open_position_summary(position, current_price)` 헬퍼

**먼저** `backend/main.py` 상단 import를 수정한다:

`import threading` 다음 줄에 `import json`을 추가:
```python
import json
import threading
```

`from backend.grid_search_service import ...` 줄(59번째 줄) 다음에 추가:
```python
import trading.db as trading_db
import trading.position_manager as position_manager
import trading.upbit_client as upbit_client
```

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_backend.py` 상단 import 블록(11번째 줄, `from tests.signal_fixtures import
make_oscillating_df` 다음)에 추가:
```python
import trading.db as trading_db_module
```

`_client` 헬퍼(14번째 줄 근처)를 다음으로 교체:
```python
def _client(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    monkeypatch.setattr(trading_db_module, "DB_PATH", tmp_path / "trading.db")
    return TestClient(app)
```

`_run_request` 함수(272번째 줄 근처) 바로 다음에 추가:
```python
def _live_strategy_request(**overrides) -> dict:
    body = {
        "source_run_id": None,
        "market": "KRW-BTC",
        "timeframe": "minutes60",
        "buy_conditions": _VALID_BUY,
        "sell_conditions": _VALID_SELL,
        "risk_config": {
            "position_sizing_mode": "fixed",
            "position_sizing_value": 100000,
            "max_position_per_market": 500000,
            "max_total_position": 2000000,
            "order_execution_mode": "market",
            "order_timeout_sec": 10,
            "manual_intervention_policy": "all_stop",
            "daily_loss_limit_pct": -5.0,
            "consecutive_loss_limit": 3,
        },
    }
    body.update(overrides)
    return body


def test_create_live_strategy_creates_draft(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])

    resp = client.post("/api/v1/live-strategies", json=_live_strategy_request())

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "draft"
    assert body["market"] == "KRW-BTC"
    assert body["timeframe"] == "minutes60"
    assert body["current_capital"] is None


def test_create_live_strategy_rejects_unknown_market(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])

    resp = client.post("/api/v1/live-strategies", json=_live_strategy_request(market="KRW-NOPE"))
    assert resp.status_code == 400


def test_create_live_strategy_rejects_invalid_timeframe(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])

    resp = client.post("/api/v1/live-strategies", json=_live_strategy_request(timeframe="not-a-timeframe"))
    assert resp.status_code == 400


def test_create_live_strategy_rejects_non_negative_daily_loss_limit(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    req = _live_strategy_request()
    req["risk_config"]["daily_loss_limit_pct"] = 5.0

    resp = client.post("/api/v1/live-strategies", json=req)
    assert resp.status_code == 400
```

- [x] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_backend.py -k "create_live_strategy" -v`
Expected: FAIL — 404 Not Found(라우트 없음)

- [x] **Step 3: 최소 구현 작성**

`backend/main.py` 파일 맨 끝(`delete_grid_search_job_endpoint` 함수 다음)에 추가:

```python
class CreateLiveStrategyRiskConfig(BaseModel):
    position_sizing_mode: Literal["fixed", "percent"]
    position_sizing_value: float
    max_position_per_market: float
    max_total_position: float
    order_execution_mode: Literal["market", "limit", "limit_timeout"]
    order_timeout_sec: int = 10
    manual_intervention_policy: Literal["all_stop", "acknowledge_and_continue"]
    daily_loss_limit_pct: float
    consecutive_loss_limit: int


class CreateLiveStrategyRequest(BaseModel):
    source_run_id: str | None = None
    market: str
    timeframe: str
    buy_conditions: ConditionGroupRequest
    sell_conditions: ConditionGroupRequest
    risk_config: CreateLiveStrategyRiskConfig


def _validate_live_strategy_request(req: CreateLiveStrategyRequest) -> list[str]:
    errors: list[str] = []
    if req.timeframe not in VALID_TIMEFRAMES:
        errors.append(f"지원하지 않는 봉데이터입니다: {req.timeframe}")
    krw_markets = {m["market"] for m in get_krw_markets()}
    if req.market not in krw_markets:
        errors.append(f"{req.market}은(는) 업비트 KRW 마켓 목록에 없습니다.")

    risk = req.risk_config
    if risk.position_sizing_value <= 0:
        errors.append("자금관리 값은 0보다 커야 합니다.")
    if risk.position_sizing_mode == "percent" and risk.position_sizing_value > 100:
        errors.append("퍼센트 자금관리 값은 100 이하여야 합니다.")
    if risk.max_position_per_market <= 0:
        errors.append("코인당 최대 포지션 금액은 0보다 커야 합니다.")
    if risk.max_total_position <= 0:
        errors.append("전체 최대 포지션 금액은 0보다 커야 합니다.")
    if risk.order_execution_mode == "limit_timeout" and risk.order_timeout_sec <= 0:
        errors.append("지정가+타임아웃 모드에서는 타임아웃 초가 0보다 커야 합니다.")
    if risk.daily_loss_limit_pct >= 0:
        errors.append("일일 손실 한도는 음수여야 합니다(예: -5.0).")
    if risk.consecutive_loss_limit <= 0:
        errors.append("연속 손실 한도는 0보다 커야 합니다.")
    return errors


def _open_position_summary(position: dict, current_price: float | None) -> dict:
    unrealized_pnl_pct = None
    if current_price is not None and position["entry_price"]:
        unrealized_pnl_pct = (current_price - position["entry_price"]) / position["entry_price"] * 100
    return {
        "entry_price": position["entry_price"],
        "entry_qty": position["entry_qty"],
        "entry_time": position["entry_time"],
        "unrealized_pnl_pct": unrealized_pnl_pct,
    }


def _live_strategy_response(strategy: dict, position: dict | None, current_price: float | None) -> dict:
    return {
        "id": strategy["id"],
        "market": strategy["market"],
        "timeframe": strategy["timeframe"],
        "status": strategy["status"],
        "current_capital": strategy["current_capital"],
        "created_at": strategy["created_at"],
        "approved_at": strategy["approved_at"],
        "started_at": strategy["started_at"],
        "stopped_at": strategy["stopped_at"],
        "open_position": _open_position_summary(position, current_price) if position else None,
    }


def _full_live_strategy_response(strategy_id: str) -> dict:
    strategy = trading_db.get_live_strategy(strategy_id)
    position = trading_db.get_open_position(strategy_id)
    current_price = None
    if position is not None:
        prices = get_current_prices([strategy["market"]])
        current_price = prices.get(strategy["market"])
    return _live_strategy_response(strategy, position, current_price)


@app.post("/api/v1/live-strategies")
def create_live_strategy_endpoint(req: CreateLiveStrategyRequest) -> dict:
    errors = _validate_live_strategy_request(req)
    if errors:
        raise HTTPException(status_code=400, detail=" / ".join(errors))

    strategy_id = trading_db.insert_live_strategy(
        source_run_id=req.source_run_id,
        market=req.market,
        timeframe=req.timeframe,
        buy_conditions_json=json.dumps(req.buy_conditions.model_dump()),
        sell_conditions_json=json.dumps(req.sell_conditions.model_dump()),
        risk_config_json=json.dumps(req.risk_config.model_dump()),
    )
    return _full_live_strategy_response(strategy_id)
```

- [x] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_backend.py -k "create_live_strategy" -v`
Expected: PASS (4 passed)

- [x] **Step 5: 전체 backend 테스트 회귀 확인**

Run: `python -m pytest tests/test_backend.py -v`
Expected: 전부 PASS

- [x] **Step 6: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "feat: 라이브 전략 draft 생성 API 추가"
```

---

## Task 5: `backend/main.py` — 라이브 전략 목록 조회 API

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_backend.py`

**Interfaces:**
- Consumes: Task 1의 `trading_db.list_live_strategies`, Task 4의 `_live_strategy_response`/
  `_open_position_summary`, 기존 `trading_db.get_open_position`, `get_current_prices`(이미 import됨)
- Produces: `GET /api/v1/live-strategies` → `list[dict]`(status 무관 전체, 각 행에
  `open_position` 포함)

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_backend.py`의 `test_create_live_strategy_rejects_non_negative_daily_loss_limit`
다음에 추가. `pytest.approx`를 쓰므로 파일 최상단 import 블록에 `import pytest`가 없으면
추가한다(현재 파일에 없음 — `from datetime import ...` 줄 앞에 추가):

```python
def test_list_live_strategies_returns_empty_when_none(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    resp = client.get("/api/v1/live-strategies")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_live_strategies_includes_open_position_summary(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    create_resp = client.post("/api/v1/live-strategies", json=_live_strategy_request())
    strategy_id = create_resp.json()["id"]
    trading_db_module.insert_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)
    _patch_get_current_prices(monkeypatch, {"KRW-BTC": 55_000_000.0})

    resp = client.get("/api/v1/live-strategies")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    position = body[0]["open_position"]
    assert position["entry_price"] == 50_000_000.0
    assert position["entry_qty"] == 0.01
    assert position["unrealized_pnl_pct"] == pytest.approx(10.0)


def test_list_live_strategies_open_position_is_null_when_no_position(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    client.post("/api/v1/live-strategies", json=_live_strategy_request())

    resp = client.get("/api/v1/live-strategies")

    assert resp.json()[0]["open_position"] is None
```

- [x] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_backend.py -k "list_live_strategies" -v`
Expected: FAIL — 404 Not Found

- [x] **Step 3: 최소 구현 작성**

Task 4에서 추가한 `create_live_strategy_endpoint` 함수 바로 다음에 추가:

```python
@app.get("/api/v1/live-strategies")
def list_live_strategies_endpoint() -> list[dict]:
    strategies = trading_db.list_live_strategies()
    positions = {s["id"]: trading_db.get_open_position(s["id"]) for s in strategies}
    open_markets = {s["market"] for s in strategies if positions[s["id"]] is not None}
    current_prices = get_current_prices(list(open_markets)) if open_markets else {}
    return [
        _live_strategy_response(s, positions[s["id"]], current_prices.get(s["market"]))
        for s in strategies
    ]
```

- [x] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_backend.py -k "list_live_strategies" -v`
Expected: PASS (3 passed)

- [x] **Step 5: 전체 backend 테스트 회귀 확인**

Run: `python -m pytest tests/test_backend.py -v`
Expected: 전부 PASS

- [x] **Step 6: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "feat: 라이브 전략 목록 조회 API 추가(열린 포지션 요약 포함)"
```

---

## Task 6: `backend/main.py` — 승인/일시정지/재개/중지 API

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_backend.py`

**Interfaces:**
- Consumes: Task 2의 `trading_db.approve_live_strategy` / `transition_live_strategy_status` /
  `stop_live_strategy_if_no_open_position`, Task 4의 `_full_live_strategy_response`,
  `position_manager.calculate_initial_capital`(이미 존재), `upbit_client.get_accounts`(이미
  존재), `trading_db.list_active_strategies`(이미 존재)
- Produces:
  - `POST /api/v1/live-strategies/{strategy_id}/approve` (async)
  - `POST /api/v1/live-strategies/{strategy_id}/pause`
  - `POST /api/v1/live-strategies/{strategy_id}/resume`
  - `POST /api/v1/live-strategies/{strategy_id}/stop`

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_backend.py`의 `test_list_live_strategies_open_position_is_null_when_no_position`
다음에 추가:

```python
def _accounts_with_krw_balance(balance: float):
    async def _fake(*args, **kwargs):
        return [{"currency": "KRW", "balance": str(balance), "locked": "0"}]
    return _fake


def test_approve_live_strategy_transitions_to_running(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    monkeypatch.setattr(backend_module.upbit_client, "get_accounts", _accounts_with_krw_balance(1_000_000))
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]

    resp = client.post(f"/api/v1/live-strategies/{strategy_id}/approve")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["current_capital"] == 100000.0


def test_approve_live_strategy_returns_404_for_missing(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    resp = client.post("/api/v1/live-strategies/does-not-exist/approve")
    assert resp.status_code == 404


def test_approve_live_strategy_returns_409_when_already_running(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    monkeypatch.setattr(backend_module.upbit_client, "get_accounts", _accounts_with_krw_balance(1_000_000))
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]
    client.post(f"/api/v1/live-strategies/{strategy_id}/approve")

    resp = client.post(f"/api/v1/live-strategies/{strategy_id}/approve")
    assert resp.status_code == 409


def test_approve_live_strategy_rejects_when_balance_insufficient(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    monkeypatch.setattr(backend_module.upbit_client, "get_accounts", _accounts_with_krw_balance(50000))
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]

    resp = client.post(f"/api/v1/live-strategies/{strategy_id}/approve")

    assert resp.status_code == 400
    assert trading_db_module.get_live_strategy(strategy_id)["status"] == "draft"


def test_approve_live_strategy_sums_other_running_strategies_capital(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(
        backend_module, "get_krw_markets",
        lambda: [{"market": "KRW-BTC"}, {"market": "KRW-ETH"}],
    )
    monkeypatch.setattr(backend_module.upbit_client, "get_accounts", _accounts_with_krw_balance(150000))
    first_id = client.post(
        "/api/v1/live-strategies", json=_live_strategy_request(market="KRW-BTC"),
    ).json()["id"]
    assert client.post(f"/api/v1/live-strategies/{first_id}/approve").status_code == 200

    second_id = client.post(
        "/api/v1/live-strategies", json=_live_strategy_request(market="KRW-ETH"),
    ).json()["id"]
    resp = client.post(f"/api/v1/live-strategies/{second_id}/approve")

    assert resp.status_code == 400
    assert trading_db_module.get_live_strategy(second_id)["status"] == "draft"


def test_pause_and_resume_live_strategy(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    monkeypatch.setattr(backend_module.upbit_client, "get_accounts", _accounts_with_krw_balance(1_000_000))
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]
    client.post(f"/api/v1/live-strategies/{strategy_id}/approve")

    pause_resp = client.post(f"/api/v1/live-strategies/{strategy_id}/pause")
    assert pause_resp.status_code == 200
    assert pause_resp.json()["status"] == "paused"

    resume_resp = client.post(f"/api/v1/live-strategies/{strategy_id}/resume")
    assert resume_resp.status_code == 200
    assert resume_resp.json()["status"] == "running"


def test_pause_live_strategy_returns_409_when_not_running(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]

    resp = client.post(f"/api/v1/live-strategies/{strategy_id}/pause")
    assert resp.status_code == 409


def test_resume_live_strategy_returns_409_when_not_paused(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]

    resp = client.post(f"/api/v1/live-strategies/{strategy_id}/resume")
    assert resp.status_code == 409


def test_stop_live_strategy_succeeds_when_no_open_position(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    monkeypatch.setattr(backend_module.upbit_client, "get_accounts", _accounts_with_krw_balance(1_000_000))
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]
    client.post(f"/api/v1/live-strategies/{strategy_id}/approve")

    resp = client.post(f"/api/v1/live-strategies/{strategy_id}/stop")
    assert resp.status_code == 200
    assert resp.json()["status"] == "stopped"


def test_stop_live_strategy_rejects_when_open_position_exists(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    monkeypatch.setattr(backend_module.upbit_client, "get_accounts", _accounts_with_krw_balance(1_000_000))
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]
    client.post(f"/api/v1/live-strategies/{strategy_id}/approve")
    trading_db_module.insert_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)

    resp = client.post(f"/api/v1/live-strategies/{strategy_id}/stop")

    assert resp.status_code == 400
    assert trading_db_module.get_live_strategy(strategy_id)["status"] == "running"


def test_stop_live_strategy_returns_409_when_already_stopped(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]
    client.post(f"/api/v1/live-strategies/{strategy_id}/stop")

    resp = client.post(f"/api/v1/live-strategies/{strategy_id}/stop")
    assert resp.status_code == 409
```

- [x] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_backend.py -k "approve_live_strategy or pause_live_strategy or resume_live_strategy or stop_live_strategy" -v`
Expected: FAIL — 404 Not Found(라우트 없음)

- [x] **Step 3: 최소 구현 작성**

`list_live_strategies_endpoint` 함수 다음에 추가:

```python
@app.post("/api/v1/live-strategies/{strategy_id}/approve")
async def approve_live_strategy_endpoint(strategy_id: str) -> dict:
    strategy = trading_db.get_live_strategy(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="해당 id의 라이브 전략을 찾을 수 없습니다")
    if strategy["status"] != "draft":
        raise HTTPException(status_code=409, detail="draft 상태의 전략만 승인할 수 있습니다")

    risk_config = json.loads(strategy["risk_config_json"])
    accounts = await upbit_client.get_accounts()
    krw_account = next((a for a in accounts if a["currency"] == "KRW"), None)
    available_balance = float(krw_account["balance"]) if krw_account else 0.0

    initial_capital = position_manager.calculate_initial_capital(risk_config, available_balance)
    running_capital_sum = sum(s["current_capital"] or 0.0 for s in trading_db.list_active_strategies())
    required = running_capital_sum + initial_capital
    if required > available_balance:
        raise HTTPException(
            status_code=400,
            detail=(
                f"가용 잔고 {available_balance:,.0f}원, 필요 자금 {required:,.0f}원"
                f"(기존 전략 {running_capital_sum:,.0f}원 + 신규 {initial_capital:,.0f}원)"
            ),
        )

    approved = trading_db.approve_live_strategy(strategy_id, initial_capital)
    if not approved:
        raise HTTPException(
            status_code=409,
            detail="승인 처리 중 전략 상태가 변경되었습니다. 새로고침 후 다시 시도하세요",
        )
    return _full_live_strategy_response(strategy_id)


@app.post("/api/v1/live-strategies/{strategy_id}/pause")
def pause_live_strategy_endpoint(strategy_id: str) -> dict:
    if trading_db.get_live_strategy(strategy_id) is None:
        raise HTTPException(status_code=404, detail="해당 id의 라이브 전략을 찾을 수 없습니다")
    if not trading_db.transition_live_strategy_status(strategy_id, "running", "paused"):
        raise HTTPException(status_code=409, detail="running 상태의 전략만 일시정지할 수 있습니다")
    return _full_live_strategy_response(strategy_id)


@app.post("/api/v1/live-strategies/{strategy_id}/resume")
def resume_live_strategy_endpoint(strategy_id: str) -> dict:
    if trading_db.get_live_strategy(strategy_id) is None:
        raise HTTPException(status_code=404, detail="해당 id의 라이브 전략을 찾을 수 없습니다")
    if not trading_db.transition_live_strategy_status(strategy_id, "paused", "running"):
        raise HTTPException(status_code=409, detail="paused 상태의 전략만 재개할 수 있습니다")
    return _full_live_strategy_response(strategy_id)


@app.post("/api/v1/live-strategies/{strategy_id}/stop")
def stop_live_strategy_endpoint(strategy_id: str) -> dict:
    strategy = trading_db.get_live_strategy(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="해당 id의 라이브 전략을 찾을 수 없습니다")
    if strategy["status"] == "stopped":
        raise HTTPException(status_code=409, detail="이미 중지된 전략입니다")
    if not trading_db.stop_live_strategy_if_no_open_position(strategy_id):
        raise HTTPException(
            status_code=400,
            detail="열린 포지션이 있어 중지할 수 없습니다. 먼저 포지션을 정리하세요",
        )
    return _full_live_strategy_response(strategy_id)
```

- [x] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_backend.py -k "approve_live_strategy or pause_live_strategy or resume_live_strategy or stop_live_strategy" -v`
Expected: PASS (11 passed)

- [x] **Step 5: 전체 backend 테스트 회귀 확인**

Run: `python -m pytest tests/test_backend.py -v`
Expected: 전부 PASS

- [x] **Step 6: 전체 테스트 스위트 회귀 확인**

Run: `python -m pytest -q`
Expected: 전부 PASS (다른 모듈에 영향 없음 확인)

- [x] **Step 7: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "feat: 라이브 전략 승인/일시정지/재개/중지 API 추가"
```

---

## Task 7: 프론트엔드 — 타입 + API 클라이언트

**Files:**
- Create: `frontend/lib/types/liveStrategies.ts`
- Create: `frontend/lib/api/liveStrategies.ts`

**Interfaces:**
- Consumes: `frontend/lib/api/client.ts`의 `apiFetch`, `frontend/lib/types/strategy.ts`의
  `ConditionGroup`
- Produces: `LiveStrategy`, `LiveStrategyRiskConfig`, `CreateLiveStrategyRequest`,
  `BacktestConfig` 타입 + `getBacktestConfig`, `createLiveStrategy`, `getLiveStrategies`,
  `approveLiveStrategy`, `pauseLiveStrategy`, `resumeLiveStrategy`, `stopLiveStrategy` 함수
  (Task 8·9가 그대로 소비)

이 저장소는 프론트엔드 자동테스트 관례가 없으므로, 이 태스크는 파일 작성 + 타입체크로
검증한다(Task 10에서 전체 플로우를 dev 서버로 수동 검증).

- [x] **Step 1: 타입 파일 작성**

`frontend/lib/types/liveStrategies.ts`:
```typescript
import type { ConditionGroup } from '@/lib/types/strategy';

export type LiveStrategyStatus = 'draft' | 'running' | 'paused' | 'stopped';
export type PositionSizingMode = 'fixed' | 'percent';
export type OrderExecutionMode = 'market' | 'limit' | 'limit_timeout';
export type ManualInterventionPolicy = 'all_stop' | 'acknowledge_and_continue';

export interface LiveStrategyRiskConfig {
  position_sizing_mode: PositionSizingMode;
  position_sizing_value: number;
  max_position_per_market: number;
  max_total_position: number;
  order_execution_mode: OrderExecutionMode;
  order_timeout_sec: number;
  manual_intervention_policy: ManualInterventionPolicy;
  daily_loss_limit_pct: number;
  consecutive_loss_limit: number;
}

export interface CreateLiveStrategyRequest {
  source_run_id: string | null;
  market: string;
  timeframe: string;
  buy_conditions: ConditionGroup;
  sell_conditions: ConditionGroup;
  risk_config: LiveStrategyRiskConfig;
}

export interface LiveStrategyOpenPosition {
  entry_price: number;
  entry_qty: number;
  entry_time: string;
  unrealized_pnl_pct: number | null;
}

export interface LiveStrategy {
  id: string;
  market: string;
  timeframe: string;
  status: LiveStrategyStatus;
  current_capital: number | null;
  created_at: string;
  approved_at: string | null;
  started_at: string | null;
  stopped_at: string | null;
  open_position: LiveStrategyOpenPosition | null;
}

export interface BacktestConfig {
  market: string;
  timeframe: string;
  buy_conditions: ConditionGroup;
  sell_conditions: ConditionGroup;
}
```

- [x] **Step 2: API 클라이언트 함수 작성**

`frontend/lib/api/liveStrategies.ts`:
```typescript
import { apiFetch } from './client';
import type {
  BacktestConfig,
  CreateLiveStrategyRequest,
  LiveStrategy,
} from '@/lib/types/liveStrategies';

export function getBacktestConfig(runId: string): Promise<BacktestConfig> {
  return apiFetch<BacktestConfig>(`/api/v1/backtests/${runId}/config`);
}

export function createLiveStrategy(req: CreateLiveStrategyRequest): Promise<LiveStrategy> {
  return apiFetch<LiveStrategy>('/api/v1/live-strategies', {
    method: 'POST',
    body: JSON.stringify(req),
  });
}

export function getLiveStrategies(): Promise<LiveStrategy[]> {
  return apiFetch<LiveStrategy[]>('/api/v1/live-strategies');
}

export function approveLiveStrategy(id: string): Promise<LiveStrategy> {
  return apiFetch<LiveStrategy>(`/api/v1/live-strategies/${id}/approve`, { method: 'POST' });
}

export function pauseLiveStrategy(id: string): Promise<LiveStrategy> {
  return apiFetch<LiveStrategy>(`/api/v1/live-strategies/${id}/pause`, { method: 'POST' });
}

export function resumeLiveStrategy(id: string): Promise<LiveStrategy> {
  return apiFetch<LiveStrategy>(`/api/v1/live-strategies/${id}/resume`, { method: 'POST' });
}

export function stopLiveStrategy(id: string): Promise<LiveStrategy> {
  return apiFetch<LiveStrategy>(`/api/v1/live-strategies/${id}/stop`, { method: 'POST' });
}
```

- [x] **Step 3: 타입체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음(exit code 0)

- [x] **Step 4: 커밋**

```bash
git add frontend/lib/types/liveStrategies.ts frontend/lib/api/liveStrategies.ts
git commit -m "feat: 라이브 전략 프론트엔드 타입/API 클라이언트 추가"
```

---

## Task 8: 프론트엔드 — draft 생성 플로우 (진입 버튼 + 생성 폼)

**Files:**
- Create: `frontend/components/GoLiveButton.tsx`
- Create: `frontend/components/NewLiveStrategyPage.tsx`
- Create: `frontend/app/live-strategies/new/page.tsx`
- Modify: `frontend/app/backtests/[runId]/page.tsx`

**Interfaces:**
- Consumes: Task 7의 `getBacktestConfig`, `createLiveStrategy`, `LiveStrategyRiskConfig`,
  `BacktestConfig`, `OrderExecutionMode`, `PositionSizingMode`, `ManualInterventionPolicy`.
  기존 `@/components/ui/button`, `@/components/ui/input`, `@/components/ui/select`,
  `@/lib/api/client`(`ApiError`), `@/lib/format`(`formatTimeframe`),
  `@/lib/ui-classes`(`SECTION_HEADER_CLASS`)
- Produces: `/live-strategies/new?source_run_id={runId}` 경로, `GoLiveButton` 컴포넌트
  (Task 9는 이 태스크에 의존하지 않음 — 독립적으로 병행 가능)

- [x] **Step 1: 백테스트 상세 페이지에 진입 버튼 추가**

`frontend/components/GoLiveButton.tsx` 생성:
```tsx
import Link from 'next/link';
import { Rocket } from 'lucide-react';
import { Button } from '@/components/ui/button';

export default function GoLiveButton({ runId }: { runId: string }) {
  return (
    <Button variant="outline" size="sm" render={<Link href={`/live-strategies/new?source_run_id=${runId}`} />}>
      <Rocket className="size-3.5" />
      이 전략으로 실매매 시작
    </Button>
  );
}
```

`frontend/app/backtests/[runId]/page.tsx`의 import 블록에 추가:
```tsx
import GoLiveButton from '@/components/GoLiveButton';
```
(`RefreshBacktestButton` import 다음 줄)

같은 파일의 `<RefreshBacktestButton runId={params.runId} />` 다음 줄에 추가:
```tsx
        <GoLiveButton runId={params.runId} />
```

- [x] **Step 2: draft 생성 폼 페이지 작성**

`frontend/components/NewLiveStrategyPage.tsx`:
```tsx
'use client';

import { useEffect, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { ApiError } from '@/lib/api/client';
import { createLiveStrategy, getBacktestConfig } from '@/lib/api/liveStrategies';
import type {
  BacktestConfig,
  LiveStrategyRiskConfig,
  ManualInterventionPolicy,
  OrderExecutionMode,
  PositionSizingMode,
} from '@/lib/types/liveStrategies';
import { formatTimeframe } from '@/lib/format';
import { SECTION_HEADER_CLASS } from '@/lib/ui-classes';

const DEFAULT_RISK_CONFIG: LiveStrategyRiskConfig = {
  position_sizing_mode: 'fixed',
  position_sizing_value: 100000,
  max_position_per_market: 500000,
  max_total_position: 2000000,
  order_execution_mode: 'limit_timeout',
  order_timeout_sec: 10,
  manual_intervention_policy: 'all_stop',
  daily_loss_limit_pct: -5,
  consecutive_loss_limit: 3,
};

export default function NewLiveStrategyPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const sourceRunId = searchParams.get('source_run_id');

  const [config, setConfig] = useState<BacktestConfig | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [riskConfig, setRiskConfig] = useState<LiveStrategyRiskConfig>(DEFAULT_RISK_CONFIG);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!sourceRunId) {
      setLoadError('source_run_id가 없습니다. 백테스트 상세 페이지에서 다시 시작하세요.');
      return;
    }
    getBacktestConfig(sourceRunId)
      .then(setConfig)
      .catch((err) => setLoadError(err instanceof ApiError ? err.message : '백테스트 설정을 불러오지 못했습니다.'));
  }, [sourceRunId]);

  function updateRiskConfig<K extends keyof LiveStrategyRiskConfig>(key: K, value: LiveStrategyRiskConfig[K]) {
    setRiskConfig((prev) => ({ ...prev, [key]: value }));
  }

  async function handleSubmit() {
    if (!config || !sourceRunId) return;
    setSubmitError(null);
    setSubmitting(true);
    try {
      await createLiveStrategy({
        source_run_id: sourceRunId,
        market: config.market,
        timeframe: config.timeframe,
        buy_conditions: config.buy_conditions,
        sell_conditions: config.sell_conditions,
        risk_config: riskConfig,
      });
      router.push('/live-strategies');
    } catch (err) {
      setSubmitError(err instanceof ApiError ? err.message : '전략 생성 중 오류가 발생했습니다.');
    } finally {
      setSubmitting(false);
    }
  }

  if (loadError) return <p className="text-sm text-destructive">{loadError}</p>;
  if (!config) return <p className="text-sm text-muted-foreground">불러오는 중...</p>;

  return (
    <div className="max-w-4xl space-y-6">
      <div className="rounded-xl border p-4">
        <div className={SECTION_HEADER_CLASS}>대상 전략 (백테스트에서 그대로 승계)</div>
        <div className="p-3 text-sm">
          <p>{config.market} · {formatTimeframe(config.timeframe)}</p>
          <p className="mt-1 text-xs text-muted-foreground">
            매수/매도 조건은 백테스트 상세 페이지의 조건과 100% 동일하게 적용됩니다.
          </p>
        </div>
      </div>

      <div className="space-y-4 rounded-xl border p-6 shadow-sm">
        <div className={SECTION_HEADER_CLASS}>자금관리</div>
        <div className="grid grid-cols-1 gap-4 p-3 sm:grid-cols-2">
          <div>
            <label className="mb-1.5 block text-sm font-medium">방식</label>
            <Select
              value={riskConfig.position_sizing_mode}
              onValueChange={(v) => v !== null && updateRiskConfig('position_sizing_mode', v as PositionSizingMode)}
            >
              <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="fixed">고정금액</SelectItem>
                <SelectItem value="percent">계좌잔고 비율(%)</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-medium">
              {riskConfig.position_sizing_mode === 'fixed' ? '금액(원)' : '비율(%)'}
            </label>
            <Input
              type="number" min={0}
              value={riskConfig.position_sizing_value}
              onChange={(e) => updateRiskConfig('position_sizing_value', Number(e.target.value))}
            />
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-medium">코인당 최대 포지션(원)</label>
            <Input
              type="number" min={0}
              value={riskConfig.max_position_per_market}
              onChange={(e) => updateRiskConfig('max_position_per_market', Number(e.target.value))}
            />
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-medium">전체 최대 포지션(원)</label>
            <Input
              type="number" min={0}
              value={riskConfig.max_total_position}
              onChange={(e) => updateRiskConfig('max_total_position', Number(e.target.value))}
            />
          </div>
        </div>

        <div className={SECTION_HEADER_CLASS}>주문 실행</div>
        <div className="grid grid-cols-1 gap-4 p-3 sm:grid-cols-2">
          <div>
            <label className="mb-1.5 block text-sm font-medium">방식</label>
            <Select
              value={riskConfig.order_execution_mode}
              onValueChange={(v) => v !== null && updateRiskConfig('order_execution_mode', v as OrderExecutionMode)}
            >
              <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="market">시장가</SelectItem>
                <SelectItem value="limit">지정가</SelectItem>
                <SelectItem value="limit_timeout">지정가+타임아웃</SelectItem>
              </SelectContent>
            </Select>
          </div>
          {riskConfig.order_execution_mode === 'limit_timeout' && (
            <div>
              <label className="mb-1.5 block text-sm font-medium">타임아웃(초)</label>
              <Input
                type="number" min={1}
                value={riskConfig.order_timeout_sec}
                onChange={(e) => updateRiskConfig('order_timeout_sec', Number(e.target.value))}
              />
            </div>
          )}
        </div>

        <div className={SECTION_HEADER_CLASS}>서킷브레이커 / 수동개입</div>
        <div className="grid grid-cols-1 gap-4 p-3 sm:grid-cols-2">
          <div>
            <label className="mb-1.5 block text-sm font-medium">일일 손실 한도(%)</label>
            <Input
              type="number" max={0}
              value={riskConfig.daily_loss_limit_pct}
              onChange={(e) => updateRiskConfig('daily_loss_limit_pct', Number(e.target.value))}
            />
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-medium">연속 손실 한도(회)</label>
            <Input
              type="number" min={1}
              value={riskConfig.consecutive_loss_limit}
              onChange={(e) => updateRiskConfig('consecutive_loss_limit', Number(e.target.value))}
            />
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-medium">수동개입 감지 시 정책</label>
            <Select
              value={riskConfig.manual_intervention_policy}
              onValueChange={(v) => v !== null && updateRiskConfig('manual_intervention_policy', v as ManualInterventionPolicy)}
            >
              <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all_stop">전체 정지</SelectItem>
                <SelectItem value="acknowledge_and_continue">인지 후 계속</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>

        {submitError && <p className="text-sm text-destructive">{submitError}</p>}
        <Button onClick={handleSubmit} disabled={submitting}>
          {submitting ? '생성 중...' : '전략 만들기 (draft)'}
        </Button>
      </div>
    </div>
  );
}
```

`frontend/app/live-strategies/new/page.tsx`:
```tsx
import { Suspense } from 'react';
import NewLiveStrategyPage from '@/components/NewLiveStrategyPage';

export default function Page() {
  return (
    <div>
      <h1 className="mb-4 text-lg font-semibold">라이브 전략 만들기</h1>
      <Suspense fallback={null}>
        <NewLiveStrategyPage />
      </Suspense>
    </div>
  );
}
```

- [x] **Step 3: 타입체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [x] **Step 4: 커밋**

```bash
git add frontend/components/GoLiveButton.tsx frontend/components/NewLiveStrategyPage.tsx \
        frontend/app/live-strategies/new/page.tsx frontend/app/backtests/\[runId\]/page.tsx
git commit -m "feat: 백테스트 상세 페이지에서 라이브 전략 draft 생성 플로우 추가"
```

---

## Task 9: 프론트엔드 — 라이브 전략 관리 페이지

**Files:**
- Create: `frontend/components/LiveStrategiesPage.tsx`
- Create: `frontend/app/live-strategies/page.tsx`

**Interfaces:**
- Consumes: Task 7의 `getLiveStrategies`, `approveLiveStrategy`, `pauseLiveStrategy`,
  `resumeLiveStrategy`, `stopLiveStrategy`, `LiveStrategy`. 기존
  `@/components/ui/{button,badge,card}`, `@/lib/api/client`(`ApiError`),
  `@/lib/format`(`formatTimeframe`)

- [x] **Step 1: 관리 페이지 컴포넌트 작성**

`frontend/components/LiveStrategiesPage.tsx`:
```tsx
'use client';

import { useCallback, useEffect, useState } from 'react';
import { ApiError } from '@/lib/api/client';
import {
  approveLiveStrategy,
  getLiveStrategies,
  pauseLiveStrategy,
  resumeLiveStrategy,
  stopLiveStrategy,
} from '@/lib/api/liveStrategies';
import type { LiveStrategy } from '@/lib/types/liveStrategies';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { formatTimeframe } from '@/lib/format';

const POLL_INTERVAL_MS = 5000;

function fmtPct(value: number): string {
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`;
}

export default function LiveStrategiesPage() {
  const [strategies, setStrategies] = useState<LiveStrategy[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [pendingId, setPendingId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await getLiveStrategies();
      setStrategies(data);
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : '전략 목록을 불러오지 못했습니다.');
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [refresh]);

  async function runAction(id: string, action: (id: string) => Promise<LiveStrategy>) {
    setActionError(null);
    setPendingId(id);
    try {
      await action(id);
      await refresh();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : '요청 처리 중 오류가 발생했습니다.');
    } finally {
      setPendingId(null);
    }
  }

  if (loadError) return <p className="text-sm text-destructive">{loadError}</p>;
  if (strategies.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        등록된 라이브 전략이 없습니다. 백테스트 상세 페이지에서 시작하세요.
      </p>
    );
  }

  return (
    <div className="space-y-4">
      {actionError && <p className="text-sm text-destructive">{actionError}</p>}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {strategies.map((s) => (
          <Card key={s.id}>
            <CardHeader>
              <CardTitle className="flex items-center justify-between">
                <span>{s.market} · {formatTimeframe(s.timeframe)}</span>
                <Badge variant={s.status === 'running' ? 'default' : 'secondary'}>{s.status}</Badge>
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {s.current_capital !== null && (
                <p className="text-sm">현재 자금: {Math.round(s.current_capital).toLocaleString()}원</p>
              )}
              {s.open_position && (
                <div className="rounded-md bg-muted/50 p-2 text-sm">
                  <p>열린 포지션: 진입가 {Math.round(s.open_position.entry_price).toLocaleString()}</p>
                  <p>
                    수량 {s.open_position.entry_qty} · 손익{' '}
                    {s.open_position.unrealized_pnl_pct !== null ? fmtPct(s.open_position.unrealized_pnl_pct) : '-'}
                  </p>
                </div>
              )}
              <div className="flex flex-wrap gap-2 pt-2">
                {s.status === 'draft' && (
                  <>
                    <Button size="sm" disabled={pendingId === s.id} onClick={() => runAction(s.id, approveLiveStrategy)}>
                      승인
                    </Button>
                    <Button size="sm" variant="outline" disabled={pendingId === s.id} onClick={() => runAction(s.id, stopLiveStrategy)}>
                      취소
                    </Button>
                  </>
                )}
                {s.status === 'running' && (
                  <>
                    <Button size="sm" variant="outline" disabled={pendingId === s.id} onClick={() => runAction(s.id, pauseLiveStrategy)}>
                      일시정지
                    </Button>
                    <Button size="sm" variant="destructive" disabled={pendingId === s.id} onClick={() => runAction(s.id, stopLiveStrategy)}>
                      중지
                    </Button>
                  </>
                )}
                {s.status === 'paused' && (
                  <>
                    <Button size="sm" disabled={pendingId === s.id} onClick={() => runAction(s.id, resumeLiveStrategy)}>
                      재개
                    </Button>
                    <Button size="sm" variant="destructive" disabled={pendingId === s.id} onClick={() => runAction(s.id, stopLiveStrategy)}>
                      중지
                    </Button>
                  </>
                )}
                {s.status === 'stopped' && (
                  <p className="text-xs text-muted-foreground">
                    중지됨{s.stopped_at ? ` (${s.stopped_at})` : ''}
                  </p>
                )}
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
```

`frontend/app/live-strategies/page.tsx`:
```tsx
import LiveStrategiesPage from '@/components/LiveStrategiesPage';

export default function Page() {
  return (
    <div>
      <h1 className="mb-4 text-lg font-semibold">라이브 전략 관리</h1>
      <LiveStrategiesPage />
    </div>
  );
}
```

- [x] **Step 2: 타입체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [x] **Step 3: 커밋**

```bash
git add frontend/components/LiveStrategiesPage.tsx frontend/app/live-strategies/page.tsx
git commit -m "feat: 라이브 전략 관리 페이지(목록+제어) 추가"
```

---

## Task 10: 내비게이션 탭 추가 + 전체 플로우 수동 검증

**Files:**
- Modify: `frontend/components/NavTabs.tsx`

**Interfaces:**
- Consumes: Task 1~9의 모든 산출물(엔드포인트 6개 + 페이지 2개 + 진입 버튼 1개)

- [x] **Step 1: 내비게이션 탭에 "라이브 전략" 추가**

`frontend/components/NavTabs.tsx`의 import 줄을 다음으로 교체:
```tsx
import { BarChart3, BookOpen, FlaskConical, Grid3x3, Rocket, Settings } from 'lucide-react';
```

`STEPS` 배열을 다음으로 교체:
```tsx
const STEPS = [
  { href: '/', title: '백테스트 설정', icon: Settings },
  { href: '/grid-search', title: 'Grid Search', icon: Grid3x3 },
  { href: '/backtests', title: '백테스트 결과', icon: FlaskConical },
  { href: '/live-strategies', title: '라이브 전략', icon: Rocket },
  { href: '/analysis', title: '분석', icon: BarChart3 },
  { href: '/guide', title: '지표 가이드', icon: BookOpen },
];
```

- [x] **Step 2: 타입체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [x] **Step 3: 커밋**

```bash
git add frontend/components/NavTabs.tsx
git commit -m "feat: 내비게이션에 라이브 전략 관리 탭 추가"
```

- [x] **Step 4: 전체 백엔드 테스트 스위트 최종 확인**

Run: `python -m pytest -q`
Expected: 전부 PASS

- [x] **Step 5: 수동 e2e 검증 (dev 서버)**

기존 dev 서버가 떠 있으면 재사용하고, 없으면 저장소 루트에서
`uvicorn backend.main:app --reload --port 8000`과 `cd frontend && npm run dev`를 각각
새 터미널에서 띄운다(`npm run build`는 실행하지 않는다 — 살아있는 `npm run dev`의 `.next`를
깨뜨린다).

브라우저에서 확인할 시나리오:
1. `/backtests`에서 아무 백테스트 결과나 상세 페이지로 이동 → "이 전략으로 실매매 시작"
   버튼이 보이는지 확인
2. 버튼 클릭 → `/live-strategies/new?source_run_id=...`로 이동, market/timeframe이
   올바르게 표시되는지 확인
3. risk_config 폼을 채우고 "전략 만들기 (draft)" 클릭 → `/live-strategies`로 이동,
   방금 만든 전략이 `draft` 배지로 카드에 나타나는지 확인
4. "승인" 클릭 → 성공하면 `running`으로 바뀌고 `current_capital`이 표시되는지 확인.
   **주의:** 이 액션은 실제 `upbit_client.get_accounts()`를 호출한다 — `.env`에 유효한
   Upbit API 키가 설정돼 있지 않으면 401/오류가 나는 게 정상이다(이 플랜은 API 연동
   자체를 새로 만들지 않는다). 키가 있다면 성공 응답과 실제 잔고 기반 초기자금 계산 결과를
   확인한다. 이 항목의 실거래 결합 검증은 로드맵의 다음 단계인 "소액 실전 테스트"에서
   별도로 수행한다.
5. "일시정지" → `paused`로 바뀌는지, "재개" → 다시 `running`으로 바뀌는지 확인
6. "중지" → 열린 포지션이 없는 상태에서는 성공, `data/trading.db`에 직접
   `INSERT INTO positions (...) VALUES (...)`로 열린 포지션을 하나 넣은 뒤 다시 "중지"를
   누르면 400 에러 메시지가 뜨고 상태가 안 바뀌는지 확인

문제 발견 시 해당 태스크로 돌아가 수정한다. 모두 정상이면 이 태스크를 완료 처리한다(별도
커밋 없음 — 검증 전용 태스크).
