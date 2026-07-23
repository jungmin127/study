# 성과 지표 툴팁 + 미청산 포지션 실시간 재평가 + 목록 페이지 개선 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 백테스트 상세 페이지 성과 지표에 설명 툴팁을 추가하고, 미청산("보유중(기간종료)") 포지션의 수익률을 페이지 로드 시점마다 최신 가격으로 재계산해 상세/목록 페이지 양쪽에 반영하며, 목록 페이지에 기간 포맷 단순화·매수/매도전략 표시·4개 컬럼 정렬을 추가한다.

**Architecture:** 백엔드는 `engine/live_valuation.py`(신규)의 순수 함수로 미청산 거래를 재평가하고, 상세 엔드포인트는 어차피 확장 조회하는 캔들의 마지막 종가를 "현재가"로 재사용(추가 API 호출 없음)하며, 목록 엔드포인트는 업비트 ticker를 배치 조회해서 쓴다. 재평가에 필요한 `size`/`commission_rate`/`buy_conditions`/`sell_conditions`를 `engine/cache.py`가 노출하도록 확장한다. 프론트는 `PriceChart`에 종료 경계 마커를 추가하고, 목록 페이지 테이블을 정렬 가능한 클라이언트 컴포넌트로 분리한다.

**Tech Stack:** Python(FastAPI, backtrader, pandas), pytest, Next.js/React(TypeScript), lightweight-charts v5, Tailwind.

## Global Constraints

- 재평가는 페이지를 열거나 새로고침할 때마다 서버가 재계산하는 방식이다. 브라우저에 열어둔 채로 자동 갱신(폴링/웹소켓)은 이번 범위 밖이다.
- 재평가는 거래 기록에 `size` 필드가 있을 때만 적용한다. 이번에 새로 실행되는 백테스트부터 `size`가 채워지며, **기존에 저장된 백테스트 결과는 소급 적용하지 않는다** — 재실행하면 채워진다.
- `revalue_open_trades()`는 `holdingPeriod`를 갱신하지 않는다(봉 개수 재계산에 필요한 `baropen`이 저장 데이터에 없음 — 알려진 제약).
- 캔들/ticker 조회 실패 시 예외를 삼키고 기존 저장값 그대로 반환한다(페이지가 깨지지 않아야 함).
- 참고 스펙: `docs/superpowers/specs/2026-07-23-live-open-position-revaluation-design.md`

---

### Task 1: `engine/runner.py` — 거래 기록에 `size` 필드 추가

**Files:**
- Modify: `engine/runner.py:56-130`
- Test: `tests/test_runner.py`

**Interfaces:**
- Produces: `TradeLogger.notify_trade`가 만드는 완료 거래 dict와 `_build_forced_close_trade()`의 반환 dict 양쪽에 `"size": float` 키가 추가됨.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_runner.py`의 `test_forced_close_trade_deducts_entry_and_exit_commission` 끝에 다음 줄 추가:

```python
    assert trade["size"] == 2.0
```

전체 함수는 다음과 같이 된다:

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
    assert trade["size"] == 2.0
```

`test_run_backtest_buy_and_hold_once`에도 다음 줄 추가(함수 끝):

```python
    assert result["trades"][0]["size"] > 0
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_runner.py -v`
Expected: FAIL — `KeyError: 'size'` (두 테스트 모두)

- [ ] **Step 3: `engine/runner.py` 수정**

`engine/runner.py:83-92`의 현재 코드:

```python
        self.trades.append({
            "entryTime": bt.num2date(trade.dtopen).isoformat(),
            "exitTime": bt.num2date(trade.dtclose).isoformat(),
            "entryPrice": round(entry_price, 8),
            "exitPrice": round(exit_price, 8),
            "returnRate": round(return_rate, 4),
            "holdingPeriod": int(trade.barclose - trade.baropen),
            "pnl": round(trade.pnlcomm, 4),
            "forceClosed": False,
        })
```

다음으로 교체:

```python
        self.trades.append({
            "entryTime": bt.num2date(trade.dtopen).isoformat(),
            "exitTime": bt.num2date(trade.dtclose).isoformat(),
            "entryPrice": round(entry_price, 8),
            "exitPrice": round(exit_price, 8),
            "returnRate": round(return_rate, 4),
            "holdingPeriod": int(trade.barclose - trade.baropen),
            "pnl": round(trade.pnlcomm, 4),
            "forceClosed": False,
            "size": round(size, 8),
        })
```

`engine/runner.py:121-130`의 현재 코드(`_build_forced_close_trade`의 반환문):

```python
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

다음으로 교체:

```python
    return {
        "entryTime": entry_time,
        "exitTime": last_dt,
        "entryPrice": round(entry_price, 8),
        "exitPrice": round(last_close, 8),
        "returnRate": round(return_rate, 4),
        "holdingPeriod": holding_period,
        "pnl": round(pnlcomm, 4),
        "forceClosed": True,
        "size": round(size, 8),
    }
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_runner.py -v`
Expected: 2개 테스트 모두 PASS

- [ ] **Step 5: 커밋**

```bash
git add engine/runner.py tests/test_runner.py
git commit -m "$(cat <<'EOF'
feat: 거래 기록에 size(매매 수량) 필드 추가

미청산 포지션 실시간 재평가에 필요한 size를 완료/강제청산 거래
양쪽에 기록. 이번에 새로 실행되는 백테스트부터 적용되며 기존 저장된
결과는 소급 적용되지 않는다.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `engine/live_valuation.py` — 미청산 포지션 재평가 순수 함수 신규 작성

**Files:**
- Create: `engine/live_valuation.py`
- Test: `tests/test_live_valuation.py`

**Interfaces:**
- Produces: `has_revaluable_open_trade(trades: list[dict]) -> bool`, `revalue_open_trades(trades: list[dict], live_price: float, live_time: str, commission_rate: float) -> tuple[list[dict], float]` (두 번째 반환값은 재평가로 인한 총 평가금액 변화량 delta).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_live_valuation.py` 신규 작성:

```python
from engine.live_valuation import has_revaluable_open_trade, revalue_open_trades


def test_has_revaluable_open_trade_true_when_forceclosed_with_size():
    trades = [{"forceClosed": True, "size": 1.0, "entryPrice": 100.0, "pnl": 10.0}]
    assert has_revaluable_open_trade(trades) is True


def test_has_revaluable_open_trade_false_when_size_missing():
    trades = [{"forceClosed": True, "entryPrice": 100.0, "pnl": 10.0}]
    assert has_revaluable_open_trade(trades) is False


def test_has_revaluable_open_trade_false_when_not_forceclosed():
    trades = [{"forceClosed": False, "size": 1.0, "entryPrice": 100.0, "pnl": 10.0}]
    assert has_revaluable_open_trade(trades) is False


def test_has_revaluable_open_trade_false_for_empty_list():
    assert has_revaluable_open_trade([]) is False


def test_revalue_open_trades_recomputes_pnl_and_return_rate():
    trades = [{
        "entryTime": "2026-01-01T00:00:00", "exitTime": "2026-01-10T00:00:00",
        "entryPrice": 100.0, "exitPrice": 105.0, "returnRate": 4.9,
        "holdingPeriod": 9, "pnl": 500.0, "forceClosed": True, "size": 100.0,
    }]
    updated, delta = revalue_open_trades(
        trades, live_price=120.0, live_time="2026-01-15T00:00:00", commission_rate=0.001,
    )

    entry_commission = 100.0 * 100.0 * 0.001
    exit_commission = 120.0 * 100.0 * 0.001
    expected_pnl = round((120.0 - 100.0) * 100.0 - entry_commission - exit_commission, 4)

    assert updated[0]["pnl"] == expected_pnl
    assert updated[0]["exitPrice"] == 120.0
    assert updated[0]["exitTime"] == "2026-01-15T00:00:00"
    assert updated[0]["holdingPeriod"] == 9  # 갱신 안 함(알려진 제약)
    assert delta == round(expected_pnl - 500.0, 4)


def test_revalue_open_trades_ignores_trade_without_size():
    trades = [{"entryPrice": 100.0, "exitPrice": 105.0, "pnl": 500.0, "forceClosed": True}]
    updated, delta = revalue_open_trades(
        trades, live_price=120.0, live_time="2026-01-15T00:00:00", commission_rate=0.001,
    )
    assert updated == trades
    assert delta == 0.0


def test_revalue_open_trades_ignores_non_forceclosed_trade():
    trades = [{"entryPrice": 100.0, "exitPrice": 105.0, "pnl": 500.0, "forceClosed": False, "size": 100.0}]
    updated, delta = revalue_open_trades(
        trades, live_price=120.0, live_time="2026-01-15T00:00:00", commission_rate=0.001,
    )
    assert updated == trades
    assert delta == 0.0
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_live_valuation.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.live_valuation'`

- [ ] **Step 3: `engine/live_valuation.py` 구현**

