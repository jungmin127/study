from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

import trading.signal_engine as signal_engine


def test_fetch_candles_with_warmup_computes_start_from_required_bars_plus_buffer(monkeypatch):
    captured = {}

    def fake_get_candles(market, timeframe, start, end):
        captured["market"] = market
        captured["timeframe"] = timeframe
        captured["start"] = start
        captured["end"] = end
        return pd.DataFrame(columns=["candle_time", "close"])

    monkeypatch.setattr(signal_engine, "get_candles", fake_get_candles)
    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)

    signal_engine._fetch_candles_with_warmup("KRW-BTC", "minutes60", 20, now)

    assert captured["market"] == "KRW-BTC"
    assert captured["timeframe"] == "minutes60"
    assert captured["end"] == now
    assert captured["start"] == now - timedelta(hours=25)  # (20+5)*60min


def test_merge_aux_markets_merges_btc_close_with_gap_fill(monkeypatch):
    df = pd.DataFrame({
        "candle_time": pd.date_range("2026-01-01", periods=3, freq="h", tz="UTC"),
        "close": [100.0, 101.0, 102.0],
    })

    def fake_get_candles(market, timeframe, start, end):
        assert market == "KRW-BTC"
        return pd.DataFrame({
            "candle_time": [df["candle_time"].iloc[0], df["candle_time"].iloc[2]],  # 가운데 봉 결측
            "close": [50000.0, 50200.0],
        })

    monkeypatch.setattr(signal_engine, "get_candles", fake_get_candles)
    now = datetime(2026, 1, 1, 3, tzinfo=timezone.utc)

    result = signal_engine._merge_aux_markets(df, {"KRW-BTC"}, "KRW-ETH", "minutes60", 10, now)

    assert list(result["btc_close"]) == [50000.0, 50000.0, 50200.0]  # 가운데는 ffill로 채움


def test_merge_aux_markets_uses_own_close_when_aux_market_is_target_market():
    df = pd.DataFrame({
        "candle_time": pd.date_range("2026-01-01", periods=2, freq="h", tz="UTC"),
        "close": [100.0, 101.0],
    })
    result = signal_engine._merge_aux_markets(
        df, {"KRW-BTC"}, "KRW-BTC", "minutes60", 10, datetime.now(timezone.utc),
    )
    assert list(result["btc_close"]) == [100.0, 101.0]


def test_populate_b_group_columns_fills_fear_greed_only_on_last_row(monkeypatch):
    df = pd.DataFrame({
        "candle_time": pd.date_range("2026-01-01", periods=3, freq="h", tz="UTC"),
        "close": [1.0, 2.0, 3.0],
    })
    monkeypatch.setattr(signal_engine, "fetch_live_fear_greed_value", lambda now=None: 42.0)

    result = signal_engine._populate_b_group_columns(
        df, "KRW-BTC", "minutes60", {"FEAR_GREED_CMC"}, datetime.now(timezone.utc),
    )

    assert result["fear_greed_value"].iloc[-1] == 42.0
    assert result["fear_greed_value"].iloc[:-1].isna().all()


def test_populate_b_group_columns_leaves_nan_when_fetch_fails(monkeypatch):
    df = pd.DataFrame({
        "candle_time": pd.date_range("2026-01-01", periods=2, freq="h", tz="UTC"),
        "close": [1.0, 2.0],
    })
    monkeypatch.setattr(signal_engine, "fetch_live_funding_rate_value", lambda market, now=None: None)

    result = signal_engine._populate_b_group_columns(
        df, "KRW-ETH", "minutes60", {"FUNDING_RATE"}, datetime.now(timezone.utc),
    )

    assert result["funding_rate_value"].isna().all()


def test_populate_b_group_columns_computes_korea_premium_from_binance_and_usdt_close(monkeypatch):
    df = pd.DataFrame({
        "candle_time": pd.date_range("2026-01-01", periods=2, freq="h", tz="UTC"),
        "close": [100_000_000.0, 101_000_000.0],
        "usdt_close": [1400.0, 1405.0],
    })
    monkeypatch.setattr(
        signal_engine, "fetch_live_binance_close", lambda market, timeframe, now=None: 70000.0,
    )

    result = signal_engine._populate_b_group_columns(
        df, "KRW-BTC", "minutes60", {"KOREA_PREMIUM"}, datetime.now(timezone.utc),
    )

    expected = (101_000_000.0 / (70000.0 * 1405.0) - 1) * 100
    assert result["korea_premium_value"].iloc[-1] == pytest.approx(expected)
    assert pd.isna(result["korea_premium_value"].iloc[0])


from engine.condition_tree import indicator_key
from tests.signal_fixtures import make_oscillating_df
from tests.trading_db_fixtures import insert_live_strategy
from trading.live_indicators import create_rsi
from trading.position_manager import open_position

import trading.db as db


def _fresh_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "trading.db")
    return db


def test_compute_indicator_values_computes_last_value_per_indicator():
    df = make_oscillating_df()
    blocks = [{"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30}]

    values = signal_engine._compute_indicator_values(df, blocks)

    key = indicator_key("RSI", {"period": 14})
    assert key in values
    assert values[key] == pytest.approx(create_rsi(df, period=14).iloc[-1])


def test_compute_indicator_values_raises_on_unknown_indicator():
    df = make_oscillating_df()
    blocks = [{"indicator": "NOT_A_REAL_INDICATOR", "params": {}, "operator": "<", "threshold": 1}]
    with pytest.raises(ValueError):
        signal_engine._compute_indicator_values(df, blocks)


def test_compute_indicator_values_dedupes_same_indicator_key():
    df = make_oscillating_df()
    blocks = [
        {"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30},
        {"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 70},
    ]
    values = signal_engine._compute_indicator_values(df, blocks)
    assert len(values) == 1


def test_position_context_returns_none_none_when_no_open_position(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm)

    result = signal_engine._position_context(
        strategy_id, 100.0, datetime.now(timezone.utc), "minutes60",
    )

    assert result == (None, None)


def test_position_context_computes_return_pct_and_holding_bars(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm)
    open_position(strategy_id, "KRW-BTC", 100.0, 1.0)  # entry_time = DB의 datetime('now')

    latest_candle_time = datetime.now(timezone.utc) + timedelta(hours=3)
    return_pct, holding_bars = signal_engine._position_context(
        strategy_id, 110.0, latest_candle_time, "minutes60",
    )

    assert return_pct == pytest.approx(10.0)
    assert holding_bars == 3  # 3시간 경과를 60분봉으로 나누면 3 (180분 / 60분 = 3)
