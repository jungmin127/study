# ADX 기반 장세 판별 엔진 + "장세 판별" 탭 재구축 — 설계 스펙

## 배경

[[upbit-v1-regime-strategy-pivot-adx-autoswap]]에서 확정한 4단계 피벗 계획
("미래 장세 예측" 포기 → "현재 장세 규칙기반 판별 + 코인별 3전략 자동스왑")
중 1단계(ML 장세판별 + 추세기반 세그먼트 레거시 전면 삭제,
`docs/superpowers/specs_v1/2026-09-05-regime-legacy-removal-design.md`)는
완료·커밋됐다. 이 스펙은 2단계: ADX(14)+방향지표(+DI/-DI)로 1시간봉
상승/하락/횡보를 **과거+현재 모두** 인과적으로(causal, 미래 데이터 불필요)
판별하는 엔진과, 이를 보여주는 "장세 판별" 탭 재구축을 다룬다.

참고한 기존 패턴: 2026-08-30에 구현했다가 1단계에서 함께 삭제된
`RegimeFactSegmentView`(`docs/superpowers/specs_v1/2026-08-30-regime-fact-segment-viewer-design.md`)가
지금 만들 것과 거의 동일한 UX(코인 선택 → 전체 기간 캔들 색칠 차트 +
연속 구간 표 + "그리드서치로 복사" 버튼)를 가지고 있었다. 차이는 라벨링
로직 하나뿐: 그때는 `compute_triple_barrier_labels()`(Triple Barrier,
2-라벨: 하락/하락아님)를 썼지만, 이번엔 ADX+DI 규칙기반(3-라벨: 상승/하락/
횡보)을 쓴다. 이번 스펙은 그 UX 패턴을 재사용하되 라벨링만 교체하고, 추가로
20개 메이저 코인의 현재 판정을 한눈에 보는 오버뷰 히트맵을 새로 얹는다.

## 목표

1. 순수 pandas 기반 ADX/DI 계산 엔진 — 과거 전체 기간 재현과 현재 시점
   계산에 동일한 함수를 쓴다 (3단계 전략 라이브러리 UI, 4단계 daemon 자동
   스왑에서도 이 엔진의 "현재 판정" 함수를 그대로 재사용할 예정)
2. "장세 판별" 탭(`/regime`)을 다음 두 화면으로 재구축:
   - 상단: 메이저 코인 20개의 현재 장세 판정 오버뷰 히트맵
   - 하단: 코인 1개를 골라 전체 기간 캔들 색칠 차트 + 연속 구간 표 +
     그리드서치 프리필 복사 버튼을 보는 상세 뷰어

## 비범위

- 3단계(코인별 하락/횡보/상승 전략 3개 매핑 관리 UI) — 별도 세션
- 4단계(daemon 자동 스왑 루프) — 별도 세션, 실거래에 직접 영향을 주는
  가장 리스크 높은 단계
- 오버뷰/상세 뷰어 결과 캐싱 — 매 요청 즉시 계산(느리면 후속 세션에서 처리)
- `MAJOR_MARKETS`(아래) 외 마켓 지원, `minutes60` 외 타임프레임
- 최소 지속봉수(24봉=1일) 사용자 조절 UI — 상수로 고정, 필요해지면 후속
  요청으로 처리
- backtrader `bt.indicators.ADX`/`DirectionalIndicator` 사용 —
  [[upbit-v1-runner-memory-leak]](Cerebro 반복 호출 메모리 누수)를 피하고
  20개 코인 히트맵을 가볍게 계산하기 위해 순수 pandas로 새로 구현한다

## 대상 코인 (`MAJOR_MARKETS`)

브레인스토밍에서 사용자가 확정한 20개 — "잠깐 반짝 급등하는 코인이 아니라
전통적으로 항상 시가총액 상위권을 유지한 코인" 기준. Upbit KRW 마켓 존재
여부 확인 완료(LTC/EOS는 현재 KRW 마켓 미지원이라 제외, MATIC은 POL로
리브랜딩되어 POL로 반영):

