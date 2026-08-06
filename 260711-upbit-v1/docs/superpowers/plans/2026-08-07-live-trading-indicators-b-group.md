# 라이브 트레이딩 서브플랜③ — 지표 엔진 B그룹 + 장애정책 (trading/live_indicators.py) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **워크트리를 만들지 말고 main 브랜치에서 직접 작업한다** (사용자 지시, [[upbit-v1-worktree-workflow-changed]]).

**Goal:** `trading/live_indicators.py`에 B그룹 지표 6개(`MARKET_TREND`/`BTC_CORRELATION`/
`USDT_CORRELATION`/`FEAR_GREED_CMC`/`KOREA_PREMIUM`/`FUNDING_RATE`, 스펙 결정 2)를 추가해
`LIVE_INDICATOR_FACTORY`를 A그룹 33개와 합쳐 `INDICATOR_FACTORY`와 동일한 39개로 완성하고,
외부데이터 3종(공포탐욕지수/바이낸스 펀딩비/바이낸스 현물종가)의 실시간 조회가 지연·실패했을
때 오래된 값을 forward-fill하지 않고 `None`을 반환하는 장애정책(스펙 결정 8)을 구현한다.

**Architecture:** `docs/superpowers/specs/2026-08-04-live-trading-foundation-design.md`의
1단계 로드맵 중 서브플랜②(`docs/superpowers/plans/2026-08-07-live-trading-indicators-a-group.md`,
main에 커밋됨, A그룹 33개 완료) 다음 순서인 서브플랜③이다. B그룹 6개는 필요한 데이터
소스에 따라 두 갈래로 나뉜다:
- **보조마켓형(`MARKET_TREND`/`BTC_CORRELATION`/`USDT_CORRELATION`)**: A그룹과 똑같이
  **순수 계산 함수**다 — `df["btc_close"]`/`df["usdt_close"]` 컬럼이 이미 채워진
  `pd.DataFrame`을 받아 계산만 한다(I/O 없음). 이 컬럼을 실시간으로 채우는 일(Upbit
  WS로 보조 마켓 캔들 구독)은 서브플랜④(Upbit 연동)의 몫이며, 이 플랜은 손대지 않는다.
  캐들 공급이 끊겨 컬럼이 NaN이면 계산 결과도 자연히 NaN이 되어 서브플랜①의
  `eval_group_values()`가 이미 NaN을 unknown으로 처리하므로, 별도 장애정책 코드가
  필요 없다.
- **외부API형(`FEAR_GREED_CMC`/`KOREA_PREMIUM`/`FUNDING_RATE`)**: `engine/indicators/sentiment.py`와
  마찬가지로 `create_*` 함수 자체는 이미 채워진 컬럼(`fear_greed_value`/
  `korea_premium_value`/`funding_rate_value`)을 그대로 반환하는 패스스루다. 하지만 그
  컬럼을 채우는 실제 데이터(공포탐욕지수, 바이낸스 펀딩비, 바이낸스 현물종가)는 원격
  API 호출이 필요하고, 이게 지연·실패했을 때 오래된 값을 forward-fill하면 안 된다는 게
  스펙 결정 8이다. 그래서 이 플랜에서 `trading/live_indicators.py`에 **A그룹에는 없던
  실제 I/O 함수 3개**(`fetch_live_fear_greed_value`/`fetch_live_funding_rate_value`/
  `fetch_live_binance_close`)를 추가한다 — 각각 기존 `external_data_service.py`/
  `binance_data_service.py`(둘 다 `engine/` 아래가 아닌 저장소 루트의 일반 모듈이라
  backtrader와 무관, 스펙 결정 1의 제약 대상이 아님)의 조회 함수를 재사용하되, 값이
  없거나 각 소스별 최신성 기준(아래 각 태스크에서 근거와 함께 확정)을 벗어나면 `None`을
  반환한다. `KOREA_PREMIUM`은 `close`/`binance_close`/`usdt_close` 세 컬럼의 조합이므로,
  이 공식을 계산하는 `compute_korea_premium_value(df)` 헬퍼도 추가한다(백테스트의
  `backend/main.py:789` 공식과 동일) — 셋 중 하나라도 NaN이면 결과도 자연히 NaN이 되어
  역시 별도 방어코드가 필요 없다.

이 두 갈래 모두 **A그룹과 동일하게 "실제 캔들/실시간 데이터를 daemon이 어떻게 채우는가"는
다루지 않는다** — 그건 서브플랜④(Upbit 연동)·⑤(트레이딩 엔진 코어, `signal_engine.py`)의
몫이다. 이 플랜은 (1) 컬럼이 채워져 있다고 가정한 계산 함수들의 골든테스트, (2) 외부API
원시값 조회 함수 3개의 장애정책(스테일 판정)까지만 다룬다.

**Tech Stack:** Python, `pandas`, `numpy`, `httpx`(이미 `external_data_service.py`/
`binance_data_service.py`가 사용 중), `pytest`, `pytest-monkeypatch`. 새 의존성 없음.

## Global Constraints

- `trading/` 패키지는 `engine/condition_tree.py` 외에는 `engine/`의 backtrader 관련
  코드를 import하지 않는다(스펙 결정 1) — **단, 테스트 코드**는 골든테스트를 위해
  `engine/indicators`·`engine/runner`·`backtrader`를 import해도 된다. `external_data_service.py`/
  `binance_data_service.py`는 `engine/` 아래가 아닌 저장소 루트의 평범한 모듈(REST+캐시,
  backtrader 미의존)이므로 이 제약의 대상이 **아니다** — `trading/live_indicators.py`
  프로덕션 코드가 그대로 import해도 된다.
- 이 서브플랜은 **B그룹(6개)만** 다룬다. A그룹 33개는 이미 완료됨(건드리지 않음).
- 골든테스트는 `tests/signal_fixtures.py`의 `make_oscillating_df()`를 그대로 재사용한다
  (서브플랜②와 동일 관례). 보조마켓/외부데이터 컬럼이 필요한 지표는 `tests/test_indicators.py`의
  `_run_probe_with_aux()` 패턴(추가 라인을 가진 `bt.feeds.PandasData` 서브클래스)을
  `tests/live_indicator_fixtures.py`에 동일하게 이식한다.
- 각 함수는 `engine/indicators/*.py`의 동명 함수와 같은 파라미터 이름·기본값을 쓴다
  (`MARKET_TREND`는 `period=10`, `BTC_CORRELATION`/`USDT_CORRELATION`은 `period=20`,
  `FEAR_GREED_CMC`/`KOREA_PREMIUM`/`FUNDING_RATE`는 파라미터 없음) — 서브플랜②와 같은
  이유(`indicator_key(name, params)`가 백테스트/라이브 양쪽에서 같은 키로 매핑돼야 함).
- `trading/live_indicators.py`는 **하나의 파일로 유지**한다(스펙 모듈구조 절, 서브플랜②와
  동일 관례).
