"""
tests/test_regime_ml_cross_sectional.py

engine.regime_ml_cross_sectional.compute_cross_sectional_features()를 검증한다.
"""
from __future__ import annotations

import pytest
import pandas as pd

from engine.regime_ml_cross_sectional import compute_cross_sectional_features


def test_compute_cross_sectional_features_beta_neutral_subtracts_btc_return():
    index = pd.date_range("2024-01-01", periods=3, freq="h", tz="UTC")
    market_returns = {
        "KRW-BTC": pd.Series([0.01, 0.02, -0.01], index=index),
        "KRW-ETH": pd.Series([0.03, 0.02, -0.05], index=index),
    }

    result = compute_cross_sectional_features(market_returns, btc_market="KRW-BTC")

    eth_beta_neutral = result["KRW-ETH"]["BETA_NEUTRAL_RETURN"]
    assert eth_beta_neutral.iloc[0] == pytest.approx(0.03 - 0.01)
    assert eth_beta_neutral.iloc[1] == pytest.approx(0.02 - 0.02)
    assert eth_beta_neutral.iloc[2] == pytest.approx(-0.05 - (-0.01))

    btc_beta_neutral = result["KRW-BTC"]["BETA_NEUTRAL_RETURN"]
    assert (btc_beta_neutral == 0.0).all()  # BTC 자신은 항상 0


def test_compute_cross_sectional_features_rank_is_percentile_across_markets():
    index = pd.date_range("2024-01-01", periods=1, freq="h", tz="UTC")
    market_returns = {
        "KRW-BTC": pd.Series([0.01], index=index),
        "KRW-ETH": pd.Series([0.05], index=index),  # 3개 중 1등(최고 수익률)
        "KRW-XRP": pd.Series([-0.02], index=index),  # 3개 중 3등(최저)
    }

    result = compute_cross_sectional_features(market_returns, btc_market="KRW-BTC")

    assert result["KRW-ETH"]["CROSS_SECTIONAL_RANK"].iloc[0] == pytest.approx(1.0)
    assert result["KRW-XRP"]["CROSS_SECTIONAL_RANK"].iloc[0] == pytest.approx(1 / 3)
    assert result["KRW-BTC"]["CROSS_SECTIONAL_RANK"].iloc[0] == pytest.approx(2 / 3)


def test_compute_cross_sectional_features_handles_misaligned_timestamps_with_nan():
    """마켓마다 candle_time이 완전히 같지 않을 수 있다(결측 캔들 등) — outer join
    후 없는 시점은 NaN으로 남아야 하고, 에러가 나면 안 된다."""
    index_a = pd.date_range("2024-01-01", periods=3, freq="h", tz="UTC")
    index_b = pd.date_range("2024-01-01 01:00", periods=3, freq="h", tz="UTC")  # 1시간 밀림
    market_returns = {
        "KRW-BTC": pd.Series([0.01, 0.02, -0.01], index=index_a),
        "KRW-ETH": pd.Series([0.03, 0.02, -0.05], index=index_b),
    }

    result = compute_cross_sectional_features(market_returns, btc_market="KRW-BTC")

    assert pd.isna(result["KRW-ETH"]["BETA_NEUTRAL_RETURN"].loc[index_a[0]])
    assert pd.isna(result["KRW-BTC"]["BETA_NEUTRAL_RETURN"].loc[index_b[-1]])


def test_compute_cross_sectional_features_returns_one_frame_per_input_market():
    index = pd.date_range("2024-01-01", periods=2, freq="h", tz="UTC")
    market_returns = {
        "KRW-BTC": pd.Series([0.01, 0.02], index=index),
        "KRW-ETH": pd.Series([0.03, 0.02], index=index),
        "KRW-XRP": pd.Series([-0.01, 0.00], index=index),
    }

    result = compute_cross_sectional_features(market_returns, btc_market="KRW-BTC")

    assert set(result.keys()) == set(market_returns.keys())
    for df in result.values():
        assert list(df.columns) == ["BETA_NEUTRAL_RETURN", "CROSS_SECTIONAL_RANK"]
        assert len(df) == 2
