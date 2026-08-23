# 장세 판별 대시보드 현재예측카드+히트맵 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/regime` 대시보드에 "현재 시점" 예측을 명시적으로 보여주는 카드를 추가하고,
Confusion Matrix를 행=실제/열=예측으로 뒤집어 히트맵으로 시각화하며, 그 옆에 실제
카테고리 분포 표를 나란히 배치한다.

**Architecture:** `backend/regime_service.py`의 `evaluate_market()`이 이미 계산해두는
마지막 시점의 확률벡터(`regime_series[-1]`)를 응답에 `current_prediction` 필드로
노출하기만 하면 되므로 새 계산 로직은 없다. 프론트는 신규 `RegimeCurrentPrediction`
컴포넌트를 하나 추가하고, `RegimeAccuracyReport`의 Confusion Matrix 렌더링 순서와
레이아웃만 바꾼다. `confusion` 데이터 구조(`Record<예측, Record<실제, number>>`) 자체는
바뀌지 않는다 — 프론트에서 순회 순서만 바뀐다.

**Tech Stack:** Python(FastAPI, pandas), Next.js/React(TypeScript). 새 라이브러리 없음.

## Global Constraints

- 설계 문서: `docs/superpowers/specs/2026-08-24-regime-dashboard-current-prediction-and-heatmap-design.md`.
  이 스펙과 충돌하는 구현은 하지 않는다.
- `N_MULTIPLIER`/`HALF_LIFE_DAYS`(예측 지평)는 이번에도 사용자가 조절 가능하게 만들지
  않는다 — 고정값을 텍스트로 보여주기만 한다.
- `engine/regime_detector.py`는 수정하지 않는다.
- "카테고리별 적중률"(최상단 표)과 "확률벡터-실현수익률 상관계수" 섹션은 구조를 바꾸지
  않는다.
- `GET /api/v1/regime/backtest` 엔드포인트(`backend/main.py`)는 `evaluate_market()`의
  반환값을 그대로 pass-through 하므로 수정 불필요 — `current_prediction` 필드는 자동으로
  응답에 포함된다.
- 히트맵 색상은 새 CSS 커스텀 프로퍼티(`globals.css`)를 추가하지 않고, 컴포넌트 내부에서
  고정 oklch 값 + 인라인 스타일로 처리한다(`oklch(0.55 0.2 255 / {alpha})`, alpha는
  `행 내 비율 × 0.5`로 최대 50% 불투명도 — 텍스트 가독성 유지).
- `RegimeCurrentPrediction`은 `CATEGORY_ORDER`/`categoryVarName`을
  `RegimeChart.tsx`/`RegimeAccuracyReport.tsx`와 동일한 내용으로 로컬 재정의한다(공용
  유틸 추출은 비범위 — 기존 3개 차트 컴포넌트도 이미 이 패턴).
- 프론트 타입 검증은 `frontend` 디렉터리에서 `npm run build`로 확인한다.
- 백엔드 테스트는 `pytest tests/<file>.py -v`로 실행한다(저장소 루트 기준).
- 개발 서버(수동 브라우저 검증용):
  - 백엔드: 저장소 루트에서 `python -m uvicorn backend.main:app --reload --port 8000`
  - 프론트: `frontend` 디렉터리에서 `npm run dev` (포트 3000)

---

## Task 1: 백엔드 — `evaluate_market()`에 `current_prediction` 필드 추가

**Files:**
- Modify: `backend/regime_service.py`
- Test: `tests/test_regime_service.py`

**Interfaces:**
- Consumes: 기존 `regime_series`(로컬 변수, `compute_regime_probs_series()`의 반환값),
  기존 `candles`(로컬 변수)
