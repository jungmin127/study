# 라이브 전략 "전략 교체" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 열린 포지션이 없는 라이브 전략을, 같은 코인의 다른 백테스트 결과(시간봉/매수조건/매도조건)로 삭제·재생성 없이 제자리 교체하는 "전략 교체" 기능을 추가한다.

**Architecture:** 기존 라이브 전략 CRUD 패턴(FastAPI 엔드포인트 + `trading/db.py` SQLite 함수 + React 클라이언트 컴포넌트)을 그대로 따르는 추가 작업이다. `trading/db.py`에 같은 `live_strategy_id` 행을 갱신하는 새 함수를 추가하고(같은 id를 유지해야 저널의 market 단위 집계가 끊기지 않는다), `engine/cache.py`의 백테스트 목록 조회에 market 필터를 추가해 팝업 후보 목록을 좁힌다. 프론트는 `LiveStrategiesPage.tsx`에 새 `Dialog` 기반 컴포넌트를 하나 추가해 후보를 라디오로 선택하게 한다.

**Tech Stack:** FastAPI, SQLite(sqlite3 표준 라이브러리), pytest, Next.js(App Router) 클라이언트 컴포넌트, shadcn 스타일 UI 컴포넌트(base-ui 기반), lucide-react 아이콘.

## Global Constraints

- 사용자 대상 문자열(에러 메시지/버튼 라벨/안내 문구)은 전부 한국어로 작성한다 (기존 코드베이스 관례).
- 교체는 `market`, `current_capital`, `risk_config_json`, `baseline_qty`, `status`(및 `approved_at`/`started_at`/`stopped_at`)를 건드리지 않는다. 새 백테스트 값으로 덮어쓰는 필드는 `source_run_id`, `timeframe`, `buy_conditions_json`, `sell_conditions_json`뿐이다 — `docs/superpowers/specs/2026-08-18-live-strategy-swap-design.md`의 확정 사항.
- 교체 시 `last_processed_candle_time`을 NULL로 리셋한다 (timeframe 변경 시 신호 엔진이 최신 봉을 건너뛰는 것을 방지).
- 교체 시 `circuit_breaker_state`의 `tripped`/`consecutive_losses`/`tripped_reason`/`tripped_at`을 리셋한다 (해당 행이 없으면 조용히 0행 갱신).
- 교체는 `status`가 `draft`가 아니고(running/paused/stopped 전부 허용) 열린 포지션(`positions.status='open'`)이 없을 때만 허용한다.
- 팝업 후보 목록은 같은 market, 현재 적용 중인 `source_run_id` 제외, 최신순(`created_at DESC, rowid DESC` — 기존 정렬 그대로).
- 프론트엔드에는 테스트 프레임워크가 없다 (`frontend/package.json`에 test 스크립트 없음). 프론트 작업은 `npx tsc --noEmit`으로 타입 오류만 검증한다 — `npm run build`는 쓰지 않는다(로컬 dev 서버가 같은 `.next` 디렉터리를 쓰고 있으면 프로덕션 빌드가 그 디렉터리를 깨뜨리는 문제가 이 프로젝트에서 이미 확인된 적 있다).

---

## Task 1: `trading/db.py` — `replace_live_strategy_strategy()`

**Files:**
- Modify: `trading/db.py` (785행, `stop_live_strategy_if_no_open_position()` 함수 다음, `list_active_strategies()` 앞에 새 함수 추가)
- Test: `tests/test_trading_db.py`

