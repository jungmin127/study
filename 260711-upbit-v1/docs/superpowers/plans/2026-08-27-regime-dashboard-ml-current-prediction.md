# 장세판별 대시보드 ML 현재예측 카드 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/regime` 대시보드의 "현재 예측" 영역에 저장된 LightGBM 모델의 예측을 규칙기반 카드 옆에 나란히 보여준다.

**Architecture:** 기존 `scripts/regime_ml_data.py`를 `engine/`으로 옮겨 백엔드가 자연스럽게 재사용할 수 있게 하고, 신규 `backend/regime_ml_service.py`가 `data/regime_ml_models/`의 최신 모델을 로드해 "지금" 시점 예측 하나만 계산한다. 신규 API 엔드포인트(`GET /api/v1/regime/ml-current-prediction`)와 신규 프론트 컴포넌트(`RegimeMlCurrentPrediction.tsx`)가 이를 규칙기반 카드 옆 grid에 배치한다.

**Tech Stack:** Python(FastAPI, LightGBM, pandas), TypeScript/React(Next.js). 기존 `engine/regime_ml_features.py`, `engine/regime_detector.py`, `backend/main.py`, `frontend/lib/api/*`, `frontend/components/RegimeCurrentPrediction.tsx` 재사용.

## Global Constraints

- 스펙: `docs/superpowers/specs/2026-08-27-regime-dashboard-ml-current-prediction-design.md`
- `minutes60`(1시간봉) 외 타임프레임 미지원 — 저장된 모델이 1시간봉 전용
- 정확도 리포트/과거 백테스트에 ML 포함하지 않음(인샘플 문제, 이번 계획 비범위)
- 모델 자동 재학습 없음 — `data/regime_ml_models/`의 기존 파일을 읽기만 함
- `engine/regime_detector.py`, 규칙기반 대시보드 로직 변경 없음
- **`market` 범주형 컬럼의 코드 인코딩이 학습 시와 추론 시 반드시 일치해야 함**(아래 Task 2 상세 참고) — 어긋나면 크래시 없이 조용히 틀린 예측이 나옴

---

## Task 1: `engine/regime_ml_data.py`로 이동

**Files:**
- Create: `engine/regime_ml_data.py` (기존 `scripts/regime_ml_data.py` 내용, 모듈 docstring의 파일 경로 표기만 갱신)
- Delete: `scripts/regime_ml_data.py`
- Modify: `scripts/train_regime_ml.py:32` (import 경로)
- Modify: `tests/test_regime_ml_data.py` (import 경로 3곳)

**Interfaces:**
- Consumes: 없음(기존 함수 시그니처 변경 없이 그대로 이동)
- Produces: `engine.regime_ml_data.load_market_training_data(market: str, timeframe: str, start: datetime, end: datetime) -> pd.DataFrame` (기존과 동일 시그니처, import 경로만 `engine.regime_ml_data`로 바뀜)

- [ ] **Step 1: 파일 이동**

`scripts/regime_ml_data.py`의 전체 내용을 `engine/regime_ml_data.py`로 복사하고, 모듈 docstring 첫 줄만 고친다:

```python
"""
engine/regime_ml_data.py

ML 장세 판별기 학습용 마켓별 데이터 로더. backend/main.py:_fetch_backtest_dataframe()의
...
```

(나머지 docstring 본문과 함수 구현은 원본과 완전히 동일 — 그대로 복사)

원본 `scripts/regime_ml_data.py`는 삭제한다.

- [ ] **Step 2: `scripts/train_regime_ml.py`의 import 수정**

`scripts/train_regime_ml.py:32`:
```python
# 변경 전
from scripts.regime_ml_data import load_market_training_data
# 변경 후
from engine.regime_ml_data import load_market_training_data
```

- [ ] **Step 3: `tests/test_regime_ml_data.py`의 import 수정**

파일 상단 docstring과 import 3곳을 갱신:
```python
"""
tests/test_regime_ml_data.py

engine.regime_ml_data.load_market_training_data()를 검증한다. backend/main.py의
...
"""
...
import engine.regime_ml_data as regime_ml_data
from binance_data_service import BinanceSymbolNotFoundError
from engine.regime_ml_data import load_market_training_data
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_regime_ml_data.py -v`
Expected: PASS (4 passed) — 기존과 동일 개수, import 경로만 바뀌었으므로 동작 변화 없음

