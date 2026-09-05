# Grid Search 결과 개별 삭제 + 매수/매도 줄바꿈 — Design Spec

## 배경 및 목표

`frontend/components/GridSearchHistory.tsx`(`/grid-search` 탭 "요청 이력")에서 완료된
job의 행을 클릭하면 저장된 결과가 순위별로 펼쳐진다(현재는 1위를 제외한 2위~N위, 최대
`top_n - 1`개). 사용자 요청 사항:

1. 펼친 결과 목록에서 "매수"/"매도" 조건이 한 줄에 이어져 있어 읽기 어렵다 — 매수 조건과
   매도 조건을 줄바꿈해서 각각 한 줄씩 보여준다.
2. 펼친 결과 목록의 각 줄 맨 앞에 체크박스를 두고, 원하는 결과만 선택해서 영구 삭제할 수
   있어야 한다 — 백테스트 결과 탭(`BacktestRunsTable.tsx`)의 체크박스+선택삭제와 동일한
   패턴.

## 범위

- `frontend/components/GridSearchHistory.tsx`: `ResultTitle` 줄바꿈, 펼친 결과 목록을
  1위~N위 전체로 확장, 체크박스+선택삭제 UI 추가.
- `frontend/components/GridSearchPage.tsx`: 기존 `refresh` 콜백을 `GridSearchHistory`에
  `onRefresh` prop으로 전달(한 줄 추가).
- `frontend/lib/api/eda.ts`: `deleteGridSearchResult(jobId, runId)` 추가.
- `engine/cache.py`: `remove_grid_search_result(job_id, run_id)` 추가 — job의
  `result_json`에서 해당 결과 항목을 제거.
- `backend/main.py`: `DELETE /api/v1/grid-search/jobs/{job_id}/results/{run_id}` 엔드포인트
  추가.
- `scripts/grid_search.py`의 title 형식, `frontend/lib/grid-result-title.ts`의 파싱 로직은
  변경 없음(줄바꿈은 렌더링 방식만 바뀜, 파싱 결과 `{buyRest, sellRest}` 자체는 그대로).

## 1. 매수/매도 줄바꿈

`ResultTitle`을 인라인 조합에서 2줄 블록으로 변경:

```tsx
function ResultTitle({ result }: { result: GridSearchSavedResult }) {
  const parsed = parseGridResultTitle(result.title);
  if (!parsed) return <>{result.title}</>;
  return (
    <div className="space-y-0.5">
      <div><strong>매수</strong> {parsed.buyRest}</div>
      <div><strong>매도</strong> {parsed.sellRest}</div>
    </div>
  );
}
```

`null` 폴백(형식이 안 맞는 title)은 기존과 동일하게 원본 문자열을 그대로 보여준다(줄바꿈
없음 — 파싱이 안 된 문자열을 임의로 나눌 방법이 없으므로).

적용 위치 2곳 모두 동일하게 반영:
- 메인 행 "1위 조건" 셀: `max-w-[320px] truncate`(1줄 말줄임) → `max-w-[320px]
  whitespace-normal`(`BacktestRunsTable.tsx`의 매수/매도전략 셀과 동일한 패턴)로 변경.
- 펼친 결과 목록의 각 줄: 마찬가지로 `truncate` → `whitespace-normal`.

## 2. 펼친 목록을 1위~N위 전체로 확장

현재 `expansionFor()`는 완료 job의 경우 `results.slice(1)`(2위부터)만 펼침 대상으로
돌려주고, 결과가 1개뿐인 완료 job은 펼칠 수 없다(1위는 메인 행에서만 보임). 삭제 기능은
1위를 포함한 모든 결과에 적용돼야 하므로:

```typescript
function expansionFor(job: GridSearchJob): Expansion {
  if (job.status === 'failed' && job.error_message) {
    return { kind: 'error', message: job.error_message };
  }
  const results = job.result_json ?? [];
  if (results.length > 0) {
    return { kind: 'results', results }; // slice(1) 제거 — 1위부터 전체
  }
  return null;
}
```

영향:
- 결과가 정확히 1개뿐인 완료 job도 이제 펼칠 수 있게 된다(그 1개를 지울 수 있어야 하므로).
  chevron이 보이고 클릭 가능해짐 — 이전 스펙/리뷰에서 "결과 1개면 확장 불가"였던 동작이
  의도적으로 바뀐다.
- 취소(canceled) job은 여전히 확장 불가(취소 job은 항상 `result_json = NULL`이므로
  `results.length`가 0 — 기존과 동일하게 걸러짐, 명시적으로 `status === 'canceled'`를
  검사할 필요 없음).
- 메인 행의 "1위 조건"/"1위 수익률" 셀은 그대로 `results[0]`을 보여준다(펼친 목록과 별개로
  항상 존재하는 요약 미리보기) — 1위가 삭제되면 다음 남은 항목이 자동으로 그 자리를
  대체한다(별도 로직 불필요, `results[0]`이 배열에서 알아서 다음 항목이 됨).

