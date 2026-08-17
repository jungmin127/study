# 라이브 전략 시드(자본) 변경 + TWR 수익률 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 포지션이 없을 때 라이브 전략의 자본(시드)을 사용자가 직접 증액/감액할 수 있게 하고, 변경 이력이 있어도 누적 수익률(%)이 시간가중수익률(TWR)로 정확히 계산되게 한다.

**Architecture:** 새 테이블 `capital_adjustments`에 조정 이력을 기록하고, 조정 시점을 경계로 거래를 구간으로 나눠 구간수익률을 복리로 연결하는 `_twr_pct()`를 도입한다. 전략 단위(`_strategy_metrics`)뿐 아니라 코인별 합산(`_market_metrics`)과 계좌 전체 합산(`get_journal_summary`)도 각 전략의 TWR%를 원금(baseline) 가중 평균으로 블렌딩해 반영한다 — 조정 이력이 없을 때는 기존 공식과 수학적으로 완전히 동일한 값을 내므로 하위 호환이 보장된다.

**Tech Stack:** FastAPI (Python), SQLite(`trading/db.py`), Next.js/React (TypeScript).

## Global Constraints

- 스펙 문서: `docs/superpowers/specs/2026-08-17-live-strategy-capital-adjustment-design.md`
- 시드 변경은 **포지션이 없을 때만** 허용 (`trading_db.get_open_position(id) is None`)
- 시드 변경은 `running`/`paused` 상태의 전략에만 허용
- **알려진 한계(이번 계획의 범위 밖, 스펙에도 명시)**: 일별 자본 곡선(`daily_performance` 기반 equity curve)과 MDD 계산은 청산 시에만 갱신되므로, 시드 변경 이벤트 자체는 그 곡선에 별도로 반영되지 않는다. 즉 시드를 증액/감액해도 차트상의 자산 곡선/MDD 수치에는 그 변동분이 즉시 나타나지 않고, 다음 청산부터 자연히 새 자본 규모가 반영된다. `cumulative_pnl_pct`(TWR) 수정만 이번 범위이고, 곡선/MDD 보정은 별도 스펙 대상이다.
- **브레인스토밍 중 결정된 확장 범위**: TWR 보정은 `_strategy_metrics()`(전략 단위)뿐 아니라 `_market_metrics()`(코인별 합산)와 `get_journal_summary()`(계좌 전체 합산)의 `cumulative_pnl_pct`에도 반영한다. 방법은 각 전략의 `cumulative_pnl_pct`(TWR)를 그 전략의 `baseline`으로 가중평균. 조정 이력이 전혀 없으면 이 가중평균은 기존 단순 계산(`cumulative_pnl / total_baseline * 100`)과 대수적으로 동일하다.

---

### Task 1: DB 레이어 — capital_adjustments 테이블 + CRUD

**Files:**
- Modify: `trading/db.py` (`_SCHEMA` 문자열, `TABLE_NAMES` 튜플, `delete_live_strategy()`)
- Test: `tests/test_trading_db.py`

**Interfaces:**
- Produces: `insert_capital_adjustment(live_strategy_id: str, previous_capital: float, new_capital: float) -> str` (adjustment id 반환), `list_capital_adjustments(live_strategy_id: str) -> list[dict]` (adjusted_at 오름차순, 각 dict는 `id`/`live_strategy_id`/`adjusted_at`/`previous_capital`/`new_capital`/`delta` 키를 가짐). Task 2/3/4가 이 두 함수를 사용한다.

- [ ] **Step 1: 실패하는 테스트 작성 — insert/list**

`tests/test_trading_db.py`에서 `from tests.trading_db_fixtures import insert_live_strategy` import문(181행 부근) 아래, 첫 CRUD 테스트들 근처에 다음 3개 테스트를 추가한다(파일 맨 끝, `test_delete_live_strategy_returns_false_for_missing_id` 뒤에 추가):

```python
def test_insert_capital_adjustment_persists_fields(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)

    adjustment_id = db.insert_capital_adjustment(strategy_id, 500_000.0, 800_000.0)

    rows = db.list_capital_adjustments(strategy_id)
    assert len(rows) == 1
    assert rows[0]["id"] == adjustment_id
    assert rows[0]["previous_capital"] == 500_000.0
    assert rows[0]["new_capital"] == 800_000.0
    assert rows[0]["delta"] == 300_000.0


def test_list_capital_adjustments_orders_ascending_by_adjusted_at(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    first_id = db.insert_capital_adjustment(strategy_id, 500_000.0, 1_000_000.0)
    second_id = db.insert_capital_adjustment(strategy_id, 1_000_000.0, 800_000.0)

    conn = db._connect()
    try:
        conn.execute(
            "UPDATE capital_adjustments SET adjusted_at = '2026-08-01 09:00:00' WHERE id = ?",
            (first_id,),
        )
        conn.execute(
            "UPDATE capital_adjustments SET adjusted_at = '2026-08-02 09:00:00' WHERE id = ?",
            (second_id,),
        )
        conn.commit()
    finally:
        conn.close()

    rows = db.list_capital_adjustments(strategy_id)

    assert [r["id"] for r in rows] == [first_id, second_id]


def test_list_capital_adjustments_returns_empty_for_strategy_with_none(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)

    assert db.list_capital_adjustments(strategy_id) == []
```

