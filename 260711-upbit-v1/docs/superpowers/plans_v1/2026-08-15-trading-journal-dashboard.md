# 매매일지 대시보드 구현 플랜

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 라이브 트레이딩 데이터(positions/orders/daily_performance)를 조회 시점에 집계해 계좌
전체 요약 + 전략별 매매일지 + 백테스트 대조를 보여주는 읽기 전용 대시보드(`/journal`)를 만든다.

**Architecture:** 새 테이블 없이 기존 `trading.db`(positions/orders/daily_performance/
live_strategies)와 `backtest_results.db`(백테스트 비교용)를 조회 시점에 집계한다. 백엔드는
`backend/trading_analytics_service.py`(순수 집계 로직) + `backend/main.py`의 얇은 엔드포인트
2개. 프론트엔드는 새 상단 탭 `/journal` 아래 계좌 요약 → 전략 카드 → 클릭 시 드릴다운 구조.

**Tech Stack:** FastAPI, sqlite3(`trading/db.py`), pandas(MDD 계산), Next.js App Router,
recharts(라인차트), shadcn/ui(Card/Badge/Table/Button).

## Global Constraints

- 새 DB 테이블/컬럼을 추가하지 않는다 — `positions`/`orders`/`daily_performance`/
  `live_strategies`/`backtest_results`만 조회 시점에 집계한다.
- 이 화면은 읽기 전용이다 — 승인/일시정지/중지 같은 제어는 기존 `/live-strategies`의 역할.
- 새로고침은 폴링 없이 수동 버튼 + 페이지 진입 시 1회 로드만 한다.
- 표본 부족 경고 임계치는 거래횟수 10건 미만.
- 퍼센트 필드는 모두 "1.17은 1.17%"를 의미하는 그대로의 숫자로 반환한다(프론트에서 ×100 안
  함). MDD는 음수로 반환한다(고점 대비 낙폭).
- `approved_at IS NOT NULL`인 전략만 매매일지 대상이다(승인 이력 없는 draft 전략은 제외).
- 프론트엔드에는 자동 테스트가 없다(기존 프로젝트 관례) — 각 프론트 태스크의 검증은
  `npx tsc --noEmit`(타입체크)로 하고, 최종 브라우저 확인은 마지막 태스크에서 한 번에 한다.
  **`npm run build`는 쓰지 않는다** — 개발 중인 `npm run dev`의 `.next` 캐시를 깨뜨리는 알려진
  문제가 있다.
- 이 플랜은 계획 단계에서 스펙(`docs/superpowers/specs_v1/2026-08-15-trading-journal-dashboard-design.md`)의
  "알려진 한계(종료된 전략의 자금이 계좌 합산에서 사라짐)"를 해소하는 방식으로 계좌 전체
  집계 로직을 다시 설계했다 — 자세한 내용은 Task 2 설명 참고. 스펙 문서는 Task 7에서 이 변경을
  반영해 업데이트한다.

---

## 설계 노트: 계좌 전체 집계가 "종료된 전략" 문제를 피하는 방법

스펙은 "일별 계좌 총자산 = 그날 존재하는 모든 전략의 `ending_balance` 합"으로 계산하면 전략이
`stopped`된 다음 날부터 그 자금이 계좌 합산에서 사라지는 것처럼 보인다는 한계를 알려진
한계로 남겼다. 구현 플랜을 짜면서 더 나은 방법을 찾았다: **잔고(stock)를 날짜별로 합산하는
대신, 그날의 손익(flow)만 날짜별로 합산해서 누적한다.**

- `daily_performance.realized_pnl`은 "그날 이 전략이 낸 손익"이라는 flow다. 전략이
  `stopped`돼도 이미 발생한 과거 손익은 사라지지 않는다 — 그냥 그 이후로 새 flow가
  추가되지 않을 뿐이다.
- 계좌 전체 원금(baseline) = 각 전략의 최초 원금(첫 `daily_performance` 행의
  `starting_balance`, 아직 한 번도 청산 거래가 없으면 `current_capital`)을 전부 더한 값.
  이건 날짜와 무관한 한 번의 합산이라 종료 시점 문제가 없다.
- 계좌 총자산(t) = baseline + (baseline부터 t까지의 일별 손익 flow 누적합)

이 방식은 정확히 같은 정보(일별 손익)로 계좌 전체 누적손익/MDD를 계산하면서 스펙이 언급한
한계를 원천적으로 없앤다. 전략별(단일 전략) MDD는 원래 스펙대로 그 전략의 일별
`ending_balance` 시퀀스를 그대로 쓴다 — 단일 전략 안에서는애초에 이 문제가 없다(그 전략이
멈추면 시계열이 거기서 끝나는 게 맞는 그림이다).

---

### Task 1: `trading/db.py`에 매매일지용 조회 함수 3개 추가

**Files:**
- Modify: `trading/db.py` (파일 끝, `list_active_strategies()` 뒤에 추가)
- Test: `tests/test_trading_db.py` (파일 끝에 추가)

