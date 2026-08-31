"""
engine/regime_ml_data.py

ML 장세 판별기 학습용 마켓별 데이터 로더. backend/main.py:_fetch_backtest_dataframe()의
병합 패턴(get_candles + aux market close + 외부데이터 merge)을 재사용하되, 그 함수는
FastAPI HTTPException과 조건트리(buy_dict/sell_dict)에 결합돼 있어 그대로 쓸 수 없다.
이 로더는 조건 없이 항상 전체 aux 데이터를 붙이고, 결측은 (에러 대신) NaN으로 남긴다 —
LightGBM이 결측 피처를 네이티브로 처리하므로 백테스트만큼 엄격할 필요가 없다(설계
문서 "B. 피처" 절 참고). 설계 문서:
docs/superpowers/specs/2026-08-27-regime-detector-ml-classifier-design.md
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from binance_data_service import (
    BinanceSymbolNotFoundError,
    binance_symbol,
    get_binance_close,
    get_binance_funding_rate,
    merge_funding_rate,
)
from external_data_service import get_fear_greed_cmc, merge_fear_greed
from macro_data_service import (
    get_fed_funds_rate,
    get_kr_call_rate,
    get_us_yield_curve_spread,
    get_usdkrw_rate,
    merge_fred_series,
    merge_usdkrw_rate,
)
from trading.live_indicators import compute_korea_premium_value
from upbit_data_service import get_candles

# engine/runner.py:AUX_MARKET_LINE_NAME과 값이 같아야 한다. engine/runner.py는
# backtrader Cerebro 실행기라 이 로더가 임포트할 이유가 없으므로 별도 정의한다
# (engine/regime_features.py의 _MIN_VOLATILITY_FLOOR와 같은 이유).
_AUX_MARKET_LINE_NAME = {"KRW-BTC": "btc_close", "KRW-USDT": "usdt_close"}


def load_market_training_data(
    market: str, timeframe: str, start: datetime, end: datetime
) -> pd.DataFrame:
    """market의 캔들 + 학습에 필요한 모든 aux 컬럼을 병합해 반환한다. market 자체의
    캔들이 비어있으면 ValueError(어떤 피처도 계산할 수 없으므로). 그 외(BTC/USDT
    상관관계용 aux 마켓, 바이낸스 심볼 부재, 외부데이터 커버리지 부족)는 NaN으로
    남기고 계속 진행한다.

    2026-08-31 캘린더/거시경제 피처 추가 라운드에서 미국 기준금리/미국 장단기
    국채금리차/한국 콜금리(기준금리 대리지표)/원-달러 공식환율 4개 원시 컬럼도
    함께 병합한다(macro_data_service.py, FRED+Frankfurter, 둘 다 API 키 불필요).
    """
    df = get_candles(market, timeframe, start, end)
    if df.empty:
        raise ValueError(
            f"{market} {timeframe} 구간에 캔들 데이터가 없습니다: {start.date()}~{end.date()}"
        )

    for aux_market, line_name in _AUX_MARKET_LINE_NAME.items():
        if market == aux_market:
            df = df.assign(**{line_name: df["close"]})
            continue
        aux_df = get_candles(aux_market, timeframe, start, end)
        if aux_df.empty:
            df = df.assign(**{line_name: float("nan")})
            continue
        df = df.merge(
            aux_df[["candle_time", "close"]].rename(columns={"close": line_name}),
            on="candle_time", how="left",
        )
        df[line_name] = df[line_name].ffill().bfill()

    fng_df = get_fear_greed_cmc(start, end)
    df = merge_fear_greed(df, fng_df)

    symbol = binance_symbol(market)  # Pure string transform, can't raise
    try:
        binance_df = get_binance_close(symbol, timeframe, start, end)
    except BinanceSymbolNotFoundError:
        df = df.assign(binance_close=float("nan"), funding_rate_value=float("nan"))
    else:
        if binance_df.empty:
            df = df.assign(binance_close=float("nan"))
        else:
            df = df.merge(
                binance_df.rename(columns={"close": "binance_close"}), on="candle_time", how="left"
            )
        funding_df = get_binance_funding_rate(symbol, start, end)
        df = merge_funding_rate(df, funding_df)

    df["korea_premium_value"] = compute_korea_premium_value(df)

    fed_funds_df = get_fed_funds_rate(start, end)
    df = merge_fred_series(df, fed_funds_df, "fed_funds_rate_value")

    yield_curve_df = get_us_yield_curve_spread(start, end)
    df = merge_fred_series(df, yield_curve_df, "treasury_yield_spread_value")

    kr_call_rate_df = get_kr_call_rate(start, end)
    df = merge_fred_series(df, kr_call_rate_df, "kr_call_rate_value")

    usdkrw_df = get_usdkrw_rate(start, end)
    df = merge_usdkrw_rate(df, usdkrw_df)

    return df
