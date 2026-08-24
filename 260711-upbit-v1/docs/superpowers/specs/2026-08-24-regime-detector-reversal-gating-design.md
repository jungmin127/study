# 장세 판별기 예측력 개선 — 반전 게이팅 (1단계)

## 목적

`engine/regime_detector.py`의 장세 판별기는 배포 후 검증(2026-08-23) 결과
확률벡터-실현수익률 상관계수가 |r|≤0.06으로 낮고, 특히 급상승/급하락처럼 극단적인
장세에서 실제와 정반대로 예측되는 사례가 있다. 이 스펙은 그 실패 패턴을 줄이기 위해
가격(종가)만 보던 판별 스코어에 거래량·매수매도 압력·지지저항 근접도를 반영한다.

이 스펙은 **규칙기반 공식 확장까지만** 다룬다(브레인스토밍에서 결정한 2단계 로드맵의
1단계). 시장심리·BTC상관계수 등 외부데이터 신호 추가, 하이브리드 ML 보조신호 도입은
별도 세션에서 다룬다.

## 배경 / 문제의식

기존 판별기(`docs/superpowers/specs/2026-08-23-realtime-regime-detector-design.md`)는
`score = EWMA(수익률)/EWMA_표준편차(수익률)` 단일 스칼라로, 입력이 **종가 하나뿐**이다.
이 스코어는 미래를 예측하는 게 아니라 최근 추세(half-life 1일)를 지수평활해 그대로
되돌려주는 후행 지표다. 사용자 관찰(2026-08-24):

- 상관계수가 0 근방을 넘어 마이너스까지 나옴
- 횡보 적중률은 높지만 급상승/급하락은 거의 못 맞춤
- 급상승 예측인데 실제 급하락(또는 반대) — 완전 정반대 오분류가 존재

이 패턴은 구조적으로 당연하다: 급등/급락 직후는 통계적으로 반전(mean reversion)이 가장
잦은 구간인데, 판별기는 "최근 추세 지속"만 가정하고 반전 전조 신호(거래량 이탈, 매수/매도
압력 불균형, 지지/저항 근접)를 전혀 보지 않기 때문이다.

## 데이터 가용성 확인

`upbit_data_service.get_candles()`가 반환하는 DataFrame은 이미 `volume`,
`trade_value` 컬럼을 포함한다(`upbit_data_service.py:19`, `146-153`). 따라서 거래량·VPIN·
지지저항 신호는 **추가 데이터 수집 없이** 바로 계산 가능하다. 시장심리(공포탐욕/김프/
펀딩비)·BTC상관계수는 `engine/indicators/market.py`, `sentiment.py`가 요구하는 별도
외부 마켓 데이터 병합이 필요해 이번 단계 범위에서 제외한다.

**주의**: `engine/indicators/*.py`의 기존 지표는 전부 `bt.Indicator`(backtrader 전략
객체 모델 안에서만 동작)로 구현돼 있어 그대로 재사용할 수 없다. `regime_detector.py`는
backtrader 없이 순수 pandas 함수이므로, 필요한 계산 로직(VPIN의 Bulk Volume
Classification, Pivot Point 등)을 pandas로 새로 구현한다.

## 설계

### 아키텍처

신규 모듈 `engine/regime_features.py` — 순수 pandas 함수 모음, I/O 없음(`regime_detector.py`
가 지금 `pd.DataFrame`을 받는 것과 같은 패턴). `compute_regime_probs_series()` 내부
계산만 아래처럼 확장하고, 함수 시그니처·반환 타입·`evaluate_market()`의 confusion
matrix/correlation 계산·API·UI 컴포넌트(`RegimeAccuracyReport`, `RegimeCurrentPrediction`)
는 전부 무수정이다.

```
raw_score = momentum_ewma / volatility_ewma            # 기존과 동일 (regime_detector.py:110)
adjusted_score = raw_score * volume_confirm * reversal_gate
```

`adjusted_score`를 기존 `_softmax_categorize()`에 그대로 전달한다.

### 보조 신호

**거래량 확인 (`volume_confirm`)**
```python
trade_value_ratio = (trade_value - SMA(trade_value, 20)) / SMA(trade_value, 20)
volume_confirm = 1 + clip(trade_value_ratio, -0.3, 0.3)   # [0.7, 1.3]
```
`engine/indicators/volume.py:111-124`(`TradeValueRatio`)와 동일 정의를 pandas로
재구현. 방향 무관 — 평균보다 거래대금이 실린 봉이면 증폭, 안 실렸으면 감쇠.

**VPIN 불균형 (`vpin_score`)**
`engine/indicators/volume.py:131-199`(`VolumeBarVPIN`)의 Bulk Volume Classification을
pandas로 재구현: 거래량 버킷(목표=최근 20봉 평균거래량) 누적 → 버킷 완성 시 종가 델타의
z-score → 정규분포 CDF로 매수비율 추정 → `|매수량-매도량|/총량`. 결과 [0, 1], 매수/매도
쏠림이 클수록 1에 가까움.

