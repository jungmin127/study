# 라이브 전략 삭제 + 백테스트 제목/설명 편집 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** stopped 상태의 라이브 전략을 목록에서 삭제할 수 있게 하고, 백테스트 상세 페이지에서 제목/설명을 인라인으로 수정할 수 있게 한다.

**Architecture:** 두 기능 모두 기존 CRUD 패턴(FastAPI 엔드포인트 + SQLite 함수 + React 클라이언트 컴포넌트)을 그대로 따르는 독립적인 추가 작업이다. 라이브 전략 삭제는 `trading.db`(FK 제약 있음)에서 자식 테이블을 먼저 지우는 트랜잭션형 삭제이고, 백테스트 제목/설명 편집은 `backtest_results.db`의 `backtest_runs` 행을 부분 UPDATE하면서 `created_at`도 함께 갱신해 로컬→서버 push 시 "최신 판"으로 인식되게 한다.

**Tech Stack:** FastAPI, SQLite(sqlite3 표준 라이브러리), pytest, Next.js(App Router) 클라이언트 컴포넌트, shadcn 스타일 UI 컴포넌트(base-ui 기반), lucide-react 아이콘.

## Global Constraints

- 사용자 대상 문자열(에러 메시지/버튼 라벨/확인 다이얼로그 문구)은 전부 한국어로 작성한다 (기존 코드베이스 관례).
- `trading/db.py`는 `PRAGMA foreign_keys = ON`이 켜져 있다 — 자식 테이블(`positions`, `orders`, `signals`, `daily_performance`, `circuit_breaker_state`)을 부모(`live_strategies`)보다 먼저 지워야 한다.
- 라이브 전략 삭제는 `status == 'stopped'`일 때만 허용한다.
- 백테스트 제목/설명 수정 시 `backtest_runs.created_at`도 함께 `datetime('now')`로 갱신한다 (스펙 결정 — `docs/superpowers/specs/2026-08-17-live-strategy-delete-and-backtest-title-edit-design.md` 참고).
- 프론트엔드에는 테스트 프레임워크가 없다 (`frontend/package.json`에 test 스크립트 없음) — 프론트 작업은 `npm run build`로 타입/린트 오류만 자동 검증하고, 나머지는 개발 서버로 수동 확인한다.

---

## Task 1: `trading/db.py` — `delete_live_strategy()`

**Files:**
- Modify: `trading/db.py` (라인 728 뒤, `list_active_strategies()` 함수 다음에 새 함수 추가)
- Test: `tests/test_trading_db.py`

**Interfaces:**
- Consumes: 없음 (기존 `_connect()`만 사용)
- Produces: `delete_live_strategy(live_strategy_id: str) -> bool` — Task 2가 이 함수를 호출한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_trading_db.py` 맨 아래에 추가:

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

    deleted = db.delete_live_strategy(strategy_id)

    assert deleted is True
    assert db.get_live_strategy(strategy_id) is None
    assert db.get_position(position_id) is None
    assert db.get_order_by_id(order_id) is None
    assert db.get_circuit_breaker_state(strategy_id) is None
    assert db.get_daily_performance(strategy_id, "2026-08-17") is None


def test_delete_live_strategy_rejects_non_stopped_status(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="running")

    deleted = db.delete_live_strategy(strategy_id)

    assert deleted is False
    assert db.get_live_strategy(strategy_id) is not None


def test_delete_live_strategy_returns_false_for_missing_id(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)

    assert db.delete_live_strategy("does-not-exist") is False
```