```
KRW-BTC, KRW-ETH, KRW-XRP, KRW-SOL, KRW-ADA, KRW-DOGE, KRW-LINK,
KRW-DOT, KRW-AVAX, KRW-TRX, KRW-POL, KRW-BCH, KRW-ETC, KRW-XLM,
KRW-ATOM, KRW-UNI, KRW-NEAR, KRW-ICP, KRW-HBAR, KRW-SUI
```

**나중에 코인 추가/제외**: 아래 "확장 포인트" 참고 — 상수 리스트 한 줄만
고치면 백엔드 오버뷰 API와 프론트 코인 선택기 양쪽에 자동 반영되도록
설계한다(한글명은 상수에 저장하지 않고 기존 `getMarkets()` API로 조회 —
중복/오탈자 방지).

## 설계

### 1. 엔진 — `engine/regime_adx.py` (신규)

Wilder 방식 True Range/+DM/-DM 스무딩으로 ADX/+DI/-DI를 계산하는 순수
pandas 함수와, 그 값을 3-라벨로 변환하는 분류 함수:

```python
"""
engine/regime_adx.py

ADX(Average Directional Index)+방향지표(+DI/-DI)로 상승/하락/횡보를
인과적으로(미래 데이터 불필요) 판별한다. Wilder 원 공식의 순수 pandas
구현 — backtrader Cerebro를 거치지 않아 과거 전체 기간 재계산과 최신
시점 계산에 동일한 함수를 쓸 수 있고, 반복 호출 메모리 누수
([[upbit-v1-runner-memory-leak]]) 위험이 없다. 설계 문서:
docs/superpowers/specs_v2/2026-09-06-adx-regime-engine-design.md
"""
from __future__ import annotations

import pandas as pd

PERIOD = 14
ADX_TREND_THRESHOLD = 25.0


def compute_adx_di(df: pd.DataFrame, period: int = PERIOD) -> pd.DataFrame:
    """df는 high/low/close 컬럼을 포함해야 한다. Wilder 스무딩(alpha=1/period)으로
    ADX/plus_di/minus_di 3개 컬럼을 가진 DataFrame을 df와 같은 인덱스로 반환한다.
    앞쪽 워밍업 구간(대략 2*period봉)은 NaN이다."""
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    prev_high = high.shift(1)
    prev_low = low.shift(1)

    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)

    up_move = high - prev_high
    down_move = prev_low - low
    plus_dm = ((up_move > down_move) & (up_move > 0)).astype(float) * up_move.clip(lower=0)
    minus_dm = ((down_move > up_move) & (down_move > 0)).astype(float) * down_move.clip(lower=0)

    alpha = 1.0 / period
    smoothed_tr = tr.ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    smoothed_plus_dm = plus_dm.ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    smoothed_minus_dm = minus_dm.ewm(alpha=alpha, adjust=False, min_periods=period).mean()

    plus_di = 100 * smoothed_plus_dm / smoothed_tr
    minus_di = 100 * smoothed_minus_dm / smoothed_tr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = dx.ewm(alpha=alpha, adjust=False, min_periods=period).mean()

    return pd.DataFrame({"adx": adx, "plus_di": plus_di, "minus_di": minus_di})


def classify_regime(
    adx: float, plus_di: float, minus_di: float, threshold: float = ADX_TREND_THRESHOLD
) -> str | None:
    """단일 시점 값을 "상승"/"하락"/"횡보" 중 하나로 분류한다. adx가 NaN이면
    (워밍업 구간) None을 반환한다."""
    if pd.isna(adx) or pd.isna(plus_di) or pd.isna(minus_di):
        return None
    if adx <= threshold:
        return "횡보"
    return "상승" if plus_di > minus_di else "하락"
```

`plus_di + minus_di`가 0에 가까울 때(극초반 무변동 구간) `dx` 계산이
0/0 → NaN이 될 수 있는데, pandas가 자동으로 NaN 전파하므로 별도 가드
불필요(`classify_regime`이 NaN을 이미 None으로 처리).

