# 백테스트 설정 화면 코인 미리보기 차트 설계

## 목적

`PortSetupForm.tsx`(백테스트 설정 화면)에서 코인을 선택해도 우측에 넓은 빈 공간만 있고 실제 시세를
확인할 방법이 없다. 코인 선택 시 그 공간에 캔들차트 + RSI + 이동평균선(SMA 20/60)을 즉시 보여주는
미리보기 패널을 추가한다. 백테스트 실행 전 "이 코인이 최근 어떤 흐름이었는지"를 한눈에 참고하기
위한 용도이며, 전략 조건이나 매수/매도 신호를 이 패널에 얹지는 않는다(전략 실행 전 시점이라
트레이드 자체가 없음).

## 배경 리서치

- **재사용 가능한 것**: `frontend/components/PriceChart.tsx`(백테스트 결과 화면의 캔들차트)가 이미
  `lightweight-charts`(v5.2, 패널/멀티페인 지원)로 캔들+마커 렌더링을 검증된 형태로 구현해뒀다.
  CSS 커스텀 프로퍼티(oklch)를 캔버스로 우회 파싱하는 색상 해석 로직, `ResizeObserver` 리사이즈
  처리, intraday/daily 분기 패턴을 그대로 참고한다.
- **새로 필요한 것**: 이 프로젝트엔 "캔들 데이터만 빠르게 조회"하는 엔드포인트가 없다. 기존
  `/api/v1/backtests/run`·`/api/v1/backtests/validate`는 전략 조건까지 포함한 전체 백테스트
  실행/검증용이라 이 목적엔 무겁다. `upbit_data_service.get_candles(market, timeframe, start, end)`
  (`upbit_data_service.py:177`)가 캔들 조회·캐싱을 이미 담당하므로, 이 함수를 그대로 호출하는
  신규 조회 전용 엔드포인트를 추가한다.
- **RSI/SMA 계산 재사용 가능성 검토**: `engine/indicators/trend.py::create_sma`,
  `engine/indicators/momentum.py::create_rsi`는 둘 다 `bt.indicators.SMA`/`bt.indicators.RSI`
  (backtrader 내장 지표) 위에 얇게 씌운 팩토리라, backtrader `Cerebro`를 통하지 않고는 재사용할 수
  없다(입력이 `bt.feeds.PandasData` 라인). 이 미리보기만을 위해 백테스트 없이 Cerebro를 인스턴스화하는
  건 과함 — 아래 "검토한 접근"에서 독립 pandas 구현을 채택한 이유 참고.

## 아키텍처

### 위치·트리거

`PortSetupForm.tsx`의 카드(`max-w-5xl`) 오른쪽 빈 공간에 새 패널(`CoinPreviewChart`)을 배치한다.
`market`/`timeframe`/`startDate`/`endDate` 중 하나라도 바뀌면 즉시 재조회한다(사용자 확인:
봉데이터는 폼 선택과 연동, 조회 기간은 폼의 운용기간 전체와 동일). 별도 디바운스는 두지 않는다 —
드롭다운 선택 같은 이산적 액션이라 타이핑 디바운스가 필요한 시나리오가 아니다.

### 검토한 계산 방식

- **A. pandas 독립 구현 (채택)** — SMA는 `rolling(window=period).mean()`(backtrader의 단순이동평균과
  공식이 동일해 사실상 완전히 일치), RSI는 Wilder 평활 공식을 pandas로 직접 구현(backtrader
  `bt.indicators.RSI` 기본값과 같은 평활 방식). 백테스트 엔진(Cerebro)을 전혀 거치지 않아 빠르고,
  순수 함수라 단위 테스트하기 쉽다. **주의**: 별도 구현이므로 조건식 빌더가 실제 백테스트에서
  계산하는 RSI/SMA 값과 부동소수점 수준까지 완전히 동일함을 보장하진 않는다(같은 표준 공식이라
  실질적으로는 일치). 이 패널은 참고용 미리보기이지 조건식 계산 근거가 아니므로 이 정도 보증이면
  충분하다고 판단.
- **B. backtrader Cerebro를 통한 재사용** — `create_sma`/`create_rsi`를 그대로 쓰고 `EquityAnalyzer`와
  같은 방식의 커스텀 Analyzer로 라인 값을 기록. 기각 이유: 전략 없이 지표만 뽑으려고 no-op
  Strategy + Cerebro 인스턴스를 매 조회마다 띄우는 건 이 조회 전용 엔드포인트치곤 오버엔지니어링이고,
  응답 속도도 A안보다 느려진다.

### API 계약

```
GET /api/v1/markets/preview?market=KRW-ETH&timeframe=minutes15&start=2026-05-01&end=2026-08-01
```

- 쿼리 파라미터는 `/api/v1/backtests/validate`와 동일한 규칙 재사용: `start`/`end`는 `%Y-%m-%d`,
  UTC, end는 23:59:59로 확장.
- 응답:
  ```json
  {
    "ohlcv": [{"time": "2026-05-01T00:00:00+00:00", "open": 100, "high": 105, "low": 98, "close": 102}, ...],
    "sma20": [{"time": "...", "value": 101.2}, ...],
    "sma60": [{"time": "...", "value": 99.8}, ...],
    "rsi14": [{"time": "...", "value": 55.3}, ...]
  }
  ```
- SMA/RSI는 warm-up 구간(기간 미충족)에 해당하는 선행 포인트를 **응답에서 아예 제외**한다(NaN을
  JSON으로 보내지 않음). `lightweight-charts`는 시리즈마다 데이터 길이가 달라도 되므로, 각 시리즈가
  실제 값이 시작되는 시점부터 짧게 그려지는 것이 정상 동작이다.
