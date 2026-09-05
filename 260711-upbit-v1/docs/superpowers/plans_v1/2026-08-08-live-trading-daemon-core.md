# 라이브 트레이딩 서브플랜⑤-4b — daemon.py(핵심 메인루프) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **워크트리를 만들지 말고 main 브랜치에서 직접 작업한다** (사용자 지시, [[upbit-v1-worktree-workflow-changed]]).

**Goal:** `trading/daemon.py`를 만들어, 승인된 라이브 전략들을 전략별 asyncio 태스크로
24/7 동시에 처리하는 상주 프로세스를 완성한다 — 봉타임 비례 폴링으로 신호평가/주문실행을
돌리고, 같은 태스크 안에서 순차적으로 reconciler(수동개입 감지)와 서킷브레이커 실제
트립 판정까지 실행한다.

**Architecture:** 설계 스펙 `docs/superpowers/specs_v1/2026-08-08-live-trading-daemon-core.md`를
그대로 구현한다. `trading/daemon.py`는 `trading.db` + `trading.signal_engine` +
`trading.order_executor` + `trading.reconciler` + `trading.risk_manager` +
`upbit_data_service`만 import한다 — `engine/`은 전혀 의존하지 않는다. 이 스펙 작성
중 발견한 실제 문제 2건(서킷브레이커 미호출, 캔들조회 API 무방비 상태)도 이 플랜에서
같이 메운다.

**Tech Stack:** Python, `asyncio`, `pytest`(+`pytest-asyncio`, `asyncio_mode = auto`).
새 의존성 없음.

## Global Constraints

- `trading/daemon.py`는 **하나의 파일로 유지**한다(다른 `trading/` 모듈과 동일 관례).
- `engine/`은 전혀 import하지 않는다.
- 전략별 폴링 주기: `clamp(timeframe_duration_seconds // 12, 5, 60)`초(설계 스펙 결정4).
- reconciler 재확인 주기: 20초(설계 스펙 결정3). 태스크셋 재조회 주기: 20초(결정2).
  NTP 체크 주기: 600초, 드리프트 임계치 500ms(결정10).
- 업비트 캔들(candle) 그룹 실제 한도는 **IP당 초당 10회**(2026-08-08 docs.upbit.com
  확인, 설계 스펙 결정6) — `_SyncTokenBucket(rate_per_sec=10)`로 반영.
- 커밋은 태스크 단위로 작게, 테스트가 통과한 뒤에만 한다.

---

## File Structure

- **Modify:** `trading/db.py` — `list_active_strategies()` 추가.
- **Modify:** `tests/test_trading_db.py` — 위 함수 테스트 추가.
- **Modify:** `upbit_data_service.py` — `_SyncTokenBucket`(+ `_CANDLE_BUCKET` 전역
  인스턴스, `_fetch_page()`에 배선), `get_server_time_offset_sec()` 추가.
- **Modify:** `tests/test_upbit_data_service.py` — 위 추가분 테스트.
- **Modify:** `trading/reconciler.py` — `_sync_pending_limit_orders` →
  `sync_pending_limit_orders`(rename, public화). 동작 불변.
- **Modify:** `tests/test_reconciler.py` — rename된 이름으로 3개 호출부 갱신.
- **Create:** `trading/daemon.py` — `_poll_interval_sec()`, `_run_strategy_loop()`,
  `_task_set_manager_loop()`, `_run_ntp_check_loop()`, `main()`.
- **Create:** `tests/test_daemon.py`.

---

### Task 1: `trading/db.py` — `list_active_strategies()`

**Files:**
- Modify: `trading/db.py`
- Modify: `tests/test_trading_db.py`

