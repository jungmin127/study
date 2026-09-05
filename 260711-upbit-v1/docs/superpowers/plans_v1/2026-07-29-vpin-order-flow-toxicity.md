# VPIN(주문흐름 독성도) 지표 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `docs/superpowers/specs_v1/2026-07-29-vpin-order-flow-toxicity-design.md`에서 설계한 대로,
거래량 버킷 기반 VPIN(Volume-Synchronized Probability of Informed Trading, Bulk Volume
Classification 방식)을 조건 빌더에 `VPIN` 지표로 추가한다.

**Architecture:** `BTC_CORRELATION`/`USDT_CORRELATION`(`engine/indicators/market.py`)과 같은 패턴 —
대상 코인 자신의 `close`/`volume`만 쓰는 커스텀 `bt.Indicator`(`VolumeBarVPIN`)로 backtrader 안에서
라이브 계산한다. 외부 데이터도, 다른 마켓 캔들도, 백엔드 병합 로직도 필요 없다 — `backend/main.py`는
카탈로그 등록 한 줄만 추가한다.

**Tech Stack:** Python 3.11, backtrader, `statistics`(표준 라이브러리, `NormalDist.cdf`로 표준정규분포
누적함수 계산 — scipy 등 새 의존성 불필요), pytest / Next.js 14, TypeScript.

## Global Constraints

- 기존 pytest 테스트는 계속 100% 통과해야 한다.
- `npx tsc --noEmit` (frontend)이 항상 깨끗해야 한다.
- 카탈로그(백엔드) ↔ 지표 가이드 탭(프론트) ↔ 조건 빌더 카테고리 상수는 항상 같이 갱신한다. 카테고리는
  기존 "거래량"을 재사용하므로 `frontend/lib/indicator-categories.ts` 수정은 필요 없다.
- 파라미터는 `period` 하나(기본값 20)로 버킷 크기 계산과 VPIN 평균 창을 동시에 제어한다.
- `AUX_MARKET_INDICATORS`(`engine/condition_tree.py`), `_OPTIONAL_LINE_CANDIDATES`(`engine/runner.py`)
  둘 다 수정하지 않는다 — VPIN은 보조 마켓도 외부 데이터 라인도 필요 없다.
- 커밋은 Task 단위로 작게 나눠서 한다.

---

## Task 1: VPIN 지표 구현 및 등록

**Files:**
- Modify: `engine/indicators/volume.py`
- Modify: `engine/indicators/__init__.py`
- Test: `tests/test_indicators.py` (append)

**Interfaces:**
- Produces: `VolumeBarVPIN(bt.Indicator)`(단일 라인 `vpin`, 파라미터 `period`, 기본값 20).
  `create_vpin(data, **params) -> bt.Indicator`. `INDICATOR_FACTORY["VPIN"]`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_indicators.py` 파일 상단(`import statistics` 다음 줄)에 `import pytest` 추가 — 이 파일은
지금까지 `pytest.approx`를 쓴 적이 없어 임포트가 안 돼 있다(`statistics`/`bt`/`pd`/`INDICATOR_FACTORY`는
이미 임포트돼 있어 추가로 손댈 것 없음):
```python
import statistics

import backtrader as bt
import pandas as pd
import pytest

from engine.condition_tree import get_indicator_value
from engine.indicators import INDICATOR_FACTORY
from tests.signal_fixtures import make_oscillating_df
from engine.runner import build_data_feed_class, run_backtest
```

파일 끝에 추가:
```python
def _run_vpin_probe(volumes: list[float], closes: list[float], period: int) -> list[float]:
    """명시적인 volume/close 시퀀스로 VPIN 버킷 경계를 손으로 검증할 수 있게 만드는 전용 하네스.
    make_oscillating_df 기반 _run_probe/_run_probe_with_aux와 달리, 매 봉의 vpin 값 전체 이력을
    리스트로 반환한다(버킷이 몇 번째 봉에서 완성되는지까지 검증해야 하므로 마지막 값만으론 부족)."""
    n = len(volumes)
    idx = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    df = pd.DataFrame({
        "candle_time": idx,
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": volumes,
    })
    df_bt = df.set_index("candle_time")
    df_bt.index = df_bt.index.tz_localize(None)

    class _VpinProbeStrategy(bt.Strategy):
        def __init__(self):
            self.probe = INDICATOR_FACTORY["VPIN"](self.data, period=period)
            self.seen_values: list[float] = []

        def next(self):
            self.seen_values.append(float(self.probe[0]))

    cerebro = bt.Cerebro()
    cerebro.adddata(bt.feeds.PandasData(dataname=df_bt, openinterest=-1))
    cerebro.addstrategy(_VpinProbeStrategy)
    results = cerebro.run()
    return results[0].seen_values