- 오래된 값 forward-fill 금지(스펙 결정 8)는 **외부API형 3개의 원시값 조회 함수**에서만
  명시적으로 구현한다. 보조마켓형 3개는 컬럼의 자연스러운 NaN 전파로 이미 같은 효과를
  낸다(위 Architecture 절 참고) — 별도 스테일 판정 코드를 추가하지 않는다.
- 커밋은 태스크 단위로 작게, 테스트가 통과한 뒤에만 한다.

---

## File Structure

- **Modify:** `trading/live_indicators.py` — B그룹 6개 `create_*` 함수 + `compute_korea_premium_value()`
  + `fetch_live_*()` 3개 + import 추가(`datetime`/`timedelta`/`timezone`,
  `external_data_service.get_fear_greed_cmc`, `binance_data_service`의 관련 함수들).
  `LIVE_INDICATOR_FACTORY`에 6개 항목 추가(총 39개). 모듈 docstring을 "A그룹만" →
  "A+B그룹 39개 전부"로 갱신(Task 8).
- **Modify:** `tests/live_indicator_fixtures.py` — `run_backtrader_probe_with_aux()`/
  `assert_matches_backtrader_with_aux()` 추가(보조 라인이 필요한 지표용, Task 1).
- **Modify:** `binance_data_service.py` — `_timeframe_duration()`을 공개 `timeframe_duration()`으로
  리네임(Task 4) — `fetch_live_binance_close()`가 재사용.
- **Create:** `tests/test_live_indicators_market.py` — `MARKET_TREND`/`BTC_CORRELATION`/
  `USDT_CORRELATION` 골든테스트(Task 1·2).
- **Create:** `tests/test_live_indicators_external.py` — `FEAR_GREED_CMC`/`KOREA_PREMIUM`/
  `FUNDING_RATE` 골든테스트 + `compute_korea_premium_value()` 단위테스트(Task 3) +
  `fetch_live_*()` 3개의 모킹 기반 장애정책 단위테스트(Task 5·6·7).

---

### Task 1: 골든테스트 하네스 확장(보조 라인 지원) + `MARKET_TREND`

**Files:**
- Modify: `tests/live_indicator_fixtures.py`
- Modify: `trading/live_indicators.py`
- Create: `tests/test_live_indicators_market.py`

**Interfaces:**
- Consumes: `engine.runner.build_data_feed_class`(기존), Task 1 이전까지의
  `trading.live_indicators.LIVE_INDICATOR_FACTORY`(33개).
- Produces: `tests.live_indicator_fixtures.run_backtrader_probe_with_aux(indicator: str,
  params: dict, aux_line: str, aux_series: pd.Series) -> list[float]`,
  `tests.live_indicator_fixtures.assert_matches_backtrader_with_aux(indicator: str,
  params: dict, aux_line: str, aux_series: pd.Series, pandas_series: pd.Series, tol: float
  = 1e-6) -> None`(이후 태스크들이 재사용), `trading.live_indicators.create_market_trend`
  — `LIVE_INDICATOR_FACTORY["MARKET_TREND"]`로 등록됨.

- [x] **Step 1: 골든테스트 하네스에 보조 라인 지원 추가**

`tests/live_indicator_fixtures.py`의 기존 import 목록을 아래로 교체:
```python
"""라이브 지표(trading/live_indicators.py, pandas)와 백테스트 지표
(engine/indicators/*.py, backtrader)가 같은 값을 내는지 검증하는 골든테스트 공용 하네스."""
from __future__ import annotations

import backtrader as bt

from engine.condition_tree import get_indicator_value
from engine.indicators import INDICATOR_FACTORY
from engine.runner import build_data_feed_class
from tests.signal_fixtures import make_oscillating_df
```

파일 맨 끝(`assert_matches_backtrader` 함수 뒤)에 추가:
```python


def run_backtrader_probe_with_aux(indicator: str, params: dict, aux_line: str, aux_series) -> list[float]:
    """run_backtrader_probe()와 같지만, MARKET_TREND/BTC_CORRELATION/USDT_CORRELATION/
    FEAR_GREED_CMC/KOREA_PREMIUM/FUNDING_RATE처럼 btc_close/usdt_close/fear_greed_value/
    korea_premium_value/funding_rate_value 같은 추가 데이터 라인이 필요한 지표용
    (tests/test_indicators.py의 _run_probe_with_aux()와 동일한 패턴)."""
    df = make_oscillating_df()
    df[aux_line] = aux_series
    df_bt = df.set_index("candle_time")
    df_bt.index = df_bt.index.tz_localize(None)

    class _Probe(bt.Strategy):
        def __init__(self) -> None:
            create_fn = INDICATOR_FACTORY[indicator]
            self.probe = create_fn(self.data, **params)
            self.seen_values: list[float] = []

        def next(self) -> None:
            self.seen_values.append(get_indicator_value(indicator, self.probe))

    cerebro = bt.Cerebro()
    cerebro.adddata(
        build_data_feed_class((aux_line,))(
            dataname=df_bt, open="open", high="high", low="low", close="close",
            volume="volume", openinterest=-1, **{aux_line: aux_line},
        )
    )
    cerebro.addstrategy(_Probe)
    results = cerebro.run()
    return results[0].seen_values


def assert_matches_backtrader_with_aux(
    indicator: str, params: dict, aux_line: str, aux_series, pandas_series, tol: float = 1e-6
) -> None:
    """assert_matches_backtrader()와 같지만 보조 라인이 필요한 지표용."""
    bt_values = run_backtrader_probe_with_aux(indicator, params, aux_line, aux_series)
    pandas_last = pandas_series.iloc[-1]
    bt_last = bt_values[-1]
    assert abs(pandas_last - bt_last) < tol, (
        f"{indicator}({params}) 불일치: pandas={pandas_last!r} vs backtrader={bt_last!r} "
        f"(오차={abs(pandas_last - bt_last)!r})"
    )
```

- [x] **Step 2: 실패하는 테스트 작성**

`tests/test_live_indicators_market.py`(신규 파일):
```python
from tests.live_indicator_fixtures import assert_matches_backtrader_with_aux
from tests.signal_fixtures import make_oscillating_df
from trading.live_indicators import LIVE_INDICATOR_FACTORY, create_market_trend


def test_market_trend_matches_backtrader():
    df = make_oscillating_df()
    btc_close = df["close"] * 2 + 1000  # 대상 마켓과 스케일이 다른 별도 시세임을 검증
    df["btc_close"] = btc_close
    assert_matches_backtrader_with_aux(
        "MARKET_TREND", {"period": 5}, "btc_close", btc_close,
        create_market_trend(df, period=5),
    )


def test_market_trend_uses_default_period_10_when_omitted():
    df = make_oscillating_df()
    df["btc_close"] = df["close"] * 2 + 1000
    default = create_market_trend(df)
    explicit = create_market_trend(df, period=10)
    assert default.equals(explicit)


def test_live_indicator_factory_registers_market_trend():
    assert LIVE_INDICATOR_FACTORY["MARKET_TREND"] is create_market_trend
```

