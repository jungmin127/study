"""
tests/test_train_regime_ml.py

scripts.train_regime_ml.run_training()의 end-to-end 스모크 테스트. 실제 네트워크
호출 없이(engine.regime_ml_data.load_market_training_data를 monkeypatch) 합성
데이터로 전체 파이프라인(데이터 로드 -> 피처 -> fold 루프 -> LightGBM 학습 -> 리포트
-> 모델 저장)이 에러 없이 완주하는지만 검증한다. 개별 단계(레이블/분할/피처/로더/
분류지표)의 세부 동작은 각자의 유닛테스트(test_regime_ml_labels.py 등)가 이미
검증한다. barrier_k=6.0은 이 합성 데이터(seed 1/2/3, _N=24*40시간)에서 모든 fold의
train/test에 하락/하락아님 두 클래스가 전부 나타나는 것으로 실측 확인된 값이다
(LightGBM binary 학습이 클래스 1개짜리 표본으로 실패하지 않도록 — 실제 운영 상수
BARRIER_K=6.25와는 별개로, 테스트 전용으로 고른 값).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import scripts.train_regime_ml as train_regime_ml
from scripts.train_regime_ml import run_training

START = datetime(2024, 1, 1, tzinfo=timezone.utc)
_N = 24 * 40  # minutes60, 40일치
_BARRIER_K = 6.0


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
        "usdkrw_rate_value": 1300.0 + np.cumsum(rng.normal(0, 1.0, _N)),
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
        barrier_k=_BARRIER_K,
        model_output_dir=tmp_path,
    )

    assert len(reports) >= 1
    for report in reports:
        assert report["n_test"] > 0
        assert set(report["metrics"]["confusion"].keys()) == set(train_regime_ml.CATEGORY_LABELS)
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
        barrier_k=_BARRIER_K,
        model_output_dir=tmp_path,
    )

    assert reports == []
    assert list(tmp_path.glob("*.txt")) == []


def test_run_training_prints_aggregate_summary_after_folds(tmp_path, monkeypatch, capsys):
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
        barrier_k=_BARRIER_K,
        model_output_dir=tmp_path,
    )
    assert len(reports) >= 1

    captured = capsys.readouterr().out
    assert "macro F1" in captured
    assert "전체 fold 풀링" in captured
    assert "마켓별 성능" in captured

    last_fold_marker = f"=== fold {reports[-1]['fold_index']}"
    assert captured.index(last_fold_marker) < captured.index("전체 fold 풀링")


def test_run_training_covers_all_requested_folds(tmp_path, monkeypatch):
    """fold 0은 train_end가 항상 start 이전이라 훈련 표본이 구조적으로 비어 있어
    언제나 스킵된다. 내부적으로 n_folds+1개를 만들어 fold 0만 스킵되고, fold
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
        barrier_k=_BARRIER_K,
        model_output_dir=tmp_path,
    )

    assert len(reports) == n_folds
    assert sorted(r["fold_index"] for r in reports) == list(range(1, n_folds + 1))


def test_run_training_saves_json_sidecar_alongside_model(tmp_path, monkeypatch):
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
        barrier_k=_BARRIER_K,
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

    assert set(sidecar.keys()) == {
        "markets", "labeling_method", "barrier_k", "classes", "fold_index", "performance",
        "decision_threshold", "calibration_breakpoints", "threshold_table",
    }
    assert sidecar["markets"] == list(seeds.keys())
    assert sidecar["labeling_method"] == "triple_barrier"
    assert sidecar["barrier_k"] == _BARRIER_K
    # set()이 아니라 순서를 그대로 비교한다 — LightGBM의 model.classes_는 이
    # 한국어 레이블들을 유니코드 코드포인트 순으로 정렬하는데(실측: sorted(['하락',
    # '하락아님']) == ['하락', '하락아님']), 이 값이 마침 sorted(CATEGORY_LABELS)와
    # 같다. 이 순서가 binary objective에서 model.predict()가 반환하는 스칼라가 어느
    # 클래스의 확률인지(classes[1])를 결정하므로(backend/regime_ml_service.py 참고),
    # set 비교로는 순서가 어긋나는 회귀를 놓친다.
    assert sidecar["classes"] == sorted(train_regime_ml.CATEGORY_LABELS)
    assert isinstance(sidecar["fold_index"], int)
    assert sidecar["fold_index"] == reports[-1]["fold_index"]

    performance = sidecar["performance"]
    assert len(performance["folds"]) == len(reports)
    for fold_perf, report in zip(performance["folds"], reports):
        assert fold_perf["fold_index"] == report["fold_index"]
        assert fold_perf["n_train"] == report["n_train"]
        assert fold_perf["n_test"] == report["n_test"]
        assert fold_perf["macro_f1"] == report["metrics"]["macro_f1"]
        assert fold_perf["weighted_kappa"] == report["metrics"]["weighted_kappa"]

    pooled = performance["pooled"]
    assert pooled["n"] == sum(r["n_test"] for r in reports)
    assert -1.0 <= pooled["weighted_kappa"] <= 1.0
    assert 0.0 <= pooled["macro_f1"] <= 1.0
    assert set(pooled["class_precision_recall"].keys()) == set(train_regime_ml.CATEGORY_LABELS)

    per_market = performance["per_market"]
    assert set(per_market.keys()) == set(seeds.keys())
    assert sum(m["n"] for m in per_market.values()) == pooled["n"]


