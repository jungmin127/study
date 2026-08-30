# 장세 판별 ML — HMM 상태 피처 추가 설계 스펙

## 배경

[[2026-08-29-regime-ml-problem-redefinition-design]]에서 Triple Barrier 3단계
재정의 + 코인 차별화 피처로 재구성한 뒤, 같은 세션 후속으로 시간누출 피처
(LISTING_AGE_BARS) 제거 + FEAR_GREED_CMC 제거 + barrier_k 재탐색(5.5→6.25)까지
반영해 pooled weighted kappa가 0.028→0.072로 개선됐고 AWS 라이브에 배포됐다
([[upbit-v1-regime-ml-market-expansion-b]]). 사용자가 이후 두 방향(트위터 감성분석,
HMM)을 검토하다가, 트위터 쪽은 조사 결과 "실시간+무료+인물지정"을 동시에 만족하는
안정적 경로가 없다고 확인돼(공식 X API 2026년 무료 티어 폐지, 유저 타임라인
3,200트윗 상한, Santiment/LunarCrush는 유료+집계형이라 오늘 실측으로 확인한
"전 마켓 공유 시계열 피처가 성능을 깎아먹는" 패턴과 같은 구조적 위험) 일단
보류하고, HMM부터 진행하기로 했다.

오늘 조사한 문헌(MDPI Regime-Aware LightGBM 논문 등)이 공통적으로 제시하는 패턴은
"HMM이 LightGBM을 대체하는 게 아니라, HMM으로 추론한 장세 상태(확률)를 LightGBM의
피처로 추가"하는 하이브리드다. 이번 스펙은 이 패턴을 채택한다.

## 목표

1. 로그수익률+변동성 기반 Gaussian HMM으로 마켓별 잠재 상태를 추론하고, 상태별
   확률을 새 피처로 만들어 기존 LightGBM 파이프라인에 추가한다
2. Walk-forward 무결성을 유지한다 — 기존 롤링 피처와 달리 HMM은 파라미터를 학습
   (EM)하는 모델이라, fold의 train 구간에서만 fit하고 train/test 각각에 대해
   별도로 상태확률을 추론한다
3. **효과가 실측으로 확인된 뒤에만** 프로덕션(서빙 경로 포함)에 반영한다 — 오늘
   세션 내내 지켜온 "ablation으로 먼저 검증 → 확인되면 코드 반영 → 재학습 →
   배포는 사용자 승인" 순서를 그대로 따른다([[upbit-v1-dont-push-on-empirical-regression]])

## 비범위

- HMM이 LightGBM을 대체하는 앙상블/스태킹 구조 — 이번엔 피처 추가만
- HMM 상태와 Triple Barrier 3클래스(하락/횡보/상승)의 1:1 매핑 — 비지도 학습
  결과를 라벨과 강제로 맞추지 않는다. LightGBM에 추가 신호로만 제공
- 완전한 실시간 인과적 필터링(순방향 전용, hmmlearn 저수준 API로 직접 구현) —
  1차는 `predict_proba()`(순방향+역방향 스무딩)로 구현하고, 효과가 있을 때만
  후속 세션에서 정교화 검토(아래 "알려진 근사" 참고)
- 트위터 감성분석, 구글 트렌드 — 별도 세션, 이번 스펙과 무관
- 하이퍼파라미터 자동튜닝, 1시간봉 외 타임프레임 — 기존 스펙과 동일 이유로 계속 비범위
- AWS 라이브 배포 여부 결정 — 재학습 결과 확인 후 사용자 승인 필요

## A. HMM 모델링 방식

**입력 신호**: 로그수익률(`returns = close.pct_change()`) + EWM 변동성(기존
`RAW_SCORE` 계산에 이미 쓰는 것과 같은 변동성 시리즈, `engine/regime_ml_features.py`
참고) 2개 변수. Gaussian HMM은 고차원(우리 LightGBM 피처 64개)에서 공분산 추정이
불안정해지므로 저차원 입력이 표준이며, regime-switching 문헌도 대체로 이 조합을
쓴다.

**상태 개수**: `n_components=3`으로 시작. 우리 3클래스(하락/횡보/상승)와 억지로
맞추지 않는다.

**마켓별 개별 학습**: 코인마다 변동성 스케일이 다르므로 마켓별로 개별 HMM을 학습
(14개, `market` 파라미터 없는 pooled 방식은 채택 안 함).

