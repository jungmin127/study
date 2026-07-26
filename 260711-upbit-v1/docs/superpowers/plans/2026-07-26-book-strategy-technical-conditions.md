# 책 전략 이식 - 기술적 조건 추가 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 조건식 시스템에 세 가지 새 지표를 추가한다 — 보유기간 매도(`HOLDING_PERIOD_BARS`), KRW-BTC 추세 필터(`MARKET_TREND`), 모멘텀/역추세(`MOMENTUM_PCT`). 가치투자 백테스트 서적(`docs/book_ref/`)과 퀀트 단타 전략 서적(`docs/book_ref_v2/`)을 조사한 결과, 재무제표/기관수급 기반 팩터는 코인에 이식 불가능하지만 이 세 가지 기술적 규칙은 이식 가능하다고 판단했다.

**Architecture:** `HOLDING_PERIOD_BARS`는 기존 `STOP_LOSS_PCT`/`TAKE_PROFIT_PCT`와 같은 "포지션 상태 기반" 패턴을 그대로 확장한다(`engine/condition_tree.py`의 `eval_group`, `engine/condition_strategy.py`의 봉 번호 추적). `MARKET_TREND`는 `engine/runner.py`에 이미 있지만 아무도 호출하지 않던 `PandasDataWithExtra`/`extra_column` 확장포인트를 되살려, KRW-BTC 캔들을 별도 데이터 라인으로 병합하고 그 위에서 `종가 - SMA` 지표를 계산한다. 백엔드는 조건식에 `MARKET_TREND`가 있을 때만 KRW-BTC 캔들을 추가로 조회한다. `MOMENTUM_PCT`는 대상 코인 자신의 종가만으로 계산되는 일반 지표라(외부 데이터도 포지션 상태도 불필요) `RSI`/`SMA`와 동일한 방식으로 `INDICATOR_FACTORY`에 등록하기만 하면 된다 — backtrader 내장 `ROC100`을 그대로 사용한다.

**Tech Stack:** Python 3.11 / backtrader / pandas / FastAPI / Next.js 14 App Router / TypeScript.

## Global Constraints

- `HOLDING_PERIOD_BARS`의 단위는 봉(bar) 개수다 — 캘린더 일수가 아니다.
- `HOLDING_PERIOD_BARS`, `MARKET_TREND`, `MOMENTUM_PCT` 모두 카탈로그 등록만으로 프론트 조건 빌더 UI에 자동 노출된다(카탈로그를 순회하는 기존 구조 덕분) — 프론트 코드 변경은 추천 임계값/제로크로스 매핑 추가뿐이다.
- `MOMENTUM_PCT`는 `sellOnly`/`fixedOperator` 없이 매수/매도 조건 양쪽에서 자유롭게 쓴다 — 양수 임계값+`>`는 모멘텀(상승 포착), 음수 임계값+`<`는 역추세(급락 포착)로, 지표 하나로 책의 두 전략을 표현한다. `MARKET_TREND`처럼 외부 데이터(`extra_column`)나 포지션 상태가 필요 없는 순수 가격 지표다.
- `MARKET_TREND`은 KRW-BTC를 시장 대표 지표로 **하드코딩**한다 — 파라미터화하지 않는다.
- `MARKET_TREND`이 조건식에 실제로 있을 때만 KRW-BTC 캔들을 추가 조회한다. 대상 마켓이 KRW-BTC 자신이면 별도 조회 없이 자기 종가를 재사용한다.
- `GET /api/v1/backtests/validate`는 BTC 데이터를 별도로 조회하지 않는다(의도적 설계 — `max_required_period` 검증은 조건식의 숫자 파라미터만 보므로 이미 충분).
- `engine/runner.py`는 코드 변경이 필요 없다 — `PandasDataWithExtra`/`extra_column`이 이미 완성되어 있다. 이 플랜의 Task 3은 그 기존 코드에 대한 테스트만 추가한다.
- 기존 파일의 스타일(`from __future__ import annotations`, 타입힌트, docstring 관례)을 따른다.
- 프론트엔드에는 테스트 러너가 없다(`package.json`에 test 스크립트 없음) — 프론트 변경은 `npx tsc --noEmit`과 수동 브라우저 확인으로 검증한다.

---

## File Structure

