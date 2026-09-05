# 장세 판별 ML — 메타 레이블링(c-3) 학습+측정 설계

## 배경

`docs/regime-ml-backlog.md`의 잔여 후보 (c) 중 c-3. c-2(로지스틱회귀 baseline
비교)와 horizon 그리드서치 두 라운드 모두 pooled weighted kappa가 자연변동폭
(±0.005) 안에서 맴돌아, "피처/모델/horizon을 더 조정해 kappa를 올리는" 접근은
사실상 한계에 도달한 것으로 판단했다(`docs/regime-ml-backlog.md` c-2/horizon
절 참고). 메타 레이블링은 kappa 자체가 아니라 **"하락" 경고의 precision**을
목표로 하는, 지금까지와 질적으로 다른 시도다(AFML 표준 기법,
`docs/ML_Regime_Switching_Additional_Improvements.md` 3-2절).

**현재 상태**: `engine/regime_ml_calibration.py`의 threshold 튜닝만으로는
`TARGET_DOWN_PRECISION=0.55`를 달성 못 한다 — 그리드 내 최고 precision이
threshold=0.70에서 39%대(recall 10.8%), 폴백(threshold=0.90)은 precision
40%대에 recall 0.3~0.6%로 사실상 "하락" 경고를 거의 안 냄. 메타 레이블링은
1차 모델의 확률 하나만 보는 threshold 튜닝과 달리, **전체 74개 피처를 다시
활용**해 "1차가 하락이라 부른 게 진짜 믿을만한가"를 판단하는 별도 분류기를
얹어 이 한계를 구조적으로 공략한다.

**목적**: 메타모델을 학습·평가해, threshold 튜닝만으로는 못 넘은 precision
장벽을 메타 게이트로 넘을 수 있는지 실측한다. 이번 라운드는 **학습+측정까지만**
— 서빙(`backend/regime_ml_service.py`) 연결배선은 결과가 좋을 경우 별도
세션에서 진행한다(범위 밖).

## 아키텍처

### `run_training()`에 `collect_oof` 파라미터 추가 (`scripts/train_regime_ml.py`)

지금 `run_training()`은 fold별 **집계 지표**(pooled kappa/macro F1 등)만
반환하고, 메타 레이블링에 필요한 **행 단위 원본 데이터**(각 시점의 피처값 +
1차 모델의 `proba_down` + 실제 라벨)는 어디에도 노출하지 않는다. 이를 위해
c-2/horizon 라운드와 같은 성격의 하위호환 확장점을 하나 더 추가한다:

```python
def run_training(
    ...,
    n_multiplier: float = N_MULTIPLIER,
    collect_oof: bool = False,
) -> TrainingResult:
```

`collect_oof=True`면 fold 루프가 테스트 구간을 채점할 때마다(이미 `all_proba_down`/
`all_true`/`all_markets`를 누적하는 지점) 아래 컬럼을 가진 레코드도 함께
누적한다: `candle_time`, `market`, `true_label`(실제 CATEGORY_LABELS 값),
`proba_down`(1차 모델의 하락 확률), 그리고 `test_X_fit`의 74개 피처 컬럼
전부. 루프가 끝나면 이 레코드들을 하나의 DataFrame으로 합쳐
`TrainingResult.oof`에 담는다. `collect_oof=False`(기본값)면 `oof=None`이고
메모리/연산 비용이 전혀 추가되지 않는다 — 기존 15개 호출부(테스트 12개 +
`main()` + `compare_regime_ml_baseline.py`/`tune_regime_ml_hyperparams.py`/
`tune_regime_ml_horizon.py`)는 전부 영향받지 않는다.

**`TrainingResult`에 필드 추가**:

```python
@dataclass
class TrainingResult:
    reports: list[dict]
    pooled: dict
    per_market: dict[str, dict]
    oof: pd.DataFrame | None = None
```

### 신규 스크립트 `scripts/train_regime_ml_meta_label.py`

`select_barrier_k.py`/c-2/horizon 스크립트들과 같은 성격의 1회성 진단
스크립트(scripts/에 커밋해 재실행 가능, 전용 테스트 없음). 마켓/기간/폴드
설정은 `scripts.train_regime_ml`의 상수를 그대로 재사용한다.

**흐름**:
1. `run_training(collect_oof=True, save_model=False)`로 1차 모델을 **한 번만**
   돌려(재학습 안 함, 지금 프로덕션과 동일한 기본 LightGBM 설정) 전체 기간의
   OOF 데이터프레임을 얻는다.
2. 후보 집합 = `oof[oof["proba_down"] >= 0.5]`(1차 모델이 "하락"으로 분류한
   모든 케이스 — 이미 보수적으로 걸러진 threshold=0.70/0.90 집합이 아니라
   기본 0.5 분류 경계 전부. 표본이 너무 적으면 메타모델을 학습할 데이터
   자체가 부족해지므로).
