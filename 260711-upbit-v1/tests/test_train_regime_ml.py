"""
tests/test_train_regime_ml.py

scripts.train_regime_ml.run_training()의 end-to-end 스모크 테스트. 실제 네트워크
호출 없이(scripts.regime_ml_data.load_market_training_data를 monkeypatch) 합성
데이터로 전체 파이프라인(데이터 로드 -> 피처 -> fold 루프 -> LightGBM 학습 -> 리포트
-> 모델 저장)이 에러 없이 완주하는지만 검증한다. 개별 단계(레이블/분할/피처/로더)의
세부 동작은 각자의 유닛테스트(test_regime_ml_labels.py 등)가 이미 검증한다.
"""
from __future__ import annotations

import json
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
    # run_training은 내부적으로 n_folds+1개 fold를 요청한다(fold 0이 항상 훈련표본이
    # 비는 문제의 수정 — Finding 3). 독립 재계산도 동일하게 맞춰야 fold 경계가 일치한다.
    folds = train_regime_ml.generate_walk_forward_folds(START, end, n_folds + 1, embargo)
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


def test_aggregate_confusion_and_totals_sum_across_folds():
    """Finding 2b: cross-fold 합산이 fold별 confusion/actual_totals를 elementwise로
    정확히 더하는지 확인한다(가짜 report 딕셔너리로 run_training 없이 직접 검증)."""
    reports = [
        {
            "confusion": {
                "급하락": {"급하락": 2, "완만하락": 0, "횡보": 0, "완만상승": 0, "급상승": 0},
                "완만하락": {"급하락": 0, "완만하락": 3, "횡보": 1, "완만상승": 0, "급상승": 0},
                "횡보": {"급하락": 0, "완만하락": 0, "횡보": 5, "완만상승": 0, "급상승": 0},
                "완만상승": {"급하락": 0, "완만하락": 0, "횡보": 0, "완만상승": 4, "급상승": 0},
                "급상승": {"급하락": 0, "완만하락": 0, "횡보": 0, "완만상승": 0, "급상승": 1},
            },
            "actual_totals": {"급하락": 2, "완만하락": 3, "횡보": 6, "완만상승": 4, "급상승": 1},
        },
        {
            "confusion": {
                "급하락": {"급하락": 1, "완만하락": 0, "횡보": 0, "완만상승": 0, "급상승": 0},
                "완만하락": {"급하락": 0, "완만하락": 2, "횡보": 0, "완만상승": 0, "급상승": 0},
                "횡보": {"급하락": 0, "완만하락": 1, "횡보": 3, "완만상승": 0, "급상승": 0},
                "완만상승": {"급하락": 0, "완만하락": 0, "횡보": 0, "완만상승": 2, "급상승": 0},
                "급상승": {"급하락": 0, "완만하락": 0, "횡보": 0, "완만상승": 0, "급상승": 2},
            },
            "actual_totals": {"급하락": 1, "완만하락": 3, "횡보": 3, "완만상승": 2, "급상승": 2},
        },
    ]

    summed_confusion = train_regime_ml._sum_confusion_matrices(reports)
    summed_totals = train_regime_ml._sum_actual_totals(reports)

    for predicted in train_regime_ml.CATEGORY_LABELS:
        for actual in train_regime_ml.CATEGORY_LABELS:
            expected = sum(r["confusion"][predicted][actual] for r in reports)
            assert summed_confusion[predicted][actual] == expected

    for actual in train_regime_ml.CATEGORY_LABELS:
        expected = sum(r["actual_totals"][actual] for r in reports)
        assert summed_totals[actual] == expected


def test_run_training_prints_caveat_and_aggregate_summary_after_folds(tmp_path, monkeypatch, capsys):
    """Finding 1: hit-rate/confusion이 regime_backtest.py와 직접 비교 불가하다는
    안내가 콘솔 상단에 찍히는지. Finding 2b: 모든 fold 리포트 이후에 "전체 fold 합산"
    블록이 한 번 더 찍히는지(순서까지) 확인한다."""
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

    captured = capsys.readouterr().out
    assert "비교하지 마세요" in captured
    assert "상관계수" in captured
    assert "전체 fold 합산" in captured

    last_fold_marker = f"=== fold {reports[-1]['fold_index']}"
    assert captured.index(last_fold_marker) < captured.index("전체 fold 합산")


def test_run_training_covers_all_requested_folds(tmp_path, monkeypatch):
    """Finding 3: fold 0은 train_end가 항상 start 이전이라 훈련 표본이 구조적으로
    비어 있어 언제나 스킵된다. 수정 전에는 이 때문에 요청한 n_folds 중 하나가 통째로
    누락됐다. 수정 후에는 내부적으로 n_folds+1개를 만들어 fold 0만 스킵되고, fold
    1..n_folds는 모두 평가돼 반환된 report 개수가 n_folds와 같아야 한다."""
    seeds = {"KRW-BTC": 1, "KRW-ETH": 2, "KRW-XRP": 3}
    monkeypatch.setattr(
        train_regime_ml, "load_market_training_data",
        lambda market, timeframe, start, end: _make_synthetic_market_df(market, seeds[market]),
    )

    n_folds = 3
    reports = run_training(
        markets=list(seeds.keys()),
        timeframe="minutes60",
        start=START,
        end=START + pd.Timedelta(hours=_N),
        n_folds=n_folds,
        min_train_samples=50,
        model_output_dir=tmp_path,
    )

    assert len(reports) == n_folds
    assert sorted(r["fold_index"] for r in reports) == list(range(1, n_folds + 1))


def test_run_training_saves_json_sidecar_alongside_model(tmp_path, monkeypatch):
    """Finding 4: 저장된 booster(.txt) 옆에 같은 base filename의 .json sidecar가
    boundaries/ref_scores/classes/fold_index를 담아 저장돼야 한다."""
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

    txt_files = list(tmp_path.glob("*.txt"))
    json_files = list(tmp_path.glob("*.json"))
    assert len(txt_files) == 1
    assert len(json_files) == 1
    assert txt_files[0].stem == json_files[0].stem

    with open(json_files[0], encoding="utf-8") as f:
        sidecar = json.load(f)

    assert set(sidecar.keys()) == {"boundaries", "ref_scores", "classes", "fold_index"}
    assert isinstance(sidecar["boundaries"], list) and len(sidecar["boundaries"]) == 4
    assert isinstance(sidecar["ref_scores"], dict)
    assert set(sidecar["ref_scores"].keys()) == set(train_regime_ml.CATEGORY_LABELS)
    assert isinstance(sidecar["classes"], list) and len(sidecar["classes"]) >= 1
    assert isinstance(sidecar["fold_index"], int)
    assert sidecar["fold_index"] == reports[-1]["fold_index"]
