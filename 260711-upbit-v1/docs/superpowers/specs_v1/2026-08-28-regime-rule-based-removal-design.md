# 장세 판별 규칙기반 제거(E) 설계

배경: `docs/regime-ml-backlog.md` E 항목. 확정된 우선순위(A2 → E → A1 → B → C) 중
2번째. A2([[upbit-v1-regime-ml-performance-metrics]], `5f851a5..5e23e81`)가 먼저
끝나서 "ML 카드가 자체 성능을 보여주는" 상태가 이미 갖춰졌으므로, 규칙기반을 지워도
정보 손실이 없다.

사용자 결정: 장세 판별은 ML로만 간다. 다만 코드를 직접 뒤져보니 규칙기반 모듈이
순수하게 독립적이지 않고, ML 파이프라인이 그 안의 함수 일부를 가져다 쓰고 있어서
통째로 지우면 ML이 깨진다 — 아래처럼 이관과 삭제를 분리해야 한다.

## 현재 상태 확인 (백로그 작성 이후 코드 재확인 결과)

백로그 문서 작성 시점(2026-08-27) 이후 코드를 직접 읽어 아래 3가지를 백로그보다
정확하게 다시 확인했다:

1. **`ewm_volatility()`가 private 헬퍼 `_ewm_std_series()`에 의존한다.** 백로그는
   이관 대상을 "함수 2개 + 상수 1개"로 적었지만, `ewm_volatility()`(공개 함수)의
   실제 구현은 `_ewm_std_series()`(private)를 호출한다. `_ewm_std_series()`는
   `compute_regime_probs_series()`(삭제 대상)도 직접 호출하지만, 그건 별개로
   `ewm_volatility()`가 독립적으로 이 헬퍼 없이는 동작할 수 없다는 사실은
   변하지 않는다. 이관 대상은 실질적으로 4개(3개 공개 + 1개 private 구현
   디테일)다.
2. **`engine/regime_detector.py`는 이관 후 완전히 빈 파일이 된다.** 이관 대상
   4개(half_life_bars_for_timeframe + HALF_LIFE_DAYS, ewm_volatility,
   _ewm_std_series)를 빼면, 남는 건 전부 삭제 대상
   (`_softmax_categorize`/`compute_regime_probs`/`compute_regime_probs_series`/
   `classify_score_to_category`/`CATEGORY_REFERENCE_SCORES`/
   `WARMUP_MULTIPLIER`/`_ewm_series`/`_MIN_VOLATILITY_FLOOR`/
   `_ADJUSTMENT_COLUMNS`)뿐이다. 따라서 이 파일은 트리밍이 아니라 **파일 자체를
   삭제**한다.
3. **`frontend/components/RegimeDashboard.tsx`가 ML 카드를 규칙기반 폼 제출에
   얹어서만 렌더링한다.** `RegimeMlCurrentPrediction`은 자체 `useEffect`로
   `market`/`timeframe`이 바뀔 때마다 즉시 재조회하도록 이미 만들어져 있는데,
   지금 `RegimeDashboard.tsx`는 그 `market`/`timeframe` state를
   `RegimeBacktestForm`의 `onSubmit`(규칙기반 `getRegimeBacktest` 호출) 콜백
   안에서만 설정한다. 백로그가 "순수 삭제 가능"으로 분류한 4개 프론트 컴포넌트를
   그냥 지우면 ML 카드를 화면에 띄울 방법 자체가 사라진다 — 대시보드를
   재구성해야 한다(아래 "프론트엔드" 절).
4. **`frontend/lib/types/eda.ts`의 `CurrentPrediction` 타입도 삭제 대상에
   빠져 있었다.** `RegimeBacktestResult.current_prediction` 필드에서만
   쓰이고, 그 결과를 소비하는 `RegimeCurrentPrediction.tsx`도 함께 삭제되므로
   같이 삭제해야 한다.
5. **`trading/signal_engine.py`의 grep 매치는 오탐이었다.** `_WARMUP_MULTIPLIER`
   (private, 값 3, indicator 워밍업 배수)가 이번에 삭제하는
   `engine.regime_detector.WARMUP_MULTIPLIER`(값 5.0, 워밍업 최소 봉수)와
   이름만 우연히 비슷할 뿐 서로 무관한 별개 상수다. 백로그의 "trading/는 무관"
   결론은 재확인 후에도 유지된다.

