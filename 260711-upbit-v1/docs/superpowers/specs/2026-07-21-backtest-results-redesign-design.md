# 백테스트 결과 탭 — 색상 컨벤션/상세 페이지 리디자인 설계

- 작성일: 2026-07-21
- 상태: 승인 대기 (사용자 리뷰 전)
- 참고: `C:\Users\jungm\project\backtesting_1`의 `backend/app/engine/metrics.py`(12종 성과 지표 계산)와 `frontend/components/charts/PriceChart.tsx`(캔들+마커 차트)를 포팅해서 쓴다.
- 레퍼런스 이미지: `docs/jenport_ref/fng-success.png` (성과 지표 그리드 배치, 가격 차트 스타일)

## 배경 및 목적

`백테스트 결과` 탭(목록 페이지 + 상세 페이지)에 7가지 개선 요청이 있었다:

1. 수익률 색상: 마이너스=파랑, 플러스=빨강(한국식 컨벤션)으로 통일
2. 상세 페이지 거래 내역에 매수가/매도가/수익금 병기
3. 진입/청산 타임스탬프를 `YYYY-MM-DD HH:MM:SS`로 표기(`T` 제거)
4. 상세 페이지 상단에 대상 코인 표기
5. 상단 성과 지표를 레퍼런스 이미지처럼 제대로 표기
6. `forceClosed`(강제청산 "Y") 의미 명확화
7. 수수료가 수익률에 반영되는지 확인
8. 차트: 자산 곡선(라인) → 코인 캔들+진입/청산 마커, 업비트 색상 컨벤션

조사 결과 2/3/6/7은 대부분 이미 존재하는 데이터의 표시 문제이거나 사소한 수정이고, 4/5/8은 백엔드에 실제 기능 추가가 필요한 갭이다.

## 결정된 사항 (사용자 승인)

- 차트는 자산 곡선을 완전히 대체하는 가격 차트(캔들+B/S 마커)로 간다(둘 다 유지 안 함).
- 색상 컨벤션은 백테스트 탭뿐 아니라 앱 전체(`heatmap`, `ranking` 페이지 포함)로 확장한다.
- 성과 지표는 상세 페이지 **요청 시점마다 재계산**한다(DB에 저장하지 않음) — 차트용 캔들 데이터를 어차피 매번 재조회해야 하므로, 그 김에 지표도 계산. 대신 목록 페이지의 샤프/MDD(backtrader 분석기 값)와 상세 페이지의 샤프/MDD(`calculate_metrics` 값)는 계산 방식이 달라 근소하게 다를 수 있음 — 이번 범위에서는 감수한다.
- 진입/청산 마커 색상은 캔들 몸통 색(빨강=상승/파랑=하락)과는 별도의 색으로, 시각적 혼동을 피한다.

## 1. 색상 컨벤션 공용 유틸

신규 파일 `frontend/lib/return-rate-color.ts`:

```ts
export function returnRateColor(rate: number | null): string {
  if (rate === null || rate === 0) return '';
  if (rate > 0) return 'text-red-600 dark:text-red-400';
  return 'text-blue-600 dark:text-blue-400';
}
```

다음 파일들의 동일 목적 중복 함수를 이 유틸로 교체:
- `frontend/app/backtests/page.tsx`
- `frontend/app/backtests/[runId]/page.tsx` (인라인 삼항 연산자 → 함수 호출로)
- `frontend/app/heatmap/page.tsx`
- `frontend/app/ranking/page.tsx`

`frontend/components/CoinSelect.tsx`의 `changeColorClass`는 이미 같은 컨벤션이라 변경하지 않는다.

## 2. `engine/metrics.py` 신규 (성과 지표 계산)

`backtesting_1/backend/app/engine/metrics.py`를 포팅하되 `monthly_returns`는 제외(요청 범위 밖):

```python
def calculate_metrics(
    equity_curve: list[dict],
    trades: list[dict],
    initial_capital: float,
    df: pd.DataFrame,
    timeframe: str = "1d",
) -> dict:
    """반환: total_return, cagr, mdd, sharpe_ratio, sortino_ratio, calmar_ratio,
    win_rate, profit_factor, avg_holding_period, max_consecutive_loss,
    buy_and_hold_return, total_trades"""
```

