# 거래량/OBV 정규화 지표 (VOLUME, VOLUME_PCT, OBV_ROC) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 코인마다 스케일이 제각각인 `OBV`/`VOLUME_SMA`에 짝이 되는 정규화 지표 3개(`VOLUME`, `VOLUME_PCT`, `OBV_ROC`)를 조건식 카탈로그에 독립 항목으로 추가한다.

**Architecture:** `VOLUME`/`VOLUME_PCT`는 이미 있는 `TRADE_VALUE`/`TRADE_VALUE_PCT` 패턴을 거래량(수량)에 그대로 적용한다. `OBV_ROC`는 OBV의 N봉 변화량(순매수 거래량)을 같은 구간 총 거래량으로 나눈 새 공식(`RollingCorrelation`의 `addminperiod` 관례를 재사용)이 필요하다. 세 지표 모두 (1) 백테스트 엔진(`engine/indicators/volume.py` + `engine/indicators/__init__.py`), (2) 라이브 트레이딩 pandas 구현(`trading/live_indicators.py`), (3) 백엔드 카탈로그(`backend/main.py`), (4) 프론트엔드 조건식 빌더/가이드(`frontend/`) 네 곳에 동시에 반영해야 한다 — 이 프로젝트는 백테스트/라이브 계산 로직을 "의도적으로 중복된 쌍둥이 함수"로 유지하고, 백엔드에는 `INDICATOR_FACTORY`와 `INDICATOR_CATALOG`가 정확히 일치해야 하는 기존 테스트(`test_get_indicator_catalog_covers_all_registered_indicators`)가, 라이브 쪽에는 `LIVE_INDICATOR_FACTORY`와 워밍업 파라미터 표가 정확히 일치해야 하는 기존 테스트(`test_a_group_params_cover_every_a_group_indicator`)가 이미 있다 — 새 지표를 한쪽에만 등록하면 CI가 아니라 이 두 테스트가 즉시 실패한다.

**Tech Stack:** Python(backtrader, pandas), FastAPI, TypeScript/React(Next.js), pytest.

## Global Constraints

- `VOLUME_PCT`/`OBV_ROC` 공식은 `docs/superpowers/specs/2026-08-19-volume-obv-normalization-design.md`에서 확정한 그대로다: `VOLUME_PCT = (거래량 - VOLUME_SMA) / VOLUME_SMA × 100`, `OBV_ROC = (OBV[0] - OBV[N봉전]) / (최근 N봉 거래량 합) × 100`.
- `VOLUME_PCT`의 `period` 기본값은 20 (`VOLUME_SMA`와 동일). `OBV_ROC`의 `period` 기본값은 14.
- 기존 `OBV`, `VOLUME_SMA` 카탈로그 항목은 삭제·변경 없이 그대로 유지한다. `usage` 필드 끝에만 신규 정규화 버전으로의 교차참조 문장을 추가한다.
- 신규 3개의 카탈로그 카테고리는 기존 `OBV`/`VOLUME_SMA`와 동일하게 "거래량"으로 배치한다.
- UI 기본 threshold 추천값: `VOLUME_PCT`/`OBV_ROC`는 0(`ZERO_CROSS_INDICATORS`에 등록). `VOLUME`(원시값)은 등록하지 않는다 — 기존 `OBV`/`VOLUME_SMA`와 마찬가지로 코인마다 스케일이 제각각이라 fallback 0 placeholder만 받는다.
- 세 지표 모두 `data.volume`/`data.close` 등 표준 OHLCV 필드만 사용한다 — `AUX_MARKET_INDICATORS`나 `_NEEDS_EXTRA_LINE`/`_NEEDS_TRADE_VALUE_LINE` 같은 스모크 테스트 제외 목록 수정이 필요 없다.
- 사용자 대상 문자열(카탈로그 label/description/example, 프론트 가이드 텍스트)은 전부 한국어로 작성한다.

---

## Task 1: `VOLUME` / `VOLUME_PCT`

**Files:**
- Modify: `engine/indicators/volume.py` (`create_volume_sma` 함수 다음에 `create_volume` + `VolumeRatio` 클래스 + `create_volume_pct` 추가)
- Modify: `engine/indicators/__init__.py`
- Test: `tests/test_indicators.py`
- Modify: `trading/live_indicators.py`
- Test: `tests/test_live_indicators_volume.py`
- Modify: `tests/test_signal_engine_warmup.py` (`_A_GROUP_PARAMS`)
- Modify: `backend/main.py` (`INDICATOR_CATALOG`)
- Modify: `frontend/components/StrategyConditionBuilder.tsx` (`ZERO_CROSS_INDICATORS`)
- Modify: `frontend/lib/indicator-guide.ts`

