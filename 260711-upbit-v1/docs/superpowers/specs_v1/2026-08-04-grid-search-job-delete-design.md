# Grid Search Job 행 삭제 — Design Spec

## 배경 및 목표

`/grid-search` 탭의 "요청 이력" 표(`frontend/components/GridSearchHistory.tsx`)에는 이미
job 하나의 개별 저장 결과(1위~N위)를 체크박스로 골라 삭제하는 기능이 있다(2026-08-04
grid-search-result-delete 플랜). 하지만 이 삭제는 완료(completed) job 안의 결과에만
적용되고, **job 행 자체를 지우는 방법이 없다** — 특히 취소(canceled)/실패(failed) job은
저장된 결과가 아예 없어서(`result_json = NULL`) 펼칠 수도, 개별 결과를 지울 수도 없다.
사용자가 KRW-BTC 이력을 보다가 취소/실패 행을 지울 방법이 없다는 걸 발견했다.

## 범위

- `engine/cache.py`: `delete_grid_search_job(job_id)` 추가.
- `backend/main.py`: `DELETE /api/v1/grid-search/jobs/{job_id}` 추가.
- `frontend/lib/api/eda.ts`: `deleteGridSearchJob(jobId)` 추가.
- `frontend/components/GridSearchHistory.tsx`: 표에 "삭제" 컬럼 추가, 행마다 휴지통 버튼 +
  확인 다이얼로그.
- 기존 결과 개별 삭제 기능(`DELETE /api/v1/grid-search/jobs/{job_id}/results/{run_id}`,
  `remove_grid_search_result`)은 변경하지 않는다 — 이번 기능은 그 위에 "job 행 전체 삭제"를
  추가하는 것이다.

## 적용 범위

완료/취소/실패 세 상태 전부에 적용한다(진행중 job은 애초에 이 표에서 제외되므로 대상
아님). 완료 job을 지우면 그 job이 저장해둔 결과(백테스트 run)들도 함께 삭제된다 —
"이력만 지우고 결과는 남기기" 같은 부분 삭제 옵션은 없다(사용자 승인, 기존 개별 결과
삭제로 이미 그런 세밀한 제어가 가능하므로 job 전체 삭제는 "통째로 지우기"로 단순하게
간다).

## 1. UI — 행마다 휴지통 버튼

