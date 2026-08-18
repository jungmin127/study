# 조건식 threshold 정규화 확장 (가격대/추세/거래대금/MARKET_TREND) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 코인 절대 시세/스케일에 종속된 14개 지표(가격대 9 + 추세 3 + 거래대금 1 + 시장심리 1)에 `(종가-레벨)/레벨×100` 형태의 정규화 짝 지표(`_PCT` 접미사)를 독립 카탈로그 항목으로 추가한다.

**Architecture:** 각 지표는 (1) 백테스트 엔진(`engine/indicators/*.py` + `engine/condition_tree.py`), (2) 라이브 트레이딩 pandas 구현(`trading/live_indicators.py`), (3) 백엔드 카탈로그(`backend/main.py`), (4) 프론트엔드 조건식 빌더/가이드(`frontend/`) 네 곳에 동시에 반영해야 한다 — 이 프로젝트는 백테스트/라이브 계산 로직을 "의도적으로 중복된 쌍둥이 함수"로 유지하고, 백엔드에는 `INDICATOR_FACTORY`와 `INDICATOR_CATALOG`가 정확히 일치해야 하는 기존 테스트(`test_get_indicator_catalog_covers_all_registered_indicators`)가, 라이브 쪽에는 `LIVE_INDICATOR_FACTORY`와 워밍업 파라미터 표가 정확히 일치해야 하는 기존 테스트(`test_a_group_params_cover_every_a_group_indicator`)가 이미 있다 — 새 지표를 한쪽에만 등록하면 CI가 아니라 이 두 테스트가 즉시 실패한다.

**Tech Stack:** Python(backtrader, pandas), FastAPI, TypeScript/React(Next.js), pytest.

## Global Constraints

- 정규화 공식은 전부 `(종가 - 레벨) / 레벨 × 100` (TRADE_VALUE_PCT만 "레벨" 자리에 거래대금 자체 이동평균). 양수=종가가 레벨 위, 음수=아래.
- 명명 규칙은 전부 `_PCT` 접미사.
- 신규 14개의 UI 기본 threshold 추천값은 전부 0(`ZERO_CROSS_INDICATORS`에 등록).
- TRADE_VALUE_PCT의 내부 SMA 기간 파라미터 기본값은 20.
- 기존 14개 원본 카탈로그 항목은 삭제·변경 없이 그대로 유지.
- 나눗셈 분모가 0이면(합성 테스트 데이터 등 극단 케이스) 0.0을 반환 — ATR_PCT/BB_PERCENT_B의 기존 관례.
- 참조 스펙: `docs/superpowers/specs/2026-08-19-condition-threshold-normalization-design.md`.

---

## Task 1: FIB_382_PCT / FIB_500_PCT / FIB_618_PCT

**Files:**
- Modify: `engine/indicators/price_levels.py` (파일 끝에 `FibPct` 클래스 + 3개 factory 추가)
- Modify: `engine/indicators/__init__.py`
- Test: `tests/test_indicators.py`
- Modify: `trading/live_indicators.py`
- Test: `tests/test_live_indicators_price_levels.py`
- Modify: `tests/test_signal_engine_warmup.py` (`_A_GROUP_PARAMS`)
- Modify: `backend/main.py` (`INDICATOR_CATALOG`)
- Modify: `frontend/components/StrategyConditionBuilder.tsx` (`ZERO_CROSS_INDICATORS`)
- Modify: `frontend/lib/indicator-guide.ts`

**Interfaces:**
- Produces: `engine.indicators.price_levels.FibPct` (bt.Indicator, lines=("pct",), params=(period, ratio)); `engine.indicators.create_fib_382_pct/create_fib_500_pct/create_fib_618_pct(data, **params) -> bt.Indicator`; `trading.live_indicators.create_fib_382_pct/create_fib_500_pct/create_fib_618_pct(df, **params) -> pd.Series`; `INDICATOR_FACTORY`/`LIVE_INDICATOR_FACTORY`에 3개 키 등록.

- [ ] **Step 1: 백테스트 엔진 실패하는 테스트 작성**

`tests/test_indicators.py`의 `test_fib_618_matches_manual_swing_calculation` 함수 바로 다음에 추가:

```python
def test_fib_382_pct_matches_manual_pct_from_level():
    values = _run_probe("FIB_382_PCT", {"period": 5})
    df = make_oscillating_df()
    hh = df["high"].rolling(5).max().iloc[-1]
    ll = df["low"].rolling(5).min().iloc[-1]
    level = hh - (hh - ll) * 0.382
    close = df["close"].iloc[-1]
    manual = (close - level) / level * 100
    assert abs(values[-1] - manual) < 1e-6


def test_fib_500_pct_matches_manual_pct_from_level():
    values = _run_probe("FIB_500_PCT", {"period": 5})
    df = make_oscillating_df()
    hh = df["high"].rolling(5).max().iloc[-1]
    ll = df["low"].rolling(5).min().iloc[-1]
    level = hh - (hh - ll) * 0.5
    close = df["close"].iloc[-1]
    manual = (close - level) / level * 100
    assert abs(values[-1] - manual) < 1e-6


def test_fib_618_pct_matches_manual_pct_from_level():
    values = _run_probe("FIB_618_PCT", {"period": 5})
    df = make_oscillating_df()
    hh = df["high"].rolling(5).max().iloc[-1]
    ll = df["low"].rolling(5).min().iloc[-1]
    level = hh - (hh - ll) * 0.618
    close = df["close"].iloc[-1]
    manual = (close - level) / level * 100
    assert abs(values[-1] - manual) < 1e-6


def test_fib_pct_handles_zero_level_without_crashing():
    # 레벨이 0인 극단 케이스(합성 데이터) — ZeroDivisionError 없이 0.0을 반환해야 함.
    idx = pd.date_range("2026-01-01", periods=10, freq="h", tz="UTC")
    df = pd.DataFrame({
        "candle_time": idx, "open": [0.0] * 10, "high": [0.0] * 10,
        "low": [0.0] * 10, "close": [0.0] * 10, "volume": [1.0] * 10,
    })
    df_bt = df.set_index("candle_time")
    df_bt.index = df_bt.index.tz_localize(None)
    cerebro = bt.Cerebro()
    cerebro.adddata(bt.feeds.PandasData(dataname=df_bt, openinterest=-1))
    cerebro.addstrategy(_ProbeStrategy, indicator="FIB_382_PCT", indicator_params={"period": 3})
    results = cerebro.run()
    assert results[0].seen_values[-1] == pytest.approx(0.0)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_indicators.py -k fib_pct -v`
Expected: FAIL — `KeyError: 'FIB_382_PCT'` (INDICATOR_FACTORY에 아직 없음).

- [ ] **Step 3: `FibPct` 클래스와 3개 factory 구현**

`engine/indicators/price_levels.py` 파일 끝(`create_vpvr_val` 다음)에 추가:

```python
class FibPct(bt.Indicator):
    """피보나치 되돌림 레벨 대비 종가의 이격을 %로 나타낸 정규화 버전.
    FIB_382/500/618과 같은 레벨 계산에, 종가와의 이격도만 추가로 계산한다."""

    lines = ("pct",)
    params = (("period", 20), ("ratio", 0.382))

    def __init__(self) -> None:
        hh = bt.indicators.Highest(self.data.high, period=self.p.period)
        ll = bt.indicators.Lowest(self.data.low, period=self.p.period)
        self.level = hh - (hh - ll) * self.p.ratio

    def next(self) -> None:
        level = self.level[0]
        self.lines.pct[0] = (self.data.close[0] - level) / level * 100 if level else 0.0


def create_fib_382_pct(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 20))
    return FibPct(data, period=period, ratio=0.382)


def create_fib_500_pct(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 20))
    return FibPct(data, period=period, ratio=0.5)


def create_fib_618_pct(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 20))
    return FibPct(data, period=period, ratio=0.618)
```

`engine/indicators/__init__.py`의 `from .price_levels import (...)` 블록에 3개 추가:

```python
from .price_levels import (
    create_fib_382,
    create_fib_382_pct,
    create_fib_500,
    create_fib_500_pct,
    create_fib_618,
    create_fib_618_pct,
    create_pivot_p,
    create_pivot_r1,
    create_pivot_s1,
    create_vpvr_poc,
    create_vpvr_vah,
    create_vpvr_val,
)
```

`INDICATOR_FACTORY` 딕셔너리의 `"FIB_618": create_fib_618,` 다음 줄에 3개 추가:

```python
    "FIB_382_PCT": create_fib_382_pct,
    "FIB_500_PCT": create_fib_500_pct,
    "FIB_618_PCT": create_fib_618_pct,
```

- [ ] **Step 4: 백테스트 엔진 테스트 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_indicators.py -k fib_pct -v`
Expected: PASS (4 tests)

- [ ] **Step 5: 워밍업 회귀 테스트 표 갱신**

`tests/test_signal_engine_warmup.py`의 `_A_GROUP_PARAMS` 딕셔너리에서 `"FIB_618": {"period": 20},` 다음 줄에 추가:

```python
    "FIB_382_PCT": {"period": 20},
    "FIB_500_PCT": {"period": 20},
    "FIB_618_PCT": {"period": 20},
```

(이 시점엔 아직 `LIVE_INDICATOR_FACTORY`에 이 키들이 없으므로 `test_a_group_params_cover_every_a_group_indicator`는 여전히 실패한다 — Step 7에서 등록 후 통과.)

- [ ] **Step 6: 라이브 pandas 구현 (테스트 먼저)**

`tests/test_live_indicators_price_levels.py` 상단 import 블록에 3개 추가:

```python
from trading.live_indicators import (
    LIVE_INDICATOR_FACTORY,
    create_fib_382,
    create_fib_382_pct,
    create_fib_500,
    create_fib_500_pct,
    create_fib_618,
    create_fib_618_pct,
    create_pivot_p,
    create_pivot_r1,
    create_pivot_s1,
)
```

`test_pivot_s1_matches_backtrader` 함수 다음에 추가:

```python
def test_fib_382_pct_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("FIB_382_PCT", {"period": 20}, create_fib_382_pct(df, period=20))


def test_fib_500_pct_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("FIB_500_PCT", {"period": 20}, create_fib_500_pct(df, period=20))


def test_fib_618_pct_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("FIB_618_PCT", {"period": 20}, create_fib_618_pct(df, period=20))


def test_live_indicator_factory_registers_fib_pct():
    assert LIVE_INDICATOR_FACTORY["FIB_382_PCT"] is create_fib_382_pct
    assert LIVE_INDICATOR_FACTORY["FIB_500_PCT"] is create_fib_500_pct
    assert LIVE_INDICATOR_FACTORY["FIB_618_PCT"] is create_fib_618_pct
```

Run: `PYTHONPATH=. python -m pytest tests/test_live_indicators_price_levels.py -k fib_pct -v`
Expected: FAIL — `ImportError: cannot import name 'create_fib_382_pct'`

- [ ] **Step 7: `trading/live_indicators.py`에 3개 함수 구현**

`create_fib_618` 함수 다음에 추가:

```python
def create_fib_382_pct(df: pd.DataFrame, **params) -> pd.Series:
    level = create_fib_382(df, **params)
    return (df["close"] - level) / level * 100


