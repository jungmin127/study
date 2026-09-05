# 장세 판별기 웹 대시보드 설계

## 배경 및 목적

`docs/superpowers/specs_v1/2026-08-23-realtime-regime-detector-design.md`에서 만든 실시간 장세
판별기(`engine/regime_detector.py`)는 카테고리 대표값 재보정(커밋 f0d1d1b)까지 마쳤지만,
"이 규칙기반 EWMA 판별기가 실제로 예측력이 있는가"라는 질문은 여전히 열려 있다
(확률벡터-실현수익률 상관계수 |r|≤0.06). 검증은 지금 `scripts/regime_backtest.py`를 CLI로
돌려 콘솔 출력을 읽는 방식뿐이라, 코인/봉타입을 바꿔가며 반복 확인하기 번거롭고 예측이
실제로 어떻게 나오는지 시각적으로 확인할 수도 없다.

이 세션의 목적은 사용자가 프론트엔드에서 **코인/봉타입별로 판별기가 어떤 예측을 내는지
시계열로 보고, 그 예측이 실제 데이터와 비교했을 때 얼마나 정확한지**를 직접 확인할 수 있는
화면을 추가하는 것이다. "예측력이 있는가"를 결론짓는 것 자체는 이 세션의 목적이 아니다 —
그 판단에 필요한 근거를 반복 조회 가능한 형태로 제공하는 것이 목적이다.

## 비범위 (명시적으로 하지 않는 것)

- 캘리브레이션 곡선(신뢰도 구간별 실제 적중률 비교)은 만들지 않는다. 사용자가 원하는 건
  "예측값 표시 + 그 예측이 실제와 비교해 얼마나 정확한가"이며, 이는 이미
  `regime_backtest.py`가 만드는 카테고리별 hit-rate/confusion matrix 수준으로 충분하다고
  확인함.
- 결과 캐싱/DB 저장은 하지 않는다. 매 조회마다 즉시 계산(온디맨드)한다.
- 파라미터 자동튜닝, 프리셋 매핑, 라이브 데몬 연동은 여전히 별도 세션 범위다(선행 스펙에서
  이미 비범위로 못박음, 이 세션도 동일).
- 판별기 로직(`engine/regime_detector.py`) 자체는 수정하지 않는다. 이번 세션은 순수하게
  기존 판별기의 결과를 조회/시각화하는 레이어만 추가한다.

## 아키텍처

```
[프론트: /regime 탭]
  RegimeBacktestForm (마켓/봉타입/기간 선택)
        │ GET /api/v1/regime/backtest?market=&timeframe=&start=&end=
        ▼
[백엔드: backend/main.py → backend/regime_service.py]
  evaluate_market()  ← scripts/regime_backtest.py의 _evaluate_market()을 이 파일로 이전
        │ get_candles() + engine.regime_detector.compute_regime_probs_series() 등 호출
        ▼
  { candles(시계열+예측카테고리), report(confusion/correlation/actual_totals) }
        │
        ▼
[프론트 렌더]
  RegimeChart (캔들스틱 + 예측 카테고리 색상)
  RegimeAccuracyReport (hit-rate 표 + confusion matrix + 상관계수 + 실제분포)
```

`scripts/regime_backtest.py`의 `_evaluate_market()`을 `backend/regime_service.py`로
이전하고, 스크립트는 이 함수를 import해서 그대로 쓴다(CLI 동작 불변). API와 CLI가 같은
함수를 호출하므로 계산 로직이 두 곳으로 갈라져 스케일 버그가 재발할 여지가 없다.

## 백엔드 설계

### `backend/regime_service.py` (신규)

- `evaluate_market(market: str, timeframe: str, start: datetime, end: datetime) -> dict`:
  기존 `_evaluate_market()` 로직(get_candles → compute_regime_probs_series →
  confusion/correlation/actual_totals 집계)을 그대로 옮기되, 반환값에 봉별 원시 데이터를
  추가한다:
  ```python
  {
      "half_life_bars": float,
      "n_bars": int,
      "candles": [
          {"time": "2024-01-01T00:00:00Z", "open": ..., "high": ..., "low": ...,
           "close": ..., "predicted_category": "완만상승" | None},
          ...
      ],
      "confusion": {...},          # 기존과 동일
      "actual_totals": {...},      # 기존과 동일
      "correlation": float | None, # NaN 대신 None(JSON 직렬화)
  }
  ```
  - `predicted_category`는 `regime_series[t]`가 `None`이 아니면
    `max(probs, key=probs.get)`, `None`이면 워밍업 미달로 `None`.
  - confusion/actual_totals/correlation 집계는 기존 로직 그대로(마지막 `n_bars`는 미래
    구간이 없어 "정답"을 매길 수 없으므로 집계에서 제외 — 기존과 동일한 동작). 단, `candles`
    배열에는 이 꼬리 구간의 `predicted_category`도 그대로 포함한다(차트에는 표시하되
    정확도 집계에는 넣지 않는다는 뜻).

- `scripts/regime_backtest.py`는 `from backend.regime_service import evaluate_market`으로
  바꾸고, 자체 정의했던 `_evaluate_market()`은 제거한다. `main()`의 출력 포맷팅 로직은 그대로
  유지(단, 반환값에 `candles` 필드가 추가된 것은 무시하면 된다).

### `backend/main.py` — 신규 엔드포인트

```
GET /api/v1/regime/backtest?market=KRW-BTC&timeframe=minutes60&start=2025-01-01&end=2026-08-23
```