def test_vpin_produces_nan_during_warmup_before_enough_buckets():
    # period=2: 최근 2봉 평균 거래량이 버킷 목표치. 처음 두 완성 버킷(2번째·4번째 봉)까지는
    # 불균형 비율이 1개(또는 0개)뿐이라 아직 평균 낼 period(2)개가 안 모여 NaN이어야 한다.
    volumes = [10, 10, 2, 2]
    closes = [100, 100, 100, 100]
    values = _run_vpin_probe(volumes, closes, period=2)
    assert all(v != v for v in values), "버킷이 period개 미만일 때는 전부 NaN이어야 함(v != v는 NaN 체크)"


def test_vpin_matches_hand_traced_bucket_sequence():
    # 손으로 추적 가능한 8봉 시퀀스(period=2). 실제 알고리즘을 파이썬으로 직접 재현해 검증한
    # 기대값이며(스펙 문서의 알고리즘과 동일), 봉별 기대 결과:
    #   1~4봉: NaN(워밍업, 완성 버킷이 아직 1개뿐이거나 없음)
    #   5봉: 0.0 (두 번째 유효 불균형 비율까지 모여 평균 0.0)
    #   6봉: 0.0 (거래량 1 < 목표 1.5라 버킷 미완성 → 5봉 값을 그대로 이어붙임, forward-fill)
    #   7봉: 0.0 (버킷은 새로 완성되지만 가격 변화가 0이라 우연히 같은 값)
    #   8봉: 아래에서 statistics로 직접 계산한 기대값(가격이 5 상승하는 버킷)
    volumes = [10, 10, 2, 2, 2, 1, 1, 10]
    closes = [100, 100, 100, 100, 100, 100, 100, 105]
    values = _run_vpin_probe(volumes, closes, period=2)

    assert all(v != v for v in values[:4])
    assert values[4] == pytest.approx(0.0)
    assert values[5] == values[4], "버킷 미완성 봉(6번째)은 직전 값을 그대로 이어붙여야 함(forward-fill)"
    assert values[6] == pytest.approx(0.0)

    sigma = statistics.stdev([0.0, 5.0])
    z = 5.0 / sigma
    buy_ratio = statistics.NormalDist().cdf(z)
    imbalance_bucket_8 = abs(2 * buy_ratio - 1)
    expected_bar8 = imbalance_bucket_8 / 2  # 직전 불균형(0.0)과 평균
    assert values[7] == pytest.approx(expected_bar8)


def test_vpin_handles_zero_price_variance_without_crashing():
    # 가격이 한 번도 안 바뀌면 버킷 가격변화 표준편차가 0 — ZeroDivisionError 없이 z=0(매수/매도
    # 50:50, 불균형 0)으로 처리되어야 한다.
    volumes = [10] * 10
    closes = [100.0] * 10
    values = _run_vpin_probe(volumes, closes, period=2)
    non_nan = [v for v in values if v == v]
    assert non_nan, "버킷이 완성되는 구간이 있어야 함"
    assert all(v == pytest.approx(0.0) for v in non_nan)


def test_vpin_registered_in_factory_and_runs_on_default_oscillating_data():
    # 다른 지표들과 동일한 스모크 테스트 경로(_run_probe, make_oscillating_df)로도 크래시 없이
    # 값을 내야 한다 — VPIN은 aux 라인이 필요 없으므로 _NEEDS_EXTRA_LINE에 추가하지 않는다.
    values = _run_probe("VPIN", {})
    assert len(values) > 0
