# 코인별 fact 장세 구간 뷰어 + 그리드서치 연동 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/regime` 탭에서 코인별로 fact 라벨(하락/하락아님) 구간을 캔들 색칠 차트 +
표로 보여주고, 표의 각 구간을 그리드서치 폼으로 바로 복사하는 버튼을 추가한다.

**Architecture:** 백엔드는 새 `backend/regime_fact_service.py`가
`compute_triple_barrier_labels()`(기존 함수, Triple Barrier·ML 아님)로 봉별 라벨을
계산해 연속 구간으로 묶고, 새 엔드포인트 `GET /api/v1/regime/fact-segments`가 이를
노출한다. 프론트는 `/analysis` 탭의 `TrendSegmentChart`/`TrendSegmentTable`과 같은
패턴(색칠 캔들차트 + 정렬 가능 표 + "그리드서치로 복사" 버튼)을 그대로 재사용하는
신규 컴포넌트 3개를 만들어 `RegimeDashboard.tsx`에 조립한다.

**Tech Stack:** FastAPI, pandas (백엔드) / Next.js, TypeScript, lightweight-charts
(프론트). 기존 `compute_triple_barrier_labels`, `half_life_bars_for_timeframe`,
`N_MULTIPLIER`, `get_candles`, `TrendSegmentChart`/`TrendSegmentTable`의 UI 패턴,
`RegimeMlCurrentPrediction`의 `categoryVarName`을 재사용.

## Global Constraints

- 대상 코인: `TRAINED_MARKETS`(`RegimeDashboard.tsx`가 이미 쓰는 20개, ML 학습 마켓과
  동일) — 다른 마켓 미지원
- 타임프레임: `minutes60` 고정 — 다른 타임프레임 미지원
- 캐싱/DB 저장 없음 — 요청마다 즉시 계산
- `BARRIER_K = 6.25`(`scripts/train_regime_ml.py:BARRIER_K`와 동일 값, 학습 모듈은
  import하지 않고 값만 복제)
- `MIN_SEGMENT_BARS = 24`(1일=24봉, `minutes60` 기준) — 표에 나열할 최소 지속봉수.
  차트의 봉별 색칠은 이 필터와 무관하게 항상 전체 표시
- 그리드서치 복사는 URL 프리필까지만(`/grid-search?market=&timeframe=&start=&end=`)
  — 자동 실행 없음
- 새 프론트엔드 컴포넌트에는 자동화 테스트를 추가하지 않는다(이 프로젝트의
  `TrendSegmentChart`/`TrendSegmentTable`도 컴포넌트 테스트가 없는 기존 관례) —
  대신 구현 후 브라우저로 수동 검증

---

### Task 1: 백엔드 — fact 장세 구간 서비스 + 엔드포인트

**Files:**
- Create: `backend/regime_fact_service.py`
- Create: `tests/test_regime_fact_service.py`
- Modify: `backend/main.py` (import 추가 + 엔드포인트 추가)
- Modify: `tests/test_backend.py` (엔드포인트 테스트 1개 추가)

**Interfaces:**
- Consumes:
  - `engine.regime_math.half_life_bars_for_timeframe(timeframe: str) -> float`,
    `engine.regime_math.N_MULTIPLIER: float`
  - `engine.regime_ml_labels.compute_triple_barrier_labels(df, half_life_bars, n_bars, k) -> pd.Series`
  - `upbit_data_service.get_candles(market, timeframe, start, end) -> pd.DataFrame`
    (컬럼: `candle_time`(tz-aware UTC), `open`, `high`, `low`, `close`, ...)
- Produces:
  - `backend.regime_fact_service.compute_fact_regime_segments(market: str, timeframe: str) -> dict`
    — `{"market": str, "timeframe": str, "bars": list[dict], "segments": list[dict]}`.
    `bars`의 각 원소: `{"time": str(ISO), "open": float, "high": float, "low": float,
    "close": float, "label": "하락" | "하락아님" | None}`. `segments`의 각 원소:
    `{"start": str(ISO), "end": str(ISO), "label": "하락" | "하락아님", "bar_count": int}`
  - `backend.regime_fact_service._same_label(a: object, b: object) -> bool`
  - `backend.regime_fact_service.MIN_SEGMENT_BARS: int`(모듈 상수, 테스트에서
    monkeypatch로 조절)
  - `GET /api/v1/regime/fact-segments?market=&timeframe=` → 위 dict를 그대로 JSON
    응답

