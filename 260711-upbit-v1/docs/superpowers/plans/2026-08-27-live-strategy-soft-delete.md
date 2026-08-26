# 라이브 전략 소프트 삭제 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** "삭제" 버튼이 `live_strategies`와 그 자식 행(positions/orders/signals 등)을 하드
삭제하던 것을, `deleted_at` 타임스탬프만 채우는 소프트 삭제로 바꿔서 — 관리 목록에서는
사라지되 매매일지 집계에는 계속 남게 한다.

**Architecture:** `live_strategies`에 nullable `deleted_at TEXT` 컬럼을 추가한다. 새
`soft_delete_live_strategy()` DB 함수가 `UPDATE ... SET deleted_at=datetime('now')`만
실행하고 자식 테이블은 건드리지 않는다. 매매일지 집계
(`get_journal_summary`/`get_market_journal`)는 이미 `status`가 아니라 `approved_at`만
보므로 코드 변경이 필요 없다 — "라이브 전략 관리" 목록 엔드포인트 한 곳에만
`deleted_at IS NULL` 필터를 추가한다. 기존 `delete_live_strategy`(하드 삭제)는 그대로
남겨둔다.

**Tech Stack:** Python 3.11 / FastAPI / SQLite (WAL) / pytest / Next.js·TypeScript(변경
없음, 문구만 수정) — 기존 스택 그대로, 신규 의존성 없음.

## Global Constraints

- 스펙: `docs/superpowers/specs/2026-08-27-live-strategy-soft-delete-design.md`
- 프로덕션 DB(`data/trading.db`, AWS 서버)를 지우고 재생성할 수 없다 — 새 컬럼은 반드시
  `ALTER TABLE`로 추가한다(`_ensure_positions_entry_fee_column`과 동일 패턴), "DB 파일
  지우고 다시 시작하라"는 assert 패턴은 쓰지 않는다.
- 기존 `delete_live_strategy`(하드 삭제) 함수와 그 테스트는 삭제하지 않고 그대로
  둔다 — 새 함수를 추가하는 것이지 기존 함수를 바꾸는 게 아니다.
- "중지"(`stop_live_strategy_if_no_open_position`, `/live-strategies/{id}/stop`)는 이번
  계획에서 전혀 건드리지 않는다.
- 매매일지 집계 함수(`backend/trading_analytics_service.py`)는 이번 계획에서 코드를
  수정하지 않는다 — 이미 요구사항을 만족한다는 걸 회귀테스트로만 증명한다.

---

### Task 1: DB 스키마 — `deleted_at` 컬럼 + `soft_delete_live_strategy()`

**Files:**
- Modify: `trading/db.py:30-48` (`_SCHEMA`의 `live_strategies` CREATE TABLE)
- Modify: `trading/db.py:194-223` (마이그레이션 가드 함수 + `_connect()`)
- Modify: `trading/db.py:923-956` 부근 (`delete_live_strategy` 바로 뒤에 신규 함수 추가)
- Test: `tests/test_trading_db.py` (기존 delete 테스트들 바로 뒤, 980번대 줄 부근)

**Interfaces:**
- Produces: `trading.db.soft_delete_live_strategy(live_strategy_id: str) -> bool` —
  `status='stopped'`이고 아직 `deleted_at`이 NULL인 행에만 적용, 성공 시 True/실패
  (id 없음·status 불일치·이미 삭제됨) 시 False. `trading.db.get_live_strategy()`가
  반환하는 dict에 `deleted_at` 키가 추가됨(문자열 또는 None).

- [ ] **Step 1: `_SCHEMA`에 `deleted_at` 컬럼 추가**

`trading/db.py:30-48`의 `live_strategies` CREATE TABLE 블록에서 `baseline_qty REAL`
다음 줄에 추가:

```python
CREATE TABLE IF NOT EXISTS live_strategies (
    id                  TEXT PRIMARY KEY,
    source_run_id       TEXT,
    market              TEXT NOT NULL,
    timeframe           TEXT NOT NULL,
    buy_conditions_json TEXT NOT NULL,
    sell_conditions_json TEXT NOT NULL,
    risk_config_json    TEXT NOT NULL,
    current_capital     REAL,
    status              TEXT NOT NULL DEFAULT 'draft',
    manual_pause        INTEGER NOT NULL DEFAULT 0,
    last_processed_candle_time TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    approved_at         TEXT,
    started_at          TEXT,
    stopped_at          TEXT,
    baseline_qty        REAL,
    deleted_at           TEXT
);
```

