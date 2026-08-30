"""
tests/test_regime_ml_service.py

backend.regime_ml_service의 find_latest_model()/predict_current_ml_regime()을
검증한다. predict_current_ml_regime()은 market 범주형 컬럼에 명시적으로
categories=sorted(_TRAINING_MARKETS)를 지정해 저장된 부스터의 카테고리 코드
(scripts/train_regime_ml.py가 알파벳순으로 배정: KRW-BTC=0/KRW-ETH=1/KRW-XRP=2)와
어긋나지 않게 방어한다 — 다만 아래
test_predict_current_ml_regime_matches_sklearn_wrapper_for_same_row의 docstring에
적었듯, LightGBM이 자체 pandas_categorical 메타데이터로 값 기준 재매핑을 해주기
때문에 이 스위트의 테스트들이 그 방어 코드의 필요성 자체를 증명하지는 못한다.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone

import lightgbm as lgb
import numpy as np
import pandas as pd
import pytest

import backend.regime_ml_service as regime_ml_service
from backend.regime_ml_service import find_latest_model, predict_current_ml_regime
from engine.regime_ml_features import build_feature_matrix

_MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-XRP"]
_LABELS = ["하락", "하락아님"]


def _make_synthetic_ohlcv_df(n: int = 150) -> pd.DataFrame:
    """tests/test_regime_ml_features.py의 _make_full_df()와 같은 형태 —
    build_feature_matrix()가 요구하는 전체 컬럼(OHLCV+외부지표)을 갖춘 합성
    데이터프레임."""
    rng = np.random.default_rng(42)
    dates = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    close = 100.0 + np.cumsum(rng.normal(0, 1.0, n))
    high = close + rng.uniform(0.1, 1.0, n)
    low = close - rng.uniform(0.1, 1.0, n)
    volume = rng.uniform(10, 100, n)
    return pd.DataFrame({
        "candle_time": dates,
        "close": close, "high": high, "low": low,
        "volume": volume, "trade_value": volume * close,
        "btc_close": close * 1.1, "usdt_close": np.full(n, 1350.0),
        "binance_close": close / 1350.0,
        "fear_greed_value": rng.uniform(0, 100, n),
        "funding_rate_value": rng.uniform(-0.05, 0.05, n),
        "korea_premium_value": rng.uniform(-2, 2, n),
    })


def _train_and_save_tiny_model(
    model_dir, timestamp: str, fold_index: int = 3, performance: dict | None = None,
    markets: list[str] | None = None,
):
    """scripts/train_regime_ml.py의 실제 흐름(3마켓 풀링 -> market astype category
    -> LightGBM 학습 -> booster_.save_model)을 축소 재현해 .txt+.json 페어를 저장한다.
    performance를 None으로 두면(기본값) performance 키 자체가 없는 구형 사이드카를
    재현한다 — 명시하면 그 값을 그대로 담는다. markets를 None으로 두면(기본값)
    "markets" 키 자체가 없는 구형(레거시) 사이드카를 재현한다 — 명시하면 그 목록을
    그대로 담는다(신형 사이드카 재현)."""
    rng = np.random.default_rng(0)
    rows = []
    for market in _MARKETS:
        for _ in range(30):
            rows.append({"FEATURE_A": rng.normal(), "FEATURE_B": rng.normal(), "market": market})
    df = pd.DataFrame(rows)
    df["market"] = df["market"].astype("category")
    labels = pd.Series(rng.choice(_LABELS, size=len(df)))

    model = lgb.LGBMClassifier(objective="binary", num_leaves=4, min_child_samples=1, random_state=0)
    model.fit(df, labels)

    model_dir.mkdir(parents=True, exist_ok=True)
    txt_path = model_dir / f"regime_ml_{timestamp}.txt"
    json_path = model_dir / f"regime_ml_{timestamp}.json"
    model.booster_.save_model(str(txt_path))
    sidecar = {
        "boundaries": [-0.2, -0.1, 0.1, 0.2],
        "ref_scores": {label: 0.0 for label in _LABELS},
        "classes": [str(c) for c in model.classes_],
        "fold_index": fold_index,
    }
    if markets is not None:
        sidecar["markets"] = markets
    if performance is not None:
        sidecar["performance"] = performance
    json_path.write_text(json.dumps(sidecar), encoding="utf-8")
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


def test_predict_current_ml_regime_rejects_untrained_market():
    with pytest.raises(ValueError, match="만 학습되어"):
        predict_current_ml_regime("KRW-AVAX", "minutes60")


def test_predict_current_ml_regime_accepts_newly_expanded_market(tmp_path, monkeypatch):
    """KRW-SHIB는 이번에 TRAINING_MARKETS에 새로 추가되는 마켓이다 — "학습 안 된
    마켓" ValueError가 아니라, 모델 파일이 없다는 FileNotFoundError가 나야 한다
    (마켓 검증은 통과했다는 뜻)."""
    monkeypatch.setattr(regime_ml_service, "MODEL_DIR", tmp_path)
    with pytest.raises(FileNotFoundError, match="학습된 ML 모델이 없습니다"):
        predict_current_ml_regime("KRW-SHIB", "minutes60")


def test_predict_current_ml_regime_rejects_market_not_in_serving_model(tmp_path, monkeypatch):
    """KRW-SOL은 전역 TRAINING_MARKETS(14개)에는 있지만, 이 서빙 모델의 사이드카가
    명시한 markets(신형 포맷, KRW-BTC/ETH/XRP 3개)에는 없다 — 전역 상수 검사는
    통과해도 실제 배포된 모델 기준으로는 거부돼야 한다. load_market_training_data/
    build_feature_matrix를 monkeypatch해두는 건, 마켓 검증이 그 실제 I/O에 도달하기도
    전에 먼저 걸려야 한다는 걸 보장하기 위해서다(호출되면 다른 테스트들처럼 이
    fake들이 반환할 뿐, 진짜 네트워크/데이터 경로를 타지 않는다)."""
    monkeypatch.setattr(regime_ml_service, "MODEL_DIR", tmp_path)
    _train_and_save_tiny_model(tmp_path, "20260827T052047Z", fold_index=5, markets=list(_MARKETS))

    fake_raw_df = pd.DataFrame({
        "close": [1.0] * 5,
        "candle_time": pd.date_range("2026-08-27T01:00:00", periods=5, freq="h"),
    })
    monkeypatch.setattr(regime_ml_service, "load_market_training_data", lambda *a, **k: fake_raw_df)

    def _fake_build_feature_matrix(df, market, half_life_bars):
        rng = np.random.default_rng(1)
        return pd.DataFrame({
            "FEATURE_A": rng.normal(size=len(df)),
            "FEATURE_B": rng.normal(size=len(df)),
            "market": pd.Categorical([market] * len(df)),
        })

    monkeypatch.setattr(regime_ml_service, "build_feature_matrix", _fake_build_feature_matrix)

    with pytest.raises(ValueError, match="만 학습되어"):
        predict_current_ml_regime("KRW-SOL", "minutes60")


def test_predict_current_ml_regime_rejects_market_not_in_legacy_model_without_markets_key(tmp_path, monkeypatch):
    """사이드카에 "markets" 키가 아예 없는 구형 모델(이번 마켓 확장 이전에 학습된
    모든 모델)은 하드코딩된 레거시 3마켓(KRW-BTC/ETH/XRP)만 커버한다고 간주해야
    한다 — 전역 TRAINING_MARKETS(현재 14개)로 폴백하면 이 테스트가 방지하려는
    버그(학습 안 된 마켓이 조용히 통과)가 재현된다."""
    monkeypatch.setattr(regime_ml_service, "MODEL_DIR", tmp_path)
    _train_and_save_tiny_model(tmp_path, "20260827T052047Z", fold_index=5)  # markets 없음(구형)

    fake_raw_df = pd.DataFrame({
        "close": [1.0] * 5,
        "candle_time": pd.date_range("2026-08-27T01:00:00", periods=5, freq="h"),
    })
    monkeypatch.setattr(regime_ml_service, "load_market_training_data", lambda *a, **k: fake_raw_df)

    def _fake_build_feature_matrix(df, market, half_life_bars):
        rng = np.random.default_rng(1)
        return pd.DataFrame({
            "FEATURE_A": rng.normal(size=len(df)),
            "FEATURE_B": rng.normal(size=len(df)),
            "market": pd.Categorical([market] * len(df)),
        })

    monkeypatch.setattr(regime_ml_service, "build_feature_matrix", _fake_build_feature_matrix)

    with pytest.raises(ValueError, match="만 학습되어"):
        predict_current_ml_regime("KRW-SOL", "minutes60")


def test_predict_current_ml_regime_raises_when_no_model(tmp_path, monkeypatch):
    monkeypatch.setattr(regime_ml_service, "MODEL_DIR", tmp_path)
    with pytest.raises(FileNotFoundError, match="학습된 ML 모델이 없습니다"):
        predict_current_ml_regime("KRW-BTC", "minutes60")


def test_predict_current_ml_regime_returns_valid_response(tmp_path, monkeypatch):
    monkeypatch.setattr(regime_ml_service, "MODEL_DIR", tmp_path)
    _train_and_save_tiny_model(tmp_path, "20260827T052047Z", fold_index=5)

    fake_raw_df = pd.DataFrame({
        "close": [1.0] * 5,
        "candle_time": pd.date_range("2026-08-27T01:00:00", periods=5, freq="h"),
    })
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
    assert result["bar_time"] == datetime(2026, 8, 27, 5, 0, 0, tzinfo=timezone.utc).isoformat()
    assert result["model_performance"] is None


def test_predict_current_ml_regime_includes_performance_when_present(tmp_path, monkeypatch):
    monkeypatch.setattr(regime_ml_service, "MODEL_DIR", tmp_path)
    performance = {
        "folds": [{"fold_index": 5, "n_train": 100, "n_test": 20, "correlation": 0.12}],
        "pooled_correlation": 0.12,
        "pooled_hit_rate": {label: 0.2 for label in _LABELS},
    }
    _train_and_save_tiny_model(tmp_path, "20260827T052047Z", fold_index=5, performance=performance)

    fake_raw_df = pd.DataFrame({
        "close": [1.0] * 5,
        "candle_time": pd.date_range("2026-08-27T01:00:00", periods=5, freq="h"),
    })
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

    assert result["model_performance"] == performance


def test_predict_current_ml_regime_performance_is_none_for_legacy_sidecar(tmp_path, monkeypatch):
    monkeypatch.setattr(regime_ml_service, "MODEL_DIR", tmp_path)
    _train_and_save_tiny_model(tmp_path, "20260827T052047Z", fold_index=5)  # performance 없음(기본값)

    fake_raw_df = pd.DataFrame({
        "close": [1.0] * 5,
        "candle_time": pd.date_range("2026-08-27T01:00:00", periods=5, freq="h"),
    })
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

    assert result["model_performance"] is None


def test_predict_current_ml_regime_uses_deployed_marker_over_latest_file(tmp_path, monkeypatch):
    """배포 마커가 있으면, 그 이후 새로 학습된(더 최신 파일명의) 모델이 있어도
    마커가 가리키는 모델을 써야 한다 — "배포" 버튼이 실제 서빙 모델을 결정해야
    하고, 파일명 타임스탬프가 그걸 무시하고 이겨서는 안 된다."""
    monkeypatch.setattr(regime_ml_service, "MODEL_DIR", tmp_path)
    _train_and_save_tiny_model(tmp_path, "20260101T000000Z", fold_index=1)
    _train_and_save_tiny_model(tmp_path, "20260827T052047Z", fold_index=5)  # 더 최근에 학습됨
    regime_ml_service.set_last_deployed_marker("regime_ml_20260101T000000Z")  # 하지만 이게 배포됨

    fake_raw_df = pd.DataFrame({
        "close": [1.0] * 5,
        "candle_time": pd.date_range("2026-08-27T01:00:00", periods=5, freq="h"),
    })
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

    assert result["model_fold_index"] == 1


def test_predict_current_ml_regime_falls_back_to_latest_when_marker_model_missing(tmp_path, monkeypatch):
    """배포 마커가 가리키는 모델 파일이 실제로는 없으면(지워졌거나 손상됐거나),
    조용히 실패하지 않고 최신 학습 파일로 폴백해야 한다."""
    monkeypatch.setattr(regime_ml_service, "MODEL_DIR", tmp_path)
    _train_and_save_tiny_model(tmp_path, "20260827T052047Z", fold_index=5)
    regime_ml_service.set_last_deployed_marker("regime_ml_20260101T000000Z")  # 존재하지 않는 모델

    fake_raw_df = pd.DataFrame({
        "close": [1.0] * 5,
        "candle_time": pd.date_range("2026-08-27T01:00:00", periods=5, freq="h"),
    })
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

    assert result["model_fold_index"] == 5


def test_predict_current_ml_regime_matches_sklearn_wrapper_for_same_row(tmp_path, monkeypatch):
    """운영 코드가 쓰는 저수준 lgb.Booster.predict() 경로가, 학습 스크립트가 검증에 쓰는
    고수준 LGBMClassifier.predict_proba() 경로와 동일한 입력에 대해 동일한 결과를 내야
    한다는 일반적인 sanity check다 — "우리 추론 코드가 라이브러리 자체의 참조 구현과
    일치하는가"를 검증한다.

    주의: 이 테스트는 market 범주형 코드 인코딩(_TRAINING_MARKETS 정렬 순서로
    categories=를 명시하는 부분)의 정확성을 증명하지 않는다 — 실측 확인 결과, LightGBM의
    Booster.predict()는 호출자가 넘긴 Categorical 객체의 코드가 아니라, 부스터 자신이
    저장해둔 pandas_categorical 메타데이터를 기준으로 각 카테고리 "값"을 다시 매핑한다.
    즉 categories=sorted(_TRAINING_MARKETS)로 명시하든, 카테고리가 1개뿐인 naive한
    Categorical을 넘기든 예측 결과가 동일해서, 이 테스트는 그 차이를 구분하지 못한다.
    그래도 predict_current_ml_regime()의 명시적 categories= 코드는 계속 유지한다 — 이
    LightGBM 내부 자동 재매핑 동작이 버전 간에도 계속 유지된다고 의존하지 않는 방어적
    코딩이기 때문이다."""
    monkeypatch.setattr(regime_ml_service, "MODEL_DIR", tmp_path)
    _, _, model = _train_and_save_tiny_model(tmp_path, "20260827T052047Z", fold_index=5)

    query_row = pd.DataFrame({
        "FEATURE_A": [0.5],
        "FEATURE_B": [-0.3],
        "market": pd.Categorical(["KRW-ETH"], categories=sorted(_MARKETS)),
    })
    sklearn_probs = dict(zip(model.classes_, model.predict_proba(query_row)[0]))

    fake_raw_df = pd.DataFrame({
        "close": [1.0],
        "candle_time": [pd.Timestamp("2026-08-27T05:00:00")],
    })
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


def test_predict_current_ml_regime_applies_custom_threshold_and_calibration(tmp_path, monkeypatch):
    """sidecar에 decision_threshold=0.3(0.5보다 낮음)과, 원래 확률을 항상 0.9로
    끌어올리는 calibration_breakpoints가 있으면, 원래라면 "하락아님"으로
    argmax됐을 raw_prediction도 보정+낮은 threshold를 거쳐 "하락"으로 뒤집혀야
    한다."""
    monkeypatch.setattr(regime_ml_service, "MODEL_DIR", tmp_path)
    txt_path, json_path, model = _train_and_save_tiny_model(tmp_path, "20260827T052047Z", fold_index=5)
    sidecar = json.loads(json_path.read_text(encoding="utf-8"))
    sidecar["decision_threshold"] = 0.3
    # 모든 입력 확률을 0.9로 보정하는 항등에 가까운(사실상 상수) 브레이크포인트
    sidecar["calibration_breakpoints"] = [[0.0, 0.9], [1.0, 0.9]]
    json_path.write_text(json.dumps(sidecar), encoding="utf-8")

    fake_raw_df = pd.DataFrame({
        "close": [1.0] * 5,
        "candle_time": pd.date_range("2026-08-27T01:00:00", periods=5, freq="h"),
    })
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

    assert result["predicted_category"] == "하락"
    assert result["probs"]["하락"] == pytest.approx(0.9, abs=1e-6)
    assert result["probs"]["하락아님"] == pytest.approx(0.1, abs=1e-6)


def test_predict_current_ml_regime_legacy_sidecar_keeps_argmax_behavior(tmp_path, monkeypatch):
    """decision_threshold/calibration_breakpoints 키가 아예 없는 구형 sidecar는
    기존과 동일하게 항등 보정 + threshold 0.5(=argmax와 동치)로 동작해야 한다."""
    monkeypatch.setattr(regime_ml_service, "MODEL_DIR", tmp_path)
    _train_and_save_tiny_model(tmp_path, "20260827T052047Z", fold_index=5)

    fake_raw_df = pd.DataFrame({
        "close": [1.0] * 5,
        "candle_time": pd.date_range("2026-08-27T01:00:00", periods=5, freq="h"),
    })
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

    # 보정 전 raw 확률 그대로 argmax한 결과와 같아야 한다.
    assert result["predicted_category"] == max(result["probs"], key=result["probs"].get)


def test_real_feature_matrix_matches_real_saved_model_feature_count(tmp_path):
    """Fix 1(피처/모델 스키마 불일치로 인한 lightgbm.basic.LightGBMError 미처리
    크래시)의 회귀 방지 테스트다. 이 파일의 다른 모든 테스트는
    build_feature_matrix()를 monkeypatch로 대체해 FEATURE_A/FEATURE_B 2개짜리
    가짜 피처만 쓰기 때문에, engine.regime_ml_features.build_feature_matrix가 실제로
    만드는 컬럼 수와 저장된 모델이 기대하는 피처 수가 어긋나는 종류의 버그는
    그 무엇으로도 잡히지 않는다(실제로 61->64 컬럼 변경이 이렇게 새어나가
    운영에서 크래시가 났다). 여기서는 monkeypatch 없이 진짜
    build_feature_matrix()로 피처를 만들고, 그걸로 진짜 LightGBM 모델을
    학습·저장·재로드해서 저장된 부스터가 기대하는 피처 개수가 현재 코드가
    만드는 피처 개수와 정확히 일치하는지, 그리고 predict()가 실제로 에러 없이
    도는지를 직접 확인한다."""
    df = _make_synthetic_ohlcv_df()
    features_df = build_feature_matrix(df, market="KRW-BTC", half_life_bars=24.0).dropna()
    assert len(features_df) > 10

    rng = np.random.default_rng(7)
    labels = rng.choice(_LABELS, size=len(features_df))

    model = lgb.LGBMClassifier(objective="binary", num_leaves=4, min_child_samples=1, random_state=0)
    model.fit(features_df, labels)

    model_path = tmp_path / "regime_ml_test_model.txt"
    model.booster_.save_model(str(model_path))
    booster = lgb.Booster(model_file=str(model_path))

    assert booster.num_feature() == len(features_df.columns)
    probs = booster.predict(features_df.iloc[[-1]], validate_features=True)
    assert probs.shape[0] == 1


def test_list_trained_models_returns_empty_when_dir_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(regime_ml_service, "MODEL_DIR", tmp_path / "does_not_exist")
    assert regime_ml_service.list_trained_models() == []


def test_list_trained_models_orders_newest_first_and_marks_deployed(tmp_path, monkeypatch):
    monkeypatch.setattr(regime_ml_service, "MODEL_DIR", tmp_path)
    _train_and_save_tiny_model(tmp_path, "20260101T000000Z", performance={
        "folds": [], "pooled_correlation": 0.05,
        "pooled_hit_rate": {label: None for label in _LABELS},
    })
    _train_and_save_tiny_model(tmp_path, "20260102T000000Z", performance={
        "folds": [], "pooled_correlation": 0.08,
        "pooled_hit_rate": {label: None for label in _LABELS},
    })
    regime_ml_service.set_last_deployed_marker("regime_ml_20260101T000000Z")

    models = regime_ml_service.list_trained_models()

    assert [m["model_timestamp"] for m in models] == [
        "regime_ml_20260102T000000Z", "regime_ml_20260101T000000Z",
    ]
    assert models[0]["performance"]["pooled_correlation"] == 0.08
    assert models[0]["is_deployed"] is False
    assert models[1]["is_deployed"] is True


def test_list_trained_models_skips_incomplete_pairs(tmp_path, monkeypatch):
    monkeypatch.setattr(regime_ml_service, "MODEL_DIR", tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "regime_ml_20260101T000000Z.json").write_text('{"performance": null}', encoding="utf-8")
    # .txt 짝이 없음 — 불완전한 저장으로 취급해 건너뛴다.

    assert regime_ml_service.list_trained_models() == []


def test_get_last_deployed_marker_returns_none_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(regime_ml_service, "MODEL_DIR", tmp_path)
    assert regime_ml_service.get_last_deployed_marker() is None


def test_set_last_deployed_marker_persists_and_reads_back(tmp_path, monkeypatch):
    monkeypatch.setattr(regime_ml_service, "MODEL_DIR", tmp_path)
    regime_ml_service.set_last_deployed_marker("regime_ml_20260101T000000Z")

    marker = regime_ml_service.get_last_deployed_marker()
    assert marker["model_timestamp"] == "regime_ml_20260101T000000Z"
    assert "deployed_at" in marker


def test_deploy_model_raises_file_not_found_when_model_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(regime_ml_service, "MODEL_DIR", tmp_path)

    with pytest.raises(FileNotFoundError):
        regime_ml_service.deploy_model("regime_ml_20260101T000000Z")


def test_deploy_model_runs_push_script_and_sets_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(regime_ml_service, "MODEL_DIR", tmp_path)
    _train_and_save_tiny_model(tmp_path, "20260101T000000Z")

    captured = {}

    class _FakeResult:
        returncode = 0
        stdout = "모델 전송 완료"
        stderr = ""

    def _fake_run(args, **kwargs):
        captured["args"] = args
        return _FakeResult()

    monkeypatch.setattr(regime_ml_service.subprocess, "run", _fake_run)
    monkeypatch.setattr(regime_ml_service.shutil, "which", lambda name: "/usr/bin/bash")

    regime_ml_service.deploy_model("regime_ml_20260101T000000Z")

    assert captured["args"] == [
        "/usr/bin/bash", str(regime_ml_service.REPO_ROOT / "scripts" / "push_regime_ml_model.sh"),
        "regime_ml_20260101T000000Z",
    ]
    marker = regime_ml_service.get_last_deployed_marker()
    assert marker["model_timestamp"] == "regime_ml_20260101T000000Z"


def test_deploy_model_raises_runtime_error_when_bash_not_found(tmp_path, monkeypatch):
    # Windows에서 subprocess.run(["bash", ...])는 CreateProcess의 System32 우선
    # 검색 순서 때문에 PATH의 Git Bash보다 WSL의 bash.exe 런처를 먼저 찾을 수
    # 있다(WSL 배포판 미설치 시 UTF-16LE 오류 배너를 찍고 깨짐). 그래서
    # deploy_model()은 shutil.which("bash")로 명시적 경로를 구해서 넘긴다 —
    # 그마저도 못 찾으면 RuntimeError로 실패해야 한다(subprocess.run에 넘겨
    # FileNotFoundError로 새게 두지 않음).
    monkeypatch.setattr(regime_ml_service, "MODEL_DIR", tmp_path)
    _train_and_save_tiny_model(tmp_path, "20260101T000000Z")
    monkeypatch.setattr(regime_ml_service.shutil, "which", lambda name: None)

    with pytest.raises(RuntimeError, match="bash"):
        regime_ml_service.deploy_model("regime_ml_20260101T000000Z")


def test_deploy_model_raises_runtime_error_when_script_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(regime_ml_service, "MODEL_DIR", tmp_path)
    _train_and_save_tiny_model(tmp_path, "20260101T000000Z")

    class _FakeResult:
        returncode = 1
        stdout = ""
        stderr = "DEPLOY_SSH_KEY_PATH가 설정되어 있지 않습니다."

    monkeypatch.setattr(regime_ml_service.subprocess, "run", lambda args, **kwargs: _FakeResult())

    with pytest.raises(RuntimeError, match="DEPLOY_SSH_KEY_PATH"):
        regime_ml_service.deploy_model("regime_ml_20260101T000000Z")

    assert regime_ml_service.get_last_deployed_marker() is None


def test_deploy_model_raises_runtime_error_on_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(regime_ml_service, "MODEL_DIR", tmp_path)
    _train_and_save_tiny_model(tmp_path, "20260101T000000Z")

    def _fake_run(args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args, timeout=kwargs.get("timeout", 120))

    monkeypatch.setattr(regime_ml_service.subprocess, "run", _fake_run)

    with pytest.raises(RuntimeError, match="120초"):
        regime_ml_service.deploy_model("regime_ml_20260101T000000Z")
