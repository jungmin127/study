"""
tests/test_regime_ml_features.py

engine.regime_ml_features.build_feature_matrix()를 검증한다. LIVE_INDICATOR_FACTORY를
그대로 순회하되 OBV(스케일 불일치, docs/superpowers/specs/2026-08-27-regime-ml-
backlog-cleanup-design.md 참고)와 FEAR_GREED_CMC(캘린더 프록시로 작동해 성능을 깎아먹음,
2026-08-30 ablation)를 뺀다 — 반환 컬럼 집합이 그 레지스트리 키 전체(둘 제외) + regime
전용 5개 + market과 정확히 일치해야 한다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from engine.regime_ml_features import build_feature_matrix
from trading.live_indicators import LIVE_INDICATOR_FACTORY

_N = 150


def _make_full_df() -> pd.DataFrame:
    rng = np.random.default_rng(42)
    dates = pd.date_range("2024-01-01", periods=_N, freq="h", tz="UTC")
    close = 100.0 + np.cumsum(rng.normal(0, 1.0, _N))
    high = close + rng.uniform(0.1, 1.0, _N)
    low = close - rng.uniform(0.1, 1.0, _N)
    volume = rng.uniform(10, 100, _N)
    return pd.DataFrame({
        "candle_time": dates,
        "close": close, "high": high, "low": low,
        "volume": volume, "trade_value": volume * close,
        "btc_close": close * 1.1, "usdt_close": np.full(_N, 1350.0),
        "binance_close": close / 1350.0,
        "fear_greed_value": rng.uniform(0, 100, _N),
        "funding_rate_value": rng.uniform(-0.05, 0.05, _N),
        "korea_premium_value": rng.uniform(-2, 2, _N),
    })


def test_build_feature_matrix_has_one_column_per_registered_indicator_except_obv_plus_regime_features():
    df = _make_full_df()

    result = build_feature_matrix(df, market="KRW-BTC", half_life_bars=24.0)

    expected_columns = (
        (set(LIVE_INDICATOR_FACTORY.keys()) - {"OBV", "FEAR_GREED_CMC"})
        | {
            "RAW_SCORE", "VOLUME_CONFIRM", "VPIN_SCORE", "LEVEL_PROXIMITY", "REVERSAL_GATE",
            "VOLATILITY_PERCENTILE", "LIQUIDITY_PERCENTILE", "market",
            "HOUR_SIN", "HOUR_COS", "DOW_SIN", "DOW_COS", "DAY_OF_MONTH_SIN", "DAY_OF_MONTH_COS",
        }
    )
    assert set(result.columns) == expected_columns


def test_build_feature_matrix_excludes_obv_but_keeps_obv_roc():
    df = _make_full_df()

    result = build_feature_matrix(df, market="KRW-BTC", half_life_bars=24.0)

    assert "OBV" not in result.columns
    assert "OBV_ROC" in result.columns


def test_build_feature_matrix_excludes_fear_greed_cmc():
    df = _make_full_df()

    result = build_feature_matrix(df, market="KRW-BTC", half_life_bars=24.0)

    assert "FEAR_GREED_CMC" not in result.columns


def test_build_feature_matrix_preserves_row_count_and_index():
    df = _make_full_df()
    result = build_feature_matrix(df, market="KRW-ETH", half_life_bars=24.0)

    assert len(result) == len(df)
    assert list(result.index) == list(df.index)


def test_build_feature_matrix_sets_market_column_as_category():
    df = _make_full_df()
    result = build_feature_matrix(df, market="KRW-XRP", half_life_bars=24.0)

    assert (result["market"] == "KRW-XRP").all()
    assert str(result["market"].dtype) == "category"


def test_build_feature_matrix_percentile_features_start_nan_then_bounded_zero_to_one():
    df = _make_full_df()  # _N=150 > 백분위 min_periods(100)
    result = build_feature_matrix(df, market="KRW-BTC", half_life_bars=24.0)

    for column in ("VOLATILITY_PERCENTILE", "LIQUIDITY_PERCENTILE"):
        assert pd.isna(result[column].iloc[0])
        last_value = result[column].iloc[-1]
        assert not pd.isna(last_value)
        assert 0.0 <= last_value <= 1.0


def test_build_feature_matrix_calendar_features_match_kst_sin_cos_formula():
    df = _make_full_df()
    result = build_feature_matrix(df, market="KRW-BTC", half_life_bars=24.0)

    kst_time = df["candle_time"].dt.tz_convert("Asia/Seoul")
    expected_hour_sin = np.sin(2 * np.pi * kst_time.dt.hour / 24)
    expected_dow_cos = np.cos(2 * np.pi * kst_time.dt.dayofweek / 7)
    expected_day_cos = np.cos(2 * np.pi * (kst_time.dt.day - 1) / 31)

    pd.testing.assert_series_equal(
        result["HOUR_SIN"].reset_index(drop=True), expected_hour_sin.reset_index(drop=True), check_names=False
    )
    pd.testing.assert_series_equal(
        result["DOW_COS"].reset_index(drop=True), expected_dow_cos.reset_index(drop=True), check_names=False
    )
    pd.testing.assert_series_equal(
        result["DAY_OF_MONTH_COS"].reset_index(drop=True), expected_day_cos.reset_index(drop=True), check_names=False
    )


def test_build_feature_matrix_hour_sin_is_continuous_across_kst_midnight():
    """KST 23시->0시 전환처럼 raw hour 값은 23->0으로 불연속이지만, sin 인코딩은
    실제 1시간 차이만큼만 작게 움직여야 한다."""
    dates = pd.to_datetime(["2024-01-01 14:00", "2024-01-01 15:00"], utc=True)  # UTC 14/15시 -> KST 23시/(다음날)0시
    df = _make_full_df().iloc[:2].copy()
    df["candle_time"] = dates

    result = build_feature_matrix(df, market="KRW-BTC", half_life_bars=24.0)

    diff_across_midnight = abs(result["HOUR_SIN"].iloc[0] - result["HOUR_SIN"].iloc[1])
    assert diff_across_midnight < 0.3  # sin(2π*23/24)≈-0.259, sin(0)=0 -> 실제 차이≈0.259