**Interfaces:**
- Consumes: 없음 (기존 `_connect()`만 사용)
- Produces: `replace_live_strategy_strategy(live_strategy_id: str, source_run_id: str, timeframe: str, buy_conditions_json: str, sell_conditions_json: str) -> bool` — Task 4가 이 함수를 호출한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_trading_db.py` 맨 아래에 추가 (파일 181번째 줄 부근에 이미 `from tests.trading_db_fixtures import insert_live_strategy`가 있으므로 그 이후 아무 위치나 가능):

```python
def test_replace_live_strategy_strategy_updates_fields_and_preserves_others(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(
        db,
        source_run_id="old-run",
        market="KRW-BTC",
        timeframe="minutes60",
        buy_conditions_json='{"old": true}',
        sell_conditions_json='{"old": true}',
        risk_config_json='{"position_sizing_value": 100000}',
        current_capital=500000.0,
        status="running",
    )
    db.update_live_strategy_last_candle(strategy_id, "2026-08-17T00:00:00")
    db.update_live_strategy_baseline_qty(strategy_id, 0.05)

    result = db.replace_live_strategy_strategy(
        strategy_id,
        source_run_id="new-run",
        timeframe="minutes30",
        buy_conditions_json='{"new": true}',
        sell_conditions_json='{"new": true}',
    )

    assert result is True
    strategy = db.get_live_strategy(strategy_id)
    assert strategy["source_run_id"] == "new-run"
    assert strategy["timeframe"] == "minutes30"
    assert strategy["buy_conditions_json"] == '{"new": true}'
    assert strategy["sell_conditions_json"] == '{"new": true}'
    assert strategy["last_processed_candle_time"] is None
    # market/자본/자금관리/기존 보유량 판단 기준은 그대로 유지되어야 한다
    assert strategy["market"] == "KRW-BTC"
    assert strategy["current_capital"] == 500000.0
    assert strategy["risk_config_json"] == '{"position_sizing_value": 100000}'
    assert strategy["baseline_qty"] == 0.05


def test_replace_live_strategy_strategy_refuses_when_position_open(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="running", timeframe="minutes60")
    db.insert_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)

    result = db.replace_live_strategy_strategy(
        strategy_id,
        source_run_id="new-run",
        timeframe="minutes30",
        buy_conditions_json='{"new": true}',
        sell_conditions_json='{"new": true}',
    )

    assert result is False
    assert db.get_live_strategy(strategy_id)["timeframe"] == "minutes60"


def test_replace_live_strategy_strategy_refuses_draft_status(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="draft", timeframe="minutes60")

    result = db.replace_live_strategy_strategy(
        strategy_id,
        source_run_id="new-run",
        timeframe="minutes30",
        buy_conditions_json='{"new": true}',
        sell_conditions_json='{"new": true}',
    )

    assert result is False
    assert db.get_live_strategy(strategy_id)["timeframe"] == "minutes60"


def test_replace_live_strategy_strategy_resets_tripped_circuit_breaker(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="running")
    db.upsert_circuit_breaker_state(
        strategy_id, "2026-08-18", 3, 1,
        tripped_reason="일일 손실 한도 초과", tripped_at="2026-08-18T05:00:00",
    )

    result = db.replace_live_strategy_strategy(
        strategy_id,
        source_run_id="new-run",
        timeframe="minutes30",
        buy_conditions_json='{"new": true}',
        sell_conditions_json='{"new": true}',
    )

    assert result is True
    cb_state = db.get_circuit_breaker_state(strategy_id)
    assert cb_state["tripped"] == 0
    assert cb_state["consecutive_losses"] == 0
    assert cb_state["tripped_reason"] is None
    assert cb_state["tripped_at"] is None


