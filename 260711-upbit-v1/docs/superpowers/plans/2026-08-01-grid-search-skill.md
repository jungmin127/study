# Grid Search 스킬 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `grid search 코인명,운용자금,봉데이터,운용기간,상위N개` 자연어 명령으로 오실레이터 5종 + 매도전용 3종의 전 교차 그리드(2,565개 조합)를 계산해, 중복 거래를 제거한 상위 N개만 "백테스트 결과"에 저장하는 Claude Code 스킬을 만든다.

**Architecture:** `scripts/grid_search.py`가 그리드 생성(`build_condition_grid`) → 전체 계산(`compute_grid_results`, `engine.runner.run_backtest` 반복 호출) → dedup(`dedup_top_results`) → 상위 N개 저장(`engine.cache.run_backtest_cached`) 순으로 동작하는 CLI 스크립트. `.claude/skills/grid-search/SKILL.md`가 자연어 명령을 파싱해 이 스크립트를 백그라운드로 호출하고 결과를 보고한다. 곁다리로 `frontend/lib/condition-summary.ts`의 `summarizeGroup()`이 지표 params를 표시하도록 고친다.

**Tech Stack:** Python(argparse, backtrader — `engine.runner`/`engine.cache`/`engine.condition_strategy`), pytest, TypeScript(Next.js 프론트).

## Global Constraints

- 스펙 문서: `docs/superpowers/specs/2026-08-01-grid-search-skill-design.md` (STOCH_K/STOCH_D 파라미터 키 정정 포함, 최신 버전 기준).
- 그리드: period `[10, 14, 20]`, RSI/CCI/WILLIAMS_R은 `params={"period": p}`, STOCH_K/STOCH_D는 `params={"k_period": p}` (`engine/indicators/momentum.py`의 `create_stoch_k`/`create_stoch_d`가 `k_period`를 읽음 — `period` 키는 무시됨).
- 상위 N: 기본 20, 50 초과 입력은 50으로 캡.
- 저장은 dedup 후 상위 N개만 — 나머지는 DB에 쓰지 않는다.
- SKILL.md 등 `.claude/` 설정 파일은 한국어로 작성한다(기술 용어는 영어 유지) — 사용자 전역 CLAUDE.md 규칙.
- 프론트에는 테스트 프레임워크(jest/vitest 등)가 설치돼 있지 않다 — 이번 작업에서 새로 도입하지 않는다(`condition-summary.ts` 수정은 수동 브라우저 검증).
- `scripts/` 하위 스크립트는 이 저장소 컨벤션상 pytest 단위 테스트 대상이 아니다(`run_eda_sweep.py` 참고) — CLI 진입점(`main()`)은 수동 스모크 테스트로 검증하고, 내부 순수 로직 함수(`build_condition_grid`/`compute_grid_results`/`dedup_top_results`)만 `tests/test_grid_search.py`로 단위 테스트한다.

---

### Task 1: `build_condition_grid()` — 그리드 생성

**Files:**
- Create: `scripts/grid_search.py`
- Test: `tests/test_grid_search.py`

**Interfaces:**
- Produces: `build_condition_grid() -> tuple[list[dict], list[dict]]` — `(buy_conditions, sell_conditions)`. 각 원소는 `ConditionBlock` 딕셔너리 `{"indicator": str, "params": dict, "operator": str, "threshold": float}`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_grid_search.py` 새로 생성:

```python
from scripts.grid_search import build_condition_grid


def test_build_condition_grid_combo_counts():
    buy_conditions, sell_conditions = build_condition_grid()
    assert len(buy_conditions) == 45
    assert len(sell_conditions) == 57


def test_build_condition_grid_uses_k_period_for_stochastics():
    buy_conditions, sell_conditions = build_condition_grid()
    for indicator in ("STOCH_K", "STOCH_D"):
        blocks = [b for b in buy_conditions if b["indicator"] == indicator]
        assert len(blocks) == 9
        assert all("k_period" in b["params"] for b in blocks)
        assert all("period" not in b["params"] for b in blocks)
        sell_blocks = [b for b in sell_conditions if b["indicator"] == indicator]
        assert all("k_period" in b["params"] for b in sell_blocks)


def test_build_condition_grid_uses_period_for_non_stochastic_oscillators():
    buy_conditions, _ = build_condition_grid()
    for indicator in ("RSI", "CCI", "WILLIAMS_R"):
        blocks = [b for b in buy_conditions if b["indicator"] == indicator]
        assert len(blocks) == 9
        assert {b["params"]["period"] for b in blocks} == {10, 14, 20}


