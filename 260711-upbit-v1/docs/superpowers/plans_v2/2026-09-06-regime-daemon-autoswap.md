# daemon 장세 자동 스왑 루프 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `regime_strategy_library`(3단계 완료)와 `replace_live_strategy_strategy()`(기존 API)를 잇는 daemon 자동 스왑 루프를 만들어, `auto_swap_enabled`가 켜진 라이브 전략이 코인의 현재 확정 장세(1시간봉 ADX, 3봉 연속 확정)에 맞는 라이브러리 매핑으로 자동 교체되게 한다.

**Architecture:** `trading/regime_autoswap.py`(신규)가 장세 판정+교체 결정 로직을 담당하고, `daemon.py`는 10분마다 그 진입점(`process_autoswap_tick()`)만 호출하는 얇은 루프를 추가한다. 새 컬럼 2개(`auto_swap_enabled`, `active_regime`)를 `live_strategies`에, 새 로그 테이블(`regime_swap_log`)을 추가한다. 기존 "전략 교체" 엔드포인트도 수정해 자동 스위치가 켜진 전략에 수동 개입이 일어나면 `active_regime`을 그 시점 장세로 stamp해, automation이 다음 실제 장세변화 전까지 되돌리지 않게 한다. 프론트엔드는 라이브 전략 카드에 토글+배지+이력을 추가한다.

**Tech Stack:** Python(FastAPI, sqlite3, pandas 기반 `engine.regime_adx`), TypeScript/React(Next.js, shadcn/ui)

## Global Constraints

- 설계 스펙: `docs/superpowers/specs_v2/2026-09-06-regime-daemon-autoswap-design.md` (이 플랜의 모든 세부사항은 이 스펙에서 파생됨)
- 장세 판정은 항상 1시간봉(`minutes60`) 고정 — 라이브 전략 자신의 거래 시간봉과 무관
- 확정 규칙: 최근 3봉이 전부 같은 라벨이어야 확정, 아니면(불일치/데이터부족/미분류 전부 포함) "기본"으로 폴백 — `determine_target_regime()`은 항상 `"하락"`/`"횡보"`/`"상승"`/`"기본"` 중 하나를 반환하고 절대 `None`을 반환하지 않는다
- daemon 루프 주기는 10분(`_AUTOSWAP_CHECK_INTERVAL_SEC = 600`)
- `auto_swap_enabled` 기본값은 0(꺼짐/수동) — 기존/신규 라이브 전략 모두
- `engine.regime_adx`(순수 pandas, backtrader 미사용)는 daemon.py의 "engine/ 미의존" 원칙의 예외로 직접 import 허용(사용자 승인)
- 이 프로젝트는 개발 단계 무마이그레이션 정책이다 — 새 컬럼/테이블은 `trading/db.py`의 `_SCHEMA` 문자열에 직접 추가하고, 기존 `data/trading.db` 파일이 있다면 삭제 후 재생성해야 한다(다른 dev 컬럼 추가 때와 동일 관례)
- 테스트는 전부 `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest ...`로 실행(Windows 콘솔 인코딩 이슈 회피, 기존 관례)
- 프론트엔드는 자동화 테스트가 없다(이 프로젝트 관례) — Task 5는 webapp-testing으로 브라우저 수동 검증

---

### Task 1: DB 스키마 + CRUD 함수 (`trading/db.py`)

**Files:**
- Modify: `trading/db.py:17-27`(TABLE_NAMES), `trading/db.py:32-51`(live_strategies 스키마), `trading/db.py:149-159`(regime_strategy_library 다음에 신규 테이블), `trading/db.py:918-927`(신규 함수 4개를 `list_regime_strategy_mappings()` 다음, `list_active_strategies()` 이전에 추가)
- Test: `tests/test_trading_db.py`(1498번째 줄 이후에 추가)

**Interfaces:**
- Consumes: 없음(순수 DB 레이어)
- Produces: `set_auto_swap_enabled(strategy_id: str, enabled: bool) -> bool`, `set_active_regime(strategy_id: str, regime: str | None) -> None`, `insert_regime_swap_log(live_strategy_id: str, market: str, event: str, from_regime: str | None, to_regime: str, detail: str | None = None) -> str`, `list_regime_swap_log(live_strategy_id: str, limit: int = 50) -> list[dict]` — Task 2/4에서 사용

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_trading_db.py` 파일 끝(1497번째 줄, `test_list_regime_strategy_mappings_returns_empty_list_when_none` 다음)에 추가:

```python
def test_set_auto_swap_enabled_updates_flag(tmp_path, monkeypatch):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="running")

    result = db.set_auto_swap_enabled(strategy_id, True)

    assert result is True
    assert db.get_live_strategy(strategy_id)["auto_swap_enabled"] == 1


def test_set_auto_swap_enabled_returns_false_for_missing_strategy(tmp_path, monkeypatch):
    db = _fresh_db(monkeypatch, tmp_path)

    result = db.set_auto_swap_enabled("does-not-exist", True)

    assert result is False


def test_new_live_strategy_defaults_auto_swap_disabled_and_no_active_regime(tmp_path, monkeypatch):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="running")

    strategy = db.get_live_strategy(strategy_id)

    assert strategy["auto_swap_enabled"] == 0
    assert strategy["active_regime"] is None


def test_set_active_regime_updates_field(tmp_path, monkeypatch):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="running")

    db.set_active_regime(strategy_id, "상승")

    assert db.get_live_strategy(strategy_id)["active_regime"] == "상승"


def test_set_active_regime_accepts_none(tmp_path, monkeypatch):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="running")
    db.set_active_regime(strategy_id, "상승")

    db.set_active_regime(strategy_id, None)

    assert db.get_live_strategy(strategy_id)["active_regime"] is None


def test_insert_and_list_regime_swap_log(tmp_path, monkeypatch):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="running")

    log_id = db.insert_regime_swap_log(
        strategy_id, "KRW-BTC", "swap_success", "하락", "상승", detail="source_run_id=run-1",
    )

    rows = db.list_regime_swap_log(strategy_id)
    assert len(rows) == 1
    assert rows[0]["id"] == log_id
    assert rows[0]["market"] == "KRW-BTC"
    assert rows[0]["event"] == "swap_success"
    assert rows[0]["from_regime"] == "하락"
    assert rows[0]["to_regime"] == "상승"
    assert rows[0]["detail"] == "source_run_id=run-1"