**Interfaces:**
- Consumes: `trading.db._connect()`(기존).
- Produces: `trading.db.list_active_strategies() -> list[dict]` — `status IN
  ('running', 'paused')`인 `live_strategies` 행 전부.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_trading_db.py` 파일 끝에 추가:
```python
def test_list_active_strategies_returns_only_running_and_paused(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    running_id = insert_live_strategy(db, status="running")
    paused_id = insert_live_strategy(db, status="paused")
    insert_live_strategy(db, status="draft")
    insert_live_strategy(db, status="approved")
    insert_live_strategy(db, status="stopped")

    active = db.list_active_strategies()

    assert {s["id"] for s in active} == {running_id, paused_id}
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_trading_db.py -v -k list_active_strategies`
Expected: FAIL — `AttributeError: module 'trading.db' has no attribute 'list_active_strategies'`

- [ ] **Step 3: `trading/db.py`에 구현 추가**

파일 끝에 추가:
```python


def list_active_strategies() -> list[dict]:
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM live_strategies WHERE status IN ('running', 'paused')"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
```

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_trading_db.py -v`
Expected: 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add trading/db.py tests/test_trading_db.py
git commit -m "feat: db.py에 list_active_strategies 추가"
```

---

### Task 2: `upbit_data_service.py` — `_SyncTokenBucket` + 캔들조회 rate limit 배선

**Files:**
- Modify: `upbit_data_service.py`
- Modify: `tests/test_upbit_data_service.py`

**Interfaces:**
- Consumes: `time.monotonic`/`time.sleep`(기존 import).
- Produces: `upbit_data_service._SyncTokenBucket(rate_per_sec, capacity=None, *,
  clock=time.monotonic, sleep=time.sleep)`(`.acquire() -> None`),
  `upbit_data_service._CANDLE_BUCKET`(전역 인스턴스, `rate_per_sec=10`).
  `_fetch_page()`가 매 재시도 시도 전에 `_CANDLE_BUCKET.acquire()`를 호출하도록 수정.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_upbit_data_service.py` 파일 끝에 추가:
```python
def _fake_clock_and_sleep():
    fake_time = [0.0]

    def clock() -> float:
        return fake_time[0]

    def sleep(seconds: float) -> None:
        fake_time[0] += seconds

    return fake_time, clock, sleep


def test_sync_token_bucket_allows_burst_up_to_capacity():
    fake_time, clock, sleep = _fake_clock_and_sleep()
    bucket = uds._SyncTokenBucket(rate_per_sec=2, capacity=2, clock=clock, sleep=sleep)
    bucket.acquire()
    bucket.acquire()
    assert fake_time[0] == 0.0


def test_sync_token_bucket_waits_when_capacity_exhausted():
    fake_time, clock, sleep = _fake_clock_and_sleep()
    bucket = uds._SyncTokenBucket(rate_per_sec=2, capacity=2, clock=clock, sleep=sleep)
    bucket.acquire()
    bucket.acquire()
    bucket.acquire()
    assert fake_time[0] == pytest.approx(0.5)


def test_sync_token_bucket_refills_over_time():
    fake_time, clock, sleep = _fake_clock_and_sleep()
    bucket = uds._SyncTokenBucket(rate_per_sec=1, capacity=1, clock=clock, sleep=sleep)
    bucket.acquire()
    fake_time[0] += 2.0
    bucket.acquire()
    assert fake_time[0] == 2.0


def test_candle_bucket_default_rate_is_ten_per_sec():
    assert uds._CANDLE_BUCKET._rate == 10


def test_fetch_page_acquires_candle_bucket_before_each_request(monkeypatch):
    calls = {"acquire": 0}

    class _FakeBucket:
        def acquire(self):
            calls["acquire"] += 1

    monkeypatch.setattr(uds, "_CANDLE_BUCKET", _FakeBucket())

    def handler(request):
        return httpx.Response(200, json=[])

    client = httpx.Client(transport=httpx.MockTransport(handler))

    uds._fetch_page(client, "https://api.upbit.com/v1/candles/days", "KRW-BTC", None)

    assert calls["acquire"] == 1
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_upbit_data_service.py -v -k "token_bucket or candle_bucket or acquires_candle_bucket"`
Expected: FAIL — `AttributeError: module 'upbit_data_service' has no attribute '_SyncTokenBucket'`

- [ ] **Step 3: `upbit_data_service.py`에 구현 추가**

import 블록을:
```python
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pandas as pd
```
에서:
```python
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pandas as pd
```
로 교체한다.

`_CANDLE_COLUMNS` 정의 뒤(`_endpoint_for_timeframe` 함수 앞)에 추가:
```python


class _SyncTokenBucket:
    """trading.upbit_client.TokenBucket과 동일한 토큰버킷 알고리즘의 동기 버전(설계
    스펙 결정6) — get_candles() 호출 체인이 전부 동기 함수라 asyncio 기반
    TokenBucket을 쓸 수 없다. clock/sleep을 주입할 수 있어 테스트에서 실제 대기 없이
    결정론적으로 검증 가능하다."""

    def __init__(
        self,
        rate_per_sec: float,
        capacity: float | None = None,
        *,
        clock=time.monotonic,
        sleep=time.sleep,
    ) -> None:
        self._rate = rate_per_sec
        self._capacity = capacity if capacity is not None else rate_per_sec
        self._tokens = self._capacity
        self._clock = clock
        self._sleep = sleep
        self._last = clock()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            while True:
                now = self._clock()
                elapsed = now - self._last
                self._last = now
                self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                wait_seconds = (1 - self._tokens) / self._rate
                self._sleep(wait_seconds)


_CANDLE_BUCKET = _SyncTokenBucket(rate_per_sec=10)  # 업비트 candle 그룹 실제 한도(IP 단위)
```

`_fetch_page()` 안의:
```python
    last_exc: Exception | None = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            resp = client.get(url, params=params)
```
를:
```python
    last_exc: Exception | None = None
    for attempt in range(RETRY_ATTEMPTS):
        _CANDLE_BUCKET.acquire()
        try:
            resp = client.get(url, params=params)
```
로 교체한다.

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_upbit_data_service.py -v`
Expected: 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add upbit_data_service.py tests/test_upbit_data_service.py
git commit -m "feat: 캔들조회 경로에 _SyncTokenBucket(업비트 candle 그룹 10req/s) 배선"
```

---

### Task 3: `upbit_data_service.py` — `get_server_time_offset_sec()`

**Files:**
- Modify: `upbit_data_service.py`
- Modify: `tests/test_upbit_data_service.py`

**Interfaces:**
- Consumes: `httpx.get`(기존, 이미 `get_krw_markets()`가 씀).
- Produces: `upbit_data_service.get_server_time_offset_sec() -> float`(로컬 UTC 시각 −
  서버 시각, 초 단위. 양수면 로컬이 빠름).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_upbit_data_service.py` 파일 끝에 추가:
```python
def test_get_server_time_offset_sec_computes_positive_when_local_is_behind(monkeypatch):
    class _FakeResponse:
        headers = {"Date": "Sat, 08 Aug 2026 00:00:00 GMT"}

        def raise_for_status(self):
            pass

    def _fake_get(url, params=None, timeout=None):
        assert "market/all" in url
        return _FakeResponse()

    monkeypatch.setattr(uds, "httpx", type("_FakeHttpx", (), {
        "get": staticmethod(_fake_get),
    }))
    monkeypatch.setattr(
        uds, "datetime",
        type("_FixedDatetime", (), {
            "now": staticmethod(lambda tz=None: datetime(2026, 8, 8, 0, 0, 5, tzinfo=timezone.utc))
        }),
    )

    offset = uds.get_server_time_offset_sec()

    assert offset == pytest.approx(5.0)
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_upbit_data_service.py -v -k get_server_time_offset`
Expected: FAIL — `AttributeError: module 'upbit_data_service' has no attribute 'get_server_time_offset_sec'`

- [ ] **Step 3: `upbit_data_service.py`에 구현 추가**

import 블록을(Task2에서 이미 `threading`을 추가한 상태 기준):
```python
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
```
에서:
```python
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
```
로 교체한다.

`get_krw_markets()` 함수 바로 앞에 추가:
```python
def get_server_time_offset_sec() -> float:
    """업비트 서버 응답의 Date 헤더와 로컬 UTC 시각의 차이(초)를 반환한다. 양수면 로컬
    시각이 서버보다 느리다는 뜻. 인증이 필요 없는 공개 엔드포인트(마켓 목록)를 재사용해
    가볍게 확인한다(설계 스펙 결정10)."""
    resp = httpx.get(f"{UPBIT_BASE_URL}/market/all", params={"isDetails": "false"}, timeout=10)
    resp.raise_for_status()
    server_time = parsedate_to_datetime(resp.headers["Date"])
    if server_time.tzinfo is None:
        server_time = server_time.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - server_time).total_seconds()


