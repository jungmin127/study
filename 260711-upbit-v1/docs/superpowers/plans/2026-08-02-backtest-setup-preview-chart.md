# 백테스트 설정 화면 코인 미리보기 차트 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 백테스트 설정 화면(`PortSetupForm.tsx`)의 우측 빈 공간에, 선택한 코인의 캔들차트 + SMA(20/60) +
RSI(14)를 즉시 보여주는 미리보기 패널을 추가한다.

**Architecture:** 신규 백엔드 엔드포인트 `GET /api/v1/markets/preview`가 기존
`upbit_data_service.get_candles()`로 캔들을 가져와 pandas로 SMA/RSI를 직접 계산해(backtrader
Cerebro를 거치지 않음) 반환한다. 프론트는 `PriceChart.tsx`의 색상 처리·리사이즈 패턴을 재사용하는
신규 `CoinPreviewChart.tsx`가 이 데이터를 lightweight-charts 멀티페인(상단 캔들+이평선, 하단 RSI)으로
그린다. `PortSetupForm.tsx`는 코인/봉데이터/운용기간 상태를 그대로 이 컴포넌트에 넘긴다.

**Tech Stack:** Python(FastAPI, pandas — `backend/main.py`), pytest, TypeScript(Next.js,
`lightweight-charts` v5 — 자동 테스트 없음, `tsc --noEmit` + 수동 확인).

## Global Constraints

- 스펙 문서: `docs/superpowers/specs/2026-08-02-backtest-setup-preview-chart-design.md`.
- SMA/RSI 계산은 backtrader Cerebro를 거치지 않는 독립 pandas 구현(Wilder 평활 RSI, 단순 rolling
  mean SMA) — 조건식 빌더가 실제 백테스트에서 계산하는 값과 소수점까지 완전히 동일함을 보장하지
  않는다(같은 표준 공식이라 실질적으로는 일치, 이 패널은 참고용).
- 이동평균은 SMA 20/60 고정, RSI는 14 고정 — 이번 라운드는 지표 선택 UI 없음.
- 미리보기 봉데이터 단위는 폼의 "봉데이터 선택"과 항상 연동(별도 고정 안 함). 조회 기간은 폼의
  "운용기간" 전체와 동일(다운샘플링/개수 제한 없음 — 큰 구간+짧은 타임프레임일 때 느려지는 트레이드
  오프를 사용자가 인지하고 승인함).
- 매수/매도 조건, 트레이드 마커는 이 패널에 넣지 않는다(전략 실행 전 시점이라 트레이드 자체가 없음).
- 에러 처리 컨벤션: `get_candles`가 `ValueError`를 내면 400, `RuntimeError`는 500(
  `validate_backtest_endpoint`, `backend/main.py:816`와 동일), 빈 DataFrame(예외 없이 데이터
  없음)도 400.
- SMA/RSI 응답은 warm-up 구간(NaN)을 제외하고 값이 있는 포인트만 보낸다 — JSON은 NaN을 표현할 수
  없고, `lightweight-charts`는 시리즈마다 데이터 길이가 달라도 정상 동작한다.
- 프론트에는 테스트 프레임워크가 없다 — 신규 도입 안 함. `npx tsc --noEmit` + 수동 브라우저 확인으로 검증.

---

### Task 1: 백엔드 — RSI/포인트 변환 헬퍼 함수

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_backend.py`

**Interfaces:**
- Produces: `_compute_rsi(close: pd.Series, period: int = 14) -> pd.Series`,
  `_series_to_points(df: pd.DataFrame, value_col: str) -> list[dict]`(각 dict는
  `{"time": str, "value": float}`). 둘 다 Task 2의 엔드포인트가 그대로 가져다 씀.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_backend.py` 파일 끝에 추가:

```python
def test_compute_rsi_first_period_values_are_nan_then_matches_wilder_formula():
    closes = pd.Series([10.0, 11.0, 12.0, 11.0, 13.0, 14.0, 13.0, 15.0])
    rsi = backend_module._compute_rsi(closes, period=3)

    assert rsi.iloc[:3].isna().all()
    assert rsi.iloc[3] == pytest.approx(66.66666666666669)
    assert rsi.iloc[4] == pytest.approx(83.33333333333334)
    assert rsi.iloc[5] == pytest.approx(87.87878787878788)
    assert rsi.iloc[6] == pytest.approx(62.365591397849464)
    assert rsi.iloc[7] == pytest.approx(79.88505747126436)


def test_series_to_points_excludes_nan_and_formats_time():
    df = pd.DataFrame({
        "candle_time": pd.date_range("2026-01-01", periods=4, freq="h", tz="UTC"),
        "sma20": [float("nan"), float("nan"), 11.0, 11.5],
    })

    points = backend_module._series_to_points(df, "sma20")

    assert len(points) == 2
    assert points[0] == {"time": "2026-01-01T02:00:00+00:00", "value": 11.0}
    assert points[1] == {"time": "2026-01-01T03:00:00+00:00", "value": 11.5}
```

`tests/test_backend.py` 상단에 `import pytest`가 없다면 추가.

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_backend.py -k "compute_rsi or series_to_points" -v`
Expected: FAIL — `AttributeError: module 'backend.main' has no attribute '_compute_rsi'`.

- [ ] **Step 3: `backend/main.py`에 함수 추가**

상단 임포트 블록의 다음 줄:
```python
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
```
을 아래로 교체:
```python
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import pandas as pd
```

`_to_utc_iso` 함수 정의 바로 다음(`app = FastAPI(...)` 줄 바로 앞)에 추가:

```python
def _compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder 평활 RSI. backtrader bt.indicators.RSI 기본값과 동일한 평활 방식(지수 평활,
    alpha=1/period)이나 별도 pandas 구현이라 소수점까지 완전히 동일함을 보장하진 않는다.
    최초 `period`개는 NaN — 평활에 필요한 만큼의 델타가 아직 쌓이지 않았다."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    return 100 - (100 / (1 + rs))


def _series_to_points(df: pd.DataFrame, value_col: str) -> list[dict]:
    """value_col의 NaN(warm-up 구간)을 제외하고 [{"time", "value"}, ...]로 변환한다.
    JSON은 NaN을 표현할 수 없으므로 값이 있는 포인트만 프론트에 보낸다."""
    valid = df[df[value_col].notna()]
    return [
        {"time": _to_utc_iso(row.candle_time.isoformat()), "value": float(getattr(row, value_col))}
        for row in valid.itertuples()
    ]
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_backend.py -v`
Expected: PASS (전체 통과, 신규 2개 포함)

- [ ] **Step 5: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "feat: add RSI/point-series helpers for market preview"
```

---

### Task 2: 백엔드 — `/api/v1/markets/preview` 엔드포인트

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_backend.py`

**Interfaces:**
- Consumes: Task 1의 `_compute_rsi`/`_series_to_points`, 기존 `get_candles`(`upbit_data_service.py`),
  `_to_utc_iso`.
- Produces: `GET /api/v1/markets/preview?market=...&timeframe=...&start=...&end=...` →
  `{"ohlcv": [...], "sma20": [...], "sma60": [...], "rsi14": [...]}`. Task 3(프론트 API 클라이언트)가
  이 응답 스키마를 그대로 타입으로 옮겨 씀.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_backend.py` 파일 끝에 추가:

```python
def test_market_preview_returns_ohlcv_and_indicator_series(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch)  # make_oscillating_df() 기본값: n=300, hourly, 2026-01-01~

    resp = client.get(
        "/api/v1/markets/preview",
        params={"market": "KRW-BTC", "timeframe": "minutes60", "start": "2026-01-01", "end": "2026-01-13"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["ohlcv"]) == 300
    assert len(body["sma20"]) == 281  # 300 - (20-1)
    assert len(body["sma60"]) == 241  # 300 - (60-1)
    assert len(body["rsi14"]) == 286  # 300 - 14
    assert body["ohlcv"][0]["time"] == "2026-01-01T00:00:00+00:00"
    assert set(body["sma20"][0].keys()) == {"time", "value"}


def test_market_preview_rejects_when_no_candles(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch, df=make_oscillating_df(n=0))

    resp = client.get(
        "/api/v1/markets/preview",
        params={"market": "KRW-BTC", "timeframe": "days", "start": "2026-01-01", "end": "2026-01-13"},
    )

    assert resp.status_code == 400


def test_market_preview_maps_value_error_to_400(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    def _raise_value_error(market, timeframe, start, end):
        raise ValueError("잘못된 timeframe입니다")

    monkeypatch.setattr(backend_module, "get_candles", _raise_value_error)

    resp = client.get(
        "/api/v1/markets/preview",
        params={"market": "KRW-BTC", "timeframe": "bogus", "start": "2026-01-01", "end": "2026-01-13"},
    )

    assert resp.status_code == 400


def test_market_preview_maps_runtime_error_to_500(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    def _raise_runtime_error(market, timeframe, start, end):
        raise RuntimeError("업비트 API 재시도 실패")

    monkeypatch.setattr(backend_module, "get_candles", _raise_runtime_error)

    resp = client.get(
        "/api/v1/markets/preview",
        params={"market": "KRW-BTC", "timeframe": "days", "start": "2026-01-01", "end": "2026-01-13"},
    )

    assert resp.status_code == 500
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_backend.py -k market_preview -v`
Expected: FAIL — `404 Not Found`(엔드포인트가 아직 없음).

- [ ] **Step 3: `backend/main.py`에 엔드포인트 추가**

`get_markets()` 함수(`@app.get("/api/v1/markets")`) 바로 다음에 추가:

```python
@app.get("/api/v1/markets/preview")
def get_market_preview(
    market: str = Query(...),
    timeframe: str = Query(...),
    start: str = Query(...),
    end: str = Query(...),
) -> dict:
    start_dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(end, "%Y-%m-%d").replace(
        hour=23, minute=59, second=59, tzinfo=timezone.utc
    )

    try:
        df = get_candles(market, timeframe, start_dt, end_dt)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if df.empty:
        raise HTTPException(status_code=400, detail=f"{market}의 해당 기간 캔들 데이터가 없습니다")

    df = df.sort_values("candle_time").reset_index(drop=True)
    df["sma20"] = df["close"].rolling(window=20).mean()
    df["sma60"] = df["close"].rolling(window=60).mean()
    df["rsi14"] = _compute_rsi(df["close"], period=14)

    ohlcv = [
        {
            "time": _to_utc_iso(row.candle_time.isoformat()),
            "open": float(row.open),
            "high": float(row.high),
            "low": float(row.low),
            "close": float(row.close),
        }
        for row in df.itertuples()
    ]

    return {
        "ohlcv": ohlcv,
        "sma20": _series_to_points(df, "sma20"),
        "sma60": _series_to_points(df, "sma60"),
        "rsi14": _series_to_points(df, "rsi14"),
    }
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_backend.py -v`
Expected: PASS (전체 통과, 신규 4개 포함)

- [ ] **Step 5: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "feat: add /api/v1/markets/preview endpoint for coin preview chart"
```

---

### Task 3: 프론트 — 타입 및 API 클라이언트

**Files:**
- Modify: `frontend/lib/types/eda.ts`
- Modify: `frontend/lib/api/eda.ts`

**Interfaces:**
- Consumes: Task 2의 응답 스키마(`ohlcv`/`sma20`/`sma60`/`rsi14`).
- Produces: `MarketPreview`, `IndicatorSeriesPoint` 타입, `getMarketPreview(params): Promise<MarketPreview>`.
  Task 4(`CoinPreviewChart.tsx`)가 그대로 가져다 씀.

- [ ] **Step 1: `frontend/lib/types/eda.ts`에 타입 추가**

`OhlcvPoint` 인터페이스(`export interface OhlcvPoint { ... }`) 바로 다음에 추가:

```ts
export interface IndicatorSeriesPoint {
  time: string;
  value: number;
}