```

같은 파일 상단에 `import statistics`가 이미 없다면 추가(현재 파일 1번째 줄이 `import statistics`인지
확인 — `market.py`의 `RollingCorrelation` 임포트 목적이 아니라 이 테스트 파일 자체에서
`statistics.stdev`/`statistics.NormalDist`를 직접 쓰므로 필요).

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `pytest tests/test_indicators.py -k vpin -v`
Expected: FAIL — `KeyError: 'VPIN'`(아직 `INDICATOR_FACTORY`에 없음)

- [ ] **Step 3: 최소 구현 작성**

`engine/indicators/volume.py` 파일 상단 import 줄(`import backtrader as bt`) 다음에 추가:
```python
import statistics
from collections import deque
```

파일 끝(`create_trade_value_sma` 다음)에 추가:
```python
class VolumeBarVPIN(bt.Indicator):
    """거래량 버킷(volume bar) 기반 VPIN. Easley/López de Prado/O'Hara의 Bulk Volume
    Classification(BVC, 2012)을 따른다 — 틱 단위 매수/매도 라벨이 필요 없고, 캔들
    종가·거래량만으로 버킷별 매수/매도 비율을 확률적으로 추정한다."""

    lines = ("vpin",)
    params = (("period", 20),)

    def __init__(self) -> None:
        period = self.p.period
        self._recent_volumes: deque = deque(maxlen=period)
        self._bucket_cum_volume = 0.0
        self._last_bucket_close: float | None = None
        self._bucket_deltas: deque = deque(maxlen=period)
        self._bucket_imbalance_ratios: deque = deque(maxlen=period)

    def _accumulate(self) -> None:
        """이번 봉을 현재 버킷에 누적하고, 목표 거래량에 도달했으면 버킷을 완성해
        BVC로 매수/매도 불균형 비율을 계산·기록한다. next()/nextstart() 공통 로직."""
        period = self.p.period
        self._recent_volumes.append(self.data.volume[0])
        self._bucket_cum_volume += self.data.volume[0]

        target = (
            statistics.mean(self._recent_volumes)
            if len(self._recent_volumes) == period
            else None
        )
        if target is None or self._bucket_cum_volume < target:
            return

        bucket_close = self.data.close[0]
        bucket_volume = self._bucket_cum_volume

        if self._last_bucket_close is not None:
            delta = bucket_close - self._last_bucket_close
            self._bucket_deltas.append(delta)
            sigma = statistics.stdev(self._bucket_deltas) if len(self._bucket_deltas) >= 2 else 0.0
            z = delta / sigma if sigma > 0 else 0.0
            buy_ratio = statistics.NormalDist().cdf(z)
            buy_volume = bucket_volume * buy_ratio
            sell_volume = bucket_volume - buy_volume
            imbalance_ratio = abs(buy_volume - sell_volume) / bucket_volume if bucket_volume else 0.0
            self._bucket_imbalance_ratios.append(imbalance_ratio)

        self._last_bucket_close = bucket_close
        self._bucket_cum_volume = 0.0

    def nextstart(self) -> None:
        # next()가 이 지표에 대해 처음 호출되는 시점에도 이 지표 자신의 vpin 라인은 아직
        # 한 번도 기록된 적이 없어 vpin[-1]이 정의돼 있지 않다 — OBV의 nextstart()와 같은
        # 이유로, 첫 호출만 [-1] 참조 없이 따로 처리한다.
        self._accumulate()
        if len(self._bucket_imbalance_ratios) == self.p.period:
            self.lines.vpin[0] = statistics.mean(self._bucket_imbalance_ratios)
        else:
            self.lines.vpin[0] = float("nan")

    def next(self) -> None:
        self._accumulate()
        if len(self._bucket_imbalance_ratios) == self.p.period:
            self.lines.vpin[0] = statistics.mean(self._bucket_imbalance_ratios)
        else:
            self.lines.vpin[0] = self.lines.vpin[-1]


def create_vpin(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 20))
    return VolumeBarVPIN(data, period=period)