## 이관: `engine/regime_math.py` 신설

순수 수학/시간프레임 헬퍼 전용 모듈. `engine/regime_ml_labels.py`(레이블 생성
로직)와는 목적이 다르고, `backend/regime_ml_service.py`는
`half_life_bars_for_timeframe`만 필요해서(레이블 생성과 무관) 레이블 모듈에
합치면 개념이 어긋난다.

```python
# engine/regime_math.py
from __future__ import annotations

import pandas as pd

from upbit_data_service import timeframe_duration

HALF_LIFE_DAYS = 1.0
N_MULTIPLIER = 2.5


def half_life_bars_for_timeframe(timeframe: str) -> float:
    ...  # engine/regime_detector.py에서 그대로 이동


def _ewm_std_series(returns: pd.Series, half_life_bars: float) -> pd.Series:
    ...  # engine/regime_detector.py에서 이동, docstring 수정(아래 참고)


def ewm_volatility(returns: pd.Series, half_life_bars: float) -> float:
    ...  # engine/regime_detector.py에서 그대로 이동(내부에서 _ewm_std_series 호출)
```

함수 구현은 `engine/regime_detector.py`에서 그대로 복사(로직 변경 없음, 위치만
이동). docstring은 대부분 그대로 옮기되, `_ewm_std_series`는 예외 — 원래
docstring이 "ewm_volatility와 compute_regime_probs_series가 공유한다(momentum이
_ewm_series를 공유하는 것과 대칭 구조)"라고 적혀 있는데, `compute_regime_probs_series`와
`_ewm_series` 둘 다 삭제 대상이라 이관 후에는 `ewm_volatility` 하나만 이 헬퍼를
쓴다. "수익률의 지수가중 표준편차 시계열(변동성 계산 전용) — ewm_volatility의
구현 디테일"처럼 현재 사실에 맞게 다시 쓴다. `N_MULTIPLIER`는
`backend/regime_service.py`에서 값(2.5) 그대로 가져온다.

**import 경로 갱신 대상 (3개 파일):**
- `backend/regime_ml_service.py`: `from engine.regime_detector import half_life_bars_for_timeframe` → `from engine.regime_math import half_life_bars_for_timeframe`
- `scripts/train_regime_ml.py`: `from backend.regime_service import N_MULTIPLIER` + `from engine.regime_detector import half_life_bars_for_timeframe` → 둘 다 `from engine.regime_math import N_MULTIPLIER, half_life_bars_for_timeframe`
- `engine/regime_ml_labels.py`: `from engine.regime_detector import ewm_volatility` → `from engine.regime_math import ewm_volatility`. 모듈 docstring의 "backend/regime_service.py:evaluate_market()의 정규화 실현수익률 루프(100~119행)와 동일하다"는 삭제되는 함수를 가리키므로, 그 공식 자체를 직접 설명하는 문장으로 재작성한다(예: "다음 n_bars 평균수익률을 이후 EWM변동성으로 정규화한 값 — 과거 규칙기반 evaluate_market()이 쓰던 것과 같은 정규화 방식을 ML 레이블에도 유지한다").

## 삭제

### 백엔드

- `backend/regime_service.py` 파일 전체(N_MULTIPLIER 이관 완료 후)
- `scripts/regime_backtest.py` CLI 전체
- `backend/main.py`의 `GET /api/v1/regime/backtest` 엔드포인트(`evaluate_market` import 포함)
- `engine/regime_detector.py` 파일 전체(위 "현재 상태 확인" 2번 참고)

### `scripts/train_regime_ml.py`의 부수 정리

`run_training()` 상단의 콘솔 경고 블록을 삭제한다:

```python
print(
    "  [주의] 이 스크립트의 hit-rate/confusion matrix는 fold별 학습구간 분위수"
    "(2%/16%/84%/98%)로 카테고리 경계를 정합니다. scripts/regime_backtest.py는"
    " 고정 임계값(CATEGORY_REFERENCE_SCORES 중간값)을 씁니다 — 두 스크립트의"
    " hit-rate/confusion 숫자는 직접 비교하지 마세요. 상관계수(correlation)는"
    " 두 스크립트가 동일한 방식으로 계산하므로, 이것이 비교에 쓸 지표입니다."
)
```