- **Modify** `engine/condition_tree.py` — `POSITION_RELATIVE_INDICATORS`에 `HOLDING_PERIOD_BARS` 추가, `eval_group`에 `position_holding_bars` 파라미터 추가, `requires_market_data()` 헬퍼 추가.
- **Modify** `engine/condition_strategy.py` — 진입 봉 번호 추적, `position_holding_bars` 계산 및 전달.
- **Create** `engine/indicators/market.py` — `create_market_trend()` (KRW-BTC 종가 - SMA, `self.data.extra` 라인에서 계산).
- **Modify** `engine/indicators/momentum.py` — `create_momentum_pct()` (backtrader 내장 `ROC100`).
- **Modify** `engine/indicators/__init__.py` — `MARKET_TREND`, `MOMENTUM_PCT` 등록.
- **Modify** `engine/cache.py` — `run_backtest_cached()`에 `extra_column` 파라미터 추가, `run_backtest()`로 전달.
- **Modify** `backend/main.py` — `INDICATOR_CATALOG`에 3개 항목 추가, `requires_market_data` import, `run_backtest_endpoint`에 KRW-BTC 캔들 조회/병합 로직 추가.
- **Modify** `frontend/components/StrategyConditionBuilder.tsx` — `ZERO_CROSS_INDICATORS`에 `MARKET_TREND`/`MOMENTUM_PCT`, `POSITION_RELATIVE_DEFAULTS`에 `HOLDING_PERIOD_BARS` 추가.
- **Modify** `tests/test_condition_tree.py`, `tests/test_condition_strategy.py`, `tests/test_indicators.py`, `tests/test_runner.py`, `tests/test_cache.py`, `tests/test_backend.py` — 위 변경에 대한 테스트 추가.

---

### Task 1: `engine/condition_tree.py` — `HOLDING_PERIOD_BARS` + `requires_market_data`

**Files:**
- Modify: `engine/condition_tree.py:23`(`POSITION_RELATIVE_INDICATORS` 정의), `:77-109`(`eval_group` 함수 전체), `:139-149`(`__all__`), 파일 끝(`requires_market_data` 추가 — `max_required_period` 함수 뒤, `__all__` 앞)
- Test: `tests/test_condition_tree.py`

**Interfaces:**
- Consumes: 없음(독립 모듈).
- Produces:
  - `eval_group(group, indicators, position_return_pct=None, position_holding_bars=None) -> bool` — 기존 시그니처에 `position_holding_bars: int | None = None` 파라미터 추가.
  - `requires_market_data(group: dict) -> bool` — 조건 트리에 `MARKET_TREND`가 있으면 True.
  - `POSITION_RELATIVE_INDICATORS = {"STOP_LOSS_PCT", "TAKE_PROFIT_PCT", "HOLDING_PERIOD_BARS"}`.

- [ ] **Step 1: Write the failing tests**

`tests/test_condition_tree.py` 상단 import를 아래로 교체:

```python
from engine.condition_tree import (
    apply_operator,
    collect_blocks,
    eval_group,
    find_unknown_indicators,
    is_empty,
    max_required_period,
    requires_market_data,
)
```

파일 맨 아래에 추가:

```python
def test_eval_group_evaluates_holding_period_bars_against_position_state():
    tree = {
        "type": "AND",
        "conditions": [{"indicator": "HOLDING_PERIOD_BARS", "params": {}, "operator": ">=", "threshold": 5}],
    }
    assert eval_group(tree, {}, position_holding_bars=5) is True
    assert eval_group(tree, {}, position_holding_bars=4) is False


def test_eval_group_holding_period_bars_false_without_position():
    tree = {
        "type": "AND",
        "conditions": [{"indicator": "HOLDING_PERIOD_BARS", "params": {}, "operator": ">=", "threshold": 5}],
    }
    assert eval_group(tree, {}) is False


def test_find_unknown_indicators_allows_holding_period_bars():
    tree = {
        "type": "AND",
        "conditions": [{"indicator": "HOLDING_PERIOD_BARS", "params": {}, "operator": ">=", "threshold": 5}],
    }
    assert find_unknown_indicators(tree) == []


def test_requires_market_data_true_when_market_trend_present():
    tree = {
        "type": "AND",
        "conditions": [{"indicator": "MARKET_TREND", "params": {"period": 10}, "operator": "<", "threshold": 0}],
    }
    assert requires_market_data(tree) is True


def test_requires_market_data_false_when_market_trend_absent():
    tree = {"type": "AND", "conditions": [{"indicator": "RSI", "params": {}, "operator": "<", "threshold": 30}]}
    assert requires_market_data(tree) is False


def test_requires_market_data_checks_nested_groups():
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
    assert requires_market_data(tree) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_condition_tree.py -v`
Expected: `ImportError: cannot import name 'requires_market_data'` (나머지 신규 테스트도 같은 이유로 수집 단계에서 실패)

- [ ] **Step 3: Write minimal implementation**

`engine/condition_tree.py:23`을 아래로 교체:

```python
POSITION_RELATIVE_INDICATORS = {"STOP_LOSS_PCT", "TAKE_PROFIT_PCT", "HOLDING_PERIOD_BARS"}
```

`engine/condition_tree.py:77-109`의 `eval_group` 함수 전체를 아래로 교체:

```python
def eval_group(
    group: dict,
    indicators: dict[str, bt.Indicator],
    position_return_pct: float | None = None,
    position_holding_bars: int | None = None,
) -> bool:
    """ConditionGroup을 재귀적으로 평가해 bool 반환. indicators는 indicator_key -> bt.Indicator 매핑.
    position_return_pct는 포지션 진입가 대비 현재 수익률(%)로 STOP_LOSS_PCT/TAKE_PROFIT_PCT 평가에,
    position_holding_bars는 포지션 보유 봉수로 HOLDING_PERIOD_BARS 평가에 쓰인다. 포지션이 없어
    해당 값이 None이면 그 블록은 False로 처리한다."""
    group_type = group.get("type", "AND")
    conditions = group.get("conditions", [])

    if not conditions:
        return False

    results: list[bool] = []
    for item in conditions:
        if "indicator" in item:
            if item["indicator"] == "HOLDING_PERIOD_BARS":
                if position_holding_bars is None:
                    results.append(False)
                else:
                    results.append(
                        apply_operator(position_holding_bars, item["operator"], float(item["threshold"]))
                    )
                continue
            if item["indicator"] in POSITION_RELATIVE_INDICATORS:
                if position_return_pct is None:
                    results.append(False)
                else:
                    results.append(apply_operator(position_return_pct, item["operator"], float(item["threshold"])))
                continue
            key = indicator_key(item["indicator"], item.get("params", {}))
            if key not in indicators:
                results.append(False)
                continue
            value = get_indicator_value(item["indicator"], indicators[key])
            results.append(apply_operator(value, item["operator"], float(item["threshold"])))
        elif "type" in item:
            results.append(eval_group(item, indicators, position_return_pct, position_holding_bars))

    return all(results) if group_type == "AND" else any(results)
```

`engine/condition_tree.py`에서 `max_required_period` 함수 정의 바로 뒤(파일 끝, `__all__` 앞)에 추가:

```python
def requires_market_data(group: dict) -> bool:
    """조건 트리가 MARKET_TREND처럼 대상 마켓이 아닌 외부 마켓(KRW-BTC) 데이터가 필요한
    지표를 포함하는지 확인한다. backend가 이 값을 보고 KRW-BTC 캔들을 추가로 조회할지 정한다."""
    return any(b["indicator"] == "MARKET_TREND" for b in collect_blocks(group))
```

`engine/condition_tree.py:139-149`의 `__all__`을 아래로 교체:

```python
__all__ = [
    "POSITION_RELATIVE_INDICATORS",
    "indicator_key",
    "collect_blocks",
    "get_indicator_value",
    "apply_operator",
    "eval_group",
    "find_unknown_indicators",
    "is_empty",
    "max_required_period",
    "requires_market_data",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_condition_tree.py -v`
Expected: PASS (기존 9개 + 신규 6개 = 15개)

- [ ] **Step 5: Commit**

```bash
git add engine/condition_tree.py tests/test_condition_tree.py
git commit -m "feat: 보유기간 매도(HOLDING_PERIOD_BARS)와 시장 지표 감지 헬퍼 추가"
```

---

### Task 2: `engine/condition_strategy.py` — 진입 봉 번호 추적

**Files:**
- Modify: `engine/condition_strategy.py`(전체 — 26줄 → 33줄 규모)
- Test: `tests/test_condition_strategy.py`

**Interfaces:**
- Consumes: `engine.condition_tree.eval_group(group, indicators, position_return_pct=None, position_holding_bars=None)` (Task 1).
- Produces: 변경 없음(`ConditionTreeStrategy` 클래스 자체의 공개 인터페이스는 동일).

- [ ] **Step 1: Write the failing test**

`tests/test_condition_strategy.py` 파일 맨 아래에 추가:

```python
def test_holding_period_bars_forces_exit_after_n_bars():
    buy = {"type": "AND", "conditions": [{"indicator": "SMA", "params": {"period": 1}, "operator": ">", "threshold": 0}]}  # 항상 참
    sell = {"type": "AND", "conditions": [{"indicator": "HOLDING_PERIOD_BARS", "params": {}, "operator": ">=", "threshold": 3}]}
    result = _run(buy, sell)
    assert len(result["trades"]) > 10
    assert all(t["holdingPeriod"] <= 5 for t in result["trades"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_condition_strategy.py::test_holding_period_bars_forces_exit_after_n_bars -v`
Expected: FAIL — `position_holding_bars`가 항상 `None`으로 전달되므로 `HOLDING_PERIOD_BARS` 조건이 절대 참이 되지 않아 포지션이 청산되지 않고, 마지막에 강제청산(`forceClosed=True`) 거래 1건만 생기며 `holdingPeriod`가 300에 가까운 큰 값이 됨 → `len(result["trades"]) > 10` 단언 실패

- [ ] **Step 3: Write minimal implementation**

`engine/condition_strategy.py` 전체를 아래로 교체:

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

