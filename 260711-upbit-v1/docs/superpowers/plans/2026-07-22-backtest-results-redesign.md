# 백테스트 결과 탭 리디자인 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 백테스트 결과 탭의 수익률 색상을 한국식(플러스=빨강/마이너스=파랑)으로 통일하고, 상세 페이지에 12종 성과 지표 그리드·코인/기간 표기·매수가/매도가/수익금·타임스탬프 포맷·상태 명확화를 추가하고, 자산 곡선 차트를 캔들+진입/청산 마커 가격 차트로 교체한다.

**Architecture:** 백엔드는 `engine/metrics.py`(신규, `backtesting_1`에서 포팅)로 12종 지표를 상세 페이지 요청 시점에 계산하고, `engine/cache.py::load_result()`를 확장해 market/timeframe/기간 정보를 함께 반환하며, `backend/main.py`의 상세 엔드포인트가 이를 조합해 캔들 데이터(`ohlcv`)까지 응답에 포함시킨다. 프론트는 공용 색상/포맷 유틸을 만들어 4개 페이지에 적용하고, `lightweight-charts`의 `CandlestickSeries`+`createSeriesMarkers`로 새 `PriceChart` 컴포넌트를 만들어 기존 `EquityCurveChart`를 대체한다.

**Tech Stack:** Python(FastAPI, backtrader, pandas), pytest, Next.js/React(TypeScript), lightweight-charts v5, Tailwind.

## Global Constraints

- 색상 컨벤션(전역): 수익률 등 손익 관련 값은 플러스=`text-red-600 dark:text-red-400`, 마이너스=`text-blue-600 dark:text-blue-400`, 0/null=중립(빈 문자열).
- 성과 지표는 DB에 저장하지 않고 상세 페이지 요청마다 재계산한다(스펙 문서 결정 사항).
- 진입/청산 마커 색상은 캔들 몸통 색(빨강/파랑)과 겹치지 않는 별도 색을 쓴다.
- 참고 스펙: `docs/superpowers/specs/2026-07-21-backtest-results-redesign-design.md`
- 참고 소스: `C:\Users\jungm\project\backtesting_1\backend\app\engine\metrics.py`, `C:\Users\jungm\project\backtesting_1\frontend\components\charts\PriceChart.tsx`

---

### Task 1: `engine/metrics.py` — 성과 지표 계산 모듈 신규 작성

**Files:**
- Create: `engine/metrics.py`
- Test: `tests/test_metrics.py`

**Interfaces:**
- Produces: `calculate_metrics(equity_curve: list[dict], trades: list[dict], initial_capital: float, df: pd.DataFrame, timeframe: str = "days") -> dict`. 반환 dict 키: `total_return, cagr, mdd, sharpe_ratio, sortino_ratio, calmar_ratio, win_rate, profit_factor, avg_holding_period, max_consecutive_loss, buy_and_hold_return, total_trades` (전부 `float`, `total_trades`/`max_consecutive_loss`만 `int`).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_metrics.py` 새로 작성:

```python
import pandas as pd
import pytest

from engine.metrics import calculate_metrics


def _df(closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="D", tz="UTC")
    return pd.DataFrame({
        "candle_time": idx,
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": [1.0] * len(closes),
    })


def test_total_return_and_cagr():
    equity_curve = [
        {"timestamp": "2026-01-01T00:00:00", "value": 10000.0},
        {"timestamp": "2026-07-01T00:00:00", "value": 11000.0},
    ]
    result = calculate_metrics(equity_curve, [], 10000.0, _df([100, 110]), "days")
    assert result["total_return"] == 10.0
    assert result["cagr"] > 0


def test_mdd_is_max_drawdown_from_peak():
    equity_curve = [
        {"timestamp": "2026-01-01T00:00:00", "value": 10000.0},
        {"timestamp": "2026-01-02T00:00:00", "value": 12000.0},
        {"timestamp": "2026-01-03T00:00:00", "value": 9000.0},
    ]
    result = calculate_metrics(equity_curve, [], 10000.0, _df([100, 100, 100]), "days")
    assert result["mdd"] == pytest.approx(-25.0)  # (9000-12000)/12000*100


def test_win_rate_and_profit_factor_from_trades():
    equity_curve = [{"timestamp": "2026-01-01T00:00:00", "value": 10000.0}]
    trades = [
        {"pnl": 100.0, "holdingPeriod": 2},
        {"pnl": -50.0, "holdingPeriod": 3},
        {"pnl": 200.0, "holdingPeriod": 1},
    ]
    result = calculate_metrics(equity_curve, trades, 10000.0, _df([100]), "days")
    assert result["total_trades"] == 3
    assert result["win_rate"] == pytest.approx(200 / 3)
    assert result["profit_factor"] == pytest.approx(300 / 50)


def test_max_consecutive_loss():
    equity_curve = [{"timestamp": "2026-01-01T00:00:00", "value": 10000.0}]
    trades = [{"pnl": p, "holdingPeriod": 1} for p in [10, -5, -3, -1, 8, -2]]
    result = calculate_metrics(equity_curve, trades, 10000.0, _df([100]), "days")
    assert result["max_consecutive_loss"] == 3


def test_buy_and_hold_return_from_df():
    equity_curve = [{"timestamp": "2026-01-01T00:00:00", "value": 10000.0}]
    result = calculate_metrics(equity_curve, [], 10000.0, _df([100, 150]), "days")
    assert result["buy_and_hold_return"] == pytest.approx(50.0)


def test_empty_equity_curve_returns_zeroed_metrics():
    result = calculate_metrics([], [], 10000.0, _df([100]), "days")
    assert result["total_trades"] == 0
    assert result["total_return"] == 0.0
    assert result["max_consecutive_loss"] == 0
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_metrics.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.metrics'`

- [ ] **Step 3: `engine/metrics.py` 구현**

`C:\Users\jungm\project\backtesting_1\backend\app\engine\metrics.py`를 포팅하되 `monthly_returns`/`_monthly_returns`는 이번 요청 범위 밖이라 제외:

```python
"""
engine/metrics.py

백테스트 결과(equity_curve, trades)로부터 12종 성과 지표를 계산.
C:\\Users\\jungm\\project\\backtesting_1의 backend/app/engine/metrics.py를 참고해
포팅했다. 원본과 다른 점: monthly_returns(월별 수익률)는 이번 요청 범위 밖이라 제외.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

# 타임프레임별 1 bar당 분(minute) 수
_TIMEFRAME_MINUTES: dict[str, float] = {
    "minutes1": 1,
    "minutes5": 5,
    "minutes15": 15,
    "minutes30": 30,
    "minutes60": 60,
    "minutes240": 240,
    "days": 1440,
}


def bars_to_days(bars: int | float, timeframe: str) -> float:
    """bar 수를 일(day) 단위로 변환."""
    minutes_per_bar = _TIMEFRAME_MINUTES.get(timeframe, 1440)
    return bars * minutes_per_bar / 1440.0


def calculate_metrics(
    equity_curve: list[dict],
    trades: list[dict],
    initial_capital: float,
    df: pd.DataFrame,
    timeframe: str = "days",
) -> dict:
    """
    성과 지표 계산.

    Args:
        equity_curve: [{'timestamp': str, 'value': float}, ...]
        trades: [{'entryTime', 'exitTime', 'entryPrice', 'exitPrice',
                  'returnRate', 'holdingPeriod', 'pnl'}, ...]
        initial_capital: 초기 자본
        df: OHLCV DataFrame (buy_and_hold_return 계산용)

    Returns:
        {total_return, cagr, mdd, sharpe_ratio, sortino_ratio, calmar_ratio,
         win_rate, profit_factor, avg_holding_period, max_consecutive_loss,
         buy_and_hold_return, total_trades}
    """
    if not equity_curve:
        return _empty_metrics()

    values = pd.Series([float(e["value"]) for e in equity_curve])
    final_val = float(values.iloc[-1])

    total_return = _safe_div(final_val - initial_capital, initial_capital) * 100.0

    try:
        t0 = pd.Timestamp(equity_curve[0]["timestamp"])
        t1 = pd.Timestamp(equity_curve[-1]["timestamp"])
        days = max((t1 - t0).days, 1)
    except Exception:
        days = 1
    ratio = final_val / initial_capital if initial_capital > 0 else 1.0
    cagr = (ratio ** (365.0 / days) - 1.0) * 100.0 if ratio > 0 else 0.0

    cummax = values.cummax()
    drawdown = (values - cummax) / cummax * 100.0
    mdd = float(drawdown.min()) if not drawdown.empty else 0.0

    period_returns = values.pct_change().dropna()
    sharpe_ratio = _sharpe(period_returns)
    sortino_ratio = _sortino(period_returns)
    calmar_ratio = _safe_div(cagr, abs(mdd)) if mdd != 0 else 0.0

    total_trades = len(trades)
    win_rate = 0.0
    profit_factor = 0.0
    avg_holding_period = 0.0
    max_consecutive_loss = 0

    if trades:
        pnls = [float(t.get("pnl", 0.0)) for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        win_rate = (len(wins) / total_trades * 100.0) if total_trades else 0.0
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        profit_factor = _safe_div(gross_profit, gross_loss) if gross_loss > 0 else 999.0

        holding_periods = [bars_to_days(int(t.get("holdingPeriod", 0)), timeframe) for t in trades]
        avg_holding_period = float(np.mean(holding_periods)) if holding_periods else 0.0

        max_consecutive_loss = _max_consecutive_loss(pnls)

    buy_and_hold_return = 0.0
    if not df.empty and "close" in df.columns:
        first_close = float(df["close"].iloc[0])
        last_close = float(df["close"].iloc[-1])
        if first_close > 0:
            buy_and_hold_return = (last_close - first_close) / first_close * 100.0

    return {
        "total_return": round(total_return, 4),
        "cagr": round(cagr, 4),
        "mdd": round(mdd, 4),
        "sharpe_ratio": round(sharpe_ratio, 4),
        "sortino_ratio": round(sortino_ratio, 4),
        "calmar_ratio": round(calmar_ratio, 4),
        "win_rate": round(win_rate, 4),
        "profit_factor": round(profit_factor, 4),
        "avg_holding_period": round(avg_holding_period, 2),
        "max_consecutive_loss": max_consecutive_loss,
        "buy_and_hold_return": round(buy_and_hold_return, 4),
        "total_trades": total_trades,
    }


def _empty_metrics() -> dict:
    return {
        "total_return": 0.0, "cagr": 0.0, "mdd": 0.0,
        "sharpe_ratio": 0.0, "sortino_ratio": 0.0, "calmar_ratio": 0.0,
        "win_rate": 0.0, "profit_factor": 0.0, "avg_holding_period": 0.0,
        "max_consecutive_loss": 0, "buy_and_hold_return": 0.0, "total_trades": 0,
    }


def _safe_div(a: float, b: float) -> float:
    return a / b if b != 0 else 0.0


def _sharpe(period_returns: pd.Series) -> float:
    if period_returns.empty or period_returns.std() == 0:
        return 0.0
    return float(period_returns.mean() / period_returns.std() * math.sqrt(252))


def _sortino(period_returns: pd.Series) -> float:
    if period_returns.empty:
        return 0.0
    neg = period_returns[period_returns < 0]
    if neg.empty or neg.std() == 0:
        return 0.0
    return float(period_returns.mean() / neg.std() * math.sqrt(252))


def _max_consecutive_loss(pnls: list[float]) -> int:
    max_consec = 0
    current = 0
    for p in pnls:
        if p <= 0:
            current += 1
            max_consec = max(max_consec, current)
        else:
            current = 0
    return max_consec


__all__ = ["calculate_metrics", "bars_to_days"]
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_metrics.py -v`
Expected: 6개 테스트 모두 PASS

- [ ] **Step 5: 커밋**

