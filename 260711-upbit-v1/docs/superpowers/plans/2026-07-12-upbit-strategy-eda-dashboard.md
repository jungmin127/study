# 서브프로젝트 2-1: 백테스팅 전략 EDA 대시보드 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 신호(지표) 기반 전략을 코인×봉타입 조합으로 스윕 실행하고 결과 히스토리를 SQLite에 쌓으며, FastAPI+Next.js 대시보드로 조회한다.

**Architecture:** `signals.py`(4개 독립 신호 + 레지스트리) → `engine/strategies.py`(`SignalStrategy`, 매수 AND/매도 OR) → `engine/sweep.py`(`run_sweep()`, `get_candles()`+`run_backtest_cached()` 재사용) → `engine/cache.py`에 추가한 `sweep_history` 테이블(append-only) → `backend/`(FastAPI, SQLite 직접 조회) → `frontend/`(Next.js App Router + shadcn/ui + Tailwind + TradingView Lightweight Charts, 5개 탭 중 4개 활성 + 1개 플레이스홀더).

**Tech Stack:** Python 3.11(기존), FastAPI + uvicorn(신규), Node.js 24 + Next.js 14 + TypeScript + shadcn/ui + Tailwind + lightweight-charts + recharts(신규).

## Global Constraints

- 서버 프로세스는 FastAPI 하나만 추가한다 — 스케줄러·설정 파일 레이어는 두지 않는다.
- 전략(신호)은 `bt.Strategy`/`Signal` 프로토콜을 코드로 직접 작성한다 — JSON 조건 트리 없음.
- 신호는 `should_buy`/`should_sell`을 **현재 상태**(예: MACD>시그널, RSI<30) 기준으로 정의한다 — 순간 교차 이벤트가 아니다. (프로토타이핑으로 검증: 이벤트 기준은 혼합(AND) 전략에서 실질적으로 거래가 발생하지 않음.)
- 혼합 전략의 매수는 모든 신호 AND, 매도는 신호 중 하나라도 OR.
- 새 신호 추가는 (1) `Signal` 구현 클래스 작성, (2) `SIGNAL_REGISTRY`에 등록 두 단계로 끝나야 한다 — `engine/strategies.py`, `engine/sweep.py`, 백엔드, 프론트엔드는 수정 불필요.
- `sweep_history`는 append-only — 같은 조합을 다시 스윕해도 기존 행을 덮어쓰지 않고 새 행을 추가한다.
- `run_sweep()`은 조합 하나가 실패해도 전체를 중단하지 않고 건너뛴다.

---

### Task 1: 프로젝트 스캐폴딩 확장 (FastAPI + Next.js)

**Files:**
- Modify: `requirements.txt`
- Create: `backend/__init__.py`
- Create: `frontend/` (Next.js 프로젝트 전체, `create-next-app`으로 생성)

**Interfaces:**
- Produces: `uvicorn backend.main:app`(저장소 루트에서 실행)로 뜰 수 있는 빈 FastAPI 앱 껍데기, `npm run dev`(frontend/에서 실행)로 뜨는 빈 Next.js 앱.

- [ ] **Step 1: Python 의존성 추가**

`requirements.txt`에 추가:
```
fastapi>=0.109
uvicorn>=0.27
```

Run: `pip install -r requirements.txt`

- [ ] **Step 2: backend 패키지 초기화**

`backend/__init__.py`: (빈 파일)

- [ ] **Step 3: Next.js 프로젝트 생성**

Run (저장소 루트에서):
```bash
npx create-next-app@14.2.35 frontend --typescript --eslint --tailwind --app --no-src-dir --import-alias "@/*"
```
프롬프트가 뜨면 모두 기본값(Enter)으로 진행.

**버전을 14.2.35로 고정하는 이유**: `backtesting_1` 프로젝트가 이미 이 버전으로 검증됨. Next.js 15부터는 동적 라우트의 `params`가 Promise로 바뀌어(`await params` 필요) Task 14의 `params: { runId: string }` 동기 접근 코드가 깨진다 — `@latest`를 쓰면 이 프로젝트를 만드는 시점에 따라 15+가 설치될 위험이 있으므로 명시적으로 고정한다.

- [ ] **Step 4: shadcn/ui 초기화 및 필요한 컴포넌트 설치**

Run:
```bash
cd frontend
npx shadcn@latest init -d
npx shadcn@latest add table card badge
npm install lightweight-charts recharts
cd ..
```

- [ ] **Step 5: 두 서버가 각각 뜨는지 확인**

Run:
```bash
python -c "from fastapi import FastAPI; app = FastAPI(); print('fastapi ok')"
cd frontend && npm run build && cd ..
```
Expected: 둘 다 에러 없이 통과 (`npm run build`는 아직 빈 기본 페이지라 성공해야 함).

- [ ] **Step 6: 커밋**

```bash
git add requirements.txt backend/__init__.py frontend
git commit -m "chore: scaffold FastAPI backend and Next.js frontend for EDA dashboard"
```

---

### Task 2: `Signal` 프로토콜 + `SignalStrategy`(제네릭 AND매수/OR매도)

**Files:**
- Create: `signals.py`
- Create: `engine/strategies.py`
- Test: `tests/test_strategies.py`

**Interfaces:**
- Produces: `signals.Signal`(Protocol: `setup`, `should_buy`, `should_sell`), `engine.strategies.SignalStrategy(bt.Strategy)` (params: `signals: list[Signal]`).

이 태스크는 아직 실제 지표 신호(MACD 등)를 만들지 않는다. `SignalStrategy`의 AND/OR 결합 로직 자체를 결정론적으로 검증하기 위해, 테스트 전용 스텁 신호(정해진 bar 번호에서만 매수/매도)를 사용한다 — 실제 backtrader 지표의 워밍업/노이즈에 좌우되지 않는 순수 로직 테스트.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_strategies.py`:
```python
import pandas as pd

from engine.runner import run_backtest
from engine.strategies import SignalStrategy


class StubSignal:
    """지정된 bar 번호(len(strategy) 기준)에서만 매수/매도 신호를 낸다."""

    def __init__(self, buy_bars: set[int], sell_bars: set[int]):
        self.buy_bars = buy_bars
        self.sell_bars = sell_bars

    def setup(self, strategy) -> None:
        pass

    def should_buy(self, strategy) -> bool:
        return len(strategy) in self.buy_bars

    def should_sell(self, strategy) -> bool:
        return len(strategy) in self.sell_bars


def _make_df(n: int = 40) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    prices = [10000 + i * 2 for i in range(n)]
    return pd.DataFrame(
        {
            "candle_time": idx,
            "open": prices,
            "high": [p + 1 for p in prices],
            "low": [p - 1 for p in prices],
            "close": prices,
            "volume": [1.0] * n,
        }
    )


RISK_CONFIG = {"initial_capital": 10000, "commission_rate": 0.001, "position_sizing": "percent", "position_size": 100}


def test_single_signal_buys_and_sells_at_its_own_bars():
    df = _make_df()
    signal = StubSignal(buy_bars={5}, sell_bars={10})
    result = run_backtest(df, SignalStrategy, RISK_CONFIG, strategy_params={"signals": [signal]})

    assert len(result["trades"]) == 1
    assert result["trades"][0]["forceClosed"] is False


def test_combined_buy_requires_all_signals_to_agree_same_bar():
    df = _make_df()
    # 매수 조건이 겹치는 bar는 8뿐 (5,8 ∩ 8,12 = {8})
    a = StubSignal(buy_bars={5, 8}, sell_bars=set())
    b = StubSignal(buy_bars={8, 12}, sell_bars=set())
    result = run_backtest(df, SignalStrategy, RISK_CONFIG, strategy_params={"signals": [a, b]})

    assert len(result["trades"]) == 1
    # bar 8에 진입했는지 확인 (bar 8 = 인덱스 7, candle_time 8번째 값)
    entry_time = result["trades"][0]["entryTime"]
    assert entry_time == df["candle_time"].iloc[8].isoformat()


