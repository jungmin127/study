# ML 기반 장세 판별 모델 — 추가 개선 포인트 조사 (2026-08-31)

`docs/ML_Regime_Switching_Improvement_Plan.md`(사용자 최초 진단: 문제 재정의, 클래스
불균형, Triple Barrier, HMM, Hysteresis)와 겹치지 않는 항목만 정리한다. 코드
(`engine/regime_ml_features.py`, `regime_ml_labels.py`, `regime_ml_splits.py`,
`scripts/train_regime_ml.py`)와 금융 ML 학계/Kaggle 사례를 함께 조사했다.

## 1. 코드에서 실제로 확인한 근본적 구조 문제

### 1-1. 라벨 중첩(overlapping labels) — 표본이 사실상 독립이 아님 [최우선]

`compute_triple_barrier_labels`가 **매 시간봉마다** 라벨을 만들고, 각 라벨은 최대
n_bars(~60시간) 앞을 내다본다. 인접한 t, t+1, t+2… 시점의 라벨 윈도우가 대부분
겹치므로 실질적으로 심하게 자기상관된 표본이다. `regime_ml_splits.py`의 embargo는
train/test **경계**만 막을 뿐, train 셋 **내부**의 중첩은 그대로 방치한다.
LightGBM은 이걸 서로 다른 관측치로 취급해 같은 패턴을 여러 번 반복학습하게 되고,
gain 기반 feature importance나 in-fold 성능이 부풀려질 수 있다.

López de Prado(AFML)의 표준 해법 두 가지가 모두 미적용 상태:

- **Sample uniqueness 가중치**: 각 시점 t의 동시활성 라벨 개수 c_t로 1/c_t를
  계산해 `sample_weight`로 LightGBM에 전달(지금의 `class_weight="balanced"`와는
  별개 축)
- **CUSUM 필터 이벤트 샘플링**: 매 시간봉이 아니라 누적 수익률이 임계값을 넘는
  "의미있는 이벤트" 시점에서만 샘플링 — 표본 수는 줄지만 정보 밀도가 높아짐

백로그(`docs/regime-ml-backlog.md`)의 "문제 재정의 후보 (b)"(멀티 horizon
앙상블)와는 다른 축의 문제다 — horizon 개수가 아니라 **표본 자체의 독립성**이다.

### 1-2. 의사결정 threshold가 0.5 고정 — Precision 36.8% 문제와 직결

`model.predict()`는 내부적으로 0.5 확률 컷을 쓴다. `class_weight="balanced"`는
클래스 불균형을 보정하지만 "하락 경고를 얼마나 신중하게 낼지"는 별도 문제다.
"경고 3번 중 2번 오보"는 모델 예측력 자체보다 이 threshold 미조정 때문일 가능성이
있다.

- `predict_proba()`로 precision-recall curve를 그려서 원하는 precision(예 55%+)에
  맞는 threshold를 잡고, 그 대가로 recall이 얼마나 줄어드는지 확인
- LightGBM binary 확률은 종종 왜곡되어 있어 **Platt scaling/isotonic regression으로
  확률 보정(calibration)** 후 threshold를 잡아야 신뢰 가능
- 사용자 문서 4-2의 Hysteresis(확신도 threshold)는 이 작업의 상위 개념 — threshold
  자체부터 먼저 최적화해야 함
- **재학습 불필요, 기존 저장 모델의 predict_proba만으로 바로 실험 가능** — 비용
  대비 가장 빠른 시도

### 1-3. 다중 시행에 의한 selection bias — PBO(Probability of Backtest Overfitting)

이 프로젝트는 이미 barrier_k 그리드서치, LISTING_AGE/FEAR_GREED/HMM 등 여러
ablation을 거쳤다. 여러 번 시도해서 제일 좋은 결과를 채택하는 것 자체가 일종의
selection bias다. Combinatorial Purged CV(CPCV)로 여러 조합의 train/test 분할을
만들어 backtest path를 다양화하고, PBO/Deflated Sharpe 개념으로 "이 kappa 개선이
진짜 신호인지 fold 조합 운인지" 검증하는 게 원칙적으로는 필요하다.

## 2. 피처 엔지니어링 — 미탐색 영역

