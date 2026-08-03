---
name: grid-search
description: Parse a grid search request (coin/capital/timeframe/date-range/topN) and hand the user a prefilled link to the Grid Search web tab, which actually runs it. Trigger when the user sends a message starting with "grid search" followed by comma-separated 코인명,운용자금,봉데이터,운용기간,상위N개 (e.g. "grid search 이더리움,1000만원,1시간,2026-06-01~2026-07-31,20"). 업비트 백테스트 전략의 매수/매도 오실레이터 지표 조합 그리드서치 요청을 파싱해 웹 탭(/grid-search) 실행 링크로 안내할 때 사용합니다.
---

# Grid Search

`grid search` 명령을 받으면 오실레이터 9종(RSI/STOCH_K/STOCH_D/CCI/WILLIAMS_R/BB_PERCENT_B/
MACD_PPO/MACD_PPO_signal/ATR_PCT — ATR_PCT만 매수·매도 양방향) + 매도전용 3종
(STOP_LOSS_PCT/TAKE_PROFIT_PCT/HOLDING_PERIOD_BARS)의 전 교차 그리드(20,700개 조합) 요청을
파싱/검증하고, 실제 계산은 프론트엔드 "Grid Search" 탭(`/grid-search`)에서 사용자가 직접
실행하도록 프리필된 링크로 안내한다. 이 스킬 자신은 `scripts/grid_search.py`를 실행하지도,
결과를 저장하지도 않는다.

## 명령 형식

```
grid search [코인명],[운용자금],[봉데이터],[운용기간],[상위N개]
```

- 콤마로 구분된 5개 필드, 순서 고정.
- `상위N개`는 생략 가능(생략 시 20).
- 예시: `grid search 이더리움,1000만원,1시간,2026-06-01~2026-07-31,20`

## 파싱 규칙

| 필드 | 예시 | 변환 규칙 |
|---|---|---|
| 코인명 | `이더리움` | 코인명을 마켓코드로 매핑(이더리움→ETH→`KRW-ETH`). 별도 룩업 테이블 없이 직접 추론. 모호하면 사용자에게 되물어라. |
| 운용자금 | `1000만원` | 원화 정수로 환산(`10000000`). `1억`, `500만원` 등 한글 단위를 지원하라. |
| 봉데이터 | `1시간` | 아래 고정 매핑표만 사용하라: `1분→minutes1`, `3분→minutes3`, `5분→minutes5`, `15분→minutes15`, `30분→minutes30`, `1시간→minutes60`, `4시간→minutes240`, `1일→days`. 표에 없는 단위는 미지원이라고 안내하고 진행하지 마라. |
| 운용기간 | `2026-06-01~2026-07-31` | `~`로 시작일/종료일을 그대로 분리해 사용하라. `최근 3개월` 같은 duration 표현은 지원하지 않는다 — 명시적 날짜 범위를 요청하라. |
| 상위N개 | `20` | 생략 시 20. 50 초과 입력은 50으로 캡하고, 캡했다는 사실을 사용자에게 안내하라. |

코인명/운용자금/봉데이터/운용기간 4개 필드 중 하나라도 파싱할 수 없거나 모호하면, 실행 안내
없이 사용자에게 되물어라. 부분 입력으로 임의 진행하지 마라.

## 실행 절차

1. 위 규칙대로 명령을 파싱한다.
2. 코인명/운용자금/봉데이터/운용기간 4개 필드 중 하나라도 파싱할 수 없거나 모호하면,
   실행 안내 없이 사용자에게 되물어라.
3. 파싱에 성공하면 아래 형태의 링크를 만든다(로컬 개발 서버 주소):

   ```
   http://localhost:3000/grid-search?market=KRW-SOL&timeframe=minutes60&capital=1000000&start=2026-06-05&end=2026-08-03&topN=20
   ```

4. `scripts/grid_search.py`를 직접 실행하지 말고, 파싱 결과 표와 함께 위 링크를 안내한다.
   예:

   > 아래 조건으로 "Grid Search" 탭에서 바로 실행할 수 있습니다:
   >
   > | 필드 | 값 |
   > |---|---|
   > | 마켓코드 | KRW-SOL |
   > | timeframe | minutes60 |
   > | 운용자금 | 1,000,000원 |
   > | 기간 | 2026-06-05 ~ 2026-08-03 |
   > | 상위N개 | 20 |
   >
   > http://localhost:3000/grid-search?market=KRW-SOL&timeframe=minutes60&capital=1000000&start=2026-06-05&end=2026-08-03&topN=20
   >
   > 진행률과 요청 이력도 그 탭에서 확인할 수 있습니다.

## 주의 사항

- 이 스킬은 더 이상 `scripts/grid_search.py`를 직접 실행하지 않는다 — 파싱/검증 후 웹 탭으로
  안내만 한다. 실제 실행/진행률 추적/취소는 프론트엔드 "Grid Search" 탭(`/grid-search`)과
  백엔드 `/api/v1/grid-search/jobs*` 엔드포인트가 담당한다.
- 링크의 쿼리파라미터는 `market`(마켓코드)/`timeframe`(timeframe 코드)/`capital`(원 단위
  정수)/`start`/`end`(`YYYY-MM-DD`)/`topN`(정수) 6개다. 값에 특수문자가 없으므로 별도 URL
  인코딩 없이 그대로 이어 붙이면 된다.