비교 대상 스크립트(`regime_backtest.py`) 자체가 사라지므로 이 경고의 전제가
무의미해진다. 모듈 docstring(4~7행)과 `_print_confusion_grid()` docstring(241행
부근)의 `scripts/regime_backtest.py` 언급도 함께 정리(그 스크립트와의 형식
비교라는 이유가 사라지므로, 형식 자체를 설명하는 문장으로 축약).

`engine/regime_ml_constants.py` docstring의 `scripts/regime_backtest.py` 언급
(9~12행, "자체 MARKETS 상수를 별도로 정의... 단일화하지 말 것")도 그 스크립트가
삭제되므로 함께 제거.

`engine/regime_features.py`의 `_MIN_VOLATILITY_FLOOR` 주석(23~25행, "regime_detector.py의
동명 상수와 값이 같아야 한다... 반대 방향은 순환참조... backend/regime_service.py의
_to_utc_iso와 같은 이유")은 `regime_detector.py`/`regime_service.py`가 모두
삭제되므로 무의미해진다. "이 모듈이 자체적으로 갖는 최소 변동성 하한값"이라는
설명으로 축약(값 1e-6은 그대로 유지 — `level_proximity()`가 실제로 쓰는 상수).

### 프론트엔드

**삭제**:
- `frontend/components/RegimeBacktestForm.tsx`
- `frontend/components/RegimeCurrentPrediction.tsx`
- `frontend/components/RegimeAccuracyReport.tsx`
- `frontend/components/RegimeChart.tsx`
- `frontend/lib/api/eda.ts`의 `getRegimeBacktest()`
- `frontend/lib/types/eda.ts`의 `RegimeBacktestResult`/`RegimeCandle`/`CurrentPrediction`
  (`RegimeCategory`/`MlCurrentPrediction`/`MlFoldPerformance`/`MlModelPerformance`는
  ML 카드가 쓰므로 유지)

**재작성 — `frontend/components/RegimeDashboard.tsx`**:

지금 이 파일의 유일한 책임은 "코인/봉타입을 고르면 규칙기반 백테스트를 돌려 4개
카드에 결과를 뿌리는 것"이다. 규칙기반이 사라지면 남는 책임은 "코인/봉타입을
고르면 ML 카드가 그 값을 받는 것" 하나뿐이다. 새 구현:

```tsx
'use client';

import { useEffect, useState } from 'react';
import RegimeMlCurrentPrediction from '@/components/RegimeMlCurrentPrediction';
import CoinSelect, { sortMarkets } from '@/components/CoinSelect';
import { Button } from '@/components/ui/button';
import { ApiError } from '@/lib/api/client';
import { getMarkets } from '@/lib/api/eda';
import { SECTION_HEADER_CLASS } from '@/lib/ui-classes';
import { formatTimeframe, TIMEFRAME_CODES } from '@/lib/format';
import type { Market } from '@/lib/types/eda';

const TIMEFRAME_OPTIONS = TIMEFRAME_CODES.map((timeframe) => ({
  label: formatTimeframe(timeframe),
  timeframe,
}));

export default function RegimeDashboard() {
  const [markets, setMarkets] = useState<Market[]>([]);
  const [marketsError, setMarketsError] = useState<string | null>(null);
  const [market, setMarket] = useState('');
  const [timeframe, setTimeframe] = useState('minutes60');

  useEffect(() => {
    getMarkets()
      .then((data) => {
        setMarkets(data);
        const sorted = sortMarkets(data, 'change_rate', 'desc');
        if (sorted.length > 0) setMarket((prev) => prev || sorted[0].market);
      })
      .catch((err) => setMarketsError(err instanceof ApiError ? err.message : '코인 목록을 불러오지 못했습니다.'));
  }, []);

  return (
    <div className="space-y-4">
      <div className="max-w-4xl space-y-4 rounded-xl border p-6 shadow-sm">
        <div>
          <label className="mb-1.5 block text-sm font-medium">코인 선택</label>
          <CoinSelect markets={markets} value={market} onChange={setMarket} />
          {marketsError && <p className="mt-1 text-xs text-destructive">{marketsError}</p>}
        </div>
        <div>
          <div className={SECTION_HEADER_CLASS}>봉데이터</div>
          <div className="flex flex-wrap gap-2 p-3">
            {TIMEFRAME_OPTIONS.map((opt) => (
              <Button
                key={opt.timeframe}
                type="button"
                variant={timeframe === opt.timeframe ? 'default' : 'outline'}
                size="sm"
                onClick={() => setTimeframe(opt.timeframe)}
              >
                {opt.label}
              </Button>
            ))}
          </div>
        </div>
      </div>
      {market && <RegimeMlCurrentPrediction market={market} timeframe={timeframe} />}
    </div>
  );
}
```

`RegimeMlCurrentPrediction` 자체가 이미 `timeframe !== 'minutes60'`/미학습 마켓을
안내문으로 처리하므로, 셀렉터 자체를 제한하지 않는다 — 이후 B(마켓 확장) 작업이
셀렉터를 건드릴 필요가 없다. `market`이 빈 문자열일 동안(마켓 목록 로딩 전)만
카드를 안 그린다(기존 `RegimeBacktestForm`도 같은 패턴으로 `market` 초기값을
빈 문자열로 뒀었다).

`frontend/app/regime/page.tsx`는 변경 없음(제목 "장세 판별" 그대로 유지 — ML
전용으로 바뀌었다고 제목을 바꿔야 할 이유는 없음).

## 테스트

- **삭제**: `tests/test_regime_service.py` 전체, `tests/test_backend.py`의
  `test_regime_backtest_returns_evaluated_result`/
  `test_regime_backtest_returns_400_when_evaluate_market_raises_value_error`/
  `test_regime_backtest_returns_500_when_evaluate_market_raises_runtime_error`/
  `test_regime_backtest_returns_400_for_malformed_start_date`(4개),
  `tests/test_regime_detector.py` 전체(모듈이 통째로 삭제되므로 트리밍이 아니라
  파일 삭제)
- **신설**: `tests/test_regime_math.py` — `test_regime_detector.py`에서 살아남는
  5개(`test_half_life_bars_for_timeframe_days_is_one`,
  `test_half_life_bars_for_timeframe_minutes60_is_24`,
  `test_half_life_bars_for_timeframe_minutes15_is_96`,
  `test_ewm_volatility_of_constant_returns_is_near_zero`,
  `test_ewm_volatility_matches_pandas_ewm_std`)를 그대로 옮기고 import만
  `from engine.regime_math import ewm_volatility, half_life_bars_for_timeframe`로
  갱신
- **수정**:
  - `tests/test_regime_ml_labels.py`: import 경로 갱신(`from engine.regime_math import ewm_volatility`), 모듈 docstring의 `evaluate_market()` 참조 문장 재작성(위 "이관" 절 참고)
  - `tests/test_train_regime_ml.py`의
    `test_run_training_prints_caveat_and_aggregate_summary_after_folds`: `assert "비교하지 마세요" in captured` 단언만 제거(나머지 `"상관계수"`/`"전체 fold 합산"` 단언은 유지). 테스트 이름이 더 이상 "caveat"을 검증하지 않으므로 `test_run_training_prints_aggregate_summary_after_folds`로 리네임

## 확인된 사실 (백로그에서 이관, 재검증 완료)

- `trading/`(실거래 로직) 전체가 `regime_detector`/`regime_service`/
  `evaluate_market`를 참조하지 않음 — grep 매치는 이름만 비슷한 무관한 상수였음
  (위 "현재 상태 확인" 5번 참고). 실거래에는 영향 없음.
- 과거 설계 문서(`docs/superpowers/specs_v1/2026-08-23-*`,
  `2026-08-24-regime-detector-reversal-gating-design.md` 등)는 규칙기반
  시스템을 설명하는 이력 문서로 그대로 남긴다(갱신하지 않음).

## 비범위

- ML 정확도 리포트/confusion matrix/과거 백테스트(백로그 D 항목, 계속 비범위)
- 1시간봉 외 타임프레임 지원
- B(마켓 확장), A1(재학습 자동화) — 이번 라운드 이후 별도 세션
