# 거래량/OBV 정규화 지표 설계 (VOLUME, VOLUME_PCT, OBV_ROC)

- 작성일: 2026-08-19
- 상태: 승인 대기 (사용자 리뷰 전)
- 전제: [[2026-08-19-condition-threshold-normalization-design.md]](14개 가격대/추세/거래대금/시장심리 정규화 지표, 이미 구현·배포됨)의 §5에서 "정규화가 구조적으로 어려워 이번 라운드에 구현하지 않는다"고 리스트업만 해둔 OBV/VOLUME_SMA의 후속 작업.

## 목적

`OBV`와 `VOLUME_SMA`는 코인의 절대 거래량 스케일에 종속돼 있어 조건식 threshold를 코인마다 다시 추정해야 한다. 특히 `OBV`는 누적합(cumulative sum)이라 절대값이 시간이 지날수록 계속 커져, 기존 14개가 쓴 "레벨 대비 % 거리" 공식(`(종가-레벨)/레벨×100`)이 그대로 적용되지 않는다. 이번 작업은 두 가지를 한다:

1. 조건식에서 직접 쓸 수 있는 원시 `VOLUME` 지표를 신설하고, 그 자체 이동평균 대비 비율인 `VOLUME_PCT`를 추가한다 (`TRADE_VALUE`/`TRADE_VALUE_PCT`와 동일 패턴 — 새 설계 없음).
2. `OBV`의 누적 성질에 맞는 새로운 정규화 공식(`OBV_ROC`)을 설계해 추가한다 — 나이브한 등락률(`(OBV[0]-OBV[-N])/OBV[-N]×100`)은 OBV가 0 근처이거나 음수로 넘어가는 구간에서 분모가 불안정해 쓸 수 없다.

## 결정된 사항 (사용자 승인)

- `OBV_ROC = (OBV[0] - OBV[N봉전]) / (최근 N봉 거래량 합) × 100`. OBV의 N봉 변화량은 수학적으로 그 구간의 "부호 있는 거래량 합"과 정확히 같다는 성질을 이용해, 같은 구간의 "총 거래량 합"으로 나눈다. 값은 항상 -100~+100 범위로 자연스럽게 유계되고, 분모가 0이 되는 경우는 그 구간에 거래가 아예 없을 때뿐이다.
- 지표명은 `OBV_ROC` — 기존 14개의 `_PCT`(레벨 대비 이격도) 접미사와 계산 성격이 다르다는 것을 이름에서도 드러낸다.
- `OBV_ROC`의 `period` 기본값은 14 (RSI 등 지표 관례를 따름).
- `VOLUME`(원시 거래량)과 `VOLUME_PCT`(자체 SMA 대비 비율)를 함께 추가한다. `VOLUME_PCT`는 `TRADE_VALUE_PCT`와 완전히 동일한 공식이며 `period` 기본값은 20 (기존 `VOLUME_SMA`와 동일).
- 기존 `OBV`, `VOLUME_SMA` 카탈로그 항목은 삭제·변경 없이 그대로 유지한다. `usage` 필드 끝에만 신규 정규화 버전으로의 교차참조 문장을 추가한다.
- 세 지표 모두 카탈로그 카테고리는 기존 `OBV`/`VOLUME_SMA`와 동일한 "거래량"에 배치한다.

## 설계

### 1. 대상 3개 신규 지표

| 지표 | 원본 | params | 비고 |
|---|---|---|---|
| `VOLUME` | (신규, 원시값) | 없음 | `TRADE_VALUE`와 동일 위상 — 원시 거래량을 조건식에서 직접 쓸 수 있게 노출 |
| `VOLUME_PCT` | VOLUME_SMA | period(20) | `TRADE_VALUE_PCT`와 동일 공식 |
| `OBV_ROC` | OBV | period(14) | 누적합 특성에 맞는 전용 공식(순매수/총거래량 비율) |

