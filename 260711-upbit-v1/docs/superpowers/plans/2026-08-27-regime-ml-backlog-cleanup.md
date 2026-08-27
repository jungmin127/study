# 장세 판별 ML 백로그 정리 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Task 4와 Task 5는 subagent에게 위임하지 말 것.** 코드 변경이 아니라 (a) 실데이터
> 재학습 결과를 사람이 판단해야 하는 검증 단계, (b) 실서버에 SSH/SCP로 파일을 올리는
> 되돌리기 까다로운 배포 액션이라 메인 세션에서 직접 수행하고 사용자 확인을 구한다.

**Goal:** 장세 판별 ML 백로그 3항목(OBV 스케일 불일치, 마켓 목록 4중복, AWS 배포
미검증)을 우선순위 순서(1→2→3, 이 문서의 Task 1-2 → Task 3 → Task 4-5)로 해결한다.

**Architecture:** 기존 `engine/regime_ml_features.py`(피처 생성 단일 chokepoint)에서
OBV 컬럼만 제외하고, 신규 `engine/regime_ml_constants.py`로 마켓 목록을 단일 소스화한
뒤, 정규식 기반 가드레일 테스트로 프론트 하드코딩 배열과의 드리프트를 감시한다.
마지막으로 재학습 스크립트를 실행해 실측 검증하고, 검증 통과 시 새 모델을 AWS
서버로 배포한다.

**Tech Stack:** Python(pandas, LightGBM), pytest, TypeScript/React(프론트, 코드
변경 없음 — 가드레일 테스트의 대상으로만 참조)

## Global Constraints

- 테스트는 `pytest tests/<path>::<test_name> -v`로 실행 (`pytest.ini`에
  `pythonpath = .` 설정되어 있어 별도 `PYTHONPATH` 지정 불필요)
- [[upbit-v1-dont-push-on-empirical-regression]]: 리뷰를 통과해도 실측 지표(fold
  상관계수)가 기존 기록값(0.06~0.16)보다 눈에 띄게 나빠지면 push하지 않고 사용자
  승인을 먼저 받는다
- `data/regime_ml_models/`는 gitignore 대상 — 재학습 산출물(.txt/.json)은 커밋하지
  않는다
- 커밋 메시지는 한국어, 기존 저장소 컨벤션(`fix:`/`feat:`/`docs:` 접두사) 유지

---

### Task 1: OBV를 ML 피처에서 제외

**Files:**
- Modify: `engine/regime_ml_features.py:29-37`
- Modify: `backend/regime_ml_service.py:25-36` (주석 정리, 동작 변경 없음)
- Test: `tests/test_regime_ml_features.py`

**Interfaces:**
- Consumes: 없음 (기존 `LIVE_INDICATOR_FACTORY`, `build_feature_matrix` 시그니처
  그대로 — 반환 컬럼 집합만 달라짐)
- Produces: `build_feature_matrix()`가 더 이상 `"OBV"` 컬럼을 포함하지 않음. Task 2,
  Task 4가 이 변경된 피처 매트릭스로 재학습을 수행한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime_ml_features.py`를 열어 모듈 docstring과
`test_build_feature_matrix_has_one_column_per_registered_indicator_plus_regime_features`
함수를 아래로 교체하고, 새 테스트를 하나 추가한다.

```python
"""
tests/test_regime_ml_features.py

engine.regime_ml_features.build_feature_matrix()를 검증한다. LIVE_INDICATOR_FACTORY를
그대로 순회하되 OBV(스케일 불일치로 제외, docs/superpowers/specs/2026-08-27-regime-ml-
backlog-cleanup-design.md 참고)만 뺀다 — 반환 컬럼 집합이 그 레지스트리 키 전체(OBV
제외) + regime 전용 5개 + market과 정확히 일치해야 한다.
"""
```

```python
def test_build_feature_matrix_has_one_column_per_registered_indicator_except_obv_plus_regime_features():
    df = _make_full_df()

    result = build_feature_matrix(df, market="KRW-BTC", half_life_bars=24.0)

    expected_columns = (
        (set(LIVE_INDICATOR_FACTORY.keys()) - {"OBV"})
        | {"RAW_SCORE", "VOLUME_CONFIRM", "VPIN_SCORE", "LEVEL_PROXIMITY", "REVERSAL_GATE", "market"}
    )
    assert set(result.columns) == expected_columns


