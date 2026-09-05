# B 레이어(파생 지표) 구현 Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `docs/superpowers/specs_v1/2026-07-27-strategy-source-classification.md`에서 B 레이어로 분류된
7개 후보 중, 바로 실행 가능한 5개(피보나치 되돌림, Pivot Points, BTC 상관계수, 테더 상관계수)를 조건
빌더에 새 지표로 추가한다. 나머지 2개(Volume Profile, 체결강도/VPIN — 별도 리서치·설계 필요)는 이 플랜의
범위 밖이며 문서 끝의 "이 플랜에 포함하지 않은 것" 절에서 이유를 밝힌다.

**Architecture:** 기존 `engine/indicators/*.py` + `INDICATOR_FACTORY` + `INDICATOR_CATALOG` 파이프라인을
그대로 재사용한다(파이프라인 자체는 안 건드림 — 새 파일 하나, 기존 파일에 항목 추가). 다만 BTC/테더
상관계수는 "보조 마켓 캔들을 병합해야 하는" 지표라서, 지금 `MARKET_TREND` 하나에만 하드코딩된 "KRW-BTC
전용 병합" 메커니즘(`engine/condition_tree.py::requires_market_data`, `engine/runner.py`의 조합별
`PandasData` 서브클래스 4개, `backend/main.py`의 KRW-BTC 하드코딩 블록)을 **여러 보조 마켓을 동시에
지원하도록 일반화**한다. 이 일반화가 이 플랜에서 가장 리스크가 큰 부분이라 Part 2로 분리했다.

**Tech Stack:** Python 3.11, FastAPI, backtrader, pytest / Next.js 14 App Router, TypeScript, recharts.

## Global Constraints
- 기존 168개 pytest 테스트는 계속 100% 통과해야 한다.
- `npx tsc --noEmit` (frontend)이 항상 깨끗해야 한다.
- 카탈로그(백엔드) ↔ 지표 가이드 탭(프론트) ↔ 조건 빌더 카테고리 상수는 항상 같이 갱신한다(이전
  세션에서 거래대금 카테고리를 추가할 때 확립된 컨벤션).
- 새 backtrader 커스텀 지표는 기존 `engine/indicators/volume.py`의 `OBV`(`next()` 기반 커스텀
  `bt.Indicator`) 패턴을 따른다 — 불확실한 backtrader 내장 지표 존재 여부에 기대지 않는다.
- 커밋은 Task 단위(또는 Task 내 Step 단위)로 작게 나눠서 한다.

---

# Part 1: 피보나치 되돌림 + Pivot Points (새 카테고리 "가격대")

## Task 1: 피보나치 되돌림 지표 3개

**Files:**
- Create: `engine/indicators/price_levels.py`
- Test: `tests/test_indicators.py` (append)

**Interfaces:**
- Produces: `create_fib_382(data, **params)`, `create_fib_500(data, **params)`, `create_fib_618(data, **params)`
  — 각각 `bt.Indicator`(LinesOperation)를 반환. `params["period"]`(기본 20)를 씀.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_indicators.py` 끝에 추가:
```python
def test_fib_382_matches_manual_swing_calculation():
    values = _run_probe("FIB_382", {"period": 5})
    df = make_oscillating_df()
    hh = df["high"].rolling(5).max().iloc[-1]
    ll = df["low"].rolling(5).min().iloc[-1]
    manual = hh - (hh - ll) * 0.382
    assert abs(values[-1] - manual) < 1e-6


def test_fib_618_matches_manual_swing_calculation():
    values = _run_probe("FIB_618", {"period": 5})
    df = make_oscillating_df()
    hh = df["high"].rolling(5).max().iloc[-1]
    ll = df["low"].rolling(5).min().iloc[-1]
    manual = hh - (hh - ll) * 0.618
    assert abs(values[-1] - manual) < 1e-6
```
(`FIB_500`은 Step 4에서 `INDICATOR_FACTORY`에 등록하면 `test_all_registered_indicators_produce_values`가
자동으로 커버하므로 별도 수동계산 테스트는 382/618 두 개만 — 세 함수 모두 같은 수식 패턴이라 가운데
레벨까지 중복 테스트할 필요는 없다, YAGNI.)

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `pytest tests/test_indicators.py -k fib -v`
Expected: FAIL — `KeyError: 'FIB_382'` (아직 `INDICATOR_FACTORY`에 없음)

- [ ] **Step 3: 최소 구현 작성**

`engine/indicators/price_levels.py` (신규):
```python
"""
engine/indicators/price_levels.py

가격대(지지/저항) 계열 지표 — 피보나치 되돌림, Pivot Points.
둘 다 캔들의 고가/저가/종가만으로 계산되는 순수 파생 지표라 보조 마켓·틱 데이터가 필요 없다.
"""
from __future__ import annotations

import backtrader as bt


def create_fib_382(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 20))
    hh = bt.indicators.Highest(data.high, period=period)
    ll = bt.indicators.Lowest(data.low, period=period)
    return hh - (hh - ll) * 0.382


def create_fib_500(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 20))
    hh = bt.indicators.Highest(data.high, period=period)
    ll = bt.indicators.Lowest(data.low, period=period)
    return hh - (hh - ll) * 0.5


def create_fib_618(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 20))
    hh = bt.indicators.Highest(data.high, period=period)
    ll = bt.indicators.Lowest(data.low, period=period)
    return hh - (hh - ll) * 0.618
```

`engine/indicators/__init__.py` — import 줄에 추가:
```python
from .price_levels import create_fib_382, create_fib_500, create_fib_618
```
`INDICATOR_FACTORY` dict에 추가(`"MOMENTUM_PCT": create_momentum_pct,` 다음 줄):
```python
    "FIB_382": create_fib_382,
    "FIB_500": create_fib_500,
    "FIB_618": create_fib_618,
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_indicators.py -k fib -v`
Expected: PASS (2 tests)

Run: `pytest tests/test_indicators.py -v`
Expected: 기존 테스트 전부 PASS + 새 FIB 관련 항목이 `test_all_registered_indicators_produce_values` 루프에도 포함돼 통과

- [ ] **Step 5: 커밋**

```bash
git add engine/indicators/price_levels.py engine/indicators/__init__.py tests/test_indicators.py
git commit -m "feat: add Fibonacci retracement indicators (FIB_382/500/618)"
```

---

## Task 2: Pivot Points 지표 3개 (P/R1/S1)

**Files:**
- Modify: `engine/indicators/price_levels.py`
- Modify: `engine/condition_tree.py` (`get_indicator_value` 함수)
- Test: `tests/test_indicators.py` (append)
- Test: `tests/test_condition_tree.py` (append — 다중 라인 dispatch 확인)

**Interfaces:**
- Consumes: 없음(캔들 고/저/종가만 사용).
- Produces: `create_pivot_p(data, **params)`/`create_pivot_r1`/`create_pivot_s1` — 셋 다 같은
  `PivotPoints` 커스텀 `bt.Indicator`(`lines = ("p", "r1", "s1")`)의 새 인스턴스를 반환. `condition_tree.py`의
  `get_indicator_value()`가 지표 이름별로 `obj.p[0]`/`obj.r1[0]`/`obj.s1[0]`을 골라 읽는다(`BB_upper`/
  `BB_middle`/`BB_lower`가 이미 이 패턴을 쓰고 있음).

전통적 Pivot Point는 "전일" 고/저/종가를 쓰지만, 이 앱은 캔들을 timeframe 하나로만 받아오는 구조라 별도
타임프레임(항상 일봉) 추가 조회는 이 플랜 범위 밖이다. **v1은 "직전 1봉" 기준**으로 계산한다(timeframe이
`days`면 결과가 전통적 daily pivot과 같아짐).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_indicators.py` 끝에 추가:
```python
def test_pivot_p_matches_manual_prev_bar_average():
    values = _run_probe("PIVOT_P", {})
    df = make_oscillating_df()
    manual = (df["high"].iloc[-2] + df["low"].iloc[-2] + df["close"].iloc[-2]) / 3.0
    assert abs(values[-1] - manual) < 1e-6


def test_pivot_r1_matches_manual_formula():
    values = _run_probe("PIVOT_R1", {})
    df = make_oscillating_df()
    pivot = (df["high"].iloc[-2] + df["low"].iloc[-2] + df["close"].iloc[-2]) / 3.0
    manual = pivot * 2 - df["low"].iloc[-2]
    assert abs(values[-1] - manual) < 1e-6


def test_pivot_s1_matches_manual_formula():
    values = _run_probe("PIVOT_S1", {})
    df = make_oscillating_df()
    pivot = (df["high"].iloc[-2] + df["low"].iloc[-2] + df["close"].iloc[-2]) / 3.0
    manual = pivot * 2 - df["high"].iloc[-2]
    assert abs(values[-1] - manual) < 1e-6
```

