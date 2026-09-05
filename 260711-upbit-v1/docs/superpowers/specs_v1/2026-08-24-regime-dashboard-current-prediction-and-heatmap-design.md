# 장세 판별 대시보드 — 현재 예측 카드 + Confusion Matrix 히트맵 설계

## 배경 및 목적

`/regime` 대시보드([[upbit-v1-regime-detector-web-dashboard]] 참고, SHIPPED&PUSHED
ee83496)를 실제로 써본 사용자가 KRW-DOGE 결과를 보며 두 가지를 요청했다.

1. "지금 시점에서 앞으로 어떻게 예측하는지"를 명시적으로 보여주는 화면이 없다 — 지금은
   과거 검증(백테스트) 결과만 보여줄 뿐, 조회 결과의 마지막 봉이 사실상 "현재" 예측인데도
   차트의 캔들 색으로만 존재하고 숫자/텍스트로 뽑아 보여주지 않는다.
2. Confusion Matrix의 행/열을 "행=실제, 열=예측"으로 바꾸고, 시각화(히트맵)로 왼쪽에
   배치, 오른쪽엔 실제 카테고리 분포 표를 배치해달라.

논의 중 사용자가 "1시간봉이면 1시간 뒤만 예측하는 구조인가"를 물어서, 예측 지평이
타임프레임과 무관하게 `HALF_LIFE_DAYS(1.0) × N_MULTIPLIER(2.5) ≈ 2.5일`로 고정돼 있다는
점(엔진 상수, 이번 세션에서 안 바꿈)을 확인했고, 이걸 사용자가 조절 가능하게 만들지
물어본 결과 "지금은 고정값을 명시만" 하기로 결정됨.

## 비범위 (명시적으로 하지 않는 것)

- `N_MULTIPLIER`/`HALF_LIFE_DAYS` 등 예측 지평을 사용자가 조절 가능하게 만들지 않는다 —
  고정값을 텍스트로 명확히 보여주는 것까지만.
- `engine/regime_detector.py`는 여전히 수정하지 않는다.
- "카테고리별 적중률"(최상단 표)의 구조는 바꾸지 않는다 — 이번 요청 범위 밖.
- `resolveColor`/`CATEGORY_ORDER` 같은 기존 컴포넌트 간 소규모 중복을 이번 기회에
  공용 유틸로 추출하지 않는다 — 기존 대시보드 3개 컴포넌트(TrendSegmentChart,
  PriceChart, RegimeChart)도 이미 이 패턴이고, 최종 브랜치 리뷰가 "지금 당장 고칠 필요
  없음"으로 확인한 부분이다. 새 컴포넌트도 같은 관례(작은 중복 허용)를 따른다.
- 히트맵 색상은 새 CSS 커스텀 프로퍼티를 추가하지 않고, 컴포넌트 내부에서 고정
  oklch 값 + 인라인 스타일로 처리한다(캔버스 색상 변환 트릭은 lightweight-charts
  전용 문제라 여기선 불필요 — 일반 DOM 엘리먼트는 oklch()를 네이티브로 파싱함).

## 설계 ① 현재 예측 카드

### 백엔드 — `evaluate_market()` 응답 확장

`backend/regime_service.py`의 `evaluate_market()`이 이미 매 봉의 확률벡터를
`regime_series`(로컬 변수)로 갖고 있다 — 그 마지막 원소를 응답에 노출하기만 하면 된다.
새 계산 로직 없음.

응답에 `current_prediction` 필드 추가:

```python
current_prediction: dict | None = None
if candles:
    last_probs = regime_series[-1] if regime_series else None
    current_prediction = {
        "time": candles[-1]["time"],
        "predicted_category": candles[-1]["predicted_category"],
        "probs": last_probs,  # None이면 워밍업 미달
    }
```

- `candles`가 비어있으면(빈 df) `current_prediction`도 `None`.
- `candles`는 있지만 마지막 봉이 워밍업 기간 안이면 `predicted_category`/`probs` 둘 다
  `None`(이미 `candles[-1]["predicted_category"]`가 `None`인 경우와 동일 조건).

### 프론트 — `RegimeCurrentPrediction` 컴포넌트(신규)

폼 바로 아래, 차트 위에 배치. `RegimeBacktestResult`(확장된 `current_prediction` 포함) +
현재 조회한 `market`/`timeframe` 문자열을 props로 받는 순수 표시 컴포넌트.

- `current_prediction`이 `null` → "데이터 없음"(기존 빈 상태와 동일 문구 재사용)
- `current_prediction.predicted_category`가 `null` → "판단 불가(데이터 부족 — 워밍업
  기간 이내)"
