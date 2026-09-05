# 코인별 fact 장세 구간 뷰어 + 그리드서치 연동 — 설계 스펙

## 배경

`docs/regime-ml-backlog.md`의 ②(모델 성능 대폭 개선) 착수 전 선행 작업. ①(Phase 1
fact 라벨 백테스트 분석, `scripts/analyze_regime_fact_performance.py`, SHIPPED
2026-08-30)에서 장세별 전략 성과가 실제로 크게 갈린다는 게 확인됐지만, 그 스크립트는
콘솔 리포트일 뿐 마켓별로 어느 기간이 fact 기준 "하락"/"하락아님"인지 직접 보여주는
화면이 아니다. 사용자 요청(2026-08-30): ② 착수 전에 각 코인별로 이 기간을 눈으로
확인할 수 있어야 하고, 확인한 기간을 그대로 그리드서치로 복사해 돌릴 수 있어야 한다.

`/analysis`(세그먼트) 탭의 추세 구간 분석(`engine/trend_segments.py` +
`TrendSegmentChart`/`TrendSegmentTable`)이 정확히 같은 형태의 기능(구간별 색칠 차트 +
표 + "그리드서치로 복사" 버튼, `[[upbit-v1-trend-segment-grid-search-copy]]`)을 이미
가지고 있어 이번 기능은 그 패턴을 그대로 재사용한다. 차이는 하나: 추세 구간은
ZigZag 스윙으로 만든 알고리즘적 구간(일봉)이고, 이번 fact 구간은 이미 존재하는
`compute_triple_barrier_labels()`(Triple Barrier, ML 아님)가 매긴 봉 단위(시간봉)
라벨을 연속 구간으로 묶은 것이다.

브레인스토밍에서 다음이 확정됐다:

- 배치: `/regime` 탭 안, `RegimeMlCurrentPrediction` 카드 아래에 새 카드로 추가
  (같은 코인 선택기·`minutes60` 타임프레임 공유)
- 대상 코인: `RegimeDashboard.tsx`가 이미 쓰는 `TRAINED_MARKETS`(20개, ML 학습
  대상과 동일) — fact 라벨 자체는 ML과 무관하지만, "② 작업 전에 학습 데이터를
  눈으로 검증한다"는 목적에 맞춰 범위를 ML 학습 마켓으로 한정
  (`engine/regime_ml_constants.py:TRAINING_MARKETS`와 동일 집합)
- 캐싱 없음: `KRW-BTC` `minutes60` 2024-01-01~현재(23,305봉) 기준 캔들 fetch+라벨
  계산 실측 0.57초 — DB 캐시 테이블/갱신 버튼 없이 요청마다 즉시 계산
- "그리드서치로 복사"는 `TrendSegmentTable.tsx`의 `buildGridSearchHref`와 동일하게
  `/grid-search?market=&timeframe=&start=&end=` 쿼리스트링으로 그리드서치 폼을
  프리필만 한다 — 자동 실행 없음. 이후 백테스트 실행/DB 저장/라이브 전략 교체는
  기존 그리드서치 파이프라인과 100% 동일(사용자에게 확인 완료)
- 표에 나열할 구간은 최소 지속봉수(기본 24봉=1일) 이상만 — 라벨이 바 단위로 자주
  뒤집혀 1~2봉짜리 구간까지 표에 나오면 실용성이 떨어짐. 차트의 봉별 색칠은
  필터와 무관하게 항상 전체 표시

## 목표

1. 선택한 코인의 `minutes60` 캔들 전체 기간을 fact 라벨(하락/하락아님)로 색칠한
   캔들스틱 차트를 보여준다
2. 최소 지속봉수 이상인 연속 구간을 표로 나열하고, 각 구간을 그리드서치 폼으로
   바로 복사할 수 있는 버튼을 제공한다
3. ML 예측 카드(`RegimeMlCurrentPrediction`)와 시각적으로 통일된 색상(하락/
   하락아님)을 쓴다

## 비범위

- 캐싱/DB 저장(위 확정사항 참고) — 매번 즉시 계산
- 그리드서치 자동 실행 — URL 프리필까지만, 실행은 사용자가 `/grid-search`
  페이지에서 직접
- `TRAINED_MARKETS` 외 마켓 지원
- `minutes60` 외 타임프레임 지원 — 장세 판별 ML 파이프라인 자체가 `minutes60`
  전용(`RegimeMlCurrentPrediction.tsx`와 동일 제약)
- 최소 지속봉수 값을 사용자가 UI에서 조절하는 기능 — 상수로 고정(24봉), 필요해지면
  후속 요청으로 처리

