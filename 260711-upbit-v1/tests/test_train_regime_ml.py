"""
tests/test_train_regime_ml.py

scripts.train_regime_ml.run_training()의 end-to-end 스모크 테스트. 실제 네트워크
호출 없이(scripts.regime_ml_data.load_market_training_data를 monkeypatch) 합성
데이터로 전체 파이프라인(데이터 로드 -> 피처 -> fold 루프 -> LightGBM 학습 -> 리포트
-> 모델 저장)이 에러 없이 완주하는지만 검증한다. 개별 단계(레이블/분할/피처/로더)의
세부 동작은 각자의 유닛테스트(test_regime_ml_labels.py 등)가 이미 검증한다.
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

import scripts.train_regime_ml as train_regime_ml
from scripts.train_regime_ml import run_training

START = datetime(2024, 1, 1, tzinfo=timezone.utc)
_N = 24 * 40  # minutes60, 40일치


def _make_synthetic_market_df(market: str, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range(START, periods=_N, freq="h", tz="UTC")
    close = 100.0 + np.cumsum(rng.normal(0, 1.0, _N))
    high = close + rng.uniform(0.1, 1.0, _N)
    low = close - rng.uniform(0.1, 1.0, _N)
    volume = rng.uniform(10, 100, _N)
    return pd.DataFrame({
        "candle_time": dates,
        "open": close, "high": high, "low": low, "close": close,
        "volume": volume, "trade_value": volume * close,
        "btc_close": close * 1.1, "usdt_close": np.full(_N, 1350.0),
        "binance_close": close / 1350.0,
        "fear_greed_value": rng.uniform(0, 100, _N),
        "funding_rate_value": rng.uniform(-0.05, 0.05, _N),
        "korea_premium_value": rng.uniform(-2, 2, _N),
    })


def test_run_training_completes_and_saves_model(tmp_path, monkeypatch):
    seeds = {"KRW-BTC": 1, "KRW-ETH": 2, "KRW-XRP": 3}
    monkeypatch.setattr(
        train_regime_ml, "load_market_training_data",
        lambda market, timeframe, start, end: _make_synthetic_market_df(market, seeds[market]),
    )

    reports = run_training(
        markets=list(seeds.keys()),
        timeframe="minutes60",
        start=START,
        end=START + pd.Timedelta(hours=_N),
        n_folds=2,
        min_train_samples=50,
        model_output_dir=tmp_path,
    )

    assert len(reports) >= 1
    for report in reports:
        assert report["n_test"] > 0
        assert set(report["confusion"].keys()) <= {
            "급하락", "완만하락", "횡보", "완만상승", "급상승",
        }
        assert 1 <= len(report["top_features"]) <= 15
        assert all(isinstance(name, str) and isinstance(score, float) for name, score in report["top_features"])

    saved_models = list(tmp_path.glob("*.txt"))
    assert len(saved_models) == 1


def test_run_training_skips_folds_below_min_train_samples(tmp_path, monkeypatch):
    seeds = {"KRW-BTC": 1}
    monkeypatch.setattr(
        train_regime_ml, "load_market_training_data",
        lambda market, timeframe, start, end: _make_synthetic_market_df(market, seeds[market]),
    )

    reports = run_training(
        markets=list(seeds.keys()),
        timeframe="minutes60",
        start=START,
        end=START + pd.Timedelta(hours=_N),
        n_folds=2,
        min_train_samples=10**9,  # 항상 표본 부족
        model_output_dir=tmp_path,
    )

    assert reports == []
    assert list(tmp_path.glob("*.txt")) == []


def test_quantile_boundaries_computed_from_train_window_only(tmp_path, monkeypatch):
    """이 계획 전체의 핵심 불변조건: fold별 카테고리 경계(quantile)는 그 fold의
    TRAIN 구간 데이터로만 계산돼야 하고, test 구간이나 train+test를 합친 데이터로
    계산되면 안 된다(look-ahead 누수). compute_quantile_boundaries를 monkeypatch해
    실제로 넘어온 Series를 fold별로 기록한 뒤, 독립적으로(run_training 내부 로직을
    그대로 재현해) 계산한 "train 구간만"의 기대 레이블 집합과 정확히 일치하고,
    "train+test 합산" 표본 수보다는 반드시 적다는 것을 검증한다. fold 루프가 실수로
    combined 데이터를 넘기도록 바뀌면 이 테스트가 실패한다."""
    seeds = {"KRW-BTC": 1, "KRW-ETH": 2, "KRW-XRP": 3}
    monkeypatch.setattr(
        train_regime_ml, "load_market_training_data",
        lambda market, timeframe, start, end: _make_synthetic_market_df(market, seeds[market]),
    )

    recorded_calls: list[pd.Series] = []
    original_compute_quantile_boundaries = train_regime_ml.compute_quantile_boundaries

    def _recording_compute_quantile_boundaries(series):
        recorded_calls.append(series.copy())
        return original_compute_quantile_boundaries(series)

    monkeypatch.setattr(
        train_regime_ml, "compute_quantile_boundaries", _recording_compute_quantile_boundaries
    )

    timeframe = "minutes60"
    end = START + pd.Timedelta(hours=_N)
    n_folds = 2

    reports = run_training(
        markets=list(seeds.keys()),
        timeframe=timeframe,
        start=START,
        end=end,
        n_folds=n_folds,
        min_train_samples=50,
        model_output_dir=tmp_path,
    )

    assert len(reports) >= 1
    assert len(recorded_calls) == len(reports)

    # run_training 내부와 동일한 공식으로 fold 경계를 독립적으로 재계산한다.
    half_life_bars = train_regime_ml.half_life_bars_for_timeframe(timeframe)
    n_bars = round(half_life_bars * train_regime_ml.N_MULTIPLIER)
    embargo = train_regime_ml.timeframe_duration(timeframe) * n_bars
    folds = train_regime_ml.generate_walk_forward_folds(START, end, n_folds, embargo)
    folds_by_index = {fold.fold_index: fold for fold in folds}

    for call_idx, report in enumerate(reports):
        fold = folds_by_index[report["fold_index"]]

        expected_train_parts = []
        expected_train_len = 0
        expected_combined_len = 0
        for market, seed in seeds.items():
            raw_df = _make_synthetic_market_df(market, seed)
            candle_time = raw_df["candle_time"]
            labels = train_regime_ml.compute_normalized_realized_series(raw_df, half_life_bars, n_bars)
            valid = labels.notna()
            train_mask = valid & (candle_time <= fold.train_end)
            test_mask = valid & (candle_time >= fold.test_start) & (candle_time <= fold.test_end)
            expected_train_parts.append(labels[train_mask])
            expected_train_len += int(train_mask.sum())
            expected_combined_len += int(train_mask.sum()) + int(test_mask.sum())

        expected_train_series = pd.concat(expected_train_parts)
        recorded = recorded_calls[call_idx]

        # 핵심 검증: 실제로 compute_quantile_boundaries에 넘어간 표본 수/값이
        # "train 구간만"의 기대치와 정확히 일치한다.
        assert len(recorded) == expected_train_len == report["n_train"]
        np.testing.assert_array_equal(
            np.sort(recorded.to_numpy()), np.sort(expected_train_series.to_numpy())
        )

        # 회귀 방지 net: train+test를 합친 표본 수보다는 반드시 적어야 한다.
        # (fold 루프가 실수로 combined 데이터를 넘기면 여기서 걸린다.)
        assert len(recorded) < expected_combined_len
