# 백테스트 제목 컬럼 폭 고정 + Frequency 컬럼 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 백테스트 결과 표의 제목 컬럼 폭을 고정하고, 각 전략의 매매 빈도(총 캔들 수 대비 실제 매수 체결 횟수)를 보여주는 `frequency` 컬럼을 추가한다. Grid Search 펼쳐보기에도 동일하게 표시한다.

**Architecture:** `engine/runner.py`의 `run_backtest()`가 반환하는 dict에 `candle_count`를 추가하고, 이를 `engine/cache.py`의 `save_result()`/`load_result()`/`list_backtest_runs()`가 신규 DB 컬럼(`backtest_results.candle_count`)으로 영속화한다. 이 경로는 온디맨드 백테스트와 Grid Search가 공유하므로 한 곳만 고치면 양쪽에 다 적용된다. 매수 체결 횟수(`trade_count`)는 이미 저장된 `trades` 리스트 길이를 그대로 쓴다(신규 컬럼 불필요). 프론트는 목록 API 응답에 추가된 두 필드로 `분자/분모 (비율%)` 문자열을 조립해 표시한다. 기존에 이미 저장된 백테스트 결과는 `candle_count`가 NULL이므로 1회성 백필 스크립트로 소급한다.

**Tech Stack:** FastAPI + SQLite(`engine/cache.py`) 백엔드, Next.js/React(App Router) + TypeScript 프론트엔드, pytest.

## Global Constraints

- DB 마이그레이션은 기존 `_connect()`의 `ALTER TABLE ... ADD COLUMN` + `try/except sqlite3.OperationalError` 패턴을 그대로 재사용하고, 여러 번 호출해도 에러 없이 안전해야 한다(`test_connect_migration_is_idempotent` 통과 유지).
- `save_result()`의 `result` dict에 `candle_count` 키가 없어도(기존 테스트 다수가 이 키 없이 호출) 에러 없이 NULL로 저장되어야 한다 — `result.get("candle_count")` 사용.
- 신규 컬럼명은 `candle_count`(분모, 실제 캔들 개수). 분자는 신규 컬럼 없이 `len(trades)`로 매 요청 시 계산.
- Grid Search의 `GridSearchSavedResult`는 저장된 JSON blob이라 기존 이력에는 `trade_count`/`candle_count`가 없을 수 있다 — TS 타입에서 두 필드를 optional로 선언하고, 프론트에서 `candle_count != null`로 방어한다.
- 프론트 새 컬럼 헤더 텍스트는 사용자가 명시적으로 지정한 대로 영문 `frequency` 그대로 사용한다(다른 헤더는 한국어).
- 이 프로젝트 frontend에는 테스트 프레임워크가 없다 — frontend 작업의 검증은 `npx tsc -p tsconfig.json`(타입체크)과 최종 `npm run build`로 한다.
- `scripts/grid_search.py`의 `main()`은 이 코드베이스에서 원래 유닛테스트 대상이 아니다(`tests/test_grid_search.py`가 개별 헬퍼 함수만 테스트하고 `main()`은 호출하지 않음) — 해당 태스크의 검증은 기존 테스트 스위트 통과 + 수동 코드 확인으로 한다.

---

### Task 1: `engine/runner.py` — `run_backtest()`가 `candle_count`를 반환

**Files:**
- Modify: `engine/runner.py:217-241`
- Test: `tests/test_runner.py`

**Interfaces:**
- Consumes: 없음(기존 `run_backtest()` 시그니처 그대로).
- Produces: `run_backtest()`의 반환 dict에 `"candle_count": int` 키 추가(항상 존재, `len(df_bt)`와 동일). Task 2(`engine/cache.py`)가 `result["candle_count"]`로 이 값을 읽는다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_runner.py` 맨 아래(마지막 테스트 함수 뒤)에 추가:

```python
def test_run_backtest_includes_candle_count():
    df = _make_synthetic_df(n=30)
    result = run_backtest(
        df=df,
        strategy_cls=BuyAndHoldOnce,
        risk_config={
            "initial_capital": 10000,
            "commission_rate": 0.001,
            "position_sizing": "percent",
            "position_size": 100,
        },
    )

    assert result["candle_count"] == 30
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_runner.py::test_run_backtest_includes_candle_count -v`
Expected: FAIL with `KeyError: 'candle_count'`

- [ ] **Step 3: 최소 구현**

`engine/runner.py`의 217~241줄(현재):

```python
    open_trades = strategy.analyzers.trades.get_open_trades()
    if open_trades:
        last_close = float(df_bt["close"].iloc[-1])
        last_dt = df_bt.index[-1].isoformat()
        total_bars = len(df_bt)

        for ot in open_trades:
            trades.append(_build_forced_close_trade(
                entry_time=ot["entryTime"],
                entry_price=ot["entryPrice"],
                size=ot["size"],
                baropen=ot["baropen"],
                last_close=last_close,
                last_dt=last_dt,
                total_bars=total_bars,
                commission_rate=commission_rate,
            ))

    return {
        "equity_curve": equity_curve,
        "trades": trades,
        "final_value": final_value,
        "sharpe": sharpe_ratio,
        "max_drawdown": max_drawdown_pct,
    }