def test_build_feature_matrix_excludes_obv_but_keeps_obv_roc():
    df = _make_full_df()

    result = build_feature_matrix(df, market="KRW-BTC", half_life_bars=24.0)

    assert "OBV" not in result.columns
    assert "OBV_ROC" in result.columns
```

(기존 함수명 `test_build_feature_matrix_has_one_column_per_registered_indicator_plus_regime_features`를
찾아 통째로 위 새 함수로 바꾸는 것 — 이름이 달라졌으므로 삭제 후 새로 추가하는 형태가 된다.)

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `pytest tests/test_regime_ml_features.py -v`
Expected: `test_build_feature_matrix_has_one_column_per_registered_indicator_except_obv_plus_regime_features`와
`test_build_feature_matrix_excludes_obv_but_keeps_obv_roc` 둘 다 FAIL (현재 코드는
OBV를 포함하므로 `set(result.columns) == expected_columns`가 거짓이고 `"OBV" not in
result.columns`도 거짓)

- [ ] **Step 3: `engine/regime_ml_features.py` 수정**

`build_feature_matrix()`의 `features` 딕셔너리 생성부(29번째 줄 함수 정의부터
37번째 줄까지)를 아래로 교체:

```python
def build_feature_matrix(df: pd.DataFrame, market: str, half_life_bars: float) -> pd.DataFrame:
    """df: close/high/low/volume/trade_value + btc_close/usdt_close/binance_close/
    fear_greed_value/funding_rate_value/korea_premium_value를 전부 포함해야 한다
    (engine.regime_ml_data.load_market_training_data()가 반환하는 형태). 반환
    DataFrame은 df와 같은 행 수/인덱스를 유지하며(워밍업 구간은 NaN), 원본 OHLCV
    컬럼은 포함하지 않는다(피처 전용) — market 범주형 컬럼만 추가한다."""
    # OBV(create_obv)는 윈도우 없는 누적합이라 추론 시(짧은 최근 구간)와 학습
    # 시(수년치) 스케일이 어긋난다(backend/regime_ml_service.py 참고) — 피처에서
    # 제외한다. 같은 레지스트리의 OBV_ROC는 rolling window 기반 %지표라 스케일
    # 문제가 없으므로 그대로 둔다.
    features: dict[str, pd.Series] = {
        name: factory(df) for name, factory in LIVE_INDICATOR_FACTORY.items() if name != "OBV"
    }
```

- [ ] **Step 4: 테스트가 통과하는지 확인**

Run: `pytest tests/test_regime_ml_features.py -v`
Expected: PASS (전체)

- [ ] **Step 5: `backend/regime_ml_service.py` 주석 정리**

25번째 줄부터 36번째 줄까지(`# WARMUP_DAYS=30로...`로 시작해 `WARMUP_DAYS = 30`까지,
"재학습해야 한다." 코멘트 끝 포함)를 아래로 교체:

```python
# OBV 스케일 불일치는 engine/regime_ml_features.py:build_feature_matrix()가 OBV를
# 피처에서 제외해 해결했다(OBV_ROC로 대체) — WARMUP_DAYS=30이 짧아도 더 이상
# 학습/추론 간 스케일이 어긋나지 않는다.
WARMUP_DAYS = 30
```

- [ ] **Step 6: 전체 regime_ml 테스트 스위트로 회귀 확인**

Run: `pytest tests/test_regime_ml_features.py tests/test_regime_ml_service.py tests/test_train_regime_ml.py -v`
Expected: 전체 PASS (regime_ml_service.py는 동작 변경 없이 주석만 지웠으므로 기존
테스트가 그대로 통과해야 함)

- [ ] **Step 7: 커밋**

```bash
git add engine/regime_ml_features.py backend/regime_ml_service.py tests/test_regime_ml_features.py
git commit -m "fix: OBV 스케일 불일치 해결 — ML 피처에서 OBV 제외(OBV_ROC로 대체)"
```

---

### Task 2: 마켓 목록 공유 상수 모듈 도입

**Files:**
- Create: `engine/regime_ml_constants.py`
- Modify: `backend/regime_ml_service.py:19-45` (import 추가, `_TRAINING_MARKETS` 정의
  제거 및 사용처 3곳 치환)
