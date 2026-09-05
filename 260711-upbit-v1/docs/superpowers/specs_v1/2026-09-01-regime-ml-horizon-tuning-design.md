# 장세 판별 ML — 예측 horizon(N_MULTIPLIER) 그리드서치 설계

## 배경

`docs/regime-ml-backlog.md`의 c-2(로지스틱회귀 baseline 비교 + LightGBM 하이퍼파라미터
튜닝) 라운드에서 "LogisticRegression이 LightGBM과 거의 동급"이라는 결과가 나왔고,
이는 병목이 트리모델의 표현력이 아니라 **피처/라벨에 담긴 신호 자체가 약하다**는
해석을 뒷받침했다. 이 세션에서 사용자가 던진 질문("horizon을 줄이거나 늘리면
쉬워지는가")을 조사하는 과정에서, 지금 라벨 horizon `n_bars=60`(1시간봉 기준
2.5일)이 **kappa 기준으로 한 번도 튜닝된 적 없는 값**임을 확인했다:

- `HALF_LIFE_DAYS = 1.0`은 2026-08-23 설계 당시 "사용자가 반응속도 '빠름' 선택"으로
  정해진 값(`docs/superpowers/plans_v1/2026-08-23-realtime-regime-detector.md`).
- `N_MULTIPLIER = 2.5`는 그 위에 곱해지는 고정 배수로, 역시 kappa 기준 실측 없이
  도입됐다.
- 반면 `barrier_k`(변동폭 배수)는 `scripts/select_barrier_k.py`로 두 차례
  실측 재탐색된 전례가 있다(1차: 클래스 분포 균형 기준, 2차: 2026-08-30 실제
  walk-forward kappa 기준 재탐색 — 0.0603→0.0658).

**목적**: barrier_k와 같은 방식으로 horizon(N_MULTIPLIER)을 kappa 기준으로
실측 비교해, 지금 값(2.5)이 최선인지 확인한다.

## 아키텍처

### `run_training()`에 `n_multiplier` 파라미터 추가 (`scripts/train_regime_ml.py`)

지금 함수 내부에서:

```python
half_life_bars = half_life_bars_for_timeframe(timeframe)
n_bars = round(half_life_bars * N_MULTIPLIER)
```

이 하드코딩된 모듈 상수 `N_MULTIPLIER`(전역, `engine/regime_math.py`) 대신
새 파라미터를 쓴다:

```python
def run_training(
    ...,
    n_multiplier: float = N_MULTIPLIER,
) -> TrainingResult:
    ...
    n_bars = round(half_life_bars * n_multiplier)
```

**`half_life_bars`(피처 EWM 윈도우, 라벨 변동성 EWM 윈도우 — `engine/regime_ml_features.py`의
`RAW_SCORE`/모멘텀/변동성 계산과 `engine/regime_ml_labels.py`의 `vol_t` 계산 양쪽에
쓰인다)는 그대로 두고 **`n_bars`(Triple Barrier 만기 — 라벨 horizon)만 바뀐다.**
이렇게 분리하는 이유: 지금 조사 대상은 "라벨을 몇 봉 뒤까지 내다보고 매길지"이지
"피처가 최근 데이터에 얼마나 빨리 반응하도록 만들지"가 아니다 — 두 축을 같이
바꾸면 "horizon 효과"와 "반응속도 효과"가 뒤섞여 해석이 어려워진다.

`embargo = timeframe_duration(timeframe) * n_bars`는 이미 `n_bars`에서 파생되므로
자동으로 함께 조정된다. `compute_triple_barrier_labels(df, half_life_bars, n_bars, barrier_k)`와
`compute_sample_uniqueness_weights(labels, n_bars)`도 이미 `n_bars`를 파라미터로
받으므로 추가 배선 없이 그대로 동작한다.

이 파라미터는 Task 1(c-2)의 `model_factory`/`preprocess_fold`/`save_model`과
같은 성격의 하위호환 확장점이다 — 기본값(`N_MULTIPLIER`, 지금 상수 2.5)을 쓰면
기존 동작과 100% 동일하다.

### 신규 스크립트 `scripts/tune_regime_ml_horizon.py`