- Produces: `evaluate_market()`의 반환 dict에 `current_prediction: dict | None` 추가 —
  `{"time": str, "predicted_category": str | None, "probs": dict[str, float] | None}`
  또는 `candles`가 빈 리스트면 `None`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime_service.py` 파일 끝에 추가:

```python
def test_evaluate_market_current_prediction_matches_last_candle(monkeypatch):
    closes = [100.0 * (1.01**i) for i in range(40)]
    monkeypatch.setattr(regime_service, "get_candles", lambda *a, **k: _make_candle_df(closes))

    result = evaluate_market(
        "KRW-BTC", "days",
        datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 2, 10, tzinfo=timezone.utc),
    )

    assert result["current_prediction"] is not None
    assert result["current_prediction"]["time"] == result["candles"][-1]["time"]
    assert result["current_prediction"]["predicted_category"] == result["candles"][-1]["predicted_category"]
    assert result["current_prediction"]["probs"] is not None
    assert sum(result["current_prediction"]["probs"].values()) == pytest.approx(1.0, abs=1e-9)


def test_evaluate_market_current_prediction_is_none_when_warmup_not_reached(monkeypatch):
    closes = [100.0, 101.0, 102.0]
    monkeypatch.setattr(regime_service, "get_candles", lambda *a, **k: _make_candle_df(closes))

    result = evaluate_market(
        "KRW-BTC", "days",
        datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 4, tzinfo=timezone.utc),
    )

    assert result["current_prediction"] is not None
    assert result["current_prediction"]["predicted_category"] is None
    assert result["current_prediction"]["probs"] is None


def test_evaluate_market_current_prediction_is_none_when_no_candles(monkeypatch):
    monkeypatch.setattr(regime_service, "get_candles", lambda *a, **k: pd.DataFrame(columns=_CANDLE_COLUMNS))

    result = evaluate_market(
        "KRW-BTC", "days",
        datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 2, tzinfo=timezone.utc),
    )

    assert result["current_prediction"] is None
```

(이 파일은 이미 `regime_service`/`evaluate_market`/`_make_candle_df`/`_CANDLE_COLUMNS`를
import/정의하고 있으므로 추가 import 불필요.)

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_regime_service.py -v -k current_prediction`
Expected: FAIL — `KeyError: 'current_prediction'`(반환 dict에 아직 이 키가 없음)

- [ ] **Step 3: 최소 구현 작성**

`backend/regime_service.py`의 `evaluate_market()` 안, `candles` 리스트를 만드는 for문
(현재 78~87번째 줄) 바로 뒤 — `for t in range(len(df) - n_bars):` 루프(현재 89번째 줄)
앞에 추가:

```python
    current_prediction: dict | None = None
    if candles:
        last_probs = regime_series[-1] if regime_series else None
        current_prediction = {
            "time": candles[-1]["time"],
            "predicted_category": candles[-1]["predicted_category"],
            "probs": last_probs,
        }
```

함수 맨 끝의 `return` 문(현재 116~123번째 줄)에 `current_prediction` 키 추가:

```python
    return {
        "half_life_bars": half_life_bars,
        "n_bars": n_bars,
        "candles": candles,
        "current_prediction": current_prediction,
        "confusion": confusion,
        "actual_totals": actual_totals,
        "correlation": correlation,
    }
```

함수 docstring(반환값 설명 부분)에 한 줄 추가:

```
      current_prediction: {time, predicted_category, probs} — candles의 마지막 원소와
        동일한 시점의 예측(확률벡터 포함). candles가 비어있으면 None.
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_regime_service.py -v`
Expected: PASS (전체 통과, 기존 5개 + 신규 3개 = 8개)

- [ ] **Step 5: 커밋**

```bash
git add backend/regime_service.py tests/test_regime_service.py
git commit -m "feat: evaluate_market에 현재 시점 예측(current_prediction) 필드 추가"
```

---

## Task 2: 프론트 타입 — `CurrentPrediction`/`RegimeBacktestResult` 확장

**Files:**
- Modify: `frontend/lib/types/eda.ts`

**Interfaces:**
- Produces: `CurrentPrediction`(타입), `RegimeBacktestResult.current_prediction:
  CurrentPrediction | null`

- [ ] **Step 1: 타입 추가/수정**

`frontend/lib/types/eda.ts`의 `RegimeBacktestResult` 인터페이스(현재 129~136번째 줄) 전체를
다음으로 교체:

```typescript
export interface CurrentPrediction {
  time: string;
  predicted_category: RegimeCategory | null;
  probs: Record<RegimeCategory, number> | null;
}

export interface RegimeBacktestResult {
  half_life_bars: number;
  n_bars: number;
  candles: RegimeCandle[];
  current_prediction: CurrentPrediction | null;
  confusion: Record<RegimeCategory, Record<RegimeCategory, number>>;
  actual_totals: Record<RegimeCategory, number>;
  correlation: number | null;
}
```

- [ ] **Step 2: 타입 검증**

Run(`frontend` 디렉터리에서): `npm run build`
Expected: 빌드 성공. `RegimeAccuracyReport.tsx`/`RegimeChart.tsx`/`RegimeDashboard.tsx`는
`current_prediction`을 아직 안 쓰지만, `RegimeBacktestResult`에 필드가 추가된 것만으로는
기존 코드가 깨지지 않는다(추가 필드는 기존 사용처의 구조분해/접근 방식과 무관).

- [ ] **Step 3: 커밋**

```bash
git add frontend/lib/types/eda.ts
git commit -m "feat: CurrentPrediction 타입 추가 및 RegimeBacktestResult 확장"
```

---

## Task 3: `RegimeCurrentPrediction` 컴포넌트 + 대시보드 연결

**Files:**
- Create: `frontend/components/RegimeCurrentPrediction.tsx`
- Modify: `frontend/components/RegimeDashboard.tsx`

**Interfaces:**
- Consumes: `RegimeBacktestResult`(Task 2), `formatTimeframe`(기존, `@/lib/format`)
- Produces: `RegimeCurrentPrediction`(컴포넌트, props: `{ result: RegimeBacktestResult;
  market: string; timeframe: string }`)

이 태스크는 자동 테스트가 없는 프론트 UI라, 브라우저로 직접 확인하는 것으로 검증한다.

- [ ] **Step 1: `RegimeCurrentPrediction.tsx` 작성**

`frontend/components/RegimeCurrentPrediction.tsx` 신규 생성:

```typescript
import type { RegimeBacktestResult, RegimeCategory } from '@/lib/types/eda';
import { formatTimeframe } from '@/lib/format';

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

interface RegimeCurrentPredictionProps {
  result: RegimeBacktestResult;
  market: string;
  timeframe: string;
}

export default function RegimeCurrentPrediction({ result, market, timeframe }: RegimeCurrentPredictionProps) {
  const { current_prediction, n_bars, half_life_bars } = result;

  if (!current_prediction) {
    return null;
  }

  const { time, predicted_category, probs } = current_prediction;
  const daysAhead = (n_bars / half_life_bars).toFixed(1);
  const formattedTime = new Date(time).toLocaleString('ko-KR', { dateStyle: 'medium', timeStyle: 'short' });

  return (
    <div className="rounded-xl border p-6 shadow-sm">
      <h2 className="mb-3 text-sm font-semibold">현재 예측</h2>
      {predicted_category === null || probs === null ? (
        <p className="text-sm text-muted-foreground">판단 불가(데이터 부족 — 워밍업 기간 이내)</p>
      ) : (
        <>
          <div className="mb-3 flex items-baseline gap-2">
            <span className="text-2xl font-bold">{predicted_category}</span>
            <span className="text-sm text-muted-foreground">
              확신도 {(probs[predicted_category] * 100).toFixed(1)}%
            </span>
          </div>
          <div className="mb-3 space-y-1.5">
            {CATEGORY_ORDER.map((label) => (
              <div key={label} className="flex items-center gap-2 text-xs">
                <span className="w-14 shrink-0 text-muted-foreground">{label}</span>
                <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
                  <div
                    className="h-full rounded-full"
                    style={{
                      width: `${(probs[label] * 100).toFixed(1)}%`,
                      backgroundColor: `var(${categoryVarName(label)})`,
                    }}
                  />
                </div>
                <span className="w-10 shrink-0 text-right tabular-nums">{(probs[label] * 100).toFixed(1)}%</span>
              </div>
            ))}
          </div>
        </>
      )}
      <p className="text-xs text-muted-foreground">
        {market} {formatTimeframe(timeframe)} 기준, {formattedTime} 봉 데이터. 약 {n_bars}봉({daysAhead}일)
        뒤까지의 추세를 예측합니다.
      </p>
    </div>
  );
}
```

