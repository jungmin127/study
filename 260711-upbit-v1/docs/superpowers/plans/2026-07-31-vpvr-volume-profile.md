# Volume Profile (VPVR) 지표 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `docs/superpowers/specs/2026-07-31-vpvr-volume-profile-design.md`에서 설계한 대로, 가격대별
거래량 분포(Volume Profile)에서 뽑아낸 POC(Point of Control)/VAH/VAL(Value Area 상단/하단)을 조건
빌더에 `VPVR_POC`/`VPVR_VAH`/`VPVR_VAL` 3개 지표로 추가한다.

**Architecture:** `PivotPoints`(`engine/indicators/price_levels.py`)와 같은 패턴 — 대상 코인 자신의
`high`/`low`/`volume`만 쓰는 커스텀 `bt.Indicator`(`VolumeProfile`, 3개 라인 공유)로 backtrader 안에서
라이브 계산한다. 외부 데이터도, 다른 마켓 캔들도, 백엔드 병합 로직도 필요 없다.

**Tech Stack:** Python 3.11, backtrader, `collections.deque`(표준 라이브러리) / Next.js 14, TypeScript.

## Global Constraints

- 기존 pytest 테스트는 계속 100% 통과해야 한다.
- `npx tsc --noEmit`(frontend)이 항상 깨끗해야 한다.
- 카탈로그(백엔드) ↔ 지표 가이드 탭(프론트) ↔ 조건 빌더 카테고리 상수는 항상 같이 갱신한다. 카테고리는
  기존 "가격대"를 재사용하므로 `frontend/lib/indicator-categories.ts` 수정은 필요 없다.
- 파라미터는 `period` 하나(기본값 50)로 롤링 윈도우 크기를 제어한다. `NUM_BINS`(24)와
  `VALUE_AREA_PCT`(0.7)는 모듈 상수로 고정하고 파라미터로 노출하지 않는다.
- `AUX_MARKET_INDICATORS`(`engine/condition_tree.py`), `_OPTIONAL_LINE_CANDIDATES`(`engine/runner.py`)
  둘 다 수정하지 않는다 — VPVR은 보조 마켓도 외부 데이터도 필요 없다.
- 커밋은 Task 단위로 작게 나눠서 한다.
- 백엔드는 코드 수정마다 재시작 필요(`uvicorn --reload`가 이 저장소에서 간헐적으로 안 먹는 이슈가
  기존에 있었음 — 반드시 수동 재시작 후 확인). Task 1은 백엔드 코드를 건드리지 않으므로 재시작
  불필요 — Task 2부터 재시작이 필요하다.

---

## Task 1: VolumeProfile 지표 구현 및 등록

**Files:**
- Modify: `engine/indicators/price_levels.py`
- Modify: `engine/indicators/__init__.py`
- Modify: `engine/condition_tree.py`
- Test: `tests/test_indicators.py` (append)

