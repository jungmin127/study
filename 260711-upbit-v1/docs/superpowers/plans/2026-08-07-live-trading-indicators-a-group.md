# 라이브 트레이딩 서브플랜② — 지표 엔진 A그룹 (trading/live_indicators.py) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **워크트리를 만들지 말고 main 브랜치에서 직접 작업한다** (사용자 지시, [[upbit-v1-worktree-workflow-changed]]).

**Goal:** `trading/live_indicators.py`에 A그룹 지표 33개(대상 마켓 OHLCV만으로 계산되는
지표, 스펙 결정 2)를 pandas로 포팅하고, 각각이 `engine/indicators/*.py`(backtrader 기반
백테스트 엔진)와 같은 값을 내는지 골든테스트로 검증한다.

**Architecture:** `docs/superpowers/specs/2026-08-04-live-trading-foundation-design.md`의
1단계 로드맵 중 서브플랜①(`docs/superpowers/plans/2026-08-06-live-trading-engine-stage1.md`,
main에 병합됨) 다음 순서인 서브플랜②다. `trading/live_indicators.py`의 각 함수는
`engine/indicators/*.py`의 동명 함수와 1:1 대응하며, `bt.feeds.PandasData` 대신 OHLCV
컬럼(open/high/low/close/volume, 필요시 trade_value)을 가진 `pandas.DataFrame`을 받아
같은 이름의 `pandas.Series`(워밍업 구간 NaN)를 반환한다. 이 서브플랜은 지표 계산만
다룬다 — `engine/condition_tree.py`의 `eval_group_values()`(서브플랜①에서 이미 구현됨)와
결합해 실제 신호를 만드는 로직은 `trading/signal_engine.py`(서브플랜⑤)의 몫이다.

**Tech Stack:** Python, `pandas`, `numpy`, `pytest`. 새 의존성 없음(둘 다 이미
`requirements.txt`에 있음).

## Global Constraints

- `trading/` 패키지는 `engine/condition_tree.py` 외에는 `engine/`의 backtrader 관련
  코드를 import하지 않는다(스펙 결정 1) — 단, **테스트 코드**는 골든테스트를 위해
  `engine/indicators`와 `backtrader`를 import해도 된다(테스트는 이 제약의 대상이 아님,
  프로덕션 코드 `trading/live_indicators.py`만 해당).
- 이 서브플랜은 **A그룹(33개)만** 다룬다. B그룹 6개(`FUNDING_RATE`, `FEAR_GREED_CMC`,
  `KOREA_PREMIUM`, `MARKET_TREND`, `BTC_CORRELATION`, `USDT_CORRELATION`)는 다음 서브플랜
  ③에서 다룬다 — 이 플랜에서 손대지 않는다.
- 골든테스트는 `tests/signal_fixtures.py`의 `make_oscillating_df()`(기존 백테스트
  테스트들이 쓰는 합성 OHLCV 데이터, 이미 존재)를 그대로 재사용해 backtrader 쪽과 같은
  입력 데이터로 비교한다.
- 각 함수는 `engine/indicators/*.py`의 동명 함수와 **같은 파라미터 이름·기본값**을 쓴다
  (예: `SMA`는 `period=14`, `BB류`는 `period=20`·`devfactor` 고정 2.0 등) — 이래야
  `eval_group_values()`가 만드는 `indicator_key(name, params)`가 백테스트/라이브 양쪽에서
  같은 키로 매핑된다(스펙 결정 1의 핵심 전제).
- `trading/live_indicators.py`는 **하나의 파일**로 유지한다(스펙의 모듈구조 절이 이렇게
  명시함) — `engine/indicators/`처럼 카테고리별 서브모듈로 쪼개지 않는다.
- 커밋은 태스크 단위로 작게, 테스트가 통과한 뒤에만 한다.

---

## 골든테스트 방법론

`engine/indicators/*.py`의 각 지표는 이미 `bt.Indicator`로 정확한 수치가 검증된
백테스트 엔진의 일부다. 이 플랜의 목표는 **같은 입력에 같은 출력**을 내는 pandas
버전을 만드는 것이므로, "정답"을 미리 하드코딩하지 않고 **backtrader를 그 자리에서
실행해 나온 실제 값**과 비교한다(스펙 결정 1의 "후속 검증 필요" 항목 그대로).

이 방법론이므로 각 태스크의 pandas 코드가 100% 정확하지 않더라도(부동소수점 처리
디테일 등) 골든테스트 자체가 참值이라 안전하다 — 테스트가 실패하면 pandas 코드 쪽을
고쳐서 맞추면 된다(TDD의 "실패 확인 → 구현 → 통과 확인" 루프가 여기서는 값 일치를
위한 반복도 포함한다).

이 플랜의 모든 pandas 공식은 이미 이 세션에서 `make_oscillating_df()` 데이터로
backtrader와 직접 대조해 사전 검증했다(오차 1e-6 이내) — 그대로 구현하면 테스트가 바로
통과해야 하지만, 혹시 환경 차이로 안 맞으면 위 원칙대로 pandas 코드를 조정한다.

---

## File Structure

- **Create:** `trading/live_indicators.py` — A그룹 33개 지표 함수 + `LIVE_INDICATOR_FACTORY`
  레지스트리. Task 1에서 파일과 레지스트리를 만들고, Task 2~8이 함수를 추가해간다.
- **Create:** `tests/live_indicator_fixtures.py` — 골든테스트 공용 하네스
  (`run_backtrader_probe()`, `assert_matches_backtrader()`). Task 1에서 생성, 이후
  모든 태스크의 테스트가 여기서 import한다.
- **Test:** `tests/test_live_indicators_trend.py`, `tests/test_live_indicators_momentum.py`
  (2개 파일로 분리, 태스크 2·3), `tests/test_live_indicators_volatility.py`,
  `tests/test_live_indicators_volume.py`, `tests/test_live_indicators_price_levels.py`
  — 태스크마다 새 파일 또는 기존 파일에 추가.

---

### Task 1: 모듈 골격 + 골든테스트 하네스 + 추세(SMA/EMA/WMA)

**Files:**
- Create: `trading/live_indicators.py`
- Create: `tests/live_indicator_fixtures.py`
- Test: `tests/test_live_indicators_trend.py`

**Interfaces:**
- Consumes: `engine.indicators.INDICATOR_FACTORY`(기존), `engine.condition_tree.get_indicator_value`
  (기존, 테스트 하네스 전용).
- Produces: `trading.live_indicators.LIVE_INDICATOR_FACTORY: dict[str, Callable[[pd.DataFrame],
  pd.Series]]`(이후 태스크들이 계속 항목을 추가), `create_sma`/`create_ema`/`create_wma`.
  `tests.live_indicator_fixtures.run_backtrader_probe(indicator: str, params: dict) ->
  list[float]`와 `tests.live_indicator_fixtures.assert_matches_backtrader(indicator: str,
  params: dict, pandas_series: pd.Series, tol: float = 1e-6) -> None`(이후 모든 태스크의
  테스트가 이 두 함수를 재사용).

