# Grid Search 결과 개별 삭제 + 매수/매도 줄바꿈 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/grid-search` 탭의 펼친 결과 목록에서 매수/매도 조건을 줄바꿈해서 보여주고,
1위~N위 전체를 펼침 대상으로 넓힌 뒤, 체크박스로 원하는 결과를 골라 영구 삭제할 수 있게
한다.

**Architecture:** 백엔드에 새 삭제 함수(`engine/cache.py`)와 새 엔드포인트
(`backend/main.py`)를 추가해 grid search job의 저장된 결과 목록(`result_json`)에서 특정
결과를 영구 제거한다. 프론트엔드는 새 API 함수 하나(`deleteGridSearchResult`)와
`GridSearchHistory.tsx`의 펼친 목록 재구성(전체 순위 노출 + 체크박스 + 선택삭제)으로
구성된다. `GridSearchPage.tsx`는 기존 `refresh` 콜백을 한 줄 추가로 전달만 한다.

**Tech Stack:** FastAPI + SQLite(`engine/cache.py`), pytest(TDD), Next.js 14(App Router) +
TypeScript, 기존 shadcn 스타일 컴포넌트(`@/components/ui/checkbox`,
`@/components/ui/alert-dialog`, `@/components/ui/button`), 새 의존성 없음.

## Global Constraints

- 스펙: `docs/superpowers/specs_v1/2026-08-04-grid-search-result-delete-design.md`(사용자
  승인됨).
- 새 삭제 엔드포인트(`DELETE /api/v1/grid-search/jobs/{job_id}/results/{run_id}`)는 반드시
  해당 job의 `result_json`에 그 `run_id`가 실제로 있는지 먼저 확인하고 없으면 404를
  반환한다 — 임의의 `run_id`를 지우는 뒷문이 되면 안 된다.
- `delete_backtest_run(run_id)`의 반환값(`bool`)은 검사하지 않는다 — best-effort 정리
  호출이며, 이미 다른 경로로 지워져 있어도 `result_json` 쪽 참조만 정리하면 된다.
- 삭제는 항상 job 단위로 스코프된다(한 job 안에서 선택한 결과만 지움, 다른 job에 영향
  없음).
- 순위(`rank`) 라벨은 삭제 후에도 재번호를 매기지 않는다 — 저장된 원래 순위를 그대로
  표시한다.
- `scripts/grid_search.py`가 만드는 title 문자열 형식, `frontend/lib/grid-result-title.ts`의
  `parseGridResultTitle` 파싱 로직은 이 플랜에서 변경하지 않는다 — 렌더링 방식(줄바꿈)만
  바뀐다.
- 프론트엔드 검증은 `npx tsc --noEmit`과 `npx eslint <파일>`만 사용한다. **`npm run
  build`는 쓰지 않는다** — 이 저장소는 `npm run dev`가 이미 떠 있는 상태에서 `npm run
  build`를 돌리면 `.next`가 깨지는 알려진 문제가 있다(memory:
  upbit-frontend-tailwind-opacity-gotcha).
- 이 저장소에는 프론트엔드 자동 단위테스트 러너가 없다 — 프론트 검증은
  `tsc`/`eslint`/수동 브라우저 확인으로 한다(기존 관례). 백엔드(`engine/cache.py`,
  `backend/main.py`)는 pytest로 TDD한다(기존 관례, `tests/test_cache.py`/
  `tests/test_backend.py`에 이미 같은 스타일의 테스트가 많음).
- 각 태스크는 순서 의존성이 있다: Task 2는 Task 1의 `remove_grid_search_result`를
  import해서 쓰므로 Task 1이 먼저 끝나야 한다. Task 4는 Task 2의 엔드포인트 계약과 Task
  3의 `deleteGridSearchResult` 함수를 그대로 쓰므로 Task 2·3이 먼저 끝나야 한다.

---

### Task 1: `engine/cache.py`에 `remove_grid_search_result` 추가

**Files:**
- Modify: `engine/cache.py` (`finish_grid_search_job` 함수 바로 다음에 새 함수 추가)
- Test: `tests/test_cache.py` (`test_finish_grid_search_job_marks_failed_with_error_message`
  함수 바로 다음에 새 테스트 4개 추가, import 블록에 함수 추가)

**Interfaces:**
- Consumes: 없음
- Produces: `remove_grid_search_result(job_id: str, run_id: str) -> bool` — Task 2가 새
  엔드포인트에서 이 함수를 호출한다.

- [x] **Step 1: import 블록에 새 함수 추가**

`tests/test_cache.py`의 아래 부분(20~26번째 줄 부근)을:

```python
from engine.cache import (
    create_grid_search_job,
    finish_grid_search_job,
    get_grid_search_job,
    list_grid_search_jobs,
    update_grid_search_job_progress,
)
```

아래로 교체:

```python
from engine.cache import (
    create_grid_search_job,
    finish_grid_search_job,
    get_grid_search_job,
    list_grid_search_jobs,
    remove_grid_search_result,
    update_grid_search_job_progress,
)
```

- [x] **Step 2: 실패하는 테스트 4개 작성**

`tests/test_cache.py`의 `test_finish_grid_search_job_marks_failed_with_error_message` 함수
(628번째 줄 부근, `assert job["result_json"] is None`으로 끝남) 바로 다음, 빈 줄 2개를
사이에 두고 아래 4개 테스트를 추가:

```python
def test_remove_grid_search_result_removes_matching_entry(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    create_grid_search_job(
        job_id="job-1", market="KRW-SOL", timeframe="minutes60", capital=1_000_000.0,
        start="2026-06-05", end="2026-08-03", top_n=20,
    )
    finish_grid_search_job(
        "job-1", status="completed", elapsed_sec=100.0,
        result_json=(
            '[{"rank": 1, "run_id": "run-a", "return_pct": 10.0, "title": "a"}, '
            '{"rank": 2, "run_id": "run-b", "return_pct": 5.0, "title": "b"}]'
        ),
    )

    assert remove_grid_search_result("job-1", "run-a") is True

    job = get_grid_search_job("job-1")
    assert job["result_json"] == [{"rank": 2, "run_id": "run-b", "return_pct": 5.0, "title": "b"}]


def test_remove_grid_search_result_returns_false_for_missing_run_id(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    create_grid_search_job(
        job_id="job-1", market="KRW-SOL", timeframe="minutes60", capital=1_000_000.0,
        start="2026-06-05", end="2026-08-03", top_n=20,
    )
    finish_grid_search_job(
        "job-1", status="completed", elapsed_sec=100.0,
        result_json='[{"rank": 1, "run_id": "run-a", "return_pct": 10.0, "title": "a"}]',
    )

    assert remove_grid_search_result("job-1", "does-not-exist") is False

    job = get_grid_search_job("job-1")
    assert job["result_json"] == [{"rank": 1, "run_id": "run-a", "return_pct": 10.0, "title": "a"}]


def test_remove_grid_search_result_returns_false_when_result_json_is_none(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    create_grid_search_job(
        job_id="job-1", market="KRW-SOL", timeframe="minutes60", capital=1_000_000.0,
        start="2026-06-05", end="2026-08-03", top_n=20,
    )

    assert remove_grid_search_result("job-1", "run-a") is False


def test_remove_grid_search_result_returns_false_for_missing_job(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    assert remove_grid_search_result("does-not-exist", "run-a") is False
```

- [x] **Step 3: 테스트 실행해서 실패 확인**

Run: `PYTHONPATH=. pytest tests/test_cache.py -k remove_grid_search_result -v`
Expected: 4개 모두 FAIL — `ImportError: cannot import name 'remove_grid_search_result'`
(아직 `engine/cache.py`에 함수가 없으므로)

- [x] **Step 4: `engine/cache.py`에 함수 구현**

`engine/cache.py`의 `finish_grid_search_job` 함수(590~608번째 줄 부근, `finally:
conn.close()`로 끝남) 바로 다음, 빈 줄 2개를 사이에 두고 아래 함수를 추가:

```python
def remove_grid_search_result(job_id: str, run_id: str) -> bool:
    """job_id의 저장된 결과 목록(result_json)에서 run_id 항목을 제거한다.
    제거된 항목이 있었으면 True를 반환한다."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT result_json FROM grid_search_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None or row[0] is None:
            return False
        results = json.loads(row[0])
        filtered = [r for r in results if r.get("run_id") != run_id]
        if len(filtered) == len(results):
            return False
        conn.execute(
            "UPDATE grid_search_jobs SET result_json = ? WHERE id = ?",
            (json.dumps(filtered), job_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()
```

- [x] **Step 5: 테스트 실행해서 통과 확인**

Run: `PYTHONPATH=. pytest tests/test_cache.py -k remove_grid_search_result -v`
Expected: 4개 모두 PASS

- [x] **Step 6: 전체 테스트 스위트 실행(회귀 확인)**

Run: `PYTHONPATH=. pytest tests/test_cache.py -v`
Expected: 기존 테스트 전부 PASS(신규 4개 포함, 총 개수 증가 외에 실패 없음)

