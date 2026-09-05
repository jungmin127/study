# 장세 판별 — HMM 비지도 클러스터링 대안 검증 (Phase 1) 설계 스펙

## 배경

[[upbit-v1-regime-ml-meta-labeling]]까지 이번 라운드에서 시도한 4개 방향(피처
추가/모델 교체/horizon 조정/메타 레이블링)이 전부 유의미한 개선 없이 끝났다.
`docs/regime-ml-backlog.md`가 정리한 다음 후보 (a) HMM 비지도 클러스터링 /
(b) AWS 배포 전환 중, 사용자가 (a)를 선택했다.

`docs/ML_Regime_Switching_Improvement_Plan.md`(2026-08-29, 커밋 안 됨) "제안
C"가 이 방향의 초안이다: Triple Barrier 지도학습 라벨을 버리고, 수익률/변동성
기반 Gaussian HMM으로 시장의 잠재 상태를 통계적으로 클러스터링한다.

**중요 발견**: 2026-08-30에 이미 "HMM 상태확률을 LightGBM 피처에 추가"하는
다른 실험(설계: `docs/superpowers/specs_v1/2026-08-30-regime-ml-hmm-feature-design.md`)의
코드(`engine/regime_ml_hmm.py`)가 작성됐지만, 결과가 한 번도 실행/기록되지
않고 방치되어 있었다. 그 실험은 "HMM을 지도학습 피처로 추가"였고 이번은
"지도학습 라벨 자체를 버림"이라 목적은 다르지만, 마켓별 Gaussian HMM을
학습/추론하는 순수함수(`build_hmm_observations`/`fit_hmm`/
`score_hmm_state_probabilities`)는 그대로 재사용 가능하다.

## 목표

지도학습(Triple Barrier 라벨 + LightGBM) 없이, HMM으로 뽑아낸 잠재 상태만으로
"장세에 따라 실제 매매 성과가 갈리는가"를 확인한다 — 2026-08-30 fact 라벨
백테스트 분석(설계: `docs/superpowers/specs_v1/2026-08-30-regime-fact-label-backtest-analysis-design.md`,
구현: `scripts/analyze_regime_fact_performance.py`)과 동일한 사후(ex-post)
분석 패턴을 그대로 재사용해, 기존 지도학습 기준값과 나란히 비교 가능한
숫자를 낸다.

## 비범위

- 프로덕션 반영(`backend/regime_ml_service.py`를 HMM 기반으로 교체) — 이번
  검증 결과가 뚜렷하게 좋을 때만 별도 세션에서 브레인스토밍
- 상태별 최적 전략 자동 매핑, 실시간 자동 전략전환(`docs/regime-ml-backlog.md`
  우선순위2 "③ 실시간 자동 장세 대응") — 훨씬 큰 별도 작업, 이번 스코프 아님
- 워크포워드(fold별 fit) 방식의 "라이브 예측용" HMM — 2026-08-30 설계가 이미
  이 방식을 다뤘다. 이번은 "장세 구조 자체가 매매 성과와 상관이 있는가"를
  먼저 사후 분석으로 확인하는 게 목적이라, 방식이 다르다.
- 거래량을 HMM 입력에 추가하는 것 — 제안 C의 아이디어지만 이번엔 이미 검증된
  2변수(수익률+변동성)로 시작. 신호가 보이면 후속 검토.
- `n_states` 그리드서치(3/4/5 비교) — 3-state 결과가 애매하면 후속 검토
- KRW-BTC/KRW-XLM 외 마켓 확장 — 기존 fact 라벨 백테스트 스크립트가 이 두
  마켓의 저장된 백테스트 결과만 다뤘던 것과 동일한 이유(다른 마켓은 저장된
  백테스트 run이 없어 거래 데이터 자체가 없음)

## A. HMM 모델링 (기존 코드 재사용)

`engine/regime_ml_hmm.py`를 그대로 재사용한다(수정 없음):

- `build_hmm_observations(df, half_life_bars)` — 로그수익률 + EWM 변동성 2컬럼
- `fit_hmm(observations, n_states=3, random_state=42)` — `GaussianHMM(covariance_type="diag")`
- `score_hmm_state_probabilities(model, observations)` — 상태확률 3컬럼