```

다음으로 교체(`total_bars`를 조건문 밖으로 옮겨 무조건 계산하고, 반환 dict에 `candle_count` 추가):

```python
    total_bars = len(df_bt)
    open_trades = strategy.analyzers.trades.get_open_trades()
    if open_trades:
        last_close = float(df_bt["close"].iloc[-1])
        last_dt = df_bt.index[-1].isoformat()

        for ot in open_trades:
            trades.append(_build_forced_close_trade(
                entry_time=ot["entryTime"],
                entry_price=ot["entryPrice"],
                size=ot["size"],
                baropen=ot["baropen"],
                last_close=last_close,
                last_dt=last_dt,
                total_bars=total_bars,
                commission_rate=commission_rate,
            ))

    return {
        "equity_curve": equity_curve,
        "trades": trades,
        "final_value": final_value,
        "sharpe": sharpe_ratio,
        "max_drawdown": max_drawdown_pct,
        "candle_count": total_bars,
    }
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_runner.py -v`
Expected: 전체 PASS (기존 테스트 포함, 신규 테스트 포함)

- [ ] **Step 5: 커밋**

```bash
git add engine/runner.py tests/test_runner.py
git commit -m "feat: run_backtest 결과에 candle_count(실제 캔들 개수) 추가"
```

---

### Task 2: `engine/cache.py` — `candle_count` 컬럼 저장/조회 + 백필 헬퍼

**Files:**
- Modify: `engine/cache.py` (schema 23-44줄, `_connect()` 172-184줄, `save_result()` 295-342줄, `load_result()` 187-227줄, `list_backtest_runs()` 499-567줄)
- Test: `tests/test_cache.py`

**Interfaces:**
- Consumes: Task 1이 반환하는 `run_backtest()`의 `result["candle_count"]` (선택적 키 — `.get()`으로 접근).
- Produces:
  - `save_result(..., result: dict)` — `result`에 `candle_count` 키가 있으면 저장, 없으면 NULL.
  - `load_result(run_id) -> dict | None` — 반환 dict에 `"candle_count": int | None` 포함.
  - `list_backtest_runs(...) -> list[dict]` — 각 항목에 `"candle_count": int | None` 포함.
  - `list_runs_missing_candle_count() -> list[dict]` — `[{"run_id", "market", "timeframe", "start", "end"}, ...]`, `candle_count IS NULL`인 행만. Task 4(백필 스크립트)가 사용.
  - `set_candle_count(run_id: str, candle_count: int) -> None` — 지정 run의 `candle_count`만 갱신. Task 4가 사용.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_cache.py`의 cache import 블록(12~23줄)에 `list_runs_missing_candle_count`, `set_candle_count` 추가:

```python
from engine.cache import (
    list_backtest_runs,
    list_combined_ranking,
    list_distinct_combos,
    list_latest_sweep_results,
    list_runs_missing_candle_count,
    list_segment_classification,
    list_sweep_history,
    save_segment_classification,
    save_sweep_result,
    set_candle_count,
    list_trend_segments,
    save_trend_segments,
)
```

파일 맨 아래에 추가:

```python
def test_save_result_stores_and_returns_candle_count(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    save_result(
        run_id="r1", strategy_name="ConditionTreeStrategy", strategy_params={},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={
            "final_value": 10500.0, "sharpe": None, "max_drawdown": None,
            "equity_curve": [], "trades": [], "candle_count": 240,
        },
    )

    loaded = load_result("r1")
    assert loaded["candle_count"] == 240

    runs = list_backtest_runs()
    assert runs[0]["candle_count"] == 240


def test_save_result_without_candle_count_stores_none(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    save_result(
        run_id="r1", strategy_name="ConditionTreeStrategy", strategy_params={},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={"final_value": 10500.0, "sharpe": None, "max_drawdown": None, "equity_curve": [], "trades": []},
    )

    loaded = load_result("r1")
    assert loaded["candle_count"] is None


def test_list_runs_missing_candle_count_returns_only_null_rows(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    save_result(
        run_id="has-count", strategy_name="ConditionTreeStrategy", strategy_params={},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={
            "final_value": 10500.0, "sharpe": None, "max_drawdown": None,
            "equity_curve": [], "trades": [], "candle_count": 100,
        },
    )
    save_result(
        run_id="missing-count", strategy_name="ConditionTreeStrategy", strategy_params={},
        market="KRW-ETH", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={"final_value": 10500.0, "sharpe": None, "max_drawdown": None, "equity_curve": [], "trades": []},
    )

    missing = list_runs_missing_candle_count()
    assert [r["run_id"] for r in missing] == ["missing-count"]
    assert missing[0]["market"] == "KRW-ETH"
    assert missing[0]["timeframe"] == "days"


def test_set_candle_count_updates_existing_row(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    save_result(
        run_id="r1", strategy_name="ConditionTreeStrategy", strategy_params={},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={"final_value": 10500.0, "sharpe": None, "max_drawdown": None, "equity_curve": [], "trades": []},
    )

    set_candle_count("r1", 365)

    assert load_result("r1")["candle_count"] == 365
    assert list_runs_missing_candle_count() == []
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_cache.py -k candle_count -v`
Expected: FAIL — `ImportError: cannot import name 'list_runs_missing_candle_count'`

- [ ] **Step 3: 최소 구현**