- [x] **Step 7: 커밋**

```bash
git add engine/cache.py tests/test_cache.py
git commit -m "feat: grid search job의 저장된 결과 목록에서 개별 결과를 제거하는 remove_grid_search_result 추가"
```

---

### Task 2: `backend/main.py`에 결과 삭제 엔드포인트 추가

**Files:**
- Modify: `backend/main.py` (import 블록 + 파일 맨 끝에 새 엔드포인트 추가)
- Test: `tests/test_backend.py` (파일 맨 끝에 새 테스트 3개 추가)

**Interfaces:**
- Consumes: Task 1의 `remove_grid_search_result(job_id, run_id) -> bool`, 기존
  `delete_backtest_run(run_id) -> bool`, 기존 `get_grid_search_job(job_id) -> dict | None`
- Produces: `DELETE /api/v1/grid-search/jobs/{job_id}/results/{run_id}` — 성공 시
  `{"deleted": true}`, job 또는 결과가 없으면 404. Task 3의 `deleteGridSearchResult`가 이
  엔드포인트를 호출한다.

- [x] **Step 1: import 블록에 새 함수 추가**

`backend/main.py`의 아래 부분(17~32번째 줄 부근)을:

```python
from engine.cache import (
    delete_backtest_run,
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
    run_backtest_cached,
    save_result,
)
```

아래로 교체:

```python
from engine.cache import (
    delete_backtest_run,
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
)
```

- [x] **Step 2: 실패하는 테스트 3개 작성**

`tests/test_backend.py` 파일 맨 끝(1416번째 줄, `test_cancel_grid_search_job_returns_409_
when_not_active` 다음)에 빈 줄 2개를 사이에 두고 아래 3개 테스트를 추가:

```python
def test_delete_grid_search_result_removes_run_and_updates_job(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    from engine.cache import create_grid_search_job

    save_result(
        run_id="run-a", strategy_name="ConditionTreeStrategy", strategy_params={},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={"final_value": 10500.0, "sharpe": 1.0, "max_drawdown": 2.0, "equity_curve": [], "trades": []},
        title="[Grid] 매수 A / 매도 B",
    )
    create_grid_search_job(
        job_id="job-1", market="KRW-BTC", timeframe="days", capital=1_000_000.0,
        start="2026-01-01", end="2026-01-10", top_n=20,
    )
    finish_grid_search_job(
        "job-1", status="completed", elapsed_sec=10.0,
        result_json='[{"rank": 1, "run_id": "run-a", "return_pct": 5.0, "title": "[Grid] 매수 A / 매도 B"}]',
    )

    resp = client.delete("/api/v1/grid-search/jobs/job-1/results/run-a")
    assert resp.status_code == 200
    assert resp.json() == {"deleted": True}

    job_resp = client.get("/api/v1/grid-search/jobs")
    job = next(j for j in job_resp.json() if j["id"] == "job-1")
    assert job["result_json"] == []

    backtests_resp = client.get("/api/v1/backtests")
    assert backtests_resp.json() == []


def test_delete_grid_search_result_returns_404_for_missing_job(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    resp = client.delete("/api/v1/grid-search/jobs/does-not-exist/results/run-a")
    assert resp.status_code == 404


def test_delete_grid_search_result_returns_404_when_run_id_not_in_job(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    from engine.cache import create_grid_search_job

    create_grid_search_job(
        job_id="job-1", market="KRW-BTC", timeframe="days", capital=1_000_000.0,
        start="2026-01-01", end="2026-01-10", top_n=20,
    )
    finish_grid_search_job(
        "job-1", status="completed", elapsed_sec=10.0,
        result_json='[{"rank": 1, "run_id": "run-a", "return_pct": 5.0, "title": "x"}]',
    )

    resp = client.delete("/api/v1/grid-search/jobs/job-1/results/run-b")
    assert resp.status_code == 404
```

- [x] **Step 3: 테스트 실행해서 실패 확인**

Run: `PYTHONPATH=. pytest tests/test_backend.py -k delete_grid_search_result -v`
Expected: 3개 모두 FAIL — 첫 번째/세 번째는 404 대신 405(Method Not Allowed, 엔드포인트가
아직 없으므로), 두 번째도 405

- [x] **Step 4: `backend/main.py`에 엔드포인트 구현**

`backend/main.py` 파일 맨 끝(947번째 줄, `cancel_grid_search_job_endpoint` 함수의
`return {"status": "canceling"}`로 끝남)에 빈 줄 2개를 사이에 두고 아래 엔드포인트를 추가:

```python
@app.delete("/api/v1/grid-search/jobs/{job_id}/results/{run_id}")
def delete_grid_search_result_endpoint(job_id: str, run_id: str) -> dict:
    job = get_grid_search_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="해당 job_id의 grid search를 찾을 수 없습니다")
    results = job.get("result_json") or []
    if not any(r["run_id"] == run_id for r in results):
        raise HTTPException(status_code=404, detail="해당 job에 이 run_id의 결과가 없습니다")
    delete_backtest_run(run_id)
    remove_grid_search_result(job_id, run_id)
    return {"deleted": True}
```

- [x] **Step 5: 테스트 실행해서 통과 확인**

Run: `PYTHONPATH=. pytest tests/test_backend.py -k delete_grid_search_result -v`
Expected: 3개 모두 PASS

- [x] **Step 6: 전체 백엔드 테스트 스위트 실행(회귀 확인)**

Run: `PYTHONPATH=. pytest tests/ -v`
Expected: 전체 PASS(신규 7개 포함 — Task 1의 4개 + Task 2의 3개 — 실패 없음)

- [x] **Step 7: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "feat: 그리드서치 결과 개별 삭제 엔드포인트(DELETE /api/v1/grid-search/jobs/{job_id}/results/{run_id}) 추가"
```

---

### Task 3: 프론트엔드 API 함수 `deleteGridSearchResult` 추가

**Files:**
- Modify: `frontend/lib/api/eda.ts`

**Interfaces:**
- Consumes: Task 2의 `DELETE /api/v1/grid-search/jobs/{job_id}/results/{run_id}` 엔드포인트
- Produces: `deleteGridSearchResult(jobId: string, runId: string): Promise<{ deleted:
  boolean }>` — Task 4의 `GridSearchHistory.tsx`가 이 함수를 호출한다.

- [x] **Step 1: 함수 추가**

`frontend/lib/api/eda.ts`의 아래 부분(파일 맨 끝, `cancelGridSearchJob` 함수 다음)을:

```typescript
export function cancelGridSearchJob(jobId: string): Promise<{ status: string }> {
  return apiFetch(`/api/v1/grid-search/jobs/${jobId}/cancel`, { method: 'POST' });
}
```

아래로 교체(함수 하나 추가):

```typescript
export function cancelGridSearchJob(jobId: string): Promise<{ status: string }> {
  return apiFetch(`/api/v1/grid-search/jobs/${jobId}/cancel`, { method: 'POST' });
}

export function deleteGridSearchResult(jobId: string, runId: string): Promise<{ deleted: boolean }> {
  return apiFetch<{ deleted: boolean }>(`/api/v1/grid-search/jobs/${jobId}/results/${runId}`, {
    method: 'DELETE',
  });
}
```

- [x] **Step 2: 타입 체크 + lint**

Run: `cd frontend && npx tsc --noEmit && npx eslint lib/api/eda.ts`
Expected: 에러 없음

- [x] **Step 3: 백엔드가 떠 있는 상태에서 실제 호출 확인**

`uvicorn backend.main:app --reload --port 8000`이 떠 있는 상태에서, 브라우저 콘솔이나
`curl`로 존재하지 않는 job에 대해 호출해 404가 오는지 확인(Task 2에서 이미 pytest로
검증했으므로 이 단계는 배선이 실제로 연결됐는지 확인하는 가벼운 스모크 테스트):

Run: `curl -s -o /dev/null -w "%{http_code}\n" -X DELETE "http://localhost:8000/api/v1/grid-search/jobs/does-not-exist/results/run-a"`
Expected: `404`

- [x] **Step 4: 커밋**

```bash
git add frontend/lib/api/eda.ts
git commit -m "feat: 그리드서치 결과 삭제 API 함수 deleteGridSearchResult 추가"
```

---

### Task 4: `GridSearchHistory.tsx` 줄바꿈 + 전체 순위 펼침 + 체크박스 삭제

**Files:**
- Modify: `frontend/components/GridSearchHistory.tsx` (전면 재작성)
- Modify: `frontend/components/GridSearchPage.tsx` (한 줄 추가)

**Interfaces:**
- Consumes: Task 3의 `deleteGridSearchResult(jobId, runId)`, 기존 `Checkbox`
  (`@/components/ui/checkbox`), 기존 `AlertDialog` 계열(`@/components/ui/alert-dialog`),
  기존 `Button`/`buttonVariants`(`@/components/ui/button`), 기존 `GridSearchJob`/
  `GridSearchSavedResult` 타입, 기존 `parseGridResultTitle`/`returnRateColor`
- Produces: `GridSearchHistoryProps`에 `onRefresh: () => void | Promise<void>` 추가(기존
  `jobs` prop은 유지) — `GridSearchPage.tsx`가 기존 `refresh` 콜백을 이 prop으로 전달한다.

**배경:** 스펙의 (1) 매수/매도 줄바꿈, (2) 펼친 목록을 1위~N위 전체로 확장, (3)
체크박스+선택삭제 UI, (4) 삭제 후 새로고침 연결을 한 번에 구현한다. 이 파일은 이미
2026-08-04-grid-search-history-table 플랜에서 필터/정렬 가능한 표로 재작성된 상태이며,
이번 태스크는 그 위에 펼친 목록 부분만 다시 손댄다(필터/정렬/컬럼 구조는 그대로).

- [x] **Step 1: 파일 전체를 아래 내용으로 교체**

`frontend/components/GridSearchHistory.tsx`(전체 교체):

```tsx
'use client';

