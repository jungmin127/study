# 장세 판별 규칙기반 제거(E) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 장세 판별 규칙기반 시스템(engine/regime_detector.py, backend/regime_service.py, scripts/regime_backtest.py, 관련 프론트 4개 컴포넌트)을 삭제하고, ML 파이프라인이 실제로 의존하는 함수 4개만 새 `engine/regime_math.py`로 이관해 ML 전용 체제로 전환한다.

**Architecture:** 백엔드는 마이그레이션과 삭제가 원자적이다(`_ewm_std_series`가 이관 대상 `ewm_volatility`와 삭제 대상 `compute_regime_probs_series` 양쪽에서 쓰이므로, 중간에 파일을 반쪽만 남겨두면 빌드가 깨진다) — Task 1이 이관+삭제를 한 번에 처리한다. 프론트는 기존 ML API(변경 없음)에만 의존하므로 별도 원자적 단위인 Task 2로 분리한다.

**Tech Stack:** Python(pytest), FastAPI, Next.js/React/TypeScript.

## Global Constraints

- 이관 대상은 4개: `half_life_bars_for_timeframe`(+ 상수 `HALF_LIFE_DAYS`), `ewm_volatility`, `ewm_volatility`가 내부적으로 쓰는 private 헬퍼 `_ewm_std_series`, `N_MULTIPLIER`(값 2.5, 원래 `backend/regime_service.py`).
- 이관 목적지: 새 `engine/regime_math.py` 모듈(신설).
- `engine/regime_detector.py`는 이관 후 파일 전체를 삭제한다(트리밍 아님 — 남는 코드가 없음).
- 새 API 엔드포인트를 추가하지 않는다. 기존 `GET /api/v1/regime/ml-current-prediction`은 변경하지 않는다.
- 프론트 `RegimeMlCurrentPrediction.tsx`/`frontend/lib/types/eda.ts`의 `RegimeCategory`/`MlCurrentPrediction`/`MlFoldPerformance`/`MlModelPerformance`는 변경하지 않는다(ML 카드가 계속 씀).
- 새 `RegimeDashboard.tsx`는 셀렉터 자체를 3마켓·1시간봉으로 제한하지 않는다(전체 마켓/전체 타임프레임 노출, 학습 안 된 조합은 `RegimeMlCurrentPrediction`이 이미 갖고 있는 안내문으로 처리).
- 전체 설계 근거는 `docs/superpowers/specs/2026-08-28-regime-rule-based-removal-design.md` 참고.

---

## Task 1: 백엔드 — engine/regime_math.py 이관 + 규칙기반 전체 삭제

**Files:**
- Create: `engine/regime_math.py`
- Create: `tests/test_regime_math.py`
- Modify: `backend/regime_ml_service.py`
- Modify: `scripts/train_regime_ml.py`
- Modify: `engine/regime_ml_labels.py`
- Modify: `tests/test_regime_ml_labels.py`
- Modify: `tests/test_train_regime_ml.py`
- Modify: `engine/regime_ml_constants.py`
- Modify: `engine/regime_features.py`
- Modify: `backend/main.py`
- Modify: `tests/test_backend.py`
- Delete: `engine/regime_detector.py`
- Delete: `tests/test_regime_detector.py`
- Delete: `backend/regime_service.py`
- Delete: `tests/test_regime_service.py`
- Delete: `scripts/regime_backtest.py`

**Interfaces:**
- Produces: `engine.regime_math.half_life_bars_for_timeframe(timeframe: str) -> float`, `engine.regime_math.ewm_volatility(returns: pd.Series, half_life_bars: float) -> float`, `engine.regime_math.N_MULTIPLIER: float = 2.5` — Task 2(프론트)는 이 모듈을 직접 쓰지 않지만, 이 태스크 안의 `backend/regime_ml_service.py`/`scripts/train_regime_ml.py`/`engine/regime_ml_labels.py`가 소비한다. `GET /api/v1/regime/ml-current-prediction`의 응답 형태(Task 2가 실제로 의존하는 유일한 계약)는 이 태스크에서 변경하지 않는다.

- [ ] **Step 1: `tests/test_regime_math.py` 작성(아직 존재하지 않는 모듈을 import — 실패 예상)**