`insert_live_strategy` 헬퍼는 파일 상단에서 이미 `from tests.trading_db_fixtures import insert_live_strategy`로 임포트되어 있다 (181번째 줄 부근). 새 테스트가 그 임포트 아래(181번째 줄 이후)에 위치하는지 확인한다 — 그 이전 위치에 추가하면 `NameError`가 난다.

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_trading_db.py -k test_delete_live_strategy -v`
Expected: FAIL with `AttributeError: module 'trading.db' has no attribute 'delete_live_strategy'`

- [ ] **Step 3: 최소 구현 작성**

`trading/db.py`의 `list_active_strategies()` 함수(718~727행) 바로 다음에 추가:

```python
def delete_live_strategy(live_strategy_id: str) -> bool:
    """stopped 상태의 라이브 전략을 자식 행까지 포함해 완전히 삭제한다. FK 제약
    (PRAGMA foreign_keys = ON)이 켜져 있어 부모(live_strategies)보다 자식 테이블을
    먼저 지워야 한다. 삭제 순서: signals(orders 참조) -> orders(positions 자기참조
    포함, 단 같은 live_strategy_id의 orders는 한 문장으로 전부 지우므로 자기참조
    문제 없음) -> positions -> daily_performance/circuit_breaker_state -> live_strategies.
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
        cursor = conn.execute(
            "DELETE FROM live_strategies WHERE id = ? AND status = 'stopped'",
            (live_strategy_id,),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_trading_db.py -k test_delete_live_strategy -v`
Expected: PASS (3 passed)

- [ ] **Step 5: 커밋**

```bash
git add trading/db.py tests/test_trading_db.py
git commit -m "feat: stopped 라이브 전략 삭제 함수 추가"
```

---

## Task 2: `backend/main.py` — `DELETE /api/v1/live-strategies/{strategy_id}`

**Files:**
- Modify: `backend/main.py` (import 블록, `stop_live_strategy_endpoint` 다음)
- Test: `tests/test_backend.py`

**Interfaces:**
- Consumes: `trading_db.delete_live_strategy(strategy_id: str) -> bool` (Task 1)
- Produces: `DELETE /api/v1/live-strategies/{strategy_id}` — 200 `{"deleted": true}` / 404 / 409. Task 3의 프론트가 이 엔드포인트를 호출한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_backend.py`의 `test_stop_live_strategy_returns_409_when_already_stopped` 함수(804~811행) 바로 다음에 추가:

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


def test_delete_live_strategy_returns_409_when_not_stopped(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]

    resp = client.delete(f"/api/v1/live-strategies/{strategy_id}")

    assert resp.status_code == 409
    assert trading_db_module.get_live_strategy(strategy_id) is not None


def test_delete_live_strategy_returns_404_for_missing_id(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    resp = client.delete("/api/v1/live-strategies/does-not-exist")

    assert resp.status_code == 404
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_backend.py -k test_delete_live_strategy -v`
Expected: FAIL with 404/405 mismatch (엔드포인트가 없어 405 Method Not Allowed 또는 404가 반환되어 assert 실패)

- [ ] **Step 3: 최소 구현 작성**

`backend/main.py`의 `stop_live_strategy_endpoint` 함수(1287~1299행) 바로 다음에 추가:

```python
@app.delete("/api/v1/live-strategies/{strategy_id}")
def delete_live_strategy_endpoint(strategy_id: str) -> dict:
    strategy = trading_db.get_live_strategy(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="해당 id의 라이브 전략을 찾을 수 없습니다")
    if strategy["status"] != "stopped":
        raise HTTPException(status_code=409, detail="중지된 전략만 삭제할 수 있습니다")
    trading_db.delete_live_strategy(strategy_id)
    return {"deleted": True}
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_backend.py -k "test_delete_live_strategy or test_stop_live_strategy or test_pause_live_strategy or test_resume_live_strategy" -v`
Expected: PASS (기존 pause/resume/stop 테스트도 회귀 없이 함께 통과)

- [ ] **Step 5: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "feat: 라이브 전략 삭제 API 엔드포인트 추가"
```

---

## Task 3: 프론트엔드 — 라이브 전략 삭제 버튼

**Files:**
- Modify: `frontend/lib/api/liveStrategies.ts`
- Modify: `frontend/components/LiveStrategiesPage.tsx`

**Interfaces:**
- Consumes: `DELETE /api/v1/live-strategies/{strategy_id}` (Task 2)
- Produces: 없음 (최종 사용자 대상 UI)

- [ ] **Step 1: API 함수 추가**

`frontend/lib/api/liveStrategies.ts` 맨 아래(`stopLiveStrategy` 함수 다음)에 추가:

```typescript
export function deleteLiveStrategy(id: string): Promise<{ deleted: boolean }> {
  return apiFetch<{ deleted: boolean }>(`/api/v1/live-strategies/${id}`, { method: 'DELETE' });
}
```

- [ ] **Step 2: `LiveStrategiesPage.tsx`에 삭제 버튼 + 확인 다이얼로그 추가**

`frontend/components/LiveStrategiesPage.tsx`의 import 블록을 다음과 같이 수정한다 (기존 4~19행):

```typescript
import { useCallback, useState } from 'react';
import { Check, Pause, Play, Square, Trash2, X } from 'lucide-react';
import { ApiError } from '@/lib/api/client';
import {
  approveLiveStrategy,
  deleteLiveStrategy,
  getLiveStrategies,
  pauseLiveStrategy,
  resumeLiveStrategy,
  stopLiveStrategy,
} from '@/lib/api/liveStrategies';
import type { LiveStrategy } from '@/lib/types/liveStrategies';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogHeader,
  AlertDialogFooter,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import { Badge } from '@/components/ui/badge';
import { Button, buttonVariants } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { formatTimeframe } from '@/lib/format';
import { returnRateColor } from '@/lib/return-rate-color';
import { useVisiblePolling } from '@/lib/hooks/useVisiblePolling';
```

현재 `status === 'stopped'`일 때 상단 액션 영역(91~163행의 `<div className="flex shrink-0 items-center gap-1.5">`)에는 draft/running/paused 분기(93~162행)만 있고 stopped 분기가 없다. 카드 하단의 "중지 시각" 텍스트(188~190행, `{s.status === 'stopped' && s.stopped_at && (...)}`)는 그대로 두고, 상단 액션 영역에 새 stopped 분기만 추가한다.

paused 분기가 끝나는 162행(`)}`) 바로 다음, 그 영역을 닫는 163행(`</div>`) 바로 전에 삽입:

```tsx
                {s.status === 'stopped' && (
                  <AlertDialog>
                    {/* AlertDialogTrigger has no asChild in this project's base-ui-backed
                        shadcn style; apply Button's own class-variance styles directly
                        (same pattern as BacktestRunsTable.tsx). */}
                    <AlertDialogTrigger
                      type="button"
                      className={buttonVariants({ variant: 'destructive', size: 'icon-lg' })}
                      aria-label="삭제"
                      title="삭제"
                      disabled={pendingId === s.id}
                    >
                      <Trash2 />
                    </AlertDialogTrigger>
                    <AlertDialogContent>
                      <AlertDialogHeader>
                        <AlertDialogTitle>이 전략을 삭제하시겠습니까?</AlertDialogTitle>
                        <AlertDialogDescription>삭제 후에는 되돌릴 수 없습니다.</AlertDialogDescription>
                      </AlertDialogHeader>
                      <AlertDialogFooter>
                        <AlertDialogCancel>취소</AlertDialogCancel>
                        <AlertDialogAction onClick={() => runAction(s.id, deleteLiveStrategy)}>
                          삭제
                        </AlertDialogAction>
                      </AlertDialogFooter>
                    </AlertDialogContent>
                  </AlertDialog>
                )}
```

`runAction`은 이미 `(id: string, action: (id: string) => Promise<LiveStrategy>) => Promise<void>` 시그니처인데(54~65행), `deleteLiveStrategy`는 `Promise<{ deleted: boolean }>`을 반환해 타입이 맞지 않는다. `runAction`을 제네릭으로 바꿔 두 반환 타입을 모두 받도록 수정한다 (54행 근처):

```tsx
  async function runAction<T>(id: string, action: (id: string) => Promise<T>) {
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
```

- [ ] **Step 3: 타입/빌드 확인**

Run: `cd frontend && npm run build`
Expected: 빌드 성공 (타입 에러 없음)

- [ ] **Step 4: 개발 서버로 수동 확인**

Run: 백엔드(`uvicorn backend.main:app --reload --port 8000`)와 프론트(`cd frontend && npm run dev`)를 각각 실행한 뒤 브라우저에서 라이브 전략 탭을 연다.
Expected:
- draft/running/paused 상태 카드에는 기존 버튼(승인/취소/일시정지/재개/중지)만 보이고 휴지통 버튼이 없다.
- stopped 상태 카드에만 휴지통 아이콘 버튼이 보인다.
- 휴지통 클릭 → 확인 다이얼로그 → 삭제 클릭 시 카드가 목록에서 사라진다.
- 취소를 누르면 카드가 그대로 남는다.

- [ ] **Step 5: 커밋**

```bash
git add frontend/lib/api/liveStrategies.ts frontend/components/LiveStrategiesPage.tsx
git commit -m "feat: 중지된 라이브 전략 삭제 버튼 추가"
```

---

## Task 4: `engine/cache.py` — `update_backtest_run_metadata()` + `load_result()` 필드 추가

**Files:**
- Modify: `engine/cache.py`
- Test: `tests/test_cache.py`

**Interfaces:**
- Consumes: 없음 (기존 `_connect()`만 사용)
- Produces:
  - `update_backtest_run_metadata(run_id: str, title: str | None, description: str | None) -> bool` — Task 5가 호출한다.
  - `load_result(run_id)`가 반환하는 dict에 `title: str | None`, `description: str | None`, `created_at: str` 키가 추가됨 — Task 5의 `get_backtest_detail`이 이 키들을 사용한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_cache.py`의 `test_delete_backtest_run_returns_false_for_missing_run` 함수(417~419행) 바로 다음에 추가:

```python
def test_update_backtest_run_metadata_updates_title_and_description(monkeypatch, tmp_path):
    _save_condition_tree_run(monkeypatch, tmp_path, "run-1", title="원래 제목", description="원래 설명")

    updated = update_backtest_run_metadata("run-1", "새 제목", "새 설명")

    assert updated is True
    runs = list_backtest_runs()
    run = next(r for r in runs if r["run_id"] == "run-1")
    assert run["title"] == "새 제목"
    assert run["description"] == "새 설명"


def test_update_backtest_run_metadata_returns_false_for_missing_run(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")

    assert update_backtest_run_metadata("does-not-exist", "제목", None) is False


def test_load_result_includes_title_description_and_created_at(monkeypatch, tmp_path):
    _save_condition_tree_run(monkeypatch, tmp_path, "run-1", title="제목", description="설명")

    result = load_result("run-1")

    assert result["title"] == "제목"
    assert result["description"] == "설명"
    assert result["created_at"] is not None
```

이 세 테스트는 `update_backtest_run_metadata`를 `from engine.cache import ...`로 가져와야 한다. 파일 상단 임포트 블록(10행)을 다음과 같이 수정한다:

```python
from engine.cache import compute_cache_key, delete_backtest_run, get_run_config, load_result, save_result
from engine.cache import update_backtest_run_metadata
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_cache.py -k "update_backtest_run_metadata or load_result_includes" -v`
Expected: FAIL with `ImportError: cannot import name 'update_backtest_run_metadata'`

- [ ] **Step 3: 최소 구현 작성**

`engine/cache.py`의 `delete_backtest_run` 함수(256~265행) 바로 다음에 추가:

```python
def update_backtest_run_metadata(run_id: str, title: str | None, description: str | None) -> bool:
    """title/description을 수정하고 created_at도 함께 갱신한다. created_at을 갱신해야
    scripts/import_backtest_results.py의 merge_databases()가 이 수정을 "더 최신"으로
    인식해 라이브 서버의 기존 행(backtest_runs+backtest_results 전체)을 덮어쓴다 —
    갱신하지 않으면 로컬에서 제목을 고쳐 push해도 created_at이 그대로라 서버에 반영되지
    않는다."""
    conn = _connect()
    try:
        cursor = conn.execute(
            "UPDATE backtest_runs SET title = ?, description = ?, created_at = datetime('now') "
            "WHERE id = ?",
            (title, description, run_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()
```

`load_result` 함수(187~222행)를 다음으로 교체한다:

```python
def load_result(run_id: str) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT res.final_value, res.sharpe, res.max_drawdown, res.equity_curve_json, res.trades_json, "
            "       r.market, r.timeframe, r.start, r.end, r.risk_config_json, "
            "       r.title, r.description, r.created_at "
            "FROM backtest_results res "
            "JOIN backtest_runs r ON r.id = res.run_id "
            "WHERE res.run_id = ?",
            (run_id,),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return None

    (final_value, sharpe, max_drawdown, equity_curve_json, trades_json,
     market, timeframe, start, end, risk_config_json,
     title, description, created_at) = row
    risk_config = json.loads(risk_config_json)
    initial_capital = risk_config.get("initial_capital")
    commission_rate = risk_config.get("commission_rate", 0.0005)
    return {
        "final_value": final_value,
        "sharpe": sharpe,
        "max_drawdown": max_drawdown,
        "equity_curve": json.loads(equity_curve_json),
        "trades": json.loads(trades_json),
        "market": market,
        "timeframe": timeframe,
        "start": start,
        "end": end,
        "initial_capital": initial_capital,
        "commission_rate": commission_rate,
        "title": title,
        "description": description,
        "created_at": created_at,
        "from_cache": True,
    }
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_cache.py -v`
Expected: PASS, 전부 통과 (기존 `load_result` 사용처가 새 키를 무시하므로 회귀 없음)

- [ ] **Step 5: 커밋**

```bash
git add engine/cache.py tests/test_cache.py
git commit -m "feat: 백테스트 title/description 수정 함수 및 load_result 필드 추가"
```

---

## Task 5: `backend/main.py` — 상세 응답 필드 추가 + `PATCH /api/v1/backtests/{run_id}`

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_backend.py`

**Interfaces:**
- Consumes: `update_backtest_run_metadata(run_id, title, description) -> bool` (Task 4), `load_result(run_id)`의 `title`/`description`/`created_at` 키 (Task 4)
- Produces:
  - `GET /api/v1/backtests/{run_id}` 응답에 `title: str | None`, `description: str | None`, `created_at: str` 추가 — Task 6의 상세 페이지가 사용한다.
  - `PATCH /api/v1/backtests/{run_id}` — body `{title, description}`, 200 `{title, description, created_at}` / 404 — Task 6이 호출한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_backend.py`의 `test_delete_backtest_returns_404_for_missing_run` 함수(211~214행) 바로 다음에 추가:

```python
def test_update_backtest_metadata_updates_title_and_description(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    save_result(
        run_id="r1", strategy_name="ConditionTreeStrategy", strategy_params={},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={"final_value": 10500.0, "sharpe": 1.0, "max_drawdown": 2.0, "equity_curve": [], "trades": []},
        title="원래 제목", description="원래 설명",
    )

    resp = client.patch("/api/v1/backtests/r1", json={"title": "새 제목", "description": "새 설명"})

    assert resp.status_code == 200
    assert resp.json()["title"] == "새 제목"
    assert resp.json()["description"] == "새 설명"

    detail_resp = client.get("/api/v1/backtests/r1")
    detail = detail_resp.json()
    assert detail["title"] == "새 제목"
    assert detail["description"] == "새 설명"
    assert detail["created_at"].endswith("+00:00")


def test_update_backtest_metadata_returns_404_for_missing_run(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    resp = client.patch("/api/v1/backtests/does-not-exist", json={"title": "제목", "description": None})

    assert resp.status_code == 404
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_backend.py -k test_update_backtest_metadata -v`
Expected: FAIL — `client.patch`가 405 Method Not Allowed를 반환하거나 `detail["created_at"]`에서 `KeyError`

- [ ] **Step 3: 최소 구현 작성**

`backend/main.py`의 import 블록(20~37행)에서 `engine.cache` import에 `update_backtest_run_metadata` 추가:

```python
from engine.cache import (
    delete_backtest_run,
    delete_grid_search_job,
    finish_grid_search_job,
    get_grid_search_job,
    get_run_config,
    list_backtest_runs,
    list_combined_ranking,
    list_distinct_combos,
    list_grid_search_jobs,
    list_latest_sweep_results,
    list_segment_classification,
    list_sweep_history,
    load_result,
    remove_grid_search_result,
    run_backtest_cached,
    save_result,
    update_backtest_run_metadata,
)
```

`get_backtest_detail`의 반환 dict(629~640행)를 다음으로 교체:

```python
    return {
        "market": result["market"],
        "timeframe": result["timeframe"],
        "start": result["start"],
        "end": result["end"],
        "initial_capital": result["initial_capital"],
        "final_value": final_value,
        "metrics": metrics,
        "ohlcv": ohlcv,
        "trades": trades_out,
        "live_price_as_of": _to_utc_iso(live_price_as_of) if live_price_as_of else None,
        "title": result["title"],
        "description": result["description"],
        "created_at": _to_utc_iso(result["created_at"]),
    }
```

`RunBacktestRequest` 클래스(717~726행) 바로 다음에 새 Pydantic 모델 추가:

```python
class UpdateBacktestMetadataRequest(BaseModel):
    title: str | None = None
    description: str | None = None
```

`delete_backtest` 엔드포인트(656~661행) 바로 다음에 새 엔드포인트 추가:

```python
@app.patch("/api/v1/backtests/{run_id}")
def update_backtest_metadata_endpoint(run_id: str, req: UpdateBacktestMetadataRequest) -> dict:
    updated = update_backtest_run_metadata(run_id, req.title, req.description)
    if not updated:
        raise HTTPException(status_code=404, detail="해당 run_id의 백테스트 결과를 찾을 수 없습니다")
    result = load_result(run_id)
    return {
        "title": result["title"],
        "description": result["description"],
        "created_at": _to_utc_iso(result["created_at"]),
    }
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_backend.py -v`
Expected: PASS, 전부 통과 (기존 상세/목록/refresh 테스트도 회귀 없음)

- [ ] **Step 5: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "feat: 백테스트 title/description 수정 API 및 상세 응답 필드 추가"
```

---

## Task 6: 프론트엔드 — 백테스트 상세 페이지 인라인 편집

**Files:**
- Modify: `frontend/lib/types/eda.ts`
- Modify: `frontend/lib/api/eda.ts`
- Create: `frontend/components/BacktestMetaEditor.tsx`
- Modify: `frontend/app/backtests/[runId]/page.tsx`

**Interfaces:**
- Consumes: `GET /api/v1/backtests/{run_id}` (title/description/created_at 포함, Task 5), `PATCH /api/v1/backtests/{run_id}` (Task 5)
- Produces: 없음 (최종 사용자 대상 UI)

- [ ] **Step 1: 타입 추가**

`frontend/lib/types/eda.ts`의 `BacktestDetail` 인터페이스(58~69행)를 다음으로 교체:

```typescript
export interface BacktestDetail {
  market: string;
  timeframe: string;
  start: string;
  end: string;
  initial_capital: number;
  final_value: number;
  metrics: BacktestMetrics;
  ohlcv: OhlcvPoint[];
  trades: Trade[];
  live_price_as_of: string | null;
  title: string | null;
  description: string | null;
  created_at: string;
}
```

- [ ] **Step 2: API 함수 추가**

`frontend/lib/api/eda.ts`의 `deleteBacktestRun` 함수(70~74행) 바로 다음에 추가:

```typescript
export function updateBacktestRun(
  runId: string,
  req: { title: string | null; description: string | null },
): Promise<{ title: string | null; description: string | null; created_at: string }> {
  return apiFetch(`/api/v1/backtests/${runId}`, {
    method: 'PATCH',
    body: JSON.stringify(req),
  });
}
```

- [ ] **Step 3: `BacktestMetaEditor` 컴포넌트 작성**

`frontend/components/BacktestMetaEditor.tsx` 새로 작성:

```tsx
'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { Pencil } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { updateBacktestRun } from '@/lib/api/eda';
import { ApiError } from '@/lib/api/client';

interface BacktestMetaEditorProps {
  runId: string;
  title: string | null;
  description: string | null;
}

export default function BacktestMetaEditor({ runId, title, description }: BacktestMetaEditorProps) {
  const router = useRouter();
  const [editing, setEditing] = useState(false);
  const [titleInput, setTitleInput] = useState(title ?? '');
  const [descriptionInput, setDescriptionInput] = useState(description ?? '');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function startEditing() {
    setTitleInput(title ?? '');
    setDescriptionInput(description ?? '');
    setError(null);
    setEditing(true);
  }

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      await updateBacktestRun(runId, {
        title: titleInput.trim() || null,
        description: descriptionInput.trim() || null,
      });
      setEditing(false);
      router.refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '저장에 실패했습니다.');
    } finally {
      setSaving(false);
    }
  }

  if (editing) {
    return (
      <div className="mb-2 max-w-md space-y-2">
        <Input
          value={titleInput}
          onChange={(e) => setTitleInput(e.target.value)}
          placeholder="제목"
          maxLength={200}
        />
        <Textarea
          value={descriptionInput}
          onChange={(e) => setDescriptionInput(e.target.value)}
          placeholder="설명"
          maxLength={2000}
        />
        {error && <p className="text-sm text-destructive">{error}</p>}
        <div className="flex gap-2">
          <Button size="sm" onClick={handleSave} disabled={saving}>
            {saving ? '저장 중...' : '저장'}
          </Button>
          <Button size="sm" variant="outline" onClick={() => setEditing(false)} disabled={saving}>
            취소
          </Button>
        </div>
      </div>
    );
  }

  return (
    <button type="button" onClick={startEditing} className="group mb-2 flex items-start gap-1.5 text-left">
      <div>
        <p className="text-sm font-medium">
          {title || <span className="text-muted-foreground">(제목 없음)</span>}
        </p>
        {description && <p className="text-xs text-muted-foreground">{description}</p>}
      </div>
      <Pencil className="mt-0.5 size-3.5 text-muted-foreground opacity-0 group-hover:opacity-100" />
    </button>
  );
}
```

- [ ] **Step 4: 상세 페이지에 연결**

`frontend/app/backtests/[runId]/page.tsx`의 import 블록에 추가 (6행 `GoLiveButton` 다음):

```typescript
import GoLiveButton from '@/components/GoLiveButton';
import BacktestMetaEditor from '@/components/BacktestMetaEditor';
```

`<h1>` 다음(134행 다음, 135행 이전)에 추가:

```tsx
      <h1 className="mb-1 text-lg font-semibold">백테스트 상세</h1>
      <BacktestMetaEditor runId={params.runId} title={detail.title} description={detail.description} />
      <div className="mb-1 flex flex-wrap items-center gap-3">
```

- [ ] **Step 5: 타입/빌드 확인**

Run: `cd frontend && npm run build`
Expected: 빌드 성공 (타입 에러 없음)

- [ ] **Step 6: 개발 서버로 수동 확인**

Run: 백엔드와 프론트 개발 서버를 실행한 뒤 브라우저에서 아무 백테스트 상세 페이지(`/backtests/{run_id}`)를 연다.
Expected:
- 제목(또는 "(제목 없음)")과 설명이 h1 아래에 보인다.
- 클릭하면 입력 필드로 전환되고, 제목/설명을 고친 뒤 저장을 누르면 값이 반영되고 편집모드가 닫힌다.
- 취소를 누르면 원래 값으로 되돌아간다.
- 목록 페이지(`/backtests` 또는 홈)로 돌아가 해당 런의 "실행 시각"이 방금 수정 시각으로 바뀌어 있는지 확인한다.

- [ ] **Step 7: 커밋**

```bash
git add frontend/lib/types/eda.ts frontend/lib/api/eda.ts frontend/components/BacktestMetaEditor.tsx frontend/app/backtests/\[runId\]/page.tsx
git commit -m "feat: 백테스트 상세 페이지에 제목/설명 인라인 편집 추가"
```

---

## Self-Review Notes

- **스펙 커버리지:** 스펙의 두 기능(라이브 전략 삭제 UX, 백테스트 제목/설명 인라인 편집) 모두 Task 1~3, 4~6으로 매핑됨. `created_at` 갱신 결정은 Task 4에서 구현·주석으로 반영됨. "실행 시각" 라벨 유지 결정은 별도 코드 변경이 필요 없으므로(라벨 텍스트를 바꾸지 않는 것 자체가 그대로 두는 것) 태스크가 없는 것이 맞음.
- **자식 테이블 삭제 순서:** `orders.replaces_order_id`가 같은 테이블을 자기참조하지만, `DELETE FROM orders WHERE live_strategy_id = ?`가 해당 전략의 모든 order를 한 문장으로 함께 지우므로 자기참조로 인한 FK 위반은 발생하지 않는다(SQLite는 문장 완료 시점에 immediate FK를 검사한다).
- **타입 일관성:** `deleteLiveStrategy`/`updateBacktestRun`의 반환 타입과 `BacktestMetaEditor`/`LiveStrategiesPage`에서의 사용이 일치하는지 확인함. `runAction`을 제네릭으로 바꾸는 것도 Task 3 Step 2에 명시함(그렇지 않으면 `deleteLiveStrategy`의 반환 타입이 기존 시그니처와 안 맞아 타입 에러가 남).