- [ ] **Step 2: 테스트 실행 → 실패 확인**

Run: `python -m pytest tests/test_trading_db.py -k capital_adjustment -v`
Expected: FAIL — `AttributeError: module 'trading.db' has no attribute 'insert_capital_adjustment'`

- [ ] **Step 3: 스키마에 테이블 추가**

`trading/db.py`의 `_SCHEMA` 문자열에서 `manual_intervention_events` 테이블 정의(127-134행) 바로 뒤, 닫는 `"""`(135행) 앞에 추가:

```sql

CREATE TABLE IF NOT EXISTS capital_adjustments (
    id                TEXT PRIMARY KEY,
    live_strategy_id  TEXT NOT NULL REFERENCES live_strategies(id),
    adjusted_at       TEXT NOT NULL DEFAULT (datetime('now')),
    previous_capital  REAL NOT NULL,
    new_capital       REAL NOT NULL,
    delta             REAL NOT NULL
);
```

- [ ] **Step 4: TABLE_NAMES에 추가**

`TABLE_NAMES` 튜플(17-25행)을 다음으로 교체:

```python
TABLE_NAMES = (
    "live_strategies",
    "positions",
    "orders",
    "signals",
    "daily_performance",
    "circuit_breaker_state",
    "manual_intervention_events",
    "capital_adjustments",
)
```

(`tests/test_trading_db.py::test_connect_creates_all_seven_tables`는 `set(db.TABLE_NAMES)`와 실제 테이블 집합을 비교하는 동적 assertion이라 이 변경만으로 자동으로 8개 테이블을 기대하게 된다 — 테스트 코드 자체는 손댈 필요 없다.)

- [ ] **Step 5: insert/list 함수 추가**

`trading/db.py`의 `delete_live_strategy` 함수(730행) 바로 앞에 추가:

```python
def insert_capital_adjustment(live_strategy_id: str, previous_capital: float, new_capital: float) -> str:
    adjustment_id = str(uuid.uuid4())
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO capital_adjustments "
            "(id, live_strategy_id, previous_capital, new_capital, delta) "
            "VALUES (?, ?, ?, ?, ?)",
            (adjustment_id, live_strategy_id, previous_capital, new_capital, new_capital - previous_capital),
        )
        conn.commit()
    finally:
        conn.close()
    return adjustment_id


def list_capital_adjustments(live_strategy_id: str) -> list[dict]:
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM capital_adjustments WHERE live_strategy_id = ? "
            "ORDER BY adjusted_at ASC, rowid ASC",
            (live_strategy_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


```

- [ ] **Step 6: 테스트 실행 → 통과 확인**

Run: `python -m pytest tests/test_trading_db.py -k capital_adjustment -v`
Expected: 3 passed

`tests/test_trading_db.py`의 `test_connect_creates_all_seven_tables`(13행)는 이제 8개 테이블을 검증하게 되므로, 이름이 실제 내용과 어긋나지 않도록 `test_connect_creates_all_tables`로 이름만 바꾼다(본문은 그대로 — `set(db.TABLE_NAMES)`와 비교하는 동적 assertion이라 로직 변경 불필요):

```python
def test_connect_creates_all_tables(monkeypatch, tmp_path):
```

Run: `python -m pytest tests/test_trading_db.py::test_connect_creates_all_tables -v`
Expected: PASS (8개 테이블 자동 반영)

- [ ] **Step 7: 삭제 시 capital_adjustments도 함께 지우도록 수정 (실패하는 테스트 먼저)**

`PRAGMA foreign_keys = ON`이 켜져 있어(`_connect()`, 187행), `capital_adjustments.live_strategy_id`가 `live_strategies(id)`를 참조하는 이상 이 행을 먼저 지우지 않으면 부모 삭제 시 FK 제약 위반이 난다. 기존 통합 삭제 테스트를 확장한다.

`tests/test_trading_db.py`의 `test_delete_live_strategy_removes_strategy_and_child_rows`(978-998행)를 다음으로 교체:

```python
def test_delete_live_strategy_removes_strategy_and_child_rows(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="stopped")
    position_id = db.insert_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)
    order_id = db.insert_order(
        strategy_id, position_id, "KRW-BTC", "buy", "market", None, None, 50_000_000.0,
    )
    db.insert_signal(strategy_id, "buy", "2026-08-17T00:00:00", "{}")
    db.upsert_daily_performance(
        strategy_id, "2026-08-17", 0.0, 0.0, 0, 0, 0, 100000.0, 100000.0,
    )
    db.upsert_circuit_breaker_state(strategy_id, "2026-08-17", 0, 0)
    db.insert_capital_adjustment(strategy_id, 100000.0, 200000.0)

    deleted = db.delete_live_strategy(strategy_id)

    assert deleted is True
    assert db.get_live_strategy(strategy_id) is None
    assert db.get_position(position_id) is None
    assert db.get_order_by_id(order_id) is None
    assert db.get_circuit_breaker_state(strategy_id) is None
    assert db.get_daily_performance(strategy_id, "2026-08-17") is None
    assert db.list_capital_adjustments(strategy_id) == []
```

- [ ] **Step 8: 테스트 실행 → FK 제약 위반으로 실패 확인**