- [ ] **Step 2: 기존 프로덕션 DB용 마이그레이션 가드 함수 추가**

`trading/db.py:194` 근처, `_ensure_positions_entry_fee_column` 함수 바로 뒤에 추가:

```python
def _ensure_live_strategies_deleted_at_column(conn: sqlite3.Connection) -> None:
    """CREATE TABLE IF NOT EXISTS는 이미 존재하는 live_strategies 테이블에 새 컬럼
    deleted_at을 추가하지 못한다. entry_fee와 동일한 이유로(AWS에서 실거래 중인
    프로덕션 DB라 파일을 지울 수 없음) ALTER TABLE로 직접 추가한다 — 기존 행은
    NULL로 채워지며, 이는 "아직 소프트 삭제되지 않음"이라는 올바른 기본값이다."""
    table_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='live_strategies'"
    ).fetchone() is not None
    if not table_exists:
        return
    columns = {row[1] for row in conn.execute("PRAGMA table_info('live_strategies')")}
    if "deleted_at" in columns:
        return
    conn.execute("ALTER TABLE live_strategies ADD COLUMN deleted_at TEXT")
    conn.commit()
```

`_connect()`(`trading/db.py:212-223`)에서 `_ensure_positions_entry_fee_column(conn)`
호출 바로 다음 줄에 추가:

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
        _ensure_live_strategies_deleted_at_column(conn)
        _initialized_paths.add(DB_PATH)
    return conn
```

- [ ] **Step 3: 실패하는 테스트 작성**

`tests/test_trading_db.py`의 `test_delete_live_strategy_returns_false_for_missing_id`
(약 1013-1016줄) 바로 뒤에 추가:

```python
def test_soft_delete_live_strategy_marks_deleted_at_and_keeps_child_rows(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="stopped")
    position_id = db.insert_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)
    order_id = db.insert_order(
        strategy_id, position_id, "KRW-BTC", "buy", "market", None, None, 50_000_000.0,
    )

    deleted = db.soft_delete_live_strategy(strategy_id)

    assert deleted is True
    strategy = db.get_live_strategy(strategy_id)
    assert strategy is not None
    assert strategy["deleted_at"] is not None
    assert db.get_position(position_id) is not None
    assert db.get_order_by_id(order_id) is not None


def test_soft_delete_live_strategy_rejects_non_stopped_status(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="running")

    deleted = db.soft_delete_live_strategy(strategy_id)

    assert deleted is False
    assert db.get_live_strategy(strategy_id)["deleted_at"] is None


def test_soft_delete_live_strategy_returns_false_for_missing_id(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)

    assert db.soft_delete_live_strategy("does-not-exist") is False


def test_soft_delete_live_strategy_returns_false_when_already_deleted(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="stopped")
    assert db.soft_delete_live_strategy(strategy_id) is True

    deleted_again = db.soft_delete_live_strategy(strategy_id)

    assert deleted_again is False
