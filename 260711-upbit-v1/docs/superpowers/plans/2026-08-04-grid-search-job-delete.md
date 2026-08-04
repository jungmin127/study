# Grid Search Job 행 삭제 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/grid-search` 탭 "요청 이력" 표에서 완료/취소/실패 job 행을 통째로(완료 job이면
저장된 결과까지 함께) 삭제할 수 있는 휴지통 버튼을 추가한다.

**Architecture:** 백엔드에 새 삭제 함수(`engine/cache.py`)와 새 엔드포인트
(`backend/main.py`)를 추가해 `grid_search_jobs` 행을 지우고, 완료 job이면 그 전에 저장된
백테스트 결과들을 기존 `delete_backtest_run`으로 cascade 삭제한다. 프론트엔드는 새 API
함수 하나(`deleteGridSearchJob`)와 `GridSearchHistory.tsx`에 삭제 컬럼 + 확인 다이얼로그를
추가한다.

**Tech Stack:** FastAPI + SQLite(`engine/cache.py`), pytest(TDD), Next.js 14(App Router) +
TypeScript, 기존 shadcn 스타일 컴포넌트(`@/components/ui/alert-dialog`,
`@/components/ui/button`), 새 의존성 없음.

## Global Constraints

- 스펙: `docs/superpowers/specs/2026-08-04-grid-search-job-delete-design.md`(사용자 승인됨).
- 완료/취소/실패 세 상태 전부에 적용한다. 완료 job을 지우면 그 job이 저장해둔 결과
  (백테스트 run)들도 함께 삭제된다 — 부분 삭제 옵션 없음.
- 새 엔드포인트(`DELETE /api/v1/grid-search/jobs/{job_id}`)는 job이 없으면 404를 반환한다.
- job에 저장된 결과가 있으면 각각에 대해 기존 `delete_backtest_run(run_id)`를 호출한다 —
  반환값은 검사하지 않는다(best-effort, 기존 개별 결과 삭제 엔드포인트와 동일한 원칙).
- `delete_grid_search_job`은 `grid_search_jobs` 테이블에서 단일 `DELETE` 문으로 행 하나만
  지운다 — 공유 JSON 컬럼을 읽고 고치는 게 아니므로(개별 결과 삭제 때와 달리) 별도의
  트랜잭션/락 처리가 필요 없다.
- 기존 개별 결과 삭제 기능(`DELETE /api/v1/grid-search/jobs/{job_id}/results/{run_id}`,
  `remove_grid_search_result`, `GridSearchHistory.tsx`의 "선택 삭제" 다이얼로그)은 이
  플랜에서 변경하지 않는다 — job 행 삭제용 새 다이얼로그를 별도 인스턴스로 추가한다.
- 프론트엔드 검증은 `npx tsc --noEmit`과 `npx eslint <파일>`만 사용한다. **`npm run
  build`는 쓰지 않는다** — `npm run dev`가 떠 있는 상태에서 돌리면 `.next`가 깨지는
  알려진 문제가 있다(memory: upbit-frontend-tailwind-opacity-gotcha).
- 이 저장소에는 프론트엔드 자동 단위테스트 러너가 없다 — 프론트 검증은
  `tsc`/`eslint`/수동 브라우저 확인으로 한다(기존 관례). 백엔드는 pytest로 TDD한다(기존
  관례).
- 각 태스크는 순서 의존성이 있다: Task 2는 Task 1의 `delete_grid_search_job`을 import해서
  쓰므로 Task 1이 먼저 끝나야 한다. Task 4는 Task 2의 엔드포인트 계약과 Task 3의
  `deleteGridSearchJob` 함수를 그대로 쓰므로 Task 2·3이 먼저 끝나야 한다.

---

### Task 1: `engine/cache.py`에 `delete_grid_search_job` 추가

**Files:**
- Modify: `engine/cache.py` (`remove_grid_search_result` 함수 바로 다음에 새 함수 추가)
- Test: `tests/test_cache.py` (파일 맨 끝에 새 테스트 2개 추가, import 블록에 함수 추가)