`tests/test_condition_tree.py` 끝에 추가(파일 상단 import 줄에 `get_indicator_value`도 추가해야 함 —
현재 `from engine.condition_tree import (apply_operator, collect_blocks, eval_group,
find_unknown_indicators, is_empty, max_required_period, requires_market_data)`인데, 이 Task에서는
아직 `requires_market_data`를 건드리지 않으므로 이 줄엔 `get_indicator_value`만 추가):
```python
def test_get_indicator_value_dispatches_pivot_sublines():
    import backtrader as bt

    class _FakePivot:
        def __init__(self):
            self.p = [105.0]
            self.r1 = [110.0]
            self.s1 = [100.0]

    obj = _FakePivot()
    assert get_indicator_value("PIVOT_P", obj) == 105.0
    assert get_indicator_value("PIVOT_R1", obj) == 110.0
    assert get_indicator_value("PIVOT_S1", obj) == 100.0
```
(`_FakePivot`은 `bt.Indicator`를 상속하지 않는 순수 더미 객체 — `get_indicator_value`는 속성 접근과
`[0]` 인덱싱만 하므로 실제 backtrader 객체가 아니어도 된다. `BB_upper` 등 기존 다중 라인 지표 테스트가
없다는 게 이미 확인된 상태라 — 이 프로젝트에 지금까지 이런 dispatch 전용 테스트가 없었음 — 새로
추가하는 이 테스트가 그 갭도 같이 메운다.)

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `pytest tests/test_indicators.py -k pivot tests/test_condition_tree.py -k pivot -v`
Expected: FAIL — `KeyError: 'PIVOT_P'` / `NameError: get_indicator_value`

- [ ] **Step 3: 최소 구현 작성**

`engine/indicators/price_levels.py` 끝에 추가:
```python
class PivotPoints(bt.Indicator):
    """직전 1봉의 고가·저가·종가로 계산하는 표준 Pivot Point 기준선(P)/저항선(R1)/지지선(S1)."""

    lines = ("p", "r1", "s1")

    def __init__(self) -> None:
        self.addminperiod(2)

    def next(self) -> None:
        prev_high = self.data.high[-1]
        prev_low = self.data.low[-1]
        prev_close = self.data.close[-1]
        pivot = (prev_high + prev_low + prev_close) / 3.0
        self.lines.p[0] = pivot
        self.lines.r1[0] = pivot * 2 - prev_low
        self.lines.s1[0] = pivot * 2 - prev_high


def create_pivot_p(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    return PivotPoints(data)


def create_pivot_r1(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    return PivotPoints(data)


def create_pivot_s1(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    return PivotPoints(data)
```

`engine/indicators/__init__.py` — import 줄 갱신:
```python
from .price_levels import (
    create_fib_382,
    create_fib_500,
    create_fib_618,
    create_pivot_p,
    create_pivot_r1,
    create_pivot_s1,
)
```
`INDICATOR_FACTORY`에 추가:
```python
    "PIVOT_P": create_pivot_p,
    "PIVOT_R1": create_pivot_r1,
    "PIVOT_S1": create_pivot_s1,
```

`engine/condition_tree.py`의 `get_indicator_value()` — `elif indicator_name == "STOCH_D": return float(obj.percD[0])` 다음, `else:` 이전에 추가:
```python
    elif indicator_name == "PIVOT_P":
        return float(obj.p[0])
    elif indicator_name == "PIVOT_R1":
        return float(obj.r1[0])
    elif indicator_name == "PIVOT_S1":
        return float(obj.s1[0])
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_indicators.py tests/test_condition_tree.py -v`
Expected: 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add engine/indicators/price_levels.py engine/indicators/__init__.py engine/condition_tree.py tests/test_indicators.py tests/test_condition_tree.py
git commit -m "feat: add Pivot Points indicators (PIVOT_P/R1/S1)"
```

---

## Task 3: 카탈로그 등록 ("가격대" 카테고리)

**Files:**
- Modify: `backend/main.py` (`INDICATOR_CATALOG`)
- Modify: `tests/test_backend.py`

**Interfaces:**
- Consumes: Task 1/2의 `INDICATOR_FACTORY` 6개 키.
- Produces: `GET /api/v1/indicators/catalog` 응답에 6개 항목(카테고리 `"가격대"`) 추가.

- [ ] **Step 1: 실패하는 테스트 확인**

`tests/test_backend.py`의 `test_get_indicator_catalog_covers_all_registered_indicators`(기존 테스트,
수정 없이 그대로 재사용)는 이미 `catalog_values == set(INDICATOR_FACTORY.keys()) | POSITION_RELATIVE_INDICATORS`를
검증하므로, `INDICATOR_FACTORY`엔 6개가 있는데 `INDICATOR_CATALOG`엔 아직 없는 지금 시점에 이 테스트가
저절로 실패한다.

Run: `pytest tests/test_backend.py -k test_get_indicator_catalog_covers_all_registered_indicators -v`
Expected: FAIL — `catalog_values`에 `FIB_382` 등이 빠져 있어 set 비교 실패

같은 파일의 카테고리 화이트리스트 라인도 미리 고쳐둔다(`assert item["category"] in {"추세", "오실레이터",
"거래량", "거래대금", "손익", "시장 심리"}` → `"가격대"` 추가):
```python
        assert item["category"] in {"추세", "오실레이터", "거래량", "거래대금", "손익", "시장 심리", "가격대"}
```

- [ ] **Step 2: (Step 1에서 이미 실패 확인함 — 별도 실행 불필요)**

- [ ] **Step 3: 최소 구현 작성**

`backend/main.py`의 `INDICATOR_CATALOG` 리스트에서 `"STOP_LOSS_PCT"` 항목 바로 앞에 추가:
```python
    {
        "value": "FIB_382", "label": "피보나치 38.2%", "category": "가격대",
        "params": [{"key": "period", "label": "기간", "default": 20}],
        "description": "최근 period봉의 스윙 고점과 저점 사이에서 38.2% 되돌림 지점을 계산합니다. 상승 추세 중 조정이 어디까지 진행될지 가늠하는 지지선으로 흔히 씁니다.",
        "example": "period=20이면 최근 20봉의 최고가·최저가 구간에서, 고점 대비 38.2% 되돌아온 가격입니다.",
    },
    {
        "value": "FIB_500", "label": "피보나치 50%", "category": "가격대",
        "params": [{"key": "period", "label": "기간", "default": 20}],
        "description": "최근 period봉의 스윙 고점과 저점의 정중앙(50%) 되돌림 지점입니다. 엄밀히는 피보나치 비율이 아니지만 관례적으로 함께 봅니다.",
        "example": "period=20이면 최근 20봉 구간의 정확히 중간 가격입니다.",
    },
    {
        "value": "FIB_618", "label": "피보나치 61.8%", "category": "가격대",
        "params": [{"key": "period", "label": "기간", "default": 20}],
        "description": "황금비율로 불리는 61.8% 되돌림 지점입니다. 조정이 깊게 들어와도 추세가 살아있는지 가늠하는 마지노선급 지지/저항으로 흔히 해석합니다.",
        "example": "period=20이면 최근 20봉 구간에서 고점 대비 61.8% 되돌아온 가격입니다.",
    },
    {
        "value": "PIVOT_P", "label": "Pivot 기준선", "category": "가격대",
        "params": [],
        "description": "직전 1봉의 고가·저가·종가 평균으로 계산하는 기준선입니다. 오늘 가격이 이 선 위/아래 어디서 노는지로 매수/매도 심리 우위를 가늠하는 전통적 지표입니다.",
        "example": "직전 봉 고가 110, 저가 100, 종가 105면 Pivot = (110+100+105)/3 ≈ 105입니다.",
    },
    {
        "value": "PIVOT_R1", "label": "Pivot 저항선(R1)", "category": "가격대",
        "params": [],
        "description": "Pivot 기준선을 기준으로 계산하는 1차 저항선입니다. 종가가 이 선을 넘으면 상승 모멘텀이 강하다고 흔히 해석합니다.",
        "example": "Pivot이 105, 직전 봉 저가가 100이면 R1 = 105×2 − 100 = 110입니다.",
    },
    {
        "value": "PIVOT_S1", "label": "Pivot 지지선(S1)", "category": "가격대",
        "params": [],
        "description": "Pivot 기준선을 기준으로 계산하는 1차 지지선입니다. 종가가 이 선 아래로 내려가면 하락 압력이 강하다고 흔히 해석합니다.",
        "example": "Pivot이 105, 직전 봉 고가가 110이면 S1 = 105×2 − 110 = 100입니다.",
    },
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_backend.py -v`
Expected: 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "feat: register price-level indicators in the catalog under a new 가격대 category"
```

---

## Task 4: 프론트엔드 카테고리 + threshold 추천값

**Files:**
- Modify: `frontend/lib/indicator-categories.ts`
- Modify: `frontend/components/StrategyConditionBuilder.tsx`

**Interfaces:**
- Consumes: 백엔드 카탈로그의 `category: "가격대"`, `value: "FIB_382"` 등 6개 지표 키(Task 3).

- [ ] **Step 1~2: (프론트 로직 테스트는 이 저장소에 별도 단위테스트 인프라가 없음 — 기존
      `frontend/lib/indicator-categories.ts` 추가 때도 `tsc`+Playwright 수동 검증만 했음. 이 Task도
      동일하게 Step 3 구현 후 `tsc`+런타임 확인으로 대체한다.)**

