# 로컬 그리드서치 지표 풀 확장 + 체이닝 설계

날짜: 2026-08-22

## 배경

지금 로컬 그리드서치(`scripts/grid_search.py`)는 오실레이터 9종 + 매도전용 3종(`OSCILLATOR_SPECS`, `SELL_ONLY`)의 매수 조건 1개 x 매도 조건 1개 전 교차(20,700개 조합)만 계산한다. 사용자는 실제로는 1차로 오실레이터만 돌려 좋은 조합을 찾은 뒤, 라이브에 옮길 만한 것만 골라 반영해왔다.

원하는 확장은 세 갈래다: 오실레이터 외 지표(추세/가격대/거래량/거래대금/시장심리/손익, `backend/main.py`의 `INDICATOR_CATALOG` 기준 총 7개 카테고리 59개 지표)로 1차 grid search를 돌리는 것, 오실레이터+다른 카테고리를 합쳐 1차를 돌리는 것, 그리고 1차 결과를 베이스로 삼아 다른 카테고리 조건을 추가로 얹는 2차 grid search. 브레인스토밍 결과 이 세 갈래는 "① 1차 grid search의 지표 풀을 선택 가능하게 일반화" + "② 이전 결과를 베이스로 재귀적으로 체이닝 가능한 grid search" 두 메커니즘으로 수렴한다는 결론에 도달했다.

**스코프 아님(나중에):** 특정 코인에서 찾은 전략을 나머지 모든 코인에 대입해 가장 수익률 좋은 코인을 찾는 "코인 스윕" 기능. 이번 설계에 포함하지 않는다.

## 현재 구조 확인

- `engine/condition_tree.py`의 `ConditionGroup`은 이미 `{"type": "AND"|"OR", "conditions": [ConditionBlock|ConditionGroup, ...]}` 형태의 재귀 트리를 평가할 수 있다. 즉 다중 지표 AND/OR 조합 자체는 백테스트 엔진(러너)에서 이미 지원되며, 지금 제약은 순전히 `scripts/grid_search.py`가 매수/매도 각각 단일 `ConditionBlock`만 그리드로 순회하기 때문이다(조합 폭발 방지 목적).
- `scripts/grid_search.py`의 `main()`은 `build_condition_grid()`로 `(buy_conditions, sell_conditions)` 리스트를 만들고, 두 리스트를 전 교차해 `compute_grid_results_parallel()`로 계산한 뒤, `dedup_top_results()`로 상위 N개만 골라 각각 `run_backtest_cached()`로 저장한다. 저장 시 `buy_group = {"type": "AND", "conditions": [buy_block]}` 형태로 단일 블록을 AND 그룹 하나로 감싼다.
- `backend/grid_search_service.py`가 위 스크립트를 서브프로세스로 실행하며 stdout의 진행률/`RESULT_JSON` 라인을 파싱해 `engine.cache`의 `grid_search_jobs` 테이블(`id, market, timeframe, capital, start, end, top_n, status, total_combos, done_combos, started_at, finished_at, elapsed_sec, error_message, result_json`)에 기록한다. `result_json`은 `[{rank, run_id, return_pct, title, trade_count, candle_count}, ...]` 형태의 JSON 배열이며, 개별 결과는 실제 백테스트 run(`backtest_runs`, `buy_conditions`/`sell_conditions` 포함)으로 저장된다.
- 프론트엔드는 `GridSearchForm.tsx`(요청 폼: market/timeframe/capital/start/end/top_n)와 `GridSearchHistory.tsx`(job 목록, 각 job을 펼치면 `result_json`의 top-N 결과가 나열)로 구성된다.

## 데이터 모델 변경

### `grid_search_jobs` 테이블 (engine/cache.py)

새 컬럼 3개 추가(모두 nullable, 기존 1차 job은 NULL):
- `indicator_pool TEXT` — 선택한 카테고리/개별 지표를 JSON으로 직렬화해 기록(감사/재현용). 미지정 시 오실레이터 전용으로 간주(기존 동작과 동일).
- `base_run_id TEXT` — 체이닝 job이 베이스로 삼은 결과의 `run_id`. 1차 job은 NULL.
- `combinator TEXT` — `"AND" | "OR"`. `base_run_id`가 있을 때만 값을 가짐.

### 프론트엔드 타입 (frontend/lib/types/eda.ts)

- `GridSearchJobRequest`에 `indicator_pool?: { categories: string[]; indicators?: string[] }` 추가. 미지정 시 서버가 오실레이터 전용으로 처리(하위호환).
- 체이닝 요청은 별도 필드로 확장: `base_run_id?: string`, `combinator?: 'AND' | 'OR'`, `new_pool?: { categories: string[]; indicators?: string[] }`.
- `GridSearchJob`에 `indicator_pool`, `base_run_id`, `combinator` 추가(모두 `| null`).

## 1차 grid search: 지표 풀 선택

- `scripts/grid_search.py`의 `OSCILLATOR_SPECS`를 카테고리별 스펙 레지스트리로 일반화한다(`INDICATOR_CATALOG`의 7개 카테고리를 그대로 따름). `build_condition_grid()`는 선택된 카테고리/지표 집합을 인자로 받아 그 풀에 한해서만 조합을 생성하도록 변경한다. 인자를 안 주면 지금처럼 오실레이터만(기존 기본 동작 보존).
- 오실레이터 외 지표의 임계값 그리드(예: RSI의 `low=[20,30,40]`에 대응하는 값)는 지금처럼 사람이 큐레이션한다. 구현 계획 단계에서 각 지표의 실제 값 분포를 분석해 합리적인 기본 그리드를 지표별로 제안하고, 필요하면 리뷰 후 조정한다.
- 프론트 `GridSearchForm.tsx`에 "지표 풀 선택" 섹션을 추가한다: 7개 카테고리 체크박스(기본값 = 오실레이터만 체크, 기존 폼 동작과 동일)와, 펼치면 카테고리 내 개별 지표를 체크 해제할 수 있는 세부조정 UI.
- 실행 전 예상 조합 수(`buy_conditions 수 x sell_conditions 수`)와 예상 소요 시간(기존 20,700개 조합 기준 실측 소요시간을 기준 삼아 선형 추정)을 폼에 표시한다. 임계치를 넘으면 경고 문구만 보여주고 실행은 막지 않는다.

