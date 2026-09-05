# 조건식 threshold 정규화 확장 설계 (가격대/추세/거래대금/MARKET_TREND)

- 작성일: 2026-08-19
- 상태: 승인 대기 (사용자 리뷰 전)
- 전제: [[2026-08-01-grid-search-oscillator-normalization.md]] (오실레이터 9종 정규화, 이미 구현·배포됨)에서 이연했던 [[upbit-v1-catalog-normalization-roadmap]] 후속 작업. 오실레이터 세션에서 확립된 관례(원본 유지 + 독립 항목 추가, threshold 추천 로직 확장)를 그대로 계승한다.

## 목적

지표 카탈로그 중 가격대(FIB/PIVOT/VPVR), 추세(SMA/EMA/WMA), 거래대금(TRADE_VALUE), 시장 심리(MARKET_TREND) — 총 14개 항목은 값이 코인의 절대 시세/스케일에 종속돼 있어, 조건식 threshold를 코인마다 다시 추정해야 하고 grid search 시에도 방해가 된다. 이 14개 각각에 "종가 대비 몇 % 떨어져 있는가"로 정규화한 짝 지표(`_PCT` 접미사)를 독립 항목으로 추가한다. 정규화·%화가 구조적으로 어려운 OBV/VOLUME_SMA는 이번 라운드에 구현하지 않고 사유만 문서화한다.

## 결정된 사항 (사용자 승인)

- 이번 세션에서 14개 전부를 한 번에 설계·구현한다(카테고리별로 세션을 나누지 않음).
- 정규화 공식은 전부 `(종가 - 레벨) / 레벨 × 100`으로 통일한다 — 양수면 종가가 레벨 위, 음수면 아래. TRADE_VALUE_PCT만 "레벨" 자리에 자기 자신의 이동평균(TRADE_VALUE_SMA)을 쓴다: `(거래대금 - 거래대금SMA) / 거래대금SMA × 100`.
- 명명 규칙은 전부 `_PCT` 접미사로 통일한다(SMA/EMA/WMA도 `_DISPARITY`가 아니라 `SMA_PCT`/`EMA_PCT`/`WMA_PCT`).
- 신규 14개의 UI 기본 threshold 추천값은 전부 0(`ZERO_CROSS_INDICATORS`에 편입) — "0 = 종가가 레벨과 정확히 일치".
- TRADE_VALUE_PCT는 내부 SMA 계산용 `period` 파라미터가 필요하며 기본값은 기존 TRADE_VALUE_SMA와 동일하게 20.
- 기존 14개 원본 항목(FIB_382/500/618, PIVOT_P/R1/S1, VPVR_POC/VAH/VAL, SMA/EMA/WMA, TRADE_VALUE, MARKET_TREND)은 삭제·변경 없이 그대로 유지한다.
- OBV/VOLUME_SMA는 이번 라운드에 구현하지 않고, 왜 어려운지와 향후 방향 후보만 스펙에 남긴다.
- grid search를 오실레이터 외 지표로 확장하는 2차 작업은 이번 범위 밖 — 정규화 완료 후 별도 세션에서 다시 브레인스토밍.

## 설계

### 1. 대상 14개 신규 지표

| 카테고리 | 신규 지표 | 원본 | params |
|---|---|---|---|
| 가격대 | `FIB_382_PCT` | FIB_382 | period(20) |
| 가격대 | `FIB_500_PCT` | FIB_500 | period(20) |
| 가격대 | `FIB_618_PCT` | FIB_618 | period(20) |
| 가격대 | `PIVOT_P_PCT` | PIVOT_P | (없음) |
| 가격대 | `PIVOT_R1_PCT` | PIVOT_R1 | (없음) |
| 가격대 | `PIVOT_S1_PCT` | PIVOT_S1 | (없음) |
| 가격대 | `VPVR_POC_PCT` | VPVR_POC | period(50) |
| 가격대 | `VPVR_VAH_PCT` | VPVR_VAH | period(50) |
| 가격대 | `VPVR_VAL_PCT` | VPVR_VAL | period(50) |
| 추세 | `SMA_PCT` | SMA | period(14) |
| 추세 | `EMA_PCT` | EMA | period(14) |
| 추세 | `WMA_PCT` | WMA | period(14) |
| 거래대금 | `TRADE_VALUE_PCT` | TRADE_VALUE | period(20) |
| 시장 심리 | `MARKET_TREND_PCT` | MARKET_TREND | period(10) |