- [ ] **Step 3: 구현**

`frontend/lib/indicator-categories.ts` 전체를 다음으로 교체:
```typescript
import { Activity, BarChart3, Coins, DollarSign, Ruler, TrendingUp, Users } from 'lucide-react';

export const CATEGORY_ORDER = ['추세', '오실레이터', '거래량', '거래대금', '가격대', '손익', '시장 심리'];

export const CATEGORY_DOT_COLOR: Record<string, string> = {
  추세: 'bg-blue-500',
  오실레이터: 'bg-violet-500',
  거래량: 'bg-teal-500',
  거래대금: 'bg-amber-500',
  가격대: 'bg-cyan-500',
  손익: 'bg-orange-500',
  '시장 심리': 'bg-rose-500',
};

export const CATEGORY_ICON: Record<string, typeof TrendingUp> = {
  추세: TrendingUp,
  오실레이터: Activity,
  거래량: BarChart3,
  거래대금: Coins,
  가격대: Ruler,
  손익: DollarSign,
  '시장 심리': Users,
};
```

`frontend/components/StrategyConditionBuilder.tsx`의 `PRICE_SCALE_INDICATORS`를:
```typescript
const PRICE_SCALE_INDICATORS = new Set(['SMA', 'EMA', 'WMA', 'BB_upper', 'BB_middle', 'BB_lower']);
```
다음으로 교체:
```typescript
const PRICE_SCALE_INDICATORS = new Set([
  'SMA', 'EMA', 'WMA', 'BB_upper', 'BB_middle', 'BB_lower',
  'FIB_382', 'FIB_500', 'FIB_618', 'PIVOT_P', 'PIVOT_R1', 'PIVOT_S1',
]);
```

- [ ] **Step 4: 확인**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

브라우저(Playwright)에서 `/`(조건 빌더)의 지표 드롭다운에 "가격대" 그룹과 6개 항목이 뜨는지, 하나
선택 시 threshold가 현재가 근처로 자동 채워지는지 확인.

- [ ] **Step 5: 커밋**

```bash
git add frontend/lib/indicator-categories.ts frontend/components/StrategyConditionBuilder.tsx
git commit -m "feat: add 가격대 category to condition builder"
```

---

## Task 5: 지표 가이드 탭 콘텐츠

**Files:**
- Modify: `frontend/lib/indicator-calc.ts` (rolling highest/lowest 계산 함수 추가)
- Modify: `frontend/lib/indicator-guide.ts`
- Modify: `frontend/lib/indicator-example-builder.ts`

**Interfaces:**
- Consumes: `frontend/lib/guide-sample-data.ts`의 `SAMPLE_BARS`(기존, 변경 없음).
- Produces: `indicator-calc.ts`에 `highest(values, period)`/`lowest(values, period)` 추가(다른 계산
  함수들과 동일한 시그니처: `number[] -> number[]`, warm-up 구간은 `NaN`).

- [ ] **Step 1~2: (지표 가이드 탭도 별도 단위테스트가 없는 순수 프레젠테이션 레이어 — 기존 컨벤션대로
      `tsc`+Playwright로 검증한다. Step 3 이후로 진행.)**

- [ ] **Step 3: 구현**

`frontend/lib/indicator-calc.ts` 끝(`round` 함수 앞)에 추가:
```typescript
export function highest(values: number[], period: number): number[] {
  return values.map((_, i) => {
    if (i < period - 1) return NaN;
    let max = -Infinity;
    for (let j = i - period + 1; j <= i; j++) max = Math.max(max, values[j]);
    return max;
  });
}

export function lowest(values: number[], period: number): number[] {
  return values.map((_, i) => {
    if (i < period - 1) return NaN;
    let min = Infinity;
    for (let j = i - period + 1; j <= i; j++) min = Math.min(min, values[j]);
    return min;
  });
}
```

`frontend/lib/indicator-guide.ts`의 `INDICATOR_GUIDE` 객체에 `MARKET_TREND` 항목 앞에 6개 추가:
```typescript
  FIB_382: {
    meaning: '최근 period봉의 스윙 고점(최고가)과 저점(최저가) 사이에서, 고점 대비 38.2% 되돌아온 가격입니다.',
    params: [{ key: 'period', role: '스윙 고점/저점을 찾을 봉 개수.' }],
    formula: '최고가 = period봉 최고가, 최저가 = period봉 최저가\nFIB_382 = 최고가 − (최고가 − 최저가) × 0.382',
    thresholdExample: `${'이 앱의 조건식은 "지표값과 숫자 threshold"만 비교합니다. 이 지표를 쓸 때 threshold는 보통 지금 가격대와 비슷한 값을 넣어 "가격이 이 지지/저항 레벨 근처에 있는지"를 거르는 용도로 씁니다.'}`,
    usage: '상승 추세 중 조정이 38.2%선에서 멈추는지 확인해, 그 근처에서 반등을 노리는 눌림목 매수 조건으로 씁니다.',
  },
  FIB_500: {
    meaning: '최근 period봉의 스윙 고점과 저점의 정중앙(50%) 되돌림 가격입니다.',
    params: [{ key: 'period', role: '스윙 고점/저점을 찾을 봉 개수.' }],
    formula: 'FIB_500 = 최고가 − (최고가 − 최저가) × 0.5',
    thresholdExample: '이 앱의 조건식은 지표값과 숫자 threshold만 비교합니다. threshold는 보통 현재 가격대 근처 값을 넣어 레벨 필터로 씁니다.',
    usage: '38.2%/61.8%와 함께 3단계 되돌림 구간을 나눠, 가격이 어느 구간에 있는지로 조정의 깊이를 가늠할 때 씁니다.',
  },
  FIB_618: {
    meaning: '황금비율로 불리는 61.8% 되돌림 가격입니다. 조정이 깊게 들어와도 추세가 살아있는지 가늠하는 마지노선급 지지/저항으로 흔히 해석합니다.',
    params: [{ key: 'period', role: '스윙 고점/저점을 찾을 봉 개수.' }],
    formula: 'FIB_618 = 최고가 − (최고가 − 최저가) × 0.618',
    thresholdExample: '이 앱의 조건식은 지표값과 숫자 threshold만 비교합니다. threshold는 보통 현재 가격대 근처 값을 넣어 레벨 필터로 씁니다.',
    usage: '61.8%선까지 눌리고도 지지되면 추세가 아직 살아있다고 보고, 반대로 깨지면 추세 전환으로 보는 필터로 씁니다.',
  },
  PIVOT_P: {
    meaning: '직전 1봉의 고가·저가·종가 평균입니다. 오늘 가격이 이 선 위/아래 어디서 노는지로 매수/매도 심리 우위를 가늠하는 전통적 지표입니다.',
    params: [],
    formula: 'Pivot = (직전 봉 고가 + 직전 봉 저가 + 직전 봉 종가) ÷ 3',
    thresholdExample: '이 앱의 조건식은 지표값과 숫자 threshold만 비교합니다. threshold는 보통 현재 가격대 근처 값을 넣어 레벨 필터로 씁니다.',
    usage: '종가가 Pivot 위/아래 어느 쪽에 있는지를 다른 오실레이터 조건과 AND로 묶어, 그날의 우세한 방향으로만 진입하는 필터로 씁니다.',
  },
  PIVOT_R1: {
    meaning: 'Pivot 기준선 대비 1차 저항선입니다. 종가가 이 선을 넘으면 상승 모멘텀이 강하다고 흔히 해석합니다.',
    params: [],
    formula: 'R1 = Pivot × 2 − 직전 봉 저가',
    thresholdExample: '이 앱의 조건식은 지표값과 숫자 threshold만 비교합니다. threshold는 보통 현재 가격대 근처 값을 넣어 레벨 필터로 씁니다.',
    usage: '종가가 R1을 상향 돌파하는 걸 돌파 매수 신호로, 혹은 R1 근처를 저항으로 보고 매도 신호로 반대로 쓰기도 합니다.',
  },
  PIVOT_S1: {
    meaning: 'Pivot 기준선 대비 1차 지지선입니다. 종가가 이 선 아래로 내려가면 하락 압력이 강하다고 흔히 해석합니다.',
    params: [],
    formula: 'S1 = Pivot × 2 − 직전 봉 고가',
    thresholdExample: '이 앱의 조건식은 지표값과 숫자 threshold만 비교합니다. threshold는 보통 현재 가격대 근처 값을 넣어 레벨 필터로 씁니다.',
    usage: 'S1 근처에서 반등을 노리는 매수 조건, 혹은 S1 하향 이탈을 손절/추가 하락 신호로 씁니다.',
  },
```