def test_replace_live_strategy_strategy_noop_when_no_circuit_breaker_row(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="stopped")

    result = db.replace_live_strategy_strategy(
        strategy_id,
        source_run_id="new-run",
        timeframe="minutes30",
        buy_conditions_json='{"new": true}',
        sell_conditions_json='{"new": true}',
    )

    assert result is True
    assert db.get_circuit_breaker_state(strategy_id) is None
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_trading_db.py -k test_replace_live_strategy_strategy -v`
Expected: FAIL with `AttributeError: module 'trading.db' has no attribute 'replace_live_strategy_strategy'`

- [ ] **Step 3: 최소 구현 작성**

`trading/db.py`의 `stop_live_strategy_if_no_open_position()` 함수(767~785행) 바로 다음, `list_active_strategies()`(788행) 앞에 추가:

```python
def replace_live_strategy_strategy(
    live_strategy_id: str,
    source_run_id: str,
    timeframe: str,
    buy_conditions_json: str,
    sell_conditions_json: str,
) -> bool:
    """열린 포지션이 없는 라이브 전략을 같은 market 안에서 다른 백테스트 결과로
    제자리 교체한다. market/current_capital/risk_config_json/baseline_qty/status는
    건드리지 않는다 — 자금관리(risk_config)는 백테스트 결과에 존재하지 않는
    라이브 전용 입력값이라 사용자가 이미 설정해둔 값을 그대로 유지한다.
    last_processed_candle_time은 NULL로 리셋한다 — timeframe이 바뀌면 이전 봉
    기준 타임스탬프가 새 timeframe에서 최신 봉을 건너뛰게 만들 수 있다
    (signal_engine.py의 <= 게이트). circuit_breaker_state도 함께 리셋한다 —
    이전 전략의 손실로 트립된 상태가 새 전략의 매수를 막지 않도록 하기 위함
    (그 행이 아예 없으면 UPDATE는 조용히 0행 갱신되고 넘어간다)."""
    conn = _connect()
    try:
        open_position = conn.execute(
            "SELECT id FROM positions WHERE live_strategy_id = ? AND status = 'open'",
            (live_strategy_id,),
        ).fetchone()
        if open_position is not None:
            return False

        cursor = conn.execute(
            "UPDATE live_strategies SET source_run_id=?, timeframe=?, buy_conditions_json=?, "
            "sell_conditions_json=?, last_processed_candle_time=NULL "
            "WHERE id=? AND status IN ('running','paused','stopped')",
            (source_run_id, timeframe, buy_conditions_json, sell_conditions_json, live_strategy_id),
        )
        if cursor.rowcount == 0:
            conn.rollback()
            return False

        conn.execute(
            "UPDATE circuit_breaker_state SET tripped=0, consecutive_losses=0, "
            "tripped_reason=NULL, tripped_at=NULL WHERE live_strategy_id=?",
            (live_strategy_id,),
        )
        conn.commit()
        return True
    finally:
        conn.close()
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_trading_db.py -k test_replace_live_strategy_strategy -v`
Expected: PASS (5 passed)

- [ ] **Step 5: 커밋**

```bash
git add trading/db.py tests/test_trading_db.py
git commit -m "feat: 라이브 전략 제자리 교체 함수(replace_live_strategy_strategy) 추가"
```

---

## Task 2: `engine/cache.py` — `list_backtest_runs()`에 market 필터 추가

**Files:**
- Modify: `engine/cache.py:496-521` (`list_backtest_runs` 함수 시그니처와 쿼리)
- Test: `tests/test_cache.py`

**Interfaces:**
- Consumes: 없음
- Produces: `list_backtest_runs(strategy_name: str = "ConditionTreeStrategy", limit: int = 1000, market: str | None = None) -> list[dict]` — Task 3이 이 함수를 호출한다. `market` 생략 시(기본 None) 기존과 동일하게 전체 market을 반환한다(하위 호환).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_cache.py`의 `_save_condition_tree_run` 헬퍼(356행)에 `market` 파라미터를 추가:

```python
def _save_condition_tree_run(monkeypatch, tmp_path, run_id: str, title: str | None, description: str | None,
                              final_value: float = 11000.0, initial_capital: float = 10000.0,
                              market: str = "KRW-BTC"):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    save_result(
        run_id=run_id,
        strategy_name="ConditionTreeStrategy",
        strategy_params={"buy_conditions": {}, "sell_conditions": {}},
        market=market,
        timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": initial_capital},
        result={"final_value": final_value, "sharpe": 1.0, "max_drawdown": 2.0, "equity_curve": [], "trades": []},
        title=title,
        description=description,
    )
```

(기존 호출부는 전부 키워드 인자로 호출하고 있으므로 `market` 기본값 추가는 하위 호환이다.)

파일 맨 아래에 추가:

```python
def test_list_backtest_runs_filters_by_market(monkeypatch, tmp_path):
    _save_condition_tree_run(monkeypatch, tmp_path, "btc-run", title="BTC", description=None, market="KRW-BTC")
    _save_condition_tree_run(monkeypatch, tmp_path, "eth-run", title="ETH", description=None, market="KRW-ETH")

    runs = list_backtest_runs(market="KRW-ETH")

    assert [r["run_id"] for r in runs] == ["eth-run"]


def test_list_backtest_runs_without_market_returns_all(monkeypatch, tmp_path):
    _save_condition_tree_run(monkeypatch, tmp_path, "btc-run", title="BTC", description=None, market="KRW-BTC")
    _save_condition_tree_run(monkeypatch, tmp_path, "eth-run", title="ETH", description=None, market="KRW-ETH")

    runs = list_backtest_runs()

    assert {r["run_id"] for r in runs} == {"btc-run", "eth-run"}
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_cache.py -k test_list_backtest_runs_filters_by_market -v`
Expected: FAIL with `TypeError: list_backtest_runs() got an unexpected keyword argument 'market'`

- [ ] **Step 3: 최소 구현 작성**

`engine/cache.py`의 `list_backtest_runs` 함수(496~521행)를 다음으로 교체:

```python
def list_backtest_runs(
    strategy_name: str = "ConditionTreeStrategy", limit: int = 1000, market: str | None = None,
) -> list[dict]:
    """온디맨드 조건식 실행(홈 화면) 결과만 최신순으로 반환한다.

    strategy_name으로 필터링해 run_sweep()이 남기는 SignalStrategy 기반 행(히트맵/랭킹
    전용)은 섞이지 않게 한다 — 두 시스템은 의도적으로 분리되어 있다. market이 주어지면
    해당 market의 결과만 추가로 필터링한다(라이브 전략 "전략 교체" 팝업이 같은 코인
    후보만 보여주기 위해 사용).

    initial_capital/commission_rate/trades/buy_conditions/sell_conditions는 클라이언트
    응답용이 아니라 backend/main.py가 미청산 포지션 실시간 재평가 계산에 쓰는 내부 필드다."""
    conn = _connect()
    try:
        where = "r.strategy_name = ?"
        params: list = [strategy_name]
        if market is not None:
            where += " AND r.market = ?"
            params.append(market)
        params.append(limit)
        rows = conn.execute(
            "SELECT r.id, r.title, r.description, r.market, r.timeframe, r.start, r.end, "
            "       r.created_at, r.risk_config_json, r.params_json, "
            "       res.final_value, res.sharpe, res.max_drawdown, res.trades_json "
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
         final_value, sharpe, max_drawdown, trades_json) = row
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
            "initial_capital": initial_capital,
            "commission_rate": commission_rate,
            "trades": json.loads(trades_json),
            "buy_conditions": params["buy_conditions"],
            "sell_conditions": params["sell_conditions"],
        })
    return runs
```

(변경점: 함수 시그니처에 `market` 파라미터 추가, WHERE 절을 동적으로 조립, 언패킹 변수명을 기존 지역변수 `market`과의 충돌을 피하기 위해 `market_val`로 변경. 이후 로직은 기존과 동일.)

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_cache.py -v`
Expected: 전체 PASS (기존 `list_backtest_runs` 관련 테스트 포함, 새 2건 포함)

- [ ] **Step 5: 커밋**

```bash
git add engine/cache.py tests/test_cache.py
git commit -m "feat: list_backtest_runs에 market 필터 추가"
```

---

## Task 3: `backend/main.py` — `GET /api/v1/backtests`에 market 쿼리 파라미터 연결

**Files:**
- Modify: `backend/main.py:510-512` (`get_backtest_runs` 함수 시그니처)
- Test: `tests/test_backend.py`

**Interfaces:**
- Consumes: `list_backtest_runs(market: str | None = None)` (Task 2)
- Produces: `GET /api/v1/backtests?market=KRW-BTC` — Task 6의 프론트 `getBacktestRuns(market)`가 호출한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_backend.py` 맨 아래에 추가:

```python
def test_get_backtest_runs_filters_by_market(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    save_result(
        run_id="btc-run", strategy_name="ConditionTreeStrategy",
        strategy_params={"buy_conditions": {}, "sell_conditions": {}},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={"final_value": 10500.0, "sharpe": 1.0, "max_drawdown": 2.0, "equity_curve": [], "trades": []},
        title="BTC 결과",
    )
    save_result(
        run_id="eth-run", strategy_name="ConditionTreeStrategy",
        strategy_params={"buy_conditions": {}, "sell_conditions": {}},
        market="KRW-ETH", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={"final_value": 10500.0, "sharpe": 1.0, "max_drawdown": 2.0, "equity_curve": [], "trades": []},
        title="ETH 결과",
    )

    resp = client.get("/api/v1/backtests", params={"market": "KRW-ETH"})

    assert resp.status_code == 200
    run_ids = [r["run_id"] for r in resp.json()]
    assert run_ids == ["eth-run"]


def test_get_backtest_runs_without_market_returns_all(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    save_result(
        run_id="btc-run", strategy_name="ConditionTreeStrategy",
        strategy_params={"buy_conditions": {}, "sell_conditions": {}},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={"final_value": 10500.0, "sharpe": 1.0, "max_drawdown": 2.0, "equity_curve": [], "trades": []},
        title="BTC 결과",
    )

    resp = client.get("/api/v1/backtests")

    assert resp.status_code == 200
    assert len(resp.json()) == 1
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_backend.py -k test_get_backtest_runs_filters_by_market -v`
Expected: FAIL — `assert run_ids == ["eth-run"]`에서 실패 (둘 다 반환되어 `["btc-run", "eth-run"]` 또는 `["eth-run", "btc-run"]`이 나옴, market 파라미터가 무시되므로)

- [ ] **Step 3: 최소 구현 작성**

`backend/main.py`의 `get_backtest_runs` 함수(510~512행) 시그니처를 다음으로 교체:

```python
@app.get("/api/v1/backtests")
def get_backtest_runs(market: str | None = Query(None)) -> list[dict]:
    runs = list_backtest_runs(market=market)
```

