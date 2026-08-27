"""
tests/test_regime_ml_service.py

backend.regime_ml_service의 find_latest_model()/predict_current_ml_regime()을
검증한다. 특히 predict_current_ml_regime()이 저장된 부스터의 market 범주형 코드
인코딩(scripts/train_regime_ml.py가 pd.concat 후 astype("category")로 만드는
알파벳순 KRW-BTC=0/KRW-ETH=1/KRW-XRP=2)을 추론 시에도 정확히 재현하는지가
핵심이다 — 어긋나면 크래시 없이 조용히 틀린 예측이 나온다.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import lightgbm as lgb
import numpy as np
import pandas as pd
import pytest

import backend.regime_ml_service as regime_ml_service
from backend.regime_ml_service import find_latest_model, predict_current_ml_regime

_MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-XRP"]
_LABELS = ["급하락", "완만하락", "횡보", "완만상승", "급상승"]


def _train_and_save_tiny_model(model_dir, timestamp: str, fold_index: int = 3):
    """scripts/train_regime_ml.py의 실제 흐름(3마켓 풀링 -> market astype category
    -> LightGBM 학습 -> booster_.save_model)을 축소 재현해 .txt+.json 페어를 저장한다."""
    rng = np.random.default_rng(0)
    rows = []
    for market in _MARKETS:
        for _ in range(30):
            rows.append({"FEATURE_A": rng.normal(), "FEATURE_B": rng.normal(), "market": market})
    df = pd.DataFrame(rows)
    df["market"] = df["market"].astype("category")
    labels = pd.Series(rng.choice(_LABELS, size=len(df)))

    model = lgb.LGBMClassifier(objective="multiclass", num_leaves=4, min_child_samples=1, random_state=0)
    model.fit(df, labels)

    model_dir.mkdir(parents=True, exist_ok=True)
    txt_path = model_dir / f"regime_ml_{timestamp}.txt"
    json_path = model_dir / f"regime_ml_{timestamp}.json"
    model.booster_.save_model(str(txt_path))
    json_path.write_text(json.dumps({
        "boundaries": [-0.2, -0.1, 0.1, 0.2],
        "ref_scores": {label: 0.0 for label in _LABELS},
        "classes": [str(c) for c in model.classes_],
        "fold_index": fold_index,
    }), encoding="utf-8")
    return txt_path, json_path, model


def test_find_latest_model_returns_none_when_directory_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(regime_ml_service, "MODEL_DIR", tmp_path / "does_not_exist")
    assert find_latest_model() is None


def test_find_latest_model_picks_most_recent_timestamp(tmp_path, monkeypatch):
    monkeypatch.setattr(regime_ml_service, "MODEL_DIR", tmp_path)
    _train_and_save_tiny_model(tmp_path, "20260101T000000Z", fold_index=1)
    latest_txt, _, _ = _train_and_save_tiny_model(tmp_path, "20260827T052047Z", fold_index=5)

    found = find_latest_model()

    assert found is not None
    txt_path, sidecar = found
    assert txt_path == latest_txt
    assert sidecar["fold_index"] == 5


def test_find_latest_model_skips_txt_without_matching_json(tmp_path, monkeypatch):
    monkeypatch.setattr(regime_ml_service, "MODEL_DIR", tmp_path)
    _train_and_save_tiny_model(tmp_path, "20260101T000000Z", fold_index=1)
    orphan_txt = tmp_path / "regime_ml_20260827T000000Z.txt"
    orphan_txt.write_text("not a real model")

    found = find_latest_model()

    assert found is not None
    txt_path, _ = found
    assert txt_path.name == "regime_ml_20260101T000000Z.txt"


def test_predict_current_ml_regime_rejects_non_hourly_timeframe():
    with pytest.raises(ValueError, match="1시간봉"):
        predict_current_ml_regime("KRW-BTC", "days")


def test_predict_current_ml_regime_raises_when_no_model(tmp_path, monkeypatch):
    monkeypatch.setattr(regime_ml_service, "MODEL_DIR", tmp_path)
    with pytest.raises(FileNotFoundError, match="학습된 ML 모델이 없습니다"):
        predict_current_ml_regime("KRW-BTC", "minutes60")


def test_predict_current_ml_regime_returns_valid_response(tmp_path, monkeypatch):
    monkeypatch.setattr(regime_ml_service, "MODEL_DIR", tmp_path)
    _train_and_save_tiny_model(tmp_path, "20260827T052047Z", fold_index=5)

    fake_raw_df = pd.DataFrame({"close": [1.0] * 5})
    monkeypatch.setattr(regime_ml_service, "load_market_training_data", lambda *a, **k: fake_raw_df)

    def _fake_build_feature_matrix(df, market, half_life_bars):
        rng = np.random.default_rng(1)
        return pd.DataFrame({
            "FEATURE_A": rng.normal(size=len(df)),
            "FEATURE_B": rng.normal(size=len(df)),
            "market": pd.Categorical([market] * len(df)),
        })

    monkeypatch.setattr(regime_ml_service, "build_feature_matrix", _fake_build_feature_matrix)

    result = predict_current_ml_regime("KRW-ETH", "minutes60")

    assert result["predicted_category"] in _LABELS
    assert set(result["probs"].keys()) == set(_LABELS)
    assert sum(result["probs"].values()) == pytest.approx(1.0, abs=1e-6)
    assert result["model_fold_index"] == 5
    assert result["model_trained_at"] == datetime(2026, 8, 27, 5, 20, 47, tzinfo=timezone.utc).isoformat()


def test_predict_current_ml_regime_matches_sklearn_wrapper_for_same_row(tmp_path, monkeypatch):
    """가장 중요한 회귀 테스트: 운영 코드가 쓰는 저수준 lgb.Booster.predict() 경로가,
    학습 스크립트가 검증에 쓰는 고수준 LGBMClassifier.predict_proba() 경로와 동일한
    입력에 대해 동일한 결과를 내야 한다. market 범주형 코드 인코딩이 어긋나면 두
    결과가 달라진다."""
    monkeypatch.setattr(regime_ml_service, "MODEL_DIR", tmp_path)
    _, _, model = _train_and_save_tiny_model(tmp_path, "20260827T052047Z", fold_index=5)

    query_row = pd.DataFrame({
        "FEATURE_A": [0.5],
        "FEATURE_B": [-0.3],
        "market": pd.Categorical(["KRW-ETH"], categories=sorted(_MARKETS)),
    })
    sklearn_probs = dict(zip(model.classes_, model.predict_proba(query_row)[0]))

    fake_raw_df = pd.DataFrame({"close": [1.0]})
    monkeypatch.setattr(regime_ml_service, "load_market_training_data", lambda *a, **k: fake_raw_df)
    monkeypatch.setattr(
        regime_ml_service, "build_feature_matrix",
        lambda df, market, half_life_bars: pd.DataFrame({
            "FEATURE_A": [0.5], "FEATURE_B": [-0.3], "market": pd.Categorical([market]),
        }),
    )

    result = predict_current_ml_regime("KRW-ETH", "minutes60")

    for label in _LABELS:
        assert result["probs"][label] == pytest.approx(float(sklearn_probs[label]), abs=1e-6)