카탈로그 label/description/example:
- `VOLUME`: "거래량" / "봉의 원시 거래량(코인 수량)입니다. 거래대금(TRADE_VALUE)과 달리 가격이 반영되지 않은 순수 수량 기준입니다." / "거래량이 특정 절대 수치 이상인지 확인하는 조건 등에 씁니다. 코인마다 스케일이 크게 달라 여러 코인에 같은 threshold를 재사용하기 어렵습니다."
- `VOLUME_PCT`: "거래량 비율 (%)" / "이번 봉 거래량이 자체 이동평균(VOLUME_SMA) 대비 몇 % 높거나 낮은지 나타냅니다. 코인마다 다른 거래량 스케일을 제거합니다." / "VOLUME_PCT > 100이면 평소 대비 거래량이 2배 이상으로 튄 구간입니다."
- `OBV_ROC`: "OBV 변화율 (정규화, %)" / "최근 N봉 동안의 순매수 거래량(OBV 변화량)이 같은 구간 총 거래량의 몇 %였는지 나타냅니다. 항상 -100~100 범위입니다." / "OBV_ROC > 30이면 최근 N봉 동안 매수세가 거래량의 30% 이상을 차지한 강한 매수 압력 구간입니다."

### 2. 백테스트 엔진 구현 (`engine/indicators/volume.py`)

`VOLUME`/`VOLUME_PCT`는 이미 있는 `create_trade_value`/`TradeValueRatio` 패턴을 거래량에 그대로 적용한다:

```python
def create_volume(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    return data.volume


class VolumeRatio(bt.Indicator):
    """이번 봉 거래량이 자체 이동평균(period봉) 대비 몇 % 높거나 낮은지를 나타낸
    정규화 버전. TradeValueRatio와 동일한 패턴을 거래량(수량)에 적용한다."""

    lines = ("pct",)
    params = (("period", 20),)

    def __init__(self) -> None:
        self.sma = bt.indicators.SMA(self.data.volume, period=self.p.period)

    def next(self) -> None:
        sma_val = self.sma[0]
        self.lines.pct[0] = (self.data.volume[0] - sma_val) / sma_val * 100 if sma_val else 0.0


def create_volume_pct(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 20))
    return VolumeRatio(data, period=period)
```

`OBV_ROC`는 새 클래스가 필요하다. `self.obv[-self.p.period]`처럼 next() 안에서 과거 시점을 수동으로 참조하는 패턴은 `RollingCorrelation`(engine/indicators/market.py)이 이미 쓰고 있으며, 그 경우처럼 `addminperiod()`로 backtrader에 추가 lookback 버퍼를 명시적으로 알려줘야 한다:

```python
class OBVRoc(bt.Indicator):
    """OBV의 N봉 변화량(순매수 거래량)을 같은 구간 총 거래량으로 나눈 정규화 버전.
    OBV 자체는 누적합이라 절대값이 무한히 커지고 코인마다 스케일이 달라 threshold를
    코인 간 공유할 수 없다 — 이 버전은 그 구간 순매수세가 총 거래량의 몇 %였는지를
    나타내 항상 -100~+100 범위로 유계된다."""

    lines = ("pct",)
    params = (("period", 14),)

    def __init__(self) -> None:
        self.obv = OBV(self.data)
        self.volume_sum = bt.indicators.SumN(self.data.volume, period=self.p.period)
        self.addminperiod(self.p.period + 2)  # OBV 자체 minperiod(2) + N봉 lookback

    def next(self) -> None:
        net_change = self.obv[0] - self.obv[-self.p.period]
        total_volume = self.volume_sum[0]
        self.lines.pct[0] = net_change / total_volume * 100 if total_volume else 0.0


def create_obv_roc(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 14))
    return OBVRoc(data, period=period)
```

`addminperiod`의 정확한 값(`period + 2`)은 설계 단계의 최선 추정이며, 구현 단계에서 합성 데이터 기반 테스트로 실제 유효한 첫 시점을 확인해 필요하면 조정한다(RollingCorrelation도 같은 방식으로 값을 확정했다).