(이 아래 513행부터 이어지는 재평가 로직은 그대로 둔다.)

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_backend.py -k test_get_backtest_runs -v`
Expected: PASS (2 passed)

- [ ] **Step 5: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "feat: GET /api/v1/backtests에 market 쿼리 파라미터 추가"
```

---

## Task 4: `backend/main.py` — `source_run_id` 응답 필드 + `POST /replace-strategy` 엔드포인트

**Files:**
- Modify: `backend/main.py:1170-1195` (`_live_strategy_response`에 `source_run_id` 필드 추가)
- Modify: `backend/main.py:1371-1379` 바로 다음 (새 `ReplaceLiveStrategyRequest` + 엔드포인트 추가, `/api/v1/journal/summary` 앞)
- Test: `tests/test_backend.py`

**Interfaces:**
- Consumes: `trading_db.replace_live_strategy_strategy(...)` (Task 1), `get_run_config(run_id)` (기존 함수, 이미 import됨), `VALID_TIMEFRAMES` (기존, 이미 import됨)
- Produces: `POST /api/v1/live-strategies/{strategy_id}/replace-strategy` — Task 6의 프론트 `replaceLiveStrategyStrategy(id, sourceRunId)`가 호출한다. 응답 바디는 기존 `LiveStrategy` 응답 형태에 `source_run_id: string | null` 필드가 추가된 것.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_backend.py` 맨 아래에 추가:

```python
def _seed_backtest_run(run_id: str, market: str, timeframe: str, buy_conditions: dict, sell_conditions: dict) -> None:
    save_result(
        run_id=run_id, strategy_name="ConditionTreeStrategy",
        strategy_params={"buy_conditions": buy_conditions, "sell_conditions": sell_conditions},
        market=market, timeframe=timeframe,
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={"final_value": 10500.0, "sharpe": 1.0, "max_drawdown": 2.0, "equity_curve": [], "trades": []},
        title="교체용",
    )


def test_live_strategy_response_includes_source_run_id(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])

    resp = client.post("/api/v1/live-strategies", json=_live_strategy_request(source_run_id="orig-run"))

    assert resp.json()["source_run_id"] == "orig-run"


