# 라이브 트레이딩 서브플랜⑤-2 — signal_engine.py Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking. **워크트리를 만들지 말고 main 브랜치에서 직접 작업한다** (사용자 지시, [[upbit-v1-worktree-workflow-changed]]).

**Goal:** `trading/signal_engine.py`를 만들어, 서브플랜②③(`trading/live_indicators.py`)과
서브플랜①(`engine/condition_tree.eval_group_values()`)을 결합해 새 봉 마감마다 매수/매도
신호를 계산하고 `signals` 테이블에 기록한다. 판단불가(None) 시 일시정지, 재개 시
서킷브레이커 확인까지 포함한다(⑤-1의 CRUD 재사용).

**Architecture:** 설계 스펙
`docs/superpowers/specs_v1/2026-08-07-live-trading-signal-engine-design.md`를 그대로 구현한다.
서브플랜⑤(트레이딩 엔진 코어) 4단계 중 두 번째다. `trading/signal_engine.py`는
`engine.condition_tree`(스펙 결정1이 허용한 유일한 예외)만 `engine/`에서 import하고,
네트워크 호출은 전부 `upbit_data_service.get_candles()`와
`trading.live_indicators.fetch_live_*()`(이미 검증됨)를 통해서만 한다 — `httpx`나
`trading.upbit_client`를 직접 import하지 않는다.

**Tech Stack:** Python, `pandas`, `pytest`. 새 의존성 없음.

## Global Constraints

- `trading/signal_engine.py`는 `engine.condition_tree` 외에 다른 `engine/` 서브모듈을
  import하지 않는다(스펙 결정1의 유일한 예외 유지). `httpx`/`trading.upbit_client` 직접
  import 금지 — 네트워크는 `upbit_data_service`/`trading.live_indicators`가 이미
  캡슐화한 함수로만.
- `trading/signal_engine.py`는 **하나의 파일로 유지**한다(다른 `trading/` 모듈과 동일 관례).
- 이 서브플랜은 `orders` 테이블 CRUD나 `signals.resulting_order_id` 갱신 함수를 만들지
  않는다(YAGNI — ⑤-3 주문실행 서브플랜의 몫). 실제 주문 실행 여부 결정(서킷브레이커 체크 →
  `order_executor` 호출)과 ticker 기반 손절/익절 실시간 평가도 이 서브플랜에서 다루지
  않는다(⑤-3/⑤-4로 명확히 넘김) — `evaluate_signals()`는 신호의 True/False/판단불가만
  계산·기록한다.
- 커밋은 태스크 단위로 작게, 테스트가 통과한 뒤에만 한다.

---

## File Structure

- **Modify:** `upbit_data_service.py` — `_timeframe_duration()`을 공개 `timeframe_duration()`
  으로 리네임(서브플랜④ Task4가 `binance_data_service.py`에 한 것과 동일 패턴).
- **Modify:** `tests/test_upbit_data_service.py` — 위 리네임에 맞춰 테스트 갱신.
- **Modify:** `trading/db.py` — `insert_signal()` CRUD 추가.
- **Modify:** `tests/test_trading_db.py` — `insert_signal()` 테스트 추가.
- **Create:** `trading/signal_engine.py` — 캔들 조회+워밍업, 보조마켓 병합, B그룹 외부데이터
  결합, 지표값 계산, 포지션 컨텍스트, `evaluate_signals()`.
- **Create:** `tests/test_signal_engine.py`.

---

### Task 1: `upbit_data_service.py` — `_timeframe_duration()`을 공개 `timeframe_duration()`으로 리네임

**Files:**
- Modify: `upbit_data_service.py`
- Modify: `tests/test_upbit_data_service.py`

**Interfaces:**
- Consumes: 없음(내부 리네임).
- Produces: `upbit_data_service.timeframe_duration(timeframe: str) -> timedelta`(공개) —
  Task 3의 `signal_engine._fetch_candles_with_warmup()`이 재사용.

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_upbit_data_service.py`의 기존 `test_timeframe_duration` 함수(파일 하단 부근)를
아래로 교체:
```python
def test_timeframe_duration():
    assert uds.timeframe_duration("days") == timedelta(days=1)
    assert uds.timeframe_duration("minutes60") == timedelta(minutes=60)
    with pytest.raises(ValueError):
        uds.timeframe_duration("weeks")
```

- [x] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_upbit_data_service.py -v -k timeframe_duration`
Expected: FAIL — `AttributeError: module 'upbit_data_service' has no attribute 'timeframe_duration'`

- [x] **Step 3: 리네임**

`upbit_data_service.py`에서:
```python
def _timeframe_duration(timeframe: str) -> timedelta:
```
를
```python
def timeframe_duration(timeframe: str) -> timedelta:
```
로 바꾸고, 유일한 내부 호출부(`get_candles()` 안, `duration = _timeframe_duration(timeframe)`
줄)를:
```python
    duration = timeframe_duration(timeframe)
```
로 바꾼다.

- [x] **Step 4: 테스트 실행해서 통과 확인 + 전체 회귀**

Run: `python -m pytest tests/test_upbit_data_service.py -v`
Expected: 전부 PASS

Run: `python -m pytest -q`
Expected: 전부 PASS(회귀 없음 — 이 함수를 외부에서 쓰던 곳이 없었으므로)

- [x] **Step 5: 커밋**