## 설계

### 백엔드

**새 파일 `backend/regime_fact_service.py`** (`backend/regime_ml_service.py`는
독스트링에 "정확도 리포트/과거 백테스트는 다루지 않는다"고 명시된 ML 예측 전용
파일이라, ML과 무관한 fact 라벨 서비스는 분리한다):

```python
"""
backend/regime_fact_service.py

engine/regime_ml_labels.py의 compute_triple_barrier_labels()(Triple Barrier, ML
아님)로 코인별 과거 "하락/하락아님" fact 구간을 계산한다. /regime 탭의 시각화 +
그리드서치 프리필 전용. 캐싱 없음 — 요청마다 즉시 계산(실측: KRW-BTC minutes60
2024-01-01~현재 23,305봉 기준 0.57초). 설계 문서:
docs/superpowers/specs_v1/2026-08-30-regime-fact-segment-viewer-design.md
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from engine.regime_math import N_MULTIPLIER, half_life_bars_for_timeframe
from engine.regime_ml_labels import compute_triple_barrier_labels
from upbit_data_service import get_candles

START = datetime(2024, 1, 1, tzinfo=timezone.utc)
# scripts/train_regime_ml.py:BARRIER_K와 동일 값(2026-08-29 select_barrier_k.py로
# 결정한 프로덕션 학습 파이프라인 상수). 이 모듈은 학습 모듈을 import하지 않으므로
# 값만 복제한다.
BARRIER_K = 6.25
# 표에 나열할 최소 지속봉수(24봉=minutes60 기준 1일). 미만인 구간은 차트에는
# 그대로 색칠되지만 표에는 나오지 않는다(바 단위 라벨이 자주 뒤집혀 실용성 저하 방지).
MIN_SEGMENT_BARS = 24


def _to_iso(ts: pd.Timestamp) -> str:
    return ts.isoformat()


def _same_label(a: object, b: object) -> bool:
    """NaN != NaN이 True인 파이썬 기본 비교로는 "같은 NaN 구간"을 라벨이 계속
    바뀌는 것으로 오인하게 되므로, 둘 다 NaN이면 같다고 취급한다."""
    if pd.isna(a) and pd.isna(b):
        return True
    return a == b


def compute_fact_regime_segments(market: str, timeframe: str) -> dict:
    """market의 fact 장세 라벨을 봉별 OHLCV+라벨 배열과, 최소 지속봉수 이상인
    연속 구간 목록으로 반환한다. 반환값: {market, timeframe, bars, segments}.
    bars의 각 원소는 {time, open, high, low, close, label}(label은 "하락"/
    "하락아님"/None). segments의 각 원소는 {start, end, label, bar_count}."""
    df = get_candles(market, timeframe, START, datetime.now(timezone.utc))
    half_life_bars = half_life_bars_for_timeframe(timeframe)
    n_bars = round(half_life_bars * N_MULTIPLIER)
    labels = compute_triple_barrier_labels(df, half_life_bars, n_bars, BARRIER_K)

    bars = [
        {
            "time": _to_iso(row.candle_time),
            "open": float(row.open), "high": float(row.high),
            "low": float(row.low), "close": float(row.close),
            "label": None if pd.isna(label) else label,
        }
        for row, label in zip(df.itertuples(), labels)
    ]

    segments: list[dict] = []
    if len(labels) > 0:
        run_start_idx = 0
        run_label = labels.iloc[0]
        for i in range(1, len(labels) + 1):
            at_end = i == len(labels)
            cur_label = None if at_end else labels.iloc[i]
            if at_end or not _same_label(cur_label, run_label):
                bar_count = i - run_start_idx
                if pd.notna(run_label) and bar_count >= MIN_SEGMENT_BARS:
                    segments.append({
                        "start": _to_iso(df["candle_time"].iloc[run_start_idx]),
                        "end": _to_iso(df["candle_time"].iloc[i - 1]),
                        "label": run_label,
                        "bar_count": bar_count,
                    })
                run_start_idx = i
                run_label = cur_label

    return {"market": market, "timeframe": timeframe, "bars": bars, "segments": segments}
```

**새 엔드포인트** (`backend/main.py`, 기존 `/api/v1/regime/ml-current-prediction`
근처):

`backend/main.py` 상단 import 목록(`from backend.regime_ml_service import (...)` 근처)에
추가:

```python
from backend.regime_fact_service import compute_fact_regime_segments
```

엔드포인트 본체(`Query`는 이미 `from fastapi import FastAPI, HTTPException, Query`로
import돼 있음):