카탈로그 label/description/example 예시(나머지는 동일 패턴으로 작성):
- `FIB_382_PCT`: "피보나치 38.2% (정규화)" / "종가가 피보나치 38.2% 되돌림 레벨 대비 몇 % 떨어져 있는지를 나타냅니다. 코인 시세와 무관하게 항상 같은 범위입니다." / "값이 +5면 종가가 그 레벨보다 5% 위, -3이면 3% 아래에 있다는 뜻입니다."
- `SMA_PCT`: "SMA 이격도 (%)" / "종가가 SMA(period) 대비 몇 % 떨어져 있는지 나타냅니다(이격도)." / "SMA_PCT > 5면 이동평균보다 5% 이상 위로 벌어진(과열 가능성) 구간을 포착합니다."
- `TRADE_VALUE_PCT`: "거래대금 비율 (%)" / "이번 봉 거래대금이 자체 이동평균(TRADE_VALUE_SMA) 대비 몇 % 높거나 낮은지 나타냅니다. 코인마다 다른 거래대금 스케일을 제거합니다." / "TRADE_VALUE_PCT > 100이면 평소 대비 거래대금이 2배 이상으로 튄 구간입니다."
- `MARKET_TREND_PCT`: "시장 추세 (정규화, %)" / "KRW-BTC 종가가 자신의 이동평균 대비 몇 % 위/아래에 있는지 나타냅니다. 절대 KRW 차이값(MARKET_TREND)의 정규화 버전입니다." / "MARKET_TREND_PCT < -2면 BTC가 자기 이동평균보다 2% 이상 아래(약세)인 구간을 필터로 씁니다."

### 2. 백테스트 엔진 구현 (`engine/indicators/*.py`, `engine/condition_tree.py`)

기존 factory가 만드는 객체의 `.data`가 실제 캔들 피드를 정상적으로 가리키는 9개(PIVOT_P/R1/S1, VPVR_POC/VAH/VAL, SMA, EMA, WMA — `PivotPoints`/`VolumeProfile`/`bt.indicators.SMA` 등 진짜 `bt.Indicator` 서브클래스)는 **기존 factory 함수를 그대로 재사용**하고, `condition_tree.py::get_indicator_value()`에 분기만 추가한다(ATR_PCT/BB_PERCENT_B와 동일 패턴):

```python
elif indicator_name in ("SMA_PCT", "EMA_PCT", "WMA_PCT"):
    close, ma = float(obj.data.close[0]), float(obj[0])
    return (close - ma) / ma * 100 if ma else 0.0
elif indicator_name == "PIVOT_P_PCT":
    close, level = float(obj.data.close[0]), float(obj.lines.p[0])
    return (close - level) / level * 100 if level else 0.0
elif indicator_name == "PIVOT_R1_PCT":
    close, level = float(obj.data.close[0]), float(obj.lines.r1[0])
    return (close - level) / level * 100 if level else 0.0
elif indicator_name == "PIVOT_S1_PCT":
    close, level = float(obj.data.close[0]), float(obj.lines.s1[0])
    return (close - level) / level * 100 if level else 0.0
elif indicator_name == "VPVR_POC_PCT":
    close, level = float(obj.data.close[0]), float(obj.lines.poc[0])
    return (close - level) / level * 100 if level else 0.0
elif indicator_name == "VPVR_VAH_PCT":
    close, level = float(obj.data.close[0]), float(obj.lines.vah[0])
    return (close - level) / level * 100 if level else 0.0
elif indicator_name == "VPVR_VAL_PCT":
    close, level = float(obj.data.close[0]), float(obj.lines.val[0])
    return (close - level) / level * 100 if level else 0.0
```

`engine/indicators/__init__.py`의 `INDICATOR_FACTORY`에는 기존 함수를 그대로 별칭 등록한다:
```python
"SMA_PCT": create_sma, "EMA_PCT": create_ema, "WMA_PCT": create_wma,
"PIVOT_P_PCT": create_pivot_p, "PIVOT_R1_PCT": create_pivot_r1, "PIVOT_S1_PCT": create_pivot_s1,
"VPVR_POC_PCT": create_vpvr_poc, "VPVR_VAH_PCT": create_vpvr_vah, "VPVR_VAL_PCT": create_vpvr_val,
```

