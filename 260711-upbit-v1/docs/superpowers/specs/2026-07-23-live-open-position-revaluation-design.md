# 성과 지표 툴팁 + 미청산 포지션 실시간 재평가 + 목록 페이지 개선 설계

- 작성일: 2026-07-23
- 상태: 승인 완료
- 선행 작업: `docs/superpowers/specs/2026-07-21-backtest-results-redesign-design.md` (12종 성과 지표 그리드, 캔들+마커 차트, forceClosed 배지 — 이번 설계가 그 결과물 위에 얹힌다)

## 배경 및 목적

백테스트 결과 탭 리디자인 완료 후 후속 요청:

1. 성과 지표 12개 타일에 물음표 아이콘 + 설명 툴팁 추가
2. 가격 차트가 백테스트 종료일까지만 보이는 이유 확인 (예: 종료일 7/21, 오늘 7/23)
3. 아직 청산되지 않고 "보유중(기간종료)"인 포지션의 수익률이 실시간(현재가 기준)으로 보이는지 확인, 안 되면 구현 — 상세 페이지와 목록(`/backtests`) 탭 양쪽에
4. (추가 요청) 목록 페이지 기간 컬럼을 `YYYY-MM-DD ~ YYYY-MM-DD`로 단순화, 매수전략/매도전략 컬럼 추가, 수익률/실행시각/코인명/봉타입 기준 정렬(오름/내림차순) 추가

조사 결과 2/3은 사실 하나의 근본 원인으로 연결된다: `forceClosed: true` 거래는 "백테스트 종료 시점 종가로 강제 평가"된 스냅샷일 뿐, 그 이후의 실제 가격 변동이 전혀 반영되지 않는다. 이번 설계는 이 스냅샷을 요청 시점의 최신 데이터로 다시 평가하는 기능을 추가한다. 4번은 같은 목록 페이지를 다루는 별개의 UI 개선 요청이라 같은 설계 문서/구현 계획에 함께 담는다.

## 결정된 사항 (사용자 승인)

- 차트 확장(종료일 이후 ~ 지금까지 캔들 추가 조회)은 **미청산 포지션이 있을 때만** 적용한다. 모든 거래가 정상 청산된 백테스트는 지금처럼 요청한 기간만 보여준다.
- "실시간"은 **페이지를 열거나 새로고침할 때마다 서버가 최신 가격으로 재계산**하는 방식이다. 브라우저에 열어둔 채로 자동 갱신(폴링/웹소켓)되는 것은 이번 범위 밖이다.
- 재평가에는 거래의 매매 수량(`size`)이 필요한데 기존 저장된 거래 기록에는 없다. 이번에 `size` 필드를 추가하되, **이미 저장된 기존 백테스트 결과는 소급 적용하지 않는다** — `size` 없이는 재평가를 건너뛰고 지금처럼 고정값을 보여준다. 미청산 포지션이 있는 기존 런은 재실행하면 새 필드가 채워진다.
- 상세 페이지와 목록 페이지는 "현재가"의 출처가 다르다: 상세 페이지는 어차피 확장 조회한 캔들의 마지막 종가를 그대로 쓰고(추가 API 호출 없음), 목록 페이지는 캔들 전체를 매번 가져오기엔 무거우므로 업비트 ticker를 배치 조회해서 쓴다. 두 값이 미세하게 다를 수 있음(캔들 마감가 vs 실시간 체결가)을 감수한다.

## 1. 성과 지표 툴팁

`frontend/components/StrategyConditionBuilder.tsx`에 이미 있는 `InfoTooltip` 컴포넌트(호버 시 나타나는 React 상태 기반 팝오버 — native `title` 속성은 과거에 안 보이는 문제가 있어 폐기된 전례가 있음, `[[upbit-frontend-tailwind-opacity-gotcha]]` 계열 이슈)를 `frontend/components/PriceChart.tsx` 근처 공용 위치로 옮기지 않고, `[runId]/page.tsx`에 동일 패턴으로 새로 만든다(파일 간 의존을 늘리지 않기 위해 — 두 곳 다 작은 컴포넌트라 중복 비용이 낮다).