## 3. 체크박스 + 선택 삭제 UI

`BacktestRunsTable.tsx`와 동일한 패턴(체크박스 → "선택 삭제 (N)" 버튼 → `AlertDialog` 확인
→ `Promise.allSettled` 병렬 삭제 → 실패 건수 있으면 다이얼로그 내 인라인 에러 → 완료 후
선택 초기화 + `onRefresh()`)을 재사용하되, **삭제는 job 단위로 스코프**된다(여러 job을
동시에 펼쳐도 서로 섞이지 않음).

**상태:**
- `selected: Record<string, Set<string>>` — job id → 선택된 `run_id` 집합.
- `deleteTarget: string | null` — 확인 다이얼로그가 열려 있는 대상 job id(다이얼로그는
  컴포넌트에 하나만 두고, 열 때마다 대상 job을 바꿔 낌 — `BacktestRunsTable`처럼 표 하나에
  다이얼로그 하나인 구조가 아니라 job마다 별도 다이얼로그 인스턴스를 두지 않기 위함).
- `bulkDeleting: boolean`, `bulkError: string | null` — 다이얼로그 하나만 쓰므로 공유.

**펼친 결과 블록(`expansion.kind === 'results'`) 레이아웃:**

```
[전체선택 체크박스]                              [선택 삭제 (N)]
[ ] 1위  +17.30%  매수 STOCH_K{...}<10 / 매도 ...  [보기]
[ ] 2위  +12.61%  매수 ...                          [보기]
...
```

- 툴바(전체선택 체크박스 + "선택 삭제 (N)" 버튼)는 이 job의 결과가 1개 이상일 때만 표시.
- 각 결과 줄: 체크박스, `{rank}위`, 색상 있는 수익률, `ResultTitle`(줄바꿈 적용), 결과
  링크(`/backtests/{run_id}`) — 링크 클릭은 기존과 동일하게 `stopPropagation`으로 행 확장
  토글과 분리(이 목록 자체는 이미 펼쳐진 상태이므로 부모 job 행 토글과는 무관하지만, 체크박스
  클릭이 실수로 다른 이벤트를 트리거하지 않도록 각 인터랙티브 요소는 자기 클릭만 처리).
- 순위(`{rank}위`) 라벨은 삭제 후에도 재번호를 매기지 않는다 — `result_json`에 저장된 원래
  순위를 그대로 보여준다(2위를 지워도 남은 3위는 계속 "3위").

**삭제 실행 흐름:**
1. 사용자가 특정 job의 결과 몇 개를 체크 → "선택 삭제 (N)" 클릭 → `deleteTarget =
   job.id`로 설정, 다이얼로그 오픈.
2. 다이얼로그에서 "삭제" 확인 → `selected[job.id]`의 각 `run_id`에 대해
   `deleteGridSearchResult(job.id, runId)`를 `Promise.allSettled`로 병렬 호출.
3. 성공/실패 여부와 무관하게 `await onRefresh()`를 먼저 호출해 부모의 `jobs` 상태를
   최신화한다 — 성공한 항목은 새로고침 없이도 즉시 목록에서 사라진다(부분 실패 시에도
   이미 지워진 항목이 화면에 유령처럼 남지 않도록).
4. 실패 건수가 있으면 실패한 `run_id`만 남겨 해당 job의 선택 상태를 좁히고(성공한 항목은
   선택에서 제거), 다이얼로그 안에 인라인 에러 표시(`BacktestRunsTable`과 동일 문구 패턴:
   "N건 삭제에 실패했습니다. 잠시 후 다시 시도해 주세요."), 다이얼로그는 열어둔 채 유지 —
   재시도하면 실패했던 항목만 다시 시도한다(이미 지워진 항목을 다시 삭제 시도해 404
   루프에 빠지는 것을 방지).
5. 전부 성공하면 다이얼로그 닫고, 해당 job의 선택 상태 초기화.
6. 모든 결과가 삭제돼 `result_json`이 빈 배열이 되는 경우도 허용한다 — 이 job은 이후
   `expansionFor()`가 `null`을 돌려주므로 chevron이 사라지고, 메인 행의 "1위 조건"/"1위
   수익률"은 기존에 이미 있는 "결과 없음"(`-`) 처리 경로를 그대로 탄다(별도 분기 불필요).

## 4. 백엔드: 영구 삭제

**`engine/cache.py` — `remove_grid_search_result(job_id: str, run_id: str) -> bool`**