나머지 5개(`FIB_382_PCT`/`FIB_500_PCT`/`FIB_618_PCT`, `TRADE_VALUE_PCT`, `MARKET_TREND_PCT`)는 원본 factory가 산술 조합(`hh - (hh-ll)*ratio` 등)을 그대로 반환해 `.data`가 온전한 캔들 피드를 가리키지 않는다 — 재사용 대신 **전용 소형 Indicator 클래스 3개**를 새로 만든다(기존 FIB/TRADE_VALUE/MARKET_TREND 코드는 변경 없음):

```python
# engine/indicators/price_levels.py
class FibPct(bt.Indicator):
    lines = ("pct",)
    params = (("period", 20), ("ratio", 0.382))

    def __init__(self) -> None:
        hh = bt.indicators.Highest(self.data.high, period=self.p.period)
        ll = bt.indicators.Lowest(self.data.low, period=self.p.period)
        self.level = hh - (hh - ll) * self.p.ratio

    def next(self) -> None:
        level = self.level[0]
        self.lines.pct[0] = (self.data.close[0] - level) / level * 100 if level else 0.0


def create_fib_382_pct(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    return FibPct(data, period=int(params.get("period", 20)), ratio=0.382)

def create_fib_500_pct(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    return FibPct(data, period=int(params.get("period", 20)), ratio=0.5)

def create_fib_618_pct(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    return FibPct(data, period=int(params.get("period", 20)), ratio=0.618)
```

```python
# engine/indicators/volume.py
class TradeValueRatio(bt.Indicator):
    lines = ("pct",)
    params = (("period", 20),)

    def __init__(self) -> None:
        self.sma = bt.indicators.SMA(self.data.trade_value, period=self.p.period)

    def next(self) -> None:
        sma_val = self.sma[0]
        self.lines.pct[0] = (self.data.trade_value[0] - sma_val) / sma_val * 100 if sma_val else 0.0


def create_trade_value_pct(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    return TradeValueRatio(data, period=int(params.get("period", 20)))
```

```python
# engine/indicators/market.py
class MarketTrendPct(bt.Indicator):
    lines = ("pct",)
    params = (("period", 10),)

    def __init__(self) -> None:
        self.market_close = self.data.btc_close
        self.sma = bt.indicators.SMA(self.market_close, period=self.p.period)

    def next(self) -> None:
        sma_val = self.sma[0]
        self.lines.pct[0] = (self.market_close[0] - sma_val) / sma_val * 100 if sma_val else 0.0


def create_market_trend_pct(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    return MarketTrendPct(data, period=int(params.get("period", 10)))
```

이 5개는 단일 라인(`pct`)만 선언하므로 `get_indicator_value()`의 기본 분기(`else: return float(obj[0])`)로 충분하다 — OBV/VPIN과 동일한 패턴이라 추가 분기가 필요 없다.

### 3. 라이브 트레이딩 pandas 구현 (`trading/live_indicators.py`)

14개 전부 기존 원본 계산 함수를 재사용해 한 줄 공식만 추가한다(`create_atr_pct`가 이미 이 패턴):