```

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_upbit_data_service.py -v`
Expected: 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add upbit_data_service.py tests/test_upbit_data_service.py
git commit -m "feat: get_server_time_offset_sec 추가(NTP 드리프트 체크용)"
```

---

### Task 4: `trading/reconciler.py` — `sync_pending_limit_orders` public화

**Files:**
- Modify: `trading/reconciler.py`
- Modify: `tests/test_reconciler.py`

**Interfaces:**
- Consumes: 없음(순수 rename, 동작 불변).
- Produces: `trading.reconciler.sync_pending_limit_orders(strategy: dict, *,
  client=None) -> list[dict]`(기존 `_sync_pending_limit_orders`와 완전히 동일한 동작).

- [ ] **Step 1: 기존 테스트를 새 이름 기준으로 갱신**

`tests/test_reconciler.py`에서 다음 3곳을:
```python
    synced = await reconciler._sync_pending_limit_orders(strategy)
```
(총 3번 등장 — `test_sync_pending_limit_orders_updates_filled_order`,
`test_sync_pending_limit_orders_skips_orders_still_waiting`,
`test_sync_pending_limit_orders_ignores_non_limit_wait_orders` 안에 각각 1번씩) 전부:
```python
    synced = await reconciler.sync_pending_limit_orders(strategy)
```
로 바꾼다(언더스코어만 제거, `replace_all`로 3곳 한 번에 처리 가능 — 이 문자열은 파일
안에 이 3곳 외에는 등장하지 않는다).

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_reconciler.py -v -k sync_pending_limit_orders`
Expected: FAIL — `AttributeError: module 'trading.reconciler' has no attribute 'sync_pending_limit_orders'`(3개 테스트 모두)

- [ ] **Step 3: `trading/reconciler.py` 수정**

```python
async def _sync_pending_limit_orders(
```
를:
```python
async def sync_pending_limit_orders(
```
로 교체(정의부, 1곳).

```python
    synced_orders = await _sync_pending_limit_orders(strategy, client=client)
```
(`hydrate_state()` 안)를:
```python
    synced_orders = await sync_pending_limit_orders(strategy, client=client)
```
로 교체.

```python
    _sync_pending_limit_orders 결과)이다 — 잔고 변화를 설명하는 데는 external_orders와
```
(`_reconcile_position`의 docstring 안)를:
```python
    sync_pending_limit_orders 결과)이다 — 잔고 변화를 설명하는 데는 external_orders와
```
로 교체.

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_reconciler.py -v`
Expected: 전부 PASS(30개, 회귀 없음 — 동작은 완전히 동일)

- [ ] **Step 5: 커밋**

```bash
git add trading/reconciler.py tests/test_reconciler.py
git commit -m "refactor: sync_pending_limit_orders public화(daemon.py 재사용 대비)"
```

---

### Task 5: `trading/daemon.py` — 모듈 뼈대 + `_poll_interval_sec()`

**Files:**
- Create: `trading/daemon.py`
- Create: `tests/test_daemon.py`

**Interfaces:**
- Consumes: `upbit_data_service.timeframe_duration`(기존).
- Produces: `trading.daemon._poll_interval_sec(timeframe: str) -> float`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_daemon.py`(신규 파일):
```python
import trading.daemon as daemon


def test_poll_interval_sec_scales_with_timeframe():
    assert daemon._poll_interval_sec("minutes1") == 5.0
    assert daemon._poll_interval_sec("minutes3") == 15.0
    assert daemon._poll_interval_sec("minutes5") == 25.0
    assert daemon._poll_interval_sec("minutes15") == 60.0  # 75초 -> 60초 상한
    assert daemon._poll_interval_sec("minutes60") == 60.0  # 300초 -> 60초 상한
    assert daemon._poll_interval_sec("minutes240") == 60.0
    assert daemon._poll_interval_sec("days") == 60.0
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_daemon.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'trading.daemon'`

- [ ] **Step 3: `trading/daemon.py` 구현**

