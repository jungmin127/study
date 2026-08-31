"""
tests/test_regime_ml_data.py

engine.regime_ml_data.load_market_training_data()를 검증한다. backend/main.py의
_fetch_backtest_dataframe() 병합 패턴(get_candles + aux market close + 외부데이터
merge)을 조건트리 없이 항상 전체 재사용하되, 외부데이터 결측은 (그 컬럼이 완전히
비어있어도) 에러 없이 NaN으로 남긴다 — ML 피처는 LightGBM이 결측을 네이티브로
처리하므로 규칙기반 백테스트(backend/main.py)만큼 엄격할 필요가 없다.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

import engine.regime_ml_data as regime_ml_data
from binance_data_service import BinanceSymbolNotFoundError
from engine.regime_ml_data import load_market_training_data

START = datetime(2024, 1, 1, tzinfo=timezone.utc)
END = datetime(2024, 1, 3, tzinfo=timezone.utc)
_CANDLE_COLUMNS = ["candle_time", "open", "high", "low", "close", "volume", "trade_value"]


def _make_candle_df(n: int, base: float = 100.0) -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    closes = [base + i for i in range(n)]
    return pd.DataFrame({
        "candle_time": dates,
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": [1.0] * n, "trade_value": [1.0] * n,
    })


def _patch_common(monkeypatch, *, symbol_found: bool = True):
    monkeypatch.setattr(regime_ml_data, "get_fear_greed_cmc", lambda *a, **k: pd.DataFrame(columns=["date", "fear_greed_value"]))
    monkeypatch.setattr(
        regime_ml_data, "merge_fear_greed",
        lambda df, fng_df: df.assign(fear_greed_value=float("nan")),
    )
    monkeypatch.setattr(regime_ml_data, "binance_symbol", lambda market: "BTCUSDT")  # Always return a string
    if symbol_found:
        monkeypatch.setattr(regime_ml_data, "get_binance_close", lambda *a, **k: pd.DataFrame(columns=["candle_time", "close"]))
        monkeypatch.setattr(regime_ml_data, "get_binance_funding_rate", lambda *a, **k: pd.DataFrame(columns=["funding_time", "funding_rate_value"]))
        monkeypatch.setattr(
            regime_ml_data, "merge_funding_rate",
            lambda df, funding_df: df.assign(funding_rate_value=float("nan")),
        )
    else:
        # Patch get_binance_close to raise the exception (the actual failure mode in production)
        def _raise_symbol_not_found(*args, **kwargs):
            raise BinanceSymbolNotFoundError("BTCUSDT")
        monkeypatch.setattr(regime_ml_data, "get_binance_close", _raise_symbol_not_found)
    monkeypatch.setattr(regime_ml_data, "compute_korea_premium_value", lambda df: pd.Series([float("nan")] * len(df), index=df.index))
    monkeypatch.setattr(regime_ml_data, "get_fed_funds_rate", lambda *a, **k: pd.DataFrame(columns=["date", "fed_funds_rate_value"]))
    monkeypatch.setattr(regime_ml_data, "get_us_yield_curve_spread", lambda *a, **k: pd.DataFrame(columns=["date", "treasury_yield_spread_value"]))
    monkeypatch.setattr(regime_ml_data, "get_kr_call_rate", lambda *a, **k: pd.DataFrame(columns=["date", "kr_call_rate_value"]))
    monkeypatch.setattr(regime_ml_data, "get_usdkrw_rate", lambda *a, **k: pd.DataFrame(columns=["date", "usdkrw_rate_value"]))
    monkeypatch.setattr(regime_ml_data, "get_sp500_index", lambda *a, **k: pd.DataFrame(columns=["date", "sp500_close_value"]))
    monkeypatch.setattr(regime_ml_data, "get_djia_index", lambda *a, **k: pd.DataFrame(columns=["date", "djia_close_value"]))
    monkeypatch.setattr(regime_ml_data, "get_nasdaq_index", lambda *a, **k: pd.DataFrame(columns=["date", "nasdaq_close_value"]))


def test_load_market_training_data_has_all_required_columns(monkeypatch):
    monkeypatch.setattr(regime_ml_data, "get_candles", lambda market, tf, s, e: _make_candle_df(20))
    _patch_common(monkeypatch)

    df = load_market_training_data("KRW-ETH", "minutes60", START, END)

    required = {
        "close", "high", "low", "volume", "trade_value",
        "btc_close", "usdt_close", "binance_close",
        "fear_greed_value", "funding_rate_value", "korea_premium_value",
        "fed_funds_rate_value", "treasury_yield_spread_value", "kr_call_rate_value", "usdkrw_rate_value",
        "sp500_close_value", "djia_close_value", "nasdaq_close_value",
    }
    assert required.issubset(set(df.columns))
    assert len(df) == 20


def test_load_market_training_data_sets_btc_close_equal_to_close_for_btc_market(monkeypatch):
    monkeypatch.setattr(regime_ml_data, "get_candles", lambda market, tf, s, e: _make_candle_df(10))
    _patch_common(monkeypatch)

    df = load_market_training_data("KRW-BTC", "minutes60", START, END)

    assert (df["btc_close"] == df["close"]).all()


def test_load_market_training_data_tolerates_missing_binance_symbol(monkeypatch):
    monkeypatch.setattr(regime_ml_data, "get_candles", lambda market, tf, s, e: _make_candle_df(10))
    _patch_common(monkeypatch, symbol_found=False)

    df = load_market_training_data("KRW-WEIRD", "minutes60", START, END)

    assert df["binance_close"].isna().all()
    assert df["funding_rate_value"].isna().all()


def test_load_market_training_data_raises_when_primary_candles_empty(monkeypatch):
    monkeypatch.setattr(regime_ml_data, "get_candles", lambda market, tf, s, e: pd.DataFrame(columns=_CANDLE_COLUMNS))
    _patch_common(monkeypatch)

    with pytest.raises(ValueError, match="캔들 데이터가 없습니다"):
        load_market_training_data("KRW-BTC", "minutes60", START, END)