```python
"""
engine/live_valuation.py

미청산("보유중(기간종료)") 포지션의 수익률/손익을 최신 가격 기준으로 다시 계산한다.
백테스트가 끝난 뒤에도 실제로는 아직 청산되지 않은 포지션의 현재 가치를 보여주기 위함.
"""
from __future__ import annotations


def has_revaluable_open_trade(trades: list[dict]) -> bool:
    """size가 있는 forceClosed 거래가 하나라도 있으면 True.

    size는 이번 기능과 함께 거래 기록에 추가된 필드라, 그 이전에 저장된 결과에는
    없을 수 있다 — 그런 경우는 재평가 대상에서 제외한다(알려진 제약)."""
    return any(t.get("forceClosed") and "size" in t for t in trades)


def revalue_open_trades(
    trades: list[dict],
    live_price: float,
    live_time: str,
    commission_rate: float,
) -> tuple[list[dict], float]:
    """forceClosed=True이고 size가 있는 거래를 live_price 기준으로 재평가한 새 리스트와,
    그로 인한 총 평가금액 변화량(delta, 원 단위)을 함께 반환한다.
    size가 없는(레거시) 거래나 forceClosed가 아닌 거래는 그대로 둔다.
    holdingPeriod는 갱신하지 않는다 — 봉 개수 기준 재계산에는 baropen이 필요한데
    저장된 거래 기록에 없어, 이번 범위에서는 "백테스트 종료 시점까지의 보유 기간"으로
    고정해 둔다(알려진 제약, 상세 페이지 캡션에 명시).
    """
    updated: list[dict] = []
    delta = 0.0
    for t in trades:
        if t.get("forceClosed") and "size" in t:
            entry_price = t["entryPrice"]
            size = t["size"]
            pnl_gross = (live_price - entry_price) * size
            entry_commission = entry_price * size * commission_rate
            exit_commission = live_price * size * commission_rate
            new_pnl = round(pnl_gross - entry_commission - exit_commission, 4)
            return_rate = (new_pnl / (entry_price * size) * 100) if (entry_price and size) else 0.0
            delta += new_pnl - t["pnl"]
            updated.append({
                **t,
                "exitPrice": round(live_price, 8),
                "exitTime": live_time,
                "returnRate": round(return_rate, 4),
                "pnl": new_pnl,
            })
        else:
            updated.append(t)
    return updated, round(delta, 4)


__all__ = ["has_revaluable_open_trade", "revalue_open_trades"]
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_live_valuation.py -v`
Expected: 6개 테스트 모두 PASS

- [ ] **Step 5: 커밋**

```bash
git add engine/live_valuation.py tests/test_live_valuation.py
git commit -m "$(cat <<'EOF'
feat: 미청산 포지션 재평가 순수 함수(engine/live_valuation.py) 추가

forceClosed 거래를 최신 가격 기준으로 다시 계산하는
revalue_open_trades()와 재평가 가능 여부를 판단하는
has_revaluable_open_trade()를 추가.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `engine/cache.py` — `load_result()`/`list_backtest_runs()` 확장

**Files:**
- Modify: `engine/cache.py:135-157` (`load_result`), `engine/cache.py:376-421` (`list_backtest_runs`)
- Test: `tests/test_cache.py`

**Interfaces:**
- Produces: `load_result(run_id)`가 `commission_rate: float` 키를 추가로 반환. `list_backtest_runs()`가 각 항목에 `commission_rate: float`, `initial_capital: float`, `trades: list[dict]`, `buy_conditions: dict`, `sell_conditions: dict`를 추가로 반환(클라이언트 응답용이 아니라 `backend/main.py`가 재평가 계산에 쓰는 내부 필드).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_cache.py` 끝에 추가:

```python
def test_load_result_includes_commission_rate(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    save_result(
        run_id="r1", strategy_name="ConditionTreeStrategy", strategy_params={},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000, "commission_rate": 0.002},
        result={"final_value": 10500.0, "sharpe": None, "max_drawdown": None, "equity_curve": [], "trades": []},
    )
    loaded = load_result("r1")
    assert loaded["commission_rate"] == 0.002


def test_list_backtest_runs_includes_revaluation_fields_and_strategy_conditions(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    save_result(
        run_id="run-1", strategy_name="ConditionTreeStrategy",
        strategy_params={
            "buy_conditions": {
                "type": "AND",
                "conditions": [{"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30}],
            },
            "sell_conditions": {
                "type": "AND",
                "conditions": [{"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 70}],
            },
        },
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000, "commission_rate": 0.001},
        result={
            "final_value": 11000.0, "sharpe": None, "max_drawdown": None, "equity_curve": [],
            "trades": [{"forceClosed": True, "size": 1.0, "entryPrice": 100.0, "pnl": 5.0}],
        },
        title="테스트",
    )

    runs = list_backtest_runs()
    assert len(runs) == 1
    run = runs[0]
    assert run["initial_capital"] == 10000
    assert run["commission_rate"] == 0.001
    assert run["buy_conditions"]["conditions"][0]["indicator"] == "RSI"
    assert run["sell_conditions"]["conditions"][0]["threshold"] == 70
    assert run["trades"][0]["size"] == 1.0
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_cache.py -v -k "commission_rate or revaluation_fields"`
Expected: FAIL — `KeyError: 'commission_rate'` / `KeyError: 'trades'`

- [ ] **Step 3: `load_result()` 수정**

`engine/cache.py`의 현재 `load_result()`:

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

다음으로 교체:

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
    risk_config = json.loads(risk_config_json)
    initial_capital = risk_config.get("initial_capital")
    commission_rate = risk_config.get("commission_rate", 0.0005)
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
        "commission_rate": commission_rate,
        "from_cache": True,
    }
```

- [ ] **Step 4: `list_backtest_runs()` 수정**

`engine/cache.py`의 현재 `list_backtest_runs()`:

```python
def list_backtest_runs(strategy_name: str = "ConditionTreeStrategy", limit: int = 100) -> list[dict]:
    """온디맨드 조건식 실행(홈 화면) 결과만 최신순으로 반환한다.

    strategy_name으로 필터링해 run_sweep()이 남기는 SignalStrategy 기반 행(히트맵/랭킹
    전용)은 섞이지 않게 한다 — 두 시스템은 의도적으로 분리되어 있다."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT r.id, r.title, r.description, r.market, r.timeframe, r.start, r.end, "
            "       r.created_at, r.risk_config_json, res.final_value, res.sharpe, res.max_drawdown "
            "FROM backtest_runs r "
            "JOIN backtest_results res ON res.run_id = r.id "
            "WHERE r.strategy_name = ? "
            # created_at은 초 단위라 같은 초에 여러 건이 저장되면 순서가 불안정해질 수 있어,
            # 삽입 순서를 그대로 보존하는 rowid를 보조 정렬 기준으로 둔다.
            "ORDER BY r.created_at DESC, r.rowid DESC "
            "LIMIT ?",
            (strategy_name, limit),
        ).fetchall()
    finally:
        conn.close()

    runs: list[dict] = []
    for row in rows:
        (run_id, title, description, market, timeframe, start, end,
         created_at, risk_config_json, final_value, sharpe, max_drawdown) = row
        initial_capital = json.loads(risk_config_json).get("initial_capital")
        return_rate = (
            (final_value - initial_capital) / initial_capital * 100
            if initial_capital else None
        )
        runs.append({
            "run_id": run_id,
            "title": title,
            "description": description,
            "market": market,
            "timeframe": timeframe,
            "start": start,
            "end": end,
            "created_at": created_at,
            "final_value": final_value,
            "return_rate": return_rate,
            "sharpe": sharpe,
            "max_drawdown": max_drawdown,
        })
    return runs
```

다음으로 교체:

```python
def list_backtest_runs(strategy_name: str = "ConditionTreeStrategy", limit: int = 100) -> list[dict]:
    """온디맨드 조건식 실행(홈 화면) 결과만 최신순으로 반환한다.

    strategy_name으로 필터링해 run_sweep()이 남기는 SignalStrategy 기반 행(히트맵/랭킹
    전용)은 섞이지 않게 한다 — 두 시스템은 의도적으로 분리되어 있다.

    initial_capital/commission_rate/trades/buy_conditions/sell_conditions는 클라이언트
    응답용이 아니라 backend/main.py가 미청산 포지션 실시간 재평가 계산에 쓰는 내부 필드다."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT r.id, r.title, r.description, r.market, r.timeframe, r.start, r.end, "
            "       r.created_at, r.risk_config_json, r.params_json, "
            "       res.final_value, res.sharpe, res.max_drawdown, res.trades_json "
            "FROM backtest_runs r "
            "JOIN backtest_results res ON res.run_id = r.id "
            "WHERE r.strategy_name = ? "
            # created_at은 초 단위라 같은 초에 여러 건이 저장되면 순서가 불안정해질 수 있어,
            # 삽입 순서를 그대로 보존하는 rowid를 보조 정렬 기준으로 둔다.
            "ORDER BY r.created_at DESC, r.rowid DESC "
            "LIMIT ?",
            (strategy_name, limit),
        ).fetchall()
    finally:
        conn.close()

    runs: list[dict] = []
    for row in rows:
        (run_id, title, description, market, timeframe, start, end,
         created_at, risk_config_json, params_json,
         final_value, sharpe, max_drawdown, trades_json) = row
        risk_config = json.loads(risk_config_json)
        initial_capital = risk_config.get("initial_capital")
        commission_rate = risk_config.get("commission_rate", 0.0005)
        return_rate = (
            (final_value - initial_capital) / initial_capital * 100
            if initial_capital else None
        )
        params = json.loads(params_json)
        runs.append({
            "run_id": run_id,
            "title": title,
            "description": description,
            "market": market,
            "timeframe": timeframe,
            "start": start,
            "end": end,
            "created_at": created_at,
            "final_value": final_value,
            "return_rate": return_rate,
            "sharpe": sharpe,
            "max_drawdown": max_drawdown,
            "initial_capital": initial_capital,
            "commission_rate": commission_rate,
            "trades": json.loads(trades_json),
            "buy_conditions": params["buy_conditions"],
            "sell_conditions": params["sell_conditions"],
        })
    return runs
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `python -m pytest tests/test_cache.py -v`
Expected: 모든 테스트 PASS (기존 테스트도 그대로 통과 — 필드 추가는 기존 assert를 깨지 않음)

