# 장세 판별 ML — 로지스틱회귀 baseline 비교 + LightGBM 하이퍼파라미터 튜닝 (c-2) 설계

## 배경

`docs/regime-ml-backlog.md`의 잔여 후보 (c) 중 c-2. 지금 `scripts/train_regime_ml.py`는
LightGBM을 `objective`/`class_weight`/`importance_type`/`random_state` 외 전부 sklearn
기본 하이퍼파라미터로 학습한다. `docs/ML_Regime_Switching_Additional_Improvements.md`
3-1절이 지적하듯, 신호대잡음비가 낮은 금융시계열에서는 트리 앙상블이 노이즈에
과적합하기 쉽고 정규화 선형모델이 오히려 견고할 수 있다 — 지금 모델이 정말 신호를
잡고 있는지, 트리모델의 유연성이 노이즈를 외운 것뿐인지 아직 확인한 적 없다.

**목적**: (1) LightGBM(현재 기본값) vs LogisticRegression(L2) walk-forward pooled
weighted kappa 비교로 "트리모델이 실제로 더 나은가"를 확인하고, (2) LightGBM
하이퍼파라미터를 축소 그리드로 튜닝해 baseline(0.108, `docs/regime-ml-backlog.md`
주가지수 라운드 결과) 대비 개선 여지가 있는지 진단한다.

**성격**: `scripts/select_barrier_k.py`와 같은 "1회성 측정이지만 재실행 가능하게
scripts/에 커밋" 패턴. 매 학습(`scripts/train_regime_ml.py`)마다 자동 실행되지 않고,
결과가 채택될 경우에만 `train_regime_ml.py`의 LightGBM 생성 코드를 수동으로 갱신한다.

## 아키텍처

### `run_training()` 확장 (`scripts/train_regime_ml.py`)

기존 함수에 하위호환 기본값을 가진 파라미터 3개를 추가한다:

- `model_factory: Callable[[], Any] = _default_lgbm_factory` — fold마다 새 모델
  인스턴스 생성. 기본값은 지금 인라인으로 있던
  `lgb.LGBMClassifier(objective="binary", class_weight="balanced", importance_type="gain", random_state=42)`
  생성 코드를 그대로 함수로 뺀 것 — 동작 변경 없음.
- `preprocess_fold: Callable[[pd.DataFrame, pd.DataFrame], tuple[pd.DataFrame, pd.DataFrame]] | None = None`
  — `(train_X, test_X)`를 받아 전처리된 `(train_X_fit, test_X_fit)`를 반환. `None`이면
  지금처럼 `market` 컬럼만 `category` dtype으로 cast하는 기본 전처리(`_default_preprocess`)를 쓴다.
- `save_model: bool = True` — `False`면 모델 파일/JSON 사이드카 저장을 건너뛴다
  (비교/튜닝 스크립트가 `data/regime_ml_models/`를 오염시키지 않도록).

**반환 타입 변경**: 지금은 fold별 리포트 `list[dict]`만 반환하고 pooled
metrics(`weighted_kappa` 등)는 함수 내부에서 계산 후 sidecar JSON에만 기록한다.
비교/튜닝 스크립트는 pooled kappa가 반드시 필요하므로, 반환값을 아래 데이터클래스로
바꾼다:

```python
@dataclass
class TrainingResult:
    reports: list[dict]       # 기존 반환값과 동일한 fold별 리포트
    pooled: dict               # compute_classification_metrics()의 pooled 결과
    per_market: dict[str, dict]
```

`tests/test_train_regime_ml.py`의 기존 9개 테스트와 `main()`을 `result.reports`/
`result.pooled` 접근 방식으로 기계적으로 수정한다(동작 검증 내용은 변경 없음).

**LightGBM 전용 후처리 가드**: `top_features`(gain 기반 `feature_importances_`)와
모델 저장(`last_model.booster_.save_model(...)`)은 LightGBM에만 있는 속성이다.
`hasattr(model, "feature_importances_")`로 가드해 없으면 `top_features=[]`,
`save_model=False`일 때는 저장 블록 전체를 건너뛴다.

### `scripts/compare_regime_ml_baseline.py` (신규)

`run_training()`을 두 번 호출한다 — 마켓/기간/폴드 설정은 `train_regime_ml.py`의
상수(`TRAINING_MARKETS`, `TIMEFRAME`, `TRAIN_START`, `TRAIN_END`, `N_FOLDS`,
`MIN_TRAIN_SAMPLES`, `BARRIER_K`)를 그대로 import해 재사용(중복 정의 금지):

1. `model_factory=_default_lgbm_factory`(=현재 배포 모델과 동일 설정), `preprocess_fold=None`
2. `model_factory=lambda: LogisticRegression(penalty="l2", class_weight="balanced", max_iter=1000, random_state=42)`,
   `preprocess_fold=_lr_preprocess`