```python
"""
tests/test_regime_math.py

engine/regime_math.py — half_life_bars_for_timeframe/ewm_volatility 검증.
engine/regime_detector.py(규칙기반, E 작업으로 삭제됨)에 있던 동명 함수 테스트를
그대로 옮겨왔다. 이 두 함수는 ML 파이프라인(engine/regime_ml_labels.py,
backend/regime_ml_service.py, scripts/train_regime_ml.py)이 실제로 의존한다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine.regime_math import ewm_volatility, half_life_bars_for_timeframe


def test_half_life_bars_for_timeframe_days_is_one():
    assert half_life_bars_for_timeframe("days") == pytest.approx(1.0)


def test_half_life_bars_for_timeframe_minutes60_is_24():
    assert half_life_bars_for_timeframe("minutes60") == pytest.approx(24.0)


def test_half_life_bars_for_timeframe_minutes15_is_96():
    assert half_life_bars_for_timeframe("minutes15") == pytest.approx(96.0)


def test_ewm_volatility_of_constant_returns_is_near_zero():
    """수익률이 일정하면 지수가중 표준편차는 0에 가까워야 한다(EWMA 절댓값평균이던
    구버전에서는 이 값이 0.01이 나왔지만, 삼각부등식으로 score가 [-1, 1]에 갇히는 버그의
    원인이었다 — 표준편차 기반으로 바뀐 지금은 변동성이 없는 시계열의 분산은 0이 맞다)."""
    returns = pd.Series([0.01] * 30)
    vol = ewm_volatility(returns, half_life_bars=5.0)
    assert vol == pytest.approx(0.0, abs=1e-9)


def test_ewm_volatility_matches_pandas_ewm_std():
    """ewm_volatility가 pandas의 지수가중 표준편차와 동일한 값을 내는지 직접 대조한다."""
    rng = np.random.default_rng(seed=42)
    returns = pd.Series(rng.normal(loc=0.0, scale=0.02, size=30))
    vol = ewm_volatility(returns, half_life_bars=5.0)
    expected = float(returns.ewm(halflife=5.0).std().iloc[-1])
    assert vol == pytest.approx(expected, rel=1e-9)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_regime_math.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.regime_math'`

- [ ] **Step 3: `engine/regime_math.py` 작성**

```python
"""
engine/regime_math.py

장세 판별 ML 파이프라인이 쓰는 순수 수학/시간프레임 헬퍼. 원래
engine/regime_detector.py(규칙기반 판별기)의 일부였으나, 규칙기반 시스템이
E 작업(2026-08-28)으로 삭제되면서 ML이 실제로 의존하는 이 부분만 이 모듈로
옮겨졌다. 설계 문서: docs/superpowers/specs/2026-08-28-regime-rule-based-removal-design.md
"""
from __future__ import annotations

import pandas as pd

from upbit_data_service import timeframe_duration

HALF_LIFE_DAYS = 1.0
N_MULTIPLIER = 2.5


def half_life_bars_for_timeframe(timeframe: str) -> float:
    """전략의 timeframe(예: 'minutes60', 'days')에서 HALF_LIFE_DAYS에 해당하는 봉 수를
    환산한다. 타임프레임이 달라도 체감 반응속도가 동일하게 유지된다."""
    bar_seconds = timeframe_duration(timeframe).total_seconds()
    return HALF_LIFE_DAYS * 86400.0 / bar_seconds


def _ewm_std_series(returns: pd.Series, half_life_bars: float) -> pd.Series:
    """수익률의 지수가중 표준편차 시계열(변동성 계산 전용) — ewm_volatility의 구현
    디테일."""
    return returns.ewm(halflife=half_life_bars).std()


def ewm_volatility(returns: pd.Series, half_life_bars: float) -> float:
    """수익률의 지수가중 표준편차(가장 최근 값) — 변동성 정규화용.
    분자(모멘텀=EWMA 평균)와 분모가 서로 다른 통계량이어야 score가 카테고리 대표값
    ±2.0(급상승/급하락)에 실제로 도달할 수 있다(EWMA 절댓값평균을 쓰면 삼각부등식으로
    score가 [-1, 1]에 갇히는 버그가 있었다 — 규칙기반 판별기 시절 Task 3 최종리뷰에서
    발견)."""
    return float(_ewm_std_series(returns, half_life_bars).iloc[-1])
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_regime_math.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: `backend/regime_ml_service.py`의 import 경로 갱신**

Old:
```python
from engine.regime_detector import half_life_bars_for_timeframe
```

New:
```python
from engine.regime_math import half_life_bars_for_timeframe
```

- [ ] **Step 6: `scripts/train_regime_ml.py`의 import 경로 갱신**

Old:
```python
from backend.regime_service import N_MULTIPLIER
from engine.regime_detector import half_life_bars_for_timeframe
```

New:
```python
from engine.regime_math import N_MULTIPLIER, half_life_bars_for_timeframe
```

(이 두 줄은 파일 상단 import 블록에서 서로 떨어진 위치에 있다 — 각각 원래 있던
줄을 그대로 찾아서 첫 번째 줄은 삭제하고, 두 번째 줄만 위 New 내용으로 교체한다.)

- [ ] **Step 7: `engine/regime_ml_labels.py`의 import 경로 갱신 + docstring 재작성**

Old(파일 전체 docstring, 1~9행):
```python
"""
engine/regime_ml_labels.py

장세 판별 ML 분류기의 레이블(정답 카테고리)을 만든다. 정규화 실현수익률 정의는
backend/regime_service.py:evaluate_market()의 100~119행 루프와 동일하다(같은 잣대로
규칙기반과 ML을 비교하기 위함) — 카테고리 경계만 고정값이 아니라 fold별 훈련구간
분위수로 계산한다는 점이 다르다. 설계 문서:
docs/superpowers/specs/2026-08-27-regime-detector-ml-classifier-design.md
"""
from __future__ import annotations