`engine/cache.py`의 `_SCHEMA`(36~43줄) 교체:

```python
CREATE TABLE IF NOT EXISTS backtest_results (
    run_id TEXT PRIMARY KEY REFERENCES backtest_runs(id),
    final_value REAL,
    sharpe REAL,
    max_drawdown REAL,
    equity_curve_json TEXT NOT NULL,
    trades_json TEXT NOT NULL,
    candle_count INTEGER
);
```

`_connect()`(172~184줄) 교체:

```python
def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(_SCHEMA)
    # backtest_runs에 title/description 컬럼을 추가하는 경량 마이그레이션.
    # CREATE TABLE IF NOT EXISTS는 이미 존재하는 테이블의 컬럼을 추가해주지 않으므로,
    # 기존 DB 파일에도 안전하게 적용되도록 ALTER TABLE을 시도하고 이미 있으면 무시한다.
    for column in ("title", "description"):
        try:
            conn.execute(f"ALTER TABLE backtest_runs ADD COLUMN {column} TEXT")
        except sqlite3.OperationalError:
            pass
    try:
        conn.execute("ALTER TABLE backtest_results ADD COLUMN candle_count INTEGER")
    except sqlite3.OperationalError:
        pass
    return conn
```

`load_result()`(187~227줄) 교체:

```python
def load_result(run_id: str) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT res.final_value, res.sharpe, res.max_drawdown, res.equity_curve_json, res.trades_json, "
            "       res.candle_count, "
            "       r.market, r.timeframe, r.start, r.end, r.risk_config_json, "
            "       r.title, r.description, r.created_at "
            "FROM backtest_results res "
            "JOIN backtest_runs r ON r.id = res.run_id "
            "WHERE res.run_id = ?",
            (run_id,),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return None

    (final_value, sharpe, max_drawdown, equity_curve_json, trades_json, candle_count,
     market, timeframe, start, end, risk_config_json,
     title, description, created_at) = row
    risk_config = json.loads(risk_config_json)
    initial_capital = risk_config.get("initial_capital")
    commission_rate = risk_config.get("commission_rate", 0.0005)
    return {
        "final_value": final_value,
        "sharpe": sharpe,
        "max_drawdown": max_drawdown,
        "equity_curve": json.loads(equity_curve_json),
        "trades": json.loads(trades_json),
        "candle_count": candle_count,
        "market": market,
        "timeframe": timeframe,
        "start": start,
        "end": end,
        "initial_capital": initial_capital,
        "commission_rate": commission_rate,
        "title": title,
        "description": description,
        "created_at": created_at,
        "from_cache": True,
    }
```

`save_result()`(295~342줄) 안의 INSERT 문 교체:

```python
        conn.execute(
            "INSERT OR REPLACE INTO backtest_results "
            "(run_id, final_value, sharpe, max_drawdown, equity_curve_json, trades_json, candle_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                result["final_value"],
                result["sharpe"],
                result["max_drawdown"],
                json.dumps(result["equity_curve"]),
                json.dumps(result["trades"]),
                result.get("candle_count"),
            ),
        )
```

`list_backtest_runs()`(519~567줄) 교체:

```python
        rows = conn.execute(
            "SELECT r.id, r.title, r.description, r.market, r.timeframe, r.start, r.end, "
            "       r.created_at, r.risk_config_json, r.params_json, "
            "       res.final_value, res.sharpe, res.max_drawdown, res.trades_json, res.candle_count "
            "FROM backtest_runs r "
            "JOIN backtest_results res ON res.run_id = r.id "
            f"WHERE {where} "
            # created_at은 초 단위라 같은 초에 여러 건이 저장되면 순서가 불안정해질 수 있어,
            # 삽입 순서를 그대로 보존하는 rowid를 보조 정렬 기준으로 둔다.
            "ORDER BY r.created_at DESC, r.rowid DESC "
            "LIMIT ?",
            params,
        ).fetchall()
    finally:
        conn.close()

    runs: list[dict] = []
    for row in rows:
        (run_id, title, description, market_val, timeframe, start, end,
         created_at, risk_config_json, params_json,
         final_value, sharpe, max_drawdown, trades_json, candle_count) = row
        risk_config = json.loads(risk_config_json)
        initial_capital = risk_config.get("initial_capital")
        commission_rate = risk_config.get("commission_rate", 0.0005)
        return_rate = (
            (final_value - initial_capital) / initial_capital * 100
            if initial_capital else None
        )
        params = json.loads(params_json)
        runs.append({
            "run_id": run_id,
            "title": title,
            "description": description,
            "market": market_val,
            "timeframe": timeframe,
            "start": start,
            "end": end,
            "created_at": created_at,
            "final_value": final_value,
            "return_rate": return_rate,
            "sharpe": sharpe,
            "max_drawdown": max_drawdown,
            "candle_count": candle_count,
            "initial_capital": initial_capital,
            "commission_rate": commission_rate,
            "trades": json.loads(trades_json),
            "buy_conditions": params["buy_conditions"],
            "sell_conditions": params["sell_conditions"],
        })
    return runs
```

`list_backtest_runs()` 함수 바로 뒤(568줄 이후, `save_segment_classification` 앞)에 신규 함수 2개 추가:

```python
def list_runs_missing_candle_count() -> list[dict]:
    """candle_count가 아직 채워지지 않은(candle_count 컬럼 도입 이전에 저장된) run들을
    market/timeframe/start/end와 함께 반환한다. scripts/backfill_candle_count.py 전용."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT r.id, r.market, r.timeframe, r.start, r.end "
            "FROM backtest_runs r "
            "JOIN backtest_results res ON res.run_id = r.id "
            "WHERE res.candle_count IS NULL"
        ).fetchall()
    finally:
        conn.close()
    return [
        {"run_id": run_id, "market": market, "timeframe": timeframe, "start": start, "end": end}
        for run_id, market, timeframe, start, end in rows
    ]


def set_candle_count(run_id: str, candle_count: int) -> None:
    """scripts/backfill_candle_count.py 전용: 이미 저장된 run의 candle_count만 갱신한다."""
    conn = _connect()
    try:
        conn.execute(
            "UPDATE backtest_results SET candle_count = ? WHERE run_id = ?",
            (candle_count, run_id),
        )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_cache.py -v`
Expected: 전체 PASS (기존 876줄 분량 테스트 전부 포함)

- [ ] **Step 5: 커밋**

```bash
git add engine/cache.py tests/test_cache.py
git commit -m "feat: backtest_results에 candle_count 컬럼 추가 + 백필 헬퍼 함수"
```

---

### Task 3: `backend/main.py` — 목록 API 응답에 `trade_count`/`candle_count` 추가

**Files:**
- Modify: `backend/main.py:612-661`
- Test: `tests/test_backend.py`