**Interfaces:**
- Produces: `VolumeProfile(bt.Indicator)`(3개 라인 `poc`/`vah`/`val`, 파라미터 `period`, 기본값 50).
  `create_vpvr_poc(data, **params) -> bt.Indicator`, `create_vpvr_vah(...)`, `create_vpvr_val(...)`.
  `INDICATOR_FACTORY["VPVR_POC"]`/`["VPVR_VAH"]`/`["VPVR_VAL"]`.
  `get_indicator_value("VPVR_POC"|"VPVR_VAH"|"VPVR_VAL", obj)`가 각각 `obj.lines.poc[0]`/`vah[0]`/`val[0]`을
  반환.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_indicators.py` 파일 상단 import 줄(`from engine.indicators import INDICATOR_FACTORY` 다음
줄)에 추가:
```python
from engine.indicators import price_levels
```

파일 끝에 추가:
```python
def _run_vpvr_probe(
    highs: list[float], lows: list[float], volumes: list[float], period: int, num_bins: int
) -> list[tuple[float, float, float]]:
    """명시적 high/low/volume 시퀀스로 VPVR bin 분배를 손으로 검증할 수 있게 만드는 전용 하네스.
    NUM_BINS을 테스트 전용 값으로 좁혀 손 계산이 가능하게 하고, 매 봉의 (poc, vah, val) 이력을
    리스트로 반환한다."""
    n = len(highs)
    closes = [(h + l) / 2 for h, l in zip(highs, lows)]
    idx = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    df = pd.DataFrame({
        "candle_time": idx,
        "open": closes, "high": highs, "low": lows, "close": closes,
        "volume": volumes,
    })
    df_bt = df.set_index("candle_time")
    df_bt.index = df_bt.index.tz_localize(None)

    class _VpvrProbeStrategy(bt.Strategy):
        def __init__(self):
            self.probe = price_levels.VolumeProfile(self.data, period=period)
            self.seen_values: list[tuple[float, float, float]] = []

        def next(self):
            self.seen_values.append((
                float(self.probe.lines.poc[0]),
                float(self.probe.lines.vah[0]),
                float(self.probe.lines.val[0]),
            ))

    original_num_bins = price_levels.NUM_BINS
    price_levels.NUM_BINS = num_bins
    try:
        cerebro = bt.Cerebro()
        cerebro.adddata(bt.feeds.PandasData(dataname=df_bt, openinterest=-1))
        cerebro.addstrategy(_VpvrProbeStrategy)
        results = cerebro.run()
        return results[0].seen_values
    finally:
        price_levels.NUM_BINS = original_num_bins


def test_vpvr_produces_nan_during_warmup_before_enough_bars():
    # period=3인데 봉이 2개뿐이라 롤링 윈도우가 아직 안 찼음 — 세 라인 모두 NaN이어야 함.
    highs = [10, 10]
    lows = [0, 0]
    volumes = [100, 100]
    values = _run_vpvr_probe(highs, lows, volumes, period=3, num_bins=4)
    assert all(v != v for poc, vah, val in values for v in (poc, vah, val)), (
        "워밍업 중(봉이 period개 미만)엔 poc/vah/val 전부 NaN이어야 함(v != v는 NaN 체크)"
    )


def test_vpvr_matches_hand_traced_bin_distribution():
    # period=3, num_bins=4로 손 계산 가능한 3봉 시퀀스.
    # window_high=10.0(2번째 봉), window_low=0.0(1번째 봉) → bin_width=2.5.
    # bin0=[0,2.5], bin1=[2.5,5.0], bin2=[5.0,7.5], bin3=[7.5,10.0].
    # 1번째 봉(h=2.5,l=0,v=100)은 bin0에만 전량 겹침 → bin0 += 100.
    # 2번째 봉(h=10,l=7.5,v=10)은 bin3에만 전량 겹침 → bin3 += 10.
    # 3번째 봉(h=5,l=2.5,v=5)은 bin1에만 전량 겹침 → bin1 += 5.
    # 최종 bin_volumes = [100, 5, 0, 10], 합계 115.
    # POC = bin0(최댓값) 중간값 = 0 + 0.5*2.5 = 1.25.
    # Value Area: bin0 하나만으로 100/115 ≈ 87% > 70% 목표 도달 → 확장 없음.
    # VAH = bin0 윗값 = 2.5, VAL = bin0 아랫값 = 0.0.
    highs = [2.5, 10.0, 5.0]
    lows = [0.0, 7.5, 2.5]
    volumes = [100, 10, 5]
    values = _run_vpvr_probe(highs, lows, volumes, period=3, num_bins=4)
    poc, vah, val = values[-1]
    assert poc == pytest.approx(1.25)
    assert vah == pytest.approx(2.5)
    assert val == pytest.approx(0.0)


def test_vpvr_handles_completely_flat_window_without_dividing_by_zero():
    # 윈도우 안 모든 봉이 h==l==100(무변동) — window_high==window_low라 bin 분할이 무의미.
    # ZeroDivisionError 없이 poc=vah=val=100.0으로 처리되어야 함.
    highs = [100.0, 100.0, 100.0]
    lows = [100.0, 100.0, 100.0]
    volumes = [10, 10, 10]
    values = _run_vpvr_probe(highs, lows, volumes, period=3, num_bins=4)
    poc, vah, val = values[-1]
    assert poc == pytest.approx(100.0)
    assert vah == pytest.approx(100.0)
    assert val == pytest.approx(100.0)