**적합 방식**: 마켓별로 **전체 기간(2024-01-01~현재) 한 번에 fit** — 워크포워드
fold 분리 없음. 이유: Triple Barrier fact 라벨(`compute_triple_barrier_labels`)도
미래 바 정보를 쓰는 사후 라벨이라 애초에 "라이브 예측"이 아닌 "사후 분석"
목적으로 쓰인다(`scripts/analyze_regime_fact_performance.py`가 정확히 이
패턴). HMM도 같은 사후 분석 목적이므로 동일 기준으로 fit한다. 이렇게 하면
라이브 서빙 정합성(fold 경계, 모델 아티팩트 저장 등) 문제를 이번 검증
단계에서는 아예 마주치지 않는다.

## B. 검증 스크립트

신규 파일: `scripts/analyze_regime_hmm_fact_performance.py` — 기존
`scripts/analyze_regime_fact_performance.py`와 거의 동일한 구조를 따르되
라벨 소스만 HMM으로 바꾼다. 프로덕션 코드(`engine/regime_ml_features.py`,
`scripts/train_regime_ml.py`, `backend/regime_ml_service.py`)는 전혀 건드리지
않는다.

1. `build_label_lookup(market)`을 대체하는 `build_hmm_state_lookup(market)`:
   - `get_candles(market, TIMEFRAME, START, END)`로 캔들 로드(기존과 동일
     기간/타임프레임: minutes60, 2024-01-01~현재)
   - `build_hmm_observations` → NaN 제거 → `fit_hmm(n_states=3)` →
     `score_hmm_state_probabilities`로 전체 구간 상태확률 계산
   - 바별로 `argmax`해서 이산 상태(정수 0~2)를 뽑고, naive UTC candle_time을
     인덱스로 하는 `pd.Series` 반환(기존 `build_label_lookup`과 동일한 반환
     형태 — 값만 문자열 라벨 대신 정수 상태)
2. `label_for_entry`, `load_labeled_trades`, `print_pooled_comparison`,
   `print_run_ranking`은 기존 스크립트에서 그대로 가져오되, `label` 컬럼 값이
   HMM 상태 정수라는 점만 다르다. 상태값은 의미가 없는 임의 정수(0/1/2는
   "하락"처럼 사전에 정해진 의미가 아님)이므로, 출력 시 상태별 관측 특성
   (state 내 평균 수익률/평균 변동성)도 같이 찍어 사후에 "이 상태가 대략
   상승장/하락장/횡보장 중 뭐에 가까운지" 해석할 수 있게 한다(`print_state_profile`
   신규 함수 — state별 관측치의 mean returns/mean volatility 출력).
3. `main()`은 기존과 동일한 흐름(마켓별 lookup 생성 → 거래 라벨링 → 풀링 비교
   출력 → run 랭킹 출력)에 `print_state_profile` 호출만 추가.

## C. 성공 판단 기준

기존 지도학습 모델의 사후 분석 기준값(2026-08-30, `docs/regime-ml-backlog.md`):
하락 진입 승률 41.6%(총기여 -17.7%) vs 하락아님 진입 승률 73.2%(총기여
+1767.1%) — 표본이 충분한(각 상태 최소 `MIN_TRADES_FOR_RANKING=5`, 풀링 비교는
더 낮은 문턱 없이 전체 표본 사용) 상태 간에 이와 견줄 만하거나 더 뚜렷한
승률/총기여 격차가 나오면 **유의미**로 판단한다. 격차가 작거나(승률 차이
10%p 미만 등 애매한 수준) 상태별 표본이 너무 작아 판단 불가하면 **미채택**으로
기록하고 종료 — 지금까지의 4연속 미채택과 같은 패턴을 반복하되, 데이터에
기반해 판단한다(사전에 숫자 기준을 억지로 고정하지 않고, 결과를 보고 기존
기준값과 정성적으로 비교).

## D. 문서화

결과는 `docs/regime-ml-backlog.md` 최상단에 기존 라운드들과 같은 형식으로
기록한다(설계/계획 링크, 실측 표, 결론, 다음 후보).