- [ ] **Step 6: 커밋**

```bash
git add engine/cache.py tests/test_cache.py
git commit -m "$(cat <<'EOF'
feat: cache.py가 재평가에 필요한 commission_rate/trades/전략조건 노출

load_result()에 commission_rate, list_backtest_runs()에
commission_rate/initial_capital/trades/buy_conditions/sell_conditions를
추가로 반환하도록 확장. 미청산 포지션 실시간 재평가와 목록 페이지
매수/매도전략 표시에 쓰인다.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: `upbit_data_service.py` — 배치 ticker 조회 `get_current_prices()` 추가

**Files:**
- Modify: `upbit_data_service.py` (파일 끝, `get_krw_markets_with_ticker()` 다음)
- Test: `tests/test_upbit_data_service.py`

**Interfaces:**
- Produces: `get_current_prices(markets: list[str]) -> dict[str, float]` — 마켓 코드 → 현재가(trade_price) 매핑. 빈 리스트 입력 시 빈 dict.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_upbit_data_service.py` 끝에 추가:

```python
def test_get_current_prices_returns_empty_dict_for_empty_input():
    import upbit_data_service

    assert upbit_data_service.get_current_prices([]) == {}


def test_get_current_prices_maps_market_to_trade_price(monkeypatch):
    import upbit_data_service

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return [
                {"market": "KRW-BTC", "trade_price": 150_000_000.0},
                {"market": "KRW-ETH", "trade_price": 5_000_000.0},
            ]

    def _fake_get(url, params=None, timeout=None):
        assert "ticker" in url
        assert params == {"markets": "KRW-BTC,KRW-ETH"}
        return _FakeResponse()

    monkeypatch.setattr(upbit_data_service.httpx, "get", _fake_get)

    prices = upbit_data_service.get_current_prices(["KRW-BTC", "KRW-ETH"])
    assert prices == {"KRW-BTC": 150_000_000.0, "KRW-ETH": 5_000_000.0}
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_upbit_data_service.py -v -k get_current_prices`
Expected: FAIL — `AttributeError: module 'upbit_data_service' has no attribute 'get_current_prices'`

- [ ] **Step 3: `get_current_prices()` 구현**

`upbit_data_service.py` 끝(`get_krw_markets_with_ticker()` 함수 뒤)에 추가:

```python
def get_current_prices(markets: list[str]) -> dict[str, float]:
    """주어진 마켓들의 현재가(ticker trade_price)를 한 번에 조회한다.

    미청산 포지션이 있는 백테스트 목록의 수익률을 실시간에 준하게 재계산할 때,
    관련된 마켓들을 한 번에 배치 조회하기 위해 쓴다."""
    if not markets:
        return {}
    market_codes = ",".join(markets)
    resp = httpx.get(f"{UPBIT_BASE_URL}/ticker", params={"markets": market_codes}, timeout=10)
    resp.raise_for_status()
    return {t["market"]: t["trade_price"] for t in resp.json()}
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_upbit_data_service.py -v`
Expected: 모든 테스트 PASS

- [ ] **Step 5: 커밋**

```bash
git add upbit_data_service.py tests/test_upbit_data_service.py
git commit -m "$(cat <<'EOF'
feat: 마켓 목록의 현재가를 배치 조회하는 get_current_prices 추가

백테스트 결과 목록 페이지가 미청산 포지션이 있는 마켓들의 현재가를
한 번에 조회해 수익률을 재계산할 때 쓴다.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: `backend/main.py` — 상세 엔드포인트에 차트 확장 + 실시간 재평가 통합

**Files:**
- Modify: `backend/main.py` (import 구역, `get_backtest_detail` 함수)
- Test: `tests/test_backend.py`

**Interfaces:**
- Consumes: `has_revaluable_open_trade`/`revalue_open_trades` (Task 2), `load_result()`가 반환하는 `commission_rate` (Task 3).
- Produces: `GET /api/v1/backtests/{run_id}`가 `live_price_as_of: str | None` 필드를 추가로 반환. 미청산 포지션이 있고 백테스트 종료일이 지났으면 `ohlcv`가 현재 시각까지 확장되고 `trades`/`final_value`/`metrics`가 재평가된 값으로 대체된다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_backend.py`에 다음 3개 테스트 추가(파일 끝):

```python
def test_backtest_detail_revalues_open_position_with_extended_candles(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    extended_df = make_oscillating_df(n=40)
    monkeypatch.setattr(backend_module, "get_candles", lambda market, timeframe, start, end: extended_df)

    save_result(
        run_id="r-open", strategy_name="ConditionTreeStrategy",
        strategy_params={"buy_conditions": {}, "sell_conditions": {}},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000, "commission_rate": 0.001},
        result={
            "final_value": 10500.0, "sharpe": None, "max_drawdown": None,
            "equity_curve": [{"timestamp": "2026-01-01T00:00:00", "value": 10000.0}],
            "trades": [{
                "entryTime": "2026-01-01T00:00:00", "exitTime": "2026-01-10T00:00:00",
                "entryPrice": 100.0, "exitPrice": 105.0, "returnRate": 4.9,
                "holdingPeriod": 9, "pnl": 500.0, "forceClosed": True, "size": 100.0,
            }],
        },
    )

    resp = client.get("/api/v1/backtests/r-open")
    assert resp.status_code == 200
    body = resp.json()
    assert body["live_price_as_of"] is not None
    last_close = float(extended_df["close"].iloc[-1])
    assert body["trades"][0]["exitPrice"] == round(last_close, 8)
    assert body["trades"][0]["pnl"] != 500.0
    assert len(body["ohlcv"]) == 40


def test_backtest_detail_skips_revaluation_for_legacy_trade_without_size(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch, df=make_oscillating_df(n=40))

    save_result(
        run_id="r-legacy", strategy_name="ConditionTreeStrategy",
        strategy_params={"buy_conditions": {}, "sell_conditions": {}},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000, "commission_rate": 0.001},
        result={
            "final_value": 10500.0, "sharpe": None, "max_drawdown": None,
            "equity_curve": [{"timestamp": "2026-01-01T00:00:00", "value": 10000.0}],
            "trades": [{
                "entryTime": "2026-01-01T00:00:00", "exitTime": "2026-01-10T00:00:00",
                "entryPrice": 100.0, "exitPrice": 105.0, "returnRate": 4.9,
                "holdingPeriod": 9, "pnl": 500.0, "forceClosed": True,
            }],
        },
    )

    resp = client.get("/api/v1/backtests/r-legacy")
    assert resp.status_code == 200
    body = resp.json()
    assert body["live_price_as_of"] is None
    assert body["trades"][0]["pnl"] == 500.0
    assert body["trades"][0]["exitPrice"] == 105.0


def test_backtest_detail_falls_back_when_extended_candle_fetch_fails(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    original_df = make_oscillating_df(n=30)
    call_count = {"n": 0}

    def flaky_get_candles(market, timeframe, start, end):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("업비트 API 오류")
        return original_df

    monkeypatch.setattr(backend_module, "get_candles", flaky_get_candles)

    save_result(
        run_id="r-fallback", strategy_name="ConditionTreeStrategy",
        strategy_params={"buy_conditions": {}, "sell_conditions": {}},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000, "commission_rate": 0.001},
        result={
            "final_value": 10500.0, "sharpe": None, "max_drawdown": None,
            "equity_curve": [{"timestamp": "2026-01-01T00:00:00", "value": 10000.0}],
            "trades": [{
                "entryTime": "2026-01-01T00:00:00", "exitTime": "2026-01-10T00:00:00",
                "entryPrice": 100.0, "exitPrice": 105.0, "returnRate": 4.9,
                "holdingPeriod": 9, "pnl": 500.0, "forceClosed": True, "size": 100.0,
            }],
        },
    )

    resp = client.get("/api/v1/backtests/r-fallback")
    assert resp.status_code == 200
    assert call_count["n"] == 2  # 확장 조회 실패 → 원래 end_dt로 재시도
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_backend.py -v -k "revalues_open_position or skips_revaluation or falls_back_when_extended"`
Expected: FAIL — `KeyError: 'live_price_as_of'` (첫 두 테스트), 세 번째 테스트는 `call_count["n"] == 1`로 실패(폴백 로직이 아직 없어 재시도하지 않음)