- [ ] **Step 1: 골든테스트 공용 하네스 작성**

`tests/live_indicator_fixtures.py`:
```python
"""라이브 지표(trading/live_indicators.py, pandas)와 백테스트 지표
(engine/indicators/*.py, backtrader)가 같은 값을 내는지 검증하는 골든테스트 공용 하네스."""
from __future__ import annotations

import backtrader as bt

from engine.condition_tree import get_indicator_value
from engine.indicators import INDICATOR_FACTORY
from tests.signal_fixtures import make_oscillating_df


def run_backtrader_probe(indicator: str, params: dict) -> list[float]:
    """make_oscillating_df() 전체 구간에 대해 backtrader로 indicator를 계산해, next()가
    호출된 매 봉의 값을 리스트로 반환한다(워밍업 구간은 backtrader의 minperiod 로직에
    따라 애초에 리스트에 포함되지 않는다). 라이브(pandas) 지표 함수의 골든테스트
    기준값으로 쓴다."""
    df = make_oscillating_df()
    df_bt = df.set_index("candle_time")
    df_bt.index = df_bt.index.tz_localize(None)

    class _Probe(bt.Strategy):
        def __init__(self) -> None:
            create_fn = INDICATOR_FACTORY[indicator]
            self.probe = create_fn(self.data, **params)
            self.seen_values: list[float] = []

        def next(self) -> None:
            self.seen_values.append(get_indicator_value(indicator, self.probe))

    cerebro = bt.Cerebro()
    cerebro.adddata(bt.feeds.PandasData(dataname=df_bt, openinterest=-1))
    cerebro.addstrategy(_Probe)
    results = cerebro.run()
    return results[0].seen_values


def assert_matches_backtrader(indicator: str, params: dict, pandas_series, tol: float = 1e-6) -> None:
    """pandas_series(라이브 지표 함수가 make_oscillating_df() 전체에 대해 계산한 결과)의
    마지막 값이, 같은 지표·같은 파라미터로 backtrader를 돌린 결과의 마지막 값과 tol 오차
    내로 일치하는지 검증한다. 라이브 엔진은 매 캔들 마감 시 항상 '지금까지의 마지막 값'만
    쓰므로, 이 골든테스트도 마지막 값 비교면 충분하다."""
    bt_values = run_backtrader_probe(indicator, params)
    pandas_last = pandas_series.iloc[-1]
    bt_last = bt_values[-1]
    assert abs(pandas_last - bt_last) < tol, (
        f"{indicator}({params}) 불일치: pandas={pandas_last!r} vs backtrader={bt_last!r} "
        f"(오차={abs(pandas_last - bt_last)!r})"
    )
```

- [ ] **Step 2: 실패하는 테스트 작성**

`tests/test_live_indicators_trend.py`:
```python
from tests.live_indicator_fixtures import assert_matches_backtrader
from tests.signal_fixtures import make_oscillating_df
from trading.live_indicators import LIVE_INDICATOR_FACTORY, create_ema, create_sma, create_wma


def test_sma_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("SMA", {"period": 14}, create_sma(df, period=14))


def test_ema_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("EMA", {"period": 14}, create_ema(df, period=14))


def test_wma_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("WMA", {"period": 14}, create_wma(df, period=14))


def test_sma_uses_default_period_14_when_omitted():
    df = make_oscillating_df()
    default = create_sma(df)
    explicit = create_sma(df, period=14)
    assert default.equals(explicit)


def test_sma_warmup_is_nan_before_period_bars():
    df = make_oscillating_df()
    result = create_sma(df, period=14)
    assert result.iloc[:13].isna().all()
    assert result.iloc[13:].notna().all()


def test_live_indicator_factory_registers_trend_indicators():
    assert LIVE_INDICATOR_FACTORY["SMA"] is create_sma
    assert LIVE_INDICATOR_FACTORY["EMA"] is create_ema
    assert LIVE_INDICATOR_FACTORY["WMA"] is create_wma
```

- [ ] **Step 3: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_live_indicators_trend.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'trading.live_indicators'`

- [ ] **Step 4: `trading/live_indicators.py` 구현**

```python
"""
trading/live_indicators.py

라이브 트레이딩용 지표 계산 — pandas 기반. engine/indicators/*.py(backtrader 기반,
백테스트 전용)와 값이 일치하도록 골든테스트로 검증한다(스펙 결정 1). A그룹(대상 마켓
OHLCV만으로 계산되는 지표 33개)만 다룬다 — B그룹(외부데이터·보조마켓 의존 6개)은 별도
서브플랜에서 추가한다(스펙 결정 2).

각 함수는 engine/indicators/*.py의 동명 함수와 1:1 대응하며, bt.feeds.PandasData 대신
OHLCV 컬럼(open/high/low/close/volume, 일부는 trade_value)을 가진 pandas.DataFrame을
받아 같은 이름의 pandas.Series(워밍업 구간 NaN)를 반환한다. LIVE_INDICATOR_FACTORY
레지스트리는 engine.indicators.INDICATOR_FACTORY와 같은 패턴이다.
"""
from __future__ import annotations

import statistics
from collections import deque

import numpy as np
import pandas as pd


def create_sma(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 14))
    return df["close"].rolling(period).mean()


def create_ema(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 14))
    return df["close"].ewm(span=period, adjust=False).mean()


def create_wma(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 14))
    weights = np.arange(1, period + 1)
    return df["close"].rolling(period).apply(
        lambda window: np.dot(window, weights) / weights.sum(), raw=True
    )


LIVE_INDICATOR_FACTORY: dict[str, object] = {
    "SMA": create_sma,
    "EMA": create_ema,
    "WMA": create_wma,
}

__all__ = ["LIVE_INDICATOR_FACTORY"]
```

- [ ] **Step 5: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_live_indicators_trend.py -v`
Expected: 6개 테스트 전부 PASS

- [ ] **Step 6: 커밋**

```bash
git add trading/live_indicators.py tests/live_indicator_fixtures.py tests/test_live_indicators_trend.py
git commit -m "feat: live_indicators 골든테스트 하네스 + 추세지표(SMA/EMA/WMA) pandas 포팅"
```

---

### Task 2: 모멘텀 그룹 파트1 — RSI, MACD류(4개)

**Files:**
- Modify: `trading/live_indicators.py`
- Test: `tests/test_live_indicators_momentum.py` (신규)

**Interfaces:**
- Consumes: Task 1의 `LIVE_INDICATOR_FACTORY` 딕셔너리, `tests.live_indicator_fixtures.assert_matches_backtrader`.
- Produces: `create_rsi`, `create_macd_line`, `create_macd_signal`, `create_macd_ppo`,
  `create_macd_ppo_signal` — `LIVE_INDICATOR_FACTORY`에 `"RSI"`, `"MACD_line"`,
  `"MACD_signal"`, `"MACD_PPO"`, `"MACD_PPO_signal"` 키로 등록됨.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_live_indicators_momentum.py`(신규 파일):
```python
from tests.live_indicator_fixtures import assert_matches_backtrader
from tests.signal_fixtures import make_oscillating_df
from trading.live_indicators import (
    LIVE_INDICATOR_FACTORY,
    create_macd_line,
    create_macd_ppo,
    create_macd_ppo_signal,
    create_macd_signal,
    create_rsi,
)