`frontend/lib/indicator-example-builder.ts`의 `buildGuideExample` switch문에, `case 'STOCH_D':` 블록이
끝나는 지점(`case 'CCI': {` 시작 직전)에 6개 case 추가:
```typescript
    case 'FIB_382':
    case 'FIB_500':
    case 'FIB_618': {
      const period = 20;
      const ratio = value === 'FIB_382' ? 0.382 : value === 'FIB_500' ? 0.5 : 0.618;
      const hh = calc.highest(highs, period);
      const ll = calc.lowest(lows, period);
      const fib = closes.map((_, i) => (Number.isNaN(hh[i]) ? NaN : hh[i] - (hh[i] - ll[i]) * ratio));
      const start = firstValidIndex(fib);
      const rows = windowFrom(start).map((bar, i) => ({
        bar: bar.bar,
        cells: { close: n(bar.close, 0), high: n(hh[start + i], 0), low: n(ll[start + i], 0), fib: n(fib[start + i]) },
      }));
      return {
        columns: [
          { key: 'close', label: '종가' },
          { key: 'high', label: `${period}봉 최고가` },
          { key: 'low', label: `${period}봉 최저가` },
          { key: 'fib', label: '되돌림 가격' },
        ],
        rows,
        chart: {
          type: 'line',
          data: SAMPLE_BARS.map((bar, i) => ({ bar: bar.bar, close: bar.close, fib: clean(fib[i]) })),
          lines: [
            { key: 'close', name: '종가', color: '#94a3b8' },
            { key: 'fib', name: `${value}`, color: '#0891b2', dash: true },
          ],
        },
      };
    }
    case 'PIVOT_P':
    case 'PIVOT_R1':
    case 'PIVOT_S1': {
      const p = SAMPLE_BARS.map((_, i) => (i === 0 ? NaN : (SAMPLE_BARS[i - 1].high + SAMPLE_BARS[i - 1].low + SAMPLE_BARS[i - 1].close) / 3));
      const r1 = SAMPLE_BARS.map((bar, i) => (i === 0 ? NaN : p[i] * 2 - SAMPLE_BARS[i - 1].low));
      const s1 = SAMPLE_BARS.map((bar, i) => (i === 0 ? NaN : p[i] * 2 - SAMPLE_BARS[i - 1].high));
      const line = value === 'PIVOT_P' ? p : value === 'PIVOT_R1' ? r1 : s1;
      const rows = windowFrom(1, 6).map((bar, i) => {
        const idx = i + 1;
        return {
          bar: bar.bar,
          cells: {
            prevHigh: n(SAMPLE_BARS[idx - 1].high, 0),
            prevLow: n(SAMPLE_BARS[idx - 1].low, 0),
            prevClose: n(SAMPLE_BARS[idx - 1].close, 0),
            value: n(line[idx]),
          },
        };
      });
      return {
        columns: [
          { key: 'prevHigh', label: '직전 봉 고가' },
          { key: 'prevLow', label: '직전 봉 저가' },
          { key: 'prevClose', label: '직전 봉 종가' },
          { key: 'value', label: value },
        ],
        rows,
        chart: {
          type: 'line',
          data: SAMPLE_BARS.map((bar, i) => ({ bar: bar.bar, close: bar.close, value: clean(line[i]) })),
          lines: [
            { key: 'close', name: '종가', color: '#94a3b8' },
            { key: 'value', name: value, color: '#0891b2', dash: true },
          ],
        },
      };
    }
```

- [ ] **Step 4: 확인**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

Playwright로 `/guide`를 열어 좌측 사이드바에 "가격대" 중분류와 6개 소분류가 뜨는지, 하나 클릭해
표+차트가 정상 렌더되는지 확인.

- [ ] **Step 5: 커밋**

```bash
git add frontend/lib/indicator-calc.ts frontend/lib/indicator-guide.ts frontend/lib/indicator-example-builder.ts
git commit -m "feat: add price-level indicators to the indicator guide tab"
```

---

# Part 2: BTC/테더 상관계수 + 보조 마켓 아키텍처 일반화

## Task 6: `condition_tree.py` — 보조 마켓 요구사항을 다건으로 일반화

**Files:**
- Modify: `engine/condition_tree.py`
- Modify: `tests/test_condition_tree.py`

**Interfaces:**
- Produces: `AUX_MARKET_INDICATORS: dict[str, str]`(지표명 → 필요 마켓 코드),
  `required_aux_markets(group: dict) -> set[str]`(기존 `requires_market_data`를 완전히 대체·삭제).

- [ ] **Step 1: 실패하는 테스트로 교체**

`tests/test_condition_tree.py`의 `test_requires_market_data_true_when_market_trend_present`(107행)~
`test_requires_market_data_checks_nested_groups`(133행) 3개 테스트를 통째로 삭제하고 다음으로 교체:
```python
def test_required_aux_markets_returns_btc_when_market_trend_present():
    tree = {
        "type": "AND",
        "conditions": [{"indicator": "MARKET_TREND", "params": {"period": 10}, "operator": "<", "threshold": 0}],
    }
    assert required_aux_markets(tree) == {"KRW-BTC"}


def test_required_aux_markets_empty_when_absent():
    tree = {"type": "AND", "conditions": [{"indicator": "RSI", "params": {}, "operator": "<", "threshold": 30}]}
    assert required_aux_markets(tree) == set()


def test_required_aux_markets_checks_nested_groups():
    tree = {
        "type": "OR",
        "conditions": [
            {"indicator": "RSI", "params": {}, "operator": "<", "threshold": 30},
            {
                "type": "AND",
                "conditions": [
                    {"indicator": "MARKET_TREND", "params": {"period": 5}, "operator": "<", "threshold": 0}
                ],
            },
        ],
    }
    assert required_aux_markets(tree) == {"KRW-BTC"}


def test_required_aux_markets_returns_both_btc_and_usdt_when_both_correlations_present():
    tree = {
        "type": "AND",
        "conditions": [
            {"indicator": "BTC_CORRELATION", "params": {"period": 20}, "operator": ">", "threshold": 0.5},
            {"indicator": "USDT_CORRELATION", "params": {"period": 20}, "operator": ">", "threshold": 0.5},
        ],
    }
    assert required_aux_markets(tree) == {"KRW-BTC", "KRW-USDT"}


def test_required_aux_markets_dedupes_when_market_trend_and_btc_correlation_both_need_btc():
    tree = {
        "type": "AND",
        "conditions": [
            {"indicator": "MARKET_TREND", "params": {"period": 10}, "operator": "<", "threshold": 0},
            {"indicator": "BTC_CORRELATION", "params": {"period": 20}, "operator": ">", "threshold": 0.5},
        ],
    }
    assert required_aux_markets(tree) == {"KRW-BTC"}
```
파일 맨 위 import 줄의 `requires_market_data`를 `required_aux_markets`로 교체.

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `pytest tests/test_condition_tree.py -v`
Expected: FAIL — `ImportError: cannot import name 'required_aux_markets'`

- [ ] **Step 3: 최소 구현 작성**

`engine/condition_tree.py`의 `requires_market_data` 함수(149~152행)를 통째로 다음으로 교체:
```python
AUX_MARKET_INDICATORS: dict[str, str] = {
    "MARKET_TREND": "KRW-BTC",
    "BTC_CORRELATION": "KRW-BTC",
    "USDT_CORRELATION": "KRW-USDT",
}


def required_aux_markets(group: dict) -> set[str]:
    """조건 트리가 대상 마켓이 아닌 다른 마켓(KRW-BTC, KRW-USDT 등) 캔들이 필요한 지표를
    포함하는지 확인해, 필요한 마켓 코드 집합을 반환한다. backend가 이 집합을 보고 각 마켓의
    캔들을 추가로 조회해 병합할지 정한다. 여러 지표가 같은 마켓을 요구하면(예: MARKET_TREND와
    BTC_CORRELATION이 둘 다 KRW-BTC) 한 번만 등장한다."""
    return {
        AUX_MARKET_INDICATORS[b["indicator"]]
        for b in collect_blocks(group)
        if b["indicator"] in AUX_MARKET_INDICATORS
    }
```
파일 끝의 `__all__` 리스트에서 `"requires_market_data"`를 `"required_aux_markets"`로 교체.

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_condition_tree.py -v`
Expected: PASS (기존 항목 + 신규 5개)

- [ ] **Step 5: 커밋**

```bash
git add engine/condition_tree.py tests/test_condition_tree.py
git commit -m "refactor: generalize requires_market_data to required_aux_markets (supports multiple aux markets)"
```

---

## Task 7: `runner.py` — 동적 feed 클래스로 리팩터

**Files:**
- Modify: `engine/runner.py`
- Modify: `tests/test_runner.py`
- Modify: `tests/test_indicators.py`

**Interfaces:**
- Consumes: 없음(이 Task는 순수 내부 리팩터).
- Produces: `AUX_MARKET_LINE_NAME: dict[str, str]`(마켓 코드 → backtrader 라인 이름),
  `build_data_feed_class(extra_lines: tuple[str, ...]) -> type[bt.feeds.PandasData]`(공개 함수 —
  테스트에서도 재사용). `run_backtest()`에서 `extra_column` 파라미터 제거(더 이상 필요 없음 — df에
  `trade_value`/`btc_close`/`usdt_close` 컬럼이 있는지만 보고 자동으로 라인을 붙임).
  `PandasDataWithExtra`/`PandasDataWithTradeValue`/`PandasDataWithExtraAndTradeValue` 3개 클래스는 삭제.

- [ ] **Step 1: 실패하는 테스트로 교체**

`tests/test_runner.py`의 `test_run_backtest_with_extra_column_exposes_data_extra_line`(79~102행)을
통째로 다음으로 교체:
```python
def test_run_backtest_with_btc_close_column_exposes_data_btc_close_line():
    df = _make_synthetic_df()
    df["btc_close"] = [50000 + i * 10 for i in range(len(df))]

    captured: list[float] = []

    class _CapturesBtcCloseLine(bt.Strategy):
        def next(self):
            captured.append(float(self.data.btc_close[0]))

    run_backtest(
        df=df,
        strategy_cls=_CapturesBtcCloseLine,
        risk_config={
            "initial_capital": 10000,
            "commission_rate": 0.001,
            "position_sizing": "percent",
            "position_size": 100,
        },
    )

    assert captured[0] == 50000.0
    assert captured[-1] == 50000 + (len(df) - 1) * 10