**라이브러리**: `hmmlearn.hmm.GaussianHMM`(신규 의존성, `requirements.txt`에 추가
필요 — 무료, 로컬 연산, API 비용 없음). `covariance_type="diag"`(대각 공분산 —
입력이 2차원뿐이고 fold별 학습 표본이 제한적이라 `"full"`보다 안정적), `random_state`
고정(재현성).

## B. Walk-forward 안전성

기존 피처(ATR, RSI 등)는 롤링 윈도우라 마켓당 한 번만 계산해두고 fold별로 행만
잘라 쓰면 됐지만(`scripts/train_regime_ml.py`의 `market_frames`가 fold 루프
바깥에서 한 번만 만들어짐), HMM은 파라미터를 EM으로 학습하는 모델이라 이 구조를
그대로 못 쓴다. **fold 루프 안에서** 그 fold의 train 구간 데이터로만
`GaussianHMM.fit()`을 호출하고, 학습된(고정된) 모델로 train/test 각각의 관측
시퀀스에 대해 별도로 `predict_proba()`를 호출해 상태확률 3개 컬럼을 만든다.
(fold 5개 × 마켓 14개 = 70번 fit — 각각 2차원 입력의 가벼운 EM이라 LightGBM 학습에
비하면 비용이 크지 않다.)

**알려진 근사(한계)**: `hmmlearn`의 기본 `predict_proba()`는 순방향+역방향(스무딩)
알고리즘이라, test 구간 "안에서" 미래 시점이 과거 시점의 상태확률 추정을 살짝
도와주는 약한 형태의 정보유출이 남는다(모델 파라미터 자체는 train에서만 학습되므로
train→test 누출은 없음 — 이건 test 구간 내부의 스무딩만의 문제). 완전한
실시간 인과적 필터링(순방향 전용)을 쓰려면 `hmmlearn`의 저수준 forward-pass를
직접 호출해야 한다. 1차 구현은 `predict_proba()`로 빠르게 효과를 확인하고,
효과가 있으면 그때 정교화 여부를 판단한다.

## C. 파이프라인 통합

### C-1. 검증 단계 (Phase 1 — 코드 반영 없이 먼저 효과 확인)

오늘 썼던 ablation 스크립트 패턴(`scratchpad/ablate_*.py`, 커밋 안 함)을 그대로
재사용한다: 데이터/피처는 마켓당 한 번만 로드하고(HMM과 무관한 부분), fold
루프에서 HMM 상태확률 3개를 계산해 기존 피처에 추가한 뒤, HMM 피처 포함/제외
두 버전으로 학습해 pooled macro F1/weighted kappa를 비교한다. **이 단계는
프로덕션 코드(`engine/regime_ml_features.py`, `scripts/train_regime_ml.py`)를
건드리지 않는다.**

### C-2. 프로덕션 반영 (Phase 2 — Phase 1에서 개선이 확인된 경우만)

- `engine/regime_ml_hmm.py`(신규): `fit_and_score_hmm_states(train_returns_vol, infer_returns_vol, n_states=3, random_state=...) -> np.ndarray`(상태확률 행렬) 형태의 순수 함수. `build_feature_matrix()`와 달리 fold 경계 정보가 필요하므로 `build_feature_matrix()` 내부에 넣지 않고, `scripts/train_regime_ml.py`의 fold 루프에서 별도로 호출한다.
- **신규 아티팩트 필요**: LightGBM 모델(`regime_ml_TIMESTAMP.txt`)은 "마지막 성공한 fold"의 것을 저장하는 기존 관례를 따르는데, 서빙 시(`predict_current_ml_regime()`)도 그 fold에서 학습된 HMM 파라미터를 그대로 써야 학습/서빙 간 정합성이 유지된다. 마켓별 HMM 파라미터(mean/covariance/transition matrix)를 `regime_ml_TIMESTAMP_hmm.pkl` 같은 이름으로 사이드카 옆에 저장하고, `backend/regime_ml_service.py`가 예측 시 이 pickle을 불러와 `predict_proba()`만 호출(재학습 없음)하도록 확장한다.
- `tests/test_regime_ml_hmm.py`, 기존 `test_train_regime_ml.py`/`test_regime_ml_service.py` 갱신.

## D. 검증 계획

Phase 1 ablation에서 pooled weighted kappa가 현재 배포 기준(0.072) 대비 개선되는지만
본다. 개선이 없거나 악화되면 Phase 2(프로덕션 반영)로 넘어가지 않고 이 스펙 결과를
메모리에 기록한 뒤 종료한다([[upbit-v1-dont-push-on-empirical-regression]] 원칙).
