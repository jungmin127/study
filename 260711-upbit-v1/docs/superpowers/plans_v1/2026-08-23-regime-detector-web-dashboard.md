# 장세 판별기 웹 대시보드 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `engine/regime_detector.py`의 예측 결과를 코인/봉타입/기간별로 조회할 수 있는 웹
엔드포인트(`GET /api/v1/regime/backtest`)와, 캔들스틱 차트(예측 카테고리 색상) + 정확도
리포트(hit-rate/confusion matrix/상관계수)를 보여주는 새 프론트엔드 탭(`/regime`, "장세
판별")을 추가한다.

**Architecture:** `scripts/regime_backtest.py`에 있던 평가 로직(`_evaluate_market`)을
`backend/regime_service.py`의 `evaluate_market()`로 옮겨 CLI와 API가 동일한 함수를
공유하게 한다(스케일 버그 재발 방지). API는 이 함수를 그대로 호출해 봉별 예측
카테고리(`candles`)와 집계 리포트(`confusion`/`actual_totals`/`correlation`)를 JSON으로
반환한다. 프론트는 그리드서치 폼(`GridSearchForm.tsx`)과 트렌드세그먼트 차트
(`TrendSegmentChart.tsx`)/가격차트(`PriceChart.tsx`) 패턴을 재사용해 마켓/봉타입/기간
선택 폼, 예측 카테고리로 캔들을 칠하는 차트, 정확도 표를 만든다.

**Tech Stack:** Python(FastAPI, pandas, numpy), Next.js/React(TypeScript),
lightweight-charts, pytest.

## Global Constraints

- 설계 문서: `docs/superpowers/specs_v1/2026-08-23-regime-detector-web-dashboard-design.md`.
  이 스펙과 충돌하는 구현은 하지 않는다.
- 이번 플랜은 `engine/regime_detector.py`(판별 로직 자체)를 수정하지 않는다. 순수하게
  기존 판별기 결과를 조회/시각화하는 서비스+API+프론트 레이어만 추가한다.
- 카테고리는 정확히 5개, 이 순서로 다룬다:
  `["급상승", "완만상승", "횡보", "완만하락", "급하락"]`
  (`engine.regime_detector.CATEGORY_REFERENCE_SCORES`의 실제 순서는
  `{"급하락": -0.35, "완만하락": -0.15, "횡보": 0.0, "완만상승": 0.15, "급상승": 0.35}`이지만,
  화면 표시 순서는 급상승→급하락으로 통일한다).
- 계산은 매 요청마다 즉시 수행한다(캐시/DB 저장 없음).
- 새 백엔드 모듈은 파일 최상단에 `from __future__ import annotations`를 둔다(이
  코드베이스의 기존 관례).
- 날짜 파라미터 파싱은 기존 `/api/v1/backtests/run`과 동일한 패턴을 따른다:
  `datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)`, end는
  `hour=23, minute=59, second=59`까지 포함.
- 프론트 기본 조회기간은 시작일 `defaultDate(365)`, 종료일 `defaultDate(0)`.
- 백엔드 테스트는 `pytest tests/<file>.py -v`로 실행한다(저장소 루트 기준).
- 프론트 타입 검증은 `frontend` 디렉터리에서 `npm run build`로 확인한다.
- 프론트/백엔드 개발 서버(수동 브라우저 검증용):
  - 백엔드: 저장소 루트에서 `python -m uvicorn backend.main:app --reload --port 8000`
  - 프론트: `frontend` 디렉터리에서 `npm run dev` (기본 포트 3000,
    `NEXT_PUBLIC_API_URL` 미설정 시 `http://localhost:8000`을 자동으로 바라봄)

---

## Task 1: `backend/regime_service.py` — 평가 로직 이전 + candles 필드 추가

**Files:**
- Create: `backend/regime_service.py`
- Modify: `scripts/regime_backtest.py`
- Test: `tests/test_regime_service.py`

**Interfaces:**
- Consumes: `engine.regime_detector.{CATEGORY_REFERENCE_SCORES, classify_score_to_category,
  compute_regime_probs_series, ewm_volatility, half_life_bars_for_timeframe}`,
  `upbit_data_service.get_candles(market, timeframe, start, end) -> pd.DataFrame`(컬럼:
  `candle_time, open, high, low, close, volume, trade_value`)
- Produces: `evaluate_market(market: str, timeframe: str, start: datetime, end: datetime) ->
  dict`(키: `half_life_bars: float, n_bars: int, candles: list[dict], confusion: dict[str,
  dict[str, int]], actual_totals: dict[str, int], correlation: float | None`),
  `N_MULTIPLIER: float`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime_service.py` 신규 생성:

```python
"""
tests/test_regime_service.py

backend.regime_service.evaluate_market()의 반환 스키마와 기본 동작을 검증한다.
get_candles는 네트워크 호출을 하므로 monkeypatch로 합성 데이터로 대체한다.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

import backend.regime_service as regime_service
from backend.regime_service import evaluate_market
from engine.regime_detector import CATEGORY_REFERENCE_SCORES

_CANDLE_COLUMNS = ["candle_time", "open", "high", "low", "close", "volume", "trade_value"]


def _make_candle_df(closes: list[float]) -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=len(closes), freq="D", tz="UTC")
    return pd.DataFrame({
        "candle_time": dates,
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": [1.0] * len(closes), "trade_value": [1.0] * len(closes),
    })


def test_evaluate_market_returns_empty_result_when_no_candles(monkeypatch):
    monkeypatch.setattr(regime_service, "get_candles", lambda *a, **k: pd.DataFrame(columns=_CANDLE_COLUMNS))

    result = evaluate_market(
        "KRW-BTC", "days",
        datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 2, tzinfo=timezone.utc),
    )

    assert result["candles"] == []
    assert result["correlation"] is None
    assert sum(sum(row.values()) for row in result["confusion"].values()) == 0
    assert sum(result["actual_totals"].values()) == 0


def test_evaluate_market_returns_one_candle_entry_per_input_row(monkeypatch):
    closes = [100.0 * (1.01**i) for i in range(40)]
    monkeypatch.setattr(regime_service, "get_candles", lambda *a, **k: _make_candle_df(closes))

    result = evaluate_market(
        "KRW-BTC", "days",
        datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 2, 10, tzinfo=timezone.utc),
    )

    assert len(result["candles"]) == 40
    assert result["candles"][0]["predicted_category"] is None  # 워밍업 미달(half_life_bars=1.0*5=5봉)
    assert result["candles"][-1]["predicted_category"] in CATEGORY_REFERENCE_SCORES
    assert result["candles"][-1]["close"] == pytest.approx(closes[-1])
    assert result["candles"][0]["time"].endswith("+00:00")


def test_evaluate_market_uptrend_confusion_favors_up_categories(monkeypatch):
    closes = [100.0 * (1.01**i) for i in range(40)]
    monkeypatch.setattr(regime_service, "get_candles", lambda *a, **k: _make_candle_df(closes))

    result = evaluate_market(
        "KRW-BTC", "days",
        datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 2, 10, tzinfo=timezone.utc),
    )

    up_labels = ("완만상승", "급상승")
    total_up_predictions = sum(sum(result["confusion"][label].values()) for label in up_labels)
    total_predictions = sum(sum(row.values()) for row in result["confusion"].values())
    assert total_predictions > 0
    assert total_up_predictions == total_predictions  # 순수 상승추세이므로 하락계열 예측은 0건이어야 함
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_regime_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.regime_service'`

- [ ] **Step 3: 최소 구현 작성**

`backend/regime_service.py` 신규 생성:

```python
"""
backend/regime_service.py

engine.regime_detector.compute_regime_probs_series()의 결과를 마켓 단위로 평가한다.
scripts/regime_backtest.py(CLI)와 GET /api/v1/regime/backtest(웹 API)가 이 함수를
공유한다 — 계산 로직이 두 곳으로 갈라지면 스케일 버그가 재발할 위험이 있다(과거 실제로
2번 발생: 판별스코어-실현수익률 스케일 불일치, 부호없는 확신도로 상관계수 계산). 설계
문서: docs/superpowers/specs_v1/2026-08-23-regime-detector-web-dashboard-design.md
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np

from engine.regime_detector import (
    CATEGORY_REFERENCE_SCORES,
    classify_score_to_category,
    compute_regime_probs_series,
    ewm_volatility,
    half_life_bars_for_timeframe,
)
from upbit_data_service import get_candles

N_MULTIPLIER = 2.5


def _to_utc_iso(value: datetime) -> str:
    """candle_time이 tz 정보 없이 UTC 값만 담고 있을 수 있어, API 응답에 넘기기 전에 항상
    UTC 오프셋을 명시한다. backend/main.py의 동명 헬퍼와 같은 이유로 존재하되, 문자열이
    아니라 pandas Timestamp를 직접 받는다(pandas Timestamp도 datetime 서브클래스라
    replace/isoformat을 그대로 쓸 수 있다) — backend.main이 backend.regime_service를
    import하므로, 반대 방향으로 backend.main의 헬퍼를 가져오면 순환참조가 생긴다."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def evaluate_market(market: str, timeframe: str, start: datetime, end: datetime) -> dict:
    """market 하나에 대해 봉별 예측 카테고리 시계열 + confusion matrix + 상관계수 +
    실제분포를 반환한다. "적중" 판정의 정규화(봉당 스케일 맞추기)는
    docs/superpowers/specs_v1/2026-08-23-realtime-regime-detector-design.md
    "정정(Task 5 최종리뷰, 2026-08-23)" 문단 참고.

    반환값:
      half_life_bars, n_bars: 이 timeframe에 대해 실제로 쓰인 값(디버깅/표시용)
      candles: [{time, open, high, low, close, predicted_category}, ...] — 워밍업 미달
        구간은 predicted_category가 None. 마지막 n_bars 구간도 "정답"을 매길 미래 데이터가
        없어 confusion/actual_totals 집계에서는 빠지지만, candles에는 예측값이 그대로 담긴다.
      confusion: {예측카테고리: {실제카테고리: 건수}}
      actual_totals: {실제카테고리: 건수}
      correlation: 확률벡터 기댓값과 정규화된 실현수익률의 상관계수(샘플 2건 미만이면 None)
    """
    half_life_bars = half_life_bars_for_timeframe(timeframe)
    n_bars = round(half_life_bars * N_MULTIPLIER)

    df = get_candles(market, timeframe, start, end)
    closes = df["close"]
    returns = closes.pct_change(fill_method=None)
    regime_series = compute_regime_probs_series(df, half_life_bars)

    labels = list(CATEGORY_REFERENCE_SCORES.keys())
    confusion: dict[str, dict[str, int]] = {p: {a: 0 for a in labels} for p in labels}
    actual_totals: dict[str, int] = {a: 0 for a in labels}
    expected_scores: list[float] = []
    normalized_realized_values: list[float] = []

    candles: list[dict] = []
    for i, row in enumerate(df.itertuples()):
        probs = regime_series[i]
        predicted_category = max(probs, key=probs.get) if probs is not None else None
        candles.append({
            "time": _to_utc_iso(row.candle_time),
            "open": float(row.open), "high": float(row.high),
            "low": float(row.low), "close": float(row.close),
            "predicted_category": predicted_category,
        })

    for t in range(len(df) - n_bars):
        probs = regime_series[t]
        if probs is None:
            continue
        predicted = max(probs, key=probs.get)

        future_returns = returns.iloc[t + 1 : t + 1 + n_bars]
        if future_returns.empty or future_returns.isna().any():
            continue
        realized_volatility = ewm_volatility(future_returns, half_life_bars)
        if realized_volatility <= 0:
            continue
        normalized_realized = future_returns.mean() / realized_volatility
        actual = classify_score_to_category(normalized_realized)

        confusion[predicted][actual] += 1
        actual_totals[actual] += 1
        expected_score = sum(probs[label] * CATEGORY_REFERENCE_SCORES[label] for label in probs)
        expected_scores.append(expected_score)
        normalized_realized_values.append(normalized_realized)

    correlation: float | None = None
    if len(expected_scores) >= 2:
        computed = float(np.corrcoef(expected_scores, normalized_realized_values)[0, 1])
        if not math.isnan(computed):
            correlation = computed

    return {
        "half_life_bars": half_life_bars,
        "n_bars": n_bars,
        "candles": candles,
        "confusion": confusion,
        "actual_totals": actual_totals,
        "correlation": correlation,
    }
```

`scripts/regime_backtest.py` 전체를 다음으로 교체(기존 `_evaluate_market` 제거, 콘솔 출력
포맷은 그대로 유지):

```python
"""
scripts/regime_backtest.py

engine.regime_detector.compute_regime_probs_series()가 실제로 쓸모 있는지 과거 캔들로
검증한다. 규칙기반 결정론적 함수라 학습 없이 지금 바로 확인 가능. 평가 로직 자체는
backend/regime_service.py의 evaluate_market()로 이전됐다(GET /api/v1/regime/backtest
웹 API와 공유). 설계 문서:
docs/superpowers/specs_v1/2026-08-23-realtime-regime-detector-design.md,
docs/superpowers/specs_v1/2026-08-23-regime-detector-web-dashboard-design.md

Run: PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/regime_backtest.py
"""
from __future__ import annotations

from datetime import datetime, timezone

from backend.regime_service import evaluate_market
from engine.regime_detector import CATEGORY_REFERENCE_SCORES, half_life_bars_for_timeframe

MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-XRP"]
TIMEFRAME = "minutes60"
VALIDATION_START = datetime(2024, 1, 1, tzinfo=timezone.utc)
VALIDATION_END = datetime.now(timezone.utc)


def main() -> None:
    half_life_bars = half_life_bars_for_timeframe(TIMEFRAME)
    print(f"half_life_bars={half_life_bars:.1f}, timeframe={TIMEFRAME}")

    for market in MARKETS:
        print(f"\n=== {market} ({TIMEFRAME}) ===")
        result = evaluate_market(market, TIMEFRAME, VALIDATION_START, VALIDATION_END)
        confusion = result["confusion"]
        actual_totals = result["actual_totals"]
        correlation = result["correlation"]
        print(f"  n_bars={result['n_bars']}")

        print("  [예측 카테고리별 hit-rate]")
        for label in CATEGORY_REFERENCE_SCORES:
            row = confusion[label]
            total = sum(row.values())
            if total == 0:
                print(f"    {label}: 샘플 없음")
                continue
            hit = row[label]
            hit_rate = hit / total * 100
            print(f"    {label}: {hit}/{total} 적중 ({hit_rate:.1f}%)")

        if correlation is None:
            print("  [확률벡터-실현수익률 상관계수] 계산 불가(샘플 부족)")
        else:
            print(f"  [확률벡터-실현수익률 상관계수] {correlation:.3f}")

        print("  [confusion matrix] 행=예측, 열=실제")
        header = "    " + "예측\\실제".ljust(10) + "".join(label.ljust(10) for label in CATEGORY_REFERENCE_SCORES)
        print(header)
        for predicted_label in CATEGORY_REFERENCE_SCORES:
            row = confusion[predicted_label]
            row_str = "    " + predicted_label.ljust(10) + "".join(
                str(row[actual_label]).ljust(10) for actual_label in CATEGORY_REFERENCE_SCORES
            )
            print(row_str)

        total_samples = sum(actual_totals.values())
        print(f"  [실제 카테고리 분포(전체 샘플 {total_samples}건 기준)]")
        for label in CATEGORY_REFERENCE_SCORES:
            n = actual_totals[label]
            pct = n / total_samples * 100 if total_samples else 0.0
            print(f"    {label}: {n} ({pct:.1f}%)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_regime_service.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: (선택, 네트워크 필요) 스크립트가 여전히 동작하는지 사람이 직접 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/regime_backtest.py`
Expected: 마켓별로 `n_bars=...`, `[예측 카테고리별 hit-rate]`, `[확률벡터-실현수익률
상관계수]`, `[confusion matrix]`, `[실제 카테고리 분포]` 출력이 이전과 동일한 형식으로
나온다(수치는 실행 시점 캔들에 따라 달라질 수 있음 — 형식이 안 깨지는지만 확인).

- [ ] **Step 6: 커밋**

```bash
git add backend/regime_service.py scripts/regime_backtest.py tests/test_regime_service.py
git commit -m "refactor: 장세 판별 평가 로직을 backend/regime_service.py로 이전"
```

---

## Task 2: `GET /api/v1/regime/backtest` 엔드포인트

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_backend.py`

**Interfaces:**
- Consumes: `backend.regime_service.evaluate_market(market, timeframe, start, end) -> dict`
  (Task 1)
- Produces: `GET /api/v1/regime/backtest?market=&timeframe=&start=&end=` — 응답 스키마는
  `evaluate_market()`의 반환값 그대로.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_backend.py` 파일 끝에 추가:

```python
def test_regime_backtest_returns_evaluated_result(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    captured = {}

    def _fake_evaluate_market(market, timeframe, start, end):
        captured["args"] = (market, timeframe, start, end)
        return {
            "half_life_bars": 24.0, "n_bars": 60, "candles": [],
            "confusion": {}, "actual_totals": {}, "correlation": None,
        }

    monkeypatch.setattr(backend_module, "evaluate_market", _fake_evaluate_market)

    resp = client.get(
        "/api/v1/regime/backtest",
        params={"market": "KRW-BTC", "timeframe": "minutes60", "start": "2026-01-01", "end": "2026-01-31"},
    )

    assert resp.status_code == 200
    assert resp.json() == {
        "half_life_bars": 24.0, "n_bars": 60, "candles": [],
        "confusion": {}, "actual_totals": {}, "correlation": None,
    }
    market, timeframe, start, end = captured["args"]
    assert market == "KRW-BTC"
    assert timeframe == "minutes60"
    assert start == datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert end == datetime(2026, 1, 31, 23, 59, 59, tzinfo=timezone.utc)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_backend.py::test_regime_backtest_returns_evaluated_result -v`
Expected: FAIL — `AttributeError: module 'backend.main' has no attribute 'evaluate_market'`
(또는 404 Not Found — 엔드포인트가 아직 없음)

- [ ] **Step 3: 최소 구현 작성**

`backend/main.py`의 `from backend.grid_search_service import (...)` 블록(현재 65~71번째
줄) 바로 뒤에 추가:

```python
from backend.regime_service import evaluate_market
```

`get_trend_segments_endpoint`/`refresh_trend_segments_endpoint`(현재 586~595번째 줄) 뒤,
`get_indicator_catalog`(현재 598번째 줄) 앞에 추가:

```python
@app.get("/api/v1/regime/backtest")
def get_regime_backtest_endpoint(
    market: str = Query(...),
    timeframe: str = Query(...),
    start: str = Query(...),
    end: str = Query(...),
) -> dict:
    start_dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(end, "%Y-%m-%d").replace(
        hour=23, minute=59, second=59, tzinfo=timezone.utc
    )
    return evaluate_market(market, timeframe, start_dt, end_dt)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_backend.py::test_regime_backtest_returns_evaluated_result -v`
Expected: PASS (1 passed)

- [ ] **Step 5: 전체 백엔드 테스트 회귀 확인**

Run: `pytest tests/test_backend.py tests/test_regime_service.py tests/test_regime_detector.py -v`
Expected: 전부 PASS, 실패 없음

- [ ] **Step 6: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "feat: GET /api/v1/regime/backtest 엔드포인트 추가"
```

---

## Task 3: 프론트 타입 + API 클라이언트 함수

**Files:**
- Modify: `frontend/lib/types/eda.ts`
- Modify: `frontend/lib/api/eda.ts`

**Interfaces:**
- Produces: `RegimeCategory`(타입), `RegimeCandle`, `RegimeBacktestResult`(타입),
  `getRegimeBacktest(params: { market: string; timeframe: string; start: string; end: string
  }) -> Promise<RegimeBacktestResult>`

- [ ] **Step 1: 타입 추가**

`frontend/lib/types/eda.ts`의 `TrendSegmentAnalysis` 인터페이스(현재 110~116번째 줄) 바로
뒤에 추가:

```typescript
export type RegimeCategory = '급상승' | '완만상승' | '횡보' | '완만하락' | '급하락';

export interface RegimeCandle {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  predicted_category: RegimeCategory | null;
}

export interface RegimeBacktestResult {
  half_life_bars: number;
  n_bars: number;
  candles: RegimeCandle[];
  confusion: Record<RegimeCategory, Record<RegimeCategory, number>>;
  actual_totals: Record<RegimeCategory, number>;
  correlation: number | null;
}
```

- [ ] **Step 2: API 클라이언트 함수 추가**

`frontend/lib/api/eda.ts` 상단 import 목록은 알파벳 순으로 정렬돼 있다(`Market,` 다음이
`RunBacktestRequest,`). 그 사이, `Market,` 다음 줄에 `RegimeBacktestResult` 추가:

```typescript
  RegimeBacktestResult,
```

파일 끝에 추가:

```typescript
export function getRegimeBacktest(params: {
  market: string;
  timeframe: string;
  start: string;
  end: string;
}): Promise<RegimeBacktestResult> {
  const query = new URLSearchParams(params);
  return apiFetch<RegimeBacktestResult>(`/api/v1/regime/backtest?${query.toString()}`);
}
```

- [ ] **Step 3: 타입 검증**

Run(`frontend` 디렉터리에서): `npm run build`
Expected: 빌드 성공(타입 에러 없음). `getRegimeBacktest`/`RegimeBacktestResult`는 아직 아무
곳에서도 쓰이지 않지만, export되지 않은 미사용 지역 변수가 아니라 미사용 export이므로
TypeScript가 에러를 내지 않는다.

- [ ] **Step 4: 커밋**

```bash
git add frontend/lib/types/eda.ts frontend/lib/api/eda.ts
git commit -m "feat: 장세 판별 결과 타입 + API 클라이언트 함수 추가"
```

---

## Task 4: `/regime` 탭 — 폼 + 대시보드 셸(원시 JSON 미리보기)

**Files:**
- Modify: `frontend/components/NavTabs.tsx`
- Create: `frontend/app/regime/page.tsx`
- Create: `frontend/components/RegimeBacktestForm.tsx`
- Create: `frontend/components/RegimeDashboard.tsx`

**Interfaces:**
- Consumes: `getMarkets`, `getRegimeBacktest`(Task 3), `CoinSelect`/`sortMarkets`,
  `defaultDate`/`formatTimeframe`/`TIMEFRAME_CODES`, `ApiError`, `SECTION_HEADER_CLASS`
- Produces: `RegimeBacktestForm`(컴포넌트, props: `{ submitting: boolean; onSubmit: (params:
  RegimeBacktestParams) => void }`), `RegimeBacktestParams`(타입, `{ market, timeframe,
  start, end }`), `RegimeDashboard`(컴포넌트, props 없음)

이 태스크는 자동 테스트가 없는 프론트 UI 셸이라, 브라우저로 직접 확인하는 것으로
검증한다(이 코드베이스의 프론트 검증 방침).

- [ ] **Step 1: `NavTabs.tsx`에 새 탭 추가**

`frontend/components/NavTabs.tsx` 전체를 다음으로 교체:

```typescript
'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Activity, BarChart3, BookOpen, ClipboardList, FlaskConical, Grid3x3, Rocket, Settings } from 'lucide-react';
import ThemeToggle from '@/components/ThemeToggle';
import MobileNavDrawer from '@/components/MobileNavDrawer';
import { isActive } from '@/lib/nav-active';

const STEPS = [
  { href: '/', title: '백테스트 설정', icon: Settings },
  { href: '/grid-search', title: 'Grid Search', icon: Grid3x3 },
  { href: '/backtests', title: '백테스트 결과', icon: FlaskConical },
  { href: '/live-strategies', title: '라이브 전략', icon: Rocket },
  { href: '/journal', title: '매매일지', icon: ClipboardList },
  { href: '/analysis', title: '세그먼트', icon: BarChart3 },
  { href: '/regime', title: '장세 판별', icon: Activity },
  { href: '/guide', title: '지표 가이드', icon: BookOpen },
];

export default function NavTabs() {
  const pathname = usePathname();
  const activeStep = STEPS.find((step) => isActive(pathname, step.href));

  return (
    <header className="flex items-center justify-between border-b px-3 md:px-6">
      <div className="flex w-full items-center justify-between py-2.5 md:hidden">
        <span className="truncate text-sm font-semibold">{activeStep?.title ?? 'Upbit 전략 EDA'}</span>
        <MobileNavDrawer steps={STEPS} />
      </div>

      <nav className="hidden gap-6 md:flex">
        {STEPS.map((step) => {
          const Icon = step.icon;
          const active = isActive(pathname, step.href);
          return (
            <Link
              key={step.href}
              href={step.href}
              className={
                active
                  ? 'flex items-center gap-1.5 border-b-2 border-primary py-3 font-semibold text-foreground'
                  : 'flex items-center gap-1.5 border-b-2 border-transparent py-3 text-muted-foreground hover:text-foreground'
              }
            >
              <Icon className="size-4" />
              {step.title}
            </Link>
          );
        })}
      </nav>
      <div className="hidden md:block">
        <ThemeToggle />
      </div>
    </header>
  );
}
```

- [ ] **Step 2: `RegimeBacktestForm.tsx` 작성**

`frontend/components/RegimeBacktestForm.tsx` 신규 생성:

```typescript
'use client';

import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import CoinSelect, { sortMarkets } from '@/components/CoinSelect';
import { ApiError } from '@/lib/api/client';
import { getMarkets } from '@/lib/api/eda';
import { SECTION_HEADER_CLASS } from '@/lib/ui-classes';
import { defaultDate, formatTimeframe, TIMEFRAME_CODES } from '@/lib/format';
import type { Market } from '@/lib/types/eda';

const TIMEFRAME_OPTIONS = TIMEFRAME_CODES.map((timeframe) => ({
  label: formatTimeframe(timeframe),
  timeframe,
}));

export interface RegimeBacktestParams {
  market: string;
  timeframe: string;
  start: string;
  end: string;
}

interface RegimeBacktestFormProps {
  submitting: boolean;
  onSubmit: (params: RegimeBacktestParams) => void;
}

export default function RegimeBacktestForm({ submitting, onSubmit }: RegimeBacktestFormProps) {
  const [markets, setMarkets] = useState<Market[]>([]);
  const [marketsError, setMarketsError] = useState<string | null>(null);
  const [market, setMarket] = useState('');
  const [timeframe, setTimeframe] = useState('minutes60');
  const [start, setStart] = useState(defaultDate(365));
  const [end, setEnd] = useState(defaultDate(0));
  const [validationError, setValidationError] = useState<string | null>(null);

  useEffect(() => {
    getMarkets()
      .then((data) => {
        setMarkets(data);
        const sorted = sortMarkets(data, 'change_rate', 'desc');
        if (sorted.length > 0) setMarket((prev) => prev || sorted[0].market);
      })
      .catch((err) => setMarketsError(err instanceof ApiError ? err.message : '코인 목록을 불러오지 못했습니다.'));
  }, []);

  function handleSubmit() {
    setValidationError(null);
    if (start >= end) {
      setValidationError('시작일은 종료일보다 빨라야 합니다.');
      return;
    }
    onSubmit({ market, timeframe, start, end });
  }

  return (
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

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <label className="mb-1.5 block text-sm font-medium">시작일</label>
          <Input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
        </div>
        <div>
          <label className="mb-1.5 block text-sm font-medium">종료일</label>
          <Input type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
        </div>
      </div>

      {validationError && <p className="text-sm text-destructive">{validationError}</p>}

      <Button onClick={handleSubmit} disabled={submitting || !market}>
        {submitting ? '조회 중...' : '조회'}
      </Button>
    </div>
  );
}
```

- [ ] **Step 3: `RegimeDashboard.tsx` 작성 (원시 JSON 미리보기)**

`frontend/components/RegimeDashboard.tsx` 신규 생성:

```typescript
'use client';

import { useState } from 'react';
import RegimeBacktestForm, { type RegimeBacktestParams } from '@/components/RegimeBacktestForm';
import { ApiError } from '@/lib/api/client';
import { getRegimeBacktest } from '@/lib/api/eda';
import type { RegimeBacktestResult } from '@/lib/types/eda';

export default function RegimeDashboard() {
  const [result, setResult] = useState<RegimeBacktestResult | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(params: RegimeBacktestParams) {
    setSubmitting(true);
    setError(null);
    setResult(null);
    try {
      const data = await getRegimeBacktest(params);
      setResult(data);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '장세 판별 결과를 불러오지 못했습니다.');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-4">
      <RegimeBacktestForm submitting={submitting} onSubmit={handleSubmit} />
      {error && <p className="text-sm text-destructive">{error}</p>}
      {result && result.candles.length === 0 && (
        <p className="text-sm text-muted-foreground">선택한 기간에 데이터가 부족합니다.</p>
      )}
      {result && result.candles.length > 0 && (
        <pre className="max-h-96 overflow-auto rounded-lg border bg-muted p-4 text-xs">
          {JSON.stringify(result, null, 2)}
        </pre>
      )}
    </div>
  );
}
```

- [ ] **Step 4: 페이지 작성**

`frontend/app/regime/page.tsx` 신규 생성:

```typescript
import RegimeDashboard from '@/components/RegimeDashboard';

export default function Page() {
  return (
    <div>
      <h1 className="mb-4 text-lg font-semibold">장세 판별</h1>
      <RegimeDashboard />
    </div>
  );
}
```

- [ ] **Step 5: 브라우저로 직접 확인**

Run(저장소 루트): `python -m uvicorn backend.main:app --reload --port 8000`
Run(`frontend` 디렉터리, 별도 터미널): `npm run dev`

브라우저로 `http://localhost:3000/regime` 접속.
Expected:
- 상단 네비게이션에 "장세 판별" 탭이 보이고 클릭하면 이 페이지로 이동한다.
- 코인 선택 드롭다운에 마켓 목록이 뜬다(그리드서치 탭과 동일한 목록).
- 봉데이터 버튼(1분~1일)과 시작일/종료일 입력이 보인다(기본값: 시작일이 오늘로부터
  365일 전, 종료일이 오늘).
- 코인을 선택하고 "조회" 버튼을 누르면 로딩 중 "조회 중..."으로 바뀌고, 완료되면 원시
  JSON이 화면에 출력된다(`half_life_bars`, `n_bars`, `candles`, `confusion`,
  `actual_totals`, `correlation` 키가 보여야 함).
- 시작일을 종료일보다 늦게 설정하면 "시작일은 종료일보다 빨라야 합니다." 에러 메시지가
  뜬다.

- [ ] **Step 6: 커밋**

```bash
git add frontend/components/NavTabs.tsx frontend/app/regime/page.tsx \
  frontend/components/RegimeBacktestForm.tsx frontend/components/RegimeDashboard.tsx
git commit -m "feat: 장세 판별 탭 셸 추가 (폼 + 원시 결과 미리보기)"
```

---

## Task 5: `RegimeChart` — 예측 카테고리로 캔들 색칠

**Files:**
- Modify: `frontend/app/globals.css`
- Create: `frontend/components/RegimeChart.tsx`
- Modify: `frontend/components/RegimeDashboard.tsx`

**Interfaces:**
- Consumes: `RegimeCandle`, `RegimeCategory`(Task 3), CSS 변수
  `--regime-surge-up/--regime-mild-up/--marker-boundary/--regime-mild-down/--regime-surge-down/--trend-unclassified`
- Produces: `RegimeChart`(컴포넌트, props: `{ candles: RegimeCandle[]; timeframe: string }`)

- [ ] **Step 1: CSS 변수 추가**

`frontend/app/globals.css`의 `:root` 블록(현재 87~92번째 줄, `--trend-unclassified` 다음)에
추가:

```css
    --regime-surge-up: oklch(0.5 0.27 27.325);
    --regime-mild-up: oklch(0.75 0.14 35);
    --regime-mild-down: oklch(0.75 0.1 255);
    --regime-surge-down: oklch(0.48 0.24 262.881);
```

`.dark` 블록(현재 126~131번째 줄, `--trend-unclassified` 다음)에 동일하게 추가:

```css
    --regime-surge-up: oklch(0.5 0.27 27.325);
    --regime-mild-up: oklch(0.75 0.14 35);
    --regime-mild-down: oklch(0.75 0.1 255);
    --regime-surge-down: oklch(0.48 0.24 262.881);
```

(횡보는 기존 `--marker-boundary`를, 미분류는 기존 `--trend-unclassified`를 그대로 재사용 —
`price-up`/`price-down`처럼 라이트/다크 값이 동일한 이 코드베이스의 기존 패턴을 따른다.)

- [ ] **Step 2: `RegimeChart.tsx` 작성**

`frontend/components/RegimeChart.tsx` 신규 생성:

```typescript
'use client';

import { useEffect, useRef } from 'react';
import { createChart, CandlestickSeries, ColorType, CrosshairMode, type UTCTimestamp } from 'lightweight-charts';
import type { RegimeCandle, RegimeCategory } from '@/lib/types/eda';

interface RegimeChartProps {
  candles: RegimeCandle[];
  timeframe: string;
}

type DayString = `${number}-${number}-${number}`;

const CATEGORY_ORDER: RegimeCategory[] = ['급상승', '완만상승', '횡보', '완만하락', '급하락'];

function categoryVarName(label: RegimeCategory): string {
  switch (label) {
    case '급상승':
      return '--regime-surge-up';
    case '완만상승':
      return '--regime-mild-up';
    case '횡보':
      return '--marker-boundary';
    case '완만하락':
      return '--regime-mild-down';
    case '급하락':
      return '--regime-surge-down';
  }
}

function isIntraday(timeframe: string): boolean {
  return timeframe.startsWith('minutes');
}

function toUnix(iso: string): UTCTimestamp {
  return Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp;
}

export default function RegimeChart({ candles, timeframe }: RegimeChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const intradayMode = isIntraday(timeframe);

  useEffect(() => {
    if (!containerRef.current || candles.length === 0) return;

    // PriceChart.tsx/TrendSegmentChart.tsx와 동일한 이유: getComputedStyle의 oklch()
    // 반환값을 lightweight-charts가 파싱하지 못해, canvas 2D로 한 번 그려 rgba()로 변환한다.
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

    const background = resolveColor('--background');
    const foreground = resolveColor('--foreground');
    const border = resolveColor('--border');
    const unclassifiedColor = resolveColor('--trend-unclassified');
    const categoryColor: Record<RegimeCategory, string> = {
      급상승: resolveColor('--regime-surge-up'),
      완만상승: resolveColor('--regime-mild-up'),
      횡보: resolveColor('--marker-boundary'),
      완만하락: resolveColor('--regime-mild-down'),
      급하락: resolveColor('--regime-surge-down'),
    };

    function colorFor(candle: RegimeCandle): string {
      return candle.predicted_category ? categoryColor[candle.predicted_category] : unclassifiedColor;
    }

    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: containerRef.current.clientHeight,
      layout: { background: { type: ColorType.Solid, color: background }, textColor: foreground },
      crosshair: { mode: CrosshairMode.Normal },
      timeScale: { timeVisible: intradayMode, secondsVisible: false, borderColor: border },
      rightPriceScale: { borderColor: border },
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: unclassifiedColor, downColor: unclassifiedColor, borderVisible: false,
      wickUpColor: unclassifiedColor, wickDownColor: unclassifiedColor,
    });

    if (intradayMode) {
      const candleData = candles
        .map((c) => {
          const color = colorFor(c);
          return {
            time: toUnix(c.time), open: c.open, high: c.high, low: c.low, close: c.close,
            color, borderColor: color, wickColor: color,
          };
        })
        .sort((a, b) => a.time - b.time);
      candleSeries.setData(candleData);
    } else {
      const candleData = candles
        .map((c) => {
          const color = colorFor(c);
          return {
            time: c.time.split('T')[0] as DayString,
            open: c.open, high: c.high, low: c.low, close: c.close,
            color, borderColor: color, wickColor: color,
          };
        })
        .sort((a, b) => String(a.time).localeCompare(String(b.time)))
        .filter((bar, i, arr) => i === 0 || bar.time !== arr[i - 1].time);
      candleSeries.setData(candleData);
    }

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
  }, [candles, timeframe, intradayMode]);

  return (
    <div className="w-full">
      <div className="mb-2 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
        {CATEGORY_ORDER.map((label) => (
          <span key={label} className="flex items-center gap-1.5">
            <span
              className="inline-block h-2 w-2 rounded-full"
              style={{ backgroundColor: `var(${categoryVarName(label)})` }}
            />
            {label}
          </span>
        ))}
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: 'var(--trend-unclassified)' }} />
          미분류(워밍업)
        </span>
      </div>
      <div ref={containerRef} className="h-60 w-full rounded-lg overflow-hidden border md:h-80" />
    </div>
  );
}
```

- [ ] **Step 3: `RegimeDashboard.tsx`에 차트 연결**

`frontend/components/RegimeDashboard.tsx` 전체를 다음으로 교체:

```typescript
'use client';

import { useState } from 'react';
import RegimeBacktestForm, { type RegimeBacktestParams } from '@/components/RegimeBacktestForm';
import RegimeChart from '@/components/RegimeChart';
import { ApiError } from '@/lib/api/client';
import { getRegimeBacktest } from '@/lib/api/eda';
import type { RegimeBacktestResult } from '@/lib/types/eda';

export default function RegimeDashboard() {
  const [result, setResult] = useState<RegimeBacktestResult | null>(null);
  const [timeframe, setTimeframe] = useState('minutes60');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(params: RegimeBacktestParams) {
    setSubmitting(true);
    setError(null);
    setResult(null);
    try {
      const data = await getRegimeBacktest(params);
      setTimeframe(params.timeframe);
      setResult(data);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '장세 판별 결과를 불러오지 못했습니다.');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-4">
      <RegimeBacktestForm submitting={submitting} onSubmit={handleSubmit} />
      {error && <p className="text-sm text-destructive">{error}</p>}
      {result && result.candles.length === 0 && (
        <p className="text-sm text-muted-foreground">선택한 기간에 데이터가 부족합니다.</p>
      )}
      {result && result.candles.length > 0 && (
        <>
          <RegimeChart candles={result.candles} timeframe={timeframe} />
          <pre className="max-h-96 overflow-auto rounded-lg border bg-muted p-4 text-xs">
            {JSON.stringify(
              { confusion: result.confusion, actual_totals: result.actual_totals, correlation: result.correlation },
              null,
              2,
            )}
          </pre>
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 4: 브라우저로 직접 확인**

백엔드/프론트 개발 서버가 이미 떠 있지 않다면 Task 4 Step 5와 동일하게 실행.

브라우저로 `http://localhost:3000/regime` 접속, 코인 선택 후 조회.
Expected:
- 범례(급상승/완만상승/횡보/완만하락/급하락/미분류)가 차트 위에 보인다.
- 캔들스틱 차트가 렌더되고, 초반 구간(워밍업 미달) 캔들은 회색 계열(미분류)로, 이후
  구간은 예측 카테고리에 따라 색이 달라진다(상승계열은 빨강 계열, 하락계열은 파랑 계열,
  횡보는 회색).
- 봉타입을 `1시간`처럼 분단위로 선택하면 x축에 시각(timeVisible)이 표시되고, `1일`로
  선택하면 날짜 단위로 표시된다.
- 차트 아래에 여전히 confusion/actual_totals/correlation의 원시 JSON이 보인다(Task 6에서
  표로 교체 예정).
- 라이트/다크 테마를 모두 토글해서 차트 색이 깨지지 않는지 확인한다(`ThemeToggle` 사용).

- [ ] **Step 5: 커밋**

```bash
git add frontend/app/globals.css frontend/components/RegimeChart.tsx frontend/components/RegimeDashboard.tsx
git commit -m "feat: 예측 카테고리로 캔들을 칠하는 RegimeChart 추가"
```

---

## Task 6: `RegimeAccuracyReport` — 정확도 표

**Files:**
- Create: `frontend/components/RegimeAccuracyReport.tsx`
- Modify: `frontend/components/RegimeDashboard.tsx`

**Interfaces:**
- Consumes: `RegimeBacktestResult`(Task 3)
- Produces: `RegimeAccuracyReport`(컴포넌트, props: `{ report: RegimeBacktestResult }`)

- [ ] **Step 1: `RegimeAccuracyReport.tsx` 작성**

`frontend/components/RegimeAccuracyReport.tsx` 신규 생성:

```typescript
import type { RegimeBacktestResult, RegimeCategory } from '@/lib/types/eda';

const CATEGORY_ORDER: RegimeCategory[] = ['급상승', '완만상승', '횡보', '완만하락', '급하락'];

interface RegimeAccuracyReportProps {
  report: RegimeBacktestResult;
}

export default function RegimeAccuracyReport({ report }: RegimeAccuracyReportProps) {
  const { confusion, actual_totals, correlation } = report;
  const totalSamples = CATEGORY_ORDER.reduce((sum, label) => sum + actual_totals[label], 0);

  return (
    <div className="space-y-6 rounded-xl border p-6 shadow-sm">
      <div>
        <h2 className="mb-2 text-sm font-semibold">카테고리별 적중률</h2>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b text-left text-muted-foreground">
              <th className="py-1.5">예측 카테고리</th>
              <th className="py-1.5 text-right">총건수</th>
              <th className="py-1.5 text-right">적중건수</th>
              <th className="py-1.5 text-right">적중률</th>
            </tr>
          </thead>
          <tbody>
            {CATEGORY_ORDER.map((label) => {
              const row = confusion[label];
              const total = CATEGORY_ORDER.reduce((sum, a) => sum + row[a], 0);
              const hit = row[label];
              return (
                <tr key={label} className="border-b last:border-0">
                  <td className="py-1.5">{label}</td>
                  <td className="py-1.5 text-right tabular-nums">{total}</td>
                  <td className="py-1.5 text-right tabular-nums">{hit}</td>
                  <td className="py-1.5 text-right tabular-nums">
                    {total === 0 ? '샘플 없음' : `${((hit / total) * 100).toFixed(1)}%`}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div>
        <h2 className="mb-2 text-sm font-semibold">확률벡터-실현수익률 상관계수</h2>
        <p className="text-sm tabular-nums">
          {correlation === null ? '계산 불가(샘플 부족)' : correlation.toFixed(3)}
        </p>
      </div>

      <div>
        <h2 className="mb-2 text-sm font-semibold">Confusion Matrix (행=예측, 열=실제)</h2>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-muted-foreground">
                <th className="py-1.5">예측\실제</th>
                {CATEGORY_ORDER.map((label) => (
                  <th key={label} className="py-1.5 text-right">
                    {label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {CATEGORY_ORDER.map((predicted) => (
                <tr key={predicted} className="border-b last:border-0">
                  <td className="py-1.5 font-medium">{predicted}</td>
                  {CATEGORY_ORDER.map((actual) => (
                    <td key={actual} className="py-1.5 text-right tabular-nums">
                      {confusion[predicted][actual]}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div>
        <h2 className="mb-2 text-sm font-semibold">실제 카테고리 분포 (전체 {totalSamples}건)</h2>
        <table className="w-full text-sm">
          <tbody>
            {CATEGORY_ORDER.map((label) => {
              const n = actual_totals[label];
              const pct = totalSamples ? (n / totalSamples) * 100 : 0;
              return (
                <tr key={label} className="border-b last:border-0">
                  <td className="py-1.5">{label}</td>
                  <td className="py-1.5 text-right tabular-nums">{n}</td>
                  <td className="py-1.5 text-right tabular-nums">{pct.toFixed(1)}%</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: `RegimeDashboard.tsx`에 리포트 연결 (원시 JSON 제거)**

`frontend/components/RegimeDashboard.tsx` 전체를 다음으로 교체:

```typescript
'use client';

import { useState } from 'react';
import RegimeBacktestForm, { type RegimeBacktestParams } from '@/components/RegimeBacktestForm';
import RegimeChart from '@/components/RegimeChart';
import RegimeAccuracyReport from '@/components/RegimeAccuracyReport';
import { ApiError } from '@/lib/api/client';
import { getRegimeBacktest } from '@/lib/api/eda';
import type { RegimeBacktestResult } from '@/lib/types/eda';

export default function RegimeDashboard() {
  const [result, setResult] = useState<RegimeBacktestResult | null>(null);
  const [timeframe, setTimeframe] = useState('minutes60');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(params: RegimeBacktestParams) {
    setSubmitting(true);
    setError(null);
    setResult(null);
    try {
      const data = await getRegimeBacktest(params);
      setTimeframe(params.timeframe);
      setResult(data);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '장세 판별 결과를 불러오지 못했습니다.');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-4">
      <RegimeBacktestForm submitting={submitting} onSubmit={handleSubmit} />
      {error && <p className="text-sm text-destructive">{error}</p>}
      {result && result.candles.length === 0 && (
        <p className="text-sm text-muted-foreground">선택한 기간에 데이터가 부족합니다.</p>
      )}
      {result && result.candles.length > 0 && (
        <>
          <RegimeChart candles={result.candles} timeframe={timeframe} />
          <RegimeAccuracyReport report={result} />
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 3: 브라우저로 직접 확인**

백엔드/프론트 개발 서버가 이미 떠 있지 않다면 Task 4 Step 5와 동일하게 실행.

브라우저로 `http://localhost:3000/regime` 접속, 코인/봉타입/기간을 몇 가지 바꿔가며 조회.
Expected:
- 차트 아래에 "카테고리별 적중률", "확률벡터-실현수익률 상관계수",
  "Confusion Matrix (행=예측, 열=실제)", "실제 카테고리 분포" 4개 섹션이 표로 렌더된다.
- confusion matrix의 대각선 값(예측=실제)이 "카테고리별 적중률" 표의 "적중건수"와 각 행마다
  일치한다.
- 예측 카테고리별 총건수 합이 confusion matrix 각 행의 합과 일치한다.
- "실제 카테고리 분포" 표의 건수 합이 헤더에 적힌 "전체 N건"과 일치한다.
- 샘플이 부족한 기간(예: 시작일=종료일 하루 전)을 선택하면 "샘플 없음"/"계산 불가(샘플
  부족)"가 정상적으로 뜨고 화면이 깨지지 않는다.

- [ ] **Step 4: 커밋**

```bash
git add frontend/components/RegimeAccuracyReport.tsx frontend/components/RegimeDashboard.tsx
git commit -m "feat: 장세 판별 정확도 리포트(RegimeAccuracyReport) 추가"
```

---

## Self-Review 결과

- **스펙 커버리지**: 백엔드 서비스 추출(Task 1), 신규 GET 엔드포인트(Task 2), 프론트
  타입/API(Task 3), 새 탭+폼(Task 4), 캔들 색상 차트(Task 5), 정확도 리포트(Task 6) — 스펙의
  "백엔드 설계"/"프론트엔드 설계" 섹션 전부에 대응하는 태스크가 있다. 스펙의 "비범위"
  항목(캘리브레이션 곡선, 캐싱, 파라미터 튜닝, 판별기 로직 수정)은 의도적으로 태스크 없음.
- **플레이스홀더 스캔**: TBD/TODO 없음. Task 1 Step 5(스크립트 수동 실행)는 "선택,
  네트워크 필요"로 명시했지만 실제 명령과 기대 출력을 구체적으로 적었다.
- **타입 일관성**: `evaluate_market()`의 반환 키(`half_life_bars, n_bars, candles,
  confusion, actual_totals, correlation`)가 Task 1(백엔드 구현) → Task 2(엔드포인트가
  그대로 반환) → Task 3(`RegimeBacktestResult` 타입) → Task 5/6(프론트 컴포넌트 props)까지
  드리프트 없이 동일하게 쓰인다. `RegimeCategory` 5개 값과 표시 순서(`CATEGORY_ORDER`)도
  Task 5/6에서 동일한 배열을 재사용한다. `RegimeBacktestParams`(Task 4에서 정의)를 Task
  5/6에서도 그대로 import해서 쓴다.