```

`engine/indicators/__init__.py` — import 줄을:
```python
from .volume import create_obv, create_trade_value, create_trade_value_sma, create_volume_sma
```
다음으로 교체:
```python
from .volume import create_obv, create_trade_value, create_trade_value_sma, create_volume_sma, create_vpin
```
`INDICATOR_FACTORY` dict에 추가(`"VOLUME_SMA": create_volume_sma,` 다음 줄):
```python
    "VPIN": create_vpin,
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_indicators.py -v`
Expected: 기존 테스트 전부 PASS + 신규 4개 PASS

- [ ] **Step 5: 커밋**

```bash
git add engine/indicators/volume.py engine/indicators/__init__.py tests/test_indicators.py
git commit -m "feat: add VolumeBarVPIN indicator (Bulk Volume Classification)"
```

---

## Task 2: 카탈로그 등록

**Files:**
- Modify: `backend/main.py` (`INDICATOR_CATALOG`)

**Interfaces:**
- Consumes: Task 1의 `INDICATOR_FACTORY["VPIN"]`.
- Produces: `GET /api/v1/indicators/catalog` 응답에 `VPIN` 항목 추가(카테고리 `"거래량"` 재사용).

- [ ] **Step 1: 실패하는 테스트 확인**

기존 테스트 `test_get_indicator_catalog_covers_all_registered_indicators`(수정 없이 재사용)가
Task 1에서 `INDICATOR_FACTORY`에 `VPIN`이 생겼는데 카탈로그엔 없는 지금 시점에 저절로 실패한다.

Run: `pytest tests/test_backend.py -k test_get_indicator_catalog_covers_all_registered_indicators -v`
Expected: FAIL — `catalog_values`에 `VPIN`이 빠져 있어 set 비교 실패

- [ ] **Step 2: (Step 1에서 이미 실패 확인함 — 별도 실행 불필요)**

- [ ] **Step 3: 최소 구현 작성**

`backend/main.py`의 `INDICATOR_CATALOG` 리스트에서 `"VOLUME_SMA"` 항목 바로 뒤에 추가:
```python
    {
        "value": "VPIN", "label": "VPIN (주문흐름 독성도)", "category": "거래량",
        "params": [{"key": "period", "label": "기간", "default": 20}],
        "description": "거래량 버킷 단위로 매수/매도 주문 불균형을 추정한 값(0~1)입니다. 1에 가까울수록 그 구간 거래가 한쪽(매수 또는 매도)으로 강하게 쏠렸다는 뜻으로, 급등락 직전의 정보거래(독성 주문흐름) 징후로 해석합니다. 틱 데이터가 아니라 캔들 가격 변화로 매수/매도 비율을 확률적으로 추정하는 방식(Bulk Volume Classification)을 씁니다.",
        "example": "period=20, 연산자 >, 임계값 0.4면: 최근 20개 거래량 버킷 동안 주문흐름 불균형이 뚜렷한(변동성 폭발 전조로 흔히 해석되는) 구간을 포착합니다.",
    },
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_backend.py -v`
Expected: 전부 PASS

Run: `pytest tests/ -v`
Expected: 전체 스위트 PASS

- [ ] **Step 5: 커밋**

```bash
git add backend/main.py
git commit -m "feat: register VPIN in the catalog under 거래량 category"
```

---

## Task 3: 조건 빌더 프론트엔드 (threshold 추천값)

**Files:**
- Modify: `frontend/components/StrategyConditionBuilder.tsx`

**Interfaces:**
- Consumes: 백엔드 카탈로그의 `category: "거래량"`, `value: "VPIN"`(Task 2).

- [ ] **Step 1~2: (프론트 로직 테스트는 이 저장소에 별도 단위테스트 인프라가 없음 — 기존 컨벤션대로
      Step 3 구현 후 `tsc`+Playwright 수동 검증으로 대체한다.)**

- [ ] **Step 3: 구현**

`frontend/components/StrategyConditionBuilder.tsx`의 `OSCILLATOR_BOUNDS`(현재 `RSI`/`STOCH_K`/
`STOCH_D`/`CCI`/`WILLIAMS_R`/`FEAR_GREED_CMC` 6개)에 추가:
```typescript
const OSCILLATOR_BOUNDS: Record<string, { low: number; high: number }> = {
  RSI: { low: 30, high: 70 },
  STOCH_K: { low: 20, high: 80 },
  STOCH_D: { low: 20, high: 80 },
  CCI: { low: -100, high: 100 },
  WILLIAMS_R: { low: -80, high: -20 },
  FEAR_GREED_CMC: { low: 20, high: 80 },
  VPIN: { low: 0.2, high: 0.4 },
};
```
(`recommendedThreshold()`가 `OSCILLATOR_BOUNDS`에 있는 지표는 연산자에 따라 `low`/`high`/중간값을
자동으로 추천하는 기존 로직을 그대로 타므로, 이 한 줄 추가만으로 `<`/`<=`면 0.2, `>`/`>=`면 0.4가
채워진다 — VPIN은 0~1 범위 오실레이터라 이 패턴에 그대로 맞는다.)

- [ ] **Step 4: 확인**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

브라우저(Playwright)에서 `/`(조건 빌더)의 "거래량" 카테고리에 "VPIN (주문흐름 독성도)"가 뜨는지,
선택 시 연산자를 `<`로 두면 threshold가 0.2로, `>`로 바꾸면 0.4로 자동 채워지는지 확인.

- [ ] **Step 5: 커밋**

```bash
git add frontend/components/StrategyConditionBuilder.tsx
git commit -m "feat: add VPIN threshold recommendation to condition builder"
```

---

## Task 4: 지표 가이드 탭 콘텐츠

**Files:**
- Modify: `frontend/lib/guide-sample-data.ts`
- Modify: `frontend/lib/indicator-guide.ts`
- Modify: `frontend/lib/indicator-example-builder.ts`

**Interfaces:**
- Produces: `guide-sample-data.ts`에 `SAMPLE_VPIN: number[]`(길이 60, `SAMPLE_BARS`와 같은 `bar`
  인덱스에 대응하는 0~1 합성 시계열) 추가.

- [ ] **Step 1~2: (지표 가이드 탭도 별도 단위테스트가 없는 순수 프레젠테이션 레이어 — 기존 컨벤션대로
      `tsc`+Playwright로 검증한다. Step 3 이후로 진행.)**

- [ ] **Step 3: 구현**

`frontend/lib/guide-sample-data.ts`의 `buildKoreaPremiumSeries` 함수 뒤, `const closeSeries =
buildCloseSeries();` 줄 앞에 추가:
```typescript
function buildVpinSeries(): number[] {
  const values: number[] = [];
  for (let i = 0; i < TOTAL_BARS; i++) {
    const wave = 0.2 * Math.sin((2 * Math.PI * i) / 15) + 0.1 * Math.sin((2 * Math.PI * i) / 5);
    values.push(Math.max(0, Math.min(1, Math.round((0.25 + wave) * 100) / 100)));
  }
  return values;
}
```
`const koreaPremiumSeries = buildKoreaPremiumSeries();` 다음 줄에 추가:
```typescript
const vpinSeries = buildVpinSeries();
```
파일 끝(`SAMPLE_KOREA_PREMIUM` export 다음)에 추가:
```typescript
/** VPIN은 코인 캔들과 무관한 고정 시계열이라 SAMPLE_BARS의 bar 인덱스에 맞춰 별도 배열로 둔다. */
export const SAMPLE_VPIN: number[] = vpinSeries;
```

`frontend/lib/indicator-guide.ts`의 `INDICATOR_GUIDE` 객체에서 `KOREA_PREMIUM` 항목(파일 마지막 항목)
바로 뒤, 객체를 닫는 `};` 앞에 추가:
```typescript
  VPIN: {
    meaning: '거래량 버킷(누적 거래량이 일정량에 도달할 때마다 하나씩 완성되는 구간) 단위로, 매수 주도 거래와 매도 주도 거래의 불균형 정도를 0~1 값으로 나타냅니다. 1에 가까울수록 그 구간 거래가 한쪽으로 강하게 쏠렸다는 뜻이며, 급등락 직전의 정보거래(독성 주문흐름) 징후로 흔히 해석합니다.',
    params: [{ key: 'period', label: '기간', default: 20 }],
    formula: '체결 틱의 매수/매도 라벨이 아니라, 거래량 버킷의 가격 변화를 표준화(z)해 표준정규분포 누적함수(Φ)에 넣어 매수 비율을 확률적으로 추정합니다(Bulk Volume Classification). 매수추정량 = 버킷거래량 × Φ(z), 매도추정량 = 버킷거래량 − 매수추정량. 최근 period개 버킷의 |매수추정량−매도추정량|/버킷거래량 평균이 VPIN 값입니다.',
    thresholdExample: '값은 0~1 범위입니다. 예: period=20, 임계값 0.4, 연산자 ">"면 최근 20개 거래량 버킷 동안 주문흐름 불균형이 뚜렷한 구간을 포착합니다.',
    usage: '변동성 폭발이나 급등락 직전 국면을 포착하는 선행 신호로 흔히 씁니다. 값 자체는 방향(매수/매도 중 어느 쪽인지)을 말해주지 않고 "얼마나 쏠렸는지"만 나타내므로, RSI나 이동평균 같은 방향성 지표와 함께 AND로 묶어 쓰는 경우가 많습니다.',
  },
