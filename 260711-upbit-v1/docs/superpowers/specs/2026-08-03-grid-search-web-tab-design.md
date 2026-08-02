# Grid Search 웹 탭 설계 — 채팅 대신 브라우저에서 직접 실행

- 작성일: 2026-08-03
- 상태: 승인 대기 (사용자 리뷰 전)
- 선행 문서: `docs/superpowers/specs/2026-08-02-grid-search-worker-pool-design.md`(구현 완료,
  `scripts/grid_search.py`가 워커 풀 기반으로 20,700개 조합을 크래시 없이 계산하는 것까지 확인됨)

## 배경 및 목적

지금까지 grid search는 `.claude/skills/grid-search/SKILL.md`를 통해 사용자가 채팅으로 요청하면
LLM이 `scripts/grid_search.py`를 Bash로 백그라운드 실행하고, 완료까지 대화 중간중간 진행률을
직접 확인해 보고하는 방식이었다. 하지만 grid search는 LLM의 판단이 필요한 작업이 아니라
코인/자금/봉데이터/기간/상위N개라는 고정된 입력을 받아 정해진 로직을 20~30분 실행하는
단순 반복 작업이다. 이걸 매번 LLM 세션을 통해서만 돌릴 수 있게 하는 건 불필요한 간접 계층이다.

이 스펙은 grid search를 브라우저에서 직접 실행할 수 있는 독립 기능으로 만든다. 사용자가
새 "Grid Search" 탭에서 폼을 채우고 실행하면, 백엔드가 `scripts/grid_search.py`를 그대로
서브프로세스로 실행하고, 진행률/이력을 웹에서 폴링으로 보여준다. LLM은 더 이상 grid search
자체를 실행하지 않고, 요청이 오면 이 탭으로 안내만 한다.

## 스코프

- **포함**: 새 백엔드 API(`/api/v1/grid-search/jobs*`), `grid_search_jobs` DB 테이블, 새
  프론트엔드 탭(`/grid-search`)과 폼/진행률/이력 UI, `.claude/skills/grid-search/SKILL.md`를
  "실행"에서 "안내"로 변경.
- **제외**: `scripts/grid_search.py` 자체의 로직 변경(이미 검증된 그대로 서브프로세스로 재사용,
  코드 수정 없음), 동시 다중 실행(한 번에 1개 job만), 백엔드 `--reload` 재시작 시 진행률 재연결
  (알려진 제약으로 명시, 아래 참고), 그리드 조합 자체를 커스터마이징하는 기능(9-오실레이터
  고정 그리드는 스크립트에 이미 고정되어 있고 이번 스펙에서도 그대로 둔다).

## 이전 설계와의 관계 / 재사용

- `scripts/grid_search.py`는 코드 수정 없이 그대로 서브프로세스로 실행한다. stdout에 이미
  기계 판독 가능한 형태로 찍히는 진행률 로그(`완료 N/전체건 (%)`)와 마지막 줄의
  `RESULT_JSON: {...}`을 그대로 파싱 대상으로 삼는다 — 지금 LLM이 Bash로 하고 있는 것과
  동일한 계약(contract)이다.
- `_validate_backtest_request`/`_fetch_backtest_dataframe`(`backend/main.py`)의 검증 패턴
  (마켓 존재 확인, 기간 역전 확인 등)을 grid search 요청 검증에도 동일하게 적용한다. 단,
  캔들 워밍업 부족 체크는 중복 구현하지 않고 스크립트 자체의 `_check_candle_warmup`이 처리하게
  둔다(실패해도 캔들 조회 직후 몇 초 안에 끝나므로 빠르게 실패한다).
- 프론트엔드 `PortSetupForm.tsx`의 `CoinSelect` 컴포넌트, 운용자금 포맷팅(`formatCapital`),
  URL 쿼리파라미터 프리필 패턴(`parsePreset`)을 그대로 재사용한다.
- DB 스키마는 `engine/cache.py`의 기존 `CREATE TABLE IF NOT EXISTS` 블록에 테이블을 하나
  추가하는 방식으로, 기존 `backtest_runs`/`sweep_history` 관례를 따른다.

## 아키텍처

