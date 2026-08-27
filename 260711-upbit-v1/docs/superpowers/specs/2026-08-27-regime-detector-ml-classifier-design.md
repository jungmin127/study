# 장세 판별기 ML 전환 — 설계 스펙 (2단계)

## 배경

[[2026-08-23-realtime-regime-detector-design]]에서 구축한 규칙기반 EWMA 판별기(`engine/regime_detector.py`)는 상관계수가 3개 마켓 전부 `|r|≤0.06`으로 예측력이 거의 없다. 이를 개선하려고 규칙기반 정교화(반전 게이팅, `docs/superpowers/specs/2026-08-24-regime-detector-reversal-gating-design.md`)를 시도했으나, 실측에서 핵심 목표(정반대 오분류)가 오히려 악화되어 push하지 않고 로컬에만 보관 중이다(로컬 main 15커밋, `51d4d59..c8df45a`).

규칙기반을 두 번 정교화해도 예측력이 개선되지 않았다는 것은 피처 문제가 아니라 모델 구조(고정 공식 + 하드코딩된 대표값)의 한계일 가능성을 시사한다. 이번 세션은 지도학습(ML)으로 전환해 같은 문제를 다시 공략한다.

**원래 규칙기반을 택했던 이유**(비정상성/누수/소표본 과최적화 위험)는 이번 설계의 워크포워드 검증·embargo·fold별 레이블 재계산으로 대응한다 — "지도학습은 위험하니 배제"가 아니라 "위험을 구체적으로 방어하며 시도"로 방향 전환.

## 목표

`/regime` 대시보드가 쓰는 것과 동일한 평가 기준(카테고리별 hit-rate, confusion matrix, 상관계수)으로 규칙기반 대비 예측력이 개선되는지 실측 검증한다. 개선이 확인되지 않으면 [[upbit-v1-dont-push-on-empirical-regression]] 원칙에 따라 push하지 않는다.

## 비범위 (이번 세션)

- `engine/regime_detector.py` 변경 없음(비교 기준으로 그대로 유지)
- `/regime` 대시보드, API, 라이브 데몬 통합 없음 — 학습+검증 파이프라인까지만
- 하이퍼파라미터 자동튜닝 없음(검증 안 된 목적함수의 자동 최적화는 과최적화 위험 — 기존 half-life 자동튜닝을 비범위로 뒀던 것과 같은 원칙)
- 1시간봉 외 다른 타임프레임 미지원(기존 검증 기준과 동일하게 1시간봉만)
- KRW-BTC/ETH/XRP 외 마켓 확장 없음

## A. 타깃(레이블) 정의

`backend/regime_service.py`의 기존 `normalized_realized` 계산(다음 n_bars 평균수익률 / 이후 EWM변동성, `n_bars = N_MULTIPLIER(2.5) × half_life_bars`)을 그대로 재사용해 레이블을 만든다. 규칙기반과 동일한 잣대이므로 개선 여부를 직접 비교할 수 있다.

카테고리 5구간(급하락/완만하락/횡보/완만상승/급상승) 경계는 **각 워크포워드 fold의 훈련구간에서만** 분위수(하위 2%/16%/84%/98%)를 계산해 정한다. 테스트구간 정보가 경계 산정에 섞이지 않고(누수 방지), fold마다 경계가 달라지며 비정상성에 자연스럽게 대응한다. 기존 고정값(±0.15/±0.35)이 실측 분포와 안 맞아 재보정을 두 번 거친 전례([[upbit-v1-realtime-regime-detector-design]])를 반복하지 않기 위함.

분류는 5-class 표준 multiclass. 모델 출력은 `predict_proba`로 확률벡터를 내므로 기존 대시보드의 softmax 확률벡터와 형태가 호환된다.

## B. 피처

**핵심 재사용 대상**: `trading/live_indicators.py`의 `LIVE_INDICATOR_FACTORY`(39개 지표, 이미 순수 pandas로 구현되어 백트레이더 대비 골든테스트로 검증됨 — RSI/MACD류/Stoch/CCI/Williams%R/BB/ATR/OBV/거래대금비율/VPIN/Pivot/Fib/VPVR/MarketTrend/BTC·USDT상관계수/공포탐욕지수/김치프리미엄/funding rate). **새로 재구현하지 않고 이 레지스트리를 그대로 순회**해 피처 매트릭스를 만든다.

추가:
- `engine/regime_features.py`의 5개 함수(volume_confirm/pivot_levels/vpin_score/level_proximity/reversal_gate) — regime 전용이라 레지스트리에 없으므로 별도 호출
- `engine/regime_detector.py`의 momentum/volatility EWMA 자체(raw_score) — 기존 신호도 피처 후보로 포함
- `market`(코인) 범주형 피처 — 풀링 학습이므로 코인 구분용, LightGBM 네이티브 categorical 처리