def test_vpvr_handles_doji_bar_within_a_non_flat_window():
    # 윈도우 전체는 평평하지 않지만(window_high=10 != window_low=0), 그 안의 한 봉(2번째)만
    # h==l==5(도지)인 경우 — 그 봉 처리에서 크래시(ZeroDivisionError) 없이 값이 나와야 함.
    # 정확한 값 대신 불변식(VAL <= POC <= VAH, 윈도우 범위 안)만 검증한다.
    highs = [10, 5, 10]
    lows = [0, 5, 0]
    volumes = [50, 30, 50]
    values = _run_vpvr_probe(highs, lows, volumes, period=3, num_bins=4)
    poc, vah, val = values[-1]
    assert 0.0 <= val <= poc <= vah <= 10.0


def test_vpvr_default_settings_keep_value_area_ordering_and_stays_within_window_range():
    # 기본 설정(NUM_BINS=24, period=50)의 실제 오실레이팅 데이터로, 워밍업 이후 모든 봉에서
    # VAL <= POC <= VAH이고 셋 다 그 시점 롤링 윈도우([window_low, window_high]) 범위 안에
    # 있는지 스모크 검증한다(정확한 값이 아니라 불변식 검증).
    poc_values = _run_probe("VPVR_POC", {})
    vah_values = _run_probe("VPVR_VAH", {})
    val_values = _run_probe("VPVR_VAL", {})
    df = make_oscillating_df()
    period = 50
    checked_any = False
    for i in range(len(poc_values)):
        poc, vah, val = poc_values[i], vah_values[i], val_values[i]
        if poc != poc:  # 워밍업 구간(NaN)은 건너뜀
            continue
        checked_any = True
        window_start = max(0, i - period + 1)
        window_high = df["high"].iloc[window_start:i + 1].max()
        window_low = df["low"].iloc[window_start:i + 1].min()
        assert val <= poc <= vah, f"bar {i}: VAL<=POC<=VAH 불변식 깨짐 ({val}, {poc}, {vah})"
        assert window_low - 1e-6 <= val, f"bar {i}: VAL이 윈도우 최저가보다 낮음"
        assert vah <= window_high + 1e-6, f"bar {i}: VAH가 윈도우 최고가보다 높음"
    assert checked_any, "워밍업 이후 값이 하나도 없음"
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `pytest tests/test_indicators.py -k vpvr -v`
Expected: FAIL — `KeyError: 'VPVR_POC'`(아직 `INDICATOR_FACTORY`에 없음), 그리고
`AttributeError: module 'engine.indicators.price_levels' has no attribute 'VolumeProfile'`

- [ ] **Step 3: 최소 구현 작성**

`engine/indicators/price_levels.py` 파일 상단 import 줄(`import backtrader as bt`) 다음에 추가:
```python
from collections import deque
```