3. 후보 집합을 `candle_time` 기준 시간순으로 정렬해 앞 70%를 메타 학습,
   뒤 30%를 메타 테스트로 나눈다(1차처럼 다시 워크포워드하지 않는다 — 이번
   스코프는 "메타 레이블링이 원리적으로 통하는가"를 빠르게 확인하는 것이라
   단순 시간순 홀드아웃으로 충분하다는 판단, 범위 밖 절 참고).
4. 메타 라벨 = `(candidate["true_label"] == "하락")`(1차의 "하락" 판정이
   실제로 맞았는지). 메타 피처 = 후보 집합의 74개 피처 컬럼 + `proba_down`
   (1차의 확신도도 메타가 참고할 수 있게).
5. 메타모델(LightGBM, `objective="binary", class_weight="balanced",
   random_state=42` — 1차와 같은 설정 재사용)을 메타 학습 구간에 학습.
6. 메타 테스트 구간에서 메타모델의 `predict_proba`를 얻어
   `engine.regime_ml_calibration.compute_precision_recall_table`(기존 함수
   그대로 재사용 — threshold 스윕 로직이 1차와 메타에 공통)로 threshold별
   precision/recall 표를 출력한다.
7. **비교 기준 출력**: "1차만(threshold 튜닝 최선, threshold=0.70,
   precision=0.39/recall=0.108)" vs "1차+메타 게이트(메타 테스트 구간 기준)"
   표를 나란히 출력해, 같은 recall대에서 메타 게이트가 precision을 유의미하게
   올리는지 육안으로 비교 가능하게 한다.

## 에러 처리

- 후보 집합(전체 OOF 중 `proba_down>=0.5`)이 시간순 분할 후 한쪽(특히 메타
  테스트)에 500행 미만이면 "표본 부족" 경고를 출력하고 중단한다(메타모델
  학습/평가 자체가 무의미해지므로 — 1차의 `MIN_TRAIN_SAMPLES=500` 관례와
  동일한 기준 재사용).
- 메타 학습 구간의 메타 라벨이 단일 클래스만 있으면(예: 후보 전부가 실제
  하락이었거나 전부 아니었던 경우) LightGBM binary 학습이 실패한다 — 이 경우도
  표본 부족과 동일하게 경고 출력 후 중단(1차 파이프라인의 폴백 없음 원칙과
  동일 — NaN/기본값으로 조용히 넘어가지 않는다).

## 테스트

- `run_training`에 추가된 `collect_oof` 파라미터 검증 테스트 1~2개
  (`tests/test_train_regime_ml.py`):
  - `collect_oof=True`일 때 `result.oof`가 예상 컬럼(`candle_time`, `market`,
    `true_label`, `proba_down`, 74개 피처)을 가진 DataFrame인지, 행 수가
    pooled 표본 수(`result.pooled["n"]`)와 일치하는지
  - `collect_oof=False`(기본값)일 때 `result.oof is None`이고 기존 동작과
    100% 동일한지(회귀 안전장치)
- `train_regime_ml_meta_label.py` 자체는 기존 관례대로 전용 테스트 없음.

## 실행 리스크

1차 모델은 **한 번만** 돌린다(재학습 없음, `collect_oof=True`만 추가 —
c-2/horizon에서 실측한 단일 학습 시간 40분~2시간 그대로). 메타모델 학습은
후보 집합(전체의 일부, 대략 수십만 행 중 30~50% 규모로 추정)에 대한 단일
LightGBM 학습 1회뿐이라 1차보다 빠를 것으로 예상되지만, 실행 전 정확한 후보
집합 크기를 알 수 없으므로 **실행 중 소요시간을 확인하고 과도하게 길어지면
중단 여부를 사용자에게 확인**한다(c-2/horizon에서 반복된 교훈 — 설계 단계
추정을 과신하지 않는다).

## 범위 밖

- 서빙(`backend/regime_ml_service.py`) 연결배선 — 이번 라운드 결과가 채택할
  만하면 별도 세션에서 진행.
- 메타모델의 완전한 중첩(nested) 워크포워드 재검증(AFML 원래 기준 — 1차+메타를
  fold마다 함께 재학습) — 이번엔 1차 OOF 재사용 + 메타만 시간순 홀드아웃으로
  단순화(사용자 확인).
- 메타모델 자체의 하이퍼파라미터 튜닝(c-2 하이퍼파라미터 튜닝과 별개 축,
  필요시 후속 검토).
- `proba_down>=0.5`가 아닌 다른 후보 집합 정의(예: threshold=0.70 이상만)
  실험 — 표본 크기 문제로 이번엔 0.5 고정.