def test_combined_sell_fires_when_any_signal_says_sell():
    df = _make_df()
    a = StubSignal(buy_bars={5}, sell_bars={20})
    b = StubSignal(buy_bars={5}, sell_bars={15})
    result = run_backtest(df, SignalStrategy, RISK_CONFIG, strategy_params={"signals": [a, b]})

    assert len(result["trades"]) == 1
    # b가 더 빠른 bar(15)에 매도 신호를 내므로 그때 청산돼야 함
    exit_time = result["trades"][0]["exitTime"]
    assert exit_time == df["candle_time"].iloc[15].isoformat()


def test_no_signals_never_trades():
    df = _make_df()
    result = run_backtest(df, SignalStrategy, RISK_CONFIG, strategy_params={"signals": []})
    assert len(result["trades"]) == 0
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_strategies.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'engine.strategies'`

- [ ] **Step 3: 구현 작성**

`signals.py`:
```python
"""
signals.py

백테스팅에 사용할 지표 기반 신호. 각 신호는 Signal 프로토콜을 구현하며,
서로의 존재를 모르는 완전히 독립된 단위다. SignalStrategy가 신호 리스트를
받아 매수는 AND, 매도는 OR로 결합해 실행한다.

should_buy/should_sell은 "지금 막 교차했는가"가 아니라 "지금 이 상태에
있는가"를 기준으로 정의한다 — 여러 신호를 AND로 묶었을 때 서로 다른
지표가 정확히 같은 봉에서 동시에 교차할 확률은 매우 낮아, 이벤트 기준으로
정의하면 혼합 전략이 사실상 거래를 하지 않게 된다(프로토타이핑으로 확인).

새 신호 추가 절차:
1. Signal 프로토콜(setup/should_buy/should_sell)을 구현하는 클래스를 작성.
2. SIGNAL_REGISTRY에 한 줄 등록.
그 외 engine/strategies.py, engine/sweep.py, 백엔드/프론트엔드는 수정할 필요 없다.
"""
from __future__ import annotations

from typing import Protocol

import backtrader as bt


class Signal(Protocol):
    def setup(self, strategy: bt.Strategy) -> None: ...
    def should_buy(self, strategy: bt.Strategy) -> bool: ...
    def should_sell(self, strategy: bt.Strategy) -> bool: ...


SIGNAL_REGISTRY: dict[str, Signal] = {}
```

`engine/strategies.py`:
```python
"""
engine/strategies.py

signals.Signal 리스트를 받아 매수는 AND, 매도는 OR로 결합해 실행하는
제네릭 backtrader 전략. 신호가 1개면 단독 전략, 여러 개면 혼합 전략이 된다
— 별도의 "단독 전략용" 클래스는 두지 않는다.
"""
from __future__ import annotations

import backtrader as bt


class SignalStrategy(bt.Strategy):
    params = (("signals", []),)

    def __init__(self):
        self.signals = list(self.p.signals)
        for signal in self.signals:
            signal.setup(self)

    def next(self):
        if not self.position:
            if self.signals and all(s.should_buy(self) for s in self.signals):
                self.buy()
        else:
            if any(s.should_sell(self) for s in self.signals):
                self.sell()
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_strategies.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: 커밋**

```bash
git add signals.py engine/strategies.py tests/test_strategies.py
git commit -m "feat: add Signal protocol and generic AND-buy/OR-sell SignalStrategy"
```

---

### Task 3: MACD 신호

**Files:**
- Modify: `signals.py`
- Test: `tests/test_signals.py` (신규)

**Interfaces:**
- Produces: `signals.MacdCrossSignal`, `tests/signal_fixtures.py`의 `make_oscillating_df()`(이후 태스크에서도 재사용하는 공용 합성 데이터 — sin 파형 + 작은 리플로, 지표 워밍업 구간에서 0으로 나누기 에러 없이 매수/매도 상태가 반복적으로 뒤바뀌도록 프로토타이핑으로 검증된 파라미터).

프로토타이핑 근거: 단순 등락 구간(하락→상승→하락)으로는 MACD/RSI/볼린저밴드가 지표 워밍업 구간에서 이미 상태가 결정돼버리거나(교차를 관측 못 함), 매수 신호가 급등 구간에서만 발동해 마진 부족으로 주문이 거부되는 문제가 있었다. sin파(느린 큰 파동) + 작은 리플(빠른 잔파동)을 합성하면 워밍업 이후에도 상태가 반복적으로 뒤바뀌고, 봉 간 변동폭이 항상 작아(사이저 버퍼 이내) 주문이 안정적으로 체결된다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/signal_fixtures.py`:
```python
"""신호 테스트에서 공유하는 합성 OHLCV 데이터."""
from __future__ import annotations

import math

import pandas as pd


def make_oscillating_df(
    n: int = 300,
    base: float = 20000.0,
    amplitude: float = 600.0,
    period: int = 120,
    ripple_amplitude: float = 50.0,
    ripple_period: int = 6,
) -> pd.DataFrame:
    prices = [
        base
        + amplitude * math.sin(2 * math.pi * i / period)
        + ripple_amplitude * math.sin(2 * math.pi * i / ripple_period)
        for i in range(n)
    ]
    idx = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame(
        {
            "candle_time": idx,
            "open": prices,
            "high": [p + 1 for p in prices],
            "low": [p - 1 for p in prices],
            "close": prices,
            "volume": [1.0] * n,
        }
    )
```

`tests/test_signals.py`:
```python
from engine.runner import run_backtest
from engine.strategies import SignalStrategy
from signals import MacdCrossSignal
from tests.signal_fixtures import make_oscillating_df

RISK_CONFIG = {"initial_capital": 10000, "commission_rate": 0.001, "position_sizing": "percent", "position_size": 100}


def test_macd_cross_signal_trades_on_oscillating_data():
    df = make_oscillating_df()
    result = run_backtest(df, SignalStrategy, RISK_CONFIG, strategy_params={"signals": [MacdCrossSignal()]})

    assert len(result["trades"]) >= 1
    assert any(t["forceClosed"] is False for t in result["trades"])
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_signals.py -v`
Expected: FAIL with `ImportError: cannot import name 'MacdCrossSignal'`

- [ ] **Step 3: 구현 추가**

`signals.py`에 추가:
```python
class MacdCrossSignal:
    """MACD선이 시그널선 위(강세 상태)/아래(약세 상태)인지를 매수/매도 조건으로 사용."""

    def __init__(self, fast: int = 12, slow: int = 26, signal_period: int = 9):
        self.fast = fast
        self.slow = slow
        self.signal_period = signal_period

    def setup(self, strategy: bt.Strategy) -> None:
        self._macd = bt.indicators.MACD(
            strategy.data,
            period_me1=self.fast,
            period_me2=self.slow,
            period_signal=self.signal_period,
        )

    def should_buy(self, strategy: bt.Strategy) -> bool:
        return self._macd.macd[0] > self._macd.signal[0]

    def should_sell(self, strategy: bt.Strategy) -> bool:
        return self._macd.macd[0] < self._macd.signal[0]


SIGNAL_REGISTRY["macd_cross"] = MacdCrossSignal()
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_signals.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: 커밋**

```bash
git add signals.py tests/signal_fixtures.py tests/test_signals.py
git commit -m "feat: add MACD state signal and shared oscillating test fixture"
```

---

### Task 4: RSI 신호

**Files:**
- Modify: `signals.py`
- Modify: `tests/test_signals.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_signals.py`에 추가:
```python
from signals import RsiZoneSignal


def test_rsi_zone_signal_trades_on_oscillating_data():
    df = make_oscillating_df()
    result = run_backtest(df, SignalStrategy, RISK_CONFIG, strategy_params={"signals": [RsiZoneSignal()]})

    assert len(result["trades"]) >= 1
    assert any(t["forceClosed"] is False for t in result["trades"])
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_signals.py -v`
Expected: FAIL with `ImportError: cannot import name 'RsiZoneSignal'`

- [ ] **Step 3: 구현 추가**