- Modify: `scripts/train_regime_ml.py:32-41, 289-297` (import 추가, `MARKETS` 정의
  제거 및 사용처 치환, `MODEL_OUTPUT_DIR` 경로 수정)
- Test: 기존 `tests/test_regime_ml_service.py`, `tests/test_train_regime_ml.py` 회귀
  확인만(신규 테스트 없음 — 리네임/치환이라 동작 변경 없음)

**Interfaces:**
- Consumes: 없음
- Produces: `engine.regime_ml_constants.TRAINING_MARKETS: list[str]` —
  `["KRW-BTC", "KRW-ETH", "KRW-XRP"]`. Task 3의 가드레일 테스트가 이 이름을
  import해서 프론트 배열과 비교한다.

- [ ] **Step 1: `engine/regime_ml_constants.py` 생성**

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
from __future__ import annotations

TRAINING_MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-XRP"]
```

- [ ] **Step 2: 모듈이 정상 import되는지 확인**

Run: `python -c "from engine.regime_ml_constants import TRAINING_MARKETS; print(TRAINING_MARKETS)"`
Expected: `['KRW-BTC', 'KRW-ETH', 'KRW-XRP']` 출력, 에러 없음

- [ ] **Step 3: `backend/regime_ml_service.py`에서 `_TRAINING_MARKETS` 제거**

19-21번째 줄의 import 블록:
```python
from engine.regime_detector import half_life_bars_for_timeframe
from engine.regime_ml_data import load_market_training_data
from engine.regime_ml_features import build_feature_matrix
```
을 아래로 교체:
```python
from engine.regime_detector import half_life_bars_for_timeframe
from engine.regime_ml_constants import TRAINING_MARKETS
from engine.regime_ml_data import load_market_training_data
from engine.regime_ml_features import build_feature_matrix
```

39-45번째 줄(`# scripts/train_regime_ml.py의 MARKETS와...`부터
`_TRAINING_MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-XRP"]`까지)를 아래로 교체(정의를
지우고 주석만 무엇을 참조하는지로 축약):
```python
# 학습 시 train_X["market"].astype("category")가 TRAINING_MARKETS의 알파벳순으로
# 카테고리 코드(0/1/2)를 배정했고, 저장된 부스터는 그 정수 코드만 기억한다 — 추론 시
# 이 전체 목록을 categories=로 명시하지 않으면(예: 1행짜리 프레임에 그냥
# astype("category")를 부르면) 카테고리가 1개뿐이라 코드가 다시 0으로 배정돼
# 학습 때와 다른 마켓을 가리키는 것처럼 조용히 오작동한다.
```

이제 파일 안에서 `_TRAINING_MARKETS`를 쓰던 3곳(`if market not in _TRAINING_MARKETS:`,
`f"이 모델은 {', '.join(_TRAINING_MARKETS)}로만 학습되어 있습니다"`,
`categories=sorted(_TRAINING_MARKETS)`)을 전부 `TRAINING_MARKETS`로 치환한다.

- [ ] **Step 4: `scripts/train_regime_ml.py`에서 `MARKETS` 제거 + `MODEL_OUTPUT_DIR` 경로 수정**

32-41번째 줄:
```python
from engine.regime_ml_splits import generate_walk_forward_folds
from engine.regime_ml_data import load_market_training_data
from upbit_data_service import timeframe_duration

MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-XRP"]
TIMEFRAME = "minutes60"
TRAIN_START = datetime(2024, 1, 1, tzinfo=timezone.utc)
TRAIN_END = datetime.now(timezone.utc)
N_FOLDS = 5
MIN_TRAIN_SAMPLES = 500
MODEL_OUTPUT_DIR = Path("data/regime_ml_models")
```
을 아래로 교체:
```python
from engine.regime_ml_constants import TRAINING_MARKETS
from engine.regime_ml_splits import generate_walk_forward_folds
from engine.regime_ml_data import load_market_training_data
from upbit_data_service import timeframe_duration

TIMEFRAME = "minutes60"
TRAIN_START = datetime(2024, 1, 1, tzinfo=timezone.utc)
TRAIN_END = datetime.now(timezone.utc)
N_FOLDS = 5
MIN_TRAIN_SAMPLES = 500
MODEL_OUTPUT_DIR = Path(__file__).parent.parent / "data" / "regime_ml_models"
```