import pandas as pd

from engine.regime_detector import ewm_volatility
```

New:
```python
"""
engine/regime_ml_labels.py

장세 판별 ML 분류기의 레이블(정답 카테고리)을 만든다. 정규화 실현수익률(다음 n_bars
평균수익률을 이후 EWM변동성으로 정규화한 값)은 과거 규칙기반 판별기(E 작업으로
2026-08-28 삭제됨)가 쓰던 것과 같은 정규화 방식이다 — 카테고리 경계만 고정값이 아니라
fold별 훈련구간 분위수로 계산한다는 점이 다르다. 설계 문서:
docs/superpowers/specs/2026-08-27-regime-detector-ml-classifier-design.md
"""
from __future__ import annotations

import pandas as pd

from engine.regime_math import ewm_volatility
```

- [ ] **Step 8: `tests/test_regime_ml_labels.py`의 import 경로 갱신 + docstring 재작성**

Old(1~14행):
```python
"""
tests/test_regime_ml_labels.py

engine.regime_ml_labels의 레이블 생성 함수를 검증한다. compute_normalized_realized_series는
backend/regime_service.py:evaluate_market()의 정규화 실현수익률 루프(100~119행)와 동일한
값을 내야 한다(같은 잣대로 규칙기반과 비교하기 위함).
"""
from __future__ import annotations

import pandas as pd
import pytest

from engine.regime_detector import ewm_volatility
```

New:
```python
"""
tests/test_regime_ml_labels.py

engine.regime_ml_labels의 레이블 생성 함수를 검증한다. compute_normalized_realized_series는
과거 규칙기반 판별기(E 작업으로 2026-08-28 삭제됨)가 쓰던 것과 같은 정규화 실현수익률
공식(다음 n_bars 평균수익률 / 이후 EWM변동성)을 그대로 따른다.
"""
from __future__ import annotations

import pandas as pd
import pytest

from engine.regime_math import ewm_volatility
```

- [ ] **Step 9: import 경로 갱신 후 ML 관련 테스트 통과 확인(회귀 없음)**

Run: `python -m pytest tests/test_regime_ml_labels.py tests/test_train_regime_ml.py tests/test_regime_ml_service.py -v`
Expected: PASS (전체 — 이 시점엔 아직 engine/regime_detector.py를 지우지 않았으므로
기존 규칙기반 테스트도 그대로 통과해야 함)

- [ ] **Step 10: `engine/regime_detector.py` 파일 삭제**

```bash
git rm engine/regime_detector.py
```

- [ ] **Step 11: `tests/test_regime_detector.py` 파일 삭제**

이 파일의 테스트 중 살아남는 5개(half_life_bars_for_timeframe 3개 + ewm_volatility
2개)는 이미 Step 1에서 `tests/test_regime_math.py`로 옮겨졌다. 나머지(약 20개)는
삭제되는 `_softmax_categorize`/`compute_regime_probs`/`compute_regime_probs_series`/
`classify_score_to_category`를 검증하던 것들이라 함께 삭제한다.

```bash
git rm tests/test_regime_detector.py
```

- [ ] **Step 12: `backend/regime_service.py` 파일 삭제**

`N_MULTIPLIER`는 Step 3에서 이미 `engine/regime_math.py`로 이관 완료했다.

```bash
git rm backend/regime_service.py
```

- [ ] **Step 13: `tests/test_regime_service.py` 파일 삭제**

```bash
git rm tests/test_regime_service.py
```

- [ ] **Step 14: `scripts/regime_backtest.py` 파일 삭제**

```bash
git rm scripts/regime_backtest.py
```

- [ ] **Step 15: `backend/main.py`에서 규칙기반 백테스트 엔드포인트 제거**

Old(72행, import):
```python
from backend.regime_service import evaluate_market
```

New: 이 줄을 완전히 삭제(다음 줄인 `from backend.regime_ml_service import predict_current_ml_regime`는 유지).

Old(600~617행, 엔드포인트 전체):
```python
@app.get("/api/v1/regime/backtest")
def get_regime_backtest_endpoint(
    market: str = Query(...),
    timeframe: str = Query(...),
    start: str = Query(...),
    end: str = Query(...),
) -> dict:
    try:
        start_dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_dt = datetime.strptime(end, "%Y-%m-%d").replace(
            hour=23, minute=59, second=59, tzinfo=timezone.utc
        )
        return evaluate_market(market, timeframe, start_dt, end_dt)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/v1/regime/ml-current-prediction")