from engine.condition_tree import POSITION_RELATIVE_INDICATORS, collect_blocks, eval_group, indicator_key
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
        self._entry_bar: int | None = None

        for block in collect_blocks(self._buy_cond):
            self._ensure_indicator(self._buy_inds, block)
        for block in collect_blocks(self._sell_cond):
            self._ensure_indicator(self._sell_inds, block)

    def _ensure_indicator(self, store: dict[str, bt.Indicator], block: dict) -> None:
        if block["indicator"] in POSITION_RELATIVE_INDICATORS:
            return
        key = indicator_key(block["indicator"], block.get("params", {}))
        if key in store:
            return
        create_fn = INDICATOR_FACTORY.get(block["indicator"])
        if create_fn is None:
            raise ValueError(f"알 수 없는 지표: {block['indicator']}")
        store[key] = create_fn(self.data, **block.get("params", {}))

    def next(self) -> None:
        if not self.position:
            self._entry_bar = None
            if eval_group(self._buy_cond, self._buy_inds):
                self.buy()
        else:
            if self._entry_bar is None:
                self._entry_bar = len(self)
            entry_price = self.position.price
            position_return_pct = (
                (self.data.close[0] - entry_price) / entry_price * 100 if entry_price else None
            )
            position_holding_bars = len(self) - self._entry_bar
            if eval_group(
                self._sell_cond,
                self._sell_inds,
                position_return_pct=position_return_pct,
                position_holding_bars=position_holding_bars,
            ):
                self.sell()