import { Fragment, useMemo, useState } from 'react';
import Link from 'next/link';
import { ArrowDown, ArrowUp, ArrowUpDown, ChevronDown, ChevronRight, Trash2 } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import BacktestCoinFilter, { type CoinFilterOption } from '@/components/BacktestCoinFilter';
import { returnRateColor } from '@/lib/return-rate-color';
import { formatDateTime, formatTimeframe, TIMEFRAME_CODES } from '@/lib/format';
import { parseGridResultTitle } from '@/lib/grid-result-title';
import { deleteGridSearchResult } from '@/lib/api/eda';
import type { GridSearchJob, GridSearchSavedResult } from '@/lib/types/eda';

const STATUS_LABEL: Record<GridSearchJob['status'], string> = {
  running: '진행중',
  completed: '완료',
  failed: '실패',
  canceled: '취소',
};

const STATUS_VARIANT: Record<GridSearchJob['status'], 'secondary' | 'default' | 'destructive' | 'outline'> = {
  running: 'secondary',
  completed: 'default',
  failed: 'destructive',
  canceled: 'outline',
};

const ALL_TIMEFRAMES = '__all__';

type SortKey = 'start' | 'started_at' | 'return_pct';
type SortDir = 'asc' | 'desc';

function sortValue(job: GridSearchJob, key: SortKey): string | number | null {
  if (key === 'start') return job.start;
  if (key === 'started_at') return job.started_at;
  return job.result_json?.[0]?.return_pct ?? null;
}

function sortJobs(list: GridSearchJob[], key: SortKey | null, dir: SortDir): GridSearchJob[] {
  if (!key) return list;
  const factor = dir === 'asc' ? 1 : -1;
  return [...list].sort((a, b) => {
    const av = sortValue(a, key);
    const bv = sortValue(b, key);
    if (av === null && bv === null) return 0;
    if (av === null) return 1;
    if (bv === null) return -1;
    if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * factor;
    return String(av).localeCompare(String(bv)) * factor;
  });
}

type Expansion =
  | { kind: 'error'; message: string }
  | { kind: 'results'; results: GridSearchSavedResult[] }
  | null;

function expansionFor(job: GridSearchJob): Expansion {
  if (job.status === 'failed' && job.error_message) {
    return { kind: 'error', message: job.error_message };
  }
  const results = job.result_json ?? [];
  if (results.length > 0) {
    return { kind: 'results', results };
  }
  return null;
}

function ResultTitle({ result }: { result: GridSearchSavedResult }) {
  const parsed = parseGridResultTitle(result.title);
  if (!parsed) return <>{result.title}</>;
  return (
    <div className="space-y-0.5">
      <div>
        <strong>매수</strong> {parsed.buyRest}
      </div>
      <div>
        <strong>매도</strong> {parsed.sellRest}
      </div>
    </div>
  );
}

interface GridSearchHistoryProps {
  jobs: GridSearchJob[];
  onRefresh: () => void | Promise<void>;
}