- [ ] **Step 3: import 추가**

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
from engine.condition_strategy import ConditionTreeStrategy
from engine.condition_tree import find_unknown_indicators, is_empty, max_required_period
from engine.metrics import calculate_metrics
from engine.strategies import SignalStrategy
from engine.sweep import DEFAULT_RISK_CONFIG
from signals import SIGNAL_REGISTRY
from upbit_data_service import get_candles, get_krw_markets, get_krw_markets_with_ticker
```

다음으로 교체:

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
from engine.condition_strategy import ConditionTreeStrategy
from engine.condition_tree import find_unknown_indicators, is_empty, max_required_period
from engine.live_valuation import has_revaluable_open_trade, revalue_open_trades
from engine.metrics import calculate_metrics
from engine.strategies import SignalStrategy
from engine.sweep import DEFAULT_RISK_CONFIG
from signals import SIGNAL_REGISTRY
from upbit_data_service import get_candles, get_krw_markets, get_krw_markets_with_ticker
```

(`get_current_prices`는 Task 6에서 목록 엔드포인트를 구현할 때 이 import 줄에 추가한다 — 상세 엔드포인트는 쓰지 않음)

- [ ] **Step 4: `get_backtest_detail` 재구성**

`backend/main.py`의 현재 `get_backtest_detail` 전체:

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
        "initial_capital": result["initial_capital"],
        "final_value": result["final_value"],
        "metrics": metrics,
        "ohlcv": ohlcv,
        "trades": result["trades"],
    }
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

    # 미청산 포지션이 있고 백테스트 종료일이 이미 지났으면, 그 이후 캔들도 함께
    # 조회해서(a) 가격 차트에 종료일 이후 흐름을 보여주고 (b) 그 마지막 종가를
    # "현재가"로 재사용해 미청산 포지션을 재평가한다(별도 ticker 호출 불필요).
    has_open = has_revaluable_open_trade(result["trades"])
    fetch_end_dt = end_dt
    now = datetime.now(timezone.utc)
    if has_open and now > end_dt:
        fetch_end_dt = now

    try:
        df = get_candles(result["market"], result["timeframe"], start_dt, fetch_end_dt)
    except Exception:
        df = get_candles(result["market"], result["timeframe"], start_dt, end_dt)
        fetch_end_dt = end_dt

    # candle_time은 tz-aware(UTC)인데 trades의 entryTime/exitTime은 backtrader가
    # tz를 벗겨낸 naive 문자열이다(engine/runner.py의 df_bt.index.tz_localize(None)).
    # 프론트에서 new Date(...)로 파싱할 때 tz 표기 유무가 섞이면 로컬 타임존만큼
    # 어긋나 보이므로, 여기서도 naive로 맞춰 캔들/거래 시각의 기준을 통일한다.
    df_chart = df.copy()
    if not df_chart.empty and df_chart["candle_time"].dt.tz is not None:
        df_chart["candle_time"] = df_chart["candle_time"].dt.tz_localize(None)

    trades = result["trades"]
    equity_curve = result["equity_curve"]
    final_value = result["final_value"]
    live_price_as_of = None

    if has_open and fetch_end_dt > end_dt and not df_chart.empty:
        live_close = float(df_chart["close"].iloc[-1])
        live_time = df_chart["candle_time"].iloc[-1].isoformat()
        revalued, delta = revalue_open_trades(trades, live_close, live_time, result["commission_rate"])
        if delta != 0.0:
            final_value = round(final_value + delta, 4)
            equity_curve = equity_curve + [{"timestamp": live_time, "value": final_value}]
            trades = revalued
            live_price_as_of = live_time

    metrics = calculate_metrics(
        equity_curve=equity_curve,
        trades=trades,
        initial_capital=result["initial_capital"],
        df=df,
        timeframe=result["timeframe"],
    )

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
        "initial_capital": result["initial_capital"],
        "final_value": final_value,
        "metrics": metrics,
        "ohlcv": ohlcv,
        "trades": trades,
        "live_price_as_of": live_price_as_of,
    }
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `python -m pytest tests/test_backend.py -v`
Expected: 모든 테스트 PASS

- [ ] **Step 6: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "$(cat <<'EOF'
feat: 상세 엔드포인트가 미청산 포지션을 현재가로 재평가

종료일이 지난 미청산 포지션이 있으면 캔들을 지금 시각까지 확장
조회해 그 마지막 종가로 거래/자산곡선/성과지표를 재계산한다.
확장 조회 실패 시 원래 종료일 기준으로 조용히 폴백.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: `backend/main.py` — 목록 엔드포인트에 실시간 재평가 + 전략 조건 통합

**Files:**
- Modify: `backend/main.py` (`get_backtest_runs` 함수)
- Test: `tests/test_backend.py`

**Interfaces:**
- Consumes: `has_revaluable_open_trade`/`revalue_open_trades` (Task 2), `get_current_prices` (Task 4), `list_backtest_runs()`가 반환하는 `commission_rate`/`initial_capital`/`trades`/`buy_conditions`/`sell_conditions` (Task 3).
- Produces: `GET /api/v1/backtests`의 각 항목에 `is_live: bool`, `buy_conditions: dict`, `sell_conditions: dict` 필드 추가.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_backend.py`에 다음 헬퍼와 3개 테스트 추가(파일 끝):

```python
def _patch_get_current_prices(monkeypatch, prices: dict[str, float] | None = None):
    monkeypatch.setattr(
        backend_module, "get_current_prices",
        lambda markets: prices if prices is not None else {},
    )


def test_get_backtests_marks_is_live_and_updates_return_rate_for_open_position(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _patch_get_current_prices(monkeypatch, {"KRW-BTC": 120.0})

    save_result(
        run_id="r-open", strategy_name="ConditionTreeStrategy",
        strategy_params={"buy_conditions": {}, "sell_conditions": {}},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000, "commission_rate": 0.001},
        result={
            "final_value": 10500.0, "sharpe": None, "max_drawdown": None, "equity_curve": [],
            "trades": [{
                "entryTime": "2026-01-01T00:00:00", "exitTime": "2026-01-10T00:00:00",
                "entryPrice": 100.0, "exitPrice": 105.0, "returnRate": 4.9,
                "holdingPeriod": 9, "pnl": 500.0, "forceClosed": True, "size": 100.0,
            }],
        },
    )

    resp = client.get("/api/v1/backtests")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["is_live"] is True
    assert body[0]["final_value"] != 10500.0
    assert body[0]["return_rate"] != 5.0