export interface MarketPreview {
  ohlcv: OhlcvPoint[];
  sma20: IndicatorSeriesPoint[];
  sma60: IndicatorSeriesPoint[];
  rsi14: IndicatorSeriesPoint[];
}
```

- [ ] **Step 2: `frontend/lib/api/eda.ts`에 함수 추가**

상단 타입 임포트의 다음 줄:
```ts
import type {
  BacktestDetail,
  BacktestRunSummary,
  Combo,
  IndicatorCatalogItem,
  Market,
  RunBacktestRequest,
  RunBacktestResponse,
  SegmentSizeEntry,
  SweepResult,
  ValidateBacktestResponse,
} from '@/lib/types/eda';
```
을 아래로 교체:
```ts
import type {
  BacktestDetail,
  BacktestRunSummary,
  Combo,
  IndicatorCatalogItem,
  Market,
  MarketPreview,
  RunBacktestRequest,
  RunBacktestResponse,
  SegmentSizeEntry,
  SweepResult,
  ValidateBacktestResponse,
} from '@/lib/types/eda';
```

`getMarkets` 함수 바로 다음에 추가:

```ts
export function getMarketPreview(params: {
  market: string;
  timeframe: string;
  start: string;
  end: string;
}): Promise<MarketPreview> {
  const qs = new URLSearchParams(params).toString();
  return apiFetch<MarketPreview>(`/api/v1/markets/preview?${qs}`);
}
```

- [ ] **Step 3: 타입체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 4: 커밋**

```bash
git add frontend/lib/types/eda.ts frontend/lib/api/eda.ts
git commit -m "feat: add market preview type and API client function"
```

---

### Task 4: 프론트 — `CoinPreviewChart.tsx` 컴포넌트

**Files:**
- Create: `frontend/components/CoinPreviewChart.tsx`

**Interfaces:**
- Consumes: Task 3의 `getMarketPreview`, `MarketPreview` 타입. `frontend/components/PriceChart.tsx`의
  색상 해석(`resolveColor`)·리사이즈 패턴을 복사해 재사용(공통 유틸로 추출하지 않음 — 두 컴포넌트가
  독립적으로 진화할 수 있게).
- Produces: `<CoinPreviewChart market timeframe start end />`. Task 5(`PortSetupForm.tsx`)가 그대로
  가져다 씀.

- [ ] **Step 1: 파일 생성**

`frontend/components/CoinPreviewChart.tsx`:

```tsx
'use client';

import { useEffect, useRef, useState } from 'react';
import {
  createChart,
  CandlestickSeries,
  LineSeries,
  LineStyle,
  ColorType,
  CrosshairMode,
  type UTCTimestamp,
} from 'lightweight-charts';
import { getMarketPreview } from '@/lib/api/eda';
import { ApiError } from '@/lib/api/client';
import type { MarketPreview } from '@/lib/types/eda';

interface CoinPreviewChartProps {
  market: string;
  timeframe: string;
  start: string;
  end: string;
}

type DayString = `${number}-${number}-${number}`;

function isIntraday(timeframe: string): boolean {
  return timeframe.startsWith('minutes');
}

function toUnix(iso: string): UTCTimestamp {
  return Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp;
}

// PriceChart.tsx와 동일한 이유로 캔버스를 거쳐 색상을 해석한다: getComputedStyle이 반환하는
// oklch() 문자열을 lightweight-charts의 ColorParser가 직접 파싱하지 못하기 때문이다.
function resolveColor(varName: string): string {
  const raw = getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d');
  if (!ctx) return raw;
  ctx.fillStyle = raw;
  ctx.fillRect(0, 0, 1, 1);
  const pixel = ctx.getImageData(0, 0, 1, 1).data;
  return `rgba(${pixel[0]}, ${pixel[1]}, ${pixel[2]}, ${(pixel[3] / 255).toFixed(3)})`;
}