```bash
git add engine/metrics.py tests/test_metrics.py
git commit -m "$(cat <<'EOF'
feat: 백테스트 성과 지표(CAGR/소르티노/칼마/승률 등) 계산 모듈 추가

backtesting_1의 metrics.py를 포팅해 상세 페이지에 필요한 12종 성과
지표를 계산하는 engine/metrics.py를 추가.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `engine/runner.py` — 강제청산 거래 수수료 버그 수정

**Files:**
- Modify: `engine/runner.py:174-198`
- Test: `tests/test_runner.py`

**Interfaces:**
- Produces: `_build_forced_close_trade(entry_time: str, entry_price: float, size: float, baropen: int, last_close: float, last_dt: str, total_bars: int, commission_rate: float) -> dict` — 진입+청산 양쪽 수수료를 차감한 강제청산 거래 dict.

**배경:** 지금 강제청산(`forceClosed: True`) 거래는 청산 시 수수료만 빼고 진입 시 수수료를 빠뜨리고 있다(`engine/runner.py:184-185`). 정상 청산 거래는 backtrader의 `trade.pnlcomm`이 양쪽 수수료를 자동으로 뺀 값이라 이 버그가 없다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_runner.py`에 추가 (파일 상단 import에 `_build_forced_close_trade` 추가):

```python
from engine.runner import _build_forced_close_trade, run_backtest
```

파일 끝에 추가:

```python
def test_forced_close_trade_deducts_entry_and_exit_commission():
    trade = _build_forced_close_trade(
        entry_time="2026-01-01T00:00:00", entry_price=100.0, size=2.0,
        baropen=0, last_close=110.0, last_dt="2026-01-10T00:00:00",
        total_bars=10, commission_rate=0.01,
    )
    pnl_gross = (110.0 - 100.0) * 2.0
    entry_commission = 100.0 * 2.0 * 0.01
    exit_commission = 110.0 * 2.0 * 0.01
    expected_pnl = pnl_gross - entry_commission - exit_commission
    assert trade["pnl"] == round(expected_pnl, 4)
    assert trade["forceClosed"] is True
    assert trade["holdingPeriod"] == 9
    assert trade["entryPrice"] == 100.0
    assert trade["exitPrice"] == 110.0
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_runner.py -v`
Expected: FAIL — `ImportError: cannot import name '_build_forced_close_trade' from 'engine.runner'`

- [ ] **Step 3: 함수 추출 + 수수료 버그 수정**

`engine/runner.py:174-198`의 현재 코드:

```python
    open_trades = strategy.analyzers.trades.get_open_trades()
    if open_trades:
        last_close = float(df_bt["close"].iloc[-1])
        last_dt = df_bt.index[-1].isoformat()
        total_bars = len(df_bt)

        for ot in open_trades:
            entry_price = ot["entryPrice"]
            size = ot["size"]
            pnl_gross = (last_close - entry_price) * size
            commission_cost = last_close * size * commission_rate
            pnlcomm = pnl_gross - commission_cost
            return_rate = (pnlcomm / (entry_price * size) * 100) if (entry_price and size) else 0.0
            holding_period = max(total_bars - 1 - ot["baropen"], 0)

            trades.append({
                "entryTime": ot["entryTime"],
                "exitTime": last_dt,
                "entryPrice": round(entry_price, 8),
                "exitPrice": round(last_close, 8),
                "returnRate": round(return_rate, 4),
                "holdingPeriod": holding_period,
                "pnl": round(pnlcomm, 4),
                "forceClosed": True,
            })
```

다음으로 교체(`_build_forced_close_trade`를 `run_backtest` 함수 정의보다 앞, 모듈 상단 클래스 정의들 뒤에 추가):

```python
    open_trades = strategy.analyzers.trades.get_open_trades()
    if open_trades:
        last_close = float(df_bt["close"].iloc[-1])
        last_dt = df_bt.index[-1].isoformat()
        total_bars = len(df_bt)

        for ot in open_trades:
            trades.append(_build_forced_close_trade(
                entry_time=ot["entryTime"],
                entry_price=ot["entryPrice"],
                size=ot["size"],
                baropen=ot["baropen"],
                last_close=last_close,
                last_dt=last_dt,
                total_bars=total_bars,
                commission_rate=commission_rate,
            ))
```

그리고 `def run_backtest(` 정의 바로 위에 새 함수 추가:

```python
def _build_forced_close_trade(
    entry_time: str,
    entry_price: float,
    size: float,
    baropen: int,
    last_close: float,
    last_dt: str,
    total_bars: int,
    commission_rate: float,
) -> dict:
    """백테스트 종료 시점까지 매도 조건을 만족하지 못한 포지션을, 리포팅을 위해
    마지막 봉 종가로 강제 청산 처리한 거래 기록을 만든다. 진입/청산 양쪽 수수료를
    모두 차감해야 정상 청산 거래(trade.pnlcomm)와 계산 방식이 일치한다."""
    pnl_gross = (last_close - entry_price) * size
    entry_commission = entry_price * size * commission_rate
    exit_commission = last_close * size * commission_rate
    pnlcomm = pnl_gross - entry_commission - exit_commission
    return_rate = (pnlcomm / (entry_price * size) * 100) if (entry_price and size) else 0.0
    holding_period = max(total_bars - 1 - baropen, 0)

    return {
        "entryTime": entry_time,
        "exitTime": last_dt,
        "entryPrice": round(entry_price, 8),
        "exitPrice": round(last_close, 8),
        "returnRate": round(return_rate, 4),
        "holdingPeriod": holding_period,
        "pnl": round(pnlcomm, 4),
        "forceClosed": True,
    }
```

파일 끝의 `__all__ = ["run_backtest"]`는 그대로 둔다(밑줄 시작 함수는 `from engine.runner import _build_forced_close_trade`로 직접 import 가능하며 `__all__`은 `import *`에만 영향).

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_runner.py -v`
Expected: 2개 테스트 모두 PASS (`test_run_backtest_buy_and_hold_once`, `test_forced_close_trade_deducts_entry_and_exit_commission`)

- [ ] **Step 5: 커밋**

```bash
git add engine/runner.py tests/test_runner.py
git commit -m "$(cat <<'EOF'
fix: 강제청산 거래의 진입 수수료 누락 수정