### 2. 상수 — `engine/regime_adx_constants.py` (신규)

```python
"""
engine/regime_adx_constants.py

"장세 판별" 탭 오버뷰 히트맵 + 3단계 전략 라이브러리 UI가 공유하는 대상
코인 목록. 설계 문서: docs/superpowers/specs_v2/2026-09-06-adx-regime-engine-design.md

**확장 포인트**: 코인 추가/제외는 이 리스트만 수정하면 된다. 한글명은
저장하지 않는다 — 프론트가 기존 getMarkets() API로 조회해 표시한다.
"""
MAJOR_MARKETS = [
    "KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-ADA", "KRW-DOGE",
    "KRW-LINK", "KRW-DOT", "KRW-AVAX", "KRW-TRX", "KRW-POL", "KRW-BCH",
    "KRW-ETC", "KRW-XLM", "KRW-ATOM", "KRW-UNI", "KRW-NEAR", "KRW-ICP",
    "KRW-HBAR", "KRW-SUI",
]
```

### 3. 백엔드 서비스 — `backend/regime_adx_service.py` (신규)

세그먼트 묶기 로직은 옛 `regime_fact_service.py`(1단계에서 삭제, git
history에 남아있음)의 `_same_label`/연속 구간 스캔 패턴을 그대로 가져오되
2-라벨(하락/하락아님)에서 3-라벨(상승/하락/횡보)로 바꾼다:

```python
"""
backend/regime_adx_service.py

engine/regime_adx.py의 ADX+DI 규칙기반 판정으로 "장세 판별" 탭의 (1) 코인별
과거 전체 기간 상승/하락/횡보 구간 뷰어, (2) 메이저 코인 20개 현재 판정
오버뷰를 계산한다. 캐싱 없음 — 요청마다 즉시 계산. 설계 문서:
docs/superpowers/specs_v2/2026-09-06-adx-regime-engine-design.md
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from engine.regime_adx import classify_regime, compute_adx_di
from engine.regime_adx_constants import MAJOR_MARKETS
from upbit_data_service import get_candles

HISTORY_START = datetime(2024, 1, 1, tzinfo=timezone.utc)
# 표에 나열할 최소 지속봉수(24봉=minutes60 기준 1일). 미만인 구간은 차트에는
# 그대로 색칠되지만 표에는 나오지 않는다.
MIN_SEGMENT_BARS = 24
# 오버뷰(최신 시점만 필요)용 조회 기간 — ADX(14) 워밍업(약 28봉)에 여유를
# 둔 값. 전체 히스토리를 긁을 필요 없다.
OVERVIEW_LOOKBACK_BARS = 200


def _to_iso(ts: pd.Timestamp) -> str:
    return ts.isoformat()


def _same_label(a: object, b: object) -> bool:
    """NaN != NaN이 True인 파이썬 기본 비교로는 "같은 미분류 구간"을 라벨이
    계속 바뀌는 것으로 오인하게 되므로, 둘 다 None이면 같다고 취급한다."""
    if a is None and b is None:
        return True
    return a == b


def compute_adx_regime_history(market: str, timeframe: str) -> dict:
    """market의 전체 기간(HISTORY_START~현재) ADX 장세 라벨을 봉별 OHLCV+라벨
    배열과, 최소 지속봉수 이상인 연속 구간 목록으로 반환한다. 반환값:
    {market, timeframe, bars, segments}. bars의 각 원소는
    {time, open, high, low, close, label}(label은 "상승"/"하락"/"횡보"/None).
    segments의 각 원소는 {start, end, label, bar_count}."""
    df = get_candles(market, timeframe, HISTORY_START, datetime.now(timezone.utc))
    adx_di = compute_adx_di(df)
    labels = [
        classify_regime(row.adx, row.plus_di, row.minus_di)
        for row in adx_di.itertuples()
    ]

    bars = [
        {
            "time": _to_iso(row.candle_time),
            "open": float(row.open), "high": float(row.high),
            "low": float(row.low), "close": float(row.close),
            "label": label,
        }
        for row, label in zip(df.itertuples(), labels)
    ]

    segments: list[dict] = []
    if labels:
        run_start_idx = 0
        run_label = labels[0]
        for i in range(1, len(labels) + 1):
            at_end = i == len(labels)
            cur_label = None if at_end else labels[i]
            if at_end or not _same_label(cur_label, run_label):
                bar_count = i - run_start_idx
                if run_label is not None and bar_count >= MIN_SEGMENT_BARS:
                    segments.append({
                        "start": _to_iso(df["candle_time"].iloc[run_start_idx]),
                        "end": _to_iso(df["candle_time"].iloc[i - 1]),
                        "label": run_label,
                        "bar_count": bar_count,
                    })
                run_start_idx = i
                run_label = cur_label

    return {"market": market, "timeframe": timeframe, "bars": bars, "segments": segments}


def compute_adx_regime_overview(timeframe: str) -> list[dict]:
    """MAJOR_MARKETS 각각의 현재(최신 봉) ADX 장세 판정을 반환한다. 반환값:
    [{market, label, adx, plus_di, minus_di}, ...] (label은 "상승"/"하락"/
    "횡보"/None, 순서는 MAJOR_MARKETS와 동일)."""
    start = datetime.now(timezone.utc) - timedelta(hours=OVERVIEW_LOOKBACK_BARS)
    results = []
    for market in MAJOR_MARKETS:
        df = get_candles(market, timeframe, start, datetime.now(timezone.utc))
        adx_di = compute_adx_di(df)
        last = adx_di.iloc[-1]
        label = classify_regime(last.adx, last.plus_di, last.minus_di)
        results.append({
            "market": market,
            "label": label,
            "adx": None if pd.isna(last.adx) else float(last.adx),
            "plus_di": None if pd.isna(last.plus_di) else float(last.plus_di),
            "minus_di": None if pd.isna(last.minus_di) else float(last.minus_di),
        })
    return results
```