둘 다 `save_model=False`. 결과를 pooled weighted kappa/macro F1 표로 나란히 출력하고,
"LightGBM이 우세/LR이 우세/거의 동일(자연변동폭 ±0.005 이내)" 결론 문구를 자동 출력한다.

**`_lr_preprocess(train_X, test_X)`** (스크립트 로컬 함수, 프로덕션 서빙 경로에서
쓰이지 않으므로 `engine/`이 아닌 스크립트 안에 둔다):
- 수치 피처: `SimpleImputer(strategy="median")` → `StandardScaler` — `train_X`에만
  `fit`, `test_X`는 `transform`만(fold 간 leakage 방지, 기존 embargo 원칙과 동일선상)
- `market` 컬럼: `OneHotEncoder(handle_unknown="ignore")` — train에서 관측된
  마켓만으로 `fit`(현재 모든 fold가 같은 20마켓 집합을 쓰므로 실질적으로 문제 없음)

### `scripts/tune_regime_ml_hyperparams.py` (신규)

`model_factory`만 바꿔가며 `run_training()`을 반복 호출하는 2단계 축소 그리드:

- **1단계**: `num_leaves ∈ {15, 31, 63}` × `learning_rate ∈ {0.01, 0.05, 0.1}` ×
  `min_child_samples ∈ {10, 20, 50}` = 27조합. 나머지는 `_default_lgbm_factory`와
  동일(`objective="binary", class_weight="balanced", importance_type="gain", random_state=42`).
  pooled weighted kappa가 가장 높은 조합을 1단계 최적값으로 고정.
- **2단계**: 1단계 최적값 고정 + `reg_alpha ∈ {0.0, 0.1, 1.0}` × `reg_lambda ∈ {0.0, 0.1, 1.0}`
  = 9조합 추가 탐색.
- 최종적으로 전체 36조합 중 최고 pooled kappa와 그 하이퍼파라미터 조합을 baseline(0.108)과
  비교해 출력.

**실행시간 안전장치**: 스크립트 시작 시 baseline 설정으로 1회 학습(5-fold)을 먼저
실행해 소요시간을 측정하고 "예상 총 소요시간 = 측정시간 × 37"을 출력한다. 이 값이
과도하게 크면(예: 60분 초과) 그리드를 줄이라는 안내만 출력하고 계속 진행 여부는
사용자 판단에 맡긴다(자동으로 중단하지 않음 — 백그라운드 실행 가능하므로).

## 에러 처리

- `preprocess_fold=None`일 때 지금 동작과 100% 동일함을 회귀 테스트로 보장.
- 그리드서치 중 특정 조합이 fold 표본부족으로 전부 스킵되면(현재 로직 그대로)
  `pooled.n == 0` → 해당 조합은 "표본 없음"으로 표시하고 최적값 후보에서 제외.
- LR 전처리에서 `SimpleImputer`가 학습 후 특정 컬럼이 전부 NaN이면(현재 스킴에서는
  발생 안 하지만 방어적으로) sklearn이 해당 컬럼을 상수 0으로 대체하는 기본 동작을
  그대로 둔다(별도 처리 안 함 — 발생 시 콘솔 경고로 충분히 드러남).

## 테스트

- `run_training`에 추가된 `model_factory`/`preprocess_fold`/`save_model` 파라미터
  동작을 검증하는 테스트 2~3개 신규 추가(`tests/test_train_regime_ml.py`):
  - 커스텀 `model_factory`(예: `DummyClassifier`)를 넘기면 그 모델이 실제로
    쓰이는지(저장된 예측이 LightGBM과 다름 등)
  - `preprocess_fold`가 넘겨받은 `(train_X, test_X)`를 그대로 변형해 반환하는지
  - `save_model=False`일 때 `model_output_dir`에 파일이 생성되지 않는지
- 기존 9개 테스트는 `TrainingResult` 접근 방식으로 기계적 업데이트(신규 동작 검증 아님).
- `compare_regime_ml_baseline.py`/`tune_regime_ml_hyperparams.py` 자체는
  `select_barrier_k.py` 선례대로 전용 테스트 없음(1회성 진단 도구, 실행 결과는
  콘솔 출력+백로그 문서 기록으로 남김).

## 범위 밖

- LR의 계수 기반 피처 랭킹(요청 범위 아님, kappa 비교만)
- 그리드서치 결과가 개선으로 판정돼도 이번 스코프에서 자동으로 `train_regime_ml.py`에
  반영하지 않음 — 실측 후 사용자와 논의해 별도로 반영(과거 라운드들과 동일 패턴)
- Optuna 등 베이지안 탐색 도입(이번은 수동 그리드로 충분, 필요시 별도 후보)
