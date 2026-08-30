# 장세 판별 ML 모델 성능 개선 설계 (2026-08-31)

## 배경

`docs/regime-ml-backlog.md`의 우선순위 1(② 모델 성능 대폭 개선) 착수. 전제 조건은
① fact 라벨 백테스트로 이미 충족 확인됨(장세 조건부 전략 전환이 실제로 유의미한
수익 차이를 만듦). 조사 결과는 `docs/ML_Regime_Switching_Additional_Improvements.md`에
정리되어 있고, 이 문서의 섹션 4(우선순위 제안 1~6) 중 사용자가 이번 세션 범위로
선택한 1~4번을 다룬다. 5번(로지스틱회귀 baseline + LightGBM 하이퍼파라미터 튜닝),
6번(메타 레이블링)은 범위 밖.

현재 baseline: 20마켓 풀링, 이진분류(하락/하락아님), barrier_k=6.25, walk-forward
pooled weighted kappa **0.097**, macro F1 0.538 (`docs/regime-ml-backlog.md` B 항목).

## 평가 프로토콜(공통)

- `scripts/train_regime_ml.py`가 리포트하는 pooled weighted kappa를 1순위,
  macro F1을 2순위 지표로 삼는다.
- 항목 ①→②→③→④ 순서로 하나씩 독립적으로 ablation한 뒤, kappa가 개선되면 다음
  항목의 베이스라인으로 채택하고, 악화되면 그 항목만 폐기한다(누적 채택 방식 —
  기존 라운드들과 동일).
- 각 단계 사이에 재확인 질문 없이 자동으로 다음 항목으로 진행한다. 세션 끝에
  최종 채택 항목과 최종 kappa를 요약 보고한다.
- 최종 모델의 AWS 배포 여부는 전체 작업이 끝난 뒤 별도로 사용자에게 확인한다
  (배포는 이 설계의 범위 밖 — `[[upbit-v1-dont-push-on-empirical-regression]]`
  메모리 원칙에 따름).

## ① Threshold 튜닝 + 확률 보정 (재학습 불필요)

- 신규 스크립트 `scripts/analyze_threshold_calibration.py`: `train_regime_ml.py`의
  walk-forward 루프(같은 fold 경계, 같은 markets/feature/label 파이프라인)를
  재사용하되, fold별 `model.predict()` 대신 `model.predict_proba()`를 모아
  전체 fold의 out-of-fold 확률을 pool한다(각 fold는 그 fold 시점까지의 데이터로만
  학습하므로 여전히 미래정보 누수 없음).
- Pooled 확률로 precision-recall curve를 그려 목표 precision(예: 55%+) 대비 recall
  트레이드오프를 표로 출력.
- Isotonic regression(`sklearn.isotonic.IsotonicRegression`)으로 확률 보정 후
  같은 분석을 반복 — 보정이 threshold 선택을 더 안정적으로 만드는지 비교.
- **채택 기준**: 이 항목은 kappa 자체를 바꾸지 않는다(같은 모델의 decision
  threshold만 바꾸는 것) — 대신 "목표 precision에서 recall이 실용적으로 남아있는지"로
  채택 여부를 판단한다. 채택되면 `regime_ml_service.py::predict_current_ml_regime`의
  argmax 방식을 "보정된 확률 + 커스텀 threshold"로 교체하고, threshold와 isotonic
  보정 파라미터(x/y 브레이크포인트)를 모델 학습 시 sidecar json에 저장해
  `train_regime_ml.py`도 함께 갱신한다.

## ② 구조 개선 — sample uniqueness 가중치 + vol_t shift

### Sample uniqueness 가중치

- `train_regime_ml.py`의 각 fold 학습 직전에, train 구간 라벨들의 시점별 동시활성
  개수 c_t를 계산한다: 라벨 i의 활성 구간은 `[t_i, t_i + n_bars]`이므로, 각 시점
  t에서 활성인 라벨 개수를 rolling sum으로 구한 뒤, 라벨 i의 uniqueness weight =
  `mean(1/c_t for t in [t_i, t_i+n_bars])`.
- `model.fit(train_X_fit, train_y, sample_weight=uniqueness_weights)`로 전달.
  LightGBM sklearn API는 `class_weight="balanced"`와 `sample_weight`를 함께 받으면
  곱해서 적용하므로 두 축이 동시에 유지된다.