- 정상 케이스:
  - 예측 카테고리를 큰 글씨로, 옆에 확신도(해당 카테고리 확률 %) 표시
  - 5개 카테고리 확률을 작은 가로 막대로 전체 표시(어느 쪽으로 치우쳤는지 한눈에 —
    `RegimeChart`의 카테고리 색상 매핑과 같은 색 계열 사용, 컴포넌트 내부에 로컬
    상수로 재정의)
  - 안내문: "{market} {timeframe 한글라벨} 기준, {current_prediction.time} 봉 데이터.
    약 {n_bars}봉({half_life_bars × N_MULTIPLIER를 일수로 환산, 소수 첫째자리}일) 뒤까지의
    추세를 예측합니다." — 조회 종료일이 오늘이 아니면 "지금"이 아니라는 걸 사용자가 알 수
    있도록 시각을 항상 명시.

## 설계 ② Confusion Matrix 행/열 반전 + 히트맵 + 레이아웃

### 백엔드 변경 없음

`confusion: Record<예측, Record<실제, number>>` 데이터 구조는 그대로 둔다. 이미
`confusion[predicted][actual]`로 양쪽 축 모두 이름으로 접근 가능하므로, 프론트에서
순회 순서만 바꾸면 된다 — API 계약 변경 없음(회귀 위험 최소화).

### 프론트 — `RegimeAccuracyReport.tsx` 재구성

- **Confusion Matrix**: 바깥 루프를 실제 카테고리(행), 안쪽 루프를 예측 카테고리(열)로
  바꿔 렌더링. 헤더도 "실제\예측"으로 변경.
- **히트맵**: 각 셀 배경을 그 행(고정된 실제 카테고리) 내 비율
  (`confusion[predicted][actual] / rowTotal`, `rowTotal = actual_totals[actual]`)에
  비례한 음영으로 칠한다 — 실제 카테고리 총 발생 건수 대비, 판별기가 그 순간마다 뭐라고
  예측했는지의 분포를 색으로 즉시 보여준다. 값이 0이면 무색, 값이 100%에 가까울수록
  진해짐. 텍스트(건수)는 항상 셀 안에 유지.
- **레이아웃**: 상관계수 섹션 아래를 2단 그리드로 분할 — 왼쪽에 이 히트맵, 오른쪽에
  기존 "실제 카테고리 분포" 표를 그대로 이동(내용/계산 변경 없음, 위치만 이동). 모바일
  폭에서는 세로로 쌓이도록(기존 그리드 반응형 관례 재사용).

## 데이터 흐름

```
[프론트 폼 제출] → GET /api/v1/regime/backtest
  → evaluate_market() (candles + current_prediction + confusion + actual_totals + correlation)
  → RegimeDashboard 상태 저장(market/timeframe도 함께 보관 — 현재 예측 카드 안내문에 필요)
  → RegimeCurrentPrediction (신규, current_prediction 소비)
  → RegimeChart (기존, candles 소비, 변경 없음)
  → RegimeAccuracyReport (재구성 — confusion 축 반전+히트맵, actual_totals 위치 이동,
    correlation 위치 불변)
```

## 에러/빈 상태

- `candles`가 비어있는 기존 케이스(빈 상태 문구)는 그대로 — `current_prediction`도 자연히
  `null`이 되므로 `RegimeCurrentPrediction`은 아예 렌더하지 않거나(다른 컴포넌트들과 동일하게
  전체가 빈 상태 문구로 대체됨) 별도 처리 불필요.
- 워밍업 미달로 `predicted_category`/`probs`가 `null`인 경우는 위에 명시한 "판단 불가"
  문구로 명확히 구분 — 0%/빈 막대처럼 오해할 수 있는 표시를 피한다.

## 테스트

- 백엔드: `tests/test_regime_service.py`에 `current_prediction` 필드 검증 테스트 추가(정상
  케이스: 마지막 봉의 `time`/`predicted_category`/`probs`가 `compute_regime_probs_series`의
  마지막 원소와 일치하는지; 워밍업 미달 케이스: `predicted_category`/`probs`가 `None`인지;
  빈 candles 케이스: `current_prediction`이 `None`인지).
- 프론트: 자동 테스트 프레임워크 없음(기존 관례 동일) — 개발 서버로 실제 데이터 조회해
  현재 예측 카드 숫자가 차트 맨 오른쪽 캔들 색과 일치하는지, 히트맵 행 합계가 실제
  카테고리 분포 표의 해당 카테고리 건수와 일치하는지 직접 확인.