세 지표 모두 `data.volume`/`data.close`만 사용하는 표준 OHLCV 필드라서, `TRADE_VALUE`나 `MARKET_TREND_PCT`처럼 별도로 병합해야 하는 보조 라인(`trade_value`, `btc_close`)이 필요 없다 — `AUX_MARKET_INDICATORS` 등록이나 스모크 테스트 제외 목록(`_NEEDS_EXTRA_LINE`/`_NEEDS_TRADE_VALUE_LINE`) 수정이 필요 없다. `condition_tree.py::get_indicator_value()`도 셋 다 단일 `pct`(또는 기본) 라인 지표라 기존 기본 분기(`else: return float(obj[0])`)로 충분하다 — 새 분기 추가가 필요 없다.

`engine/indicators/__init__.py`의 `INDICATOR_FACTORY`에 3개 등록:
```python
"VOLUME": create_volume,
"VOLUME_PCT": create_volume_pct,
"OBV_ROC": create_obv_roc,
```

### 3. 라이브 트레이딩 pandas 구현 (`trading/live_indicators.py`)

AWS에 상시 실행 중인 라이브 트레이딩 데몬은 backtrader를 쓰지 않고 매 봉마다 pandas로 지표를 직접 계산해 매수/매도 조건을 평가한다. 이 프로젝트는 백테스트/라이브 계산 로직을 의도적으로 중복된 쌍둥이 함수로 유지하므로, 이번 3개도 반드시 양쪽 다 구현하고 골든테스트(`assert_matches_backtrader`)로 두 계산이 일치하는지 검증한다:

```python
def create_volume(df: pd.DataFrame, **params) -> pd.Series:
    return df["volume"]


def create_volume_pct(df: pd.DataFrame, **params) -> pd.Series:
    sma = create_volume_sma(df, **params)
    return (df["volume"] - sma) / sma * 100


def create_obv_roc(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 14))
    obv = create_obv(df, **params)
    volume_sum = df["volume"].rolling(period).sum()
    net_change = obv - obv.shift(period)
    return net_change / volume_sum * 100
```

`create_obv_roc`의 `obv.shift(period)`가 백테스트 쪽 `self.obv[-self.p.period]`와, `volume_sum`(rolling sum)이 `bt.indicators.SumN`과 정확히 같은 구간(현재 봉 포함 최근 N봉)을 가리키는지는 골든테스트로 직접 확인한다. 세 지표 모두 pandas 나눗셈은 0으로 나눠도 예외 없이 `inf`/`NaN`을 반환하므로(기존 `ATR_PCT`/`BB_PERCENT_B` 라이브 구현과 동일 스타일), 라이브 쪽엔 백테스트처럼 명시적 0-가드가 필요 없다.

`LIVE_INDICATOR_FACTORY`에 3개 등록. `tests/test_signal_engine_warmup.py`의 `_A_GROUP_PARAMS`에도 3개 추가한다(`btc_close` 같은 보조 마켓 라인이 필요 없으므로 `_B_GROUP`이 아니라 A그룹) — `"VOLUME": {}`, `"VOLUME_PCT": {"period": 20}`, `"OBV_ROC": {"period": 14}`.

### 4. 부수 변경

- `backend/main.py::INDICATOR_CATALOG`에 3개 항목 추가. 카테고리는 기존 `OBV`/`VOLUME_SMA`와 동일하게 "거래량".
- `frontend/components/StrategyConditionBuilder.tsx::ZERO_CROSS_INDICATORS`에 `VOLUME_PCT`, `OBV_ROC` 추가(threshold 기본값 0 = 평균과 같음 / 순매수·순매도 균형). `VOLUME`은 추가하지 않는다 — 기존 `OBV`/`VOLUME_SMA`와 마찬가지로 코인마다 스케일이 제각각이라 스마트 기본값을 못 주고, 기존 fallback(0 placeholder)만 받는다.
- `frontend/lib/indicator-guide.ts`: `VOLUME`/`VOLUME_PCT`/`OBV_ROC` 3개 신규 최상위 가이드 항목(meaning/params/formula/thresholdExample/usage) 추가. 기존 `OBV`/`VOLUME_SMA` 항목의 `usage` 끝에 교차참조 문장 추가:
  - `OBV`: "...코인 시세와 무관한 정규화 버전이 필요하면 OBV_ROC를 대신 쓰세요."
  - `VOLUME_SMA`: "...코인 시세와 무관한 정규화 버전이 필요하면 VOLUME_PCT를 대신 쓰세요."