```python
def create_fib_382_pct(df: pd.DataFrame, **params) -> pd.Series:
    level = create_fib_382(df, **params)
    return (df["close"] - level) / level * 100

def create_fib_500_pct(df: pd.DataFrame, **params) -> pd.Series:
    level = create_fib_500(df, **params)
    return (df["close"] - level) / level * 100

def create_fib_618_pct(df: pd.DataFrame, **params) -> pd.Series:
    level = create_fib_618(df, **params)
    return (df["close"] - level) / level * 100

def create_pivot_p_pct(df: pd.DataFrame, **params) -> pd.Series:
    level = create_pivot_p(df, **params)
    return (df["close"] - level) / level * 100

def create_pivot_r1_pct(df: pd.DataFrame, **params) -> pd.Series:
    level = create_pivot_r1(df, **params)
    return (df["close"] - level) / level * 100

def create_pivot_s1_pct(df: pd.DataFrame, **params) -> pd.Series:
    level = create_pivot_s1(df, **params)
    return (df["close"] - level) / level * 100

def create_vpvr_poc_pct(df: pd.DataFrame, **params) -> pd.Series:
    level = create_vpvr_poc(df, **params)
    return (df["close"] - level) / level * 100

def create_vpvr_vah_pct(df: pd.DataFrame, **params) -> pd.Series:
    level = create_vpvr_vah(df, **params)
    return (df["close"] - level) / level * 100

def create_vpvr_val_pct(df: pd.DataFrame, **params) -> pd.Series:
    level = create_vpvr_val(df, **params)
    return (df["close"] - level) / level * 100

def create_sma_pct(df: pd.DataFrame, **params) -> pd.Series:
    ma = create_sma(df, **params)
    return (df["close"] - ma) / ma * 100

def create_ema_pct(df: pd.DataFrame, **params) -> pd.Series:
    ma = create_ema(df, **params)
    return (df["close"] - ma) / ma * 100

def create_wma_pct(df: pd.DataFrame, **params) -> pd.Series:
    ma = create_wma(df, **params)
    return (df["close"] - ma) / ma * 100

def create_trade_value_pct(df: pd.DataFrame, **params) -> pd.Series:
    sma = create_trade_value_sma(df, **params)
    return (df["trade_value"] - sma) / sma * 100

def create_market_trend_pct(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 10))
    btc_close = df["btc_close"]
    sma = btc_close.rolling(period).mean()
    return (btc_close - sma) / sma * 100
```

`LIVE_INDICATOR_FACTORY`에 14개 등록. pandas 나눗셈은 0으로 나눠도 예외 없이 `inf`/`NaN`을 반환하므로(백테스트 쪽처럼 명시적 가드가 필요 없음) 기존 ATR_PCT/BB_PERCENT_B 라이브 구현과 동일한 스타일을 따른다.

### 4. 부수 변경

- `engine/condition_tree.py::AUX_MARKET_INDICATORS`에 `"MARKET_TREND_PCT": "KRW-BTC"` 추가. 빠뜨리면 MARKET_TREND_PCT만 쓰는 조건에서 BTC 캔들이 병합되지 않아 `eval_group_values()`가 조용히 unknown 처리한다(운영 중 원인 파악이 어려운 버그이므로 설계 단계에서 미리 반영).
- `backend/main.py::INDICATOR_CATALOG`에 14개 항목 추가. 카테고리는 원본과 동일하게 배치(가격대 9 / 추세 3 / 거래대금 1 / 시장 심리 1).
- `frontend/components/StrategyConditionBuilder.tsx::ZERO_CROSS_INDICATORS`에 14개 추가(threshold 기본값 0). `PRICE_SCALE_INDICATORS`에는 추가하지 않음(더 이상 코인 시세에 종속되지 않으므로).
- `frontend/lib/indicator-guide.ts`: 14개 신규 최상위 가이드 항목(meaning/params/formula/usage) 추가 + 원본 14개 항목의 `usage` 끝에 교차참조 문장 추가(예: "코인 시세와 무관한 정규화 버전이 필요하면 SMA_PCT를 대신 쓰세요") — 오실레이터 세션에서 MACD_PPO 등이 실제로 받은 처우(독립 항목 + 교차참조 둘 다)와 동일한 패턴.

### 5. OBV / VOLUME_SMA — 구현 없이 리스트업만

- **OBV**: 누적합(cumulative sum)이라 절대값 자체가 코인마다 다르고 시간이 지날수록 계속 커진다. "레벨 대비 % 거리" 정규화가 구조적으로 성립하지 않는다 — %화하려면 절대값이 아니라 변화율/기울기(rate of change)로 재정의해야 하는데, 이는 OBV를 대체하는 새 지표(예: `OBV_ROC` — 최근 N봉 대비 OBV 변화율) 설계가 되어 이번 정규화 범위를 벗어난다. 향후 별도 브레인스토밍 대상으로만 남긴다.
- **VOLUME_SMA**: TRADE_VALUE_PCT처럼 "현재값 대비 자체 이동평균 비율"로 비율화하려면 "현재 거래량" 원시값이 카탈로그에 독립 지표로 있어야 하는데, 현재는 캔들 데이터에 거래량 컬럼은 있어도 조건식에서 쓸 수 있는 원시 `VOLUME` 카탈로그 항목이 없다. 새 원시 지표(`VOLUME`)를 먼저 추가해야 하는 선행 작업이 있어 이번 범위 밖으로 문서화만 한다. 향후 `VOLUME`을 추가하면 `TRADE_VALUE_PCT`와 동일한 패턴으로 `VOLUME_PCT`를 만들 수 있다.