- [ ] **Step 2: `RegimeDashboard.tsx`에 연결**

`frontend/components/RegimeDashboard.tsx` 전체를 다음으로 교체:

```typescript
'use client';

import { useState } from 'react';
import RegimeBacktestForm, { type RegimeBacktestParams } from '@/components/RegimeBacktestForm';
import RegimeCurrentPrediction from '@/components/RegimeCurrentPrediction';
import RegimeChart from '@/components/RegimeChart';
import RegimeAccuracyReport from '@/components/RegimeAccuracyReport';
import { ApiError } from '@/lib/api/client';
import { getRegimeBacktest } from '@/lib/api/eda';
import type { RegimeBacktestResult } from '@/lib/types/eda';

export default function RegimeDashboard() {
  const [result, setResult] = useState<RegimeBacktestResult | null>(null);
  const [market, setMarket] = useState('');
  const [timeframe, setTimeframe] = useState('minutes60');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(params: RegimeBacktestParams) {
    setSubmitting(true);
    setError(null);
    setResult(null);
    try {
      const data = await getRegimeBacktest(params);
      setMarket(params.market);
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
          <RegimeCurrentPrediction result={result} market={market} timeframe={timeframe} />
          <RegimeChart candles={result.candles} timeframe={timeframe} />
          <RegimeAccuracyReport report={result} />
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 3: 브라우저로 직접 확인**

Run(저장소 루트, 아직 안 떠 있으면): `python -m uvicorn backend.main:app --reload --port 8000`
Run(`frontend` 디렉터리, 별도 터미널, 아직 안 떠 있으면): `npm run dev`

브라우저로 `http://localhost:3000/regime` 접속, 코인(예: KRW-DOGE) + 봉타입 + 기간(종료일을
오늘로) 선택 후 조회.

Expected:
- 폼 바로 아래, 차트 위에 "현재 예측" 카드가 보인다.
- 예측 카테고리(큰 글씨) + 확신도 %가 표시되고, 5개 카테고리 확률 막대의 길이 합이
  시각적으로 100%에 가깝다(막대 옆 숫자 5개를 더하면 100%에 근접해야 함).
- 카드에 표시된 예측 카테고리가 차트 맨 오른쪽(가장 최근) 캔들의 색과 같은 카테고리인지
  범례와 대조해 확인한다.
- 안내문에 마켓/봉타입/봉시각/"약 N봉(M일) 뒤까지" 문구가 채워져 있다(예: 1시간봉이면
  약 2.5일).
- 조회기간이 워밍업도 못 채울 만큼 짧으면(예: 시작일=종료일 하루 전) "판단 불가(데이터
  부족 — 워밍업 기간 이내)"가 뜨고 화면이 깨지지 않는다.
- 라이트/다크 테마 모두에서 확률 막대 색이 깨지지 않는지 확인한다.

- [ ] **Step 4: 커밋**

```bash
git add frontend/components/RegimeCurrentPrediction.tsx frontend/components/RegimeDashboard.tsx
git commit -m "feat: 현재 시점 예측 카드(RegimeCurrentPrediction) 추가"
```

---

## Task 4: Confusion Matrix 행/열 반전 + 히트맵 + 2단 레이아웃

**Files:**
- Modify: `frontend/components/RegimeAccuracyReport.tsx`

**Interfaces:**
- Consumes: `RegimeBacktestResult.confusion`/`actual_totals`(기존, 데이터 구조 변경 없음)

- [ ] **Step 1: `RegimeAccuracyReport.tsx` 재구성**

