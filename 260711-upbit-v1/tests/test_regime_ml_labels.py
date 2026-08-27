"""
tests/test_regime_ml_labels.py

engine.regime_ml_labels의 레이블 생성 함수를 검증한다. compute_normalized_realized_series는
backend/regime_service.py:evaluate_market()의 정규화 실현수익률 루프(100~119행)와 동일한
값을 내야 한다(같은 잣대로 규칙기반과 비교하기 위함).
"""
from __future__ import annotations

import pandas as pd
import pytest

from engine.regime_detector import ewm_volatility
from engine.regime_ml_labels import (
    CATEGORY_LABELS,
    bucket_to_category,
    category_representative_scores,
    compute_normalized_realized_series,
    compute_quantile_boundaries,
)


def _make_close_df(closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"close": closes})


def test_compute_normalized_realized_series_matches_evaluate_market_formula():
    # 상승폭이 점점 커지는 시계열 — 뒤로 갈수록 정규화 실현수익률이 커져야 함
    closes = [100.0 * (1.001**i) for i in range(80)]
    df = _make_close_df(closes)
    half_life_bars = 24.0
    n_bars = 60

    series = compute_normalized_realized_series(df, half_life_bars, n_bars)

    assert len(series) == len(df)
    # 마지막 n_bars 구간은 미래 데이터가 없어 NaN
    assert series.iloc[-n_bars:].isna().all()
    # 워밍업 이후 앞부분은 값이 존재
    assert series.iloc[0:len(df) - n_bars].notna().all()

    # 수치 검증: 특정 인덱스 t에서 독립적으로 계산한 예상값과 비교
    t = 10
    returns = df["close"].pct_change(fill_method=None)
    future_returns = returns.iloc[t + 1 : t + 1 + n_bars]
    realized_volatility = ewm_volatility(future_returns, half_life_bars)
    expected = future_returns.mean() / realized_volatility
    assert series.iloc[t] == pytest.approx(expected)


def test_compute_normalized_realized_series_returns_all_nan_when_too_short():
    df = _make_close_df([100.0, 101.0, 102.0])
    series = compute_normalized_realized_series(df, half_life_bars=24.0, n_bars=60)
    assert series.isna().all()
    assert len(series) == 3


def test_compute_quantile_boundaries_are_ascending_and_within_range():
    values = pd.Series([float(i) for i in range(1, 101)])  # 1..100
    boundaries = compute_quantile_boundaries(values, quantiles=(0.02, 0.16, 0.84, 0.98))

    assert len(boundaries) == 4
    assert boundaries == sorted(boundaries)
    assert values.min() <= boundaries[0]
    assert boundaries[-1] <= values.max()


def test_compute_quantile_boundaries_ignores_nan():
    values = pd.Series([1.0, 2.0, float("nan"), 3.0, 4.0, float("nan"), 5.0])
    quantiles_tuple = (0.25, 0.4, 0.6, 0.75)
    boundaries = compute_quantile_boundaries(values, quantiles=quantiles_tuple)

    # NaN이 섞이지 않음
    assert all(b == b for b in boundaries)

    # NaN을 제외한 값으로 예상값을 직접 계산하여 검증
    clean = values.dropna()
    expected = [float(clean.quantile(q)) for q in quantiles_tuple]
    for actual, exp in zip(boundaries, expected):
        assert actual == pytest.approx(exp)


def test_compute_quantile_boundaries_raises_when_all_nan():
    values = pd.Series([float("nan"), float("nan")])
    with pytest.raises(ValueError, match="표본이 없습니다"):
        compute_quantile_boundaries(values)


def test_bucket_to_category_assigns_correct_label():
    boundaries = [-10.0, -1.0, 1.0, 10.0]
    assert bucket_to_category(-20.0, boundaries) == "급하락"
    assert bucket_to_category(-10.0, boundaries) == "완만하락"  # 경계값은 다음 구간(>=)
    assert bucket_to_category(0.0, boundaries) == "횡보"
    assert bucket_to_category(5.0, boundaries) == "완만상승"
    assert bucket_to_category(100.0, boundaries) == "급상승"


def test_category_representative_scores_uses_median_of_bucket():
    # 급하락 구간에 -20, -15 두 값 -> 중앙값 -17.5
    values = pd.Series([-20.0, -15.0, 0.0, 0.0, 5.0, 5.0, 20.0])
    boundaries = [-10.0, -1.0, 1.0, 10.0]

    scores = category_representative_scores(values, boundaries)

    assert set(scores.keys()) == set(CATEGORY_LABELS)
    assert scores["급하락"] == pytest.approx(-17.5)
    assert scores["횡보"] == pytest.approx(0.0)


def test_category_representative_scores_falls_back_when_bucket_empty():
    # "완만하락" 구간(-10<=v<-1)에 값이 하나도 없음
    values = pd.Series([-20.0, 0.0, 20.0])
    boundaries = [-10.0, -1.0, 1.0, 10.0]

    scores = category_representative_scores(values, boundaries)

    assert scores["완만하락"] == pytest.approx((boundaries[0] + boundaries[1]) / 2)