기간 종료 시 강제 청산되는 거래(forceClosed=True)가 청산 수수료만
빼고 진입 수수료를 빠뜨리던 버그를 수정. 계산 로직을
_build_forced_close_trade로 분리해 단위 테스트 가능하게 함.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `engine/cache.py` — `load_result()`에 market/timeframe/기간 정보 추가

**Files:**
- Modify: `engine/cache.py:135-157`
- Test: `tests/test_cache.py`

**Interfaces:**
- Produces: `load_result(run_id)`가 이제 `market`, `timeframe`, `start`, `end`, `initial_capital` 키를 추가로 반환.

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_cache.py` 끝에 추가:

```python
def test_load_result_includes_market_timeframe_and_initial_capital(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    save_result(
        run_id="r1", strategy_name="ConditionTreeStrategy", strategy_params={},
        market="KRW-BTC", timeframe="minutes15",
        start=datetime(2026, 4, 22, tzinfo=timezone.utc), end=datetime(2026, 7, 21, tzinfo=timezone.utc),
        risk_config={"initial_capital": 1_000_000},
        result={"final_value": 1_100_000.0, "sharpe": 1.0, "max_drawdown": 2.0, "equity_curve": [], "trades": []},
    )

    loaded = load_result("r1")
    assert loaded["market"] == "KRW-BTC"
    assert loaded["timeframe"] == "minutes15"
    assert loaded["start"] == datetime(2026, 4, 22, tzinfo=timezone.utc).isoformat()
    assert loaded["end"] == datetime(2026, 7, 21, tzinfo=timezone.utc).isoformat()
    assert loaded["initial_capital"] == 1_000_000
```

- [x] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_cache.py -v -k test_load_result_includes_market_timeframe_and_initial_capital`
Expected: FAIL — `KeyError: 'market'`

- [x] **Step 3: `load_result()` 수정**

`engine/cache.py:135-157`의 현재 코드:

```python
def load_result(run_id: str) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT final_value, sharpe, max_drawdown, equity_curve_json, trades_json "
            "FROM backtest_results WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return None

    final_value, sharpe, max_drawdown, equity_curve_json, trades_json = row
    return {
        "final_value": final_value,
        "sharpe": sharpe,
        "max_drawdown": max_drawdown,
        "equity_curve": json.loads(equity_curve_json),
        "trades": json.loads(trades_json),
        "from_cache": True,
    }
```

다음으로 교체(기존 `list_backtest_runs`가 쓰는 것과 같은 JOIN 패턴 — `save_result`가 항상 두 테이블에 같은 트랜잭션으로 쓰기 때문에 안전):

```python
def load_result(run_id: str) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT res.final_value, res.sharpe, res.max_drawdown, res.equity_curve_json, res.trades_json, "
            "       r.market, r.timeframe, r.start, r.end, r.risk_config_json "
            "FROM backtest_results res "
            "JOIN backtest_runs r ON r.id = res.run_id "
            "WHERE res.run_id = ?",
            (run_id,),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return None

    (final_value, sharpe, max_drawdown, equity_curve_json, trades_json,
     market, timeframe, start, end, risk_config_json) = row
    initial_capital = json.loads(risk_config_json).get("initial_capital")
    return {
        "final_value": final_value,
        "sharpe": sharpe,
        "max_drawdown": max_drawdown,
        "equity_curve": json.loads(equity_curve_json),
        "trades": json.loads(trades_json),
        "market": market,
        "timeframe": timeframe,
        "start": start,
        "end": end,
        "initial_capital": initial_capital,
        "from_cache": True,
    }
```

- [x] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_cache.py -v`
Expected: 모든 테스트 PASS (기존 `test_save_then_load_round_trips` 등도 그대로 통과해야 함 — 필드 추가는 기존 assert를 깨지 않음)

- [x] **Step 5: 커밋**

```bash
git add engine/cache.py tests/test_cache.py
git commit -m "$(cat <<'EOF'
feat: load_result가 market/timeframe/기간/초기자본도 반환하도록 확장

상세 페이지에서 코인명 표기와 캔들 재조회, 성과 지표 계산에 필요한
정보를 backtest_runs와 JOIN해서 함께 반환.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: `backend/main.py` — 상세 엔드포인트가 metrics/ohlcv/market 반환하도록 재구성

**Files:**
- Modify: `backend/main.py` (import 구역, `get_backtest_detail` 함수)
- Test: `tests/test_backend.py`

**Interfaces:**
- Consumes: `calculate_metrics(equity_curve, trades, initial_capital, df, timeframe)` (Task 1), `load_result(run_id)`가 반환하는 `market/timeframe/start/end/initial_capital` (Task 3), `get_candles(market, timeframe, start_dt, end_dt) -> pd.DataFrame`(이미 존재, `upbit_data_service.py`).
- Produces: `GET /api/v1/backtests/{run_id}`가 `{market, timeframe, start, end, final_value, metrics, ohlcv, trades}` 반환.

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_backend.py`의 기존 `test_backtest_detail_returns_result_for_known_run`(90~106행)을 다음으로 교체:

```python
def test_backtest_detail_returns_result_for_known_run(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch)
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
    body = resp.json()
    assert body["final_value"] == 10500.0
    assert body["market"] == "KRW-BTC"
    assert body["timeframe"] == "days"
    assert body["metrics"]["total_trades"] == 0
    assert isinstance(body["ohlcv"], list)
    assert len(body["ohlcv"]) > 0
    # candle_time이 naive로 정규화됐는지 확인 — tz-aware면 "+00:00" 같은 오프셋이 붙어있다.
    # trades의 entryTime/exitTime(backtrader가 tz를 벗겨낸 naive 문자열)과 기준을 맞춰야
    # 프론트에서 new Date(...)로 파싱할 때 캔들과 마커 위치가 어긋나지 않는다.
    assert "+00:00" not in body["ohlcv"][0]["time"]
```

주의: `_patch_get_candles`(199~203행 부근)는 이미 파일에 있으며 `backend_module.get_candles`를 패치한다 — 새로 만들 필요 없음.

- [x] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_backend.py -v -k test_backtest_detail_returns_result_for_known_run`
Expected: FAIL — `KeyError: 'metrics'` (또는 `assert 'KRW-BTC' == ...` 관련 실패 — 지금 `get_backtest_detail`은 `load_result` 결과를 그대로 반환하기만 함)

- [x] **Step 3: import 추가**

`backend/main.py` 상단의 다음 import 블록:

```python
from engine.cache import (
    delete_backtest_run,
    list_backtest_runs,
    list_combined_ranking,
    list_distinct_combos,
    list_latest_sweep_results,
    list_sweep_history,
    load_result,
    run_backtest_cached,
)
```

바로 아래에 추가:

```python
from engine.metrics import calculate_metrics
```

- [x] **Step 4: 엔드포인트 재구성**

`backend/main.py`의 현재 코드(219~224행):

```python
@app.get("/api/v1/backtests/{run_id}")
def get_backtest_detail(run_id: str) -> dict:
    result = load_result(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="해당 run_id의 백테스트 결과를 찾을 수 없습니다")
    return result
```

다음으로 교체:

```python
@app.get("/api/v1/backtests/{run_id}")
def get_backtest_detail(run_id: str) -> dict:
    result = load_result(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="해당 run_id의 백테스트 결과를 찾을 수 없습니다")

    start_dt = datetime.fromisoformat(result["start"])
    end_dt = datetime.fromisoformat(result["end"])
    df = get_candles(result["market"], result["timeframe"], start_dt, end_dt)

    metrics = calculate_metrics(
        equity_curve=result["equity_curve"],
        trades=result["trades"],
        initial_capital=result["initial_capital"],
        df=df,
        timeframe=result["timeframe"],
    )

    # candle_time은 tz-aware(UTC)인데 trades의 entryTime/exitTime은 backtrader가
    # tz를 벗겨낸 naive 문자열이다(engine/runner.py의 df_bt.index.tz_localize(None)).
    # 프론트에서 new Date(...)로 파싱할 때 tz 표기 유무가 섞이면 로컬 타임존만큼
    # 어긋나 보이므로, 여기서도 naive로 맞춰 캔들/거래 시각의 기준을 통일한다.
    df_chart = df.copy()
    if df_chart["candle_time"].dt.tz is not None:
        df_chart["candle_time"] = df_chart["candle_time"].dt.tz_localize(None)
    ohlcv = [
        {
            "time": row.candle_time.isoformat(),
            "open": float(row.open),
            "high": float(row.high),
            "low": float(row.low),
            "close": float(row.close),
        }
        for row in df_chart.itertuples()
    ]

    return {
        "market": result["market"],
        "timeframe": result["timeframe"],
        "start": result["start"],
        "end": result["end"],
        "final_value": result["final_value"],
        "metrics": metrics,
        "ohlcv": ohlcv,
        "trades": result["trades"],
    }
```

- [x] **Step 5: 테스트 통과 확인**

Run: `python -m pytest tests/test_backend.py -v`
Expected: 모든 테스트 PASS (특히 `test_backtest_detail_returns_result_for_known_run`, `test_run_backtest_returns_run_id_and_is_retrievable`)

- [x] **Step 6: 전체 백엔드 테스트 스위트 실행**

Run: `python -m pytest`
Expected: 전부 PASS (기존 96개 + Task 1~4에서 추가한 테스트)

- [x] **Step 7: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "$(cat <<'EOF'
feat: 상세 엔드포인트가 성과 지표/캔들 데이터/코인 정보 반환하도록 확장

GET /api/v1/backtests/{run_id}가 이제 market/timeframe/기간,
calculate_metrics() 결과, 캔들 차트용 ohlcv 배열을 함께 반환한다.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: 프론트 공용 유틸(`return-rate-color.ts`, `format.ts`) + 목록/heatmap/ranking 페이지 색상 통일

**Files:**
- Create: `frontend/lib/return-rate-color.ts`
- Create: `frontend/lib/format.ts`
- Modify: `frontend/app/backtests/page.tsx`
- Modify: `frontend/app/heatmap/page.tsx`
- Modify: `frontend/app/ranking/page.tsx`

**Interfaces:**
- Produces: `returnRateColor(rate: number | null): string`, `formatDateTime(iso: string): string`

이 프로젝트는 프론트엔드 테스트 러너가 없으므로(기존 관례), 이 태스크의 "테스트"는 `npx tsc --noEmit` 타입체크로 대체한다.

- [x] **Step 1: `frontend/lib/return-rate-color.ts` 작성**

```ts
export function returnRateColor(rate: number | null): string {
  if (rate === null || rate === 0) return '';
  return rate > 0 ? 'text-red-600 dark:text-red-400' : 'text-blue-600 dark:text-blue-400';
}
```

- [x] **Step 2: `frontend/lib/format.ts` 작성**

```ts
export function formatDateTime(iso: string): string {
  return iso.replace('T', ' ').slice(0, 19);
}
```

- [x] **Step 3: `frontend/app/backtests/page.tsx` 수정**

파일 상단의 다음 코드:

```ts
import Link from 'next/link';
import { getBacktestRuns } from '@/lib/api/eda';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import DeleteRunButton from '@/components/DeleteRunButton';

function returnRateColor(rate: number | null): string {
  if (rate === null) return '';
  if (rate > 0) return 'text-green-600 dark:text-green-400';
  if (rate < 0) return 'text-red-600 dark:text-red-400';
  return '';
}
```

다음으로 교체:

```ts
import Link from 'next/link';
import { getBacktestRuns } from '@/lib/api/eda';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import DeleteRunButton from '@/components/DeleteRunButton';
import { returnRateColor } from '@/lib/return-rate-color';
```

(파일 나머지 부분은 그대로 — `returnRateColor(run.return_rate)` 호출 부분은 이미 있음)

- [x] **Step 4: `frontend/app/heatmap/page.tsx` 수정**

파일 상단의 다음 코드:

```ts
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
```

다음으로 교체:

```ts
import Link from 'next/link';
import { getHeatmap } from '@/lib/api/eda';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { returnRateColor } from '@/lib/return-rate-color';
```

- [x] **Step 5: `frontend/app/ranking/page.tsx` 수정**

파일 상단의 다음 코드:

```ts
import { getRanking } from '@/lib/api/eda';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

function returnRateColor(rate: number | null): string {
  if (rate === null) return '';
  if (rate > 0) return 'text-green-600 dark:text-green-400';
  if (rate < 0) return 'text-red-600 dark:text-red-400';
  return '';
}
```

다음으로 교체:

```ts
import { getRanking } from '@/lib/api/eda';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { returnRateColor } from '@/lib/return-rate-color';
```

- [x] **Step 6: 타입체크**

Run (frontend 디렉토리에서): `npx tsc --noEmit`
Expected: 에러 없음

- [x] **Step 7: 커밋**

```bash
git add frontend/lib/return-rate-color.ts frontend/lib/format.ts frontend/app/backtests/page.tsx frontend/app/heatmap/page.tsx frontend/app/ranking/page.tsx
git commit -m "$(cat <<'EOF'
feat: 수익률 색상 컨벤션을 한국식(플러스=빨강/마이너스=파랑)으로 통일

backtests/heatmap/ranking 페이지에 중복 정의돼 있던 색상 함수를
공용 유틸(lib/return-rate-color.ts)로 교체하고 컨벤션을 반전.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: 프론트 타입(`eda.ts`) — `BacktestMetrics`/`OhlcvPoint` 추가, `BacktestDetail` 재정의

**Files:**
- Modify: `frontend/lib/types/eda.ts`

**Interfaces:**
- Produces: `BacktestMetrics`, `OhlcvPoint`, 재정의된 `BacktestDetail` (Task 4의 백엔드 응답과 1:1 대응).

- [x] **Step 1: 타입 수정**

`frontend/lib/types/eda.ts`의 현재 코드:

```ts
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

다음으로 교체(`Trade`는 변경 없음 — 이미 필요한 필드가 다 있어 그대로 둠):

```ts
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

export interface BacktestMetrics {
  total_return: number;
  cagr: number;
  mdd: number;
  sharpe_ratio: number;
  sortino_ratio: number;
  calmar_ratio: number;
  win_rate: number;
  profit_factor: number;
  avg_holding_period: number;
  max_consecutive_loss: number;
  buy_and_hold_return: number;
  total_trades: number;
}

export interface OhlcvPoint {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
}

export interface BacktestDetail {
  market: string;
  timeframe: string;
  start: string;
  end: string;
  final_value: number;
  metrics: BacktestMetrics;
  ohlcv: OhlcvPoint[];
  trades: Trade[];
}
```

(`EquityPoint`는 삭제 — 이 타입을 쓰는 곳은 `BacktestDetail`뿐이었고 Task 8에서 `EquityCurveChart.tsx`도 삭제되므로 완전히 미사용이 됨)

- [x] **Step 2: 타입체크**

Run (frontend 디렉토리에서): `npx tsc --noEmit`
Expected: `frontend/app/backtests/[runId]/page.tsx`와 `frontend/components/EquityCurveChart.tsx`에서 `EquityPoint`/이전 `BacktestDetail` 필드 참조 관련 에러가 뜨는 게 정상(Task 8에서 그 파일들을 고칠 것이므로 지금은 에러 나는 게 맞음 — 여기서는 `eda.ts` 자체에 문법 에러가 없는지만 확인)

- [x] **Step 3: 커밋**

```bash
git add frontend/lib/types/eda.ts
git commit -m "$(cat <<'EOF'
feat: BacktestDetail 타입에 metrics/ohlcv/market 필드 추가

성과 지표 그리드, 가격 차트, 코인명 표기에 필요한 타입을 백엔드
응답 구조에 맞춰 재정의.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: `frontend/components/PriceChart.tsx` — 캔들+진입/청산 마커 차트 신규

**Files:**
- Create: `frontend/components/PriceChart.tsx`

**Interfaces:**
- Consumes: `OhlcvPoint[]`, `Trade[]` (Task 6에서 정의)
- Produces: `<PriceChart ohlcv={...} trades={...} timeframe={...} />`

`C:\Users\jungm\project\backtesting_1\frontend\components\charts\PriceChart.tsx`를 포팅하되: (1) 업비트 컨벤션으로 캔들 색 반전(상승=빨강/하락=파랑), (2) 마커 색은 캔들 색과 겹치지 않게 진입=파랑/청산=주황으로, (3) 원본의 고정 다크 배경(`#0e1420`)은 빼고 이 앱의 라이트/다크 테마를 그대로 따르도록(기존 `EquityCurveChart.tsx`와 동일한 방식), (4) 타임프레임 판별을 이 프로젝트의 실제 값(`minutes15`, `days` 등)에 맞게 수정.

- [x] **Step 1: 파일 작성**

```tsx
'use client';

import { useEffect, useRef } from 'react';
import {
  createChart,
  CandlestickSeries,
  CrosshairMode,
  createSeriesMarkers,
  type UTCTimestamp,
} from 'lightweight-charts';
import type { OhlcvPoint, Trade } from '@/lib/types/eda';

interface PriceChartProps {
  ohlcv: OhlcvPoint[];
  trades: Trade[];
  timeframe: string;
}

function isIntraday(timeframe: string): boolean {
  return timeframe.startsWith('minutes');
}

function toUnix(iso: string): UTCTimestamp {
  return Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp;
}

type DayString = `${number}-${number}-${number}`;

export default function PriceChart({ ohlcv, trades, timeframe }: PriceChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const intradayMode = isIntraday(timeframe);

  useEffect(() => {
    if (!containerRef.current || ohlcv.length === 0) return;

    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: 320,
      crosshair: { mode: CrosshairMode.Normal },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: '#d1d5db' },
      rightPriceScale: { borderColor: '#d1d5db' },
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#dc2626',
      downColor: '#2563eb',
      borderVisible: false,
      wickUpColor: '#dc2626',
      wickDownColor: '#2563eb',
    });

    if (intradayMode) {
      const candleData = ohlcv
        .map((bar) => ({
          time: toUnix(bar.time),
          open: bar.open, high: bar.high, low: bar.low, close: bar.close,
        }))
        .sort((a, b) => a.time - b.time);
      candleSeries.setData(candleData);

      const markers = [
        ...trades.map((t) => ({
          time: toUnix(t.entryTime), position: 'belowBar' as const,
          color: '#2563eb', shape: 'arrowUp' as const, text: 'B',
        })),
        ...trades.map((t) => ({
          time: toUnix(t.exitTime), position: 'aboveBar' as const,
          color: '#d97706', shape: 'arrowDown' as const, text: 'S',
        })),
      ].sort((a, b) => a.time - b.time);
      createSeriesMarkers(candleSeries, markers);
    } else {
      const candleData = ohlcv
        .map((bar) => ({
          time: bar.time.split('T')[0] as DayString,
          open: bar.open, high: bar.high, low: bar.low, close: bar.close,
        }))
        .sort((a, b) => String(a.time).localeCompare(String(b.time)))
        .filter((bar, i, arr) => i === 0 || bar.time !== arr[i - 1].time);
      candleSeries.setData(candleData);

      const buysByDay = new Map<string, number>();
      const sellsByDay = new Map<string, number>();
      trades.forEach((t) => {
        const day = t.entryTime.split('T')[0];
        buysByDay.set(day, (buysByDay.get(day) ?? 0) + 1);
      });
      trades.forEach((t) => {
        const day = t.exitTime.split('T')[0];
        sellsByDay.set(day, (sellsByDay.get(day) ?? 0) + 1);
      });

      const markers = [
        ...[...buysByDay.entries()].map(([day, count]) => ({
          time: day as DayString, position: 'belowBar' as const,
          color: '#2563eb', shape: 'arrowUp' as const, text: count > 1 ? `B×${count}` : 'B',
        })),
        ...[...sellsByDay.entries()].map(([day, count]) => ({
          time: day as DayString, position: 'aboveBar' as const,
          color: '#d97706', shape: 'arrowDown' as const, text: count > 1 ? `S×${count}` : 'S',
        })),
      ].sort((a, b) => String(a.time).localeCompare(String(b.time)));
      createSeriesMarkers(candleSeries, markers);
    }

    chart.timeScale().fitContent();

    const resizeObserver = new ResizeObserver(() => {
      if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth });
    });
    resizeObserver.observe(containerRef.current);

    return () => {
      resizeObserver.disconnect();
      chart.remove();
    };
  }, [ohlcv, trades, intradayMode]);

  return (
    <div className="w-full">
      <div className="mb-2 flex items-center gap-4 text-xs text-muted-foreground">
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-2 rounded-full bg-blue-500" />
          매수 (B)
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-2 rounded-full bg-amber-500" />
          매도 (S)
        </span>
      </div>
      <div ref={containerRef} className="w-full rounded-lg overflow-hidden border" />
    </div>
  );
}
```

- [x] **Step 2: 타입체크**

Run (frontend 디렉토리에서): `npx tsc --noEmit`
Expected: `PriceChart.tsx` 관련 에러 없음(다른 파일의 기존 에러는 Task 8에서 해소)

- [x] **Step 3: 커밋**

```bash
git add frontend/components/PriceChart.tsx
git commit -m "$(cat <<'EOF'
feat: 캔들+진입/청산 마커 가격 차트 컴포넌트 추가

backtesting_1의 PriceChart를 포팅. 업비트 컨벤션(상승=빨강/하락=파랑)
으로 캔들 색을 반전하고, 마커 색은 캔들 색과 구분되게 조정.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: `frontend/app/backtests/[runId]/page.tsx` 리디자인 + `EquityCurveChart.tsx` 삭제

**Files:**
- Modify: `frontend/app/backtests/[runId]/page.tsx`
- Delete: `frontend/components/EquityCurveChart.tsx`

**Interfaces:**
- Consumes: `BacktestDetail`/`BacktestMetrics` (Task 6), `PriceChart` (Task 7), `returnRateColor`/`formatDateTime` (Task 5).

- [ ] **Step 1: 페이지 전체 교체**

`frontend/app/backtests/[runId]/page.tsx`의 현재 전체 내용을 다음으로 교체:

```tsx
import { getBacktestDetail } from '@/lib/api/eda';
import PriceChart from '@/components/PriceChart';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { returnRateColor } from '@/lib/return-rate-color';
import { formatDateTime } from '@/lib/format';
import type { BacktestMetrics } from '@/lib/types/eda';

function fmtPct(value: number): string {
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`;
}

function MetricTile({ label, value, colorClass }: { label: string; value: string; colorClass?: string }) {
  return (
    <div className="rounded-md border p-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className={`mt-1 text-base font-semibold ${colorClass ?? ''}`}>{value}</p>
    </div>
  );
}

function MetricsGrid({ metrics }: { metrics: BacktestMetrics }) {
  const tiles: { label: string; value: string; colorClass?: string }[] = [
    { label: '총 수익률', value: fmtPct(metrics.total_return), colorClass: returnRateColor(metrics.total_return) },
    { label: 'CAGR', value: fmtPct(metrics.cagr), colorClass: returnRateColor(metrics.cagr) },
    { label: 'Buy&Hold', value: fmtPct(metrics.buy_and_hold_return), colorClass: returnRateColor(metrics.buy_and_hold_return) },
    { label: 'MDD', value: fmtPct(metrics.mdd), colorClass: returnRateColor(metrics.mdd) },
    { label: '샤프 비율', value: metrics.sharpe_ratio.toFixed(2) },
    { label: '소르티노', value: metrics.sortino_ratio.toFixed(2) },
    { label: '칼마 비율', value: metrics.calmar_ratio.toFixed(2) },
    { label: '총 거래', value: `${metrics.total_trades}건` },
    { label: '승률', value: `${metrics.win_rate.toFixed(1)}%` },
    { label: '손익비', value: metrics.profit_factor.toFixed(2) },
    { label: '평균 보유', value: `${metrics.avg_holding_period.toFixed(1)}일` },
    { label: '최대연속손실', value: `${metrics.max_consecutive_loss}건` },
  ];

  return (
    <div>
      <h2 className="mb-2 text-sm font-semibold">성과 지표</h2>
      <div className="grid grid-cols-2 gap-3">
        {tiles.map((tile) => (
          <MetricTile key={tile.label} label={tile.label} value={tile.value} colorClass={tile.colorClass} />
        ))}
      </div>
    </div>
  );
}

export default async function BacktestDetailPage({ params }: { params: { runId: string } }) {
  const detail = await getBacktestDetail(params.runId);

  return (
    <div>
      <h1 className="mb-1 text-lg font-semibold">백테스트 상세</h1>
      <p className="mb-4 text-sm text-muted-foreground">
        {detail.market} · {detail.timeframe} · {detail.start.slice(0, 10)} ~ {detail.end.slice(0, 10)}
      </p>

      <div className="mb-6 flex gap-6 rounded-md border p-4">
        <div>
          <p className="text-xs text-muted-foreground">총 수익률</p>
          <p className={`text-lg font-semibold ${returnRateColor(detail.metrics.total_return)}`}>
            {fmtPct(detail.metrics.total_return)}
          </p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">MDD</p>
          <p className={`text-lg font-semibold ${returnRateColor(detail.metrics.mdd)}`}>
            {fmtPct(detail.metrics.mdd)}
          </p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">총 거래</p>
          <p className="text-lg font-semibold">{detail.metrics.total_trades}건</p>
        </div>
      </div>

      <div className="mb-6">
        <MetricsGrid metrics={detail.metrics} />
      </div>

      <h2 className="mb-2 font-medium">가격 차트</h2>
      <PriceChart ohlcv={detail.ohlcv} trades={detail.trades} timeframe={detail.timeframe} />

      <h2 className="mt-6 mb-2 font-medium">거래 내역 ({detail.trades.length}건)</h2>
      {detail.trades.length === 0 ? (
        <p className="text-muted-foreground">거래 내역이 없습니다.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>진입</TableHead>
              <TableHead>청산</TableHead>
              <TableHead>수익률(%)</TableHead>
              <TableHead>매수가</TableHead>
              <TableHead>매도가</TableHead>
              <TableHead>수익금</TableHead>
              <TableHead>보유기간</TableHead>
              <TableHead>상태</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {detail.trades.map((t, i) => (
              <TableRow key={i}>
                <TableCell>{formatDateTime(t.entryTime)}</TableCell>
                <TableCell>{formatDateTime(t.exitTime)}</TableCell>
                <TableCell className={returnRateColor(t.returnRate)}>{t.returnRate.toFixed(2)}</TableCell>
                <TableCell>{t.entryPrice.toLocaleString()}</TableCell>
                <TableCell>{t.exitPrice.toLocaleString()}</TableCell>
                <TableCell className={returnRateColor(t.pnl)}>{t.pnl.toLocaleString()}</TableCell>
                <TableCell>{t.holdingPeriod}</TableCell>
                <TableCell>
                  {t.forceClosed ? (
                    <Badge
                      variant="secondary"
                      title="매도 조건을 만족하지 못해 백테스트 종료 시점 종가로 평가된 상태입니다."
                    >
                      보유중(기간종료)
                    </Badge>
                  ) : (
                    <Badge variant="outline">청산됨</Badge>
                  )}
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

- [ ] **Step 2: `EquityCurveChart.tsx` 삭제**

```bash
rm frontend/components/EquityCurveChart.tsx
```

- [ ] **Step 3: 타입체크**

Run (frontend 디렉토리에서): `npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 4: 커밋**

```bash
git add -A frontend/app/backtests/[runId]/page.tsx frontend/components/EquityCurveChart.tsx
git commit -m "$(cat <<'EOF'
feat: 백테스트 상세 페이지 리디자인

코인/봉타입/기간 표기, 12종 성과 지표 그리드, 매수가/매도가/수익금
병기, YYYY-MM-DD HH:MM:SS 타임스탬프, 강제청산 상태 배지, 캔들+마커
가격 차트를 반영. 자산 곡선 라인차트는 가격 차트로 완전히 대체.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: 전체 검증 (백엔드 테스트 스위트 + 브라우저 수동 확인)

**Files:** 없음(검증만)

- [ ] **Step 1: 백엔드 전체 테스트 재실행**

Run: `python -m pytest`
Expected: 전부 PASS

- [ ] **Step 2: 백엔드 서버 재시작**

`uvicorn --reload`가 이번 세션 초반에 파일 변경을 놓친 전례가 있으므로, 기존 uvicorn 프로세스를 완전히 종료 후 재시작:

```bash
uvicorn backend.main:app --reload --port 8000
```

- [ ] **Step 3: 브라우저에서 실제 백테스트 실행 후 상세 페이지 확인**

Next.js dev 서버(`npm run dev`, 3000번 포트)가 이미 떠 있지 않으면 `frontend` 디렉토리에서 `npm run dev` 실행. 그 다음 Playwright MCP로:
1. `http://localhost:3000` 접속, 코인 선택 + 매수/매도 조건 설정(예: SMA/RSI 등 기존 지표) 후 "백테스트 실행" 클릭
2. 상세 페이지에서 확인할 것:
   - 상단에 `코인 · 봉타입 · 기간` 표기
   - 총수익률/MDD 색상이 플러스=빨강/마이너스=파랑인지
   - "성과 지표" 12개 타일이 값과 함께 렌더링되는지
   - 가격 차트가 캔들(빨강=상승/파랑=하락)로 렌더링되고 B/S 마커가 보이는지
   - 거래 내역 테이블에 매수가/매도가/수익금 컬럼이 있고, 시각이 `YYYY-MM-DD HH:MM:SS` 형식인지
   - `forceClosed`인 거래가 있다면 "보유중(기간종료)" 배지로 표시되는지
3. `/backtests`(목록), `/heatmap`, `/ranking` 페이지에서도 수익률 색상이 플러스=빨강/마이너스=파랑인지 확인

- [ ] **Step 4: 문제 발견 시 해당 Task로 돌아가 수정, 문제 없으면 완료**

이 태스크는 커밋 없음(검증 전용). 브라우저 확인 중 버그 발견 시 원인이 된 Task의 파일을 수정하고 새 커밋을 추가한다.