`MetricTile`에 `tooltip?: string` prop 추가, 있으면 라벨 옆에 물음표 아이콘 렌더링:

```tsx
const METRIC_TOOLTIPS: Record<string, string> = {
  총수익률: '초기 자본 대비 최종 자산의 증감률입니다.',
  CAGR: '연평균 복리 성장률입니다. 백테스트 기간과 무관하게 "연 단위로 환산하면 몇 %인가"를 보여줍니다.',
  'Buy&Hold': '같은 기간 동안 그냥 사서 들고만 있었을 때의 수익률입니다. 전략이 단순 보유보다 나은지 비교하는 기준입니다.',
  MDD: '최대 낙폭(Max Drawdown). 자산이 고점 대비 가장 많이 떨어졌던 비율입니다. 작을수록(0에 가까울수록) 좋습니다.',
  샤프비율: '위험(변동성) 대비 수익률입니다. 무위험수익률 0%를 가정하며, 높을수록 안정적으로 수익을 냈다는 뜻입니다.',
  소르티노: '샤프 비율과 비슷하지만 하락 변동성만 위험으로 봅니다. 상승 변동은 페널티로 치지 않아 샤프보다 후하게 나올 수 있습니다.',
  칼마비율: 'CAGR을 MDD(절대값)로 나눈 값입니다. 수익뿐 아니라 "그 수익을 위해 감수한 최대 손실"까지 함께 고려합니다.',
  총거래: '백테스트 기간 동안 체결된 매수→매도 거래 쌍의 개수입니다.',
  승률: '전체 거래 중 수익이 난(pnl > 0) 거래의 비율입니다.',
  손익비: '총 이익 금액을 총 손실 금액으로 나눈 값입니다(Profit Factor). 1보다 크면 이익이 손실보다 큽니다.',
  평균보유: '한 번 진입해서 청산까지 평균적으로 보유한 기간(일)입니다.',
  최대연속손실: '연속으로 손실이 난 거래의 최대 횟수입니다. 클수록 연속 손실 구간에서 심리적/자금 압박이 컸다는 뜻입니다.',
};
```

(정확한 key 문자열은 구현 시 라벨과 1:1 매핑되도록 정리 — 위는 의미 전달용 초안)

## 2. 차트 확장 (미청산 포지션이 있을 때만)

### 왜 지금은 종료일까지만 보이나

`backend/main.py::get_backtest_detail`이 `get_candles(market, timeframe, start_dt, end_dt)`를 호출할 때 `end_dt`가 항상 **저장된 백테스트의 요청 종료일**이기 때문이다(`result["end"]`). 이 자체는 버그가 아니라 "요청한 백테스트 기간만 보여준다"는 원래 설계다.

### 변경: 미청산 포지션이 있으면 `end_dt`를 지금 시각으로 확장

```python
has_open = has_revaluable_open_trade(result["trades"])  # engine/live_valuation.py
fetch_end_dt = end_dt
if has_open and datetime.now(timezone.utc) > end_dt:
    fetch_end_dt = datetime.now(timezone.utc)

df = get_candles(result["market"], result["timeframe"], start_dt, fetch_end_dt)
```

`get_candles()`는 이미 "마감된 봉만" 반환하므로(`upbit_data_service.py:186`) 별도 처리 없이 그대로 재사용 가능. 이 `df`가 그대로 `ohlcv` 응답과 3번 항목의 "현재가" 출처 양쪽에 다 쓰인다 — 별도 API 호출 불필요.

### 프론트: 경계 마커

`PriceChart.tsx`에 `backtestEnd: string` prop 추가(= `detail.end`, 기존 필드 재사용, 새 API 필드 불필요). `ohlcv` 중 시각이 `backtestEnd` 이후인 첫 봉에 회색 마커(원형, 텍스트 "종료")를 하나 찍는다. 캔들 자체의 색(빨강/파랑)은 확장 구간이라고 달리하지 않는다 — 마커 하나로 구분에 충분하다고 판단. 범례에 회색 점 + "백테스트 종료" 항목 추가.

## 3. 미청산 포지션 실시간 재평가

### 3-1. 데이터 모델: 거래 기록에 `size` 추가

