"""
engine/segment_analysis.py

세그먼트(규모) 분석: KRW 마켓 코인을 24시간 거래대금과 30일 변동성 기준으로
대형주/중형주/잡주로 분류하고 SQLite(engine.cache)에 저장한다.

업비트 API는 시가총액(유통량 x 가격)을 제공하지 않으므로, 거래대금을 규모의
대리지표로 쓴다. 변동성이 높을수록, 거래대금이 낮을수록 "잡주"에 가깝다고 보는
분류 규칙은 도메인 설계 문서
docs/superpowers/specs_v1/2026-07-25-segment-size-analysis-design.md 참고.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from engine.cache import save_segment_classification
from upbit_data_service import get_candles, get_krw_markets_with_ticker, get_market_cautions

VOLATILITY_WINDOW_DAYS = 30
LARGE_CAP_TRADE_VALUE_PERCENTILE = 70.0
LARGE_CAP_VOLATILITY_PERCENTILE = 50.0
JUNK_TRADE_VALUE_PERCENTILE = 30.0
JUNK_VOLATILITY_PERCENTILE = 50.0


def _compute_volatility(market: str) -> float | None:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=VOLATILITY_WINDOW_DAYS + 5)
    df = get_candles(market, "days", start, end)
    if len(df) < 2:
        return None
    closes = df["close"].tail(VOLATILITY_WINDOW_DAYS + 1)
    returns = closes.pct_change().dropna()
    if returns.empty:
        return None
    return float(returns.std())


def _percentile_rank(values: list[float | None]) -> list[float | None]:
    """값이 클수록 큰 percentile(0~100)을 부여한다. None은 그대로 None 유지."""
    series = pd.Series(values, dtype="float64")
    ranked = series.rank(pct=True) * 100
    return [None if pd.isna(v) else float(v) for v in ranked]


def _classify(trade_value_pct: float | None, volatility_pct: float | None) -> str:
    if trade_value_pct is None or volatility_pct is None:
        return "mid"
    if (
        trade_value_pct >= LARGE_CAP_TRADE_VALUE_PERCENTILE
        and volatility_pct <= LARGE_CAP_VOLATILITY_PERCENTILE
    ):
        return "large"
    if trade_value_pct < JUNK_TRADE_VALUE_PERCENTILE and volatility_pct > JUNK_VOLATILITY_PERCENTILE:
        return "junk"
    return "mid"


def run_segment_batch() -> int:
    """모든 KRW 마켓을 대형주/중형주/잡주로 분류해 저장한다. 저장한 행 수를 반환한다."""
    markets = get_krw_markets_with_ticker()
    cautions = get_market_cautions()

    trade_values = [m["trade_price_24h"] for m in markets]
    volatilities = [_compute_volatility(m["market"]) for m in markets]

    trade_value_percentiles = _percentile_rank(trade_values)
    volatility_percentiles = _percentile_rank(volatilities)

    computed_at = datetime.now(timezone.utc).isoformat()
    rows = []
    for m, trade_value, volatility, tv_pct, vol_pct in zip(
        markets, trade_values, volatilities, trade_value_percentiles, volatility_percentiles
    ):
        rows.append({
            "market": m["market"],
            "korean_name": m["korean_name"],
            "segment": _classify(tv_pct, vol_pct),
            "trade_value_24h": trade_value,
            "volatility_30d": volatility,
            "trade_value_percentile": tv_pct,
            "volatility_percentile": vol_pct,
            "is_caution": cautions.get(m["market"], False),
            "computed_at": computed_at,
        })

    save_segment_classification(rows)
    return len(rows)