```
scripts/grid_search.py (변경 없음)
  ↑ subprocess.Popen(창 없이, stdout+stderr 파이프)

backend/grid_search_service.py (신규)
  - start_job(request) -> job_id
      · 검증(마켓/기간/자금/top_n)
      · 이미 실행 중인 job 있으면 409
      · subprocess.Popen([sys.executable, "scripts/grid_search.py", --market, ...],
                          cwd=repo_root, creationflags=CREATE_NEW_PROCESS_GROUP,
                          stdout=PIPE, stderr=STDOUT, text=True, encoding="utf-8")
      · daemon 스레드가 stdout을 줄 단위로 읽으며 진행률/RESULT_JSON 파싱 → DB 갱신
      · 모듈 전역 변수 하나(_active: {"job_id", "proc"} | None)로 단일 실행 상태 관리
  - cancel_job(job_id)
      · 실행 중인 job과 id 일치 확인
      · Windows: os.kill(proc.pid, signal.CTRL_BREAK_EVENT) → 스크립트 쪽 KeyboardInterrupt →
        기존 `finally: pool.terminate(); pool.join()`이 정리 → 프로세스 자연 종료
      · 15초 내 안 죽으면 proc.terminate()로 강제 종료(폴백)

engine/cache.py (수정 — 테이블 1개 + CRUD 함수 추가)
  - create_grid_search_job(...) -> job_id
  - update_grid_search_job_progress(job_id, done_combos, total_combos)
  - finish_grid_search_job(job_id, status, elapsed_sec=None, result_json=None, error_message=None)
  - get_grid_search_job(job_id) -> dict | None
  - list_grid_search_jobs() -> list[dict]  (최신순)

backend/main.py (수정 — 엔드포인트 4개 추가, 로직은 grid_search_service에 위임)
  - POST   /api/v1/grid-search/jobs
  - GET    /api/v1/grid-search/jobs
  - GET    /api/v1/grid-search/jobs/{job_id}
  - POST   /api/v1/grid-search/jobs/{job_id}/cancel

frontend/app/grid-search/page.tsx (신규)
  - GridSearchForm + GridSearchProgress + GridSearchHistory 렌더링

frontend/components/GridSearchForm.tsx (신규)
frontend/components/GridSearchProgress.tsx (신규)
frontend/components/GridSearchHistory.tsx (신규)
frontend/components/NavTabs.tsx (수정 — 탭 1개 추가)
frontend/lib/api/eda.ts, lib/types/eda.ts (수정 — 함수/타입 추가)

.claude/skills/grid-search/SKILL.md (수정 — 실행 → 안내로 변경)
```

## 데이터 모델

`engine/cache.py`의 스키마 블록에 추가:

```sql
CREATE TABLE IF NOT EXISTS grid_search_jobs (
    id             TEXT PRIMARY KEY,
    market         TEXT NOT NULL,
    timeframe      TEXT NOT NULL,
    capital        REAL NOT NULL,
    start          TEXT NOT NULL,
    end            TEXT NOT NULL,
    top_n          INTEGER NOT NULL,
    status         TEXT NOT NULL,       -- running | completed | failed | canceled
    total_combos   INTEGER,
    done_combos    INTEGER NOT NULL DEFAULT 0,
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    elapsed_sec    REAL,
    error_message  TEXT,
    result_json    TEXT,                -- RESULT_JSON.saved와 동일한 shape (JSON 문자열)
    pid            INTEGER
)
```

`id`는 `uuid.uuid4().hex`. `result_json`은 `[{"rank", "run_id", "return_pct", "title"}, ...]` —
`scripts/grid_search.py`가 이미 `RESULT_JSON.saved`로 찍는 것과 완전히 동일한 구조라 프론트가
결과 링크(`/backtests/{run_id}`)를 만들 때 그대로 쓴다.

## 인터페이스

```python
# backend/main.py

class GridSearchJobRequest(BaseModel):
    market: str
    timeframe: str
    capital: float
    start: str        # "YYYY-MM-DD"
    end: str           # "YYYY-MM-DD"
    top_n: int = 20

@app.post("/api/v1/grid-search/jobs")
def create_grid_search_job_endpoint(req: GridSearchJobRequest) -> dict:
    # 검증: market이 KRW 마켓 목록에 있는지, start < end, capital > 0, 1 <= top_n <= 50
    # 실행 중인 job 있으면 409 (detail에 진행 중인 job_id 포함)
    # grid_search_service.start_job(req) -> job_id
    # return get_grid_search_job(job_id)

@app.get("/api/v1/grid-search/jobs")
def list_grid_search_jobs_endpoint() -> list[dict]:
    # engine.cache.list_grid_search_jobs() 그대로 반환 (최신순)

@app.get("/api/v1/grid-search/jobs/{job_id}")
def get_grid_search_job_endpoint(job_id: str) -> dict:
    # 없으면 404

@app.post("/api/v1/grid-search/jobs/{job_id}/cancel")
def cancel_grid_search_job_endpoint(job_id: str) -> dict:
    # 실행 중인 job과 id 불일치 또는 이미 종료된 job이면 409
    # grid_search_service.cancel_job(job_id)
    # return {"status": "canceling"}
```