__all__ = ["ConditionTreeStrategy"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_condition_strategy.py -v`
Expected: PASS (기존 5개 + 신규 1개 = 6개)

- [ ] **Step 5: Commit**

```bash
git add engine/condition_strategy.py tests/test_condition_strategy.py
git commit -m "feat: ConditionTreeStrategy가 보유 봉수를 추적해 HOLDING_PERIOD_BARS를 지원하도록 변경"
```

---

### Task 3: `engine/runner.py`의 기존 `extra_column` 확장포인트 검증

**Files:**
- Test: `tests/test_runner.py` (신규 테스트만 추가 — `engine/runner.py`는 코드 변경 없음)

**Interfaces:**
- Consumes: `engine.runner.run_backtest(df, strategy_cls, risk_config, strategy_params=None, extra_column=None)` (기존, 변경 없음).
- Produces: 없음(검증용 테스트만).

- [ ] **Step 1: Write the failing test**

`tests/test_runner.py` 파일 맨 아래에 추가:

```python
def test_run_backtest_with_extra_column_exposes_data_extra_line():
    df = _make_synthetic_df()
    df["market_close"] = [50000 + i * 10 for i in range(len(df))]

    captured: list[float] = []

    class _CapturesExtraLine(bt.Strategy):
        def next(self):
            captured.append(float(self.data.extra[0]))

    run_backtest(
        df=df,
        strategy_cls=_CapturesExtraLine,
        risk_config={
            "initial_capital": 10000,
            "commission_rate": 0.001,
            "position_sizing": "percent",
            "position_size": 100,
        },
        extra_column="market_close",
    )

    assert captured[0] == 50000.0
    assert captured[-1] == 50000 + (len(df) - 1) * 10
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_runner.py::test_run_backtest_with_extra_column_exposes_data_extra_line -v`
Expected: 이 테스트는 기존 코드가 이미 올바르다면 곧바로 PASS해야 한다. 만약 FAIL한다면 `engine/runner.py`의 `extra_column`/`PandasDataWithExtra` 처리(파일 상단 `PandasDataWithExtra` 클래스, `run_backtest` 함수의 `if extra_column and extra_column in df_bt.columns:` 분기)에 실제 버그가 있다는 뜻이므로, 그 지점을 고쳐서 통과시킨다. (Global Constraints에 따르면 이 코드는 이미 완성되어 있어야 하지만, 실제로 호출된 적이 없으므로 이 스텝에서 처음 검증한다.)

- [ ] **Step 3: (조건부) 버그가 있었다면 최소 수정, 없었다면 스킵**

Step 2에서 이미 PASS했다면 이 스텝은 건너뛴다.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_runner.py -v`
Expected: PASS (기존 2개 + 신규 1개 = 3개)

- [ ] **Step 5: Commit**

```bash
git add tests/test_runner.py
git commit -m "test: 기존 extra_column 확장포인트에 대한 회귀 테스트 추가"
```

---

### Task 4: `engine/indicators/market.py` — `MARKET_TREND` 지표

**Files:**
- Create: `engine/indicators/market.py`
- Modify: `engine/indicators/__init__.py`
- Test: `tests/test_indicators.py:5`(import), 그 외 테스트 추가

**Interfaces:**
- Consumes: `bt.feeds.PandasData`(`.extra` 라인 — Task 3에서 검증된 `PandasDataWithExtra`가 제공).
- Produces: `create_market_trend(data: bt.feeds.PandasData, **params) -> bt.Indicator`, `INDICATOR_FACTORY["MARKET_TREND"]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_indicators.py:5`(`from engine.runner import run_backtest`)를 아래로 교체:

```python
from engine.runner import PandasDataWithExtra, run_backtest
```

`test_all_registered_indicators_produce_values` 함수를 아래로 교체(`MARKET_TREND`는 `data.extra` 라인이 있는 피드가 필요해 일반 프로브로는 검증할 수 없으므로 제외하고, 아래에서 전용 테스트로 검증):

```python
def test_all_registered_indicators_produce_values():
    for name in INDICATOR_FACTORY:
        if name == "MARKET_TREND":
            continue  # extra 데이터 라인이 필요 — test_market_trend_matches_manual_close_minus_sma_of_extra_line 참고
        values = _run_probe(name, {})
        assert len(values) > 0, f"{name} 지표가 값을 하나도 생성하지 못함"
```

파일 맨 아래에 추가:

```python
def _run_probe_with_extra(indicator: str, params: dict) -> list[float]:
    df = make_oscillating_df()
    df["market_close"] = df["close"] * 2 + 1000  # 대상 마켓과 스케일이 다른 별도 시세임을 검증하기 위해 배율을 둠
    df_bt = df.set_index("candle_time")
    df_bt.index = df_bt.index.tz_localize(None)
    df_bt = df_bt.rename(columns={"market_close": "extra"})
    cerebro = bt.Cerebro()
    cerebro.adddata(
        PandasDataWithExtra(
            dataname=df_bt, open="open", high="high", low="low", close="close",
            volume="volume", openinterest=-1, extra="extra",
        )
    )
    cerebro.addstrategy(_ProbeStrategy, indicator=indicator, indicator_params=params)
    results = cerebro.run()
    return results[0].seen_values


def test_market_trend_matches_manual_close_minus_sma_of_extra_line():
    values = _run_probe_with_extra("MARKET_TREND", {"period": 5})
    df = make_oscillating_df()
    market_close = df["close"] * 2 + 1000
    manual = (market_close - market_close.rolling(5).mean()).iloc[-1]
    assert abs(values[-1] - manual) < 1e-6
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_indicators.py -v`
Expected: `test_market_trend_matches_manual_close_minus_sma_of_extra_line` FAIL with `KeyError: 'MARKET_TREND'` (아직 `INDICATOR_FACTORY`에 없음)

- [ ] **Step 3: Write minimal implementation**

`engine/indicators/market.py` 신규 생성:

```python
"""
engine/indicators/market.py

대상 코인 자체가 아니라 시장 전체 추세를 반영하는 지표. engine.runner의
PandasDataWithExtra가 채워주는 self.data.extra 라인(백엔드가 KRW-BTC 종가를 병합해 넣는다)
에서 계산한다.
"""
from __future__ import annotations

import backtrader as bt


def create_market_trend(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 10))
    market_close = data.extra
    sma = bt.indicators.SMA(market_close, period=period)
    return market_close - sma
```

`engine/indicators/__init__.py` 전체를 아래로 교체:

```python
from __future__ import annotations

from .market import create_market_trend
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
    "MARKET_TREND": create_market_trend,
}

__all__ = ["INDICATOR_FACTORY"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_indicators.py -v`
Expected: PASS (기존 2개 + 신규 1개 = 3개)

- [ ] **Step 5: Commit**

```bash
git add engine/indicators/market.py engine/indicators/__init__.py tests/test_indicators.py
git commit -m "feat: KRW-BTC 추세 필터(MARKET_TREND) 지표 추가"
```

---

### Task 5: `engine/indicators/momentum.py` — `MOMENTUM_PCT` 지표 (모멘텀/역추세)

**Files:**
- Modify: `engine/indicators/momentum.py`(파일 끝에 함수 추가)
- Modify: `engine/indicators/__init__.py`(Task 4에서 만든 버전 위에 이어서 수정)
- Test: `tests/test_indicators.py`

**Interfaces:**
- Consumes: 없음(대상 코인 자신의 `data.close`만 사용 — `MARKET_TREND`와 달리 `extra` 라인도 포지션 상태도 불필요).
- Produces: `create_momentum_pct(data: bt.feeds.PandasData, **params) -> bt.Indicator`, `INDICATOR_FACTORY["MOMENTUM_PCT"]`.

- [ ] **Step 1: Write the failing test**

`tests/test_indicators.py` 파일 맨 아래에 추가:

```python
def test_momentum_pct_matches_manual_pct_change_over_period():
    values = _run_probe("MOMENTUM_PCT", {"period": 5})
    df = make_oscillating_df()
    manual = (df["close"].iloc[-1] - df["close"].iloc[-6]) / df["close"].iloc[-6] * 100
    assert abs(values[-1] - manual) < 1e-6
```

(이 지표는 `MARKET_TREND`와 달리 `data.extra` 라인이 필요 없으므로, 기존 `test_all_registered_indicators_produce_values` 루프에 별도 예외 처리 없이 자연스럽게 포함되어 함께 검증된다.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_indicators.py -v`
Expected: `test_momentum_pct_matches_manual_pct_change_over_period` FAIL with `KeyError: 'MOMENTUM_PCT'`. `test_all_registered_indicators_produce_values`는 아직 `MOMENTUM_PCT`가 `INDICATOR_FACTORY`에 없으므로 이 시점에는 영향받지 않는다(루프가 기존 지표만 순회).

- [ ] **Step 3: Write minimal implementation**

`engine/indicators/momentum.py` 파일 끝에 추가:

```python
def create_momentum_pct(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 5))
    return bt.indicators.ROC100(data, period=period)
```

`engine/indicators/__init__.py` 전체를 아래로 교체(Task 4의 버전에 `create_momentum_pct` import와 `MOMENTUM_PCT` 등록만 추가):

```python
from __future__ import annotations

from .market import create_market_trend
from .momentum import (
    create_cci,
    create_macd_line,
    create_macd_signal,
    create_momentum_pct,
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
    "MARKET_TREND": create_market_trend,
    "MOMENTUM_PCT": create_momentum_pct,
}

__all__ = ["INDICATOR_FACTORY"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_indicators.py -v`
Expected: PASS (Task 4 이후 3개 + 신규 1개 = 4개)

- [ ] **Step 5: Commit**

```bash
git add engine/indicators/momentum.py engine/indicators/__init__.py tests/test_indicators.py
git commit -m "feat: 모멘텀/역추세 지표(MOMENTUM_PCT) 추가"
```

---

### Task 6: `engine/cache.py` — `extra_column` 전달

**Files:**
- Modify: `engine/cache.py:185-280`(`run_backtest_cached` 함수)
- Test: `tests/test_cache.py`

**Interfaces:**
- Consumes: `engine.runner.run_backtest(df, strategy_cls, risk_config, strategy_params=None, extra_column=None)` (기존).
- Produces: `run_backtest_cached(..., extra_column: str | None = None) -> dict` — 기존 시그니처에 파라미터 추가.

- [ ] **Step 1: Write the failing test**

`tests/test_cache.py`의 기존 3개 테스트 더블 시그니처를 모두 아래처럼 `extra_column=None`을 받도록 수정한다(파일 내 `def fake_run_backtest(df, strategy_cls, risk_config, strategy_params=None):`가 2곳, `def failing_run_backtest(df, strategy_cls, risk_config, strategy_params=None):`가 1곳 — 총 3곳):

```python
def fake_run_backtest(df, strategy_cls, risk_config, strategy_params=None, extra_column=None):
```

```python
def failing_run_backtest(df, strategy_cls, risk_config, strategy_params=None, extra_column=None):
```

파일 맨 아래에 추가:

```python
def test_run_backtest_cached_passes_extra_column_through(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    captured: dict = {}

    def fake_run_backtest(df, strategy_cls, risk_config, strategy_params=None, extra_column=None):
        captured["extra_column"] = extra_column
        return {"equity_curve": [], "trades": [], "final_value": 10000.0, "sharpe": None, "max_drawdown": None}

    monkeypatch.setattr(cache_module, "run_backtest", fake_run_backtest)

    run_backtest_cached(
        df=_synthetic_df(),
        strategy_cls=_StrategyA,
        risk_config={"initial_capital": 10000},
        market="KRW-BTC",
        timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        extra_column="market_close",
    )

    assert captured["extra_column"] == "market_close"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cache.py::test_run_backtest_cached_passes_extra_column_through -v`
Expected: FAIL with `TypeError: run_backtest_cached() got an unexpected keyword argument 'extra_column'`

- [ ] **Step 3: Write minimal implementation**

`engine/cache.py`의 `run_backtest_cached` 함수 시그니처(`def run_backtest_cached(` 로 시작하는 블록)를 아래로 교체:

```python
def run_backtest_cached(
    df: pd.DataFrame,
    strategy_cls: type[bt.Strategy],
    risk_config: dict,
    market: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    strategy_params: dict | None = None,
    title: str | None = None,
    description: str | None = None,
    extra_column: str | None = None,
) -> dict:
```

같은 함수 안의 `result = run_backtest(df, strategy_cls, risk_config, strategy_params)` 줄을 아래로 교체:

```python
    result = run_backtest(df, strategy_cls, risk_config, strategy_params, extra_column=extra_column)
```

(함수의 나머지 부분 — docstring, 캐시 조회, `save_result` 호출부 — 은 그대로 둔다.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cache.py -v`
Expected: PASS (기존 모든 테스트 + 신규 1개, 3개의 수정된 fake 함수 포함)

- [ ] **Step 5: Commit**

```bash
git add engine/cache.py tests/test_cache.py
git commit -m "feat: run_backtest_cached가 extra_column을 run_backtest로 전달하도록 변경"
```

---

### Task 7: `backend/main.py` — 카탈로그 등록 + KRW-BTC 캔들 병합

**Files:**
- Modify: `backend/main.py:29`(import), `backend/main.py:200-201`(`INDICATOR_CATALOG` 끝에 3개 항목 추가), `backend/main.py:454-501`(`run_backtest_endpoint`)
- Test: `tests/test_backend.py`

**Interfaces:**
- Consumes: `engine.condition_tree.requires_market_data(group: dict) -> bool` (Task 1), `engine.cache.run_backtest_cached(..., extra_column=None)` (Task 6), `upbit_data_service.get_candles(market, timeframe, start, end) -> pd.DataFrame` (기존).
- Produces: `GET /api/v1/indicators/catalog` 응답에 `HOLDING_PERIOD_BARS`, `MARKET_TREND`, `MOMENTUM_PCT` 포함.

- [ ] **Step 1: Write the failing tests**

`tests/test_backend.py` 파일 맨 아래에 추가:

```python
def test_indicator_catalog_includes_new_indicators(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    resp = client.get("/api/v1/indicators/catalog")
    values = {item["value"] for item in resp.json()}

    assert "HOLDING_PERIOD_BARS" in values
    assert "MARKET_TREND" in values
    assert "MOMENTUM_PCT" in values


def test_run_backtest_fetches_btc_candles_when_market_trend_used_on_other_market(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    calls: list[str] = []

    def _fake_get_candles(market, timeframe, start, end):
        calls.append(market)
        return make_oscillating_df()

    monkeypatch.setattr(backend_module, "get_candles", _fake_get_candles)

    buy = {"type": "AND", "conditions": [{"indicator": "MARKET_TREND", "params": {"period": 5}, "operator": "<", "threshold": 0}]}
    resp = client.post("/api/v1/backtests/run", json=_run_request(market="KRW-ETH", buy_conditions=buy))

    assert resp.status_code == 200
    assert calls == ["KRW-ETH", "KRW-BTC"]


def test_run_backtest_reuses_own_close_when_market_is_btc_itself(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    calls: list[str] = []

    def _fake_get_candles(market, timeframe, start, end):
        calls.append(market)
        return make_oscillating_df()

    monkeypatch.setattr(backend_module, "get_candles", _fake_get_candles)

    buy = {"type": "AND", "conditions": [{"indicator": "MARKET_TREND", "params": {"period": 5}, "operator": "<", "threshold": 0}]}
    resp = client.post("/api/v1/backtests/run", json=_run_request(market="KRW-BTC", buy_conditions=buy))

    assert resp.status_code == 200
    assert calls == ["KRW-BTC"]


def test_run_backtest_skips_btc_fetch_when_market_trend_not_used(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    calls: list[str] = []

    def _fake_get_candles(market, timeframe, start, end):
        calls.append(market)
        return make_oscillating_df()

    monkeypatch.setattr(backend_module, "get_candles", _fake_get_candles)

    resp = client.post("/api/v1/backtests/run", json=_run_request(market="KRW-ETH"))

    assert resp.status_code == 200
    assert calls == ["KRW-ETH"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_backend.py -k "catalog_includes_new_indicators or market_trend or skips_btc_fetch" -v`
Expected: `test_indicator_catalog_includes_new_indicators` FAIL(카탈로그에 아직 없음). 나머지 3개는 `MARKET_TREND`가 카탈로그에 없어 400(알 수 없는 지표) 또는 backtrader 실행 중 `AttributeError`로 실패.

- [ ] **Step 3: Write minimal implementation**

`backend/main.py:29`를 아래로 교체:

```python
from engine.condition_tree import find_unknown_indicators, is_empty, max_required_period, requires_market_data
```

`backend/main.py:200-201`(`TAKE_PROFIT_PCT` 항목의 닫는 `},` 바로 뒤, `]` 앞)에 추가:

```python
    {
        "value": "HOLDING_PERIOD_BARS", "label": "보유기간 (봉)", "category": "손익",
        "params": [], "sellOnly": True, "fixedOperator": ">=",
        "description": "캔들 지표가 아니라 포지션을 진입한 이후 지난 봉의 개수입니다. 이 값이 임계값 이상이 되면 매도합니다(캘린더 일수가 아니라 봉 개수 기준).",
        "example": "임계값 20을 넣으면, 진입 후 20개 봉이 지나는 순간(15분봉이면 5시간, 일봉이면 20일) 매도 조건이 참이 됩니다.",
    },
    {
        "value": "MARKET_TREND", "label": "시장 추세 (BTC 종가-이동평균)", "category": "시장 심리",
        "params": [{"key": "period", "label": "기간", "default": 10}],
        "description": "대상 코인이 아니라 KRW-BTC 종가에서 KRW-BTC의 이동평균을 뺀 값입니다. 알트코인이 BTC 추세를 따라가는 경향을 이용해, 시장 전체가 약세일 때 매수를 쉬거나 매도하는 필터로 씁니다.",
        "example": "period=10이고 연산자 <, 임계값 0이면: KRW-BTC 종가가 자신의 10봉 이동평균보다 낮을 때(BTC가 하락 추세일 때) 조건이 참이 됩니다.",
    },
    {
        "value": "MOMENTUM_PCT", "label": "모멘텀 (N봉 전 대비 등락률 %)", "category": "추세",
        "params": [{"key": "period", "label": "기간", "default": 5}],
        "description": "N봉 전 종가 대비 현재 종가의 등락률(%)입니다. 양수 임계값이면 최근 상승 흐름(모멘텀)을, 음수 임계값이면 최근 급락(눌림목)을 포착하는 조건으로 쓸 수 있습니다.",
        "example": "period=5, 연산자 >, 임계값 3이면: 5봉 전보다 종가가 3% 이상 오른 상태(모멘텀 진입)를 포착합니다. period=5, 연산자 <, 임계값 -5면: 5봉 전보다 5% 이상 급락한 상태(눌림목/역추세 진입)를 포착합니다.",
    },
```

`backend/main.py:454-501`의 `run_backtest_endpoint` 함수를 아래로 교체:

```python
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

    extra_column = None
    if requires_market_data(buy_dict) or requires_market_data(sell_dict):
        if req.market == "KRW-BTC":
            df = df.assign(market_close=df["close"])
        else:
            btc_df = get_candles("KRW-BTC", req.timeframe, start_dt, end_dt)
            df = df.merge(
                btc_df[["candle_time", "close"]].rename(columns={"close": "market_close"}),
                on="candle_time",
                how="left",
            )
            df["market_close"] = df["market_close"].ffill().bfill()
        extra_column = "market_close"

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
        title=req.title,
        description=req.description,
        extra_column=extra_column,
    )
    return {"run_id": result["run_id"]}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_backend.py -v`
Expected: PASS (전체 기존 테스트 포함)

- [ ] **Step 5: Commit**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "feat: 보유기간/BTC 추세/모멘텀 지표를 카탈로그에 등록하고 백테스트 실행 시 BTC 캔들 병합"
```

---

### Task 8: 프론트엔드 조건 빌더 기본값

**Files:**
- Modify: `frontend/components/StrategyConditionBuilder.tsx:50-56`

**Interfaces:**
- Consumes: 없음(기존 `IndicatorCatalogItem[]`를 그대로 순회하는 구조 — 카탈로그에 새 항목이 추가되면 자동으로 UI에 나타남).
- Produces: 없음(내부 추천값 매핑 갱신).

- [ ] **Step 1: `frontend/components/StrategyConditionBuilder.tsx:50-56`을 아래로 교체**

```tsx
const ZERO_CROSS_INDICATORS = new Set(['MACD_line', 'MACD_signal', 'MARKET_TREND', 'MOMENTUM_PCT']);
const PRICE_SCALE_INDICATORS = new Set(['SMA', 'EMA', 'WMA', 'BB_upper', 'BB_middle', 'BB_lower']);

const POSITION_RELATIVE_DEFAULTS: Record<string, number> = {
  STOP_LOSS_PCT: -5,
  TAKE_PROFIT_PCT: 10,
  HOLDING_PERIOD_BARS: 20,
};
```

(`MOMENTUM_PCT`도 `MARKET_TREND`처럼 임계값 기본값을 0으로 두는 게 자연스럽다 — 사용자가 방향에 맞춰 양수/음수로 직접 조정한다.)

- [ ] **Step 2: 타입 체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 3: 브라우저로 확인**

1. 백엔드(`uvicorn backend.main:app --reload --port 8000`)와 프론트(`npm run dev`)가 떠 있다면 재기동 후, `http://localhost:3000`(백테스트 설정 페이지)에서 매도 조건에 "+조건 추가"를 눌러 카탈로그를 연다.
2. "손익" 카테고리에 "보유기간 (봉)"이 손절/익절과 나란히 보이는지, "시장 심리" 카테고리에 "시장 추세 (BTC 종가-이동평균)"가, "추세" 카테고리에 "모멘텀 (N봉 전 대비 등락률 %)"가 SMA/EMA/WMA와 나란히 보이는지 확인.
3. `MARKET_TREND`/`MOMENTUM_PCT`를 조건에 추가했을 때 연산자가 자유롭게 선택되고(고정 연산자 아님) 기본 임계값이 0으로 채워지는지, `HOLDING_PERIOD_BARS`는 매도 조건에서만 보이고 연산자가 "≥"로 고정 표시되는지 확인.
4. 실제로 세 조건을 하나씩(그리고 `MOMENTUM_PCT`는 양수/음수 임계값 둘 다) 넣어 백테스트를 실행해보고, 500 에러 없이 정상적으로 결과가 나오는지 확인.

- [ ] **Step 4: Commit**

```bash
git add frontend/components/StrategyConditionBuilder.tsx
git commit -m "feat: 조건 빌더에 보유기간/BTC 추세/모멘텀 지표 추천값 추가"
```