- 마켓별로 c_t를 따로 계산한다(마켓 간 라벨은 시간축이 같아도 서로 독립 이벤트).

### vol_t shift

- `engine/regime_ml_labels.py::compute_triple_barrier_labels`의
  `volatility = returns.ewm(halflife=half_life_bars).std()`가 t 시점 자기 자신의
  수익률까지 포함해 계산됨(백로그 기술부채 항목, 급락 봉이 "하락아님"으로
  오라벨링되는 원인). `volatility.shift(1)`로 변경해 t 시점 barrier 폭이 t-1까지의
  정보로만 결정되게 한다.
- 이 변경은 `compute_triple_barrier_labels`의 반환값 자체가 바뀌므로 라벨을
  다시 생성해야 한다(재학습 필요).

### 실행 순서

1. sample uniqueness만 단독 ablation
2. vol_t shift만 단독 ablation
3. 둘 다 개선이면 조합, 하나만 개선이면 그것만 채택
4. CUSUM 이벤트 샘플링은 이번 세션에서 시도하지 않음(문서상 sample uniqueness와
   "또는" 관계 — sample uniqueness가 유의미한 개선을 못 주면 다음 세션 후보로
   백로그에 남긴다)

## ③ 베타중립 cross-sectional 피처

- `engine/regime_ml_features.py`에 신규 피처 2개 추가:
  - `BETA_NEUTRAL_RETURN`: 이 마켓의 수익률 − BTC 수익률(같은 시점)
  - `CROSS_SECTIONAL_RANK`: 같은 시각 20개 학습 마켓 수익률 중 이 마켓의
    백분위 순위
- 학습 파이프라인(`scripts/train_regime_ml.py::run_training`)은 이미 전체
  마켓의 `raw_df`를 한 번에 로드하므로, candle_time 기준으로 전체 마켓 수익률을
  pivot(`DataFrame`, index=candle_time, columns=market)한 뒤 각 마켓 feature
  프레임에 rank/잔차를 병합하는 함수를 `engine/regime_ml_features.py`(또는
  신규 `engine/regime_ml_cross_sectional.py`)에 추가한다.
- **서빙 트레이드오프**: `backend/regime_ml_service.py::predict_current_ml_regime`은
  현재 단일 마켓만 로드한다. 이 피처가 채택되면 예측 시에도 20마켓 전체를
  새로 불러와야 하므로(캔들 API 호출 20배 증가) 지연시간이 늘어난다. Ablation
  결과가 유의미한 개선일 때만 이 비용을 감수하고 서빙 코드에 반영한다 — 개선이
  미미하면 이 피처는 폐기하고 서빙 변경도 하지 않는다.

## ④ 캔들 결측 구간 스캔

- 1회성 분석 스크립트(스크래치, 커밋 여부는 결과 보고 후 결정): 20개 학습
  마켓 각각에 대해 `get_candles(market, "minutes60", TRAIN_START, now)`를 불러와
  `candle_time` 간격이 1시간을 벗어나는 지점(결측 구간)을 찾아 마켓별
  빈도/총 결측시간 분포를 표로 출력.
- **분기 기준**: 결측 규모가 유의미하면(예: 특정 마켓에서 결측 시간 총합이
  전체 기간의 일정 비율 이상, 또는 라벨 개수 대비 결측 걸친 라벨 비율이
  체감될 수준) `compute_triple_barrier_labels`에서 결측 구간을 걸친 라벨을
  NaN 처리(학습 제외)하는 보정을 구현하고 재학습 ablation. 미미하면 스캔
  결과만 `docs/regime-ml-backlog.md`에 기록하고 코드 변경 없이 종료.

## 범위 밖

- 로지스틱회귀 baseline 비교, LightGBM 하이퍼파라미터 튜닝(문서 우선순위 5번)
- 메타 레이블링(문서 우선순위 6번)
- CUSUM 이벤트 샘플링(② 참고, 이번 세션 조건부 보류)
- 최종 모델의 AWS 배포 실행(세션 끝에 별도 확인)
- PBO/CPCV 검증(문서 1-3, 이번 4개 항목과 별도 축 — 다음 백로그 후보)