```typescript
// frontend/lib/types/eda.ts

export interface GridSearchJobRequest {
  market: string;
  timeframe: string;
  capital: number;
  start: string;
  end: string;
  top_n: number;
}

export interface GridSearchSavedResult {
  rank: number;
  run_id: string;
  return_pct: number;
  title: string;
}

export interface GridSearchJob {
  id: string;
  market: string;
  timeframe: string;
  capital: number;
  start: string;
  end: string;
  top_n: number;
  status: 'running' | 'completed' | 'failed' | 'canceled';
  total_combos: number | null;
  done_combos: number;
  started_at: string;
  finished_at: string | null;
  elapsed_sec: number | null;
  error_message: string | null;
  result_json: GridSearchSavedResult[] | null;
}
```

```typescript
// frontend/lib/api/eda.ts

export function createGridSearchJob(req: GridSearchJobRequest): Promise<GridSearchJob> {
  return apiFetch<GridSearchJob>('/api/v1/grid-search/jobs', {
    method: 'POST',
    body: JSON.stringify(req),
  });
}

export function getGridSearchJobs(): Promise<GridSearchJob[]> {
  return apiFetch<GridSearchJob[]>('/api/v1/grid-search/jobs');
}

export function getGridSearchJob(jobId: string): Promise<GridSearchJob> {
  return apiFetch<GridSearchJob>(`/api/v1/grid-search/jobs/${jobId}`);
}

export function cancelGridSearchJob(jobId: string): Promise<{ status: string }> {
  return apiFetch(`/api/v1/grid-search/jobs/${jobId}/cancel`, { method: 'POST' });
}
```

## 화면 (프론트엔드)

`NavTabs.tsx` — `백테스트 설정`과 `백테스트 결과` 사이에 탭 추가:

```typescript
{ href: '/grid-search', title: 'Grid Search', icon: Grid3x3 },
```

`/grid-search` 페이지:

```
┌─ Grid Search ────────────────────────────────────────────┐
│ 코인:   [KRW-SOL ▾]     운용자금: [1,000,000    ]원        │
│ 봉데이터: (1분)(3분)(5분)(15분)(30분)[1시간](4시간)(1일)      │
│ 기간:   [2026-06-05] ~ [2026-08-03]                        │
│ 상위N개: [20  ] (1~50)                                     │
│                                    [그리드서치 시작]         │
│ 9-오실레이터 전 교차 20,700개 조합, 워커 4개 병렬 기준 약    │
│ 20~30분 소요됩니다.                                        │
└─────────────────────────────────────────────────────────┘

(실행 중인 job이 있으면 폼 대신/위에 표시)
┌─ 진행 중: KRW-SOL · 1시간봉 · 06-05~08-03 ──────────────────┐
│ ████████████████░░░░░░░░░░  62.3%  (12,900 / 20,700건)      │
│ 경과 8분 12초                              [취소]            │
└─────────────────────────────────────────────────────────┘

┌─ 요청 이력 ────────────────────────────────────────────────┐
│ ● 완료  KRW-SOL  1시간  2026-06-05~08-03  상위20  23.1분     │
│    1위 +34.25% BB_PERCENT_B(20)<0.0 / STOCH_K(10)>80 →상세  │
│ ● 실패  KRW-ETH  1시간  2026-05-01~05-03  워밍업 부족 에러    │
│ ● 취소  KRW-BTC  일봉   2026-01-01~08-01                     │
└─────────────────────────────────────────────────────────┘
```

- 봉데이터 버튼 8종은 SKILL.md의 고정 매핑표와 동일(`minutes1`~`days`).
- "그리드서치 시작" 버튼은 이미 실행 중인 job이 있으면 비활성화 + "이미 실행 중인 작업이
  있습니다" 안내, 진행 중 카드로 스크롤.
- 진행률 카드: `GET /jobs/{id}`를 3초 간격 폴링. `done_combos/total_combos`로 퍼센트 계산,
  경과 시간은 `started_at` 기준 클라이언트에서 매초 갱신(서버 재요청 없이).
- 요청 이력: `GET /jobs` 최신순 전체 렌더링(페이지네이션 없음 — 그리드서치는 자주 돌리는
  작업이 아니라 개수가 적을 것으로 예상, 필요해지면 나중에 추가). 완료 항목은 `result_json`의
  1위 결과를 미리보기로 보여주고, 클릭하면 펼쳐서 상위 N개 전체 목록(각각 `/backtests/{run_id}`
  링크) 표시.