`engine/runner.py`:
- `TradeLogger.notify_trade`가 만드는 완료 거래 dict에 `"size": round(size, 8)` 추가 (이미 지역변수 `size`가 있음, 그냥 딕셔너리에 포함만 하면 됨).
- `_build_forced_close_trade`의 인자 `size: float`도 반환 dict에 `"size": round(size, 8)`로 포함.

완료된(정상 청산) 거래에도 `size`가 붙지만 재평가 대상은 아니다(`forceClosed`인 것만 재평가) — 그냥 일관성을 위해 항상 채워 넣는다.

### 3-2. 순수 계산 함수: `engine/live_valuation.py` (신규)

```python
def has_revaluable_open_trade(trades: list[dict]) -> bool:
    """size가 있는 forceClosed 거래가 하나라도 있으면 True."""
    return any(t.get("forceClosed") and "size" in t for t in trades)


def revalue_open_trades(
    trades: list[dict],
    live_price: float,
    live_time: str,
    commission_rate: float,
) -> tuple[list[dict], float]:
    """forceClosed=True이고 size가 있는 거래를 live_price 기준으로 재평가한 새 리스트와,
    그로 인한 총 평가금액 변화량(delta, 원 단위)을 함께 반환한다.
    size가 없는(레거시) 거래나 forceClosed가 아닌 거래는 그대로 둔다.
    holdingPeriod는 갱신하지 않는다 — 봉 개수 기준 재계산에는 baropen이 필요한데
    저장된 거래 기록에 없어, 이번 범위에서는 "백테스트 종료 시점까지의 보유 기간"으로
    고정해 둔다(알려진 제약, 상세 페이지 캡션에 명시).
    """
    updated: list[dict] = []
    delta = 0.0
    for t in trades:
        if t.get("forceClosed") and "size" in t:
            entry_price = t["entryPrice"]
            size = t["size"]
            pnl_gross = (live_price - entry_price) * size
            entry_commission = entry_price * size * commission_rate
            exit_commission = live_price * size * commission_rate
            new_pnl = round(pnl_gross - entry_commission - exit_commission, 4)
            return_rate = (new_pnl / (entry_price * size) * 100) if (entry_price and size) else 0.0
            delta += new_pnl - t["pnl"]
            updated.append({
                **t,
                "exitPrice": round(live_price, 8),
                "exitTime": live_time,
                "returnRate": round(return_rate, 4),
                "pnl": new_pnl,
            })
        else:
            updated.append(t)
    return updated, round(delta, 4)
```

기존 `_build_forced_close_trade`와 수수료 계산식을 동일하게 맞춰(진입+청산 양쪽 수수료 차감) 재평가 전후 값의 일관성을 보장한다.

### 3-3. `engine/cache.py` 확장

- `load_result()`: 반환 dict에 `"commission_rate": json.loads(risk_config_json).get("commission_rate", 0.0005)` 추가.
- `list_backtest_runs()`: SELECT에 `res.trades_json`도 포함시키고, 반환 dict에 `"trades": json.loads(trades_json)`, `"commission_rate"`, `"initial_capital"`(기존에 `return_rate` 계산용으로 이미 파싱하던 값을 응답 필드로도 노출)을 추가. (이 세 필드는 `backend/main.py`가 재평가 계산에만 쓰고, 클라이언트 응답에는 노출하지 않는다 — 아래 3-5 참고.)

### 3-4. `upbit_data_service.py`: 배치 ticker 조회

```python
def get_current_prices(markets: list[str]) -> dict[str, float]:
    """주어진 마켓들의 현재가(ticker trade_price)를 한 번에 조회한다."""
    if not markets:
        return {}
    market_codes = ",".join(markets)
    resp = httpx.get(f"{UPBIT_BASE_URL}/ticker", params={"markets": market_codes}, timeout=10)
    resp.raise_for_status()
    return {t["market"]: t["trade_price"] for t in resp.json()}
```

`get_krw_markets_with_ticker()`와 거의 같은 패턴이지만 이쪽은 마켓 목록을 인자로 받아 임의의 부분집합에 대해 호출 가능하게 한다(목록 페이지에서 "미청산 포지션이 있는 마켓들"만 골라 한 번에 조회하기 위함).