def test_list_regime_swap_log_orders_newest_first(tmp_path, monkeypatch):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="running")
    conn = db._connect()
    try:
        conn.execute(
            "INSERT INTO regime_swap_log (id, live_strategy_id, market, occurred_at, event, from_regime, to_regime) "
            "VALUES ('log-old', ?, 'KRW-BTC', '2026-01-01T00:00:00', 'swap_success', NULL, '상승')",
            (strategy_id,),
        )
        conn.execute(
            "INSERT INTO regime_swap_log (id, live_strategy_id, market, occurred_at, event, from_regime, to_regime) "
            "VALUES ('log-new', ?, 'KRW-BTC', '2026-01-02T00:00:00', 'swap_success', '상승', '하락')",
            (strategy_id,),
        )
        conn.commit()
    finally:
        conn.close()

    rows = db.list_regime_swap_log(strategy_id)

    assert [r["id"] for r in rows] == ["log-new", "log-old"]


def test_list_regime_swap_log_returns_empty_for_strategy_with_no_logs(tmp_path, monkeypatch):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="running")

    assert db.list_regime_swap_log(strategy_id) == []


def test_connect_creates_all_tables_includes_regime_swap_log(tmp_path, monkeypatch):
    """test_connect_creates_all_tables가 이미 TABLE_NAMES 전체를 검증하지만, 이
    테이블을 빠뜨리기 쉬운 실수(TABLE_NAMES에는 추가했는데 _SCHEMA에는 빠뜨림, 또는
    반대)를 이 파일을 읽는 사람이 바로 알아볼 수 있게 명시적으로 하나 더 남긴다."""
    db = _fresh_db(monkeypatch, tmp_path)
    conn = db._connect()
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='regime_swap_log'"
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_trading_db.py -v -k "auto_swap or active_regime or regime_swap_log"`
Expected: FAIL — `AttributeError: module 'trading.db' has no attribute 'set_auto_swap_enabled'`(또는 동일 계열의 AttributeError/KeyError)

- [ ] **Step 3: 최소 구현 작성**

`trading/db.py:17-27`(TABLE_NAMES)을 다음으로 교체:

```python
TABLE_NAMES = (
    "live_strategies",
    "positions",
    "orders",
    "signals",
    "daily_performance",
    "circuit_breaker_state",
    "manual_intervention_events",
    "capital_adjustments",
    "regime_strategy_library",
    "regime_swap_log",
)
```

`trading/db.py:48-50`(live_strategies 스키마의 마지막 두 컬럼+닫는 괄호)을 다음으로 교체:

```python
    baseline_qty        REAL,
    deleted_at          TEXT,
    auto_swap_enabled   INTEGER NOT NULL DEFAULT 0,
    active_regime       TEXT
);
```

`trading/db.py:158`(`regime_strategy_library`의 닫는 `);`) 바로 다음, `"""`(159번째 줄) 이전에 추가:

```sql
CREATE TABLE IF NOT EXISTS regime_swap_log (
    id                TEXT PRIMARY KEY,
    live_strategy_id  TEXT NOT NULL REFERENCES live_strategies(id),
    market            TEXT NOT NULL,
    occurred_at       TEXT NOT NULL DEFAULT (datetime('now')),
    event             TEXT NOT NULL CHECK (event IN (
                          'swap_success', 'swap_skipped_open_position',
                          'swap_skipped_no_mapping', 'manual_override_ack'
                      )),
    from_regime       TEXT,
    to_regime         TEXT NOT NULL,
    detail            TEXT
);
```

`trading/db.py`의 `list_regime_strategy_mappings()`(918-926번째 줄) 다음, `list_active_strategies()`(929번째 줄) 이전에 추가:

```python
def set_auto_swap_enabled(strategy_id: str, enabled: bool) -> bool:
    """존재하는 라이브 전략의 auto_swap_enabled를 갱신한다. 반환값은 갱신 성공
    여부(해당 id가 없으면 False)."""
    conn = _connect()
    try:
        cursor = conn.execute(
            "UPDATE live_strategies SET auto_swap_enabled = ? WHERE id = ?",
            (1 if enabled else 0, strategy_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def set_active_regime(strategy_id: str, regime: str | None) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE live_strategies SET active_regime = ? WHERE id = ?",
            (regime, strategy_id),
        )
        conn.commit()
    finally:
        conn.close()


def insert_regime_swap_log(
    live_strategy_id: str, market: str, event: str,
    from_regime: str | None, to_regime: str, detail: str | None = None,
) -> str:
    log_id = str(uuid.uuid4())
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO regime_swap_log "
            "(id, live_strategy_id, market, event, from_regime, to_regime, detail) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (log_id, live_strategy_id, market, event, from_regime, to_regime, detail),
        )
        conn.commit()
    finally:
        conn.close()
    return log_id


def list_regime_swap_log(live_strategy_id: str, limit: int = 50) -> list[dict]:
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM regime_swap_log WHERE live_strategy_id = ? "
            "ORDER BY occurred_at DESC LIMIT ?",
            (live_strategy_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_trading_db.py -v`
Expected: PASS(전체) — 특히 `test_connect_creates_all_tables`가 새 테이블 포함해서 여전히 통과하는지 확인

전체 회귀도 같이 확인:
Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/ -q`
Expected: 전부 통과(알려진 무관 flake 1건 제외)

- [ ] **Step 5: 커밋**

```bash
git add trading/db.py tests/test_trading_db.py
git commit -m "feat: 라이브 전략에 자동스왑 스위치/장세 상태 컬럼 + 스왑 로그 테이블 추가"
```

---

### Task 2: 판정+실행 로직 (`trading/regime_autoswap.py`, 신규)

**Files:**
- Create: `trading/regime_autoswap.py`
- Test: `tests/test_regime_autoswap.py`(신규)

**Interfaces:**
- Consumes: `trading.db.list_active_strategies()`, `trading.db.list_regime_strategy_mappings()`, `trading.db.replace_live_strategy_strategy()`, `trading.db.set_active_regime()`, `trading.db.insert_regime_swap_log()`(전부 Task 1/기존), `engine.regime_adx.compute_adx_di`/`classify_regime`(기존), `upbit_data_service.get_candles`(기존)
- Produces: `determine_target_regime(market: str) -> str`(항상 `"하락"`/`"횡보"`/`"상승"`/`"기본"` 중 하나), `process_autoswap_tick() -> None` — Task 3(daemon)과 Task 4(backend 수동교체 연동)에서 사용

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime_autoswap.py`(신규):

```python
"""
tests/test_regime_autoswap.py