**Interfaces:**
- Produces: `engine.indicators.volume.create_volume(data, **params) -> bt.Indicator`(사실상 `data.volume` 그대로 반환); `engine.indicators.volume.VolumeRatio`(bt.Indicator, lines=("pct",), params=(period,)); `engine.indicators.volume.create_volume_pct(data, **params) -> bt.Indicator`; `trading.live_indicators.create_volume(df, **params) -> pd.Series`; `trading.live_indicators.create_volume_pct(df, **params) -> pd.Series`. `INDICATOR_FACTORY`/`LIVE_INDICATOR_FACTORY`에 `"VOLUME"`/`"VOLUME_PCT"` 키 등록. Task 2가 이 파일들을 계속 이어서 수정한다.

- [ ] **Step 1: 백테스트 엔진 실패하는 테스트 작성**

`tests/test_indicators.py`의 `test_trade_value_pct_matches_manual_ratio_to_own_sma` 함수(140~146행) 다음에 추가:

```python
def test_volume_matches_raw_volume_column():
    values = _run_probe("VOLUME", {})
    df = make_oscillating_df()
    assert abs(values[-1] - df["volume"].iloc[-1]) < 1e-6


def test_volume_pct_matches_manual_ratio_to_own_sma():
    values = _run_probe("VOLUME_PCT", {"period": 5})
    df = make_oscillating_df()
    sma = df["volume"].rolling(5).mean().iloc[-1]
    manual = (df["volume"].iloc[-1] - sma) / sma * 100
    assert abs(values[-1] - manual) < 1e-6


def test_volume_pct_handles_zero_level_without_crashing():
    # 거래량 이동평균이 0인 극단 케이스(합성 데이터) — ZeroDivisionError 없이 0.0을 반환해야 함.
    idx = pd.date_range("2026-01-01", periods=10, freq="h", tz="UTC")
    df = pd.DataFrame({
        "candle_time": idx, "open": [100.0] * 10, "high": [100.0] * 10,
        "low": [100.0] * 10, "close": [100.0] * 10, "volume": [0.0] * 10,
    })
    df_bt = df.set_index("candle_time")
    df_bt.index = df_bt.index.tz_localize(None)
    cerebro = bt.Cerebro()
    cerebro.adddata(bt.feeds.PandasData(dataname=df_bt, openinterest=-1))
    cerebro.addstrategy(_ProbeStrategy, indicator="VOLUME_PCT", indicator_params={"period": 3})
    results = cerebro.run()
    assert results[0].seen_values[-1] == pytest.approx(0.0)
```

`VOLUME`/`VOLUME_PCT` 둘 다 `data.volume`이 모든 기본 합성 데이터에 이미 있는 표준 OHLCV 필드라, `TRADE_VALUE`처럼 `_run_probe_with_aux`로 보조 라인을 병합할 필요 없이 `_run_probe`를 그대로 쓴다.

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_indicators.py -k "volume_matches_raw or volume_pct" -v`
Expected: FAIL — `KeyError: 'VOLUME'` (INDICATOR_FACTORY에 아직 없음).

- [ ] **Step 3: `create_volume`/`VolumeRatio`/`create_volume_pct` 구현**

`engine/indicators/volume.py`의 `create_volume_sma` 함수(41~43행) 다음, `create_trade_value` 함수(46행) 앞에 추가:

```python
def create_volume(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    """봉의 원시 거래량(수량) — TRADE_VALUE와 동일한 위상으로, 조건식에서 직접 쓸 수
    있게 data.volume 라인을 그대로 노출한다."""
    return data.volume


class VolumeRatio(bt.Indicator):
    """이번 봉 거래량이 자체 이동평균(period봉) 대비 몇 % 높거나 낮은지를 나타낸
    정규화 버전. TradeValueRatio와 동일한 패턴을 거래량(수량)에 적용한다."""

    lines = ("pct",)
    params = (("period", 20),)

    def __init__(self) -> None:
        self.sma = bt.indicators.SMA(self.data.volume, period=self.p.period)

    def next(self) -> None:
        sma_val = self.sma[0]
        self.lines.pct[0] = (self.data.volume[0] - sma_val) / sma_val * 100 if sma_val else 0.0


def create_volume_pct(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 20))
    return VolumeRatio(data, period=period)