def test_get_backtests_includes_strategy_condition_summaries(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    save_result(
        run_id="r1", strategy_name="ConditionTreeStrategy",
        strategy_params={
            "buy_conditions": {
                "type": "AND",
                "conditions": [{"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30}],
            },
            "sell_conditions": {
                "type": "AND",
                "conditions": [{"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 70}],
            },
        },
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={"final_value": 10500.0, "sharpe": None, "max_drawdown": None, "equity_curve": [], "trades": []},
    )

    resp = client.get("/api/v1/backtests")
    body = resp.json()
    assert body[0]["buy_conditions"]["conditions"][0]["indicator"] == "RSI"
    assert body[0]["sell_conditions"]["conditions"][0]["threshold"] == 70
    assert body[0]["is_live"] is False


def test_get_backtests_falls_back_when_ticker_fetch_fails(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    def failing_get_current_prices(markets):
        raise RuntimeError("업비트 ticker 오류")

    monkeypatch.setattr(backend_module, "get_current_prices", failing_get_current_prices)

    save_result(
        run_id="r-open", strategy_name="ConditionTreeStrategy",
        strategy_params={"buy_conditions": {}, "sell_conditions": {}},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000, "commission_rate": 0.001},
        result={
            "final_value": 10500.0, "sharpe": None, "max_drawdown": None, "equity_curve": [],
            "trades": [{
                "entryTime": "2026-01-01T00:00:00", "exitTime": "2026-01-10T00:00:00",
                "entryPrice": 100.0, "exitPrice": 105.0, "returnRate": 4.9,
                "holdingPeriod": 9, "pnl": 500.0, "forceClosed": True, "size": 100.0,
            }],
        },
    )

    resp = client.get("/api/v1/backtests")
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["is_live"] is False
    assert body[0]["final_value"] == 10500.0
```

또한 기존 `test_run_backtest_persists_title_and_description` 테스트가 실제 `run_backtest_cached()`를 실행한 뒤 `GET /api/v1/backtests`를 호출하는데, 이 실행 결과에 미청산 포지션이 생기면 패치되지 않은 `get_current_prices`가 실제 네트워크 호출을 시도하게 된다. 이를 막기 위해 해당 테스트 맨 위(`_patch_get_candles(monkeypatch)` 다음 줄)에 다음을 추가:

```python
    _patch_get_current_prices(monkeypatch)
```

(반환값 없이 호출하면 헬퍼 기본값인 빈 dict `{}`가 쓰여, 재평가가 필요해도 조용히 건너뛴다 — 이 테스트는 재평가 자체를 검증하는 게 목적이 아니므로 안전한 처리.)

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_backend.py -v -k "marks_is_live or condition_summaries or falls_back_when_ticker"`
Expected: FAIL — `KeyError: 'is_live'` / `KeyError: 'buy_conditions'`

- [ ] **Step 3: import에 `get_current_prices` 추가**

`backend/main.py`의 다음 import 줄(Task 5에서 `get_current_prices`를 뺀 채로 정리해 둔 상태):

```python
from upbit_data_service import get_candles, get_krw_markets, get_krw_markets_with_ticker
```

다음으로 교체:

```python
from upbit_data_service import get_candles, get_current_prices, get_krw_markets, get_krw_markets_with_ticker
```

- [ ] **Step 4: `get_backtest_runs` 재구성**

`backend/main.py`의 현재 코드:

```python
@app.get("/api/v1/backtests")
def get_backtest_runs() -> list[dict]:
    return list_backtest_runs()
```

다음으로 교체:

```python
@app.get("/api/v1/backtests")
def get_backtest_runs() -> list[dict]:
    runs = list_backtest_runs()
    markets_needing_price = {r["market"] for r in runs if has_revaluable_open_trade(r["trades"])}

    live_prices: dict[str, float] = {}
    if markets_needing_price:
        try:
            live_prices = get_current_prices(list(markets_needing_price))
        except Exception:
            live_prices = {}

    result: list[dict] = []
    for r in runs:
        live_price = live_prices.get(r["market"])
        is_live = False
        final_value = r["final_value"]
        return_rate = r["return_rate"]
        if live_price is not None and has_revaluable_open_trade(r["trades"]):
            _, delta = revalue_open_trades(
                r["trades"], live_price, datetime.now(timezone.utc).isoformat(), r["commission_rate"],
            )
            if delta != 0.0:
                final_value = round(r["final_value"] + delta, 4)
                initial_capital = r["initial_capital"]
                return_rate = (final_value - initial_capital) / initial_capital * 100 if initial_capital else None
                is_live = True
        result.append({
            "run_id": r["run_id"],
            "title": r["title"],
            "description": r["description"],
            "market": r["market"],
            "timeframe": r["timeframe"],
            "start": r["start"],
            "end": r["end"],
            "created_at": r["created_at"],
            "final_value": final_value,
            "return_rate": return_rate,
            "sharpe": r["sharpe"],
            "max_drawdown": r["max_drawdown"],
            "is_live": is_live,
            "buy_conditions": r["buy_conditions"],
            "sell_conditions": r["sell_conditions"],
        })
    return result
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `python -m pytest tests/test_backend.py -v`
Expected: 모든 테스트 PASS

- [ ] **Step 6: 전체 백엔드 테스트 스위트 실행**

Run: `python -m pytest`
Expected: 전부 PASS

- [ ] **Step 7: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "$(cat <<'EOF'
feat: 목록 엔드포인트가 미청산 포지션 실시간 재평가 + 전략 조건 반환

GET /api/v1/backtests가 미청산 포지션이 있는 마켓들의 현재가를
배치 조회해 수익률을 재계산(is_live 플래그)하고, 매수/매도 조건
트리도 함께 반환한다.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: 프론트 타입(`eda.ts`) 확장

**Files:**
- Modify: `frontend/lib/types/eda.ts`

**Interfaces:**
- Produces: `BacktestDetail.live_price_as_of: string | null`, `BacktestRunSummary.is_live: boolean`, `BacktestRunSummary.buy_conditions: ConditionGroup`, `BacktestRunSummary.sell_conditions: ConditionGroup`.

- [ ] **Step 1: 타입 수정**

`frontend/lib/types/eda.ts`의 현재 `BacktestDetail`:

```ts
export interface BacktestDetail {
  market: string;
  timeframe: string;
  start: string;
  end: string;
  initial_capital: number;
  final_value: number;
  metrics: BacktestMetrics;
  ohlcv: OhlcvPoint[];
  trades: Trade[];
}
```

다음으로 교체:

```ts
export interface BacktestDetail {
  market: string;
  timeframe: string;
  start: string;
  end: string;
  initial_capital: number;
  final_value: number;
  metrics: BacktestMetrics;
  ohlcv: OhlcvPoint[];
  trades: Trade[];
  live_price_as_of: string | null;
}
```

현재 `BacktestRunSummary`:

```ts
export interface BacktestRunSummary {
  run_id: string;
  title: string | null;
  description: string | null;
  market: string;
  timeframe: string;
  start: string;
  end: string;
  created_at: string;
  final_value: number;
  return_rate: number | null;
  sharpe: number | null;
  max_drawdown: number | null;
}
```

다음으로 교체:

```ts
export interface BacktestRunSummary {
  run_id: string;
  title: string | null;
  description: string | null;
  market: string;
  timeframe: string;
  start: string;
  end: string;
  created_at: string;
  final_value: number;
  return_rate: number | null;
  sharpe: number | null;
  max_drawdown: number | null;
  is_live: boolean;
  buy_conditions: ConditionGroup;
  sell_conditions: ConditionGroup;
}
```

(`ConditionGroup`은 파일 상단에 이미 `import type { ComparisonOperator, ConditionGroup } from './strategy';`로 import돼 있음 — 추가 import 불필요)

- [ ] **Step 2: 타입체크**

Run (frontend 디렉토리에서): `npx tsc --noEmit`
Expected: `frontend/app/backtests/page.tsx`(period 필드 미사용 관련은 없음 — 기존 `run.start`/`run.end` 그대로 문자열이라 에러 없음)와 `frontend/components/PriceChart.tsx`(아직 `backtestEnd` prop 없음) 관련해서는 에러 없음. `getBacktestRuns()`/`getBacktestDetail()` 응답 타입 확장은 하위 호환(선택적 필드 아님 — 사용하지 않는 곳도 타입 에러 없음, 필드를 추가만 했으므로).

- [ ] **Step 3: 커밋**

```bash
git add frontend/lib/types/eda.ts
git commit -m "$(cat <<'EOF'
feat: BacktestDetail/BacktestRunSummary 타입에 재평가 관련 필드 추가

live_price_as_of, is_live, buy_conditions, sell_conditions을 백엔드
응답 구조에 맞춰 추가.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: `frontend/components/PriceChart.tsx` — 종료 경계 마커 추가

**Files:**
- Modify: `frontend/components/PriceChart.tsx`

**Interfaces:**
- Consumes: 없음(신규 prop만 추가)
- Produces: `<PriceChart ohlcv trades timeframe backtestEnd={detail.end} />` — `ohlcv`의 마지막 봉 시각이 `backtestEnd`보다 이후면(=차트가 확장됐으면) 그 경계에 회색 원형 마커("종료")를 하나 찍는다.

- [ ] **Step 1: `backtestEnd` prop 추가 및 경계 마커 렌더링**

`frontend/components/PriceChart.tsx`의 현재 코드:

```tsx
interface PriceChartProps {
  ohlcv: OhlcvPoint[];
  trades: Trade[];
  timeframe: string;
}
```

다음으로 교체:

```tsx
interface PriceChartProps {
  ohlcv: OhlcvPoint[];
  trades: Trade[];
  timeframe: string;
  backtestEnd: string;
}
```

`export default function PriceChart({ ohlcv, trades, timeframe }: PriceChartProps) {`를:

```tsx
export default function PriceChart({ ohlcv, trades, timeframe, backtestEnd }: PriceChartProps) {
```

로 교체하고, `useEffect`의 의존성 배열에 `backtestEnd`를 추가(`}, [ohlcv, trades, intradayMode]);` → `}, [ohlcv, trades, intradayMode, backtestEnd]);`).

intraday 분기의 현재 코드:

```tsx
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
```

다음으로 교체:

```tsx
      const boundaryUnix = toUnix(backtestEnd);
      const boundaryBar = candleData.find((bar) => bar.time > boundaryUnix);

      const markers = [
        ...trades.map((t) => ({
          time: toUnix(t.entryTime), position: 'belowBar' as const,
          color: '#2563eb', shape: 'arrowUp' as const, text: 'B',
        })),
        ...trades.map((t) => ({
          time: toUnix(t.exitTime), position: 'aboveBar' as const,
          color: '#d97706', shape: 'arrowDown' as const, text: 'S',
        })),
        ...(boundaryBar ? [{
          time: boundaryBar.time, position: 'inBar' as const,
          color: '#9ca3af', shape: 'circle' as const, text: '종료',
        }] : []),
      ].sort((a, b) => a.time - b.time);
      createSeriesMarkers(candleSeries, markers);
    } else {
```

daily 분기의 현재 코드:

```tsx
      const markers = [
        ...Array.from(buysByDay.entries()).map(([day, count]) => ({
          time: day as DayString, position: 'belowBar' as const,
          color: '#2563eb', shape: 'arrowUp' as const, text: count > 1 ? `B×${count}` : 'B',
        })),
        ...Array.from(sellsByDay.entries()).map(([day, count]) => ({
          time: day as DayString, position: 'aboveBar' as const,
          color: '#d97706', shape: 'arrowDown' as const, text: count > 1 ? `S×${count}` : 'S',
        })),
      ].sort((a, b) => String(a.time).localeCompare(String(b.time)));
      createSeriesMarkers(candleSeries, markers);
    }
```

다음으로 교체:

```tsx
      const boundaryDay = backtestEnd.split('T')[0];
      const boundaryBar = candleData.find((bar) => String(bar.time) > boundaryDay);

      const markers = [
        ...Array.from(buysByDay.entries()).map(([day, count]) => ({
          time: day as DayString, position: 'belowBar' as const,
          color: '#2563eb', shape: 'arrowUp' as const, text: count > 1 ? `B×${count}` : 'B',
        })),
        ...Array.from(sellsByDay.entries()).map(([day, count]) => ({
          time: day as DayString, position: 'aboveBar' as const,
          color: '#d97706', shape: 'arrowDown' as const, text: count > 1 ? `S×${count}` : 'S',
        })),
        ...(boundaryBar ? [{
          time: boundaryBar.time, position: 'inBar' as const,
          color: '#9ca3af', shape: 'circle' as const, text: '종료',
        }] : []),
      ].sort((a, b) => String(a.time).localeCompare(String(b.time)));
      createSeriesMarkers(candleSeries, markers);
    }
```

범례에 종료 표시 추가. 현재 코드:

```tsx
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
```

다음으로 교체:

```tsx
      <div className="mb-2 flex items-center gap-4 text-xs text-muted-foreground">
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-2 rounded-full bg-blue-500" />
          매수 (B)
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-2 rounded-full bg-amber-500" />
          매도 (S)
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-2 rounded-full bg-gray-400" />
          백테스트 종료
        </span>
      </div>
```

- [ ] **Step 2: 타입체크**

Run (frontend 디렉토리에서): `npx tsc --noEmit`
Expected: `PriceChart.tsx` 자체 에러 없음. `PriceChart`를 호출하는 `frontend/app/backtests/[runId]/page.tsx`에서 `backtestEnd` prop 누락 에러가 뜨는 게 정상(Task 9에서 고침).

- [ ] **Step 3: 커밋**

```bash
git add frontend/components/PriceChart.tsx
git commit -m "$(cat <<'EOF'
feat: PriceChart에 백테스트 종료 경계 마커 추가

미청산 포지션 때문에 차트가 종료일 이후까지 확장된 경우, 그
경계 지점에 회색 원형 마커로 "종료"를 표시한다.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: 상세 페이지 — 성과 지표 툴팁 + 실시간 재평가 캡션

**Files:**
- Create: `frontend/components/MetricTile.tsx`
- Modify: `frontend/app/backtests/[runId]/page.tsx`

**Interfaces:**
- Produces: `<MetricTile label value colorClass? tooltip? />` (호버 시 물음표 아이콘 옆에 설명 팝오버). `page.tsx`가 `PriceChart`에 `backtestEnd={detail.end}`를 넘기고, `live_price_as_of`가 있으면 캡션을 보여준다.

`MetricTile`은 호버 상태(`useState`)가 필요한 클라이언트 컴포넌트라, `async` 서버 컴포넌트인 `page.tsx` 안에 직접 정의할 수 없다(훅은 클라이언트 컴포넌트에서만 쓸 수 있음) — 별도 파일로 분리한다.

- [ ] **Step 1: `frontend/components/MetricTile.tsx` 작성**

```tsx
'use client';

import { useState } from 'react';

function InfoTooltip({ text }: { text: string }) {
  const [open, setOpen] = useState(false);

  return (
    <span className="relative shrink-0">
      <button
        type="button"
        className="flex h-4 w-4 items-center justify-center rounded-full border border-muted-foreground text-[10px] leading-none text-muted-foreground hover:border-foreground hover:text-foreground"
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        aria-label="지표 설명"
      >
        ?
      </button>
      {open && (
        <div className="absolute left-1/2 top-full z-50 mt-1 w-64 -translate-x-1/2 whitespace-pre-line rounded-md border bg-background p-2 text-left text-xs font-normal text-foreground shadow-lg">
          {text}
        </div>
      )}
    </span>
  );
}

interface MetricTileProps {
  label: string;
  value: string;
  colorClass?: string;
  tooltip?: string;
}

export default function MetricTile({ label, value, colorClass, tooltip }: MetricTileProps) {
  return (
    <div className="rounded-md border p-3">
      <p className="flex items-center gap-1 text-xs text-muted-foreground">
        {label}
        {tooltip && <InfoTooltip text={tooltip} />}
      </p>
      <p className={`mt-1 text-base font-semibold ${colorClass ?? ''}`}>{value}</p>
    </div>
  );
}
```

- [ ] **Step 2: `page.tsx`에서 `MetricTile` 로컬 정의 제거하고 import로 교체**

`frontend/app/backtests/[runId]/page.tsx`의 현재 코드:

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
      <div className="grid grid-cols-6 gap-3">
        {tiles.map((tile) => (
          <MetricTile key={tile.label} label={tile.label} value={tile.value} colorClass={tile.colorClass} />
        ))}
      </div>
    </div>
  );
}
```

다음으로 교체:

```tsx
import { getBacktestDetail } from '@/lib/api/eda';
import PriceChart from '@/components/PriceChart';
import MetricTile from '@/components/MetricTile';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { returnRateColor } from '@/lib/return-rate-color';
import { formatDateTime } from '@/lib/format';
import type { BacktestMetrics } from '@/lib/types/eda';

function fmtPct(value: number): string {
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`;
}

function MetricsGrid({ metrics }: { metrics: BacktestMetrics }) {
  const tiles: { label: string; value: string; colorClass?: string; tooltip: string }[] = [
    {
      label: '총 수익률', value: fmtPct(metrics.total_return), colorClass: returnRateColor(metrics.total_return),
      tooltip: '초기 자본 대비 최종 자산의 증감률입니다.',
    },
    {
      label: 'CAGR', value: fmtPct(metrics.cagr), colorClass: returnRateColor(metrics.cagr),
      tooltip: '연평균 복리 성장률입니다. 백테스트 기간과 무관하게 "연 단위로 환산하면 몇 %인가"를 보여줍니다.',
    },
    {
      label: 'Buy&Hold', value: fmtPct(metrics.buy_and_hold_return), colorClass: returnRateColor(metrics.buy_and_hold_return),
      tooltip: '같은 기간 동안 그냥 사서 들고만 있었을 때의 수익률입니다. 전략이 단순 보유보다 나은지 비교하는 기준입니다.',
    },
    {
      label: 'MDD', value: fmtPct(metrics.mdd), colorClass: returnRateColor(metrics.mdd),
      tooltip: '최대 낙폭(Max Drawdown). 자산이 고점 대비 가장 많이 떨어졌던 비율입니다. 작을수록(0에 가까울수록) 좋습니다.',
    },
    {
      label: '샤프 비율', value: metrics.sharpe_ratio.toFixed(2),
      tooltip: '위험(변동성) 대비 수익률입니다. 무위험수익률 0%를 가정하며, 높을수록 안정적으로 수익을 냈다는 뜻입니다.',
    },
    {
      label: '소르티노', value: metrics.sortino_ratio.toFixed(2),
      tooltip: '샤프 비율과 비슷하지만 하락 변동성만 위험으로 봅니다. 상승 변동은 페널티로 치지 않아 샤프보다 후하게 나올 수 있습니다.',
    },
    {
      label: '칼마 비율', value: metrics.calmar_ratio.toFixed(2),
      tooltip: 'CAGR을 MDD(절대값)로 나눈 값입니다. 수익뿐 아니라 "그 수익을 위해 감수한 최대 손실"까지 함께 고려합니다.',
    },
    {
      label: '총 거래', value: `${metrics.total_trades}건`,
      tooltip: '백테스트 기간 동안 체결된 매수→매도 거래 쌍의 개수입니다.',
    },
    {
      label: '승률', value: `${metrics.win_rate.toFixed(1)}%`,
      tooltip: '전체 거래 중 수익이 난(pnl > 0) 거래의 비율입니다.',
    },
    {
      label: '손익비', value: metrics.profit_factor.toFixed(2),
      tooltip: '총 이익 금액을 총 손실 금액으로 나눈 값입니다(Profit Factor). 1보다 크면 이익이 손실보다 큽니다.',
    },
    {
      label: '평균 보유', value: `${metrics.avg_holding_period.toFixed(1)}일`,
      tooltip: '한 번 진입해서 청산까지 평균적으로 보유한 기간(일)입니다.',
    },
    {
      label: '최대연속손실', value: `${metrics.max_consecutive_loss}건`,
      tooltip: '연속으로 손실이 난 거래의 최대 횟수입니다. 클수록 연속 손실 구간에서 심리적/자금 압박이 컸다는 뜻입니다.',
    },
  ];

  return (
    <div>
      <h2 className="mb-2 text-sm font-semibold">성과 지표</h2>
      <div className="grid grid-cols-6 gap-3">
        {tiles.map((tile) => (
          <MetricTile
            key={tile.label} label={tile.label} value={tile.value}
            colorClass={tile.colorClass} tooltip={tile.tooltip}
          />
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: `PriceChart`에 `backtestEnd` 전달 + 실시간 재평가 캡션/배지 문구 추가**

`frontend/app/backtests/[runId]/page.tsx`의 현재 코드:

```tsx
      <p className="mb-4 text-sm text-muted-foreground">
        {detail.market} · {detail.timeframe} · {detail.start.slice(0, 10)} ~ {detail.end.slice(0, 10)}
      </p>
```

다음으로 교체:

```tsx
      <p className="mb-1 text-sm text-muted-foreground">
        {detail.market} · {detail.timeframe} · {detail.start.slice(0, 10)} ~ {detail.end.slice(0, 10)}
      </p>
      {detail.live_price_as_of && (
        <p className="mb-4 text-xs text-amber-600 dark:text-amber-400">
          미청산 포지션이 있어 현재가 기준으로 재평가됨 ({formatDateTime(detail.live_price_as_of)} 기준)
        </p>
      )}
```

(원래 이 `<p>` 다음에 `<div className="mb-6 flex gap-6...">` 요약 박스가 이어지므로, `live_price_as_of`가 없을 때는 `mb-4`가 그대로 유지되어 레이아웃이 안 바뀐다.)

`PriceChart` 호출부의 현재 코드:

```tsx
      <PriceChart ohlcv={detail.ohlcv} trades={detail.trades} timeframe={detail.timeframe} />
```

다음으로 교체:

```tsx
      <PriceChart ohlcv={detail.ohlcv} trades={detail.trades} timeframe={detail.timeframe} backtestEnd={detail.end} />
```

forceClosed 배지의 현재 코드:

```tsx
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
```

다음으로 교체:

```tsx
                  {t.forceClosed ? (
                    <Badge
                      variant="secondary"
                      title={
                        detail.live_price_as_of
                          ? '매도 조건을 만족하지 못한 채 아직 보유 중입니다. 현재가로 재평가된 수익률입니다.'
                          : '매도 조건을 만족하지 못해 백테스트 종료 시점 종가로 평가된 상태입니다.'
                      }
                    >
                      보유중(기간종료)
                    </Badge>
                  ) : (
                    <Badge variant="outline">청산됨</Badge>
                  )}
```

- [ ] **Step 4: 타입체크**

Run (frontend 디렉토리에서): `npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 5: 커밋**

```bash
git add frontend/components/MetricTile.tsx "frontend/app/backtests/[runId]/page.tsx"
git commit -m "$(cat <<'EOF'
feat: 성과 지표 툴팁 + 미청산 포지션 실시간 재평가 UI 반영

성과 지표 12개 타일에 호버 설명 툴팁 추가. live_price_as_of가
있으면 "현재가 기준으로 재평가됨" 캡션과 forceClosed 배지 문구를
보강하고, PriceChart에 백테스트 종료 경계를 전달한다.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: 조건식 요약 함수 공용화 (`lib/condition-summary.ts`)

**Files:**
- Create: `frontend/lib/condition-summary.ts`
- Modify: `frontend/components/StrategyConditionBuilder.tsx`

**Interfaces:**
- Produces: `OPERATOR_SYMBOLS: Record<ComparisonOperator, string>`, `isConditionBlock(item): item is ConditionBlock`, `summarizeGroup(group: ConditionGroup): string`.

- [ ] **Step 1: `frontend/lib/condition-summary.ts` 작성**

```ts
import type { ComparisonOperator, ConditionBlock, ConditionGroup } from '@/lib/types/strategy';

export const OPERATOR_SYMBOLS: Record<ComparisonOperator, string> = {
  '>': '>',
  '<': '<',
  '>=': '≥',
  '<=': '≤',
  '==': '=',
};

export function isConditionBlock(item: ConditionBlock | ConditionGroup): item is ConditionBlock {
  return 'indicator' in item;
}

export function summarizeGroup(group: ConditionGroup): string {
  if (group.conditions.length === 0) return '(조건 없음)';
  const parts = group.conditions.map((c) =>
    isConditionBlock(c)
      ? `${c.indicator}${OPERATOR_SYMBOLS[c.operator]}${c.threshold}`
      : `(${summarizeGroup(c)})`
  );
  return parts.join(group.type === 'AND' ? ' and ' : ' or ');
}
```

- [ ] **Step 2: `StrategyConditionBuilder.tsx`에서 중복 제거**

`frontend/components/StrategyConditionBuilder.tsx`의 현재 import:

```tsx
import { useState } from 'react';
import type { ComparisonOperator, ConditionBlock, ConditionGroup } from '@/lib/types/strategy';
import type { IndicatorCatalogItem } from '@/lib/types/eda';
import { INPUT_CLASS, SECTION_HEADER_CLASS } from '@/lib/ui-classes';
```

다음으로 교체:

```tsx
import { useState } from 'react';
import type { ComparisonOperator, ConditionBlock, ConditionGroup } from '@/lib/types/strategy';
import type { IndicatorCatalogItem } from '@/lib/types/eda';
import { INPUT_CLASS, SECTION_HEADER_CLASS } from '@/lib/ui-classes';
import { OPERATOR_SYMBOLS, isConditionBlock, summarizeGroup } from '@/lib/condition-summary';
```

파일 내 다음 블록(현재 `OPERATOR_SYMBOLS` 상수 정의)을 통째로 삭제:

```tsx
const OPERATOR_SYMBOLS: Record<ComparisonOperator, string> = {
  '>': '>',
  '<': '<',
  '>=': '≥',
  '<=': '≤',
  '==': '=',
};
```

그리고 다음 블록(현재 `isConditionBlock`/`summarizeGroup` 함수 정의)도 통째로 삭제:

```tsx
function isConditionBlock(item: ConditionBlock | ConditionGroup): item is ConditionBlock {
  return 'indicator' in item;
}

function summarizeGroup(group: ConditionGroup): string {
  if (group.conditions.length === 0) return '(조건 없음)';
  const parts = group.conditions.map((c) =>
    isConditionBlock(c)
      ? `${c.indicator}${OPERATOR_SYMBOLS[c.operator]}${c.threshold}`
      : `(${summarizeGroup(c)})`
  );
  return parts.join(group.type === 'AND' ? ' and ' : ' or ');
}
```

(파일의 나머지 부분은 그대로 — `OPERATOR_SYMBOLS`/`isConditionBlock`/`summarizeGroup`을 쓰던 기존 코드는 이제 import된 동일한 이름을 그대로 참조하므로 수정 불필요.)

- [ ] **Step 3: 타입체크**

Run (frontend 디렉토리에서): `npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 4: 커밋**

```bash
git add frontend/lib/condition-summary.ts frontend/components/StrategyConditionBuilder.tsx
git commit -m "$(cat <<'EOF'
refactor: 조건식 요약 함수를 lib/condition-summary.ts로 공용화

StrategyConditionBuilder.tsx에 있던 summarizeGroup/isConditionBlock/
OPERATOR_SYMBOLS를 공용 파일로 옮겨, 백테스트 결과 목록 페이지에서도
재사용할 수 있게 한다. 동작 변경 없음.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: 목록 페이지 — 기간 포맷 + 매수/매도전략 컬럼 + 정렬

**Files:**
- Create: `frontend/components/BacktestRunsTable.tsx`
- Modify: `frontend/app/backtests/page.tsx`

**Interfaces:**
- Consumes: `BacktestRunSummary`(Task 7), `summarizeGroup`(Task 10), `returnRateColor`, `DeleteRunButton`.
- Produces: `<BacktestRunsTable runs={runs} />` — 기간을 `YYYY-MM-DD ~ YYYY-MM-DD`로 표시, 매수/매도전략 컬럼, 수익률/실행시각/코인/봉타입 클릭 정렬(오름/내림차순 토글).

- [ ] **Step 1: `frontend/components/BacktestRunsTable.tsx` 작성**

```tsx
'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import DeleteRunButton from '@/components/DeleteRunButton';
import { returnRateColor } from '@/lib/return-rate-color';
import { summarizeGroup } from '@/lib/condition-summary';
import type { BacktestRunSummary } from '@/lib/types/eda';

type SortKey = 'return_rate' | 'created_at' | 'market' | 'timeframe';
type SortDir = 'asc' | 'desc';

function sortRuns(runs: BacktestRunSummary[], key: SortKey | null, dir: SortDir): BacktestRunSummary[] {
  if (!key) return runs;
  const factor = dir === 'asc' ? 1 : -1;
  return [...runs].sort((a, b) => {
    const av = a[key];
    const bv = b[key];
    if (av === null && bv === null) return 0;
    if (av === null) return 1;
    if (bv === null) return -1;
    if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * factor;
    return String(av).localeCompare(String(bv)) * factor;
  });
}

interface BacktestRunsTableProps {
  runs: BacktestRunSummary[];
}

export default function BacktestRunsTable({ runs }: BacktestRunsTableProps) {
  const [sortKey, setSortKey] = useState<SortKey | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>('desc');

  const sorted = useMemo(() => sortRuns(runs, sortKey, sortDir), [runs, sortKey, sortDir]);

  function toggleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === 'desc' ? 'asc' : 'desc'));
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
  }

  function sortIndicator(key: SortKey): string {
    if (sortKey !== key) return '⇅';
    return sortDir === 'desc' ? '▼' : '▲';
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>제목</TableHead>
          <TableHead>
            <button type="button" className="flex items-center gap-1 hover:text-foreground" onClick={() => toggleSort('market')}>
              코인 {sortIndicator('market')}
            </button>
          </TableHead>
          <TableHead>
            <button type="button" className="flex items-center gap-1 hover:text-foreground" onClick={() => toggleSort('timeframe')}>
              봉타입 {sortIndicator('timeframe')}
            </button>
          </TableHead>
          <TableHead>기간</TableHead>
          <TableHead>매수전략</TableHead>
          <TableHead>매도전략</TableHead>
          <TableHead>
            <button type="button" className="flex items-center gap-1 hover:text-foreground" onClick={() => toggleSort('return_rate')}>
              수익률(%) {sortIndicator('return_rate')}
            </button>
          </TableHead>
          <TableHead>
            <button type="button" className="flex items-center gap-1 hover:text-foreground" onClick={() => toggleSort('created_at')}>
              실행 시각 {sortIndicator('created_at')}
            </button>
          </TableHead>
          <TableHead>상세</TableHead>
          <TableHead>삭제</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {sorted.map((run) => (
          <TableRow key={run.run_id}>
            <TableCell>
              {run.title || <span className="text-muted-foreground">(제목 없음)</span>}
              {run.description && (
                <p className="text-xs text-muted-foreground">{run.description}</p>
              )}
            </TableCell>
            <TableCell>{run.market}</TableCell>
            <TableCell>{run.timeframe}</TableCell>
            <TableCell>
              {run.start.slice(0, 10)} ~ {run.end.slice(0, 10)}
            </TableCell>
            <TableCell className="max-w-[240px] whitespace-normal font-mono text-xs">
              {summarizeGroup(run.buy_conditions)}
            </TableCell>
            <TableCell className="max-w-[240px] whitespace-normal font-mono text-xs">
              {summarizeGroup(run.sell_conditions)}
            </TableCell>
            <TableCell className={returnRateColor(run.return_rate)}>
              {run.return_rate?.toFixed(2) ?? '-'}
              {run.is_live && <span className="ml-1 text-xs text-muted-foreground">(실시간)</span>}
            </TableCell>
            <TableCell>{run.created_at}</TableCell>
            <TableCell>
              <Link href={`/backtests/${run.run_id}`} className="text-blue-600 hover:underline dark:text-blue-400">
                보기
              </Link>
            </TableCell>
            <TableCell>
              <DeleteRunButton runId={run.run_id} />
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
```

- [ ] **Step 2: `frontend/app/backtests/page.tsx`를 `BacktestRunsTable` 사용으로 교체**

`frontend/app/backtests/page.tsx`의 현재 전체 내용:

```tsx
import Link from 'next/link';
import { getBacktestRuns } from '@/lib/api/eda';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import DeleteRunButton from '@/components/DeleteRunButton';
import { returnRateColor } from '@/lib/return-rate-color';

export default async function BacktestResultsPage() {
  const runs = await getBacktestRuns();

  return (
    <div>
      <h1 className="mb-4 text-lg font-semibold">백테스트 결과</h1>
      {runs.length === 0 ? (
        <p className="text-muted-foreground">
          아직 실행한 백테스트가 없습니다. &quot;백테스트 설정&quot; 탭에서 먼저 실행해 보세요.
        </p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>제목</TableHead>
              <TableHead>코인</TableHead>
              <TableHead>봉타입</TableHead>
              <TableHead>기간</TableHead>
              <TableHead>수익률(%)</TableHead>
              <TableHead>실행 시각</TableHead>
              <TableHead>상세</TableHead>
              <TableHead>삭제</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {runs.map((run) => (
              <TableRow key={run.run_id}>
                <TableCell>
                  {run.title || <span className="text-muted-foreground">(제목 없음)</span>}
                  {run.description && (
                    <p className="text-xs text-muted-foreground">{run.description}</p>
                  )}
                </TableCell>
                <TableCell>{run.market}</TableCell>
                <TableCell>{run.timeframe}</TableCell>
                <TableCell>
                  {run.start} ~ {run.end}
                </TableCell>
                <TableCell className={returnRateColor(run.return_rate)}>
                  {run.return_rate?.toFixed(2) ?? '-'}
                </TableCell>
                <TableCell>{run.created_at}</TableCell>
                <TableCell>
                  <Link
                    href={`/backtests/${run.run_id}`}
                    className="text-blue-600 hover:underline dark:text-blue-400"
                  >
                    보기
                  </Link>
                </TableCell>
                <TableCell>
                  <DeleteRunButton runId={run.run_id} />
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

다음으로 교체:

```tsx
import { getBacktestRuns } from '@/lib/api/eda';
import BacktestRunsTable from '@/components/BacktestRunsTable';

export default async function BacktestResultsPage() {
  const runs = await getBacktestRuns();

  return (
    <div>
      <h1 className="mb-4 text-lg font-semibold">백테스트 결과</h1>
      {runs.length === 0 ? (
        <p className="text-muted-foreground">
          아직 실행한 백테스트가 없습니다. &quot;백테스트 설정&quot; 탭에서 먼저 실행해 보세요.
        </p>
      ) : (
        <BacktestRunsTable runs={runs} />
      )}
    </div>
  );
}
```

- [ ] **Step 3: 타입체크**

Run (frontend 디렉토리에서): `npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 4: 커밋**

```bash
git add frontend/components/BacktestRunsTable.tsx frontend/app/backtests/page.tsx
git commit -m "$(cat <<'EOF'
feat: 목록 페이지에 기간 포맷/전략 요약/정렬 기능 추가

기간을 YYYY-MM-DD ~ YYYY-MM-DD로 단순화하고 매수/매도전략 요약
컬럼을 추가. 수익률/실행시각/코인/봉타입 4개 컬럼을 클릭해
오름차순/내림차순 정렬할 수 있는 BacktestRunsTable로 분리.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 12: 전체 검증 (백엔드 테스트 스위트 + 브라우저 수동 확인)

**Files:** 없음(검증만)

- [x] **Step 1: 백엔드 전체 테스트 재실행**

Run: `python -m pytest`
Expected: 전부 PASS

- [x] **Step 2: 백엔드 서버 재시작**

`uvicorn --reload` 없이 실행 중이던 프로세스는 코드 변경을 반영하지 못하므로, 기존 프로세스를 완전히 종료 후 재시작:

```bash
uvicorn backend.main:app --port 8000
```

- [x] **Step 3: 프론트 재시작 확인**

`npm run dev`(3000번 포트)가 이미 떠 있지 않으면 `frontend` 디렉토리에서 `npm run dev` 실행.

- [x] **Step 4: Playwright로 실제 확인**

1. 미청산 포지션이 있는 백테스트를 하나 새로 실행(코인 선택 + 조건 설정 후 "백테스트 실행", 매도 조건이 쉽게 안 걸리도록 임계값을 극단적으로 설정하면 미청산 포지션을 쉽게 만들 수 있음)해서 `size` 필드가 채워진 새 결과를 만든다.
2. 상세 페이지에서 확인:
   - 성과 지표 12개 타일 각각의 물음표 아이콘에 마우스를 올리면 설명이 뜨는지
   - `forceClosed` 거래가 있으면 상단에 "현재가 기준으로 재평가됨" 캡션이 뜨는지
   - 가격 차트가 백테스트 종료일 이후까지 확장되고, 경계에 회색 "종료" 마커가 보이는지
   - 거래 내역의 미청산 거래 exitPrice/pnl이 최신 가격 기준으로 바뀌어 있는지(원래 저장된 강제청산 값과 다른지)
3. `/backtests`(목록) 페이지에서 확인:
   - 기간 컬럼이 `YYYY-MM-DD ~ YYYY-MM-DD` 형식인지
   - 매수전략/매도전략 컬럼에 조건식 요약이 표시되는지
   - 수익률/실행 시각/코인/봉타입 헤더를 클릭하면 정렬되고, 다시 클릭하면 방향이 토글되는지
   - 미청산 포지션이 있는 행에 "(실시간)" 표시가 뜨는지

- [x] **Step 5: 문제 발견 시 해당 Task로 돌아가 수정, 문제 없으면 완료**

이 태스크는 커밋 없음(검증 전용). 브라우저 확인 중 버그 발견 시 원인이 된 Task의 파일을 수정하고 새 커밋을 추가한다.