```python
"""
trading/daemon.py

라이브 트레이딩 상주 프로세스 진입점(서브플랜⑤-4b). 승인된 전략(status IN
('running','paused'))을 전략별 asyncio 태스크로 동시에 처리한다 — 각 태스크는 봉타임에
비례한 주기로 signal_engine -> order_executor를 돌리고, 그 안에서 reconciler(수동개입
감지)와 서킷브레이커 판정까지 순차적으로 실행해 동시성 충돌을 원천 차단한다(설계 스펙
결정3). 실시간 손절/익절(ticker 기반)은 ⑤-4c 몫이라 여기 없다. trading.db +
trading.signal_engine + trading.order_executor + trading.reconciler +
trading.risk_manager + upbit_data_service만 의존. engine/ 미의존.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

import trading.db as db
import trading.order_executor as order_executor
import trading.reconciler as reconciler
import trading.risk_manager as risk_manager
import trading.signal_engine as signal_engine
import upbit_data_service

logger = logging.getLogger(__name__)

_TASK_REFRESH_INTERVAL_SEC = 20
_RECONCILE_INTERVAL_SEC = 20
_NTP_CHECK_INTERVAL_SEC = 600
_NTP_DRIFT_THRESHOLD_SEC = 0.5
_MIN_POLL_INTERVAL_SEC = 5.0
_MAX_POLL_INTERVAL_SEC = 60.0


def _poll_interval_sec(timeframe: str) -> float:
    """봉타임에 비례한 폴링 주기(설계 스펙 결정4). 1분봉=5초, 3분봉=15초, 15분봉
    이상은 전부 60초 상한."""
    duration_sec = upbit_data_service.timeframe_duration(timeframe).total_seconds()
    return max(_MIN_POLL_INTERVAL_SEC, min(_MAX_POLL_INTERVAL_SEC, duration_sec // 12))
```

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_daemon.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add trading/daemon.py tests/test_daemon.py
git commit -m "feat: daemon.py 뼈대 + 봉타임 비례 폴링주기(_poll_interval_sec) 추가"
```

---

### Task 6: `trading/daemon.py` — `_run_strategy_loop()`

**Files:**
- Modify: `trading/daemon.py`
- Modify: `tests/test_daemon.py`

**Interfaces:**
- Consumes: `trading.reconciler.hydrate_state`/`check_manual_intervention`/
  `sync_pending_limit_orders`(⑤-4a, Task4), `trading.signal_engine.evaluate_signals`
  (⑤-2), `trading.order_executor.handle_signal_result`(⑤-3),
  `trading.risk_manager.check_circuit_breaker`(⑤-1, 지금까지 아무 데도 안 불리던
  함수 — 설계 스펙 결정7), `trading.db.get_live_strategy`(기존).
- Produces: `trading.daemon._run_strategy_loop(strategy_id: str) -> None`(전략 하나를
  담당하는 무한루프. `status`가 `running`/`paused`가 아니게 되면 스스로 반환).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_daemon.py` 파일 끝에 추가:
```python
import json

import trading.db as db
import trading.order_executor as order_executor
import trading.reconciler as reconciler
import trading.risk_manager as risk_manager
import trading.signal_engine as signal_engine
from tests.trading_db_fixtures import insert_live_strategy


def _fresh_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "trading.db")
    return db


def _no_new_candle_result():
    return {"new_candle": False, "candle_time": None, "buy_signal": None,
            "sell_signal": None, "paused": False, "resumed": False}


def _stop_after_one_tick(dbm, strategy_id):
    """fake_sleep으로 주입 — sleep이 호출되는 시점(한 틱이 끝난 시점)에 상태를
    'stopped'로 바꿔 다음 루프 최상단 체크에서 자연스럽게 종료되게 한다."""
    calls = {"count": 0}

    async def fake_sleep(seconds):
        calls["count"] += 1
        dbm.update_live_strategy_status(strategy_id, "stopped")

    return calls, fake_sleep


async def test_run_strategy_loop_hydrates_state_once_then_processes_one_tick(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm, status="running", timeframe="minutes1")
    hydrate_calls = {"count": 0}

    async def fake_hydrate_state(strategy, *, client=None):
        hydrate_calls["count"] += 1
        return {"synced_wait_orders": 0, "baseline_captured": True}

    monkeypatch.setattr(reconciler, "hydrate_state", fake_hydrate_state)
    monkeypatch.setattr(signal_engine, "evaluate_signals", lambda sid, now=None: _no_new_candle_result())
    calls, fake_sleep = _stop_after_one_tick(dbm, strategy_id)
    monkeypatch.setattr(daemon.asyncio, "sleep", fake_sleep)

    await daemon._run_strategy_loop(strategy_id)

    assert hydrate_calls["count"] == 1
    assert calls["count"] == 1


async def test_run_strategy_loop_calls_handle_signal_result_on_new_candle(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm, status="running", timeframe="minutes1")
    handle_calls = {"count": 0}

    async def fake_hydrate_state(strategy, *, client=None):
        return {"synced_wait_orders": 0, "baseline_captured": True}

    def fake_evaluate_signals(sid, now=None):
        return {"new_candle": True, "candle_time": "2026-08-08T00:00:00+00:00",
                "buy_signal": False, "sell_signal": False,
                "buy_signal_id": "b1", "sell_signal_id": "s1",
                "latest_close": 50000000.0, "paused": False, "resumed": False}

    async def fake_handle_signal_result(sid, result, *, dry_run=False):
        handle_calls["count"] += 1
        return {"buy_action": None, "sell_action": None, "buy_order_id": None, "sell_order_id": None}

    monkeypatch.setattr(reconciler, "hydrate_state", fake_hydrate_state)
    monkeypatch.setattr(signal_engine, "evaluate_signals", fake_evaluate_signals)
    monkeypatch.setattr(order_executor, "handle_signal_result", fake_handle_signal_result)
    calls, fake_sleep = _stop_after_one_tick(dbm, strategy_id)
    monkeypatch.setattr(daemon.asyncio, "sleep", fake_sleep)

    await daemon._run_strategy_loop(strategy_id)

    assert handle_calls["count"] == 1


async def test_run_strategy_loop_checks_circuit_breaker_after_exit(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm, status="running", timeframe="minutes1")
    cb_calls = {"count": 0, "risk_config": None}

    async def fake_hydrate_state(strategy, *, client=None):
        return {"synced_wait_orders": 0, "baseline_captured": True}

    def fake_evaluate_signals(sid, now=None):
        return {"new_candle": True, "candle_time": "2026-08-08T00:00:00+00:00",
                "buy_signal": False, "sell_signal": True,
                "buy_signal_id": "b1", "sell_signal_id": "s1",
                "latest_close": 50000000.0, "paused": False, "resumed": False}

    async def fake_handle_signal_result(sid, result, *, dry_run=False):
        return {"buy_action": None, "sell_action": "exited", "buy_order_id": None, "sell_order_id": "o1"}

    def fake_check_circuit_breaker(sid, risk_config):
        cb_calls["count"] += 1
        cb_calls["risk_config"] = risk_config
        return False

    monkeypatch.setattr(reconciler, "hydrate_state", fake_hydrate_state)
    monkeypatch.setattr(signal_engine, "evaluate_signals", fake_evaluate_signals)
    monkeypatch.setattr(order_executor, "handle_signal_result", fake_handle_signal_result)
    monkeypatch.setattr(risk_manager, "check_circuit_breaker", fake_check_circuit_breaker)
    calls, fake_sleep = _stop_after_one_tick(dbm, strategy_id)
    monkeypatch.setattr(daemon.asyncio, "sleep", fake_sleep)

    await daemon._run_strategy_loop(strategy_id)

    assert cb_calls["count"] == 1
    assert cb_calls["risk_config"] is not None


async def test_run_strategy_loop_skips_circuit_breaker_when_sell_not_exited(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm, status="running", timeframe="minutes1")
    cb_calls = {"count": 0}

    async def fake_hydrate_state(strategy, *, client=None):
        return {"synced_wait_orders": 0, "baseline_captured": True}

    monkeypatch.setattr(reconciler, "hydrate_state", fake_hydrate_state)
    monkeypatch.setattr(signal_engine, "evaluate_signals", lambda sid, now=None: _no_new_candle_result())
    monkeypatch.setattr(risk_manager, "check_circuit_breaker", lambda sid, cfg: cb_calls.__setitem__("count", cb_calls["count"] + 1))
    calls, fake_sleep = _stop_after_one_tick(dbm, strategy_id)
    monkeypatch.setattr(daemon.asyncio, "sleep", fake_sleep)

    await daemon._run_strategy_loop(strategy_id)

    assert cb_calls["count"] == 0


async def test_run_strategy_loop_reconciles_when_interval_elapsed(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm, status="running", timeframe="minutes1")
    reconcile_calls = {"manual": 0, "sync": 0}

    async def fake_hydrate_state(strategy, *, client=None):
        return {"synced_wait_orders": 0, "baseline_captured": True}

    async def fake_check_manual_intervention(strategy, *, client=None):
        reconcile_calls["manual"] += 1
        return {"balance_mismatch": False, "action": "none", "paused": False}

    async def fake_sync_pending_limit_orders(strategy, *, client=None):
        reconcile_calls["sync"] += 1
        return []

    monkeypatch.setattr(reconciler, "hydrate_state", fake_hydrate_state)
    monkeypatch.setattr(reconciler, "check_manual_intervention", fake_check_manual_intervention)
    monkeypatch.setattr(reconciler, "sync_pending_limit_orders", fake_sync_pending_limit_orders)
    monkeypatch.setattr(signal_engine, "evaluate_signals", lambda sid, now=None: _no_new_candle_result())
    # last_reconcile을 과거로 못박아 이번 틱에서 무조건 재확인 주기가 지난 것으로 만든다.
    monkeypatch.setattr(daemon.time, "monotonic", lambda: 10_000.0)
    calls, fake_sleep = _stop_after_one_tick(dbm, strategy_id)
    monkeypatch.setattr(daemon.asyncio, "sleep", fake_sleep)

    await daemon._run_strategy_loop(strategy_id)

    assert reconcile_calls["manual"] == 1
    assert reconcile_calls["sync"] == 1


async def test_run_strategy_loop_returns_immediately_when_strategy_missing(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)

    await daemon._run_strategy_loop("no-such-strategy-id")
    # 예외 없이 조용히 반환하면 성공(hydrate_state 등 아무것도 호출되지 않음)


async def test_run_strategy_loop_logs_and_continues_on_exception(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm, status="running", timeframe="minutes1")

    async def fake_hydrate_state(strategy, *, client=None):
        return {"synced_wait_orders": 0, "baseline_captured": True}

    def fake_evaluate_signals(sid, now=None):
        raise RuntimeError("네트워크 순간 장애")

    monkeypatch.setattr(reconciler, "hydrate_state", fake_hydrate_state)
    monkeypatch.setattr(signal_engine, "evaluate_signals", fake_evaluate_signals)
    calls, fake_sleep = _stop_after_one_tick(dbm, strategy_id)
    monkeypatch.setattr(daemon.asyncio, "sleep", fake_sleep)

    await daemon._run_strategy_loop(strategy_id)  # 예외가 밖으로 전파되면 테스트 실패

    assert calls["count"] == 1
```