- [ ] **Step 5: 전체 회귀 테스트 확인**

Run: `python -m pytest tests/ -v`
Expected: 전부 PASS — `scripts/train_regime_ml.py`의 import 변경으로 다른 테스트가 깨지지 않는지 확인(특히 `tests/test_train_regime_ml.py`)

- [ ] **Step 6: 커밋**

```bash
git add engine/regime_ml_data.py tests/test_regime_ml_data.py scripts/train_regime_ml.py
git rm scripts/regime_ml_data.py
git commit -m "refactor: regime_ml_data.py를 scripts/에서 engine/으로 이동 (백엔드 재사용 대비)"
```

---

## Task 2: `backend/regime_ml_service.py` — 현재예측 계산

**Files:**
- Create: `backend/regime_ml_service.py`
- Test: `tests/test_regime_ml_service.py`

**Interfaces:**
- Consumes:
  - `engine.regime_detector.half_life_bars_for_timeframe(timeframe: str) -> float`(기존)
  - `engine.regime_ml_data.load_market_training_data(market, timeframe, start, end) -> pd.DataFrame`(Task 1)
  - `engine.regime_ml_features.build_feature_matrix(df, market, half_life_bars) -> pd.DataFrame`(기존, market 컬럼은 `pd.Categorical([market]*len(df))`로 카테고리가 1개뿐인 상태로 반환됨에 주의)
- Produces:
  - `MODEL_DIR: Path` = `Path("data/regime_ml_models")`
  - `find_latest_model() -> tuple[Path, dict] | None`
  - `predict_current_ml_regime(market: str, timeframe: str) -> dict` — `{"predicted_category": str, "probs": dict[str, float], "model_trained_at": str, "model_fold_index": int}`

**⚠️ 핵심 위험 — market 범주형 코드 인코딩**: `scripts/train_regime_ml.py`의 학습 파이프라인은 3개 마켓(KRW-BTC/KRW-ETH/KRW-XRP)의 피처를 `pd.concat`한 뒤 `train_X["market"].astype("category")`로 재캐스팅한다. 이 시점에 pandas는 유니크값을 **알파벳순으로 정렬**해 카테고리 코드를 배정한다(실측 확인: KRW-BTC=0, KRW-ETH=1, KRW-XRP=2). 저장된 LightGBM 부스터는 이 정수 코드만 기억하지, 원래 문자열을 기억하지 않는다. 추론 시 `build_feature_matrix()`가 반환하는 `market` 컬럼은 **해당 마켓 하나짜리 카테고리**(예: `KRW-ETH`만)라서, 그대로 `.astype("category")`를 부르면 코드가 다시 0부터 배정되어(카테고리가 1개뿐이므로 무조건 0) 학습 때와 다른 마켓을 가리키는 것처럼 조용히 틀린다. **반드시 `pd.Categorical(values, categories=sorted(["KRW-BTC","KRW-ETH","KRW-XRP"]))`처럼 학습 때와 동일한 전체 카테고리 목록을 명시**해야 한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime_ml_service.py`:
```python
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_regime_ml_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.regime_ml_service'`

- [ ] **Step 3: 구현 작성**

