# 조건식 기반 백테스트 엔진 백엔드 구축 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 프런트엔드 `PortSetupForm`/`StrategyConditionBuilder`가 이미 요구하는 "지표 + 연산자 + threshold 블록을 AND/OR(괄호 묶음 포함)로 결합한 매수/매도 조건 트리" 모델을 백엔드가 실제로 실행·검증할 수 있도록 만든다. 업비트 KRW 마켓 전체 목록 조회, 지표 카탈로그(설명/예시 포함) 제공, 조건 트리 실행 엔진, 사전 검증 엔드포인트를 새로 구축한다.

**Architecture:** `C:\Users\jungm\project\backtesting_1`의 `strategy_builder.py`(조건 트리 평가)와 `engine/indicators/*`(원시 지표 팩토리)를 포팅하되, 핵심 설계 하나를 바꾼다 — 원본은 조건 트리마다 `type()`으로 **동적 `bt.Strategy` 클래스**를 새로 만드는데, 이 프로젝트의 `engine/cache.compute_cache_key()`는 `inspect.getsource(strategy_cls)`로 클래스 소스를 읽어 캐시 키를 만들기 때문에 동적 클래스에서는 소스를 못 읽어 캐싱이 깨진다. 그래서 **정적 `ConditionTreeStrategy` 클래스 하나만 두고, 조건 트리는 backtrader의 `params`(즉 `run_backtest_cached`의 `strategy_params`)로 전달**하는 방식으로 바꾼다 — 이러면 클래스 소스는 항상 동일하고, 캐시 키는 트리 내용(JSON 직렬화 가능한 순수 dict)만으로 정확히 갈린다. `engine/runner.py`, `engine/cache.py`는 이미 이 방식과 완전히 호환되므로 수정하지 않는다.

기존 `SIGNAL_REGISTRY` 기반 sweep 시스템(`signals.py`, `engine/strategies.py`, `engine/sweep.py`, 히트맵/랭킹/추이 탭)은 **건드리지 않는다** — 새 조건 트리 엔진은 홈 화면 온디맨드 실행(`POST /api/v1/backtests/run`) 전용으로 별도 구축하며, 두 시스템이 당분간 공존한다.

**Tech Stack:** FastAPI, pydantic v2.6(재귀 모델은 `model_rebuild()` 사용), pytest + `fastapi.testclient.TestClient`, backtrader, httpx.

## Global Constraints

- `engine/runner.py`, `engine/cache.py`, `signals.py`, `engine/strategies.py`, `engine/sweep.py`, `frontend/app/backtests/[runId]/page.tsx`는 이 계획에서 수정하지 않는다.
- 새로 만드는 `bt.Strategy`는 반드시 모듈 최상위에 정적으로 정의한다(요청마다 동적 생성 금지) — 캐시 키가 `inspect.getsource()`에 의존하기 때문.
- 조건 트리(`ConditionBlock`/`ConditionGroup`)를 다루는 모든 함수는 pydantic 모델이 아니라 **순수 dict**를 받는다(`model.model_dump()`로 변환 후 전달) — `engine/` 모듈이 FastAPI/pydantic에 의존하지 않도록 유지.
- 프런트엔드가 이미 하드코딩해 둔 지표 카탈로그(`frontend/components/StrategyConditionBuilder.tsx`의 `INDICATOR_CATEGORIES`)와 지표 키(`SMA`/`EMA`/`WMA`/`RSI`/`MACD_line`/`MACD_signal`/`STOCH_K`/`STOCH_D`/`CCI`/`WILLIAMS_R`/`BB_upper`/`BB_lower`/`BB_middle`/`ATR`/`OBV`/`VOLUME_SMA`)와 정확히 동일한 키 이름을 백엔드 `INDICATOR_FACTORY`에서 사용한다. `FEAR_GREED_CMC`는 제외한다(외부 데이터 소스 없음, 참고 프로젝트에도 실제 계산 로직 없었음).
- `운용자금`(`initial_capital`)은 이번에 요청 파라미터로 반영한다. `수수료율`은 프런트에서 이미 제거되었으므로 `DEFAULT_RISK_CONFIG`의 값을 계속 사용한다.
- 코인은 이번에도 단일 선택만 지원한다(`market: str`) — 다중 선택/세그먼트 라벨링은 이 계획 범위 밖이며, 문서 맨 끝 "향후 확장" 절에만 기록한다.

---

### Task 1: 지표 팩토리 포팅

**Files:**
- Create: `engine/indicators/__init__.py`
- Create: `engine/indicators/trend.py`
- Create: `engine/indicators/momentum.py`
- Create: `engine/indicators/volatility.py`
- Create: `engine/indicators/volume.py`
- Test: `tests/test_indicators.py`

**Interfaces:**
- Produces: `INDICATOR_FACTORY: dict[str, Callable[..., bt.Indicator]]` — 지표 이름 → `(data: bt.feeds.PandasData, **params) -> bt.Indicator` 생성 함수. Task 2/3이 그대로 사용한다.

- [x] **Step 1: `engine/indicators/trend.py` 작성**

```python
from __future__ import annotations

import backtrader as bt


def create_sma(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 14))
    return bt.indicators.SMA(data, period=period)


def create_ema(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 14))
    return bt.indicators.EMA(data, period=period)


def create_wma(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 14))
    return bt.indicators.WeightedMovingAverage(data, period=period)
```

- [x] **Step 2: `engine/indicators/momentum.py` 작성**

```python
from __future__ import annotations

import backtrader as bt


def create_rsi(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 14))
    return bt.indicators.RSI(data, period=period)


def create_macd_line(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    fast = int(params.get("fast", 12))
    slow = int(params.get("slow", 26))
    signal = int(params.get("signal", 9))
    return bt.indicators.MACD(data, period1=fast, period2=slow, period3=signal)


def create_macd_signal(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    fast = int(params.get("fast", 12))
    slow = int(params.get("slow", 26))
    signal = int(params.get("signal", 9))
    return bt.indicators.MACD(data, period1=fast, period2=slow, period3=signal)


def create_stoch_k(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    k_period = int(params.get("k_period", 14))
    d_period = int(params.get("d_period", 3))
    return bt.indicators.Stochastic(data, period=k_period, period_dfast=d_period)


def create_stoch_d(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    k_period = int(params.get("k_period", 14))
    d_period = int(params.get("d_period", 3))
    return bt.indicators.Stochastic(data, period=k_period, period_dfast=d_period)


def create_cci(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 20))
    return bt.indicators.CCI(data, period=period)


def create_williams_r(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 14))
    return bt.indicators.WilliamsR(data, period=period)
```

- [x] **Step 3: `engine/indicators/volatility.py` 작성**

```python
from __future__ import annotations

import backtrader as bt


def create_bb_upper(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 20))
    return bt.indicators.BollingerBands(data, period=period, devfactor=2.0)


def create_bb_lower(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 20))
    return bt.indicators.BollingerBands(data, period=period, devfactor=2.0)


def create_bb_middle(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 20))
    return bt.indicators.BollingerBands(data, period=period, devfactor=2.0)


def create_atr(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 14))
    return bt.indicators.ATR(data, period=period)
```

- [x] **Step 4: `engine/indicators/volume.py` 작성**

