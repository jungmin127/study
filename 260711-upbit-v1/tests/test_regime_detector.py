from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine.regime_detector import (
    CATEGORY_REFERENCE_SCORES,
    _softmax_categorize,
    compute_regime_probs,
    compute_regime_probs_series,
    ewm_volatility,
    half_life_bars_for_timeframe,
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


def test_ewm_volatility_of_constant_returns_is_near_zero():
    """수익률이 일정하면 지수가중 표준편차는 0에 가까워야 한다(EWMA 절댓값평균이던
    구버전에서는 이 값이 0.01이 나왔지만, 삼각부등식으로 score가 [-1, 1]에 갇히는 버그의
    원인이었다 — 표준편차 기반으로 바뀐 지금은 변동성이 없는 시계열의 분산은 0이 맞다)."""
    returns = pd.Series([0.01] * 30)
    vol = ewm_volatility(returns, half_life_bars=5.0)
    assert vol == pytest.approx(0.0, abs=1e-9)


def test_ewm_volatility_matches_pandas_ewm_std():
    """ewm_volatility가 pandas의 지수가중 표준편차와 동일한 값을 내는지 직접 대조한다."""
    rng = np.random.default_rng(seed=42)
    returns = pd.Series(rng.normal(loc=0.0, scale=0.02, size=30))
    vol = ewm_volatility(returns, half_life_bars=5.0)
    expected = float(returns.ewm(halflife=5.0).std().iloc[-1])
    assert vol == pytest.approx(expected, rel=1e-9)


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


def test_compute_regime_probs_empty_df_returns_none():
    df = pd.DataFrame({"candle_time": [], "close": []})
    assert compute_regime_probs(df, half_life_bars=3.0) is None


def test_compute_regime_probs_missing_close_column_returns_none():
    df = pd.DataFrame({"candle_time": pd.date_range("2026-01-01", periods=5, freq="D")})
    assert compute_regime_probs(df, half_life_bars=3.0) is None


def test_compute_regime_probs_series_empty_df_returns_empty_list():
    df = pd.DataFrame({"candle_time": [], "close": []})
    assert compute_regime_probs_series(df, half_life_bars=3.0) == []


def test_compute_regime_probs_series_missing_close_column_returns_empty_list():
    df = pd.DataFrame({"candle_time": pd.date_range("2026-01-01", periods=5, freq="D")})
    assert compute_regime_probs_series(df, half_life_bars=3.0) == []


def test_compute_regime_probs_probabilities_sum_to_one():
    closes = [100.0 * (1.01**i) for i in range(60)]
    df = _make_price_df(closes)
    probs = compute_regime_probs(df, half_life_bars=3.0)
    assert probs is not None
    assert sum(probs.values()) == pytest.approx(1.0, abs=1e-9)


def _make_noisy_trend_df(
    base_daily_return: float, noise_scale: float, seed: int, n: int = 60
) -> pd.DataFrame:
    """일정한 추세(base_daily_return) 위에 정규분포 노이즈(noise_scale)를 더한
    비단조(non-monotonic) 수익률 시계열을 만든다. 시드 고정으로 결정론적."""
    rng = np.random.default_rng(seed=seed)
    noise = rng.normal(0.0, noise_scale, size=n)
    returns = [base_daily_return + noise[i] for i in range(n)]
    closes = [100.0]
    for r in returns:
        closes.append(closes[-1] * (1.0 + r))
    return _make_price_df(closes)


def test_compute_regime_probs_scale_invariant_across_volatility():
    """변동성이 다른 두 코인이 '위험조정 기준(추세/노이즈 비율) 동일한 강도'의
    노이즈 섞인 비단조 추세일 때 같은 카테고리가 우세해야 한다(변동성 정규화 검증).
    구버전 테스트는 둘 다 단조추세라 새 공식으로도 여전히 포화돼 아무것도 검증하지
    못하는 동어반복이었다 — 노이즈를 섞은 비단조 시계열로 다시 짰다."""
    # 추세/노이즈 비율(0.4)은 동일, 절대 변동성(noise_scale)은 4배 차이, 시드도 다르게
    low_vol_df = _make_noisy_trend_df(base_daily_return=0.005, noise_scale=0.002, seed=10)
    high_vol_df = _make_noisy_trend_df(base_daily_return=0.02, noise_scale=0.008, seed=99)
    probs_low = compute_regime_probs(low_vol_df, half_life_bars=3.0)
    probs_high = compute_regime_probs(high_vol_df, half_life_bars=3.0)
    assert probs_low is not None and probs_high is not None
    assert max(probs_low, key=probs_low.get) == max(probs_high, key=probs_high.get) == "급상승"


def test_compute_regime_probs_strong_consistent_uptrend_reaches_surge_up():
    """분산이 거의 0인 강한 일관된 상승 추세(매 봉 +5%)는 실제로 '급상승'을 최댓값으로
    내야 한다 — EWMA 절댓값평균 버그에서는 score가 [-1, 1]에 갇혀 급상승(대표값 +2.0)에
    영원히 도달할 수 없었다. 이 테스트가 그 버그가 실제로 고쳐졌음을 직접 증명한다."""
    closes = [100.0 * (1.05**i) for i in range(60)]
    df = _make_price_df(closes)
    probs = compute_regime_probs(df, half_life_bars=3.0)
    assert probs is not None
    assert max(probs, key=probs.get) == "급상승"


def test_compute_regime_probs_strong_consistent_downtrend_reaches_surge_down():
    """대칭 검증: 분산이 거의 0인 강한 일관된 하락 추세(매 봉 -5%)는 '급하락'을
    최댓값으로 내야 한다."""
    closes = [100.0 * (0.95**i) for i in range(60)]
    df = _make_price_df(closes)
    probs = compute_regime_probs(df, half_life_bars=3.0)
    assert probs is not None
    assert max(probs, key=probs.get) == "급하락"


def test_compute_regime_probs_noisy_strong_uptrend_reaches_surge_up():
    """위 두 테스트는 분산이 거의 0이라 _MIN_VOLATILITY_FLOOR에 걸리는 퇴화 케이스다
    (구공식 대비 신공식을 구분하는 증거로는 유효하지만, 실제 volatility 항이 쓰이는지는
    검증하지 못한다). 매 봉 +5% 근처에 ±0.5% 노이즈를 섞어 분산이 뚜렷이 0보다 큰
    비퇴화 케이스에서도 여전히 급상승이 최댓값이 되는지 확인한다(score ≈ 8.8)."""
    df = _make_noisy_trend_df(base_daily_return=0.05, noise_scale=0.005, seed=3, n=60)
    probs = compute_regime_probs(df, half_life_bars=3.0)
    assert probs is not None
    assert max(probs, key=probs.get) == "급상승"


def test_compute_regime_probs_noisy_strong_downtrend_reaches_surge_down():
    """대칭 검증: 매 봉 -5% 근처에 ±0.5% 노이즈를 섞은 비퇴화 하락 케이스에서도
    급하락이 최댓값이 되는지 확인한다."""
    df = _make_noisy_trend_df(base_daily_return=-0.05, noise_scale=0.005, seed=4, n=60)
    probs = compute_regime_probs(df, half_life_bars=3.0)
    assert probs is not None
    assert max(probs, key=probs.get) == "급하락"


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
    고정한다(인과성 회귀가드 — 미래 데이터가 새어 들어가면 이 테스트가 깨진다).

    등비수열(노이즈 없는 완전 매끄러운 시계열)을 쓰면 EWMA 표준편차가 거의 0
    (1e-17 수준)이 되어 _MIN_VOLATILITY_FLOOR(1e-6)에 항상 걸리고, score가 항상
    momentum / floor가 되어 volatility 항이 전혀 안 쓰인다 — 그 결과 volatility
    계산부에 미래 데이터 누수가 생겨도 이 테스트가 못 잡는 사각지대가 있었다
    (Task 3 재리뷰에서 발견). 노이즈 섞인 비단조 시계열(_make_noisy_trend_df)로
    바꿔 실제 표준편차 값이 쓰이는 상황에서 인과성을 검증한다."""
    df = _make_noisy_trend_df(base_daily_return=0.01, noise_scale=0.006, seed=7, n=60)
    half_life_bars = 3.0

    series = compute_regime_probs_series(df, half_life_bars)
    assert len(series) == len(df)

    for t in (20, 40, 59):
        pointwise = compute_regime_probs(df.iloc[: t + 1], half_life_bars)
        assert series[t] == pointwise