def test_run_backtest_with_btc_and_usdt_columns_exposes_both_lines_simultaneously():
    df = _make_synthetic_df()
    df["btc_close"] = [50000 + i * 10 for i in range(len(df))]
    df["usdt_close"] = [1300 + i for i in range(len(df))]

    captured: list[tuple[float, float]] = []

    class _CapturesBoth(bt.Strategy):
        def next(self):
            captured.append((float(self.data.btc_close[0]), float(self.data.usdt_close[0])))

    run_backtest(
        df=df,
        strategy_cls=_CapturesBoth,
        risk_config={
            "initial_capital": 10000,
            "commission_rate": 0.001,
            "position_sizing": "percent",
            "position_size": 100,
        },
    )

    assert captured[0] == (50000.0, 1300.0)
    assert captured[-1] == (50000 + (len(df) - 1) * 10, 1300 + (len(df) - 1))
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `pytest tests/test_runner.py -v`
Expected: FAIL — `TypeError: run_backtest() got an unexpected keyword argument`는 없지만(이 테스트는
`extra_column`을 안 씀), `AttributeError: 'LineSeries' object has no attribute 'btc_close'`로 실패
(지금 코드는 `btc_close`라는 이름을 모르고 여전히 `extra`만 처리하기 때문).

- [ ] **Step 3: 최소 구현 작성**

`engine/runner.py`의 클래스 3개(`PandasDataWithExtra`, `PandasDataWithTradeValue`,
`PandasDataWithExtraAndTradeValue`, 13~35행)를 통째로 다음으로 교체:
```python
AUX_MARKET_LINE_NAME: dict[str, str] = {"KRW-BTC": "btc_close", "KRW-USDT": "usdt_close"}
_OPTIONAL_LINE_CANDIDATES: tuple[str, ...] = ("trade_value", *AUX_MARKET_LINE_NAME.values())


def build_data_feed_class(extra_lines: tuple[str, ...]) -> type[bt.feeds.PandasData]:
    """주어진 이름들을 추가 라인으로 갖는 PandasData 서브클래스를 동적으로 만든다.

    보조 컬럼 조합(거래대금, BTC 종가, USDT 종가, ...)이 늘어나도 조합마다 클래스를 손으로
    나열하지 않아도 되게 하기 위한 헬퍼 — 조합 수는 2^n으로 늘어나지만 이 함수는 필요한
    조합만 그때그때 만든다."""
    if not extra_lines:
        return bt.feeds.PandasData
    return type(
        "DynamicPandasData",
        (bt.feeds.PandasData,),
        {"lines": extra_lines, "params": tuple((name, name) for name in extra_lines)},
    )
```
`run_backtest()` 함수 시그니처에서 `extra_column: str | None = None,` 파라미터와 그 docstring 줄
(`extra_column: df에 포함된 외부 데이터 컬럼명`)을 삭제.

`run_backtest()` 본문의 다음 블록(현재 179~199행 부근, `has_trade_value = ...`부터 `data_feed = ...`
끝까지)을:
```python
    has_trade_value = "trade_value" in df_bt.columns
    has_extra = bool(extra_column and extra_column in df_bt.columns)

    if has_extra:
        df_bt = df_bt.rename(columns={extra_column: "extra"})

    if has_extra and has_trade_value:
        data_feed = PandasDataWithExtraAndTradeValue(
            dataname=df_bt, open="open", high="high", low="low", close="close",
            volume="volume", openinterest=-1, extra="extra", trade_value="trade_value",
        )
    elif has_extra:
        data_feed = PandasDataWithExtra(
            dataname=df_bt, open="open", high="high", low="low", close="close",
            volume="volume", openinterest=-1, extra="extra",
        )
    elif has_trade_value:
        data_feed = PandasDataWithTradeValue(
            dataname=df_bt, open="open", high="high", low="low", close="close",
            volume="volume", openinterest=-1, trade_value="trade_value",
        )
    else:
        data_feed = bt.feeds.PandasData(
            dataname=df_bt, open="open", high="high", low="low", close="close",
            volume="volume", openinterest=-1,
        )
```
다음으로 교체:
```python
    extra_lines = tuple(name for name in _OPTIONAL_LINE_CANDIDATES if name in df_bt.columns)
    feed_kwargs = {
        "dataname": df_bt, "open": "open", "high": "high", "low": "low", "close": "close",
        "volume": "volume", "openinterest": -1,
    }
    feed_kwargs.update({name: name for name in extra_lines})
    data_feed = build_data_feed_class(extra_lines)(**feed_kwargs)
```
`run_backtest()`를 호출하는 곳(같은 파일 내부엔 없음)은 Task 9에서 정리한다 — 이 Task에서는 우선
`engine/runner.py`만 정리하고, `engine/cache.py`/`backend/main.py`가 여전히 `extra_column=`을
넘기고 있어 이 시점엔 **`pytest tests/` 전체가 아직 깨져 있는 게 정상**(Task 9까지가 한 묶음).

`__all__` 리스트(파일 끝)를:
```python
__all__ = [
    "run_backtest",
    "PandasDataWithExtra",
    "PandasDataWithTradeValue",
    "PandasDataWithExtraAndTradeValue",
]
```
다음으로 교체:
```python
__all__ = [
    "run_backtest",
    "build_data_feed_class",
    "AUX_MARKET_LINE_NAME",
]
```

`tests/test_indicators.py`의 import 줄(5행)을:
```python
from engine.runner import PandasDataWithExtra, PandasDataWithTradeValue, run_backtest
```
다음으로 교체:
```python
from engine.runner import build_data_feed_class, run_backtest
```
같은 파일의 `_run_probe_with_extra`(50~65행)와 `_run_probe_with_trade_value`(83~97행) 두 헬퍼를
하나로 통합해 다음으로 교체(호출부 `test_market_trend_matches_manual_close_minus_sma_of_extra_line`/
`test_trade_value_matches_raw_trade_value_column`/`test_trade_value_sma_matches_manual_average_of_trade_value`
는 아래처럼 갱신):
```python
def _run_probe_with_aux(indicator: str, params: dict, aux_line: str, aux_series) -> list[float]:
    df = make_oscillating_df()
    df[aux_line] = aux_series
    df_bt = df.set_index("candle_time")
    df_bt.index = df_bt.index.tz_localize(None)
    cerebro = bt.Cerebro()
    cerebro.adddata(
        build_data_feed_class((aux_line,))(
            dataname=df_bt, open="open", high="high", low="low", close="close",
            volume="volume", openinterest=-1, **{aux_line: aux_line},
        )
    )
    cerebro.addstrategy(_ProbeStrategy, indicator=indicator, indicator_params=params)
    results = cerebro.run()
    return results[0].seen_values


def test_market_trend_matches_manual_close_minus_sma_of_btc_close_line():
    df = make_oscillating_df()
    btc_close = df["close"] * 2 + 1000  # 대상 마켓과 스케일이 다른 별도 시세임을 검증하기 위해 배율을 둠
    values = _run_probe_with_aux("MARKET_TREND", {"period": 5}, "btc_close", btc_close)
    manual = (btc_close - btc_close.rolling(5).mean()).iloc[-1]
    assert abs(values[-1] - manual) < 1e-6


def test_trade_value_matches_raw_trade_value_column():
    df = make_oscillating_df()
    trade_value = df["close"] * df["volume"]
    values = _run_probe_with_aux("TRADE_VALUE", {}, "trade_value", trade_value)
    assert abs(values[-1] - trade_value.iloc[-1]) < 1e-6


def test_trade_value_sma_matches_manual_average_of_trade_value():
    df = make_oscillating_df()
    trade_value = df["close"] * df["volume"]
    values = _run_probe_with_aux("TRADE_VALUE_SMA", {"period": 5}, "trade_value", trade_value)
    manual = trade_value.rolling(5).mean().iloc[-1]
    assert abs(values[-1] - manual) < 1e-6
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_runner.py tests/test_indicators.py -v`
Expected: `test_runner.py` PASS. `test_indicators.py`는 이 시점엔 일부 실패해도 된다(Task 8에서
`engine/indicators/market.py`가 아직 `data.extra`를 쓰고 있어서 `MARKET_TREND` 테스트가 깨짐 —
Task 8 끝나면 정상화됨. **커밋 전에 반드시 Task 8까지 마치고 전체 스위트로 재확인할 것.**)