trading.regime_autoswap의 determine_target_regime()과 process_autoswap_tick()을
검증한다. compute_adx_di/classify_regime/get_candles는 전부 monkeypatch로
대체해 실제 캔들 데이터 없이 판정 로직만 단위 검증한다.
"""
from __future__ import annotations

from unittest.mock import Mock

import pandas as pd

import trading.db as db
import trading.regime_autoswap as regime_autoswap
from tests.trading_db_fixtures import insert_live_strategy


def _fresh_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "trading.db")
    return db


def _fake_raw_df(n: int) -> pd.DataFrame:
    return pd.DataFrame({"close": [1.0] * n})


def _fake_adx_df(n: int) -> pd.DataFrame:
    return pd.DataFrame({"adx": [30.0] * n, "plus_di": [25.0] * n, "minus_di": [10.0] * n})


def test_determine_target_regime_confirms_when_last_three_bars_agree(monkeypatch):
    monkeypatch.setattr(regime_autoswap, "get_candles", lambda *a, **k: _fake_raw_df(5))
    monkeypatch.setattr(regime_autoswap, "compute_adx_di", lambda df: _fake_adx_df(5))
    monkeypatch.setattr(regime_autoswap, "classify_regime", Mock(side_effect=["상승", "상승", "상승"]))

    assert regime_autoswap.determine_target_regime("KRW-BTC") == "상승"


def test_determine_target_regime_falls_back_when_last_three_bars_disagree(monkeypatch):
    monkeypatch.setattr(regime_autoswap, "get_candles", lambda *a, **k: _fake_raw_df(5))
    monkeypatch.setattr(regime_autoswap, "compute_adx_di", lambda df: _fake_adx_df(5))
    monkeypatch.setattr(regime_autoswap, "classify_regime", Mock(side_effect=["상승", "상승", "하락"]))

    assert regime_autoswap.determine_target_regime("KRW-BTC") == "기본"


def test_determine_target_regime_falls_back_when_unclassified(monkeypatch):
    monkeypatch.setattr(regime_autoswap, "get_candles", lambda *a, **k: _fake_raw_df(5))
    monkeypatch.setattr(regime_autoswap, "compute_adx_di", lambda df: _fake_adx_df(5))
    monkeypatch.setattr(regime_autoswap, "classify_regime", Mock(side_effect=[None, None, None]))

    assert regime_autoswap.determine_target_regime("KRW-BTC") == "기본"


def test_determine_target_regime_falls_back_when_not_enough_bars(monkeypatch):
    monkeypatch.setattr(regime_autoswap, "get_candles", lambda *a, **k: _fake_raw_df(2))

    assert regime_autoswap.determine_target_regime("KRW-BTC") == "기본"


def test_process_autoswap_tick_skips_when_already_synced(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm, status="running", market="KRW-BTC")
    dbm.set_auto_swap_enabled(strategy_id, True)
    dbm.set_active_regime(strategy_id, "상승")
    monkeypatch.setattr(regime_autoswap, "determine_target_regime", lambda market: "상승")

    regime_autoswap.process_autoswap_tick()

    assert dbm.get_live_strategy(strategy_id)["active_regime"] == "상승"
    assert dbm.list_regime_swap_log(strategy_id) == []


def test_process_autoswap_tick_logs_when_no_mapping(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm, status="running", market="KRW-BTC")
    dbm.set_auto_swap_enabled(strategy_id, True)
    monkeypatch.setattr(regime_autoswap, "determine_target_regime", lambda market: "상승")

    regime_autoswap.process_autoswap_tick()

    assert dbm.get_live_strategy(strategy_id)["active_regime"] is None
    logs = dbm.list_regime_swap_log(strategy_id)
    assert len(logs) == 1
    assert logs[0]["event"] == "swap_skipped_no_mapping"
    assert logs[0]["to_regime"] == "상승"


def test_process_autoswap_tick_logs_and_waits_when_position_open(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm, status="running", market="KRW-BTC")
    dbm.set_auto_swap_enabled(strategy_id, True)
    dbm.upsert_regime_strategy_mapping(
        "KRW-BTC", "상승", source_run_id="run-up", timeframe="minutes60",
        buy_conditions_json="{}", sell_conditions_json="{}",
    )
    dbm.insert_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)
    monkeypatch.setattr(regime_autoswap, "determine_target_regime", lambda market: "상승")

    regime_autoswap.process_autoswap_tick()

    assert dbm.get_live_strategy(strategy_id)["active_regime"] is None
    logs = dbm.list_regime_swap_log(strategy_id)
    assert len(logs) == 1
    assert logs[0]["event"] == "swap_skipped_open_position"


def test_process_autoswap_tick_swaps_and_updates_active_regime(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(
        dbm, status="running", market="KRW-BTC", timeframe="minutes60",
        buy_conditions_json="{}", sell_conditions_json="{}",
    )
    dbm.set_auto_swap_enabled(strategy_id, True)
    dbm.upsert_regime_strategy_mapping(
        "KRW-BTC", "상승", source_run_id="run-up", timeframe="minutes30",
        buy_conditions_json='{"buy": true}', sell_conditions_json='{"sell": true}',
    )
    monkeypatch.setattr(regime_autoswap, "determine_target_regime", lambda market: "상승")

    regime_autoswap.process_autoswap_tick()

    strategy = dbm.get_live_strategy(strategy_id)
    assert strategy["active_regime"] == "상승"
    assert strategy["source_run_id"] == "run-up"
    assert strategy["timeframe"] == "minutes30"
    assert strategy["buy_conditions_json"] == '{"buy": true}'
    logs = dbm.list_regime_swap_log(strategy_id)
    assert len(logs) == 1
    assert logs[0]["event"] == "swap_success"
    assert logs[0]["to_regime"] == "상승"


def test_process_autoswap_tick_ignores_strategies_with_auto_swap_disabled(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm, status="running", market="KRW-BTC")
    # auto_swap_enabled 기본값 0(꺼짐) — 켜지 않음
    monkeypatch.setattr(
        regime_autoswap, "determine_target_regime",
        lambda market: (_ for _ in ()).throw(AssertionError("호출되면 안 됨")),
    )

    regime_autoswap.process_autoswap_tick()  # 예외 없이 조용히 넘어가야 함

    assert dbm.list_regime_swap_log(strategy_id) == []


def test_process_autoswap_tick_continues_after_one_strategy_raises(monkeypatch, tmp_path, caplog):
    dbm = _fresh_db(monkeypatch, tmp_path)
    broken_id = insert_live_strategy(dbm, status="running", market="KRW-BTC")
    dbm.set_auto_swap_enabled(broken_id, True)
    ok_id = insert_live_strategy(dbm, status="running", market="KRW-ETH")
    dbm.set_auto_swap_enabled(ok_id, True)
    dbm.set_active_regime(ok_id, "상승")

    def fake_determine(market):
        if market == "KRW-BTC":
            raise RuntimeError("캔들 조회 실패")
        return "상승"

    monkeypatch.setattr(regime_autoswap, "determine_target_regime", fake_determine)

    with caplog.at_level("ERROR"):
        regime_autoswap.process_autoswap_tick()  # 예외가 밖으로 새면 테스트 실패

    assert any(broken_id in r.message for r in caplog.records)  # 실패한 전략도 로그로 남음
    assert dbm.list_regime_swap_log(ok_id) == []  # 정상 전략은 이미 동기화 상태라 로그 없이 계속 처리됨
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_regime_autoswap.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.regime_autoswap'`

- [ ] **Step 3: 최소 구현 작성**

`trading/regime_autoswap.py`(신규):

```python
"""
trading/regime_autoswap.py