Run: `python -m pytest tests/test_trading_db.py::test_delete_live_strategy_removes_strategy_and_child_rows -v`
Expected: FAIL — `sqlite3.IntegrityError: FOREIGN KEY constraint failed`

- [ ] **Step 9: delete_live_strategy에 삭제 라인 추가**

`trading/db.py`의 `delete_live_strategy` 함수(730-761행)를 다음으로 교체:

```python
def delete_live_strategy(live_strategy_id: str) -> bool:
    """stopped 상태의 라이브 전략을 자식 행까지 포함해 완전히 삭제한다. FK 제약
    (PRAGMA foreign_keys = ON)이 켜져 있어 부모(live_strategies)보다 자식 테이블을
    먼저 지워야 한다. 삭제 순서: signals(orders 참조) -> orders(position_id로 positions
    참조 + replaces_order_id로 같은 테이블을 자기참조하지만, 해당 전략의 orders를
    한 문장으로 전부 지우므로 자기참조로 인한 FK 위반 없음) -> positions ->
    daily_performance/circuit_breaker_state/capital_adjustments -> live_strategies.
    manual_intervention_events는 live_strategy_id를 FK로 참조하지 않으므로 건드리지
    않는다. status가 'stopped'가 아니면(또는 id가 없으면) 아무것도 지우지 않고
    False를 반환한다."""
    conn = _connect()
    try:
        exists = conn.execute(
            "SELECT 1 FROM live_strategies WHERE id = ? AND status = 'stopped'",
            (live_strategy_id,),
        ).fetchone()
        if exists is None:
            return False

        conn.execute("DELETE FROM signals WHERE live_strategy_id = ?", (live_strategy_id,))
        conn.execute("DELETE FROM orders WHERE live_strategy_id = ?", (live_strategy_id,))
        conn.execute("DELETE FROM positions WHERE live_strategy_id = ?", (live_strategy_id,))
        conn.execute("DELETE FROM daily_performance WHERE live_strategy_id = ?", (live_strategy_id,))
        conn.execute("DELETE FROM circuit_breaker_state WHERE live_strategy_id = ?", (live_strategy_id,))
        conn.execute("DELETE FROM capital_adjustments WHERE live_strategy_id = ?", (live_strategy_id,))
        cursor = conn.execute(
            "DELETE FROM live_strategies WHERE id = ? AND status = 'stopped'",
            (live_strategy_id,),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()
```

- [ ] **Step 10: 테스트 실행 → 통과 확인**

Run: `python -m pytest tests/test_trading_db.py -v`
Expected: 전체 PASS (기존 테스트 포함 회귀 없음)

- [ ] **Step 11: 커밋**

```bash
git add trading/db.py tests/test_trading_db.py
git commit -m "feat: capital_adjustments 테이블과 CRUD 함수 추가"
```

---

### Task 2: 백엔드 — 시드 변경 엔드포인트

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_backend.py`

**Interfaces:**
- Consumes: `trading_db.get_live_strategy`, `trading_db.get_open_position`, `trading_db.insert_capital_adjustment`, `trading_db.update_live_strategy_capital`(기존 함수, `trading/db.py:232`), `_full_live_strategy_response`(기존, `backend/main.py:1185`) — Task 1에서 만든 `insert_capital_adjustment(live_strategy_id: str, previous_capital: float, new_capital: float) -> str`을 사용.
- Produces: `PATCH /api/v1/live-strategies/{strategy_id}/capital` 엔드포인트. body `{"new_capital": number}`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_backend.py`의 `test_stop_live_strategy_rejects_when_open_position_exists` 테스트(855-865행) 뒤에 추가:

```python
def test_update_capital_succeeds_when_no_open_position(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    monkeypatch.setattr(backend_module.upbit_client, "get_accounts", _accounts_with_krw_balance(1_000_000))
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]
    client.post(f"/api/v1/live-strategies/{strategy_id}/approve")

    resp = client.patch(f"/api/v1/live-strategies/{strategy_id}/capital", json={"new_capital": 800000})

    assert resp.status_code == 200
    assert resp.json()["current_capital"] == 800000


def test_update_capital_rejects_when_open_position_exists(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    monkeypatch.setattr(backend_module.upbit_client, "get_accounts", _accounts_with_krw_balance(1_000_000))
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]
    client.post(f"/api/v1/live-strategies/{strategy_id}/approve")
    trading_db_module.insert_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)

    resp = client.patch(f"/api/v1/live-strategies/{strategy_id}/capital", json={"new_capital": 800000})

    assert resp.status_code == 400


def test_update_capital_rejects_non_positive_value(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    monkeypatch.setattr(backend_module.upbit_client, "get_accounts", _accounts_with_krw_balance(1_000_000))
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]
    client.post(f"/api/v1/live-strategies/{strategy_id}/approve")

    resp = client.patch(f"/api/v1/live-strategies/{strategy_id}/capital", json={"new_capital": 0})

    assert resp.status_code == 400


def test_update_capital_rejects_draft_status(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]

    resp = client.patch(f"/api/v1/live-strategies/{strategy_id}/capital", json={"new_capital": 800000})

    assert resp.status_code == 409


def test_update_capital_returns_404_for_missing_id(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    resp = client.patch("/api/v1/live-strategies/does-not-exist/capital", json={"new_capital": 800000})

    assert resp.status_code == 404
```