def create_fib_500_pct(df: pd.DataFrame, **params) -> pd.Series:
    level = create_fib_500(df, **params)
    return (df["close"] - level) / level * 100


def create_fib_618_pct(df: pd.DataFrame, **params) -> pd.Series:
    level = create_fib_618(df, **params)
    return (df["close"] - level) / level * 100
```

`LIVE_INDICATOR_FACTORY` 딕셔너리의 `"FIB_618": create_fib_618,` 다음 줄에 추가:

```python
    "FIB_382_PCT": create_fib_382_pct,
    "FIB_500_PCT": create_fib_500_pct,
    "FIB_618_PCT": create_fib_618_pct,
```

- [ ] **Step 8: 라이브 테스트 + 워밍업 테스트 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_live_indicators_price_levels.py tests/test_signal_engine_warmup.py -v`
Expected: PASS (전부)

- [ ] **Step 9: 백엔드 카탈로그 등록 (테스트 먼저)**

Run: `PYTHONPATH=. python -m pytest tests/test_backend.py -k covers_all_registered -v`
Expected: FAIL — `catalog_values == set(INDICATOR_FACTORY.keys())...` 불일치(3개 누락).

`backend/main.py`의 `INDICATOR_CATALOG` 리스트에서 `FIB_618` 항목(`"value": "FIB_618", ...`) 딕셔너리 바로 다음에 추가:

```python
    {
        "value": "FIB_382_PCT", "label": "피보나치 38.2% (정규화)", "category": "가격대",
        "params": [{"key": "period", "label": "기간", "default": 20}],
        "description": "최근 period봉의 스윙 고점·저점으로 계산한 피보나치 38.2% 되돌림 레벨 대비, 종가가 몇 % 위/아래에 있는지 나타냅니다. 코인 시세와 무관하게 항상 같은 범위입니다.",
        "example": "레벨이 95,000원, 종가가 100,000원이면 FIB_382_PCT ≈ +5.26입니다(레벨보다 5.26% 위).",
    },
    {
        "value": "FIB_500_PCT", "label": "피보나치 50% (정규화)", "category": "가격대",
        "params": [{"key": "period", "label": "기간", "default": 20}],
        "description": "최근 period봉의 스윙 구간 정중앙(50%) 되돌림 레벨 대비 종가의 이격을 %로 나타냅니다.",
        "example": "레벨 대비 종가가 3% 아래면 FIB_500_PCT = -3입니다.",
    },
    {
        "value": "FIB_618_PCT", "label": "피보나치 61.8% (정규화)", "category": "가격대",
        "params": [{"key": "period", "label": "기간", "default": 20}],
        "description": "황금비율 61.8% 되돌림 레벨 대비 종가의 이격을 %로 나타냅니다.",
        "example": "FIB_618_PCT > 0이면 종가가 61.8% 되돌림 레벨보다 위에 있다는 뜻입니다.",
    },
```

Run: `PYTHONPATH=. python -m pytest tests/test_backend.py -k covers_all_registered -v`
Expected: PASS

- [ ] **Step 10: 프론트엔드 — 조건식 빌더 threshold 기본값**

`frontend/components/StrategyConditionBuilder.tsx`의 `ZERO_CROSS_INDICATORS` Set 리터럴을 수정:

```typescript
const ZERO_CROSS_INDICATORS = new Set([
  'MACD_line', 'MACD_signal', 'MARKET_TREND', 'MOMENTUM_PCT', 'KOREA_PREMIUM', 'MACD_PPO', 'MACD_PPO_signal',
  'FIB_382_PCT', 'FIB_500_PCT', 'FIB_618_PCT',
]);
```

- [ ] **Step 11: 프론트엔드 — 지표 가이드**

`frontend/lib/indicator-guide.ts`의 `FIB_382` 항목 `usage` 필드를 수정:

```typescript
    usage: '상승 추세 중 조정이 38.2%선에서 멈추는지 확인해, 그 근처에서 반등을 노리는 눌림목 매수 조건으로 씁니다. 코인 시세와 무관한 정규화 버전이 필요하면 FIB_382_PCT를 대신 쓰세요.',
```

`FIB_500` 항목 `usage` 필드를 수정:

```typescript
    usage: '38.2%/61.8%와 함께 3단계 되돌림 구간을 나눠, 가격이 어느 구간에 있는지로 조정의 깊이를 가늠할 때 씁니다. 코인 시세와 무관한 정규화 버전이 필요하면 FIB_500_PCT를 대신 쓰세요.',
```

`FIB_618` 항목 `usage` 필드를 수정:

```typescript
    usage: '61.8%선까지 눌리고도 지지되면 추세가 아직 살아있다고 보고, 반대로 깨지면 추세 전환으로 보는 필터로 씁니다. 코인 시세와 무관한 정규화 버전이 필요하면 FIB_618_PCT를 대신 쓰세요.',
```

`VPVR_VAL` 항목 바로 앞(즉 `FIB_618` 항목보다는 뒤, 원본들 근처 아무 위치나 무방 — 여기서는 `PIVOT_S1` 항목 앞)에 3개 신규 최상위 항목 추가:

```typescript
  FIB_382_PCT: {
    meaning:
      '피보나치 38.2% 되돌림 레벨 대비, 종가가 몇 % 위/아래에 있는지 나타낸 정규화 지표입니다. 레벨 계산 자체는 FIB_382와 동일합니다 — 계산 배경은 FIB_382 가이드를 참고하세요.',
    params: [{ key: 'period', role: '스윙 고점/저점을 찾을 봉 개수. FIB_382와 동일한 의미.' }],
    formula: 'FIB_382_PCT = (종가 − FIB_382 레벨) ÷ FIB_382 레벨 × 100',
    thresholdExample:
      '값이 +5면 종가가 레벨보다 5% 위, -3이면 3% 아래에 있다는 뜻입니다. FIB_382와 달리 코인 시세와 무관하게 항상 같은 스케일이라 여러 코인에 같은 threshold를 그대로 쓸 수 있습니다.',
    usage: 'FIB_382를 절대가격 필터로 쓰기 애매할 때(코인마다 가격 스케일이 달라서), 대신 이 지표로 "레벨 대비 몇 % 떨어져 있는지"를 오실레이터처럼 씁니다.',
  },
  FIB_500_PCT: {
    meaning:
      '피보나치 50% 되돌림 레벨 대비, 종가가 몇 % 위/아래에 있는지 나타낸 정규화 지표입니다 — 계산 배경은 FIB_500 가이드를 참고하세요.',
    params: [{ key: 'period', role: '스윙 고점/저점을 찾을 봉 개수. FIB_500과 동일한 의미.' }],
    formula: 'FIB_500_PCT = (종가 − FIB_500 레벨) ÷ FIB_500 레벨 × 100',
    thresholdExample: '값이 +5면 종가가 레벨보다 5% 위, -3이면 3% 아래에 있다는 뜻입니다. 여러 코인에 같은 threshold를 그대로 쓸 수 있습니다.',
    usage: 'FIB_500을 절대가격 필터로 쓰기 애매할 때, 대신 이 지표로 "레벨 대비 몇 % 떨어져 있는지"를 오실레이터처럼 씁니다.',
  },
  FIB_618_PCT: {
    meaning:
      '피보나치 61.8% 되돌림 레벨 대비, 종가가 몇 % 위/아래에 있는지 나타낸 정규화 지표입니다 — 계산 배경은 FIB_618 가이드를 참고하세요.',
    params: [{ key: 'period', role: '스윙 고점/저점을 찾을 봉 개수. FIB_618과 동일한 의미.' }],
    formula: 'FIB_618_PCT = (종가 − FIB_618 레벨) ÷ FIB_618 레벨 × 100',
    thresholdExample: '값이 +5면 종가가 레벨보다 5% 위, -3이면 3% 아래에 있다는 뜻입니다. 여러 코인에 같은 threshold를 그대로 쓸 수 있습니다.',
    usage: 'FIB_618을 절대가격 필터로 쓰기 애매할 때, 대신 이 지표로 "레벨 대비 몇 % 떨어져 있는지"를 오실레이터처럼 씁니다.',
  },
```

- [ ] **Step 12: 전체 백엔드 테스트 스위트 확인**

Run: `PYTHONPATH=. python -m pytest tests/ -v`
Expected: PASS (전부, 신규 포함)

- [ ] **Step 13: 커밋**

```bash
git add engine/indicators/price_levels.py engine/indicators/__init__.py trading/live_indicators.py backend/main.py frontend/components/StrategyConditionBuilder.tsx frontend/lib/indicator-guide.ts tests/test_indicators.py tests/test_live_indicators_price_levels.py tests/test_signal_engine_warmup.py
git commit -m "feat: FIB_382/500/618_PCT 정규화 지표 추가"
```

---

## Task 2: PIVOT_P_PCT / PIVOT_R1_PCT / PIVOT_S1_PCT

**Files:**
- Modify: `engine/condition_tree.py` (`get_indicator_value` 분기 3개)
- Modify: `engine/indicators/__init__.py` (`INDICATOR_FACTORY`에 별칭 등록)
- Test: `tests/test_condition_tree.py` 또는 `tests/test_indicators.py`
- Modify: `trading/live_indicators.py`
- Test: `tests/test_live_indicators_price_levels.py`
- Modify: `tests/test_signal_engine_warmup.py`
- Modify: `backend/main.py`
- Modify: `frontend/components/StrategyConditionBuilder.tsx`
- Modify: `frontend/lib/indicator-guide.ts`

**Interfaces:**
- Consumes: `engine.indicators.price_levels.PivotPoints`(Task 1 이전부터 존재, lines=("p","r1","s1")), `engine.indicators.create_pivot_p/r1/s1`(기존).
- Produces: `INDICATOR_FACTORY["PIVOT_P_PCT"/"PIVOT_R1_PCT"/"PIVOT_S1_PCT"]`가 각각 `create_pivot_p`/`create_pivot_r1`/`create_pivot_s1`을 그대로 가리킴(별칭). `trading.live_indicators.create_pivot_p_pct/r1_pct/s1_pct(df, **params) -> pd.Series`.

- [ ] **Step 1: 백테스트 엔진 실패하는 테스트 작성**

`tests/test_indicators.py`의 `test_pivot_s1_matches_manual_formula` 함수 다음에 추가:

```python
def test_pivot_p_pct_matches_manual_pct_from_level():
    values = _run_probe("PIVOT_P_PCT", {})
    df = make_oscillating_df()
    level = (df["high"].iloc[-2] + df["low"].iloc[-2] + df["close"].iloc[-2]) / 3.0
    close = df["close"].iloc[-1]
    manual = (close - level) / level * 100
    assert abs(values[-1] - manual) < 1e-6


def test_pivot_r1_pct_matches_manual_pct_from_level():
    values = _run_probe("PIVOT_R1_PCT", {})
    df = make_oscillating_df()
    pivot = (df["high"].iloc[-2] + df["low"].iloc[-2] + df["close"].iloc[-2]) / 3.0
    level = pivot * 2 - df["low"].iloc[-2]
    close = df["close"].iloc[-1]
    manual = (close - level) / level * 100
    assert abs(values[-1] - manual) < 1e-6


def test_pivot_s1_pct_matches_manual_pct_from_level():
    values = _run_probe("PIVOT_S1_PCT", {})
    df = make_oscillating_df()
    pivot = (df["high"].iloc[-2] + df["low"].iloc[-2] + df["close"].iloc[-2]) / 3.0
    level = pivot * 2 - df["high"].iloc[-2]
    close = df["close"].iloc[-1]
    manual = (close - level) / level * 100
    assert abs(values[-1] - manual) < 1e-6
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_indicators.py -k pivot_pct -v`
Expected: FAIL — `KeyError: 'PIVOT_P_PCT'`