- [ ] **Step 1: 서비스 유닛 테스트 작성(실패 상태)**

`tests/test_regime_fact_service.py` 생성:

```python
"""
tests/test_regime_fact_service.py

backend.regime_fact_service.compute_fact_regime_segments()를 검증한다. 라벨링
수학 자체(compute_triple_barrier_labels)는 tests/test_regime_ml_labels.py가 이미
검증하므로, 여기서는 compute_triple_barrier_labels를 monkeypatch로 고정해 이
함수가 새로 하는 일(봉별 bars 배열 조립, 연속 구간 묶기, 최소 지속봉수 필터링)만
검증한다.
"""
from __future__ import annotations

import pandas as pd

import backend.regime_fact_service as regime_fact_service
from backend.regime_fact_service import _same_label, compute_fact_regime_segments


def _make_df(n: int) -> pd.DataFrame:
    times = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame({
        "candle_time": times,
        "open": [100.0 + i for i in range(n)],
        "high": [101.0 + i for i in range(n)],
        "low": [99.0 + i for i in range(n)],
        "close": [100.5 + i for i in range(n)],
    })


def test_same_label_treats_both_nan_as_equal():
    assert _same_label(float("nan"), float("nan")) is True


def test_same_label_treats_label_and_nan_as_different():
    assert _same_label("하락", float("nan")) is False
    assert _same_label(float("nan"), "하락") is False


def test_same_label_compares_equal_and_different_strings():
    assert _same_label("하락", "하락") is True
    assert _same_label("하락", "하락아님") is False


def test_compute_fact_regime_segments_bars_carry_per_bar_label(monkeypatch):
    df = _make_df(4)
    labels = pd.Series(["하락", "하락", "하락아님", float("nan")], index=df.index)
    monkeypatch.setattr(regime_fact_service, "get_candles", lambda *a, **k: df)
    monkeypatch.setattr(regime_fact_service, "compute_triple_barrier_labels", lambda *a, **k: labels)

    result = compute_fact_regime_segments("KRW-BTC", "minutes60")

    assert [b["label"] for b in result["bars"]] == ["하락", "하락", "하락아님", None]
    assert result["market"] == "KRW-BTC"
    assert result["timeframe"] == "minutes60"
    assert len(result["bars"]) == 4
    assert result["bars"][0]["open"] == 100.0
    assert result["bars"][0]["time"] == df["candle_time"].iloc[0].isoformat()


def test_compute_fact_regime_segments_merges_consecutive_same_label_into_one_segment(monkeypatch):
    df = _make_df(6)
    labels = pd.Series(["하락", "하락", "하락", "하락아님", "하락아님", "하락아님"], index=df.index)
    monkeypatch.setattr(regime_fact_service, "get_candles", lambda *a, **k: df)
    monkeypatch.setattr(regime_fact_service, "compute_triple_barrier_labels", lambda *a, **k: labels)
    monkeypatch.setattr(regime_fact_service, "MIN_SEGMENT_BARS", 2)

    result = compute_fact_regime_segments("KRW-BTC", "minutes60")

    assert len(result["segments"]) == 2
    first, second = result["segments"]
    assert first["label"] == "하락"
    assert first["bar_count"] == 3
    assert first["start"] == df["candle_time"].iloc[0].isoformat()
    assert first["end"] == df["candle_time"].iloc[2].isoformat()
    assert second["label"] == "하락아님"
    assert second["bar_count"] == 3
    assert second["start"] == df["candle_time"].iloc[3].isoformat()
    assert second["end"] == df["candle_time"].iloc[5].isoformat()


def test_compute_fact_regime_segments_excludes_runs_shorter_than_min_bars(monkeypatch):
    df = _make_df(5)
    labels = pd.Series(["하락", "하락아님", "하락아님", "하락아님", "하락아님"], index=df.index)
    monkeypatch.setattr(regime_fact_service, "get_candles", lambda *a, **k: df)
    monkeypatch.setattr(regime_fact_service, "compute_triple_barrier_labels", lambda *a, **k: labels)
    monkeypatch.setattr(regime_fact_service, "MIN_SEGMENT_BARS", 2)

    result = compute_fact_regime_segments("KRW-BTC", "minutes60")

    assert len(result["segments"]) == 1
    assert result["segments"][0]["label"] == "하락아님"
    assert result["segments"][0]["bar_count"] == 4


def test_compute_fact_regime_segments_excludes_nan_runs_from_segments(monkeypatch):
    df = _make_df(5)
    labels = pd.Series(["하락", "하락", float("nan"), float("nan"), float("nan")], index=df.index)
    monkeypatch.setattr(regime_fact_service, "get_candles", lambda *a, **k: df)
    monkeypatch.setattr(regime_fact_service, "compute_triple_barrier_labels", lambda *a, **k: labels)
    monkeypatch.setattr(regime_fact_service, "MIN_SEGMENT_BARS", 1)

    result = compute_fact_regime_segments("KRW-BTC", "minutes60")

    assert len(result["segments"]) == 1
    assert result["segments"][0]["label"] == "하락"
    assert result["segments"][0]["bar_count"] == 2
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd C:\Users\jungm\personal\study\260711-upbit-v1 && PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_regime_fact_service.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'backend.regime_fact_service'`
(파일이 아직 없으므로).