- [x] **Step 3: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_live_indicators_market.py -v`
Expected: FAIL — `ImportError: cannot import name 'create_market_trend'`

- [x] **Step 4: `trading/live_indicators.py`에 함수 추가**

`LIVE_INDICATOR_FACTORY: dict[str, object] = {` 줄 바로 위에 추가:
```python
def create_market_trend(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 10))
    btc_close = df["btc_close"]
    return btc_close - btc_close.rolling(period).mean()


```

`LIVE_INDICATOR_FACTORY` 딕셔너리 리터럴에 다음을 마지막 항목으로 추가:
```python
    "MARKET_TREND": create_market_trend,
```

- [x] **Step 5: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_live_indicators_market.py -v`
Expected: 3개 테스트 전부 PASS

- [x] **Step 6: 커밋**

```bash
git add trading/live_indicators.py tests/live_indicator_fixtures.py tests/test_live_indicators_market.py
git commit -m "feat: live_indicators 골든테스트 하네스에 보조라인 지원 추가 + MARKET_TREND 포팅"
```

---

### Task 2: `BTC_CORRELATION` / `USDT_CORRELATION`(롤링 피어슨 상관계수, 2개)

**Files:**
- Modify: `trading/live_indicators.py`
- Modify: `tests/test_live_indicators_market.py`

**Interfaces:**
- Consumes: Task 1의 `LIVE_INDICATOR_FACTORY`, `assert_matches_backtrader_with_aux`.
- Produces: `create_btc_correlation`, `create_usdt_correlation`(내부적으로
  `_rolling_pearson_corr()` 헬퍼 공유) — `"BTC_CORRELATION"`, `"USDT_CORRELATION"` 키로
  등록됨.

`engine/indicators/market.py`의 `RollingCorrelation`(bt.Indicator)은 두 종가 라인의
봉 대비 등락률(ROC100, period=1)을 최근 `period`봉 모아 `statistics.correlation()`으로
피어슨 상관계수를 계산하되, 윈도우 중 하나라도 분산이 0이면(`StatisticsError`) 0.0을
반환한다(페그·스테이블코인 마켓이 완전히 flat한 구간 대응). pandas
`Series.rolling(period).corr(other)`는 수학적으로 동일한 값을 내지만 분산 0인 경우
`StatisticsError` 대신 조용히 `NaN`을 낸다 — 이 차이를 `_rolling_pearson_corr()`에서
직접 보정해야 한다.

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_live_indicators_market.py` 파일 끝에 추가:
```python
import pandas as pd

from trading.live_indicators import create_btc_correlation, create_usdt_correlation


def test_btc_correlation_matches_backtrader():
    df = make_oscillating_df()
    btc_df = make_oscillating_df(base=50000.0, amplitude=3000.0, period=45, ripple_period=9)
    df["btc_close"] = btc_df["close"]
    assert_matches_backtrader_with_aux(
        "BTC_CORRELATION", {"period": 10}, "btc_close", btc_df["close"],
        create_btc_correlation(df, period=10),
    )


def test_usdt_correlation_matches_backtrader():
    df = make_oscillating_df()
    usdt_df = make_oscillating_df(base=1300.0, amplitude=40.0, period=30, ripple_period=4)
    df["usdt_close"] = usdt_df["close"]
    assert_matches_backtrader_with_aux(
        "USDT_CORRELATION", {"period": 10}, "usdt_close", usdt_df["close"],
        create_usdt_correlation(df, period=10),
    )


def test_usdt_correlation_returns_zero_when_aux_series_is_constant():
    # KRW-USDT 등 페그/스테이블코인 마켓이 완전히 flat(무변동)한 구간에서는 피어슨
    # 상관계수가 수학적으로 정의되지 않는다 — 크래시/NaN 대신 "상관 신호 없음"으로
    # 0.0을 반환해야 한다(engine/indicators/market.py의 RollingCorrelation과 동일 정책).
    df = make_oscillating_df()
    flat_usdt = pd.Series([1300.0] * len(df))
    df["usdt_close"] = flat_usdt
    result = create_usdt_correlation(df, period=10)
    assert result.iloc[-1] == 0.0


def test_btc_correlation_uses_default_period_20_when_omitted():
    df = make_oscillating_df()
    btc_df = make_oscillating_df(base=50000.0, amplitude=3000.0, period=45, ripple_period=9)
    df["btc_close"] = btc_df["close"]
    default = create_btc_correlation(df)
    explicit = create_btc_correlation(df, period=20)
    assert default.equals(explicit)


def test_live_indicator_factory_registers_correlations():
    assert LIVE_INDICATOR_FACTORY["BTC_CORRELATION"] is create_btc_correlation
    assert LIVE_INDICATOR_FACTORY["USDT_CORRELATION"] is create_usdt_correlation
```

- [x] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_live_indicators_market.py -v`
Expected: FAIL — `ImportError: cannot import name 'create_btc_correlation'`

- [x] **Step 3: `trading/live_indicators.py`에 함수 추가**

`LIVE_INDICATOR_FACTORY: dict[str, object] = {` 줄 바로 위에 추가:
```python
def _rolling_pearson_corr(a: pd.Series, b: pd.Series, period: int) -> pd.Series:
    """두 종가 시리즈의 봉 대비 등락률(ROC100, period=1)을 최근 period봉 모아 피어슨
    상관계수를 구한다. pandas rolling corr는 윈도우 내 분산이 0이면 NaN을 내는데,
    engine/indicators/market.py의 RollingCorrelation은 이 경우 0.0을 반환하므로(페그·
    스테이블코인 마켓 대응) 여기서도 같은 값으로 보정한다."""
    roc_a = a.pct_change() * 100
    roc_b = b.pct_change() * 100
    corr = roc_a.rolling(period).corr(roc_b)
    std_a = roc_a.rolling(period).std()
    std_b = roc_b.rolling(period).std()
    is_flat = (std_a == 0) | (std_b == 0)
    return corr.where(~is_flat, 0.0)


def create_btc_correlation(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 20))
    return _rolling_pearson_corr(df["close"], df["btc_close"], period)


def create_usdt_correlation(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 20))
    return _rolling_pearson_corr(df["close"], df["usdt_close"], period)


```

`LIVE_INDICATOR_FACTORY` 딕셔너리 리터럴에 다음 2개를 마지막 항목들로 추가:
```python
    "BTC_CORRELATION": create_btc_correlation,
    "USDT_CORRELATION": create_usdt_correlation,
```