```python
@app.get("/api/v1/regime/fact-segments")
def get_regime_fact_segments_endpoint(
    market: str = Query(...),
    timeframe: str = Query(...),
) -> dict:
    return compute_fact_regime_segments(market, timeframe)
```

`get_candles`가 던질 수 있는 예외는 기존 다른 엔드포인트들과 동일하게 FastAPI가
500으로 처리하도록 둔다(현재 `/api/v1/regime/ml-current-prediction`도 `ValueError`/
`FileNotFoundError`/`RuntimeError`만 별도 처리하고 그 외는 그대로 두는 것과 동일한
수준 — 이 엔드포인트는 그 세 예외를 던지지 않으므로 별도 `try/except` 불필요).

### 프론트엔드

**타입 추가** (`frontend/lib/types/eda.ts`, `RegimeCategory`/`OhlcvPoint` 정의
근처):

```typescript
export interface RegimeFactBar extends OhlcvPoint {
  label: RegimeCategory | null;
}

export interface RegimeFactSegment {
  start: string;
  end: string;
  label: RegimeCategory;
  bar_count: number;
}

export interface RegimeFactAnalysis {
  market: string;
  timeframe: string;
  bars: RegimeFactBar[];
  segments: RegimeFactSegment[];
}
```

**API 함수 추가** (`frontend/lib/api/eda.ts`, `getRegimeMlCurrentPrediction` 근처):

```typescript
export function getRegimeFactSegments(params: {
  market: string;
  timeframe: string;
}): Promise<RegimeFactAnalysis> {
  const query = new URLSearchParams(params);
  return apiFetch<RegimeFactAnalysis>(`/api/v1/regime/fact-segments?${query.toString()}`);
}
```

**색상 함수 재사용(기존 파일 수정)**: `frontend/components/RegimeMlCurrentPrediction.tsx`의
`function categoryVarName(label: RegimeCategory): string`(하락→`--regime-surge-down`,
하락아님→`--marker-boundary`) 선언 앞에 `export`만 추가한다(`export function
categoryVarName(...)`) — 함수 본문은 변경 없음. 새 차트는 이 함수를 호출해 CSS 변수
이름을 얻는다(`--regime-surge-down`/`--marker-boundary`를 새 파일에 직접 다시 적지
않음 — 나중에 `RegimeMlCurrentPrediction.tsx`에서 색이 바뀌면 이 차트도 자동으로
같이 바뀌어야 하므로).

**`frontend/components/RegimeFactChart.tsx`** (신규, `TrendSegmentChart.tsx`와
같은 lightweight-charts 캔들 색칠 패턴이지만 날짜별 구간 조회 없이 봉마다 이미
붙은 `label`을 바로 씀):

```typescript
'use client';

import { useEffect, useRef } from 'react';
import { createChart, CandlestickSeries, ColorType, CrosshairMode, type UTCTimestamp } from 'lightweight-charts';
import type { RegimeFactBar } from '@/lib/types/eda';
import { categoryVarName } from '@/components/RegimeMlCurrentPrediction';

export default function RegimeFactChart({ bars }: { bars: RegimeFactBar[] }) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current || bars.length === 0) return;

    // TrendSegmentChart.tsx와 동일한 이유: getComputedStyle의 oklch() 반환값을
    // lightweight-charts가 파싱하지 못해, canvas 2D로 한 번 그려 rgba()로 변환한다.
    const resolveColor = (varName: string): string => {
      const raw = getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
      const canvas = document.createElement('canvas');
      const ctx = canvas.getContext('2d');
      if (!ctx) return raw;
      ctx.fillStyle = raw;
      ctx.fillRect(0, 0, 1, 1);
      const pixel = ctx.getImageData(0, 0, 1, 1).data;
      return `rgba(${pixel[0]}, ${pixel[1]}, ${pixel[2]}, ${(pixel[3] / 255).toFixed(3)})`;
    };

    const downColor = resolveColor(categoryVarName('하락'));
    const notDownColor = resolveColor(categoryVarName('하락아님'));
    const unclassifiedColor = resolveColor('--trend-unclassified');
    const background = resolveColor('--background');
    const foreground = resolveColor('--foreground');
    const border = resolveColor('--border');

    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: containerRef.current.clientHeight,
      layout: { background: { type: ColorType.Solid, color: background }, textColor: foreground },
      crosshair: { mode: CrosshairMode.Normal },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: border },
      rightPriceScale: { borderColor: border },
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: notDownColor, downColor, borderVisible: false,
      wickUpColor: notDownColor, wickDownColor: downColor,
    });

    // PriceChart.tsx:toUnix()와 동일한 변환(초 단위 유닉스 타임으로 내림).
    const toUnix = (iso: string): UTCTimestamp => Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp;

    const candleData = bars.map((bar) => {
      const color = bar.label === '하락' ? downColor : bar.label === '하락아님' ? notDownColor : unclassifiedColor;
      return {
        time: toUnix(bar.time),
        open: bar.open, high: bar.high, low: bar.low, close: bar.close,
        color, borderColor: color, wickColor: color,
      };
    });
    candleSeries.setData(candleData);

    chart.timeScale().fitContent();

    const resizeObserver = new ResizeObserver(() => {
      if (!containerRef.current) return;
      chart.applyOptions({
        width: containerRef.current.clientWidth,
        height: containerRef.current.clientHeight,
      });
    });
    resizeObserver.observe(containerRef.current);

    return () => {
      resizeObserver.disconnect();
      chart.remove();
    };
  }, [bars]);

  return (
    <div className="w-full">
      <div className="mb-2 flex items-center gap-4 text-xs text-muted-foreground">
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: 'var(--regime-surge-down)' }} />
          하락
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: 'var(--marker-boundary)' }} />
          하락아님
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: 'var(--trend-unclassified)' }} />
          미분류(최신)
        </span>
      </div>
      <div ref={containerRef} className="h-60 w-full rounded-lg overflow-hidden border md:h-80" />
    </div>
  );
}
```