`signals.py`에 추가:
```python
class RsiZoneSignal:
    """RSI가 과매도(매수 구간)/과매수(매도 구간) 상태인지를 조건으로 사용."""

    def __init__(self, period: int = 14, oversold: float = 30.0, overbought: float = 70.0):
        self.period = period
        self.oversold = oversold
        self.overbought = overbought

    def setup(self, strategy: bt.Strategy) -> None:
        self._rsi = bt.indicators.RSI(strategy.data, period=self.period)

    def should_buy(self, strategy: bt.Strategy) -> bool:
        return self._rsi[0] < self.oversold

    def should_sell(self, strategy: bt.Strategy) -> bool:
        return self._rsi[0] > self.overbought


SIGNAL_REGISTRY["rsi_zone"] = RsiZoneSignal()
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_signals.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: 커밋**

```bash
git add signals.py tests/test_signals.py
git commit -m "feat: add RSI overbought/oversold zone signal"
```

---

### Task 5: SMA 교차 신호

**Files:**
- Modify: `signals.py`
- Modify: `tests/test_signals.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_signals.py`에 추가:
```python
from signals import SmaCrossSignal


def test_sma_cross_signal_trades_on_oscillating_data():
    df = make_oscillating_df()
    result = run_backtest(df, SignalStrategy, RISK_CONFIG, strategy_params={"signals": [SmaCrossSignal()]})

    assert len(result["trades"]) >= 1
    assert any(t["forceClosed"] is False for t in result["trades"])
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_signals.py -v`
Expected: FAIL with `ImportError: cannot import name 'SmaCrossSignal'`

- [ ] **Step 3: 구현 추가**

`signals.py`에 추가:
```python
class SmaCrossSignal:
    """단기 SMA가 장기 SMA 위(강세 상태)/아래(약세 상태)인지를 조건으로 사용."""

    def __init__(self, short: int = 10, long: int = 30):
        self.short = short
        self.long = long

    def setup(self, strategy: bt.Strategy) -> None:
        self._short = bt.indicators.SMA(strategy.data, period=self.short)
        self._long = bt.indicators.SMA(strategy.data, period=self.long)

    def should_buy(self, strategy: bt.Strategy) -> bool:
        return self._short[0] > self._long[0]

    def should_sell(self, strategy: bt.Strategy) -> bool:
        return self._short[0] < self._long[0]


SIGNAL_REGISTRY["sma_cross"] = SmaCrossSignal()
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_signals.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: 커밋**

```bash
git add signals.py tests/test_signals.py
git commit -m "feat: add SMA short/long cross state signal"
```

---

### Task 6: 볼린저밴드 신호 + 레지스트리 확장성 테스트

**Files:**
- Modify: `signals.py`
- Modify: `tests/test_signals.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_signals.py`에 추가:
```python
from signals import BollingerBandSignal, SIGNAL_REGISTRY


def test_bollinger_band_signal_trades_on_oscillating_data():
    df = make_oscillating_df()
    result = run_backtest(df, SignalStrategy, RISK_CONFIG, strategy_params={"signals": [BollingerBandSignal()]})

    assert len(result["trades"]) >= 1
    assert any(t["forceClosed"] is False for t in result["trades"])


def test_signal_registry_contains_all_four_signals():
    assert set(SIGNAL_REGISTRY.keys()) == {"macd_cross", "rsi_zone", "sma_cross", "bollinger_band"}


def test_signal_registry_extensible_without_touching_other_modules(monkeypatch):
    """새 신호를 레지스트리에 등록하면 (engine/sweep.py 등 다른 모듈 수정 없이)
    바로 SignalStrategy에서 사용할 수 있어야 한다 — 확장성 요구사항 검증."""

    class DummyAlwaysBuySignal:
        def setup(self, strategy) -> None:
            pass

        def should_buy(self, strategy) -> bool:
            return True

        def should_sell(self, strategy) -> bool:
            return False

    monkeypatch.setitem(SIGNAL_REGISTRY, "dummy_always_buy", DummyAlwaysBuySignal())

    assert "dummy_always_buy" in SIGNAL_REGISTRY
    df = make_oscillating_df(n=20)
    signal = SIGNAL_REGISTRY["dummy_always_buy"]
    result = run_backtest(df, SignalStrategy, RISK_CONFIG, strategy_params={"signals": [signal]})
    # DummyAlwaysBuySignal은 항상 매수이므로 첫 bar에 바로 진입해야 함
    assert len(result["trades"]) == 1
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_signals.py -v`
Expected: FAIL with `ImportError: cannot import name 'BollingerBandSignal'`

- [ ] **Step 3: 구현 추가**

`signals.py`에 추가:
```python
class BollingerBandSignal:
    """종가가 볼린저밴드 하단 아래(매수 구간)/상단 위(매도 구간)인지를 조건으로 사용."""

    def __init__(self, period: int = 20, devfactor: float = 2.0):
        self.period = period
        self.devfactor = devfactor

    def setup(self, strategy: bt.Strategy) -> None:
        self._bb = bt.indicators.BollingerBands(strategy.data, period=self.period, devfactor=self.devfactor)

    def should_buy(self, strategy: bt.Strategy) -> bool:
        return strategy.data.close[0] < self._bb.bot[0]

    def should_sell(self, strategy: bt.Strategy) -> bool:
        return strategy.data.close[0] > self._bb.top[0]


SIGNAL_REGISTRY["bollinger_band"] = BollingerBandSignal()
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_signals.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: 전체 스위트 확인**

Run: `pytest -v`
Expected: 이전 태스크 테스트 전부 + 이번 태스크 테스트 PASS

- [ ] **Step 6: 커밋**

```bash
git add signals.py tests/test_signals.py
git commit -m "feat: add Bollinger Band signal and registry extensibility test"
```

---

### Task 7: `sweep_history` 저장소 + `run_backtest_cached()`에 `run_id` 노출

**Files:**
- Modify: `engine/cache.py`
- Modify: `tests/test_cache.py`

**Interfaces:**
- Consumes: 기존 `engine/cache.py`의 `DB_PATH`, `_connect()`
- Produces: `save_sweep_result(run_id, signal_set_name, is_combined, market, timeframe, start, end, return_rate, sharpe, max_drawdown) -> None`, `list_latest_sweep_results() -> list[dict]`, `list_combined_ranking() -> list[dict]`, `list_sweep_history(signal_set_name, market, timeframe, is_combined) -> list[dict]`, `list_distinct_combos() -> list[dict]`. `run_backtest_cached()`의 반환 dict에 `run_id` 키 추가(기존 키는 그대로 유지 — 추가적 변경).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_cache.py`에 추가:
```python
from engine.cache import (
    list_combined_ranking,
    list_distinct_combos,
    list_latest_sweep_results,
    list_sweep_history,
    save_sweep_result,
)


def test_run_backtest_cached_exposes_run_id(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")

    def fake_run_backtest(df, strategy_cls, risk_config, strategy_params=None):
        return {"equity_curve": [], "trades": [], "final_value": 10000.0, "sharpe": None, "max_drawdown": None}

    monkeypatch.setattr(cache_module, "run_backtest", fake_run_backtest)

    result = run_backtest_cached(
        df=_synthetic_df(), strategy_cls=_StrategyA, risk_config={"initial_capital": 10000},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
    )
    expected_run_id = compute_cache_key(
        _StrategyA, {}, "KRW-BTC", "days",
        datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 10, tzinfo=timezone.utc),
        {"initial_capital": 10000},
    )
    assert result["run_id"] == expected_run_id


def test_save_and_list_latest_sweep_results(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")

    save_sweep_result(
        run_id="run-1", signal_set_name="macd_cross", is_combined=False,
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        return_rate=5.0, sharpe=1.1, max_drawdown=2.0,
    )
    # 같은 조합을 다시 스윕 — append-only이므로 새 행이 추가돼야 함
    save_sweep_result(
        run_id="run-2", signal_set_name="macd_cross", is_combined=False,
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 11, tzinfo=timezone.utc),
        return_rate=7.0, sharpe=1.3, max_drawdown=2.5,
    )

    latest = list_latest_sweep_results()
    assert len(latest) == 1  # 같은 (signal_set_name, is_combined, market, timeframe) 조합은 최신 1건만
    assert latest[0]["return_rate"] == 7.0

    history = list_sweep_history("macd_cross", "KRW-BTC", "days", is_combined=False)
    assert len(history) == 2  # 히스토리는 append-only로 둘 다 보여야 함


def test_combined_ranking_filters_and_sorts_by_return_rate(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")

    save_sweep_result(
        run_id="run-a", signal_set_name="macd_cross", is_combined=False,
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        return_rate=100.0, sharpe=None, max_drawdown=None,
    )
    save_sweep_result(
        run_id="run-b", signal_set_name="mixed_all", is_combined=True,
        market="KRW-ETH", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        return_rate=3.0, sharpe=None, max_drawdown=None,
    )
    save_sweep_result(
        run_id="run-c", signal_set_name="mixed_all", is_combined=True,
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        return_rate=8.0, sharpe=None, max_drawdown=None,
    )

    ranking = list_combined_ranking()
    assert [r["market"] for r in ranking] == ["KRW-BTC", "KRW-ETH"]  # is_combined=False(run-a)는 제외, 수익률 내림차순


def test_list_distinct_combos(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")

    save_sweep_result(
        run_id="run-x", signal_set_name="rsi_zone", is_combined=False,
        market="KRW-BTC", timeframe="minutes60",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        return_rate=1.0, sharpe=None, max_drawdown=None,
    )
    combos = list_distinct_combos()
    assert combos == [
        {"signal_set_name": "rsi_zone", "is_combined": False, "market": "KRW-BTC", "timeframe": "minutes60"}
    ]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_cache.py -v`