## 검증 절차 (구현 완료 후)

1. 엔진 단위 테스트(`tests/test_indicators.py`): 14개 각각 합성 데이터로 부호(종가가 레벨 위/아래일 때 +/-)와 0-나눗셈 가드(레벨이 0인 극단 케이스) 확인.
2. 라이브 pandas 단위 테스트(`tests/test_live_indicators_*.py`): 14개 각각 백테스트 엔진과 값이 일치하는지 골든테스트 확장.
3. `engine/condition_tree.py`: `get_indicator_value()` 신규 9개 분기 + `AUX_MARKET_INDICATORS`에 `MARKET_TREND_PCT` 반영 테스트(`tests/test_condition_tree.py`).
4. 프론트 조건식 빌더에서 신규 14개 지표 선택 시 threshold 기본값이 0으로 채워지는지, 카테고리 배치가 올바른지 확인.
5. 지표 가이드 페이지에서 신규 14개 항목과, 원본 14개 항목의 교차참조 문장이 보이는지 확인.

## 범위 밖

- grid search를 오실레이터 외 지표(이번에 추가하는 14개 포함)로 확장하는 작업 — 정규화 완료 후 별도 세션에서 다시 브레인스토밍(1차 오실레이터 grid search 방식을 그대로 확장할지, 별도 2차 grid search 기능으로 만들지 미정).
- OBV/VOLUME_SMA 실제 정규화 구현.
- 기존 14개 원본 카탈로그 항목 삭제/변경.

## Self-Review 결과

- **스펙 커버리지**: 브레인스토밍에서 확정한 모든 결정(14개 한 번에, 공식 통일, `_PCT` 명명 통일, threshold 기본값 0, TRADE_VALUE_PCT period=20, OBV/VOLUME_SMA 리스트업만)이 반영됨.
- **내부 정합성**: "기존 factory 재사용 가능한 9개 vs 전용 클래스 필요한 5개" 구분 기준(`.data`가 온전한 캔들 피드를 가리키는지 여부)이 실제 원본 구현(PivotPoints/VolumeProfile/bt.indicators.SMA는 진짜 Indicator 서브클래스, FIB/TRADE_VALUE/MARKET_TREND는 산술 조합 반환)과 정확히 일치하는지 코드를 직접 읽어 확인함.
- **쌍둥이 함수 누락 방지**: `engine/indicators/*.py`(백테스트)뿐 아니라 `trading/live_indicators.py`(라이브)도 이 프로젝트의 명시적 관례("의도적으로 중복된 쌍둥이 함수... 하나만 고치면 조용히 어긋난다")에 따라 반드시 같이 구현해야 함을 설계 단계에서 미리 반영 — 오실레이터 세션 스펙에는 이 부분이 없었는데, 이번 스펙에서 새로 짚어낸 리스크.
- **AUX_MARKET_INDICATORS 누락 방지**: MARKET_TREND_PCT가 BTC 보조 마켓 데이터를 필요로 한다는 사실을 코드 확인 없이 지나쳤다면 놓쳤을 버그 — 설계 단계에서 미리 발견해 부수 변경 항목에 포함시킴.
- **대상 파일 목록**: `engine/indicators/price_levels.py`, `engine/indicators/trend.py`(변경 없음, 재사용만), `engine/indicators/volume.py`, `engine/indicators/market.py`, `engine/indicators/__init__.py`, `engine/condition_tree.py`, `trading/live_indicators.py`, `backend/main.py`, `frontend/components/StrategyConditionBuilder.tsx`, `frontend/lib/indicator-guide.ts`, `tests/test_indicators.py`, `tests/test_live_indicators_*.py`, `tests/test_condition_tree.py`.