`OVERVIEW_LOOKBACK_BARS`는 시간 기준(`timedelta(hours=...)`)으로 어림잡은
값이라 `minutes60` 전제에서만 정확하다 — 비범위에 명시했듯 다른 타임프레임은
지원하지 않으므로 문제없다.

### 4. 엔드포인트 — `backend/main.py` 정리

상단 import 목록에 추가:
```python
from backend.regime_adx_service import compute_adx_regime_history, compute_adx_regime_overview
```

엔드포인트 본체(`Query`는 이미 import돼 있음):
```python
@app.get("/api/v1/regime/adx-segments")
def get_regime_adx_segments_endpoint(
    market: str = Query(...),
    timeframe: str = Query(...),
) -> dict:
    return compute_adx_regime_history(market, timeframe)


@app.get("/api/v1/regime/adx-overview")
def get_regime_adx_overview_endpoint(timeframe: str = Query(...)) -> list[dict]:
    return compute_adx_regime_overview(timeframe)
```

`get_candles`가 던질 수 있는 예외는 기존 다른 엔드포인트들과 동일하게
FastAPI가 500으로 처리하도록 둔다(별도 `try/except` 불필요 — 옛
`/api/v1/regime/fact-segments`와 동일 판단).

### 5. 프론트엔드

**`NavTabs.tsx`**: `{ href: '/regime', title: '장세 판별', icon: Activity }`
항목 복원(1단계에서 삭제된 것과 동일한 위치·아이콘).

**타입 추가** (`frontend/lib/types/eda.ts`):
```typescript
export type RegimeAdxLabel = '상승' | '하락' | '횡보';

export interface RegimeAdxBar extends OhlcvPoint {
  label: RegimeAdxLabel | null;
}

export interface RegimeAdxSegment {
  start: string;
  end: string;
  label: RegimeAdxLabel;
  bar_count: number;
}

export interface RegimeAdxHistory {
  market: string;
  timeframe: string;
  bars: RegimeAdxBar[];
  segments: RegimeAdxSegment[];
}

export interface RegimeAdxOverviewItem {
  market: string;
  label: RegimeAdxLabel | null;
  adx: number | null;
  plus_di: number | null;
  minus_di: number | null;
}
```