```

`engine/indicators/__init__.py`의 `from .volume import (...)` 줄(33행)을 수정:

```python
from .volume import create_obv, create_trade_value, create_trade_value_pct, create_trade_value_sma, create_volume, create_volume_pct, create_volume_sma, create_vpin
```

`INDICATOR_FACTORY` 딕셔너리에서 `"VOLUME_SMA": create_volume_sma,` 다음 줄(58행 다음)에 추가:

```python
    "VOLUME": create_volume,
    "VOLUME_PCT": create_volume_pct,
```

- [ ] **Step 4: 백테스트 엔진 테스트 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_indicators.py -k "volume_matches_raw or volume_pct" -v`
Expected: PASS (3 tests)

이어서 블랭킷 스모크 테스트가 여전히 통과하는지 확인 (VOLUME/VOLUME_PCT는 표준 OHLCV 필드만 쓰므로 `_NEEDS_EXTRA_LINE`/`_NEEDS_TRADE_VALUE_LINE` 수정 없이도 통과해야 함):

Run: `PYTHONPATH=. python -m pytest tests/test_indicators.py -v`
Expected: 전체 PASS

- [ ] **Step 5: 워밍업 회귀 테스트 표 갱신**

`tests/test_signal_engine_warmup.py`의 `_A_GROUP_PARAMS` 딕셔너리에서 `"VOLUME_SMA": {"period": 20},` 다음 줄(62행 다음)에 추가:

```python
    "VOLUME": {},
    "VOLUME_PCT": {"period": 20},
```

(이 시점엔 아직 `LIVE_INDICATOR_FACTORY`에 이 키들이 없으므로 `test_a_group_params_cover_every_a_group_indicator`는 여전히 실패한다 — Step 7에서 등록 후 통과.)

- [ ] **Step 6: 라이브 pandas 구현 (테스트 먼저)**

`tests/test_live_indicators_volume.py` 상단 import 블록(8~16행)을 수정:

```python
from trading.live_indicators import (
    LIVE_INDICATOR_FACTORY,
    create_obv,
    create_trade_value,
    create_trade_value_pct,
    create_trade_value_sma,
    create_volume,
    create_volume_pct,
    create_volume_sma,
    create_vpin,
)
```

`test_volume_sma_matches_backtrader` 함수(27~29행) 다음에 추가:

```python
def test_volume_matches_raw_volume_column():
    df = make_oscillating_df()
    result = create_volume(df)
    assert abs(result.iloc[-1] - df["volume"].iloc[-1]) < 1e-6


def test_volume_pct_matches_manual_ratio_to_own_sma():
    df = make_oscillating_df()
    result = create_volume_pct(df, period=5)
    sma = df["volume"].rolling(5).mean()
    manual = (df["volume"] - sma) / sma * 100
    assert abs(result.iloc[-1] - manual.iloc[-1]) < 1e-6


def test_live_indicator_factory_registers_volume_and_volume_pct():
    assert LIVE_INDICATOR_FACTORY["VOLUME"] is create_volume
    assert LIVE_INDICATOR_FACTORY["VOLUME_PCT"] is create_volume_pct
```

Run: `PYTHONPATH=. python -m pytest tests/test_live_indicators_volume.py -k "volume_matches_raw or volume_pct" -v`
Expected: FAIL — `ImportError: cannot import name 'create_volume' from 'trading.live_indicators'`

- [ ] **Step 7: `trading/live_indicators.py`에 함수 구현**

`create_volume_sma` 함수(201~203행) 다음, `create_trade_value` 함수(206행) 앞에 추가:

```python
def create_volume(df: pd.DataFrame, **params) -> pd.Series:
    return df["volume"]


def create_volume_pct(df: pd.DataFrame, **params) -> pd.Series:
    sma = create_volume_sma(df, **params)
    return (df["volume"] - sma) / sma * 100
```

`LIVE_INDICATOR_FACTORY` 딕셔너리에서 `"VOLUME_SMA": create_volume_sma,` 다음 줄에 추가:

```python
    "VOLUME": create_volume,
    "VOLUME_PCT": create_volume_pct,
```