- 에러 처리: `get_candles`가 `ValueError`(잘못된 마켓/타임프레임)를 내면 400, `RuntimeError`(업비트
  API 재시도 실패)는 500 — `validate_backtest_endpoint`(`backend/main.py:816`)가 이미 이 두 예외를
  구분해 잡는 것과 동일한 컨벤션. 빈 DataFrame(예외 없이 그냥 데이터 없음)도 400.

### 프론트 컴포넌트 설계

- `frontend/components/CoinPreviewChart.tsx`(신규): `market`/`timeframe`/`start`/`end` props를
  받아 마운트/변경 시 `getMarketPreview(...)` 호출. 로딩 중 스켈레톤, 에러 시 메시지 표시(기존
  `ApiError` 패턴 재사용).
- 차트 렌더링: `lightweight-charts` v5 pane 기능으로 상단 페인(캔들 + SMA20/SMA60 라인 오버레이),
  하단 페인(RSI, 30/70 기준선 포함). `PriceChart.tsx`의 색상 해석 헬퍼(`resolveColor`)와
  `ResizeObserver` 처리를 그대로 복사해 재사용(공통 유틸로 추출하지는 않음 — 두 컴포넌트가 독립적으로
  진화할 수 있게, 이 프로젝트의 기존 서비스 분리 컨벤션과 동일한 이유).
- `PortSetupForm.tsx`: 현재 카드 하나(`max-w-5xl`)로 된 레이아웃을 좌(카드)/우(미리보기) 2컬럼으로
  바꾸고, `CoinPreviewChart`에 현재 상태(`market`, `timeframe`, `startDate`, `endDate`)를 그대로
  전달한다.
- `frontend/lib/api/eda.ts`에 `getMarketPreview(params): Promise<MarketPreview>` 추가,
  `frontend/lib/types/eda.ts`에 `MarketPreview`/`IndicatorSeriesPoint` 타입 추가.

## 상세 설계

### 1. 백엔드 — 신규 함수 (`backend/main.py` 또는 별도 모듈)

```python
def _compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder 평활 RSI. 최초 `period`개는 시드 SMA, 이후 지수 평활."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    return 100 - (100 / (1 + rs))
```

- `_series_to_points(time_col, value_col)`: NaN을 걸러내고 `[{"time": ..., "value": ...}, ...]` 형태로 변환하는 공통 헬퍼.

### 2. 엔드포인트

```python
@app.get("/api/v1/markets/preview")
def get_market_preview(
    market: str = Query(...), timeframe: str = Query(...),
    start: str = Query(...), end: str = Query(...),
) -> dict:
    start_dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(end, "%Y-%m-%d").replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
    try:
        df = get_candles(market, timeframe, start_dt, end_dt)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if df.empty:
        raise HTTPException(status_code=400, detail=f"{market}의 해당 기간 캔들 데이터가 없습니다")

    df["sma20"] = df["close"].rolling(window=20).mean()
    df["sma60"] = df["close"].rolling(window=60).mean()
    df["rsi14"] = _compute_rsi(df["close"], period=14)

    return {
        "ohlcv": [...],
        "sma20": _series_to_points(df, "sma20"),
        "sma60": _series_to_points(df, "sma60"),
        "rsi14": _series_to_points(df, "rsi14"),
    }
```

### 3. 프론트 타입/API 클라이언트

```ts
export interface IndicatorSeriesPoint { time: string; value: number; }
export interface MarketPreview {
  ohlcv: OhlcvPoint[];
  sma20: IndicatorSeriesPoint[];
  sma60: IndicatorSeriesPoint[];
  rsi14: IndicatorSeriesPoint[];
}
```

```ts
export function getMarketPreview(params: {
  market: string; timeframe: string; start: string; end: string;
}): Promise<MarketPreview> {
  const qs = new URLSearchParams(params).toString();
  return apiFetch<MarketPreview>(`/api/v1/markets/preview?${qs}`);
}
```

### 4. 테스트 전략

- **백엔드**: `_compute_rsi`가 알려진 고정 입력(수기 계산 가능한 소규모 시계열)에서 정확한 값을
  내는지, 초반 `period`개는 NaN인지 유닛 테스트. `/api/v1/markets/preview` 통합 테스트: (1) 정상
  응답 스키마, (2) SMA/RSI 응답에 NaN 구간이 제외됐는지, (3) 빈 캔들(마켓/기간 문제) → 400.
- **프론트**: 테스트 프레임워크 없음(기존 컨벤션) — `npx tsc --noEmit` + Playwright/수동 확인으로
  코인 변경 시 패널이 재조회·리렌더링되는지, 봉데이터/운용기간 변경 시에도 동기화되는지 확인.

## 이 스펙에 포함하지 않은 것

- **매수/매도 조건이나 트레이드 마커 오버레이**: 전략 실행 전 시점이라 범위 밖.
- **RSI 외 다른 오실레이터, SMA 외 다른 이동평균 종류**: 이번 라운드는 SMA 20/60 + RSI 14 고정.
  나중에 패널에 지표 선택 UI를 추가하는 건 별도 논의.
- **대용량 구간(예: 3개월치 15분봉 = 수천 개 캔들) 응답 최적화**: 사용자가 "운용기간과 동일하게
  전체 조회"를 선택하면서 이 트레이드오프(초기 조회 느려짐, 페이로드 커짐)를 인지하고 진행하기로
  했다 — 실사용에서 체감 지연이 문제가 되면 그때 재논의(다운샘플링, 캔들 개수 상한 등).