- URL 쿼리파라미터(`?market=&timeframe=&capital=&start=&end=&topN=`)로 폼 프리필 지원 —
  `PortSetupForm`의 `parsePreset`과 동일한 패턴.

## 데이터 흐름

1. 사용자가 폼을 채우고 "그리드서치 시작" 클릭 (또는 SKILL.md가 만든 프리필 링크로 진입 후
   바로 클릭).
2. 프론트 즉시 검증(기간 역전, top_n 범위) 실패 시 API 호출 없이 인라인 에러.
3. `POST /api/v1/grid-search/jobs` 호출.
4. 백엔드: 마켓/기간/자금 검증 → 이미 실행 중인 job 있으면 409 → `grid_search_jobs`에
   `status='running'` 행 삽입 → `scripts/grid_search.py` 서브프로세스 시작 → 즉시 job 응답.
5. 백그라운드 스레드가 stdout을 읽으며:
   - `총 N,NNN개 조합` 로그 → `total_combos` 갱신
   - `완료 N/전체건 (%)` 로그(약 20회, PROGRESS_LOG_INTERVAL=1000 간격) → `done_combos` 갱신
   - `RESULT_JSON: {...}` 로그 → 파싱해 `result_json`/`elapsed_sec` 저장, `status='completed'`
   - 프로세스가 0이 아닌 코드로 종료 & 취소 요청 없었음 → 남은 stdout/stderr 텍스트를
     `error_message`로 저장, `status='failed'`
   - 취소 요청 중이었음 → `status='canceled'`
6. 프론트는 진행 중인 동안 3초마다 폴링, `status`가 `running`이 아니게 되면 폴링 중단하고
   이력 목록 재조회.
7. 완료된 job의 결과 항목 클릭 → 기존 `/backtests/{run_id}` 상세 페이지로 이동(변경 없음).

## 취소 처리

- `POST /jobs/{id}/cancel` → 진행 중인 job과 id 일치 확인 → Windows에서
  `os.kill(proc.pid, signal.CTRL_BREAK_EVENT)` 전송(서브프로세스를
  `creationflags=subprocess.CREATE_NEW_PROCESS_GROUP`로 띄워야 이 신호를 받을 수 있음).
- `scripts/grid_search.py`는 이 신호를 `KeyboardInterrupt`로 받는다 — 이미 있는
  `compute_grid_results_parallel`의 `try/finally: pool.terminate(); pool.join()`이 자연스럽게
  워커 4개까지 정리하고 프로세스가 0이 아닌 코드로 종료된다. **스크립트 코드 변경 불필요.**
- 종료를 감지하는 stdout 리더 스레드가 프로세스 종료를 확인하면 `status='canceled'`로 표시.
- 15초 내에 안 죽으면(워커가 CTRL_BREAK를 못 받는 등 예외 상황) `proc.terminate()`로 강제
  종료 — 이 경로에서는 워커 프로세스가 orphan으로 남을 수 있음을 감수한다(드문 폴백 경로).

## 에러 처리

| 상황 | 처리 |
|---|---|
| 마켓이 KRW 마켓 목록에 없음 / 기간 역전 / 자금 ≤ 0 / top_n 범위 밖 | 백엔드 400 |
| 이미 실행 중인 job이 있음 | 백엔드 409, 현재 진행 중인 job_id 포함 |
| 캔들 워밍업 부족(`_check_candle_warmup`이 `SystemExit`) | 서브프로세스가 곧바로 비정상 종료 → `status='failed'`, `error_message`에 스크립트가 낸 한글 에러 그대로 |
| 워치독 타임아웃(5분 무진행) | 스크립트가 `RuntimeError`로 종료 → `status='failed'`, `error_message`에 해당 메시지 |
| 취소 요청 | `status='canceled'`, `error_message` 없음 |
| 존재하지 않는 job_id 조회/취소 | 백엔드 404 |
| 완료/실패/취소된 job을 다시 취소 시도 | 백엔드 409 |
| 백엔드 `--reload` 재시작 중 발생 | **알려진 제약** — 재시작 후 해당 job은 진행률 갱신이 멈춘 `running` 상태로 이력에 남는다. 스크립트 자체(별도 OS 프로세스)는 계속 돌아가 완료 시 DB에 결과는 저장되지만(스크립트가 직접 `run_backtest_cached`로 저장), `grid_search_jobs` 행은 이 백엔드 인스턴스가 다시 그 stdout에 붙을 방법이 없어 `running`으로 멈춰 보인다. 사용자는 "백테스트 결과" 탭에서 `[Grid]` 항목으로 실제 완료 여부를 확인할 수 있다. |

