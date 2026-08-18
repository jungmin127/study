"""워밍업 버퍼 회귀 테스트 (최종 whole-branch 리뷰 Critical #1).

LIVE_INDICATOR_FACTORY의 A그룹(순수 OHLCV만 필요한 지표) 33개 전부에 대해, 실제
evaluate_signals()가 계산하는 것과 동일한 방식(engine.condition_tree.max_required_period)으로
required_bars를 구하고, signal_engine의 워밍업 공식(_WARMUP_MULTIPLIER/_WARMUP_BUFFER_BARS)으로
필요한 봉 수를 계산해 df를 만들었을 때 각 지표의 마지막 값이 NaN이 아닌지 검증한다.

B그룹(MARKET_TREND/BTC_CORRELATION/USDT_CORRELATION/FEAR_GREED_CMC/KOREA_PREMIUM/
FUNDING_RATE)은 OHLCV 외 추가 컬럼(btc_close/usdt_close/fear_greed_value/
korea_premium_value/funding_rate_value)이 필요해 이 테스트 범위 밖이다.

CCI/VPIN(이중 rolling/버킷 구조)과 MACD_signal/MACD_PPO_signal(create_macd_line을 거쳐
slow+signal 두 단계 워밍업이 누적)이 특히 취약하다 — 워밍업 버퍼가 부족하면 예외도 로그도
없이 해당 지표값이 NaN이 되고, engine/condition_tree.eval_group_values가 이를 "판단불가"로
건너뛰어 실거래 조건이 조용히 무시된다.
"""
from __future__ import annotations

import pandas as pd
import pytest

import trading.signal_engine as signal_engine
from engine.condition_tree import max_required_period
from tests.signal_fixtures import make_oscillating_df
from trading.live_indicators import LIVE_INDICATOR_FACTORY

# B그룹: OHLCV 외 추가 컬럼이 필요해 이 테스트 범위 밖(설계 스펙 결정 8 관련, signal_engine.py의
# _populate_b_group_columns/_merge_aux_markets가 별도로 채운다).
_B_GROUP = {
    "MARKET_TREND", "BTC_CORRELATION", "USDT_CORRELATION",
    "FEAR_GREED_CMC", "KOREA_PREMIUM", "FUNDING_RATE",
}

_MACD_PARAMS = {"fast": 12, "slow": 26, "signal": 9}

# 각 A그룹 지표의 대표 파라미터 — 기존 골든테스트(tests/test_live_indicators_*.py)가 쓰는
# 관례를 그대로 재사용한다.
_A_GROUP_PARAMS: dict[str, dict] = {
    "SMA": {"period": 14},
    "EMA": {"period": 14},
    "WMA": {"period": 14},
    "RSI": {"period": 14},
    "MACD_line": _MACD_PARAMS,
    "MACD_signal": _MACD_PARAMS,
    "MACD_PPO": _MACD_PARAMS,
    "MACD_PPO_signal": _MACD_PARAMS,
    "STOCH_K": {"k_period": 14, "d_period": 3},
    "STOCH_D": {"k_period": 14, "d_period": 3},
    "CCI": {"period": 20},
    "WILLIAMS_R": {"period": 14},
    "MOMENTUM_PCT": {"period": 5},
    "ATR": {"period": 14},
    "ATR_PCT": {"period": 14},
    "BB_upper": {"period": 20},
    "BB_lower": {"period": 20},
    "BB_middle": {"period": 20},
    "BB_PERCENT_B": {"period": 20},
    "OBV": {},
    "VOLUME_SMA": {"period": 20},
    "TRADE_VALUE": {},
    "TRADE_VALUE_SMA": {"period": 20},
    "VPIN": {"period": 20},
    "FIB_382": {"period": 20},
    "FIB_500": {"period": 20},
    "FIB_618": {"period": 20},
    "FIB_382_PCT": {"period": 20},
    "FIB_500_PCT": {"period": 20},
    "FIB_618_PCT": {"period": 20},
    "PIVOT_P": {},
    "PIVOT_R1": {},
    "PIVOT_S1": {},
    "PIVOT_P_PCT": {},
    "PIVOT_R1_PCT": {},
    "PIVOT_S1_PCT": {},
    "VPVR_POC": {"period": 50},
    "VPVR_VAH": {"period": 50},
    "VPVR_VAL": {"period": 50},
}


def test_a_group_params_cover_every_a_group_indicator():
    """새 지표가 LIVE_INDICATOR_FACTORY에 추가됐는데 이 표를 안 갱신하면 이 assert가
    먼저 실패해야 한다(파라미터 표와 실제 A그룹 집합의 drift 방지)."""
    a_group = set(LIVE_INDICATOR_FACTORY) - _B_GROUP
    assert a_group == set(_A_GROUP_PARAMS)


def _required_bars_for(name: str, params: dict) -> int:
    """evaluate_signals()가 required_bars를 구하는 방식과 정확히 동일하게 계산한다."""
    group = {"type": "AND", "conditions": [
        {"indicator": name, "params": params, "operator": ">", "threshold": 0},
    ]}
    return max_required_period(group)


@pytest.mark.parametrize("name,params", sorted(_A_GROUP_PARAMS.items()))
def test_warmup_formula_produces_non_nan_last_value(name, params):
    required_bars = _required_bars_for(name, params)
    n = required_bars * signal_engine._WARMUP_MULTIPLIER + signal_engine._WARMUP_BUFFER_BARS
    df = make_oscillating_df(n=n)
    if name in {"TRADE_VALUE", "TRADE_VALUE_SMA"}:
        df["trade_value"] = df["close"] * df["volume"]

    result = LIVE_INDICATOR_FACTORY[name](df, **params).iloc[-1]

    assert pd.notna(result), (
        f"{name}({params})의 마지막 값이 워밍업 {n}봉(required_bars={required_bars})으로도 NaN — "
        "워밍업 공식이 부족하다"
    )
