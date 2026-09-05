"""
tests/test_regime_ml_hmm.py

engine.regime_ml_hmm의 build_hmm_observations/fit_hmm/score_hmm_state_probabilities를
검증한다. HMM은 파라미터를 학습하는 모델이라(롤링 피처와 다름) fit과 score를
분리해서 제공한다 — walk-forward 무결성을 위해 train 구간에서만 fit해야 한다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from engine.regime_ml_hmm import (
    HMM_STATE_COLUMNS,
    N_STATES,
    build_hmm_observations,
    compute_dominant_state,
    fit_hmm,
    score_hmm_state_probabilities,
)

_HALF_LIFE_BARS = 24.0


def _make_close_df(n: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100.0 + np.cumsum(rng.normal(0, 1.0, n))
    return pd.DataFrame({"close": close})


def test_build_hmm_observations_has_returns_and_volatility_columns():
    df = _make_close_df(200, seed=1)
    result = build_hmm_observations(df, _HALF_LIFE_BARS)

    assert list(result.columns) == ["returns", "volatility"]
    assert len(result) == len(df)
    assert pd.isna(result["returns"].iloc[0])


def test_fit_hmm_and_score_state_probabilities_sum_to_one():
    df = _make_close_df(500, seed=2)
    observations = build_hmm_observations(df, _HALF_LIFE_BARS)
    model = fit_hmm(observations.dropna(), n_states=N_STATES, random_state=42)

    result = score_hmm_state_probabilities(model, observations)

    assert list(result.columns) == HMM_STATE_COLUMNS
    assert len(result) == len(observations)
    assert pd.isna(result.iloc[0]).all()
    valid_rows = result.dropna()
    assert len(valid_rows) > 0
    assert np.allclose(valid_rows.sum(axis=1), 1.0, atol=1e-6)


def test_score_hmm_state_probabilities_does_not_refit_model():
    df = _make_close_df(500, seed=3)
    observations = build_hmm_observations(df, _HALF_LIFE_BARS)
    model = fit_hmm(observations.dropna(), n_states=N_STATES, random_state=42)
    means_before = model.means_.copy()

    score_hmm_state_probabilities(model, observations)

    assert np.array_equal(means_before, model.means_)


def test_score_hmm_state_probabilities_preserves_index():
    df = _make_close_df(300, seed=4)
    observations = build_hmm_observations(df, _HALF_LIFE_BARS)
    model = fit_hmm(observations.dropna(), n_states=N_STATES, random_state=42)

    result = score_hmm_state_probabilities(model, observations)

    assert list(result.index) == list(observations.index)


def test_compute_dominant_state_matches_df_length_with_warmup_nan():
    df = _make_close_df(500, seed=5)
    result = compute_dominant_state(df, _HALF_LIFE_BARS)

    assert len(result) == len(df)
    assert pd.isna(result.iloc[0])


def test_compute_dominant_state_values_are_valid_state_indices():
    df = _make_close_df(500, seed=6)
    result = compute_dominant_state(df, _HALF_LIFE_BARS, n_states=N_STATES)

    valid = result.dropna()
    assert len(valid) > 0
    assert set(valid.unique()).issubset({float(i) for i in range(N_STATES)})
    # state starvation(모든 바가 단일 상태로 붕괴하는 GaussianHMM/EM 실패 모드) 감지:
    # 위 assert는 전부 하나의 상태로 collapse해도 통과하므로, 상태가 실제로 2개
    # 이상 나타나는지 별도로 확인해야 한다.
    assert len(valid.unique()) > 1


def test_compute_dominant_state_is_deterministic_with_same_random_state():
    df = _make_close_df(500, seed=7)
    first = compute_dominant_state(df, _HALF_LIFE_BARS, random_state=42)
    second = compute_dominant_state(df, _HALF_LIFE_BARS, random_state=42)

    pd.testing.assert_series_equal(first, second)