```

- [ ] **Step 4: 테스트 실행해서 실패 확인**

Run: `pytest tests/test_trading_db.py -k soft_delete_live_strategy -v`
Expected: FAIL with `AttributeError: module 'trading.db' has no attribute
'soft_delete_live_strategy'`

- [ ] **Step 5: `soft_delete_live_strategy()` 구현**

`trading/db.py:923-956`의 `delete_live_strategy` 함수 바로 뒤(957줄 근처, `orders`가
`upbit_uuid UNIQUE`를 갖는 등 순서 설명이 끝나는 지점)에 추가:

```python
def soft_delete_live_strategy(live_strategy_id: str) -> bool:
    """live_strategies 행과 positions/orders 등 자식 테이블은 그대로 두고 deleted_at만
    채운다 — "라이브 전략 관리" 목록(list_live_strategies_endpoint)에서는 사라지지만
    매매일지 집계(get_market_journal/get_journal_summary, approved_at IS NOT NULL만
    봄)에는 계속 잡히게 하기 위해서다. status가 'stopped'가 아니거나 이미 삭제된
    경우(또는 id가 없으면) 아무것도 바꾸지 않고 False를 반환한다 — delete_live_strategy와
    동일한 가드 시맨틱(WHERE 절에 deleted_at IS NULL을 추가해 이중 삭제 시 False가
    나오게 한다)."""
    conn = _connect()
    try:
        cursor = conn.execute(
            "UPDATE live_strategies SET deleted_at = datetime('now') "
            "WHERE id = ? AND status = 'stopped' AND deleted_at IS NULL",
            (live_strategy_id,),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()
```

- [ ] **Step 6: 테스트 실행해서 통과 확인**

Run: `pytest tests/test_trading_db.py -k soft_delete_live_strategy -v`
Expected: 4 passed

- [ ] **Step 7: 전체 DB 테스트 스위트 회귀 확인**

Run: `pytest tests/test_trading_db.py -v`
Expected: 모두 PASS(기존 `delete_live_strategy` 테스트 3개 포함, 회귀 없음)

- [ ] **Step 8: 커밋**

```bash
git add trading/db.py tests/test_trading_db.py
git commit -m "feat: 라이브 전략 소프트 삭제 DB 함수 추가 (deleted_at 컬럼)"
```

---

### Task 2: 백엔드 API — DELETE 엔드포인트 전환 + 관리 목록 필터

**Files:**
- Modify: `backend/main.py:1435-1447` (`list_live_strategies_endpoint`)
- Modify: `backend/main.py:1578-1586` (`delete_live_strategy_endpoint`)
- Test: `tests/test_backend.py:1062-1092` (기존 delete 테스트 3개 중 1개 수정 +
  신규 목록 테스트 1개 추가)

**Interfaces:**
- Consumes: Task 1의 `trading_db.soft_delete_live_strategy(id) -> bool`,
  `strategy["deleted_at"]` 필드(문자열 또는 None)
- Produces: `GET /api/v1/live-strategies`가 소프트 삭제된 전략을 응답 목록에서 제외.
  `DELETE /api/v1/live-strategies/{id}`는 여전히 `{"deleted": True}`를 반환하지만
  내부적으로 소프트 삭제만 수행(HTTP 계약 자체는 안 바뀜).

- [ ] **Step 1: 실패하는 테스트로 기존 테스트 교체**

`tests/test_backend.py:1062-1072`에 있는 아래 **기존 함수 전체를 삭제**한다:

```python
def test_delete_live_strategy_removes_stopped_strategy(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]
    client.post(f"/api/v1/live-strategies/{strategy_id}/stop")

    resp = client.delete(f"/api/v1/live-strategies/{strategy_id}")

    assert resp.status_code == 200
    assert resp.json() == {"deleted": True}
    assert trading_db_module.get_live_strategy(strategy_id) is None
```

그 자리(같은 줄 위치)에 아래 **새 함수를 추가**한다 — 함수명도 바뀐다(더 이상
"제거"가 아니라 "소프트 삭제"이므로 `removes_stopped_strategy` →
`soft_deletes_stopped_strategy`):

```python
def test_delete_live_strategy_soft_deletes_stopped_strategy(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]
    client.post(f"/api/v1/live-strategies/{strategy_id}/stop")

    resp = client.delete(f"/api/v1/live-strategies/{strategy_id}")

    assert resp.status_code == 200
    assert resp.json() == {"deleted": True}
    strategy = trading_db_module.get_live_strategy(strategy_id)
    assert strategy is not None
    assert strategy["deleted_at"] is not None
```

같은 파일, `test_delete_live_strategy_returns_404_for_missing_id`(약 1086-1091줄)
바로 뒤에 신규 테스트 추가:

```python
def test_list_live_strategies_excludes_soft_deleted(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]
    client.post(f"/api/v1/live-strategies/{strategy_id}/stop")
    client.delete(f"/api/v1/live-strategies/{strategy_id}")

    resp = client.get("/api/v1/live-strategies")

    assert resp.status_code == 200
    assert resp.json() == []
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `pytest tests/test_backend.py -k "soft_deletes_stopped_strategy or excludes_soft_deleted" -v`
Expected: `test_delete_live_strategy_soft_deletes_stopped_strategy`는 FAIL한다(아직
`delete_live_strategy_endpoint`가 하드 삭제라 `get_live_strategy`가 None을 반환).
`test_list_live_strategies_excludes_soft_deleted`는 **지금 단계에서는 통과할 수도
있다** — 아직 하드 삭제라 삭제된 전략이 목록에서 빠지는 게 어차피 맞기 때문이다(같은
결과를 잘못된 이유로 통과하는 상태). 이 테스트가 실제로 의미 있어지는 건 Step 3에서
삭제를 소프트로 바꾼 뒤부터다 — 지금 통과하더라도 정상이니 Step 3을 건너뛰지 말 것.

- [ ] **Step 3: 엔드포인트 구현 수정**

`backend/main.py:1435-1447`을 아래로 교체:

```python
@app.get("/api/v1/live-strategies")
def list_live_strategies_endpoint() -> list[dict]:
    strategies = [s for s in trading_db.list_live_strategies() if s["deleted_at"] is None]
    positions = {s["id"]: trading_db.get_open_position(s["id"]) for s in strategies}
    open_markets = {s["market"] for s in strategies if positions[s["id"]] is not None}
    try:
        current_prices = get_current_prices(list(open_markets)) if open_markets else {}
    except Exception:
        current_prices = {}
    return [
        _live_strategy_response(s, positions[s["id"]], current_prices.get(s["market"]))
        for s in strategies
    ]
```

`backend/main.py:1578-1586`을 아래로 교체:

```python
@app.delete("/api/v1/live-strategies/{strategy_id}")
def delete_live_strategy_endpoint(strategy_id: str) -> dict:
    strategy = trading_db.get_live_strategy(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="해당 id의 라이브 전략을 찾을 수 없습니다")
    if strategy["status"] != "stopped":
        raise HTTPException(status_code=409, detail="중지된 전략만 삭제할 수 있습니다")
    trading_db.soft_delete_live_strategy(strategy_id)
    return {"deleted": True}
```

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `pytest tests/test_backend.py -k "soft_deletes_stopped_strategy or excludes_soft_deleted" -v`
Expected: 2 passed

- [ ] **Step 5: live-strategies 관련 백엔드 테스트 전체 회귀 확인**

Run: `pytest tests/test_backend.py -k "live_strateg" -v`
Expected: 모두 PASS (409/404 테스트 2개 포함 회귀 없음)

- [ ] **Step 6: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "feat: 라이브 전략 삭제 API를 소프트 삭제로 전환 + 관리 목록에서 제외"
```

---

### Task 3: 매매일지 집계가 소프트 삭제된 전략도 계속 포함하는지 회귀테스트로 증명

**Files:**
- Test: `tests/test_trading_analytics_service.py` (기존
  `test_market_journal_merges_stopped_and_restarted_strategies_for_same_market`
  바로 뒤, 약 256줄)

**Interfaces:**
- Consumes: Task 1의 `db.soft_delete_live_strategy()`. 이 태스크는 프로덕션 코드를
  전혀 수정하지 않는다 — `backend/trading_analytics_service.py`가 이미 `status`가
  아니라 `approved_at`만 보고 전략을 고르므로(기존 코드), 소프트 삭제된 전략도
  이미 집계에 잡힌다. 이 태스크는 그 사실을 회귀테스트로 못박는다(재발 방지의
  핵심 검증).

- [ ] **Step 1: 회귀테스트 작성**

`tests/test_trading_analytics_service.py:256`(`test_market_journal_merges_stopped_and_restarted_strategies_for_same_market`
함수 끝) 바로 뒤에 추가:

```python
def test_market_journal_includes_soft_deleted_strategy(monkeypatch, tmp_path):
    """소프트 삭제(deleted_at)는 "라이브 전략 관리" 목록에서만 숨기기 위한 것이다 —
    매매일지 집계는 status/deleted_at을 보지 않고 approved_at만 보므로, 삭제된
    전략의 거래도 계속 잡혀야 한다. 재발 방지 대상: 2026-08-24 KRW-DOGE 전략을
    하드 삭제해서 매매일지 이력(거래 10건, 실현손익 -66,598원)이 통째로 사라졌던
    사고 — 업비트 자체 주문 이력을 대조해 수동으로 복구했다."""
    db = _fresh(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(
        db, status="draft", market="KRW-DOGE", timeframe="minutes60",
    )
    _approve(db, strategy_id, 100_000.0)
    position_id = db.insert_position(strategy_id, "KRW-DOGE", 300.0, 300.0)
    db.close_position_row(position_id, 303.51, 300.0, 1053.0, 1.17, "sell_signal")
    db.stop_live_strategy_if_no_open_position(strategy_id)

    deleted = db.soft_delete_live_strategy(strategy_id)
    assert deleted is True

    detail = svc.get_market_journal("KRW-DOGE")

    assert detail is not None
    assert detail["trade_count"] == 1
    assert detail["cumulative_pnl"] == 1053.0

    summary = svc.get_journal_summary()
    assert summary["cumulative_pnl"] == 1053.0
    assert [s["market"] for s in summary["strategies"]] == ["KRW-DOGE"]
```

- [ ] **Step 2: 테스트 실행해서 통과 확인 (구현 변경 없이 바로 통과해야 함)**

Run: `pytest tests/test_trading_analytics_service.py -k soft_deleted -v`
Expected: 1 passed (프로덕션 코드 변경이 이 태스크에 없으므로, 실패한다면 스펙의
전제("이미 approved_at만 본다")가 틀렸다는 뜻 — 그 경우 Task 3을 멈추고 스펙을
재검토할 것)

- [ ] **Step 3: 커밋**

```bash
git add tests/test_trading_analytics_service.py
git commit -m "test: 매매일지가 소프트 삭제된 전략도 계속 집계함을 회귀테스트로 고정"
```

---

### Task 4: 프론트엔드 삭제 확인 다이얼로그 문구 수정

**Files:**
- Modify: `frontend/components/LiveStrategiesPage.tsx:579-582`

**Interfaces:**
- Consumes: 없음(순수 문구 변경, API 계약 변경 없음 — `deleteLiveStrategy()`
  (`frontend/lib/api/liveStrategies.ts:39`)는 그대로 `DELETE
  /api/v1/live-strategies/{id}`를 호출하고 `{deleted: boolean}`을 받는다)

- [ ] **Step 1: 다이얼로그 문구 교체**

`frontend/components/LiveStrategiesPage.tsx:577-583`을 아래로 교체:

```tsx
                    <AlertDialogContent>
                      <AlertDialogHeader>
                        <AlertDialogTitle>이 전략을 삭제하시겠습니까?</AlertDialogTitle>
                        <AlertDialogDescription>
                          이 전략을 목록에서 삭제합니다. 매매일지에 남은 거래 기록은
                          계속 보존되며, 이 목록에서는 다시 볼 수 없습니다.
                        </AlertDialogDescription>
                      </AlertDialogHeader>
```

- [ ] **Step 2: 타입체크**

Run(디렉토리: `frontend/`): `npx tsc --noEmit`
Expected: 에러 없음. **주의**: `npm run dev`가 이미 같은 `frontend/.next`로 실행 중이면
`npm run build`는 절대 쓰지 말 것(dev 서버 런타임이 깨짐, `.next` 삭제+재기동 필요) —
`tsc --noEmit`만 쓴다.

- [ ] **Step 3: 로컬 dev 서버로 수동 확인**

로컬 백엔드(`uvicorn backend.main:app --reload --port 8000`)와 프론트(`npm run dev`)를
**로컬 DB**(`data/trading.db`, AWS 프로덕션 DB 아님)로 띄운 상태에서:
1. `/live-strategies`에서 테스트용 초안 전략 하나를 만들고 승인 → 중지
2. 카드에 뜨는 삭제(휴지통) 아이콘 클릭 → 다이얼로그 문구가 위 Step 1 내용으로
   보이는지 확인
3. "삭제" 클릭 → 카드가 목록에서 사라지는지 확인
4. `/journal`에서 해당 코인을 조회해 거래 기록이 여전히 보이는지 확인(테스트
   전략이라 거래가 없으면 이 항목은 생략 가능 — Task 1~3의 pytest가 이미 이
   동작을 자동검증했으므로 필수는 아님)

- [ ] **Step 4: 커밋**

```bash
git add frontend/components/LiveStrategiesPage.tsx
git commit -m "fix: 라이브 전략 삭제 다이얼로그 문구를 소프트 삭제 동작에 맞게 수정"
```

---

## 전체 회귀 확인 (모든 태스크 완료 후)

- [ ] Run: `pytest -v`
- [ ] Expected: 전체 스위트 PASS, 회귀 없음
- [ ] Run(디렉토리: `frontend/`): `npx tsc --noEmit`
- [ ] Expected: 에러 없음

## 배포 메모 (구현 완료 후, 별도 확인 필요 — 이 계획의 범위 밖)

이 변경은 `data/trading.db`(AWS 프로덕션)에 `ALTER TABLE ... ADD COLUMN deleted_at`을
실행해야 한다. `_ensure_live_strategies_deleted_at_column`이 백엔드 시작 시 자동으로
처리하므로 별도 마이그레이션 스크립트는 필요 없다 — `deploy/update.sh`로 배포하고
`backend.service`가 재시작되면 자동으로 컬럼이 추가된다. 배포 전 로컬에서 전체
pytest가 통과하는지 반드시 확인할 것(Global Constraints 참고).