**API 함수 추가** (`frontend/lib/api/eda.ts`):
```typescript
export function getRegimeAdxHistory(params: {
  market: string;
  timeframe: string;
}): Promise<RegimeAdxHistory> {
  const query = new URLSearchParams(params);
  return apiFetch<RegimeAdxHistory>(`/api/v1/regime/adx-segments?${query.toString()}`);
}

export function getRegimeAdxOverview(timeframe: string): Promise<RegimeAdxOverviewItem[]> {
  const query = new URLSearchParams({ timeframe });
  return apiFetch<RegimeAdxOverviewItem[]>(`/api/v1/regime/adx-overview?${query.toString()}`);
}
```

**상수 미러 (`frontend/lib/constants/regime.ts`, 신규)**: 백엔드
`MAJOR_MARKETS`와 값을 동일하게 유지해야 하는 프론트 전용 목록(코인
선택기 범위 제한용). 값 동기화는 `test_regime_adx_constants_frontend_sync.py`
(아래 테스트 전략 참고)로 회귀 방지한다 — 옛 `TRAINED_MARKETS` 프론트
동기화 테스트와 동일한 관례.
```typescript
export const TIMEFRAME = 'minutes60';

export const MAJOR_MARKETS = [
  'KRW-BTC', 'KRW-ETH', 'KRW-XRP', 'KRW-SOL', 'KRW-ADA', 'KRW-DOGE',
  'KRW-LINK', 'KRW-DOT', 'KRW-AVAX', 'KRW-TRX', 'KRW-POL', 'KRW-BCH',
  'KRW-ETC', 'KRW-XLM', 'KRW-ATOM', 'KRW-UNI', 'KRW-NEAR', 'KRW-ICP',
  'KRW-HBAR', 'KRW-SUI',
] as const;
```

**색상**: 상승=`--regime-surge-up`, 하락=`--regime-surge-down`,
횡보=`--marker-boundary`, 미분류(워밍업)=`--trend-unclassified` — 전부
1단계 삭제 이후에도 `globals.css`에 남아있는 기존 변수 재사용(다른 곳에서
이미 쓰던 값이라 새로 정의하지 않음).

**`frontend/app/regime/page.tsx`** (신규) + **`RegimeDashboard.tsx`**
(신규, 옛 이름 재사용하되 내용은 새로 작성) — 2단 구성, 선택된 코인
상태는 `RegimeDashboard`가 소유하고 오버뷰/상세 양쪽에 props로 내림:

```typescript
'use client';

import { useState } from 'react';
import RegimeAdxOverview from '@/components/RegimeAdxOverview';
import RegimeAdxDetailView from '@/components/RegimeAdxDetailView';
import { MAJOR_MARKETS } from '@/lib/constants/regime';

export default function RegimeDashboard() {
  const [market, setMarket] = useState<string>(MAJOR_MARKETS[0]);

  return (
    <div className="space-y-6">
      <RegimeAdxOverview selectedMarket={market} onSelectMarket={setMarket} />
      <RegimeAdxDetailView market={market} onMarketChange={setMarket} />
    </div>
  );
}
```

**`frontend/components/RegimeAdxOverview.tsx`** (신규): `MAJOR_MARKETS`
20개를 그리드 타일로 렌더링, 각 타일이 `getRegimeAdxOverview(TIMEFRAME)`
결과에서 자기 마켓의 `label`로 색칠되고 클릭 시 `onSelectMarket(market)`
호출. 코인 한글명은 기존 마켓 조회 API(다른 코인 선택기들이 쓰는 것과
동일한 소스)로 가져와 표시. 로딩/에러 상태는 기존 컴포넌트들과 동일한
패턴(`불러오는 중...`/에러 메시지 텍스트).