- [ ] **Step 5: 커밋 (Task 8과 함께 — 아래 참고)**

이 Task는 Task 8의 `market.py` 수정과 강하게 결합돼 있어(둘 다 끝나야 `test_indicators.py`가 다시
전부 통과) 별도 커밋 없이 바로 Task 8로 진행한다.

---

## Task 8: `market.py` — 라인 이름 정리 + 상관계수 지표 2개

**Files:**
- Modify: `engine/indicators/market.py`
- Test: `tests/test_indicators.py` (append, Task 7의 임시 실패 해소)

**Interfaces:**
- Consumes: `data.btc_close`/`data.usdt_close` 라인(Task 7의 `build_data_feed_class`가 채움).
- Produces: `create_market_trend`(기존, `data.extra`→`data.btc_close`로 내부만 수정, 동작 동일),
  `create_btc_correlation(data, **params)`, `create_usdt_correlation(data, **params)`,
  `RollingCorrelation` 커스텀 `bt.Indicator`(`lines = ("corr",)`).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_indicators.py` 끝에 추가(파일 상단에 `import statistics` 추가 필요):
```python
def test_btc_correlation_matches_manual_pearson_of_pct_returns():
    df = make_oscillating_df()
    btc_df = make_oscillating_df(base=50000.0, amplitude=3000.0, period=45, ripple_period=9)
    values = _run_probe_with_aux("BTC_CORRELATION", {"period": 10}, "btc_close", btc_df["close"])

    coin_roc = df["close"].pct_change() * 100
    btc_roc = btc_df["close"].pct_change() * 100
    manual = statistics.correlation(coin_roc.iloc[-10:].tolist(), btc_roc.iloc[-10:].tolist())
    assert abs(values[-1] - manual) < 1e-6


def test_usdt_correlation_matches_manual_pearson_of_pct_returns():
    df = make_oscillating_df()
    usdt_df = make_oscillating_df(base=1300.0, amplitude=40.0, period=30, ripple_period=4)
    values = _run_probe_with_aux("USDT_CORRELATION", {"period": 10}, "usdt_close", usdt_df["close"])

    coin_roc = df["close"].pct_change() * 100
    usdt_roc = usdt_df["close"].pct_change() * 100
    manual = statistics.correlation(coin_roc.iloc[-10:].tolist(), usdt_roc.iloc[-10:].tolist())
    assert abs(values[-1] - manual) < 1e-6
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `pytest tests/test_indicators.py -v`
Expected: FAIL — `KeyError: 'BTC_CORRELATION'`(아직 미등록) + `test_market_trend_matches_manual_close_minus_sma_of_btc_close_line`도
`data.extra` 참조 때문에 실패 중(Task 7에서 이미 예견됨).

- [ ] **Step 3: 최소 구현 작성**

`engine/indicators/market.py` 전체를 다음으로 교체:
```python
"""
engine/indicators/market.py

대상 코인 자체가 아니라 다른 마켓과의 관계를 반영하는 지표. engine.runner의
build_data_feed_class가 채워주는 self.data.btc_close / self.data.usdt_close 라인
(백엔드가 KRW-BTC·KRW-USDT 종가를 병합해 넣는다)에서 계산한다.
"""
from __future__ import annotations

import statistics

import backtrader as bt


def create_market_trend(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 10))
    market_close = data.btc_close
    sma = bt.indicators.SMA(market_close, period=period)
    return market_close - sma


class RollingCorrelation(bt.Indicator):
    """두 종가 라인의 봉 대비 등락률(bt.indicators.ROC100, period=1)을 최근 period봉 모아
    Pearson 상관계수를 계산한다. 등락률·롤링 윈도우 방식은
    docs/superpowers/specs_v1/2026-07-27-strategy-source-classification.md의 "상관계수 계산
    방법론" 절에서 합의된 정의를 그대로 따른다."""

    lines = ("corr",)
    params = (("period", 20),)

    def __init__(self) -> None:
        self.roc_a = bt.indicators.ROC100(self.data0, period=1)
        self.roc_b = bt.indicators.ROC100(self.data1, period=1)
        self.addminperiod(self.p.period + 1)

    def next(self) -> None:
        xs = [self.roc_a[-i] for i in range(self.p.period)]
        ys = [self.roc_b[-i] for i in range(self.p.period)]
        self.lines.corr[0] = statistics.correlation(xs, ys)


def create_btc_correlation(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 20))
    return RollingCorrelation(data.close, data.btc_close, period=period)


def create_usdt_correlation(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 20))
    return RollingCorrelation(data.close, data.usdt_close, period=period)
```

`engine/indicators/__init__.py` — import 줄을:
```python
from .market import create_market_trend
```
다음으로 교체:
```python
from .market import create_btc_correlation, create_market_trend, create_usdt_correlation
```
`INDICATOR_FACTORY`에 추가(`"MARKET_TREND": create_market_trend,` 다음 줄):
```python
    "BTC_CORRELATION": create_btc_correlation,
    "USDT_CORRELATION": create_usdt_correlation,
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_indicators.py tests/test_runner.py tests/test_condition_tree.py -v`
Expected: 전부 PASS. **주의**: `RollingCorrelation(data.close, data.btc_close, period=period)`처럼
커스텀 `bt.Indicator`에 데이터 라인 2개를 위치 인자로 넘겨 `self.data0`/`self.data1`로 받는 건 이
프로젝트에서 처음 쓰는 패턴이다 — 여기서 실패하면 `bt.Indicator.__init__(self, *datas)` 시그니처와
`self.datas[0]`/`self.datas[1]` 직접 접근으로 바꿔서 재시도.

- [ ] **Step 5: 커밋**

```bash
git add engine/indicators/market.py engine/indicators/__init__.py tests/test_indicators.py
git commit -m "feat: add BTC/USDT correlation indicators and rename extra line to btc_close"
```

---

## Task 9: 백엔드 — 다중 보조 마켓 병합으로 일반화

**Files:**
- Modify: `engine/cache.py`
- Modify: `backend/main.py`
- Modify: `tests/test_cache.py`
- Modify: `tests/test_backend.py`

**Interfaces:**
- Consumes: `engine.condition_tree.required_aux_markets`(Task 6), `engine.runner.AUX_MARKET_LINE_NAME`(Task 7).
- Produces: `run_backtest_cached()`에서 `extra_column` 파라미터 제거(df에 보조 컬럼이 이미 병합된
  상태로 들어온다고 가정).

- [ ] **Step 1: 테스트 정리 (실패 확인 겸)**

`tests/test_cache.py`의 `test_run_backtest_cached_passes_extra_column_through`(528~549행)를
**삭제**한다 — `extra_column`이라는 개념 자체가 사라져 이 테스트가 검증하던 동작이 더 이상 존재하지
않는다.

같은 파일의 `fake_run_backtest`/`failing_run_backtest` 시그니처 3곳(`test_run_backtest_cached_hits_cache_on_second_call`,
`test_run_backtest_cached_does_not_cache_on_failure`, `test_run_backtest_cached_exposes_run_id`)에서
`extra_column=None` 파라미터를 제거한다. 예:
```python
    def fake_run_backtest(df, strategy_cls, risk_config, strategy_params=None):
```