## 검증 절차 (구현 완료 후)

1. 엔진 단위 테스트(`tests/test_indicators.py`): 3개 각각 합성 데이터로 수동 계산값과 일치 검증. `OBV_ROC`는 0-나눗셈 가드(거래량 합이 0인 극단 케이스) 및 순매수/순매도 방향에 따른 부호 검증도 포함.
2. 라이브 pandas 단위 테스트(`tests/test_live_indicators_volume.py`, 신규 파일 또는 기존 거래량 테스트 파일에 추가): `assert_matches_backtrader`로 백테스트 엔진과 값이 일치하는지 골든테스트.
3. `tests/test_signal_engine_warmup.py`: `_A_GROUP_PARAMS`에 3개 반영, 회귀 테스트 통과 확인.
4. `tests/test_backend.py::test_get_indicator_catalog_covers_all_registered_indicators`: 카탈로그 등록 확인.
5. 프론트 조건식 빌더에서 3개 지표 선택 시 threshold 기본값(`VOLUME_PCT`/`OBV_ROC`는 0, `VOLUME`은 placeholder)이 올바른지, 카테고리 배치("거래량")가 올바른지 확인.
6. 지표 가이드 페이지에서 신규 3개 항목과, `OBV`/`VOLUME_SMA` 항목의 교차참조 문장이 보이는지 확인.
7. `INDICATOR_FACTORY`/`LIVE_INDICATOR_FACTORY` 양쪽에 3개 키가 정확히 일치하는지 확인 + 전체 테스트 스위트 PASS.

## 범위 밖

- grid search를 이번 3개 포함 오실레이터 외 지표로 확장하는 작업 — 별도 세션.
- 기존 `OBV`/`VOLUME_SMA` 카탈로그 항목 삭제/변경.
- `OBV_ROC`의 `addminperiod` 최종 값 확정 — 설계 단계의 추정치이며 구현 단계 TDD에서 검증·조정.

## Self-Review 결과

- **스펙 커버리지**: 브레인스토밍에서 확정한 모든 결정(OBV_ROC 공식, 이름, period 기본값, VOLUME+VOLUME_PCT 신설, 카테고리 배치)이 반영됨.
- **내부 정합성**: `VOLUME`/`VOLUME_PCT`가 `TRADE_VALUE`/`TRADE_VALUE_PCT`와 동일 패턴임을 코드 직접 인용으로 확인. `OBV_ROC`가 `RollingCorrelation`의 `addminperiod` 관례를 따른다는 점도 실제 코드 위치(engine/indicators/market.py)를 확인해 반영.
- **쌍둥이 함수 누락 방지**: 백테스트(`engine/indicators/volume.py`)와 라이브(`trading/live_indicators.py`) 양쪽 구현을 설계 단계에서부터 명시 — 지난 14개 라운드에서 확립한 관례를 그대로 계승.
- **부수 변경 최소화 확인**: 세 지표 모두 표준 OHLCV 필드(`volume`/`close`)만 사용해 `AUX_MARKET_INDICATORS`/스모크 테스트 제외 목록 수정이 필요 없음을 코드 확인 후 명시 — 지난 라운드의 `MARKET_TREND_PCT`보다 부수 변경이 적음.
- **대상 파일 목록**: `engine/indicators/volume.py`, `engine/indicators/__init__.py`, `trading/live_indicators.py`, `backend/main.py`, `frontend/components/StrategyConditionBuilder.tsx`, `frontend/lib/indicator-guide.ts`, `tests/test_indicators.py`, `tests/test_live_indicators_volume.py`(신규 또는 기존 파일), `tests/test_signal_engine_warmup.py`.