- `regime_service.evaluate_market()`을 호출해 그대로 JSON으로 반환.
- 캔들 데이터가 없거나(빈 DataFrame) 워밍업 기준(`half_life_bars * WARMUP_MULTIPLIER`)조차
  못 채우는 짧은 기간이면 `candles`는 전부 `predicted_category: null`, `report`는 전부 0건 —
  에러가 아니라 정상 응답(빈 상태는 프론트에서 판단).
- 다른 온디맨드 엔드포인트(`/api/v1/backtests/run` 등)와 동일하게 별도 인증/속도제한 없음.

## 프론트엔드 설계

### 네비게이션

- `frontend/components/NavTabs.tsx`의 `STEPS` 배열에 `{ href: '/regime', title: '장세 판별',
  icon: Waves }` 추가(`lucide-react`의 `Waves` 아이콘 사용 — 기존 아이콘과 겹치지 않고
  파형/추세 뉘앙스에 맞음). `MobileNavDrawer`는 `STEPS`를 그대로 받으므로 추가 수정 불필요.

### `/regime` 페이지 구성

- `frontend/app/regime/page.tsx`: `getMarkets()`를 서버에서 미리 가져와
  `RegimeDashboard`(클라이언트 컴포넌트)에 전달(`AnalysisPage`/`GridSearchPage` 패턴과 동일).
- `frontend/components/RegimeBacktestForm.tsx`: `GridSearchForm.tsx`에서 마켓/봉타입/기간
  선택 UI를 그대로 재사용하는 형태로 신규 작성(`CoinSelect`, `TIMEFRAME_OPTIONS`,
  `defaultDate`). 그리드서치와 달리 자본금/topN 등은 필요 없음 — 마켓/봉타입/시작일/종료일
  4개 필드만. 기본 기간은 시작일 1년 전 ~ 오늘(`defaultDate(365)` ~ 오늘).
- `frontend/components/RegimeChart.tsx`: `TrendSegmentChart.tsx`를 5카테고리용으로 변형.
  캔들스틱 시리즈를 봉별 `predicted_category` 색으로 칠한다. 색상은 기존
  `--price-up`/`--price-down`/`--marker-boundary`/`--trend-unclassified` CSS 변수 팔레트를
  5단계로 확장(급상승/완만상승/횡보/완만하락/급하락 + 미분류 6색). 정확한 색상값은 구현
  단계에서 `globals.css`의 기존 CSS 변수 정의 방식을 따라 라이트/다크 테마 모두 지정한다.
  범례는 6개 항목(카테고리 5개 + 미분류).
- `frontend/components/RegimeAccuracyReport.tsx`: `report`(confusion/correlation/
  actual_totals)를 표로 렌더링. 세 부분:
  1. 카테고리별 hit-rate 표(예측카테고리 / 총건수 / 적중건수 / 적중률%) — confusion matrix
     대각선에서 도출.
  2. confusion matrix 전체(행=예측, 열=실제) — `regime_backtest.py` 콘솔 출력과 동일 구조.
  3. 상관계수 + 실제 카테고리 분포(카테고리별 건수/비율).
  `correlation`이 `null`이면(샘플 부족) "계산 불가(샘플 부족)" 표시.

### 데이터 페칭

- `frontend/lib/api/`에 `getRegimeBacktest(params: { market, timeframe, start, end })` 함수
  추가(기존 `lib/api/eda.ts` 등의 fetch 래퍼 패턴 재사용).
- 폼 제출 시 로딩 상태 표시(기존 `submitting` 패턴), 완료되면 차트+리포트 동시 렌더.

### 에러/빈 상태

- 마켓 목록 조회 실패: `GridSearchForm`의 `marketsError` 패턴과 동일하게 인라인 메시지.
- 캔들 데이터가 아예 없거나 워밍업 기준 미달: 차트 영역에 "선택한 기간에 데이터가
  부족합니다" 안내, 리포트 영역은 렌더하지 않음(0건 표 대신 안내 문구).
- API 호출 자체 실패(5xx/네트워크): 폼 하단에 에러 메시지, 이전 결과 유지하지 않고 초기화.

## 테스트

- 백엔드: `backend/regime_service.py`로 이전한 `evaluate_market()`의 반환 스키마(candles
  배열 형태, confusion/correlation 필드)를 확인하는 pytest 유닛 테스트 추가(합성 가격
  시계열 사용 — 네트워크 호출 없이 `get_candles`를 모킹하거나, 이미 있는
  `tests/test_regime_detector.py`의 `_make_price_df` 패턴을 재사용해 순수 함수 부분만
  검증). `scripts/regime_backtest.py`가 여전히 정상 동작하는지는 실제 실행으로 확인
  (네트워크 호출 필요, 사람이 실행).
- 프론트: 개발 서버를 띄워 실제 브라우저에서 마켓/봉타입/기간을 바꿔가며 차트와 리포트가
  정상 렌더되는지 확인(골든 패스 + 워밍업 미달/데이터 없음 같은 엣지 케이스 포함).

## 열린 질문 (참고, 이 세션 범위 밖)

이 대시보드는 "판별기가 예측력이 있는가"에 대한 사용자의 반복 검증을 돕는 도구다. 상관계수가
낮다는 근본 문제(`[[upbit-v1-realtime-regime-detector-design]]` 메모리 참고)는 이 세션으로
해결되지 않는다 — 대시보드로 여러 코인/봉타입/기간을 눈으로 훑어본 뒤, ②(프리셋 선정) 세션을
어떻게 진행할지는 별도로 논의한다.