핵심 계산 방식(포팅 원본 그대로):
- `total_return`: `(final_val - initial_capital) / initial_capital * 100`
- `cagr`: `(final_val/initial_capital) ** (365/days) - 1`, `days`는 equity_curve 첫/마지막 타임스탬프 차이
- `mdd`: equity curve의 누적최고점 대비 낙폭 최솟값
- `sharpe_ratio`/`sortino_ratio`: equity curve의 bar-to-bar 수익률에 `sqrt(252)` 연율화(짧은 봉타임프레임에서도 이 근사를 그대로 사용 — 기존 backtrader `SharpeRatio` 분석기도 동일한 단순화를 쓰고 있어 새로운 부정확성을 추가하는 게 아님)
- `calmar_ratio`: `cagr / abs(mdd)`
- `win_rate`/`profit_factor`/`max_consecutive_loss`: trades의 `pnl` 부호로 계산
- `avg_holding_period`: `holdingPeriod`(봉 개수)를 타임프레임별 분(分)으로 환산해 일 단위로
- `buy_and_hold_return`: 조회한 OHLCV `df`의 첫/마지막 종가로 계산

## 3. `engine/cache.py::load_result()` 확장

```python
def load_result(run_id: str) -> dict | None:
    # 기존 backtest_results 조회에 backtest_runs JOIN 추가
    # SELECT ... r.market, r.timeframe, r.start, r.end, r.risk_config_json
    # FROM backtest_results res JOIN backtest_runs r ON r.id = res.run_id
    # WHERE res.run_id = ?
```

반환 dict에 `market`, `timeframe`, `start`, `end`, `initial_capital`(risk_config_json에서 추출) 추가.

## 4. `engine/runner.py` 수수료 버그 수정

강제청산 경로(174~198행 부근)에서 청산 수수료만 빼고 있음:

```python
# 기존
commission_cost = last_close * size * commission_rate
pnlcomm = pnl_gross - commission_cost
```

진입 수수료도 빼도록 수정:

```python
entry_commission = entry_price * size * commission_rate
exit_commission = last_close * size * commission_rate
pnlcomm = pnl_gross - entry_commission - exit_commission
```

## 5. `backend/main.py` — 상세 엔드포인트 재구성

`GET /api/v1/backtests/{run_id}`:

1. `load_result(run_id)`로 `market`/`timeframe`/`start`/`end`/`initial_capital`/`equity_curve`/`trades`/`final_value` 확보
2. `get_candles(market, timeframe, start, end)`로 차트/Buy&Hold용 OHLCV `df` 재조회 (이미 파일 캐시되어 있어 빠름)
3. `calculate_metrics(equity_curve, trades, initial_capital, df, timeframe)` 호출
4. 응답 바디:
   ```python
   {
     "market": ..., "timeframe": ..., "start": ..., "end": ...,
     "final_value": ...,
     "metrics": {...12개 필드...},
     "ohlcv": [{"time": ..., "open": ..., "high": ..., "low": ..., "close": ...}, ...],
     "trades": [...],
   }
   ```
   최상위 `equity_curve`/`sharpe`/`max_drawdown`는 제거(대신 `metrics` 안의 값 사용).

## 6. 프론트 타입 (`frontend/lib/types/eda.ts`)

```ts
export interface BacktestMetrics {
  total_return: number;
  cagr: number;
  mdd: number;
  sharpe_ratio: number;
  sortino_ratio: number;
  calmar_ratio: number;
  win_rate: number;
  profit_factor: number;
  avg_holding_period: number;
  max_consecutive_loss: number;
  buy_and_hold_return: number;
  total_trades: number;
}

export interface OhlcvPoint {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
}

export interface BacktestDetail {
  market: string;
  timeframe: string;
  start: string;
  end: string;
  final_value: number;
  metrics: BacktestMetrics;
  ohlcv: OhlcvPoint[];
  trades: Trade[];
}
```

`EquityPoint`는 더 이상 `BacktestDetail`에서 쓰지 않지만(다른 곳에서 참조 안 하면) 타입 자체는 남겨둬도 무방 — 실제 미사용 확인되면 삭제.

## 7. 가격 차트 (`frontend/components/PriceChart.tsx` 신규)

`backtesting_1/frontend/components/charts/PriceChart.tsx`를 포팅:

- `chart.addSeries(CandlestickSeries, { upColor: '<빨강>', downColor: '<파랑>', ... })` — 업비트 컨벤션으로 반전(원본은 초록/빨강)
- `createSeriesMarkers(series, markers)`로 진입(B)/청산(S) 마커 표시. 마커 색은 캔들 색과 별도(예: 진입=파란 화살표 위, 청산=주황 화살표 아래)
- 일봉처럼 여러 거래가 한 캔들에 겹치는 타임프레임에서는 원본의 날짜별 집계 로직(`B×N`/`S×N`) 그대로 사용
- 다크 테마, 리사이즈 대응 등 기존 `EquityCurveChart.tsx`의 패턴 유지

`frontend/components/EquityCurveChart.tsx`는 삭제.

## 8. 상세 페이지 (`frontend/app/backtests/[runId]/page.tsx`) 레이아웃

```
KRW-BTC · 15분 · 2026-04-22 ~ 2026-07-21          ← market/timeframe/기간
총 수익률 +12.34%   MDD -8.21%   총 거래 11건        ← 압축 요약 줄

성과 지표
┌───────────────┬───────────────┐
│ 총 수익률      │ CAGR          │
│ Buy&Hold      │ MDD           │
│ 샤프 비율      │ 소르티노       │
│ 칼마 비율      │ 총 거래        │
│ 승률          │ 손익비         │
│ 평균 보유      │ 최대연속손실    │
└───────────────┴───────────────┘

가격 차트 (캔들 + B/S 마커)

거래 내역 (N건)
진입              청산              수익률(%)   매수가   매도가   수익금   보유기간   상태
2026-06-05 08:00:00 2026-06-06 04:15:00  -5.29    ...     ...     ...      71       청산됨
...                                                                                  보유중(기간종료) ← forceClosed
```

- `상태` 컬럼: `forceClosed`가 `true`면 "보유중(기간종료)" 배지 + 툴팁("매도 조건을 만족하지 못해 백테스트 종료 시점 종가로 평가됨"), `false`면 "청산됨"
- 진입/청산 시각은 신규 포맷 함수(`frontend/lib/format.ts`의 `formatDateTime`)로 `YYYY-MM-DD HH:MM:SS` 표기
- 수익률 색상은 1번의 `returnRateColor` 재사용

## 범위 밖

- 목록 페이지(`backtests/page.tsx`)의 샤프/MDD 계산 방식을 `calculate_metrics`로 통일하는 것 — 이번엔 상세 페이지만 바꾼다(2번 섹션에 명시한 근소한 수치 차이는 감수).
- 월별 수익률(`monthly_returns`) — 요청에 없어 포팅하지 않음.
- 마커/차트 상호작용(줌, 툴팁 커스터마이징 등) 고도화 — 포팅 원본 수준으로만.

## Self-Review 결과

- **스펙 커버리지**: 사용자가 승인한 4가지(가격 차트로 완전 교체, 색상 컨벤션 앱 전체 확장, 지표 요청 시점 재계산, 마커 색 분리)가 각각 결정된 사항/5번/7번 섹션에 반영됨.
- **내부 정합성**: 목록 페이지와 상세 페이지의 샤프/MDD 계산 방식이 다르다는 점을 "결정된 사항"과 "범위 밖" 양쪽에 일관되게 명시해 모순 없앰.
- **범위 확인**: monthly_returns, 목록 페이지 지표 통일, 차트 고도화를 범위 밖으로 명시.
- **대상 파일 목록**: `engine/metrics.py`(신규), `engine/cache.py`, `engine/runner.py`, `backend/main.py`, `frontend/lib/types/eda.ts`, `frontend/lib/return-rate-color.ts`(신규), `frontend/lib/format.ts`(신규), `frontend/components/PriceChart.tsx`(신규), `frontend/components/EquityCurveChart.tsx`(삭제), `frontend/app/backtests/[runId]/page.tsx`, `frontend/app/backtests/page.tsx`, `frontend/app/heatmap/page.tsx`, `frontend/app/ranking/page.tsx`, 그리고 관련 테스트 파일(`tests/test_cache.py`, `tests/test_runner.py`, 신규 `tests/test_metrics.py` — TDD로 구현 시 writing-plans 단계에서 다룸).
