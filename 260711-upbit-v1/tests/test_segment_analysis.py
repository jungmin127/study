from datetime import datetime, timezone

import pandas as pd
import pytest

import engine.segment_analysis as segment_analysis_module
from engine.segment_analysis import _classify, _compute_volatility, run_segment_batch


def test_classify_large_cap_requires_high_trade_value_and_low_volatility():
    assert _classify(trade_value_pct=80.0, volatility_pct=30.0) == "large"


def test_classify_junk_requires_low_trade_value_and_high_volatility():
    assert _classify(trade_value_pct=10.0, volatility_pct=80.0) == "junk"


def test_classify_mid_for_everything_else():
    assert _classify(trade_value_pct=50.0, volatility_pct=50.0) == "mid"
    assert _classify(trade_value_pct=90.0, volatility_pct=90.0) == "mid"
    assert _classify(trade_value_pct=10.0, volatility_pct=10.0) == "mid"


def test_classify_falls_back_to_mid_when_percentile_missing():
    assert _classify(trade_value_pct=None, volatility_pct=80.0) == "mid"
    assert _classify(trade_value_pct=80.0, volatility_pct=None) == "mid"


def test_percentile_rank_orders_values_from_zero_to_hundred():
    result = segment_analysis_module._percentile_rank([10.0, 30.0, 20.0])
    assert result[1] == 100.0
    assert result[0] < result[2] < result[1]


def test_percentile_rank_keeps_none_as_none():
    result = segment_analysis_module._percentile_rank([10.0, None, 20.0])
    assert result[1] is None
    assert result[0] is not None and result[2] is not None


def test_compute_volatility_matches_pandas_pct_change_std(monkeypatch):
    closes = [100.0, 102.0, 101.0, 105.0, 103.0]
    df = pd.DataFrame({
        "candle_time": pd.date_range("2026-07-01", periods=len(closes), freq="D", tz="UTC"),
        "close": closes,
    })

    def _fake_get_candles(market, timeframe, start, end):
        assert market == "KRW-BTC"
        assert timeframe == "days"
        return df

    monkeypatch.setattr(segment_analysis_module, "get_candles", _fake_get_candles)

    expected = pd.Series(closes).pct_change().dropna().std()
    assert _compute_volatility("KRW-BTC") == pytest.approx(expected)


def test_compute_volatility_returns_none_when_not_enough_candles(monkeypatch):
    df = pd.DataFrame({
        "candle_time": pd.date_range("2026-07-01", periods=1, freq="D", tz="UTC"),
        "close": [100.0],
    })
    monkeypatch.setattr(segment_analysis_module, "get_candles", lambda *a, **k: df)

    assert _compute_volatility("KRW-NEW") is None


def test_run_segment_batch_classifies_and_saves_all_markets(monkeypatch):
    markets = [
        {"market": f"KRW-M{i}", "korean_name": f"코인{i}", "trade_price_24h": i * 1_000_000_000.0}
        for i in range(1, 11)
    ]
    volatilities = {f"KRW-M{i}": (11 - i) * 0.01 for i in range(1, 11)}
    cautions = {f"KRW-M{i}": (i == 1) for i in range(1, 11)}

    monkeypatch.setattr(segment_analysis_module, "get_krw_markets_with_ticker", lambda: markets)
    monkeypatch.setattr(segment_analysis_module, "get_market_cautions", lambda: cautions)
    monkeypatch.setattr(
        segment_analysis_module, "_compute_volatility", lambda market: volatilities[market]
    )

    saved: dict = {}
    monkeypatch.setattr(
        segment_analysis_module,
        "save_segment_classification",
        lambda rows: saved.setdefault("rows", rows),
    )

    count = run_segment_batch()

    assert count == 10
    rows_by_market = {r["market"]: r for r in saved["rows"]}
    assert rows_by_market["KRW-M10"]["segment"] == "large"
    assert rows_by_market["KRW-M1"]["segment"] == "junk"
    assert rows_by_market["KRW-M1"]["is_caution"] is True
    assert rows_by_market["KRW-M10"]["is_caution"] is False
    assert rows_by_market["KRW-M5"]["segment"] == "mid"
    assert all(isinstance(r["computed_at"], str) for r in saved["rows"])
