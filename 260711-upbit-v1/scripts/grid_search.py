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