289-297번째 줄의 `main()` 함수:
```python
def main() -> None:
    reports = run_training(
        markets=MARKETS,
        timeframe=TIMEFRAME,
        start=TRAIN_START,
        end=TRAIN_END,
        n_folds=N_FOLDS,
        min_train_samples=MIN_TRAIN_SAMPLES,
        model_output_dir=MODEL_OUTPUT_DIR,
    )
```
을 아래로 교체:
```python
def main() -> None:
    reports = run_training(
        markets=TRAINING_MARKETS,
        timeframe=TIMEFRAME,
        start=TRAIN_START,
        end=TRAIN_END,
        n_folds=N_FOLDS,
        min_train_samples=MIN_TRAIN_SAMPLES,
        model_output_dir=MODEL_OUTPUT_DIR,
    )
```

- [ ] **Step 5: 회귀 테스트 확인**

Run: `pytest tests/test_regime_ml_service.py tests/test_train_regime_ml.py -v`
Expected: 전체 PASS (두 파일 다 모듈 레벨 상수를 리네임/치환했을 뿐 동작은
동일 — `tests/test_train_regime_ml.py`는 `run_training()`에 `markets=`를 직접
넘기므로 영향 없음, `tests/test_regime_ml_service.py`는 `regime_ml_service`
모듈의 공개 동작만 검증하므로 영향 없음)

- [ ] **Step 6: import 순환 확인**

Run: `python -c "import backend.regime_ml_service; import scripts.train_regime_ml; print('ok')"`
Expected: `ok` 출력, `ImportError`/`ModuleNotFoundError` 없음

- [ ] **Step 7: 커밋**

```bash
git add engine/regime_ml_constants.py backend/regime_ml_service.py scripts/train_regime_ml.py
git commit -m "refactor: 장세 판별 ML 마켓 목록을 engine/regime_ml_constants.py로 통합"
```

---

### Task 3: 프론트-백엔드 마켓 목록 동기화 가드레일 테스트

**Files:**
- Create: `tests/test_regime_ml_constants_frontend_sync.py`

**Interfaces:**
- Consumes: `engine.regime_ml_constants.TRAINING_MARKETS` (Task 2에서 생성)
- Produces: 없음(가드레일 테스트, 다른 태스크가 이 파일을 참조하지 않음)

- [ ] **Step 1: 프론트 파일의 현재 배열 형태 확인**

Run: `grep -n "TRAINED_MARKETS" "frontend/components/RegimeMlCurrentPrediction.tsx"`
Expected:
```
10:const TRAINED_MARKETS = ['KRW-BTC', 'KRW-ETH', 'KRW-XRP'];
38:    if (timeframe !== 'minutes60' || !market || !TRAINED_MARKETS.includes(market)) {
67:      ) : !TRAINED_MARKETS.includes(market) ? (
```
(정규식이 10번째 줄 선언 형태를 전제하므로, 실제 출력이 이와 다르면 Step 3의 정규식을
그에 맞게 조정한다.)

- [ ] **Step 2: 테스트 작성**

`tests/test_regime_ml_constants_frontend_sync.py` 신규 생성:

```python
"""
tests/test_regime_ml_constants_frontend_sync.py

engine.regime_ml_constants.TRAINING_MARKETS와
frontend/components/RegimeMlCurrentPrediction.tsx의 TRAINED_MARKETS 배열이 어긋나지
않는지 감시하는 가드레일 테스트. 프론트는 API 호출 없이 하드코딩 배열을 그대로
유지하기로 결정했으므로(docs/superpowers/specs/2026-08-27-regime-ml-backlog-cleanup-
design.md 참고), 한쪽만 바뀌면 이 테스트가 실패해 드리프트를 잡는다.
"""
from __future__ import annotations

import re
from pathlib import Path

from engine.regime_ml_constants import TRAINING_MARKETS

_FRONTEND_FILE = (
    Path(__file__).parent.parent
    / "frontend" / "components" / "RegimeMlCurrentPrediction.tsx"
)
_ARRAY_PATTERN = re.compile(r"TRAINED_MARKETS\s*=\s*\[([^\]]*)\]")
_QUOTED_STRING_PATTERN = re.compile(r"'([^']*)'")


def _extract_frontend_markets() -> list[str]:
    content = _FRONTEND_FILE.read_text(encoding="utf-8")
    match = _ARRAY_PATTERN.search(content)
    assert match is not None, (
        f"{_FRONTEND_FILE}에서 TRAINED_MARKETS 배열을 찾지 못했습니다 — "
        "파일 구조가 바뀌었으면 이 테스트의 정규식도 갱신하세요."
    )
    return _QUOTED_STRING_PATTERN.findall(match.group(1))


def test_frontend_trained_markets_matches_backend_training_markets():
    frontend_markets = _extract_frontend_markets()

    assert sorted(frontend_markets) == sorted(TRAINING_MARKETS)
```