- [x] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_live_indicators_market.py -v`
Expected: 8개 테스트 전부 PASS

- [x] **Step 5: 커밋**

```bash
git add trading/live_indicators.py tests/test_live_indicators_market.py
git commit -m "feat: live_indicators에 BTC_CORRELATION/USDT_CORRELATION pandas 포팅 추가"
```

---

### Task 3: `FEAR_GREED_CMC` / `KOREA_PREMIUM`(패스스루) / `FUNDING_RATE`(패스스루) + `compute_korea_premium_value()`

**Files:**
- Modify: `trading/live_indicators.py`
- Create: `tests/test_live_indicators_external.py`

**Interfaces:**
- Consumes: Task 1의 `assert_matches_backtrader_with_aux`.
- Produces: `create_fear_greed_cmc`, `create_korea_premium`, `create_funding_rate`
  — `"FEAR_GREED_CMC"`, `"KOREA_PREMIUM"`, `"FUNDING_RATE"` 키로 등록됨.
  `compute_korea_premium_value(df: pd.DataFrame) -> pd.Series` — Task 7까지는 아무도
  호출하지 않지만(daemon이 실제로 이 값을 채우는 건 서브플랜⑤ 몫), 공식을 한 곳에
  정의해 백테스트(`backend/main.py:789`)와 동일하게 유지한다.

`engine/indicators/sentiment.py`의 `create_fear_greed_cmc`/`create_korea_premium`/
`create_funding_rate`는 전부 이미 채워진 데이터 라인(`data.fear_greed_value` 등)을 그대로
반환하는 패스스루다 — 라이브 버전도 동일하게 `df["fear_greed_value"]` 등을 그대로
반환한다. `KOREA_PREMIUM`의 실제 공식(`(close / (binance_close * usdt_close) - 1) * 100`,
`backend/main.py:789`)은 백테스트에서도 지표 함수가 아니라 `backend/main.py`가 캔들
병합 시점에 미리 계산해 `korea_premium_value` 컬럼에 채워 넣는다 — 라이브도 같은 구조를
따라 `compute_korea_premium_value()`를 별도 헬퍼로 둔다(daemon이 매 캔들마다 이 함수로
컬럼을 채운 뒤 `create_korea_premium()`을 호출하는 흐름은 서브플랜⑤에서 구현).

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_live_indicators_external.py`(신규 파일):
```python
import pandas as pd
import pytest

from tests.live_indicator_fixtures import assert_matches_backtrader_with_aux
from tests.signal_fixtures import make_oscillating_df
from trading.live_indicators import (
    LIVE_INDICATOR_FACTORY,
    compute_korea_premium_value,
    create_fear_greed_cmc,
    create_funding_rate,
    create_korea_premium,
)


def test_fear_greed_cmc_matches_backtrader():
    df = make_oscillating_df()
    fear_greed = pd.Series([30.0 + (i % 50) for i in range(len(df))])
    df["fear_greed_value"] = fear_greed
    assert_matches_backtrader_with_aux(
        "FEAR_GREED_CMC", {}, "fear_greed_value", fear_greed,
        create_fear_greed_cmc(df),
    )


def test_korea_premium_matches_backtrader():
    df = make_oscillating_df()
    korea_premium = pd.Series([3.0 + (i % 5) * 0.1 for i in range(len(df))])
    df["korea_premium_value"] = korea_premium
    assert_matches_backtrader_with_aux(
        "KOREA_PREMIUM", {}, "korea_premium_value", korea_premium,
        create_korea_premium(df),
    )


def test_funding_rate_matches_backtrader():
    df = make_oscillating_df()
    funding = pd.Series([0.03] * len(df))
    df["funding_rate_value"] = funding
    assert_matches_backtrader_with_aux(
        "FUNDING_RATE", {}, "funding_rate_value", funding,
        create_funding_rate(df),
    )


def test_compute_korea_premium_value_matches_formula():
    df = pd.DataFrame({
        "close": [100_000_000.0, 101_000_000.0],
        "binance_close": [70000.0, 70500.0],
        "usdt_close": [1400.0, 1405.0],
    })
    result = compute_korea_premium_value(df)
    expected = (df["close"] / (df["binance_close"] * df["usdt_close"]) - 1) * 100
    assert result.iloc[0] == pytest.approx(expected.iloc[0])
    assert result.iloc[1] == pytest.approx(expected.iloc[1])


def test_compute_korea_premium_value_propagates_nan_when_binance_close_missing():
    df = pd.DataFrame({
        "close": [100_000_000.0],
        "binance_close": [float("nan")],
        "usdt_close": [1400.0],
    })
    result = compute_korea_premium_value(df)
    assert result.iloc[0] != result.iloc[0]  # NaN


def test_live_indicator_factory_registers_external_group():
    assert LIVE_INDICATOR_FACTORY["FEAR_GREED_CMC"] is create_fear_greed_cmc
    assert LIVE_INDICATOR_FACTORY["KOREA_PREMIUM"] is create_korea_premium
    assert LIVE_INDICATOR_FACTORY["FUNDING_RATE"] is create_funding_rate
```