Expected: FAIL with `ImportError: cannot import name 'save_sweep_result'`

- [ ] **Step 3: 구현 작성**

`engine/cache.py`의 `_SCHEMA` 문자열에 추가(기존 `backtest_runs`/`backtest_results` 정의 뒤에 이어 붙임):
```python
_SCHEMA += """
CREATE TABLE IF NOT EXISTS sweep_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    signal_set_name TEXT NOT NULL,
    is_combined INTEGER NOT NULL,
    market TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    return_rate REAL,
    sharpe REAL,
    max_drawdown REAL,
    swept_at TEXT NOT NULL
);
"""
```

`engine/cache.py`의 `run_backtest_cached()` 안에서 `cached is not None` 분기와 최종 `return result` 직전 각각에 `result["run_id"] = run_id`를 추가(캐시 hit/miss 두 경로 모두 `run_id`를 갖도록):
```python
def run_backtest_cached(...) -> dict:
    strategy_params = strategy_params or {}
    run_id = compute_cache_key(
        strategy_cls, strategy_params, market, timeframe, start, end, risk_config
    )

    cached = load_result(run_id)
    if cached is not None:
        cached["run_id"] = run_id
        return cached

    result = run_backtest(df, strategy_cls, risk_config, strategy_params)
    save_result(
        run_id=run_id, strategy_name=strategy_cls.__name__, strategy_params=strategy_params,
        market=market, timeframe=timeframe, start=start, end=end, risk_config=risk_config, result=result,
    )
    result["from_cache"] = False
    result["run_id"] = run_id
    return result
```

`engine/cache.py` 끝에 추가:
```python
def save_sweep_result(
    run_id: str,
    signal_set_name: str,
    is_combined: bool,
    market: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    return_rate: float | None,
    sharpe: float | None,
    max_drawdown: float | None,
) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO sweep_history "
            "(run_id, signal_set_name, is_combined, market, timeframe, start, end, "
            " return_rate, sharpe, max_drawdown, swept_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
            (
                run_id, signal_set_name, int(is_combined), market, timeframe,
                start.isoformat(), end.isoformat(), return_rate, sharpe, max_drawdown,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _row_to_sweep_dict(row: tuple) -> dict:
    (run_id, signal_set_name, is_combined, market, timeframe, start, end,
     return_rate, sharpe, max_drawdown, swept_at) = row
    return {
        "run_id": run_id,
        "signal_set_name": signal_set_name,
        "is_combined": bool(is_combined),
        "market": market,
        "timeframe": timeframe,
        "start": start,
        "end": end,
        "return_rate": return_rate,
        "sharpe": sharpe,
        "max_drawdown": max_drawdown,
        "swept_at": swept_at,
    }


def list_latest_sweep_results() -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT run_id, signal_set_name, is_combined, market, timeframe, start, end, "
            "       return_rate, sharpe, max_drawdown, swept_at "
            "FROM sweep_history "
            "WHERE id IN ("
            "  SELECT MAX(id) FROM sweep_history "
            "  GROUP BY signal_set_name, is_combined, market, timeframe"
            ") "
            "ORDER BY signal_set_name, market, timeframe"
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_sweep_dict(r) for r in rows]


def list_combined_ranking() -> list[dict]:
    return sorted(
        (r for r in list_latest_sweep_results() if r["is_combined"]),
        key=lambda r: (r["return_rate"] if r["return_rate"] is not None else float("-inf")),
        reverse=True,
    )


def list_sweep_history(
    signal_set_name: str, market: str, timeframe: str, is_combined: bool
) -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT run_id, signal_set_name, is_combined, market, timeframe, start, end, "
            "       return_rate, sharpe, max_drawdown, swept_at "
            "FROM sweep_history "
            "WHERE signal_set_name = ? AND market = ? AND timeframe = ? AND is_combined = ? "
            "ORDER BY swept_at",
            (signal_set_name, market, timeframe, int(is_combined)),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_sweep_dict(r) for r in rows]


def list_distinct_combos() -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT DISTINCT signal_set_name, is_combined, market, timeframe FROM sweep_history "
            "ORDER BY signal_set_name, market, timeframe"
        ).fetchall()
    finally:
        conn.close()
    return [
        {"signal_set_name": r[0], "is_combined": bool(r[1]), "market": r[2], "timeframe": r[3]}
        for r in rows
    ]
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_cache.py -v`
Expected: PASS (전체 — 기존 8개 + 이번 태스크 4개)

- [ ] **Step 5: 커밋**

```bash
git add engine/cache.py tests/test_cache.py
git commit -m "feat: add append-only sweep_history storage and expose run_id from run_backtest_cached"
```

---

### Task 8: `run_sweep()` 오케스트레이션

**Files:**
- Create: `engine/sweep.py`
- Test: `tests/test_sweep.py`

**Interfaces:**
- Consumes: `upbit_data_service.get_candles`, `engine.cache.run_backtest_cached`, `engine.cache.save_sweep_result`, `engine.strategies.SignalStrategy`
- Produces: `run_sweep(markets: list[str], timeframes: list[str], signal_sets: list[tuple[str, list, bool]], start: datetime, end: datetime, risk_config: dict | None = None) -> None`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_sweep.py`:
```python
from datetime import datetime, timezone

import pandas as pd
import pytest

import engine.sweep as sweep_module
from engine.sweep import run_sweep