def test_rsi_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("RSI", {"period": 14}, create_rsi(df, period=14))


def test_macd_line_matches_backtrader():
    df = make_oscillating_df()
    params = {"fast": 12, "slow": 26, "signal": 9}
    assert_matches_backtrader("MACD_line", params, create_macd_line(df, **params))


def test_macd_signal_matches_backtrader():
    df = make_oscillating_df()
    params = {"fast": 12, "slow": 26, "signal": 9}
    assert_matches_backtrader("MACD_signal", params, create_macd_signal(df, **params))


def test_macd_ppo_matches_backtrader():
    df = make_oscillating_df()
    params = {"fast": 12, "slow": 26, "signal": 9}
    assert_matches_backtrader("MACD_PPO", params, create_macd_ppo(df, **params))


def test_macd_ppo_signal_matches_backtrader():
    df = make_oscillating_df()
    params = {"fast": 12, "slow": 26, "signal": 9}
    assert_matches_backtrader("MACD_PPO_signal", params, create_macd_ppo_signal(df, **params))


def test_macd_ppo_param_mapping_actually_changes_output():
    df = make_oscillating_df()
    default = create_macd_ppo(df, fast=12, slow=26, signal=9)
    different = create_macd_ppo(df, fast=5, slow=10, signal=3)
    assert default.iloc[-1] != different.iloc[-1]


def test_live_indicator_factory_registers_momentum_part1():
    assert LIVE_INDICATOR_FACTORY["RSI"] is create_rsi
    assert LIVE_INDICATOR_FACTORY["MACD_line"] is create_macd_line
    assert LIVE_INDICATOR_FACTORY["MACD_signal"] is create_macd_signal
    assert LIVE_INDICATOR_FACTORY["MACD_PPO"] is create_macd_ppo
    assert LIVE_INDICATOR_FACTORY["MACD_PPO_signal"] is create_macd_ppo_signal
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_live_indicators_momentum.py -v`
Expected: FAIL — `ImportError: cannot import name 'create_rsi'`

- [ ] **Step 3: `trading/live_indicators.py`에 함수 추가**

`LIVE_INDICATOR_FACTORY: dict[str, object] = {` 줄 바로 위에 추가:
```python
def create_rsi(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 14))
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def create_macd_line(df: pd.DataFrame, **params) -> pd.Series:
    fast = int(params.get("fast", 12))
    slow = int(params.get("slow", 26))
    ema_fast = df["close"].ewm(span=fast, adjust=False, min_periods=fast).mean()
    ema_slow = df["close"].ewm(span=slow, adjust=False, min_periods=slow).mean()
    return ema_fast - ema_slow


def create_macd_signal(df: pd.DataFrame, **params) -> pd.Series:
    signal = int(params.get("signal", 9))
    macd_line = create_macd_line(df, **params)
    return macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()


def create_macd_ppo(df: pd.DataFrame, **params) -> pd.Series:
    fast = int(params.get("fast", 12))
    slow = int(params.get("slow", 26))
    ema_fast = df["close"].ewm(span=fast, adjust=False, min_periods=fast).mean()
    ema_slow = df["close"].ewm(span=slow, adjust=False, min_periods=slow).mean()
    return (ema_fast - ema_slow) / ema_slow * 100


def create_macd_ppo_signal(df: pd.DataFrame, **params) -> pd.Series:
    signal = int(params.get("signal", 9))
    ppo = create_macd_ppo(df, **params)
    return ppo.ewm(span=signal, adjust=False, min_periods=signal).mean()


```

`LIVE_INDICATOR_FACTORY` 딕셔너리 리터럴을 다음으로 교체(기존 3개 항목 + 신규 5개):
```python
LIVE_INDICATOR_FACTORY: dict[str, object] = {
    "SMA": create_sma,
    "EMA": create_ema,
    "WMA": create_wma,
    "RSI": create_rsi,
    "MACD_line": create_macd_line,
    "MACD_signal": create_macd_signal,
    "MACD_PPO": create_macd_ppo,
    "MACD_PPO_signal": create_macd_ppo_signal,
}
```

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_live_indicators_momentum.py tests/test_live_indicators_trend.py -v`
Expected: 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add trading/live_indicators.py tests/test_live_indicators_momentum.py
git commit -m "feat: live_indicators에 RSI/MACD류 pandas 포팅 추가"
```

---

### Task 3: 모멘텀 그룹 파트2 — STOCH_K/D, CCI, WILLIAMS_R, MOMENTUM_PCT(5개)

**Files:**
- Modify: `trading/live_indicators.py`
- Modify: `tests/test_live_indicators_momentum.py`

**Interfaces:**
- Consumes: Task 1·2의 `LIVE_INDICATOR_FACTORY`, `assert_matches_backtrader`.
- Produces: `create_stoch_k`, `create_stoch_d`, `create_cci`, `create_williams_r`,
  `create_momentum_pct` — `"STOCH_K"`, `"STOCH_D"`, `"CCI"`, `"WILLIAMS_R"`,
  `"MOMENTUM_PCT"` 키로 등록됨.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_live_indicators_momentum.py` 파일 끝에 추가:
```python
from trading.live_indicators import (
    create_cci,
    create_momentum_pct,
    create_stoch_d,
    create_stoch_k,
    create_williams_r,
)


def test_stoch_k_matches_backtrader():
    df = make_oscillating_df()
    params = {"k_period": 14, "d_period": 3}
    assert_matches_backtrader("STOCH_K", params, create_stoch_k(df, **params))


def test_stoch_d_matches_backtrader():
    df = make_oscillating_df()
    params = {"k_period": 14, "d_period": 3}
    assert_matches_backtrader("STOCH_D", params, create_stoch_d(df, **params))


def test_cci_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("CCI", {"period": 20}, create_cci(df, period=20))


def test_williams_r_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("WILLIAMS_R", {"period": 14}, create_williams_r(df, period=14))


def test_momentum_pct_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("MOMENTUM_PCT", {"period": 5}, create_momentum_pct(df, period=5))


def test_live_indicator_factory_registers_momentum_part2():
    from trading.live_indicators import LIVE_INDICATOR_FACTORY
    assert LIVE_INDICATOR_FACTORY["STOCH_K"] is create_stoch_k
    assert LIVE_INDICATOR_FACTORY["STOCH_D"] is create_stoch_d
    assert LIVE_INDICATOR_FACTORY["CCI"] is create_cci
    assert LIVE_INDICATOR_FACTORY["WILLIAMS_R"] is create_williams_r
    assert LIVE_INDICATOR_FACTORY["MOMENTUM_PCT"] is create_momentum_pct
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_live_indicators_momentum.py -v`
Expected: FAIL — `ImportError: cannot import name 'create_stoch_k'`