**지지/저항 근접도 (`level_proximity`)**
직전 봉 고가/저가/종가로 Pivot Point(P, R1, S1) 계산(`engine/indicators/price_levels.py:35-51`
와 동일 정의). `거리 = |close - 가장_가까운_레벨| / volatility_ewma`,
`level_proximity = 1 - min(거리, 1)`. **방향 필터**: `raw_score > 0`(상승 중)일 때는 R1
근접만, `raw_score < 0`(하락 중)일 때는 S1 근접만 카운트 — 추세 방향과 무관한 레벨
근접까지 반전 신호로 잡으면 오탐이 늘어난다.

**반전 게이트 (`reversal_gate`)**
```python
reversal_risk = vpin_score * level_proximity_in_direction   # 두 조건이 동시에 성립해야 인정
reversal_gate = 1 - 0.7 * reversal_risk   # [0.3, 1.0], 완전히 0으로 짓누르지 않음
```
`raw_score`가 극값 방향으로 크게 나온 상황에서 VPIN 쏠림과 저항/지지 근접이 동시에
나타나면 반전 직전일 확률이 높다는 판단 하에, score를 0(횡보) 쪽으로 끌어당겨 "급상승처럼
보이지만 실제로는 꺾이기 직전"인 경우를 과신하지 않게 한다.

### 워밍업

전체 판단불가(`None`) 기준은 지금과 동일하게 `half_life_bars * WARMUP_MULTIPLIER`만
쓴다(확장하지 않음). 대신 보조 신호(VPIN·거래대금 SMA·Pivot) 각각이 자기 워밍업
기간(예: VPIN period=20봉) 동안 준비되지 않았으면 해당 신호는 **중립값**(거래량
확인=1.0, VPIN=NaN→0 취급)으로 자연스럽게 무시된다 — `reversal_gate`/`volume_confirm`이
`NaN`을 "위험 없음"으로 처리하도록 설계했기 때문이다(구현 세부사항, 계획 문서 Task 5
참고). 워밍업 기준을 확장해 예측 자체를 더 늦게 시작하는 것보다, 보조 신호가 준비될
때까지는 조정 없이 기존 raw_score로 예측을 계속 내놓는 쪽이 더 안전하다고 판단했다 —
불필요하게 예측 가능 구간을 줄이지 않으면서도 "덜 여문 조정값을 쓰지 않는다"는 목적은
동일하게 달성한다.

## 테스트 (`tests/test_regime_features.py` 신설)

- `volume_confirm`: 평균 대비 거래대금 급증/급감 합성 데이터에서 [0.7, 1.3] 범위 내
  기대 방향으로 나오는지
- `vpin_score`: 완전 균형(매수=매도) 합성 데이터에서 0 근방, 한쪽으로 쏠린 합성 데이터에서
  1 근방인지
- `level_proximity`: 가격이 Pivot 레벨에 근접/이탈하는 합성 데이터, 방향 필터가 실제로
  반대 방향 근접을 무시하는지
- **반전 시나리오 회귀 테스트**: 급등 후 급락하는 합성 시계열에서, `raw_score` 기준으로는
  "급상승"으로 확신하지만 `adjusted_score` 기준으로는 최댓값 확률(신뢰도)이 유의미하게
  낮아지는지 — 이번 스펙이 해결하려는 문제를 코드로 고정
- 기존 `tests/test_regime_detector.py`의 불변식(확률합=1, 워밍업 부족 시 `None` 등)이
  `adjusted_score` 도입 후에도 그대로 통과하는지

## 검증 (수동)

변경 전/후로 `/regime` 대시보드(또는 `scripts/regime_backtest.py`)를 KRW-BTC/ETH/XRP
1시간봉에 돌려 비교:
- 확률벡터-실현수익률 상관계수가 개선되는지
- confusion matrix에서 "정반대 오분류" 셀(급상승 예측×급하락 실제, 급하락 예측×급상승
  실제)의 건수가 줄어드는지
- 급상승/급하락 카테고리 자체가 여전히 통계적으로 도달 가능한지(과도한 게이팅으로 모든
  스코어가 횡보로 수렴하지 않는지)

## 비범위

- 시장심리(공포탐욕/김프/펀딩비)·BTC상관계수 신호 — 외부데이터 병합 필요, 후속 세션
- 가중치/클립범위(`±0.3`, `0.7` 등 상수)의 자동탐색 — 1차는 수동 튜닝 후 검증, 검증되면
  `scripts/grid_search.py` 인프라 재사용 여부를 별도 논의
- 2단계 하이브리드 ML(반전 위험 감지 보조모델) — 별도 세션. 단 `regime_features.py`의
  함수들은 순수 pandas 함수로 설계해 나중에 ML 피처로도 재사용 가능하게 한다
- 프리셋 매핑, 라이브 전환 메커니즘 — 기존 스펙과 동일하게 여전히 범위 밖