- [ ] **Step 3: `get_indicator_value` 분기 + factory 별칭 등록**

`engine/condition_tree.py`의 `get_indicator_value()`에서 `elif indicator_name == "PIVOT_S1":` 분기 다음에 추가:

```python
    elif indicator_name == "PIVOT_P_PCT":
        close, level = float(obj.data.close[0]), float(obj.lines.p[0])
        return (close - level) / level * 100 if level else 0.0
    elif indicator_name == "PIVOT_R1_PCT":
        close, level = float(obj.data.close[0]), float(obj.lines.r1[0])
        return (close - level) / level * 100 if level else 0.0
    elif indicator_name == "PIVOT_S1_PCT":
        close, level = float(obj.data.close[0]), float(obj.lines.s1[0])
        return (close - level) / level * 100 if level else 0.0
```

`engine/indicators/__init__.py`의 `INDICATOR_FACTORY` 딕셔너리에서 `"PIVOT_S1": create_pivot_s1,` 다음 줄에 추가(새 import는 필요 없음 — 이미 임포트된 함수를 재사용):

```python
    "PIVOT_P_PCT": create_pivot_p,
    "PIVOT_R1_PCT": create_pivot_r1,
    "PIVOT_S1_PCT": create_pivot_s1,
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_indicators.py -k pivot_pct -v`
Expected: PASS (3 tests)

- [ ] **Step 5: 워밍업 표 갱신**

`tests/test_signal_engine_warmup.py`의 `_A_GROUP_PARAMS`에서 `"PIVOT_S1": {},` 다음 줄에 추가:

```python
    "PIVOT_P_PCT": {},
    "PIVOT_R1_PCT": {},
    "PIVOT_S1_PCT": {},
```

- [ ] **Step 6: 라이브 pandas 구현 (테스트 먼저)**

`tests/test_live_indicators_price_levels.py` 상단 import 튜플에 3개 추가:

```python
    create_pivot_p,
    create_pivot_p_pct,
    create_pivot_r1,
    create_pivot_r1_pct,
    create_pivot_s1,
    create_pivot_s1_pct,
```

`test_pivot_s1_matches_backtrader` 함수 다음에 추가:

```python
def test_pivot_p_pct_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("PIVOT_P_PCT", {}, create_pivot_p_pct(df))


def test_pivot_r1_pct_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("PIVOT_R1_PCT", {}, create_pivot_r1_pct(df))


def test_pivot_s1_pct_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("PIVOT_S1_PCT", {}, create_pivot_s1_pct(df))


def test_live_indicator_factory_registers_pivot_pct():
    assert LIVE_INDICATOR_FACTORY["PIVOT_P_PCT"] is create_pivot_p_pct
    assert LIVE_INDICATOR_FACTORY["PIVOT_R1_PCT"] is create_pivot_r1_pct
    assert LIVE_INDICATOR_FACTORY["PIVOT_S1_PCT"] is create_pivot_s1_pct
```

Run: `PYTHONPATH=. python -m pytest tests/test_live_indicators_price_levels.py -k pivot_pct -v`
Expected: FAIL — `ImportError`

- [ ] **Step 7: `trading/live_indicators.py`에 3개 함수 구현**

`create_pivot_s1` 함수 다음에 추가:

```python
def create_pivot_p_pct(df: pd.DataFrame, **params) -> pd.Series:
    level = create_pivot_p(df, **params)
    return (df["close"] - level) / level * 100


def create_pivot_r1_pct(df: pd.DataFrame, **params) -> pd.Series:
    level = create_pivot_r1(df, **params)
    return (df["close"] - level) / level * 100


def create_pivot_s1_pct(df: pd.DataFrame, **params) -> pd.Series:
    level = create_pivot_s1(df, **params)
    return (df["close"] - level) / level * 100
```

`LIVE_INDICATOR_FACTORY`의 `"PIVOT_S1": create_pivot_s1,` 다음 줄에 추가:

```python
    "PIVOT_P_PCT": create_pivot_p_pct,
    "PIVOT_R1_PCT": create_pivot_r1_pct,
    "PIVOT_S1_PCT": create_pivot_s1_pct,
```

- [ ] **Step 8: 라이브 + 워밍업 테스트 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_live_indicators_price_levels.py tests/test_signal_engine_warmup.py tests/test_condition_tree.py -v`
Expected: PASS

- [ ] **Step 9: 백엔드 카탈로그 등록 (테스트 먼저 실패 확인 후 추가)**

Run: `PYTHONPATH=. python -m pytest tests/test_backend.py -k covers_all_registered -v` → FAIL 확인.

`backend/main.py`의 `INDICATOR_CATALOG`에서 `PIVOT_S1` 항목 다음에 추가:

```python
    {
        "value": "PIVOT_P_PCT", "label": "Pivot 기준선 (정규화, %)", "category": "가격대",
        "params": [],
        "description": "Pivot 기준선(P) 대비 종가가 몇 % 위/아래에 있는지 나타냅니다.",
        "example": "PIVOT_P_PCT > 2면 기준선보다 2% 이상 위에서 거래되는 구간입니다.",
    },
    {
        "value": "PIVOT_R1_PCT", "label": "Pivot 저항선(R1) (정규화, %)", "category": "가격대",
        "params": [],
        "description": "1차 저항선(R1) 대비 종가의 이격을 %로 나타냅니다.",
        "example": "PIVOT_R1_PCT > 0이면 저항선을 이미 돌파했다는 뜻입니다.",
    },
    {
        "value": "PIVOT_S1_PCT", "label": "Pivot 지지선(S1) (정규화, %)", "category": "가격대",
        "params": [],
        "description": "1차 지지선(S1) 대비 종가의 이격을 %로 나타냅니다.",
        "example": "PIVOT_S1_PCT < 0이면 지지선 아래로 이탈했다는 뜻입니다.",
    },
```

Run: `PYTHONPATH=. python -m pytest tests/test_backend.py -k covers_all_registered -v`
Expected: PASS

- [ ] **Step 10: 프론트엔드 — 조건식 빌더 threshold 기본값**

`frontend/components/StrategyConditionBuilder.tsx`의 `ZERO_CROSS_INDICATORS`에 이어서 추가(Task 1에서 만든 Set 리터럴을 계속 확장):

```typescript
const ZERO_CROSS_INDICATORS = new Set([
  'MACD_line', 'MACD_signal', 'MARKET_TREND', 'MOMENTUM_PCT', 'KOREA_PREMIUM', 'MACD_PPO', 'MACD_PPO_signal',
  'FIB_382_PCT', 'FIB_500_PCT', 'FIB_618_PCT',
  'PIVOT_P_PCT', 'PIVOT_R1_PCT', 'PIVOT_S1_PCT',
]);
```

- [ ] **Step 11: 프론트엔드 — 지표 가이드**

`frontend/lib/indicator-guide.ts`의 `PIVOT_P`/`PIVOT_R1`/`PIVOT_S1` 항목 `usage` 필드를 각각 수정:

```typescript
  // PIVOT_P
    usage: '종가가 Pivot 위/아래 어느 쪽에 있는지를 다른 오실레이터 조건과 AND로 묶어, 그날의 우세한 방향으로만 진입하는 필터로 씁니다. 코인 시세와 무관한 정규화 버전이 필요하면 PIVOT_P_PCT를 대신 쓰세요.',
  // PIVOT_R1
    usage: '종가가 R1을 상향 돌파하는 걸 돌파 매수 신호로, 혹은 R1 근처를 저항으로 보고 매도 신호로 반대로 쓰기도 합니다. 코인 시세와 무관한 정규화 버전이 필요하면 PIVOT_R1_PCT를 대신 쓰세요.',
  // PIVOT_S1
    usage: 'S1 근처에서 반등을 노리는 매수 조건, 혹은 S1 하향 이탈을 손절/추가 하락 신호로 씁니다. 코인 시세와 무관한 정규화 버전이 필요하면 PIVOT_S1_PCT를 대신 쓰세요.',
```

`PIVOT_S1` 항목 다음(`MARKET_TREND` 항목 앞)에 3개 신규 최상위 항목 추가:

```typescript
  PIVOT_P_PCT: {
    meaning: 'Pivot 기준선(P) 대비, 종가가 몇 % 위/아래에 있는지 나타낸 정규화 지표입니다 — 계산 배경은 PIVOT_P 가이드를 참고하세요.',
    params: [],
    formula: 'PIVOT_P_PCT = (종가 − Pivot P) ÷ Pivot P × 100',
    thresholdExample: 'PIVOT_P_PCT > 2면 기준선보다 2% 이상 위에서 거래되는 구간입니다. 코인 시세와 무관하게 여러 코인에 같은 threshold를 그대로 쓸 수 있습니다.',
    usage: 'PIVOT_P를 절대가격 필터로 쓰기 애매할 때, 대신 이 지표로 "기준선 대비 몇 % 떨어져 있는지"를 오실레이터처럼 씁니다.',
  },
  PIVOT_R1_PCT: {
    meaning: '1차 저항선(R1) 대비, 종가가 몇 % 위/아래에 있는지 나타낸 정규화 지표입니다 — 계산 배경은 PIVOT_R1 가이드를 참고하세요.',
    params: [],
    formula: 'PIVOT_R1_PCT = (종가 − R1) ÷ R1 × 100',
    thresholdExample: 'PIVOT_R1_PCT > 0이면 저항선을 이미 돌파했다는 뜻입니다.',
    usage: 'PIVOT_R1을 절대가격 필터로 쓰기 애매할 때, 대신 이 지표로 "저항선 대비 몇 % 떨어져 있는지"를 오실레이터처럼 씁니다.',
  },
  PIVOT_S1_PCT: {
    meaning: '1차 지지선(S1) 대비, 종가가 몇 % 위/아래에 있는지 나타낸 정규화 지표입니다 — 계산 배경은 PIVOT_S1 가이드를 참고하세요.',
    params: [],
    formula: 'PIVOT_S1_PCT = (종가 − S1) ÷ S1 × 100',
    thresholdExample: 'PIVOT_S1_PCT < 0이면 지지선 아래로 이탈했다는 뜻입니다.',
    usage: 'PIVOT_S1을 절대가격 필터로 쓰기 애매할 때, 대신 이 지표로 "지지선 대비 몇 % 떨어져 있는지"를 오실레이터처럼 씁니다.',
  },