## 2차 이상: 체이닝 grid search

- `GridSearchHistory.tsx`의 펼쳐진 결과 행마다 "이 결과 기반으로 추가 탐색" 버튼을 추가한다. 클릭하면 해당 결과의 `run_id`, `market`, `timeframe`, `start`, `end`를 프리필한 상태로 체이닝 폼(AND/OR 선택 + 새 지표 풀 선택)이 열린다.
- 폼에는 AND/OR의 트레이드오프를 안내하는 문구를 넣는다: "AND는 베이스 조건을 좁히기만 해 항상 안전합니다(최악의 경우 거래 0건). OR은 베이스 조건과 새 조건 중 하나만 맞아도 매매하므로, 새 조건의 질이 낮으면 베이스의 성과가 오히려 나빠질 수 있습니다."
- `scripts/grid_search.py`에 체이닝 모드를 추가한다: `--base-run-id`, `--combinator {AND,OR}`, 새 풀 선택 인자를 받으면, 베이스 run의 `buy_conditions`/`sell_conditions`(engine.cache에서 조회)를 각각 `base_buy_group`/`base_sell_group`으로 삼고, 새 풀에서 뽑은 후보 1개씩을 매번 다음과 같이 감싼다:
  ```python
  buy_group = {"type": combinator, "conditions": [base_buy_group, candidate_buy_block]}
  sell_group = {"type": combinator, "conditions": [base_sell_group, candidate_sell_block]}
  ```
  이후 흐름(전 교차 계산 → dedup → 상위 N 저장)은 1차와 동일하다. 매번 새 후보 지표 1개만 그리드로 열기 때문에 조합 수는 새 풀의 크기로 제한된다.
- 저장되는 job 행에 `base_run_id`/`combinator`/`indicator_pool`(새 풀)이 기록되므로, 이 job의 결과를 다시 베이스로 삼아 3차, 4차로 반복 체이닝할 수 있다(재귀적으로 동일한 메커니즘 재사용).

## 이력 UI: 체이닝 표시

- `GridSearchHistory.tsx`의 job 목록 트리를 재구성한다: 최상위는 `base_run_id`가 NULL인 1차 job들이고, `base_run_id`가 특정 1차 job의 어느 결과와 일치하면 그 결과 행 바로 아래에 들여쓰기로 표시한다(현재는 모든 job이 평평하게 나열됨).
- 체이닝된 job을 펼쳤을 때 각 결과 행에는 직계 베이스 결과(그 job이 seed로 쓴 `base_run_id`) 대비 수익률/거래수/승률/MDD 델타를 함께 보여준다. 3차 이상의 경우도 "직계 부모"(자신이 seed로 삼은 바로 그 결과) 기준으로 델타를 계산한다 — 원 1차와의 누적 델타는 계산하지 않는다.
- 정렬/필터(코인/봉/수익률)는 최상위 1차 job 기준으로 동작하고, 체이닝된 하위 job은 부모를 따라간다(부모가 필터에 걸리면 자식도 함께 숨김).

## 에러 처리 / 엣지 케이스

- 체이닝 요청의 `base_run_id`가 가리키는 run이 삭제된 경우(그리드서치 결과 개별 삭제 기능이 이미 있음) 체이닝 job 생성 시 400으로 거부하고 "베이스 결과가 삭제되어 더 이상 사용할 수 없습니다" 메시지를 반환한다.
- 새 풀을 하나도 선택하지 않고 1차/체이닝을 실행하면(카테고리 전부 해제) 프론트에서 제출 전 검증으로 막는다.
- job 행 통째 삭제 기능이 이미 있으므로(베이스가 된 1차 job이 삭제되는 경우), 이력 트리 구성 시 `base_run_id`가 가리키는 결과를 더 이상 찾을 수 없는 체이닝 job은 고아로 방치하지 않고 최상위로 끌어올려 "베이스 삭제됨" 표시와 함께 보여준다.
- 기존 `WORKER_COUNT`/`MAX_TASKS_PER_CHILD` 메모리 릭 예산은 오실레이터 9종 기준 실측치라, 다른 카테고리(특히 가격대 18종)를 포함하면 예산이 달라질 수 있다 — 구현 단계에서 재실측 후 필요시 조정한다.

## 테스트 계획

- `tests/test_grid_search.py`/`tests/test_grid_search_service.py`: 풀 선택 인자에 따라 `build_condition_grid()`가 올바른 부분집합만 생성하는지, 체이닝 모드에서 `buy_group`/`sell_group`이 베이스+후보로 올바르게 감싸지는지 단위 테스트로 검증한다.
- 백엔드 API(`backend/main.py`) 테스트: `indicator_pool`/`base_run_id`/`combinator`가 요청 검증 및 job 생성에 올바르게 반영되는지, 삭제된 `base_run_id`에 대한 400 처리를 확인한다.
- 프론트엔드는 기존 관례대로 브라우저 수동 확인: 지표 풀 섹션 기본값이 오실레이터만 체크되어 있는지, "이 결과 기반으로 추가 탐색" 버튼 동작, 체이닝 결과의 들여쓰기/델타 표시, 예상 조합수/시간 안내와 경고 문구.