```

`frontend/lib/indicator-example-builder.ts`의 import 줄을:
```typescript
import { SAMPLE_BARS, SAMPLE_BTC, SAMPLE_FEAR_GREED, SAMPLE_KOREA_PREMIUM, type SampleBar } from '@/lib/guide-sample-data';
```
다음으로 교체:
```typescript
import { SAMPLE_BARS, SAMPLE_BTC, SAMPLE_FEAR_GREED, SAMPLE_KOREA_PREMIUM, SAMPLE_VPIN, type SampleBar } from '@/lib/guide-sample-data';
```
`buildGuideExample` switch문의 `case 'KOREA_PREMIUM': { ... }` 블록이 끝나는 닫는 중괄호(`}`) 바로 뒤,
`case 'STOP_LOSS_PCT':` 시작 줄 바로 앞에 추가:
```typescript
    case 'VPIN': {
      const rows = windowFrom(0, 7).map((bar, i) => ({
        bar: bar.bar,
        cells: { vpin: n(SAMPLE_VPIN[i]) },
      }));
      const gauge = gaugeExample(
        SAMPLE_VPIN,
        0,
        1,
        [
          { from: 0, to: 0.2, color: '#94a3b8', label: '평온(<0.2)' },
          { from: 0.2, to: 0.4, color: '#f59e0b', label: '주의' },
          { from: 0.4, to: 1, color: '#ef4444', label: '독성 흐름(>0.4)' },
        ],
        'VPIN'
      );
      return {
        columns: [{ key: 'vpin', label: 'VPIN' }],
        rows,
        chart: gauge.chart,
      };
    }