```python
def remove_grid_search_result(job_id: str, run_id: str) -> bool:
    """job_id의 저장된 결과 목록(result_json)에서 run_id 항목을 제거한다.
    제거된 항목이 있었으면 True."""
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

> **구현 후 수정(최종 리뷰에서 발견):** 위 함수는 SELECT → 필터링 → UPDATE가 원자적이지
> 않다. 프론트엔드가 "전체 선택 후 선택 삭제"로 여러 `run_id`를 동시에(`Promise.allSettled`)
> 지우면, 같은 job 행을 두 개 이상의 요청이 동시에 읽고 쓰면서 먼저 커밋된 삭제가 나중
> 커밋에 덮어써지는 lost-update가 실제로 재현됐다(20개 동시 삭제 시 일부만 반영됨). 이를
> 막기 위해 `conn.execute("BEGIN IMMEDIATE")`를 `SELECT` 직전에 추가해 같은 job에 대한
> 동시 쓰기를 직렬화한다 — 커밋된 최종 코드는 `engine/cache.py`를 참고.

**`backend/main.py` — `DELETE /api/v1/grid-search/jobs/{job_id}/results/{run_id}`**

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

- `job_id`/`run_id` 존재 여부를 이 job의 `result_json` 안에서 먼저 확인한다 — 이 엔드포인트가
  임의의 `run_id`를 삭제하는 뒷문이 되지 않도록(그리드서치가 만든 결과가 아니면 404).
- `delete_backtest_run(run_id)`의 반환값(`bool`)은 검사하지 않는다 — 이미 다른 경로로
  삭제돼 있어도(예: 백테스트 결과 탭에서 먼저 지운 경우) `remove_grid_search_result`로
  `result_json`의 남은 참조만 정리하면 되는, best-effort 정리 성격의 호출이기 때문.
- `remove_grid_search_result`가 `False`를 돌려주는 경우(위 존재 확인을 통과했는데 그 사이
  다른 요청이 먼저 지운 극히 드문 race)도 별도 에러로 취급하지 않는다 — 최종 상태(그
  `run_id`가 `result_json`에 없음)는 어차피 원하는 상태와 같으므로 `{"deleted": True}`를
  그대로 반환.

**`frontend/lib/api/eda.ts`**

```typescript
export function deleteGridSearchResult(jobId: string, runId: string): Promise<{ deleted: boolean }> {
  return apiFetch<{ deleted: boolean }>(`/api/v1/grid-search/jobs/${jobId}/results/${runId}`, {
    method: 'DELETE',
  });
}
```

## 5. 새로고침 연결

`GridSearchHistory`는 `jobs`를 prop으로만 받고 자체 fetch가 없다(부모
`GridSearchPage.tsx`가 폴링/새로고침을 전담). 삭제 후 최신 상태를 반영하려면 부모의 기존
`refresh` 콜백을 새 prop으로 내려받아야 한다.

```typescript
interface GridSearchHistoryProps {
  jobs: GridSearchJob[];
  onRefresh: () => void | Promise<void>;
}
```

`GridSearchPage.tsx` 변경(한 줄):

```tsx
<GridSearchHistory jobs={jobs} onRefresh={refresh} />
```

`refresh`는 이미 `GridSearchPage.tsx`에 존재하는 `useCallback`이며(폴링/취소 후 새로고침에
쓰임), 이번 변경으로 새로 만들 필요 없이 그대로 전달만 하면 된다.

## 고려했지만 채택하지 않은 대안

`result_json`을 고정 스냅샷으로 계속 저장하는 대신, `backtest_runs` 테이블과 JOIN해서
매 조회 시 동적으로 필터링하는 방법도 검토했다(그러면 백테스트 결과 탭에서 직접 지운
경우에도 자동으로 반영됨). 하지만 `list_grid_search_jobs`/`get_grid_search_job`의 반환
로직을 더 크게 바꿔야 하고, `return_pct`(그리드서치 시점 값) vs `return_rate`(백테스트
결과 탭의 값) 필드 불일치 가능성도 검토가 더 필요해 이번 스코프에서는 제외 — 대신
`result_json`을 직접 patch하는 단순한 접근을 택했다(사용자 승인됨).

## 자기 검토(스펙 완성도)

- 플레이스홀더/TBD 없음.
- 1위 포함 전체를 펼침 대상으로 바꾸는 것이 이전 스펙(2026-08-04-grid-search-history-table
  -design.md)의 "결과 1개면 확장 불가" 규칙과 상충되는데, 이는 이번 스펙에서 의도적으로
  덮어쓰는 변경임을 명시했다(§2).
- 삭제 스코프(job 단위), 순위 재번호 매기지 않음, 전체 삭제 시 빈 배열 허용 등 모호할 수
  있는 지점을 모두 명시적으로 결정했다.
- 백엔드 삭제 엔드포인트가 임의 run_id 삭제 뒷문이 되지 않도록 소유권 확인 로직을 포함했다.
- 스코프가 기존 컴포넌트 1개 확장 + 새 API 함수 1개 + 새 백엔드 함수/엔드포인트 각 1개로
  좁아 단일 구현 플랜으로 처리 가능.