(`daemon`은 `import trading.daemon as daemon`으로 이미 Task5에서 import돼 있다.)

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_daemon.py -v -k run_strategy_loop`
Expected: FAIL — `AttributeError: module 'trading.daemon' has no attribute '_run_strategy_loop'`

- [ ] **Step 3: `trading/daemon.py`에 구현 추가**

파일 끝에 추가:
```python


async def _run_strategy_loop(strategy_id: str) -> None:
    """전략 하나를 담당하는 유일한 태스크(설계 스펙 결정3). hydrate_state() 1회 →
    무한루프(새 봉 처리 → 매도체결 시 서킷브레이커 판정 → 20초마다 reconciler 2종
    호출 → 봉타임 비례 sleep). status가 running/paused가 아니게 되면 스스로 종료한다.
    예외는 로그만 남기고 다음 틱에 재시도(결정8)."""
    strategy = db.get_live_strategy(strategy_id)
    if strategy is None:
        return
    await reconciler.hydrate_state(strategy)
    last_reconcile = time.monotonic()

    while True:
        strategy = db.get_live_strategy(strategy_id)
        if strategy is None or strategy["status"] not in ("running", "paused"):
            return

        try:
            result = await asyncio.to_thread(signal_engine.evaluate_signals, strategy_id)
            if result["new_candle"]:
                action_result = await order_executor.handle_signal_result(strategy_id, result)
                if action_result["sell_action"] == "exited":
                    risk_config = json.loads(strategy["risk_config_json"])
                    risk_manager.check_circuit_breaker(strategy_id, risk_config)

            now = time.monotonic()
            if now - last_reconcile >= _RECONCILE_INTERVAL_SEC:
                strategy = db.get_live_strategy(strategy_id) or strategy
                await reconciler.check_manual_intervention(strategy)
                await reconciler.sync_pending_limit_orders(strategy)
                last_reconcile = now
        except Exception:
            logger.exception("전략 처리 중 예외 발생: strategy_id=%s", strategy_id)

        await asyncio.sleep(_poll_interval_sec(strategy["timeframe"]))
```

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_daemon.py -v`
Expected: 전부 PASS(1 + 8 = 9개)

- [ ] **Step 5: 커밋**

```bash
git add trading/daemon.py tests/test_daemon.py
git commit -m "feat: daemon에 _run_strategy_loop(전략별 처리 루프) 추가"
```

---

### Task 7: `trading/daemon.py` — `_task_set_manager_loop()`

**Files:**
- Modify: `trading/daemon.py`
- Modify: `tests/test_daemon.py`

**Interfaces:**
- Consumes: `trading.db.list_active_strategies`(Task1), `_run_strategy_loop`(Task6).
- Produces: `trading.daemon._task_set_manager_loop() -> None`(20초마다 전략 집합을
  다시 조회해 태스크를 생성/취소).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_daemon.py` 파일 끝에 추가:
```python
async def test_task_set_manager_creates_task_for_new_strategy(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm, status="running")
    started = {"ids": []}

    async def fake_run_strategy_loop(sid):
        started["ids"].append(sid)
        await asyncio.sleep(3600)  # 태스크가 살아있는 채로 유지(취소되기 전까지)

    monkeypatch.setattr(daemon, "_run_strategy_loop", fake_run_strategy_loop)

    async def stop_after_one_scan(seconds):
        raise asyncio.CancelledError()

    monkeypatch.setattr(daemon.asyncio, "sleep", stop_after_one_scan)

    with pytest.raises(asyncio.CancelledError):
        await daemon._task_set_manager_loop()
    await asyncio.sleep(0)  # 생성된 태스크가 실제로 스케줄되게 한 틱 양보

    assert started["ids"] == [strategy_id]