`backend/regime_ml_service.py`:
```python
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

MODEL_DIR = Path("data/regime_ml_models")
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


def predict_current_ml_regime(market: str, timeframe: str) -> dict:
    """market의 가장 최근 봉 하나에 대한 ML 예측을 반환한다."""
    if timeframe != "minutes60":
        raise ValueError("ML 모델은 1시간봉(minutes60)으로만 학습되어 있습니다")

    found = find_latest_model()
    if found is None:
        raise FileNotFoundError(
            "학습된 ML 모델이 없습니다. scripts/train_regime_ml.py를 먼저 실행하세요"
        )
    model_path, sidecar = found

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=WARMUP_DAYS)
    df = load_market_training_data(market, timeframe, start, end)

    half_life_bars = half_life_bars_for_timeframe(timeframe)
    features_df = build_feature_matrix(df, market, half_life_bars)
    features_df = features_df.assign(
        market=pd.Categorical(features_df["market"], categories=sorted(_TRAINING_MARKETS))
    )
    last_row = features_df.iloc[[-1]]

    booster = lgb.Booster(model_file=str(model_path))
    probs_row = booster.predict(last_row)[0]
    classes: list[str] = sidecar["classes"]
    probs = {label: float(p) for label, p in zip(classes, probs_row)}
    predicted_category = max(probs, key=probs.get)

    return {
        "predicted_category": predicted_category,
        "probs": probs,
        "model_trained_at": _parse_trained_at(model_path),
        "model_fold_index": sidecar["fold_index"],
    }
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_regime_ml_service.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: 커밋**

```bash
git add backend/regime_ml_service.py tests/test_regime_ml_service.py
git commit -m "feat: ML 장세 판별기 현재예측 서비스 함수 추가 (저장된 모델 로드+추론)"
```

---

## Task 3: API 엔드포인트

**Files:**
- Modify: `backend/main.py` (import 1줄 + 엔드포인트 함수 1개 추가)
- Test: `tests/test_backend.py` (테스트 함수 추가)

**Interfaces:**
- Consumes: `backend.regime_ml_service.predict_current_ml_regime(market, timeframe) -> dict`(Task 2)
- Produces: `GET /api/v1/regime/ml-current-prediction?market=...&timeframe=...` — 200에 Task 2의 반환 dict 그대로, 400(timeframe 불일치/기타 ValueError), 404(모델 없음), 500(기타 RuntimeError)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_backend.py` 끝에 추가(기존 `test_regime_backtest_*` 테스트들 근처, `backend_module`/`_client` 헬퍼는 이미 파일 상단에 정의돼 있음):
```python
def test_regime_ml_current_prediction_returns_result(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    captured = {}

    def _fake_predict(market, timeframe):
        captured["args"] = (market, timeframe)
        return {
            "predicted_category": "횡보",
            "probs": {"급하락": 0.1, "완만하락": 0.2, "횡보": 0.3, "완만상승": 0.25, "급상승": 0.15},
            "model_trained_at": "2026-08-27T05:20:47+00:00",
            "model_fold_index": 5,
        }

    monkeypatch.setattr(backend_module, "predict_current_ml_regime", _fake_predict)

    resp = client.get(
        "/api/v1/regime/ml-current-prediction",
        params={"market": "KRW-ETH", "timeframe": "minutes60"},
    )

    assert resp.status_code == 200
    assert resp.json()["predicted_category"] == "횡보"
    assert captured["args"] == ("KRW-ETH", "minutes60")


def test_regime_ml_current_prediction_returns_400_for_wrong_timeframe(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    def _fake_predict(market, timeframe):
        raise ValueError("ML 모델은 1시간봉(minutes60)으로만 학습되어 있습니다")

    monkeypatch.setattr(backend_module, "predict_current_ml_regime", _fake_predict)

    resp = client.get(
        "/api/v1/regime/ml-current-prediction",
        params={"market": "KRW-ETH", "timeframe": "days"},
    )

    assert resp.status_code == 400
    assert "1시간봉" in resp.json()["detail"]


def test_regime_ml_current_prediction_returns_404_when_no_model(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    def _fake_predict(market, timeframe):
        raise FileNotFoundError("학습된 ML 모델이 없습니다. scripts/train_regime_ml.py를 먼저 실행하세요")

    monkeypatch.setattr(backend_module, "predict_current_ml_regime", _fake_predict)

    resp = client.get(
        "/api/v1/regime/ml-current-prediction",
        params={"market": "KRW-ETH", "timeframe": "minutes60"},
    )

    assert resp.status_code == 404
    assert "학습된 ML 모델이 없습니다" in resp.json()["detail"]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_backend.py -k regime_ml_current_prediction -v`
Expected: FAIL — `AttributeError: module 'backend.main' has no attribute 'predict_current_ml_regime'`

- [ ] **Step 3: 구현 작성**

`backend/main.py:72` 근처(`from backend.regime_service import evaluate_market` 바로 아래)에 추가:
```python
from backend.regime_ml_service import predict_current_ml_regime
```

`backend/main.py`의 `/api/v1/regime/backtest` 엔드포인트 바로 아래에 추가:
```python
@app.get("/api/v1/regime/ml-current-prediction")
def get_regime_ml_current_prediction_endpoint(
    market: str = Query(...),
    timeframe: str = Query(...),
) -> dict:
    try:
        return predict_current_ml_regime(market, timeframe)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_backend.py -k regime_ml_current_prediction -v`