`select_barrier_k.py`/c-2의 `compare_regime_ml_baseline.py`/
`tune_regime_ml_hyperparams.py`와 같은 성격의 1회성 진단 스크립트(scripts/에
커밋해 재실행 가능하게 유지, 전용 테스트 없음). 마켓/기간/폴드 설정은
`scripts.train_regime_ml`의 상수(`TRAINING_MARKETS`, `TIMEFRAME`, `TRAIN_START`,
`TRAIN_END`, `N_FOLDS`, `MIN_TRAIN_SAMPLES`, `BARRIER_K`, `MODEL_OUTPUT_DIR`)를
그대로 import해 재사용한다.

`_CANDIDATES = [0.5, 1.0, 1.5, 2.5, 4.0]`(2.5는 현재 프로덕션 값 — `n_bars`로
환산하면 1시간봉 기준 12/24/36/60/96시간)를 순회하며 각각
`run_training(..., n_multiplier=후보, barrier_k=BARRIER_K, save_model=False)`를
호출하고, 후보별 pooled weighted kappa/macro F1을 표로 출력한다. 최고 kappa
후보와 현재값(2.5) 대비 델타도 함께 출력한다.

**barrier_k는 5개 후보 전부 `BARRIER_K`(6.25, 현재 프로덕션 값)로 고정**한다 —
horizon이 길어질수록 만기 전 바리어를 터치할 확률이 자연히 올라가 라벨 분포가
후보마다 달라지는 걸 알고 있지만, 이번 조사 목적("horizon이 kappa에 영향을
주는가")에는 그 상태 그대로가 유의미한 관측치다. barrier_k와 horizon을 동시에
재탐색하는 조인트 그리드서치는 이번 스코프가 아니다(범위 밖 — 유망한 horizon이
나오면 후속 작업으로 검토).

## 에러 처리

- `n_multiplier`가 작을수록(horizon이 짧을수록) embargo도 작아져 fold당 표본이
  늘고, 클수록 표본이 줄 수 있다 — 기존 `min_train_samples` 가드가 이미 있어
  극단적으로 짧은/긴 horizon이 표본부족으로 스킵되면 그대로 드러난다(수동
  처리 불필요, `run_training`이 이미 처리).
- 특정 후보가 `pooled["n"] == 0`(전 fold 스킵)이면 `weighted_kappa`가 `None`이다
  — c-2의 `compare_regime_ml_baseline.py`/`tune_regime_ml_hyperparams.py`에서
  이미 추가한 None 가드와 같은 패턴으로 처리한다(포맷팅 크래시 대신 "표본
  없음"으로 표시하고 최적값 후보에서 제외).

## 테스트

- `tests/test_train_regime_ml.py`에 `n_multiplier` 파라미터가 실제로
  `n_bars`(라벨/embargo)에 반영되는지 확인하는 테스트 1~2개 추가:
  - 커스텀 `n_multiplier`를 넘기면 기본값과 다른 `n_bars`가 쓰였는지(예: 라벨
    분포가 달라지거나, embargo로 인해 fold별 train/test 표본 수가 달라짐을 확인)
  - 기본값(`n_multiplier` 생략)일 때 기존 동작과 100% 동일한지(회귀 없음)
- 기존 9+3=12개 테스트(c-2에서 이미 마이그레이션됨)는 영향 없음 — 기본값
  유지로 그대로 통과해야 한다.
- `tune_regime_ml_horizon.py` 자체는 `select_barrier_k.py`/c-2 스크립트들과
  같은 관례로 전용 테스트 없음.

## 실행 리스크

5개 후보 × 실측 단일 학습 시간(~40분, c-2 세션에서 실측) ≈ **3.3시간**.
c-2에서 학습한 교훈대로, `disown` 없이 `run_in_background: true`로 직접
실행해 실제 완료 시점에 정확히 알림을 받는다(백그라운드 셸 안에서 다시
`&`/`disown`으로 감싸면 도구가 즉시 "완료"로 오인식하는 문제가 있었음).

## 범위 밖

- barrier_k와 horizon의 조인트 그리드서치(유망한 horizon이 나오면 후속 검토).
- `HALF_LIFE_DAYS`(피처 반응속도) 자체를 바꾸는 실험.
- 튜닝 결과를 `engine/regime_math.py`의 `N_MULTIPLIER` 프로덕션 상수에 자동
  반영하는 것 — 결과가 개선으로 나와도 사용자와 별도 논의 후 다음 세션에서.