### 3-5. `backend/main.py`: 두 엔드포인트에 통합

**상세 엔드포인트** (`GET /api/v1/backtests/{run_id}`):

```python
has_open = has_revaluable_open_trade(result["trades"])
fetch_end_dt = end_dt
now = datetime.now(timezone.utc)
if has_open and now > end_dt:
    fetch_end_dt = now

try:
    df = get_candles(result["market"], result["timeframe"], start_dt, fetch_end_dt)
except Exception:
    df = get_candles(result["market"], result["timeframe"], start_dt, end_dt)
    fetch_end_dt = end_dt

trades = result["trades"]
equity_curve = result["equity_curve"]
final_value = result["final_value"]
live_price_as_of = None

if has_open and fetch_end_dt > end_dt and not df.empty:
    live_close = float(df["close"].iloc[-1])
    live_time = df["candle_time"].iloc[-1].isoformat()
    revalued, delta = revalue_open_trades(trades, live_close, live_time, result["commission_rate"])
    if delta != 0.0:
        final_value = round(final_value + delta, 4)
        equity_curve = equity_curve + [{"timestamp": live_time, "value": final_value}]
        trades = revalued
        live_price_as_of = live_time

metrics = calculate_metrics(
    equity_curve=equity_curve, trades=trades,
    initial_capital=result["initial_capital"], df=df, timeframe=result["timeframe"],
)
# ohlcv는 기존처럼 df(이제 확장됐을 수 있는)로부터 구성 — 변경 없음

return {
    ..., "initial_capital": result["initial_capital"], "final_value": final_value,
    "metrics": metrics, "ohlcv": ohlcv, "trades": trades,
    "live_price_as_of": live_price_as_of,
}
```

캔들/네트워크 조회 실패 시(`except Exception`) 원래 `end_dt` 기준으로 조용히 폴백 — 페이지가 깨지지 않는다.

**목록 엔드포인트** (`GET /api/v1/backtests`):

```python
@app.get("/api/v1/backtests")
def get_backtest_runs() -> list[dict]:
    runs = list_backtest_runs()
    markets_needing_price = {r["market"] for r in runs if has_revaluable_open_trade(r["trades"])}

    live_prices: dict[str, float] = {}
    if markets_needing_price:
        try:
            live_prices = get_current_prices(list(markets_needing_price))
        except Exception:
            live_prices = {}

    result = []
    for r in runs:
        live_price = live_prices.get(r["market"])
        is_live = False
        final_value = r["final_value"]
        return_rate = r["return_rate"]
        if live_price is not None and has_revaluable_open_trade(r["trades"]):
            _, delta = revalue_open_trades(
                r["trades"], live_price, datetime.now(timezone.utc).isoformat(), r["commission_rate"],
            )
            if delta != 0.0:
                final_value = round(r["final_value"] + delta, 4)
                initial_capital = r["initial_capital"]  # cache.py가 반환하는 필드, 최종 응답에서는 제외
                return_rate = (final_value - initial_capital) / initial_capital * 100 if initial_capital else None
                is_live = True
        result.append({
            "run_id": r["run_id"], "title": r["title"], "description": r["description"],
            "market": r["market"], "timeframe": r["timeframe"], "start": r["start"], "end": r["end"],
            "created_at": r["created_at"], "final_value": final_value, "return_rate": return_rate,
            "sharpe": r["sharpe"], "max_drawdown": r["max_drawdown"], "is_live": is_live,
        })
    return result
```

`list_backtest_runs()`는 이미 내부적으로 `initial_capital`을 계산해 `return_rate`를 구하고 있다(`engine/cache.py:402-406`). 재평가 시에도 같은 값이 필요하므로, `list_backtest_runs()`의 반환 dict에 `initial_capital`을 정식 필드로 추가한다(3-3 항목에도 반영). `backend/main.py`는 최종 클라이언트 응답을 조립할 때 `trades`/`commission_rate`/`initial_capital`을 제외하고 필요한 필드만 골라 담는다(위 코드의 `result.append({...})` 블록 참고).