### 2-1. 베타 중립화(cross-sectional relative return) — Kaggle 상위권 공통 인사이트

Kaggle **G-Research Crypto Forecasting**(15분 뒤 여러 코인 수익률 예측, target이
시장 전체 대비 상대순위/가중상관) 대회의 상위권 공통점은 "feature engineering이
모델 선택보다 압도적으로 중요했다"는 것과, BTC/시장 전체 베타를 제거한
**잔차 수익률**을 피처로 쓴 것이다. 현재 `build_feature_matrix`는 btc_close
레벨은 참조하지만 "이 코인 수익률 − BTC 수익률"(베타 중립 잔차)이나 "20개 마켓 중
오늘 상대적으로 몇 등인가"(cross-sectional rank) 피처는 없어 보인다. 알트코인
대부분이 BTC와 강하게 동조하므로, 종목 고유 신호만 걸러내는 이 피처축이 게인이
클 가능성이 있다.

### 2-2. 변동성의 변화율(vol-of-vol) 피처

크립토는 GARCH조차 못 따라갈 정도로 변동성 군집이 극단적이라는 게 최근 연구
방향이다. GARCH를 직접 적합하기보다, "최근 변동성 자체가 앞으로의 변동성을
예측한다"는 성질을 명시적 피처로(예: realized vol의 여러 lag, 변동성의 가속도)
넣는 게 실용적이다. 지금 `VOLATILITY_PERCENTILE`은 있지만 "변동성 레짐이 방금
전환됐는지"를 잡는 피처는 없다.

### 2-3. 피처 중요도 재검증 — MDA(Permutation Importance)

지금 `top_features`는 LightGBM gain 기반인데, gain은 고유값이 많거나 노이즈가
낀 피처를 과대평가하는 경향이 있다(트리모델의 흔한 함정). MDA로 재검증하면 이미
제거된 LISTING_AGE_BARS류(캘린더 프록시) 같은 숨은 무의미 피처가 더 있을 수
있다.

### 2-4. 캔들 결측 구간(거래 정지) 전처리 — 2026-08-31 실측으로 확인

`upbit_data_service.get_candles()`와 업비트 공식 API를 직접 재조회해 확인:
KRW-DOGE 2026-07-05 17:00~20:59(4시간)에 **거래소 자체에 체결이 없어** 60분봉이
통째로 결측이다(1분봉 레벨에서도 동일하게 확인, 캐싱/전처리 버그 아님 — 다른
마켓에도 이런 결측 구간이 더 있는지는 미확인).

`compute_triple_barrier_labels`(engine/regime_ml_labels.py)의 `future = close[t+1:
t+1+n_bars]`는 **실제 경과 시간이 아니라 행(row) 개수** 기준으로 미래를 본다.
결측 구간을 걸친 바로 그 라벨은 "n_bars(60)개 뒤"가 시계상 60시간이 아니라
결측 시간만큼(이 경우 64시간) 더 뒤를 보게 되어, `half_life_bars_for_timeframe`가
가정하는 "1시간봉 = 1시간 간격"이 깨진다. barrier 폭 계산에 쓰는 EWM
변동성(`returns.ewm(halflife=half_life_bars)`)도 같은 이유로 결측 구간 앞뒤의
"수익률"이 4시간치가 아니라 1개 행의 수익률로 뭉뚱그려져 왜곡될 수 있다.

**해볼 것**: 20개 학습 마켓 전체를 스캔해 결측 구간(캔들 간 시간 간격이
timeframe 배수를 벗어나는 지점)의 빈도/길이 분포를 먼저 파악하고, 규모가
유의미하면 (a) 결측 구간을 걸친 라벨을 아예 NaN 처리해 학습에서 제외하거나
(b) `future`/`volatility` 계산을 행 인덱스가 아니라 `candle_time` 기준으로
다시 정렬하는 보정을 검토한다. 규모가 미미하면(예: 20마켓 합쳐 결측 구간이
극소수) 굳이 안 고쳐도 됨 — 먼저 스캔부터.

## 3. 모델 선택

### 3-1. 선형/정규화 모델을 baseline으로 병행 (반직관적이지만 중요)