daemon 장세 자동 스왑 루프(4단계)의 판정+실행 로직. daemon.py는 10분마다
process_autoswap_tick()만 호출하는 얇은 래퍼다. backend/main.py의 수동
"전략 교체" 엔드포인트도 determine_target_regime()을 재사용해 auto_swap_enabled인
전략의 active_regime을 수동 교체 시점에 stamp한다 — 자동 스위치를 켜둔 채
수동 개입해도 automation이 다음 실제 장세변화 전까지 되돌리지 않게 하기 위함
(설계 스펙 docs/superpowers/specs_v2/2026-09-06-regime-daemon-autoswap-design.md
"수동 개입 연동" 절 참고).

engine.regime_adx(순수 pandas, backtrader 미사용)를 직접 import한다 —
daemon.py의 "engine/ 미의존" 원칙은 무거운 backtrader/lightgbm 의존성 회피가
취지이므로 이 가벼운 모듈은 예외로 둔다(설계 문서 결정, 사용자 승인).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import trading.db as db
from engine.regime_adx import classify_regime, compute_adx_di
from upbit_data_service import get_candles

logger = logging.getLogger(__name__)

REGIME_TIMEFRAME = "minutes60"
CONFIRM_BAR_COUNT = 3
FALLBACK_REGIME = "기본"
# ADX(14) 워밍업(약 28봉)에 여유를 둔 값 — backend/regime_adx_service.py의
# OVERVIEW_LOOKBACK_BARS와 동일한 근거(수렴 확보).
LOOKBACK_HOURS = 200


def determine_target_regime(market: str) -> str:
    """market의 1시간봉 기준 현재 확정 장세를 반환한다. 최근 CONFIRM_BAR_COUNT개
    봉의 라벨이 전부 같고 None이 아니면 그 라벨을, 아니면(끈백질 방지 조건
    미충족/데이터부족/미분류) FALLBACK_REGIME("기본")을 반환한다. 항상 4개
    라벨 중 하나를 반환한다(None을 반환하지 않음 — 호출부가 라이브러리의
    "기본" 매핑으로 항상 폴백할 수 있게 하기 위함)."""
    now = datetime.now(timezone.utc)
    df = get_candles(market, REGIME_TIMEFRAME, now - timedelta(hours=LOOKBACK_HOURS), now)
    if len(df) < CONFIRM_BAR_COUNT:
        return FALLBACK_REGIME

    adx_di = compute_adx_di(df)
    recent_labels = [
        classify_regime(row.adx, row.plus_di, row.minus_di)
        for row in adx_di.tail(CONFIRM_BAR_COUNT).itertuples()
    ]
    first = recent_labels[0]
    if first is not None and all(label == first for label in recent_labels):
        return first
    return FALLBACK_REGIME


def process_autoswap_tick() -> None:
    """auto_swap_enabled=1인 모든 활성(running/paused) 라이브 전략을 순회하며
    장세변화를 감지하고 필요하면 교체한다. 전략 단위로 예외를 흡수해 한
    전략의 실패가 나머지 전략 처리를 막지 않게 한다(daemon.py의 기존
    '예외는 로그만 남기고 다음 틱 재시도' 원칙과 동일)."""
    for strategy in db.list_active_strategies():
        if not strategy["auto_swap_enabled"]:
            continue
        try:
            _process_one(strategy)
        except Exception:
            logger.exception("자동스왑 처리 중 예외 발생: strategy_id=%s", strategy["id"])


def _process_one(strategy: dict) -> None:
    strategy_id = strategy["id"]
    market = strategy["market"]
    active_regime = strategy["active_regime"]

    target_regime = determine_target_regime(market)
    if target_regime == active_regime:
        return  # 이미 동기화됨(자동으로 맞췄든 사용자가 수동으로 맞췄든 무관)

    mapping = next(
        (m for m in db.list_regime_strategy_mappings()
         if m["market"] == market and m["regime"] == target_regime),
        None,
    )
    if mapping is None:
        db.insert_regime_swap_log(
            strategy_id, market, "swap_skipped_no_mapping",
            active_regime, target_regime,
            detail=f"{market}/{target_regime} 슬롯이 라이브러리에 없음",
        )
        return

    replaced = db.replace_live_strategy_strategy(
        strategy_id,
        source_run_id=mapping["source_run_id"],
        timeframe=mapping["timeframe"],
        buy_conditions_json=mapping["buy_conditions_json"],
        sell_conditions_json=mapping["sell_conditions_json"],
    )
    if not replaced:
        db.insert_regime_swap_log(
            strategy_id, market, "swap_skipped_open_position",
            active_regime, target_regime,
            detail="오픈 포지션(또는 체결 대기중 매수 주문)이 있어 교체 보류 — 다음 틱 재시도",
        )
        return

    db.set_active_regime(strategy_id, target_regime)
    db.insert_regime_swap_log(
        strategy_id, market, "swap_success",
        active_regime, target_regime,
        detail=f"source_run_id={mapping['source_run_id']}",
    )
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_regime_autoswap.py -v`
Expected: PASS(전체)

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/ -q`
Expected: 전부 통과(알려진 무관 flake 1건 제외)