Expected: PASS (3 passed)

- [ ] **Step 5: 전체 백엔드 회귀 테스트**

Run: `python -m pytest tests/ -v`
Expected: 전부 PASS

- [ ] **Step 6: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "feat: GET /api/v1/regime/ml-current-prediction 엔드포인트 추가"
```

---

## Task 4: 프론트엔드 타입/API클라이언트/컴포넌트

**Files:**
- Modify: `frontend/lib/types/eda.ts` (타입 추가)
- Modify: `frontend/lib/api/eda.ts` (API 함수 추가)
- Create: `frontend/components/RegimeMlCurrentPrediction.tsx`

**Interfaces:**
- Consumes: `GET /api/v1/regime/ml-current-prediction`(Task 3), `frontend/lib/api/client.ts:apiFetch`(기존), `frontend/lib/format.ts:formatDateTime`(기존)
- Produces: `RegimeMlCurrentPrediction` 컴포넌트 — `{ market: string; timeframe: string }` props

- [ ] **Step 1: 타입 추가**

`frontend/lib/types/eda.ts`에 `RegimeCategory` 타입 정의 바로 아래 추가:
```typescript
export interface MlCurrentPrediction {
  predicted_category: RegimeCategory;
  probs: Record<RegimeCategory, number>;
  model_trained_at: string;
  model_fold_index: number;
}
```

- [ ] **Step 2: API 클라이언트 함수 추가**

`frontend/lib/api/eda.ts`의 `getRegimeBacktest` 함수 바로 아래 추가:
```typescript
export function getRegimeMlCurrentPrediction(params: {
  market: string;
  timeframe: string;
}): Promise<MlCurrentPrediction> {
  const query = new URLSearchParams(params);
  return apiFetch<MlCurrentPrediction>(`/api/v1/regime/ml-current-prediction?${query.toString()}`);
}
```
(`MlCurrentPrediction`을 파일 상단 import에 추가)

- [ ] **Step 3: 컴포넌트 작성**

`frontend/components/RegimeMlCurrentPrediction.tsx`:
```tsx
'use client';

import { useEffect, useState } from 'react';
import type { MlCurrentPrediction, RegimeCategory } from '@/lib/types/eda';
import { ApiError } from '@/lib/api/client';
import { getRegimeMlCurrentPrediction } from '@/lib/api/eda';
import { formatDateTime } from '@/lib/format';

const CATEGORY_ORDER: RegimeCategory[] = ['급상승', '완만상승', '횡보', '완만하락', '급하락'];

function categoryVarName(label: RegimeCategory): string {
  switch (label) {
    case '급상승':
      return '--regime-surge-up';
    case '완만상승':
      return '--regime-mild-up';
    case '횡보':
      return '--marker-boundary';
    case '완만하락':
      return '--regime-mild-down';
    case '급하락':
      return '--regime-surge-down';
  }
}

interface RegimeMlCurrentPredictionProps {
  market: string;
  timeframe: string;
}