신호대잡음비가 극도로 낮은 금융시계열에서는 트리 앙상블이 노이즈에 과적합하기
쉽고, L1/L2 정규화 로지스틱회귀 같은 단순 선형모델이 오히려 out-of-sample에서
덜 무너지는 경우가 실무에서 흔하다. 지금 LightGBM 단일모델만 있는데, 같은
walk-forward에 로지스틱 회귀를 나란히 돌려 kappa를 비교하면 "지금 모델이 정말
신호를 잡고 있는지, 트리모델의 유연성이 노이즈를 외운 것뿐인지" 판별할 수 있다.

### 3-2. 메타 레이블링(Meta-Labeling)

사용자 제안 A(전략별 기대수익 직접예측)와 다른 AFML 표준 기법. 1단계는 지금처럼
방향(하락/하락아님)을 예측하고, 2단계로 "1차 모델이 하락이라고 낸 신호가 진짜
믿을만한지"를 판단하는 별도 이진분류기(정밀도 극대화가 목적함수)를 얹는
구조다. "언제 거래를 실행할지"와 "어느 방향인지"를 분리해 precision 문제를
구조적으로 공략한다. Hysteresis(확신도 threshold)는 이것의 축소판이라 볼 수
있고, 정식 메타모델을 얹으면 더 체계적이다.

## 4. 우선순위 제안 (비용 대비 효과 순)

1. **Threshold 튜닝 + 확률 보정** — 재학습 불필요, precision 문제에 가장
   직접적, 가장 빠르게 시도 가능
2. **Sample uniqueness 가중치 또는 CUSUM 이벤트 샘플링** — 가장 근본적인 구조
   문제, kappa 자체를 끌어올릴 잠재력이 가장 큼
3. **베타중립 cross-sectional 피처** — Kaggle 상위권 공통 인사이트, 현재 없는
   피처축
4. **캔들 결측 구간 스캔 + 라벨/변동성 보정 검토** — 스캔 자체는 비용이 매우
   낮음(20마켓 시간간격 점검), 규모 파악 후 실제 보정 여부 결정
5. **로지스틱회귀 baseline 비교 + LightGBM 하이퍼파라미터 튜닝** — 백로그
   `docs/regime-ml-backlog.md`의 잔여 후보 (c)와 겹침, 우선순위 재확인 차원
6. **메타 레이블링** — 구조 변경이 크므로 1~5 검증 후 착수

## 참고 자료

- [Data Sampling — Mlfin.py (sample uniqueness, sequential bootstrap)](https://mlfinpy.readthedocs.io/en/latest/Sampling.html)
- [AFML Part 1 — Data Analysis](https://medium.com/@caneradilirfanoglu/afml-part-1-data-analysis-f237af7e83c4)
- [CUSUM Event Identification — ostirion.net](https://www.ostirion.net/post/cusum-event-identification-not-just-for-sampling)
- [Data Filtering — Mlfin.py](https://mlfinpy.readthedocs.io/en/latest/Filtering.html)
- [Cross-Validation For Financial Data (Purging and Embargoing)](https://www.tradinginterview.com/courses/machine-learning/lessons/cross-validation-for-financial-data-purging-and-embargoing/)
- [Combinatorial Purged Cross-Validation Insights (SSRN via Scribd)](https://www.scribd.com/document/725401650/SSRN-id4778909)
- [Purged cross-validation — Wikipedia](https://en.wikipedia.org/wiki/Purged_cross-validation)
- [Meta labeling in Cryptocurrencies Market](https://medium.com/@liangnguyen612/meta-labeling-in-cryptocurrencies-market-95f761410fac)
- [G-Research Crypto Forecasting — wrap-up (G-Research)](https://www.gresearch.com/news/wrapping-up-the-g-research-crypto-forecasting-competition/)
- [37th place approach — G-Research Crypto Forecasting (Kaggle writeup)](https://www.kaggle.com/competitions/g-research-crypto-forecasting/writeups/kirderf-37th-place-approach-in-the-g-research-cryp)
- [Cryptocurrency volatility dynamics — GARCH-family models (2025)](https://link.springer.com/article/10.1186/s43093-025-00568-w)
