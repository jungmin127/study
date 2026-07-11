from __future__ import annotations

import pandas as pd

UPBIT_BASE_URL = "https://api.upbit.com/v1"

_CANDLE_COLUMNS = ["candle_time", "open", "high", "low", "close", "volume"]


def _endpoint_for_timeframe(timeframe: str) -> str:
    if timeframe == "days":
        return f"{UPBIT_BASE_URL}/candles/days"
    if timeframe.startswith("minutes"):
        unit = timeframe[len("minutes"):]
        if not unit.isdigit():
            raise ValueError(f"지원하지 않는 timeframe: {timeframe}")
        return f"{UPBIT_BASE_URL}/candles/minutes/{unit}"
    raise ValueError(f"지원하지 않는 timeframe: {timeframe}")


def _parse_candles(raw: list[dict]) -> pd.DataFrame:
    if not raw:
        return pd.DataFrame(columns=_CANDLE_COLUMNS)

    df = pd.DataFrame(raw)
    df["candle_time"] = pd.to_datetime(df["candle_date_time_utc"], utc=True)
    df = df.rename(
        columns={
            "opening_price": "open",
            "high_price": "high",
            "low_price": "low",
            "trade_price": "close",
            "candle_acc_trade_volume": "volume",
        }
    )
    df = df[_CANDLE_COLUMNS]
    return df.sort_values("candle_time").reset_index(drop=True)