export default function GridSearchHistory({ jobs, onRefresh }: GridSearchHistoryProps) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [coinFilter, setCoinFilter] = useState<string | null>(null);
  const [timeframeFilterValue, setTimeframeFilterValue] = useState<string>(ALL_TIMEFRAMES);
  const [sortKey, setSortKey] = useState<SortKey | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>('desc');
  const [selected, setSelected] = useState<Record<string, Set<string>>>({});
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const [bulkError, setBulkError] = useState<string | null>(null);

  const timeframeFilter = timeframeFilterValue === ALL_TIMEFRAMES ? null : timeframeFilterValue;

  const historyJobs = useMemo(() => jobs.filter((j) => j.status !== 'running'), [jobs]);

  const coinOptions = useMemo<CoinFilterOption[]>(() => {
    const seen = new Set<string>();
    const options: CoinFilterOption[] = [];
    for (const j of historyJobs) {
      if (!seen.has(j.market)) {
        seen.add(j.market);
        options.push({ market: j.market });
      }
    }
    return options.sort((a, b) => a.market.localeCompare(b.market));
  }, [historyJobs]);

  const timeframeOptions = useMemo(() => {
    const present = new Set(historyJobs.map((j) => j.timeframe));
    return TIMEFRAME_CODES.filter((tf) => present.has(tf));
  }, [historyJobs]);

  const filtered = useMemo(() => {
    return historyJobs.filter((j) => {
      if (coinFilter && j.market !== coinFilter) return false;
      if (timeframeFilter && j.timeframe !== timeframeFilter) return false;
      return true;
    });
  }, [historyJobs, coinFilter, timeframeFilter]);

  const sorted = useMemo(() => sortJobs(filtered, sortKey, sortDir), [filtered, sortKey, sortDir]);

  function toggle(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === 'desc' ? 'asc' : 'desc'));
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
  }

  function SortIcon({ sortKeyOf }: { sortKeyOf: SortKey }) {
    if (sortKey !== sortKeyOf) return <ArrowUpDown className="size-3.5" />;
    return sortDir === 'desc' ? <ArrowDown className="size-3.5" /> : <ArrowUp className="size-3.5" />;
  }

  function toggleResultSelection(jobId: string, runId: string, checked: boolean) {
    setSelected((prev) => {
      const current = new Set(prev[jobId] ?? []);
      if (checked) current.add(runId);
      else current.delete(runId);
      return { ...prev, [jobId]: current };
    });
  }

  function toggleAllForJob(jobId: string, runIds: string[], checked: boolean) {
    setSelected((prev) => ({ ...prev, [jobId]: checked ? new Set(runIds) : new Set() }));
  }

  async function handleConfirmDelete() {
    if (!deleteTarget) return;
    const jobId = deleteTarget;
    const ids = Array.from(selected[jobId] ?? []);
    setBulkDeleting(true);
    setBulkError(null);
    const results = await Promise.allSettled(ids.map((runId) => deleteGridSearchResult(jobId, runId)));
    const failedCount = results.filter((r) => r.status === 'rejected').length;
    setBulkDeleting(false);
    if (failedCount > 0) {
      setBulkError(`${failedCount}건 삭제에 실패했습니다. 잠시 후 다시 시도해 주세요.`);
      return;
    }
    setSelected((prev) => {
      const next = { ...prev };
      delete next[jobId];
      return next;
    });
    setDeleteTarget(null);
    await onRefresh();
  }

  if (jobs.length === 0) {
    return <p className="text-sm text-muted-foreground">아직 실행한 grid search가 없습니다.</p>;
  }

  if (historyJobs.length === 0) {
    return <p className="text-sm text-muted-foreground">아직 완료된 이력이 없습니다.</p>;
  }

  return (
    <div>
      <h2 className="mb-2 text-sm font-semibold">요청 이력</h2>

      <div className="mb-3 flex flex-wrap items-center gap-3 rounded-md border bg-muted/30 px-3 py-2">
        <BacktestCoinFilter options={coinOptions} value={coinFilter} onChange={setCoinFilter} />
        <Select value={timeframeFilterValue} onValueChange={(value) => value !== null && setTimeframeFilterValue(value)}>
          <SelectTrigger className="w-40">
            <SelectValue>
              {(value: string | null) => (value && value !== ALL_TIMEFRAMES ? formatTimeframe(value) : '전체 봉타입')}
            </SelectValue>
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL_TIMEFRAMES}>전체 봉타입</SelectItem>
            {timeframeOptions.map((tf) => (
              <SelectItem key={tf} value={tf}>
                {formatTimeframe(tf)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {sorted.length === 0 ? (
        <p className="text-sm text-muted-foreground">조건에 맞는 이력이 없습니다.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-8" />
              <TableHead>상태</TableHead>
              <TableHead>코인</TableHead>
              <TableHead>봉타입</TableHead>
              <TableHead>
                <button type="button" className="flex items-center gap-1 hover:text-foreground" onClick={() => toggleSort('start')}>
                  기간 <SortIcon sortKeyOf="start" />
                </button>
              </TableHead>
              <TableHead>
                <button type="button" className="flex items-center gap-1 hover:text-foreground" onClick={() => toggleSort('started_at')}>
                  실행시각 <SortIcon sortKeyOf="started_at" />
                </button>
              </TableHead>
              <TableHead>1위 조건</TableHead>
              <TableHead>
                <button type="button" className="flex items-center gap-1 hover:text-foreground" onClick={() => toggleSort('return_pct')}>
                  1위 수익률 <SortIcon sortKeyOf="return_pct" />
                </button>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {sorted.map((job) => {
              const results = job.result_json ?? [];
              const top = results[0];
              const expansion = expansionFor(job);
              const expandable = expansion !== null;
              const isExpanded = expanded.has(job.id);
              const jobSelected = selected[job.id] ?? new Set<string>();

              return (
                <Fragment key={job.id}>
                  <TableRow
                    className={expandable ? 'cursor-pointer' : ''}
                    role={expandable ? 'button' : undefined}
                    tabIndex={expandable ? 0 : undefined}
                    aria-expanded={expandable ? isExpanded : undefined}
                    onClick={() => expandable && toggle(job.id)}
                    onKeyDown={(e) => {
                      if (!expandable) return;
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        toggle(job.id);
                      }
                    }}
                  >
                    <TableCell>
                      {expandable &&
                        (isExpanded ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />)}
                    </TableCell>
                    <TableCell>
                      <Badge variant={STATUS_VARIANT[job.status]}>{STATUS_LABEL[job.status]}</Badge>
                    </TableCell>
                    <TableCell>{job.market.replace('KRW-', '')}</TableCell>
                    <TableCell>{formatTimeframe(job.timeframe)}</TableCell>
                    <TableCell>
                      {job.start} ~ {job.end}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">{formatDateTime(job.started_at)}</TableCell>
                    <TableCell className="max-w-[320px] whitespace-normal">
                      {top ? (
                        <Link href={`/backtests/${top.run_id}`} className="underline" onClick={(e) => e.stopPropagation()}>
                          <ResultTitle result={top} />
                        </Link>
                      ) : (
                        <span className="text-muted-foreground">-</span>
                      )}
                    </TableCell>
                    <TableCell className="tabular-nums">
                      {top ? (
                        <span className={returnRateColor(top.return_pct)}>{top.return_pct.toFixed(2)}%</span>
                      ) : (
                        <span className="text-muted-foreground">-</span>
                      )}
                    </TableCell>
                  </TableRow>
                  {isExpanded && expansion?.kind === 'error' && (
                    <TableRow>
                      <TableCell colSpan={8} className="whitespace-normal text-sm text-destructive">
                        {expansion.message}
                      </TableCell>
                    </TableRow>
                  )}
                  {isExpanded && expansion?.kind === 'results' && (
                    <TableRow>
                      <TableCell colSpan={8}>
                        <div className="space-y-2">
                          <div className="flex items-center gap-3">
                            <div className="flex items-center gap-1.5">
                              <Checkbox
                                checked={jobSelected.size > 0 && jobSelected.size === expansion.results.length}
                                onCheckedChange={(checked) =>
                                  toggleAllForJob(
                                    job.id,
                                    expansion.results.map((r) => r.run_id),
                                    checked === true
                                  )
                                }
                                aria-label="이 job의 결과 전체 선택"
                              />
                              <span className="text-xs text-muted-foreground">전체 선택</span>
                            </div>
                            <Button
                              variant="destructive"
                              size="sm"
                              disabled={jobSelected.size === 0}
                              onClick={() => setDeleteTarget(job.id)}
                            >
                              <Trash2 className="size-3.5" />
                              선택 삭제{jobSelected.size > 0 ? ` (${jobSelected.size})` : ''}
                            </Button>
                          </div>
                          <div className="space-y-1">
                            {expansion.results.map((r) => (
                              <div key={r.run_id} className="flex items-start gap-2 text-sm">
                                <Checkbox
                                  className="mt-0.5"
                                  checked={jobSelected.has(r.run_id)}
                                  onCheckedChange={(checked) => toggleResultSelection(job.id, r.run_id, checked === true)}
                                  aria-label={`${r.rank}위 결과 선택`}
                                />
                                <span className="shrink-0 text-muted-foreground">{r.rank}위</span>
                                <span className={`shrink-0 ${returnRateColor(r.return_pct)}`}>{r.return_pct.toFixed(2)}%</span>
                                <Link href={`/backtests/${r.run_id}`} className="whitespace-normal underline">
                                  <ResultTitle result={r} />
                                </Link>
                              </div>
                            ))}
                          </div>
                        </div>
                      </TableCell>
                    </TableRow>
                  )}
                </Fragment>
              );
            })}
          </TableBody>
        </Table>
      )}

      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open) {
            setDeleteTarget(null);
            setBulkError(null);
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              선택한 {deleteTarget ? (selected[deleteTarget]?.size ?? 0) : 0}개의 그리드서치 결과를 삭제하시겠습니까?
            </AlertDialogTitle>
            <AlertDialogDescription>삭제 후에는 되돌릴 수 없습니다.</AlertDialogDescription>
          </AlertDialogHeader>
          {bulkError && <p className="text-sm text-destructive">{bulkError}</p>}
          <AlertDialogFooter>
            <AlertDialogCancel>취소</AlertDialogCancel>
            <AlertDialogAction onClick={handleConfirmDelete} disabled={bulkDeleting}>
              {bulkDeleting ? '삭제 중...' : '삭제'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
```

- [x] **Step 2: `GridSearchPage.tsx`에 `onRefresh` prop 전달**

`frontend/components/GridSearchPage.tsx`의 아래 부분을:

```tsx
      <GridSearchHistory jobs={jobs} />
```

아래로 교체:

```tsx
      <GridSearchHistory jobs={jobs} onRefresh={refresh} />
```

- [x] **Step 3: 타입 체크 + lint**

Run: `cd frontend && npx tsc --noEmit && npx eslint components/GridSearchHistory.tsx components/GridSearchPage.tsx`
Expected: 에러 없음

- [x] **Step 4: 실제 데이터로 수동 확인 (`npm run dev` + 실제 백엔드)**

`npm run dev`와 `uvicorn backend.main:app --reload --port 8000`이 떠 있는 상태에서(백엔드는
Task 1·2에서 코드를 바꿨으므로 `--reload`가 반영됐는지 확인하고, 안 됐으면 재시작한다 —
memory: 이 저장소에서 반복된 이슈) `/grid-search` 방문 후 아래를 확인한다:

1. 완료된 job(결과 2개 이상)을 펼쳤을 때, 각 결과의 조건이 "매수 ..." 줄과 "매도 ..." 줄로
   나뉘어 보이는지("... / ..." 한 줄이 아님).
2. 메인 행의 "1위 조건" 셀도 마찬가지로 매수/매도가 줄바꿈돼 보이는지.
3. 결과가 정확히 1개뿐인 완료 job이 있다면(없으면 이 항목은 코드 리뷰로 대체 —
   `expansionFor`의 `results.length > 0` 조건을 읽고 1개짜리도 펼침 대상이 되는지 확인)
   chevron이 보이고 클릭하면 그 1개 결과가 체크박스와 함께 펼쳐지는지.
4. 펼친 목록에서 결과 하나를 체크하면 "선택 삭제 (1)" 버튼이 활성화되는지, 클릭하면 확인
   다이얼로그가 뜨는지, "취소"를 누르면 아무것도 안 지워지고 닫히는지.
5. 다시 체크하고 "삭제" 확인 → 다이얼로그가 닫히고, 방금 지운 결과가 목록에서 사라지는지
   (페이지 새로고침 없이, `onRefresh` 호출로 최신화됨).
6. 1위 결과를 지웠다면, 메인 행의 "1위 조건"/"1위 수익률"이 남은 결과 중 다음 순위로
   자동으로 바뀌는지.
7. "전체 선택" 체크박스를 누르면 그 job의 모든 결과가 한 번에 선택/해제되는지, "선택 삭제
   (N)"의 N이 정확한지.
8. 결과가 2개 이상인 job 두 개를 동시에 펼쳐서 한쪽에서만 체크했을 때, 다른 job의 선택
   상태에 영향이 없는지.
9. 결과 제목의 링크(`/backtests/{run_id}`)를 클릭하면 체크박스나 삭제와 무관하게 정상적으로
   상세 페이지로 이동하는지.
10. 브라우저 콘솔에 에러/경고 없는지.

- [x] **Step 5: 커밋**

```bash
git add frontend/components/GridSearchHistory.tsx frontend/components/GridSearchPage.tsx
git commit -m "feat: 그리드서치 결과 매수/매도 줄바꿈 + 1위~N위 전체 펼침 + 체크박스 선택삭제 추가"
```

---

## 마무리 체크

- [x] `PYTHONPATH=. pytest tests/ -v` 전체 PASS(Task 1·2 신규 테스트 7개 포함)
- [x] `cd frontend && npx tsc --noEmit` 클린(3개 태스크 전부 반영 후)
- [x] `npm run dev` + 백엔드가 떠 있는 상태에서 `/grid-search` 탭 수동 확인(Task 4 Step 4의
  10개 항목 전부)
- [x] 브라우저 콘솔에 에러/경고 없는지 확인
