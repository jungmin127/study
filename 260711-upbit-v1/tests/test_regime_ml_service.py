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

_MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-XRP"]
_LABELS = ["급하락", "완만하락", "횡보", "완만상승", "급상승"]


def _train_and_save_tiny_model(model_dir, timestamp: str, fold_index: int = 3, performance: dict | None = None):
    """scripts/train_regime_ml.py의 실제 흐름(3마켓 풀링 -> market astype category
    -> LightGBM 학습 -> booster_.save_model)을 축소 재현해 .txt+.json 페어를 저장한다.
    performance를 None으로 두면(기본값) performance 키 자체가 없는 구형 사이드카를
    재현한다 — 명시하면 그 값을 그대로 담는다."""
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
    sidecar = {
        "boundaries": [-0.2, -0.1, 0.1, 0.2],
        "ref_scores": {label: 0.0 for label in _LABELS},
        "classes": [str(c) for c in model.classes_],
        "fold_index": fold_index,
    }
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
        predict_current_ml_regime("KRW-DOGE", "minutes60")


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