- [ ] **Step 3: 테스트 실행 확인 (현재 상태에서 이미 통과해야 함)**

Run: `pytest tests/test_regime_ml_constants_frontend_sync.py -v`
Expected: PASS (Task 1-2에서 마켓 목록 값 자체는 바꾸지 않았으므로, 이 시점에는
이미 일치 — 이 테스트는 "지금 통과하는지"가 아니라 "나중에 한쪽만 바뀌면 실패하는지"가
목적이다. 통과하지 않으면 Step 1에서 확인한 프론트 파일 실제 형태와 정규식이 안
맞는 것이므로 정규식을 다시 조정한다.)

- [ ] **Step 4: 가드레일이 실제로 작동하는지 임시 확인**

`engine/regime_ml_constants.py`의 `TRAINING_MARKETS`를 임시로
`["KRW-BTC", "KRW-ETH"]`로 바꾼 뒤:

Run: `pytest tests/test_regime_ml_constants_frontend_sync.py -v`
Expected: FAIL (`assert sorted(['KRW-BTC', 'KRW-ETH', 'KRW-XRP']) == sorted(['KRW-BTC', 'KRW-ETH'])`)

확인 후 `TRAINING_MARKETS`를 `["KRW-BTC", "KRW-ETH", "KRW-XRP"]`로 즉시 되돌린다.

Run: `pytest tests/test_regime_ml_constants_frontend_sync.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add tests/test_regime_ml_constants_frontend_sync.py
git commit -m "test: 장세 판별 ML 마켓 목록 프론트-백엔드 동기화 가드레일 추가"
```

---

### Task 4: 재학습 실행 및 실측 검증 (메인 세션 직접 수행, subagent 위임 금지)

**Files:** 없음(코드 변경 없음 — 실행+판단 단계)

**Interfaces:**
- Consumes: Task 1의 `build_feature_matrix()`(OBV 제외), Task 2의
  `scripts/train_regime_ml.py`(수정된 `MODEL_OUTPUT_DIR`)
- Produces: `data/regime_ml_models/regime_ml_<timestamp>.txt` + `.json`(gitignore
  대상, 커밋 안 함). Task 5가 이 파일을 AWS 서버로 올린다.

- [ ] **Step 1: 재학습 스크립트 실행**

Run: `python scripts/train_regime_ml.py`
Expected: 에러 없이 완주, fold별 리포트와 전체 fold 합산 리포트가 콘솔에 출력됨,
마지막에 `data/regime_ml_models/`에 새 `.txt`+`.json` 페어가 생성됨

- [ ] **Step 2: 상관계수 비교**

콘솔 출력의 "[확률벡터-실현수익률 상관계수]" 값들(fold별 + 전체 합산)을 메모리
`upbit-v1-regime-ml-classifier`에 기록된 기존값(fold r=0.06~0.16)과 비교한다.

- 개선되었거나 동등(같은 범위 내)하면 Step 3으로 진행
- 눈에 띄게 나빠졌으면(예: 전체 합산 상관계수가 기존 대비 절반 이하로 떨어지거나
  음수로 전환) [[upbit-v1-dont-push-on-empirical-regression]] 원칙에 따라 Task 5로
  진행하지 않고, 실측 결과를 그대로 사용자에게 보고한 뒤 사용자 지시를 기다린다

- [ ] **Step 3: 새 모델 파일 확인**