- [ ] **Step 3: `trading/live_indicators.py`에 함수 추가**

`LIVE_INDICATOR_FACTORY: dict[str, object] = {` 줄 바로 위에 추가:
```python
def create_stoch_k(df: pd.DataFrame, **params) -> pd.Series:
    k_period = int(params.get("k_period", 14))
    d_period = int(params.get("d_period", 3))
    lowest_low = df["low"].rolling(k_period).min()
    highest_high = df["high"].rolling(k_period).max()
    fast_k = 100 * (df["close"] - lowest_low) / (highest_high - lowest_low)
    # backtrader의 Stochastic(StochasticFast가 아님)은 %K 자체가 이미 period_dfast로
    # 스무딩된 값을 노출한다 — fast_k를 그대로 쓰면 안 됨.
    return fast_k.rolling(d_period).mean()


def create_stoch_d(df: pd.DataFrame, **params) -> pd.Series:
    slow_k = create_stoch_k(df, **params)
    # period_dslow는 backtrader 기본값 3으로 고정(이 프로젝트의 STOCH 팩토리가
    # 파라미터화하지 않음, engine/indicators/momentum.py 참고).
    return slow_k.rolling(3).mean()


def create_cci(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 20))
    tp = (df["high"] + df["low"] + df["close"]) / 3
    tp_mean = tp.rolling(period).mean()
    # backtrader의 MeanDev는 각 시점의 |tp-tpmean|을 먼저 전체 시계열로 만든 뒤 그
    # 절대편차 시계열을 다시 이동평균한다 — 각 윈도우 내부에서 자기 평균을 새로 구해
    # 편차를 재는 것과 다르다(둘은 값이 다르다, 반드시 이 순서를 지킬 것).
    mean_dev = (tp - tp_mean).abs().rolling(period).mean()
    return (tp - tp_mean) / (0.015 * mean_dev)


def create_williams_r(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 14))
    hh = df["high"].rolling(period).max()
    ll = df["low"].rolling(period).min()
    return -100 * (hh - df["close"]) / (hh - ll)


def create_momentum_pct(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 5))
    close = df["close"]
    return (close - close.shift(period)) / close.shift(period) * 100


```

`LIVE_INDICATOR_FACTORY` 딕셔너리 리터럴을 다음으로 교체:
```python
LIVE_INDICATOR_FACTORY: dict[str, object] = {
    "SMA": create_sma,
    "EMA": create_ema,
    "WMA": create_wma,
    "RSI": create_rsi,
    "MACD_line": create_macd_line,
    "MACD_signal": create_macd_signal,
    "MACD_PPO": create_macd_ppo,
    "MACD_PPO_signal": create_macd_ppo_signal,
    "STOCH_K": create_stoch_k,
    "STOCH_D": create_stoch_d,
    "CCI": create_cci,
    "WILLIAMS_R": create_williams_r,
    "MOMENTUM_PCT": create_momentum_pct,
}
```

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_live_indicators_momentum.py tests/test_live_indicators_trend.py -v`
Expected: 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add trading/live_indicators.py tests/test_live_indicators_momentum.py
git commit -m "feat: live_indicators에 STOCH/CCI/WILLIAMS_R/MOMENTUM_PCT pandas 포팅 추가"
```

---

### Task 4: 변동성 그룹 — ATR, ATR_PCT, BB류(6개)

**Files:**
- Modify: `trading/live_indicators.py`
- Test: `tests/test_live_indicators_volatility.py` (신규)

**Interfaces:**
- Consumes: Task 1의 `LIVE_INDICATOR_FACTORY`, `assert_matches_backtrader`.
- Produces: `create_atr`, `create_atr_pct`, `create_bb_middle`, `create_bb_upper`,
  `create_bb_lower`, `create_bb_percent_b` — `"ATR"`, `"ATR_PCT"`, `"BB_upper"`,
  `"BB_lower"`, `"BB_middle"`, `"BB_PERCENT_B"` 키로 등록됨.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_live_indicators_volatility.py`(신규 파일):
```python
from tests.live_indicator_fixtures import assert_matches_backtrader
from tests.signal_fixtures import make_oscillating_df
from trading.live_indicators import (
    LIVE_INDICATOR_FACTORY,
    create_atr,
    create_atr_pct,
    create_bb_lower,
    create_bb_middle,
    create_bb_percent_b,
    create_bb_upper,
)


def test_atr_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("ATR", {"period": 14}, create_atr(df, period=14))


def test_atr_pct_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("ATR_PCT", {"period": 14}, create_atr_pct(df, period=14))


def test_bb_upper_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("BB_upper", {"period": 20}, create_bb_upper(df, period=20))


def test_bb_lower_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("BB_lower", {"period": 20}, create_bb_lower(df, period=20))


def test_bb_middle_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("BB_middle", {"period": 20}, create_bb_middle(df, period=20))


def test_bb_percent_b_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("BB_PERCENT_B", {"period": 20}, create_bb_percent_b(df, period=20))


def test_live_indicator_factory_registers_volatility():
    assert LIVE_INDICATOR_FACTORY["ATR"] is create_atr
    assert LIVE_INDICATOR_FACTORY["ATR_PCT"] is create_atr_pct
    assert LIVE_INDICATOR_FACTORY["BB_upper"] is create_bb_upper
    assert LIVE_INDICATOR_FACTORY["BB_lower"] is create_bb_lower
    assert LIVE_INDICATOR_FACTORY["BB_middle"] is create_bb_middle
    assert LIVE_INDICATOR_FACTORY["BB_PERCENT_B"] is create_bb_percent_b
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_live_indicators_volatility.py -v`
Expected: FAIL — `ImportError: cannot import name 'create_atr'`

- [ ] **Step 3: `trading/live_indicators.py`에 함수 추가**

`LIVE_INDICATOR_FACTORY: dict[str, object] = {` 줄 바로 위에 추가:
```python
def create_atr(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 14))
    prev_close = df["close"].shift(1)
    true_range = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def create_atr_pct(df: pd.DataFrame, **params) -> pd.Series:
    atr = create_atr(df, **params)
    return atr / df["close"] * 100


def create_bb_middle(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 20))
    return df["close"].rolling(period).mean()


def create_bb_upper(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 20))
    mid = create_bb_middle(df, **params)
    std = df["close"].rolling(period).std(ddof=0)
    return mid + 2 * std


def create_bb_lower(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 20))
    mid = create_bb_middle(df, **params)
    std = df["close"].rolling(period).std(ddof=0)
    return mid - 2 * std


def create_bb_percent_b(df: pd.DataFrame, **params) -> pd.Series:
    upper = create_bb_upper(df, **params)
    lower = create_bb_lower(df, **params)
    return (df["close"] - lower) / (upper - lower)


