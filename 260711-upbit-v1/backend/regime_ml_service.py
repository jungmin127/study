"""
backend/regime_ml_service.py

scripts/train_regime_ml.py가 data/regime_ml_models/에 저장한 LightGBM 모델을 불러와
"현재" 한 봉에 대한 예측만 계산한다. 정확도 리포트/과거 백테스트는 다루지 않는다
(인샘플 문제 — docs/superpowers/specs/2026-08-27-regime-dashboard-ml-current-
prediction-design.md "비범위" 참고). 설계 문서: 같은 파일.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import lightgbm as lgb
import pandas as pd

from engine.regime_detector import half_life_bars_for_timeframe
from engine.regime_ml_data import load_market_training_data
from engine.regime_ml_features import build_feature_matrix

MODEL_DIR = Path(__file__).parent.parent / "data" / "regime_ml_models"

# OBV 스케일 불일치는 engine/regime_ml_features.py:build_feature_matrix()가 OBV를
# 피처에서 제외해 해결했다(OBV_ROC로 대체) — WARMUP_DAYS=30이 짧아도 더 이상
# 학습/추론 간 스케일이 어긋나지 않는다.
WARMUP_DAYS = 30
_TIMESTAMP_PATTERN = re.compile(r"regime_ml_(\d{8}T\d{6}Z)")

# scripts/train_regime_ml.py의 MARKETS와 반드시 같은 집합이어야 한다. 학습 시
# train_X["market"].astype("category")가 이 3개 마켓의 알파벳순으로 카테고리
# 코드(0/1/2)를 배정했고, 저장된 부스터는 그 정수 코드만 기억한다 — 추론 시 이
# 전체 목록을 categories=로 명시하지 않으면(예: 1행짜리 프레임에 그냥
# astype("category")를 부르면) 카테고리가 1개뿐이라 코드가 다시 0으로 배정돼
# 학습 때와 다른 마켓을 가리키는 것처럼 조용히 오작동한다.
_TRAINING_MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-XRP"]


def find_latest_model() -> tuple[Path, dict] | None:
    """MODEL_DIR에서 파일명 타임스탬프 기준 가장 최근 .txt+.json 페어를 찾는다.
    .json 사이드카가 없는 .txt는 불완전한 저장으로 취급해 건너뛴다. 없으면 None."""
    if not MODEL_DIR.exists():
        return None
    txt_files = sorted(MODEL_DIR.glob("regime_ml_*.txt"))
    for txt_path in reversed(txt_files):  # 파일명이 타임스탬프라 사전순 정렬이 곧 시간순
        json_path = txt_path.with_suffix(".json")
        if json_path.exists():
            sidecar = json.loads(json_path.read_text(encoding="utf-8"))
            return txt_path, sidecar
    return None


def _parse_trained_at(txt_path: Path) -> str:
    match = _TIMESTAMP_PATTERN.search(txt_path.stem)
    if not match:
        raise RuntimeError(f"모델 파일명에서 타임스탬프를 읽을 수 없습니다: {txt_path.name}")
    dt = datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _to_utc_iso(value) -> str:
    """candle_time이 tz 정보 없이 UTC 값만 담고 있을 수 있어, API 응답에 넘기기 전에 항상
    UTC 오프셋을 명시한다(backend/main.py·engine/regime_ml_data.py의 동명 헬퍼와 같은
    이유 — 순환참조를 피하려고 이 모듈에도 별도로 복제한다)."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def predict_current_ml_regime(market: str, timeframe: str) -> dict:
    """market의 가장 최근 봉 하나에 대한 ML 예측을 반환한다."""
    if timeframe != "minutes60":
        raise ValueError("ML 모델은 1시간봉(minutes60)으로만 학습되어 있습니다")
    if market not in _TRAINING_MARKETS:
        raise ValueError(
            f"이 모델은 {', '.join(_TRAINING_MARKETS)}로만 학습되어 있습니다"
        )

    found = find_latest_model()
    if found is None:
        raise FileNotFoundError(
            "학습된 ML 모델이 없습니다. scripts/train_regime_ml.py를 먼저 실행하세요"
        )
    model_path, sidecar = found

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=WARMUP_DAYS)
    df = load_market_training_data(market, timeframe, start, end)
    bar_time = _to_utc_iso(df["candle_time"].iloc[-1])

    half_life_bars = half_life_bars_for_timeframe(timeframe)
    features_df = build_feature_matrix(df, market, half_life_bars)
    features_df = features_df.assign(
        market=pd.Categorical(features_df["market"], categories=sorted(_TRAINING_MARKETS))
    )
    last_row = features_df.iloc[[-1]]

    booster = lgb.Booster(model_file=str(model_path))
    probs_row = booster.predict(last_row, validate_features=True)[0]
    classes: list[str] = sidecar["classes"]
    probs = {label: float(p) for label, p in zip(classes, probs_row)}
    predicted_category = max(probs, key=probs.get)

    return {
        "predicted_category": predicted_category,
        "probs": probs,
        "model_trained_at": _parse_trained_at(model_path),
        "model_fold_index": sidecar["fold_index"],
        "bar_time": bar_time,
    }