파일 끝(`create_pivot_s1` 다음)에 추가:
```python
NUM_BINS = 24
VALUE_AREA_PCT = 0.7


class VolumeProfile(bt.Indicator):
    """최근 period개 봉의 고가-저가 범위에 거래량을 겹치는 비율만큼 분배해 가격대별 거래량
    분포(Volume Profile)를 만들고, 그 분포에서 POC(거래량 최다 가격대)와 Value Area
    상단/하단(VAH/VAL)을 뽑아낸다. 틱 데이터 없이 캔들만으로 계산하는 근사치다."""

    lines = ("poc", "vah", "val")
    params = (("period", 50),)

    def __init__(self) -> None:
        period = self.p.period
        self._highs: deque = deque(maxlen=period)
        self._lows: deque = deque(maxlen=period)
        self._volumes: deque = deque(maxlen=period)

    def next(self) -> None:
        self._highs.append(self.data.high[0])
        self._lows.append(self.data.low[0])
        self._volumes.append(self.data.volume[0])

        if len(self._highs) < self.p.period:
            self.lines.poc[0] = float("nan")
            self.lines.vah[0] = float("nan")
            self.lines.val[0] = float("nan")
            return

        window_high = max(self._highs)
        window_low = min(self._lows)

        if window_high == window_low:
            self.lines.poc[0] = window_high
            self.lines.vah[0] = window_high
            self.lines.val[0] = window_high
            return

        bin_width = (window_high - window_low) / NUM_BINS
        bin_volumes = [0.0] * NUM_BINS

        for h, l, v in zip(self._highs, self._lows, self._volumes):
            if h == l:
                idx = min(int((h - window_low) / bin_width), NUM_BINS - 1)
                bin_volumes[idx] += v
                continue
            for i in range(NUM_BINS):
                bin_bottom = window_low + i * bin_width
                bin_top = bin_bottom + bin_width
                overlap = min(h, bin_top) - max(l, bin_bottom)
                if overlap > 0:
                    bin_volumes[i] += v * (overlap / (h - l))

        total_volume = sum(bin_volumes)
        poc_idx = max(range(NUM_BINS), key=lambda i: bin_volumes[i])
        poc_price = window_low + (poc_idx + 0.5) * bin_width

        lo = hi = poc_idx
        accumulated = bin_volumes[poc_idx]
        target = total_volume * VALUE_AREA_PCT
        while accumulated < target and (lo > 0 or hi < NUM_BINS - 1):
            expand_lo = bin_volumes[lo - 1] if lo > 0 else -1.0
            expand_hi = bin_volumes[hi + 1] if hi < NUM_BINS - 1 else -1.0
            if expand_hi >= expand_lo:
                hi += 1
                accumulated += expand_hi
            else:
                lo -= 1
                accumulated += expand_lo

        self.lines.poc[0] = poc_price
        self.lines.vah[0] = window_low + (hi + 1) * bin_width
        self.lines.val[0] = window_low + lo * bin_width


def create_vpvr_poc(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 50))
    return VolumeProfile(data, period=period)


def create_vpvr_vah(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 50))
    return VolumeProfile(data, period=period)


def create_vpvr_val(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 50))
    return VolumeProfile(data, period=period)
```

`engine/indicators/__init__.py`의 import 줄을:
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
다음으로 교체:
```python
from .price_levels import (
    create_fib_382,
    create_fib_500,
    create_fib_618,
    create_pivot_p,
    create_pivot_r1,
    create_pivot_s1,
    create_vpvr_poc,
    create_vpvr_vah,
    create_vpvr_val,
)
```
`INDICATOR_FACTORY` dict에 추가(`"PIVOT_S1": create_pivot_s1,` 다음 줄):
```python
    "VPVR_POC": create_vpvr_poc,
    "VPVR_VAH": create_vpvr_vah,
    "VPVR_VAL": create_vpvr_val,
```

`engine/condition_tree.py`의 `get_indicator_value()`에서 `PIVOT_S1` 분기(`elif indicator_name ==
"PIVOT_S1": return float(obj.lines.s1[0])`) 다음, `else:` 앞에 추가:
```python
    elif indicator_name == "VPVR_POC":
        return float(obj.lines.poc[0])
    elif indicator_name == "VPVR_VAH":
        return float(obj.lines.vah[0])
    elif indicator_name == "VPVR_VAL":
        return float(obj.lines.val[0])
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_indicators.py -v`
Expected: 기존 테스트 전부 PASS + 신규 5개 PASS(`test_all_registered_indicators_produce_values`도
자동으로 `VPVR_POC`/`VPVR_VAH`/`VPVR_VAL`을 포함해 검증함 — 별도 수정 불필요)

- [ ] **Step 5: 커밋**

```bash
git add engine/indicators/price_levels.py engine/indicators/__init__.py engine/condition_tree.py tests/test_indicators.py
git commit -m "feat: add VolumeProfile indicator (POC/VAH/VAL)"
```

---

## Task 2: 카탈로그 등록

**Files:**
- Modify: `backend/main.py` (`INDICATOR_CATALOG`)

**Interfaces:**
- Consumes: Task 1의 `INDICATOR_FACTORY["VPVR_POC"|"VPVR_VAH"|"VPVR_VAL"]`.
- Produces: `GET /api/v1/indicators/catalog` 응답에 `VPVR_POC`/`VPVR_VAH`/`VPVR_VAL` 3개 항목 추가
  (카테고리 `"가격대"` 재사용).