- [ ] **Step 2: 테스트 실행 → 실패 확인**

Run: `python -m pytest tests/test_backend.py -k update_capital -v`
Expected: FAIL — 404 Not Found (라우트 없음, TestClient가 405 또는 404 반환)

- [ ] **Step 3: 요청 모델 + 엔드포인트 구현**

`backend/main.py`에서 `stop_live_strategy_endpoint`(1317-1329행) 뒤, `delete_live_strategy_endpoint`(1332행) 앞에 추가:

```python
class UpdateLiveStrategyCapitalRequest(BaseModel):
    new_capital: float


@app.patch("/api/v1/live-strategies/{strategy_id}/capital")
def update_live_strategy_capital_endpoint(strategy_id: str, req: UpdateLiveStrategyCapitalRequest) -> dict:
    strategy = trading_db.get_live_strategy(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="해당 id의 라이브 전략을 찾을 수 없습니다")
    if strategy["status"] not in ("running", "paused"):
        raise HTTPException(status_code=409, detail="running 또는 paused 상태의 전략만 시드를 변경할 수 있습니다")
    if trading_db.get_open_position(strategy_id) is not None:
        raise HTTPException(status_code=400, detail="포지션 보유 중에는 시드를 변경할 수 없습니다")
    if req.new_capital <= 0:
        raise HTTPException(status_code=400, detail="시드는 0보다 커야 합니다")

    trading_db.insert_capital_adjustment(strategy_id, strategy["current_capital"], req.new_capital)
    trading_db.update_live_strategy_capital(strategy_id, req.new_capital)
    return _full_live_strategy_response(strategy_id)
```

- [ ] **Step 4: 테스트 실행 → 통과 확인**

Run: `python -m pytest tests/test_backend.py -k update_capital -v`
Expected: 5 passed

- [ ] **Step 5: 회귀 테스트**

Run: `python -m pytest tests/test_backend.py -k live_strateg -v`
Expected: 전체 PASS

- [ ] **Step 6: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "feat: 라이브 전략 시드 변경 PATCH 엔드포인트 추가"
```

---

### Task 3: TWR(시간가중수익률) 계산

**Files:**
- Modify: `backend/trading_analytics_service.py`
- Test: `tests/test_trading_analytics_service.py`

**Interfaces:**
- Consumes: Task 1의 `trading_db.list_capital_adjustments(live_strategy_id) -> list[dict]` (adjusted_at 오름차순, 각 dict에 `adjusted_at`/`new_capital` 키 포함).
- Produces: `_twr_pct(closed_positions: list[dict], baseline: float, adjustments: list[dict]) -> float` — `_strategy_metrics()`가 내부에서 사용. `_market_metrics()`/`get_journal_summary()`의 `cumulative_pnl_pct` 계산 방식도 함께 바뀐다(아래 참고).

- [ ] **Step 1: 실패하는 테스트 작성 — `_twr_pct` 순수 함수**

`tests/test_trading_analytics_service.py` 맨 위 import 블록에 `pytest` 추가(1행을 아래로 교체):

```python
import pytest
import pandas as pd
```

파일 맨 끝에 추가:

```python
def test_twr_pct_matches_simple_calc_when_no_adjustments():
    closed = [
        {"realized_pnl": 50_000.0, "exit_time": "2026-08-01 10:00:00"},
        {"realized_pnl": -20_000.0, "exit_time": "2026-08-02 10:00:00"},
    ]

    result = svc._twr_pct(closed, 500_000.0, [])

    assert result == pytest.approx((30_000.0 / 500_000.0) * 100.0)


def test_twr_pct_returns_zero_when_baseline_is_zero_and_no_adjustments():
    result = svc._twr_pct([], 0.0, [])
    assert result == 0.0


def test_twr_pct_chains_segment_returns_around_single_capital_increase():
    """50만 원 시작 -> +10%(55만) -> 50만 증액(105만) -> -5%(99.75만).
    TWR = (1.10 * 0.95) - 1 = +4.5%. 단순 계산(순손실 -2500 / 50만 = -0.5%)과는 다르다."""
    closed = [
        {"realized_pnl": 50_000.0, "exit_time": "2026-08-01 10:00:00"},
        {"realized_pnl": -52_500.0, "exit_time": "2026-08-03 10:00:00"},
    ]
    adjustments = [
        {"adjusted_at": "2026-08-02 09:00:00", "new_capital": 1_050_000.0},
    ]

    result = svc._twr_pct(closed, 500_000.0, adjustments)

    assert result == pytest.approx(4.5, abs=0.01)


def test_twr_pct_chains_multiple_adjustments_with_trades_in_each_segment():
    closed = [
        {"realized_pnl": 10_000.0, "exit_time": "2026-08-01 10:00:00"},   # 구간1: 100000 -> +10%
        {"realized_pnl": -6_000.0, "exit_time": "2026-08-05 10:00:00"},   # 구간2: 200000 -> -3%
        {"realized_pnl": 9_700.0, "exit_time": "2026-08-10 10:00:00"},    # 구간3: 194000 -> +5%
    ]
    adjustments = [
        {"adjusted_at": "2026-08-02 09:00:00", "new_capital": 200_000.0},
        {"adjusted_at": "2026-08-06 09:00:00", "new_capital": 194_000.0},
    ]

    result = svc._twr_pct(closed, 100_000.0, adjustments)

    expected = ((1.10) * (1 - 0.03) * (1.05) - 1) * 100.0
    assert result == pytest.approx(expected, abs=0.01)


