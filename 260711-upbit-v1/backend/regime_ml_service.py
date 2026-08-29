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
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import lightgbm as lgb
import pandas as pd

from engine.regime_math import half_life_bars_for_timeframe
from engine.regime_ml_constants import TRAINING_MARKETS
from engine.regime_ml_data import load_market_training_data
from engine.regime_ml_features import build_feature_matrix

MODEL_DIR = Path(__file__).parent.parent / "data" / "regime_ml_models"
REPO_ROOT = Path(__file__).resolve().parent.parent

# OBV 스케일 불일치는 engine/regime_ml_features.py:build_feature_matrix()가 OBV를
# 피처에서 제외해 해결했다(OBV_ROC로 대체) — WARMUP_DAYS=30이 짧아도 더 이상
# 학습/추론 간 스케일이 어긋나지 않는다.
WARMUP_DAYS = 30
_TIMESTAMP_PATTERN = re.compile(r"regime_ml_(\d{8}T\d{6}Z)")

# 2026-08-29 마켓 확장 이전에 학습된 모든 모델은 정확히 이 3개 마켓으로만
# 학습됐다. "markets" 키가 없는 사이드카는 전부 그 시절 모델이라는 뜻이므로,
# 현재(더 커진) TRAINING_MARKETS로 폴백하면 그 모델이 실제로 학습한 적 없는
# 마켓까지 조용히 통과시켜버린다(바로 이 폴백이 막으려는 버그).
_LEGACY_SIDECAR_MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-XRP"]


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


def _find_serving_model() -> tuple[Path, dict] | None:
    """예측에 실제로 쓸 모델을 고른다. 배포 마커(.last_deployed.json)가 가리키는
    모델이 있으면 그걸 최우선으로 쓴다 — "배포" 버튼이 실제 서빙 모델을 결정해야
    하고, 그 뒤에 로컬에서 새로 학습한(파일명이 더 최신인) 모델이 조용히 그걸
    이겨서는 안 된다. 마커가 없거나(최초 설치 등) 마커가 가리키는 파일이 없으면
    find_latest_model()로 폴백한다."""
    marker = get_last_deployed_marker()
    if marker is not None:
        txt_path = MODEL_DIR / f"{marker['model_timestamp']}.txt"
        json_path = MODEL_DIR / f"{marker['model_timestamp']}.json"
        if txt_path.exists() and json_path.exists():
            return txt_path, json.loads(json_path.read_text(encoding="utf-8"))
    return find_latest_model()


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
    if market not in TRAINING_MARKETS:
        raise ValueError(
            f"이 모델은 {', '.join(TRAINING_MARKETS)}로만 학습되어 있습니다"
        )

    found = _find_serving_model()
    if found is None:
        raise FileNotFoundError(
            "학습된 ML 모델이 없습니다. scripts/train_regime_ml.py를 먼저 실행하세요"
        )
    model_path, sidecar = found

    serving_markets = sidecar.get("markets", _LEGACY_SIDECAR_MARKETS)
    if market not in serving_markets:
        raise ValueError(
            f"현재 배포된 모델은 {', '.join(serving_markets)}로만 학습되어 있습니다. "
            "재학습 후 배포하면 새 마켓이 반영됩니다."
        )

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=WARMUP_DAYS)
    df = load_market_training_data(market, timeframe, start, end)
    bar_time = _to_utc_iso(df["candle_time"].iloc[-1])

    half_life_bars = half_life_bars_for_timeframe(timeframe)
    features_df = build_feature_matrix(df, market, half_life_bars)
    # 학습 시 train_X["market"].astype("category")가 TRAINING_MARKETS의 알파벳순으로
    # 카테고리 코드(0/1/2)를 배정했고, 저장된 부스터는 그 정수 코드만 기억한다 — 추론 시
    # 이 전체 목록을 categories=로 명시하지 않으면(예: 1행짜리 프레임에 그냥
    # astype("category")를 부르면) 카테고리가 1개뿐이라 코드가 다시 0으로 배정돼
    # 학습 때와 다른 마켓을 가리키는 것처럼 조용히 오작동한다.
    features_df = features_df.assign(
        market=pd.Categorical(features_df["market"], categories=sorted(TRAINING_MARKETS))
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
        "model_performance": sidecar.get("performance"),
    }


def get_last_deployed_marker() -> dict | None:
    """가장 최근에 배포에 성공한 모델의 타임스탬프를 담은 로컬 마커. 참고용
    표시일 뿐 신뢰 소스는 아니다(예: AWS에서 수동으로 모델을 되돌리면 이 마커와
    실제 배포 상태가 어긋날 수 있다 — 그런 동기화까지는 비범위)."""
    marker_path = MODEL_DIR / ".last_deployed.json"
    if not marker_path.exists():
        return None
    return json.loads(marker_path.read_text(encoding="utf-8"))


def set_last_deployed_marker(model_timestamp: str) -> None:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    marker_path = MODEL_DIR / ".last_deployed.json"
    payload = {
        "model_timestamp": model_timestamp,
        "deployed_at": datetime.now(timezone.utc).isoformat(),
    }
    marker_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def list_trained_models() -> list[dict]:
    """data/regime_ml_models/의 모든 학습 이력을 최신순으로 반환한다. .json
    사이드카가 있는데 .txt 짝이 없는 항목(불완전한 저장, find_latest_model()과
    같은 기준)은 건너뛴다."""
    if not MODEL_DIR.exists():
        return []

    deployed_marker = get_last_deployed_marker()
    deployed_timestamp = deployed_marker["model_timestamp"] if deployed_marker else None

    models: list[dict] = []
    json_files = sorted(MODEL_DIR.glob("regime_ml_*.json"), reverse=True)
    for json_path in json_files:
        txt_path = json_path.with_suffix(".txt")
        if not txt_path.exists():
            continue
        sidecar = json.loads(json_path.read_text(encoding="utf-8"))
        model_timestamp = json_path.stem
        models.append({
            "model_timestamp": model_timestamp,
            "trained_at": _parse_trained_at(txt_path),
            "performance": sidecar.get("performance"),
            "is_deployed": model_timestamp == deployed_timestamp,
        })
    return models


def deploy_model(model_timestamp: str) -> None:
    """model_timestamp(예: "regime_ml_20260827T223633Z")에 해당하는 모델을
    scripts/push_regime_ml_model.sh로 AWS 라이브 서버에 배포한다. 성공하면
    마지막 배포 마커를 갱신한다."""
    txt_path = MODEL_DIR / f"{model_timestamp}.txt"
    json_path = MODEL_DIR / f"{model_timestamp}.json"
    if not txt_path.exists() or not json_path.exists():
        raise FileNotFoundError(f"모델을 찾을 수 없습니다: {model_timestamp}")

    script_path = REPO_ROOT / "scripts" / "push_regime_ml_model.sh"
    bash_path = shutil.which("bash")
    if bash_path is None:
        raise RuntimeError("bash 실행 파일을 찾을 수 없습니다(Git Bash가 설치되어 있는지 확인하세요).")
    try:
        result = subprocess.run(
            [bash_path, str(script_path), model_timestamp],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("배포 스크립트가 120초 내에 끝나지 않아 중단되었습니다") from exc
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "배포 스크립트 실행 실패")

    set_last_deployed_marker(model_timestamp)