```

`LIVE_INDICATOR_FACTORY` 딕셔너리 리터럴을 다음으로 교체:
```python
LIVE_INDICATOR_FACTORY: dict[str, object] = {
    "SMA": create_sma,
    "EMA": create_ema,
    "WMA": create_wma,
    "RSI": create_rsi,
    "MACD_line": create_macd_line,
    "MACD_signal": create_macd_signal,
    "MACD_PPO": create_macd_ppo,
    "MACD_PPO_signal": create_macd_ppo_signal,
    "STOCH_K": create_stoch_k,
    "STOCH_D": create_stoch_d,
    "CCI": create_cci,
    "WILLIAMS_R": create_williams_r,
    "MOMENTUM_PCT": create_momentum_pct,
    "ATR": create_atr,
    "ATR_PCT": create_atr_pct,
    "BB_upper": create_bb_upper,
    "BB_lower": create_bb_lower,
    "BB_middle": create_bb_middle,
    "BB_PERCENT_B": create_bb_percent_b,
}
```

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_live_indicators_volatility.py -v`
Expected: 7개 테스트 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add trading/live_indicators.py tests/test_live_indicators_volatility.py
git commit -m "feat: live_indicators에 ATR/BB류 pandas 포팅 추가"
```

---

### Task 5: 거래량 그룹 파트1 — OBV, VOLUME_SMA, TRADE_VALUE류(4개)

**Files:**
- Modify: `trading/live_indicators.py`
- Test: `tests/test_live_indicators_volume.py` (신규)

**Interfaces:**
- Consumes: Task 1의 `LIVE_INDICATOR_FACTORY`, `assert_matches_backtrader`.
- Produces: `create_obv`, `create_volume_sma`, `create_trade_value`, `create_trade_value_sma`
  — `"OBV"`, `"VOLUME_SMA"`, `"TRADE_VALUE"`, `"TRADE_VALUE_SMA"` 키로 등록됨.
  `create_trade_value`/`create_trade_value_sma`는 `df["trade_value"]` 컬럼이 이미 있다고
  가정한다(업비트 캔들의 `candle_acc_trade_price`를 그대로 담는 컬럼 — 이 컬럼을 실제로
  채우는 건 Upbit 연동 서브플랜④의 몫이며, 이 태스크는 컬럼이 있다고 가정하고 값을
  꺼내기만 한다).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_live_indicators_volume.py`(신규 파일):
```python
from tests.live_indicator_fixtures import assert_matches_backtrader, run_backtrader_probe
from tests.signal_fixtures import make_oscillating_df
from trading.live_indicators import (
    LIVE_INDICATOR_FACTORY,
    create_obv,
    create_trade_value,
    create_trade_value_sma,
    create_volume_sma,
)


def test_obv_matches_backtrader():
    df = make_oscillating_df()
    # OBV는 backtrader의 minperiod=2 때문에 next() 첫 값이 bar1부터 시작한다(bar0은 bt가
    # 아예 안 냄). assert_matches_backtrader는 마지막 값만 비교하므로 이 offset과 무관하게
    # 그대로 재사용 가능하다.
    assert_matches_backtrader("OBV", {}, create_obv(df))


def test_volume_sma_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("VOLUME_SMA", {"period": 20}, create_volume_sma(df, period=20))


def test_trade_value_matches_raw_trade_value_column():
    df = make_oscillating_df()
    df["trade_value"] = df["close"] * df["volume"]
    result = create_trade_value(df)
    assert abs(result.iloc[-1] - df["trade_value"].iloc[-1]) < 1e-6


def test_trade_value_sma_matches_manual_rolling_mean():
    df = make_oscillating_df()
    df["trade_value"] = df["close"] * df["volume"]
    result = create_trade_value_sma(df, period=5)
    manual = df["trade_value"].rolling(5).mean()
    assert abs(result.iloc[-1] - manual.iloc[-1]) < 1e-6


def test_live_indicator_factory_registers_volume_part1():
    assert LIVE_INDICATOR_FACTORY["OBV"] is create_obv
    assert LIVE_INDICATOR_FACTORY["VOLUME_SMA"] is create_volume_sma
    assert LIVE_INDICATOR_FACTORY["TRADE_VALUE"] is create_trade_value
    assert LIVE_INDICATOR_FACTORY["TRADE_VALUE_SMA"] is create_trade_value_sma
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_live_indicators_volume.py -v`
Expected: FAIL — `ImportError: cannot import name 'create_obv'`

- [ ] **Step 3: `trading/live_indicators.py`에 함수 추가**

`LIVE_INDICATOR_FACTORY: dict[str, object] = {` 줄 바로 위에 추가:
```python
def create_obv(df: pd.DataFrame, **params) -> pd.Series:
    direction = np.sign(df["close"].diff())
    return (direction * df["volume"]).fillna(0).cumsum()


def create_volume_sma(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 20))
    return df["volume"].rolling(period).mean()


def create_trade_value(df: pd.DataFrame, **params) -> pd.Series:
    return df["trade_value"]


def create_trade_value_sma(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 20))
    return df["trade_value"].rolling(period).mean()


```

`LIVE_INDICATOR_FACTORY` 딕셔너리 리터럴을 다음으로 교체:
```python
LIVE_INDICATOR_FACTORY: dict[str, object] = {
    "SMA": create_sma,
    "EMA": create_ema,
    "WMA": create_wma,
    "RSI": create_rsi,
    "MACD_line": create_macd_line,
    "MACD_signal": create_macd_signal,
    "MACD_PPO": create_macd_ppo,
    "MACD_PPO_signal": create_macd_ppo_signal,
    "STOCH_K": create_stoch_k,
    "STOCH_D": create_stoch_d,
    "CCI": create_cci,
    "WILLIAMS_R": create_williams_r,
    "MOMENTUM_PCT": create_momentum_pct,
    "ATR": create_atr,
    "ATR_PCT": create_atr_pct,
    "BB_upper": create_bb_upper,
    "BB_lower": create_bb_lower,
    "BB_middle": create_bb_middle,
    "BB_PERCENT_B": create_bb_percent_b,
    "OBV": create_obv,
    "VOLUME_SMA": create_volume_sma,
    "TRADE_VALUE": create_trade_value,
    "TRADE_VALUE_SMA": create_trade_value_sma,
}
```

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_live_indicators_volume.py -v`
Expected: 5개 테스트 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add trading/live_indicators.py tests/test_live_indicators_volume.py
git commit -m "feat: live_indicators에 OBV/VOLUME_SMA/TRADE_VALUE류 pandas 포팅 추가"
```

---

### Task 6: 거래량 그룹 파트2 — VPIN(1개, 상태 기반 버킷 알고리즘)

**Files:**
- Modify: `trading/live_indicators.py`
- Modify: `tests/test_live_indicators_volume.py`

**Interfaces:**
- Consumes: Task 1·5의 `LIVE_INDICATOR_FACTORY`, `assert_matches_backtrader`. 이미 파일
  상단에 있는 `import statistics`/`from collections import deque`(Task 1에서 추가됨).