- [ ] **Step 5: 커밋**

```bash
git add trading/regime_autoswap.py tests/test_regime_autoswap.py
git commit -m "feat: 장세 판정+자동스왑 실행 로직 모듈 추가"
```

---

### Task 3: daemon.py 통합

**Files:**
- Modify: `trading/daemon.py`(import 블록, 상수 블록, `main()`, 그리고 신규 `_run_regime_autoswap_loop`)
- Test: `tests/test_daemon.py`(끝에 추가)

**Interfaces:**
- Consumes: `trading.regime_autoswap.process_autoswap_tick()`(Task 2)
- Produces: `daemon._run_regime_autoswap_loop()` — 이후 태스크 없음(daemon 프로세스 자체의 최종 진입점)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_daemon.py` 파일 끝에 추가(파일 상단에 이미 `import trading.daemon as daemon`가 있음):

```python
import trading.regime_autoswap as regime_autoswap


async def test_regime_autoswap_loop_calls_process_tick_each_cycle(monkeypatch):
    calls = {"count": 0}

    def fake_process_tick():
        calls["count"] += 1

    monkeypatch.setattr(regime_autoswap, "process_autoswap_tick", fake_process_tick)

    async def stop_after_one_check(seconds):
        raise asyncio.CancelledError()

    monkeypatch.setattr(daemon.asyncio, "sleep", stop_after_one_check)

    with pytest.raises(asyncio.CancelledError):
        await daemon._run_regime_autoswap_loop()

    assert calls["count"] == 1


async def test_regime_autoswap_loop_survives_exception(monkeypatch, caplog):
    def fake_process_tick():
        raise RuntimeError("캔들 조회 실패")

    monkeypatch.setattr(regime_autoswap, "process_autoswap_tick", fake_process_tick)

    async def stop_after_one_check(seconds):
        raise asyncio.CancelledError()

    monkeypatch.setattr(daemon.asyncio, "sleep", stop_after_one_check)

    with caplog.at_level("ERROR"), pytest.raises(asyncio.CancelledError):
        await daemon._run_regime_autoswap_loop()  # RuntimeError가 밖으로 새면 테스트 실패

    assert len(caplog.records) == 1
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_daemon.py -v -k regime_autoswap_loop`
Expected: FAIL with `AttributeError: module 'trading.daemon' has no attribute '_run_regime_autoswap_loop'`

- [ ] **Step 3: 최소 구현 작성**

`trading/daemon.py:24-25`(기존 `import trading.reconciler as reconciler` 다음)에 추가:

```python
import trading.reconciler as reconciler
import trading.regime_autoswap as regime_autoswap
import trading.risk_manager as risk_manager
```

`trading/daemon.py:36-43`(기존 폴링 상수 블록, `_MIN_POLL_INTERVAL_SEC` 근처)에 추가:

```python
_AUTOSWAP_CHECK_INTERVAL_SEC = 600  # 10분 — 판정 기준이 1시간봉이라 더 자주 볼 필요 없음
```

`trading/daemon.py`의 `_run_ntp_check_loop()` 함수(기존 366-377번째 줄) 바로 다음, `async def main()`(380번째 줄) 이전에 추가:

```python
async def _run_regime_autoswap_loop() -> None:
    """10분마다 regime_autoswap.process_autoswap_tick()을 호출한다. 판정 기준이
    1시간봉이라 더 짧은 주기로 볼 필요는 없다(설계 스펙 결정). process_autoswap_tick
    자체도 전략 단위로 예외를 흡수하지만, 이 루프 레벨에서도 한 번 더 감싸
    list_active_strategies() 자체가 실패하는 것 같은 예상 밖의 오류로도 루프가
    죽지 않게 한다(daemon.py의 기존 이중 방어 패턴과 동일)."""
    while True:
        try:
            await asyncio.to_thread(regime_autoswap.process_autoswap_tick)
        except Exception:
            logger.exception("자동스왑 틱 처리 중 예외 발생")
        await asyncio.sleep(_AUTOSWAP_CHECK_INTERVAL_SEC)
```

`trading/daemon.py`의 `main()`(380-384번째 줄)을 다음으로 교체:

```python
async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    await asyncio.gather(
        _task_set_manager_loop(), _run_ntp_check_loop(), _run_regime_autoswap_loop(),
    )
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_daemon.py -v`
Expected: PASS(전체)

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/ -q`
Expected: 전부 통과(알려진 무관 flake 1건 제외)

- [ ] **Step 5: 커밋**

```bash
git add trading/daemon.py tests/test_daemon.py
git commit -m "feat: daemon에 장세 자동스왑 루프(10분 주기) 통합"
```

---

### Task 4: backend API (`backend/main.py`)

**Files:**
- Modify: `backend/main.py`(import 블록, `_live_strategy_response()`, `replace_live_strategy_endpoint()`, 그리고 `REGIME_LIBRARY_SLOTS` 근처에 신규 엔드포인트 2개)
- Test: `tests/test_backend.py`(끝에 추가)

**Interfaces:**
- Consumes: `trading_db.set_auto_swap_enabled`/`set_active_regime`/`insert_regime_swap_log`/`list_regime_swap_log`(Task 1), `regime_autoswap.determine_target_regime`(Task 2)
- Produces: `PATCH /api/v1/live-strategies/{id}/auto-swap`, `GET /api/v1/live-strategies/{id}/regime-swap-log`, `_live_strategy_response()`의 `auto_swap_enabled`/`active_regime` 필드 — Task 5(프론트엔드)에서 사용

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_backend.py` 파일 끝에 추가(파일 상단에 이미 `_client`/`_live_strategy_request`/`_seed_backtest_run`/`_VALID_BUY`/`_VALID_SELL`이 정의돼 있음):

```python
import trading.regime_autoswap as regime_autoswap