export default function CoinPreviewChart({ market, timeframe, start, end }: CoinPreviewChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [preview, setPreview] = useState<MarketPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!market) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    getMarketPreview({ market, timeframe, start, end })
      .then((data) => {
        if (!cancelled) setPreview(data);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : '미리보기를 불러오지 못했습니다.');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [market, timeframe, start, end]);

  useEffect(() => {
    if (!containerRef.current || !preview || preview.ohlcv.length === 0) return;
    const intradayMode = isIntraday(timeframe);
    const toTime = (iso: string): UTCTimestamp | DayString =>
      intradayMode ? toUnix(iso) : (iso.split('T')[0] as DayString);

    const priceUp = resolveColor('--price-up');
    const priceDown = resolveColor('--price-down');
    const sma20Color = resolveColor('--chart-1');
    const sma60Color = resolveColor('--chart-2');
    const rsiColor = resolveColor('--chart-3');
    const background = resolveColor('--background');
    const foreground = resolveColor('--foreground');
    const border = resolveColor('--border');

    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: 480,
      layout: { background: { type: ColorType.Solid, color: background }, textColor: foreground },
      crosshair: { mode: CrosshairMode.Normal },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: border },
      rightPriceScale: { borderColor: border },
    });

    const candleSeries = chart.addSeries(
      CandlestickSeries,
      {
        upColor: priceUp, downColor: priceDown, borderVisible: false,
        wickUpColor: priceUp, wickDownColor: priceDown,
      },
      0,
    );
    candleSeries.setData(
      preview.ohlcv
        .map((bar) => ({
          time: toTime(bar.time), open: bar.open, high: bar.high, low: bar.low, close: bar.close,
        }))
        .sort((a, b) => String(a.time).localeCompare(String(b.time))),
    );

    const sma20Series = chart.addSeries(LineSeries, { color: sma20Color, lineWidth: 1 }, 0);
    sma20Series.setData(preview.sma20.map((p) => ({ time: toTime(p.time), value: p.value })));

    const sma60Series = chart.addSeries(LineSeries, { color: sma60Color, lineWidth: 1 }, 0);
    sma60Series.setData(preview.sma60.map((p) => ({ time: toTime(p.time), value: p.value })));

    const rsiSeries = chart.addSeries(LineSeries, { color: rsiColor, lineWidth: 1 }, 1);
    rsiSeries.setData(preview.rsi14.map((p) => ({ time: toTime(p.time), value: p.value })));
    rsiSeries.createPriceLine({
      price: 70, color: border, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: '70',
    });
    rsiSeries.createPriceLine({
      price: 30, color: border, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: '30',
    });

    chart.timeScale().fitContent();

    const resizeObserver = new ResizeObserver(() => {
      if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth });
    });
    resizeObserver.observe(containerRef.current);

    return () => {
      resizeObserver.disconnect();
      chart.remove();
    };
  }, [preview, timeframe]);

  return (
    <div className="w-full">
      <h2 className="mb-2 text-sm font-semibold">시세 미리보기 (SMA 20/60, RSI 14)</h2>
      {loading && <p className="mb-2 text-xs text-muted-foreground">불러오는 중...</p>}
      {error && <p className="mb-2 text-xs text-destructive">{error}</p>}
      <div ref={containerRef} className="w-full rounded-lg overflow-hidden border" />
    </div>
  );
}
```

- [ ] **Step 2: 타입체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 3: 커밋**

```bash
git add frontend/components/CoinPreviewChart.tsx
git commit -m "feat: add CoinPreviewChart component"
```

---

### Task 5: 프론트 — `PortSetupForm.tsx` 레이아웃 연동

**Files:**
- Modify: `frontend/components/PortSetupForm.tsx`

**Interfaces:**
- Consumes: Task 4의 `CoinPreviewChart`. `market`/`timeframe`/`startDate`/`endDate`는 이미
  `PortSetupForm`의 기존 state.

- [ ] **Step 1: import 추가**

현재:
```tsx
import CoinSelect, { sortMarkets } from '@/components/CoinSelect';
import StrategyConditionBuilder from '@/components/StrategyConditionBuilder';
```
다음으로 교체:
```tsx
import CoinSelect, { sortMarkets } from '@/components/CoinSelect';
import CoinPreviewChart from '@/components/CoinPreviewChart';
import StrategyConditionBuilder from '@/components/StrategyConditionBuilder';
```

- [ ] **Step 2: 레이아웃을 좌(설정 카드)/우(미리보기) 2컬럼으로 변경**

현재(`return` 블록의 최상위 래퍼와 그 닫는 태그):
```tsx
  return (
    <div className="max-w-5xl space-y-6 rounded-xl border p-6 shadow-sm">
      <div className="grid grid-cols-1 gap-6 sm:grid-cols-[1fr_1fr_4fr]">
```
다음으로 교체:
```tsx
  return (
    <div className="flex flex-col gap-6 lg:flex-row">
      <div className="max-w-5xl flex-1 space-y-6 rounded-xl border p-6 shadow-sm">
      <div className="grid grid-cols-1 gap-6 sm:grid-cols-[1fr_1fr_4fr]">
```

파일 끝부분의 현재:
```tsx
      </AlertDialog>
    </div>
  );
}
```
다음으로 교체:
```tsx
      </AlertDialog>
      </div>

      <div className="flex-1">
        <CoinPreviewChart market={market} timeframe={timeframe} start={startDate} end={endDate} />
      </div>
    </div>
  );
}
```

(들여쓰기가 한 단계 얕아진 채 남아있는 중간 JSX는 그대로 둬도 동작에 지장 없음 — 다만 가독성을 위해
`git diff` 확인 후 prettier나 에디터 자동 정렬로 들여쓰기를 맞춰도 된다.)

- [ ] **Step 3: 타입체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 4: 커밋**

```bash
git add frontend/components/PortSetupForm.tsx
git commit -m "feat: show coin preview chart next to backtest setup form"
```

---

### Task 6: 통합 검증

**Files:** 없음 (검증 전용 태스크)

- [ ] **Step 1: 전체 테스트 스위트 확인**

Run: `pytest -q`
Expected: 전부 통과.

- [ ] **Step 2: 프론트 타입체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음.

- [ ] **Step 3: 실제 화면 확인**

백엔드(`uvicorn backend.main:app --reload --port 8000`)와 프론트(`npm run dev`)가 떠 있는 상태에서
"백테스트 설정" 화면을 열고 확인할 것:
- 코인 선택 시 우측 패널에 캔들차트 + SMA 20/60 선 + 하단 RSI 패널(30/70 기준선 포함)이 뜨는지.
- 다른 코인으로 바꾸면 패널이 재조회되어 바뀌는지.
- 봉데이터(15분/30분/1시간/1일)를 바꾸면 미리보기도 같은 단위로 바뀌는지.
- 운용기간을 바꾸면 미리보기 조회 구간도 같이 바뀌는지.
- 다크/라이트 테마 전환 시 차트 색상이 깨지지 않는지(`PriceChart.tsx`와 같은 방식으로 처리했는지 확인).
- 존재하지 않거나 데이터가 없는 코인/기간 조합에서 에러 메시지가 패널에 뜨는지(전체 화면이 깨지지
  않는지).

- [ ] **Step 4: 결과 보고**

위 단계가 모두 통과하면 "코인 미리보기 차트 구현 및 검증 완료"로 사용자에게 보고한다. 실패하는
항목이 있으면 어느 Task로 돌아가 고쳐야 하는지 명시한다.