def test_build_condition_grid_rsi_thresholds():
    buy_conditions, sell_conditions = build_condition_grid()
    rsi_buy = [b for b in buy_conditions if b["indicator"] == "RSI"]
    rsi_sell = [b for b in sell_conditions if b["indicator"] == "RSI"]
    assert {b["threshold"] for b in rsi_buy} == {20, 30, 40}
    assert all(b["operator"] == "<" for b in rsi_buy)
    assert {b["threshold"] for b in rsi_sell} == {60, 70, 80}
    assert all(b["operator"] == ">" for b in rsi_sell)


def test_build_condition_grid_sell_only_indicators():
    _, sell_conditions = build_condition_grid()
    stop_loss = [b for b in sell_conditions if b["indicator"] == "STOP_LOSS_PCT"]
    take_profit = [b for b in sell_conditions if b["indicator"] == "TAKE_PROFIT_PCT"]
    holding = [b for b in sell_conditions if b["indicator"] == "HOLDING_PERIOD_BARS"]
    assert len(stop_loss) == 4 and {b["threshold"] for b in stop_loss} == {-3, -5, -7, -10}
    assert all(b["operator"] == "<=" and b["params"] == {} for b in stop_loss)
    assert len(take_profit) == 4 and {b["threshold"] for b in take_profit} == {5, 10, 15, 20}
    assert all(b["operator"] == ">=" and b["params"] == {} for b in take_profit)
    assert len(holding) == 4 and {b["threshold"] for b in holding} == {5, 10, 20, 40}
    assert all(b["operator"] == ">=" and b["params"] == {} for b in holding)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_grid_search.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.grid_search'`

- [ ] **Step 3: `scripts/grid_search.py` 생성 — 그리드 생성 부분만**

```python
"""
scripts/grid_search.py

'grid search' 스킬의 연산 엔진. 오실레이터 5종(RSI/STOCH_K/STOCH_D/CCI/WILLIAMS_R) +
매도전용 3종(STOP_LOSS_PCT/TAKE_PROFIT_PCT/HOLDING_PERIOD_BARS)의 전 교차 그리드를
계산하고, 거래 시퀀스가 동일한 조합은 dedup한 뒤 상위 N개만 백테스트 결과에 저장한다.
Run: python scripts/grid_search.py --market KRW-ETH --timeframe minutes60 \
     --capital 10000000 --start 2026-06-01 --end 2026-07-31 --top-n 20
"""
from __future__ import annotations

PERIOD_GRID = [10, 14, 20]

OSCILLATORS: dict[str, dict[str, list[int]]] = {
    "RSI": {"low": [20, 30, 40], "high": [60, 70, 80]},
    "STOCH_K": {"low": [10, 20, 30], "high": [70, 80, 90]},
    "STOCH_D": {"low": [10, 20, 30], "high": [70, 80, 90]},
    "CCI": {"low": [-140, -100, -60], "high": [60, 100, 140]},
    "WILLIAMS_R": {"low": [-90, -80, -70], "high": [-30, -20, -10]},
}

# STOCH_K/STOCH_D는 create_stoch_k/create_stoch_d(engine/indicators/momentum.py)가
# "period"가 아니라 "k_period"를 읽는다. period 그리드가 실제로 반영되도록
# 지표별로 올바른 파라미터 키를 매핑한다.
PERIOD_PARAM_KEY: dict[str, str] = {
    "STOCH_K": "k_period",
    "STOCH_D": "k_period",
}

SELL_ONLY: dict[str, tuple[str, list[int]]] = {
    "STOP_LOSS_PCT": ("<=", [-3, -5, -7, -10]),
    "TAKE_PROFIT_PCT": (">=", [5, 10, 15, 20]),
    "HOLDING_PERIOD_BARS": (">=", [5, 10, 20, 40]),
}


def build_condition_grid() -> tuple[list[dict], list[dict]]:
    """오실레이터 5종 + 매도전용 3종의 매수/매도 ConditionBlock 그리드를 생성한다.

    Returns:
        (buy_conditions, sell_conditions) — 각각 ConditionBlock 딕셔너리 리스트
        ({"indicator": str, "params": dict, "operator": str, "threshold": float}).
    """
    buy_conditions: list[dict] = []
    sell_conditions: list[dict] = []

    for indicator, bounds in OSCILLATORS.items():
        param_key = PERIOD_PARAM_KEY.get(indicator, "period")
        for period in PERIOD_GRID:
            for t in bounds["low"]:
                buy_conditions.append(
                    {"indicator": indicator, "params": {param_key: period}, "operator": "<", "threshold": t}
                )
            for t in bounds["high"]:
                sell_conditions.append(
                    {"indicator": indicator, "params": {param_key: period}, "operator": ">", "threshold": t}
                )

    for indicator, (operator, thresholds) in SELL_ONLY.items():
        for t in thresholds:
            sell_conditions.append({"indicator": indicator, "params": {}, "operator": operator, "threshold": t})

    return buy_conditions, sell_conditions
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_grid_search.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: 커밋**

```bash
git add scripts/grid_search.py tests/test_grid_search.py
git commit -m "feat: add grid search condition grid builder"
```

---

### Task 2: `compute_grid_results()` — 전 조합 실행

**Files:**
- Modify: `scripts/grid_search.py`
- Test: `tests/test_grid_search.py`

**Interfaces:**
- Consumes: `build_condition_grid()` 반환 타입과 동일한 `ConditionBlock` 리스트. `engine.runner.run_backtest(df, strategy_cls, risk_config, strategy_params) -> dict`(`{final_value, trades, equity_curve, sharpe, max_drawdown}`, `engine/runner.py:145`). `engine.condition_strategy.ConditionTreeStrategy`. `engine.sweep.DEFAULT_RISK_CONFIG`.
- Produces: `compute_grid_results(df, buy_conditions, sell_conditions, risk_config) -> list[dict]` — 각 원소 `{"return_pct": float, "buy_block": dict, "sell_block": dict, "trades": list[dict], "final_value": float}`. `trades`의 각 항목은 `{"entryTime": str, "exitTime": str, ...}` (`engine/runner.py`의 `TradeLogger` 출력, ISO 문자열).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_grid_search.py`에 추가:

```python
from engine.sweep import DEFAULT_RISK_CONFIG
from scripts.grid_search import compute_grid_results
from tests.signal_fixtures import make_oscillating_df


def test_compute_grid_results_runs_every_combo():
    df = make_oscillating_df(n=200)
    buy_conditions = [
        {"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30},
        {"indicator": "CCI", "params": {"period": 20}, "operator": "<", "threshold": -100},
    ]
    sell_conditions = [
        {"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 70},
        {"indicator": "TAKE_PROFIT_PCT", "params": {}, "operator": ">=", "threshold": 10},
    ]
    risk_config = {**DEFAULT_RISK_CONFIG, "initial_capital": 1_000_000}

    results = compute_grid_results(df, buy_conditions, sell_conditions, risk_config)

    assert len(results) == 4
    for r in results:
        assert set(r.keys()) == {"return_pct", "buy_block", "sell_block", "trades", "final_value"}
        assert isinstance(r["trades"], list)
        assert isinstance(r["return_pct"], float)


def test_compute_grid_results_pairs_every_buy_with_every_sell():
    df = make_oscillating_df(n=200)
    buy_conditions = [
        {"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30},
    ]
    sell_conditions = [
        {"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 70},
        {"indicator": "RSI", "params": {"period": 20}, "operator": ">", "threshold": 80},
    ]
    risk_config = {**DEFAULT_RISK_CONFIG, "initial_capital": 1_000_000}

    results = compute_grid_results(df, buy_conditions, sell_conditions, risk_config)

    assert len(results) == 2
    assert results[0]["buy_block"] == buy_conditions[0]
    assert {r["sell_block"]["threshold"] for r in results} == {70, 80}
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_grid_search.py -k compute_grid_results -v`
Expected: FAIL — `ImportError: cannot import name 'compute_grid_results'`

- [ ] **Step 3: `compute_grid_results()` 구현**

`scripts/grid_search.py` 상단 import에 추가:

```python
from engine.condition_strategy import ConditionTreeStrategy
from engine.runner import run_backtest
```

파일 끝(`SELL_ONLY` 딕셔너리와 `build_condition_grid()` 사이가 아니라 그 아래)에 추가:

```python
def compute_grid_results(
    df,
    buy_conditions: list[dict],
    sell_conditions: list[dict],
    risk_config: dict,
) -> list[dict]:
    """buy_conditions x sell_conditions 전 조합을 run_backtest로 계산한다.

    Returns:
        각 조합의 결과 딕셔너리 리스트:
        {"return_pct": float, "buy_block": dict, "sell_block": dict,
         "trades": list[dict], "final_value": float}
    """
    results: list[dict] = []
    initial_capital = float(risk_config.get("initial_capital", 10000))
    total = len(buy_conditions) * len(sell_conditions)

    for i, buy_block in enumerate(buy_conditions):
        buy_group = {"type": "AND", "conditions": [buy_block]}
        for sell_block in sell_conditions:
            sell_group = {"type": "AND", "conditions": [sell_block]}
            result = run_backtest(
                df,
                ConditionTreeStrategy,
                risk_config,
                {"buy_conditions": buy_group, "sell_conditions": sell_group},
            )
            return_pct = (result["final_value"] - initial_capital) / initial_capital * 100
            results.append(
                {
                    "return_pct": return_pct,
                    "buy_block": buy_block,
                    "sell_block": sell_block,
                    "trades": result["trades"],
                    "final_value": result["final_value"],
                }
            )
        if (i + 1) % 5 == 0 or (i + 1) == len(buy_conditions):
            done = (i + 1) * len(sell_conditions)
            print(f"    매수조건 {i + 1}/{len(buy_conditions)} 완료 ({done}/{total}건)")

    return results
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_grid_search.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: 커밋**

```bash
git add scripts/grid_search.py tests/test_grid_search.py
git commit -m "feat: add grid search full-combo backtest runner"
```

---

### Task 3: `dedup_top_results()` — 중복 제거 + 상위 N개 선정

**Files:**
- Modify: `scripts/grid_search.py`
- Test: `tests/test_grid_search.py`

**Interfaces:**
- Consumes: `compute_grid_results()`가 만드는 결과 딕셔너리 리스트(`return_pct`/`buy_block`/`sell_block`/`trades`/`final_value` 키).
- Produces: `dedup_top_results(results: list[dict], top_n: int) -> list[dict]` — 입력과 동일한 키 구조(`_period_sum` 등 내부 키 없이)의 리스트, 길이 `<= top_n`, `return_pct` 내림차순.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_grid_search.py`에 추가:

```python
from scripts.grid_search import dedup_top_results

_SAME_TRADES = [{"entryTime": "2026-06-01T00:00:00", "exitTime": "2026-06-02T00:00:00"}]
_OTHER_TRADES = [{"entryTime": "2026-06-05T00:00:00", "exitTime": "2026-06-06T00:00:00"}]


def _make_result(return_pct, buy_k_period, sell_period, trades):
    return {
        "return_pct": return_pct,
        "buy_block": {
            "indicator": "STOCH_D",
            "params": {"k_period": buy_k_period},
            "operator": "<",
            "threshold": 20,
        },
        "sell_block": {"indicator": "RSI", "params": {"period": sell_period}, "operator": ">", "threshold": 70},
        "trades": trades,
        "final_value": 1_000_000 * (1 + return_pct / 100),
    }


def test_dedup_keeps_shortest_period_among_identical_trade_sequences():
    results = [
        _make_result(5.0, buy_k_period=20, sell_period=20, trades=_SAME_TRADES),
        _make_result(5.0, buy_k_period=10, sell_period=14, trades=_SAME_TRADES),
        _make_result(5.0, buy_k_period=14, sell_period=14, trades=_SAME_TRADES),
    ]
    deduped = dedup_top_results(results, top_n=20)
    assert len(deduped) == 1
    assert deduped[0]["buy_block"]["params"]["k_period"] == 10
    assert deduped[0]["sell_block"]["params"]["period"] == 14


def test_dedup_excludes_zero_trade_results():
    results = [_make_result(0.0, 10, 10, trades=[])]
    assert dedup_top_results(results, top_n=20) == []


def test_dedup_sorts_desc_and_caps_top_n():
    results = [
        _make_result(1.0, 10, 10, trades=_SAME_TRADES),
        _make_result(9.0, 10, 10, trades=_OTHER_TRADES),
    ]
    deduped = dedup_top_results(results, top_n=1)
    assert len(deduped) == 1
    assert deduped[0]["return_pct"] == 9.0


def test_dedup_leaves_distinct_trade_sequences_untouched():
    results = [
        _make_result(3.0, 10, 10, trades=_SAME_TRADES),
        _make_result(7.0, 10, 10, trades=_OTHER_TRADES),
    ]
    deduped = dedup_top_results(results, top_n=20)
    assert len(deduped) == 2
    assert [r["return_pct"] for r in deduped] == [7.0, 3.0]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_grid_search.py -k dedup -v`