```python
from __future__ import annotations

import backtrader as bt


class OBV(bt.Indicator):
    """On Balance Volume — 종가가 전일 대비 상승하면 +거래량, 하락하면 -거래량을 누적."""

    lines = ("obv",)
    plotinfo = dict(subplot=True)

    def __init__(self) -> None:
        self.addminperiod(2)

    def next(self) -> None:
        if self.data.close[0] > self.data.close[-1]:
            self.lines.obv[0] = self.lines.obv[-1] + self.data.volume[0]
        elif self.data.close[0] < self.data.close[-1]:
            self.lines.obv[0] = self.lines.obv[-1] - self.data.volume[0]
        else:
            self.lines.obv[0] = self.lines.obv[-1]


def create_obv(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    return OBV(data)


def create_volume_sma(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 20))
    return bt.indicators.SMA(data.volume, period=period)
```

- [x] **Step 5: `engine/indicators/__init__.py` 작성**

```python
from __future__ import annotations

from .momentum import (
    create_cci,
    create_macd_line,
    create_macd_signal,
    create_rsi,
    create_stoch_d,
    create_stoch_k,
    create_williams_r,
)
from .trend import create_ema, create_sma, create_wma
from .volatility import create_atr, create_bb_lower, create_bb_middle, create_bb_upper
from .volume import create_obv, create_volume_sma

INDICATOR_FACTORY: dict[str, object] = {
    "SMA": create_sma,
    "EMA": create_ema,
    "WMA": create_wma,
    "RSI": create_rsi,
    "MACD_line": create_macd_line,
    "MACD_signal": create_macd_signal,
    "STOCH_K": create_stoch_k,
    "STOCH_D": create_stoch_d,
    "CCI": create_cci,
    "WILLIAMS_R": create_williams_r,
    "BB_upper": create_bb_upper,
    "BB_lower": create_bb_lower,
    "BB_middle": create_bb_middle,
    "ATR": create_atr,
    "OBV": create_obv,
    "VOLUME_SMA": create_volume_sma,
}

__all__ = ["INDICATOR_FACTORY"]
```

- [x] **Step 6: 실패하는 테스트 작성**

`tests/test_indicators.py` 새로 생성:

```python
import backtrader as bt

from engine.indicators import INDICATOR_FACTORY
from tests.signal_fixtures import make_oscillating_df
from engine.runner import run_backtest


class _ProbeStrategy(bt.Strategy):
    params = (("indicator", "RSI"), ("indicator_params", {}))

    def __init__(self):
        create_fn = INDICATOR_FACTORY[self.p.indicator]
        self.probe = create_fn(self.data, **self.p.indicator_params)
        self.seen_values: list[float] = []

    def next(self):
        self.seen_values.append(float(self.probe[0]))


def _run_probe(indicator: str, params: dict) -> list[float]:
    df = make_oscillating_df()
    df_bt = df.set_index("candle_time")
    df_bt.index = df_bt.index.tz_localize(None)
    cerebro = bt.Cerebro()
    cerebro.adddata(bt.feeds.PandasData(dataname=df_bt, openinterest=-1))
    cerebro.addstrategy(_ProbeStrategy, indicator=indicator, indicator_params=params)
    results = cerebro.run()
    return results[0].seen_values


def test_all_registered_indicators_produce_values():
    for name in INDICATOR_FACTORY:
        values = _run_probe(name, {})
        assert len(values) > 0, f"{name} 지표가 값을 하나도 생성하지 못함"


def test_sma_matches_manual_average():
    values = _run_probe("SMA", {"period": 5})
    df = make_oscillating_df()
    manual = df["close"].rolling(5).mean().iloc[-1]
    assert abs(values[-1] - manual) < 1e-6
```

- [x] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_indicators.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.indicators'`

- [x] **Step 8: 위 Step 1~5의 구현 파일이 이미 작성되어 있으므로 통과 확인**

Run: `pytest tests/test_indicators.py -v`
Expected: PASS

- [x] **Step 9: 커밋**

```bash
git add engine/indicators tests/test_indicators.py
git commit -m "feat: add raw indicator factory (SMA/EMA/RSI/MACD/BB/ATR/OBV/...)"
```

---

### Task 2: 조건 트리 평가·검증 유틸

**Files:**
- Create: `engine/condition_tree.py`
- Test: `tests/test_condition_tree.py`

**Interfaces:**
- Consumes: `INDICATOR_FACTORY`(Task 1).
- Produces: `collect_blocks(group: dict) -> list[dict]`, `indicator_key(indicator: str, params: dict) -> str`, `get_indicator_value(indicator_name: str, obj: bt.Indicator) -> float`, `apply_operator(value: float, operator: str, threshold: float) -> bool`, `eval_group(group: dict, indicators: dict) -> bool`, `find_unknown_indicators(group: dict) -> list[str]`, `is_empty(group: dict) -> bool`, `max_required_period(group: dict) -> int`. Task 3(전략 클래스)과 Task 7(검증 엔드포인트)이 그대로 사용한다.

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_condition_tree.py` 새로 생성:

```python
from engine.condition_tree import (
    apply_operator,
    collect_blocks,
    find_unknown_indicators,
    is_empty,
    max_required_period,
)


