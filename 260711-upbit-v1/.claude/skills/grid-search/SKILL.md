---
name: grid-search
description: Sweep oscillator buy/sell threshold and period grids for an Upbit backtest strategy and save the top results. Trigger when the user sends a message starting with "grid search" followed by comma-separated 코인명,운용자금,봉데이터,운용기간,상위N개 (e.g. "grid search 이더리움,1000만원,1시간,2026-06-01~2026-07-31,20"). 업비트 백테스트 전략의 매수/매도 오실레이터 지표 조합을 그리드서치로 탐색해 상위 결과를 저장할 때 사용합니다.
---

# Grid Search

`grid search` 명령을 받으면 오실레이터 5종(RSI/STOCH_K/STOCH_D/CCI/WILLIAMS_R) + 매도전용
3종(STOP_LOSS_PCT/TAKE_PROFIT_PCT/HOLDING_PERIOD_BARS)의 전 교차 그리드(2,565개 조합)를
`scripts/grid_search.py`로 계산하고, 중복 거래를 제거한 상위 N개를 "백테스트 결과"에 저장한다.

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

코인명/운용자금/봉데이터/운용기간 4개 필드 중 하나라도 파싱할 수 없거나 모호하면, 스크립트를
실행하지 말고 사용자에게 되물어라. 부분 입력으로 임의 진행하지 마라.

## 실행 절차

1. 위 규칙대로 명령을 파싱한다.
2. 파싱 결과를 표로 정리해 사용자에게 보여주고 확인을 받는다. 이 표에는 반드시
   마켓코드/timeframe 코드/운용자금(원 단위 숫자)/시작일/종료일/상위N개가 포함되어야 한다.
   예상 소요 시간(약 9분, 2,565개 조합 기준)도 함께 안내한다.
3. 사용자가 확인하면, 아래 형태로 `scripts/grid_search.py`를 저장소 루트에서 백그라운드로
   실행한다:

   ```bash
   PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/grid_search.py --market KRW-ETH --timeframe minutes60 \
     --capital 10000000 --start 2026-06-01 --end 2026-07-31 --top-n 20
   ```

4. 실행이 끝나면 stdout 마지막 줄의 `RESULT_JSON: {...}`를 파싱한다. 그 앞의 로그 줄들은
   사람이 읽는 진행 상황이므로 필요하면 요약해서 보여줘도 되지만, 최종 보고 수치는 반드시
   `RESULT_JSON`에서 가져온다.
5. 사용자에게 `total_combos`(총 조합 수), `elapsed_sec`(소요 시간), `saved` 리스트(순위/수익률/
   제목)를 요약해서 보고하고, "백테스트 결과" 페이지(`[Grid]` 접두사)에서 상세를 확인할 수
   있다고 안내한다.

## 주의 사항

- `--capital`은 원 단위 정수로 넘긴다(예: 1000만원 → `10000000`).
- `--start`/`--end`는 `YYYY-MM-DD` 형식이어야 한다.
- 스크립트는 저장소 루트(`260711-upbit-v1/`)에서 `PYTHONPATH=.`를 붙여 실행해야 한다(`from engine...`, `from upbit_data_service...` 절대 임포트를 쓰는데, `python scripts/grid_search.py`로 직접 실행하면 스크립트 소속 디렉터리만 `sys.path`에 잡혀 `ModuleNotFoundError: No module named 'engine'`가 난다 — `run_eda_sweep.py`도 동일).
- Windows 환경에서는 `PYTHONIOENCODING=utf-8`도 함께 붙여야 한다. 안 붙이면 Python이 콘솔 코드페이지(한글 Windows는 cp949)로 stdout을 인코딩해서, 진행 로그와 `RESULT_JSON`의 한글(제목 등)이 깨져 나온다.
