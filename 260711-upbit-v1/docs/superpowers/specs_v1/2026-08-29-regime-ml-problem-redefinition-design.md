# 장세 판별 ML 문제 재정의 — 설계 스펙

## 배경

[[2026-08-27-regime-detector-ml-classifier-design]]에서 규칙기반을 ML(LightGBM)로 전환했지만 풀링 상관계수가 3마켓 기준 0.077에 그쳤고, 2026-08-29 마켓 확장(3→14개, `9c238c6..d1c7492`)에서는 오히려 0.0378로 악화되어 배포하지 않았다([[upbit-v1-regime-ml-market-expansion-b]]). 사용자가 직접 정리한 `docs/ML_Regime_Switching_Improvement_Plan.md`(이하 "개선안 문서")를 계기로, 이번 세션은 기존 파이프라인을 유지보수하는 대신 **문제 정의(레이블+분류체계+평가방식) 자체를 다시 설계**한다. 사용자 코멘트: "지금 ML은 거의 사용이 불가능한 수준입니다."

브레인스토밍에서 다음이 확정됐다:

- 신규 외부데이터(트위터 실시간 감성분석 등)는 별도 인프라 프로젝트로 분리 — 이번 스펙 비범위
- 지난 세션에 규칙기반 판별기를 완전히 삭제한 결정([[upbit-v1-regime-rule-based-removal]])은 유지 — 개선안 문서의 "제안 B: 규칙기반 필터+ML 하이브리드"는 채택하지 않음
- 이 ML 예측은 아직 `trading/`(실거래) 코드가 참조하지 않는 대시보드 전용 기능이며, 이번 라운드는 "예측 자체의 신뢰도를 올리는 것"까지가 범위. "예측 → 실시간 전략 전환 연동"은 예측력이 확보된 뒤 별도 세션에서 브레인스토밍한다
- 코인별 예측 요구는 "코인마다 완전 독립 모델(14개)"이 아니라 "**공유 모델 + 코인별 차별화 피처 + 마켓별 평가 분리**"로 간다 — 신규상장 코인(KRW-TRUMP 등)의 데이터 부족 문제를 키우지 않기 위함
- 모델 계열은 LightGBM 지도학습 유지(개선안 문서의 "제안 C: HMM 비지도 군집화"는 이번 결과가 여전히 부진할 때만 후속 검토)

## 목표

1. 레이블링을 Triple Barrier 방식으로, 분류체계를 5단계→3단계(하락/횡보/상승)로 재정의해 fold 간 레이블 정의 불안정성과 클래스 불균형을 근본적으로 완화한다
2. 코인 차별화 피처를 추가하고 평가를 마켓별로 분리해, "코인별로 예측이 갈리는지"를 실제로 확인 가능하게 한다
3. 평가지표를 (더 이상 성립하지 않는) 상관계수에서 분류 문제에 맞는 지표(macro F1/weighted kappa/confusion matrix)로 교체한다
4. 재학습 결과가 기존 `RegimeMlAdminPanel`(관리자 패널)의 모델 목록에 그대로 나타나 기존 배포 모델과 나란히 비교 가능하게 하고, 배포는 여전히 사용자가 직접 판단해 결정한다([[upbit-v1-dont-push-on-empirical-regression]] 원칙 유지)

## 비범위

- 실거래(`trading/`) 연동, 전략 자동전환 로직, Hysteresis(전환 완충) — 실거래 연동 자체가 비범위이므로 무의미
- 규칙기반 하이브리드(제안 B) — 지난 세션 결정 유지
- HMM 비지도 군집화(제안 C) — 이번 결과가 부진할 때만 후속 세션에서 검토
- 신규 외부데이터 소싱(트위터 등) — 별도 인프라 프로젝트
- 전략별 기대수익률 직접예측/메타라벨링(제안 A) — 백테스트 전략 인프라와 연동이 필요해 "실거래 연동" 범위에 속함
- 하이퍼파라미터 자동튜닝, 1시간봉 외 타임프레임 — 기존 스펙과 동일 이유로 계속 비범위
- AWS 라이브 배포 여부 결정 자체 — 코드는 GitHub까지, 실제 배포는 재학습 결과를 사용자가 확인한 뒤 별도 승인

## A. 레이블링: Triple Barrier

