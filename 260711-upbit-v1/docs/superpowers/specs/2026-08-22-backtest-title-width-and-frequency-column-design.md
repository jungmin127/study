# 백테스트 결과 제목 컬럼 폭 고정 + Frequency 컬럼 추가

## 배경 / 문제

`백테스트 결과` 목록 표(`frontend/components/BacktestRunsTable.tsx`)의 제목 컬럼이
폭 제한 없이 렌더링되어(`components/ui/table.tsx`의 기본 `whitespace-nowrap`을
override하지 않음), 제목/설명이 길면 컬럼이 한없이 넓어지고 표 우측 컬럼이
잘려 보인다.

또한 각 전략이 백테스트 기간 동안 실제로 얼마나 자주 매수했는지(매매 빈도)를
한눈에 볼 방법이 없다. 매수 조건이 지나치게 자주 걸리는 전략(과최적화 위험)과
지나치게 드물게 걸리는 전략을 구분하기 위해 "총 캔들 수 대비 실제 매수 체결
횟수"를 표로 노출한다.

## 요구사항

1. 제목 컬럼 폭을 현재 대비 절반 수준으로 고정(fixed-width)하고, 잘린 텍스트는
   hover 시 네이티브 tooltip으로 전체 표시한다. 제목/설명은 같은 셀에 렌더링되므로
   별도 처리 없이 함께 줄어든다.
2. `frequency`라는 이름의 컬럼을 신설해 "분자(매수 체결/포지션 진입 횟수) /
   분모(백테스트 기간 내 실제 캔들 개수)"를 비율(%)과 함께 표시한다.
   - 분모는 이론치(기간÷interval)가 아니라 실제 캔들 데이터 개수(거래소 데이터
     결측 반영).
   - 분자는 전략이 실제로 매수 주문을 체결해 포지션에 진입한 횟수(청산 포지션 +
     기간 종료 시점 강제청산된 미청산 포지션 포함) — `engine/runner.py`가
     생성하는 `trades` 리스트 길이와 동일 개념.
3. Grid Search 탭의 "펼쳐보기" 상세에도 동일한 frequency 표기를 추가한다(신규
   실행분부터).

## 설계

### 1. 제목 컬럼 폭 고정

`frontend/components/BacktestRunsTable.tsx`
- `TableHead`(282줄)와 title `TableCell`(338~341줄)에 `max-w-[160px] truncate`
  클래스를 적용.
- 잘렸을 때 전체 텍스트를 보여주기 위해 셀 컨테이너에 `title={...}` 속성(네이티브
  HTML tooltip)을 추가.
- 매수전략/매도전략 컬럼이 이미 쓰고 있는 `max-w-[240px] whitespace-normal`
  패턴과 동일한 결의 수정.

### 2. candle_count 계산 및 저장

`engine/runner.py`의 `run_backtest()`는 현재 `total_bars = len(df_bt)`를
미청산 포지션 강제청산 블록(217~221줄) 안에서만 계산한다. 이를 함수 최상단으로
옮겨 무조건 계산하고, 반환 dict에 `"candle_count": total_bars`를 추가한다.

`engine/cache.py`
- `_SCHEMA`의 `backtest_results` 테이블에 `candle_count` 컬럼을 추가하고,
  `_connect()`의 기존 `ALTER TABLE ... ADD COLUMN` 마이그레이션 루프(title/
  description과 동일 패턴)에 `backtest_results.candle_count INTEGER`를 추가.
- `save_result()`가 `result["candle_count"]`를 INSERT.
- `load_result()`, `list_backtest_runs()`가 `candle_count`를 SELECT해 반환
  dict에 포함.

이 경로(`run_backtest` → `save_result`, `run_backtest_cached`를 통해)는 일반
온디맨드 백테스트와 Grid Search(`scripts/grid_search.py`)가 공유하므로, 이
수정 한 번으로 두 기능 모두 candle_count를 저장하게 된다.

### 3. trade_count

이미 저장된 `trades_json`의 길이를 그대로 쓴다 (`engine/metrics.py`의
`total_trades = len(trades)`와 동일 개념). 별도 컬럼 불필요 — 읽을 때
`len(trades)`로 계산.

### 4. 목록 API 응답 확장

`backend/main.py`의 `get_backtest_runs`(612~661줄)
- 응답 dict에 `"trade_count": len(trades)`, `"candle_count": r["candle_count"]`
  추가. (주의: `trades`는 미청산 포지션 재평가로 교체된 리스트일 수 있으므로
  기존 로직대로 `trades` 지역 변수를 그대로 사용 — 원본 `r["trades"]`가 아님.)

`frontend/lib/types/eda.ts`의 `BacktestRunSummary`에 `trade_count: number`,
`candle_count: number | null` 추가 (백필 전/실패 시 null 가능).

### 5. Frequency 컬럼 UI

`BacktestRunsTable.tsx`
- 새 `TableHead`: 텍스트 `frequency` (요청대로 영문 그대로, 다른 헤더는
  한국어이지만 이 컬럼명은 사용자가 명시적으로 지정).
- 새 `TableCell`: `candle_count`가 있으면 `"{trade_count} / {candle_count}
  ({pct}%)"` (pct = `trade_count / candle_count * 100`, 소수 1자리,
  `top_trade_contribution_pct`와 동일한 `toFixed(1)` 컨벤션), 없으면 `-`.

`BacktestRunCard.tsx`(모바일)
- 기존 통계 스트립(51~61줄, "최대거래 기여도" 옆)에 같은 형식으로 frequency
  추가.

### 6. 기존 데이터 백필

`scripts/backfill_candle_count.py` (신규, 1회성 스크립트)
- 더스트 정리 스크립트(`scripts/` 기존 1회성 스크립트)와 동일한 성격.
- `list_backtest_runs()` 전체(또는 직접 SQL로 candle_count가 NULL인 행)를
  순회하며 각 run의 market/timeframe/start/end로 `get_candles()`를 호출해
  `len(df)`를 구하고, `UPDATE backtest_results SET candle_count = ? WHERE
  run_id = ?`로 채워 넣는다.
- 실행은 로컬에서 1회, 결과 요약(처리 건수/실패 건수)을 stdout에 출력.

### 7. Grid Search 펼쳐보기

`scripts/grid_search.py`의 `saved_summaries` 구성(402~404줄)
- `run_backtest_cached()`가 반환하는 `saved` dict에 이미 `trades`,
  `candle_count`가 담겨 있으므로, `"trade_count": len(saved["trades"])`,
  `"candle_count": saved["candle_count"]`를 `saved_summaries` 항목에 추가.

`frontend/lib/types/eda.ts`의 `GridSearchSavedResult`에 `trade_count: number`,
`candle_count: number` 추가.

`frontend/components/GridSearchHistory.tsx`의 펼쳐보기 상세 영역(357~411줄
부근, 결과 grid)에 동일한 `frequency` 표기(분자/분모 + %) 추가.

**제약**: `result_json`은 grid search job row에 저장된 JSON blob이라 스키마
밖에 있고 백필 대상이 아니다. 기존에 이미 저장된 Grid Search 이력에는
frequency가 표시되지 않고(`-` 또는 항목 자체 생략), 이 변경 이후 새로 실행하는
Grid Search부터 표시된다. (사용자 확인 완료)

## 범위 밖

- Grid Search 기존 이력에 대한 frequency 소급 적용.
- 제목 컬럼 폭 변경을 모바일 카드에 적용(모바일 카드는 이미 `truncate`
  처리되어 있어 해당 버그가 없음).
- frequency 컬럼 기준 정렬/필터 기능 추가(요청 범위 밖, 필요 시 후속 작업).