```

- [ ] **Step 12: 전체 테스트 스위트 확인**

Run: `PYTHONPATH=. python -m pytest tests/ -v`
Expected: PASS

- [ ] **Step 13: 커밋**

```bash
git add engine/condition_tree.py engine/indicators/__init__.py trading/live_indicators.py backend/main.py frontend/components/StrategyConditionBuilder.tsx frontend/lib/indicator-guide.ts tests/test_indicators.py tests/test_live_indicators_price_levels.py tests/test_signal_engine_warmup.py
git commit -m "feat: PIVOT_P/R1/S1_PCT 정규화 지표 추가"
```

---

## Task 3: VPVR_POC_PCT / VPVR_VAH_PCT / VPVR_VAL_PCT

**Files:**
- Modify: `engine/condition_tree.py`
- Modify: `engine/indicators/__init__.py`
- Test: `tests/test_indicators.py`
- Modify: `trading/live_indicators.py`
- Test: `tests/test_live_indicators_price_levels.py`
- Modify: `tests/test_signal_engine_warmup.py`
- Modify: `backend/main.py`
- Modify: `frontend/components/StrategyConditionBuilder.tsx`
- Modify: `frontend/lib/indicator-guide.ts`

**Interfaces:**
- Consumes: `engine.indicators.price_levels.VolumeProfile`(기존, lines=("poc","vah","val")), `create_vpvr_poc/vah/val`(기존).
- Produces: `INDICATOR_FACTORY["VPVR_POC_PCT"/"VPVR_VAH_PCT"/"VPVR_VAL_PCT"]` 별칭 등록. `trading.live_indicators.create_vpvr_poc_pct/vah_pct/val_pct(df, **params) -> pd.Series`.

- [ ] **Step 1: 백테스트 엔진 실패하는 테스트 작성**

`tests/test_indicators.py`에서 VPVR 관련 테스트(`test_vpvr_default_settings_keep_value_area_ordering_and_stays_within_window_range` 근처) 다음, 또는 `test_pivot_s1_matches_manual_formula` 이후 아무 곳에나 추가(파일 내 다른 VPVR 테스트들이 `period=50`을 롤링 윈도우로 쓰기엔 `make_oscillating_df()` 기본 300행이 필요 — 기존 관례 그대로 사용):

```python
def test_vpvr_poc_pct_matches_manual_pct_from_level():
    poc_values = _run_probe("VPVR_POC", {"period": 50})
    values = _run_probe("VPVR_POC_PCT", {"period": 50})
    df = make_oscillating_df()
    close = df["close"].iloc[-1]
    level = poc_values[-1]
    manual = (close - level) / level * 100
    assert abs(values[-1] - manual) < 1e-6


def test_vpvr_vah_pct_matches_manual_pct_from_level():
    vah_values = _run_probe("VPVR_VAH", {"period": 50})
    values = _run_probe("VPVR_VAH_PCT", {"period": 50})
    df = make_oscillating_df()
    close = df["close"].iloc[-1]
    level = vah_values[-1]
    manual = (close - level) / level * 100
    assert abs(values[-1] - manual) < 1e-6


def test_vpvr_val_pct_matches_manual_pct_from_level():
    val_values = _run_probe("VPVR_VAL", {"period": 50})
    values = _run_probe("VPVR_VAL_PCT", {"period": 50})
    df = make_oscillating_df()
    close = df["close"].iloc[-1]
    level = val_values[-1]
    manual = (close - level) / level * 100
    assert abs(values[-1] - manual) < 1e-6
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_indicators.py -k vpvr_pct -v`
Expected: FAIL — `KeyError: 'VPVR_POC_PCT'`

- [ ] **Step 3: `get_indicator_value` 분기 + factory 별칭 등록**

`engine/condition_tree.py`의 `get_indicator_value()`에서 Task 2에서 추가한 `PIVOT_S1_PCT` 분기 다음에 추가:

```python
    elif indicator_name == "VPVR_POC_PCT":
        close, level = float(obj.data.close[0]), float(obj.lines.poc[0])
        return (close - level) / level * 100 if level else 0.0
    elif indicator_name == "VPVR_VAH_PCT":
        close, level = float(obj.data.close[0]), float(obj.lines.vah[0])
        return (close - level) / level * 100 if level else 0.0
    elif indicator_name == "VPVR_VAL_PCT":
        close, level = float(obj.data.close[0]), float(obj.lines.val[0])
        return (close - level) / level * 100 if level else 0.0
```

`engine/indicators/__init__.py`의 `INDICATOR_FACTORY`에서 `"VPVR_VAL": create_vpvr_val,` 다음 줄에 추가:

```python
    "VPVR_POC_PCT": create_vpvr_poc,
    "VPVR_VAH_PCT": create_vpvr_vah,
    "VPVR_VAL_PCT": create_vpvr_val,
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_indicators.py -k vpvr_pct -v`
Expected: PASS (3 tests)

- [ ] **Step 5: 워밍업 표 갱신**

`tests/test_signal_engine_warmup.py`의 `_A_GROUP_PARAMS`에서 `"VPVR_VAL": {"period": 50},` 다음 줄에 추가:

```python
    "VPVR_POC_PCT": {"period": 50},
    "VPVR_VAH_PCT": {"period": 50},
    "VPVR_VAL_PCT": {"period": 50},
```

- [ ] **Step 6: 라이브 pandas 구현 (테스트 먼저)**

`tests/test_live_indicators_price_levels.py`에서 기존 `from trading.live_indicators import create_vpvr_poc, create_vpvr_vah, create_vpvr_val` 줄을 수정:

```python
from trading.live_indicators import (
    create_vpvr_poc,
    create_vpvr_poc_pct,
    create_vpvr_vah,
    create_vpvr_vah_pct,
    create_vpvr_val,
    create_vpvr_val_pct,
)
```

`test_vpvr_val_matches_backtrader` 함수 다음에 추가:

```python
def test_vpvr_poc_pct_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("VPVR_POC_PCT", {"period": 50}, create_vpvr_poc_pct(df, period=50))


def test_vpvr_vah_pct_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("VPVR_VAH_PCT", {"period": 50}, create_vpvr_vah_pct(df, period=50))


def test_vpvr_val_pct_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("VPVR_VAL_PCT", {"period": 50}, create_vpvr_val_pct(df, period=50))


def test_live_indicator_factory_registers_vpvr_pct():
    assert LIVE_INDICATOR_FACTORY["VPVR_POC_PCT"] is create_vpvr_poc_pct
    assert LIVE_INDICATOR_FACTORY["VPVR_VAH_PCT"] is create_vpvr_vah_pct
    assert LIVE_INDICATOR_FACTORY["VPVR_VAL_PCT"] is create_vpvr_val_pct
```

Run: `PYTHONPATH=. python -m pytest tests/test_live_indicators_price_levels.py -k vpvr_pct -v`
Expected: FAIL — `ImportError`

- [ ] **Step 7: `trading/live_indicators.py`에 3개 함수 구현**

`create_vpvr_val` 함수 다음에 추가:

```python
def create_vpvr_poc_pct(df: pd.DataFrame, **params) -> pd.Series:
    level = create_vpvr_poc(df, **params)
    return (df["close"] - level) / level * 100


def create_vpvr_vah_pct(df: pd.DataFrame, **params) -> pd.Series:
    level = create_vpvr_vah(df, **params)
    return (df["close"] - level) / level * 100


def create_vpvr_val_pct(df: pd.DataFrame, **params) -> pd.Series:
    level = create_vpvr_val(df, **params)
    return (df["close"] - level) / level * 100
```

`LIVE_INDICATOR_FACTORY`에서 `"VPVR_VAL": create_vpvr_val,` 다음 줄에 추가:

```python
    "VPVR_POC_PCT": create_vpvr_poc_pct,
    "VPVR_VAH_PCT": create_vpvr_vah_pct,
    "VPVR_VAL_PCT": create_vpvr_val_pct,
```

- [ ] **Step 8: 라이브 + 워밍업 테스트 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_live_indicators_price_levels.py tests/test_signal_engine_warmup.py -v`
Expected: PASS

- [ ] **Step 9: 백엔드 카탈로그 등록**

Run: `PYTHONPATH=. python -m pytest tests/test_backend.py -k covers_all_registered -v` → FAIL 확인.

`backend/main.py`의 `INDICATOR_CATALOG`에서 `VPVR_VAL` 항목 다음에 추가:

```python
    {
        "value": "VPVR_POC_PCT", "label": "VPVR POC (정규화, %)", "category": "가격대",
        "params": [{"key": "period", "label": "기간", "default": 50}],
        "description": "거래량이 가장 많이 몰린 가격대(POC) 대비 종가의 이격을 %로 나타냅니다.",
        "example": "VPVR_POC_PCT < -5면 POC보다 5% 이상 아래에서 거래되는 구간입니다.",
    },
    {
        "value": "VPVR_VAH_PCT", "label": "VPVR Value Area 상단 (정규화, %)", "category": "가격대",
        "params": [{"key": "period", "label": "기간", "default": 50}],
        "description": "Value Area 상단(VAH) 대비 종가의 이격을 %로 나타냅니다.",
        "example": "VPVR_VAH_PCT > 0이면 Value Area 위로 벗어났다는 뜻입니다.",
    },
    {
        "value": "VPVR_VAL_PCT", "label": "VPVR Value Area 하단 (정규화, %)", "category": "가격대",
        "params": [{"key": "period", "label": "기간", "default": 50}],
        "description": "Value Area 하단(VAL) 대비 종가의 이격을 %로 나타냅니다.",
        "example": "VPVR_VAL_PCT < 0이면 Value Area 아래로 벗어났다는 뜻입니다.",
    },
```

Run: `PYTHONPATH=. python -m pytest tests/test_backend.py -k covers_all_registered -v`
Expected: PASS

- [ ] **Step 10: 프론트엔드 — 조건식 빌더 threshold 기본값**

`frontend/components/StrategyConditionBuilder.tsx`의 `ZERO_CROSS_INDICATORS`를 계속 확장:

```typescript
const ZERO_CROSS_INDICATORS = new Set([
  'MACD_line', 'MACD_signal', 'MARKET_TREND', 'MOMENTUM_PCT', 'KOREA_PREMIUM', 'MACD_PPO', 'MACD_PPO_signal',
  'FIB_382_PCT', 'FIB_500_PCT', 'FIB_618_PCT',
  'PIVOT_P_PCT', 'PIVOT_R1_PCT', 'PIVOT_S1_PCT',
  'VPVR_POC_PCT', 'VPVR_VAH_PCT', 'VPVR_VAL_PCT',
]);
```

- [ ] **Step 11: 프론트엔드 — 지표 가이드**

`frontend/lib/indicator-guide.ts`의 `VPVR_POC`/`VPVR_VAH`/`VPVR_VAL` 항목 `usage` 필드를 각각 수정:

```typescript
  // VPVR_POC
    usage: '종가가 POC 근처로 되돌아올 때 반등을 노리는 매수 조건, 혹은 POC를 강하게 이탈할 때 추세 전환 신호로 씁니다. 코인 시세와 무관한 정규화 버전이 필요하면 VPVR_POC_PCT를 대신 쓰세요.',
  // VPVR_VAH
    usage: '종가가 VAH를 상향 돌파하면 거래가 적었던 구간으로 빠르게 움직일 수 있다고 보고 돌파 매수 신호로, 혹은 VAH를 저항으로 보고 매도 신호로 반대로 쓰기도 합니다. 코인 시세와 무관한 정규화 버전이 필요하면 VPVR_VAH_PCT를 대신 쓰세요.',
  // VPVR_VAL
    usage: 'VAL 근처에서 반등을 노리는 매수 조건, 혹은 VAL 하향 이탈을 손절/추가 하락 신호로 씁니다. 코인 시세와 무관한 정규화 버전이 필요하면 VPVR_VAL_PCT를 대신 쓰세요.',
```