- [ ] **Step 8: 라이브 + 워밍업 테스트 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_live_indicators_volume.py tests/test_signal_engine_warmup.py -v`
Expected: PASS (전부, `test_a_group_params_cover_every_a_group_indicator` 포함)

- [ ] **Step 9: 백엔드 카탈로그 등록 (테스트 먼저)**

Run: `PYTHONPATH=. python -m pytest tests/test_backend.py -k covers_all_registered -v`
Expected: FAIL — `catalog_values == set(INDICATOR_FACTORY.keys())...` 불일치(2개 누락).

`backend/main.py`의 `INDICATOR_CATALOG` 리스트에서 `VOLUME_SMA` 항목(`"value": "VOLUME_SMA", ...` 딕셔너리, 314~318행) 바로 다음, `VPIN` 항목(319행) 앞에 추가:

```python
    {
        "value": "VOLUME", "label": "거래량", "category": "거래량",
        "params": [],
        "description": "봉의 원시 거래량(코인 수량)입니다. 거래대금(TRADE_VALUE)과 달리 가격이 반영되지 않은 순수 수량 기준입니다.",
        "example": "거래량이 특정 절대 수치 이상인지 확인하는 조건 등에 씁니다. 코인마다 스케일이 크게 달라 여러 코인에 같은 threshold를 재사용하기 어렵습니다.",
    },
    {
        "value": "VOLUME_PCT", "label": "거래량 비율 (%)", "category": "거래량",
        "params": [{"key": "period", "label": "기간", "default": 20}],
        "description": "이번 봉 거래량이 자체 이동평균(VOLUME_SMA) 대비 몇 % 높거나 낮은지 나타냅니다. 코인마다 다른 거래량 스케일을 제거합니다.",
        "example": "VOLUME_PCT > 100이면 평소 대비 거래량이 2배 이상으로 튄 구간입니다.",
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
  'PIVOT_P_PCT', 'PIVOT_R1_PCT', 'PIVOT_S1_PCT',
  'VPVR_POC_PCT', 'VPVR_VAH_PCT', 'VPVR_VAL_PCT',
  'SMA_PCT', 'EMA_PCT', 'WMA_PCT',
  'TRADE_VALUE_PCT',
  'MARKET_TREND_PCT',
  'VOLUME_PCT',
]);
```

(`VOLUME`은 추가하지 않는다 — 코인마다 스케일이 제각각이라 기존 `OBV`/`VOLUME_SMA`와 같은 fallback 0 placeholder를 그대로 받는다.)

- [ ] **Step 11: 프론트엔드 — 지표 가이드**

`frontend/lib/indicator-guide.ts`의 `VOLUME_SMA` 항목(234~240행) `usage` 필드를 수정:

```typescript
    usage: '거래량 급증과 가격 조건을 AND로 묶어 "관심이 몰리는 순간의 돌파"만 남기는 필터로 씁니다. 코인 시세와 무관한 정규화 버전이 필요하면 VOLUME_PCT를 대신 쓰세요.',
```

`VOLUME_SMA` 항목 다음(`TRADE_VALUE` 항목 앞)에 신규 최상위 항목 2개 추가:

```typescript
  VOLUME: {
    meaning: '봉의 원시 거래량(코인 수량)입니다. 거래대금(TRADE_VALUE)과 달리 가격이 반영되지 않은 순수 수량 기준입니다.',
    params: [],
    formula: 'VOLUME = 해당 봉의 거래량(수량)',
    thresholdExample: '코인마다 유통량과 거래 규모가 완전히 달라 절대값 threshold는 코인별로 다시 추정해야 합니다.',
    usage: '거래량이 특정 절대 수치 이상인지 확인하는 조건, 혹은 VOLUME_SMA와 함께 "지금 거래량이 평균보다 큰가"를 판단하는 데 씁니다. 코인 시세와 무관한 정규화 버전이 필요하면 VOLUME_PCT를 대신 쓰세요.',
  },
  VOLUME_PCT: {
    meaning: '이번 봉 거래량이 자체 이동평균(VOLUME_SMA) 대비 몇 % 위/아래에 있는지 나타낸 정규화 지표입니다. TRADE_VALUE_PCT와 동일한 계산 방식을 거래량(수량)에 적용합니다.',
    params: [{ key: 'period', role: '거래량 이동평균을 계산할 봉 개수. VOLUME_SMA와 동일한 의미.' }],
    formula: 'VOLUME_PCT = (거래량 − VOLUME_SMA) ÷ VOLUME_SMA × 100',
    thresholdExample: 'VOLUME_PCT > 100이면 평소 대비 거래량이 2배 이상으로 튄 구간입니다. 코인마다 유통량이 달라도 같은 threshold를 그대로 쓸 수 있습니다.',
    usage: 'VOLUME_SMA를 절대값으로 비교하기 애매할 때, 대신 이 지표로 "평균 대비 몇 % 튀었는지"를 오실레이터처럼 씁니다. 거래량 급증과 가격 조건을 AND로 묶는 돌파 필터에 특히 유용합니다.',
  },
