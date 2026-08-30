"""
engine/regime_ml_cross_sectional.py

장세 판별 ML의 베타중립(cross-sectional) 피처. 코인별 자기상대적 피처
(VOLATILITY_PERCENTILE 등, engine/regime_ml_features.py)와 달리, 이 피처들은
"같은 시각 다른 마켓들과 비교해 지금 이 코인이 어떤지"를 표현한다 — 알트코인
대부분이 BTC와 강하게 동조하므로, 종목 고유 신호만 걸러내려는 목적. 설계 문서:
docs/superpowers/specs/2026-08-31-regime-ml-performance-improvement-design.md
"""
from __future__ import annotations

import pandas as pd


def compute_cross_sectional_features(
    market_returns: dict[str, pd.Series], btc_market: str
) -> dict[str, pd.DataFrame]:
    """market_returns: market -> candle_time을 인덱스로 하는 수익률(pct_change)
    Series. 마켓마다 인덱스가 완전히 같지 않아도 된다(outer join, 없는 시점은
    NaN). 반환: market -> DataFrame(columns=[BETA_NEUTRAL_RETURN,
    CROSS_SECTIONAL_RANK], index=전체 마켓 candle_time 합집합)."""
    wide = pd.DataFrame(market_returns)  # outer join, 컬럼=market
    btc_return = wide[btc_market]
    beta_neutral = wide.sub(btc_return, axis=0)
    rank_pct = wide.rank(axis=1, pct=True)

    result: dict[str, pd.DataFrame] = {}
    for market in market_returns:
        result[market] = pd.DataFrame({
            "BETA_NEUTRAL_RETURN": beta_neutral[market],
            "CROSS_SECTIONAL_RANK": rank_pct[market],
        })
    return result