**Interfaces:**
- Consumes: 없음
- Produces: `delete_grid_search_job(job_id: str) -> bool` — Task 2가 새 엔드포인트에서 이
  함수를 호출한다.

- [x] **Step 1: import 블록에 새 함수 추가**

`tests/test_cache.py`의 아래 부분(22~29번째 줄 부근)을:

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

아래로 교체:

```python
from engine.cache import (
    create_grid_search_job,
    delete_grid_search_job,
    finish_grid_search_job,
    get_grid_search_job,
    list_grid_search_jobs,
    remove_grid_search_result,
    update_grid_search_job_progress,
)
```

- [x] **Step 2: 실패하는 테스트 2개 작성**

`tests/test_cache.py` 파일 맨 끝(현재 마지막 테스트
`test_remove_grid_search_result_is_safe_under_concurrent_deletes`의 `assert job["result_json"]
== []`로 끝남)에 빈 줄 2개를 사이에 두고 아래 2개 테스트를 추가:

```python
def test_delete_grid_search_job_removes_the_job(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    create_grid_search_job(
        job_id="job-1", market="KRW-SOL", timeframe="minutes60", capital=1_000_000.0,
        start="2026-06-05", end="2026-08-03", top_n=20,
    )

    assert delete_grid_search_job("job-1") is True
    assert get_grid_search_job("job-1") is None


def test_delete_grid_search_job_returns_false_for_missing_job(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    assert delete_grid_search_job("does-not-exist") is False
```

- [x] **Step 3: 테스트 실행해서 실패 확인**

Run: `PYTHONPATH=. pytest tests/test_cache.py -k delete_grid_search_job -v`
Expected: 2개 모두 FAIL — `ImportError: cannot import name 'delete_grid_search_job'`(아직
`engine/cache.py`에 함수가 없으므로)

- [x] **Step 4: `engine/cache.py`에 함수 구현**

`engine/cache.py`의 `remove_grid_search_result` 함수(611~635번째 줄 부근, `finally:
conn.close()`로 끝남) 바로 다음, 빈 줄 2개를 사이에 두고 아래 함수를 추가:

```python
def delete_grid_search_job(job_id: str) -> bool:
    """job_id에 해당하는 grid search job 행을 삭제한다.
    삭제된 행이 있었으면 True를 반환한다."""
    conn = _connect()
    try:
        cur = conn.execute("DELETE FROM grid_search_jobs WHERE id = ?", (job_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
```

- [x] **Step 5: 테스트 실행해서 통과 확인**

Run: `PYTHONPATH=. pytest tests/test_cache.py -k delete_grid_search_job -v`
Expected: 2개 모두 PASS

- [x] **Step 6: 전체 테스트 스위트 실행(회귀 확인)**

Run: `PYTHONPATH=. pytest tests/test_cache.py -v`
Expected: 기존 테스트 전부 PASS(신규 2개 포함, 총 개수 증가 외에 실패 없음)

- [x] **Step 7: 커밋**

```bash
git add engine/cache.py tests/test_cache.py
git commit -m "feat: grid search job 행 자체를 삭제하는 delete_grid_search_job 추가"
```

---

### Task 2: `backend/main.py`에 job 삭제 엔드포인트 추가

**Files:**
- Modify: `backend/main.py` (import 블록 + 파일 맨 끝에 새 엔드포인트 추가)
- Test: `tests/test_backend.py` (파일 맨 끝에 새 테스트 3개 추가)

**Interfaces:**
- Consumes: Task 1의 `delete_grid_search_job(job_id) -> bool`, 기존
  `delete_backtest_run(run_id) -> bool`, 기존 `get_grid_search_job(job_id) -> dict | None`
- Produces: `DELETE /api/v1/grid-search/jobs/{job_id}` — 성공 시 `{"deleted": true}`, job이
  없으면 404. Task 3의 `deleteGridSearchJob`이 이 엔드포인트를 호출한다.