def test_twr_pct_ignores_input_order_and_sorts_by_exit_time():
    """closed_positions가 exit_time 역순으로 들어와도 결과는 같아야 한다
    (list_closed_positions는 entry_time DESC로 반환하므로 이 정렬이 함수 내부 책임)."""
    closed_forward = [
        {"realized_pnl": 50_000.0, "exit_time": "2026-08-01 10:00:00"},
        {"realized_pnl": -52_500.0, "exit_time": "2026-08-03 10:00:00"},
    ]
    closed_reversed = list(reversed(closed_forward))
    adjustments = [{"adjusted_at": "2026-08-02 09:00:00", "new_capital": 1_050_000.0}]

    assert svc._twr_pct(closed_forward, 500_000.0, adjustments) == pytest.approx(
        svc._twr_pct(closed_reversed, 500_000.0, adjustments)
    )
```

- [ ] **Step 2: 테스트 실행 → 실패 확인**

Run: `python -m pytest tests/test_trading_analytics_service.py -k twr_pct -v`
Expected: FAIL — `AttributeError: module 'backend.trading_analytics_service' has no attribute '_twr_pct'`

- [ ] **Step 3: `_twr_pct` 구현**

`backend/trading_analytics_service.py`에서 `_strategy_baseline_capital` 함수(42-48행) 뒤, `_strategy_metrics` 함수(51행) 앞에 추가:

```python
def _twr_pct(closed_positions: list[dict], baseline: float, adjustments: list[dict]) -> float:
    """자본 조정 시점을 경계로 거래를 구간으로 나눠 구간수익률을 복리로 연결한다
    (시간가중수익률). 조정 이력이 없으면 결과는 (cumulative_pnl / baseline * 100)과
    수학적으로 동일하다. adjustments는 adjusted_at 오름차순이어야 한다
    (trading_db.list_capital_adjustments가 이미 그 순서로 반환)."""
    if not adjustments:
        pnl = sum(p["realized_pnl"] for p in closed_positions)
        return (pnl / baseline * 100.0) if baseline else 0.0

    positions_sorted = sorted(closed_positions, key=lambda p: p["exit_time"])
    factor = 1.0
    seg_start_capital = baseline
    cursor = 0
    for adj in adjustments:
        seg_pnl = 0.0
        while cursor < len(positions_sorted) and positions_sorted[cursor]["exit_time"] < adj["adjusted_at"]:
            seg_pnl += positions_sorted[cursor]["realized_pnl"]
            cursor += 1
        if seg_start_capital:
            factor *= 1 + seg_pnl / seg_start_capital
        seg_start_capital = adj["new_capital"]

    seg_pnl = sum(p["realized_pnl"] for p in positions_sorted[cursor:])
    if seg_start_capital:
        factor *= 1 + seg_pnl / seg_start_capital
    return (factor - 1) * 100.0
```

- [ ] **Step 4: 테스트 실행 → 통과 확인**

Run: `python -m pytest tests/test_trading_analytics_service.py -k twr_pct -v`
Expected: 5 passed

- [ ] **Step 5: `_strategy_metrics()`가 `_twr_pct` 사용하도록 수정**

`backend/trading_analytics_service.py`의 `_strategy_metrics` 함수(51-69행)를 다음으로 교체:

```python
def _strategy_metrics(strategy: dict) -> dict:
    closed = trading_db.list_closed_positions(strategy["id"])
    daily_rows = trading_db.list_daily_performance(strategy["id"])
    adjustments = trading_db.list_capital_adjustments(strategy["id"])
    baseline = _strategy_baseline_capital(strategy, daily_rows)

    cumulative_pnl = sum(p["realized_pnl"] for p in closed)
    cumulative_pnl_pct = _twr_pct(closed, baseline, adjustments)
    mdd_pct = _mdd_pct([row["ending_balance"] for row in daily_rows])
    win_rate_pct = _win_rate_pct(closed)

    return {
        "closed_positions": closed,
        "daily_rows": daily_rows,
        "baseline": baseline,
        "cumulative_pnl": cumulative_pnl,
        "cumulative_pnl_pct": cumulative_pnl_pct,
        "mdd_pct": mdd_pct,
        "win_rate_pct": win_rate_pct,
    }
```

- [ ] **Step 6: `_market_metrics()`와 `get_journal_summary()`의 합산 수익률도 TWR 가중평균으로 변경**

`backend/trading_analytics_service.py`의 `get_journal_summary` 함수(72-121행)를 다음으로 교체:

```python
def get_journal_summary() -> dict:
    strategies = [s for s in trading_db.list_live_strategies() if s["approved_at"] is not None]

    if not strategies:
        return {
            "cumulative_pnl": 0.0, "cumulative_pnl_pct": 0.0, "mdd_pct": 0.0,
            "win_rate_pct": 0.0, "equity_curve": [], "strategies": [],
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
    }