- [x] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_live_indicators_external.py -v`
Expected: FAIL — `ImportError: cannot import name 'create_fear_greed_cmc'`

- [x] **Step 3: `trading/live_indicators.py`에 함수 추가**

`LIVE_INDICATOR_FACTORY: dict[str, object] = {` 줄 바로 위에 추가:
```python
def create_fear_greed_cmc(df: pd.DataFrame, **params) -> pd.Series:
    return df["fear_greed_value"]


def create_korea_premium(df: pd.DataFrame, **params) -> pd.Series:
    return df["korea_premium_value"]


def create_funding_rate(df: pd.DataFrame, **params) -> pd.Series:
    return df["funding_rate_value"]


def compute_korea_premium_value(df: pd.DataFrame) -> pd.Series:
    """한국프리미엄 = (대상마켓 종가 / (바이낸스 현물종가 x USDT/KRW 환율) - 1) x 100.
    backend/main.py의 백테스트 캔들 병합 로직(korea_premium_value 컬럼 생성, 결정 8과
    무관하게 이미 존재하던 공식)과 동일하다. df["binance_close"]/df["usdt_close"] 중
    하나라도 NaN이면 결과도 자연히 NaN이 되어 eval_group_values()가 unknown으로
    처리한다(스펙 결정 8) — 별도 방어코드가 필요 없다."""
    return (df["close"] / (df["binance_close"] * df["usdt_close"]) - 1) * 100


```

`LIVE_INDICATOR_FACTORY` 딕셔너리 리터럴에 다음 3개를 마지막 항목들로 추가:
```python
    "FEAR_GREED_CMC": create_fear_greed_cmc,
    "KOREA_PREMIUM": create_korea_premium,
    "FUNDING_RATE": create_funding_rate,
```

- [x] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_live_indicators_external.py -v`
Expected: 6개 테스트 전부 PASS

- [x] **Step 5: 커밋**

```bash
git add trading/live_indicators.py tests/test_live_indicators_external.py
git commit -m "feat: live_indicators에 FEAR_GREED_CMC/KOREA_PREMIUM/FUNDING_RATE 패스스루 포팅 + compute_korea_premium_value 추가"
```

---

### Task 4: `binance_data_service.py` — `_timeframe_duration()`을 공개 `timeframe_duration()`으로 리네임

**Files:**
- Modify: `binance_data_service.py`

**Interfaces:**
- Consumes: 없음(내부 리네임).
- Produces: `binance_data_service.timeframe_duration(timeframe: str) -> timedelta`(공개,
  `__all__`에 추가) — Task 7의 `fetch_live_binance_close()`가 재사용.

`_timeframe_duration()`은 현재 `binance_data_service.py` 내부 전용(비공개, 밑줄 접두)이고
호출부는 `get_binance_close()`(Line 218) 하나뿐이다. `tests/test_upbit_data_service.py`가
테스트하는 `_timeframe_duration`은 **다른 모듈**(`upbit_data_service.py`)의 동명 함수이므로
이 리네임과 무관하다(사전 확인: `grep -rn "_timeframe_duration"`으로 두 모듈에 독립적으로
존재함을 확인함). 이 함수를 그대로 공개해 `fetch_live_binance_close()`(Task 7)가
"이 timeframe 캔들 하나가 몇 분/시간짜리인지" 알아내는 데 재사용한다 — 로직을 복제하지
않기 위함.

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_binance_data_service.py` 파일 끝에 추가(파일이 이미 존재하면 맨 아래,
`from datetime import timedelta`가 이미 import돼 있는지 확인하고 없으면 추가):
```python
def test_timeframe_duration_is_public():
    from binance_data_service import timeframe_duration
    assert timeframe_duration("minutes60") == timedelta(minutes=60)
    assert timeframe_duration("days") == timedelta(days=1)
```

- [x] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_binance_data_service.py -v -k timeframe_duration_is_public`
Expected: FAIL — `ImportError: cannot import name 'timeframe_duration'`

- [x] **Step 3: 리네임**

`binance_data_service.py`에서:
```python
def _timeframe_duration(timeframe: str) -> timedelta:
```
를
```python
def timeframe_duration(timeframe: str) -> timedelta:
```
로 바꾸고, 같은 파일의 유일한 호출부(`get_binance_close()` 안)에서:
```python
    duration = _timeframe_duration(timeframe)
```
를
```python
    duration = timeframe_duration(timeframe)
```
로 바꾼다. 파일 맨 끝의 `__all__` 리스트를:
```python
__all__ = ["get_binance_close", "binance_symbol", "BinanceSymbolNotFoundError", "get_binance_funding_rate", "merge_funding_rate"]
```
에서:
```python
__all__ = ["get_binance_close", "binance_symbol", "BinanceSymbolNotFoundError", "get_binance_funding_rate", "merge_funding_rate", "timeframe_duration"]
```
로 바꾼다.

- [x] **Step 4: 테스트 실행해서 통과 확인 + 전체 회귀 확인**

Run: `python -m pytest tests/test_binance_data_service.py -v`
Expected: 전부 PASS(기존 테스트는 `_timeframe_duration`을 직접 참조하지 않았으므로 회귀 없음)

Run: `python -m pytest -q`
Expected: 전부 PASS

- [x] **Step 5: 커밋**

```bash
git add binance_data_service.py tests/test_binance_data_service.py
git commit -m "refactor: binance_data_service의 _timeframe_duration을 공개 timeframe_duration으로 리네임"
```

---

### Task 5: `fetch_live_fear_greed_value()` — 공포탐욕지수 실시간 조회 + 장애정책

**Files:**
- Modify: `trading/live_indicators.py`
- Modify: `tests/test_live_indicators_external.py`

**Interfaces:**
- Consumes: `external_data_service.get_fear_greed_cmc(start: datetime, end: datetime) ->
  pd.DataFrame`(기존, 컬럼 `[date, fear_greed_value]`).
- Produces: `trading.live_indicators.fetch_live_fear_greed_value(now: datetime | None =
  None) -> float | None`.

alternative.me 공포탐욕지수는 하루 1회 갱신된다. **스테일 기준: 최신 값의 날짜가
`now`로부터 2일(`FEAR_GREED_STALE_AFTER`)보다 오래됐으면 `None`** — 정상적인 하루 지연
(API가 아직 오늘자 값을 발행하지 않은 경우)은 허용하되, 파이프라인이 며칠째 멈춘
경우는 걸러낸다. API 호출 자체가 실패(`RuntimeError`, `get_fear_greed_cmc()`가 재시도
소진 후 던짐)하거나 빈 결과면 마찬가지로 `None`.

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_live_indicators_external.py` 파일 끝에 추가:
```python
from datetime import datetime, timedelta, timezone

import trading.live_indicators as live_indicators
from trading.live_indicators import fetch_live_fear_greed_value


def test_fetch_live_fear_greed_value_returns_latest_when_fresh(monkeypatch):
    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)

    def fake_get_fear_greed_cmc(start, end):
        return pd.DataFrame({
            "date": [now.replace(hour=0, minute=0, second=0, microsecond=0)],
            "fear_greed_value": [55.0],
        })

    monkeypatch.setattr(live_indicators, "get_fear_greed_cmc", fake_get_fear_greed_cmc)
    assert fetch_live_fear_greed_value(now=now) == pytest.approx(55.0)


def test_fetch_live_fear_greed_value_returns_none_when_stale(monkeypatch):
    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
    stale_date = now - timedelta(days=5)

    def fake_get_fear_greed_cmc(start, end):
        return pd.DataFrame({"date": [stale_date], "fear_greed_value": [55.0]})

    monkeypatch.setattr(live_indicators, "get_fear_greed_cmc", fake_get_fear_greed_cmc)
    assert fetch_live_fear_greed_value(now=now) is None


def test_fetch_live_fear_greed_value_returns_none_when_empty(monkeypatch):
    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        live_indicators, "get_fear_greed_cmc",
        lambda start, end: pd.DataFrame(columns=["date", "fear_greed_value"]),
    )
    assert fetch_live_fear_greed_value(now=now) is None


def test_fetch_live_fear_greed_value_returns_none_on_api_failure(monkeypatch):
    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)

    def raise_runtime_error(start, end):
        raise RuntimeError("alternative.me 공포탐욕지수 API 호출 실패")

    monkeypatch.setattr(live_indicators, "get_fear_greed_cmc", raise_runtime_error)
    assert fetch_live_fear_greed_value(now=now) is None
```

- [x] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_live_indicators_external.py -v -k fetch_live_fear_greed`
Expected: FAIL — `ImportError: cannot import name 'fetch_live_fear_greed_value'`

- [x] **Step 3: `trading/live_indicators.py`에 구현 추가**

파일 맨 위 import 블록을 아래로 교체(기존 `import statistics` / `from collections import
deque` / `import numpy as np` / `import pandas as pd`는 유지하고 아래 3줄만 추가):
```python
from __future__ import annotations

import statistics
from collections import deque
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from external_data_service import get_fear_greed_cmc
```

