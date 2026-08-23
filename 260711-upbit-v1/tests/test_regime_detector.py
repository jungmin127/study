from __future__ import annotations

import pandas as pd
import pytest

from engine.regime_detector import CATEGORY_REFERENCE_SCORES, _softmax_categorize, half_life_bars_for_timeframe
from engine.regime_detector import (
    compute_regime_probs,
    compute_regime_probs_series,
    ewm_volatility,
)


def test_softmax_categorize_sums_to_one():
    probs = _softmax_categorize(0.0)
    assert sum(probs.values()) == pytest.approx(1.0, abs=1e-9)


def test_softmax_categorize_returns_all_five_categories():
    probs = _softmax_categorize(1.0)
    assert set(probs.keys()) == set(CATEGORY_REFERENCE_SCORES.keys())


def test_softmax_categorize_extreme_positive_score_favors_surge_up():
    probs = _softmax_categorize(10.0)
    assert max(probs, key=probs.get) == "급상승"


def test_softmax_categorize_extreme_negative_score_favors_surge_down():
    probs = _softmax_categorize(-10.0)
    assert max(probs, key=probs.get) == "급하락"


def test_softmax_categorize_zero_score_favors_sideways():
    probs = _softmax_categorize(0.0)
    assert max(probs, key=probs.get) == "횡보"


def test_softmax_categorize_all_probabilities_nonnegative():
    probs = _softmax_categorize(-3.5)
    assert all(p >= 0.0 for p in probs.values())


def test_half_life_bars_for_timeframe_days_is_one():
    assert half_life_bars_for_timeframe("days") == pytest.approx(1.0)


def test_half_life_bars_for_timeframe_minutes60_is_24():
    assert half_life_bars_for_timeframe("minutes60") == pytest.approx(24.0)


def test_half_life_bars_for_timeframe_minutes15_is_96():
    assert half_life_bars_for_timeframe("minutes15") == pytest.approx(96.0)


def _make_price_df(closes: list[float]) -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=len(closes), freq="D")
    return pd.DataFrame({"candle_time": dates, "close": closes})


def test_ewm_volatility_of_constant_returns_equals_abs_value():
    returns = pd.Series([0.01] * 30)
    vol = ewm_volatility(returns, half_life_bars=5.0)
    assert vol == pytest.approx(0.01, rel=1e-6)


def test_compute_regime_probs_none_when_insufficient_warmup():
    df = _make_price_df([100.0, 101.0, 102.0])
    assert compute_regime_probs(df, half_life_bars=24.0) is None


def test_compute_regime_probs_monotonic_uptrend_favors_up_categories():
    closes = [100.0 * (1.02**i) for i in range(60)]
    df = _make_price_df(closes)
    probs = compute_regime_probs(df, half_life_bars=3.0)
    assert probs is not None
    assert max(probs, key=probs.get) in ("완만상승", "급상승")


def test_compute_regime_probs_monotonic_downtrend_favors_down_categories():
    closes = [100.0 * (0.98**i) for i in range(60)]
    df = _make_price_df(closes)
    probs = compute_regime_probs(df, half_life_bars=3.0)
    assert probs is not None
    assert max(probs, key=probs.get) in ("완만하락", "급하락")


def test_compute_regime_probs_flat_prices_favor_sideways():
    closes = [100.0] * 60
    df = _make_price_df(closes)
    probs = compute_regime_probs(df, half_life_bars=3.0)
    assert probs is not None
    assert max(probs, key=probs.get) == "횡보"


def test_compute_regime_probs_probabilities_sum_to_one():
    closes = [100.0 * (1.01**i) for i in range(60)]
    df = _make_price_df(closes)
    probs = compute_regime_probs(df, half_life_bars=3.0)
    assert probs is not None
    assert sum(probs.values()) == pytest.approx(1.0, abs=1e-9)


def test_compute_regime_probs_scale_invariant_across_volatility():
    """변동성이 다른 두 코인이 '위험조정 기준 동일한 강도'의 순수 추세일 때
    같은 카테고리가 우세해야 한다(변동성 정규화 검증)."""
    low_vol_closes = [100.0 * (1.005**i) for i in range(60)]
    high_vol_closes = [100.0 * (1.02**i) for i in range(60)]
    probs_low = compute_regime_probs(_make_price_df(low_vol_closes), half_life_bars=3.0)
    probs_high = compute_regime_probs(_make_price_df(high_vol_closes), half_life_bars=3.0)
    assert probs_low is not None and probs_high is not None
    assert max(probs_low, key=probs_low.get) == max(probs_high, key=probs_high.get)


def test_compute_regime_probs_shorter_half_life_reacts_faster_to_recent_reversal():
    """앞 40봉 하락 후 뒤 15봉 급격히 상승 반전 — half-life가 짧을수록
    반전 이후 상승쪽 확률 합이 더 커야 한다."""
    down_leg = [100.0 * (0.98**i) for i in range(40)]
    up_leg = [down_leg[-1] * (1.03**i) for i in range(1, 16)]
    df = _make_price_df(down_leg + up_leg)

    probs_fast = compute_regime_probs(df, half_life_bars=2.0)
    probs_slow = compute_regime_probs(df, half_life_bars=8.0)
    assert probs_fast is not None and probs_slow is not None

    fast_up = probs_fast["완만상승"] + probs_fast["급상승"]
    slow_up = probs_slow["완만상승"] + probs_slow["급상승"]
    assert fast_up > slow_up


def test_compute_regime_probs_series_matches_pointwise_calls():
    """벡터화 버전이 매 시점 truncated df로 개별 호출한 것과 동일한 결과를 내는지
    고정한다(인과성 회귀가드 — 미래 데이터가 새어 들어가면 이 테스트가 깨진다)."""
    closes = [100.0 * (1.01**i) for i in range(60)]
    df = _make_price_df(closes)
    half_life_bars = 3.0

    series = compute_regime_probs_series(df, half_life_bars)
    assert len(series) == len(df)

    for t in (20, 40, 59):
        pointwise = compute_regime_probs(df.iloc[: t + 1], half_life_bars)
        assert series[t] == pointwise