**Interfaces:**
- Consumes: Task 2의 `list_backtest_runs()`가 반환하는 각 항목의 `"candle_count"` 필드, 기존에 이미 있던 `"trades"` 필드(`len()`으로 매수 체결 횟수 계산).
- Produces: `GET /api/v1/backtests` 응답의 각 항목에 `"trade_count": int`, `"candle_count": int | None` 추가. Task 6(프론트 타입)가 이 필드명을 그대로 사용.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_backend.py` 파일의 `test_get_backtests_top_trade_contribution_pct_none_without_wins` 함수(1541~1554줄) 뒤에 추가:

```python
def test_get_backtests_includes_trade_count_and_candle_count(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    save_result(
        run_id="r1", strategy_name="ConditionTreeStrategy",
        strategy_params={"buy_conditions": {}, "sell_conditions": {}},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={
            "final_value": 10500.0, "sharpe": None, "max_drawdown": None, "equity_curve": [],
            "trades": [
                {
                    "entryTime": "2026-01-01T00:00:00", "exitTime": "2026-01-02T00:00:00",
                    "entryPrice": 100.0, "exitPrice": 105.0, "returnRate": 5.0,
                    "holdingPeriod": 1, "pnl": 50.0, "forceClosed": False, "size": 100.0,
                },
            ],
            "candle_count": 240,
        },
    )

    resp = client.get("/api/v1/backtests")
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["trade_count"] == 1
    assert body[0]["candle_count"] == 240


def test_get_backtests_candle_count_is_none_when_not_backfilled(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    save_result(
        run_id="r1", strategy_name="ConditionTreeStrategy",
        strategy_params={"buy_conditions": {}, "sell_conditions": {}},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={"final_value": 10000.0, "sharpe": None, "max_drawdown": None, "equity_curve": [], "trades": []},
    )

    resp = client.get("/api/v1/backtests")
    body = resp.json()
    assert body[0]["trade_count"] == 0
    assert body[0]["candle_count"] is None
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_backend.py -k "trade_count_and_candle_count or candle_count_is_none" -v`
Expected: FAIL — `KeyError: 'trade_count'`

- [ ] **Step 3: 최소 구현**

`backend/main.py`의 `get_backtest_runs()`(612~661줄) 안의 `result.append({...})` 블록 교체:

```python
        result.append({
            "run_id": r["run_id"],
            "title": r["title"],
            "description": r["description"],
            "market": r["market"],
            "timeframe": r["timeframe"],
            "start": r["start"],
            "end": r["end"],
            "created_at": _to_utc_iso(r["created_at"]),
            "final_value": final_value,
            "return_rate": return_rate,
            "sharpe": r["sharpe"],
            "max_drawdown": r["max_drawdown"],
            "top_trade_contribution_pct": top_trade_contribution_pct(trades),
            "trade_count": len(trades),
            "candle_count": r["candle_count"],
            "is_live": is_live,
            "last_trade_status": last_trade_status,
            "buy_conditions": r["buy_conditions"],
            "sell_conditions": r["sell_conditions"],
        })
```

(`trades`는 위쪽 루프에서 이미 계산된 지역 변수 — 미청산 포지션 재평가 시 `revalued`로 교체된 리스트일 수 있으므로 원본 `r["trades"]`가 아니라 이 지역 변수를 그대로 써야 한다. `top_trade_contribution_pct(trades)`가 이미 같은 변수를 쓰고 있으므로 바로 아래 줄에 `len(trades)`를 추가하면 된다.)

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_backend.py -v`
Expected: 전체 PASS

- [ ] **Step 5: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "feat: 백테스트 목록 API 응답에 trade_count/candle_count 추가"
```

---

### Task 4: `scripts/backfill_candle_count.py` — 기존 결과 1회성 백필 스크립트

**Files:**
- Create: `scripts/backfill_candle_count.py`
- Test: `tests/test_backfill_candle_count.py`

**Interfaces:**
- Consumes: Task 2의 `engine.cache.list_runs_missing_candle_count()`, `engine.cache.set_candle_count()`, `engine.cache.DB_PATH`; `upbit_data_service.get_candles(market, timeframe, start, end) -> pd.DataFrame`.
- Produces: `run(apply: bool) -> None` — 모듈 레벨 함수, 테스트와 CLI(`if __name__ == "__main__":`) 양쪽에서 호출.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_backfill_candle_count.py` 신규 생성:

```python
from datetime import datetime, timezone

import pandas as pd

import engine.cache as cache_module
from engine.cache import load_result, save_result
from scripts import backfill_candle_count as bf


def _seed_run_without_candle_count(run_id="r1", market="KRW-BTC"):
    save_result(
        run_id=run_id, strategy_name="ConditionTreeStrategy", strategy_params={},
        market=market, timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={"final_value": 10500.0, "sharpe": None, "max_drawdown": None, "equity_curve": [], "trades": []},
    )


def _fake_candles(n: int) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n, freq="D", tz="UTC")
    return pd.DataFrame({"candle_time": idx, "close": range(n)})


def test_backfill_apply_fills_candle_count(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    _seed_run_without_candle_count()
    monkeypatch.setattr(bf, "get_candles", lambda market, timeframe, start, end: _fake_candles(10))

    bf.run(apply=True)

    assert load_result("r1")["candle_count"] == 10


def test_backfill_dry_run_does_not_modify_db(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    _seed_run_without_candle_count()
    monkeypatch.setattr(bf, "get_candles", lambda market, timeframe, start, end: _fake_candles(10))

    bf.run(apply=False)

    assert load_result("r1")["candle_count"] is None


def test_backfill_skips_runs_that_already_have_candle_count(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    save_result(
        run_id="already-filled", strategy_name="ConditionTreeStrategy", strategy_params={},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={
            "final_value": 10500.0, "sharpe": None, "max_drawdown": None,
            "equity_curve": [], "trades": [], "candle_count": 999,
        },
    )
    calls = {"n": 0}

    def _counting_get_candles(market, timeframe, start, end):
        calls["n"] += 1
        return _fake_candles(10)

    monkeypatch.setattr(bf, "get_candles", _counting_get_candles)

    bf.run(apply=True)

    assert calls["n"] == 0
    assert load_result("already-filled")["candle_count"] == 999


def test_backfill_continues_after_a_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    _seed_run_without_candle_count(run_id="ok", market="KRW-BTC")
    _seed_run_without_candle_count(run_id="bad", market="KRW-ETH")

    def _flaky_get_candles(market, timeframe, start, end):
        if market == "KRW-ETH":
            raise RuntimeError("네트워크 오류")
        return _fake_candles(5)

    monkeypatch.setattr(bf, "get_candles", _flaky_get_candles)

    bf.run(apply=True)

    assert load_result("ok")["candle_count"] == 5
    assert load_result("bad")["candle_count"] is None
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_backfill_candle_count.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.backfill_candle_count'`

- [ ] **Step 3: 최소 구현**

`scripts/backfill_candle_count.py` 신규 생성:

```python
"""
scripts/backfill_candle_count.py

1회성 마이그레이션: candle_count 컬럼(engine/cache.py의 backtest_results) 도입 이전에
저장된 백테스트 결과들에 실제 캔들 개수를 채워 넣는다. 신규 백테스트/Grid Search는
engine/runner.py의 run_backtest()가 candle_count를 계산해 자동으로 저장하므로,
이 스크립트는 마이그레이션 이전 데이터만 대상으로 한다.

실행 전 backtest_results.db를 자동 백업한다(--apply일 때만). 기본은 드라이런(무엇을
채울지만 출력)이고, --apply를 줘야 실제로 DB를 변경한다.

사용법:
    python scripts/backfill_candle_count.py            # 드라이런
    python scripts/backfill_candle_count.py --apply     # 실제 적용
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import engine.cache as cache
from upbit_data_service import get_candles


def _backup_db() -> Path:
    """raw shutil.copy2 대신 sqlite3 온라인 백업 API를 쓴다 — WAL 모드에서 아직
    체크포인트되지 않은 커밋 데이터가 -wal 사이드카에만 있어 누락될 수 있어서다
    (scripts/backfill_entry_fee.py와 동일한 이유)."""
    backup_path = cache.DB_PATH.with_name(
        f"{cache.DB_PATH.name}.bak-{datetime.now().strftime('%Y%m%dT%H%M%S')}"
    )
    src = sqlite3.connect(cache.DB_PATH)
    try:
        dst = sqlite3.connect(backup_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return backup_path


def run(apply: bool) -> None:
    if apply:
        backup_path = _backup_db()
        print(f"백업 완료: {backup_path}")

    targets = cache.list_runs_missing_candle_count()
    filled_count = 0
    failed_count = 0

    for entry in targets:
        try:
            start_dt = datetime.fromisoformat(entry["start"])
            end_dt = datetime.fromisoformat(entry["end"])
            df = get_candles(entry["market"], entry["timeframe"], start_dt, end_dt)
            candle_count = len(df)
        except Exception as e:
            failed_count += 1
            print(f"  실패: run_id={entry['run_id']} market={entry['market']} - {e}")
            continue

        print(
            f"  run_id={entry['run_id']} market={entry['market']} "
            f"timeframe={entry['timeframe']}: candle_count={candle_count}"
        )
        if apply:
            cache.set_candle_count(entry["run_id"], candle_count)
        filled_count += 1

    print(f"\n완료: {filled_count}건 채움, {failed_count}건 실패 (대상 총 {len(targets)}건).")
    if not apply:
        print("드라이런입니다. 실제로 적용하려면 --apply를 붙여 다시 실행하세요.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="실제로 DB를 변경한다(기본은 드라이런)")
    args = parser.parse_args()
    run(args.apply)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_backfill_candle_count.py -v`
Expected: 전체 PASS

- [ ] **Step 5: 커밋**

```bash
git add scripts/backfill_candle_count.py tests/test_backfill_candle_count.py
git commit -m "feat: 기존 백테스트 결과 candle_count 백필 스크립트 추가"
```

---

### Task 5: `scripts/grid_search.py` — 저장 결과에 `trade_count`/`candle_count` 포함

**Files:**
- Modify: `scripts/grid_search.py:401-404`

**Interfaces:**
- Consumes: `run_backtest_cached()`가 반환하는 `saved` dict의 `saved["trades"]`(리스트), `saved["candle_count"]`(Task 1+2 이후 항상 존재 — 캐시 히트 경로는 `load_result()`가, 신규 실행 경로는 `run_backtest()`가 채움).
- Produces: `saved_summaries`의 각 항목에 `"trade_count": int`, `"candle_count": int` 추가. Task 9(`GridSearchHistory.tsx`)가 이 필드명을 그대로 읽는다.

- [ ] **Step 1: 코드 변경**

`scripts/grid_search.py`의 401~404줄 교체:

```python
        print(f"  {rank:2d}. {r['return_pct']:+.2f}%  run_id={saved['run_id'][:12]}...", flush=True)
        saved_summaries.append({
            "rank": rank,
            "run_id": saved["run_id"],
            "return_pct": round(r["return_pct"], 2),
            "title": title,
            "trade_count": len(saved["trades"]),
            "candle_count": saved["candle_count"],
        })
```

- [ ] **Step 2: 기존 테스트 스위트로 회귀 확인**

이 파일의 `main()`은 이 코드베이스에서 원래 유닛테스트 대상이 아니다(`tests/test_grid_search.py`는 `build_condition_grid`/`compute_grid_results`/`dedup_top_results` 등 개별 헬퍼만 테스트). 대신 기존 스위트가 깨지지 않았는지 확인한다.

Run: `pytest tests/test_grid_search.py tests/test_cache.py -v`
Expected: 전체 PASS (이 태스크에서 건드린 코드를 직접 테스트하지는 않지만, import 오류나 문법 오류가 없는지 확인)

- [ ] **Step 3: 커밋**

```bash
git add scripts/grid_search.py
git commit -m "feat: Grid Search 저장 결과에 trade_count/candle_count 포함"
```

---

### Task 6: 프론트 타입 확장 + `formatFrequency` 헬퍼

**Files:**
- Modify: `frontend/lib/types/eda.ts:156-174` (`BacktestRunSummary`), `frontend/lib/types/eda.ts:185-190` (`GridSearchSavedResult`)
- Modify: `frontend/lib/format.ts`

**Interfaces:**
- Consumes: Task 3이 추가한 `trade_count`/`candle_count` 필드(목록 API), Task 5가 추가한 동일 필드(Grid Search `result_json`).
- Produces: `BacktestRunSummary.trade_count: number`, `BacktestRunSummary.candle_count: number | null`, `GridSearchSavedResult.trade_count?: number`, `GridSearchSavedResult.candle_count?: number` (Grid Search는 기존 이력에 필드가 없을 수 있어 optional). `formatFrequency(tradeCount: number, candleCount: number | null | undefined): string` — Task 7·8·9가 공통으로 사용.

- [ ] **Step 1: 타입 수정**

`frontend/lib/types/eda.ts`의 `BacktestRunSummary`(156~174줄) 교체:

```ts
export interface BacktestRunSummary {
  run_id: string;
  title: string | null;
  description: string | null;
  market: string;
  timeframe: string;
  start: string;
  end: string;
  created_at: string;
  final_value: number;
  return_rate: number | null;
  sharpe: number | null;
  max_drawdown: number | null;
  top_trade_contribution_pct: number | null;
  trade_count: number;
  candle_count: number | null;
  is_live: boolean;
  last_trade_status: 'open' | 'closed' | 'none';
  buy_conditions: ConditionGroup;
  sell_conditions: ConditionGroup;
}
```

같은 파일의 `GridSearchSavedResult`(185~190줄) 교체:

```ts
export interface GridSearchSavedResult {
  rank: number;
  run_id: string;
  return_pct: number;
  title: string;
  trade_count?: number;
  candle_count?: number;
}
```

- [ ] **Step 2: `formatFrequency` 헬퍼 추가**

`frontend/lib/format.ts` 파일 끝에 추가:

```ts
export function formatFrequency(tradeCount: number, candleCount: number | null | undefined): string {
  if (candleCount == null) return '-';
  const pct = candleCount > 0 ? (tradeCount / candleCount) * 100 : 0;
  return `${tradeCount} / ${candleCount} (${pct.toFixed(1)}%)`;
}
```

- [ ] **Step 3: 타입체크로 확인**

Run (frontend 디렉터리에서): `npx tsc -p tsconfig.json`
Expected: 이 시점에는 `BacktestRunsTable.tsx` 등 소비자 쪽이 아직 새 필드를 안 쓰므로 에러 없이 통과해야 한다(타입에 필드를 "추가"만 했을 뿐 기존 사용처를 깨지 않음).

- [ ] **Step 4: 커밋**

```bash
git add frontend/lib/types/eda.ts frontend/lib/format.ts
git commit -m "feat: BacktestRunSummary/GridSearchSavedResult에 frequency 필드, formatFrequency 헬퍼 추가"
```

---

### Task 7: `BacktestRunsTable.tsx` — 제목 컬럼 폭 고정 + frequency 컬럼

**Files:**
- Modify: `frontend/components/BacktestRunsTable.tsx`

**Interfaces:**
- Consumes: Task 6의 `BacktestRunSummary.trade_count`/`.candle_count`, `formatFrequency()` (from `@/lib/format`).
- Produces: 없음(터미널 UI 컴포넌트).

- [ ] **Step 1: import에 `formatFrequency` 추가**

`frontend/components/BacktestRunsTable.tsx`의 26줄:

```tsx
import { formatDateTime, formatTimeframe } from '@/lib/format';
```
->
```tsx
import { formatDateTime, formatFrequency, formatTimeframe } from '@/lib/format';
```

- [ ] **Step 2: 제목 헤더/셀에 고정폭 + tooltip 적용**

282줄:
```tsx
            <TableHead>제목</TableHead>
```
그대로 유지(헤더 텍스트 자체는 짧아 폭 문제 없음 — 매수전략/매도전략 컬럼과 동일하게 `TableCell` 쪽만 제한하는 기존 패턴을 따른다).

338~341줄(현재):
```tsx
              <TableCell>
                {run.title || <span className="text-muted-foreground">(제목 없음)</span>}
                {run.description && <p className="text-xs text-muted-foreground">{run.description}</p>}
              </TableCell>
```

교체:
```tsx
              <TableCell className="max-w-[160px]">
                <div
                  className="truncate"
                  title={[run.title, run.description].filter(Boolean).join(' — ') || undefined}
                >
                  {run.title || <span className="text-muted-foreground">(제목 없음)</span>}
                </div>
                {run.description && (
                  <p className="truncate text-xs text-muted-foreground">{run.description}</p>
                )}
              </TableCell>
```

- [ ] **Step 3: frequency 컬럼 헤더 추가**

310~311줄(현재):
```tsx
            <TableHead className="text-right">최대거래 기여도(%)</TableHead>
            <TableHead>상태</TableHead>
```

교체:
```tsx
            <TableHead className="text-right">최대거래 기여도(%)</TableHead>
            <TableHead>frequency</TableHead>
            <TableHead>상태</TableHead>
```

- [ ] **Step 4: colSpan 갱신**

324줄:
```tsx
              <TableCell colSpan={14} className="text-center text-muted-foreground">
```
->
```tsx
              <TableCell colSpan={15} className="text-center text-muted-foreground">
```

- [ ] **Step 5: frequency 셀 추가**

363~368줄(현재):
```tsx
              <TableCell className="text-right tabular-nums">
                {run.top_trade_contribution_pct != null ? run.top_trade_contribution_pct.toFixed(1) : '-'}
              </TableCell>
              <TableCell>
                <LastTradeStatusBadge status={run.last_trade_status} />
              </TableCell>
```

교체:
```tsx
              <TableCell className="text-right tabular-nums">
                {run.top_trade_contribution_pct != null ? run.top_trade_contribution_pct.toFixed(1) : '-'}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {formatFrequency(run.trade_count, run.candle_count)}
              </TableCell>
              <TableCell>
                <LastTradeStatusBadge status={run.last_trade_status} />
              </TableCell>
```

- [ ] **Step 6: 타입체크**

Run (frontend 디렉터리에서): `npx tsc -p tsconfig.json`
Expected: 에러 없이 통과

- [ ] **Step 7: 개발 서버로 육안 확인**

Run: `npm run dev` (frontend 디렉터리, 이미 떠 있지 않다면) 후 브라우저로 `http://localhost:3000/backtests` 접속.
Expected: 제목 컬럼이 좁고 고정된 폭으로 보이고, 긴 제목은 말줄임표(...)로 잘리며 마우스를 올리면 전체 텍스트가 tooltip으로 보인다. `frequency` 컬럼에 `N / M (X.X%)` 또는 `-`(아직 백필 안 된 기존 데이터)가 보인다.

- [ ] **Step 8: 커밋**

```bash
git add frontend/components/BacktestRunsTable.tsx
git commit -m "feat: 백테스트 목록 표 제목 컬럼 폭 고정 + frequency 컬럼 추가"
```

---

### Task 8: `BacktestRunCard.tsx` — 모바일 카드에 frequency 통계 추가

**Files:**
- Modify: `frontend/components/BacktestRunCard.tsx`

**Interfaces:**
- Consumes: Task 6의 `BacktestRunSummary.trade_count`/`.candle_count`, `formatFrequency()`.
- Produces: 없음.

- [ ] **Step 1: import에 `formatFrequency` 추가**

`frontend/components/BacktestRunCard.tsx`의 10줄:
```tsx
import { formatDateTime, formatTimeframe } from '@/lib/format';
```
->
```tsx
import { formatDateTime, formatFrequency, formatTimeframe } from '@/lib/format';
```

- [ ] **Step 2: 통계 스트립에 frequency 추가**

56~59줄(현재):
```tsx
        <span className="text-muted-foreground">MDD {run.max_drawdown?.toFixed(2) ?? '-'}%</span>
        <span className="text-muted-foreground">
          최대거래 기여도 {run.top_trade_contribution_pct != null ? `${run.top_trade_contribution_pct.toFixed(1)}%` : '-'}
        </span>
```

교체:
```tsx
        <span className="text-muted-foreground">MDD {run.max_drawdown?.toFixed(2) ?? '-'}%</span>
        <span className="text-muted-foreground">
          최대거래 기여도 {run.top_trade_contribution_pct != null ? `${run.top_trade_contribution_pct.toFixed(1)}%` : '-'}
        </span>
        <span className="text-muted-foreground">frequency {formatFrequency(run.trade_count, run.candle_count)}</span>
```

- [ ] **Step 3: 타입체크**

Run (frontend 디렉터리에서): `npx tsc -p tsconfig.json`
Expected: 에러 없이 통과

- [ ] **Step 4: 커밋**

```bash
git add frontend/components/BacktestRunCard.tsx
git commit -m "feat: 백테스트 모바일 카드에 frequency 통계 추가"
```

---

### Task 9: `GridSearchHistory.tsx` — 펼쳐보기 상세에 frequency 표시

**Files:**
- Modify: `frontend/components/GridSearchHistory.tsx`

**Interfaces:**
- Consumes: Task 6의 `GridSearchSavedResult.trade_count`/`.candle_count`(optional), `formatFrequency()`.
- Produces: 없음.

- [ ] **Step 1: import에 `formatFrequency` 추가**

`frontend/components/GridSearchHistory.tsx`의 23줄:
```tsx
import { formatDateTime, formatTimeframe, TIMEFRAME_CODES } from '@/lib/format';
```
->
```tsx
import { formatDateTime, formatFrequency, formatTimeframe, TIMEFRAME_CODES } from '@/lib/format';
```

- [ ] **Step 2: 펼쳐보기 grid를 7열로 확장**

386줄:
```tsx
                          <div className="grid grid-cols-[auto_auto_auto_auto_auto_auto] items-center gap-x-3 gap-y-1 text-sm">
```
->
```tsx
                          <div className="grid grid-cols-[auto_auto_auto_auto_auto_auto_auto] items-center gap-x-3 gap-y-1 text-sm">
```

- [ ] **Step 3: 각 결과 행에 frequency 셀 추가**

410~413줄(현재):
```tsx
                                  <Link href={`/backtests/${r.run_id}`} className="underline">
                                    보기
                                  </Link>
                                </Fragment>
                              );
```

교체:
```tsx
                                  <span className="text-xs text-muted-foreground">
                                    {r.candle_count != null ? formatFrequency(r.trade_count ?? 0, r.candle_count) : '-'}
                                  </span>
                                  <Link href={`/backtests/${r.run_id}`} className="underline">
                                    보기
                                  </Link>
                                </Fragment>
                              );
```

- [ ] **Step 4: 타입체크**

Run (frontend 디렉터리에서): `npx tsc -p tsconfig.json`
Expected: 에러 없이 통과

- [ ] **Step 5: 개발 서버로 육안 확인**

`http://localhost:3000/grid-search`에서 완료된 job을 펼쳐(펼쳐보기) 각 결과 행에 `보기` 링크 앞에 frequency 값(또는 이전 이력이면 `-`)이 보이는지 확인.

- [ ] **Step 6: 커밋**

```bash
git add frontend/components/GridSearchHistory.tsx
git commit -m "feat: Grid Search 펼쳐보기 결과에 frequency 표시"
```

---

### Task 10: 전체 회귀 검증

**Files:** 없음(검증 전용 태스크).

**Interfaces:**
- Consumes: Task 1~9 전체.
- Produces: 없음.

- [ ] **Step 1: 백엔드 전체 테스트**

Run: `pytest tests/ -v`
Expected: 전체 PASS (신규 테스트 포함, 기존 테스트 회귀 없음)

- [ ] **Step 2: 프론트 프로덕션 빌드**

Run (frontend 디렉터리에서): `npm run build`
Expected: 타입 에러/린트 에러 없이 빌드 성공

- [ ] **Step 3: (선택) 기존 데이터 백필 실행 안내**

로컬 `data/backtest_results.db`에 실제로 존재하는 과거 백테스트 결과들에 `candle_count`를 채우려면:

```bash
python scripts/backfill_candle_count.py           # 드라이런으로 먼저 확인
python scripts/backfill_candle_count.py --apply    # 실제 적용
```

이 단계는 로컬 DB 상태에 따라 사용자가 원할 때 직접 실행한다(자동 커밋 대상 아님).