`tests/test_backend.py`의 `test_run_backtest_merges_btc_close_at_correct_scale_and_fills_gaps`(792~847행)를
다음으로 교체(핵심 변경: `market_close`→`btc_close`, `captured["extra_column"]` 체크 제거):
```python
def test_run_backtest_merges_btc_close_at_correct_scale_and_fills_gaps(monkeypatch, tmp_path):
    # target(KRW-ETH)의 close와 BTC의 close가 뒤바뀌는 버그, 그리고 candle_time이
    # 일부 겹치지 않을 때 ffill().bfill()이 실제로 동작하는지를 함께 검증한다.
    client = _client(monkeypatch, tmp_path)

    target_df = make_oscillating_df()
    btc_df = target_df.copy()
    btc_df["close"] = btc_df["close"] * 2 + 1000  # target과 스케일이 다른 별도 시세임을 검증하기 위해 배율을 둠
    gap_positions = [5, 6, 150]  # 5,6은 연속 gap, 150은 단독 gap
    btc_df = btc_df.drop(btc_df.index[gap_positions]).reset_index(drop=True)

    def _fake_get_candles(market, timeframe, start, end):
        if market == "KRW-BTC":
            return btc_df
        return target_df

    monkeypatch.setattr(backend_module, "get_candles", _fake_get_candles)

    captured = {}
    real_run_backtest_cached = backend_module.run_backtest_cached

    def _capture(**kwargs):
        captured["df"] = kwargs["df"].copy()
        return real_run_backtest_cached(**kwargs)

    monkeypatch.setattr(backend_module, "run_backtest_cached", _capture)

    buy = {"type": "AND", "conditions": [{"indicator": "MARKET_TREND", "params": {"period": 5}, "operator": "<", "threshold": 0}]}
    resp = client.post("/api/v1/backtests/run", json=_run_request(market="KRW-ETH", buy_conditions=buy))

    assert resp.status_code == 200

    merged = captured["df"].reset_index(drop=True)
    expected_btc_close = target_df["close"] * 2 + 1000

    # btc_close는 target 자신의 close가 아니라 BTC의(스케일이 다른) close를 따라야 한다.
    assert merged.loc[0, "btc_close"] != target_df.loc[0, "close"]
    assert abs(merged.loc[0, "btc_close"] - expected_btc_close.iloc[0]) < 1e-6

    # gap이 없는 행들은 정확히 BTC의 스케일된 close와 일치해야 한다.
    for i in range(len(merged)):
        if i in gap_positions:
            continue
        assert abs(merged.loc[i, "btc_close"] - expected_btc_close.iloc[i]) < 1e-6

    # 연속된 gap(5,6)은 이전 값(index 4)으로 ffill 되어야 한다.
    assert abs(merged.loc[5, "btc_close"] - expected_btc_close.iloc[4]) < 1e-6
    assert abs(merged.loc[6, "btc_close"] - expected_btc_close.iloc[4]) < 1e-6

    # 단독 gap(150)도 이전 값(index 149)으로 ffill 되어야 한다.
    assert abs(merged.loc[150, "btc_close"] - expected_btc_close.iloc[149]) < 1e-6

    # ffill/bfill 이후에는 NaN이 하나도 남아있으면 안 된다.
    assert merged["btc_close"].isna().sum() == 0


def test_run_backtest_merges_both_btc_and_usdt_when_both_correlations_present(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    target_df = make_oscillating_df()
    btc_df = target_df.copy()
    btc_df["close"] = btc_df["close"] * 2 + 1000
    usdt_df = target_df.copy()
    usdt_df["close"] = 1300.0

    def _fake_get_candles(market, timeframe, start, end):
        if market == "KRW-BTC":
            return btc_df
        if market == "KRW-USDT":
            return usdt_df
        return target_df

    monkeypatch.setattr(backend_module, "get_candles", _fake_get_candles)

    captured = {}
    real_run_backtest_cached = backend_module.run_backtest_cached

    def _capture(**kwargs):
        captured["df"] = kwargs["df"].copy()
        return real_run_backtest_cached(**kwargs)

    monkeypatch.setattr(backend_module, "run_backtest_cached", _capture)

    buy = {
        "type": "AND",
        "conditions": [
            {"indicator": "BTC_CORRELATION", "params": {"period": 10}, "operator": ">", "threshold": -1},
            {"indicator": "USDT_CORRELATION", "params": {"period": 10}, "operator": ">", "threshold": -1},
        ],
    }
    resp = client.post("/api/v1/backtests/run", json=_run_request(market="KRW-ETH", buy_conditions=buy))

    assert resp.status_code == 200
    merged = captured["df"]
    assert "btc_close" in merged.columns
    assert "usdt_close" in merged.columns
```
(`_run_request` 헬퍼가 이미 파일에 있다고 가정 — 없다면 기존 `test_run_backtest_merges_btc_close_...`
근처에서 실제 헬퍼 시그니처를 확인하고 맞춰 쓴다.)

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `pytest tests/test_cache.py tests/test_backend.py -v`
Expected: FAIL — `TypeError: run_backtest_cached() got an unexpected keyword argument 'extra_column'`은
아직 안 나고(호출부가 여전히 넘기고 있으므로), 대신 `KeyError: 'btc_close'`(컬럼명이 여전히
`market_close`라서)로 실패.

- [ ] **Step 3: 최소 구현 작성**

`engine/cache.py`의 `run_backtest_cached()` 시그니처에서 `extra_column: str | None = None,` 파라미터
삭제. 본문의 `result = run_backtest(df, strategy_cls, risk_config, strategy_params, extra_column=extra_column)`를:
```python
    result = run_backtest(df, strategy_cls, risk_config, strategy_params)
```
로 교체.

`backend/main.py`의 import 줄(29행)을:
```python
from engine.condition_tree import find_unknown_indicators, is_empty, max_required_period, requires_market_data
```
다음으로 교체:
```python
from engine.condition_tree import find_unknown_indicators, is_empty, max_required_period, required_aux_markets
from engine.runner import AUX_MARKET_LINE_NAME
```
`run_backtest_endpoint()`의 다음 블록(현재 520~547행, `extra_column = None`부터 `extra_column = "market_close"`까지)을:
```python
    extra_column = None
    if requires_market_data(buy_dict) or requires_market_data(sell_dict):
        if req.market == "KRW-BTC":
            df = df.assign(market_close=df["close"])
        else:
            try:
                btc_df = get_candles("KRW-BTC", req.timeframe, start_dt, end_dt)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except RuntimeError as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            if btc_df.empty:
                raise HTTPException(
                    status_code=400,
                    detail="MARKET_TREND 조건에 필요한 KRW-BTC 캔들 데이터가 해당 기간에 없습니다",
                )
            df = df.merge(
                btc_df[["candle_time", "close"]].rename(columns={"close": "market_close"}),
                on="candle_time",
                how="left",
            )
            if df["market_close"].isna().all():
                raise HTTPException(
                    status_code=400,
                    detail="MARKET_TREND 조건에 필요한 KRW-BTC 캔들 데이터가 해당 기간에 없습니다",
                )
            df["market_close"] = df["market_close"].ffill().bfill()
        extra_column = "market_close"
```
다음으로 교체:
```python
    aux_markets = required_aux_markets(buy_dict) | required_aux_markets(sell_dict)
    for aux_market in aux_markets:
        line_name = AUX_MARKET_LINE_NAME[aux_market]
        if req.market == aux_market:
            df = df.assign(**{line_name: df["close"]})
            continue
        try:
            aux_df = get_candles(aux_market, req.timeframe, start_dt, end_dt)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        if aux_df.empty:
            raise HTTPException(
                status_code=400,
                detail=f"이 조건에 필요한 {aux_market} 캔들 데이터가 해당 기간에 없습니다",
            )
        df = df.merge(
            aux_df[["candle_time", "close"]].rename(columns={"close": line_name}),
            on="candle_time",
            how="left",
        )
        if df[line_name].isna().all():
            raise HTTPException(
                status_code=400,
                detail=f"이 조건에 필요한 {aux_market} 캔들 데이터가 해당 기간에 없습니다",
            )
        df[line_name] = df[line_name].ffill().bfill()
```
바로 아래 `run_backtest_cached(...)` 호출에서 `extra_column=extra_column,` 줄을 삭제.

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/ -v`
Expected: 전체 스위트 PASS(Part 1+2 신규 테스트 포함). 이 시점이 Part 2 전체가 처음으로 다시 온전히
그린이 되는 지점이다.

- [ ] **Step 5: 커밋**

```bash
git add engine/cache.py backend/main.py tests/test_cache.py tests/test_backend.py
git commit -m "refactor: merge multiple aux markets (KRW-BTC, KRW-USDT) generically instead of KRW-BTC-only"
```

---

## Task 10: 카탈로그 등록 + 프론트엔드 (BTC/테더 상관계수)

**Files:**
- Modify: `backend/main.py` (`INDICATOR_CATALOG`)
- Modify: `tests/test_backend.py` (카테고리 화이트리스트는 Task 3에서 이미 갱신됨 — 변경 불필요)
- Modify: `frontend/components/StrategyConditionBuilder.tsx`
- Modify: `frontend/lib/indicator-guide.ts`
- Modify: `frontend/lib/indicator-example-builder.ts`

**Interfaces:**
- Consumes: Task 8의 `INDICATOR_FACTORY` 키 `BTC_CORRELATION`/`USDT_CORRELATION`.

- [ ] **Step 1~2: (Task 3과 동일한 이유로 `test_get_indicator_catalog_covers_all_registered_indicators`가
      이미 실패 상태 — 별도 신규 테스트 불필요, 그 기존 테스트를 그린으로 만드는 게 이 Task의 목표.)**

Run: `pytest tests/test_backend.py -k test_get_indicator_catalog_covers_all_registered_indicators -v`
Expected: FAIL — `BTC_CORRELATION`/`USDT_CORRELATION`이 `INDICATOR_FACTORY`엔 있는데 카탈로그엔 없음

- [ ] **Step 3: 구현**

`backend/main.py`의 `INDICATOR_CATALOG`에서 `"MARKET_TREND"` 항목 바로 뒤에 추가:
```python
    {
        "value": "BTC_CORRELATION", "label": "BTC 상관계수", "category": "시장 심리",
        "params": [{"key": "period", "label": "기간", "default": 20}],
        "description": "대상 코인과 KRW-BTC의 봉 대비 등락률(%)을 최근 period봉 동안 비교한 Pearson 상관계수(-1~1)입니다. 1에 가까울수록 BTC와 같은 방향으로, -1에 가까울수록 반대 방향으로 움직입니다.",
        "example": "period=20, 연산자 <, 임계값 0.3이면: 최근 20봉 동안 BTC와의 상관관계가 약해진(디커플링된) 상태를 포착합니다.",
    },
    {
        "value": "USDT_CORRELATION", "label": "테더 상관계수", "category": "시장 심리",
        "params": [{"key": "period", "label": "기간", "default": 20}],
        "description": "대상 코인과 KRW-USDT(테더)의 봉 대비 등락률(%)을 최근 period봉 동안 비교한 Pearson 상관계수(-1~1)입니다.",
        "example": "period=20, 연산자 >, 임계값 0.5면: 최근 20봉 동안 원화 유동성(테더 시세)과 강하게 같이 움직이는 구간을 포착합니다.",
    },