- Produces: `create_vpin` — `"VPIN"` 키로 등록됨.

VPIN은 롤링 함수 하나로 표현할 수 없는 상태 기반 알고리즘(거래량 버킷을 순차적으로
누적·완성시키는 루프)이다. `engine/indicators/volume.py`의 `VolumeBarVPIN` 클래스
로직을 backtrader 없이 순수 파이썬 루프로 그대로 옮긴 것 — 근사가 아니라 같은 알고리즘의
번역이다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_live_indicators_volume.py` 파일 끝에 추가:
```python
from trading.live_indicators import create_vpin


def test_vpin_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("VPIN", {"period": 20}, create_vpin(df, period=20))


def test_vpin_matches_hand_traced_bucket_sequence():
    # engine/indicators/volume.py의 VolumeBarVPIN을 검증한 것과 동일한 손 계산 시퀀스
    # (tests/test_indicators.py::test_vpin_matches_hand_traced_bucket_sequence 참고).
    import pandas as pd
    import statistics

    volumes = [10, 10, 2, 2, 2, 1, 1, 10]
    closes = [100, 100, 100, 100, 100, 100, 100, 105]
    idx = pd.date_range("2026-01-01", periods=8, freq="h", tz="UTC")
    df = pd.DataFrame({
        "candle_time": idx, "open": closes, "high": closes, "low": closes,
        "close": closes, "volume": volumes,
    })
    values = create_vpin(df, period=2)

    assert values.iloc[:4].isna().all()
    assert values.iloc[4] == pytest.approx(0.0)
    assert values.iloc[5] == values.iloc[4]
    assert values.iloc[6] == pytest.approx(0.0)

    sigma = statistics.stdev([0.0, 5.0])
    z = 5.0 / sigma
    buy_ratio = statistics.NormalDist().cdf(z)
    imbalance_bucket_8 = abs(2 * buy_ratio - 1)
    expected_bar8 = imbalance_bucket_8 / 2
    assert values.iloc[7] == pytest.approx(expected_bar8)


def test_live_indicator_factory_registers_vpin():
    assert LIVE_INDICATOR_FACTORY["VPIN"] is create_vpin
```

이 파일 맨 위 import 목록에 `import pytest`를 추가한다(기존 파일엔 없었음).

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_live_indicators_volume.py -v`
Expected: FAIL — `ImportError: cannot import name 'create_vpin'`

- [ ] **Step 3: `trading/live_indicators.py`에 함수 추가**

`LIVE_INDICATOR_FACTORY: dict[str, object] = {` 줄 바로 위에 추가:
```python
def create_vpin(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 20))
    closes = df["close"].tolist()
    volumes = df["volume"].tolist()

    recent_volumes: deque = deque(maxlen=period)
    bucket_cum_volume = 0.0
    last_bucket_close: float | None = None
    bucket_deltas: deque = deque(maxlen=period)
    bucket_imbalance_ratios: deque = deque(maxlen=period)

    out: list[float] = []
    prev_vpin = float("nan")
    for close, volume in zip(closes, volumes):
        recent_volumes.append(volume)
        bucket_cum_volume += volume
        target = statistics.mean(recent_volumes) if len(recent_volumes) == period else None
        if target is not None and bucket_cum_volume >= target:
            bucket_close = close
            bucket_volume = bucket_cum_volume
            if last_bucket_close is not None:
                delta = bucket_close - last_bucket_close
                bucket_deltas.append(delta)
                sigma = statistics.stdev(bucket_deltas) if len(bucket_deltas) >= 2 else 0.0
                z = delta / sigma if sigma > 0 else 0.0
                buy_ratio = statistics.NormalDist().cdf(z)
                buy_volume = bucket_volume * buy_ratio
                sell_volume = bucket_volume - buy_volume
                imbalance_ratio = abs(buy_volume - sell_volume) / bucket_volume if bucket_volume else 0.0
                bucket_imbalance_ratios.append(imbalance_ratio)
            last_bucket_close = bucket_close
            bucket_cum_volume = 0.0
        val = statistics.mean(bucket_imbalance_ratios) if len(bucket_imbalance_ratios) == period else prev_vpin
        out.append(val)
        prev_vpin = val
    return pd.Series(out, index=df.index)


```

`LIVE_INDICATOR_FACTORY` 딕셔너리 리터럴에 `"VPIN": create_vpin,`을 마지막 항목으로
추가한다(딕셔너리 전체를 다시 쓸 필요 없이, 닫는 중괄호 `}` 바로 앞 줄에 추가).

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_live_indicators_volume.py -v`
Expected: 8개 테스트 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add trading/live_indicators.py tests/test_live_indicators_volume.py
git commit -m "feat: live_indicators에 VPIN pandas 포팅 추가"
```

---

### Task 7: 가격대 그룹 파트1 — FIB류, PIVOT류(6개)

**Files:**
- Modify: `trading/live_indicators.py`
- Test: `tests/test_live_indicators_price_levels.py` (신규)

**Interfaces:**
- Consumes: Task 1의 `LIVE_INDICATOR_FACTORY`, `assert_matches_backtrader`.
- Produces: `create_fib_382`, `create_fib_500`, `create_fib_618`, `create_pivot_p`,
  `create_pivot_r1`, `create_pivot_s1` — `"FIB_382"`, `"FIB_500"`, `"FIB_618"`,
  `"PIVOT_P"`, `"PIVOT_R1"`, `"PIVOT_S1"` 키로 등록됨.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_live_indicators_price_levels.py`(신규 파일):
```python
from tests.live_indicator_fixtures import assert_matches_backtrader
from tests.signal_fixtures import make_oscillating_df
from trading.live_indicators import (
    LIVE_INDICATOR_FACTORY,
    create_fib_382,
    create_fib_500,
    create_fib_618,
    create_pivot_p,
    create_pivot_r1,
    create_pivot_s1,
)


def test_fib_382_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("FIB_382", {"period": 20}, create_fib_382(df, period=20))


def test_fib_500_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("FIB_500", {"period": 20}, create_fib_500(df, period=20))


def test_fib_618_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("FIB_618", {"period": 20}, create_fib_618(df, period=20))


def test_pivot_p_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("PIVOT_P", {}, create_pivot_p(df))


def test_pivot_r1_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("PIVOT_R1", {}, create_pivot_r1(df))


def test_pivot_s1_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("PIVOT_S1", {}, create_pivot_s1(df))


def test_live_indicator_factory_registers_price_levels_part1():
    assert LIVE_INDICATOR_FACTORY["FIB_382"] is create_fib_382
    assert LIVE_INDICATOR_FACTORY["FIB_500"] is create_fib_500
    assert LIVE_INDICATOR_FACTORY["FIB_618"] is create_fib_618
    assert LIVE_INDICATOR_FACTORY["PIVOT_P"] is create_pivot_p
    assert LIVE_INDICATOR_FACTORY["PIVOT_R1"] is create_pivot_r1
    assert LIVE_INDICATOR_FACTORY["PIVOT_S1"] is create_pivot_s1
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_live_indicators_price_levels.py -v`
Expected: FAIL — `ImportError: cannot import name 'create_fib_382'`

- [ ] **Step 3: `trading/live_indicators.py`에 함수 추가**

`LIVE_INDICATOR_FACTORY: dict[str, object] = {` 줄 바로 위에 추가:
```python
def create_fib_382(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 20))
    hh = df["high"].rolling(period).max()
    ll = df["low"].rolling(period).min()
    return hh - (hh - ll) * 0.382