`frontend/components/RegimeAccuracyReport.tsx` 전체를 다음으로 교체:

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

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <div>
          <h2 className="mb-2 text-sm font-semibold">Confusion Matrix (행=실제, 열=예측)</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-muted-foreground">
                  <th className="py-1.5">실제\예측</th>
                  {CATEGORY_ORDER.map((label) => (
                    <th key={label} className="py-1.5 text-right">
                      {label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {CATEGORY_ORDER.map((actual) => {
                  const rowTotal = actual_totals[actual];
                  return (
                    <tr key={actual} className="border-b last:border-0">
                      <td className="py-1.5 font-medium">{actual}</td>
                      {CATEGORY_ORDER.map((predicted) => {
                        const value = confusion[predicted][actual];
                        const ratio = rowTotal ? value / rowTotal : 0;
                        return (
                          <td
                            key={predicted}
                            className="py-1.5 text-right tabular-nums"
                            style={{ backgroundColor: `oklch(0.55 0.2 255 / ${(ratio * 0.5).toFixed(2)})` }}
                          >
                            {value}
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
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
    </div>
  );
}
```

- [ ] **Step 2: 브라우저로 직접 확인**

백엔드/프론트 개발 서버가 이미 떠 있지 않다면 Task 3 Step 3와 동일하게 실행.

브라우저로 `http://localhost:3000/regime` 접속, 코인/봉타입/기간을 조회(가능하면 이전에
캡처한 KRW-DOGE 1시간봉 7/1~8/23 같은 조합으로 재현).

Expected:
- Confusion Matrix 헤더가 "실제\예측"으로 바뀌고, 행 레이블(왼쪽)이 실제 카테고리, 열
  레이블(위)이 예측 카테고리다.
- 특정 행(예: "실제=급상승")의 셀 값을 전부 더하면 "실제 카테고리 분포" 표의 해당
  카테고리 건수(예: 141)와 일치한다.
- 셀 배경색이 그 행 안에서 값이 클수록 진하게 칠해져 있다(예: 실제=횡보 행에서 예측=횡보
  칸이 가장 진해야 함 — 스크린샷 기준 3398/5208 ≈ 65%로 그 행에서 가장 큰 비율).
- 레이아웃이 데스크톱 폭에서는 Confusion Matrix(왼쪽) + 실제 카테고리 분포(오른쪽) 2단으로
  나란히 보이고, 모바일 폭(브라우저 창을 좁혀서 확인)에서는 세로로 쌓인다.
- "카테고리별 적중률" 표와 상관계수 값은 이전과 동일하게 렌더된다(이번 변경과 무관 —
  회귀 없는지만 확인).

- [ ] **Step 3: 커밋**

```bash
git add frontend/components/RegimeAccuracyReport.tsx
git commit -m "feat: Confusion Matrix 행/열 반전(실제=행/예측=열) + 히트맵 + 2단 레이아웃"
```

---

## Self-Review 결과

- **스펙 커버리지**: 현재 예측 카드(백엔드 필드 Task 1 + 프론트 컴포넌트 Task 3),
  Confusion Matrix 행/열 반전+히트맵+레이아웃(Task 4), 타입 확장(Task 2) — 스펙의 "설계
  ①"/"설계 ②" 섹션 전부에 대응하는 태스크가 있다. 스펙이 명시한 비범위(N_MULTIPLIER
  조절, 엔진 수정, 카테고리별 적중률 표 변경, 공용 유틸 추출)는 의도적으로 태스크 없음.
- **플레이스홀더 스캔**: TBD/TODO 없음. 모든 스텝에 완전한 코드가 있다.
- **타입 일관성**: `current_prediction`의 키(`time`, `predicted_category`, `probs`)가
  Task 1(백엔드 반환값) → Task 2(`CurrentPrediction` 타입) → Task 3(컴포넌트 구조분해)까지
  드리프트 없이 동일하다. `CATEGORY_ORDER`/`categoryVarName`은 Task 3에서
  `RegimeChart.tsx`/`RegimeAccuracyReport.tsx`와 정확히 같은 내용으로 재정의된다(의도된
  중복, Global Constraints에 명시). `confusion[predicted][actual]` 접근 순서는 Task 4에서
  데이터 구조를 안 바꾸고 순회만 바꿨으므로 백엔드와 프론트 계약이 깨지지 않는다.