```bash
git add upbit_data_service.py tests/test_upbit_data_service.py
git commit -m "refactor: upbit_data_service의 _timeframe_duration을 공개 timeframe_duration으로 리네임"
```

---

### Task 2: `trading/db.py` — `insert_signal` CRUD

**Files:**
- Modify: `trading/db.py`
- Modify: `tests/test_trading_db.py`

**Interfaces:**
- Consumes: `trading.db._connect()`(기존), `tests.trading_db_fixtures.insert_live_strategy`(⑤-1).
- Produces: `trading.db.insert_signal(live_strategy_id: str, signal_type: str, candle_time: str,
  indicator_snapshot_json: str, skip_reason: str | None = None) -> str`.

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_trading_db.py` 파일 끝에 추가:
```python
def test_insert_signal_creates_row_with_null_resulting_order_id(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)

    signal_id = db.insert_signal(
        strategy_id, "buy", "2026-08-07T10:00:00+00:00", '{"RSI__[(\'period\', 14)]": 25.0}',
    )

    conn = db._connect()
    try:
        conn.row_factory = __import__("sqlite3").Row
        row = conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
    finally:
        conn.close()

    assert row["live_strategy_id"] == strategy_id
    assert row["signal_type"] == "buy"
    assert row["candle_time"] == "2026-08-07T10:00:00+00:00"
    assert row["resulting_order_id"] is None
    assert row["skip_reason"] is None
    assert row["triggered_at"] is not None


def test_insert_signal_stores_skip_reason(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)

    signal_id = db.insert_signal(
        strategy_id, "sell", "2026-08-07T10:00:00+00:00", "{}", skip_reason="unknown",
    )

    conn = db._connect()
    try:
        conn.row_factory = __import__("sqlite3").Row
        row = conn.execute("SELECT skip_reason FROM signals WHERE id = ?", (signal_id,)).fetchone()
    finally:
        conn.close()

    assert row["skip_reason"] == "unknown"
```

- [x] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_trading_db.py -v -k insert_signal`
Expected: FAIL — `AttributeError: module 'trading.db' has no attribute 'insert_signal'`

- [x] **Step 3: `trading/db.py`에 구현 추가**

파일 끝에 추가:
```python


def insert_signal(
    live_strategy_id: str, signal_type: str, candle_time: str,
    indicator_snapshot_json: str, skip_reason: str | None = None,
) -> str:
    signal_id = str(uuid.uuid4())
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO signals "
            "(id, live_strategy_id, signal_type, candle_time, indicator_snapshot_json, skip_reason) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (signal_id, live_strategy_id, signal_type, candle_time, indicator_snapshot_json, skip_reason),
        )
        conn.commit()
    finally:
        conn.close()
    return signal_id
```

- [x] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_trading_db.py -v`
Expected: 전부 PASS

- [x] **Step 5: 커밋**

```bash
git add trading/db.py tests/test_trading_db.py
git commit -m "feat: trading/db.py에 insert_signal CRUD 추가"
```

---

### Task 3: `trading/signal_engine.py` — 캔들 조회+워밍업 + 보조마켓 병합

**Files:**
- Create: `trading/signal_engine.py`
- Create: `tests/test_signal_engine.py`

**Interfaces:**
- Consumes: `upbit_data_service.get_candles`/`timeframe_duration`(Task1).
- Produces: `trading.signal_engine._fetch_candles_with_warmup(market: str, timeframe: str,
  required_bars: int, now: datetime) -> pd.DataFrame`,
  `trading.signal_engine._merge_aux_markets(df: pd.DataFrame, aux_markets: set[str], market: str,
  timeframe: str, required_bars: int, now: datetime) -> pd.DataFrame`.

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_signal_engine.py`(신규 파일):
```python
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

import trading.signal_engine as signal_engine


def test_fetch_candles_with_warmup_computes_start_from_required_bars_plus_buffer(monkeypatch):
    captured = {}

    def fake_get_candles(market, timeframe, start, end):
        captured["market"] = market
        captured["timeframe"] = timeframe
        captured["start"] = start
        captured["end"] = end
        return pd.DataFrame(columns=["candle_time", "close"])

    monkeypatch.setattr(signal_engine, "get_candles", fake_get_candles)
    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)

    signal_engine._fetch_candles_with_warmup("KRW-BTC", "minutes60", 20, now)

    assert captured["market"] == "KRW-BTC"
    assert captured["timeframe"] == "minutes60"
    assert captured["end"] == now
    assert captured["start"] == now - timedelta(hours=25)  # (20+5)*60min


def test_merge_aux_markets_merges_btc_close_with_gap_fill(monkeypatch):
    df = pd.DataFrame({
        "candle_time": pd.date_range("2026-01-01", periods=3, freq="h", tz="UTC"),
        "close": [100.0, 101.0, 102.0],
    })

    def fake_get_candles(market, timeframe, start, end):
        assert market == "KRW-BTC"
        return pd.DataFrame({
            "candle_time": [df["candle_time"].iloc[0], df["candle_time"].iloc[2]],  # 가운데 봉 결측
            "close": [50000.0, 50200.0],
        })

    monkeypatch.setattr(signal_engine, "get_candles", fake_get_candles)
    now = datetime(2026, 1, 1, 3, tzinfo=timezone.utc)

    result = signal_engine._merge_aux_markets(df, {"KRW-BTC"}, "KRW-ETH", "minutes60", 10, now)

    assert list(result["btc_close"]) == [50000.0, 50000.0, 50200.0]  # 가운데는 ffill로 채움


def test_merge_aux_markets_uses_own_close_when_aux_market_is_target_market():
    df = pd.DataFrame({
        "candle_time": pd.date_range("2026-01-01", periods=2, freq="h", tz="UTC"),
        "close": [100.0, 101.0],
    })
    result = signal_engine._merge_aux_markets(
        df, {"KRW-BTC"}, "KRW-BTC", "minutes60", 10, datetime.now(timezone.utc),
    )
    assert list(result["btc_close"]) == [100.0, 101.0]
```

