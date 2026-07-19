from __future__ import annotations

from .momentum import (
    create_cci,
    create_macd_line,
    create_macd_signal,
    create_rsi,
    create_stoch_d,
    create_stoch_k,
    create_williams_r,
)
from .trend import create_ema, create_sma, create_wma
from .volatility import create_atr, create_bb_lower, create_bb_middle, create_bb_upper
from .volume import create_obv, create_volume_sma

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
    "OBV": create_obv,
    "VOLUME_SMA": create_volume_sma,
}

__all__ = ["INDICATOR_FACTORY"]