표의 마지막 컬럼으로 "삭제"를 추가한다(현재 8컬럼 → 9컬럼: `w-8`(chevron)/상태/코인/
봉타입/기간/실행시각/1위 조건/1위 수익률/**삭제**).

```
[▶] [상태] [코인] [봉타입] [기간] [실행시각] [1위 조건] [1위 수익률] [🗑]
```

- 각 행 우측에 `Trash2` 아이콘 버튼(`variant="ghost"` `size="icon"`, 기존
  `BacktestRunsTable`류에서 쓰는 아이콘 버튼 톤과 동일하게 destructive 색상 텍스트).
- 클릭 시 `onClick={(e) => { e.stopPropagation(); setJobDeleteTarget(job.id); }}` —
  행 자체의 클릭(펼치기 토글)이 같이 발동하지 않도록 반드시 `stopPropagation`.
- 클릭하면 확인 다이얼로그가 뜬다. 문구는 대상 job의 저장 결과 개수에 따라 달라진다:
  - 결과가 1개 이상(완료 job): "이 grid search 이력과 저장된 결과 N개를 모두
    삭제하시겠습니까?"
  - 결과가 없음(취소/실패, 또는 결과가 이미 다 지워진 완료 job): "이 grid search
    이력을 삭제하시겠습니까?"
  - 공통: "삭제 후에는 되돌릴 수 없습니다." (기존 다이얼로그들과 동일한 하단 설명 문구)
- 확인 시 `deleteGridSearchJob(job.id)` 호출 → 성공하면 다이얼로그 닫고 `onRefresh()`
  호출(기존 결과 삭제와 동일하게 새로고침 없이 목록에서 즉시 사라짐) → 실패하면 다이얼로그
  안에 인라인 에러 표시하고 열어둔 채 유지(재시도 가능).
- 이 새 다이얼로그는 기존 "선택 삭제(개별 결과)" 다이얼로그와 별개의 `AlertDialog`
  인스턴스로 둔다 — 서로 다른 대상(job 전체 vs 결과 몇 개)과 문구를 가지므로 하나로
  합치면 조건 분기가 복잡해진다. 상태도 별도(`jobDeleteTarget: string | null`,
  `jobDeleteBusy: boolean`, `jobDeleteError: string | null`).
- 확장(펼치기) 상태(`expanded`)나 결과 선택 상태(`selected`)에 지워진 job의 id가 남아있어도
  문제 없다 — `onRefresh()` 이후 그 job이 `jobs` 배열에서 사라지므로 `sorted.map()`이 더
  이상 그 id로 아무것도 렌더링하지 않는다(기존 결과 삭제 기능에서도 동일하게 동작하던
  방식, 별도 정리 로직 불필요).

## 2. 백엔드

**`engine/cache.py` — `delete_grid_search_job(job_id: str) -> bool`**

`remove_grid_search_result` 함수 바로 다음에 추가. `grid_search_jobs` 테이블에서 해당 행만
삭제하는 단순한 단일 `DELETE` 문이므로(공유 JSON 컬럼을 읽고-고치고-쓰는 게 아니라 행 자체를
지우는 것) 별도 트랜잭션/락 처리가 필요 없다 — SQLite에서 단일 `DELETE` 문 자체가 이미
원자적이다.

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

**`backend/main.py` — `DELETE /api/v1/grid-search/jobs/{job_id}`**

기존 `DELETE /api/v1/grid-search/jobs/{job_id}/results/{run_id}` 엔드포인트 바로 다음에
추가(파일 맨 끝).

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

- job이 없으면 404.
- job에 저장된 결과가 있으면(완료 job) 각각에 대해 기존 `delete_backtest_run(run_id)`를
  호출해 실제 백테스트 결과(backtest_runs/backtest_results)를 먼저 지운다 — 반환값은
  검사하지 않는다(기존 개별 결과 삭제 엔드포인트와 동일한 best-effort 정리 원칙: 이미
  다른 경로로 지워져 있어도 넘어간다).
- 마지막으로 `delete_grid_search_job(job_id)`로 job 행 자체를 지운다.
- 결과가 없는 job(취소/실패)은 반복문이 그냥 0번 돌고 바로 job 행만 지워진다.
- **동시성 주의사항(참고, 이번 기능에는 해당 없음):** 이 흐름은 "행 하나를 통째로
  지운다"는 점에서 이전 개별 결과 삭제 기능이 겪었던 lost-update 레이스(같은 job 행의
  `result_json`을 여러 요청이 동시에 읽고-고치는 문제)와 다른 종류다 — 이 엔드포인트는
  같은 `job_id`에 대해 여러 번 동시에 눌릴 상황 자체가 UI상 없다(job당 삭제 버튼 하나,
  사용자가 한 번에 하나만 누름). 여러 백테스트 결과에 대한 `delete_backtest_run` 호출들은
  서로 다른 행을 지우므로 애초에 경합이 없다.

**`frontend/lib/api/eda.ts`**

```typescript
export function deleteGridSearchJob(jobId: string): Promise<{ deleted: boolean }> {
  return apiFetch<{ deleted: boolean }>(`/api/v1/grid-search/jobs/${jobId}`, {
    method: 'DELETE',
  });
}
```

## 자기 검토(스펙 완성도)

- 플레이스홀더/TBD 없음.
- 적용 범위(완료 포함 세 상태 전부, 완료 job은 결과까지 cascade 삭제)를 명시적으로
  결정했다 — 사용자 승인됨.
- 삭제 UI 방식(체크박스+일괄삭제가 아니라 행마다 휴지통 버튼)을 명시적으로 결정했다 —
  사용자 승인됨.
- 기존 개별 결과 삭제 다이얼로그와 새 job 삭제 다이얼로그를 별도 인스턴스로 두기로 한
  이유를 명시했다(문구/대상이 다름).
- 백엔드 동시성: 이전 플랜에서 발견된 lost-update 레이스와 이번 기능이 왜 다른 문제
  범주인지(공유 JSON 컬럼 read-modify-write가 아니라 단일 행 DELETE) 명시해서, 나중에
  리뷰에서 같은 우려가 다시 제기될 때 헷갈리지 않도록 했다.
- 스코프가 백엔드 함수 1개 + 엔드포인트 1개 + 프론트 API 함수 1개 + 기존 컴포넌트 1개
  확장(새 컬럼+버튼+다이얼로그)으로 좁아 단일 구현 플랜으로 처리 가능.
