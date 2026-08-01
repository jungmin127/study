from __future__ import annotations

from .market import create_btc_correlation, create_market_trend, create_usdt_correlation
from .momentum import (
    create_cci,
    create_macd_line,
    create_macd_signal,
    create_momentum_pct,
    create_rsi,
    create_stoch_d,
    create_stoch_k,
    create_williams_r,
)
from .price_levels import (
    create_fib_382,
    create_fib_500,
    create_fib_618,
    create_pivot_p,
    create_pivot_r1,
    create_pivot_s1,
    create_vpvr_poc,
    create_vpvr_vah,
    create_vpvr_val,
)
from .sentiment import create_fear_greed_cmc, create_korea_premium
from .trend import create_ema, create_sma, create_wma
from .volatility import create_atr, create_atr_pct, create_bb_lower, create_bb_middle, create_bb_percent_b, create_bb_upper
from .volume import create_obv, create_trade_value, create_trade_value_sma, create_volume_sma, create_vpin

INDICATOR_FACTORY: dict[str, object] = {
    "SMA": create_sma,
    "EMA": create_ema,
    "WMA": create_wma,
    "RSI": create_rsi,
    "MACD_line": create_macd_line,
    "MACD_signal": create_macd_signal,
    "STOCH_K": create_stoch_k,
    "STOCH_D": create_stoch_d,
    "CCI": create_cci,
    "WILLIAMS_R": create_williams_r,
    "BB_upper": create_bb_upper,
    "BB_lower": create_bb_lower,
    "BB_middle": create_bb_middle,
    "ATR": create_atr,
    "BB_PERCENT_B": create_bb_percent_b,
    "ATR_PCT": create_atr_pct,
    "OBV": create_obv,
    "VOLUME_SMA": create_volume_sma,
    "VPIN": create_vpin,
    "TRADE_VALUE": create_trade_value,
    "TRADE_VALUE_SMA": create_trade_value_sma,
    "MARKET_TREND": create_market_trend,
    "BTC_CORRELATION": create_btc_correlation,
    "USDT_CORRELATION": create_usdt_correlation,
    "MOMENTUM_PCT": create_momentum_pct,
    "FIB_382": create_fib_382,
    "FIB_500": create_fib_500,
    "FIB_618": create_fib_618,
    "PIVOT_P": create_pivot_p,
    "PIVOT_R1": create_pivot_r1,
    "PIVOT_S1": create_pivot_s1,
    "VPVR_POC": create_vpvr_poc,
    "VPVR_VAH": create_vpvr_vah,
    "VPVR_VAL": create_vpvr_val,
    "FEAR_GREED_CMC": create_fear_greed_cmc,
    "KOREA_PREMIUM": create_korea_premium,
}

__all__ = ["INDICATOR_FACTORY"]