```

- [ ] **Step 12: 타입 체크 + 전체 테스트 스위트 확인**

Run: `PYTHONPATH=. python -m pytest tests/ -v`
Expected: PASS (전부, 신규 포함)

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음 (0 errors)

- [ ] **Step 13: 커밋**

```bash
git add engine/indicators/volume.py engine/indicators/__init__.py trading/live_indicators.py backend/main.py frontend/components/StrategyConditionBuilder.tsx frontend/lib/indicator-guide.ts tests/test_indicators.py tests/test_live_indicators_volume.py tests/test_signal_engine_warmup.py
git commit -m "feat: VOLUME/VOLUME_PCT 정규화 지표 추가"
```

---

## Task 2: `OBV_ROC`

**Files:**
- Modify: `engine/indicators/volume.py` (`create_obv` 함수 다음에 `OBVRoc` 클래스 + `create_obv_roc` 추가)
- Modify: `engine/indicators/__init__.py`
- Test: `tests/test_indicators.py`
- Modify: `trading/live_indicators.py`
- Test: `tests/test_live_indicators_volume.py`
- Modify: `tests/test_signal_engine_warmup.py` (`_A_GROUP_PARAMS`)
- Modify: `backend/main.py` (`INDICATOR_CATALOG`)
- Modify: `frontend/components/StrategyConditionBuilder.tsx` (`ZERO_CROSS_INDICATORS`)
- Modify: `frontend/lib/indicator-guide.ts`

**Interfaces:**
- Consumes: `engine.indicators.volume.OBV`(기존, lines=("obv",)); Task 1이 만든 `engine/indicators/__init__.py`의 `from .volume import (...)` 줄, `trading/live_indicators.py`의 `create_obv`(기존).
- Produces: `engine.indicators.volume.OBVRoc`(bt.Indicator, lines=("pct",), params=(period,)); `engine.indicators.volume.create_obv_roc(data, **params) -> bt.Indicator`; `trading.live_indicators.create_obv_roc(df, **params) -> pd.Series`. `INDICATOR_FACTORY`/`LIVE_INDICATOR_FACTORY`에 `"OBV_ROC"` 키 등록.

- [ ] **Step 1: 백테스트 엔진 실패하는 테스트 작성**

`tests/test_indicators.py`의 `test_obv_matches_manual_cumulative_volume_by_close_direction` 함수(452~465행) 다음에 추가:

```python
def test_obv_roc_matches_manual_net_volume_over_total_volume():
    values = _run_probe("OBV_ROC", {"period": 5})
    df = make_oscillating_df()
    closes = df["close"].tolist()
    volumes = df["volume"].tolist()
    obv = [0.0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            obv.append(obv[-1] + volumes[i])
        elif closes[i] < closes[i - 1]:
            obv.append(obv[-1] - volumes[i])
        else:
            obv.append(obv[-1])
    net_change = obv[-1] - obv[-1 - 5]
    total_volume = sum(volumes[-5:])
    manual = net_change / total_volume * 100
    assert abs(values[-1] - manual) < 1e-6


def test_obv_roc_stays_within_bounded_range():
    # OBV_ROC = 순매수 거래량 / 총 거래량 × 100 이므로 수학적으로 항상 -100~+100.
    values = _run_probe("OBV_ROC", {"period": 10})
    assert all(-100.0 <= v <= 100.0 for v in values)


def test_obv_roc_handles_zero_total_volume_without_crashing():
    # 구간에 거래가 아예 없어 총 거래량이 0인 극단 케이스 — ZeroDivisionError 없이 0.0을 반환해야 함.
    idx = pd.date_range("2026-01-01", periods=10, freq="h", tz="UTC")
    df = pd.DataFrame({
        "candle_time": idx, "open": [100.0] * 10, "high": [100.0] * 10,
        "low": [100.0] * 10, "close": [100.0] * 10, "volume": [0.0] * 10,
    })
    df_bt = df.set_index("candle_time")
    df_bt.index = df_bt.index.tz_localize(None)
    cerebro = bt.Cerebro()
    cerebro.adddata(bt.feeds.PandasData(dataname=df_bt, openinterest=-1))
    cerebro.addstrategy(_ProbeStrategy, indicator="OBV_ROC", indicator_params={"period": 3})
    results = cerebro.run()
    assert results[0].seen_values[-1] == pytest.approx(0.0)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_indicators.py -k obv_roc -v`
Expected: FAIL — `KeyError: 'OBV_ROC'`

- [ ] **Step 3: `OBVRoc` 클래스와 factory 구현**

`engine/indicators/volume.py`의 `create_obv` 함수(37~38행) 다음, `create_volume_sma` 함수(41행) 앞에 추가:

```python
class OBVRoc(bt.Indicator):
    """OBV의 N봉 변화량(순매수 거래량)을 같은 구간 총 거래량으로 나눈 정규화 버전.
    OBV 자체는 누적합이라 절대값이 무한히 커지고 코인마다 스케일이 달라 threshold를
    코인 간 공유할 수 없다 — 이 버전은 그 구간 순매수세가 총 거래량의 몇 %였는지를
    나타내 항상 -100~+100 범위로 유계된다."""

    lines = ("pct",)
    params = (("period", 14),)

    def __init__(self) -> None:
        self.obv = OBV(self.data)
        self.volume_sum = bt.indicators.SumN(self.data.volume, period=self.p.period)
        self.addminperiod(self.p.period + 2)  # OBV 자체 minperiod(2) + N봉 lookback

    def next(self) -> None:
        net_change = self.obv[0] - self.obv[-self.p.period]
        total_volume = self.volume_sum[0]
        self.lines.pct[0] = net_change / total_volume * 100 if total_volume else 0.0


def create_obv_roc(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 14))
    return OBVRoc(data, period=period)