```

New(엔드포인트 함수 전체 삭제, `/regime/ml-current-prediction` 엔드포인트와 그
바로 위 함수 `refresh_trend_segments_endpoint`는 그대로 유지):
```python
@app.get("/api/v1/regime/ml-current-prediction")
```

(즉 `def get_regime_backtest_endpoint(...):` 함수 전체와 그 앞뒤 빈 줄 2개를
제거하고, `refresh_trend_segments_endpoint`의 마지막 줄 바로 다음에 2개의 빈
줄을 두고 `@app.get("/api/v1/regime/ml-current-prediction")`가 오도록 한다.)

- [ ] **Step 16: `tests/test_backend.py`에서 규칙기반 백테스트 테스트 4개 삭제**

현재 파일에서 `test_live_strategy_replace_conflict_returns_409` 테스트 바로 다음,
`test_regime_ml_current_prediction_returns_result` 테스트 바로 전에 아래 4개
함수가 순서대로 위치한다(line ~2883–2955). 이 4개 함수 전체와 그 뒤에 오는 빈 줄
2개(다음 줄이 바로 `def test_regime_ml_current_prediction_returns_result`가
되도록)를 삭제한다. 이 파일의 다른 테스트(`test_regime_ml_current_prediction_*`
4개 등)는 그대로 유지 — ML 엔드포인트 테스트라 이번 삭제와 무관하다.

Old(삭제할 전체 블록, `test_live_strategy_replace_conflict_returns_409`의 마지막
줄 `assert resp.status_code == 409` 바로 다음부터 `test_regime_ml_current_prediction_returns_result`
정의 직전까지):
```python