Run: `ls data/regime_ml_models/ | tail -4`
Expected: 방금 생성된 타임스탬프의 `.txt`+`.json` 페어가 가장 최근 파일로 보임

- [ ] **Step 4: `/regime` 대시보드에서 수동 확인 (선택, 로컬 서버 구동 중이면)**

로컬 백엔드/프론트가 떠 있다면 `/regime` 탭에서 KRW-BTC/ETH/XRP + 1시간봉 조합으로
"ML 현재예측" 카드가 에러 없이 예측을 표시하는지 확인한다(`find_latest_model()`이
방금 만든 파일을 자동으로 집는다).

이 태스크는 커밋할 코드 변경이 없다(재학습 산출물은 gitignore 대상).

---

### Task 5: AWS 서버 배포 (메인 세션 직접 수행, subagent 위임 금지, 사용자 확인 필수)

**Files:** 없음(코드 변경 없음 — 배포 액션)

**Interfaces:**
- Consumes: Task 4에서 생성된 `data/regime_ml_models/regime_ml_<timestamp>.txt/.json`
- Produces: 없음(실서버 파일 상태 변경 — 이 계획 내 다른 태스크가 결과에 의존하지
  않음)

**전제조건:** Task 4에서 상관계수가 회귀하지 않았음을 확인한 경우에만 진행한다.

- [ ] **Step 1: 배포 설정 확인**

Run: `grep -c "DEPLOY_SSH_KEY_PATH\|DEPLOY_SERVER_HOST" .env 2>/dev/null || echo "0"`
Expected: `.env` 파일이 없거나 두 변수가 없으면 `0`(또는 파일 없음) — 이 경우
"배포 설정이 안 되어 있습니다"라고 사용자에게 보고하고 여기서 중단한다(스크립트
실행하지 않음). 두 변수가 모두 있으면 Step 2로 진행.

- [ ] **Step 2: 실행 전 사용자 확인**

`scripts/push_regime_ml_model.sh`는 실서버(운영 환경)에 SSH로 접속해 모델 파일을
SCP로 올리는 되돌리기 까다로운 액션이다. 실행하기 전에 반드시 사용자에게 다음을
확인한다: "Task 4에서 새로 학습된 모델을 AWS 서버로 배포하겠습니다. 진행할까요?"
사용자 승인 없이는 Step 3을 실행하지 않는다.

- [ ] **Step 3: 배포 스크립트 실행 (사용자 승인 후)**

Run: `bash scripts/push_regime_ml_model.sh`
Expected:
```
=== 1/2: 원격 모델 디렉터리 준비 ===
=== 2/2: 모델 파일 전송 ===
모델 전송 완료: regime_ml_<timestamp>.txt (.txt + .json)
```

- [ ] **Step 4: 결과 보고**

성공/실패 여부와 전송된 파일명을 사용자에게 그대로 보고한다. 실패 시(SSH 연결 불가,
권한 오류 등) 에러 메시지 전문을 함께 보고하고, 원인 파악 전에 재시도하지 않는다.

이 태스크는 커밋할 코드 변경이 없다.

---

## Self-Review 결과

- **스펙 커버리지**: 스펙의 "1. OBV 스케일 불일치" → Task 1, "2. 마켓 목록 4중복
  제거"(+ 곁들인 `MODEL_OUTPUT_DIR` 정리) → Task 2, 프론트 동기화 가드레일 → Task 3,
  "3. AWS 서버 배포 검증" → Task 5(+ 재학습 실행 자체는 Task 4로 분리, 배포의 전제
  조건이라 스펙의 "1번에서 재학습된 새 모델이 준비된 뒤" 문구와 일치). 스펙의 "테스트
  계획" 4개 항목 모두 Task 1/3/4의 스텝으로 반영됨. 누락 없음.
- **플레이스홀더 스캔**: "TBD"/"나중에"/"적절히" 류 표현 없음. 모든 코드 스텝에 실제
  코드 블록 포함.
- **타입/시그니처 일관성**: `TRAINING_MARKETS: list[str]`이 Task 2에서 정의되고
  Task 3에서 그대로 import되어 사용됨. `build_feature_matrix()` 시그니처(Task 1)는
  변경되지 않음(반환 컬럼 집합만 달라짐) — Task 2/4가 이를 그대로 소비.