- [ ] **Step 1: 실패하는 테스트 확인**

기존 테스트 `test_get_indicator_catalog_covers_all_registered_indicators`(수정 없이 재사용)가
Task 1에서 `INDICATOR_FACTORY`에 3개가 생겼는데 카탈로그엔 없는 지금 시점에 저절로 실패한다.

Run: `pytest tests/test_backend.py -k test_get_indicator_catalog_covers_all_registered_indicators -v`
Expected: FAIL — `catalog_values`에 `VPVR_POC`/`VPVR_VAH`/`VPVR_VAL`이 빠져 있어 set 비교 실패

- [ ] **Step 2: (Step 1에서 이미 실패 확인함 — 별도 실행 불필요)**

- [ ] **Step 3: 최소 구현 작성**

`backend/main.py`의 `INDICATOR_CATALOG` 리스트에서 `"PIVOT_S1"` 항목 바로 뒤에 추가:
```python
    {
        "value": "VPVR_POC", "label": "VPVR POC (거래량 최다 가격대)", "category": "가격대",
        "params": [{"key": "period", "label": "기간", "default": 50}],
        "description": "최근 period봉의 거래량을 가격대별로 나눠 쌓았을 때, 거래량이 가장 많이 몰린 가격대(Point of Control)입니다. 시장이 '공정하다'고 합의한 가격으로 해석되어 반등/저항이 자주 일어나는 자리로 흔히 씁니다.",
        "example": "period=50이면 최근 50봉의 가격대별 거래량 분포에서 가장 거래가 많았던 가격대입니다.",
    },
    {
        "value": "VPVR_VAH", "label": "VPVR Value Area 상단(VAH)", "category": "가격대",
        "params": [{"key": "period", "label": "기간", "default": 50}],
        "description": "최근 period봉 거래량의 70%가 몰려있는 구간(Value Area)의 상단 가격입니다. 이 위는 상대적으로 거래가 적었던 구간이라 가격이 빠르게 통과하는 경향이 있습니다.",
        "example": "period=50, 연산자 >, threshold를 이 값으로 두면 가격이 Value Area 위로 벗어난 구간을 포착합니다.",
    },
    {
        "value": "VPVR_VAL", "label": "VPVR Value Area 하단(VAL)", "category": "가격대",
        "params": [{"key": "period", "label": "기간", "default": 50}],
        "description": "최근 period봉 거래량의 70%가 몰려있는 구간(Value Area)의 하단 가격입니다. 이 아래는 상대적으로 거래가 적었던 구간이라 가격이 빠르게 통과하는 경향이 있습니다.",
        "example": "period=50, 연산자 <, threshold를 이 값으로 두면 가격이 Value Area 아래로 벗어난 구간을 포착합니다.",
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
git commit -m "feat: register VPVR indicators in the catalog under 가격대 category"
```

---

## Task 3: 조건 빌더 프론트엔드 (threshold 추천값)

**Files:**
- Modify: `frontend/components/StrategyConditionBuilder.tsx`

**Interfaces:**
- Consumes: 백엔드 카탈로그의 `category: "가격대"`, `value: "VPVR_POC"|"VPVR_VAH"|"VPVR_VAL"`(Task 2).

- [ ] **Step 1~2: (프론트 로직 테스트는 이 저장소에 별도 단위테스트 인프라가 없음 — 기존 컨벤션대로
      Step 3 구현 후 `tsc`+Playwright 수동 검증으로 대체한다.)**

- [ ] **Step 3: 구현**