`LIVE_INDICATOR_FACTORY: dict[str, object] = {` 줄 바로 위에 추가:
```python
FEAR_GREED_STALE_AFTER = timedelta(days=2)


def fetch_live_fear_greed_value(now: datetime | None = None) -> float | None:
    """alternative.me 공포탐욕지수의 가장 최근 값을 조회한다. API 호출이 실패하거나
    가장 최근 값의 날짜가 FEAR_GREED_STALE_AFTER(2일)보다 오래됐으면(정상적인 하루
    발행 지연을 넘어 파이프라인이 며칠째 멈춘 경우) 오래된 값을 forward-fill하지 않고
    None을 반환한다(스펙 결정 8)."""
    now = now or datetime.now(timezone.utc)
    try:
        df = get_fear_greed_cmc(now - timedelta(days=7), now)
    except RuntimeError:
        return None
    if df.empty:
        return None
    latest = df.iloc[-1]
    if now - latest["date"] > FEAR_GREED_STALE_AFTER:
        return None
    return float(latest["fear_greed_value"])


```

- [x] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_live_indicators_external.py -v`
Expected: 전부 PASS

- [x] **Step 5: 커밋**

```bash
git add trading/live_indicators.py tests/test_live_indicators_external.py
git commit -m "feat: live_indicators에 fetch_live_fear_greed_value 장애정책 추가"
```

---

### Task 6: `fetch_live_funding_rate_value()` — 바이낸스 펀딩비 실시간 조회 + 장애정책

**Files:**
- Modify: `trading/live_indicators.py`
- Modify: `tests/test_live_indicators_external.py`

**Interfaces:**
- Consumes: `binance_data_service.get_binance_funding_rate(symbol: str, start: datetime,
  end: datetime) -> pd.DataFrame`(기존, 컬럼 `[funding_time, funding_rate]`, 퍼센트
  단위), `binance_data_service.binance_symbol(market: str) -> str`(기존).
- Produces: `trading.live_indicators.fetch_live_funding_rate_value(market: str, now:
  datetime | None = None) -> float | None`.

바이낸스 무기한 선물 펀딩비는 8시간마다 갱신된다. **스테일 기준: 최신 값의
`funding_time`이 `now`로부터 16시간(`FUNDING_RATE_STALE_AFTER`)보다 오래됐으면 `None`** —
이 16시간은 임의값이 아니라 기존 `merge_funding_rate()`(`binance_data_service.py:375`,
백테스트에서 이미 검증된 값)가 `merge_asof(..., tolerance=pd.Timedelta(hours=16))`로 쓰는
것과 동일한 상수다(8시간 주기가 한 번 밀려도 다음 주기까지 여유를 두는 근거가 이미
검증돼 있으므로 그대로 재사용). API 실패·빈 결과는 Task 5와 동일하게 `None`.

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_live_indicators_external.py` 파일 끝에 추가:
```python
from trading.live_indicators import fetch_live_funding_rate_value


def test_fetch_live_funding_rate_value_returns_latest_when_fresh(monkeypatch):
    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)

    def fake_get_binance_funding_rate(symbol, start, end):
        assert symbol == "ETHUSDT"
        return pd.DataFrame({
            "funding_time": [now - timedelta(hours=2)],
            "funding_rate": [0.012],
        })

    monkeypatch.setattr(live_indicators, "get_binance_funding_rate", fake_get_binance_funding_rate)
    assert fetch_live_funding_rate_value("KRW-ETH", now=now) == pytest.approx(0.012)


def test_fetch_live_funding_rate_value_returns_none_when_stale(monkeypatch):
    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)

    def fake_get_binance_funding_rate(symbol, start, end):
        return pd.DataFrame({
            "funding_time": [now - timedelta(hours=20)],
            "funding_rate": [0.012],
        })

    monkeypatch.setattr(live_indicators, "get_binance_funding_rate", fake_get_binance_funding_rate)
    assert fetch_live_funding_rate_value("KRW-ETH", now=now) is None


def test_fetch_live_funding_rate_value_returns_none_when_empty(monkeypatch):
    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        live_indicators, "get_binance_funding_rate",
        lambda symbol, start, end: pd.DataFrame(columns=["funding_time", "funding_rate"]),
    )
    assert fetch_live_funding_rate_value("KRW-ETH", now=now) is None


def test_fetch_live_funding_rate_value_returns_none_on_api_failure(monkeypatch):
    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)

    def raise_runtime_error(symbol, start, end):
        raise RuntimeError("바이낸스 펀딩비 API 호출 실패")

    monkeypatch.setattr(live_indicators, "get_binance_funding_rate", raise_runtime_error)
    assert fetch_live_funding_rate_value("KRW-ETH", now=now) is None
```

- [x] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_live_indicators_external.py -v -k fetch_live_funding_rate`
Expected: FAIL — `ImportError: cannot import name 'fetch_live_funding_rate_value'`

- [x] **Step 3: `trading/live_indicators.py`에 구현 추가**

import 블록의 `from external_data_service import get_fear_greed_cmc` 다음 줄에 추가:
```python
from binance_data_service import binance_symbol, get_binance_funding_rate
```

`fetch_live_fear_greed_value()` 함수 바로 뒤에 추가:
```python
FUNDING_RATE_STALE_AFTER = timedelta(hours=16)


def fetch_live_funding_rate_value(market: str, now: datetime | None = None) -> float | None:
    """바이낸스 무기한 선물 펀딩비의 가장 최근 값(퍼센트 단위)을 조회한다. API 호출이
    실패하거나 가장 최근 값의 funding_time이 FUNDING_RATE_STALE_AFTER(16시간,
    merge_funding_rate()가 백테스트 병합에 쓰는 tolerance와 동일한 근거)보다 오래됐으면
    None을 반환한다(스펙 결정 8)."""
    now = now or datetime.now(timezone.utc)
    symbol = binance_symbol(market)
    try:
        df = get_binance_funding_rate(symbol, now - timedelta(hours=24), now)
    except RuntimeError:
        return None
    if df.empty:
        return None
    latest = df.iloc[-1]
    if now - latest["funding_time"] > FUNDING_RATE_STALE_AFTER:
        return None
    return float(latest["funding_rate"])


```

- [x] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_live_indicators_external.py -v`
Expected: 전부 PASS

- [x] **Step 5: 커밋**

```bash
git add trading/live_indicators.py tests/test_live_indicators_external.py
git commit -m "feat: live_indicators에 fetch_live_funding_rate_value 장애정책 추가"
```

---

### Task 7: `fetch_live_binance_close()` — 바이낸스 현물종가 실시간 조회 + 장애정책

**Files:**
- Modify: `trading/live_indicators.py`
- Modify: `tests/test_live_indicators_external.py`