```

`engine/indicators/__init__.py`의 `from .volume import (...)` 줄(Task 1에서 수정된 상태)을 수정:

```python
from .volume import create_obv, create_obv_roc, create_trade_value, create_trade_value_pct, create_trade_value_sma, create_volume, create_volume_pct, create_volume_sma, create_vpin
```

`INDICATOR_FACTORY` 딕셔너리에서 `"OBV": create_obv,` 다음 줄에 추가:

```python
    "OBV_ROC": create_obv_roc,
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_indicators.py -k obv_roc -v`
Expected: PASS (3 tests)

이어서 블랭킷 스모크 테스트 확인 (OBV_ROC도 표준 OHLCV 필드만 쓰므로 `_NEEDS_EXTRA_LINE` 수정 없이 통과해야 함):

Run: `PYTHONPATH=. python -m pytest tests/test_indicators.py -v`
Expected: 전체 PASS

- [ ] **Step 5: 워밍업 회귀 테스트 표 갱신**

`tests/test_signal_engine_warmup.py`의 `_A_GROUP_PARAMS`에서 `"OBV": {},` 다음 줄에 추가:

```python
    "OBV_ROC": {"period": 14},
```

(이 시점엔 아직 `LIVE_INDICATOR_FACTORY`에 이 키가 없으므로 `test_a_group_params_cover_every_a_group_indicator`는 여전히 실패한다 — Step 7에서 등록 후 통과.)

- [ ] **Step 6: 라이브 pandas 구현 (테스트 먼저)**

`tests/test_live_indicators_volume.py` 상단 import 블록(Task 1에서 수정된 상태)을 수정:

```python
from trading.live_indicators import (
    LIVE_INDICATOR_FACTORY,
    create_obv,
    create_obv_roc,
    create_trade_value,
    create_trade_value_pct,
    create_trade_value_sma,
    create_volume,
    create_volume_pct,
    create_volume_sma,
    create_vpin,
)
```

`test_obv_matches_backtrader` 함수 다음에 추가:

```python
def test_obv_roc_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("OBV_ROC", {"period": 5}, create_obv_roc(df, period=5))


def test_obv_roc_stays_within_bounded_range():
    df = make_oscillating_df()
    result = create_obv_roc(df, period=10).dropna()
    assert ((result >= -100.0) & (result <= 100.0)).all()


def test_live_indicator_factory_registers_obv_roc():
    assert LIVE_INDICATOR_FACTORY["OBV_ROC"] is create_obv_roc