`frontend/components/StrategyConditionBuilder.tsx`의 `PRICE_SCALE_INDICATORS`(현재 `SMA`/`EMA`/`WMA`/
`BB_upper`/`BB_middle`/`BB_lower`/`FIB_382`/`FIB_500`/`FIB_618`/`PIVOT_P`/`PIVOT_R1`/`PIVOT_S1`)에
추가:
```typescript
const PRICE_SCALE_INDICATORS = new Set([
  'SMA', 'EMA', 'WMA', 'BB_upper', 'BB_middle', 'BB_lower',
  'FIB_382', 'FIB_500', 'FIB_618', 'PIVOT_P', 'PIVOT_R1', 'PIVOT_S1',
  'VPVR_POC', 'VPVR_VAH', 'VPVR_VAL',
]);
```
(`recommendedThreshold()`가 `PRICE_SCALE_INDICATORS`에 있는 지표는 `currentPrice ?? 0`을 threshold로
자동 채우는 기존 로직을 그대로 타므로, 이 한 줄 추가만으로 셋 다 "현재가"가 자동으로 채워진다 —
POC/VAH/VAL은 절대 가격값이라 이 패턴에 그대로 맞는다.)

- [ ] **Step 4: 확인**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

브라우저(Playwright)에서 `/`(조건 빌더)의 "가격대" 카테고리에 "VPVR POC (거래량 최다 가격대)",
"VPVR Value Area 상단(VAH)", "VPVR Value Area 하단(VAL)"이 뜨는지, 셋 중 하나를 선택하면 threshold가
현재가로 자동 채워지는지 확인.

- [ ] **Step 5: 커밋**

```bash
git add frontend/components/StrategyConditionBuilder.tsx
git commit -m "feat: add VPVR threshold recommendation to condition builder"
```

---

## Task 4: 지표 가이드 탭 콘텐츠

**Files:**
- Modify: `frontend/lib/indicator-calc.ts`
- Modify: `frontend/lib/indicator-guide.ts`
- Modify: `frontend/lib/indicator-example-builder.ts`

**Interfaces:**
- Produces: `calc.volumeProfile(highs, lows, volumes, period, numBins?, valueAreaPct?) ->
  { poc: number[]; vah: number[]; val: number[] }`(엔진의 `VolumeProfile`과 동일한 알고리즘을
  TypeScript로 재현).

- [ ] **Step 1~2: (지표 가이드 탭도 별도 단위테스트가 없는 순수 프레젠테이션 레이어 — 기존 컨벤션대로
      `tsc`+Playwright로 검증한다. Step 3 이후로 진행.)**

- [ ] **Step 3: 구현**

`frontend/lib/indicator-calc.ts` 파일 끝(`round` 함수 다음)에 추가:
```typescript
export function volumeProfile(
  highs: number[],
  lows: number[],
  volumes: number[],
  period: number,
  numBins = 24,
  valueAreaPct = 0.7
): { poc: number[]; vah: number[]; val: number[] } {
  const poc = new Array(highs.length).fill(NaN);
  const vah = new Array(highs.length).fill(NaN);
  const val = new Array(highs.length).fill(NaN);

  for (let i = period - 1; i < highs.length; i++) {
    const windowHighs = highs.slice(i - period + 1, i + 1);
    const windowLows = lows.slice(i - period + 1, i + 1);
    const windowVolumes = volumes.slice(i - period + 1, i + 1);
    const windowHigh = Math.max(...windowHighs);
    const windowLow = Math.min(...windowLows);

    if (windowHigh === windowLow) {
      poc[i] = windowHigh;
      vah[i] = windowHigh;
      val[i] = windowHigh;
      continue;
    }

    const binWidth = (windowHigh - windowLow) / numBins;
    const binVolumes = new Array(numBins).fill(0);

    for (let b = 0; b < period; b++) {
      const h = windowHighs[b];
      const l = windowLows[b];
      const v = windowVolumes[b];
      if (h === l) {
        const idx = Math.min(Math.floor((h - windowLow) / binWidth), numBins - 1);
        binVolumes[idx] += v;
        continue;
      }
      for (let bin = 0; bin < numBins; bin++) {
        const binBottom = windowLow + bin * binWidth;
        const binTop = binBottom + binWidth;
        const overlap = Math.min(h, binTop) - Math.max(l, binBottom);
        if (overlap > 0) {
          binVolumes[bin] += v * (overlap / (h - l));
        }
      }
    }

    const totalVolume = binVolumes.reduce((sum, x) => sum + x, 0);
    let pocIdx = 0;
    for (let bin = 1; bin < numBins; bin++) {
      if (binVolumes[bin] > binVolumes[pocIdx]) pocIdx = bin;
    }
    poc[i] = windowLow + (pocIdx + 0.5) * binWidth;

    let lo = pocIdx;
    let hi = pocIdx;
    let accumulated = binVolumes[pocIdx];
    const target = totalVolume * valueAreaPct;
    while (accumulated < target && (lo > 0 || hi < numBins - 1)) {
      const expandLo = lo > 0 ? binVolumes[lo - 1] : -1;
      const expandHi = hi < numBins - 1 ? binVolumes[hi + 1] : -1;
      if (expandHi >= expandLo) {
        hi += 1;
        accumulated += expandHi;
      } else {
        lo -= 1;
        accumulated += expandLo;
      }
    }

    vah[i] = windowLow + (hi + 1) * binWidth;
    val[i] = windowLow + lo * binWidth;
  }

  return { poc, vah, val };
}
```