export default function RegimeMlCurrentPrediction({ market, timeframe }: RegimeMlCurrentPredictionProps) {
  const [data, setData] = useState<MlCurrentPrediction | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (timeframe !== 'minutes60' || !market) {
      setData(null);
      setError(null);
      return;
    }
    setLoading(true);
    setError(null);
    getRegimeMlCurrentPrediction({ market, timeframe })
      .then(setData)
      .catch((err) => {
        setError(err instanceof ApiError ? err.message : 'ML 예측을 불러오지 못했습니다.');
        setData(null);
      })
      .finally(() => setLoading(false));
  }, [market, timeframe]);

  return (
    <div className="rounded-xl border p-6 shadow-sm">
      <h2 className="mb-3 text-sm font-semibold">ML 현재예측</h2>
      {timeframe !== 'minutes60' ? (
        <p className="text-sm text-muted-foreground">ML은 1시간봉 전용입니다.</p>
      ) : loading ? (
        <p className="text-sm text-muted-foreground">불러오는 중...</p>
      ) : error ? (
        <p className="text-sm text-muted-foreground">{error}</p>
      ) : data ? (
        <>
          <div className="mb-3 flex items-baseline gap-2">
            <span className="text-2xl font-bold">{data.predicted_category}</span>
            <span className="text-sm text-muted-foreground">
              확신도 {(data.probs[data.predicted_category] * 100).toFixed(1)}%
            </span>
          </div>
          <div className="mb-3 space-y-1.5">
            {CATEGORY_ORDER.map((label) => (
              <div key={label} className="flex items-center gap-2 text-xs">
                <span className="w-14 shrink-0 text-muted-foreground">{label}</span>
                <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
                  <div
                    className="h-full rounded-full"
                    style={{
                      width: `${(data.probs[label] * 100).toFixed(1)}%`,
                      backgroundColor: `var(${categoryVarName(label)})`,
                    }}
                  />
                </div>
                <span className="w-10 shrink-0 text-right tabular-nums">
                  {(data.probs[label] * 100).toFixed(1)}%
                </span>
              </div>
            ))}
          </div>
          <p className="text-xs text-muted-foreground">
            {formatDateTime(data.model_trained_at)} 학습, fold {data.model_fold_index} 모델 기준.
          </p>
        </>
      ) : null}
    </div>
  );
}
```

- [ ] **Step 4: 타입체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 5: 커밋**

```bash
git add frontend/lib/types/eda.ts frontend/lib/api/eda.ts frontend/components/RegimeMlCurrentPrediction.tsx
git commit -m "feat: ML 현재예측 프론트 컴포넌트/API클라이언트/타입 추가"
```

---

## Task 5: 대시보드 배치 + 브라우저 검증

**Files:**
- Modify: `frontend/components/RegimeDashboard.tsx`

**Interfaces:**
- Consumes: `RegimeMlCurrentPrediction`(Task 4), `RegimeCurrentPrediction`(기존)
- Produces: 없음(최종 페이지 조립)

- [ ] **Step 1: `RegimeDashboard.tsx` 수정**

`frontend/components/RegimeDashboard.tsx`의 import에 추가:
```tsx
import RegimeMlCurrentPrediction from '@/components/RegimeMlCurrentPrediction';
```

기존
```tsx
<RegimeCurrentPrediction result={result} market={market} timeframe={timeframe} />
```
줄을 아래로 교체:
```tsx
<div className="grid gap-4 md:grid-cols-2">
  <RegimeCurrentPrediction result={result} market={market} timeframe={timeframe} />
  <RegimeMlCurrentPrediction market={market} timeframe={timeframe} />
</div>
```

- [ ] **Step 2: 타입체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 3: 브라우저로 실제 동작 확인**

백엔드/프론트 dev 서버를 각각 실행(`uvicorn backend.main:app --reload`, `cd frontend && npm run dev`)한 뒤 브라우저로 `/regime` 탭에서:
1. KRW-ETH, 1시간봉으로 조회 → 규칙기반 카드와 ML 카드가 나란히 보이는지, ML 카드에 예측 카테고리+확신도+5개 막대+"~학습, fold N 모델 기준" 텍스트가 뜨는지 확인
2. 봉타입을 `일봉` 등 다른 값으로 바꿔 조회 → ML 카드가 "ML은 1시간봉 전용입니다"로 바뀌는지 확인
3. (선택, 재현 어려우면 생략) `data/regime_ml_models/`를 임시로 비워보고 새로고침 → "학습된 ML 모델이 없습니다" 등 에러 메시지가 카드에 뜨는지, 페이지 전체가 깨지지 않는지 확인 후 파일 원복

문제 발견 시 해당 파일을 고치고 1~3 다시 확인.

- [ ] **Step 4: 커밋**

```bash
git add frontend/components/RegimeDashboard.tsx
git commit -m "feat: /regime 대시보드에 ML 현재예측 카드를 규칙기반 카드 옆에 배치"
```

## 비범위 확인 (실행 중 벗어나지 않도록)

- 정확도 리포트/confusion matrix/과거 백테스트에 ML 포함 금지
- 모델 자동 재학습 로직 작성 금지
- `minutes60` 외 타임프레임에 대한 ML 계산 시도 금지(명시적으로 거부만)
- `engine/regime_detector.py`, 규칙기반 대시보드 컴포넌트(`RegimeChart`, `RegimeAccuracyReport`) 수정 금지
