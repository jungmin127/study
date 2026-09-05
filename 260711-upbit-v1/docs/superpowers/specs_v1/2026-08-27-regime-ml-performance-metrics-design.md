# 장세 판별 ML 모델 성능 지표 표기 (A2) 설계

배경: `docs/regime-ml-backlog.md` A2 항목. `8ab10b7`까지 끝난 시점 기준. 최종
우선순위(A2 → E → A1 → B → C)에서 A2를 가장 먼저 하는 이유는, E(규칙기반 제거)를
먼저 하면 "성능을 보여주는 화면"이 잠깐이라도 없어지는 공백이 생기기 때문 —
ML 카드가 먼저 자체 성능을 보여주게 만든 뒤 규칙기반을 지운다.

## 현재 상태

- `scripts/train_regime_ml.py`가 fold별 상관계수/hit-rate/confusion matrix를
  콘솔에만 출력(`_print_fold_report`, `_print_aggregate_summary`). 사이드카
  JSON(`data/regime_ml_models/*.json`)에는 `boundaries`/`ref_scores`/`classes`/
  `fold_index`만 저장됨 — 학습이 끝나면 성능 숫자가 콘솔 로그에만 남고 사라짐.
- `backend/regime_ml_service.py`의 `predict_current_ml_regime()`이 사이드카를
  로드하지만 `fold_index`만 응답에 포함, 성능 지표는 없음.
- `frontend/components/RegimeMlCurrentPrediction.tsx`(ML 현재예측 카드)는
  예측 카테고리·확률 분포만 표시. 성능 지표를 보여주는 화면은 규칙기반의
  `RegimeAccuracyReport.tsx` 하나뿐(E에서 삭제 예정).

## 목표

학습 스크립트가 계산하는 fold별/풀링 성능 지표를 사이드카에 영속화하고, 기존
ML 현재예측 API·카드에 노출해 "ML 카드가 자체 성능을 보여주는" 상태를 만든다.

## 사이드카 JSON 스키마 확장

`performance` 키 추가:

```json
{
  "boundaries": [...],
  "ref_scores": {...},
  "classes": [...],
  "fold_index": 5,
  "performance": {
    "folds": [
      {"fold_index": 1, "n_train": 2000, "n_test": 400, "correlation": 0.06},
      {"fold_index": 2, "n_train": 3500, "n_test": 500, "correlation": 0.09}
    ],
    "pooled_correlation": 0.11,
    "pooled_hit_rate": {
      "급상승": 0.32, "완만상승": 0.28, "횡보": 0.45,
      "완만하락": 0.30, "급하락": 0.35
    }
  }
}
```

- `folds`: `run_training()`이 실제로 평가한(표본 부족으로 스킵되지 않은) 모든
  fold의 `fold_index`/`n_train`/`n_test`/`correlation`. 배포되는 모델은
  마지막 fold(top-level `fold_index`와 동일)만이지만, 학습 접근법 전체의
  안정성(fold 간 편차)을 보여주기 위해 전체를 저장한다.
- `pooled_correlation`: `_print_aggregate_summary`가 이미 계산하는, 모든 fold의
  `(expected_score, actual_value)` 쌍을 fold 경계 없이 풀링해 재계산한
  상관계수. 표본 2개 미만이면 `null`.
- `pooled_hit_rate`: 모든 fold의 confusion matrix를 elementwise로 합산한
  뒤(`_sum_confusion_matrices`) 카테고리별로 계산한 hit-rate(적중/예측총합).
  분모가 0인 카테고리는 `null`.
- fold별 hit-rate/confusion matrix는 저장하지 않음(5카테고리×N fold로
  화면에 넣기엔 과도하게 촘촘함 — D 항목의 "과거 임의 시점 조회"와도 다른
  범위이며, 필요해지면 그때 추가).

### 리팩터: 계산 로직 공유