`engine/regime_ml_labels.py`의 `compute_normalized_realized_series`/`compute_quantile_boundaries`/`bucket_to_category`/`category_representative_scores`를 전부 제거하고, 다음 함수로 교체한다.

```
compute_triple_barrier_labels(df, half_life_bars, n_bars, k) -> pd.Series[str]
```

각 시점 t(0-indexed)에서:

1. `vol_t` = `ewm_volatility(returns[:t+1], half_life_bars)` — **t까지의 과거 수익률**로 계산한 EWM 변동성(추론 시점에도 미래 정보 없이 재현 가능해야 하므로 과거만 사용)
2. 상단 경계 `+k * vol_t`, 하단 경계 `-k * vol_t` (수익률 공간)
3. `i = 1..n_bars`에 대해 누적수익률 `cum_return[i] = close[t+i]/close[t] - 1`을 순서대로 스캔
   - `cum_return[i] >= 상단경계`를 먼저 만족하는 i가 있으면 → `"상승"`
   - `cum_return[i] <= 하단경계`를 먼저 만족하는 i가 있으면 → `"하락"`
   - 두 경계 중 더 먼저(작은 i) 터치된 쪽을 채택
4. `n_bars`까지 아무 경계도 안 터치되면 → `"횡보"`(만기 도달)
5. `t + n_bars`가 데이터 끝을 넘어가면(미래 구간 부족) → `NaN`(라벨 없음, 기존과 동일)

`n_bars`/`half_life_bars`는 기존과 동일하게 `engine/regime_math.py`의 `half_life_bars_for_timeframe` + `N_MULTIPLIER`로 산출(변경 없음). embargo 로직도 동일(fold train/test 경계에 `n_bars` 만큼의 시간 간격 유지).

**k(변동성 배수) 결정**: fold별로 다시 정하지 않고 **파이프라인 상수로 한 번만 고정**한다(기존 5단계의 "fold별 quantile 경계"가 가진 불안정성을 반복하지 않기 위함 — k는 fold의 학습구간이 아니라 전체 학습 데이터 스캔으로 정하는 하이퍼파라미터로, `N_MULTIPLIER`와 같은 성격). 구현 시 `k ∈ {0.5, 1.0, 1.5, 2.0, 2.5}` 그리드로 전체 풀링 데이터(2024-01-01~현재, 14마켓)의 3클래스 분포를 계산해, 가장 균형에 가까운(최대 클래스 비중과 33%의 차이가 가장 작은) k를 채택하고 그 값과 결과 분포를 사이드카에 기록한다.

이 그리드서치는 전체 기간(향후 test fold 구간 포함)을 보고 k를 고른다는 점에서 엄밀한 워크포워드는 아니다 — 다만 이건 "레이블 정답이 뭔지"를 바꾸는 게 아니라 "레이블을 만드는 잣대의 폭"만 정하는 것이라, 이미 비-fold별 상수로 존재하던 `N_MULTIPLIER`/`half_life_bars_for_timeframe`와 같은 성격의 파이프라인 하이퍼파라미터로 취급한다(개별 fold의 예측 성능 자체에 미래 test 라벨 값이 직접 섞여 들어가는 것과는 다름).

## B. 분류체계: 5단계 → 3단계

`CATEGORY_LABELS = ["하락", "횡보", "상승"]`로 축소. 클래스 불균형은 (1) Triple Barrier 자체가 균형에 가깝게 튜닝된 k를 쓰고 (2) `class_weight="balanced"`를 유지하는 이중 방어로 대응한다.

## C. 코인 차별화 피처 (자기상대적)

`engine/regime_ml_features.py:build_feature_matrix()`에 다음 3개를 추가한다(함수 시그니처는 변경 없음 — 여전히 단일 마켓 df만 입력받는 순수 함수):

- `LISTING_AGE_BARS`: `range(len(df))` — 이 마켓의 첫 캔들부터 경과한 봉 수. 신규상장 구간(KRW-TRUMP 등)을 모델이 식별할 수 있게 함
- `VOLATILITY_PERCENTILE`: 이 마켓의 EWM변동성(`RAW_SCORE` 계산에 이미 쓰는 것과 같은 변동성 시리즈)을 자기 자신의 과거 분포에서 백분위로 변환 — `series.rolling(WINDOW_BARS, min_periods=100).rank(pct=True)`
- `LIQUIDITY_PERCENTILE`: 거래대금(`trade_value` 또는 그 SMA)을 같은 방식으로 자기 과거 분포 대비 백분위 변환