## 기존 grid-search 스킬 변경

`.claude/skills/grid-search/SKILL.md`의 "실행 절차"를 아래로 교체:

1. 명령을 기존 규칙대로 파싱한다(마켓코드/timeframe/자금/기간/상위N개).
2. 파싱한 값으로 `http://localhost:3000/grid-search?market=...&timeframe=...&capital=...&start=...&end=...&topN=...` 링크를 만든다.
3. 스크립트를 실행하지 않고, "Grid Search 탭에서 아래 조건으로 바로 실행할 수 있습니다"와 함께
   파싱 결과 표 + 링크를 안내한다. 진행률/이력도 그 탭에서 확인 가능하다고 안내한다.

파싱/검증 규칙(코인명→마켓코드 매핑, 한글 단위 자금 변환, timeframe 매핑표 등)은 그대로 둔다 —
링크를 올바르게 만들려면 여전히 필요하다.

## 테스트

- `tests/test_backend.py`에 케이스 추가:
  - 정상 요청 → `status='running'`인 job 반환, 서브프로세스가 실제로 뜨는지는 모킹해서 확인
    (전체 20,700개를 테스트에서 돌리지 않는다 — `subprocess.Popen`을 짧게 끝나는 더미 스크립트나
    mock으로 대체)
  - 이미 실행 중인 job이 있을 때 새 요청 → 409
  - 존재하지 않는 job_id 조회/취소 → 404
  - 마켓 목록에 없는 마켓 / 기간 역전 / top_n 범위 밖 → 400
  - stdout 진행률 로그 파싱 함수(`완료 N/전체건 (%)` → `(done, total)`) 단위 테스트
  - `RESULT_JSON` 파싱 함수 단위 테스트
- 실제 서브프로세스 기동 + 취소 신호 + 20,700개 전체 실행은 이 저장소의 기존 관례대로 수동
  스모크 테스트로 검증(워커 풀 플랜 때와 동일 — 실제 멀티프로세싱을 mock하는 건 깨지기 쉬움).
- 프론트엔드는 기존 관례대로 수동 확인: dev 서버 기동 후 브라우저로 폼 제출 → 진행률 바가
  움직이는지 → 취소 버튼 동작 확인 → 완료 후 이력에 결과가 뜨고 `/backtests/{run_id}` 링크가
  동작하는지.

## 알려진 트레이드오프 (사용자 승인됨)

- 한 번에 grid search 1개만 실행 가능 — 동시 실행 필요해지면 워커 예산(`MAX_TASKS_PER_CHILD`
  캘리브레이션)을 다시 계산해야 하므로 이번 스코프에서는 제외.
- 백엔드 `--reload` 재시작 시 진행 중이던 job의 진행률 추적이 끊길 수 있음(스크립트 자체는
  계속 실행되고 결과는 저장되지만, 웹 UI의 진행률 표시만 멈춤).
- 진행률은 스크립트가 1,000개 조합마다 로그를 찍는 주기(`PROGRESS_LOG_INTERVAL`)에 맞춰
  약 1~1.5분 간격으로만 갱신된다 — 부드러운 프로그레스 바가 아니라 계단식으로 움직인다.
- 취소 강제 종료 폴백 경로(15초 타임아웃)에서는 워커 프로세스가 orphan으로 남을 가능성이
  있음(드문 경로로 판단해 이번 스코프에서는 자동 정리 로직을 추가하지 않는다).

## Self-Review 결과

- **스펙 커버리지**: 폼 제출 → 서브프로세스 실행 → 진행률 폴링 → 완료/실패/취소 → 이력 확인
  → 결과 상세 페이지 이동까지 전 과정을 아키텍처/데이터 흐름/에러 처리에서 다룸.
- **브레인스토밍에서 합의된 4가지 결정 반영 확인**: (1) 서브프로세스 기반 아키텍처(B안),
  (2) 한 번에 1개만 실행, (3) 취소 버튼 필요, (4) `--reload` 재시작 시 추적 끊김은 알려진
  제약으로 명시 — 모두 반영됨.
- **범위에서 의도적으로 제외한 것**: 동시 다중 실행, 재시작 후 재연결, 그리드 조합
  커스터마이징 — 모두 스코프 섹션에 명시.
- **기존 코드 재사용 확인**: `scripts/grid_search.py`, `_validate_backtest_request` 패턴,
  `CoinSelect`/`parsePreset`, `engine/cache.py` 스키마 관례 — 모두 변경 없이 그대로 재사용,
  중복 구현 없음.