**Interfaces:**
- Produces:
  - `list_daily_performance(live_strategy_id: str) -> list[dict]` — `trading_date` 오름차순
  - `list_closed_positions(live_strategy_id: str) -> list[dict]` — `entry_time` 내림차순,
    `status='closed'`만
  - `list_orders_for_strategy(live_strategy_id: str) -> list[dict]` — `created_at` 오름차순

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_trading_db.py` 파일 끝에 추가:

```python
def test_list_daily_performance_returns_rows_ordered_by_date(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    db.upsert_daily_performance(
        strategy_id, "2026-08-12", 1000.0, 1.0, 1, 1, 0, 100_000.0, 101_000.0,
    )
    db.upsert_daily_performance(
        strategy_id, "2026-08-10", -500.0, -0.5, 1, 0, 1, 100_500.0, 100_000.0,
    )

    rows = db.list_daily_performance(strategy_id)

    assert [r["trading_date"] for r in rows] == ["2026-08-10", "2026-08-12"]


def test_list_daily_performance_scoped_to_strategy(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_a = insert_live_strategy(db)
    strategy_b = insert_live_strategy(db)
    db.upsert_daily_performance(
        strategy_a, "2026-08-10", 1000.0, 1.0, 1, 1, 0, 100_000.0, 101_000.0,
    )
    db.upsert_daily_performance(
        strategy_b, "2026-08-10", -500.0, -0.5, 1, 0, 1, 200_000.0, 199_500.0,
    )

    rows = db.list_daily_performance(strategy_a)

    assert len(rows) == 1
    assert rows[0]["live_strategy_id"] == strategy_a


def test_list_closed_positions_excludes_open_and_orders_newest_first(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    open_id = db.insert_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)
    older_id = db.insert_position(strategy_id, "KRW-BTC", 49_000_000.0, 0.01)
    newer_id = db.insert_position(strategy_id, "KRW-BTC", 51_000_000.0, 0.01)
    db.close_position_row(older_id, 49_500_000.0, 0.01, 5000.0, 1.0, "take_profit")
    db.close_position_row(newer_id, 51_500_000.0, 0.01, 5000.0, 1.0, "take_profit")

    rows = db.list_closed_positions(strategy_id)

    assert [r["id"] for r in rows] == [newer_id, older_id]
    assert open_id not in [r["id"] for r in rows]


def test_list_orders_for_strategy_returns_all_orders(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    position_id = db.insert_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)
    order_id = db.insert_order(
        strategy_id, position_id, "KRW-BTC", "bid", "market", None, 0.01, 50_000_000.0,
    )
    db.update_order_filled(order_id, "uuid-1", 50_010_000.0, 0.01, 25.0, 0.02, "done")

    rows = db.list_orders_for_strategy(strategy_id)

    assert len(rows) == 1
    assert rows[0]["slippage_pct"] == 0.02
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_trading_db.py -k "list_daily_performance or list_closed_positions or list_orders_for_strategy" -v`
Expected: FAIL — `AttributeError: module 'trading.db' has no attribute 'list_daily_performance'`

- [ ] **Step 3: `trading/db.py`에 함수 3개 추가**

`trading/db.py` 파일 맨 끝(`list_active_strategies()` 함수 뒤)에 추가:

```python
def list_daily_performance(live_strategy_id: str) -> list[dict]:
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM daily_performance WHERE live_strategy_id = ? "
            "ORDER BY trading_date ASC",
            (live_strategy_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def list_closed_positions(live_strategy_id: str) -> list[dict]:
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM positions WHERE live_strategy_id = ? AND status = 'closed' "
            "ORDER BY entry_time DESC",
            (live_strategy_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def list_orders_for_strategy(live_strategy_id: str) -> list[dict]:
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM orders WHERE live_strategy_id = ? ORDER BY created_at ASC",
            (live_strategy_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
```

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_trading_db.py -k "list_daily_performance or list_closed_positions or list_orders_for_strategy" -v`
Expected: PASS (4 passed)

- [ ] **Step 5: 커밋**

```bash
git add trading/db.py tests/test_trading_db.py
git commit -m "feat: 매매일지 조회용 daily_performance/positions/orders list 함수 추가"
```

---

### Task 2: `backend/trading_analytics_service.py` — 집계 로직

**Files:**
- Create: `backend/trading_analytics_service.py`
- Test: `tests/test_trading_analytics_service.py`

**Interfaces:**
- Consumes: `trading.db.list_live_strategies() -> list[dict]`,
  `trading.db.get_live_strategy(id) -> dict | None`,
  `trading.db.list_daily_performance(id) -> list[dict]`(Task 1),
  `trading.db.list_closed_positions(id) -> list[dict]`(Task 1),
  `trading.db.list_orders_for_strategy(id) -> list[dict]`(Task 1),
  `engine.cache.load_result(run_id) -> dict | None`,
  `engine.metrics.calculate_metrics(equity_curve, trades, initial_capital, df, timeframe) -> dict`
- Produces:
  - `get_journal_summary() -> dict` — 계좌 전체 요약(형태는 아래 Step 참고)
  - `get_strategy_journal(strategy_id: str) -> dict | None` — 전략 드릴다운(찾을 수 없거나
    승인 이력 없으면 `None`)
  - `MIN_SAMPLE_SIZE = 10` (모듈 상수)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_trading_analytics_service.py` 새로 작성:

```python
import pandas as pd

import backend.trading_analytics_service as svc
import engine.cache as cache_module
import trading.db as db_module
from tests.trading_db_fixtures import insert_live_strategy


def _fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "trading.db")
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    return db_module


def _approve(db, strategy_id, capital=100_000.0):
    db.approve_live_strategy(strategy_id, capital)


def test_journal_summary_empty_when_no_approved_strategies(monkeypatch, tmp_path):
    db = _fresh(monkeypatch, tmp_path)
    insert_live_strategy(db, status="draft")

    summary = svc.get_journal_summary()

    assert summary["strategies"] == []
    assert summary["equity_curve"] == []
    assert summary["cumulative_pnl"] == 0.0
    assert summary["mdd_pct"] == 0.0
    assert summary["win_rate_pct"] == 0.0


def test_journal_summary_excludes_unapproved_draft(monkeypatch, tmp_path):
    db = _fresh(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="draft")
    _approve(db, strategy_id)
    insert_live_strategy(db, status="draft")  # 승인 안 된 채로 남음

    summary = svc.get_journal_summary()

    assert len(summary["strategies"]) == 1
    assert summary["strategies"][0]["id"] == strategy_id


def test_journal_summary_aggregates_across_strategies(monkeypatch, tmp_path):
    db = _fresh(monkeypatch, tmp_path)
    s1 = insert_live_strategy(db, status="draft")
    _approve(db, s1, 100_000.0)
    s2 = insert_live_strategy(db, status="draft")
    _approve(db, s2, 200_000.0)

    db.upsert_daily_performance(s1, "2026-08-10", 1000.0, 1.0, 1, 1, 0, 100_000.0, 101_000.0)
    db.upsert_daily_performance(s2, "2026-08-10", -2000.0, -1.0, 1, 0, 1, 200_000.0, 198_000.0)
    p1 = db.insert_position(s1, "KRW-BTC", 50_000_000.0, 0.002)
    db.close_position_row(p1, 50_500_000.0, 0.002, 1000.0, 1.0, "take_profit")
    p2 = db.insert_position(s2, "KRW-ETH", 3_000_000.0, 0.06)
    db.close_position_row(p2, 2_940_000.0, 0.06, -2000.0, -1.0, "stop_loss")

    summary = svc.get_journal_summary()

    assert summary["cumulative_pnl"] == -1000.0  # 1000 - 2000
    assert summary["win_rate_pct"] == 50.0  # 1승 1패
    assert len(summary["equity_curve"]) == 1
    assert summary["equity_curve"][0]["value"] == 300_000.0 - 1000.0  # 원금합 - 순손실


def test_journal_summary_known_limitation_resolved_after_strategy_stops(monkeypatch, tmp_path):
    """stopped된 전략의 과거 손익이 계좌 합산 누적에서 사라지지 않아야 한다(스펙의
    '알려진 한계'를 flow 기반 집계로 해소했는지 확인하는 회귀 테스트)."""
    db = _fresh(monkeypatch, tmp_path)
    s1 = insert_live_strategy(db, status="draft")
    _approve(db, s1, 100_000.0)
    p1 = db.insert_position(s1, "KRW-BTC", 50_000_000.0, 0.002)
    db.close_position_row(p1, 50_500_000.0, 0.002, 1000.0, 1.0, "take_profit")
    db.upsert_daily_performance(s1, "2026-08-10", 1000.0, 1.0, 1, 1, 0, 100_000.0, 101_000.0)
    db.stop_live_strategy_if_no_open_position(s1)

    s2 = insert_live_strategy(db, status="draft")
    _approve(db, s2, 50_000.0)
    # s2는 다음날부터 활동 시작 — s1은 이미 stopped라 이후 daily_performance 행이 없음

    summary = svc.get_journal_summary()

    last_point = summary["equity_curve"][-1]
    assert last_point["value"] == 150_000.0 + 1000.0  # s1의 과거 이익이 그대로 남아있어야 함


def test_strategy_journal_returns_none_for_missing_strategy(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    assert svc.get_strategy_journal("does-not-exist") is None


def test_strategy_journal_returns_none_for_unapproved_draft(monkeypatch, tmp_path):
    db = _fresh(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="draft")
    assert svc.get_strategy_journal(strategy_id) is None


def test_strategy_journal_includes_trade_log_and_metrics(monkeypatch, tmp_path):
    db = _fresh(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="draft", market="KRW-DOGE")
    _approve(db, strategy_id, 100_000.0)
    position_id = db.insert_position(strategy_id, "KRW-DOGE", 300.0, 300.0)
    db.close_position_row(position_id, 303.51, 300.0, 1053.0, 1.17, "sell_signal")
    db.upsert_daily_performance(
        strategy_id, "2026-08-14", 1053.0, 1.17, 1, 1, 0, 100_000.0, 101_053.0,
    )
    order_id = db.insert_order(
        strategy_id, position_id, "KRW-DOGE", "bid", "market", None, 300.0, 300.0,
    )
    db.update_order_filled(order_id, "uuid-1", 300.06, 300.0, 30.0, 0.02, "done")

    detail = svc.get_strategy_journal(strategy_id)

    assert detail["id"] == strategy_id
    assert detail["trade_count"] == 1
    assert detail["win_rate_pct"] == 100.0
    assert detail["avg_slippage_pct"] == 0.02
    assert detail["max_slippage_pct"] == 0.02
    assert len(detail["trade_log"]) == 1
    assert detail["trade_log"][0]["close_reason"] == "sell_signal"
    assert detail["backtest_comparison"] is None  # source_run_id 없음


def test_strategy_journal_backtest_comparison_present_with_source_run(monkeypatch, tmp_path):
    db = _fresh(monkeypatch, tmp_path)
    from engine.cache import save_result
    from datetime import datetime, timezone

    save_result(
        run_id="run-1", strategy_name="ConditionTreeStrategy", strategy_params={},
        market="KRW-DOGE", timeframe="minutes60",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 2, 1, tzinfo=timezone.utc),
        risk_config={"initial_capital": 100_000},
        result={
            "final_value": 108_000.0, "sharpe": 1.0, "max_drawdown": 3.2,
            "equity_curve": [
                {"timestamp": "2026-01-01T00:00:00", "value": 100_000.0},
                {"timestamp": "2026-02-01T00:00:00", "value": 108_000.0},
            ],
            "trades": [
                {
                    "entryTime": "2026-01-05T00:00:00", "exitTime": "2026-01-06T00:00:00",
                    "entryPrice": 300.0, "exitPrice": 305.0, "returnRate": 1.6,
                    "holdingPeriod": 24, "pnl": 1600.0,
                },
                {
                    "entryTime": "2026-01-10T00:00:00", "exitTime": "2026-01-11T00:00:00",
                    "entryPrice": 305.0, "exitPrice": 300.0, "returnRate": -1.5,
                    "holdingPeriod": 24, "pnl": -1500.0,
                },
            ],
        },
    )
    strategy_id = insert_live_strategy(
        db, status="draft", market="KRW-DOGE", source_run_id="run-1",
    )
    _approve(db, strategy_id, 100_000.0)
    position_id = db.insert_position(strategy_id, "KRW-DOGE", 300.0, 300.0)
    db.close_position_row(position_id, 303.51, 300.0, 1053.0, 1.17, "sell_signal")

    detail = svc.get_strategy_journal(strategy_id)

    comparison = detail["backtest_comparison"]
    assert comparison is not None
    assert comparison["backtest"]["trade_count"] == 2
    assert comparison["backtest"]["win_rate_pct"] == 50.0
    assert comparison["live"]["trade_count"] == 1
    assert comparison["sample_size_warning"] is True  # 1건 < MIN_SAMPLE_SIZE
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_trading_analytics_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.trading_analytics_service'`

(`insert_live_strategy` fixture에 `source_run_id` override를 넘기는 테스트가 있는데,
`tests/trading_db_fixtures.py`의 `insert_live_strategy()`는 INSERT 문에 `source_run_id`
컬럼을 포함하지 않는다 — 이 Step에서 함께 확인만 해두고, 다음 Step에서 fixture도 고친다.)

- [ ] **Step 3: 픽스처에 `source_run_id` 지원 추가**

`tests/trading_db_fixtures.py`의 `insert_live_strategy()`를 수정 — `defaults` 딕셔너리와
INSERT 문에 `source_run_id` 추가:

```python
def insert_live_strategy(db_module, **overrides) -> str:
    """유효한 live_strategies 행을 만들고 id를 반환한다. positions/circuit_breaker_state/
    daily_performance는 전부 live_strategy_id를 외래키로 참조하므로, 이 헬퍼 없이는
    그 테이블들의 CRUD 테스트를 작성할 수 없다."""
    defaults = {
        "id": str(uuid.uuid4()),
        "source_run_id": None,
        "market": "KRW-BTC",
        "timeframe": "minutes60",
        "buy_conditions_json": "{}",
        "sell_conditions_json": "{}",
        "risk_config_json": "{}",
        "current_capital": 100000.0,
        "status": "running",
        "manual_pause": 0,
    }
    defaults.update(overrides)

    conn = db_module._connect()
    try:
        conn.execute(
            "INSERT INTO live_strategies "
            "(id, source_run_id, market, timeframe, buy_conditions_json, sell_conditions_json, "
            "risk_config_json, current_capital, status, manual_pause) "
            "VALUES (:id, :source_run_id, :market, :timeframe, :buy_conditions_json, "
            ":sell_conditions_json, :risk_config_json, :current_capital, :status, :manual_pause)",
            defaults,
        )
        conn.commit()
    finally:
        conn.close()
    return defaults["id"]
```

- [ ] **Step 4: `backend/trading_analytics_service.py` 작성**

```python
"""
backend/trading_analytics_service.py

매매일지 대시보드(3단계 분석 대시보드)용 집계 로직. positions/daily_performance/orders를
조회 시점에 집계할 뿐 새 테이블을 두지 않는다(설계 스펙의 "중복 저장 안 함" 원칙).
main.py의 journal 엔드포인트가 이 모듈의 함수만 호출한다.

계좌 전체 집계는 잔고(ending_balance)가 아니라 일별 손익(realized_pnl) flow를 날짜별로
합산해서 누적한다 — 전략이 stopped된 뒤에도 그 전략이 과거에 낸 손익은 계좌 합산에서
사라지지 않는다(잔고를 그대로 합산하면 stopped 이후 그 전략의 daily_performance 행이
더 안 생겨서 사라지는 문제가 있었다).
"""
from __future__ import annotations

import pandas as pd

import trading.db as trading_db
from engine.cache import load_result
from engine.metrics import calculate_metrics

MIN_SAMPLE_SIZE = 10


def _mdd_pct(values: list[float]) -> float:
    if not values:
        return 0.0
    series = pd.Series(values, dtype=float)
    cummax = series.cummax()
    drawdown = pd.Series(0.0, index=series.index)
    nonzero = cummax != 0
    drawdown[nonzero] = (series[nonzero] - cummax[nonzero]) / cummax[nonzero] * 100.0
    return float(drawdown.min())


def _win_rate_pct(positions: list[dict]) -> float:
    if not positions:
        return 0.0
    wins = sum(1 for p in positions if p["realized_pnl"] >= 0)
    return wins / len(positions) * 100.0


def _strategy_baseline_capital(strategy: dict, daily_rows: list[dict]) -> float:
    """전략의 원금 근사치. 청산된 거래가 있으면 첫 daily_performance 행의
    starting_balance(첫 거래 직전 자본금)를, 아직 없으면 현재 current_capital(승인 시점
    원금에서 아직 바뀌지 않은 값)을 쓴다."""
    if daily_rows:
        return daily_rows[0]["starting_balance"] or 0.0
    return strategy["current_capital"] or 0.0


def _strategy_metrics(strategy: dict) -> dict:
    closed = trading_db.list_closed_positions(strategy["id"])
    daily_rows = trading_db.list_daily_performance(strategy["id"])
    baseline = _strategy_baseline_capital(strategy, daily_rows)

    cumulative_pnl = sum(p["realized_pnl"] for p in closed)
    cumulative_pnl_pct = (cumulative_pnl / baseline * 100.0) if baseline else 0.0
    mdd_pct = _mdd_pct([row["ending_balance"] for row in daily_rows])
    win_rate_pct = _win_rate_pct(closed)

    return {
        "closed_positions": closed,
        "daily_rows": daily_rows,
        "baseline": baseline,
        "cumulative_pnl": cumulative_pnl,
        "cumulative_pnl_pct": cumulative_pnl_pct,
        "mdd_pct": mdd_pct,
        "win_rate_pct": win_rate_pct,
    }


def get_journal_summary() -> dict:
    strategies = [s for s in trading_db.list_live_strategies() if s["approved_at"] is not None]

    if not strategies:
        return {
            "cumulative_pnl": 0.0, "cumulative_pnl_pct": 0.0, "mdd_pct": 0.0,
            "win_rate_pct": 0.0, "equity_curve": [], "strategies": [],
        }

    strategy_cards = []
    pnl_by_date: dict[str, float] = {}
    total_baseline = 0.0
    all_closed: list[dict] = []

    for strategy in strategies:
        m = _strategy_metrics(strategy)
        total_baseline += m["baseline"]
        all_closed.extend(m["closed_positions"])
        for row in m["daily_rows"]:
            pnl_by_date[row["trading_date"]] = (
                pnl_by_date.get(row["trading_date"], 0.0) + row["realized_pnl"]
            )
        strategy_cards.append({
            "id": strategy["id"],
            "market": strategy["market"],
            "timeframe": strategy["timeframe"],
            "status": strategy["status"],
            "cumulative_pnl": round(m["cumulative_pnl"], 4),
            "cumulative_pnl_pct": round(m["cumulative_pnl_pct"], 4),
            "trade_count": len(m["closed_positions"]),
        })

    equity_curve = []
    running = total_baseline
    for trading_date in sorted(pnl_by_date):
        running += pnl_by_date[trading_date]
        equity_curve.append({"trading_date": trading_date, "value": round(running, 4)})

    cumulative_pnl = sum(p["realized_pnl"] for p in all_closed)
    cumulative_pnl_pct = (cumulative_pnl / total_baseline * 100.0) if total_baseline else 0.0
    mdd_series = [total_baseline] + [e["value"] for e in equity_curve]

    return {
        "cumulative_pnl": round(cumulative_pnl, 4),
        "cumulative_pnl_pct": round(cumulative_pnl_pct, 4),
        "mdd_pct": round(_mdd_pct(mdd_series), 4),
        "win_rate_pct": round(_win_rate_pct(all_closed), 4),
        "equity_curve": equity_curve,
        "strategies": strategy_cards,
    }


def _backtest_comparison(strategy: dict, m: dict) -> dict | None:
    source_run_id = strategy["source_run_id"]
    if not source_run_id:
        return None
    result = load_result(source_run_id)
    if result is None:
        return None

    bt_metrics = calculate_metrics(
        equity_curve=result["equity_curve"], trades=result["trades"],
        initial_capital=result["initial_capital"], df=pd.DataFrame(),
        timeframe=result["timeframe"],
    )
    bt_trades = result["trades"]
    bt_avg_return_pct = (
        sum(t["returnRate"] for t in bt_trades) / len(bt_trades) if bt_trades else 0.0
    )

    live_positions = m["closed_positions"]
    live_avg_return_pct = (
        sum(p["realized_pnl_pct"] for p in live_positions) / len(live_positions)
        if live_positions else 0.0
    )

    return {
        "backtest": {
            "win_rate_pct": round(bt_metrics["win_rate"], 4),
            "avg_return_pct": round(bt_avg_return_pct, 4),
            "mdd_pct": round(bt_metrics["mdd"], 4),
            "trade_count": bt_metrics["total_trades"],
        },
        "live": {
            "win_rate_pct": round(m["win_rate_pct"], 4),
            "avg_return_pct": round(live_avg_return_pct, 4),
            "mdd_pct": round(m["mdd_pct"], 4),
            "trade_count": len(live_positions),
        },
        "sample_size_warning": len(live_positions) < MIN_SAMPLE_SIZE,
    }


def get_strategy_journal(strategy_id: str) -> dict | None:
    strategy = trading_db.get_live_strategy(strategy_id)
    if strategy is None or strategy["approved_at"] is None:
        return None

    m = _strategy_metrics(strategy)
    orders = trading_db.list_orders_for_strategy(strategy_id)
    slippages = [o["slippage_pct"] for o in orders if o["slippage_pct"] is not None]
    avg_slippage_pct = round(sum(slippages) / len(slippages), 4) if slippages else None
    max_slippage_pct = round(max(slippages, key=abs), 4) if slippages else None

    trade_log = [
        {
            "position_id": p["id"],
            "entry_time": p["entry_time"],
            "entry_price": p["entry_price"],
            "entry_qty": p["entry_qty"],
            "exit_time": p["exit_time"],
            "exit_price": p["exit_price"],
            "exit_qty": p["exit_qty"],
            "realized_pnl": p["realized_pnl"],
            "realized_pnl_pct": p["realized_pnl_pct"],
            "close_reason": p["close_reason"],
        }
        for p in m["closed_positions"]
    ]

    return {
        "id": strategy["id"],
        "market": strategy["market"],
        "timeframe": strategy["timeframe"],
        "status": strategy["status"],
        "cumulative_pnl": round(m["cumulative_pnl"], 4),
        "cumulative_pnl_pct": round(m["cumulative_pnl_pct"], 4),
        "mdd_pct": round(m["mdd_pct"], 4),
        "win_rate_pct": round(m["win_rate_pct"], 4),
        "avg_slippage_pct": avg_slippage_pct,
        "max_slippage_pct": max_slippage_pct,
        "trade_count": len(m["closed_positions"]),
        "backtest_comparison": _backtest_comparison(strategy, m),
        "trade_log": trade_log,
    }
```

- [ ] **Step 5: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_trading_analytics_service.py -v`
Expected: PASS (9 passed)

이어서 Task 1에서 고친 `tests/trading_db_fixtures.py`가 기존 테스트를 깨지 않았는지 전체
스위트로 확인:

Run: `python -m pytest tests/test_trading_db.py tests/test_risk_manager.py tests/test_backend.py -v`
Expected: PASS (전부 통과, 회귀 없음)

- [ ] **Step 6: 커밋**

```bash
git add backend/trading_analytics_service.py tests/test_trading_analytics_service.py tests/trading_db_fixtures.py
git commit -m "feat: 매매일지 집계 서비스(trading_analytics_service) 추가"
```

---

### Task 3: `backend/main.py`에 journal 엔드포인트 2개 추가

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_journal_endpoints.py` (신규)

**Interfaces:**
- Consumes: `backend.trading_analytics_service.get_journal_summary()`,
  `backend.trading_analytics_service.get_strategy_journal(id)` (Task 2)
- Produces: `GET /api/v1/journal/summary`, `GET /api/v1/journal/strategies/{strategy_id}`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_journal_endpoints.py` 새로 작성:

```python
from fastapi.testclient import TestClient

import engine.cache as cache_module
import trading.db as trading_db_module
from backend.main import app
from tests.trading_db_fixtures import insert_live_strategy


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    monkeypatch.setattr(trading_db_module, "DB_PATH", tmp_path / "trading.db")
    return TestClient(app)


def test_journal_summary_returns_empty_state(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    resp = client.get("/api/v1/journal/summary")

    assert resp.status_code == 200
    assert resp.json()["strategies"] == []


def test_journal_summary_includes_approved_strategy(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(trading_db_module, status="draft")
    trading_db_module.approve_live_strategy(strategy_id, 100_000.0)

    resp = client.get("/api/v1/journal/summary")

    body = resp.json()
    assert len(body["strategies"]) == 1
    assert body["strategies"][0]["id"] == strategy_id


def test_journal_strategy_detail_returns_404_for_missing_strategy(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    resp = client.get("/api/v1/journal/strategies/does-not-exist")

    assert resp.status_code == 404


def test_journal_strategy_detail_returns_200_for_approved_strategy(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(trading_db_module, status="draft", market="KRW-DOGE")
    trading_db_module.approve_live_strategy(strategy_id, 100_000.0)

    resp = client.get(f"/api/v1/journal/strategies/{strategy_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["market"] == "KRW-DOGE"
    assert body["trade_log"] == []
    assert body["backtest_comparison"] is None
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_journal_endpoints.py -v`
Expected: FAIL — 404 Not Found (라우트가 아직 없음)

- [ ] **Step 3: `backend/main.py`에 임포트와 엔드포인트 추가**

`backend/main.py`의 기존 import 블록에서 (Task 앞 조사 결과) 다음 줄을 찾는다:

```python
import trading.db as trading_db
```

바로 아래에 추가:

```python
from backend.trading_analytics_service import get_journal_summary, get_strategy_journal
```

그리고 파일 맨 끝(`stop_live_strategy_endpoint` 함수 뒤)에 추가:

```python
@app.get("/api/v1/journal/summary")
def get_journal_summary_endpoint() -> dict:
    return get_journal_summary()


@app.get("/api/v1/journal/strategies/{strategy_id}")
def get_journal_strategy_endpoint(strategy_id: str) -> dict:
    detail = get_strategy_journal(strategy_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="실거래 이력이 없는 전략입니다")
    return detail
```

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_journal_endpoints.py -v`
Expected: PASS (4 passed)

전체 백엔드 회귀 확인:

Run: `python -m pytest -q`
Expected: 전부 통과, 실패 0건

- [ ] **Step 5: 커밋**

```bash
git add backend/main.py tests/test_journal_endpoints.py
git commit -m "feat: 매매일지 API 엔드포인트(GET /api/v1/journal/summary, /journal/strategies/{id}) 추가"
```

---

### Task 4: 프론트엔드 타입 + API 클라이언트

**Files:**
- Create: `frontend/lib/types/journal.ts`
- Create: `frontend/lib/api/journal.ts`

**Interfaces:**
- Consumes: `frontend/lib/api/client.ts`의 `apiFetch<T>()`,
  `frontend/lib/types/liveStrategies.ts`의 `LiveStrategyStatus`
- Produces: `JournalSummary`, `JournalStrategyCard`, `JournalEquityPoint`,
  `JournalTradeLogEntry`, `JournalMetricSet`, `JournalBacktestComparison`,
  `JournalStrategyDetail` 타입 + `getJournalSummary()`, `getJournalStrategyDetail(id)` 함수

- [ ] **Step 1: `frontend/lib/types/journal.ts` 작성**

```typescript
import type { LiveStrategyStatus } from '@/lib/types/liveStrategies';

export interface JournalEquityPoint {
  trading_date: string;
  value: number;
}

export interface JournalStrategyCard {
  id: string;
  market: string;
  timeframe: string;
  status: LiveStrategyStatus;
  cumulative_pnl: number;
  cumulative_pnl_pct: number;
  trade_count: number;
}

export interface JournalSummary {
  cumulative_pnl: number;
  cumulative_pnl_pct: number;
  mdd_pct: number;
  win_rate_pct: number;
  equity_curve: JournalEquityPoint[];
  strategies: JournalStrategyCard[];
}

export interface JournalTradeLogEntry {
  position_id: string;
  entry_time: string;
  entry_price: number;
  entry_qty: number;
  exit_time: string;
  exit_price: number;
  exit_qty: number;
  realized_pnl: number;
  realized_pnl_pct: number;
  close_reason: string;
}

export interface JournalMetricSet {
  win_rate_pct: number;
  avg_return_pct: number;
  mdd_pct: number;
  trade_count: number;
}

export interface JournalBacktestComparison {
  backtest: JournalMetricSet;
  live: JournalMetricSet;
  sample_size_warning: boolean;
}

export interface JournalStrategyDetail {
  id: string;
  market: string;
  timeframe: string;
  status: LiveStrategyStatus;
  cumulative_pnl: number;
  cumulative_pnl_pct: number;
  mdd_pct: number;
  win_rate_pct: number;
  avg_slippage_pct: number | null;
  max_slippage_pct: number | null;
  trade_count: number;
  backtest_comparison: JournalBacktestComparison | null;
  trade_log: JournalTradeLogEntry[];
}
```

- [ ] **Step 2: `frontend/lib/api/journal.ts` 작성**

```typescript
import { apiFetch } from './client';
import type { JournalStrategyDetail, JournalSummary } from '@/lib/types/journal';

export function getJournalSummary(): Promise<JournalSummary> {
  return apiFetch<JournalSummary>('/api/v1/journal/summary');
}

export function getJournalStrategyDetail(id: string): Promise<JournalStrategyDetail> {
  return apiFetch<JournalStrategyDetail>(`/api/v1/journal/strategies/${id}`);
}
```

- [ ] **Step 3: 타입체크로 검증**

Run (저장소 루트에서): `cd frontend && npx tsc --noEmit`
Expected: 에러 없음(아직 아무도 이 파일들을 안 쓰므로 unused-export 경고도 없어야 함)

- [ ] **Step 4: 커밋**

```bash
git add frontend/lib/types/journal.ts frontend/lib/api/journal.ts
git commit -m "feat: 매매일지 프론트엔드 타입/API 클라이언트 추가"
```

---

### Task 5: `JournalStrategyDetail` 컴포넌트 (드릴다운)

**Files:**
- Create: `frontend/components/JournalStrategyDetail.tsx`

**Interfaces:**
- Consumes: `JournalStrategyDetail` 타입(Task 4), `formatDateTime` from
  `frontend/lib/format.ts`, `Card/CardContent/CardHeader/CardTitle` from
  `@/components/ui/card`, `Table/TableBody/TableCell/TableHead/TableHeader/TableRow` from
  `@/components/ui/table`
- Produces: `JournalStrategyDetailView({ detail: JournalStrategyDetail }) => JSX.Element`
  (default export)

- [ ] **Step 1: 컴포넌트 작성**

```tsx
'use client';

import type { JournalStrategyDetail } from '@/lib/types/journal';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import { formatDateTime } from '@/lib/format';

function fmtPct(value: number): string {
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`;
}

function fmtKrw(value: number): string {
  return `${Math.round(value).toLocaleString()}원`;
}

const CLOSE_REASON_LABELS: Record<string, string> = {
  take_profit: '익절',
  stop_loss: '손절',
  sell_signal: '매도신호',
  manual: '수동청산',
  circuit_breaker: '서킷브레이커',
};

function fmtCloseReason(reason: string): string {
  return CLOSE_REASON_LABELS[reason] ?? reason;
}

export default function JournalStrategyDetailView({
  detail,
}: {
  detail: JournalStrategyDetail;
}) {
  const comparison = detail.backtest_comparison;

  return (
    <div className="space-y-4 rounded-md border p-4">
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <div>
          <p className="text-xs text-muted-foreground">누적손익</p>
          <p className="font-semibold">
            {fmtKrw(detail.cumulative_pnl)} ({fmtPct(detail.cumulative_pnl_pct)})
          </p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">MDD</p>
          <p className="font-semibold">{fmtPct(detail.mdd_pct)}</p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">승률</p>
          <p className="font-semibold">{detail.win_rate_pct.toFixed(1)}%</p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">평균 · 최대 슬리피지</p>
          <p className="font-semibold">
            {detail.avg_slippage_pct !== null ? fmtPct(detail.avg_slippage_pct) : 'N/A'}
            {' · '}
            {detail.max_slippage_pct !== null ? fmtPct(detail.max_slippage_pct) : 'N/A'}
          </p>
        </div>
      </div>

      <div>
        <h3 className="mb-2 text-sm font-semibold">백테스트 vs 실매매</h3>
        {comparison === null ? (
          <p className="text-sm text-muted-foreground">
            백테스트 비교 불가(연결된 백테스트 결과가 없습니다).
          </p>
        ) : (
          <>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead></TableHead>
                  <TableHead>백테스트</TableHead>
                  <TableHead>실매매</TableHead>
                  <TableHead>차이</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                <TableRow>
                  <TableCell>승률</TableCell>
                  <TableCell>{comparison.backtest.win_rate_pct.toFixed(1)}%</TableCell>
                  <TableCell>{comparison.live.win_rate_pct.toFixed(1)}%</TableCell>
                  <TableCell>
                    {fmtPct(comparison.live.win_rate_pct - comparison.backtest.win_rate_pct)}p
                  </TableCell>
                </TableRow>
                <TableRow>
                  <TableCell>평균수익률</TableCell>
                  <TableCell>{fmtPct(comparison.backtest.avg_return_pct)}</TableCell>
                  <TableCell>{fmtPct(comparison.live.avg_return_pct)}</TableCell>
                  <TableCell>
                    {fmtPct(comparison.live.avg_return_pct - comparison.backtest.avg_return_pct)}p
                  </TableCell>
                </TableRow>
                <TableRow>
                  <TableCell>MDD</TableCell>
                  <TableCell>{fmtPct(comparison.backtest.mdd_pct)}</TableCell>
                  <TableCell>{fmtPct(comparison.live.mdd_pct)}</TableCell>
                  <TableCell>
                    {fmtPct(comparison.live.mdd_pct - comparison.backtest.mdd_pct)}p
                  </TableCell>
                </TableRow>
                <TableRow>
                  <TableCell>거래횟수</TableCell>
                  <TableCell>{comparison.backtest.trade_count}건</TableCell>
                  <TableCell>{comparison.live.trade_count}건</TableCell>
                  <TableCell>-</TableCell>
                </TableRow>
              </TableBody>
            </Table>
            {comparison.sample_size_warning && (
              <p className="mt-2 text-xs text-amber-600">
                실매매 표본이 10건 미만이라 통계적으로 신뢰하기 이릅니다.
              </p>
            )}
          </>
        )}
      </div>

      <div>
        <h3 className="mb-2 text-sm font-semibold">매매일지</h3>
        {detail.trade_log.length === 0 ? (
          <p className="text-sm text-muted-foreground">청산된 거래가 없습니다.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>진입</TableHead>
                <TableHead>청산</TableHead>
                <TableHead>손익</TableHead>
                <TableHead>청산사유</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {detail.trade_log.map((t) => (
                <TableRow key={t.position_id}>
                  <TableCell>
                    {formatDateTime(t.entry_time)}
                    <br />
                    {Math.round(t.entry_price).toLocaleString()}원 × {t.entry_qty}
                  </TableCell>
                  <TableCell>
                    {formatDateTime(t.exit_time)}
                    <br />
                    {Math.round(t.exit_price).toLocaleString()}원 × {t.exit_qty}
                  </TableCell>
                  <TableCell>
                    {fmtKrw(t.realized_pnl)} ({fmtPct(t.realized_pnl_pct)})
                  </TableCell>
                  <TableCell>{fmtCloseReason(t.close_reason)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: 타입체크로 검증**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 3: 커밋**

```bash
git add frontend/components/JournalStrategyDetail.tsx
git commit -m "feat: 매매일지 전략 드릴다운 컴포넌트(JournalStrategyDetail) 추가"
```

---

### Task 6: `JournalPage` + 라우트 + 네비게이션

**Files:**
- Create: `frontend/components/JournalPage.tsx`
- Create: `frontend/app/journal/page.tsx`
- Modify: `frontend/components/NavTabs.tsx`

**Interfaces:**
- Consumes: `getJournalSummary`, `getJournalStrategyDetail`(Task 4),
  `JournalStrategyDetailView`(Task 5), `ApiError` from `@/lib/api/client`,
  `formatTimeframe` from `@/lib/format`
- Produces: `/journal` 라우트, `NavTabs`에 "매매일지" 탭

- [ ] **Step 1: `frontend/components/JournalPage.tsx` 작성**

```tsx
'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { ApiError } from '@/lib/api/client';
import { getJournalStrategyDetail, getJournalSummary } from '@/lib/api/journal';
import type { JournalStrategyDetail, JournalSummary } from '@/lib/types/journal';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { formatTimeframe } from '@/lib/format';
import JournalStrategyDetailView from '@/components/JournalStrategyDetail';

function fmtPct(value: number): string {
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`;
}

function fmtKrw(value: number): string {
  return `${Math.round(value).toLocaleString()}원`;
}

export default function JournalPage() {
  const [summary, setSummary] = useState<JournalSummary | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<JournalStrategyDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getJournalSummary();
      setSummary(data);
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : '매매일지를 불러오지 못했습니다.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function selectStrategy(id: string) {
    if (selectedId === id) {
      setSelectedId(null);
      setDetail(null);
      return;
    }
    setSelectedId(id);
    setDetail(null);
    setDetailError(null);
    setDetailLoading(true);
    try {
      const data = await getJournalStrategyDetail(id);
      setDetail(data);
    } catch (err) {
      setDetailError(err instanceof ApiError ? err.message : '전략 상세를 불러오지 못했습니다.');
    } finally {
      setDetailLoading(false);
    }
  }

  if (loadError) return <p className="text-sm text-destructive">{loadError}</p>;
  if (!summary) return <p className="text-sm text-muted-foreground">불러오는 중...</p>;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold">계좌 전체 요약</h2>
        <Button size="sm" variant="outline" disabled={loading} onClick={refresh}>
          새로고침
        </Button>
      </div>

      {summary.strategies.length === 0 ? (
        <p className="text-sm text-muted-foreground">아직 실거래 이력이 없습니다.</p>
      ) : (
        <>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <Card>
              <CardHeader>
                <CardTitle className="text-sm font-medium">누적손익</CardTitle>
              </CardHeader>
              <CardContent className="text-lg font-semibold">
                {fmtKrw(summary.cumulative_pnl)} ({fmtPct(summary.cumulative_pnl_pct)})
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="text-sm font-medium">MDD</CardTitle>
              </CardHeader>
              <CardContent className="text-lg font-semibold">
                {fmtPct(summary.mdd_pct)}
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="text-sm font-medium">승률</CardTitle>
              </CardHeader>
              <CardContent className="text-lg font-semibold">
                {summary.win_rate_pct.toFixed(1)}%
              </CardContent>
            </Card>
          </div>

          {summary.equity_curve.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              아직 청산된 거래가 없어 그래프를 표시할 수 없습니다.
            </p>
          ) : (
            <ResponsiveContainer width="100%" height={280}>
              <LineChart data={summary.equity_curve}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="trading_date" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip />
                <Line
                  type="monotone"
                  dataKey="value"
                  stroke="var(--color-primary)"
                  name="계좌 총자산"
                  dot={false}
                />
              </LineChart>
            </ResponsiveContainer>
          )}

          <h2 className="text-base font-semibold">전략별 매매일지</h2>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {summary.strategies.map((s) => (
              <Card
                key={s.id}
                className={`cursor-pointer ${selectedId === s.id ? 'border-primary' : ''}`}
                onClick={() => selectStrategy(s.id)}
              >
                <CardHeader>
                  <CardTitle className="flex items-center justify-between">
                    <span>
                      {s.market} · {formatTimeframe(s.timeframe)}
                    </span>
                    <Badge variant="secondary">{s.status}</Badge>
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-1 text-sm">
                  <p>
                    누적손익: {fmtKrw(s.cumulative_pnl)} ({fmtPct(s.cumulative_pnl_pct)})
                  </p>
                  <p>거래횟수: {s.trade_count}건</p>
                </CardContent>
              </Card>
            ))}
          </div>

          {selectedId && (
            <div>
              {detailLoading && <p className="text-sm text-muted-foreground">불러오는 중...</p>}
              {detailError && <p className="text-sm text-destructive">{detailError}</p>}
              {detail && <JournalStrategyDetailView detail={detail} />}
            </div>
          )}
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 2: `frontend/app/journal/page.tsx` 작성**

```tsx
import JournalPage from '@/components/JournalPage';

export default function Page() {
  return (
    <div>
      <h1 className="mb-4 text-lg font-semibold">매매일지</h1>
      <JournalPage />
    </div>
  );
}
```

- [ ] **Step 3: `frontend/components/NavTabs.tsx`에 탭 추가**

import 줄 수정:

```typescript
import { BarChart3, BookOpen, ClipboardList, FlaskConical, Grid3x3, Rocket, Settings } from 'lucide-react';
```

`STEPS` 배열에서 `/live-strategies` 항목 바로 뒤에 추가:

```typescript
const STEPS = [
  { href: '/', title: '백테스트 설정', icon: Settings },
  { href: '/grid-search', title: 'Grid Search', icon: Grid3x3 },
  { href: '/backtests', title: '백테스트 결과', icon: FlaskConical },
  { href: '/live-strategies', title: '라이브 전략', icon: Rocket },
  { href: '/journal', title: '매매일지', icon: ClipboardList },
  { href: '/analysis', title: '분석', icon: BarChart3 },
  { href: '/guide', title: '지표 가이드', icon: BookOpen },
];
```

- [ ] **Step 4: 타입체크로 검증**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 5: 커밋**

```bash
git add frontend/components/JournalPage.tsx frontend/app/journal/page.tsx frontend/components/NavTabs.tsx
git commit -m "feat: 매매일지 페이지(/journal)와 네비게이션 탭 추가"
```

---

### Task 7: 브라우저 수동 검증 + 스펙 문서 갱신

**Files:**
- Modify: `docs/superpowers/specs_v1/2026-08-15-trading-journal-dashboard-design.md`

이 태스크는 자동화된 스텝이 없다 — CLAUDE.md 지침대로 UI 변경은 브라우저에서 직접 확인해야
한다.

- [ ] **Step 1: 백엔드 기동**

Run(저장소 루트, 별도 터미널): `python -m uvicorn backend.main:app --reload --port 8000`

- [ ] **Step 2: 프론트엔드 기동**

Run(`frontend/`, 별도 터미널): `npm run dev`

- [ ] **Step 3: 빈 상태 확인**

`data/trading.db`에 승인된 라이브 전략이 없는 상태(또는 없는 새 DB)로
`http://localhost:3000/journal` 접속 → "아직 실거래 이력이 없습니다" 문구가 보이는지 확인.
상단 네비게이션에 "매매일지" 탭이 올바른 위치(라이브 전략과 분석 사이)에 보이는지 확인.

- [ ] **Step 4: 실데이터 확인**

기존 소액 실전 테스트로 쌓인 `data/trading.db`(KRW-DOGE 1사이클)가 있으면 그 DB를 가리키게
백엔드를 재기동한 뒤 `/journal` 재접속:
- 계좌 전체 요약 카드(누적손익/MDD/승률)와 라인차트가 보이는지
- 전략 카드가 보이고 클릭하면 드릴다운(지표 카드 + BT 대조표 또는 "백테스트 비교 불가" +
  매매일지 테이블)이 펼쳐지는지
- 같은 카드를 다시 클릭하면 드릴다운이 접히는지
- "새로고침" 버튼을 누르면 재조회되는지(네트워크 탭에서 확인)

이 실제 소액 테스트 전략에 `source_run_id`가 있으면 BT 대조표가, 없으면 "백테스트 비교
불가" 안내가 보여야 한다 — 어느 쪽이든 확인.

- [ ] **Step 5: 스펙 문서의 "알려진 한계" 갱신**

`docs/superpowers/specs_v1/2026-08-15-trading-journal-dashboard-design.md`의 "엣지 케이스"
섹션에서 다음 항목을 찾는다:

```
- **알려진 한계(v1에서 미해결)**: `stopped`된 전략의 마지막 `ending_balance`는 종료 이후
  일자의 계좌 합산 그래프에 반영되지 않는다(그 전략의 `daily_performance` 갱신이 멈추므로).
  지금은 라이브 전략이 1개뿐이라 체감되지 않지만, 여러 전략을 동시 운용하기 시작하면 계좌
  총자산이 실제보다 낮게 보이는 문제가 생긴다. forward-fill 등의 해결은 그 시점에 별도 진행.
```

다음으로 교체:

```
- ~~알려진 한계: stopped된 전략의 자금이 계좌 합산에서 사라짐~~ — **구현 플랜(2026-08-15)
  단계에서 해소**. 잔고(ending_balance)를 날짜별로 합산하는 대신 일별 손익(realized_pnl)
  flow를 누적하는 방식으로 바꿔, 전략이 stopped된 뒤에도 과거 손익이 계좌 합산에 그대로
  남는다. 상세 설계는 `docs/superpowers/plans_v1/2026-08-15-trading-journal-dashboard.md`의
  "설계 노트" 참고.
```

- [ ] **Step 6: 커밋**

```bash
git add docs/superpowers/specs_v1/2026-08-15-trading-journal-dashboard-design.md
git commit -m "docs: 매매일지 스펙의 계좌 합산 알려진 한계 해소 반영"
```