`WINDOW_BARS = 8760`(1시간봉 기준 1년), `min_periods=100`(약 4일)로 신규상장 코인도 이른 시점부터 자기 히스토리 내에서 값이 나오게 한다.

**왜 코인 간 실시간 비교(cross-sectional rank)가 아니라 자기상대적인가**: 코인 간 비교는 추론 시점마다 14개 마켓 데이터를 전부 다시 불러와야 해서 `predict_current_ml_regime()`의 지연시간·복잡도가 크게 늘어난다. 자기상대적 방식은 학습·추론 모두 해당 마켓 데이터만으로 계산 가능해 지금 구조(단일 마켓 함수 순회)를 그대로 유지하면서 "코인마다 다른 신호"라는 목적을 달성한다.

`market` 범주형 피처는 그대로 유지(비용이 거의 없고, LightGBM이 여전히 코인별 이산적 편향을 학습할 여지를 남겨둠).

## D. 평가 프레임워크

`_correlation_from_pairs`/`category_representative_scores` 기반 상관계수 계산을 제거하고 다음으로 교체한다(`scripts/train_regime_ml.py`):

- **분류지표**: macro F1-score, weighted Cohen's kappa(3클래스는 순서형이므로 `sklearn.metrics.cohen_kappa_score(weights="linear")`), 클래스별 precision/recall, confusion matrix(3x3) — fold별 + 전체 fold 풀링(합산 confusion에서 재계산, 기존 "per-fold 값을 평균내지 않고 풀링 원본에서 재계산" 원칙 유지)
- **마켓별 분리(신규)**: 위 지표 전부를 "전체 풀링" 외에 **마켓별로도** 계산해 사이드카 `performance.per_market[market]`에 저장. `regime_ml.py` fold 루프에서 `test_X`/`test_y`/예측 결과에 `market` 컬럼을 유지해 마켓별로 그룹핑

사이드카 스키마 예시:
```json
{
  "markets": [...],
  "labeling_method": "triple_barrier",
  "barrier_k": 1.5,
  "classes": ["하락", "횡보", "상승"],
  "performance": {
    "folds": [{"fold_index": 0, "n_train": ..., "n_test": ..., "macro_f1": ..., "weighted_kappa": ...}, ...],
    "pooled": {"macro_f1": ..., "weighted_kappa": ..., "confusion": {...}, "class_precision_recall": {...}},
    "per_market": {"KRW-BTC": {"macro_f1": ..., "weighted_kappa": ..., "n_test": ...}, ...}
  }
}
```
`ref_scores`(확률→기댓값 점수 변환용 대표값)는 더 이상 필요 없어 제거(3클래스는 확률벡터 없이 직접 분류 지표로 평가하므로). 최상위 `fold_index`(어느 fold의 아티팩트가 저장됐는지)는 `performance`와 무관한 메타데이터이므로 그대로 유지 — `backend/regime_ml_service.py:predict_current_ml_regime()`의 `sidecar["fold_index"]` 참조도 변경 없음.

## E. 하위호환 및 프론트 반영

- **레거시 사이드카**(`labeling_method` 키 없음 = 이전 5단계 상관계수 모델)는 그대로 디스크에 남지만, 새 코드는 3단계만 다룬다. `backend/regime_ml_service.py`의 `_LEGACY_SIDECAR_MARKETS` 폴백 로직은 markets 검증에만 쓰이므로 그대로 유지 가능
- **프론트**: `frontend/lib/types/eda.ts`의 `RegimeCategory`를 `'하락' | '횡보' | '상승'`로 변경, `RegimeMlCurrentPrediction.tsx`의 `CATEGORY_ORDER`/`categoryVarName`/설명 텍스트(상관계수 설명 → macro F1/weighted kappa 설명)를 갱신
- **관리자 패널**(`RegimeMlAdminPanel.tsx`): 테이블에 macro F1/weighted kappa 컬럼 추가(레거시 행은 기존 상관계수 컬럼에 값이, 신규 행은 새 컬럼에 값이 채워짐 — 두 스키마가 공존하는 과도기 UI). 이 목록은 배포 전에도 보이므로, 재학습 후 기존 배포 모델과 나란히 비교한 뒤 배포 여부를 결정하는 기존 워크플로우를 그대로 재사용한다
- **배포 순서 주의**: 코드(프론트+백엔드)를 AWS에 배포하는 시점과, 그 코드가 기대하는 3단계 모델을 실제로 배포하는 시점이 어긋나면 라이브 대시보드가 깨진다(구모델은 5개 라벨을 반환하는데 새 프론트는 3개만 알고 있음). 따라서 **코드 배포와 신규 모델 배포는 반드시 같이 진행**한다 — 지금처럼 "코드는 GitHub까지, 모델은 로컬 검증 후"로 따로 미뤄두는 건 이번 건에는 적용하지 않는다