```

`frontend/components/StrategyConditionBuilder.tsx`의 `recommendedThreshold()` 함수에서, `if (indicator === 'ATR') return currentPrice ? Math.round(currentPrice * 0.01) : 1;` 다음 줄에 추가:
```typescript
  if (indicator === 'BTC_CORRELATION' || indicator === 'USDT_CORRELATION') {
    return operator === '<' || operator === '<=' ? -0.3 : 0.5;
  }
```

- [ ] **Step 4: 확인**

Run: `pytest tests/test_backend.py -v` → 전부 PASS
Run: `cd frontend && npx tsc --noEmit` → 에러 없음

`frontend/lib/indicator-guide.ts`의 `INDICATOR_GUIDE`에 `MOMENTUM_PCT` 항목 뒤(파일 끝)에 추가:
```typescript
  BTC_CORRELATION: {
    meaning: '대상 코인과 KRW-BTC의 봉 대비 등락률(%)을 최근 period봉 모아 계산한 Pearson 상관계수입니다. engine/indicators/market.py의 RollingCorrelation이 이 값을 계산합니다.',
    params: [{ key: 'period', role: '상관계수를 계산할 롤링 윈도우 봉 개수.' }],
    formula: '등락률_t = (종가_t − 종가_{t-1}) ÷ 종가_{t-1} × 100\n상관계수 = Pearson(대상코인 등락률_[t-period+1..t], KRW-BTC 등락률_[t-period+1..t])',
    thresholdExample: '값은 -1~1 범위입니다. 1에 가까울수록 BTC와 같은 방향으로, -1에 가까울수록 반대 방향으로 움직입니다. 예: 임계값 0.3, 연산자 "<"면 BTC와의 동조화가 약해진(디커플링) 구간을 포착합니다.',
    usage: '알트코인 매수 조건에 "BTC와 상관관계가 낮을 때만"이라는 필터를 추가해, 시장 전체 방향이 아니라 그 코인 고유의 움직임을 노리는 전략에 씁니다.',
  },
  USDT_CORRELATION: {
    meaning: '대상 코인과 KRW-USDT(테더)의 봉 대비 등락률(%)을 최근 period봉 모아 계산한 Pearson 상관계수입니다.',
    params: [{ key: 'period', role: '상관계수를 계산할 롤링 윈도우 봉 개수.' }],
    formula: '등락률_t = (종가_t − 종가_{t-1}) ÷ 종가_{t-1} × 100\n상관계수 = Pearson(대상코인 등락률_[t-period+1..t], KRW-USDT 등락률_[t-period+1..t])',
    thresholdExample: '값은 -1~1 범위입니다. 예: 임계값 0.5, 연산자 ">"면 원화 유동성(테더) 흐름과 강하게 같이 움직이는 구간만 남깁니다.',
    usage: 'BTC 상관계수와 함께 걸어, "BTC와는 무관하지만 전체 원화 유동성 흐름과는 같이 가는" 것처럼 세밀한 시장 필터 조합을 만들 때 씁니다.',
  },
```

`frontend/lib/indicator-example-builder.ts`의 `buildGuideExample` switch문, `case 'MARKET_TREND':` 블록
뒤에 추가(이 두 지표는 대상 코인 자신의 `SAMPLE_BARS` 종가와 `SAMPLE_BTC`의 종가를 등락률로 바꿔
직접 상관계수를 구하므로 `indicator-calc.ts`의 계산 함수를 새로 추가하지 않고 이 case 안에서 바로 계산한다):
```typescript
    case 'BTC_CORRELATION':
    case 'USDT_CORRELATION': {
      const period = 10;
      const coinRoc = closes.map((c, i) => (i === 0 ? NaN : ((c - closes[i - 1]) / closes[i - 1]) * 100));
      const auxCloses = SAMPLE_BTC.map((b) => b.close);
      const auxRoc = auxCloses.map((c, i) => (i === 0 ? NaN : ((c - auxCloses[i - 1]) / auxCloses[i - 1]) * 100));
      const corr = closes.map((_, i) => {
        if (i < period) return NaN;
        const xs = coinRoc.slice(i - period + 1, i + 1);
        const ys = auxRoc.slice(i - period + 1, i + 1);
        const meanX = xs.reduce((a, b) => a + b, 0) / period;
        const meanY = ys.reduce((a, b) => a + b, 0) / period;
        const cov = xs.reduce((sum, x, j) => sum + (x - meanX) * (ys[j] - meanY), 0);
        const stdX = Math.sqrt(xs.reduce((sum, x) => sum + (x - meanX) ** 2, 0));
        const stdY = Math.sqrt(ys.reduce((sum, y) => sum + (y - meanY) ** 2, 0));
        return stdX === 0 || stdY === 0 ? 0 : cov / (stdX * stdY);
      });
      const start = firstValidIndex(corr);
      const rows = windowFrom(start).map((bar, i) => ({
        bar: bar.bar,
        cells: { close: n(bar.close, 0), aux: n(auxCloses[start + i], 0), corr: n(corr[start + i]) },
      }));
      const label = value === 'BTC_CORRELATION' ? 'KRW-BTC' : 'KRW-USDT';
      return {
        columns: [
          { key: 'close', label: '종가' },
          { key: 'aux', label: `${label} 종가` },
          { key: 'corr', label: '상관계수' },
        ],
        rows,
        chart: {
          type: 'line',
          data: SAMPLE_BARS.map((bar, i) => ({ bar: bar.bar, corr: clean(corr[i]) })),
          lines: [{ key: 'corr', name: `${label} 상관계수 (period=${period})`, color: '#e11d48' }],
          refLines: [{ y: 0, label: '0선' }],
        },
      };
    }
```
(맨 앞의 빈 python 코드 블록은 오타 방지용 — 실제로 넣지 않는다. TypeScript 블록만 추가.)

- [ ] **Step 5: 최종 확인 및 커밋**

Run: `pytest tests/ -v` → 전체 PASS
Run: `cd frontend && npx tsc --noEmit` → 에러 없음
Playwright: `/`(조건 빌더)에서 "시장 심리" 카테고리에 BTC/테더 상관계수가 뜨는지, 하나 선택 시
threshold가 0.5(또는 연산자에 따라 -0.3)로 채워지는지. `/guide`에서 두 항목이 표+차트와 함께 뜨는지.
실제로 BTC 상관계수 조건 하나로 백테스트를 돌려 200번대 응답이 오는지 curl로 확인.

```bash
git add backend/main.py frontend/components/StrategyConditionBuilder.tsx frontend/lib/indicator-guide.ts frontend/lib/indicator-example-builder.ts
git commit -m "feat: register BTC/USDT correlation indicators in catalog and guide tab"
```

---

## 이 플랜에 포함하지 않은 것

`docs/superpowers/specs_v1/2026-07-27-strategy-source-classification.md`의 B 레이어 중 다음 2개는
**이 플랜 범위 밖**이다 — 별도 스펙/플랜이 필요해서 지금 같이 설계하면 이 플랜만 불필요하게 무거워진다.

- **Volume Profile(VPVR)**: condition 엔진에 넘길 단일 숫자를 뭘로 할지(POC와의 거리 % vs POC 가격
  자체)가 아직 미결정. 결정 나면 이 플랜의 Task 1~5와 거의 같은 모양(새 파일 하나 + 카탈로그 + 가이드)이
  될 것.
- **체결강도 / VPIN**: `/v1/trades/ticks` 과거 조회 가능 범위 리서치가 선행돼야 한다. 캔들과 전혀 다른
  데이터 형태(틱)라 `upbit_data_service.py`에 새 조회 함수가 필요하고, 캐싱 전략도 다시 설계해야 한다.

## Verification (전체)

- `pytest tests/ -v` — 전체 스위트 그린(기존 168 + Part1 신규 ~8 + Part2 신규 ~9, 대략 185개 안팎).
- `cd frontend && npx tsc --noEmit` — 클린.
- Playwright: `/`에서 "가격대"·"시장 심리"(BTC/테더 상관계수 추가분) 카테고리가 정상 표시되는지,
  실제 조건으로 백테스트 1건을 끝까지 실행해 결과 화면까지 나오는지. `/guide`에서 8개 신규 항목이
  전부 표+차트/게이지와 함께 렌더되는지.
- 백엔드는 코드 수정마다 재시작 필요(`uvicorn --reload`가 이 저장소에서 간헐적으로 안 먹는 이슈가
  기존에 있었음 — 반드시 수동 재시작 후 확인).