`VPVR_VAL` 항목 다음(파일 맨 끝, 닫는 `};` 앞)에 3개 신규 최상위 항목 추가:

```typescript
  VPVR_POC_PCT: {
    meaning: '거래량이 가장 많이 몰린 가격대(POC) 대비, 종가가 몇 % 위/아래에 있는지 나타낸 정규화 지표입니다 — 계산 배경은 VPVR_POC 가이드를 참고하세요.',
    params: [{ key: 'period', role: 'VPVR_POC와 같은 롤링 윈도우 크기(기본값 50).' }],
    formula: 'VPVR_POC_PCT = (종가 − POC) ÷ POC × 100',
    thresholdExample: 'VPVR_POC_PCT < -5면 POC보다 5% 이상 아래에서 거래되는 구간입니다. 코인 시세와 무관하게 여러 코인에 같은 threshold를 그대로 쓸 수 있습니다.',
    usage: 'VPVR_POC를 절대가격 필터로 쓰기 애매할 때, 대신 이 지표로 "POC 대비 몇 % 떨어져 있는지"를 오실레이터처럼 씁니다.',
  },
  VPVR_VAH_PCT: {
    meaning: 'Value Area 상단(VAH) 대비, 종가가 몇 % 위/아래에 있는지 나타낸 정규화 지표입니다 — 계산 배경은 VPVR_VAH 가이드를 참고하세요.',
    params: [{ key: 'period', role: 'VPVR_POC와 같은 롤링 윈도우 크기(기본값 50).' }],
    formula: 'VPVR_VAH_PCT = (종가 − VAH) ÷ VAH × 100',
    thresholdExample: 'VPVR_VAH_PCT > 0이면 Value Area 위로 벗어났다는 뜻입니다.',
    usage: 'VPVR_VAH를 절대가격 필터로 쓰기 애매할 때, 대신 이 지표로 "VAH 대비 몇 % 떨어져 있는지"를 오실레이터처럼 씁니다.',
  },
  VPVR_VAL_PCT: {
    meaning: 'Value Area 하단(VAL) 대비, 종가가 몇 % 위/아래에 있는지 나타낸 정규화 지표입니다 — 계산 배경은 VPVR_VAL 가이드를 참고하세요.',
    params: [{ key: 'period', role: 'VPVR_POC와 같은 롤링 윈도우 크기(기본값 50).' }],
    formula: 'VPVR_VAL_PCT = (종가 − VAL) ÷ VAL × 100',
    thresholdExample: 'VPVR_VAL_PCT < 0이면 Value Area 아래로 벗어났다는 뜻입니다.',
    usage: 'VPVR_VAL를 절대가격 필터로 쓰기 애매할 때, 대신 이 지표로 "VAL 대비 몇 % 떨어져 있는지"를 오실레이터처럼 씁니다.',
  },
```

- [ ] **Step 12: 전체 테스트 스위트 확인**

Run: `PYTHONPATH=. python -m pytest tests/ -v`
Expected: PASS

- [ ] **Step 13: 커밋**

```bash
git add engine/condition_tree.py engine/indicators/__init__.py trading/live_indicators.py backend/main.py frontend/components/StrategyConditionBuilder.tsx frontend/lib/indicator-guide.ts tests/test_indicators.py tests/test_live_indicators_price_levels.py tests/test_signal_engine_warmup.py
git commit -m "feat: VPVR_POC/VAH/VAL_PCT 정규화 지표 추가"
```

---

## Task 4: SMA_PCT / EMA_PCT / WMA_PCT

**Files:**
- Modify: `engine/condition_tree.py`
- Modify: `engine/indicators/__init__.py`
- Test: `tests/test_indicators.py`
- Modify: `trading/live_indicators.py`
- Test: `tests/test_live_indicators_trend.py`
- Modify: `tests/test_signal_engine_warmup.py`
- Modify: `backend/main.py`
- Modify: `frontend/components/StrategyConditionBuilder.tsx`
- Modify: `frontend/lib/indicator-guide.ts`

**Interfaces:**
- Consumes: `engine.indicators.trend.create_sma/create_ema/create_wma`(기존, `bt.indicators.SMA/EMA/WeightedMovingAverage`를 반환하는 진짜 Indicator).
- Produces: `INDICATOR_FACTORY["SMA_PCT"/"EMA_PCT"/"WMA_PCT"]` 별칭 등록. `trading.live_indicators.create_sma_pct/ema_pct/wma_pct(df, **params) -> pd.Series`.

- [ ] **Step 1: 백테스트 엔진 실패하는 테스트 작성**

`tests/test_indicators.py`의 `test_sma_matches_manual_average` 함수 다음에 추가:

```python
def test_sma_pct_matches_manual_disparity():
    values = _run_probe("SMA_PCT", {"period": 5})
    df = make_oscillating_df()
    ma = df["close"].rolling(5).mean().iloc[-1]
    close = df["close"].iloc[-1]
    manual = (close - ma) / ma * 100
    assert abs(values[-1] - manual) < 1e-6


def test_ema_pct_registered_and_differs_from_sma_pct():
    # EMA/SMA는 계산식이 달라 이격도도 다르다 — 두 값이 항상 같으면 EMA_PCT가 실제로
    # EMA가 아니라 SMA를 잘못 참조하고 있다는 신호.
    sma_pct = _run_probe("SMA_PCT", {"period": 5})
    ema_pct = _run_probe("EMA_PCT", {"period": 5})
    assert sma_pct[-1] != ema_pct[-1]


def test_wma_pct_matches_manual_disparity():
    values = _run_probe("WMA_PCT", {"period": 3})
    df = make_oscillating_df()
    weights = [1, 2, 3]
    window = df["close"].iloc[-3:].to_numpy()
    ma = (window * weights).sum() / sum(weights)
    close = df["close"].iloc[-1]
    manual = (close - ma) / ma * 100
    assert abs(values[-1] - manual) < 1e-6
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_indicators.py -k "sma_pct or ema_pct or wma_pct" -v`
Expected: FAIL — `KeyError: 'SMA_PCT'`

- [ ] **Step 3: `get_indicator_value` 분기 + factory 별칭 등록**

`engine/condition_tree.py`의 `get_indicator_value()`에서 Task 3에서 추가한 `VPVR_VAL_PCT` 분기 다음에 추가:

```python
    elif indicator_name in ("SMA_PCT", "EMA_PCT", "WMA_PCT"):
        close, ma = float(obj.data.close[0]), float(obj[0])
        return (close - ma) / ma * 100 if ma else 0.0
```

`engine/indicators/__init__.py`의 `INDICATOR_FACTORY`에서 `"WMA": create_wma,` 다음 줄에 추가:

```python
    "SMA_PCT": create_sma,
    "EMA_PCT": create_ema,
    "WMA_PCT": create_wma,
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_indicators.py -k "sma_pct or ema_pct or wma_pct" -v`
Expected: PASS (3 tests)

- [ ] **Step 5: 워밍업 표 갱신**

`tests/test_signal_engine_warmup.py`의 `_A_GROUP_PARAMS`에서 `"WMA": {"period": 14},` 다음 줄에 추가:

```python
    "SMA_PCT": {"period": 14},
    "EMA_PCT": {"period": 14},
    "WMA_PCT": {"period": 14},
```

- [ ] **Step 6: 라이브 pandas 구현 (테스트 먼저)**

`tests/test_live_indicators_trend.py` 상단 import 줄을 수정:

```python
from trading.live_indicators import (
    LIVE_INDICATOR_FACTORY,
    create_ema,
    create_ema_pct,
    create_sma,
    create_sma_pct,
    create_wma,
    create_wma_pct,
)
```

`test_wma_matches_backtrader` 함수 다음에 추가:

```python
def test_sma_pct_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("SMA_PCT", {"period": 14}, create_sma_pct(df, period=14))


def test_ema_pct_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("EMA_PCT", {"period": 14}, create_ema_pct(df, period=14))


def test_wma_pct_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("WMA_PCT", {"period": 14}, create_wma_pct(df, period=14))


def test_live_indicator_factory_registers_pct_variants():
    assert LIVE_INDICATOR_FACTORY["SMA_PCT"] is create_sma_pct
    assert LIVE_INDICATOR_FACTORY["EMA_PCT"] is create_ema_pct
    assert LIVE_INDICATOR_FACTORY["WMA_PCT"] is create_wma_pct
```

(`assert_matches_backtrader`는 이미 파일 상단에서 `from tests.live_indicator_fixtures import assert_matches_backtrader`로 임포트돼 있음 — 그대로 재사용.)

Run: `PYTHONPATH=. python -m pytest tests/test_live_indicators_trend.py -k pct -v`
Expected: FAIL — `ImportError`

- [ ] **Step 7: `trading/live_indicators.py`에 3개 함수 구현**

`create_wma` 함수 다음에 추가:

```python
def create_sma_pct(df: pd.DataFrame, **params) -> pd.Series:
    ma = create_sma(df, **params)
    return (df["close"] - ma) / ma * 100


def create_ema_pct(df: pd.DataFrame, **params) -> pd.Series:
    ma = create_ema(df, **params)
    return (df["close"] - ma) / ma * 100


def create_wma_pct(df: pd.DataFrame, **params) -> pd.Series:
    ma = create_wma(df, **params)
    return (df["close"] - ma) / ma * 100
```

`LIVE_INDICATOR_FACTORY`에서 `"WMA": create_wma,` 다음 줄에 추가:

```python
    "SMA_PCT": create_sma_pct,
    "EMA_PCT": create_ema_pct,
    "WMA_PCT": create_wma_pct,
```

- [ ] **Step 8: 라이브 + 워밍업 테스트 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_live_indicators_trend.py tests/test_signal_engine_warmup.py -v`
Expected: PASS

- [ ] **Step 9: 백엔드 카탈로그 등록**

Run: `PYTHONPATH=. python -m pytest tests/test_backend.py -k covers_all_registered -v` → FAIL 확인.

`backend/main.py`의 `INDICATOR_CATALOG`에서 `WMA` 항목(가장 앞쪽, `SMA`/`EMA` 다음) 바로 다음에 추가:

```python
    {
        "value": "SMA_PCT", "label": "SMA 이격도 (%)", "category": "추세",
        "params": [{"key": "period", "label": "기간", "default": 14}],
        "description": "종가가 SMA(period) 대비 몇 % 떨어져 있는지 나타냅니다(이격도).",
        "example": "SMA_PCT > 5면 이동평균보다 5% 이상 위로 벌어진 구간을 포착합니다.",
    },
    {
        "value": "EMA_PCT", "label": "EMA 이격도 (%)", "category": "추세",
        "params": [{"key": "period", "label": "기간", "default": 14}],
        "description": "종가가 EMA(period) 대비 몇 % 떨어져 있는지 나타냅니다.",
        "example": "EMA_PCT < -3이면 EMA보다 3% 이상 아래로 눌린 구간입니다.",
    },
    {
        "value": "WMA_PCT", "label": "WMA 이격도 (%)", "category": "추세",
        "params": [{"key": "period", "label": "기간", "default": 14}],
        "description": "종가가 WMA(period) 대비 몇 % 떨어져 있는지 나타냅니다.",
        "example": "WMA_PCT > 2면 WMA보다 2% 이상 위에 있는 구간입니다.",
    },