- [ ] **Step 3: `backend/regime_fact_service.py` 구현**

```python
"""
backend/regime_fact_service.py

engine/regime_ml_labels.py의 compute_triple_barrier_labels()(Triple Barrier, ML
아님)로 코인별 과거 "하락/하락아님" fact 구간을 계산한다. /regime 탭의 시각화 +
그리드서치 프리필 전용. 캐싱 없음 — 요청마다 즉시 계산(실측: KRW-BTC minutes60
2024-01-01~현재 23,305봉 기준 0.57초). 설계 문서:
docs/superpowers/specs/2026-08-30-regime-fact-segment-viewer-design.md
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

- [ ] **Step 4: 서비스 테스트 통과 확인**

Run: `cd C:\Users\jungm\personal\study\260711-upbit-v1 && PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_regime_fact_service.py -v`

Expected: PASS — 9개 테스트 전부 통과.

- [ ] **Step 5: 엔드포인트 테스트 작성(실패 상태)**

`tests/test_backend.py`에서 `test_regime_ml_current_prediction_returns_result`
함수 정의 바로 앞에 다음 테스트를 추가한다(같은 `_client(monkeypatch, tmp_path)`
헬퍼와 `backend_module`을 그대로 사용):

```python
def test_regime_fact_segments_returns_result(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    captured = {}

    def _fake_compute(market, timeframe):
        captured["args"] = (market, timeframe)
        return {
            "market": market,
            "timeframe": timeframe,
            "bars": [{"time": "2026-01-01T00:00:00+00:00", "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "label": "하락"}],
            "segments": [{"start": "2026-01-01T00:00:00+00:00", "end": "2026-01-02T00:00:00+00:00", "label": "하락", "bar_count": 24}],
        }

    monkeypatch.setattr(backend_module, "compute_fact_regime_segments", _fake_compute)

    resp = client.get(
        "/api/v1/regime/fact-segments",
        params={"market": "KRW-BTC", "timeframe": "minutes60"},
    )

    assert resp.status_code == 200
    assert resp.json()["segments"][0]["label"] == "하락"
    assert captured["args"] == ("KRW-BTC", "minutes60")
```

- [ ] **Step 6: 엔드포인트 테스트 실패 확인**

Run: `cd C:\Users\jungm\personal\study\260711-upbit-v1 && PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_backend.py -k test_regime_fact_segments_returns_result -v`

Expected: FAIL — 404 Not Found (라우트가 아직 없음) 또는
`AttributeError: module 'backend.main' has no attribute 'compute_fact_regime_segments'`
(monkeypatch 대상이 아직 import되지 않았으므로).

- [ ] **Step 7: `backend/main.py`에 엔드포인트 추가**

`backend/main.py` 상단의 다음 블록(현재 76~80번째 줄):

```python
from backend.regime_ml_service import (
    deploy_model,
    list_trained_models,
    predict_current_ml_regime,
)
```

을 아래처럼 바꿔, 이 블록의 닫는 괄호(`)`) 바로 다음 줄에 새 import를 추가한다:

```python
from backend.regime_ml_service import (
    deploy_model,
    list_trained_models,
    predict_current_ml_regime,
)
from backend.regime_fact_service import compute_fact_regime_segments
```

`backend/main.py`의 `get_regime_ml_current_prediction_endpoint` 함수 바로 앞에
추가:

```python
@app.get("/api/v1/regime/fact-segments")
def get_regime_fact_segments_endpoint(
    market: str = Query(...),
    timeframe: str = Query(...),
) -> dict:
    return compute_fact_regime_segments(market, timeframe)
```

- [ ] **Step 8: 엔드포인트 테스트 통과 확인**

Run: `cd C:\Users\jungm\personal\study\260711-upbit-v1 && PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_backend.py -k test_regime_fact_segments_returns_result -v`

Expected: PASS.

- [ ] **Step 9: 전체 백엔드 테스트 스위트 실행**

Run: `cd C:\Users\jungm\personal\study\260711-upbit-v1 && PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest -q`

Expected: 이번 작업으로 추가한 테스트(10개) 모두 PASS, 기존 테스트 결과 대비
회귀(새로 생긴 실패) 없음.

- [ ] **Step 10: 커밋**

```bash
git add backend/regime_fact_service.py tests/test_regime_fact_service.py backend/main.py tests/test_backend.py
git commit -m "$(cat <<'EOF'
feat: fact 장세 구간 백엔드 서비스 + 엔드포인트 추가

compute_triple_barrier_labels()(기존 Triple Barrier 함수, ML 아님)로 코인별
"하락/하락아님" 구간을 계산해 /api/v1/regime/fact-segments로 노출. /regime
탭의 fact 구간 뷰어 프론트엔드가 다음 태스크에서 이 엔드포인트를 쓴다.
EOF
)"
```

---

### Task 2: 프론트엔드 — fact 구간 차트/표 + 그리드서치 연동 + `/regime` 조립

**Files:**
- Modify: `frontend/lib/types/eda.ts` (타입 추가)
- Modify: `frontend/lib/api/eda.ts` (API 함수 추가)
- Modify: `frontend/components/RegimeMlCurrentPrediction.tsx` (`categoryVarName`
  export)
- Create: `frontend/components/RegimeFactChart.tsx`
- Create: `frontend/components/RegimeFactSegmentTable.tsx`
- Create: `frontend/components/RegimeFactSegmentView.tsx`
- Modify: `frontend/components/RegimeDashboard.tsx` (조립)

**Interfaces:**
- Consumes:
  - Task 1의 `GET /api/v1/regime/fact-segments?market=&timeframe=` (응답 스키마는
    이 태스크의 `RegimeFactAnalysis` 타입과 동일)
  - `RegimeCategory`(`'하락' | '하락아님'`, 이미 `frontend/lib/types/eda.ts`에 존재)
  - `OhlcvPoint`(`{time, open, high, low, close}`, 이미 존재)
  - `apiFetch`/`ApiError`(`@/lib/api/client`, 이미 존재)
  - `formatDateTimeShort(iso: string): string`(`@/lib/format`, 이미 존재)
- Produces:
  - `frontend/lib/types/eda.ts`: `RegimeFactBar`, `RegimeFactSegment`,
    `RegimeFactAnalysis`
  - `frontend/lib/api/eda.ts`: `getRegimeFactSegments(params: {market: string;
    timeframe: string}): Promise<RegimeFactAnalysis>`
  - `frontend/components/RegimeMlCurrentPrediction.tsx`:
    `export function categoryVarName(label: RegimeCategory): string`(기존 함수를
    export만 추가, 로직 변경 없음)
  - `frontend/components/RegimeFactChart.tsx`: `export default function
    RegimeFactChart({ bars }: { bars: RegimeFactBar[] })`
  - `frontend/components/RegimeFactSegmentTable.tsx`: `export default function
    RegimeFactSegmentTable({ segments, market, timeframe }: { segments:
    RegimeFactSegment[]; market: string; timeframe: string })`
  - `frontend/components/RegimeFactSegmentView.tsx`: `export default function
    RegimeFactSegmentView({ market, timeframe }: { market: string; timeframe:
    string })`

- [ ] **Step 1: 타입 추가**

`frontend/lib/types/eda.ts`의 `RegimeCategory` 정의(`export type RegimeCategory =
'하락' | '하락아님';`) 바로 다음 줄에 추가:

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

- [ ] **Step 2: API 함수 추가**

`frontend/lib/api/eda.ts`의 `getRegimeMlCurrentPrediction` 함수 바로 다음에 추가:

```typescript
export function getRegimeFactSegments(params: {
  market: string;
  timeframe: string;
}): Promise<RegimeFactAnalysis> {
  const query = new URLSearchParams(params);
  return apiFetch<RegimeFactAnalysis>(`/api/v1/regime/fact-segments?${query.toString()}`);
}
```

(`RegimeFactAnalysis`를 이 파일 상단의 기존 `import type { ... } from
'@/lib/types/eda';` 목록에 추가한다.)

- [ ] **Step 3: `categoryVarName` export**

`frontend/components/RegimeMlCurrentPrediction.tsx`에서:

```typescript
function categoryVarName(label: RegimeCategory): string {
```

를

```typescript
export function categoryVarName(label: RegimeCategory): string {
```

로 바꾼다. 함수 본문은 변경하지 않는다.

- [ ] **Step 4: `RegimeFactChart.tsx` 작성**

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

- [ ] **Step 5: `RegimeFactSegmentTable.tsx` 작성**

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
      // start(string)와 bar_count(number)는 비교 타입이 달라 분기마다 같은
      // 타입끼리 비교한다.
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

- [ ] **Step 6: `RegimeFactSegmentView.tsx` 작성**

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

- [ ] **Step 7: `RegimeDashboard.tsx`에 조립**

`frontend/components/RegimeDashboard.tsx` 상단 import 목록에 추가:

```typescript
import RegimeFactSegmentView from '@/components/RegimeFactSegmentView';
```

같은 파일의 return 문에서:

```typescript
      {market && (
        <RegimeMlCurrentPrediction
          market={market}
          timeframe={TIMEFRAME}
          rightPanel={<RegimeMlAdminPanel compact />}
        />
      )}
```

바로 다음 줄에 추가:

```typescript
      {market && <RegimeFactSegmentView market={market} timeframe={TIMEFRAME} />}
```

- [ ] **Step 8: 타입체크**

Run: `cd C:\Users\jungm\personal\study\260711-upbit-v1\frontend && npx tsc --noEmit`

Expected: 에러 없음.

- [ ] **Step 9: 브라우저 수동 검증**

프론트/백엔드 개발 서버를 실행한 상태에서(이미 실행 중이 아니면 프로젝트의 기존
개발 서버 기동 방법을 따른다), Playwright MCP 또는 수동 브라우저로:

1. `/regime` 탭 접속, 코인 하나 선택(예: KRW-BTC)
2. "fact 장세 구간 (하락/하락아님)" 카드가 뜨고, 캔들스틱 차트가 하락(빨강 계열)/
   하락아님(회색 계열) 색으로 칠해져 있는지 확인
3. 아래 표에 구간 목록이 뜨고, "기간"/"지속" 컬럼 정렬 버튼이 동작하는지 확인
4. 아무 행의 "그리드서치로 복사" 아이콘(Copy)을 클릭 → `/grid-search` 페이지로
   이동하며 코인/타임프레임/시작일/종료일이 그 행의 값으로 채워져 있는지 확인
5. 다른 코인으로 바꿔 차트/표가 다시 로드되는지 확인

문제가 있으면 이 단계에서 고치고 다시 확인한다. 통과하면 다음 단계로.

- [ ] **Step 10: 커밋**

```bash
git add frontend/lib/types/eda.ts frontend/lib/api/eda.ts frontend/components/RegimeMlCurrentPrediction.tsx frontend/components/RegimeFactChart.tsx frontend/components/RegimeFactSegmentTable.tsx frontend/components/RegimeFactSegmentView.tsx frontend/components/RegimeDashboard.tsx
git commit -m "$(cat <<'EOF'
feat: 코인별 fact 장세 구간 뷰어 + 그리드서치 연동 UI 추가

/regime 탭에 fact 라벨(하락/하락아님) 캔들 색칠 차트 + 구간 표를 추가하고,
표에서 그리드서치 폼으로 구간을 프리필 복사하는 기능. /analysis 탭의 추세
구간 차트/표 패턴을 재사용.
EOF
)"
```

---

## 완료 후

`docs/regime-ml-backlog.md`의 "선행 작업 — 코인별 fact 장세 구간 뷰어" 항목을
완료로 갱신하고, ②(모델 성능 대폭 개선) 착수로 넘어간다(이 플랜의 범위 밖 — 다음
세션에서 진행).
