"""
scripts/grid_search.py

'grid search' 스킬의 연산 엔진. 오실레이터 5종(RSI/STOCH_K/STOCH_D/CCI/WILLIAMS_R) +
매도전용 3종(STOP_LOSS_PCT/TAKE_PROFIT_PCT/HOLDING_PERIOD_BARS)의 전 교차 그리드를
계산하고, 거래 시퀀스가 동일한 조합은 dedup한 뒤 상위 N개만 백테스트 결과에 저장한다.
Run: python scripts/grid_search.py --market KRW-ETH --timeframe minutes60 \
     --capital 10000000 --start 2026-06-01 --end 2026-07-31 --top-n 20
"""
from __future__ import annotations

from engine.condition_strategy import ConditionTreeStrategy
from engine.runner import run_backtest

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