### 3-6. 프론트엔드

- `frontend/lib/types/eda.ts`: `BacktestDetail`에 `live_price_as_of: string | null` 추가. `BacktestRunSummary`에 `is_live: boolean` 추가.
- `frontend/app/backtests/[runId]/page.tsx`: `live_price_as_of`가 있으면 상단 요약 근처에 캡션 표시: `현재가 기준으로 재평가됨 (HH:MM:SS 기준)`. forceClosed 배지 title에도 "현재가로 재평가됨" 문구 보강.
- `frontend/app/backtests/page.tsx`: `return_rate` 셀 옆에 `is_live`면 작은 회색 텍스트 `(실시간)` 표시.

## 4. 목록 페이지(`/backtests`) 표시 개선: 기간 포맷 + 전략 요약 + 정렬

이 섹션은 3번(실시간 재평가)과 별개로 사용자가 추가 요청한 항목이며, 같은 페이지(`frontend/app/backtests/page.tsx`, `engine/cache.py::list_backtest_runs()`, `backend/main.py::get_backtest_runs()`)를 다루므로 같은 구현 계획에 포함한다.

### 4-1. 기간 컬럼 포맷

`run.start`/`run.end`는 `"2026-04-22T00:00:00+00:00"` 같은 전체 ISO 문자열이다. 상세 페이지(`[runId]/page.tsx`)가 이미 `detail.start.slice(0, 10)`로 날짜만 잘라 쓰는 것과 동일하게, 목록 페이지도 `${run.start.slice(0, 10)} ~ ${run.end.slice(0, 10)}`로 변경한다. 백엔드 변경 없음.

### 4-2. 매수전략/매도전략 컬럼 추가

**백엔드:**
- `engine/cache.py::list_backtest_runs()`: SELECT에 `r.params_json`을 추가하고, 반환 dict에 `"buy_conditions": json.loads(params_json)["buy_conditions"]`, `"sell_conditions": json.loads(params_json)["sell_conditions"]`를 추가한다(이 함수는 `strategy_name = 'ConditionTreeStrategy'`로 필터링되므로 두 키가 항상 존재함이 보장됨).
- `backend/main.py::get_backtest_runs()`: 3번 설계에서 이미 `list_backtest_runs()` 결과를 가공해 최종 응답을 조립하므로, 그 조립 블록(`result.append({...})`)에 `buy_conditions`/`sell_conditions`도 그대로 포함시킨다.

**프론트엔드:**
- `summarizeGroup`/`isConditionBlock`/`OPERATOR_SYMBOLS`를 `frontend/components/StrategyConditionBuilder.tsx`에서 새 공용 파일 `frontend/lib/condition-summary.ts`로 옮기고 export한다. `StrategyConditionBuilder.tsx`는 이 파일에서 import하도록 수정(중복 제거, 동작 변경 없음).
- `frontend/lib/types/eda.ts`의 `BacktestRunSummary`에 `buy_conditions: ConditionGroup`, `sell_conditions: ConditionGroup` 추가.
- `frontend/app/backtests/page.tsx`(또는 4-3에서 분리되는 `BacktestRunsTable.tsx`): "매수전략"/"매도전략" 컬럼 추가, 각 셀에 `summarizeGroup(run.buy_conditions)`/`summarizeGroup(run.sell_conditions)`를 표시. 셀은 `whitespace-normal max-w-[240px]`로 줄바꿈을 허용해(테이블 기본값인 `whitespace-nowrap` 오버라이드) 조건식이 길어도 셀 안에서 줄바꿈되게 한다(사용자 선택: "셀에 요약문 그대로 줄바꿈 표시").

### 4-3. 정렬 (수익률/실행시각/코인명/봉타입, 각 오름차순/내림차순)

**설계 방향:** 서버는 지금처럼 기본 정렬(`created_at DESC, rowid DESC`)로 전체 목록을 한 번에 반환하고(최대 100건, 페이지네이션 없음 — 기존과 동일), 정렬 자체는 클라이언트에서 처리한다. 이미 전체 목록을 한 번에 fetch하므로 서버 왕복 없이 즉시 재정렬 가능하고, `frontend/components/CoinSelect.tsx`의 "정렬 가능한 컬럼 헤더 클릭" 패턴을 그대로 재사용할 수 있어 새 UI 패턴을 만들 필요가 없다.