- [x] **Step 1: import 블록에 새 함수 추가**

`backend/main.py`의 아래 부분(17~33번째 줄 부근)을:

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

아래로 교체:

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
)
```

- [x] **Step 2: 실패하는 테스트 3개 작성**

`tests/test_backend.py` 파일 맨 끝(현재 마지막 테스트
`test_delete_grid_search_result_returns_404_when_run_id_not_in_job`의 `assert resp.status_code
== 404`로 끝남)에 빈 줄 2개를 사이에 두고 아래 3개 테스트를 추가:

```python
def test_delete_grid_search_job_removes_job_and_its_results(monkeypatch, tmp_path):
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

    resp = client.delete("/api/v1/grid-search/jobs/job-1")
    assert resp.status_code == 200
    assert resp.json() == {"deleted": True}

    jobs_resp = client.get("/api/v1/grid-search/jobs")
    assert jobs_resp.json() == []

    backtests_resp = client.get("/api/v1/backtests")
    assert backtests_resp.json() == []


def test_delete_grid_search_job_removes_job_with_no_results(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    from engine.cache import create_grid_search_job

    create_grid_search_job(
        job_id="job-1", market="KRW-BTC", timeframe="days", capital=1_000_000.0,
        start="2026-01-01", end="2026-01-10", top_n=20,
    )
    finish_grid_search_job("job-1", status="canceled")

    resp = client.delete("/api/v1/grid-search/jobs/job-1")
    assert resp.status_code == 200
    assert resp.json() == {"deleted": True}

    jobs_resp = client.get("/api/v1/grid-search/jobs")
    assert jobs_resp.json() == []


def test_delete_grid_search_job_returns_404_for_missing_job(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    resp = client.delete("/api/v1/grid-search/jobs/does-not-exist")
    assert resp.status_code == 404
```

- [x] **Step 3: 테스트 실행해서 실패 확인**

Run: `PYTHONPATH=. pytest tests/test_backend.py -k delete_grid_search_job -v`
Expected: 3개 모두 FAIL — `test_delete_grid_search_job_removes_job_and_its_results`와
`test_delete_grid_search_job_removes_job_with_no_results`는 405(Method Not Allowed, 이
경로에 DELETE 라우트가 아직 없으므로), `test_delete_grid_search_job_returns_404_for_missing
_job`는 라우트 자체가 없어 404(경로 매칭 실패로 인한 404 — 엔드포인트 구현 후에도 같은
상태코드지만 이유가 다름. Task 2의 이전 라운드에서도 같은 현상이 있었다).

- [x] **Step 4: `backend/main.py`에 엔드포인트 구현**

`backend/main.py` 파일 맨 끝(현재 마지막 함수
`delete_grid_search_result_endpoint`의 `return {"deleted": True}`로 끝남)에 빈 줄 2개를
사이에 두고 아래 엔드포인트를 추가:

```python
@app.delete("/api/v1/grid-search/jobs/{job_id}")
def delete_grid_search_job_endpoint(job_id: str) -> dict:
    job = get_grid_search_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="해당 job_id의 grid search를 찾을 수 없습니다")
    for result in job.get("result_json") or []:
        delete_backtest_run(result["run_id"])
    delete_grid_search_job(job_id)
    return {"deleted": True}
```

- [x] **Step 5: 테스트 실행해서 통과 확인**

Run: `PYTHONPATH=. pytest tests/test_backend.py -k delete_grid_search_job -v`
Expected: 3개 모두 PASS

- [x] **Step 6: 전체 백엔드 테스트 스위트 실행(회귀 확인)**

Run: `PYTHONPATH=. pytest tests/ -v`
Expected: 전체 PASS(신규 5개 포함 — Task 1의 2개 + Task 2의 3개 — 실패 없음)

- [x] **Step 7: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "feat: 그리드서치 job 행 삭제 엔드포인트(DELETE /api/v1/grid-search/jobs/{job_id}) 추가"
```

---

### Task 3: 프론트엔드 API 함수 `deleteGridSearchJob` 추가

**Files:**
- Modify: `frontend/lib/api/eda.ts`

**Interfaces:**
- Consumes: Task 2의 `DELETE /api/v1/grid-search/jobs/{job_id}` 엔드포인트
- Produces: `deleteGridSearchJob(jobId: string): Promise<{ deleted: boolean }>` — Task 4의
  `GridSearchHistory.tsx`가 이 함수를 호출한다.

- [x] **Step 1: 함수 추가**

`frontend/lib/api/eda.ts`의 아래 부분(파일 맨 끝, `deleteGridSearchResult` 함수 다음)을:

```typescript
export function deleteGridSearchResult(jobId: string, runId: string): Promise<{ deleted: boolean }> {
  return apiFetch<{ deleted: boolean }>(`/api/v1/grid-search/jobs/${jobId}/results/${runId}`, {
    method: 'DELETE',
  });
}
```

아래로 교체(함수 하나 추가):

```typescript
export function deleteGridSearchResult(jobId: string, runId: string): Promise<{ deleted: boolean }> {
  return apiFetch<{ deleted: boolean }>(`/api/v1/grid-search/jobs/${jobId}/results/${runId}`, {
    method: 'DELETE',
  });
}

export function deleteGridSearchJob(jobId: string): Promise<{ deleted: boolean }> {
  return apiFetch<{ deleted: boolean }>(`/api/v1/grid-search/jobs/${jobId}`, {
    method: 'DELETE',
  });
}
```

- [x] **Step 2: 타입 체크 + lint**

Run: `cd frontend && npx tsc --noEmit && npx eslint lib/api/eda.ts`
Expected: 에러 없음

- [x] **Step 3: 백엔드가 떠 있는 상태에서 실제 호출 확인**

`uvicorn backend.main:app --reload --port 8000`이 떠 있는 상태에서(Task 1·2에서 백엔드
코드를 바꿨으므로 `--reload`가 반영됐는지 확인, 안 됐으면 재시작 — 이 저장소에서 반복된
이슈):

Run: `curl -s -o /dev/null -w "%{http_code}\n" -X DELETE "http://localhost:8000/api/v1/grid-search/jobs/does-not-exist"`
Expected: `404`

- [x] **Step 4: 커밋**

```bash
git add frontend/lib/api/eda.ts
git commit -m "feat: 그리드서치 job 삭제 API 함수 deleteGridSearchJob 추가"
```

---

### Task 4: `GridSearchHistory.tsx`에 job 행 삭제 버튼 추가

**Files:**
- Modify: `frontend/components/GridSearchHistory.tsx` (전면 재작성)

**Interfaces:**
- Consumes: Task 3의 `deleteGridSearchJob(jobId)`, 기존 `AlertDialog` 계열
  (`@/components/ui/alert-dialog`), 기존 `Button`(`@/components/ui/button`), 기존
  `GridSearchJob` 타입, 기존 `onRefresh` prop(변경 없음)
- Produces: 없음(최상위 컴포넌트, `GridSearchPage.tsx`는 손대지 않는다 — `jobs`/`onRefresh`
  prop 시그니처 불변)

**배경:** 표에 "삭제" 컬럼을 추가하고, 각 job 행마다 휴지통 버튼 + 확인 다이얼로그(기존
"선택 삭제" 다이얼로그와는 별개의 새 `AlertDialog` 인스턴스)를 구현한다. 완료 job이고
저장된 결과가 있으면 "결과 N개도 함께 삭제됩니다"라는 문구를, 없으면 단순 확인 문구를
보여준다.

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
import { deleteGridSearchJob, deleteGridSearchResult } from '@/lib/api/eda';
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
    <>
      <strong>매수</strong> {parsed.buyRest} / <strong>매도</strong> {parsed.sellRest}
    </>
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
  const [jobDeleteTarget, setJobDeleteTarget] = useState<string | null>(null);
  const [jobDeleteBusy, setJobDeleteBusy] = useState(false);
  const [jobDeleteError, setJobDeleteError] = useState<string | null>(null);

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

  const jobDeleteJob = useMemo(() => jobs.find((j) => j.id === jobDeleteTarget) ?? null, [jobs, jobDeleteTarget]);
  const jobDeleteResultCount = jobDeleteJob?.result_json?.length ?? 0;

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
    const failedIds = ids.filter((_, i) => results[i].status === 'rejected');
    setBulkDeleting(false);
    await onRefresh();
    if (failedIds.length > 0) {
      setSelected((prev) => ({ ...prev, [jobId]: new Set(failedIds) }));
      setBulkError(`${failedIds.length}건 삭제에 실패했습니다. 잠시 후 다시 시도해 주세요.`);
      return;
    }
    setSelected((prev) => {
      const next = { ...prev };
      delete next[jobId];
      return next;
    });
    setDeleteTarget(null);
  }

  async function handleConfirmJobDelete() {
    if (!jobDeleteTarget) return;
    const jobId = jobDeleteTarget;
    setJobDeleteBusy(true);
    setJobDeleteError(null);
    try {
      await deleteGridSearchJob(jobId);
      setJobDeleteBusy(false);
      setJobDeleteTarget(null);
      await onRefresh();
    } catch {
      setJobDeleteBusy(false);
      setJobDeleteError('삭제에 실패했습니다. 잠시 후 다시 시도해 주세요.');
    }
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
              <TableHead>삭제</TableHead>
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
                    <TableCell>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                        onClick={(e) => {
                          e.stopPropagation();
                          setJobDeleteTarget(job.id);
                        }}
                        aria-label="이 grid search 이력 삭제"
                      >
                        <Trash2 className="size-3.5" />
                      </Button>
                    </TableCell>
                  </TableRow>
                  {isExpanded && expansion?.kind === 'error' && (
                    <TableRow>
                      <TableCell colSpan={9} className="whitespace-normal text-sm text-destructive">
                        {expansion.message}
                      </TableCell>
                    </TableRow>
                  )}
                  {isExpanded && expansion?.kind === 'results' && (
                    <TableRow>
                      <TableCell colSpan={9}>
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
                          <div className="grid grid-cols-[auto_auto_auto_auto_auto_auto] items-center gap-x-3 gap-y-1 text-sm">
                            {expansion.results.map((r) => {
                              const parsed = parseGridResultTitle(r.title);
                              return (
                                <Fragment key={r.run_id}>
                                  <Checkbox
                                    checked={jobSelected.has(r.run_id)}
                                    onCheckedChange={(checked) => toggleResultSelection(job.id, r.run_id, checked === true)}
                                    aria-label={`${r.rank}위 결과 선택`}
                                  />
                                  <span className="text-muted-foreground">{r.rank}위</span>
                                  <span className={returnRateColor(r.return_pct)}>{r.return_pct.toFixed(2)}%</span>
                                  {parsed ? (
                                    <>
                                      <span>
                                        <strong>매수</strong> {parsed.buyRest}
                                      </span>
                                      <span>
                                        <strong>매도</strong> {parsed.sellRest}
                                      </span>
                                    </>
                                  ) : (
                                    <span className="col-span-2">{r.title}</span>
                                  )}
                                  <Link href={`/backtests/${r.run_id}`} className="underline">
                                    보기
                                  </Link>
                                </Fragment>
                              );
                            })}
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

      <AlertDialog
        open={jobDeleteTarget !== null}
        onOpenChange={(open) => {
          if (!open) {
            setJobDeleteTarget(null);
            setJobDeleteError(null);
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {jobDeleteResultCount > 0
                ? `이 grid search 이력과 저장된 결과 ${jobDeleteResultCount}개를 모두 삭제하시겠습니까?`
                : '이 grid search 이력을 삭제하시겠습니까?'}
            </AlertDialogTitle>
            <AlertDialogDescription>삭제 후에는 되돌릴 수 없습니다.</AlertDialogDescription>
          </AlertDialogHeader>
          {jobDeleteError && <p className="text-sm text-destructive">{jobDeleteError}</p>}
          <AlertDialogFooter>
            <AlertDialogCancel>취소</AlertDialogCancel>
            <AlertDialogAction onClick={handleConfirmJobDelete} disabled={jobDeleteBusy}>
              {jobDeleteBusy ? '삭제 중...' : '삭제'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
```

- [x] **Step 2: 타입 체크 + lint**

Run: `cd frontend && npx tsc --noEmit && npx eslint components/GridSearchHistory.tsx`
Expected: 에러 없음

- [x] **Step 3: 실제 데이터로 수동 확인 (`npm run dev` + 실제 백엔드)**

`npm run dev`와 `uvicorn backend.main:app --reload --port 8000`이 떠 있는 상태에서(백엔드는
Task 1·2에서 코드를 바꿨으므로 `--reload`가 반영됐는지 확인하고, 안 됐으면 재시작한다)
`/grid-search` 방문 후 아래를 확인한다:

1. 표 맨 끝에 "삭제" 컬럼과 각 행의 휴지통 버튼이 보이는지.
2. 취소(canceled) job의 휴지통을 클릭하면 확인 다이얼로그가 뜨는지, 문구가 "이 grid
   search 이력을 삭제하시겠습니까?"(결과 개수 언급 없음)인지.
3. "취소"를 누르면 아무것도 안 지워지고 닫히는지, 다시 클릭해서 "삭제"를 누르면 그 행이
   목록에서 사라지는지(페이지 새로고침 없이).
4. 실패(failed) job도 마찬가지로 지워지는지 확인.
5. 결과가 있는 완료(completed) job의 휴지통을 클릭하면 다이얼로그 문구에 정확한 결과
   개수가 들어가는지(예: "결과 20개를 모두 삭제하시겠습니까?"), 삭제 확인 후 그 job과
   결과들이 모두 사라지는지(그 job에 속했던 결과의 `/backtests/{run_id}` 링크가 더 이상
   목록에 없는지).
6. 휴지통 버튼 클릭이 행의 펼치기/접기 토글을 같이 발동시키지 않는지(펼쳐진 상태에서
   클릭해도 접히지 않아야 함, 안 펼쳐진 상태에서 클릭해도 안 펼쳐져야 함).
7. 기존 "선택 삭제"(개별 결과) 다이얼로그와 이번 "이력 삭제" 다이얼로그가 서로 섞이지
   않는지 — 한 job에서 결과 몇 개를 체크해서 "선택 삭제" 다이얼로그를 띄운 상태에서 다른
   job의 휴지통을 누르면 무슨 일이 일어나는지도 확인(두 다이얼로그 상태가 독립적이므로
   전자를 닫지 않고 후자가 열리는 게 정상 — base-ui `AlertDialog`가 동시에 두 개 열리는
   경우의 렌더링이 이상하지 않은지 눈으로 확인).
8. 브라우저 콘솔에 에러/경고 없는지.

- [x] **Step 4: 커밋**

```bash
git add frontend/components/GridSearchHistory.tsx
git commit -m "feat: 그리드서치 요청 이력에 job 행 삭제(휴지통) 버튼 추가"
```

---

## 마무리 체크

- [x] `PYTHONPATH=. pytest tests/ -v` 전체 PASS(Task 1·2 신규 테스트 5개 포함)
- [x] `cd frontend && npx tsc --noEmit` 클린(3개 태스크 전부 반영 후)
- [x] `npm run dev` + 백엔드가 떠 있는 상태에서 `/grid-search` 탭 수동 확인(Task 4 Step 3의
  8개 항목 전부)
- [x] 브라우저 콘솔에 에러/경고 없는지 확인
