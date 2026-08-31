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
        "usdkrw_rate_value": 1300.0 + np.cumsum(rng.normal(0, 1.0, _N)),
    })


def test_build_feature_matrix_has_one_column_per_registered_indicator_except_obv_plus_regime_features():
    df = _make_full_df()

    result = build_feature_matrix(df, market="KRW-BTC", half_life_bars=24.0)

    expected_columns = (
        (set(LIVE_INDICATOR_FACTORY.keys()) - {"OBV", "FEAR_GREED_CMC"})
        | {
            "RAW_SCORE", "VOLUME_CONFIRM", "VPIN_SCORE", "LEVEL_PROXIMITY", "REVERSAL_GATE",
            "VOLATILITY_PERCENTILE", "LIQUIDITY_PERCENTILE", "market",
            "USDKRW_RETURN", "USDKRW_VOLATILITY", "UPBIT_FX_SPREAD",
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


def test_build_feature_matrix_usdkrw_return_matches_pct_change():
    df = _make_full_df()
    result = build_feature_matrix(df, market="KRW-BTC", half_life_bars=24.0)

    expected = df["usdkrw_rate_value"].pct_change(fill_method=None)
    pd.testing.assert_series_equal(
        result["USDKRW_RETURN"].reset_index(drop=True), expected.reset_index(drop=True), check_names=False
    )


def test_build_feature_matrix_upbit_fx_spread_is_zero_when_rates_match():
    df = _make_full_df()
    df["usdkrw_rate_value"] = df["usdt_close"]  # 업비트 암묵환율과 공식환율이 완전히 같다고 가정

    result = build_feature_matrix(df, market="KRW-BTC", half_life_bars=24.0)

    assert (result["UPBIT_FX_SPREAD"] == 0.0).all()


def test_build_feature_matrix_usdkrw_volatility_is_nonnegative_after_warmup():
    df = _make_full_df()
    result = build_feature_matrix(df, market="KRW-BTC", half_life_bars=24.0)

    assert (result["USDKRW_VOLATILITY"].iloc[2:] >= 0).all()