`frontend/lib/indicator-guide.ts`의 `INDICATOR_GUIDE` 객체에서 `VPIN` 항목(파일 마지막 항목) 바로 뒤,
객체를 닫는 `};` 앞에 추가:
```typescript
  VPVR_POC: {
    meaning: '최근 period봉의 거래량을 가격대별로 나눠 쌓았을 때, 거래량이 가장 많이 몰린 가격대(Point of Control)입니다. 시장이 "공정하다"고 합의한 가격으로 해석되어 반등/저항이 자주 일어나는 자리로 흔히 씁니다.',
    params: [{ key: 'period', role: '가격대별 거래량 분포를 계산할 최근 봉 개수. 다른 지표보다 길게 잡는 게 보통입니다(기본값 50).' }],
    formula: '최근 period봉의 고가-저가 범위에 각 봉의 거래량을 겹치는 비율만큼 24개 가격 구간(bin)에 나눠 담고, 누적 거래량이 가장 큰 구간의 중간값을 POC로 씁니다.',
    thresholdExample: '이 앱의 조건식은 지표값과 숫자 threshold만 비교합니다. threshold는 보통 현재 가격대 근처 값을 넣어 레벨 필터로 씁니다.',
    usage: '종가가 POC 근처로 되돌아올 때 반등을 노리는 매수 조건, 혹은 POC를 강하게 이탈할 때 추세 전환 신호로 씁니다.',
  },
  VPVR_VAH: {
    meaning: '최근 period봉 거래량의 70%가 몰려있는 구간(Value Area)의 상단 가격입니다. 이 위는 상대적으로 거래가 적었던 구간이라 가격이 빠르게 통과하는 경향이 있습니다.',
    params: [{ key: 'period', role: 'VPVR_POC와 같은 롤링 윈도우 크기(기본값 50).' }],
    formula: 'POC가 속한 가격 구간에서 시작해, 위/아래 인접 구간 중 거래량이 더 큰 쪽을 번갈아 편입시키며 누적 거래량이 전체의 70%에 도달할 때까지 확장한 뒤, 그 상단 경계값을 VAH로 씁니다.',
    thresholdExample: '이 앱의 조건식은 지표값과 숫자 threshold만 비교합니다. threshold는 보통 현재 가격대 근처 값을 넣어 레벨 필터로 씁니다.',
    usage: '종가가 VAH를 상향 돌파하면 거래가 적었던 구간으로 빠르게 움직일 수 있다고 보고 돌파 매수 신호로, 혹은 VAH를 저항으로 보고 매도 신호로 반대로 쓰기도 합니다.',
  },
  VPVR_VAL: {
    meaning: '최근 period봉 거래량의 70%가 몰려있는 구간(Value Area)의 하단 가격입니다. 이 아래는 상대적으로 거래가 적었던 구간이라 가격이 빠르게 통과하는 경향이 있습니다.',
    params: [{ key: 'period', role: 'VPVR_POC와 같은 롤링 윈도우 크기(기본값 50).' }],
    formula: 'POC가 속한 가격 구간에서 시작해, 위/아래 인접 구간 중 거래량이 더 큰 쪽을 번갈아 편입시키며 누적 거래량이 전체의 70%에 도달할 때까지 확장한 뒤, 그 하단 경계값을 VAL로 씁니다.',
    thresholdExample: '이 앱의 조건식은 지표값과 숫자 threshold만 비교합니다. threshold는 보통 현재 가격대 근처 값을 넣어 레벨 필터로 씁니다.',
    usage: 'VAL 근처에서 반등을 노리는 매수 조건, 혹은 VAL 하향 이탈을 손절/추가 하락 신호로 씁니다.',
  },
```