**데이터 준비**: `backend/main.py:_fetch_backtest_dataframe`의 병합 패턴(get_candles + aux market close + `merge_fear_greed`/`merge_funding_rate` + korea_premium_value 계산)을 참고하되, 그 함수는 FastAPI `HTTPException`과 조건트리(`buy_dict`/`sell_dict`)에 결합돼 있어 그대로 재사용할 수 없다. 학습 스크립트 전용의 독립 데이터 로더를 새로 작성하며, 조건 없이 **항상 전체 aux 데이터(BTC/USDT close, 공포탐욕지수, 김치프리미엄, funding rate)를 붙인다**.

**결측 처리**: 외부데이터 커버리지 부족 구간/코인(예: 공포탐욕지수 2018-02-01 이전, 특정 코인의 바이낸스 심볼 부재)은 NaN으로 남긴다 — LightGBM은 결측치를 트리 분기에서 네이티브로 처리하므로 별도 보간 불필요. 단, 전 구간에서 완전히 NaN인 피처(바이낸스 심볼이 아예 없는 코인 등)는 학습 전 드롭한다.

**워밍업**: 지표별 lookback이 다르므로, 전체 피처 세트 중 가장 긴 워밍업 기간이 끝난 시점부터 학습에 사용한다(그 이전 행은 드롭).

## C. 검증(워크포워드)

Expanding window, 예상 5개 fold(정확한 경계는 구현 시 실제 데이터 범위 확인 후 확정). 레이블이 미래 n_bars를 내다보므로 **train 끝과 test 시작 사이에 n_bars만큼 embargo**를 둬 레이블 누수를 막는다.

풀링 학습이지만 시간축은 공유한다 — 같은 달력 구간을 모든 마켓이 동시에 훈련/검증에 사용한다(마켓별로 다른 기간을 쓰지 않음).

fold별로 confusion matrix / 카테고리별 hit-rate / 상관계수(`expected_score` vs `normalized_realized`, 기존 `backend/regime_service.py:117-123`과 동일 정의)를 콘솔에 출력하고, 전체 fold를 합산한 요약도 출력한다. `/regime` 대시보드가 이미 쓰는 지표와 같은 정의라 규칙기반 결과와 나란히 비교 가능하다.

## D. 모델

**LightGBM**(신규 의존성 — `requirements.txt`에 추가 필요). multiclass objective, 희소 클래스(급상승/급하락) 보정을 위해 `class_weight='balanced'`(또는 동등한 sample_weight). fold별 feature importance(gain)를 출력해 다음 세션 피처 가지치기 근거로 남긴다.

하이퍼파라미터는 합리적 기본값으로 시작(자동튜닝은 비범위).

## E. 산출물

- `scripts/train_regime_ml.py`: 데이터 로드 → 피처 매트릭스 생성 → 워크포워드 fold 루프(레이블 재계산 → 학습 → 평가 → 리포트 출력) → 최종 fold 모델 아티팩트 저장
- 신규 헬퍼 모듈(피처 매트릭스 생성 함수) — 정확한 파일 위치는 구현 계획에서 결정
- 모델 저장: `models/regime_ml/`(디렉터리 신규, gitignore 대상 — 학습 산출물은 커밋하지 않음). LightGBM 네이티브 포맷.
- 대상 데이터: KRW-BTC/ETH/XRP, 1시간봉, 2024-01-01~현재(기존 규칙기반 검증과 동일 기준)

## F. 테스트

- 피처 매트릭스 생성: 워밍업 NaN 경계, 컬럼 존재 여부
- 워크포워드 분할: fold 경계가 겹치지 않는지, embargo가 실제로 적용되는지
- fold별 quantile 레이블 경계 계산: 훈련구간만 사용하는지(테스트구간 값이 섞이면 실패하는 테스트)
- 파이프라인 end-to-end: 소규모 합성 데이터로 스모크 테스트(에러 없이 완주, 모델 파일 생성 확인)

## 결정 요약 (재작업 시 참고)

| 항목 | 선택 | 비고 |
|---|---|---|
| 예측 타깃 | 실현수익률 기반 5분류 재정의 | `normalized_realized` 재사용 |
| 모델 계열 | 트리 기반 (LightGBM) | 해석 가능성(feature importance) 중시 |
| 검증 분할 | 워크포워드 다중 fold + embargo | 비정상성/누수 방어 |
| 피처 범위 | `LIVE_INDICATOR_FACTORY` 39개 전체 + regime_features.py 5개 | 재구현 없이 기존 자산 재사용 |
| 마켓 | 여러 코인 풀링 단일 모델 | market 범주형 피처로 구분 |
| 레이블 경계 | fold별 훈련구간 분위수(quantile) | 고정값 재보정 문제 재발 방지 |
| 이번 세션 범위 | 학습+검증 파이프라인까지 | 대시보드/라이브 통합은 별도 세션 |