**Interfaces:**
- Consumes: `binance_data_service.get_binance_close(symbol: str, timeframe: str, start:
  datetime, end: datetime) -> pd.DataFrame`(기존, 컬럼 `[candle_time, close]`),
  `binance_data_service.binance_symbol`(기존), `binance_data_service.timeframe_duration`
  (Task 4에서 공개함), `binance_data_service.BinanceSymbolNotFoundError`(기존).
- Produces: `trading.live_indicators.fetch_live_binance_close(market: str, timeframe: str,
  now: datetime | None = None) -> float | None`.

`KOREA_PREMIUM` 계산(`compute_korea_premium_value()`, Task 3)에 필요한 대상 코인의
바이낸스 현물 종가 최신값을 조회한다. `get_binance_close()`는 이미 "마감된 봉만"
반환하므로(`upbit_data_service.py`와 동일한 gap-fill 패턴), 이 함수는 그 결과의 마지막
행을 가져오되 **스테일 기준: 가장 최근 봉의 `candle_time`이 `now`로부터 해당
timeframe 길이의 2배(`BINANCE_CLOSE_STALE_MULTIPLIER`)보다 오래됐으면 `None`** — 정상
운영 중이면 최신 봉은 항상 1 timeframe 이내여야 하므로, 2배는 네트워크 지연 등에 대한
여유분이다. 심볼이 바이낸스에 없으면(`BinanceSymbolNotFoundError`) 재시도 없이 `None`
(스펙 결정 8이 요구하는 "지연"이 아니라 애초에 계산 불가능한 케이스이므로 즉시 unknown
처리).

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_live_indicators_external.py` 파일 끝에 추가:
```python
from binance_data_service import BinanceSymbolNotFoundError
from trading.live_indicators import fetch_live_binance_close


def test_fetch_live_binance_close_returns_latest_when_fresh(monkeypatch):
    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)

    def fake_get_binance_close(symbol, timeframe, start, end):
        assert symbol == "ETHUSDT"
        assert timeframe == "minutes60"
        return pd.DataFrame({
            "candle_time": [now - timedelta(minutes=30)],
            "close": [3500.5],
        })

    monkeypatch.setattr(live_indicators, "get_binance_close", fake_get_binance_close)
    assert fetch_live_binance_close("KRW-ETH", "minutes60", now=now) == pytest.approx(3500.5)


def test_fetch_live_binance_close_returns_none_when_stale(monkeypatch):
    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)

    def fake_get_binance_close(symbol, timeframe, start, end):
        return pd.DataFrame({
            "candle_time": [now - timedelta(hours=5)],
            "close": [3500.5],
        })

    monkeypatch.setattr(live_indicators, "get_binance_close", fake_get_binance_close)
    assert fetch_live_binance_close("KRW-ETH", "minutes60", now=now) is None


def test_fetch_live_binance_close_returns_none_when_empty(monkeypatch):
    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        live_indicators, "get_binance_close",
        lambda symbol, timeframe, start, end: pd.DataFrame(columns=["candle_time", "close"]),
    )
    assert fetch_live_binance_close("KRW-ETH", "minutes60", now=now) is None


def test_fetch_live_binance_close_returns_none_when_symbol_not_found(monkeypatch):
    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)

    def raise_not_found(symbol, timeframe, start, end):
        raise BinanceSymbolNotFoundError(symbol)

    monkeypatch.setattr(live_indicators, "get_binance_close", raise_not_found)
    assert fetch_live_binance_close("KRW-SOMECOIN", "minutes60", now=now) is None


def test_fetch_live_binance_close_returns_none_on_api_failure(monkeypatch):
    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)

    def raise_runtime_error(symbol, timeframe, start, end):
        raise RuntimeError("바이낸스 API 호출 실패")

    monkeypatch.setattr(live_indicators, "get_binance_close", raise_runtime_error)
    assert fetch_live_binance_close("KRW-ETH", "minutes60", now=now) is None
```

- [x] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_live_indicators_external.py -v -k fetch_live_binance_close`
Expected: FAIL — `ImportError: cannot import name 'fetch_live_binance_close'`

- [x] **Step 3: `trading/live_indicators.py`에 구현 추가**

import 블록의 `from binance_data_service import binance_symbol, get_binance_funding_rate`를
아래로 교체:
```python
from binance_data_service import (
    BinanceSymbolNotFoundError,
    binance_symbol,
    get_binance_close,
    get_binance_funding_rate,
    timeframe_duration,
)
```

`fetch_live_funding_rate_value()` 함수 바로 뒤에 추가:
```python
BINANCE_CLOSE_STALE_MULTIPLIER = 2


def fetch_live_binance_close(market: str, timeframe: str, now: datetime | None = None) -> float | None:
    """KOREA_PREMIUM 계산(compute_korea_premium_value())에 필요한, 대상 코인의 바이낸스
    현물 종가 최신값. 심볼이 바이낸스에 없거나(BinanceSymbolNotFoundError) API 호출이
    실패하거나, 가장 최근 봉이 timeframe 길이의 BINANCE_CLOSE_STALE_MULTIPLIER(2)배보다
    오래됐으면 None을 반환한다(스펙 결정 8). usdt_close(KRW-USDT aux 마켓 종가)와 결합해
    korea_premium_value를 만드는 건 compute_korea_premium_value()의 몫 — 이 함수는 원시
    종가 조회 + 결측 판정만 한다."""
    now = now or datetime.now(timezone.utc)
    symbol = binance_symbol(market)
    duration = timeframe_duration(timeframe)
    try:
        df = get_binance_close(symbol, timeframe, now - 5 * duration, now)
    except (RuntimeError, BinanceSymbolNotFoundError):
        return None
    if df.empty:
        return None
    latest = df.iloc[-1]
    if now - latest["candle_time"] > BINANCE_CLOSE_STALE_MULTIPLIER * duration:
        return None
    return float(latest["close"])


```