def test_replace_live_strategy_swaps_timeframe_and_conditions(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    strategy_id = client.post(
        "/api/v1/live-strategies", json=_live_strategy_request(source_run_id="old-run"),
    ).json()["id"]
    client.post(f"/api/v1/live-strategies/{strategy_id}/stop")  # draft -> stopped, 승인 없이도 가능
    new_buy = {"type": "AND", "conditions": [{"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30}]}
    new_sell = {"type": "AND", "conditions": [{"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 70}]}
    _seed_backtest_run("new-run", "KRW-BTC", "minutes30", new_buy, new_sell)

    resp = client.post(
        f"/api/v1/live-strategies/{strategy_id}/replace-strategy",
        json={"source_run_id": "new-run"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["timeframe"] == "minutes30"
    assert body["buy_conditions"] == new_buy
    assert body["sell_conditions"] == new_sell
    assert body["source_run_id"] == "new-run"
    assert body["status"] == "stopped"  # 상태는 교체 전과 동일하게 유지


def test_replace_live_strategy_returns_404_for_missing_strategy(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    resp = client.post(
        "/api/v1/live-strategies/does-not-exist/replace-strategy",
        json={"source_run_id": "new-run"},
    )

    assert resp.status_code == 404


def test_replace_live_strategy_returns_404_for_missing_run(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]
    client.post(f"/api/v1/live-strategies/{strategy_id}/stop")

    resp = client.post(
        f"/api/v1/live-strategies/{strategy_id}/replace-strategy",
        json={"source_run_id": "does-not-exist"},
    )

    assert resp.status_code == 404


def test_replace_live_strategy_returns_409_for_draft_status(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]
    _seed_backtest_run("new-run", "KRW-BTC", "minutes30", _VALID_BUY, _VALID_SELL)

    resp = client.post(
        f"/api/v1/live-strategies/{strategy_id}/replace-strategy",
        json={"source_run_id": "new-run"},
    )

    assert resp.status_code == 409


def test_replace_live_strategy_returns_400_for_market_mismatch(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]
    client.post(f"/api/v1/live-strategies/{strategy_id}/stop")
    _seed_backtest_run("eth-run", "KRW-ETH", "minutes30", _VALID_BUY, _VALID_SELL)

    resp = client.post(
        f"/api/v1/live-strategies/{strategy_id}/replace-strategy",
        json={"source_run_id": "eth-run"},
    )

    assert resp.status_code == 400


def test_replace_live_strategy_returns_409_when_position_open(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    monkeypatch.setattr(backend_module.upbit_client, "get_accounts", _accounts_with_krw_balance(1_000_000))
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]
    client.post(f"/api/v1/live-strategies/{strategy_id}/approve")
    trading_db_module.insert_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)
    _seed_backtest_run("new-run", "KRW-BTC", "minutes30", _VALID_BUY, _VALID_SELL)

    resp = client.post(
        f"/api/v1/live-strategies/{strategy_id}/replace-strategy",
        json={"source_run_id": "new-run"},
    )

    assert resp.status_code == 409
```

`_accounts_with_krw_balance`는 이미 파일 내(626행 부근)에 정의되어 있으므로 재정의하지 않는다.

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_backend.py -k "test_replace_live_strategy or test_live_strategy_response_includes_source_run_id" -v`
Expected: FAIL — `source_run_id` 관련 assert는 `KeyError`/`AssertionError`(응답에 필드 없음), replace-strategy 관련 테스트는 404 Not Found(엔드포인트 자체가 없음)

- [ ] **Step 3: 최소 구현 작성**

`backend/main.py`의 `_live_strategy_response` 함수(1170~1195행)에서 반환 dict 맨 앞부분을 수정:

```python
def _live_strategy_response(strategy: dict, position: dict | None, current_price: float | None) -> dict:
    return {
        "id": strategy["id"],
        "source_run_id": strategy["source_run_id"],
        "market": strategy["market"],
```

(이후 필드들은 그대로 둔다.)

`backend/main.py`의 `delete_live_strategy_endpoint`(1371~1379행) 바로 다음, `/api/v1/journal/summary` 엔드포인트 앞에 추가:

```python
class ReplaceLiveStrategyRequest(BaseModel):
    source_run_id: str


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
    if config["market"] != strategy["market"]:
        raise HTTPException(status_code=400, detail="선택한 백테스트 결과의 마켓이 현재 전략과 다릅니다")
    if config["timeframe"] not in VALID_TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 봉데이터입니다: {config['timeframe']}")

    replaced = trading_db.replace_live_strategy_strategy(
        strategy_id,
        source_run_id=req.source_run_id,
        timeframe=config["timeframe"],
        buy_conditions_json=json.dumps(config["buy_conditions"]),
        sell_conditions_json=json.dumps(config["sell_conditions"]),
    )
    if not replaced:
        raise HTTPException(status_code=409, detail="포지션이 열려 있어 교체할 수 없습니다")
    return _full_live_strategy_response(strategy_id)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_backend.py -k "test_replace_live_strategy or test_live_strategy_response_includes_source_run_id" -v`
Expected: PASS (7 passed)

이어서 전체 백엔드 스위트가 깨지지 않았는지 확인:

Run: `python -m pytest tests/test_backend.py -v`
Expected: 전체 PASS

- [ ] **Step 5: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "feat: 라이브 전략 교체 API(POST /replace-strategy) 추가"
```

---

## Task 5: 프론트엔드 — 타입 + API 클라이언트 함수

**Files:**
- Modify: `frontend/lib/types/liveStrategies.ts` (`LiveStrategy` 인터페이스에 `source_run_id` 추가)
- Modify: `frontend/lib/api/liveStrategies.ts` (`replaceLiveStrategyStrategy` 함수 추가)
- Modify: `frontend/lib/api/eda.ts` (`getBacktestRuns`에 선택적 `market` 파라미터 추가)

**Interfaces:**
- Consumes: 없음 (Task 4의 백엔드 API 스펙만 참고)
- Produces:
  - `LiveStrategy.source_run_id: string | null`
  - `replaceLiveStrategyStrategy(id: string, sourceRunId: string): Promise<LiveStrategy>`
  - `getBacktestRuns(market?: string): Promise<BacktestRunSummary[]>`
  - Task 6이 이 세 가지를 그대로 가져다 쓴다.

- [ ] **Step 1: `LiveStrategy` 타입에 `source_run_id` 추가**

`frontend/lib/types/liveStrategies.ts`의 `LiveStrategy` 인터페이스(43~58행)를 수정:

```typescript
export interface LiveStrategy {
  id: string;
  source_run_id: string | null;
  market: string;
  timeframe: string;
  status: LiveStrategyStatus;
  current_capital: number | null;
  created_at: string;
  approved_at: string | null;
  started_at: string | null;
  stopped_at: string | null;
  open_position: LiveStrategyOpenPosition | null;
  buy_conditions: ConditionGroup;
  sell_conditions: ConditionGroup;
  risk_config: LiveStrategyRiskConfig;
  capital_adjustments: CapitalAdjustment[];
}
```

- [ ] **Step 2: `replaceLiveStrategyStrategy` API 함수 추가**

`frontend/lib/api/liveStrategies.ts` 맨 아래에 추가:

```typescript
export function replaceLiveStrategyStrategy(id: string, sourceRunId: string): Promise<LiveStrategy> {
  return apiFetch<LiveStrategy>(`/api/v1/live-strategies/${id}/replace-strategy`, {
    method: 'POST',
    body: JSON.stringify({ source_run_id: sourceRunId }),
  });
}
```

- [ ] **Step 3: `getBacktestRuns`에 market 파라미터 추가**

`frontend/lib/api/eda.ts`의 `getBacktestRuns` 함수(44~46행)를 다음으로 교체:

```typescript
export function getBacktestRuns(market?: string): Promise<BacktestRunSummary[]> {
  const query = market ? `?market=${encodeURIComponent(market)}` : '';
  return apiFetch<BacktestRunSummary[]>(`/api/v1/backtests${query}`);
}
```

(기존 호출부(`getBacktestRuns()`, 인자 없음)는 선택적 파라미터라 그대로 동작한다.)

- [ ] **Step 4: 타입 체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음 (0 errors). `LiveStrategy.source_run_id`를 아직 아무도 안 읽으므로 미사용 관련 에러도 없어야 한다.

- [ ] **Step 5: 커밋**

```bash
git add frontend/lib/types/liveStrategies.ts frontend/lib/api/liveStrategies.ts frontend/lib/api/eda.ts
git commit -m "feat: 전략 교체용 타입/API 클라이언트 함수 추가"
```

---

## Task 6: 프론트엔드 — `StrategySwapDialog` 컴포넌트 + `LiveStrategiesPage` 연결

**Files:**
- Modify: `frontend/components/LiveStrategiesPage.tsx`

**Interfaces:**
- Consumes: `getBacktestRuns(market)`, `replaceLiveStrategyStrategy(id, sourceRunId)` (Task 5), `LiveStrategy`, `BacktestRunSummary` 타입
- Produces: 없음 (최종 UI 결과물)

- [ ] **Step 1: import 추가**

`frontend/components/LiveStrategiesPage.tsx` 상단 import를 수정:

`lucide-react` import(4행)에 `RefreshCw` 추가:

```typescript
import { Check, CircleHelp, Coins, Pause, Play, RefreshCw, Square, Trash2, X } from 'lucide-react';
```

`@/lib/api/liveStrategies` import(6~14행)에 `replaceLiveStrategyStrategy` 추가:

```typescript
import {
  approveLiveStrategy,
  deleteLiveStrategy,
  getLiveStrategies,
  pauseLiveStrategy,
  replaceLiveStrategyStrategy,
  resumeLiveStrategy,
  stopLiveStrategy,
  updateLiveStrategyCapital,
} from '@/lib/api/liveStrategies';
```

새 import 두 줄 추가 (`@/lib/api/liveStrategies` import 블록 바로 아래):

```typescript
import { getBacktestRuns } from '@/lib/api/eda';
import type { BacktestRunSummary } from '@/lib/types/eda';
```

- [ ] **Step 2: `StrategySwapDialog` 컴포넌트 작성**

`ChangeCapitalDialog` 함수(93~197행) 바로 다음에 추가:

```typescript
function StrategySwapDialog({
  strategy,
  onChanged,
}: {
  strategy: LiveStrategy;
  onChanged: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [candidates, setCandidates] = useState<BacktestRunSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  function closeAndReset() {
    setOpen(false);
    setCandidates([]);
    setSelectedRunId(null);
    setLoadError(null);
    setSubmitError(null);
  }

  async function loadCandidates() {
    setLoading(true);
    setLoadError(null);
    try {
      const runs = await getBacktestRuns(strategy.market);
      setCandidates(runs.filter((r) => r.run_id !== strategy.source_run_id));
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : '백테스트 결과를 불러오지 못했습니다.');
    } finally {
      setLoading(false);
    }
  }

  async function handleSubmit() {
    if (!selectedRunId) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      await replaceLiveStrategyStrategy(strategy.id, selectedRunId);
      await onChanged();
      closeAndReset();
    } catch (err) {
      setSubmitError(err instanceof ApiError ? err.message : '전략 교체에 실패했습니다.');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next) {
          setOpen(true);
          loadCandidates();
        } else {
          closeAndReset();
        }
      }}
    >
      <DialogTrigger
        type="button"
        className={buttonVariants({ variant: 'outline', size: 'icon-lg' })}
        aria-label="전략 교체"
        title="전략 교체"
      >
        <RefreshCw />
      </DialogTrigger>
      <DialogContent className="max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>전략 교체 — {strategy.market}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3 text-sm">
          <p className="text-xs text-muted-foreground">
            같은 코인의 다른 백테스트 결과를 선택하면 시간봉·매수/매도 조건이 그 결과로
            교체됩니다. 자금관리 설정과 거래 이력은 그대로 유지됩니다.
          </p>
          {loading && <p className="text-muted-foreground">불러오는 중...</p>}
          {loadError && <p className="text-destructive">{loadError}</p>}
          {!loading && !loadError && candidates.length === 0 && (
            <p className="rounded-md bg-muted/50 p-3 text-muted-foreground">
              교체 가능한 백테스트 결과가 없습니다.
            </p>
          )}
          {!loading && candidates.length > 0 && (
            <div className="space-y-2">
              {candidates.map((run) => (
                <label
                  key={run.run_id}
                  className={`flex cursor-pointer items-start gap-2 rounded-md border p-3 ${
                    selectedRunId === run.run_id ? 'border-primary bg-muted/50' : 'border-border'
                  }`}
                >
                  <input
                    type="radio"
                    name="swap-candidate"
                    className="mt-1"
                    checked={selectedRunId === run.run_id}
                    onChange={() => setSelectedRunId(run.run_id)}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate font-medium">
                        {run.title || <span className="text-muted-foreground">(제목 없음)</span>}
                      </span>
                      <span className={returnRateColor(run.return_rate)}>
                        수익률 {run.return_rate?.toFixed(2) ?? '-'}%
                      </span>
                    </div>
                    {run.description && (
                      <p className="mt-0.5 truncate text-xs text-muted-foreground">{run.description}</p>
                    )}
                  </div>
                </label>
              ))}
            </div>
          )}
          {submitError && <p className="text-destructive">{submitError}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={closeAndReset} disabled={submitting}>
            취소
          </Button>
          <Button onClick={handleSubmit} disabled={submitting || !selectedRunId}>
            교체
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```

- [ ] **Step 3: 카드에 버튼 연결**

`s.open_position === null && (s.status === 'running' || s.status === 'paused') && (<ChangeCapitalDialog strategy={s} onChanged={refresh} />)` 블록(323~325행) 바로 다음에 추가:

```typescript
                {s.open_position === null && s.status !== 'draft' && (
                  <StrategySwapDialog strategy={s} onChanged={refresh} />
                )}
```

(`open_position === null` + `status !== 'draft'`이면 running/paused/stopped 전부 포함 — 스펙 결정과 일치.)

- [ ] **Step 4: 타입 체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음 (0 errors)

- [ ] **Step 5: 개발 서버로 수동 확인**

1. `run` 스킬(또는 `cd frontend && npm run dev`)로 개발 서버를 띄운다.
2. `/live-strategies` 페이지에서 draft가 아니고 열린 포지션이 없는 전략 카드에 "전략 교체"(순환 화살표) 아이콘이 보이는지 확인.
3. 같은 market의 백테스트 결과가 하나도 없는 경우: 팝업 클릭 → "교체 가능한 백테스트 결과가 없습니다" 문구 + 교체 버튼 비활성화 확인.
4. 같은 market에 백테스트 결과가 있는 경우: 라디오로 하나 선택 → 교체 클릭 → 다이얼로그가 닫히고 카드의 시간봉/조건이 바뀌는지 확인. 매매일지(`/journal`)에서 해당 코인의 누적 손익 이력이 끊기지 않았는지 확인.
5. 열린 포지션이 있는 카드에는 "전략 교체" 버튼이 아예 보이지 않는지 확인.

- [ ] **Step 6: 커밋**

```bash
git add frontend/components/LiveStrategiesPage.tsx
git commit -m "feat: 라이브 전략 카드에 전략 교체 다이얼로그 추가"
```

---

## 최종 확인

- [ ] 전체 백엔드 테스트 스위트 실행: `python -m pytest -v`
- [ ] 프론트 타입 체크: `cd frontend && npx tsc --noEmit`
- [ ] `git log --oneline -6`으로 6개 커밋이 순서대로 쌓였는지 확인