def test_live_strategy_response_includes_autoswap_defaults(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])

    resp = client.post("/api/v1/live-strategies", json=_live_strategy_request())

    body = resp.json()
    assert body["auto_swap_enabled"] is False
    assert body["active_regime"] is None


def test_set_auto_swap_endpoint_enables_flag(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]

    resp = client.patch(f"/api/v1/live-strategies/{strategy_id}/auto-swap", json={"enabled": True})

    assert resp.status_code == 200
    assert resp.json()["auto_swap_enabled"] is True


def test_set_auto_swap_endpoint_returns_404_for_missing_strategy(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    resp = client.patch("/api/v1/live-strategies/does-not-exist/auto-swap", json={"enabled": True})

    assert resp.status_code == 404


def test_regime_swap_log_endpoint_returns_empty_list_initially(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]

    resp = client.get(f"/api/v1/live-strategies/{strategy_id}/regime-swap-log")

    assert resp.status_code == 200
    assert resp.json() == []


def test_regime_swap_log_endpoint_returns_404_for_missing_strategy(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    resp = client.get("/api/v1/live-strategies/does-not-exist/regime-swap-log")

    assert resp.status_code == 404


def test_replace_strategy_stamps_active_regime_when_autoswap_enabled(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    strategy_id = client.post(
        "/api/v1/live-strategies", json=_live_strategy_request(source_run_id="old-run"),
    ).json()["id"]
    client.post(f"/api/v1/live-strategies/{strategy_id}/stop")
    client.patch(f"/api/v1/live-strategies/{strategy_id}/auto-swap", json={"enabled": True})
    _seed_backtest_run("new-run", "KRW-BTC", "minutes30", _VALID_BUY, _VALID_SELL)
    monkeypatch.setattr(regime_autoswap, "determine_target_regime", lambda market: "상승")

    resp = client.post(
        f"/api/v1/live-strategies/{strategy_id}/replace-strategy",
        json={"source_run_id": "new-run"},
    )

    assert resp.status_code == 200
    assert resp.json()["active_regime"] == "상승"
    log = client.get(f"/api/v1/live-strategies/{strategy_id}/regime-swap-log").json()
    assert len(log) == 1
    assert log[0]["event"] == "manual_override_ack"
    assert log[0]["to_regime"] == "상승"


def test_replace_strategy_does_not_stamp_active_regime_when_autoswap_disabled(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    strategy_id = client.post(
        "/api/v1/live-strategies", json=_live_strategy_request(source_run_id="old-run"),
    ).json()["id"]
    client.post(f"/api/v1/live-strategies/{strategy_id}/stop")
    _seed_backtest_run("new-run", "KRW-BTC", "minutes30", _VALID_BUY, _VALID_SELL)

    resp = client.post(
        f"/api/v1/live-strategies/{strategy_id}/replace-strategy",
        json={"source_run_id": "new-run"},
    )

    assert resp.status_code == 200
    assert resp.json()["active_regime"] is None
    assert client.get(f"/api/v1/live-strategies/{strategy_id}/regime-swap-log").json() == []
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_backend.py -v -k "autoswap or auto_swap or regime_swap_log"`
Expected: FAIL — 신규 엔드포인트가 없어 404/`KeyError: 'auto_swap_enabled'` 등으로 실패

- [ ] **Step 3: 최소 구현 작성**

`backend/main.py:74`(`import trading.db as trading_db`) 바로 다음에 추가:

```python
import trading.db as trading_db
import trading.regime_autoswap as regime_autoswap
```

`backend/main.py`의 `_live_strategy_response()`에서 `"capital_adjustments": [...]` 블록(1375-1384번째 줄) 다음, 닫는 `}`(1385번째 줄) 이전에 추가:

```python
        "capital_adjustments": [
            {
                "id": adj["id"],
                "adjusted_at": _to_utc_iso(adj["adjusted_at"]),
                "previous_capital": adj["previous_capital"],
                "new_capital": adj["new_capital"],
                "delta": adj["delta"],
            }
            for adj in trading_db.list_capital_adjustments(strategy["id"])
        ],
        "auto_swap_enabled": bool(strategy["auto_swap_enabled"]),
        "active_regime": strategy["active_regime"],
    }
```

`backend/main.py`의 `replace_live_strategy_endpoint()`(1596-1618번째 줄)를 다음으로 교체:

```python
@app.post("/api/v1/live-strategies/{strategy_id}/replace-strategy")
def replace_live_strategy_endpoint(strategy_id: str, req: ReplaceLiveStrategyRequest) -> dict:
    strategy = trading_db.get_live_strategy(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="해당 id의 라이브 전략을 찾을 수 없습니다")
    if strategy["status"] == "draft":
        raise HTTPException(status_code=409, detail="draft 상태의 전략은 교체할 수 없습니다")

    config = get_run_config(req.source_run_id)
    if config is None:
        raise HTTPException(status_code=404, detail="해당 run_id의 백테스트 설정을 찾을 수 없습니다")
    _validate_backtest_config_for_market(config, strategy["market"])

    replaced = trading_db.replace_live_strategy_strategy(
        strategy_id,
        source_run_id=req.source_run_id,
        timeframe=config["timeframe"],
        buy_conditions_json=json.dumps(config["buy_conditions"]),
        sell_conditions_json=json.dumps(config["sell_conditions"]),
    )
    if not replaced:
        raise HTTPException(status_code=409, detail="포지션이 열려 있어 교체할 수 없습니다")

    if strategy["auto_swap_enabled"]:
        target_regime = regime_autoswap.determine_target_regime(strategy["market"])
        trading_db.set_active_regime(strategy_id, target_regime)
        trading_db.insert_regime_swap_log(
            strategy_id, strategy["market"], "manual_override_ack",
            strategy["active_regime"], target_regime,
            detail="수동 전략 교체로 인한 자동스왑 상태 동기화",
        )
    return _full_live_strategy_response(strategy_id)


class SetAutoSwapRequest(BaseModel):
    enabled: bool


@app.patch("/api/v1/live-strategies/{strategy_id}/auto-swap")
def set_auto_swap_endpoint(strategy_id: str, req: SetAutoSwapRequest) -> dict:
    if trading_db.get_live_strategy(strategy_id) is None:
        raise HTTPException(status_code=404, detail="해당 id의 라이브 전략을 찾을 수 없습니다")
    trading_db.set_auto_swap_enabled(strategy_id, req.enabled)
    return _full_live_strategy_response(strategy_id)


@app.get("/api/v1/live-strategies/{strategy_id}/regime-swap-log")
def get_regime_swap_log_endpoint(strategy_id: str) -> list[dict]:
    if trading_db.get_live_strategy(strategy_id) is None:
        raise HTTPException(status_code=404, detail="해당 id의 라이브 전략을 찾을 수 없습니다")
    return trading_db.list_regime_swap_log(strategy_id)
```

(`strategy["active_regime"]`은 `replace_live_strategy_strategy` 호출 *이전*에 조회한 값이므로 `manual_override_ack` 로그의 `from_regime`으로 정확하다 — 함수 상단에서 한 번만 조회한 `strategy` 변수를 그대로 재사용.)

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_backend.py -v`
Expected: PASS(전체)

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/ -q`
Expected: 전부 통과(알려진 무관 flake 1건 제외)

- [ ] **Step 5: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "feat: 자동스왑 토글/스왑로그 API + 수동교체 시 장세 상태 동기화"
```

---

### Task 5: 프론트엔드 (라이브 전략 관리 페이지)

**Files:**
- Modify: `frontend/lib/types/liveStrategies.ts`(`LiveStrategy` 인터페이스), `frontend/lib/api/liveStrategies.ts`(신규 함수 2개), `frontend/components/LiveStrategiesPage.tsx`(카드에 토글+배지+이력 추가)

**Interfaces:**
- Consumes: `PATCH /api/v1/live-strategies/{id}/auto-swap`, `GET /api/v1/live-strategies/{id}/regime-swap-log`(Task 4)
- Produces: 없음(최종 UI, 이후 태스크 없음)

- [ ] **Step 1: 타입 추가**

`frontend/lib/types/liveStrategies.ts`의 `LiveStrategy` 인터페이스(43-61번째 줄) 끝, `capital_adjustments: CapitalAdjustment[];` 다음에 추가:

```typescript
  capital_adjustments: CapitalAdjustment[];
  auto_swap_enabled: boolean;
  active_regime: '하락' | '횡보' | '상승' | '기본' | null;
}
```

- [ ] **Step 2: API 함수 추가**

`frontend/lib/api/liveStrategies.ts` 끝에 추가:

```typescript
export function setLiveStrategyAutoSwap(id: string, enabled: boolean): Promise<LiveStrategy> {
  return apiFetch<LiveStrategy>(`/api/v1/live-strategies/${id}/auto-swap`, {
    method: 'PATCH',
    body: JSON.stringify({ enabled }),
  });
}

export interface RegimeSwapLogEntry {
  id: string;
  market: string;
  occurred_at: string;
  event: 'swap_success' | 'swap_skipped_open_position' | 'swap_skipped_no_mapping' | 'manual_override_ack';
  from_regime: string | null;
  to_regime: string;
  detail: string | null;
}

export function getRegimeSwapLog(id: string): Promise<RegimeSwapLogEntry[]> {
  return apiFetch<RegimeSwapLogEntry[]>(`/api/v1/live-strategies/${id}/regime-swap-log`);
}
```

- [ ] **Step 3: 카드에 자동/수동 토글 + 현재 적용 장세 배지 추가**

`frontend/components/LiveStrategiesPage.tsx` 상단 import 블록(1-40번째 줄)을 다음으로 교체(추가분만):

```typescript
import { useCallback, useEffect, useState } from 'react';
import { Check, Coins, Pause, Play, RefreshCw, Square, Sparkles, X } from 'lucide-react';
import { ApiError } from '@/lib/api/client';
import {
  approveLiveStrategy,
  deleteLiveStrategy,
  getLiveStrategies,
  getRegimeSwapLog,
  pauseLiveStrategy,
  replaceLiveStrategyStrategy,
  resumeLiveStrategy,
  setLiveStrategyAutoSwap,
  stopLiveStrategy,
  updateLiveStrategyCapital,
} from '@/lib/api/liveStrategies';
import type { RegimeSwapLogEntry } from '@/lib/api/liveStrategies';
import type { LiveStrategy, LiveStrategyRiskConfig } from '@/lib/types/liveStrategies';
```

파일 상단(다른 상수 선언 근처, `RISK_CONFIG_LABELS` 위 등)에 장세 라벨 색상 매핑 추가 — `frontend/components/RegimeAdxSegmentTable.tsx:12-16`의 `LABEL_TEXT_CLASS`와 동일한 CSS 변수를 재사용:

```typescript
const ACTIVE_REGIME_TEXT_CLASS: Record<'하락' | '횡보' | '상승' | '기본', string> = {
  상승: 'text-[color:var(--regime-surge-up)]',
  하락: 'text-[color:var(--regime-surge-down)]',
  횡보: 'text-[color:var(--marker-boundary)]',
  기본: 'text-muted-foreground',
};
```

`RegimeSwapLogPanel` 컴포넌트를 `LiveStrategiesPage` 함수 바깥, 파일 하단에 추가(펼치면 로그를 지연 조회):

```typescript
function RegimeSwapLogPanel({ strategyId }: { strategyId: string }) {
  const [open, setOpen] = useState(false);
  const [entries, setEntries] = useState<RegimeSwapLogEntry[] | null>(null);

  const EVENT_LABELS: Record<RegimeSwapLogEntry['event'], string> = {
    swap_success: '자동 교체 성공',
    swap_skipped_open_position: '포지션 대기 중',
    swap_skipped_no_mapping: '매핑 없음',
    manual_override_ack: '수동 개입 반영',
  };

  async function toggle() {
    if (!open && entries === null) {
      try {
        setEntries(await getRegimeSwapLog(strategyId));
      } catch {
        setEntries([]);
      }
    }
    setOpen((v) => !v);
  }

  return (
    <div className="mt-2">
      <button
        type="button"
        onClick={toggle}
        className="text-xs text-muted-foreground underline underline-offset-2"
      >
        {open ? '스왑 이력 접기' : '스왑 이력 보기'}
      </button>
      {open && (
        <div className="mt-1 space-y-1 rounded-md bg-muted/50 p-2">
          {entries === null ? (
            <p className="text-xs text-muted-foreground">불러오는 중…</p>
          ) : entries.length === 0 ? (
            <p className="text-xs text-muted-foreground">이력 없음</p>
          ) : (
            entries.map((e) => (
              <div key={e.id} className="text-xs">
                <span className="text-muted-foreground">{formatDateTime(e.occurred_at)}</span>{' '}
                <span>{EVENT_LABELS[e.event]}</span>{' '}
                {e.from_regime && (
                  <span className="text-muted-foreground">
                    ({e.from_regime} → {e.to_regime})
                  </span>
                )}
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
```

`frontend/components/LiveStrategiesPage.tsx:274-278`(카드 헤더, 코인명/시간봉 표시 줄) 안에 "현재 적용 장세" 배지 추가 — 기존:

```typescript
            <div className="flex items-center justify-between gap-1.5 px-4">
              <div className="flex min-w-0 flex-1 items-center gap-1">
                <span className="min-w-0 truncate text-[0.8rem] font-semibold">
                  {koreanName} · {formatTimeframe(s.timeframe)}
                </span>
```

다음으로 교체:

```typescript
            <div className="flex items-center justify-between gap-1.5 px-4">
              <div className="flex min-w-0 flex-1 items-center gap-1">
                <span className="min-w-0 truncate text-[0.8rem] font-semibold">
                  {koreanName} · {formatTimeframe(s.timeframe)}
                </span>
                {s.active_regime && (
                  <span className={`shrink-0 text-[0.7rem] font-medium ${ACTIVE_REGIME_TEXT_CLASS[s.active_regime]}`}>
                    {s.active_regime}
                  </span>
                )}
```

`frontend/components/LiveStrategiesPage.tsx:349-374`(전략 교체 버튼이 있는 액션 그룹) 안, `{s.open_position === null && s.status !== 'draft' && (<BacktestPickerDialog ...>)}` 블록 앞에 자동/수동 토글 버튼 추가 — 기존:

```typescript
              <div className="flex shrink-0 items-center gap-1.5">
                {s.open_position === null && (s.status === 'running' || s.status === 'paused') && (
                  <ChangeCapitalDialog strategy={s} onChanged={refresh} />
                )}
                {s.open_position === null && s.status !== 'draft' && (
```

다음으로 교체:

```typescript
              <div className="flex shrink-0 items-center gap-1.5">
                {s.open_position === null && (s.status === 'running' || s.status === 'paused') && (
                  <ChangeCapitalDialog strategy={s} onChanged={refresh} />
                )}
                {s.status !== 'draft' && (
                  <Button
                    type="button"
                    variant={s.auto_swap_enabled ? 'default' : 'outline'}
                    size="icon-lg"
                    aria-label={s.auto_swap_enabled ? '자동 스왑 끄기' : '자동 스왑 켜기'}
                    title={s.auto_swap_enabled ? '자동 스왑: 켜짐 (클릭하여 끄기)' : '자동 스왑: 꺼짐 (클릭하여 켜기)'}
                    disabled={pendingId === s.id}
                    onClick={() => runAction(s.id, (id) => setLiveStrategyAutoSwap(id, !s.auto_swap_enabled))}
                  >
                    <Sparkles />
                  </Button>
                )}
                {s.open_position === null && s.status !== 'draft' && (
```

마지막으로, 카드 안 "전략 설정 보기" Dialog(266-347번째 줄, `capital_adjustments` 섹션 다음)에 스왑 이력 패널을 추가 — 기존:

```typescript
                          </div>
                        )}
                      </div>
                    </div>
                  </DialogContent>
                </Dialog>
```

다음으로 교체:

```typescript
                          </div>
                        )}
                      </div>
                      <RegimeSwapLogPanel strategyId={s.id} />
                    </div>
                  </DialogContent>
                </Dialog>
```

- [ ] **Step 4: 개발 서버 실행 후 브라우저 수동 검증**

```bash
cd frontend && npm run dev
```

webapp-testing(Playwright)으로 라이브 전략 관리 페이지(`/live-strategies` 또는 해당 탭)를 열어:
1. 전략 카드에 자동/수동 토글 버튼(반짝임 아이콘)이 보이는지 확인, 클릭해 켜지면 버튼 스타일이 바뀌는지(outline → default) 확인
2. `active_regime`이 `null`인 상태(토글을 막 켠 직후)에는 배지가 아예 안 보이는지 확인(조건부 렌더링)
3. "전략 설정 보기"(? 아이콘) 다이얼로그를 열어 "스왑 이력 보기"를 클릭 → "이력 없음"이 표시되는지 확인(아직 daemon이 한 번도 안 돈 상태)
4. 콘솔에 에러가 없는지 확인

- [ ] **Step 5: 커밋**

```bash
git add frontend/lib/types/liveStrategies.ts frontend/lib/api/liveStrategies.ts frontend/components/LiveStrategiesPage.tsx
git commit -m "feat: 라이브 전략 카드에 자동스왑 토글/현재 장세 배지/스왑 이력 UI 추가"
```

## 완료 기준

- 라이브 전략 카드에서 자동 스위치를 켜면, daemon이 10분 이내에 현재 장세에 맞는 라이브러리 매핑으로 전략을 교체한다(오픈 포지션이 있으면 닫힐 때까지 대기 후 교체).
- 자동 스위치가 켜진 채로 기존 "전략 교체" UI로 수동 교체해도, 실제 장세가 바뀌기 전까지 daemon이 되돌리지 않는다.
- "기본" 슬롯이 매핑 없음/장세 불확실 두 경우 모두의 폴백으로 동작한다.
- 모든 자동 판정/교체/실패/수동개입이 `regime_swap_log`에 기록되고 UI에서 전략별로 조회할 수 있다.
- 신규 유닛 테스트 전부 통과, 기존 테스트 스위트 회귀 없음(`PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/ -q`).
- 프론트엔드는 브라우저에서 토글/배지/이력이 정상 동작함을 육안으로 확인.