def test_regime_backtest_returns_evaluated_result(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    captured = {}

    def _fake_evaluate_market(market, timeframe, start, end):
        captured["args"] = (market, timeframe, start, end)
        return {
            "half_life_bars": 24.0, "n_bars": 60, "candles": [],
            "confusion": {}, "actual_totals": {}, "correlation": None,
            "current_prediction": None,
        }

    monkeypatch.setattr(backend_module, "evaluate_market", _fake_evaluate_market)

    resp = client.get(
        "/api/v1/regime/backtest",
        params={"market": "KRW-BTC", "timeframe": "minutes60", "start": "2026-01-01", "end": "2026-01-31"},
    )

    assert resp.status_code == 200
    assert resp.json() == {
        "half_life_bars": 24.0, "n_bars": 60, "candles": [],
        "confusion": {}, "actual_totals": {}, "correlation": None,
        "current_prediction": None,
    }
    market, timeframe, start, end = captured["args"]
    assert market == "KRW-BTC"
    assert timeframe == "minutes60"


def test_regime_backtest_returns_400_when_evaluate_market_raises_value_error(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    def _fake_evaluate_market(market, timeframe, start, end):
        raise ValueError("요청한 기간의 캔들 수가 너무 많습니다")

    monkeypatch.setattr(backend_module, "evaluate_market", _fake_evaluate_market)

    resp = client.get(
        "/api/v1/regime/backtest",
        params={"market": "KRW-BTC", "timeframe": "minutes1", "start": "2020-01-01", "end": "2026-01-01"},
    )

    assert resp.status_code == 400
    assert "너무 많습니다" in resp.json()["detail"]


def test_regime_backtest_returns_500_when_evaluate_market_raises_runtime_error(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    def _fake_evaluate_market(market, timeframe, start, end):
        raise RuntimeError("업비트 API 호출 실패")

    monkeypatch.setattr(backend_module, "evaluate_market", _fake_evaluate_market)

    resp = client.get(
        "/api/v1/regime/backtest",
        params={"market": "KRW-BTC", "timeframe": "minutes60", "start": "2026-01-01", "end": "2026-01-31"},
    )

    assert resp.status_code == 500
    assert "업비트 API 호출 실패" in resp.json()["detail"]


def test_regime_backtest_returns_400_for_malformed_start_date(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    resp = client.get(
        "/api/v1/regime/backtest",
        params={"market": "KRW-BTC", "timeframe": "minutes60", "start": "not-a-date", "end": "2026-01-31"},
    )

    assert resp.status_code == 400


def test_regime_ml_current_prediction_returns_result(monkeypatch, tmp_path):
```

New(4개 함수와 뒤따르던 빈 줄 2개를 제거 — 앞의 빈 줄 2개는 그대로 남아 있으므로
결과적으로 두 테스트 사이에 빈 줄 2개만 남는다):
```python


def test_regime_ml_current_prediction_returns_result(monkeypatch, tmp_path):
```

- [ ] **Step 17: 규칙기반 삭제 후 백엔드 테스트 통과 확인**

Run: `python -m pytest tests/test_backend.py tests/test_regime_ml_service.py tests/test_regime_math.py -v`
Expected: PASS (전체). `tests/test_regime_detector.py`/`tests/test_regime_service.py`는
Step 11/13에서 삭제했으므로 이 커맨드에 포함되지 않는다.

- [ ] **Step 18: `scripts/train_regime_ml.py`의 규칙기반 비교 안내문 제거**

Old(모듈 docstring, 1~10행):
```python
"""
scripts/train_regime_ml.py

장세 판별 ML 전환 — LightGBM 학습+워크포워드 검증 파이프라인. scripts/regime_backtest.py
(규칙기반 검증 CLI)와 나란히 비교할 수 있도록 같은 콘솔 리포트 형식(카테고리별 hit-rate/
confusion matrix/상관계수)을 쓴다. 설계 문서:
docs/superpowers/specs/2026-08-27-regime-detector-ml-classifier-design.md

Run: PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/train_regime_ml.py
"""
```

New:
```python
"""
scripts/train_regime_ml.py

장세 판별 ML 전환 — LightGBM 학습+워크포워드 검증 파이프라인. 카테고리별
hit-rate/confusion matrix/상관계수를 콘솔에 리포트한다. 설계 문서:
docs/superpowers/specs/2026-08-27-regime-detector-ml-classifier-design.md

Run: PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/train_regime_ml.py
"""
```

Old(`run_training()` 내부, half_life_bars 출력 직후 콘솔 경고 블록 — 현재
`print(f"half_life_bars={half_life_bars:.1f}, n_bars={n_bars}, timeframe={timeframe}")`
바로 다음):
```python
    print(f"half_life_bars={half_life_bars:.1f}, n_bars={n_bars}, timeframe={timeframe}")
    print(
        "  [주의] 이 스크립트의 hit-rate/confusion matrix는 fold별 학습구간 분위수"
        "(2%/16%/84%/98%)로 카테고리 경계를 정합니다. scripts/regime_backtest.py는"
        " 고정 임계값(CATEGORY_REFERENCE_SCORES 중간값)을 씁니다 — 두 스크립트의"
        " hit-rate/confusion 숫자는 직접 비교하지 마세요. 상관계수(correlation)는"
        " 두 스크립트가 동일한 방식으로 계산하므로, 이것이 비교에 쓸 지표입니다."
    )

    market_frames: dict[str, tuple[pd.Series, pd.DataFrame, pd.Series]] = {}
```

New:
```python
    print(f"half_life_bars={half_life_bars:.1f}, n_bars={n_bars}, timeframe={timeframe}")

    market_frames: dict[str, tuple[pd.Series, pd.DataFrame, pd.Series]] = {}
```

Old(`_print_confusion_grid` docstring):
```python
def _print_confusion_grid(confusion: dict[str, dict[str, int]]) -> None:
    """scripts/regime_backtest.py의 confusion matrix 출력 형식(행=예측, 열=실제)을
    그대로 따른다 — 두 스크립트를 나란히 읽을 때 레이아웃이 동일해야 한다."""
```

New:
```python
def _print_confusion_grid(confusion: dict[str, dict[str, int]]) -> None:
    """confusion matrix를 행=예측, 열=실제 형식으로 출력한다."""
```

- [ ] **Step 19: `tests/test_train_regime_ml.py`에서 사라진 안내문을 검증하던 단언 제거 + 테스트 리네임**

Old:
```python
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
```

New:
```python
def test_run_training_prints_aggregate_summary_after_folds(tmp_path, monkeypatch, capsys):
    """Finding 2b: 모든 fold 리포트 이후에 "전체 fold 합산" 블록이 한 번 더
    찍히는지(순서까지) 확인한다."""
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
    assert "상관계수" in captured
    assert "전체 fold 합산" in captured

    last_fold_marker = f"=== fold {reports[-1]['fold_index']}"
    assert captured.index(last_fold_marker) < captured.index("전체 fold 합산")
```

- [ ] **Step 20: `engine/regime_ml_constants.py`에서 regime_backtest.py 언급 제거**

Old(전체 docstring):
```python
"""
engine/regime_ml_constants.py

장세 판별 ML 파이프라인 전체(학습+추론)가 공유하는 상수. 학습 스크립트
(scripts/train_regime_ml.py)와 추론 서비스(backend/regime_ml_service.py)가 서로
다른 마켓 목록을 갖게 되는 걸 막기 위해 단일 소스로 뽑았다. 프론트엔드
(frontend/components/RegimeMlCurrentPrediction.tsx)는 이 값을 API로 받지 않고
하드코딩된 배열을 따로 유지하며, tests/test_regime_ml_constants_frontend_sync.py가
드리프트를 감시한다. scripts/regime_backtest.py(규칙기반 검증 CLI, ML 파이프라인과
무관)도 자체 MARKETS 상수를 별도로 정의하고 있는데, 이는 의도적으로 분리된 목록이다
(규칙기반 백테스트 검증 대상 vs. ML 학습 대상은 다른 개념) — TRAINING_MARKETS와
엮어서 단일화하지 말 것.
"""
```

New:
```python
"""
engine/regime_ml_constants.py

장세 판별 ML 파이프라인 전체(학습+추론)가 공유하는 상수. 학습 스크립트
(scripts/train_regime_ml.py)와 추론 서비스(backend/regime_ml_service.py)가 서로
다른 마켓 목록을 갖게 되는 걸 막기 위해 단일 소스로 뽑았다. 프론트엔드
(frontend/components/RegimeMlCurrentPrediction.tsx)는 이 값을 API로 받지 않고
하드코딩된 배열을 따로 유지하며, tests/test_regime_ml_constants_frontend_sync.py가
드리프트를 감시한다.
"""
```

- [ ] **Step 21: `engine/regime_features.py`의 순환참조 회피 주석 정리**

Old(23~26행):
```python
# regime_detector.py의 동명 상수와 값이 같아야 한다. regime_detector가 이 모듈을
# import하므로(반대 방향은 순환참조), backend/regime_service.py의 _to_utc_iso와
# 같은 이유로 별도 정의한다.
_MIN_VOLATILITY_FLOOR = 1e-6
```

New:
```python
# 이 모듈이 자체적으로 갖는 최소 변동성 하한값(level_proximity의 0-나눗셈 방지용).
_MIN_VOLATILITY_FLOOR = 1e-6
```

- [ ] **Step 22: 전체 테스트 스위트 통과 확인**

Run: `python -m pytest tests/ -q`
Expected: PASS (전체, 삭제/신설/수정된 모든 파일 포함)

- [ ] **Step 23: Commit**

```bash
git add engine/regime_math.py tests/test_regime_math.py backend/regime_ml_service.py \
  scripts/train_regime_ml.py engine/regime_ml_labels.py tests/test_regime_ml_labels.py \
  tests/test_train_regime_ml.py engine/regime_ml_constants.py engine/regime_features.py \
  backend/main.py tests/test_backend.py
git commit -m "$(cat <<'EOF'
refactor: 장세 판별 규칙기반 백엔드 제거, ML 의존 함수 4개는 regime_math.py로 이관

engine/regime_detector.py·backend/regime_service.py·scripts/regime_backtest.py와
GET /api/v1/regime/backtest 엔드포인트를 삭제. half_life_bars_for_timeframe/
ewm_volatility(+_ewm_std_series)/N_MULTIPLIER는 ML 파이프라인이 실제로 의존해
새 engine/regime_math.py로 이관(E 1/2).
EOF
)"
```

---

## Task 2: 프론트엔드 — 규칙기반 컴포넌트 삭제 + RegimeDashboard 재구성

**Files:**
- Delete: `frontend/components/RegimeBacktestForm.tsx`
- Delete: `frontend/components/RegimeCurrentPrediction.tsx`
- Delete: `frontend/components/RegimeAccuracyReport.tsx`
- Delete: `frontend/components/RegimeChart.tsx`
- Modify: `frontend/lib/api/eda.ts`
- Modify: `frontend/lib/types/eda.ts`
- Modify: `frontend/components/RegimeDashboard.tsx`

**Interfaces:**
- Consumes: `GET /api/v1/regime/ml-current-prediction`(Task 1에서 변경 없음, 이미 존재) — `frontend/lib/api/eda.ts`의 기존 `getRegimeMlCurrentPrediction()`을 그대로 쓴다(수정하지 않음). `frontend/components/RegimeMlCurrentPrediction.tsx`도 수정하지 않는다(props `{market, timeframe}` 그대로).

- [ ] **Step 1: 규칙기반 전용 컴포넌트 4개 삭제**

```bash
git rm frontend/components/RegimeBacktestForm.tsx
git rm frontend/components/RegimeCurrentPrediction.tsx
git rm frontend/components/RegimeAccuracyReport.tsx
git rm frontend/components/RegimeChart.tsx
```

- [ ] **Step 2: `frontend/lib/api/eda.ts`에서 `getRegimeBacktest` 삭제**

Old(import 목록 중 일부, 1~21행):
```typescript
import { apiFetch } from './client';
import type {
  BacktestDetail,
  BacktestRunSummary,
  Combo,
  GridSearchEstimate,
  GridSearchIndicatorPoolCatalog,
  GridSearchJob,
  GridSearchJobRequest,
  IndicatorCatalogItem,
  IndicatorPool,
  Market,
  MlCurrentPrediction,
  RegimeBacktestResult,
  RunBacktestRequest,
  RunBacktestResponse,
  SegmentSizeEntry,
  SweepResult,
  TrendSegmentAnalysis,
  ValidateBacktestResponse,
} from '@/lib/types/eda';
```

New:
```typescript
import { apiFetch } from './client';
import type {
  BacktestDetail,
  BacktestRunSummary,
  Combo,
  GridSearchEstimate,
  GridSearchIndicatorPoolCatalog,
  GridSearchJob,
  GridSearchJobRequest,
  IndicatorCatalogItem,
  IndicatorPool,
  Market,
  MlCurrentPrediction,
  RunBacktestRequest,
  RunBacktestResponse,
  SegmentSizeEntry,
  SweepResult,
  TrendSegmentAnalysis,
  ValidateBacktestResponse,
} from '@/lib/types/eda';
```

Old(`getRegimeBacktest` 함수 전체, `deleteGridSearchJob` 바로 다음):
```typescript
export function getRegimeBacktest(params: {
  market: string;
  timeframe: string;
  start: string;
  end: string;
}): Promise<RegimeBacktestResult> {
  const query = new URLSearchParams(params);
  return apiFetch<RegimeBacktestResult>(`/api/v1/regime/backtest?${query.toString()}`);
}

export function getRegimeMlCurrentPrediction(params: {
```

New:
```typescript
export function getRegimeMlCurrentPrediction(params: {
```

- [ ] **Step 3: `frontend/lib/types/eda.ts`에서 `RegimeBacktestResult`/`RegimeCandle`/`CurrentPrediction` 삭제**

Old:
```typescript
export interface MlCurrentPrediction {
  predicted_category: RegimeCategory;
  probs: Record<RegimeCategory, number>;
  bar_time: string;
  model_trained_at: string;
  model_fold_index: number;
  model_performance: MlModelPerformance | null;
}

export interface RegimeCandle {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  predicted_category: RegimeCategory | null;
}

export interface CurrentPrediction {
  time: string;
  predicted_category: RegimeCategory | null;
  probs: Record<RegimeCategory, number> | null;
}

export interface RegimeBacktestResult {
  half_life_bars: number;
  n_bars: number;
  candles: RegimeCandle[];
  current_prediction: CurrentPrediction | null;
  confusion: Record<RegimeCategory, Record<RegimeCategory, number>>;
  actual_totals: Record<RegimeCategory, number>;
  correlation: number | null;
}

export interface IndicatorParamDef {
```

New:
```typescript
export interface MlCurrentPrediction {
  predicted_category: RegimeCategory;
  probs: Record<RegimeCategory, number>;
  bar_time: string;
  model_trained_at: string;
  model_fold_index: number;
  model_performance: MlModelPerformance | null;
}

export interface IndicatorParamDef {
```

- [ ] **Step 4: TypeScript 컴파일 확인(이 시점엔 RegimeDashboard.tsx가 아직 삭제된 컴포넌트를 참조하므로 에러가 나야 정상)**

Run: `cd frontend && npx tsc --noEmit`
Expected: FAIL — `frontend/components/RegimeDashboard.tsx`에서 삭제된
`RegimeBacktestForm`/`RegimeCurrentPrediction`/`RegimeAccuracyReport`/`getRegimeBacktest`/
`RegimeBacktestResult`를 못 찾는다는 에러 다수. (다음 스텝에서 이 파일을
재작성하면 해소된다.)

- [ ] **Step 5: `frontend/components/RegimeDashboard.tsx` 재작성**

파일 전체를 아래 내용으로 교체:

```tsx
'use client';

import { useEffect, useState } from 'react';
import RegimeMlCurrentPrediction from '@/components/RegimeMlCurrentPrediction';
import CoinSelect, { sortMarkets } from '@/components/CoinSelect';
import { Button } from '@/components/ui/button';
import { ApiError } from '@/lib/api/client';
import { getMarkets } from '@/lib/api/eda';
import { SECTION_HEADER_CLASS } from '@/lib/ui-classes';
import { formatTimeframe, TIMEFRAME_CODES } from '@/lib/format';
import type { Market } from '@/lib/types/eda';

const TIMEFRAME_OPTIONS = TIMEFRAME_CODES.map((timeframe) => ({
  label: formatTimeframe(timeframe),
  timeframe,
}));

export default function RegimeDashboard() {
  const [markets, setMarkets] = useState<Market[]>([]);
  const [marketsError, setMarketsError] = useState<string | null>(null);
  const [market, setMarket] = useState('');
  const [timeframe, setTimeframe] = useState('minutes60');

  useEffect(() => {
    getMarkets()
      .then((data) => {
        setMarkets(data);
        const sorted = sortMarkets(data, 'change_rate', 'desc');
        if (sorted.length > 0) setMarket((prev) => prev || sorted[0].market);
      })
      .catch((err) => setMarketsError(err instanceof ApiError ? err.message : '코인 목록을 불러오지 못했습니다.'));
  }, []);

  return (
    <div className="space-y-4">
      <div className="max-w-4xl space-y-4 rounded-xl border p-6 shadow-sm">
        <div>
          <label className="mb-1.5 block text-sm font-medium">코인 선택</label>
          <CoinSelect markets={markets} value={market} onChange={setMarket} />
          {marketsError && <p className="mt-1 text-xs text-destructive">{marketsError}</p>}
        </div>
        <div>
          <div className={SECTION_HEADER_CLASS}>봉데이터</div>
          <div className="flex flex-wrap gap-2 p-3">
            {TIMEFRAME_OPTIONS.map((opt) => (
              <Button
                key={opt.timeframe}
                type="button"
                variant={timeframe === opt.timeframe ? 'default' : 'outline'}
                size="sm"
                onClick={() => setTimeframe(opt.timeframe)}
              >
                {opt.label}
              </Button>
            ))}
          </div>
        </div>
      </div>
      {market && <RegimeMlCurrentPrediction market={market} timeframe={timeframe} />}
    </div>
  );
}
```

- [ ] **Step 6: TypeScript 컴파일 확인**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 7: 개발 서버로 브라우저 확인**

**주의(이 저장소의 알려진 함정)**: `npm run dev`가 이미 떠 있는 상태에서
`npm run build`를 실행하면 `.next` 빌드 캐시가 깨져 `MODULE_NOT_FOUND` 에러가
난다 — 위 Step 6의 `tsc --noEmit`만 타입체크용으로 쓰고, `npm run build`는
쓰지 않는다. 먼저 `netstat -ano | grep -E ':(3000|8000)'`로 이미 떠 있는
프로세스가 있는지 확인한다.

Run(저장소 루트): `python -m uvicorn backend.main:app --reload --port 8000`
Run(`frontend` 디렉터리, 별도 터미널): `npm run dev`

브라우저로 `http://localhost:3000/regime` 접속 후 확인:
- "코인 선택" 드롭다운과 "봉데이터" 버튼 행이 보이고, 그 아래 "ML 현재예측"
  카드가 바로 보이는지(과거처럼 "조회" 버튼이 없어야 함)
- 코인을 KRW-BTC로, 봉데이터를 1시간으로 선택하면 ML 카드가 예측 카테고리 +
  확률분포 + 모델 성능 섹션을 보여주는지
- 봉데이터를 1시간이 아닌 다른 것(예: 일)으로 바꾸면 ML 카드가 "ML은 1시간봉
  전용입니다" 안내문으로 바뀌는지
- 브라우저 콘솔에 에러가 없는지

- [ ] **Step 8: Commit**

```bash
git add frontend/components/RegimeBacktestForm.tsx frontend/components/RegimeCurrentPrediction.tsx \
  frontend/components/RegimeAccuracyReport.tsx frontend/components/RegimeChart.tsx \
  frontend/lib/api/eda.ts frontend/lib/types/eda.ts frontend/components/RegimeDashboard.tsx
git commit -m "$(cat <<'EOF'
refactor: 장세 판별 규칙기반 프론트 컴포넌트 제거, 대시보드를 ML 전용 셀렉터로 재구성

RegimeBacktestForm/RegimeCurrentPrediction/RegimeAccuracyReport/RegimeChart 삭제.
RegimeDashboard가 규칙기반 폼 제출에 얹혀 ML 카드를 렌더링하던 구조를 끊고,
가벼운 코인/봉타입 셀렉터로 직접 market/timeframe을 넘기도록 재작성(E 2/2).
EOF
)"
```

---

## 완료 후 확인

- `docs/regime-ml-backlog.md`의 E 항목은 이 계획 완료로 해소됨(백로그 파일 자체
  갱신 여부는 사용자 판단).
- 다음 우선순위는 A1(재학습 자동화) — 별도 브레인스토밍 세션 필요(증분학습 vs
  스크래치 재학습, 자동배포 여부 등 미결정 사항 다수).
