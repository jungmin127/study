"""
engine/regime_ml_features.py

장세 판별 ML 분류기의 피처 매트릭스를 만든다. trading.live_indicators.LIVE_INDICATOR_FACTORY
(이미 백트레이더 대비 골든테스트로 검증된 순수 pandas 지표)를 재구현 없이 그대로
순회하고, engine.regime_features.py의 반전게이팅 실험용 5개 함수 + momentum/volatility
EWMA(raw_score)를 더한다. I/O 없는 순수 함수 — 입력 df는
engine/regime_ml_data.py가 준비한다. 설계 문서:
docs/superpowers/specs/2026-08-27-regime-detector-ml-classifier-design.md
"""
from __future__ import annotations

import pandas as pd

from engine.regime_features import (
    level_proximity,
    pivot_levels,
    reversal_gate,
    vpin_score,
    volume_confirm,
)
from trading.live_indicators import LIVE_INDICATOR_FACTORY

# engine/regime_features.py:_MIN_VOLATILITY_FLOOR와 값이 같아야 한다(raw_score
# 0-나눗셈 방지) — 순환참조를 피하려 별도 정의.
_MIN_VOLATILITY_FLOOR = 1e-6


def build_feature_matrix(df: pd.DataFrame, market: str, half_life_bars: float) -> pd.DataFrame:
    """df: close/high/low/volume/trade_value + btc_close/usdt_close/binance_close/
    fear_greed_value/funding_rate_value/korea_premium_value를 전부 포함해야 한다
    (engine.regime_ml_data.load_market_training_data()가 반환하는 형태). 반환
    DataFrame은 df와 같은 행 수/인덱스를 유지하며(워밍업 구간은 NaN), 원본 OHLCV
    컬럼은 포함하지 않는다(피처 전용) — market 범주형 컬럼만 추가한다."""
    # OBV(create_obv)는 윈도우 없는 누적합이라 추론 시(짧은 최근 구간)와 학습
    # 시(수년치) 스케일이 어긋난다(backend/regime_ml_service.py 참고) — 피처에서
    # 제외한다. 같은 레지스트리의 OBV_ROC는 rolling window 기반 %지표라 스케일
    # 문제가 없으므로 그대로 둔다.
    features: dict[str, pd.Series] = {
        name: factory(df) for name, factory in LIVE_INDICATOR_FACTORY.items() if name != "OBV"
    }

    returns = df["close"].pct_change(fill_method=None)
    momentum = returns.ewm(halflife=half_life_bars).mean()
    volatility = returns.ewm(halflife=half_life_bars).std()
    raw_score = momentum / volatility.clip(lower=_MIN_VOLATILITY_FLOOR)
    r1, s1 = pivot_levels(df["high"], df["low"], df["close"])
    proximity = level_proximity(df["close"], raw_score, r1, s1, volatility)
    vpin = vpin_score(df["volume"], df["close"])

    features["RAW_SCORE"] = raw_score
    features["VOLUME_CONFIRM"] = volume_confirm(df["trade_value"])
    features["VPIN_SCORE"] = vpin
    features["LEVEL_PROXIMITY"] = proximity
    features["REVERSAL_GATE"] = reversal_gate(vpin, proximity)

    result = pd.DataFrame(features, index=df.index)
    result["market"] = pd.Categorical([market] * len(df))
    return result