async def test_task_set_manager_cancels_task_for_removed_strategy(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm, status="running")
    cancelled = {"count": 0}

    async def fake_run_strategy_loop(sid):
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled["count"] += 1
            raise

    monkeypatch.setattr(daemon, "_run_strategy_loop", fake_run_strategy_loop)

    scan_count = {"n": 0}

    async def fake_sleep(seconds):
        scan_count["n"] += 1
        if scan_count["n"] == 1:
            dbm.update_live_strategy_status(strategy_id, "stopped")
        else:
            raise asyncio.CancelledError()

    monkeypatch.setattr(daemon.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await daemon._task_set_manager_loop()
    await asyncio.sleep(0)

    assert cancelled["count"] == 1
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_daemon.py -v -k task_set_manager`
Expected: FAIL — `AttributeError: module 'trading.daemon' has no attribute '_task_set_manager_loop'`

- [ ] **Step 3: `trading/daemon.py`에 구현 추가**

파일 끝에 추가:
```python


async def _task_set_manager_loop() -> None:
    """20초마다 db.list_active_strategies()를 다시 조회해 태스크 집합을 갱신한다
    (설계 스펙 결정2). 새 전략 -> create_task(_run_strategy_loop), 더 이상 대상
    아님 -> task.cancel(). 재시작 없이 새로 승인된 전략을 자동으로 픽업한다."""
    tasks: dict[str, asyncio.Task] = {}
    while True:
        active_ids = {s["id"] for s in db.list_active_strategies()}

        for strategy_id in active_ids:
            if strategy_id not in tasks or tasks[strategy_id].done():
                tasks[strategy_id] = asyncio.create_task(_run_strategy_loop(strategy_id))

        for strategy_id in list(tasks):
            if strategy_id not in active_ids:
                tasks[strategy_id].cancel()
                del tasks[strategy_id]

        await asyncio.sleep(_TASK_REFRESH_INTERVAL_SEC)
```

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_daemon.py -v`
Expected: 전부 PASS(9 + 2 = 11개)

- [ ] **Step 5: 커밋**

```bash
git add trading/daemon.py tests/test_daemon.py
git commit -m "feat: daemon에 _task_set_manager_loop(전략 동적 픽업/정리) 추가"
```

---

### Task 8: `trading/daemon.py` — `_run_ntp_check_loop()` + `main()`

**Files:**
- Modify: `trading/daemon.py`
- Modify: `tests/test_daemon.py`

**Interfaces:**
- Consumes: `upbit_data_service.get_server_time_offset_sec`(Task3),
  `_task_set_manager_loop`(Task7).
- Produces: `trading.daemon._run_ntp_check_loop() -> None`,
  `trading.daemon.main() -> None`(진입점).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_daemon.py` 파일 끝에 추가:
```python
async def test_ntp_check_loop_logs_warning_when_drift_exceeds_threshold(monkeypatch, caplog):
    monkeypatch.setattr(upbit_data_service, "get_server_time_offset_sec", lambda: 1.2)

    async def stop_after_one_check(seconds):
        raise asyncio.CancelledError()

    monkeypatch.setattr(daemon.asyncio, "sleep", stop_after_one_check)

    with caplog.at_level("WARNING"), pytest.raises(asyncio.CancelledError):
        await daemon._run_ntp_check_loop()

    assert any("1.2" in r.message or "1.20" in r.message for r in caplog.records)


async def test_ntp_check_loop_silent_when_drift_within_threshold(monkeypatch, caplog):
    monkeypatch.setattr(upbit_data_service, "get_server_time_offset_sec", lambda: 0.05)

    async def stop_after_one_check(seconds):
        raise asyncio.CancelledError()

    monkeypatch.setattr(daemon.asyncio, "sleep", stop_after_one_check)

    with caplog.at_level("WARNING"), pytest.raises(asyncio.CancelledError):
        await daemon._run_ntp_check_loop()

    assert len(caplog.records) == 0


async def test_ntp_check_loop_survives_exception(monkeypatch):
    def fake_offset():
        raise RuntimeError("네트워크 순간 장애")

    monkeypatch.setattr(upbit_data_service, "get_server_time_offset_sec", fake_offset)

    async def stop_after_one_check(seconds):
        raise asyncio.CancelledError()

    monkeypatch.setattr(daemon.asyncio, "sleep", stop_after_one_check)

    with pytest.raises(asyncio.CancelledError):
        await daemon._run_ntp_check_loop()  # RuntimeError가 밖으로 새면 테스트 실패
```

(`upbit_data_service`는 `tests/test_daemon.py` 상단에 `import upbit_data_service`로
추가해야 한다 — Task5~7에서는 필요 없었다.)

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_daemon.py -v -k ntp_check`
Expected: FAIL — `AttributeError: module 'trading.daemon' has no attribute '_run_ntp_check_loop'`

- [ ] **Step 3: `trading/daemon.py`에 구현 추가**

파일 끝에 추가:
```python


async def _run_ntp_check_loop() -> None:
    """시작 직후 1회 + 10분마다 로컬 시각과 업비트 서버 시각의 오차를 확인한다
    (설계 스펙 결정10). 임계치(500ms) 초과 시 로그만 남긴다 — 자동조치는 2단계
    텔레그램 이후."""
    while True:
        try:
            offset = await asyncio.to_thread(upbit_data_service.get_server_time_offset_sec)
            if abs(offset) > _NTP_DRIFT_THRESHOLD_SEC:
                logger.warning("로컬 시각이 업비트 서버와 %.3f초 어긋남", offset)
        except Exception:
            logger.exception("NTP 드리프트 체크 실패")
        await asyncio.sleep(_NTP_CHECK_INTERVAL_SEC)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    await asyncio.gather(_task_set_manager_loop(), _run_ntp_check_loop())


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_daemon.py -v`
Expected: 전부 PASS(11 + 3 = 14개)

- [ ] **Step 5: 커밋**

```bash
git add trading/daemon.py tests/test_daemon.py
git commit -m "feat: daemon에 _run_ntp_check_loop + main() 진입점 추가"
```

---

### Task 9: 최종 통합 확인 + `engine/` 미의존 검증 + 전체 회귀

**Files:**
- Modify: `trading/daemon.py`(문서화만, 필요 시)

**Interfaces:**
- Consumes: 이 플랜의 모든 이전 태스크 산출물.
- Produces: 없음(검증 전용 태스크).

- [ ] **Step 1: `engine/` 미의존 확인**

Run:
```bash
python -c "
import ast
tree = ast.parse(open('trading/daemon.py', encoding='utf-8').read())
names = set()
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        names.update(a.name for a in node.names)
    elif isinstance(node, ast.ImportFrom) and node.module:
        names.add(node.module)
engine_imports = {n for n in names if n == 'engine' or n.startswith('engine.')}
assert not engine_imports, f'engine 의존 발견: {engine_imports}'
print('OK:', sorted(names))
"
```
Expected: `engine` 관련 import 없음. `sorted(names)`에 `['__future__', 'asyncio',
'json', 'logging', 'time', 'trading.db', 'trading.order_executor',
'trading.reconciler', 'trading.risk_manager', 'trading.signal_engine',
'upbit_data_service']`가 출력됨.

- [ ] **Step 2: 짧게 bounded 통합 확인**(전략 1개를 만들고 `_run_strategy_loop`가
  실제로 `hydrate_state`부터 `evaluate_signals`까지 엮여서 도는지 — 외부 API는
  `upbit_client`/`upbit_data_service` 둘 다 monkeypatch)

Run:
```bash
python -c "
import asyncio
import json
import tempfile
from pathlib import Path

import trading.db as db
db.DB_PATH = Path(tempfile.mkdtemp()) / 'trading.db'

from tests.trading_db_fixtures import insert_live_strategy
import trading.reconciler as reconciler
import trading.signal_engine as signal_engine
import trading.daemon as daemon

risk_config = json.dumps({
    'order_execution_mode': 'market', 'max_position_per_market': 1000000.0,
    'manual_intervention_policy': 'all_stop',
})
strategy_id = insert_live_strategy(
    db, market='KRW-BTC', timeframe='minutes1', current_capital=500000.0,
    risk_config_json=risk_config,
)

async def fake_hydrate_state(strategy, *, client=None):
    return {'synced_wait_orders': 0, 'baseline_captured': True}
reconciler.hydrate_state = fake_hydrate_state

def fake_evaluate_signals(sid, now=None):
    return {'new_candle': False, 'candle_time': None, 'buy_signal': None,
            'sell_signal': None, 'paused': False, 'resumed': False}
signal_engine.evaluate_signals = fake_evaluate_signals

calls = {'n': 0}
async def fake_sleep(seconds):
    calls['n'] += 1
    db.update_live_strategy_status(strategy_id, 'stopped')
daemon.asyncio.sleep = fake_sleep

asyncio.run(daemon._run_strategy_loop(strategy_id))
assert calls['n'] == 1
print('OK: daemon._run_strategy_loop 엔드투엔드 흐름(hydrate_state -> evaluate_signals -> sleep) 정상 확인')
"
```
Expected: 에러 없이 `OK: ...` 출력.

- [ ] **Step 3: 전체 테스트 스위트 실행(회귀 확인)**

Run: `python -m pytest -q`
Expected: 전부 PASS(⑤-4a까지의 기존 660개 + 이 플랜의 신규 테스트 전부 포함, 회귀 없음).

- [ ] **Step 4: 커밋**

이 태스크는 검증 전용이라 코드 변경이 없으면 커밋할 게 없다 — Step 1~3이 전부 통과하면
빈 diff이므로 커밋을 생략한다. 검증 중 실제 코드 수정이 필요했다면 그 수정을 커밋한다:
```bash
git add trading/daemon.py
git commit -m "fix: daemon 최종 통합 검증에서 발견된 문제 수정"
```

---

## Self-Review

**스펙 커버리지:**
- 결정1(⑤-4b/⑤-4c 분리) → 이 플랜은 ⑤-4b만 다룸, ticker/실시간 손절익절 코드 없음(파일
  구조에 `upbit_ws.py` 관련 변경 없음으로 확인 가능).
- 결정2(태스크셋 동적 관리) → Task7 `_task_set_manager_loop`.
- 결정3(전략별 루프 하나로 통합) → Task6 `_run_strategy_loop`가 캔들처리·서킷브레이커·
  reconciler를 전부 한 코루틴 안에서 순차 실행.
- 결정4(봉타임 비례 폴링) → Task5 `_poll_interval_sec`.
- 결정5(to_thread) → Task6의 `asyncio.to_thread(signal_engine.evaluate_signals, ...)`.
- 결정6(캔들조회 rate limit) → Task2 `_SyncTokenBucket`+`_CANDLE_BUCKET`.
- 결정7(서킷브레이커 실제 호출) → Task6의 `sell_action == "exited"` 분기.
- 결정8(예외 격리) → Task6의 `try/except Exception`.
- 결정9(reconciler public화) → Task4.
- 결정10(NTP 체크) → Task3(`get_server_time_offset_sec`) + Task8
  (`_run_ntp_check_loop`).

**플레이스홀더 스캔:** 없음 — 모든 스텝에 완전한 코드가 있다.

**타입 일관성:** `_run_strategy_loop`/`_task_set_manager_loop`/`_run_ntp_check_loop`
전부 `-> None`(무한루프이므로 정상 반환값 없음, `_run_strategy_loop`만 조건부로
`return`). `reconciler.hydrate_state`/`check_manual_intervention`/
`sync_pending_limit_orders`가 전부 `strategy: dict` 첫 인자를 받는 기존 시그니처를
Task6이 그대로 재사용(⑤-4a 산출물과 어긋남 없음). `order_executor.handle_signal_result`
반환 dict의 `sell_action` 키(`"exited"|"slippage_exceeded"|"pending"|None`)를 Task6이
정확한 값(`"exited"`)으로 분기.

**스코프 경계:** ticker 구독·실시간 손절익절은 ⑤-4c로, 승인/제어 UI는 ⑥으로 명확히
넘겼다 — daemon.py는 `status` 컬럼을 읽기만 하므로 ⑥이 나중에 컬럼에 쓰기만 하면
daemon 쪽 변경이 필요 없다.