def test_run_training_performance_folds_excludes_skipped_folds(tmp_path, monkeypatch):
    """fold 하나가 표본 부족으로 스킵됐을 때, 사이드카 performance.folds가 실제로
    평가된 fold만 담고(reports와 정확히 같은 fold_index 집합) 스킵된 fold는 포함하지
    않는지 확인한다."""
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
        n_folds=3,
        min_train_samples=600,
        barrier_k=_BARRIER_K,
        model_output_dir=tmp_path,
    )

    assert [r["fold_index"] for r in reports] == [2, 3]

    # 이 합성 데이터(seed 1/2/3, _N=24*40시간, barrier_k=6.0)에서 n_folds=3일 때
    # 실측 n_train은 fold 1=537, fold 2=1257, fold 3=1977 —
    # min_train_samples=600이면 fold 1만 표본 부족으로 스킵되고 fold 2·3은 평가된다.
    json_files = list(tmp_path.glob("*.json"))
    assert len(json_files) == 1
    with open(json_files[0], encoding="utf-8") as f:
        sidecar = json.load(f)

    assert [f["fold_index"] for f in sidecar["performance"]["folds"]] == [2, 3]


def test_run_training_passes_sample_weight_to_fit(tmp_path, monkeypatch):
    """model.fit()이 sample_weight 인자를 받는지, 그리고 그 길이가 train 표본
    수와 같은지 확인한다 — LGBMClassifier.fit을 monkeypatch해 실제 호출 인자를
    가로챈다."""
    seeds = {"KRW-BTC": 1, "KRW-ETH": 2, "KRW-XRP": 3}
    monkeypatch.setattr(
        train_regime_ml, "load_market_training_data",
        lambda market, timeframe, start, end: _make_synthetic_market_df(market, seeds[market]),
    )

    captured_calls = []
    original_fit = train_regime_ml.lgb.LGBMClassifier.fit

    def _capturing_fit(self, X, y, sample_weight=None, **kwargs):
        captured_calls.append({"n_X": len(X), "n_y": len(y), "sample_weight": sample_weight})
        return original_fit(self, X, y, sample_weight=sample_weight, **kwargs)

    monkeypatch.setattr(train_regime_ml.lgb.LGBMClassifier, "fit", _capturing_fit)

    reports = run_training(
        markets=list(seeds.keys()),
        timeframe="minutes60",
        start=START,
        end=START + pd.Timedelta(hours=_N),
        n_folds=2,
        min_train_samples=50,
        barrier_k=_BARRIER_K,
        model_output_dir=tmp_path,
    )
    assert len(reports) >= 1
    assert len(captured_calls) == len(reports)
    for call in captured_calls:
        assert call["sample_weight"] is not None
        assert len(call["sample_weight"]) == call["n_X"]
        assert all(w > 0 for w in call["sample_weight"])


def test_run_training_saves_calibration_fields_in_sidecar(tmp_path, monkeypatch):
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
        barrier_k=_BARRIER_K,
        model_output_dir=tmp_path,
    )
    assert len(reports) >= 1

    json_files = list(tmp_path.glob("*.json"))
    assert len(json_files) == 1
    with open(json_files[0], encoding="utf-8") as f:
        sidecar = json.load(f)

    assert isinstance(sidecar["decision_threshold"], float)
    assert 0.0 <= sidecar["decision_threshold"] <= 1.0
    assert isinstance(sidecar["calibration_breakpoints"], list)
    for point in sidecar["calibration_breakpoints"]:
        assert len(point) == 2
        assert 0.0 <= point[1] <= 1.0
    assert isinstance(sidecar["threshold_table"], list)
    assert len(sidecar["threshold_table"]) > 0
    for row in sidecar["threshold_table"]:
        assert set(row.keys()) == {"threshold", "precision", "recall", "n_predicted_down"}


def test_run_training_features_include_cross_sectional_columns(tmp_path, monkeypatch):
    """model.fit()에 실제로 넘어가는 학습 피처에 BETA_NEUTRAL_RETURN/
    CROSS_SECTIONAL_RANK가 포함되는지 확인한다."""
    seeds = {"KRW-BTC": 1, "KRW-ETH": 2, "KRW-XRP": 3}
    monkeypatch.setattr(
        train_regime_ml, "load_market_training_data",
        lambda market, timeframe, start, end: _make_synthetic_market_df(market, seeds[market]),
    )

    captured_columns = []
    original_fit = train_regime_ml.lgb.LGBMClassifier.fit

    def _capturing_fit(self, X, y, **kwargs):
        captured_columns.append(list(X.columns))
        return original_fit(self, X, y, **kwargs)

    monkeypatch.setattr(train_regime_ml.lgb.LGBMClassifier, "fit", _capturing_fit)

    reports = run_training(
        markets=list(seeds.keys()),
        timeframe="minutes60",
        start=START,
        end=START + pd.Timedelta(hours=_N),
        n_folds=2,
        min_train_samples=50,
        barrier_k=_BARRIER_K,
        model_output_dir=tmp_path,
    )
    assert len(reports) >= 1
    for columns in captured_columns:
        assert "BETA_NEUTRAL_RETURN" in columns
        assert "CROSS_SECTIONAL_RANK" in columns