```

Run: `PYTHONPATH=. python -m pytest tests/test_live_indicators_volume.py -k obv_roc -v`
Expected: FAIL — `ImportError: cannot import name 'create_obv_roc' from 'trading.live_indicators'`

- [ ] **Step 7: `trading/live_indicators.py`에 함수 구현**

`create_obv` 함수(196~198행) 다음, `create_volume_sma` 함수 앞에 추가:

```python
def create_obv_roc(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 14))
    obv = create_obv(df, **params)
    volume_sum = df["volume"].rolling(period).sum()
    net_change = obv - obv.shift(period)
    return net_change / volume_sum * 100
```

`LIVE_INDICATOR_FACTORY` 딕셔너리에서 `"OBV": create_obv,` 다음 줄에 추가:

```python
    "OBV_ROC": create_obv_roc,
```

- [ ] **Step 8: 라이브 + 워밍업 테스트 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_live_indicators_volume.py tests/test_signal_engine_warmup.py -v`
Expected: PASS (전부, `test_a_group_params_cover_every_a_group_indicator` 포함)

- [ ] **Step 9: 백엔드 카탈로그 등록**

Run: `PYTHONPATH=. python -m pytest tests/test_backend.py -k covers_all_registered -v` → FAIL 확인.

`backend/main.py`의 `INDICATOR_CATALOG`에서 `OBV` 항목(`"value": "OBV", ...` 딕셔너리, 308~312행) 바로 다음, `VOLUME_SMA` 항목(313행) 앞에 추가:

```python
    {
        "value": "OBV_ROC", "label": "OBV 변화율 (정규화, %)", "category": "거래량",
        "params": [{"key": "period", "label": "기간", "default": 14}],
        "description": "최근 N봉 동안의 순매수 거래량(OBV 변화량)이 같은 구간 총 거래량의 몇 %였는지 나타냅니다. 항상 -100~100 범위입니다.",
        "example": "OBV_ROC > 30이면 최근 N봉 동안 매수세가 거래량의 30% 이상을 차지한 강한 매수 압력 구간입니다.",
    },
```

Run: `PYTHONPATH=. python -m pytest tests/test_backend.py -k covers_all_registered -v`
Expected: PASS

- [ ] **Step 10: 프론트엔드 — 조건식 빌더 threshold 기본값**

`frontend/components/StrategyConditionBuilder.tsx`의 `ZERO_CROSS_INDICATORS`를 계속 확장(Task 1에서 만든 `'VOLUME_PCT'` 다음 줄):

```typescript
const ZERO_CROSS_INDICATORS = new Set([
  'MACD_line', 'MACD_signal', 'MARKET_TREND', 'MOMENTUM_PCT', 'KOREA_PREMIUM', 'MACD_PPO', 'MACD_PPO_signal',
  'FIB_382_PCT', 'FIB_500_PCT', 'FIB_618_PCT',
  'PIVOT_P_PCT', 'PIVOT_R1_PCT', 'PIVOT_S1_PCT',
  'VPVR_POC_PCT', 'VPVR_VAH_PCT', 'VPVR_VAL_PCT',
  'SMA_PCT', 'EMA_PCT', 'WMA_PCT',
  'TRADE_VALUE_PCT',
  'MARKET_TREND_PCT',
  'VOLUME_PCT',
  'OBV_ROC',
]);
```

- [ ] **Step 11: 프론트엔드 — 지표 가이드**

`frontend/lib/indicator-guide.ts`의 `OBV` 항목(225~233행) `usage` 필드를 수정:

```typescript
    usage: '가격은 오르는데 OBV가 못 따라 오르면(다이버전스) 상승이 힘을 잃고 있다는 경고로 흔히 씁니다. 코인 시세와 무관한 정규화 버전이 필요하면 OBV_ROC를 대신 쓰세요.',
```

`OBV` 항목 다음(`VOLUME_SMA` 항목 앞)에 신규 최상위 항목 추가:

```typescript
  OBV_ROC: {
    meaning:
      'OBV의 N봉 변화량(순매수 거래량)이 같은 구간 총 거래량의 몇 %였는지 나타낸 정규화 지표입니다. OBV는 누적합이라 절대값이 코인마다 다르고 시간이 지날수록 계속 커져 "레벨 대비 % 거리" 방식이 성립하지 않는데, 이 지표는 대신 그 구간 순매수세가 총 거래량에서 차지하는 비중으로 표현해 항상 -100~+100 범위로 유계됩니다 — 계산 배경은 OBV 가이드를 참고하세요.',
    params: [{ key: 'period', role: 'OBV 변화량과 총 거래량을 계산할 롤링 윈도우 봉 개수.' }],
    formula: 'OBV_ROC = (OBV − N봉 전 OBV) ÷ (최근 N봉 거래량 합) × 100',
    thresholdExample: 'OBV_ROC > 30이면 최근 N봉 동안 매수세가 거래량의 30% 이상을 차지한 강한 매수 압력 구간입니다. OBV_ROC < -30이면 반대로 강한 매도 압력 구간입니다. 항상 -100~100 범위라 코인마다 같은 threshold를 그대로 쓸 수 있습니다.',
    usage: 'OBV는 누적값이라 코인마다, 또 같은 코인이라도 기간마다 절대 스케일이 달라 threshold를 공유할 수 없습니다 — 여러 코인이나 장기간에 걸쳐 같은 "매수/매도 압력 강도" 기준을 쓰고 싶을 때 이 지표를 대신 씁니다.',
  },
```

- [ ] **Step 12: 타입 체크 + 전체 테스트 스위트 확인**

Run: `PYTHONPATH=. python -m pytest tests/ -v`
Expected: PASS (전부, 신규 포함)

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음 (0 errors)

- [ ] **Step 13: 커밋**

```bash
git add engine/indicators/volume.py engine/indicators/__init__.py trading/live_indicators.py backend/main.py frontend/components/StrategyConditionBuilder.tsx frontend/lib/indicator-guide.ts tests/test_indicators.py tests/test_live_indicators_volume.py tests/test_signal_engine_warmup.py
git commit -m "feat: OBV_ROC 정규화 지표 추가"
```

---

## Task 3: 최종 검증

**Files:** 없음(검증 전용, 코드 변경 없음)

**Interfaces:**
- Consumes: Task 1~2에서 등록된 3개 신규 지표 전부.

- [ ] **Step 1: 전체 테스트 스위트 실행**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/ -v`
Expected: PASS 전부 (기존 테스트 포함 회귀 없음). 실패가 있으면 Task 1~2 중 어느 단계에서 빠뜨렸는지 역추적 — 가장 흔한 원인은 `INDICATOR_FACTORY`/`INDICATOR_CATALOG`/`LIVE_INDICATOR_FACTORY`/`_A_GROUP_PARAMS` 중 한 곳만 갱신하고 나머지를 빠뜨린 경우다.

- [ ] **Step 2: 신규 지표 개수 확인**

Run:
```bash
PYTHONPATH=. python -c "
from engine.indicators import INDICATOR_FACTORY
from trading.live_indicators import LIVE_INDICATOR_FACTORY
new = sorted(k for k in INDICATOR_FACTORY if k in ('VOLUME', 'VOLUME_PCT', 'OBV_ROC'))
new_live = sorted(k for k in LIVE_INDICATOR_FACTORY if k in ('VOLUME', 'VOLUME_PCT', 'OBV_ROC'))
print(len(new), new)
print(len(new_live), new_live)
"
```
Expected: 두 출력 모두 3개, 동일한 지표명 집합(`OBV_ROC`, `VOLUME`, `VOLUME_PCT`).

- [ ] **Step 3: 프론트엔드 개발 서버로 UI 확인**

Run: (`frontend/` 디렉터리에서) `npm run dev`

브라우저에서 백테스트 생성/편집 화면의 조건식 빌더를 열어:
1. "거래량" 카테고리에 `VOLUME`, `VOLUME_PCT`, `OBV_ROC` 3개가 보이는지 확인.
2. `VOLUME_PCT`/`OBV_ROC`를 선택했을 때 threshold 입력란 기본값이 `0`으로 채워지는지 확인. `VOLUME`은 (기존 `OBV`/`VOLUME_SMA`처럼) `0` placeholder인지 확인.
3. 지표 가이드 탭에서 신규 3개 항목이 각각 열리고 meaning/formula/thresholdExample/usage가 표시되는지 확인.
4. `OBV`/`VOLUME_SMA` 항목을 열어 usage 끝에 "OBV_ROC를 대신 쓰세요"/"VOLUME_PCT를 대신 쓰세요" 교차참조 문장이 보이는지 확인.

- [ ] **Step 4: 개발 서버 종료**

UI 확인이 끝나면 `npm run dev` 프로세스를 종료한다(Ctrl+C 또는 해당 터미널 종료).

이 태스크는 커밋할 코드 변경이 없다 — 검증만 수행한다.