(`OhlcvPoint`/`TrendSegmentChart`는 `time`을 `YYYY-MM-DD` day string으로 쓰지만
이번엔 시간봉이라 `UTCTimestamp`(초 단위 유닉스 타임)를 쓴다 — lightweight-charts
캔들스틱 시리즈가 두 형식 모두 지원하며, 시간봉 데이터는 day string으로는 같은 날
여러 봉을 구분할 수 없으므로 `UTCTimestamp`가 필수다.)

**`frontend/components/RegimeFactSegmentTable.tsx`** (신규, `TrendSegmentTable.tsx`와
동일한 정렬 가능 표 + 복사 버튼 패턴):

```typescript
'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { ArrowDown, ArrowUp, ArrowUpDown, Copy } from 'lucide-react';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Button } from '@/components/ui/button';
import type { RegimeFactSegment } from '@/lib/types/eda';
import { formatDateTimeShort } from '@/lib/format';

const LABEL_TEXT_CLASS: Record<RegimeFactSegment['label'], string> = {
  하락: 'text-[color:var(--regime-surge-down)]',
  하락아님: 'text-muted-foreground',
};

function buildGridSearchHref(market: string, timeframe: string, seg: RegimeFactSegment): string {
  const params = new URLSearchParams({
    market,
    timeframe,
    start: seg.start.slice(0, 10),
    end: seg.end.slice(0, 10),
  });
  return `/grid-search?${params.toString()}`;
}

type SortKey = 'start' | 'bar_count';
type SortDir = 'asc' | 'desc';

export default function RegimeFactSegmentTable({
  segments, market, timeframe,
}: { segments: RegimeFactSegment[]; market: string; timeframe: string }) {
  const [sortKey, setSortKey] = useState<SortKey>('start');
  const [sortDir, setSortDir] = useState<SortDir>('asc');

  const sorted = useMemo(() => {
    const factor = sortDir === 'asc' ? 1 : -1;
    return [...segments].sort((a, b) => {
      // start(string)와 bar_count(number)는 비교 타입이 달라 공용 변수로 뽑지 않고
      // 분기마다 같은 타입끼리 비교한다(TypeScript가 string|number 유니언의 </>를
      // 거부함).
      if (sortKey === 'start') {
        return a.start < b.start ? -factor : a.start > b.start ? factor : 0;
      }
      return (a.bar_count - b.bar_count) * factor;
    });
  }, [segments, sortKey, sortDir]);

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

  if (segments.length === 0) {
    return <p className="text-muted-foreground">최소 지속봉수를 넘는 구간이 없습니다.</p>;
  }

  return (
    <div className="max-h-96 overflow-auto rounded-md border [&>[data-slot=table-container]]:overflow-visible">
      <Table>
        <TableHeader className="sticky top-0 z-10 bg-background">
          <TableRow>
            <TableHead>
              <button
                type="button"
                className="flex items-center gap-1 hover:text-foreground"
                onClick={() => toggleSort('start')}
              >
                기간 <SortIcon sortKeyOf="start" />
              </button>
            </TableHead>
            <TableHead className="text-right">
              <button
                type="button"
                className="flex w-full items-center justify-end gap-1 hover:text-foreground"
                onClick={() => toggleSort('bar_count')}
              >
                지속 <SortIcon sortKeyOf="bar_count" />
              </button>
            </TableHead>
            <TableHead>라벨</TableHead>
            <TableHead />
          </TableRow>
        </TableHeader>
        <TableBody>
          {sorted.map((seg) => (
            <TableRow key={`${seg.start}-${seg.end}`}>
              <TableCell className="whitespace-nowrap">
                {formatDateTimeShort(seg.start)} ~ {formatDateTimeShort(seg.end)}
              </TableCell>
              <TableCell className="text-right tabular-nums">{seg.bar_count}봉</TableCell>
              <TableCell className={LABEL_TEXT_CLASS[seg.label]}>{seg.label}</TableCell>
              <TableCell>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  nativeButton={false}
                  role="link"
                  aria-label="그리드서치로 복사"
                  title="그리드서치로 복사"
                  render={<Link href={buildGridSearchHref(market, timeframe, seg)} />}
                >
                  <Copy className="size-3.5" />
                </Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
```