현재 `_print_correlation_block`/`_print_hit_rate_block`은 출력 전용이고, 상관계수/
hit-rate 계산 로직이 `run_training()`(fold별 상관계수, 168~171행 부근)과
`_print_aggregate_summary()`(풀링 상관계수) 두 곳에 흩어져 있다. 사이드카 저장
시점에 같은 값을 다시 계산해야 하므로, 계산과 출력을 분리한다:

```python
def _correlation_from_pairs(expected_scores: list[float], actual_values: list[float]) -> float | None:
    """(expected_score, actual_value) 쌍에서 피어슨 상관계수를 계산한다. fold 하나의
    쌍이든, 여러 fold를 풀링한 쌍이든 계산 방식은 동일하므로 양쪽에서 공유한다."""
    if len(expected_scores) < 2:
        return None
    computed = float(np.corrcoef(expected_scores, actual_values)[0, 1])
    return None if np.isnan(computed) else computed


def _compute_hit_rate(confusion: dict[str, dict[str, int]]) -> dict[str, float | None]:
    hit_rate: dict[str, float | None] = {}
    for label in CATEGORY_LABELS:
        row = confusion[label]
        total = sum(row.values())
        hit_rate[label] = (row[label] / total) if total else None
    return hit_rate
```

- fold별 상관계수 계산(현재 `run_training()` 안에 150~154행 인라인)과
  `_print_aggregate_summary()`의 풀링 상관계수 계산을 모두 `_correlation_from_pairs()`
  호출로 교체(fold별 호출은 해당 fold의 `(expected_scores, actual_values)`만
  넘기면 동일한 함수로 커버됨).
- `_print_hit_rate_block()`은 내부에서 `_compute_hit_rate()`를 호출하도록 리팩터.

`run_training()`의 모델 저장 블록(현재 173~186행)에서:

```python
if last_model is not None:
    ...
    pooled_confusion = _sum_confusion_matrices(reports)
    pooled_correlation = _correlation_from_pairs(all_expected_scores, all_actual_values)
    pooled_hit_rate = _compute_hit_rate(pooled_confusion)

    sidecar = {
        "boundaries": last_boundaries,
        "ref_scores": last_ref_scores,
        "classes": last_class_order,
        "fold_index": last_fold_index,
        "performance": {
            "folds": [
                {
                    "fold_index": r["fold_index"],
                    "n_train": r["n_train"],
                    "n_test": r["n_test"],
                    "correlation": r["correlation"],
                }
                for r in reports
            ],
            "pooled_correlation": pooled_correlation,
            "pooled_hit_rate": pooled_hit_rate,
        },
    }
```

## 백엔드 API

새 엔드포인트를 만들지 않는다 — 기존
`GET /api/v1/regime/ml-current-prediction`(`backend/regime_ml_service.py:predict_current_ml_regime`)이
이미 사이드카 전체를 로드하고 있으므로, 응답에 필드 하나만 추가한다:

```python
return {
    "predicted_category": predicted_category,
    "probs": probs,
    "model_trained_at": _parse_trained_at(model_path),
    "model_fold_index": sidecar["fold_index"],
    "bar_time": bar_time,
    "model_performance": sidecar.get("performance"),
}
```

`.get()`을 쓰는 이유: 이 변경 이전에 학습된 사이드카(`performance` 키 없음)를
서버가 아직 들고 있는 상태에서 코드만 먼저 배포되는 경우를 방어한다. 이런
사이드카를 만나면 `model_performance`는 `null`이 되고 프론트가 안내 문구를
보여준다(500 에러 대신). `data/`는 `.gitignore` 대상이라 이 변경 자체와 함께
로컬 재학습 → 모델 재배포까지 짝지어 진행할 것이므로 운영상 이 경로를 오래
타지는 않지만, 방어 코드 자체는 유지한다.

## 프론트엔드

`frontend/lib/types/eda.ts`에 타입 추가:

```typescript
export interface MlFoldPerformance {
  fold_index: number;
  n_train: number;
  n_test: number;
  correlation: number | null;
}

export interface MlModelPerformance {
  folds: MlFoldPerformance[];
  pooled_correlation: number | null;
  pooled_hit_rate: Record<RegimeCategory, number | null>;
}

export interface MlCurrentPrediction {
  predicted_category: RegimeCategory;
  probs: Record<RegimeCategory, number>;
  bar_time: string;
  model_trained_at: string;
  model_fold_index: number;
  model_performance: MlModelPerformance | null;
}
```

`RegimeMlCurrentPrediction.tsx`의 기존 카드(예측 카테고리 + 확률 분포 + 캡션)
아래에 구분선으로 나뉜 "모델 성능" 섹션을 신설:

- fold 표: 열은 fold/train/test/상관계수. `model_fold_index`와 일치하는 행은
  `font-semibold`로 강조(현재 배포된 모델이 어느 fold인지 표에서 바로 보이게).
  상관계수 `null`은 `-`로 표시.
- 풀링 상관계수 한 줄: `풀링 상관계수: {pooled_correlation?.toFixed(3) ?? '계산 불가'}`
- 카테고리별 hit-rate 한 줄: `CATEGORY_ORDER` 순서로 5개 퍼센트 나열(값
  `null`인 카테고리는 `-`).
- `model_performance`가 `null`이면 표 대신
  `"성능 지표 없음(모델을 재학습하면 표시됩니다)"` 안내문 한 줄만 표시.

기존 `TRAINED_MARKETS`/`timeframe !== 'minutes60'` 가드는 그대로 유지 —
이 섹션은 `data` 블록 안(현재 74~103행)에 이어서 추가되는 것이라 별도 가드가
필요 없다.

## 테스트

- `tests/test_train_regime_ml.py`
  - `test_run_training_saves_json_sidecar_alongside_model`: 현재
    `assert set(sidecar.keys()) == {...}` 단언에 `"performance"` 추가하고,
    `sidecar["performance"]["folds"]`가 `reports`와 같은 개수·같은
    `fold_index`/`n_train`/`n_test`/`correlation` 값을 담는지, `pooled_correlation`이
    `np.corrcoef`로 독립 재계산한 값과 일치하는지 검증.
  - 새 테스트: fold 하나가 표본 부족으로 스킵되는 픽스처를 만들어(기존
    `test_run_training_skips_folds_below_min_train_samples` 패턴 재사용),
    `performance.folds`에 스킵된 fold가 없는지 확인.
  - 새 테스트: `_compute_hit_rate()`를 가짜 confusion matrix로 직접 호출해
    분모 0인 카테고리가 `None`을 반환하는지 단위 검증(기존
    `test_aggregate_confusion_and_totals_sum_across_folds`와 같은 스타일).
- `tests/test_regime_ml_service.py`
  - `_train_and_save_tiny_model()` 헬퍼에 `performance` 딕셔너리를 선택적으로
    받는 파라미터 추가(기본값 `None` → 현재처럼 키 자체를 안 넣음, 명시하면
    포함).
  - 새 테스트: `performance` 있는 사이드카로 `predict_current_ml_regime()`을
    호출해 `result["model_performance"]`가 그대로 전달되는지 확인.
  - 새 테스트: 기존 헬퍼 기본값(= `performance` 키 없는 구형 사이드카)으로 호출한
    기존 `test_predict_current_ml_regime_returns_valid_response`에
    `result["model_performance"] is None` 단언 추가(하위 호환 확인).

## 비범위

- fold별 hit-rate/confusion matrix 사이드카 저장(위 "사이드카 JSON 스키마
  확장" 참고 — 필요해지면 별도로 추가).
- 과거 임의 시점의 정확도 조회(백로그 D 항목, 계속 비범위).
- 새 API 엔드포인트(기존 엔드포인트 재사용으로 충분).