```

Run: `PYTHONPATH=. python -m pytest tests/test_backend.py -k covers_all_registered -v`
Expected: PASS

- [ ] **Step 10: 프론트엔드 — 조건식 빌더 threshold 기본값**

```typescript
const ZERO_CROSS_INDICATORS = new Set([
  'MACD_line', 'MACD_signal', 'MARKET_TREND', 'MOMENTUM_PCT', 'KOREA_PREMIUM', 'MACD_PPO', 'MACD_PPO_signal',
  'FIB_382_PCT', 'FIB_500_PCT', 'FIB_618_PCT',
  'PIVOT_P_PCT', 'PIVOT_R1_PCT', 'PIVOT_S1_PCT',
  'VPVR_POC_PCT', 'VPVR_VAH_PCT', 'VPVR_VAL_PCT',
  'SMA_PCT', 'EMA_PCT', 'WMA_PCT',
]);
```

- [ ] **Step 11: 프론트엔드 — 지표 가이드**

`SMA`/`EMA`/`WMA` 항목 `usage` 필드를 각각 수정:

```typescript
  // SMA
    usage: '단독보다는 "SMA(50) 위/아래에서만 매수" 같은 큰 흐름 필터로, 다른 오실레이터 조건과 AND로 묶어 자주 씁니다. 코인 시세와 무관한 정규화 버전이 필요하면 SMA_PCT를 대신 쓰세요.',
  // EMA
    usage: 'SMA보다 최근 변화에 민감하게 반응해, 추세 전환을 좀 더 빨리 잡고 싶을 때 SMA 대신 씁니다. 코인 시세와 무관한 정규화 버전이 필요하면 EMA_PCT를 대신 쓰세요.',
  // WMA
    usage: 'SMA·EMA와 마찬가지로 절대 가격 레벨 필터로 쓰되, 최근 봉에 가장 민감하게 반응하고 싶을 때 선택합니다. 코인 시세와 무관한 정규화 버전이 필요하면 WMA_PCT를 대신 쓰세요.',
```

`WMA` 항목 다음(`RSI` 항목 앞)에 3개 신규 최상위 항목 추가:

```typescript
  SMA_PCT: {
    meaning: '종가가 SMA(period) 대비 몇 % 위/아래에 있는지 나타낸 이격도 지표입니다 — 계산 배경은 SMA 가이드를 참고하세요.',
    params: [{ key: 'period', role: '평균을 낼 봉 개수. SMA와 동일한 의미.' }],
    formula: 'SMA_PCT = (종가 − SMA) ÷ SMA × 100',
    thresholdExample: 'SMA_PCT > 5면 이동평균보다 5% 이상 위로 벌어진(과열 가능성) 구간을, < -5면 5% 이상 아래로 눌린 구간을 포착합니다. 코인 시세와 무관하게 여러 코인에 같은 threshold를 그대로 쓸 수 있습니다.',
    usage: 'SMA를 절대가격 필터로 쓰기 애매할 때, 대신 이 지표로 "이동평균 대비 몇 % 떨어져 있는지"를 오실레이터처럼 씁니다.',
  },
  EMA_PCT: {
    meaning: '종가가 EMA(period) 대비 몇 % 위/아래에 있는지 나타낸 이격도 지표입니다 — 계산 배경은 EMA 가이드를 참고하세요.',
    params: [{ key: 'period', role: '가중치 감쇠 속도를 정하는 기간. EMA와 동일한 의미.' }],
    formula: 'EMA_PCT = (종가 − EMA) ÷ EMA × 100',
    thresholdExample: 'EMA_PCT < -3이면 EMA보다 3% 이상 아래로 눌린 구간입니다.',
    usage: 'EMA를 절대가격 필터로 쓰기 애매할 때, 대신 이 지표로 "이동평균 대비 몇 % 떨어져 있는지"를 오실레이터처럼 씁니다.',
  },
  WMA_PCT: {
    meaning: '종가가 WMA(period) 대비 몇 % 위/아래에 있는지 나타낸 이격도 지표입니다 — 계산 배경은 WMA 가이드를 참고하세요.',
    params: [{ key: 'period', role: '평균에 포함할 봉 개수. WMA와 동일한 의미.' }],
    formula: 'WMA_PCT = (종가 − WMA) ÷ WMA × 100',
    thresholdExample: 'WMA_PCT > 2면 WMA보다 2% 이상 위에 있는 구간입니다.',
    usage: 'WMA를 절대가격 필터로 쓰기 애매할 때, 대신 이 지표로 "이동평균 대비 몇 % 떨어져 있는지"를 오실레이터처럼 씁니다.',
  },
```

- [ ] **Step 12: 전체 테스트 스위트 확인**

Run: `PYTHONPATH=. python -m pytest tests/ -v`
Expected: PASS

- [ ] **Step 13: 커밋**

```bash
git add engine/condition_tree.py engine/indicators/__init__.py trading/live_indicators.py backend/main.py frontend/components/StrategyConditionBuilder.tsx frontend/lib/indicator-guide.ts tests/test_indicators.py tests/test_live_indicators_trend.py tests/test_signal_engine_warmup.py
git commit -m "feat: SMA/EMA/WMA_PCT 이격도 정규화 지표 추가"
```

---

## Task 5: TRADE_VALUE_PCT

**Files:**
- Modify: `engine/indicators/volume.py` (`TradeValueRatio` 클래스 + factory)
- Modify: `engine/indicators/__init__.py`
- Test: `tests/test_indicators.py`
- Modify: `trading/live_indicators.py`
- Test: `tests/test_live_indicators_volume.py`
- Modify: `tests/test_signal_engine_warmup.py` (`_A_GROUP_PARAMS` + trade_value 특수 케이스 집합)
- Modify: `backend/main.py`
- Modify: `frontend/components/StrategyConditionBuilder.tsx`
- Modify: `frontend/lib/indicator-guide.ts`

**Interfaces:**
- Produces: `engine.indicators.volume.TradeValueRatio`(bt.Indicator, lines=("pct",), params=(period,)); `engine.indicators.create_trade_value_pct(data, **params) -> bt.Indicator`; `trading.live_indicators.create_trade_value_pct(df, **params) -> pd.Series`.

- [ ] **Step 1: 백테스트 엔진 실패하는 테스트 작성**

`tests/test_indicators.py`의 `test_trade_value_sma_matches_manual_average_of_trade_value` 함수 다음에 추가:

```python
def test_trade_value_pct_matches_manual_ratio_to_own_sma():
    df = make_oscillating_df()
    trade_value = df["close"] * df["volume"]
    values = _run_probe_with_aux("TRADE_VALUE_PCT", {"period": 5}, "trade_value", trade_value)
    sma = trade_value.rolling(5).mean().iloc[-1]
    manual = (trade_value.iloc[-1] - sma) / sma * 100
    assert abs(values[-1] - manual) < 1e-6
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_indicators.py -k trade_value_pct -v`
Expected: FAIL — `KeyError: 'TRADE_VALUE_PCT'`

- [ ] **Step 3: `TradeValueRatio` 클래스와 factory 구현**

`engine/indicators/volume.py`의 `create_trade_value_sma` 함수 다음에 추가:

```python
class TradeValueRatio(bt.Indicator):
    """이번 봉 거래대금이 자체 이동평균(period봉) 대비 몇 % 높거나 낮은지를 나타낸
    정규화 버전. 코인마다 다른 거래대금 스케일을 제거한다."""

    lines = ("pct",)
    params = (("period", 20),)

    def __init__(self) -> None:
        self.sma = bt.indicators.SMA(self.data.trade_value, period=self.p.period)

    def next(self) -> None:
        sma_val = self.sma[0]
        self.lines.pct[0] = (self.data.trade_value[0] - sma_val) / sma_val * 100 if sma_val else 0.0


def create_trade_value_pct(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 20))
    return TradeValueRatio(data, period=period)
```

`engine/indicators/__init__.py`의 `from .volume import (...)` 줄을 수정:

```python
from .volume import create_obv, create_trade_value, create_trade_value_pct, create_trade_value_sma, create_volume_sma, create_vpin
```

`INDICATOR_FACTORY`에서 `"TRADE_VALUE_SMA": create_trade_value_sma,` 다음 줄에 추가:

```python
    "TRADE_VALUE_PCT": create_trade_value_pct,
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_indicators.py -k trade_value_pct -v`
Expected: PASS

- [ ] **Step 5: `test_all_registered_indicators_produce_values` skip 목록 갱신 + 워밍업 표 갱신**

`tests/test_indicators.py`의 `_NEEDS_TRADE_VALUE_LINE` 집합을 수정(TRADE_VALUE_PCT도 trade_value 라인이 필요하므로 블랭킷 스모크 테스트에서 제외):

```python
_NEEDS_TRADE_VALUE_LINE = {"TRADE_VALUE", "TRADE_VALUE_SMA", "TRADE_VALUE_PCT"}  # trade_value 라인이 필요 — test_trade_value_* 참고
```

`tests/test_signal_engine_warmup.py`의 `_A_GROUP_PARAMS`에서 `"TRADE_VALUE_SMA": {"period": 20},` 다음 줄에 추가:

```python
    "TRADE_VALUE_PCT": {"period": 20},
```

같은 파일의 `test_warmup_formula_produces_non_nan_last_value` 함수에서 trade_value 컬럼을 채워주는 조건문을 수정:

```python
    if name in {"TRADE_VALUE", "TRADE_VALUE_SMA", "TRADE_VALUE_PCT"}:
        df["trade_value"] = df["close"] * df["volume"]
```

- [ ] **Step 6: 라이브 pandas 구현 (테스트 먼저)**

`tests/test_live_indicators_volume.py` 상단 import 줄을 수정:

```python
from trading.live_indicators import (
    LIVE_INDICATOR_FACTORY,
    create_obv,
    create_trade_value,
    create_trade_value_pct,
    create_trade_value_sma,
    create_volume_sma,
    create_vpin,
)
```

`test_trade_value_sma_warmup_is_nan_before_period_bars` 함수 다음에 추가:

```python
def test_trade_value_pct_matches_manual_ratio_to_own_sma():
    df = make_oscillating_df()
    df["trade_value"] = df["close"] * df["volume"]
    result = create_trade_value_pct(df, period=5)
    sma = df["trade_value"].rolling(5).mean()
    manual = (df["trade_value"] - sma) / sma * 100
    assert abs(result.iloc[-1] - manual.iloc[-1]) < 1e-6


def test_live_indicator_factory_registers_trade_value_pct():
    assert LIVE_INDICATOR_FACTORY["TRADE_VALUE_PCT"] is create_trade_value_pct
```

Run: `PYTHONPATH=. python -m pytest tests/test_live_indicators_volume.py -k trade_value_pct -v`
Expected: FAIL — `ImportError`

- [ ] **Step 7: `trading/live_indicators.py`에 함수 구현**

`create_trade_value_sma` 함수 다음에 추가:

```python
def create_trade_value_pct(df: pd.DataFrame, **params) -> pd.Series:
    sma = create_trade_value_sma(df, **params)
    return (df["trade_value"] - sma) / sma * 100
