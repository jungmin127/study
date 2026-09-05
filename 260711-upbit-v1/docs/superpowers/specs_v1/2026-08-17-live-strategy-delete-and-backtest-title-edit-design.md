# 라이브 전략 삭제 + 백테스트 제목/설명 편집 설계

날짜: 2026-08-17

## 배경

두 가지 독립적인 UX 공백을 메운다:

1. 라이브에서 중지(stopped)시킨 전략을 목록에서 삭제할 방법이 없다 (매수가 안 돼서 중지시킨 전략이 목록에 계속 남아있음).
2. 백테스트 결과의 title/description을 수정할 방법이 없다. `engine/cache.py`의 `backtest_runs` 테이블에는 이미 `title`, `description` 컬럼이 있고 백테스트 실행 시점에 값을 넣지만, 실행 이후에는 수정할 API/UI가 전혀 없다. 로컬에서 그리드서치로 얻은 결과의 제목을 알아보기 쉽게 고친 뒤 `scripts/import_backtest_results.py`로 라이브 서버에 push하려는 워크플로우를 지원하기 위함.

## 결정 사항 (Q&A로 확정)

- 라이브 전략 삭제 버튼: `status === 'stopped'`일 때만 카드에 노출 (스와이프 제스처 방식은 채택하지 않음 — 이 프로젝트에 스와이프 인터랙션 패턴이 없고, 기존 아이콘 버튼 패턴과의 일관성을 위해).
- 백테스트 제목/설명 편집 위치: 상세 페이지(`/backtests/[runId]`)에서 인라인 편집 (목록에서는 편집하지 않음).
- 제목/설명 수정 시 `backtest_runs.created_at`도 함께 갱신한다. 이렇게 해야 `scripts/import_backtest_results.py`의 `merge_databases()`가 `created_at` 비교만으로 "로컬에서 수정한 게 더 최신"이라고 판단해 라이브 DB의 기존 행(backtest_runs + backtest_results 전체)을 덮어쓴다. (참고: run_id는 전략/파라미터/시장/기간/리스크설정의 해시라 title/description 변경으로 바뀌지 않으므로, 같은 run_id에 대한 부분 패치가 아니라 행 전체 교체가 일어난다.)
- 목록 카드(`BacktestRunCard.tsx`)의 "실행 시각" 라벨은 그대로 유지한다 (문구를 "실행/수정 시각"으로 바꾸지 않음).

## 1. 라이브 전략 삭제

### 백엔드

**`trading/db.py`**
- `delete_live_strategy(live_strategy_id: str) -> bool` 신설
  - `status = 'stopped'`인 경우에만 삭제 허용 (다른 상태 전이 함수들과 동일하게 SQL WHERE 절에 상태 가드를 건다)
  - FK 제약이 켜져 있으므로 자식 테이블을 먼저 지워야 한다: `positions`, `orders`, `signals`, `daily_performance`, `circuit_breaker_state` (모두 `live_strategy_id`를 FK로 참조). 하나의 트랜잭션으로 묶는다.
  - `manual_intervention_events`는 `live_strategy_id` FK가 없으므로 건드리지 않는다.
  - 반환값: 실제로 삭제됐으면 True (id 없음 또는 status != 'stopped'면 False)

**`backend/main.py`**
- `DELETE /api/v1/live-strategies/{strategy_id}` 신설
  - 404: 해당 id 없음
  - 409: status != 'stopped' ("중지된 전략만 삭제할 수 있습니다")
  - 200: `{"deleted": true}`

### 프론트엔드

**`frontend/lib/api/liveStrategies.ts`**
- `deleteLiveStrategy(id: string): Promise<{ deleted: boolean }>` 추가 (DELETE 메서드)

**`frontend/components/LiveStrategiesPage.tsx`**
- 현재 `status === 'stopped'` 분기에는 액션 버튼이 전혀 없음 (승인/일시정지/재개/중지 버튼만 draft/running/paused에 있음). 여기에 휴지통 아이콘 버튼(`Trash2`, `variant="destructive"`) 추가.
- 클릭 시 `BacktestRunsTable.tsx`와 동일한 `AlertDialog` 확인창 패턴 재사용 ("이 전략을 삭제하시겠습니까? 삭제 후에는 되돌릴 수 없습니다.")
- 삭제 성공 시 기존 `runAction` 패턴처럼 에러 처리 후 `refresh()` 호출

## 2. 백테스트 제목/설명 인라인 편집

### 백엔드

**`engine/cache.py`**
- `update_backtest_run_metadata(run_id: str, title: str | None, description: str | None) -> bool` 신설
  - `UPDATE backtest_runs SET title = ?, description = ?, created_at = datetime('now') WHERE id = ?`
  - rowcount > 0이면 True 반환 (없는 run_id면 False)
- `load_result()`가 현재 `title`, `description`, `created_at`을 SELECT하지 않아 상세 API가 이 값을 아예 내려주지 못하고 있음 → SELECT 절과 반환 dict에 세 컬럼 추가

**`backend/main.py`**
- `get_backtest_detail` 응답 dict에 `title`, `description`, `created_at` 추가 (`_to_utc_iso` 적용)
- `PATCH /api/v1/backtests/{run_id}` 신설
  - body: `{title: str | None, description: str | None}` (Pydantic 모델)
  - `update_backtest_run_metadata` 호출, False면 404
  - 200: 갱신된 `{title, description, created_at}` 반환

### 프론트엔드

**`frontend/lib/types/eda.ts`**
- `BacktestDetail`에 `title: string | null`, `description: string | null`, `created_at: string` 추가

**`frontend/lib/api/eda.ts`**
- `updateBacktestRun(runId: string, req: { title: string | null; description: string | null }): Promise<{ title, description, created_at }>` 추가 (PATCH)

**새 클라이언트 컴포넌트 (예: `frontend/components/BacktestMetaEditor.tsx`)**
- 평소에는 title/description을 텍스트로 표시 (title 없으면 "(제목 없음)" placeholder, `BacktestRunCard.tsx`와 동일한 표현)
- 클릭(또는 연필 아이콘 클릭)하면 title input + description textarea + 저장/취소 버튼으로 전환
- 저장 성공 시 `router.refresh()`로 서버 컴포넌트 데이터 갱신, 편집모드 종료
- 배치 위치: `frontend/app/backtests/[runId]/page.tsx` 상단, `<h1>백테스트 상세</h1>` 아래 market/timeframe 줄과 함께 또는 그 위

## 테스트 계획

- `tests/test_trading_db.py`: `delete_live_strategy` — stopped 상태에서 성공, running/paused/draft 상태에서 거부(False), 자식 테이블(positions/orders/signals/daily_performance/circuit_breaker_state) 함께 삭제되는지 확인
- `tests/test_backend.py`: `DELETE /api/v1/live-strategies/{id}` — 404/409/200 케이스
- `tests/test_cache.py`: `update_backtest_run_metadata` — 정상 갱신, 없는 run_id에 False, created_at이 갱신되는지 확인
- `tests/test_backend.py`: `PATCH /api/v1/backtests/{run_id}` — 404/200 케이스, `get_backtest_detail` 응답에 title/description/created_at 포함되는지 확인