- `frontend/app/backtests/page.tsx`(서버 컴포넌트)는 지금처럼 `getBacktestRuns()`로 데이터만 가져오고, 실제 테이블 렌더링은 새 클라이언트 컴포넌트 `frontend/components/BacktestRunsTable.tsx`(`'use client'`, `runs: BacktestRunSummary[]` prop)로 위임한다. `DeleteRunButton`은 이미 별도 클라이언트 컴포넌트라 그대로 이 안에서 쓰인다.
- `BacktestRunsTable.tsx` 내부 정렬 상태: `{ key: 'return_rate' | 'created_at' | 'market' | 'timeframe' | null, direction: 'asc' | 'desc' }`, 초기값 `key: null`(서버가 준 순서 그대로 = 실행시각 내림차순과 결과적으로 동일).
- 정렬 가능한 4개 컬럼("수익률(%)", "실행 시각", "코인", "봉타입") 헤더를 클릭 가능한 버튼으로 바꾸고, 현재 정렬 중인 컬럼에 ▲/▼ 표시. 같은 컬럼을 다시 클릭하면 방향 토글, 다른 컬럼을 클릭하면 그 컬럼의 내림차순부터 시작.
- `return_rate`/`sharpe`/`max_drawdown`처럼 값이 `null`일 수 있는 컬럼은 정렬 방향과 무관하게 항상 맨 뒤로 보낸다.
- 제목/설명/매수전략/매도전략/상세/삭제 컬럼은 정렬 대상 아님(클릭 불가 헤더 그대로).

## 에러 처리 요약

- 캔들 확장 조회 실패 → 원래 `end_dt` 기준으로 폴백, `live_price_as_of: null`.
- ticker 배치 조회 실패 → 전체 목록에 대해 재평가 건너뜀, `is_live: false` 전체.
- `size` 필드 없는(기존) 거래 → `has_revaluable_open_trade`가 `False`를 반환해 애초에 재평가 로직에 진입하지 않음 → 지금과 동일하게 동작.

## 테스트 계획

- `tests/test_live_valuation.py` (신규): `revalue_open_trades`/`has_revaluable_open_trade` 순수 함수 단위 테스트 — 수수료 계산, delta 부호, size 없는 거래 무시, forceClosed 아닌 거래 무시.
- `tests/test_runner.py`: 기존 `test_forced_close_trade_deducts_entry_and_exit_commission`에 `trade["size"] == 2.0` assert 추가. `test_run_backtest_buy_and_hold_once`에 `result["trades"][0]["size"]` 존재 assert 추가.
- `tests/test_upbit_data_service.py`: `get_current_prices` 신규 테스트(httpx 목킹, 빈 리스트 입력 시 빈 dict 등).
- `tests/test_cache.py`: `load_result`가 `commission_rate` 포함하는지, `list_backtest_runs`가 `trades`/`commission_rate`/`initial_capital`/`buy_conditions`/`sell_conditions` 포함하는지 테스트 추가.
- `tests/test_backend.py`: 상세/목록 엔드포인트 각각에 대해 (a) 미청산+size 있음 → 재평가 발생 시나리오, (b) size 없는 레거시 거래 → 재평가 건너뜀, (c) 캔들/ticker 조회 실패 시 폴백 시나리오, (d) 목록 응답에 `buy_conditions`/`sell_conditions`가 포함되는지. 기존 `_patch_get_candles` 패턴 재사용 + 신규 `_patch_get_current_prices` 헬퍼 추가.
- 프론트: `npx tsc --noEmit` + Playwright로 실제 미청산 포지션이 있는 런을 상세/목록 페이지에서 확인(현재 DB에 이미 그런 런이 하나 있음 — `KRW-ERA`, run_id `e9cc29d9...`, 단 `size` 필드가 없어 재실행 필요). 목록 페이지 정렬(4개 컬럼 × 2방향), 기간 포맷, 매수/매도전략 컬럼 표시도 Playwright로 확인.