def _fake_df() -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=5, freq="D", tz="UTC")
    return pd.DataFrame(
        {"candle_time": idx, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
    )


def test_run_sweep_calls_backtest_and_saves_history_for_each_combo(monkeypatch, tmp_path):
    import engine.cache as cache_module

    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")

    fetch_calls = []
    monkeypatch.setattr(sweep_module, "get_candles", lambda market, timeframe, start, end: (
        fetch_calls.append((market, timeframe)), _fake_df()
    )[1])

    saved = []
    monkeypatch.setattr(
        sweep_module,
        "run_backtest_cached",
        lambda **kwargs: {"final_value": 11000.0, "sharpe": 1.0, "max_drawdown": 5.0, "run_id": "r1"},
    )
    monkeypatch.setattr(
        sweep_module,
        "save_sweep_result",
        lambda **kwargs: saved.append(kwargs),
    )

    class DummySignal:
        def setup(self, strategy): pass
        def should_buy(self, strategy): return False
        def should_sell(self, strategy): return False

    run_sweep(
        markets=["KRW-BTC", "KRW-ETH"],
        timeframes=["days"],
        signal_sets=[("dummy", [DummySignal()], False)],
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 1, 5, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
    )

    assert len(fetch_calls) == 2  # 마켓 2개 x 봉타입 1개
    assert len(saved) == 2
    assert saved[0]["return_rate"] == pytest.approx(10.0)  # (11000-10000)/10000*100
    assert saved[0]["run_id"] == "r1"


def test_run_sweep_skips_failing_combo_and_continues(monkeypatch, tmp_path):
    import engine.cache as cache_module

    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    monkeypatch.setattr(sweep_module, "get_candles", lambda market, timeframe, start, end: _fake_df())

    call_count = {"n": 0}

    def failing_then_ok(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise ValueError("존재하지 않는 마켓")
        return {"final_value": 10500.0, "sharpe": None, "max_drawdown": None, "run_id": "r2"}

    monkeypatch.setattr(sweep_module, "run_backtest_cached", failing_then_ok)

    saved = []
    monkeypatch.setattr(sweep_module, "save_sweep_result", lambda **kwargs: saved.append(kwargs))

    class DummySignal:
        def setup(self, strategy): pass
        def should_buy(self, strategy): return False
        def should_sell(self, strategy): return False

    run_sweep(
        markets=["KRW-FAKE", "KRW-ETH"],
        timeframes=["days"],
        signal_sets=[("dummy", [DummySignal()], False)],
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 1, 5, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
    )

    assert call_count["n"] == 2  # 첫 번째 실패해도 두 번째는 계속 실행됨
    assert len(saved) == 1  # 실패한 조합은 저장되지 않음
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_sweep.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'engine.sweep'`

- [ ] **Step 3: 구현 작성**

`engine/sweep.py`:
```python
"""
engine/sweep.py

코인×봉타입×신호(개별/혼합) 조합을 전부 스윕 실행하고 결과를 sweep_history에 기록한다.
캐시(run_backtest_cached)가 중복 실행을 막아주므로, 이미 실행한 조합을 다시 스윕해도
backtrader가 재실행되지 않는다 — sweep_history에만 새 행이 append된다.
"""
from __future__ import annotations

from datetime import datetime

from engine.cache import run_backtest_cached, save_sweep_result
from engine.strategies import SignalStrategy
from upbit_data_service import get_candles

DEFAULT_RISK_CONFIG = {
    "initial_capital": 10_000_000,
    "commission_rate": 0.0005,
    "position_sizing": "percent",
    "position_size": 100,
}


def run_sweep(
    markets: list[str],
    timeframes: list[str],
    signal_sets: list[tuple[str, list, bool]],
    start: datetime,
    end: datetime,
    risk_config: dict | None = None,
) -> None:
    """
    markets x timeframes x signal_sets 전 조합을 백테스트하고 sweep_history에 기록한다.

    Args:
        signal_sets: (표시용 이름, signals.Signal 리스트, 혼합 전략 여부) 튜플 리스트.
                     혼합 전략이면 signals 리스트에 신호를 2개 이상 넣고 세 번째 값을 True로.
        risk_config: 생략 시 DEFAULT_RISK_CONFIG 사용.
    """
    risk_config = risk_config or DEFAULT_RISK_CONFIG

    for market in markets:
        for timeframe in timeframes:
            df = get_candles(market, timeframe, start, end)
            for signal_set_name, signals, is_combined in signal_sets:
                try:
                    result = run_backtest_cached(
                        df=df,
                        strategy_cls=SignalStrategy,
                        risk_config=risk_config,
                        market=market,
                        timeframe=timeframe,
                        start=start,
                        end=end,
                        strategy_params={"signals": signals},
                    )
                except Exception as exc:
                    print(f"[run_sweep] 건너뜀 {signal_set_name}/{market}/{timeframe}: {exc}")
                    continue

                return_rate = (
                    (result["final_value"] - risk_config["initial_capital"])
                    / risk_config["initial_capital"]
                    * 100
                )
                save_sweep_result(
                    run_id=result["run_id"],
                    signal_set_name=signal_set_name,
                    is_combined=is_combined,
                    market=market,
                    timeframe=timeframe,
                    start=start,
                    end=end,
                    return_rate=return_rate,
                    sharpe=result["sharpe"],
                    max_drawdown=result["max_drawdown"],
                )


__all__ = ["run_sweep", "DEFAULT_RISK_CONFIG"]
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_sweep.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: 커밋**

```bash
git add engine/sweep.py tests/test_sweep.py
git commit -m "feat: add run_sweep() to backtest market/timeframe/signal combinations"
```

---

### Task 9: FastAPI 백엔드 — 5개 엔드포인트

**Files:**
- Create: `backend/main.py`
- Test: `tests/test_backend.py`

**Interfaces:**
- Consumes: `engine.cache.list_latest_sweep_results`, `list_combined_ranking`, `list_sweep_history`, `list_distinct_combos`, `load_result` (Task 7)
- Produces: `GET /api/v1/eda/heatmap`, `GET /api/v1/eda/ranking`, `GET /api/v1/eda/combos`, `GET /api/v1/eda/history`, `GET /api/v1/backtests/{run_id}`, `GET /health`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_backend.py`:
```python
from datetime import datetime, timezone

from fastapi.testclient import TestClient

import backend.main as backend_module
import engine.cache as cache_module
from backend.main import app
from engine.cache import save_result, save_sweep_result


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    return TestClient(app)


def test_health_check():
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_heatmap_returns_latest_sweep_results(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    save_sweep_result(
        run_id="r1", signal_set_name="macd_cross", is_combined=False,
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        return_rate=5.0, sharpe=1.0, max_drawdown=2.0,
    )

    resp = client.get("/api/v1/eda/heatmap")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["market"] == "KRW-BTC"
    assert body[0]["return_rate"] == 5.0


def test_ranking_returns_combined_only(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    save_sweep_result(
        run_id="r1", signal_set_name="macd_cross", is_combined=False,
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        return_rate=100.0, sharpe=None, max_drawdown=None,
    )
    save_sweep_result(
        run_id="r2", signal_set_name="mixed_all", is_combined=True,
        market="KRW-ETH", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        return_rate=8.0, sharpe=None, max_drawdown=None,
    )

    resp = client.get("/api/v1/eda/ranking")
    body = resp.json()
    assert len(body) == 1
    assert body[0]["market"] == "KRW-ETH"


def test_combos_and_history(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    save_sweep_result(
        run_id="r1", signal_set_name="macd_cross", is_combined=False,
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        return_rate=5.0, sharpe=1.0, max_drawdown=2.0,
    )

    combos_resp = client.get("/api/v1/eda/combos")
    assert combos_resp.json() == [
        {"signal_set_name": "macd_cross", "is_combined": False, "market": "KRW-BTC", "timeframe": "days"}
    ]

    history_resp = client.get(
        "/api/v1/eda/history",
        params={"signal_set_name": "macd_cross", "market": "KRW-BTC", "timeframe": "days", "is_combined": False},
    )
    assert len(history_resp.json()) == 1


def test_backtest_detail_returns_404_for_missing_run(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    resp = client.get("/api/v1/backtests/does-not-exist")
    assert resp.status_code == 404


def test_backtest_detail_returns_result_for_known_run(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    save_result(
        run_id="r1", strategy_name="SignalStrategy", strategy_params={},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={
            "final_value": 10500.0, "sharpe": 1.2, "max_drawdown": 3.0,
            "equity_curve": [{"timestamp": "2026-01-01T00:00:00", "value": 10000.0}],
            "trades": [],
        },
    )

    resp = client.get("/api/v1/backtests/r1")
    assert resp.status_code == 200
    assert resp.json()["final_value"] == 10500.0
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_backend.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.main'`

- [ ] **Step 3: 구현 작성**

`backend/main.py`:
```python
"""
backend/main.py

EDA 대시보드용 FastAPI 앱. engine.cache의 SQLite 저장소를 직접 조회한다.
Run: uvicorn backend.main:app --reload --port 8000  (저장소 루트에서 실행)
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from engine.cache import (
    list_combined_ranking,
    list_distinct_combos,
    list_latest_sweep_results,
    list_sweep_history,
    load_result,
)

app = FastAPI(title="Upbit Strategy EDA API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/eda/heatmap")
def get_heatmap() -> list[dict]:
    return list_latest_sweep_results()


@app.get("/api/v1/eda/ranking")
def get_ranking() -> list[dict]:
    return list_combined_ranking()


@app.get("/api/v1/eda/combos")
def get_combos() -> list[dict]:
    return list_distinct_combos()


@app.get("/api/v1/eda/history")
def get_history(
    signal_set_name: str = Query(...),
    market: str = Query(...),
    timeframe: str = Query(...),
    is_combined: bool = Query(...),
) -> list[dict]:
    return list_sweep_history(signal_set_name, market, timeframe, is_combined)


@app.get("/api/v1/backtests/{run_id}")
def get_backtest_detail(run_id: str) -> dict:
    result = load_result(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="해당 run_id의 백테스트 결과를 찾을 수 없습니다")
    return result
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_backend.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: 수동 확인**

Run: `uvicorn backend.main:app --port 8000` (별도 터미널) 후 `curl http://localhost:8000/health`
Expected: `{"status":"ok"}`

- [ ] **Step 6: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "feat: add FastAPI backend with EDA heatmap/ranking/history/detail endpoints"
```

---

### Task 10: Next.js 프론트엔드 — 레이아웃 + 5개 탭 + API 클라이언트

**Files:**
- Create: `frontend/lib/api/client.ts`
- Create: `frontend/lib/api/eda.ts`
- Create: `frontend/lib/types/eda.ts`
- Modify: `frontend/app/layout.tsx`
- Create: `frontend/app/ranking/page.tsx` (빈 화면, Task 12에서 채움)
- Create: `frontend/app/history/page.tsx` (빈 화면, Task 13에서 채움)
- Create: `frontend/app/backtests/[runId]/page.tsx` (빈 화면, Task 14에서 채움)
- Create: `frontend/app/model-accuracy/page.tsx` (플레이스홀더, 이 태스크에서 완성)
- Modify: `frontend/app/page.tsx` (빈 화면, Task 11에서 채움)
- Create: `frontend/.env.local`

**Interfaces:**
- Produces: `apiFetch<T>(endpoint, options?) -> Promise<T>`(backtesting_1의 `lib/api/client.ts`와 동일 패턴), `getHeatmap()`, `getRanking()`, `getCombos()`, `getHistory(params)`, `getBacktestDetail(runId)`.

- [ ] **Step 1: 환경변수 및 API 클라이언트**

`frontend/.env.local`:
```
NEXT_PUBLIC_API_URL=http://localhost:8000
```

`frontend/lib/api/client.ts`:
```typescript
const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export async function apiFetch<T>(endpoint: string, options?: RequestInit): Promise<T> {
  const url = `${BASE_URL}${endpoint}`;
  const response = await fetch(url, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    cache: 'no-store',
  });

  if (!response.ok) {
    let message = `HTTP error ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) message = body.detail;
    } catch {
      // JSON 파싱 실패 시 기본 메시지 사용
    }
    throw new ApiError(response.status, message);
  }

  return response.json() as Promise<T>;
}
```

- [ ] **Step 2: 타입 정의**

`frontend/lib/types/eda.ts`:
```typescript
export interface SweepResult {
  run_id: string;
  signal_set_name: string;
  is_combined: boolean;
  market: string;
  timeframe: string;
  start: string;
  end: string;
  return_rate: number | null;
  sharpe: number | null;
  max_drawdown: number | null;
  swept_at: string;
}

export interface Combo {
  signal_set_name: string;
  is_combined: boolean;
  market: string;
  timeframe: string;
}

export interface EquityPoint {
  timestamp: string;
  value: number;
}

export interface Trade {
  entryTime: string;
  exitTime: string;
  entryPrice: number;
  exitPrice: number;
  returnRate: number;
  holdingPeriod: number;
  pnl: number;
  forceClosed: boolean;
}

export interface BacktestDetail {
  final_value: number;
  sharpe: number | null;
  max_drawdown: number | null;
  equity_curve: EquityPoint[];
  trades: Trade[];
}
```

- [ ] **Step 3: API 함수**

`frontend/lib/api/eda.ts`:
```typescript
import { apiFetch } from './client';
import type { BacktestDetail, Combo, SweepResult } from '@/lib/types/eda';

export function getHeatmap(): Promise<SweepResult[]> {
  return apiFetch<SweepResult[]>('/api/v1/eda/heatmap');
}

export function getRanking(): Promise<SweepResult[]> {
  return apiFetch<SweepResult[]>('/api/v1/eda/ranking');
}

export function getCombos(): Promise<Combo[]> {
  return apiFetch<Combo[]>('/api/v1/eda/combos');
}

export function getHistory(combo: Combo): Promise<SweepResult[]> {
  const params = new URLSearchParams({
    signal_set_name: combo.signal_set_name,
    market: combo.market,
    timeframe: combo.timeframe,
    is_combined: String(combo.is_combined),
  });
  return apiFetch<SweepResult[]>(`/api/v1/eda/history?${params.toString()}`);
}

export function getBacktestDetail(runId: string): Promise<BacktestDetail> {
  return apiFetch<BacktestDetail>(`/api/v1/backtests/${runId}`);
}
```

- [ ] **Step 4: 5탭 레이아웃**

`frontend/app/layout.tsx` 전체를 다음으로 교체:
```typescript
import type { Metadata } from 'next';
import Link from 'next/link';
import './globals.css';

export const metadata: Metadata = {
  title: 'Upbit 전략 EDA 대시보드',
};

const TABS = [
  { href: '/', label: '수익률 히트맵' },
  { href: '/ranking', label: '혼합전략 랭킹' },
  { href: '/history', label: '시간대별 추이' },
  { href: '/backtests', label: '백테스트 상세' },
  { href: '/model-accuracy', label: '모델 정확도' },
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body>
        <header className="border-b px-6 py-3">
          <nav className="flex gap-4 text-sm">
            {TABS.map((tab) => (
              <Link key={tab.href} href={tab.href} className="hover:underline">
                {tab.label}
              </Link>
            ))}
          </nav>
        </header>
        <main className="p-6">{children}</main>
      </body>
    </html>
  );
}
```

- [ ] **Step 5: 나머지 화면 자리 + 모델 정확도 플레이스홀더**

`frontend/app/backtests/[runId]/page.tsx`:
```typescript
export default function BacktestDetailPage() {
  return <div>백테스트 상세 (Task 14에서 구현)</div>;
}
```

`frontend/app/ranking/page.tsx`:
```typescript
export default function RankingPage() {
  return <div>혼합전략 랭킹 (Task 12에서 구현)</div>;
}
```

`frontend/app/history/page.tsx`:
```typescript
export default function HistoryPage() {
  return <div>시간대별 추이 (Task 13에서 구현)</div>;
}
```

`frontend/app/model-accuracy/page.tsx` (이 태스크에서 완성 — 서브3 진행 후 채워질 플레이스홀더):
```typescript
export default function ModelAccuracyPage() {
  return (
    <div className="text-muted-foreground">
      <h1 className="text-lg font-semibold text-foreground">모델 정확도</h1>
      <p className="mt-2">서브프로젝트 3(통계/ML 모델링) 진행 후 공개됩니다.</p>
    </div>
  );
}
```

`frontend/app/page.tsx` 전체를 다음으로 교체(Task 11에서 실제 내용으로 채움):
```typescript
export default function HeatmapPage() {
  return <div>수익률 히트맵 (Task 11에서 구현)</div>;
}
```

- [ ] **Step 6: 빌드 확인**

Run: `cd frontend && npm run build`
Expected: 에러 없이 빌드 성공 (5개 라우트 모두 정적 생성됨)

- [ ] **Step 7: 커밋**

```bash
git add frontend
git commit -m "feat: add 5-tab dashboard layout and typed API client"
```

---

### Task 11: 화면① 전략×코인×봉타입 수익률 테이블/히트맵

**Files:**
- Modify: `frontend/app/page.tsx`

**Interfaces:**
- Consumes: `getHeatmap()` (Task 10)

- [ ] **Step 1: 구현 작성**

`frontend/app/page.tsx` 전체를 다음으로 교체:
```typescript
import Link from 'next/link';
import { getHeatmap } from '@/lib/api/eda';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';

function returnRateColor(rate: number | null): string {
  if (rate === null) return '';
  if (rate > 0) return 'text-green-600 dark:text-green-400';
  if (rate < 0) return 'text-red-600 dark:text-red-400';
  return '';
}

export default async function HeatmapPage() {
  const rows = await getHeatmap();

  return (
    <div>
      <h1 className="text-lg font-semibold mb-4">전략 × 코인 × 봉타입 수익률</h1>
      {rows.length === 0 ? (
        <p className="text-muted-foreground">아직 스윕 데이터가 없습니다. run_sweep()을 먼저 실행하세요.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>전략</TableHead>
              <TableHead>코인</TableHead>
              <TableHead>봉타입</TableHead>
              <TableHead>수익률(%)</TableHead>
              <TableHead>Sharpe</TableHead>
              <TableHead>MDD(%)</TableHead>
              <TableHead>스윕 시각</TableHead>
              <TableHead>상세</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={`${row.signal_set_name}-${row.market}-${row.timeframe}`}>
                <TableCell>
                  {row.signal_set_name}
                  {row.is_combined && <Badge className="ml-2" variant="secondary">혼합</Badge>}
                </TableCell>
                <TableCell>{row.market}</TableCell>
                <TableCell>{row.timeframe}</TableCell>
                <TableCell className={returnRateColor(row.return_rate)}>
                  {row.return_rate?.toFixed(2) ?? '-'}
                </TableCell>
                <TableCell>{row.sharpe?.toFixed(2) ?? '-'}</TableCell>
                <TableCell>{row.max_drawdown?.toFixed(2) ?? '-'}</TableCell>
                <TableCell>{row.swept_at}</TableCell>
                <TableCell>
                  <Link href={`/backtests/${row.run_id}`} className="text-blue-600 hover:underline dark:text-blue-400">
                    보기
                  </Link>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
```

- [ ] **Step 2: 수동 확인**

Run: 터미널 1에서 `uvicorn backend.main:app --port 8000`, 터미널 2에서 `cd frontend && npm run dev`. 브라우저로 `http://localhost:3000` 접속.
Expected: "아직 스윕 데이터가 없습니다" 문구가 보임(아직 스윕을 실행하지 않았으므로 정상).

- [ ] **Step 3: 커밋**

```bash
git add frontend/app/page.tsx
git commit -m "feat: implement strategy x market x timeframe return rate table"
```

---

### Task 12: 화면② 혼합전략 코인 랭킹

**Files:**
- Modify: `frontend/app/ranking/page.tsx`

**Interfaces:**
- Consumes: `getRanking()` (Task 10)

- [ ] **Step 1: 구현 작성**

`frontend/app/ranking/page.tsx` 전체를 다음으로 교체:
```typescript
import { getRanking } from '@/lib/api/eda';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

export default async function RankingPage() {
  const rows = await getRanking();

  return (
    <div>
      <h1 className="text-lg font-semibold mb-4">혼합전략 코인 랭킹</h1>
      {rows.length === 0 ? (
        <p className="text-muted-foreground">아직 혼합 전략 스윕 데이터가 없습니다.</p>
      ) : (
        <div className="grid gap-3">
          {rows.map((row, i) => (
            <Card key={`${row.signal_set_name}-${row.market}-${row.timeframe}`}>
              <CardHeader>
                <CardTitle className="flex items-center justify-between text-base">
                  <span>
                    #{i + 1} {row.market} · {row.timeframe}
                  </span>
                  <span className={row.return_rate && row.return_rate > 0 ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'}>
                    {row.return_rate?.toFixed(2) ?? '-'}%
                  </span>
                </CardTitle>
              </CardHeader>
              <CardContent className="text-sm text-muted-foreground">
                전략: {row.signal_set_name} · Sharpe: {row.sharpe?.toFixed(2) ?? '-'} · MDD: {row.max_drawdown?.toFixed(2) ?? '-'}%
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: 수동 확인**

Run: `npm run dev` 상태에서 `http://localhost:3000/ranking` 접속.
Expected: "아직 혼합 전략 스윕 데이터가 없습니다" 문구가 보임.

- [ ] **Step 3: 커밋**

```bash
git add frontend/app/ranking/page.tsx
git commit -m "feat: implement combined-strategy market ranking view"
```

---

### Task 13: 화면③ 조합별 시간대 수익률 추이 그래프

**Files:**
- Modify: `frontend/app/history/page.tsx`
- Create: `frontend/components/ComboHistoryChart.tsx`

**Interfaces:**
- Consumes: `getCombos()`, `getHistory(combo)` (Task 10)

- [ ] **Step 1: 클라이언트 컴포넌트 작성 (콤보 선택 + recharts 그래프)**

`frontend/components/ComboHistoryChart.tsx`:
```typescript
'use client';

import { useEffect, useState } from 'react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';
import { getCombos, getHistory } from '@/lib/api/eda';
import type { Combo, SweepResult } from '@/lib/types/eda';

function comboKey(c: Combo): string {
  return `${c.signal_set_name}|${c.market}|${c.timeframe}|${c.is_combined}`;
}

export default function ComboHistoryChart() {
  const [combos, setCombos] = useState<Combo[]>([]);
  const [selectedKey, setSelectedKey] = useState<string>('');
  const [history, setHistory] = useState<SweepResult[]>([]);

  useEffect(() => {
    getCombos().then((cs) => {
      setCombos(cs);
      if (cs.length > 0) setSelectedKey(comboKey(cs[0]));
    });
  }, []);

  useEffect(() => {
    const combo = combos.find((c) => comboKey(c) === selectedKey);
    if (!combo) return;
    getHistory(combo).then(setHistory);
  }, [selectedKey, combos]);

  if (combos.length === 0) {
    return <p className="text-muted-foreground">아직 스윕 데이터가 없습니다.</p>;
  }

  return (
    <div>
      <select
        className="mb-4 rounded border px-2 py-1 text-sm"
        value={selectedKey}
        onChange={(e) => setSelectedKey(e.target.value)}
      >
        {combos.map((c) => (
          <option key={comboKey(c)} value={comboKey(c)}>
            {c.signal_set_name}{c.is_combined ? '(혼합)' : ''} / {c.market} / {c.timeframe}
          </option>
        ))}
      </select>

      <ResponsiveContainer width="100%" height={320}>
        <LineChart data={history}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="swept_at" tick={{ fontSize: 11 }} />
          <YAxis tick={{ fontSize: 11 }} />
          <Tooltip />
          <Line type="monotone" dataKey="return_rate" stroke="#3b82f6" name="수익률(%)" />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
```

- [ ] **Step 2: 페이지에서 사용**

`frontend/app/history/page.tsx` 전체를 다음으로 교체:
```typescript
import ComboHistoryChart from '@/components/ComboHistoryChart';

export default function HistoryPage() {
  return (
    <div>
      <h1 className="text-lg font-semibold mb-4">조합별 시간대 수익률 추이</h1>
      <ComboHistoryChart />
    </div>
  );
}
```

- [ ] **Step 3: 수동 확인**

Run: `npm run dev` 상태에서 `http://localhost:3000/history` 접속.
Expected: "아직 스윕 데이터가 없습니다" 문구가 보임(스윕 실행 전이므로 정상). 콘솔에 에러 없음.

- [ ] **Step 4: 커밋**

```bash
git add frontend/app/history/page.tsx frontend/components/ComboHistoryChart.tsx
git commit -m "feat: implement combo return-rate trend chart with recharts"
```

---

### Task 14: 화면④ 백테스트 상세 (equity curve / 거래내역)

**Files:**
- Modify: `frontend/app/backtests/[runId]/page.tsx`
- Create: `frontend/components/EquityCurveChart.tsx`

**Interfaces:**
- Consumes: `getBacktestDetail(runId)` (Task 10)

- [ ] **Step 1: equity curve 차트 컴포넌트 (backtesting_1의 검증된 패턴 이식)**

`frontend/components/EquityCurveChart.tsx`:
```typescript
'use client';

import { useEffect, useRef } from 'react';
import { createChart, LineSeries } from 'lightweight-charts';
import type { EquityPoint } from '@/lib/types/eda';

export default function EquityCurveChart({ equityCurve }: { equityCurve: EquityPoint[] }) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current || equityCurve.length === 0) return;

    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: 260,
      timeScale: { timeVisible: true, borderColor: '#d1d5db' },
      rightPriceScale: { borderColor: '#d1d5db' },
    });

    const series = chart.addSeries(LineSeries, { color: '#3b82f6', lineWidth: 2 });
    const data = equityCurve
      .map((p) => ({ time: p.timestamp.split('T')[0] as `${number}-${number}-${number}`, value: p.value }))
      .sort((a, b) => String(a.time).localeCompare(String(b.time)))
      .filter((p, i, arr) => i === 0 || p.time !== arr[i - 1].time);
    series.setData(data);
    chart.timeScale().fitContent();

    const onResize = () => {
      if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth });
    };
    const observer = new ResizeObserver(onResize);
    observer.observe(containerRef.current);

    return () => {
      observer.disconnect();
      chart.remove();
    };
  }, [equityCurve]);

  return <div ref={containerRef} className="w-full rounded-lg overflow-hidden border" />;
}
```

- [ ] **Step 2: 상세 페이지 구현**

`frontend/app/backtests/[runId]/page.tsx` 전체를 다음으로 교체:
```typescript
import { getBacktestDetail } from '@/lib/api/eda';
import EquityCurveChart from '@/components/EquityCurveChart';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';

export default async function BacktestDetailPage({ params }: { params: { runId: string } }) {
  const detail = await getBacktestDetail(params.runId);

  return (
    <div>
      <h1 className="text-lg font-semibold mb-2">백테스트 상세</h1>
      <p className="text-sm text-muted-foreground mb-4">
        최종 자산: {detail.final_value.toFixed(0)} · Sharpe: {detail.sharpe?.toFixed(2) ?? '-'} · MDD: {detail.max_drawdown?.toFixed(2) ?? '-'}%
      </p>

      <h2 className="font-medium mb-2">자산 곡선</h2>
      <EquityCurveChart equityCurve={detail.equity_curve} />

      <h2 className="font-medium mt-6 mb-2">거래 내역 ({detail.trades.length}건)</h2>
      {detail.trades.length === 0 ? (
        <p className="text-muted-foreground">거래 내역이 없습니다.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>진입</TableHead>
              <TableHead>청산</TableHead>
              <TableHead>수익률(%)</TableHead>
              <TableHead>보유기간</TableHead>
              <TableHead>강제청산</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {detail.trades.map((t, i) => (
              <TableRow key={i}>
                <TableCell>{t.entryTime}</TableCell>
                <TableCell>{t.exitTime}</TableCell>
                <TableCell className={t.returnRate >= 0 ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'}>
                  {t.returnRate.toFixed(2)}
                </TableCell>
                <TableCell>{t.holdingPeriod}</TableCell>
                <TableCell>{t.forceClosed ? 'Y' : ''}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
```

- [ ] **Step 3: 수동 확인**

Run: `npm run dev` 상태에서 존재하지 않는 `http://localhost:3000/backtests/nonexistent` 접속.
Expected: FastAPI가 404를 반환하고 Next.js가 에러 페이지를 보여줌(정상 동작 확인 — 실제 데이터 확인은 Task 15에서).

- [ ] **Step 4: 커밋**

```bash
git add frontend/app/backtests/[runId]/page.tsx frontend/components/EquityCurveChart.tsx
git commit -m "feat: implement backtest detail view with equity curve and trade table"
```

---

### Task 15: 수동 통합 스모크 테스트 (실제 스윕 + 대시보드 육안 확인)

**Files:**
- Create: `scripts/run_eda_sweep.py`

- [ ] **Step 1: 스윕 실행 스크립트 작성**

`scripts/run_eda_sweep.py`:
```python
"""
수동 통합 스모크 테스트. 실제 Upbit API로 소수 코인을 스윕하고 결과를 sweep_history에 채운다.
Run: python scripts/run_eda_sweep.py
"""
from datetime import datetime, timedelta, timezone

from engine.sweep import run_sweep
from signals import SIGNAL_REGISTRY


def main() -> None:
    end = datetime.now(timezone.utc) - timedelta(days=1)
    start = end - timedelta(days=90)

    solo_sets = [(name, [signal], False) for name, signal in SIGNAL_REGISTRY.items()]
    combined_set = ("mixed_all", list(SIGNAL_REGISTRY.values()), True)

    run_sweep(
        markets=["KRW-BTC", "KRW-ETH"],
        timeframes=["days"],
        signal_sets=[*solo_sets, combined_set],
        start=start,
        end=end,
    )
    print("스윕 완료. FastAPI(uvicorn backend.main:app --port 8000)와 "
          "Next.js(cd frontend && npm run dev)를 띄운 뒤 http://localhost:3000 에서 확인하세요.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 실행 및 확인**

Run: `PYTHONPATH=. python scripts/run_eda_sweep.py`

확인할 것:
- 콘솔에 "스윕 완료" 출력, 실패한 조합이 있다면 `[run_sweep] 건너뜀 ...` 로그도 확인
- `data/backtest_results.db`에 `sweep_history` 테이블이 채워졌는지: `sqlite3 data/backtest_results.db "SELECT COUNT(*) FROM sweep_history"`

- [ ] **Step 3: 대시보드 육안 확인**

Run: 터미널 1 `uvicorn backend.main:app --port 8000`, 터미널 2 `cd frontend && npm run dev`. 브라우저로 다음을 확인:
- `http://localhost:3000` — 전략×코인×봉타입 수익률 테이블에 실제 데이터가 채워짐
- `http://localhost:3000/ranking` — 혼합전략(`mixed_all`) 행이 코인별로 정렬돼 보임
- `http://localhost:3000/history` — 드롭다운으로 조합을 선택하면 추이 그래프가 그려짐(스윕을 한 번만 돌렸다면 점 1개)
- 히트맵 테이블의 "보기" 링크를 클릭 → `/backtests/{run_id}`에서 자산 곡선과 거래 내역이 보임
- `http://localhost:3000/model-accuracy` — 플레이스홀더 문구가 보임

- [ ] **Step 4: 커밋**

```bash
git add scripts/run_eda_sweep.py
git commit -m "test: add manual end-to-end sweep script and dashboard smoke test"
```

---

## Self-Review 결과

- **스펙 커버리지**: `2026-07-12-upbit-strategy-eda-dashboard-design.md`의 신호 4개+레지스트리(Task 2-6), 스윕 프레임워크(Task 8), append-only 히스토리(Task 7), FastAPI+Next.js 5탭 대시보드(Task 9-14), 모델 정확도 플레이스홀더(Task 10)를 각각 커버.
- **레지스트리 확장성**: Task 6에서 더미 신호를 등록만 하고 다른 모듈 수정 없이 동작하는지 직접 테스트로 검증 — 사용자가 요청한 확장성 요구사항을 코드가 아니라 테스트로 증명.
- **프로토타이핑으로 수정한 설계 결정**: 신호를 "순간 교차 이벤트"가 아닌 "현재 상태" 기준으로 정의하도록 변경(사용자 확인 완료) — Global Constraints에 반영.
- **범위에서 의도적으로 제외한 것**: 대시보드에서 직접 스윕을 트리거하는 기능은 만들지 않음(스크립트로 수동 실행) — 설계 문서의 "향후 확장" 항목과 일치.