```

`LIVE_INDICATOR_FACTORY`에서 `"TRADE_VALUE_SMA": create_trade_value_sma,` 다음 줄에 추가:

```python
    "TRADE_VALUE_PCT": create_trade_value_pct,
```

- [ ] **Step 8: 라이브 + 워밍업 테스트 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_live_indicators_volume.py tests/test_signal_engine_warmup.py -v`
Expected: PASS

- [ ] **Step 9: 백엔드 카탈로그 등록**

Run: `PYTHONPATH=. python -m pytest tests/test_backend.py -k covers_all_registered -v` → FAIL 확인.

`backend/main.py`의 `INDICATOR_CATALOG`에서 `TRADE_VALUE_SMA` 항목 다음에 추가:

```python
    {
        "value": "TRADE_VALUE_PCT", "label": "거래대금 비율 (%)", "category": "거래대금",
        "params": [{"key": "period", "label": "기간", "default": 20}],
        "description": "이번 봉 거래대금이 자체 이동평균(TRADE_VALUE_SMA) 대비 몇 % 높거나 낮은지 나타냅니다. 코인마다 다른 거래대금 스케일을 제거합니다.",
        "example": "TRADE_VALUE_PCT > 100이면 평소 대비 거래대금이 2배 이상으로 튄 구간입니다.",
    },
```

Run: `PYTHONPATH=. python -m pytest tests/test_backend.py -k covers_all_registered -v`
Expected: PASS

- [ ] **Step 10: 프론트엔드 — 조건식 빌더 threshold 기본값**

```typescript
const ZERO_CROSS_INDICATORS = new Set([
  'MACD_line', 'MACD_signal', 'MARKET_TREND', 'MOMENTUM_PCT', 'KOREA_PREMIUM', 'MACD_PPO', 'MACD_PPO_signal',
  'FIB_382_PCT', 'FIB_500_PCT', 'FIB_618_PCT',
  'PIVOT_P_PCT', 'PIVOT_R1_PCT', 'PIVOT_S1_PCT',
  'VPVR_POC_PCT', 'VPVR_VAH_PCT', 'VPVR_VAL_PCT',
  'SMA_PCT', 'EMA_PCT', 'WMA_PCT',
  'TRADE_VALUE_PCT',
]);
```

- [ ] **Step 11: 프론트엔드 — 지표 가이드**

`TRADE_VALUE_SMA` 항목 `usage` 필드를 수정:

```typescript
    usage: 'TRADE_VALUE(원시값)로 절대 규모 필터를 걸고, TRADE_VALUE_SMA로는 "그 코인 기준 평소 대비 얼마나 튀었는지"를 함께 보는 조합이 유용합니다. 코인 시세와 무관한 정규화 버전이 필요하면 TRADE_VALUE_PCT를 대신 쓰세요.',
```

`TRADE_VALUE_SMA` 항목 다음(`STOP_LOSS_PCT` 항목 앞)에 신규 최상위 항목 추가:

```typescript
  TRADE_VALUE_PCT: {
    meaning:
      '이번 봉 거래대금이 자체 이동평균(TRADE_VALUE_SMA) 대비 몇 % 높거나 낮은지 나타낸 정규화 지표입니다. 코인마다 다른 거래대금 스케일을 제거합니다 — 계산 배경은 TRADE_VALUE_SMA 가이드를 참고하세요.',
    params: [{ key: 'period', role: '내부적으로 계산하는 거래대금 이동평균의 봉 개수. TRADE_VALUE_SMA와 동일한 의미.' }],
    formula: 'TRADE_VALUE_PCT = (거래대금 − TRADE_VALUE_SMA) ÷ TRADE_VALUE_SMA × 100',
    thresholdExample: 'TRADE_VALUE_PCT > 100이면 평소 대비 거래대금이 2배 이상으로 튄 구간입니다. TRADE_VALUE_SMA와 달리 코인마다 스케일이 달라도 같은 threshold를 여러 코인에 그대로 쓸 수 있습니다.',
    usage: 'TRADE_VALUE_SMA를 코인마다 다른 절대 규모로 비교하기 애매할 때, 대신 이 지표로 "평소 대비 몇 % 튀었는지"를 오실레이터처럼 씁니다.',
  },
```

- [ ] **Step 12: 전체 테스트 스위트 확인**

Run: `PYTHONPATH=. python -m pytest tests/ -v`
Expected: PASS

- [ ] **Step 13: 커밋**

```bash
git add engine/indicators/volume.py engine/indicators/__init__.py trading/live_indicators.py backend/main.py frontend/components/StrategyConditionBuilder.tsx frontend/lib/indicator-guide.ts tests/test_indicators.py tests/test_live_indicators_volume.py tests/test_signal_engine_warmup.py
git commit -m "feat: TRADE_VALUE_PCT 정규화 지표 추가"
```

---

## Task 6: MARKET_TREND_PCT

**Files:**
- Modify: `engine/indicators/market.py` (`MarketTrendPct` 클래스 + factory)
- Modify: `engine/indicators/__init__.py`
- Modify: `engine/condition_tree.py` (`AUX_MARKET_INDICATORS`)
- Test: `tests/test_indicators.py`, `tests/test_condition_tree.py`
- Modify: `trading/live_indicators.py`
- Test: `tests/test_live_indicators_market.py`
- Modify: `backend/main.py`
- Modify: `frontend/components/StrategyConditionBuilder.tsx`
- Modify: `frontend/lib/indicator-guide.ts`

**Interfaces:**
- Produces: `engine.indicators.market.MarketTrendPct`(bt.Indicator, lines=("pct",), params=(period,)); `engine.indicators.create_market_trend_pct(data, **params) -> bt.Indicator`; `trading.live_indicators.create_market_trend_pct(df, **params) -> pd.Series`. `AUX_MARKET_INDICATORS["MARKET_TREND_PCT"] == "KRW-BTC"`.

이 지표는 `btc_close` 보조 라인이 필요해 `tests/test_signal_engine_warmup.py`의 B그룹(`_B_GROUP`)에 해당한다 — A그룹 워밍업 표(`_A_GROUP_PARAMS`)에는 추가하지 않는다(MARKET_TREND와 동일 취급). 단, `_B_GROUP` 집합 자체에는 명시적으로 추가해야 한다 — 안 하면 `LIVE_INDICATOR_FACTORY`에 새로 등록된 `MARKET_TREND_PCT`가 `_B_GROUP`에도 `_A_GROUP_PARAMS`에도 없는 상태가 되어, `test_a_group_params_cover_every_a_group_indicator`의 `set(LIVE_INDICATOR_FACTORY) - _B_GROUP == set(_A_GROUP_PARAMS)` 비교가 깨진다(Step 7 참고).

- [ ] **Step 1: 백테스트 엔진 실패하는 테스트 작성**

`tests/test_indicators.py`의 `test_market_trend_matches_manual_close_minus_sma_of_btc_close_line` 함수 다음에 추가:

```python
def test_market_trend_pct_matches_manual_pct_of_btc_close_vs_sma():
    df = make_oscillating_df()
    btc_close = df["close"] * 2 + 1000
    values = _run_probe_with_aux("MARKET_TREND_PCT", {"period": 5}, "btc_close", btc_close)
    sma = btc_close.rolling(5).mean().iloc[-1]
    manual = (btc_close.iloc[-1] - sma) / sma * 100
    assert abs(values[-1] - manual) < 1e-6
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_indicators.py -k market_trend_pct -v`
Expected: FAIL — `KeyError: 'MARKET_TREND_PCT'`

- [ ] **Step 3: `MarketTrendPct` 클래스와 factory 구현**

`engine/indicators/market.py`의 `create_market_trend` 함수 다음에 추가:

```python
class MarketTrendPct(bt.Indicator):
    """KRW-BTC 종가가 자신의 이동평균(period봉) 대비 몇 % 위/아래에 있는지 나타낸
    정규화 버전. 절대 KRW 차이값(MARKET_TREND)의 코인 시세 종속성을 제거한다."""

    lines = ("pct",)
    params = (("period", 10),)

    def __init__(self) -> None:
        self.market_close = self.data.btc_close
        self.sma = bt.indicators.SMA(self.market_close, period=self.p.period)

    def next(self) -> None:
        sma_val = self.sma[0]
        self.lines.pct[0] = (self.market_close[0] - sma_val) / sma_val * 100 if sma_val else 0.0


def create_market_trend_pct(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 10))
    return MarketTrendPct(data, period=period)
```

`engine/indicators/__init__.py`의 `from .market import (...)` 줄을 수정:

```python
from .market import create_btc_correlation, create_market_trend, create_market_trend_pct, create_usdt_correlation
```

`INDICATOR_FACTORY`에서 `"MARKET_TREND": create_market_trend,` 다음 줄에 추가:

```python
    "MARKET_TREND_PCT": create_market_trend_pct,
```

- [ ] **Step 4: 테스트 통과 확인 + 블랭킷 스모크 테스트 skip 목록 갱신**

Run: `PYTHONPATH=. python -m pytest tests/test_indicators.py -k market_trend_pct -v`
Expected: PASS

`tests/test_indicators.py`의 `_NEEDS_EXTRA_LINE` 집합에 `"MARKET_TREND_PCT"`를 추가하지 않으면, `test_all_registered_indicators_produce_values`(모든 `INDICATOR_FACTORY` 키를 `btc_close` 없는 기본 데이터로 돌리는 블랭킷 스모크 테스트)가 `MarketTrendPct.__init__`의 `self.data.btc_close` 접근에서 크래시한다 — MARKET_TREND가 이미 이 집합에 있는 것과 동일한 이유. `_NEEDS_EXTRA_LINE`을 수정:

```python
_NEEDS_EXTRA_LINE = {"MARKET_TREND", "MARKET_TREND_PCT", "BTC_CORRELATION", "USDT_CORRELATION", "FEAR_GREED_CMC", "KOREA_PREMIUM", "FUNDING_RATE"}  # btc_close/usdt_close 데이터 라인이 필요 — test_market_trend_matches_manual_close_minus_sma_of_btc_close_line 등 참고
```

Run: `PYTHONPATH=. python -m pytest tests/test_indicators.py -v`
Expected: PASS (전부, 블랭킷 스모크 테스트 포함)

- [ ] **Step 5: `AUX_MARKET_INDICATORS` 등록 (테스트 먼저)**

`tests/test_condition_tree.py`의 `test_required_aux_markets_returns_btc_when_market_trend_present` 함수 다음에 추가:

```python
def test_required_aux_markets_returns_btc_when_market_trend_pct_present():
    tree = {
        "type": "AND",
        "conditions": [{"indicator": "MARKET_TREND_PCT", "params": {"period": 10}, "operator": "<", "threshold": 0}],
    }
    assert required_aux_markets(tree) == {"KRW-BTC"}
```

Run: `PYTHONPATH=. python -m pytest tests/test_condition_tree.py -k market_trend_pct -v`
Expected: FAIL — `required_aux_markets(tree) == set()` (아직 미등록)

`engine/condition_tree.py`의 `AUX_MARKET_INDICATORS` 딕셔너리에 추가:

```python
AUX_MARKET_INDICATORS: dict[str, str] = {
    "MARKET_TREND": "KRW-BTC",
    "MARKET_TREND_PCT": "KRW-BTC",
    "BTC_CORRELATION": "KRW-BTC",
    "USDT_CORRELATION": "KRW-USDT",
    "KOREA_PREMIUM": "KRW-USDT",
}
```

Run: `PYTHONPATH=. python -m pytest tests/test_condition_tree.py -v`
Expected: PASS (전부, 기존 `test_aux_market_indicators_values_are_covered_by_runner` 포함 — `AUX_MARKET_LINE_NAME`이 이미 `"KRW-BTC"`를 커버하므로 별도 수정 불필요)

- [ ] **Step 6: 라이브 pandas 구현 (테스트 먼저)**

`tests/test_live_indicators_market.py` 상단 import 줄을 수정:

```python
from trading.live_indicators import (
    LIVE_INDICATOR_FACTORY,
    create_market_trend,
    create_market_trend_pct,
    create_btc_correlation,
    create_usdt_correlation,
)
```

`test_market_trend_uses_default_period_10_when_omitted` 함수 다음에 추가:

```python
def test_market_trend_pct_matches_backtrader():
    df = make_oscillating_df()
    btc_close = df["close"] * 2 + 1000
    df["btc_close"] = btc_close
    assert_matches_backtrader_with_aux(
        "MARKET_TREND_PCT", {"period": 5}, "btc_close", btc_close,
        create_market_trend_pct(df, period=5),
    )


def test_market_trend_pct_uses_default_period_10_when_omitted():
    df = make_oscillating_df()
    df["btc_close"] = df["close"] * 2 + 1000
    default = create_market_trend_pct(df)
    explicit = create_market_trend_pct(df, period=10)
    assert default.equals(explicit)


def test_live_indicator_factory_registers_market_trend_pct():
    assert LIVE_INDICATOR_FACTORY["MARKET_TREND_PCT"] is create_market_trend_pct
```

Run: `PYTHONPATH=. python -m pytest tests/test_live_indicators_market.py -k market_trend_pct -v`
Expected: FAIL — `ImportError`

- [ ] **Step 7: `trading/live_indicators.py`에 함수 구현**

`create_market_trend` 함수 다음에 추가:

```python
def create_market_trend_pct(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 10))
    btc_close = df["btc_close"]
    sma = btc_close.rolling(period).mean()
    return (btc_close - sma) / sma * 100
```

`LIVE_INDICATOR_FACTORY`에서 `"MARKET_TREND": create_market_trend,` 다음 줄에 추가:

```python
    "MARKET_TREND_PCT": create_market_trend_pct,
```

- [ ] **Step 8: `_B_GROUP` 집합 갱신 (누락 시 기존 워밍업 회귀 테스트가 깨짐)**

`tests/test_signal_engine_warmup.py`의 `_B_GROUP` 집합을 수정:

```python
_B_GROUP = {
    "MARKET_TREND", "MARKET_TREND_PCT", "BTC_CORRELATION", "USDT_CORRELATION",
    "FEAR_GREED_CMC", "KOREA_PREMIUM", "FUNDING_RATE",
}
```

Run: `PYTHONPATH=. python -m pytest tests/test_signal_engine_warmup.py -v`
Expected: PASS(`test_a_group_params_cover_every_a_group_indicator` 포함)

- [ ] **Step 9: 라이브 테스트 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_live_indicators_market.py -v`
Expected: PASS

- [ ] **Step 10: 백엔드 카탈로그 등록**

Run: `PYTHONPATH=. python -m pytest tests/test_backend.py -k covers_all_registered -v` → FAIL 확인.

`backend/main.py`의 `INDICATOR_CATALOG`에서 `MARKET_TREND` 항목 다음에 추가:

```python
    {
        "value": "MARKET_TREND_PCT", "label": "시장 추세 (정규화, %)", "category": "시장 심리",
        "params": [{"key": "period", "label": "기간", "default": 10}],
        "description": "KRW-BTC 종가가 자신의 이동평균 대비 몇 % 위/아래에 있는지 나타냅니다. 절대 KRW 차이값(MARKET_TREND)의 정규화 버전입니다.",
        "example": "MARKET_TREND_PCT < -2면 BTC가 자기 이동평균보다 2% 이상 아래(약세)인 구간을 필터로 씁니다.",
    },
```

Run: `PYTHONPATH=. python -m pytest tests/test_backend.py -k covers_all_registered -v`
Expected: PASS

- [ ] **Step 11: 프론트엔드 — 조건식 빌더 threshold 기본값**

```typescript
const ZERO_CROSS_INDICATORS = new Set([
  'MACD_line', 'MACD_signal', 'MARKET_TREND', 'MOMENTUM_PCT', 'KOREA_PREMIUM', 'MACD_PPO', 'MACD_PPO_signal',
  'FIB_382_PCT', 'FIB_500_PCT', 'FIB_618_PCT',
  'PIVOT_P_PCT', 'PIVOT_R1_PCT', 'PIVOT_S1_PCT',
  'VPVR_POC_PCT', 'VPVR_VAH_PCT', 'VPVR_VAL_PCT',
  'SMA_PCT', 'EMA_PCT', 'WMA_PCT',
  'TRADE_VALUE_PCT',
  'MARKET_TREND_PCT',
]);
```

- [ ] **Step 12: 프론트엔드 — 지표 가이드**

`MARKET_TREND` 항목 `usage` 필드를 수정:

```typescript
    usage: '알트코인 매수 조건에 "BTC가 하락 추세가 아닐 때만"이라는 시장 필터를 AND로 추가해, 전체 시장이 흔들릴 때 매수를 쉬는 용도로 씁니다. 코인 시세와 무관한 정규화 버전이 필요하면 MARKET_TREND_PCT를 대신 쓰세요.',
```

`MARKET_TREND` 항목 다음(`MOMENTUM_PCT` 항목 앞)에 신규 최상위 항목 추가:

```typescript
  MARKET_TREND_PCT: {
    meaning:
      'KRW-BTC 종가가 자신의 이동평균(period봉) 대비 몇 % 위/아래에 있는지 나타낸 정규화 지표입니다. 절대 KRW 차이값(MARKET_TREND)의 코인 시세 종속성을 제거한 버전 — 계산 배경은 MARKET_TREND 가이드를 참고하세요.',
    params: [{ key: 'period', role: 'KRW-BTC 종가의 이동평균을 계산할 봉 개수. MARKET_TREND와 동일한 의미.' }],
    formula: 'MARKET_TREND_PCT = (KRW-BTC 종가 − KRW-BTC 이동평균) ÷ KRW-BTC 이동평균 × 100',
    thresholdExample: 'MARKET_TREND_PCT < -2면 BTC가 자기 이동평균보다 2% 이상 아래(약세)인 구간을 필터로 씁니다. MARKET_TREND와 달리 BTC 가격 수준이 시기마다 달라져도 같은 threshold를 계속 쓸 수 있습니다.',
    usage: 'MARKET_TREND는 BTC 가격이 오르내릴수록 "같은 threshold가 뜻하는 이격 폭"도 달라지는 문제가 있습니다 — 장기간 백테스트나 여러 시기에 걸쳐 같은 threshold를 쓰고 싶을 때 이 지표를 대신 씁니다.',
  },
```

- [ ] **Step 13: 전체 테스트 스위트 확인**

Run: `PYTHONPATH=. python -m pytest tests/ -v`
Expected: PASS (전부)

- [ ] **Step 14: 커밋**

```bash
git add engine/indicators/market.py engine/indicators/__init__.py engine/condition_tree.py trading/live_indicators.py backend/main.py frontend/components/StrategyConditionBuilder.tsx frontend/lib/indicator-guide.ts tests/test_indicators.py tests/test_condition_tree.py tests/test_live_indicators_market.py tests/test_signal_engine_warmup.py
git commit -m "feat: MARKET_TREND_PCT 정규화 지표 추가"
```

---

## Task 7: 최종 검증

**Files:** 없음(검증 전용, 코드 변경 없음)

**Interfaces:**
- Consumes: Task 1~6에서 등록된 14개 신규 지표 전부.

- [ ] **Step 1: 전체 백엔드 테스트 스위트 실행**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/ -v`
Expected: PASS 전부 (기존 테스트 포함 회귀 없음). 실패가 있으면 Task 1~6 중 어느 단계에서 빠뜨렸는지 역추적 — 가장 흔한 원인은 `INDICATOR_FACTORY`/`INDICATOR_CATALOG`/`LIVE_INDICATOR_FACTORY`/`_A_GROUP_PARAMS` 중 한 곳만 갱신하고 나머지를 빠뜨린 경우다.

- [ ] **Step 2: 신규 지표 개수 확인**

Run:
```bash
PYTHONPATH=. python -c "
from engine.indicators import INDICATOR_FACTORY
from trading.live_indicators import LIVE_INDICATOR_FACTORY
pct_engine = sorted(k for k in INDICATOR_FACTORY if k.endswith('_PCT') and k not in ('ATR_PCT', 'MACD_PPO', 'MACD_PPO_signal', 'MOMENTUM_PCT', 'BB_PERCENT_B'))
pct_live = sorted(k for k in LIVE_INDICATOR_FACTORY if k.endswith('_PCT') and k not in ('ATR_PCT', 'MOMENTUM_PCT'))
print(len(pct_engine), pct_engine)
print(len(pct_live), pct_live)
"
```
Expected: 두 출력 모두 14개, 동일한 지표명 집합(`FIB_382_PCT`, `FIB_500_PCT`, `FIB_618_PCT`, `PIVOT_P_PCT`, `PIVOT_R1_PCT`, `PIVOT_S1_PCT`, `VPVR_POC_PCT`, `VPVR_VAH_PCT`, `VPVR_VAL_PCT`, `SMA_PCT`, `EMA_PCT`, `WMA_PCT`, `TRADE_VALUE_PCT`, `MARKET_TREND_PCT`).

- [ ] **Step 3: 프론트엔드 개발 서버로 UI 확인**

Run: (`frontend/` 디렉터리에서) `npm run dev`

브라우저에서 백테스트 생성/편집 화면의 조건식 빌더를 열어:
1. 지표 드롭다운에서 "가격대"/"추세"/"거래대금"/"시장 심리" 카테고리에 신규 `_PCT` 항목 14개가 보이는지 확인.
2. `SMA_PCT`를 선택했을 때 threshold 입력란 기본값이 `0`으로 채워지는지 확인(다른 13개도 동일하게 확인).
3. 지표 가이드 탭에서 신규 14개 항목이 각각 열리고 meaning/formula/thresholdExample/usage가 표시되는지 확인.
4. `SMA` 항목을 열어 usage 끝에 "SMA_PCT를 대신 쓰세요" 교차참조 문장이 보이는지 확인(나머지 13개 원본도 동일 패턴인지 샘플로 2~3개 더 확인).

- [ ] **Step 4: 개발 서버 종료**

UI 확인이 끝나면 `npm run dev` 프로세스를 종료한다(Ctrl+C 또는 해당 터미널 종료).

이 태스크는 커밋할 코드 변경이 없다 — 검증만 수행한다.