**`frontend/components/RegimeFactSegmentView.tsx`** (신규, `TrendSegmentView.tsx`와
같은 컨테이너 — 데이터 로드 후 차트+표 조립. `market`/`timeframe`을 부모
(`RegimeDashboard.tsx`)에서 props로 받는다):

```typescript
'use client';

import { useEffect, useState } from 'react';
import { ApiError } from '@/lib/api/client';
import { getRegimeFactSegments } from '@/lib/api/eda';
import RegimeFactChart from '@/components/RegimeFactChart';
import RegimeFactSegmentTable from '@/components/RegimeFactSegmentTable';
import type { RegimeFactAnalysis } from '@/lib/types/eda';

export default function RegimeFactSegmentView({ market, timeframe }: { market: string; timeframe: string }) {
  const [data, setData] = useState<RegimeFactAnalysis | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!market) return;
    let ignore = false;
    setLoading(true);
    setError(null);
    setData(null);
    getRegimeFactSegments({ market, timeframe })
      .then((d) => {
        if (!ignore) setData(d);
      })
      .catch((err) => {
        if (!ignore) setError(err instanceof ApiError ? err.message : 'fact 장세 구간을 불러오지 못했습니다.');
      })
      .finally(() => {
        if (!ignore) setLoading(false);
      });
    return () => {
      ignore = true;
    };
  }, [market, timeframe]);

  return (
    <div className="rounded-xl border p-6 shadow-sm">
      <h2 className="mb-4 text-sm font-semibold">fact 장세 구간 (하락/하락아님)</h2>
      {loading ? (
        <p className="text-sm text-muted-foreground">불러오는 중...</p>
      ) : error ? (
        <p className="text-sm text-muted-foreground">{error}</p>
      ) : data ? (
        <div className="space-y-4">
          <RegimeFactChart bars={data.bars} />
          <RegimeFactSegmentTable segments={data.segments} market={data.market} timeframe={data.timeframe} />
        </div>
      ) : null}
    </div>
  );
}
```

**`RegimeDashboard.tsx` 조립**: `RegimeMlCurrentPrediction` 아래에
`{market && <RegimeFactSegmentView market={market} timeframe={TIMEFRAME} />}` 추가.

## 테스트 전략

- 백엔드: `tests/`에 `compute_fact_regime_segments()` 단위 테스트 추가 — 합성
  OHLCV(가격을 직접 조작해 하락/하락아님이 뻔히 갈리게 구성)로 (1) `bars`의 라벨이
  `compute_triple_barrier_labels`와 일치하는지, (2) 최소 지속봉수 미만 구간이
  `segments`에서 빠지는지, (3) 라벨이 바뀌는 경계에서 구간이 정확히 나뉘는지 검증
- 프론트엔드: 기존 `TrendSegmentChart`/`TrendSegmentTable`도 별도 컴포넌트
  테스트가 없는 프로젝트 관례를 따라 자동화 테스트를 추가하지 않는다 — 구현 후
  브라우저(Playwright MCP)로 `/regime` 탭에서 코인 선택 → 차트 색칠 → 표 정렬 →
  "그리드서치로 복사" 클릭 시 `/grid-search`가 올바른 쿼리스트링으로 열리는지
  수동 검증

## 완료 기준

- `/regime` 탭에서 코인을 고르면 fact 장세 구간 차트+표가 뜬다
- 표의 "그리드서치로 복사" 버튼이 해당 구간의 market/timeframe/start/end로
  `/grid-search` 폼을 정확히 프리필한다
- 신규 백엔드 유닛 테스트 통과, 기존 테스트 스위트 회귀 없음
- 브라우저로 위 흐름 수동 검증