```

- [ ] **Step 4: 확인**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

Playwright로 `/guide`를 열어 "거래량" 중분류에 "VPIN (주문흐름 독성도)"가 뜨는지, 클릭 시 표 + 게이지
차트(0~1, 0.2/0.4 구간 색상 구분)가 정상 렌더되는지 확인.

- [ ] **Step 5: 커밋**

```bash
git add frontend/lib/guide-sample-data.ts frontend/lib/indicator-guide.ts frontend/lib/indicator-example-builder.ts
git commit -m "feat: add VPIN to the indicator guide tab"
```

---

## 이 플랜에 포함하지 않은 것

`docs/superpowers/specs_v1/2026-07-29-vpin-order-flow-toxicity-design.md`의 "이 스펙에 포함하지 않은 것"
절과 동일한 이유로 범위 밖이다.

- **체결강도(Volume Power)**: 드롭됨([[upbit-v1-external-indicator-roadmap]] 참고, 업비트 틱 조회
  API 7일 제한).
- **Volume Profile(VPVR)**: 로드맵상 다음 순서, 별도 스펙 필요.
- **VPIN의 실전 예측력 검증**: 이 플랜은 BVC 방법론을 올바르게 구현하는 것까지만 다룬다.

## Verification (전체)

- `pytest tests/ -v` — 전체 스위트 그린(기존 스위트 + 이번 플랜 신규 4개).
- `cd frontend && npx tsc --noEmit` — 클린.
- Playwright: `/`에서 "거래량" 카테고리에 "VPIN (주문흐름 독성도)"가 뜨고, 실제 조건으로 백테스트
  1건을 끝까지 실행해 결과 화면까지 나오는지. `/guide`에서 신규 항목이 표+게이지 차트와 함께
  렌더되는지.
- 백엔드는 코드 수정마다 재시작 필요(`uvicorn --reload`가 이 저장소에서 간헐적으로 안 먹는 이슈가
  기존에 있었음 — 반드시 수동 재시작 후 확인). 단, Task 1은 백엔드 코드를 건드리지 않으므로 재시작
  불필요 — Task 2부터 재시작이 필요하다.