```

같은 파일의 `_market_metrics` 함수(165-205행)를 다음으로 교체:

```python
def _market_metrics(strategies: list[dict]) -> dict:
    """여러 live_strategy 행(같은 market, 서로 다른 timeframe·세대 포함)을 하나로 합친
    지표. 코인 단위 매매일지(달력/라인차트 포함)를 위해 _strategy_metrics를 전략별로
    구해 날짜별 realized_pnl을 합산한 뒤, baseline부터 날짜순으로 누적하며 그날의
    수익률(%)까지 함께 계산한다 — daily_performance에 이미 저장된 realized_pnl_pct는
    전략 단위 기준이라 코인 합산 관점에서는 재계산이 필요하다.
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
    }
```

- [ ] **Step 7: 회귀 테스트**

Run: `python -m pytest tests/test_trading_analytics_service.py -v`
Expected: 전체 PASS (기존 테스트는 조정 이력이 없는 경우만 다뤄서 값이 그대로 유지돼야 함)

- [ ] **Step 8: 통합 테스트 작성 — DB를 거쳐 TWR이 실제로 반영되는지 확인**

`tests/test_trading_analytics_service.py`의 `test_market_journal_returns_none_for_missing_market` 테스트(87-89행) 뒤에 추가:

```python
def test_market_journal_reflects_twr_after_capital_adjustment(monkeypatch, tmp_path):
    db = _fresh(monkeypatch, tmp_path)
    s1 = insert_live_strategy(db, market="KRW-BTC", status="draft")
    _approve(db, s1, 500_000.0)

    p1 = db.insert_position(s1, "KRW-BTC", 50_000_000.0, 0.01)
    db.close_position_row(p1, 55_000_000.0, 0.01, 50_000.0, 10.0, "take_profit")
    db.insert_capital_adjustment(s1, 550_000.0, 1_050_000.0)
    p2 = db.insert_position(s1, "KRW-BTC", 50_000_000.0, 0.021)
    db.close_position_row(p2, 47_500_000.0, 0.021, -52_500.0, -5.0, "stop_loss")

    conn = db._connect()
    try:
        rows = conn.execute(
            "SELECT id FROM positions WHERE live_strategy_id = ? ORDER BY rowid ASC", (s1,),
        ).fetchall()
        conn.execute("UPDATE positions SET exit_time = '2026-08-01 10:00:00' WHERE id = ?", (rows[0][0],))
        conn.execute("UPDATE positions SET exit_time = '2026-08-03 10:00:00' WHERE id = ?", (rows[1][0],))
        conn.execute(
            "UPDATE capital_adjustments SET adjusted_at = '2026-08-02 09:00:00' WHERE live_strategy_id = ?",
            (s1,),
        )
        conn.commit()
    finally:
        conn.close()

    journal = svc.get_market_journal("KRW-BTC")

    # TWR: (1.10 * 0.95) - 1 = 4.5%. 단순 계산(순손실 -2500 / 원금 500000 = -0.5%)과는 다르다.
    assert journal["cumulative_pnl_pct"] == pytest.approx(4.5, abs=0.01)
```

- [ ] **Step 9: 테스트 실행 → 통과 확인**

Run: `python -m pytest tests/test_trading_analytics_service.py -v`
Expected: 전체 PASS

- [ ] **Step 10: 커밋**

```bash
git add backend/trading_analytics_service.py tests/test_trading_analytics_service.py
git commit -m "feat: 자본 조정 반영한 시간가중수익률(TWR) 계산 도입"
```

---

### Task 4: API 응답에 자본 변경 이력 포함

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_backend.py`

**Interfaces:**
- Consumes: Task 1의 `trading_db.list_capital_adjustments`, 기존 `_to_utc_iso`(`backend/main.py:79`).
- Produces: `_live_strategy_response()` 응답에 `capital_adjustments: list[dict]` 필드 추가, 각 원소는 `{id, adjusted_at(UTC ISO), previous_capital, new_capital, delta}`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_backend.py`의 `test_list_live_strategies_includes_buy_sell_conditions_and_risk_config` 테스트 뒤에 추가:

```python
def test_list_live_strategies_includes_empty_capital_adjustments_by_default(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    client.post("/api/v1/live-strategies", json=_live_strategy_request())

    resp = client.get("/api/v1/live-strategies")

    assert resp.json()[0]["capital_adjustments"] == []


def test_list_live_strategies_includes_capital_adjustment_history(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    monkeypatch.setattr(backend_module.upbit_client, "get_accounts", _accounts_with_krw_balance(1_000_000))
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]
    client.post(f"/api/v1/live-strategies/{strategy_id}/approve")
    client.patch(f"/api/v1/live-strategies/{strategy_id}/capital", json={"new_capital": 800000})

    resp = client.get("/api/v1/live-strategies")

    adjustments = resp.json()[0]["capital_adjustments"]
    assert len(adjustments) == 1
    assert adjustments[0]["previous_capital"] == 100000
    assert adjustments[0]["new_capital"] == 800000
    assert adjustments[0]["delta"] == 700000
    assert adjustments[0]["adjusted_at"].endswith(("+00:00", "Z"))
```

- [ ] **Step 2: 테스트 실행 → 실패 확인**

Run: `python -m pytest tests/test_backend.py -k capital_adjustment -v`
Expected: FAIL — `KeyError: 'capital_adjustments'`

- [ ] **Step 3: `_live_strategy_response`에 필드 추가**

`backend/main.py`의 `_live_strategy_response` 함수(1167-1182행)를 다음으로 교체:

```python
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
        "buy_conditions": json.loads(strategy["buy_conditions_json"]),
        "sell_conditions": json.loads(strategy["sell_conditions_json"]),
        "risk_config": json.loads(strategy["risk_config_json"]),
        "capital_adjustments": [
            {
                "id": adj["id"],
                "adjusted_at": _to_utc_iso(adj["adjusted_at"]),
                "previous_capital": adj["previous_capital"],
                "new_capital": adj["new_capital"],
                "delta": adj["delta"],
            }
            for adj in trading_db.list_capital_adjustments(strategy["id"])
        ],
    }
```

- [ ] **Step 4: 테스트 실행 → 통과 확인**

Run: `python -m pytest tests/test_backend.py -k capital_adjustment -v`
Expected: 2 passed (이 파일 안에 Task 2의 `update_capital` 테스트도 `capital`을 포함하므로 `-k capital_adjustment`로 좁혀서 확인)

- [ ] **Step 5: 전체 회귀 테스트**

Run: `python -m pytest tests/test_backend.py -v`
Expected: 전체 PASS

- [ ] **Step 6: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "feat: 라이브 전략 응답에 자본 변경 이력 포함"
```

---

### Task 5: 프론트엔드 타입 + API 클라이언트

**Files:**
- Modify: `frontend/lib/types/liveStrategies.ts`, `frontend/lib/api/liveStrategies.ts`

**Interfaces:**
- Produces: `CapitalAdjustment` 타입, `LiveStrategy.capital_adjustments: CapitalAdjustment[]`, `updateLiveStrategyCapital(id: string, newCapital: number): Promise<LiveStrategy>`. Task 6이 이 타입과 함수를 사용한다.

- [ ] **Step 1: 타입 추가**

`frontend/lib/types/liveStrategies.ts`의 `LiveStrategy` 인터페이스(35-46행) 앞에 추가:

```typescript
export interface CapitalAdjustment {
  id: string;
  adjusted_at: string;
  previous_capital: number;
  new_capital: number;
  delta: number;
}

```

`LiveStrategy` 인터페이스를 다음으로 교체:

```typescript
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
  buy_conditions: ConditionGroup;
  sell_conditions: ConditionGroup;
  risk_config: LiveStrategyRiskConfig;
  capital_adjustments: CapitalAdjustment[];
}
```

- [ ] **Step 2: API 클라이언트 함수 추가**

`frontend/lib/api/liveStrategies.ts`의 `import type` 블록(2-6행)을 다음으로 교체:

```typescript
import type {
  BacktestConfig,
  CreateLiveStrategyRequest,
  LiveStrategy,
} from '@/lib/types/liveStrategies';
```

(변경 없음 — `LiveStrategy` 타입 자체가 새 필드를 이미 포함하므로 import 목록은 그대로 둔다.)

파일 맨 끝(`deleteLiveStrategy` 함수 뒤)에 추가:

```typescript

export function updateLiveStrategyCapital(id: string, newCapital: number): Promise<LiveStrategy> {
  return apiFetch<LiveStrategy>(`/api/v1/live-strategies/${id}/capital`, {
    method: 'PATCH',
    body: JSON.stringify({ new_capital: newCapital }),
  });
}
```

- [ ] **Step 3: 타입 체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 4: 커밋**

```bash
git add frontend/lib/types/liveStrategies.ts frontend/lib/api/liveStrategies.ts
git commit -m "feat: 자본 변경 이력 타입과 시드 변경 API 클라이언트 함수 추가"
```

---

### Task 6: 프론트엔드 UI — 시드 변경 버튼 + 이력 표시

**Files:**
- Modify: `frontend/components/LiveStrategiesPage.tsx`

**Interfaces:**
- Consumes: `LiveStrategy.capital_adjustments`(Task 5), `updateLiveStrategyCapital`(Task 5), `formatDateTime`(`frontend/lib/format.ts`, 시그니처: `(iso: string) => string`), `INPUT_CLASS`(`frontend/lib/ui-classes.ts`), `refresh`(컴포넌트 내부 기존 함수, `() => Promise<void>`).
- Produces: 없음 (리프 UI 변경).

- [ ] **Step 1: import 추가**

`LiveStrategiesPage.tsx` 상단 import 블록을 수정한다. 4행을 아래로 교체:

```typescript
import { Check, CircleHelp, Coins, Pause, Play, Square, Trash2, X } from 'lucide-react';
```

6-13행(liveStrategies API import)을 아래로 교체:

```typescript
import {
  approveLiveStrategy,
  deleteLiveStrategy,
  getLiveStrategies,
  pauseLiveStrategy,
  resumeLiveStrategy,
  stopLiveStrategy,
  updateLiveStrategyCapital,
} from '@/lib/api/liveStrategies';
```

`import {\n  Dialog,\n  DialogContent,\n  DialogHeader,\n  DialogTitle,\n  DialogTrigger,\n} from '@/components/ui/dialog';`(27-33행)를 아래로 교체:

```typescript
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
```

37행(`import { formatTimeframe } from '@/lib/format';`)을 아래로 교체:

```typescript
import { formatDateTime, formatTimeframe } from '@/lib/format';
import { INPUT_CLASS } from '@/lib/ui-classes';
```

- [ ] **Step 2: 시드 변경 다이얼로그 컴포넌트 추가**

`Stat` 함수 컴포넌트(현재 81-88행) 바로 뒤에 추가:

```tsx
function ChangeCapitalDialog({
  strategy,
  onChanged,
}: {
  strategy: LiveStrategy;
  onChanged: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit() {
    const newCapital = Number(value);
    if (!Number.isFinite(newCapital) || newCapital <= 0) {
      setError('0보다 큰 숫자를 입력하세요.');
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await updateLiveStrategyCapital(strategy.id, newCapital);
      await onChanged();
      setOpen(false);
      setValue('');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '시드 변경에 실패했습니다.');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) {
          setValue('');
          setError(null);
        }
      }}
    >
      <DialogTrigger
        type="button"
        className={buttonVariants({ variant: 'outline', size: 'icon-lg' })}
        aria-label="시드 변경"
        title="시드 변경"
      >
        <Coins />
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>시드 변경</DialogTitle>
        </DialogHeader>
        <div className="space-y-3 text-sm">
          <p>
            현재 자본:{' '}
            <span className="font-semibold tabular-nums">
              {strategy.current_capital !== null
                ? `${Math.round(strategy.current_capital).toLocaleString()}원`
                : '-'}
            </span>
          </p>
          <input
            type="number"
            inputMode="decimal"
            className={INPUT_CLASS}
            placeholder="새 시드 금액(원)"
            value={value}
            onChange={(e) => setValue(e.target.value)}
          />
          {error && <p className="text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)} disabled={submitting}>
            취소
          </Button>
          <Button onClick={handleSubmit} disabled={submitting}>
            확인
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```

- [ ] **Step 3: 카드에 시드 변경 버튼 배치**

정보 `<Dialog>` 블록(현재 147-190행)의 닫는 `</Dialog>` 바로 뒤, `{s.status === 'draft' && (` 앞에 추가:

```tsx
                {s.open_position === null && (s.status === 'running' || s.status === 'paused') && (
                  <ChangeCapitalDialog strategy={s} onChanged={refresh} />
                )}
```

- [ ] **Step 4: 정보 모달에 자본 변경 이력 섹션 추가**

정보 모달의 "리스크 관리" `<div>` 블록(현재 175-187행) 바로 뒤, `</div>`(모달 콘텐츠를 감싸는 `space-y-3` div의 닫는 태그, 현재 188행) 앞에 추가:

```tsx
                      <div>
                        <p className="mb-1 font-medium text-muted-foreground">자본 변경 이력</p>
                        {s.capital_adjustments.length === 0 ? (
                          <p className="rounded-md bg-muted/50 p-2 text-xs text-muted-foreground">
                            변경 이력 없음
                          </p>
                        ) : (
                          <div className="space-y-1 rounded-md bg-muted/50 p-2">
                            {s.capital_adjustments.map((adj) => (
                              <div
                                key={adj.id}
                                className="flex flex-wrap items-baseline justify-between gap-x-2 text-xs"
                              >
                                <span className="text-muted-foreground">{formatDateTime(adj.adjusted_at)}</span>
                                <span className="tabular-nums">
                                  {Math.round(adj.previous_capital).toLocaleString()}원 →{' '}
                                  {Math.round(adj.new_capital).toLocaleString()}원
                                </span>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
```

- [ ] **Step 5: 타입 체크 + lint**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

Run: `cd frontend && npx next lint`
Expected: 경고 없음

만약 `Coins` 아이콘이 `lucide-react`에 없다는 타입 에러가 나면 `Wallet`로 교체하고 버튼 아이콘도 `<Wallet />`로 바꾼다.

- [ ] **Step 6: 수동 확인**

Run: `cd frontend && npm run dev` (이미 떠 있으면 생략)

브라우저에서 라이브 전략 관리 화면을 열고, `running` 또는 `paused` 상태이며 포지션이 없는 전략 카드에서:
1. 새로 추가된 동전 아이콘(시드 변경) 버튼이 보이는지 확인
2. 클릭 → 다이얼로그에 현재 자본이 표시되는지 확인
3. 새 금액을 입력하고 확인 → 카드의 "현재 자금" 값이 갱신되는지 확인
4. 정보(물음표) 버튼을 다시 눌러 "자본 변경 이력" 섹션에 방금 변경 내역이 "이전금액원 → 새금액원" 형태로 표시되는지 확인
5. 포지션이 있는 카드에는 시드 변경 버튼이 보이지 않는지 확인
6. 새 라이브 전략을 만들거나 승인하지 말 것 — 기존 전략으로만 확인. 실거래 자금이 걸린 전략의 시드를 실제로 바꾸지 말고, 취소로 다이얼로그를 닫아 확인만 하거나 테스트 값이 이미 알려진 경우에만 확인 후 원래 값으로 되돌릴 것.

- [ ] **Step 7: 커밋**

```bash
git add frontend/components/LiveStrategiesPage.tsx
git commit -m "feat: 라이브 전략 카드에 시드 변경 버튼과 변경 이력 표시 추가"
```