def test_collect_blocks_flattens_nested_groups():
    tree = {
        "type": "AND",
        "conditions": [
            {"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30},
            {
                "type": "OR",
                "conditions": [
                    {"indicator": "MACD_line", "params": {}, "operator": ">", "threshold": 0},
                    {"indicator": "SMA", "params": {"period": 20}, "operator": ">", "threshold": 100},
                ],
            },
        ],
    }
    blocks = collect_blocks(tree)
    assert len(blocks) == 3
    assert {b["indicator"] for b in blocks} == {"RSI", "MACD_line", "SMA"}


def test_apply_operator_all_variants():
    assert apply_operator(10, ">", 5) is True
    assert apply_operator(10, "<", 5) is False
    assert apply_operator(5, ">=", 5) is True
    assert apply_operator(5, "<=", 5) is True
    assert apply_operator(5, "==", 5) is True


def test_find_unknown_indicators_detects_bad_key():
    tree = {"type": "AND", "conditions": [{"indicator": "NOPE", "params": {}, "operator": ">", "threshold": 0}]}
    assert find_unknown_indicators(tree) == ["NOPE"]


def test_is_empty_true_for_no_conditions():
    assert is_empty({"type": "AND", "conditions": []}) is True
    assert is_empty({"type": "AND", "conditions": [{"indicator": "RSI", "params": {}, "operator": "<", "threshold": 1}]}) is False


def test_max_required_period_takes_largest_param_value():
    tree = {
        "type": "AND",
        "conditions": [
            {"indicator": "SMA", "params": {"period": 200}, "operator": ">", "threshold": 0},
            {"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30},
        ],
    }
    assert max_required_period(tree) == 200
```

- [x] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_condition_tree.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.condition_tree'`

- [x] **Step 3: 구현 작성**

`engine/condition_tree.py` 새로 생성:

```python
"""
engine/condition_tree.py

JSON ConditionGroup 트리(재귀적 AND/OR, 중첩 괄호 묶음 지원)를 평가·검증한다.

ConditionGroup = {"type": "AND" | "OR", "conditions": [ConditionBlock | ConditionGroup, ...]}
ConditionBlock = {"indicator": str, "params": dict, "operator": str, "threshold": float}

C:\\Users\\jungm\\project\\backtesting_1의 backend/app/engine/strategy_builder.py를 참고해
포팅했다. 원본과 다른 점: 원본은 조건 트리로부터 매번 동적 bt.Strategy 클래스를 만들지만,
이 프로젝트의 캐시 키(engine/cache.compute_cache_key)는 inspect.getsource(strategy_cls)에
의존하므로 동적 클래스를 쓰면 캐싱이 깨진다. 그래서 이 모듈은 순수 평가/검증 함수만 제공하고,
실제 bt.Strategy는 engine/condition_strategy.py의 정적 클래스가 담당한다.
"""
from __future__ import annotations

import backtrader as bt

from engine.indicators import INDICATOR_FACTORY


def indicator_key(indicator: str, params: dict) -> str:
    """지표 이름 + 파라미터 조합의 고유 키 생성 (같은 지표를 여러 블록이 참조해도 한 번만 생성)."""
    sorted_params = sorted(params.items())
    return f"{indicator}__{sorted_params}"


def collect_blocks(group: dict) -> list[dict]:
    """ConditionGroup에서 모든 ConditionBlock을 재귀적으로 수집."""
    blocks: list[dict] = []
    for item in group.get("conditions", []):
        if "indicator" in item:
            blocks.append(item)
        elif "type" in item:
            blocks.extend(collect_blocks(item))
    return blocks


def get_indicator_value(indicator_name: str, obj: bt.Indicator) -> float:
    """지표 종류에 따라 현재 바 값을 추출 (다중 라인 지표는 대표 라인을 지정해야 함)."""
    if indicator_name == "MACD_line":
        return float(obj.macd[0])
    elif indicator_name == "MACD_signal":
        return float(obj.signal[0])
    elif indicator_name == "BB_upper":
        return float(obj.top[0])
    elif indicator_name == "BB_lower":
        return float(obj.bot[0])
    elif indicator_name == "BB_middle":
        return float(obj.mid[0])
    elif indicator_name == "STOCH_K":
        return float(obj.percK[0])
    elif indicator_name == "STOCH_D":
        return float(obj.percD[0])
    else:
        return float(obj[0])


def apply_operator(value: float, operator: str, threshold: float) -> bool:
    if operator == ">":
        return value > threshold
    elif operator == "<":
        return value < threshold
    elif operator == ">=":
        return value >= threshold
    elif operator == "<=":
        return value <= threshold
    elif operator == "==":
        return value == threshold
    return False


def eval_group(group: dict, indicators: dict[str, bt.Indicator]) -> bool:
    """ConditionGroup을 재귀적으로 평가해 bool 반환. indicators는 indicator_key -> bt.Indicator 매핑."""
    group_type = group.get("type", "AND")
    conditions = group.get("conditions", [])

    if not conditions:
        return False

    results: list[bool] = []
    for item in conditions:
        if "indicator" in item:
            key = indicator_key(item["indicator"], item.get("params", {}))
            if key not in indicators:
                results.append(False)
                continue
            value = get_indicator_value(item["indicator"], indicators[key])
            results.append(apply_operator(value, item["operator"], float(item["threshold"])))
        elif "type" in item:
            results.append(eval_group(item, indicators))

    return all(results) if group_type == "AND" else any(results)


def find_unknown_indicators(group: dict) -> list[str]:
    """INDICATOR_FACTORY에 없는 지표 키를 모두 찾아 반환(중복 제거, 정렬)."""
    unknown = {b["indicator"] for b in collect_blocks(group) if b["indicator"] not in INDICATOR_FACTORY}
    return sorted(unknown)


def is_empty(group: dict) -> bool:
    return len(group.get("conditions", [])) == 0


def max_required_period(group: dict) -> int:
    """조건 트리에 등장하는 모든 숫자 파라미터 중 최댓값을 반환 — 지표 계산에 필요한
    최소 워밍업 봉 수의 근사치로 쓴다(예: SMA period=200이면 최소 200봉 필요)."""
    periods = [0]
    for block in collect_blocks(group):
        for value in block.get("params", {}).values():
            try:
                periods.append(int(value))
            except (TypeError, ValueError):
                continue
    return max(periods)


__all__ = [
    "indicator_key",
    "collect_blocks",
    "get_indicator_value",
    "apply_operator",
    "eval_group",
    "find_unknown_indicators",
    "is_empty",
    "max_required_period",
]
```

- [x] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_condition_tree.py -v`
Expected: PASS

- [x] **Step 5: 커밋**

```bash
git add engine/condition_tree.py tests/test_condition_tree.py
git commit -m "feat: add condition tree evaluation and validation utilities"
```

---

### Task 3: 정적 `ConditionTreeStrategy`

**Files:**
- Create: `engine/condition_strategy.py`
- Test: `tests/test_condition_strategy.py`

**Interfaces:**
- Consumes: `INDICATOR_FACTORY`(Task 1), `collect_blocks`/`eval_group`/`indicator_key`(Task 2), `engine.runner.run_backtest`(기존), `engine.cache.run_backtest_cached`(기존, 수정 없음).
- Produces: `ConditionTreeStrategy(bt.Strategy)` — `params=(("buy_conditions", None), ("sell_conditions", None))`. Task 6이 `run_backtest_cached(strategy_cls=ConditionTreeStrategy, strategy_params={"buy_conditions": ..., "sell_conditions": ...}, ...)`로 호출한다.

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_condition_strategy.py` 새로 생성:

```python
from datetime import datetime, timezone

from engine.condition_strategy import ConditionTreeStrategy
from engine.runner import run_backtest
from engine.sweep import DEFAULT_RISK_CONFIG
from tests.signal_fixtures import make_oscillating_df


def _run(buy_conditions: dict, sell_conditions: dict) -> dict:
    df = make_oscillating_df()
    return run_backtest(
        df=df,
        strategy_cls=ConditionTreeStrategy,
        risk_config=DEFAULT_RISK_CONFIG,
        strategy_params={"buy_conditions": buy_conditions, "sell_conditions": sell_conditions},
    )


def test_rsi_oversold_overbought_produces_trades():
    buy = {"type": "AND", "conditions": [{"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 40}]}
    sell = {"type": "AND", "conditions": [{"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 60}]}
    result = _run(buy, sell)
    assert result["final_value"] > 0
    assert isinstance(result["trades"], list)


def test_empty_buy_conditions_never_enters():
    buy = {"type": "AND", "conditions": []}
    sell = {"type": "AND", "conditions": [{"indicator": "RSI", "params": {}, "operator": ">", "threshold": 60}]}
    result = _run(buy, sell)
    assert result["trades"] == []


def test_or_group_at_top_level_combines_with_any():
    buy = {
        "type": "OR",
        "conditions": [
            {"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 1},  # 거의 발생 안 함
            {"indicator": "SMA", "params": {"period": 5}, "operator": ">", "threshold": 0},  # 항상 참
        ],
    }
    sell = {"type": "AND", "conditions": [{"indicator": "RSI", "params": {}, "operator": ">", "threshold": 60}]}
    result = _run(buy, sell)
    # SMA > 0 조건이 항상 참이므로 OR 그룹은 항상 참 -> 첫 봉 이후 즉시 매수되어야 함
    assert len(result["trades"]) > 0 or result["final_value"] != DEFAULT_RISK_CONFIG["initial_capital"]
```

- [x] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_condition_strategy.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.condition_strategy'`

- [x] **Step 3: 구현 작성**

`engine/condition_strategy.py` 새로 생성:

```python
"""
engine/condition_strategy.py

ConditionGroup 트리(engine/condition_tree.py) 두 개(매수/매도)를 받아 실행하는
정적 bt.Strategy. 요청마다 동적으로 클래스를 만들지 않는다 — engine/cache.py의
캐시 키가 inspect.getsource(strategy_cls)에 의존하므로, 클래스는 항상 이 모듈에
고정된 소스로 존재해야 하고 트리 내용은 backtrader params(=strategy_params)로만
달라져야 캐싱이 올바르게 동작한다.
"""
from __future__ import annotations

import backtrader as bt

from engine.condition_tree import collect_blocks, eval_group, indicator_key
from engine.indicators import INDICATOR_FACTORY

_EMPTY_GROUP: dict = {"type": "AND", "conditions": []}


class ConditionTreeStrategy(bt.Strategy):
    params = (
        ("buy_conditions", None),
        ("sell_conditions", None),
    )

    def __init__(self) -> None:
        self._buy_cond: dict = self.p.buy_conditions or _EMPTY_GROUP
        self._sell_cond: dict = self.p.sell_conditions or _EMPTY_GROUP
        self._buy_inds: dict[str, bt.Indicator] = {}
        self._sell_inds: dict[str, bt.Indicator] = {}

        for block in collect_blocks(self._buy_cond):
            self._ensure_indicator(self._buy_inds, block)
        for block in collect_blocks(self._sell_cond):
            self._ensure_indicator(self._sell_inds, block)

    def _ensure_indicator(self, store: dict[str, bt.Indicator], block: dict) -> None:
        key = indicator_key(block["indicator"], block.get("params", {}))
        if key in store:
            return
        create_fn = INDICATOR_FACTORY.get(block["indicator"])
        if create_fn is None:
            raise ValueError(f"알 수 없는 지표: {block['indicator']}")
        store[key] = create_fn(self.data, **block.get("params", {}))

    def next(self) -> None:
        if not self.position:
            if eval_group(self._buy_cond, self._buy_inds):
                self.buy()
        else:
            if eval_group(self._sell_cond, self._sell_inds):
                self.sell()


__all__ = ["ConditionTreeStrategy"]
```

- [x] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_condition_strategy.py -v`
Expected: PASS

- [x] **Step 5: 캐시 키가 트리 내용만으로 정상적으로 갈리는지 수동 확인**

```bash
python -c "
from engine.cache import compute_cache_key
from engine.condition_strategy import ConditionTreeStrategy
from datetime import datetime, timezone
a = compute_cache_key(ConditionTreeStrategy, {'buy_conditions': {'type':'AND','conditions':[]}, 'sell_conditions': {'type':'AND','conditions':[]}}, 'KRW-BTC', 'days', datetime(2026,1,1,tzinfo=timezone.utc), datetime(2026,2,1,tzinfo=timezone.utc), {'initial_capital':1})
b = compute_cache_key(ConditionTreeStrategy, {'buy_conditions': {'type':'AND','conditions':[{'indicator':'RSI','params':{},'operator':'<','threshold':30}]}, 'sell_conditions': {'type':'AND','conditions':[]}}, 'KRW-BTC', 'days', datetime(2026,1,1,tzinfo=timezone.utc), datetime(2026,2,1,tzinfo=timezone.utc), {'initial_capital':1})
assert a != b, '트리 내용이 다른데 캐시 키가 같음 — 캐싱이 깨졌다'
print('OK: 캐시 키가 트리 내용에 따라 정상적으로 달라짐')
"
```
Expected: `OK: 캐시 키가 트리 내용에 따라 정상적으로 달라짐` 출력

- [x] **Step 6: 커밋**

```bash
git add engine/condition_strategy.py tests/test_condition_strategy.py
git commit -m "feat: add static ConditionTreeStrategy (cache-key-safe condition tree execution)"
```

---

### Task 4: 업비트 KRW 마켓 전체 목록

**Files:**
- Modify: `upbit_data_service.py`
- Modify: `backend/main.py`
- Test: `tests/test_upbit_data_service.py`
- Test: `tests/test_backend.py`

**Interfaces:**
- Produces: `upbit_data_service.get_krw_markets() -> list[dict]`(각 dict: `{"market": str, "korean_name": str, "english_name": str}`), `GET /api/v1/markets` → `200` + 같은 배열.

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_upbit_data_service.py` 끝에 추가(기존 테스트 스타일 확인 후 동일한 monkeypatch 패턴 사용):

```python
def test_get_krw_markets_filters_to_krw_prefix(monkeypatch):
    import upbit_data_service

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return [
                {"market": "KRW-BTC", "korean_name": "비트코인", "english_name": "Bitcoin"},
                {"market": "KRW-ETH", "korean_name": "이더리움", "english_name": "Ethereum"},
                {"market": "BTC-ETH", "korean_name": "이더리움", "english_name": "Ethereum"},
                {"market": "USDT-BTC", "korean_name": "비트코인", "english_name": "Bitcoin"},
            ]

    def _fake_get(url, params=None, timeout=None):
        assert "market/all" in url
        return _FakeResponse()

    monkeypatch.setattr(upbit_data_service.httpx, "get", _fake_get)

    markets = upbit_data_service.get_krw_markets()
    assert [m["market"] for m in markets] == ["KRW-BTC", "KRW-ETH"]
    assert markets[0]["korean_name"] == "비트코인"
```

`tests/test_backend.py` 끝에 추가:

```python
def test_get_markets_returns_krw_markets_only(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    def _fake_get_krw_markets():
        return [{"market": "KRW-BTC", "korean_name": "비트코인", "english_name": "Bitcoin"}]

    monkeypatch.setattr(backend_module, "get_krw_markets", _fake_get_krw_markets)

    resp = client.get("/api/v1/markets")
    assert resp.status_code == 200
    assert resp.json() == [{"market": "KRW-BTC", "korean_name": "비트코인", "english_name": "Bitcoin"}]
```

- [x] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_upbit_data_service.py::test_get_krw_markets_filters_to_krw_prefix tests/test_backend.py::test_get_markets_returns_krw_markets_only -v`
Expected: FAIL — `AttributeError: module 'upbit_data_service' has no attribute 'get_krw_markets'`

- [x] **Step 3: `upbit_data_service.py`에 구현 추가**

`upbit_data_service.py`의 `get_candles` 함수 뒤에 추가:

```python
def get_krw_markets() -> list[dict]:
    """업비트 KRW 마켓 전체 목록을 조회한다. 캐싱하지 않는다 — 가볍고 자주 바뀌지
    않는 호출이라, 매 조회마다 최신 상장 코인을 그대로 반영하는 편이 낫다."""
    resp = httpx.get(f"{UPBIT_BASE_URL}/market/all", params={"isDetails": "false"}, timeout=10)
    resp.raise_for_status()
    all_markets = resp.json()
    return [
        {
            "market": m["market"],
            "korean_name": m["korean_name"],
            "english_name": m["english_name"],
        }
        for m in all_markets
        if m["market"].startswith("KRW-")
    ]
```

- [x] **Step 4: `backend/main.py`에 엔드포인트 추가**

import 블록에 추가:

```python
from upbit_data_service import get_candles, get_krw_markets
```

`get_signals()` 엔드포인트 아래에 추가:

```python
@app.get("/api/v1/markets")
def get_markets() -> list[dict]:
    return get_krw_markets()
```

- [x] **Step 5: 테스트 통과 확인**

Run: `pytest tests/test_upbit_data_service.py tests/test_backend.py -v`
Expected: 전체 PASS

- [x] **Step 6: 실제 업비트 API로 수동 확인**

```bash
python -c "from upbit_data_service import get_krw_markets; ms = get_krw_markets(); print(len(ms), ms[:3])"
```
Expected: 100개 이상의 KRW 마켓이 출력됨(네트워크 필요)

- [x] **Step 7: 커밋**

```bash
git add upbit_data_service.py backend/main.py tests/test_upbit_data_service.py tests/test_backend.py
git commit -m "feat: add GET /api/v1/markets (all Upbit KRW markets)"
```

---

### Task 5: 지표 카탈로그(설명 + 예시 계산법 포함)

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_backend.py`

**Interfaces:**
- Produces: `GET /api/v1/indicators/catalog` → `200` + `list[dict]`, 각 dict: `{value, label, category, params: [{key, label, default}], description, example}`. 프런트엔드 `StrategyConditionBuilder.tsx`가 하드코딩된 `INDICATOR_CATEGORIES` 대신 이 응답을 쓰도록 바꾸는 것은 이 계획 범위 밖(맨 끝 "프런트엔드 후속 작업" 참고)이지만, 응답 필드명은 그 컴포넌트가 바로 소비할 수 있도록 맞춘다.

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_backend.py` 끝에 추가:

```python
def test_get_indicator_catalog_covers_all_registered_indicators(monkeypatch, tmp_path):
    from engine.indicators import INDICATOR_FACTORY

    client = _client(monkeypatch, tmp_path)
    resp = client.get("/api/v1/indicators/catalog")
    assert resp.status_code == 200
    body = resp.json()

    catalog_values = {item["value"] for item in body}
    assert catalog_values == set(INDICATOR_FACTORY.keys())

    for item in body:
        assert item["description"], f"{item['value']}에 description이 없음"
        assert item["example"], f"{item['value']}에 example이 없음"
        assert item["category"] in {"추세", "오실레이터", "거래량"}
```

- [x] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_backend.py::test_get_indicator_catalog_covers_all_registered_indicators -v`
Expected: FAIL — `404 Not Found`

- [x] **Step 3: `backend/main.py`에 카탈로그 상수 + 엔드포인트 추가**

`backend/main.py`의 import 블록 아래(다른 상수 선언 위치)에 추가:

```python
INDICATOR_CATALOG: list[dict] = [
    {
        "value": "SMA", "label": "SMA (단순 이동평균)", "category": "추세",
        "params": [{"key": "period", "label": "기간", "default": 14}],
        "description": "최근 N개 봉의 종가를 산술평균한 값으로, 가격의 큰 흐름을 부드럽게 보여줍니다.",
        "example": "period=20이면 최근 20개 종가의 평균을 매 봉마다 다시 계산합니다. 예: 최근 20봉 종가 합이 2,000,000이면 SMA=100,000.",
    },
    {
        "value": "EMA", "label": "EMA (지수 이동평균)", "category": "추세",
        "params": [{"key": "period", "label": "기간", "default": 14}],
        "description": "최근 가격에 더 큰 가중치를 주는 이동평균으로, SMA보다 가격 변화에 빠르게 반응합니다.",
        "example": "period=20이면 가중치 α=2/(20+1)≈0.095를 적용해 EMA_today = 종가×α + EMA_어제×(1-α)로 계산합니다.",
    },
    {
        "value": "WMA", "label": "WMA (가중 이동평균)", "category": "추세",
        "params": [{"key": "period", "label": "기간", "default": 14}],
        "description": "최근 봉일수록 더 큰 선형 가중치를 주는 이동평균입니다.",
        "example": "period=3이면 (종가1×1 + 종가2×2 + 종가3×3) / (1+2+3)로 계산합니다(가장 최근 종가의 가중치가 가장 큼).",
    },
    {
        "value": "RSI", "label": "RSI (상대강도지수)", "category": "오실레이터",
        "params": [{"key": "period", "label": "기간", "default": 14}],
        "description": "일정 기간의 평균 상승폭과 평균 하락폭을 비교해 0~100 사이 값으로 과매수/과매도를 나타냅니다.",
        "example": "period=14 기준 RSI < 30이면 과매도(매수 검토), RSI > 70이면 과매수(매도 검토) 구간으로 흔히 해석합니다.",
    },
    {
        "value": "MACD_line", "label": "MACD Line", "category": "오실레이터",
        "params": [
            {"key": "fast", "label": "단기", "default": 12},
            {"key": "slow", "label": "장기", "default": 26},
            {"key": "signal", "label": "시그널", "default": 9},
        ],
        "description": "단기 EMA에서 장기 EMA를 뺀 값으로, 모멘텀의 방향과 세기를 나타냅니다.",
        "example": "fast=12, slow=26이면 MACD Line = EMA(12) − EMA(26). 0보다 크면 상승 모멘텀입니다.",
    },
    {
        "value": "MACD_signal", "label": "MACD Signal", "category": "오실레이터",
        "params": [
            {"key": "fast", "label": "단기", "default": 12},
            {"key": "slow", "label": "장기", "default": 26},
            {"key": "signal", "label": "시그널", "default": 9},
        ],
        "description": "MACD Line을 다시 지수이동평균한 값으로, MACD Line과의 교차로 매매 신호를 잡을 때 씁니다.",
        "example": "signal=9이면 MACD Line의 9기간 EMA. MACD Line이 Signal을 상향 돌파하면 흔히 매수 신호로 봅니다.",
    },
    {
        "value": "STOCH_K", "label": "스토캐스틱 %K", "category": "오실레이터",
        "params": [
            {"key": "k_period", "label": "K기간", "default": 14},
            {"key": "d_period", "label": "D기간", "default": 3},
        ],
        "description": "최근 기간의 최고가·최저가 대비 현재 종가의 위치를 0~100으로 나타냅니다.",
        "example": "k_period=14일 때 %K = (현재종가 − 14기간 최저가) / (14기간 최고가 − 14기간 최저가) × 100.",
    },
    {
        "value": "STOCH_D", "label": "스토캐스틱 %D", "category": "오실레이터",
        "params": [
            {"key": "k_period", "label": "K기간", "default": 14},
            {"key": "d_period", "label": "D기간", "default": 3},
        ],
        "description": "%K를 다시 이동평균한 값으로, %K보다 완만하게 움직여 노이즈를 줄입니다.",
        "example": "d_period=3이면 최근 3개 %K값의 단순평균이 %D입니다.",
    },
    {
        "value": "CCI", "label": "CCI (상품채널지수)", "category": "오실레이터",
        "params": [{"key": "period", "label": "기간", "default": 20}],
        "description": "가격이 통계적 평균에서 얼마나 벗어났는지를 나타내는 지표로, 극단값에서 평균 회귀를 노릴 때 씁니다.",
        "example": "일반적으로 CCI < -100이면 과매도, CCI > 100이면 과매수 구간으로 해석합니다.",
    },
    {
        "value": "WILLIAMS_R", "label": "Williams %R", "category": "오실레이터",
        "params": [{"key": "period", "label": "기간", "default": 14}],
        "description": "스토캐스틱 %K와 유사하나 0~-100 범위로 표현되는 과매수/과매도 지표입니다.",
        "example": "%R = (기간 최고가 − 현재종가) / (기간 최고가 − 기간 최저가) × -100. -80 이하면 과매도로 흔히 해석합니다.",
    },
    {
        "value": "BB_upper", "label": "볼린저밴드 상단", "category": "오실레이터",
        "params": [{"key": "period", "label": "기간", "default": 20}],
        "description": "이동평균에 표준편차의 2배를 더한 상단 밴드로, 돌파 시 강한 상승 모멘텀으로 해석하기도 합니다.",
        "example": "period=20이면 SMA(20) + 2×표준편차(20). 종가가 이 값을 상향 돌파하면 과열 신호로도, 추세 시작으로도 해석 가능합니다.",
    },
    {
        "value": "BB_lower", "label": "볼린저밴드 하단", "category": "오실레이터",
        "params": [{"key": "period", "label": "기간", "default": 20}],
        "description": "이동평균에서 표준편차의 2배를 뺀 하단 밴드로, 이탈 시 과매도 반등을 노리는 전략에 흔히 쓰입니다.",
        "example": "period=20이면 SMA(20) − 2×표준편차(20). 종가가 이 아래로 내려가면 매수 후보 구간으로 봅니다.",
    },
    {
        "value": "BB_middle", "label": "볼린저밴드 중간선", "category": "오실레이터",
        "params": [{"key": "period", "label": "기간", "default": 20}],
        "description": "볼린저밴드의 기준이 되는 단순 이동평균선입니다(상단/하단 밴드의 중심).",
        "example": "period=20이면 그냥 SMA(20)과 동일한 값입니다.",
    },
    {
        "value": "ATR", "label": "ATR (평균실질변동폭)", "category": "오실레이터",
        "params": [{"key": "period", "label": "기간", "default": 14}],
        "description": "일정 기간의 평균 변동폭(고가-저가 등)을 나타내며, 변동성 기반 돌파 조건의 임계값으로 흔히 씁니다.",
        "example": "period=14면 최근 14봉의 True Range 평균. 예: 전일 종가 + ATR×2 를 오늘 고가가 넘으면 변동성 돌파로 봅니다.",
    },
    {
        "value": "OBV", "label": "OBV (누적 거래량)", "category": "거래량",
        "params": [],
        "description": "종가가 오른 날은 거래량을 더하고 내린 날은 뺀 누적값으로, 가격과 거래량의 방향이 일치하는지 봅니다.",
        "example": "어제 OBV=1000이고 오늘 종가가 상승, 거래량 500이면 오늘 OBV=1500.",
    },
    {
        "value": "VOLUME_SMA", "label": "거래량 SMA", "category": "거래량",
        "params": [{"key": "period", "label": "기간", "default": 20}],
        "description": "최근 N개 봉의 거래량을 산술평균한 값으로, 현재 거래량이 평소보다 급등했는지 비교할 때 기준으로 씁니다.",
        "example": "period=20이면 최근 20봉 거래량의 평균. 현재 거래량이 이 값의 2배를 넘으면 거래량 급등으로 판단하는 식으로 활용합니다.",
    },
]
```

`get_markets()` 엔드포인트 아래에 추가:

```python
@app.get("/api/v1/indicators/catalog")
def get_indicator_catalog() -> list[dict]:
    return INDICATOR_CATALOG
```

- [x] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_backend.py::test_get_indicator_catalog_covers_all_registered_indicators -v`
Expected: PASS

- [x] **Step 5: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "feat: add GET /api/v1/indicators/catalog with description and example per indicator"
```

---

### Task 6: `POST /api/v1/backtests/run` 재작성 (조건 트리 + 운용자금 반영)

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_backend.py`

**Interfaces:**
- Consumes: `ConditionTreeStrategy`(Task 3), `engine.condition_tree.{is_empty, find_unknown_indicators}`(Task 2), `get_krw_markets`(Task 4).
- Produces: `RunBacktestRequest`(재정의, 기존 `signal_keys` 필드 제거) → `POST /api/v1/backtests/run` → `200` + `{"run_id": str}` 또는 `400` + `{"detail": str}`.
- **주의(Breaking Change):** 기존 `signal_keys: list[str]` 요청 필드와 그 필드를 검증하던 테스트 5개(`test_run_backtest_returns_run_id_and_is_retrievable` 등)를 이번 Task에서 새 스키마에 맞게 **교체**한다. `signal_keys` 기반 실행 경로 자체가 없어진다 — `signals.py`/`SIGNAL_REGISTRY`는 sweep 시스템 전용으로만 계속 쓰인다.

- [x] **Step 1: 기존 관련 테스트 삭제**

`tests/test_backend.py`에서 아래 5개 테스트와 `_patch_get_candles` 헬퍼를 삭제한다(모두 `signal_keys`를 사용하므로 새 스키마와 맞지 않음):
- `test_run_backtest_returns_run_id_and_is_retrievable`
- `test_run_backtest_rejects_empty_signal_keys`
- `test_run_backtest_rejects_unknown_signal_key`
- `test_run_backtest_rejects_reversed_date_range`
- `test_run_backtest_rejects_empty_candle_range`
- `_patch_get_candles` 헬퍼

- [x] **Step 2: 새 실패하는 테스트 작성**

`tests/test_backend.py` 상단 import 블록에 추가(없다면):

```python
import pandas as pd

from tests.signal_fixtures import make_oscillating_df
```

파일 끝에 추가:

```python
def _patch_get_candles(monkeypatch, df: pd.DataFrame | None = None):
    monkeypatch.setattr(
        backend_module, "get_candles",
        lambda market, timeframe, start, end: df if df is not None else make_oscillating_df(),
    )


_VALID_BUY = {"type": "AND", "conditions": [{"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 60}]}
_VALID_SELL = {"type": "AND", "conditions": [{"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 40}]}


def _run_request(**overrides) -> dict:
    body = {
        "market": "KRW-BTC",
        "timeframe": "days",
        "start": "2026-01-01",
        "end": "2026-03-01",
        "initial_capital": 1_000_000,
        "buy_conditions": _VALID_BUY,
        "sell_conditions": _VALID_SELL,
    }
    body.update(overrides)
    return body


def test_run_backtest_returns_run_id_and_is_retrievable(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch)

    resp = client.post("/api/v1/backtests/run", json=_run_request())
    assert resp.status_code == 200
    run_id = resp.json()["run_id"]
    assert run_id

    detail_resp = client.get(f"/api/v1/backtests/{run_id}")
    assert detail_resp.status_code == 200
    assert "final_value" in detail_resp.json()


def test_run_backtest_rejects_empty_buy_conditions(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch)

    resp = client.post(
        "/api/v1/backtests/run",
        json=_run_request(buy_conditions={"type": "AND", "conditions": []}),
    )
    assert resp.status_code == 400
    assert "매수 조건" in resp.json()["detail"]


def test_run_backtest_rejects_empty_sell_conditions(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch)

    resp = client.post(
        "/api/v1/backtests/run",
        json=_run_request(sell_conditions={"type": "AND", "conditions": []}),
    )
    assert resp.status_code == 400
    assert "매도 조건" in resp.json()["detail"]


def test_run_backtest_rejects_unknown_indicator(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch)

    bad_buy = {"type": "AND", "conditions": [{"indicator": "NOPE", "params": {}, "operator": ">", "threshold": 0}]}
    resp = client.post("/api/v1/backtests/run", json=_run_request(buy_conditions=bad_buy))
    assert resp.status_code == 400
    assert "NOPE" in resp.json()["detail"]


def test_run_backtest_rejects_reversed_date_range(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch)

    resp = client.post("/api/v1/backtests/run", json=_run_request(start="2026-03-01", end="2026-01-01"))
    assert resp.status_code == 400


def test_run_backtest_rejects_market_not_in_krw_list(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])

    resp = client.post("/api/v1/backtests/run", json=_run_request(market="KRW-NOTLISTED"))
    assert resp.status_code == 400
    assert "KRW-NOTLISTED" in resp.json()["detail"]


def test_run_backtest_rejects_empty_candle_range(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    empty_df = pd.DataFrame(columns=["candle_time", "open", "high", "low", "close", "volume"])
    _patch_get_candles(monkeypatch, df=empty_df)

    resp = client.post("/api/v1/backtests/run", json=_run_request())
    assert resp.status_code == 400


def test_run_backtest_uses_requested_initial_capital(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch)

    resp = client.post("/api/v1/backtests/run", json=_run_request(initial_capital=5_000_000))
    assert resp.status_code == 200
    run_id = resp.json()["run_id"]

    resp2 = client.post("/api/v1/backtests/run", json=_run_request(initial_capital=9_000_000))
    assert resp2.status_code == 200
    run_id2 = resp2.json()["run_id"]

    assert run_id != run_id2, "운용자금이 다른데 같은 run_id(캐시 hit)가 나옴 — initial_capital이 캐시 키에 반영 안 됨"
```

- [x] **Step 3: 테스트 실패 확인**

Run: `pytest tests/test_backend.py -k run_backtest -v`
Expected: FAIL — 기존 `signal_keys` 스키마와 새 테스트가 요구하는 필드가 달라 422/실패

- [x] **Step 4: `backend/main.py` 구현 교체**

import 블록을 아래로 교체:

```python
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Union

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from engine.cache import (
    list_combined_ranking,
    list_distinct_combos,
    list_latest_sweep_results,
    list_sweep_history,
    load_result,
    run_backtest_cached,
)
from engine.condition_strategy import ConditionTreeStrategy
from engine.condition_tree import find_unknown_indicators, is_empty, max_required_period
from engine.strategies import SignalStrategy
from engine.sweep import DEFAULT_RISK_CONFIG
from signals import SIGNAL_REGISTRY
from upbit_data_service import get_candles, get_krw_markets
```

`RunBacktestRequest` 클래스와 `run_backtest_endpoint` 함수를 아래로 완전히 교체:

```python
ComparisonOperator = Literal[">", "<", ">=", "<=", "=="]


class ConditionBlockRequest(BaseModel):
    indicator: str
    params: dict[str, float] = {}
    operator: ComparisonOperator
    threshold: float


class ConditionGroupRequest(BaseModel):
    type: Literal["AND", "OR"]
    conditions: list[Union[ConditionBlockRequest, "ConditionGroupRequest"]]


ConditionGroupRequest.model_rebuild()


class RunBacktestRequest(BaseModel):
    market: str
    timeframe: str
    start: str
    end: str
    initial_capital: float
    buy_conditions: ConditionGroupRequest
    sell_conditions: ConditionGroupRequest


def _validate_backtest_request(req: RunBacktestRequest) -> list[str]:
    """구조적 검증 + 시장 데이터 기반 검증을 모두 수행해 오류 사유 목록을 반환.
    Task 7의 /validate 엔드포인트와 이 함수를 공유한다."""
    errors: list[str] = []

    buy_dict = req.buy_conditions.model_dump()
    sell_dict = req.sell_conditions.model_dump()

    if is_empty(buy_dict):
        errors.append("매수 조건이 없습니다. 최소 1개 이상의 조건을 추가하세요.")
    if is_empty(sell_dict):
        errors.append("매도 조건이 없습니다. 최소 1개 이상의 조건을 추가하세요.")

    unknown = sorted(set(find_unknown_indicators(buy_dict)) | set(find_unknown_indicators(sell_dict)))
    if unknown:
        errors.append(f"지원하지 않는 지표입니다: {', '.join(unknown)}")

    if req.start >= req.end:
        errors.append("시작일은 종료일보다 빨라야 합니다.")

    if req.initial_capital <= 0:
        errors.append("운용자금은 0보다 커야 합니다.")

    krw_markets = {m["market"] for m in get_krw_markets()}
    if req.market not in krw_markets:
        errors.append(f"{req.market}은(는) 업비트 KRW 마켓 목록에 없습니다.")

    return errors


@app.post("/api/v1/backtests/run")
def run_backtest_endpoint(req: RunBacktestRequest) -> dict:
    errors = _validate_backtest_request(req)
    if errors:
        raise HTTPException(status_code=400, detail=" / ".join(errors))

    start_dt = datetime.strptime(req.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(req.end, "%Y-%m-%d").replace(
        hour=23, minute=59, second=59, tzinfo=timezone.utc
    )

    try:
        df = get_candles(req.market, req.timeframe, start_dt, end_dt)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if df.empty:
        raise HTTPException(status_code=400, detail="해당 기간에 캔들 데이터가 없습니다")

    buy_dict = req.buy_conditions.model_dump()
    sell_dict = req.sell_conditions.model_dump()
    required_bars = max(max_required_period(buy_dict), max_required_period(sell_dict))
    if len(df) < required_bars:
        raise HTTPException(
            status_code=400,
            detail=(
                f"선택한 지표가 최소 {required_bars}개의 봉을 필요로 하지만, "
                f"해당 기간에는 {len(df)}개의 봉만 있습니다. 기간을 늘리거나 지표 파라미터를 줄이세요."
            ),
        )

    risk_config = {**DEFAULT_RISK_CONFIG, "initial_capital": req.initial_capital}

    result = run_backtest_cached(
        df=df,
        strategy_cls=ConditionTreeStrategy,
        risk_config=risk_config,
        market=req.market,
        timeframe=req.timeframe,
        start=start_dt,
        end=end_dt,
        strategy_params={"buy_conditions": buy_dict, "sell_conditions": sell_dict},
    )
    return {"run_id": result["run_id"]}
```

- [x] **Step 5: 테스트 통과 확인**

Run: `pytest tests/test_backend.py -v`
Expected: 전체 PASS

- [x] **Step 6: 전체 스위트 확인**

Run: `pytest -v`
Expected: 전체 PASS(sweep/signals 관련 기존 테스트도 영향 없어야 함)

- [x] **Step 7: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "feat: rewrite POST /api/v1/backtests/run to use buy/sell condition trees and initial_capital"
```

---

### Task 7: `POST /api/v1/backtests/validate` (사전 검증)

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_backend.py`

**Interfaces:**
- Consumes: `_validate_backtest_request`(Task 6에서 이미 정의).
- Produces: `POST /api/v1/backtests/validate` → `200` + `{"valid": bool, "errors": list[str]}`. 캔들 데이터 조회까지 포함한 "완전한" 사전 검증을 위해, 구조적 검증을 통과하면 실제로 `get_candles()`를 호출해 데이터 충분성까지 확인한다(어차피 `run`을 이어서 호출하면 parquet 캐시로 즉시 응답되므로 중복 호출 비용은 낮다).

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_backend.py` 끝에 추가:

```python
def test_validate_reports_multiple_errors_at_once(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    resp = client.post(
        "/api/v1/backtests/validate",
        json=_run_request(
            buy_conditions={"type": "AND", "conditions": []},
            sell_conditions={"type": "AND", "conditions": []},
            start="2026-03-01",
            end="2026-01-01",
        ),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is False
    assert any("매수 조건" in e for e in body["errors"])
    assert any("매도 조건" in e for e in body["errors"])
    assert any("시작일" in e for e in body["errors"])


def test_validate_passes_for_well_formed_request(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch)

    resp = client.post("/api/v1/backtests/validate", json=_run_request())
    assert resp.status_code == 200
    assert resp.json() == {"valid": True, "errors": []}


def test_validate_flags_insufficient_candle_count(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    short_df = make_oscillating_df(n=5)
    _patch_get_candles(monkeypatch, df=short_df)

    long_period_buy = {
        "type": "AND",
        "conditions": [{"indicator": "SMA", "params": {"period": 200}, "operator": ">", "threshold": 0}],
    }
    resp = client.post("/api/v1/backtests/validate", json=_run_request(buy_conditions=long_period_buy))
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is False
    assert any("200" in e for e in body["errors"])
```

- [x] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_backend.py -k validate -v`
Expected: FAIL — `404 Not Found`

- [x] **Step 3: 구현 작성**

`backend/main.py`의 `run_backtest_endpoint` 함수 아래에 추가:

```python
@app.post("/api/v1/backtests/validate")
def validate_backtest_endpoint(req: RunBacktestRequest) -> dict:
    errors = _validate_backtest_request(req)
    if errors:
        return {"valid": False, "errors": errors}

    start_dt = datetime.strptime(req.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(req.end, "%Y-%m-%d").replace(
        hour=23, minute=59, second=59, tzinfo=timezone.utc
    )

    try:
        df = get_candles(req.market, req.timeframe, start_dt, end_dt)
    except (ValueError, RuntimeError) as exc:
        return {"valid": False, "errors": [str(exc)]}

    if df.empty:
        return {"valid": False, "errors": ["해당 기간에 캔들 데이터가 없습니다."]}

    buy_dict = req.buy_conditions.model_dump()
    sell_dict = req.sell_conditions.model_dump()
    required_bars = max(max_required_period(buy_dict), max_required_period(sell_dict))
    if len(df) < required_bars:
        return {
            "valid": False,
            "errors": [
                f"선택한 지표가 최소 {required_bars}개의 봉을 필요로 하지만, "
                f"해당 기간에는 {len(df)}개의 봉만 있습니다. 기간을 늘리거나 지표 파라미터를 줄이세요."
            ],
        }

    return {"valid": True, "errors": []}
```

- [x] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_backend.py -v`
Expected: 전체 PASS

- [x] **Step 5: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "feat: add POST /api/v1/backtests/validate pre-flight validation endpoint"
```

---

## 프런트엔드 후속 연동 체크리스트 (이번 계획 범위 밖 — 별도 작업으로 진행)

이번 계획은 백엔드만 다룬다. 아래는 위 엔드포인트들이 준비된 뒤 `PortSetupForm`/`StrategyConditionBuilder`를 실제로 연결하기 위해 필요한 프런트엔드 작업 목록이다(별도 스펙/플랜으로 다룰 것):

- `코인 선택` 드롭다운: 하드코딩된 `MARKETS = ['KRW-BTC', 'KRW-ETH']` 대신 `GET /api/v1/markets`를 호출해 채운다(요청 사항 2번, "바로 필요").
- `StrategyConditionBuilder`의 `INDICATOR_CATEGORIES` 하드코딩 대신 `GET /api/v1/indicators/catalog`를 호출해 지표 목록 + `description`/`example`을 채운다(요청 사항 1, 5번).
- 지표 select 옆에 `description`/`example`을 보여주는 툴팁 UI 추가(요청 사항 5번, 백엔드 데이터는 Task 5로 이미 준비됨).
- 매수/매도 조건 트리를 사람이 읽을 수 있는 한 줄 요약("(A and B) or (C and D)")으로 렌더링하는 함수 추가(요청 사항 6번) — 트리 구조만 있으면 되므로 순수 프런트엔드 작업, 백엔드 변경 불필요.
- `봉데이터 선택`(15분/30분/1시간/1일) → 백엔드 `timeframe` 문자열(`minutes15`/`minutes30`/`minutes60`/`days`) 매핑 유틸 추가.
- `백테스트 실행` 클릭 시: 먼저 `POST /api/v1/backtests/validate` 호출 → `valid: false`면 사유 목록을 팝업(Dialog)으로 표시(요청 사항 7번) → `valid: true`면 `POST /api/v1/backtests/run` 호출 후 결과 페이지로 이동.
- `운용기간` UI가 좁은 화면에서 줄바꿈되는 문제(요청 사항 4번) 확인 — 현재 `flex flex-wrap`으로 되어 있어 컨테이너 폭이 좁으면 자동 줄바꿈됨. 필요하면 `기본 조건` 그리드의 운용기간 칼럼 비율을 더 늘리거나(`grid-cols-[1fr_1fr_2fr]` → 비율 조정), 날짜/시간 입력 폭을 줄인다.

## 향후 확장 메모 (이번 작업에서 구현하지 않음)

- **전략(지표) 추가 절차**: 새 지표를 추가하려면 (1) `engine/indicators/`에 `create_xxx()` 함수 작성 (2) `INDICATOR_FACTORY`에 등록 (3) `backend/main.py`의 `INDICATOR_CATALOG`에 메타데이터(설명/예시) 추가 (4) 다중 라인 지표라면 `engine/condition_tree.get_indicator_value()`에 분기 추가. 프런트엔드는 `/api/v1/indicators/catalog`를 동적으로 읽도록 바뀌고 나면 코드 수정 없이 새 지표를 바로 노출할 수 있다.
- **코인 다중 선택**: `RunBacktestRequest.market: str` → `markets: list[str]`로 바꾸고, 백엔드에서 마켓별로 개별 실행 후 결과를 묶어 반환하거나 프런트에서 마켓별로 별도 요청을 보내는 두 가지 방식이 가능하다. 어느 쪽이든 조건 트리/지표 엔진은 그대로 재사용 가능 — market 파라미터만 여러 번 순회하면 됨.
- **코인 세그먼트 분석/라벨링**: `GET /api/v1/markets` 응답에 `labels: list[str]` 필드를 추가하고, 라벨을 만드는 별도 배치 작업(예: 거래대금/변동성/섹터 기준 분류)이 필요하다. 프런트는 라벨별 필터/일괄 선택 UI를 추가한다. 이 항목은 자체 스펙이 필요할 만큼 크므로 별도로 브레인스토밍할 것.
- **외부 데이터 지표 재도입**(공포/탐욕 지수, BTC 도미넌스, 김치프리미엄 등): `backtesting_1`의 `sentiment_service.py`/`external_data_service.py`, `engine/runner.PandasDataWithExtra`(이미 이 프로젝트에도 포팅되어 있음, `extra` 컬럼 피드)를 참고해 별도 데이터 소스 연동 작업으로 진행한다.

## Self-Review 결과

- **스펙 커버리지**: 사용자가 명시한 7개 항목 중 백엔드가 담당해야 할 부분(1번 확장성→지표 카탈로그+등록 절차, 2번 KRW 마켓 전체 목록, 7번 사전 검증)은 Task 1~7에서 직접 구현. 3번(다중선택/라벨링)은 명시적으로 이번 범위 밖으로 결정되어 "향후 확장" 절에 기록. 4·5·6번(UI 한 줄 정리, 툴팁, 조건 트리 요약)은 프런트엔드 전용이거나 이미 백엔드가 필요한 데이터(카탈로그의 description/example)를 제공하므로 "프런트엔드 후속 연동 체크리스트"에 명시.
- **캐시 키 안전성**: Task 3에서 정적 클래스 설계로 `inspect.getsource()` 문제를 회피했음을 확인하고, Task 3 Step 5에서 캐시 키가 트리 내용에 따라 실제로 달라지는지 수동 검증 단계를 포함시켰다. Task 6의 `test_run_backtest_uses_requested_initial_capital`도 같은 종류의 캐시 키 회귀를 잡는다.
- **기존 시스템과의 경계**: `signals.py`/`engine/strategies.py`/`engine/sweep.py`/히트맵·랭킹·추이 탭은 어떤 Task에서도 수정하지 않음을 각 Task의 Global Constraints와 Interfaces에서 재확인.
- **타입 일관성**: `ConditionBlockRequest`/`ConditionGroupRequest`(Task 6)의 필드명(`indicator`/`params`/`operator`/`threshold`, `type`/`conditions`)이 프런트엔드 `frontend/lib/types/strategy.ts`의 `ConditionBlock`/`ConditionGroup`과 정확히 일치함을 확인.