- [x] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_signal_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'trading.signal_engine'`

- [x] **Step 3: `trading/signal_engine.py` 구현**

```python
"""
trading/signal_engine.py

live_indicators.py(서브플랜②③)와 condition_tree.eval_group_values()(서브플랜①)를 결합해
새 봉 마감마다 매수/매도 신호를 계산하고 signals에 기록한다. 캔들은 REST 폴링
(upbit_data_service.get_candles)으로 감지한다 — 업비트 공개 WS에는 캔들 채널이 없다
(서브플랜④에서 확인). 서킷브레이커 체크나 실제 주문 실행은 다루지 않는다(⑤-3/⑤-4의 몫) —
이 모듈은 신호의 True/False/판단불가만 계산·기록한다.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from upbit_data_service import get_candles, timeframe_duration

_AUX_MARKET_LINE_NAME: dict[str, str] = {"KRW-BTC": "btc_close", "KRW-USDT": "usdt_close"}
_WARMUP_BUFFER_BARS = 5


def _fetch_candles_with_warmup(
    market: str, timeframe: str, required_bars: int, now: datetime,
) -> pd.DataFrame:
    """대상(또는 보조) 마켓의 캔들을 required_bars + 여유분(_WARMUP_BUFFER_BARS)만큼
    워밍업 포함해 조회한다."""
    duration = timeframe_duration(timeframe)
    start = now - (required_bars + _WARMUP_BUFFER_BARS) * duration
    return get_candles(market, timeframe, start, now)


def _merge_aux_markets(
    df: pd.DataFrame, aux_markets: set[str], market: str, timeframe: str,
    required_bars: int, now: datetime,
) -> pd.DataFrame:
    """MARKET_TREND/BTC_CORRELATION/USDT_CORRELATION이 필요로 하는 보조마켓 종가를
    btc_close/usdt_close 컬럼으로 병합한다. 백테스트(backend/main.py)와 동일하게
    ffill().bfill()로 갭을 채운다(설계 스펙 결정2 — 이건 특정 타임스탬프 하나가 비는
    정상적인 갭 처리이지 스펙 결정8의 '전체 데이터 소스 장애'와는 다른 문제)."""
    for aux_market in aux_markets:
        line_name = _AUX_MARKET_LINE_NAME[aux_market]
        if aux_market == market:
            df = df.assign(**{line_name: df["close"]})
            continue
        aux_df = _fetch_candles_with_warmup(aux_market, timeframe, required_bars, now)
        df = df.merge(
            aux_df[["candle_time", "close"]].rename(columns={"close": line_name}),
            on="candle_time", how="left",
        )
        df[line_name] = df[line_name].ffill().bfill()
    return df
```

- [x] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_signal_engine.py -v`
Expected: 3개 테스트 전부 PASS

- [x] **Step 5: 커밋**

```bash
git add trading/signal_engine.py tests/test_signal_engine.py
git commit -m "feat: signal_engine에 캔들 워밍업 조회 + 보조마켓 병합 추가"
```

---

### Task 4: `trading/signal_engine.py` — B그룹 외부데이터 결합

**Files:**
- Modify: `trading/signal_engine.py`
- Modify: `tests/test_signal_engine.py`

**Interfaces:**
- Consumes: `trading.live_indicators.fetch_live_fear_greed_value`/`fetch_live_funding_rate_value`/
  `fetch_live_binance_close`/`compute_korea_premium_value`(서브플랜③).
- Produces: `trading.signal_engine._populate_b_group_columns(df: pd.DataFrame, market: str,
  timeframe: str, indicator_names: set[str], now: datetime) -> pd.DataFrame`.

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_signal_engine.py` 파일 끝에 추가:
```python
def test_populate_b_group_columns_fills_fear_greed_only_on_last_row(monkeypatch):
    df = pd.DataFrame({
        "candle_time": pd.date_range("2026-01-01", periods=3, freq="h", tz="UTC"),
        "close": [1.0, 2.0, 3.0],
    })
    monkeypatch.setattr(signal_engine, "fetch_live_fear_greed_value", lambda now=None: 42.0)

    result = signal_engine._populate_b_group_columns(
        df, "KRW-BTC", "minutes60", {"FEAR_GREED_CMC"}, datetime.now(timezone.utc),
    )

    assert result["fear_greed_value"].iloc[-1] == 42.0
    assert result["fear_greed_value"].iloc[:-1].isna().all()


def test_populate_b_group_columns_leaves_nan_when_fetch_fails(monkeypatch):
    df = pd.DataFrame({
        "candle_time": pd.date_range("2026-01-01", periods=2, freq="h", tz="UTC"),
        "close": [1.0, 2.0],
    })
    monkeypatch.setattr(signal_engine, "fetch_live_funding_rate_value", lambda market, now=None: None)

    result = signal_engine._populate_b_group_columns(
        df, "KRW-ETH", "minutes60", {"FUNDING_RATE"}, datetime.now(timezone.utc),
    )

    assert result["funding_rate_value"].isna().all()


def test_populate_b_group_columns_computes_korea_premium_from_binance_and_usdt_close(monkeypatch):
    df = pd.DataFrame({
        "candle_time": pd.date_range("2026-01-01", periods=2, freq="h", tz="UTC"),
        "close": [100_000_000.0, 101_000_000.0],
        "usdt_close": [1400.0, 1405.0],
    })
    monkeypatch.setattr(
        signal_engine, "fetch_live_binance_close", lambda market, timeframe, now=None: 70000.0,
    )

    result = signal_engine._populate_b_group_columns(
        df, "KRW-BTC", "minutes60", {"KOREA_PREMIUM"}, datetime.now(timezone.utc),
    )

    expected = (101_000_000.0 / (70000.0 * 1405.0) - 1) * 100
    assert result["korea_premium_value"].iloc[-1] == pytest.approx(expected)
    assert pd.isna(result["korea_premium_value"].iloc[0])
```

- [x] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_signal_engine.py -v -k b_group`
Expected: FAIL — `AttributeError: module 'trading.signal_engine' has no attribute '_populate_b_group_columns'`

- [x] **Step 3: `trading/signal_engine.py`에 구현 추가**

import 블록의 `from upbit_data_service import get_candles, timeframe_duration` 다음 줄에 추가:
```python
from trading.live_indicators import (
    compute_korea_premium_value,
    fetch_live_binance_close,
    fetch_live_fear_greed_value,
    fetch_live_funding_rate_value,
)
```

`_merge_aux_markets` 함수 바로 뒤에 추가:
```python


def _populate_b_group_columns(
    df: pd.DataFrame, market: str, timeframe: str, indicator_names: set[str], now: datetime,
) -> pd.DataFrame:
    """FEAR_GREED_CMC/FUNDING_RATE/KOREA_PREMIUM이 조건 트리에 있으면 fetch_live_*()로
    현재값을 조회해 df의 마지막 행에만 채운다(설계 스펙 결정1). 조회 실패/스테일이면 None이
    그대로 남아 컬럼이 NaN인 채로 유지되고, eval_group_values가 이를 unknown으로 처리한다
    (스펙 결정8) — 이 함수는 별도 방어코드를 두지 않는다."""
    last_idx = df.index[-1]

    if "FEAR_GREED_CMC" in indicator_names:
        df = df.assign(fear_greed_value=float("nan"))
        value = fetch_live_fear_greed_value(now=now)
        if value is not None:
            df.loc[last_idx, "fear_greed_value"] = value

    if "FUNDING_RATE" in indicator_names:
        df = df.assign(funding_rate_value=float("nan"))
        value = fetch_live_funding_rate_value(market, now=now)
        if value is not None:
            df.loc[last_idx, "funding_rate_value"] = value

    if "KOREA_PREMIUM" in indicator_names:
        df = df.assign(korea_premium_value=float("nan"))
        binance_close = fetch_live_binance_close(market, timeframe, now=now)
        if binance_close is not None and "usdt_close" in df.columns:
            df.loc[last_idx, "binance_close"] = binance_close
            df.loc[last_idx, "korea_premium_value"] = compute_korea_premium_value(
                df.loc[[last_idx]]
            ).iloc[0]

    return df
```

- [x] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_signal_engine.py -v`
Expected: 전부 PASS(3 + 3 = 6개)

- [x] **Step 5: 커밋**

```bash
git add trading/signal_engine.py tests/test_signal_engine.py
git commit -m "feat: signal_engine에 B그룹 외부데이터 결합(_populate_b_group_columns) 추가"
```

---

### Task 5: `trading/signal_engine.py` — 지표값 계산 + 포지션 컨텍스트

**Files:**
- Modify: `trading/signal_engine.py`
- Modify: `tests/test_signal_engine.py`

**Interfaces:**
- Consumes: `trading.live_indicators.LIVE_INDICATOR_FACTORY`(서브플랜②③),
  `engine.condition_tree.indicator_key`(서브플랜①), `trading.position_manager.get_open_position`
  (서브플랜⑤-1), `upbit_data_service.timeframe_duration`(Task1), `tests.signal_fixtures.make_oscillating_df`,
  `tests.trading_db_fixtures.insert_live_strategy`.
- Produces: `trading.signal_engine._compute_indicator_values(df: pd.DataFrame, blocks:
  list[dict]) -> dict[str, float]`, `trading.signal_engine._position_context(live_strategy_id:
  str, latest_close: float, latest_candle_time, timeframe: str) -> tuple[float | None, int |
  None]`.

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_signal_engine.py` 파일 끝에 추가:
```python
from datetime import timedelta

from engine.condition_tree import indicator_key
from tests.signal_fixtures import make_oscillating_df
from tests.trading_db_fixtures import insert_live_strategy
from trading.live_indicators import create_rsi
from trading.position_manager import open_position

import trading.db as db


def _fresh_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "trading.db")
    return db


def test_compute_indicator_values_computes_last_value_per_indicator():
    df = make_oscillating_df()
    blocks = [{"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30}]

    values = signal_engine._compute_indicator_values(df, blocks)

    key = indicator_key("RSI", {"period": 14})
    assert key in values
    assert values[key] == pytest.approx(create_rsi(df, period=14).iloc[-1])


def test_compute_indicator_values_raises_on_unknown_indicator():
    df = make_oscillating_df()
    blocks = [{"indicator": "NOT_A_REAL_INDICATOR", "params": {}, "operator": "<", "threshold": 1}]
    with pytest.raises(ValueError):
        signal_engine._compute_indicator_values(df, blocks)


def test_compute_indicator_values_dedupes_same_indicator_key():
    df = make_oscillating_df()
    blocks = [
        {"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30},
        {"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 70},
    ]
    values = signal_engine._compute_indicator_values(df, blocks)
    assert len(values) == 1


def test_position_context_returns_none_none_when_no_open_position(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm)

    result = signal_engine._position_context(
        strategy_id, 100.0, datetime.now(timezone.utc), "minutes60",
    )

    assert result == (None, None)


def test_position_context_computes_return_pct_and_holding_bars(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm)
    open_position(strategy_id, "KRW-BTC", 100.0, 1.0)  # entry_time = DB의 datetime('now')

    latest_candle_time = datetime.now(timezone.utc) + timedelta(hours=3)
    return_pct, holding_bars = signal_engine._position_context(
        strategy_id, 110.0, latest_candle_time, "minutes60",
    )

    assert return_pct == pytest.approx(10.0)
    assert holding_bars == 2  # 3시간 경과를 60분봉으로 나누면 2.99... -> 정수 절삭
```

- [x] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_signal_engine.py -v -k "compute_indicator_values or position_context"`
Expected: FAIL — `AttributeError: module 'trading.signal_engine' has no attribute '_compute_indicator_values'`

- [x] **Step 3: `trading/signal_engine.py`에 구현 추가**

import 블록에 추가(`from trading.live_indicators import (...)` 다음 줄부터):
```python
from engine.condition_tree import indicator_key
from trading.live_indicators import LIVE_INDICATOR_FACTORY
from trading.position_manager import get_open_position


def _to_utc_timestamp(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
```

(위 `from engine.condition_tree import indicator_key`와 `from trading.live_indicators import
LIVE_INDICATOR_FACTORY`는 Task 4에서 이미 추가한 `from trading.live_indicators import (...)`
블록 다음 줄에 별도 줄로 추가한다 — 기존 import를 지우지 않는다.)

`_populate_b_group_columns` 함수 바로 뒤에 추가:
```python


def _compute_indicator_values(df: pd.DataFrame, blocks: list[dict]) -> dict[str, float]:
    """조건 트리의 모든 ConditionBlock에 대해 지표값을 계산한다(설계 스펙 결정1 —
    A그룹/B그룹 구분 없이 동일하게 LIVE_INDICATOR_FACTORY를 호출, df 준비 방식만 다름)."""
    values: dict[str, float] = {}
    for block in blocks:
        name = block["indicator"]
        params = block.get("params", {})
        if name not in LIVE_INDICATOR_FACTORY:
            raise ValueError(f"알 수 없는 지표: {name}")
        key = indicator_key(name, params)
        if key in values:
            continue
        series = LIVE_INDICATOR_FACTORY[name](df, **params)
        values[key] = series.iloc[-1]
    return values


def _position_context(
    live_strategy_id: str, latest_close: float, latest_candle_time, timeframe: str,
) -> tuple[float | None, int | None]:
    """오픈 포지션이 있으면 (수익률%, 보유 봉 수)를, 없으면 (None, None)을 반환한다
    (STOP_LOSS_PCT/TAKE_PROFIT_PCT/HOLDING_PERIOD_BARS 평가용, 설계 스펙 결정7)."""
    position = get_open_position(live_strategy_id)
    if position is None:
        return None, None

    entry_price = position["entry_price"]
    position_return_pct = (latest_close - entry_price) / entry_price * 100

    entry_time = _to_utc_timestamp(position["entry_time"])
    candle_time = _to_utc_timestamp(latest_candle_time)
    elapsed = candle_time - entry_time
    position_holding_bars = max(int(elapsed / timeframe_duration(timeframe)), 0)

    return position_return_pct, position_holding_bars
```

- [x] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_signal_engine.py -v`
Expected: 전부 PASS(6 + 5 = 11개)

- [x] **Step 5: 커밋**

```bash
git add trading/signal_engine.py tests/test_signal_engine.py
git commit -m "feat: signal_engine에 지표값 계산(_compute_indicator_values) + 포지션 컨텍스트(_position_context) 추가"
```

---

### Task 6: `trading/signal_engine.py` — `evaluate_signals()`

**Files:**
- Modify: `trading/signal_engine.py`
- Modify: `tests/test_signal_engine.py`

**Interfaces:**
- Consumes: Task 3~5의 모든 헬퍼, `trading.db`(⑤-1의 `get_live_strategy`/`insert_signal`/
  `update_live_strategy_status`/`update_live_strategy_last_candle`/`get_circuit_breaker_state`),
  `trading.risk_manager.today_kst`(⑤-1), `engine.condition_tree.eval_group_values`/
  `collect_blocks`/`max_required_period`/`required_aux_markets`/`POSITION_RELATIVE_INDICATORS`
  (서브플랜①).
- Produces: `trading.signal_engine.evaluate_signals(live_strategy_id: str, now: datetime | None
  = None) -> dict`.

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_signal_engine.py` 파일 끝에 추가:
```python
import json

from trading.risk_manager import today_kst


def _strategy_conditions(buy_operator=">", buy_threshold=-1, sell_operator=">", sell_threshold=-1):
    """항상 True가 되도록 RSI(0~100 범위) 조건을 만드는 헬퍼 — 신호평가 로직 자체를
    테스트하는 게 목적이라 지표값의 실제 크기는 중요하지 않다."""
    return (
        json.dumps({"type": "AND", "conditions": [
            {"indicator": "RSI", "params": {"period": 14}, "operator": buy_operator, "threshold": buy_threshold},
        ]}),
        json.dumps({"type": "AND", "conditions": [
            {"indicator": "RSI", "params": {"period": 14}, "operator": sell_operator, "threshold": sell_threshold},
        ]}),
    )


def test_evaluate_signals_returns_no_new_candle_when_already_processed(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    df = make_oscillating_df()
    buy_json, sell_json = _strategy_conditions()
    strategy_id = insert_live_strategy(
        dbm, buy_conditions_json=buy_json, sell_conditions_json=sell_json,
    )
    latest_candle_time = df["candle_time"].iloc[-1]
    dbm.update_live_strategy_last_candle(strategy_id, latest_candle_time.isoformat())
    monkeypatch.setattr(signal_engine, "get_candles", lambda market, timeframe, start, end: df)

    result = signal_engine.evaluate_signals(strategy_id, now=datetime.now(timezone.utc))

    assert result["new_candle"] is False


def test_evaluate_signals_records_buy_and_sell_signals_for_new_candle(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    df = make_oscillating_df()
    buy_json, sell_json = _strategy_conditions()
    strategy_id = insert_live_strategy(
        dbm, buy_conditions_json=buy_json, sell_conditions_json=sell_json,
    )
    monkeypatch.setattr(signal_engine, "get_candles", lambda market, timeframe, start, end: df)

    result = signal_engine.evaluate_signals(strategy_id, now=datetime.now(timezone.utc))

    assert result["new_candle"] is True
    assert result["buy_signal"] is True  # RSI > -1 항상 참
    assert result["sell_signal"] is True  # RSI > -1 항상 참

    conn = dbm._connect()
    try:
        rows = conn.execute(
            "SELECT signal_type FROM signals WHERE live_strategy_id=?", (strategy_id,)
        ).fetchall()
    finally:
        conn.close()
    assert {r[0] for r in rows} == {"buy", "sell"}

    updated = dbm.get_live_strategy(strategy_id)
    assert updated["last_processed_candle_time"] == df["candle_time"].iloc[-1].isoformat()


def test_evaluate_signals_pauses_strategy_when_condition_unknown(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    df = make_oscillating_df()
    buy_json = json.dumps({"type": "AND", "conditions": [
        {"indicator": "FUNDING_RATE", "params": {}, "operator": "<", "threshold": -0.01},
    ]})
    _, sell_json = _strategy_conditions()
    strategy_id = insert_live_strategy(
        dbm, status="running", buy_conditions_json=buy_json, sell_conditions_json=sell_json,
    )
    monkeypatch.setattr(signal_engine, "get_candles", lambda market, timeframe, start, end: df)
    monkeypatch.setattr(signal_engine, "fetch_live_funding_rate_value", lambda market, now=None: None)

    result = signal_engine.evaluate_signals(strategy_id, now=datetime.now(timezone.utc))

    assert result["buy_signal"] is None
    assert result["paused"] is True
    assert dbm.get_live_strategy(strategy_id)["status"] == "paused"


def test_evaluate_signals_resumes_paused_strategy_when_computable_and_not_circuit_tripped(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    df = make_oscillating_df()
    buy_json, sell_json = _strategy_conditions()
    strategy_id = insert_live_strategy(
        dbm, status="paused", buy_conditions_json=buy_json, sell_conditions_json=sell_json,
    )
    monkeypatch.setattr(signal_engine, "get_candles", lambda market, timeframe, start, end: df)

    result = signal_engine.evaluate_signals(strategy_id, now=datetime.now(timezone.utc))

    assert result["resumed"] is True
    assert dbm.get_live_strategy(strategy_id)["status"] == "running"


def test_evaluate_signals_does_not_resume_when_circuit_breaker_tripped_today(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    df = make_oscillating_df()
    buy_json, sell_json = _strategy_conditions()
    strategy_id = insert_live_strategy(
        dbm, status="paused", buy_conditions_json=buy_json, sell_conditions_json=sell_json,
    )
    dbm.upsert_circuit_breaker_state(
        strategy_id, today_kst(), 3, 1, "daily_loss_limit", datetime.now(timezone.utc).isoformat(),
    )
    monkeypatch.setattr(signal_engine, "get_candles", lambda market, timeframe, start, end: df)

    result = signal_engine.evaluate_signals(strategy_id, now=datetime.now(timezone.utc))

    assert result["resumed"] is False
    assert dbm.get_live_strategy(strategy_id)["status"] == "paused"
```

- [x] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_signal_engine.py -v -k evaluate_signals`
Expected: FAIL — `AttributeError: module 'trading.signal_engine' has no attribute 'evaluate_signals'`

- [x] **Step 3: `trading/signal_engine.py`에 구현 추가**

import 블록 맨 위를 아래로 교체(기존 내용 유지하며 필요한 줄만 추가):
```python
from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd

from engine.condition_tree import (
    POSITION_RELATIVE_INDICATORS,
    collect_blocks,
    eval_group_values,
    indicator_key,
    max_required_period,
    required_aux_markets,
)
from upbit_data_service import get_candles, timeframe_duration
from trading.live_indicators import (
    LIVE_INDICATOR_FACTORY,
    compute_korea_premium_value,
    fetch_live_binance_close,
    fetch_live_fear_greed_value,
    fetch_live_funding_rate_value,
)
from trading.position_manager import get_open_position
import trading.db as db
from trading.risk_manager import today_kst
```

(Task 5에서 추가했던 `from engine.condition_tree import indicator_key`와
`from trading.live_indicators import LIVE_INDICATOR_FACTORY`는 위 블록으로 흡수되므로
중복 줄이 남지 않도록 정리한다.)

파일 끝(`_position_context` 함수 뒤)에 추가:
```python


def _no_new_candle_result() -> dict:
    return {
        "new_candle": False, "candle_time": None,
        "buy_signal": None, "sell_signal": None,
        "paused": False, "resumed": False,
    }


def evaluate_signals(live_strategy_id: str, now: datetime | None = None) -> dict:
    """새 봉 마감을 감지하면 지표 계산 + 조건평가를 수행해 signals에 기록하고,
    live_strategies.status를 필요시 갱신한다. 새 봉이 아니면 즉시 조기 반환한다(daemon이
    폴링 주기마다 안전하게 반복 호출할 수 있는 멱등적 인터페이스, 설계 스펙)."""
    now = now or datetime.now(timezone.utc)

    strategy = db.get_live_strategy(live_strategy_id)
    if strategy is None:
        raise ValueError(f"전략을 찾을 수 없습니다: {live_strategy_id}")

    market = strategy["market"]
    timeframe = strategy["timeframe"]
    buy_conditions = json.loads(strategy["buy_conditions_json"])
    sell_conditions = json.loads(strategy["sell_conditions_json"])

    required_bars = max(max_required_period(buy_conditions), max_required_period(sell_conditions))

    df = _fetch_candles_with_warmup(market, timeframe, required_bars, now)
    if df.empty:
        return _no_new_candle_result()

    latest_candle_time = df["candle_time"].iloc[-1]
    last_processed = strategy["last_processed_candle_time"]
    if last_processed is not None and latest_candle_time <= pd.Timestamp(last_processed):
        return _no_new_candle_result()

    aux_markets = required_aux_markets(buy_conditions) | required_aux_markets(sell_conditions)
    if aux_markets:
        df = _merge_aux_markets(df, aux_markets, market, timeframe, required_bars, now)

    blocks = [
        b for b in collect_blocks(buy_conditions) + collect_blocks(sell_conditions)
        if b["indicator"] not in POSITION_RELATIVE_INDICATORS
    ]
    indicator_names = {b["indicator"] for b in blocks}
    b_group_names = indicator_names & {"FEAR_GREED_CMC", "FUNDING_RATE", "KOREA_PREMIUM"}
    if b_group_names:
        df = _populate_b_group_columns(df, market, timeframe, b_group_names, now)

    values = _compute_indicator_values(df, blocks)

    latest_close = df["close"].iloc[-1]
    position_return_pct, position_holding_bars = _position_context(
        live_strategy_id, latest_close, latest_candle_time, timeframe,
    )

    buy_result = eval_group_values(buy_conditions, values, position_return_pct, position_holding_bars)
    sell_result = eval_group_values(sell_conditions, values, position_return_pct, position_holding_bars)

    snapshot_json = json.dumps({k: (None if v != v else v) for k, v in values.items()})
    candle_time_str = latest_candle_time.isoformat()

    db.insert_signal(
        live_strategy_id, "buy", candle_time_str, snapshot_json,
        skip_reason="unknown" if buy_result is None else None,
    )
    db.insert_signal(
        live_strategy_id, "sell", candle_time_str, snapshot_json,
        skip_reason="unknown" if sell_result is None else None,
    )

    paused = False
    resumed = False
    if buy_result is None or sell_result is None:
        if strategy["status"] != "paused":
            db.update_live_strategy_status(live_strategy_id, "paused")
        paused = True
    elif strategy["status"] == "paused":
        cb_state = db.get_circuit_breaker_state(live_strategy_id)
        tripped_today = (
            cb_state is not None
            and cb_state["trading_date"] == today_kst()
            and cb_state["tripped"]
        )
        if not tripped_today:
            db.update_live_strategy_status(live_strategy_id, "running")
            resumed = True

    db.update_live_strategy_last_candle(live_strategy_id, candle_time_str)

    return {
        "new_candle": True,
        "candle_time": candle_time_str,
        "buy_signal": buy_result,
        "sell_signal": sell_result,
        "paused": paused,
        "resumed": resumed,
    }
```

- [x] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_signal_engine.py -v`
Expected: 전부 PASS(11 + 5 = 16개)

- [x] **Step 5: 커밋**

```bash
git add trading/signal_engine.py tests/test_signal_engine.py
git commit -m "feat: signal_engine에 evaluate_signals 전체 흐름(신호평가+기록+일시정지/재개) 추가"
```

---

### Task 7: 최종 통합 확인 + 전체 회귀

**Files:**
- Modify: `trading/signal_engine.py`(문서화만, 필요 시)

**Interfaces:**
- Consumes: 이 플랜의 모든 이전 태스크 산출물.
- Produces: 없음(검증 전용 태스크).

- [x] **Step 1: `engine.condition_tree` 외의 `engine/` 미의존 + `httpx`/`upbit_client` 미의존 확인**

Run:
```bash
python -c "
import ast
tree = ast.parse(open('trading/signal_engine.py', encoding='utf-8').read())
names = set()
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        names.update(a.name for a in node.names)
    elif isinstance(node, ast.ImportFrom) and node.module:
        names.add(node.module)
engine_imports = {n for n in names if n == 'engine' or n.startswith('engine.')}
assert engine_imports <= {'engine.condition_tree'}, f'engine.condition_tree 외의 engine import 발견: {engine_imports}'
assert 'httpx' not in names, 'httpx 직접 import 발견(네트워크는 upbit_data_service/live_indicators를 통해서만)'
assert 'trading.upbit_client' not in names, 'upbit_client 직접 import 금지'
print('OK:', sorted(names))
"
```
Expected: `engine.condition_tree`만 있는 `engine` 관련 import, `httpx`/`trading.upbit_client`
없음, 에러 없이 통과.

- [x] **Step 2: `LIVE_INDICATOR_FACTORY`가 실제로 A그룹+B그룹 양쪽 다 결합되는지 최종 확인**

Run:
```bash
python -c "
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import trading.db as db
db.DB_PATH = Path(tempfile.mkdtemp()) / 'trading.db'

from tests.trading_db_fixtures import insert_live_strategy
from tests.signal_fixtures import make_oscillating_df
import trading.signal_engine as signal_engine
import json

df = make_oscillating_df()
signal_engine.get_candles = lambda market, timeframe, start, end: df

buy_json = json.dumps({'type': 'AND', 'conditions': [
    {'indicator': 'RSI', 'params': {'period': 14}, 'operator': '>', 'threshold': -1},
]})
sell_json = json.dumps({'type': 'AND', 'conditions': [
    {'indicator': 'RSI', 'params': {'period': 14}, 'operator': '>', 'threshold': -1},
]})
strategy_id = insert_live_strategy(db, buy_conditions_json=buy_json, sell_conditions_json=sell_json)

result = signal_engine.evaluate_signals(strategy_id, now=datetime.now(timezone.utc))
print(result)
assert result['new_candle'] is True
assert result['buy_signal'] is True
assert result['sell_signal'] is True
print('OK: signal_engine end-to-end 흐름 정상 확인')
"
```
Expected: 에러 없이 `OK: signal_engine end-to-end 흐름 정상 확인` 출력.

- [x] **Step 3: 전체 테스트 스위트 실행(회귀 확인)**

Run: `python -m pytest -q`
Expected: 전부 PASS(⑤-1까지의 기존 505개 + 이 플랜의 신규 테스트 전부 포함).

- [x] **Step 4: 커밋**

이 태스크는 검증 전용이라 코드 변경이 없으면 커밋할 게 없다 — Step 1~3이 전부 통과하면
빈 diff이므로 커밋을 생략한다. 검증 중 실제 코드 수정이 필요했다면 그 수정을 커밋한다:
```bash
git add trading/signal_engine.py
git commit -m "fix: signal_engine 최종 통합 검증에서 발견된 문제 수정"
```

---

## Self-Review

**스펙 커버리지:**
- 설계 스펙의 결정1(A/B그룹 dispatch 통일) → Task 3~6에서 전부 `LIVE_INDICATOR_FACTORY`를
  동일하게 호출, df 준비 방식만 다름.
- 결정2(보조마켓 병합 ffill/bfill) → Task 3의 `_merge_aux_markets`.
- 결정3(워밍업 +5) → Task 3의 `_fetch_candles_with_warmup`.
- 결정4(`timeframe_duration` 공개) → Task 1.
- 결정5(신호 봉당 2행) → Task 6의 `evaluate_signals`.
- 결정6(판단불가→일시정지, 재개 전 서킷브레이커 확인) → Task 6, 테스트로 두 경로(트립됨/
  안됨) 모두 검증.
- 결정7(포지션 컨텍스트) → Task 5의 `_position_context`.
- "이 스펙에서 다루지 않는 것"(주문실행 결정, ticker 기반 손절/익절, `orders` CRUD) → 이
  플랜 전체에서 손대지 않음, Global Constraints에 명시.

**플레이스홀더 스캔:** 없음 — 모든 스텝에 완전한 코드가 있다.

**타입 일관성:** `evaluate_signals()`의 반환 dict 형태(`new_candle`/`candle_time`/
`buy_signal`/`sell_signal`/`paused`/`resumed`)는 설계 스펙의 함수 시그니처 절과 정확히
일치하며, Task 6의 모든 테스트가 이 6개 키를 일관되게 검증한다. `_compute_indicator_values`가
반환하는 `values` 딕셔너리의 키(`indicator_key(name, params)`)는 `eval_group_values()`가
기대하는 키 형식과 동일(`engine/condition_tree.py`의 기존 계약, 변경 없음).

---

## 다음 서브플랜 (이 문서 이후)

⑤-3 **주문실행** — `order_executor.py`가 이 서브플랜의 `evaluate_signals()` 결과와
서브플랜⑤-1의 `position_manager`, 서브플랜④의 `upbit_client`를 엮어 시장가/지정가/
지정가+타임아웃 3모드를 구현한다. `orders` CRUD도 여기서 추가되고, `signals.resulting_order_id`를
채우는 갱신 함수도 여기서 만든다. ⑤-4 **reconciler + daemon 메인루프** —
`reconciler.py`(수동개입 감지) + `daemon.py`(State Hydration, ticker 기반 손절/익절
실시간 평가, 위 4개 서브플랜의 모듈 전부 결합).