Expected: FAIL — `ImportError: cannot import name 'dedup_top_results'`

- [ ] **Step 3: `dedup_top_results()` 구현**

`scripts/grid_search.py`의 `compute_grid_results()` 아래에 추가:

```python
def _effective_period(params: dict) -> int:
    return params.get("period", params.get("k_period", 0))


def _trade_sequence_key(trades: list[dict]) -> tuple:
    return tuple((t["entryTime"], t["exitTime"]) for t in trades)


def dedup_top_results(results: list[dict], top_n: int) -> list[dict]:
    """동일 거래 시퀀스를 만든 조합 중 매수+매도 period 합이 가장 작은 것만 남기고,
    수익률 내림차순 상위 top_n개를 반환한다. 거래가 0건인 조합은 제외한다.
    """
    groups: dict[tuple, dict] = {}
    for r in results:
        if not r["trades"]:
            continue
        key = _trade_sequence_key(r["trades"])
        period_sum = _effective_period(r["buy_block"]["params"]) + _effective_period(r["sell_block"]["params"])
        existing = groups.get(key)
        if existing is None or period_sum < existing["_period_sum"]:
            groups[key] = {**r, "_period_sum": period_sum}

    deduped = sorted(groups.values(), key=lambda r: r["return_pct"], reverse=True)
    return [{k: v for k, v in r.items() if k != "_period_sum"} for r in deduped[:top_n]]
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_grid_search.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: 커밋**

```bash
git add scripts/grid_search.py tests/test_grid_search.py
git commit -m "feat: add grid search dedup logic"
```

---

### Task 4: CLI `main()` — 조립 + 저장 + RESULT_JSON 출력

**Files:**
- Modify: `scripts/grid_search.py`

**Interfaces:**
- Consumes: `build_condition_grid`, `compute_grid_results`, `dedup_top_results`(Task 1-3). `upbit_data_service.get_candles(market, timeframe, start, end) -> pd.DataFrame`(`upbit_data_service.py:177`). `engine.cache.run_backtest_cached(df, strategy_cls, risk_config, market, timeframe, start, end, strategy_params, title, description) -> dict`(반환값에 `run_id` 키 포함, `engine/cache.py:280`).
- Produces: CLI 실행 시 stdout에 진행 로그 + 마지막 줄 `RESULT_JSON: {...}`. 이 스크립트에는 자동 테스트가 없다 — Task 7에서 수동 스모크로 검증한다.

- [ ] **Step 1: import 및 `main()` 추가**

`scripts/grid_search.py` 상단 import 블록을 아래로 교체(전체):

```python
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone

from engine.cache import run_backtest_cached
from engine.condition_strategy import ConditionTreeStrategy
from engine.runner import run_backtest
from engine.sweep import DEFAULT_RISK_CONFIG
from upbit_data_service import get_candles
```

파일 맨 끝(`dedup_top_results()` 다음)에 추가:

```python
def main() -> None:
    parser = argparse.ArgumentParser(description="오실레이터 그리드서치 백테스트")
    parser.add_argument("--market", required=True, help="마켓코드 (예: KRW-ETH)")
    parser.add_argument("--timeframe", required=True, help="timeframe 코드 (예: minutes60)")
    parser.add_argument("--capital", required=True, type=float, help="운용자금(원)")
    parser.add_argument("--start", required=True, help="시작일 YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="종료일 YYYY-MM-DD")
    parser.add_argument("--top-n", type=int, default=20, help="저장할 상위 개수 (기본 20, 상한 50)")
    args = parser.parse_args()

    top_n = min(args.top_n, 50)

    start_dt = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(args.end, "%Y-%m-%d").replace(
        hour=23, minute=59, second=59, tzinfo=timezone.utc
    )

    print(f"[1] 캔들 조회: {args.market} {args.timeframe} {args.start} ~ {args.end}")
    df = get_candles(args.market, args.timeframe, start_dt, end_dt)
    print(f"    캔들 수: {len(df)}")

    buy_conditions, sell_conditions = build_condition_grid()
    total_combos = len(buy_conditions) * len(sell_conditions)
    print(f"[2] 매수 조건 {len(buy_conditions)}개 x 매도 조건 {len(sell_conditions)}개 = 총 {total_combos:,}개 조합")

    risk_config = {**DEFAULT_RISK_CONFIG, "initial_capital": args.capital}

    t0 = time.perf_counter()
    results = compute_grid_results(df, buy_conditions, sell_conditions, risk_config)
    elapsed = time.perf_counter() - t0
    print(f"\n[3] 전체 계산 완료: {len(results)}건, {elapsed:.1f}초 ({elapsed / 60:.1f}분)")

    top_results = dedup_top_results(results, top_n)
    print(f"\n[4] dedup 후 상위 {len(top_results)}개를 백테스트 결과에 저장 중...")

    saved_summaries = []
    for rank, r in enumerate(top_results, start=1):
        buy_block, sell_block = r["buy_block"], r["sell_block"]
        buy_group = {"type": "AND", "conditions": [buy_block]}
        sell_group = {"type": "AND", "conditions": [sell_block]}
        title = (
            f"[Grid] 매수 {buy_block['indicator']}{buy_block['params']}{buy_block['operator']}{buy_block['threshold']} "
            f"/ 매도 {sell_block['indicator']}{sell_block['params']}{sell_block['operator']}{sell_block['threshold']}"
        )
        description = (
            f"grid search - {args.market}/{args.timeframe}/{args.start}~{args.end}, "
            f"수익률 {r['return_pct']:+.2f}% (상위 {rank}위)"
        )
        saved = run_backtest_cached(
            df=df,
            strategy_cls=ConditionTreeStrategy,
            risk_config=risk_config,
            market=args.market,
            timeframe=args.timeframe,
            start=start_dt,
            end=end_dt,
            strategy_params={"buy_conditions": buy_group, "sell_conditions": sell_group},
            title=title,
            description=description,
        )
        print(f"  {rank:2d}. {r['return_pct']:+.2f}%  run_id={saved['run_id'][:12]}...")
        saved_summaries.append(
            {"rank": rank, "run_id": saved["run_id"], "return_pct": round(r["return_pct"], 2), "title": title}
        )

    result_json = {"total_combos": total_combos, "elapsed_sec": round(elapsed, 1), "saved": saved_summaries}
    print("\n완료.")
    print(f"RESULT_JSON: {json.dumps(result_json, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 기존 단위 테스트가 여전히 통과하는지 확인**

Run: `pytest tests/test_grid_search.py -v`
Expected: PASS (11 tests) — `main()` 추가는 기존 함수를 바꾸지 않으므로 전부 그대로 통과해야 함.

- [ ] **Step 3: 커밋**

```bash
git add scripts/grid_search.py
git commit -m "feat: add grid search CLI entrypoint"
```

(실제 실행을 통한 수동 검증은 Task 7에서 수행 — 실 API 호출과 DB 쓰기가 있어 여기서는 정적 검증만 한다.)

---

### Task 5: `.claude/skills/grid-search/SKILL.md` 작성

**Files:**
- Create: `.claude/skills/grid-search/SKILL.md`

**Interfaces:**
- Consumes: Task 4에서 완성한 `scripts/grid_search.py`의 CLI 인자(`--market --timeframe --capital --start --end --top-n`)와 출력(`RESULT_JSON: {...}` 마지막 줄).

- [ ] **Step 1: 디렉터리 생성 및 SKILL.md 작성**

`.claude/skills/grid-search/SKILL.md` 새로 생성:

```markdown
---
name: grid-search
description: Sweep oscillator buy/sell threshold and period grids for an Upbit backtest strategy and save the top results. Trigger when the user sends a message starting with "grid search" followed by comma-separated 코인명,운용자금,봉데이터,운용기간,상위N개 (e.g. "grid search 이더리움,1000만원,1시간,2026-06-01~2026-07-31,20"). 업비트 백테스트 전략의 매수/매도 오실레이터 지표 조합을 그리드서치로 탐색해 상위 결과를 저장할 때 사용합니다.
---

# Grid Search

`grid search` 명령을 받으면 오실레이터 5종(RSI/STOCH_K/STOCH_D/CCI/WILLIAMS_R) + 매도전용
3종(STOP_LOSS_PCT/TAKE_PROFIT_PCT/HOLDING_PERIOD_BARS)의 전 교차 그리드(2,565개 조합)를
`scripts/grid_search.py`로 계산하고, 중복 거래를 제거한 상위 N개를 "백테스트 결과"에 저장한다.

## 명령 형식

```
grid search [코인명],[운용자금],[봉데이터],[운용기간],[상위N개]
```

- 콤마로 구분된 5개 필드, 순서 고정.
- `상위N개`는 생략 가능(생략 시 20).
- 예시: `grid search 이더리움,1000만원,1시간,2026-06-01~2026-07-31,20`

## 파싱 규칙

| 필드 | 예시 | 변환 규칙 |
|---|---|---|
| 코인명 | `이더리움` | 코인명을 마켓코드로 매핑(이더리움→ETH→`KRW-ETH`). 별도 룩업 테이블 없이 직접 추론. 모호하면 사용자에게 되물어라. |
| 운용자금 | `1000만원` | 원화 정수로 환산(`10000000`). `1억`, `500만원` 등 한글 단위를 지원하라. |
| 봉데이터 | `1시간` | 아래 고정 매핑표만 사용하라: `1분→minutes1`, `3분→minutes3`, `5분→minutes5`, `15분→minutes15`, `30분→minutes30`, `1시간→minutes60`, `4시간→minutes240`, `1일→days`. 표에 없는 단위는 미지원이라고 안내하고 진행하지 마라. |
| 운용기간 | `2026-06-01~2026-07-31` | `~`로 시작일/종료일을 그대로 분리해 사용하라. `최근 3개월` 같은 duration 표현은 지원하지 않는다 — 명시적 날짜 범위를 요청하라. |
| 상위N개 | `20` | 생략 시 20. 50 초과 입력은 50으로 캡하고, 캡했다는 사실을 사용자에게 안내하라. |

코인명/운용자금/봉데이터/운용기간 4개 필드 중 하나라도 파싱할 수 없거나 모호하면, 스크립트를
실행하지 말고 사용자에게 되물어라. 부분 입력으로 임의 진행하지 마라.

## 실행 절차

1. 위 규칙대로 명령을 파싱한다.
2. 파싱 결과를 표로 정리해 사용자에게 보여주고 확인을 받는다. 이 표에는 반드시
   마켓코드/timeframe 코드/운용자금(원 단위 숫자)/시작일/종료일/상위N개가 포함되어야 한다.
   예상 소요 시간(약 9분, 2,565개 조합 기준)도 함께 안내한다.
3. 사용자가 확인하면, 아래 형태로 `scripts/grid_search.py`를 저장소 루트에서 백그라운드로
   실행한다:

   ```bash
   python scripts/grid_search.py --market KRW-ETH --timeframe minutes60 \
     --capital 10000000 --start 2026-06-01 --end 2026-07-31 --top-n 20
   ```

4. 실행이 끝나면 stdout 마지막 줄의 `RESULT_JSON: {...}`를 파싱한다. 그 앞의 로그 줄들은
   사람이 읽는 진행 상황이므로 필요하면 요약해서 보여줘도 되지만, 최종 보고 수치는 반드시
   `RESULT_JSON`에서 가져온다.
5. 사용자에게 `total_combos`(총 조합 수), `elapsed_sec`(소요 시간), `saved` 리스트(순위/수익률/
   제목)를 요약해서 보고하고, "백테스트 결과" 페이지(`[Grid]` 접두사)에서 상세를 확인할 수
   있다고 안내한다.

## 주의 사항

- `--capital`은 원 단위 정수로 넘긴다(예: 1000만원 → `10000000`).
- `--start`/`--end`는 `YYYY-MM-DD` 형식이어야 한다.
- 스크립트는 저장소 루트(`260711-upbit-v1/`)에서 실행해야 한다(`from engine...`, `from upbit_data_service...` 절대 임포트를 쓰기 때문).
```

- [ ] **Step 2: 커밋**

```bash
git add .claude/skills/grid-search/SKILL.md
git commit -m "feat: add grid-search SKILL.md"
```

---

### Task 6: `summarizeGroup()` — params 표기 수정

**Files:**
- Modify: `frontend/lib/condition-summary.ts`

**Interfaces:**
- Consumes: `ConditionBlock`(`frontend/lib/types/strategy.ts:3-8`, `params: Record<string, number>`).
- Produces: `summarizeGroup(group: ConditionGroup): string` 반환 문자열 형식 변경 — 기존 `RSI<20` → `RSI(period=10)<20` (params 있을 때만).

- [ ] **Step 1: `summarizeGroup()` 수정**

`frontend/lib/condition-summary.ts` 전체를 아래로 교체:

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

function summarizeParams(params: Record<string, number>): string {
  const entries = Object.entries(params);
  if (entries.length === 0) return '';
  return `(${entries.map(([k, v]) => `${k}=${v}`).join(', ')})`;
}

export function summarizeGroup(group: ConditionGroup): string {
  if (group.conditions.length === 0) return '(조건 없음)';
  const parts = group.conditions.map((c) =>
    isConditionBlock(c)
      ? `${c.indicator}${summarizeParams(c.params)}${OPERATOR_SYMBOLS[c.operator]}${c.threshold}`
      : `(${summarizeGroup(c)})`
  );
  return parts.join(group.type === 'AND' ? ' and ' : ' or ');
}
```

- [ ] **Step 2: 타입체크로 정적 검증**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음 (frontend 디렉터리에 프론트 컨벤션대로 이미 dev 서버가 떠 있다면 별도 서버 재시작 불필요 — HMR로 반영됨).

- [ ] **Step 3: 브라우저에서 수동 확인**

프론트 dev 서버(`npm run dev`, localhost:3000)가 떠 있는 상태에서 "백테스트 결과" 페이지(`/backtests`)를 열어, params가 있는 지표(예: RSI)를 쓴 실행 항목의 매수/매도 전략 컬럼이 `RSI(period=10)<20` 형태로 표시되는지 확인한다. params가 없는 지표(STOP_LOSS_PCT 등)는 기존처럼 `STOP_LOSS_PCT<=-5`로 그대로 보여야 한다.

- [ ] **Step 4: 커밋**

```bash
git add frontend/lib/condition-summary.ts
git commit -m "fix: render indicator params in summarizeGroup"
```

---

### Task 7: 통합 검증

**Files:** 없음 (검증 전용 태스크)

- [ ] **Step 1: 스크립트 직접 실행**

저장소 루트에서:

```bash
python scripts/grid_search.py --market KRW-ETH --timeframe minutes60 \
  --capital 10000000 --start 2026-06-01 --end 2026-07-31 --top-n 20
```

확인할 것:
- `[2]` 로그의 총 조합 수가 `2,565`인지.
- 스크립트가 에러 없이 끝까지 실행되고 마지막 줄에 `RESULT_JSON: {...}`가 출력되는지, 그 JSON이 `json.loads`로 파싱 가능한지.
- `RESULT_JSON.saved`의 길이가 20 이하인지(dedup으로 20개 미만일 수 있음).

주의: STOCH_K/STOCH_D 파라미터 키 수정 때문에 2026-08-01 세션 프로토타입의 정확한 수치(1위 STOCH_D<10/RSI>80, +15.46%)와 동일할 필요는 없다.

- [ ] **Step 2: 백엔드/프론트 기동 후 "백테스트 결과" 확인**

`uvicorn backend.main:app --reload --port 8000`과 `cd frontend && npm run dev`가 떠 있는 상태에서 `http://localhost:3000/backtests`를 열어:
- 제목이 `[Grid]`로 시작하는 항목 20개(또는 dedup 결과 수)가 최상단 근처에 보이는지.
- 그중 두 항목을 비교해 진입/청산 시점이 동일한 거래 시퀀스를 만드는 두 조합이 남아있지 않은지(수동 샘플 확인 — 예: 상위 5개의 상세 페이지에서 거래 내역 비교).
- 매수/매도 전략 컬럼에 `RSI(period=10)<20` 형태로 params가 표시되는지(Task 6 검증 겸함).

- [ ] **Step 3: SKILL.md를 통한 자연어 명령 재현**

Claude Code 세션에서 `grid search 이더리움,1000만원,1시간,2026-06-01~2026-07-31,20`을 입력해:
- 매핑 확인 표(마켓코드/timeframe/자본금/기간/N)가 먼저 뜨는지.
- 확인 후 Step 1과 동일한 스크립트가 백그라운드로 실행되고, 완료 후 구조화된 요약(순위/수익률/제목)이 보고되는지.

- [ ] **Step 4: 결과 보고**

위 3단계가 모두 통과하면 사용자에게 "grid search 스킬 구현 및 검증 완료"로 보고한다. 실패하는 항목이 있으면 어느 Task로 돌아가 고쳐야 하는지 명시한다.