`frontend/lib/indicator-example-builder.ts`의 `buildGuideExample` switch문에서 `case 'PIVOT_P': case
'PIVOT_R1': case 'PIVOT_S1': { ... }` 블록이 끝나는 닫는 중괄호(`}`) 바로 뒤, `case 'CCI': {` 시작 줄
바로 앞에 추가:
```typescript
    case 'VPVR_POC':
    case 'VPVR_VAH':
    case 'VPVR_VAL': {
      const period = 50;
      const { poc, vah, val } = calc.volumeProfile(highs, lows, volumes, period);
      const line = value === 'VPVR_POC' ? poc : value === 'VPVR_VAH' ? vah : val;
      const start = firstValidIndex(line);
      const rows = windowFrom(start, 6).map((bar, i) => ({
        bar: bar.bar,
        cells: { close: n(bar.close, 0), value: n(line[start + i]) },
      }));
      return {
        columns: [
          { key: 'close', label: '종가' },
          { key: 'value', label: value },
        ],
        rows,
        chart: {
          type: 'line',
          data: SAMPLE_BARS.map((bar, i) => ({ bar: bar.bar, close: bar.close, value: clean(line[i]) })),
          lines: [
            { key: 'close', name: '종가', color: '#94a3b8' },
            { key: 'value', name: `${value}`, color: '#0891b2', dash: true },
          ],
        },
      };
    }
```

- [ ] **Step 4: 확인**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

Playwright로 `/guide`를 열어 "가격대" 중분류에 "VPVR POC (거래량 최다 가격대)" 등 3개 항목이 뜨는지,
클릭 시 표(종가/값) + 종가 위에 겹쳐 그려진 라인 차트가 정상 렌더되는지 확인.

- [ ] **Step 5: 커밋**

```bash
git add frontend/lib/indicator-calc.ts frontend/lib/indicator-guide.ts frontend/lib/indicator-example-builder.ts
git commit -m "feat: add VPVR to the indicator guide tab"
```

---

## 이 플랜에 포함하지 않은 것

`docs/superpowers/specs/2026-07-31-vpvr-volume-profile-design.md`의 "이 스펙에 포함하지 않은 것" 절과
동일한 이유로 범위 밖이다.

- **"가격이 Value Area 안/밖에 있는지"(불리언) 신호**: 조건 빌더는 지표값과 숫자 threshold만 비교하는
  구조라 두 지표 간 비교(예: 종가 vs VAL/VAH)는 애초에 지원하지 않는다.
- **Low Volume Node(LVN) 탐지**: POC/VAH/VAL과는 다른 산출물이라 범위 밖.
- **세션/일 단위 앵커링**: 순수 롤링 윈도우로만 근사(VPIN/상관계수 지표들과 같은 결정).
- **VPVR의 실전 예측력 검증**: 이 플랜은 계산 방법론을 올바르게 구현하는 것까지만 다룬다.

## Verification (전체)

- `pytest tests/ -v` — 전체 스위트 그린(기존 스위트 + 이번 플랜 신규 5개).
- `cd frontend && npx tsc --noEmit` — 클린.
- Playwright: `/`에서 "가격대" 카테고리에 VPVR 3개 지표가 뜨고 threshold가 현재가로 자동 채워지는지,
  실제 조건으로 백테스트 1건을 끝까지 실행해 결과 화면까지 나오는지. `/guide`에서 신규 항목 3개가
  표+라인 차트와 함께 렌더되는지.
- 백엔드는 코드 수정마다 재시작 필요(`uvicorn --reload`가 이 저장소에서 간헐적으로 안 먹는 이슈가
  기존에 있었음 — 반드시 수동 재시작 후 확인). Task 1은 백엔드 코드를 건드리지 않으므로 재시작
  불필요 — Task 2부터 재시작이 필요하다.