**`frontend/components/RegimeAdxDetailView.tsx`** (신규, 옛
`RegimeFactSegmentView.tsx`와 동일 구조 — 데이터 로드 후 코인 선택기+
차트+표 조립):
- 코인 선택기: `MAJOR_MARKETS` 범위 내에서만 선택(현재 `market`은 부모가
  소유, `onMarketChange`로 변경 알림 — 오버뷰 타일 클릭과 상태 공유)
- `getRegimeAdxHistory({ market, timeframe: TIMEFRAME })` 호출 →
  `RegimeAdxChart`(차트) + `RegimeAdxSegmentTable`(표) 렌더링

**`frontend/components/RegimeAdxChart.tsx`** (신규, 옛
`RegimeFactChart.tsx`와 동일한 lightweight-charts 캔들 색칠 패턴, 2색→3색
전환): `bar.label`이 `'상승'`/`'하락'`/`'횡보'`/`null` 4가지 케이스를 색상
4종(`--regime-surge-up`/`--regime-surge-down`/`--marker-boundary`/
`--trend-unclassified`)에 매핑하는 것만 다르고 canvas 2D oklch→rgba 변환,
`ResizeObserver`, `toUnix()` 등 나머지 구조는 동일.

**`frontend/components/RegimeAdxSegmentTable.tsx`** (신규, 옛
`RegimeFactSegmentTable.tsx`와 동일한 정렬 가능 표 + 복사 버튼 패턴):
`LABEL_TEXT_CLASS`가 3-라벨(상승/하락/횡보)에 대응하는 것 외에는
`buildGridSearchHref(market, timeframe, seg)` → `/grid-search?market=&
timeframe=&start=&end=` 프리필 로직 그대로.

## 테스트 전략

- **`tests/test_regime_adx.py`** (신규): 합성 OHLCV로
  1. `compute_adx_di()`가 알려진 값(수기 계산 또는 참조 구현)과 일치하는지
     — 꾸준히 오르는 구간(설계상 +DI 우세, ADX 상승), 꾸준히 내리는 구간
     (−DI 우세), 무변동/횡보 구간(ADX가 임계치 이하로 유지)을 각각 구성해
     검증
  2. `classify_regime()`의 경계값(ADX가 정확히 25일 때 "횡보"로 분류되는지
     — 스펙상 `<=`)과 워밍업 구간 NaN → `None` 처리
  3. `compute_adx_regime_history()`의 세그먼트 묶기가 라벨 전환 지점에서
     정확히 나뉘고, 최소 지속봉수 미만 구간이 `segments`에서 빠지는지
     (`bars`에는 여전히 포함)
- **`tests/test_regime_adx_constants_frontend_sync.py`** (신규, 옛
  `test_regime_ml_constants_frontend_sync.py`와 동일 관례): 백엔드
  `MAJOR_MARKETS`와 프론트 `frontend/lib/constants/regime.ts`의
  `MAJOR_MARKETS` 값이 정확히 일치하는지 텍스트 파싱으로 검증 — 둘 중
  하나만 고치는 실수 방지
- 프론트엔드: 기존 관례대로 컴포넌트 자동화 테스트 없음 — 구현 후
  Playwright MCP로 `/regime` 탭에서 오버뷰 20개 타일 표시 → 타일 클릭 →
  하단 뷰어 코인 전환 → 차트 색칠 → 표 정렬 → "그리드서치로 복사" 클릭 시
  `/grid-search`가 올바른 쿼리스트링으로 열리는지 수동 검증
- `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/ -q` 전체
  통과, `cd frontend && npm run build` 성공

## 완료 기준

- `/regime` 탭에서 20개 메이저 코인 오버뷰 히트맵이 뜬다
- 히트맵 타일 클릭 → 해당 코인의 전체 기간 차트(3색 색칠)+구간 표가
  하단에 뜬다
- 표의 "그리드서치로 복사" 버튼이 해당 구간의 market/timeframe/start/end로
  `/grid-search` 폼을 정확히 프리필한다
- 신규 백엔드 유닛 테스트(`test_regime_adx.py`,
  `test_regime_adx_constants_frontend_sync.py`) 통과, 기존 테스트 스위트
  회귀 없음
- 브라우저로 위 흐름 수동 검증