def create_fib_500(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 20))
    hh = df["high"].rolling(period).max()
    ll = df["low"].rolling(period).min()
    return hh - (hh - ll) * 0.5


def create_fib_618(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 20))
    hh = df["high"].rolling(period).max()
    ll = df["low"].rolling(period).min()
    return hh - (hh - ll) * 0.618


def create_pivot_p(df: pd.DataFrame, **params) -> pd.Series:
    prev_high = df["high"].shift(1)
    prev_low = df["low"].shift(1)
    prev_close = df["close"].shift(1)
    return (prev_high + prev_low + prev_close) / 3.0


def create_pivot_r1(df: pd.DataFrame, **params) -> pd.Series:
    pivot = create_pivot_p(df, **params)
    prev_low = df["low"].shift(1)
    return pivot * 2 - prev_low


def create_pivot_s1(df: pd.DataFrame, **params) -> pd.Series:
    pivot = create_pivot_p(df, **params)
    prev_high = df["high"].shift(1)
    return pivot * 2 - prev_high


```

`LIVE_INDICATOR_FACTORY` 딕셔너리 리터럴에 다음 6개를 마지막 항목들로 추가:
```python
    "FIB_382": create_fib_382,
    "FIB_500": create_fib_500,
    "FIB_618": create_fib_618,
    "PIVOT_P": create_pivot_p,
    "PIVOT_R1": create_pivot_r1,
    "PIVOT_S1": create_pivot_s1,
```

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_live_indicators_price_levels.py -v`
Expected: 7개 테스트 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add trading/live_indicators.py tests/test_live_indicators_price_levels.py
git commit -m "feat: live_indicators에 FIB류/PIVOT류 pandas 포팅 추가"
```

---

### Task 8: 가격대 그룹 파트2 — VPVR_POC/VAH/VAL(3개, Volume Profile)

**Files:**
- Modify: `trading/live_indicators.py`
- Modify: `tests/test_live_indicators_price_levels.py`

**Interfaces:**
- Consumes: Task 1·7의 `LIVE_INDICATOR_FACTORY`, `assert_matches_backtrader`. 이미 파일
  상단에 있는 `from collections import deque`(Task 1에서 추가됨).
- Produces: `create_vpvr_poc`, `create_vpvr_vah`, `create_vpvr_val`(내부적으로
  `_volume_profile()` 헬퍼 공유) — `"VPVR_POC"`, `"VPVR_VAH"`, `"VPVR_VAL"` 키로 등록됨.

`engine/indicators/price_levels.py`의 `VolumeProfile`(backtrader `bt.Indicator`) 클래스
로직을 순수 파이썬 루프로 옮긴 것이다 — 알고리즘은 동일하고, 상태를 라인 버퍼 대신
파이썬 리스트/deque로 관리한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_live_indicators_price_levels.py` 파일 끝에 추가:
```python
from trading.live_indicators import create_vpvr_poc, create_vpvr_vah, create_vpvr_val


def test_vpvr_poc_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("VPVR_POC", {"period": 50}, create_vpvr_poc(df, period=50))


def test_vpvr_vah_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("VPVR_VAH", {"period": 50}, create_vpvr_vah(df, period=50))


def test_vpvr_val_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("VPVR_VAL", {"period": 50}, create_vpvr_val(df, period=50))


def test_vpvr_matches_hand_traced_bin_distribution():
    # engine/indicators/price_levels.py의 VolumeProfile을 검증한 것과 동일한 손 계산
    # 시퀀스(tests/test_indicators.py::test_vpvr_matches_hand_traced_bin_distribution
    # 참고). NUM_BINS를 4로 좁혀서 손 계산 가능하게 만든다.
    import pandas as pd

    import trading.live_indicators as live_indicators

    highs = [2.5, 10.0, 5.0]
    lows = [0.0, 7.5, 2.5]
    volumes = [100, 10, 5]
    closes = [(h + l) / 2 for h, l in zip(highs, lows)]
    idx = pd.date_range("2026-01-01", periods=3, freq="h", tz="UTC")
    df = pd.DataFrame({
        "candle_time": idx, "open": closes, "high": highs, "low": lows,
        "close": closes, "volume": volumes,
    })

    original_num_bins = live_indicators.NUM_BINS
    live_indicators.NUM_BINS = 4
    try:
        poc = live_indicators.create_vpvr_poc(df, period=3)
        vah = live_indicators.create_vpvr_vah(df, period=3)
        val = live_indicators.create_vpvr_val(df, period=3)
    finally:
        live_indicators.NUM_BINS = original_num_bins

    assert poc.iloc[-1] == pytest.approx(1.25)
    assert vah.iloc[-1] == pytest.approx(2.5)
    assert val.iloc[-1] == pytest.approx(0.0)


def test_vpvr_handles_completely_flat_window_without_dividing_by_zero():
    highs = [100.0, 100.0, 100.0]
    lows = [100.0, 100.0, 100.0]
    volumes = [10, 10, 10]
    idx = pd.date_range("2026-01-01", periods=3, freq="h", tz="UTC")
    df = pd.DataFrame({
        "candle_time": idx, "open": highs, "high": highs, "low": lows,
        "close": highs, "volume": volumes,
    })
    poc = create_vpvr_poc(df, period=3)
    vah = create_vpvr_vah(df, period=3)
    val = create_vpvr_val(df, period=3)
    assert poc.iloc[-1] == pytest.approx(100.0)
    assert vah.iloc[-1] == pytest.approx(100.0)
    assert val.iloc[-1] == pytest.approx(100.0)


def test_live_indicator_factory_registers_vpvr():
    assert LIVE_INDICATOR_FACTORY["VPVR_POC"] is create_vpvr_poc
    assert LIVE_INDICATOR_FACTORY["VPVR_VAH"] is create_vpvr_vah
    assert LIVE_INDICATOR_FACTORY["VPVR_VAL"] is create_vpvr_val
```