## F. 테스트

- `tests/test_regime_ml_labels.py`: Triple Barrier 라벨링 재작성 — 상단/하단 경계 각각 먼저 터치되는 케이스, 둘 다 안 터치(만기)되는 케이스, 데이터 끝 근접(NaN) 케이스, "더 먼저 터치되는 쪽 채택" 케이스(상단이 하단보다 늦게 등장하면 하락으로 라벨링되는지)
- 코인 차별화 피처: `LISTING_AGE_BARS`가 0부터 시작하는지, `VOLATILITY_PERCENTILE`/`LIQUIDITY_PERCENTILE`이 `min_periods` 이전엔 NaN이고 이후 [0,1] 범위인지
- `tests/test_train_regime_ml.py`: 마켓별 성능 분리가 실제로 마켓 필터링해 계산되는지(합계가 풀링과 일치하는지)
- 기존 5단계/상관계수를 가정한 테스트(`tests/test_backend.py`, `tests/test_cache.py`, `tests/test_regime_ml_service.py`의 관련 부분)는 3단계 기준으로 갱신

## 성공 기준

절대적인 숫자 목표를 미리 못박지 않는다(개선안 문서 자체가 진단이지 보장이 아님). 재학습 후 관리자 패널에서 확인할 최소 기준:

- 3개 클래스 각각의 재현율이 무작위/다수클래스 베이스라인(33%/횡보 쏠림)보다 눈에 띄게 나은지
- confusion matrix가 대각선(정답 클래스) 쪽으로 유의미하게 쏠려 있는지(이전처럼 전부 "횡보"로 쏠리지 않는지)
- `per_market` 지표를 봤을 때 마켓 간에 실제로 차이가 나는지(코인별 차별화가 작동하는 증거)

이 중 하나라도 명백히 개선되지 않으면 배포하지 않고, HMM(제안 C) 등 다른 파라다임을 다음 세션에서 검토한다.

## 결정 요약 (재작업 시 참고)

| 항목 | 선택 | 비고 |
|---|---|---|
| 라벨링 | Triple Barrier(익절/손절/만기) | fold별 quantile 경계의 불안정성 제거 |
| 분류체계 | 3단계(하락/횡보/상승) | 5단계 경계 모호성+불균형 완화 |
| barrier k | 전체 데이터 그리드서치로 1회 고정 | fold별 재산정 안 함(N_MULTIPLIER와 같은 성격의 상수) |
| 모델 구조 | 공유 LightGBM 풀링 모델 유지 | 코인별 완전 독립 모델은 기각(데이터 부족 코인 리스크) |
| 코인 차별화 | 자기상대적 피처 3종(상장경과/변동성백분위/유동성백분위) | 코인 간 실시간 비교는 추론 지연시간 문제로 기각 |
| 평가지표 | macro F1/weighted kappa/confusion matrix | 상관계수는 3클래스 카테고리형 라벨과 개념적으로 안 맞음 |
| 평가 단위 | 풀링 + 마켓별 분리 둘 다 | "코인별 예측이 갈리는지" 확인이 이번 요구의 핵심 |
| 실거래 연동 | 비범위 | 예측력 확보 후 별도 세션 |
| 하이브리드/HMM/신규외부데이터 | 비범위(HMM은 조건부 후속) | 지난 세션 결정 유지 + 범위 폭발 방지 |
| 배포 | 코드+모델 동시 배포 원칙, 배포 여부는 사용자 승인 | 구/신 스키마 혼재로 인한 대시보드 깨짐 방지 |