- [x] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_live_indicators_external.py -v`
Expected: 전부 PASS

- [x] **Step 5: 커밋**

```bash
git add trading/live_indicators.py tests/test_live_indicators_external.py
git commit -m "feat: live_indicators에 fetch_live_binance_close 장애정책 추가"
```

---

### Task 8: B그룹 완성 확인(39개 전부) + 모듈 docstring 갱신 + 전체 회귀

**Files:**
- Modify: `trading/live_indicators.py`

**Interfaces:**
- Consumes: 이 플랜의 모든 이전 태스크가 만든 `LIVE_INDICATOR_FACTORY` 항목들.
- Produces: 없음(검증 + 문서 갱신 전용 태스크).

- [x] **Step 1: 모듈 docstring 갱신**

`trading/live_indicators.py` 맨 위 docstring을:
```python
"""
trading/live_indicators.py

라이브 트레이딩용 지표 계산 — pandas 기반. engine/indicators/*.py(backtrader 기반,
백테스트 전용)와 값이 일치하도록 골든테스트로 검증한다(스펙 결정 1). A그룹(대상 마켓
OHLCV만으로 계산되는 지표 33개)만 다룬다 — B그룹(외부데이터·보조마켓 의존 6개)은 별도
서브플랜에서 추가한다(스펙 결정 2).

각 함수는 engine/indicators/*.py의 동명 함수와 1:1 대응하며, bt.feeds.PandasData 대신
OHLCV 컬럼(open/high/low/close/volume, 일부는 trade_value)을 가진 pandas.DataFrame을
받아 같은 이름의 pandas.Series(워밍업 구간 NaN)를 반환한다. LIVE_INDICATOR_FACTORY
레지스트리는 engine.indicators.INDICATOR_FACTORY와 같은 패턴이다.
"""
```
로 바꾼다:
```python
"""
trading/live_indicators.py

라이브 트레이딩용 지표 계산 — pandas 기반. engine/indicators/*.py(backtrader 기반,
백테스트 전용)와 값이 일치하도록 골든테스트로 검증한다(스펙 결정 1). INDICATOR_FACTORY
39개(A그룹 33개 + B그룹 6개) 전부를 다룬다(스펙 결정 2).

대부분의 create_* 함수는 순수 계산이다 — bt.feeds.PandasData 대신 필요한 컬럼(OHLCV,
일부는 trade_value/btc_close/usdt_close/fear_greed_value/korea_premium_value/
funding_rate_value)을 가진 pandas.DataFrame을 받아 같은 이름의 pandas.Series(워밍업
구간 NaN)를 반환하며 I/O를 하지 않는다. 예외는 fetch_live_*() 3개(FEAR_GREED_CMC/
FUNDING_RATE/KOREA_PREMIUM의 원시값을 실제로 조회) — 이 셋만 외부 API를 호출하고,
지연·실패 시 오래된 값을 forward-fill하지 않고 None을 반환한다(스펙 결정 8).
LIVE_INDICATOR_FACTORY 레지스트리는 engine.indicators.INDICATOR_FACTORY와 같은 패턴이며
항목 수도 동일하다(39개).
"""
```

- [x] **Step 2: 레지스트리 완성도 확인**

Run:
```bash
python -c "
from trading.live_indicators import LIVE_INDICATOR_FACTORY
from engine.indicators import INDICATOR_FACTORY
missing = set(INDICATOR_FACTORY) - set(LIVE_INDICATOR_FACTORY)
extra = set(LIVE_INDICATOR_FACTORY) - set(INDICATOR_FACTORY)
assert not missing, f'빠진 지표: {missing}'
assert not extra, f'의도치 않은 초과 지표: {extra}'
print('OK:', len(LIVE_INDICATOR_FACTORY), '개 등록됨 (A그룹+B그룹 전부)')
"
```
Expected 출력: `OK: 39 개 등록됨 (A그룹+B그룹 전부)` (에러 없이 통과)

- [x] **Step 3: 전체 테스트 스위트 실행(회귀 확인)**

Run: `python -m pytest -q`
Expected: 전부 PASS(기존 A그룹·백테스트·그리드서치 테스트가 이 플랜으로 깨지지 않아야
함 — `engine/`은 전혀 건드리지 않았고, `binance_data_service.py`는 함수 리네임뿐이라
회귀가 없어야 정상)

- [x] **Step 4: 커밋**

```bash
git add trading/live_indicators.py
git commit -m "docs: live_indicators 모듈 docstring을 A+B그룹 39개 완료로 갱신"
```

---

## Self-Review

**스펙 커버리지:**
- 결정 2(B그룹 6개 전부 포팅, A그룹과 합쳐 39개) → Task 1~3에서 6개 전부 `create_*` 구현,
  Task 8에서 39개 집합 비교로 검증.
- 결정 1(골든테스트 필수, backtrader와 pandas 값 일치) → Task 1~3의 모든 `create_*`가
  `assert_matches_backtrader_with_aux`로 검증(예외 없음).
- 결정 8(B그룹 지표 지연/실패 시 None, forward-fill 금지) → 외부API형 3개는 Task 5·6·7의
  `fetch_live_*()`가 명시적 스테일 판정으로 구현. 보조마켓형 3개는 Architecture 절에서
  설명한 대로 NaN 자연 전파로 이미 커버(서브플랜①의 `eval_group_values` NaN 처리에 의존,
  재구현하지 않음).
- 모듈구조 절("B그룹은 지연/실패 시 None 반환"이 `live_indicators.py`의 책임) → 이 플랜의
  `fetch_live_*()` 3개가 전부 `trading/live_indicators.py` 안에 있음(파일 분리 안 함,
  "하나의 파일로 유지" 제약 준수).
- 이 플랜은 지표 원시값 계산/조회만 다루고, daemon이 매 캔들마다 이 값들을 실제로 어떻게
  수집·조합해 `eval_group_values()`에 넘기는지는 다루지 않는다 — 서브플랜⑤
  (`signal_engine.py`)로 명확히 넘김. 보조마켓(`btc_close`/`usdt_close`) 실시간 캔들 구독은
  서브플랜④(Upbit 연동)로 넘김.

**플레이스홀더 스캔:** 없음 — 모든 스텝에 완전한 코드가 있다.

**타입 일관성:** `create_*` 6개는 A그룹과 동일한 `(df: pd.DataFrame, **params) ->
pd.Series` 시그니처를 유지한다. `fetch_live_*()` 3개는 전부 `(..., now: datetime | None =
None) -> float | None` 패턴으로 통일했다(테스트에서 `now`를 주입해 결정론적으로 검증할
수 있게 하기 위함 — 실제 daemon 호출 시엔 인자를 생략해 `datetime.now(timezone.utc)`가
쓰인다). `compute_korea_premium_value(df: pd.DataFrame) -> pd.Series`는 다른 `create_*`와
달리 `**params`를 받지 않는다 — `LIVE_INDICATOR_FACTORY`에 등록되는 지표 함수가 아니라
daemon이 컬럼을 채우기 전에 호출하는 준비 단계 헬퍼이기 때문(등록 안 함, 의도적).

---

## 다음 서브플랜 (이 문서 이후)

④ **Upbit 연동** — `trading/upbit_client.py`(REST/JWT/Throttle), `trading/upbit_ws.py`
(공개 WebSocket 구독: 대상 마켓 + 보조마켓 캔들). 이 서브플랜이 완성되면 비로소
`btc_close`/`usdt_close` 컬럼을 실시간으로 채울 방법이 생긴다. ⑤ 트레이딩 엔진 코어
(`signal_engine.py`가 `collect_blocks`+`indicator_key`+`LIVE_INDICATOR_FACTORY`+
`eval_group_values`+이 플랜의 `fetch_live_*`를 전부 결합해 매 캔들 신호를 만든다),
`order_executor.py`, `position_manager.py`, `risk_manager.py`, `reconciler.py`,
`daemon.py`. ⑥ UX(`backend/main.py` 라이브 전략 관리 API + 프론트 페이지).