이 파일 맨 위 import 목록에 `import pandas as pd`와 `import pytest`를 추가한다(아직
없었다면).

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_live_indicators_price_levels.py -v`
Expected: FAIL — `ImportError: cannot import name 'create_vpvr_poc'`

- [ ] **Step 3: `trading/live_indicators.py`에 함수 추가**

`LIVE_INDICATOR_FACTORY: dict[str, object] = {` 줄 바로 위에 추가:
```python
NUM_BINS = 24
VALUE_AREA_PCT = 0.7


def _volume_profile(df: pd.DataFrame, period: int) -> tuple[pd.Series, pd.Series, pd.Series]:
    """VPVR_POC/VAH/VAL 3개가 공유하는 계산. engine/indicators/price_levels.py의
    VolumeProfile(bt.Indicator)과 같은 알고리즘을 순수 파이썬 루프로 옮긴 것이다.
    backtrader 쪽도 POC/VAH/VAL 요청마다 VolumeProfile 인스턴스를 따로 만들어 3번
    재계산하므로(engine/indicators/price_levels.py의 create_vpvr_* 참고), 여기서도 매
    호출마다 재계산하는 게 backtrader와 일관된 동작이다."""
    highs = df["high"].tolist()
    lows = df["low"].tolist()
    volumes = df["volume"].tolist()
    n = len(highs)
    poc_out: list[float] = [float("nan")] * n
    vah_out: list[float] = [float("nan")] * n
    val_out: list[float] = [float("nan")] * n

    hi_win: deque = deque(maxlen=period)
    lo_win: deque = deque(maxlen=period)
    vol_win: deque = deque(maxlen=period)

    for i in range(n):
        hi_win.append(highs[i])
        lo_win.append(lows[i])
        vol_win.append(volumes[i])
        if len(hi_win) < period:
            continue

        window_high = max(hi_win)
        window_low = min(lo_win)
        if window_high == window_low:
            poc_out[i] = vah_out[i] = val_out[i] = window_high
            continue

        bin_width = (window_high - window_low) / NUM_BINS
        bin_volumes = [0.0] * NUM_BINS
        for h, l, v in zip(hi_win, lo_win, vol_win):
            if h == l:
                idx = min(int((h - window_low) / bin_width), NUM_BINS - 1)
                bin_volumes[idx] += v
                continue
            for b in range(NUM_BINS):
                bin_bottom = window_low + b * bin_width
                bin_top = bin_bottom + bin_width
                overlap = min(h, bin_top) - max(l, bin_bottom)
                if overlap > 0:
                    bin_volumes[b] += v * (overlap / (h - l))

        total_volume = sum(bin_volumes)
        poc_idx = max(range(NUM_BINS), key=lambda k: bin_volumes[k])
        poc_price = window_low + (poc_idx + 0.5) * bin_width

        lo, hi = poc_idx, poc_idx
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

        poc_out[i] = poc_price
        vah_out[i] = window_low + (hi + 1) * bin_width
        val_out[i] = window_low + lo * bin_width

    idx = df.index
    return pd.Series(poc_out, index=idx), pd.Series(vah_out, index=idx), pd.Series(val_out, index=idx)


def create_vpvr_poc(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 50))
    return _volume_profile(df, period)[0]


def create_vpvr_vah(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 50))
    return _volume_profile(df, period)[1]


def create_vpvr_val(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 50))
    return _volume_profile(df, period)[2]


```

`LIVE_INDICATOR_FACTORY` 딕셔너리 리터럴에 다음 3개를 마지막 항목들로 추가:
```python
    "VPVR_POC": create_vpvr_poc,
    "VPVR_VAH": create_vpvr_vah,
    "VPVR_VAL": create_vpvr_val,
```

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_live_indicators_price_levels.py -v`
Expected: 12개 테스트 전부 PASS

- [ ] **Step 5: 전체 테스트 스위트 실행(회귀 확인 + A그룹 33개 전부 커버 확인)**

Run: `python -m pytest -q`
Expected: 전부 PASS. 그리고 아래를 실행해 `LIVE_INDICATOR_FACTORY`가 정확히 33개
(B그룹 6개를 제외한 A그룹 전부)를 담고 있는지 확인:

```bash
python -c "
from trading.live_indicators import LIVE_INDICATOR_FACTORY
from engine.indicators import INDICATOR_FACTORY
b_group = {'FUNDING_RATE','FEAR_GREED_CMC','KOREA_PREMIUM','MARKET_TREND','BTC_CORRELATION','USDT_CORRELATION'}
expected = set(INDICATOR_FACTORY) - b_group
missing = expected - set(LIVE_INDICATOR_FACTORY)
extra = set(LIVE_INDICATOR_FACTORY) - expected
assert not missing, f'빠진 지표: {missing}'
assert not extra, f'의도치 않은 초과 지표: {extra}'
print('OK:', len(LIVE_INDICATOR_FACTORY), '개 등록됨')
"
```
Expected 출력: `OK: 33 개 등록됨` (A그룹 33개 전부, 에러 없이 통과)

- [ ] **Step 6: 커밋**

```bash
git add trading/live_indicators.py tests/test_live_indicators_price_levels.py
git commit -m "feat: live_indicators에 VPVR류 pandas 포팅 추가 (A그룹 33개 완료)"
```

---

## Self-Review

**스펙 커버리지:**
- 결정 2(A그룹 33개 전부 포팅) → Task 1~8에서 33개 전부 커버(`INDICATOR_FACTORY` - B그룹
  6개 = 정확히 33개, Task 8의 Step 5에서 집합 비교로 검증).
- 결정 1(골든테스트 필수, backtrader와 pandas 값 일치 검증) → 모든 태스크가
  `assert_matches_backtrader`로 A그룹 33개 전부 검증(예외 없음).
- B그룹 6개는 명시적으로 이 플랜 범위 밖(다음 서브플랜③) — 손대지 않음.
- 이 플랜은 지표 계산만 다루고, `eval_group_values()`와의 결합(신호 생성)은 다루지
  않는다 — 서브플랜⑤(`signal_engine.py`)로 명확히 넘김.

**플레이스홀더 스캔:** 없음 — 모든 스텝에 완전한 코드가 있고, 모든 pandas 공식은 이
세션에서 실제 backtrader 실행 결과와 대조해 사전 검증됨(오차 1e-6 이내).

**타입 일관성:** 모든 `create_*` 함수는 `(df: pd.DataFrame, **params) -> pd.Series`
시그니처로 일관되며, `engine/indicators/*.py`의 동명 함수와 파라미터 이름·기본값이
1:1 대응한다(Task별 코드에 명시). `LIVE_INDICATOR_FACTORY`의 값 타입은 이 함수들이므로
`dict[str, object]`로 태스크 전체에서 일관됨(`engine.indicators.INDICATOR_FACTORY`와
같은 타입 어노테이션 스타일).

---

## 다음 서브플랜 (이 문서 이후)

③ **지표 엔진 B그룹 + 장애정책** — 외부데이터 6개 지표의 실시간 수집 파이프라인 +
스펙 결정 8(지연/실패 시 `None` 반환, `eval_group_values`가 이미 이걸 처리하도록
구현돼 있음, 서브플랜①)이 실제로 맞물려 동작하는지 검증. ④ Upbit 연동. ⑤ 트레이딩
엔진 코어(`signal_engine.py`가 `collect_blocks`+`indicator_key`+`LIVE_INDICATOR_FACTORY`
+`eval_group_values`를 결합). ⑥ UX.
