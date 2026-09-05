# ⑤-4c 실시간 손절/익절 백로그 8건 수정 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **워크트리를 만들지 말고 main 브랜치에서 직접 작업한다** (사용자 지시, [[upbit-v1-worktree-workflow-changed]]).

**Goal:** ⑤-4c 최종 리뷰가 남긴 Important 5건 + Minor 3건을 스펙
`docs/superpowers/specs_v1/2026-08-09-live-trading-risk-exit-backlog-fix-design.md`대로 수정한다
— 잔여 미체결 매도 주문을 정리한 정보를 `positions` 테이블에 영구 기록해, 자금 부정확
기록/포지션 방치/과다매도/이종 포지션 혼선을 구조적으로 없앤다.

**Architecture:** `positions`에 누적 컬럼 3개(`stale_resolved_qty/proceeds/fee`)를 추가하고,
`exit_for_risk()`가 잔여 주문을 정리할 때마다 그 자리에서 즉시 이 컬럼에 누적한다. 전량
소진됐으면 그 자리에서 `position_manager.close_position()`을 직접 호출해 포지션을 종료하고,
일부만 소진됐으면 `exit()`에 새로 추가한 `pre_resolved_*` 파라미터로 넘겨 최종 체결분과
가중평균으로 합산한 뒤 `close_position()`을 호출한다.

**Tech Stack:** Python, `pytest`(+`pytest-asyncio`, `asyncio_mode = auto`), `sqlite3`. 새
의존성 없음.

## Global Constraints

- 8건 전부 `trading/db.py` + `trading/order_executor.py`(+ `trading/daemon.py`의 주석
  1곳)에만 있다 — `trading/daemon.py`의 로직, `trading/signal_engine.py`,
  `trading/risk_manager.py`, `trading/position_manager.py`는 코드 변경 없음
  (`position_manager.close_position()`의 시그니처는 그대로다 — 호출자가 이미 합산한
  값을 넘긴다).
- 신규 함수/파라미터는 전부 기존 함수의 기본 동작을 바꾸지 않는다(하위호환 — 특히
  `exit()`의 `pre_resolved_qty/proceeds/fee`는 기본값 `0.0`이면 기존 호출부
  (`handle_signal_result`)와 기존 테스트가 전혀 안 바뀌어도 되게 한다).
- 아직 실거래 데이터가 없는 개발 단계이므로 `positions` 스키마 변경은 마이그레이션 없이
  `CREATE TABLE IF NOT EXISTS`에 바로 반영한다.
- 커밋은 태스크 단위로 작게, 테스트가 통과한 뒤에만 한다.

---

## File Structure

- **Modify:** `trading/db.py` — `positions` 테이블에 컬럼 3개 추가,
  `accumulate_stale_resolution()` 신규, `list_wait_orders()`에 `position_id` 파라미터 추가.
- **Modify:** `tests/test_trading_db.py` — 위 추가분 테스트.
- **Modify:** `trading/order_executor.py` — `exit()`에 `pre_resolved_*` 파라미터 3개 추가,
  `_resolve_stale_ask_order()` 반환값을 `(qty, proceeds, fee)` 튜플로 확장 + 4xx terminal
  마킹, `exit_for_risk()` 재구성, `enter()`/`exit()`에 실패 시 주문 행 terminal 마킹 추가.
- **Modify:** `tests/test_order_executor.py` — 위 추가분 테스트 + 기존 테스트 1개
  (`test_exit_for_risk_places_no_order_when_stale_order_filled_during_cancel_race`) 기대값
  갱신.
- **Modify:** `trading/daemon.py` — `_run_risk_exit_loop()` docstring의 stale
  `action=="nothing_to_sell"` 서술 정정(코드 변경 없음).

---

### Task 1: `trading/db.py` — `positions`에 누적 컬럼 3개 + `accumulate_stale_resolution()`

**Files:**
- Modify: `trading/db.py`
- Modify: `tests/test_trading_db.py`

**Interfaces:**
- Produces: `trading.db.accumulate_stale_resolution(position_id: str, qty: float,
  proceeds: float, fee: float) -> None`(신규). `positions` 테이블에 `stale_resolved_qty`/
  `stale_resolved_proceeds`/`stale_resolved_fee`(전부 `REAL NOT NULL DEFAULT 0`) 컬럼 추가
  — 기존 `db.get_position()`/`db.get_open_position()`이 `SELECT *`라 자동으로 포함된다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_trading_db.py` 파일 끝에 추가:
```python
def test_positions_have_stale_resolution_columns_defaulting_to_zero(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    position_id = db.insert_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)

    position = db.get_position(position_id)

    assert position["stale_resolved_qty"] == 0
    assert position["stale_resolved_proceeds"] == 0
    assert position["stale_resolved_fee"] == 0


def test_accumulate_stale_resolution_adds_to_existing_totals(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    position_id = db.insert_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)

    db.accumulate_stale_resolution(position_id, 0.004, 200_000.0, 50.0)
    db.accumulate_stale_resolution(position_id, 0.002, 100_000.0, 25.0)

    position = db.get_position(position_id)
    assert position["stale_resolved_qty"] == pytest.approx(0.006)
    assert position["stale_resolved_proceeds"] == pytest.approx(300_000.0)
    assert position["stale_resolved_fee"] == pytest.approx(75.0)


def test_accumulate_stale_resolution_does_not_affect_other_positions(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    position_a = db.insert_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)
    position_b = db.insert_position(strategy_id, "KRW-BTC", 51_000_000.0, 0.02)

    db.accumulate_stale_resolution(position_a, 0.004, 200_000.0, 50.0)

    assert db.get_position(position_a)["stale_resolved_qty"] == pytest.approx(0.004)
    assert db.get_position(position_b)["stale_resolved_qty"] == 0
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_trading_db.py -v -k stale_resolution`
Expected: FAIL — `KeyError: 'stale_resolved_qty'`(컬럼 없음) 또는
`AttributeError: module 'trading.db' has no attribute 'accumulate_stale_resolution'`

- [ ] **Step 3: `trading/db.py` 수정**

`CREATE TABLE IF NOT EXISTS positions (...)` 정의(`close_reason TEXT,` 다음 줄,
`created_at` 앞)에 컬럼 3개를 추가한다:
```sql
    close_reason     TEXT,
    stale_resolved_qty      REAL NOT NULL DEFAULT 0,
    stale_resolved_proceeds REAL NOT NULL DEFAULT 0,
    stale_resolved_fee      REAL NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
```

`close_position_row()` 함수 정의 바로 앞(또는 `get_position()` 함수 뒤 아무 곳)에 새 함수를
추가한다:
```python
def accumulate_stale_resolution(position_id: str, qty: float, proceeds: float, fee: float) -> None:
    """잔여 미체결 매도 주문 정리로 확인된 수량/대금/수수료를 포지션에 즉시 누적한다
    (⑤-4c 백로그 수정 Important #1/#3/#4 — 이 정보가 그 틱의 지역 변수에만 머물면 예외로
    끊기거나 다음 틱에서 사라진다). exit_for_risk()가 잔여 주문 하나를 정리할 때마다
    호출한다."""
    conn = _connect()
    try:
        conn.execute(
            "UPDATE positions SET stale_resolved_qty = stale_resolved_qty + ?, "
            "stale_resolved_proceeds = stale_resolved_proceeds + ?, "
            "stale_resolved_fee = stale_resolved_fee + ? WHERE id = ?",
            (qty, proceeds, fee, position_id),
        )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_trading_db.py -v`
Expected: 전부 PASS(회귀 없음)

- [ ] **Step 5: 커밋**

```bash
git add trading/db.py tests/test_trading_db.py
git commit -m "feat: positions에 잔여주문 정리 누적 컬럼 3개 + accumulate_stale_resolution 추가"
```

---

### Task 2: `trading/db.py` — `list_wait_orders()`에 `position_id` 파라미터 추가

**Files:**
- Modify: `trading/db.py`
- Modify: `tests/test_trading_db.py`

**Interfaces:**
- Produces: `trading.db.list_wait_orders(live_strategy_id: str, order_type: str | None = None,
  position_id: str | None = None) -> list[dict]`(기존 시그니처에 파라미터 추가, 기본값
  `None`이면 기존 동작과 동일 — 하위호환).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_trading_db.py`의 `test_list_wait_orders_filters_by_status_and_optional_type`
함수 다음에 추가:
```python
def test_list_wait_orders_filters_by_position_id(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    position_a = db.insert_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)
    position_b = db.insert_position(strategy_id, "KRW-BTC", 51_000_000.0, 0.02)
    order_a = db.insert_order(strategy_id, position_a, "KRW-BTC", "ask", "limit", 100.0, 1.0, 100.0)
    order_b = db.insert_order(strategy_id, position_b, "KRW-BTC", "ask", "limit", 100.0, 1.0, 100.0)

    result = db.list_wait_orders(strategy_id, position_id=position_a)

    assert {o["id"] for o in result} == {order_a}
    assert order_b not in {o["id"] for o in result}
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_trading_db.py -v -k filters_by_position_id`
Expected: FAIL — `TypeError: list_wait_orders() got an unexpected keyword argument 'position_id'`

- [ ] **Step 3: `trading/db.py` 수정**

`list_wait_orders()` 전체를:
```python
def list_wait_orders(live_strategy_id: str, order_type: str | None = None) -> list[dict]:
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        if order_type is not None:
            rows = conn.execute(
                "SELECT * FROM orders WHERE live_strategy_id = ? AND status = 'wait' "
                "AND order_type = ?",
                (live_strategy_id, order_type),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM orders WHERE live_strategy_id = ? AND status = 'wait'",
                (live_strategy_id,),
            ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
```
에서:
```python
def list_wait_orders(
    live_strategy_id: str, order_type: str | None = None, position_id: str | None = None,
) -> list[dict]:
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        query = "SELECT * FROM orders WHERE live_strategy_id = ? AND status = 'wait'"
        params: list = [live_strategy_id]
        if order_type is not None:
            query += " AND order_type = ?"
            params.append(order_type)
        if position_id is not None:
            query += " AND position_id = ?"
            params.append(position_id)
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
```
로 교체한다(기존 두 분기를 동적 쿼리 조립으로 일반화 — `order_type`/`position_id` 둘 다
`None`이면 기존과 완전히 동일한 쿼리가 나간다).

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_trading_db.py -v`
Expected: 전부 PASS(기존 `test_list_wait_orders_filters_by_status_and_optional_type` 포함
회귀 없음)

- [ ] **Step 5: 커밋**

```bash
git add trading/db.py tests/test_trading_db.py
git commit -m "feat: list_wait_orders에 position_id 필터 추가"
```

---

### Task 3: `trading/order_executor.py` — `exit()`에 `pre_resolved_*` 파라미터 추가(가중평균 close)

**Files:**
- Modify: `trading/order_executor.py`
- Modify: `tests/test_order_executor.py`

**Interfaces:**
- Consumes: `trading.position_manager.close_position(position_id, exit_price, exit_qty, fee,
  close_reason)`(기존, 시그니처 불변).
- Produces: `trading.order_executor.exit(strategy, position, expected_price, *, client=None,
  dry_run=False, close_reason="signal", pre_resolved_qty: float = 0.0,
  pre_resolved_proceeds: float = 0.0, pre_resolved_fee: float = 0.0) -> dict`(기존
  시그니처에 파라미터 3개 추가, 전부 기본값 있어 하위호환).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_order_executor.py`의 `test_exit_records_custom_close_reason` 함수 다음에 추가:
```python
async def test_exit_blends_pre_resolved_amounts_into_realized_pnl(monkeypatch, tmp_path):
    """exit()에 pre_resolved_*를 넘기면, 이번 체결분(dry_run이라 expected_price*entry_qty)과
    합산한 가중평균가로 close_position()이 호출돼야 한다(⑤-4c 백로그 Important #1)."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)
    position_manager.open_position(strategy["id"], "KRW-BTC", 49_000_000.0, 0.01)
    position = position_manager.get_open_position(strategy["id"])
    # 잔여 주문 정리로 이미 0.004를 20만원에(수수료 50원) 판 것으로 가정하고, 이번
    # exit()는 나머지 0.006을 5,200만원에 판다(dry_run이라 filled_price==expected_price).
    sell_position = {**position, "entry_qty": 0.006}

    order = await order_executor.exit(
        strategy, sell_position, 52_000_000.0, dry_run=True,
        pre_resolved_qty=0.004, pre_resolved_proceeds=200_000.0, pre_resolved_fee=50.0,
    )

    # total_qty=0.01, total_proceeds=52,000,000*0.006+200,000=512,000,
    # blended_price=51,200,000, total_fee=0(dry_run)+50=50
    # realized_pnl = 51,200,000*0.01 - 49,000,000*0.01 - 50 = 21,950.0
    assert order["realized_pnl"] == pytest.approx(21_950.0)
    position_row = dbm.get_position(position["id"])
    assert position_row["exit_qty"] == pytest.approx(0.01)
    assert position_row["exit_price"] == pytest.approx(51_200_000.0)
    assert position_row["status"] == "closed"
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_order_executor.py -v -k blends_pre_resolved`
Expected: FAIL — `TypeError: exit() got an unexpected keyword argument 'pre_resolved_qty'`

- [ ] **Step 3: `trading/order_executor.py` 수정**

`exit()`의 시그니처를:
```python
async def exit(
    strategy: dict, position: dict, expected_price: float,
    *, client: httpx.AsyncClient | None = None, dry_run: bool = False,
    close_reason: str = "signal",
) -> dict:
```
에서:
```python
async def exit(
    strategy: dict, position: dict, expected_price: float,
    *, client: httpx.AsyncClient | None = None, dry_run: bool = False,
    close_reason: str = "signal",
    pre_resolved_qty: float = 0.0, pre_resolved_proceeds: float = 0.0, pre_resolved_fee: float = 0.0,
) -> dict:
```
로 교체한다.

`exit()` 끝부분의:
```python
    if result["status"] != "done":
        return db.get_order_by_id(result["order_id"])

    close_result = position_manager.close_position(
        position["id"], result["filled_price"], result["filled_volume"], result["fee"], close_reason,
    )
    order = db.get_order_by_id(result["order_id"])
    order.update(close_result)
    return order
```
를:
```python
    if result["status"] != "done":
        return db.get_order_by_id(result["order_id"])

    # ⑤-4c 백로그 수정(Important #1) — 잔여 주문 정리로 이미 확보한 체결분(pre_resolved_*,
    # 기본값 0이라 일반 캔들경로는 영향 없음)을 이번 체결분과 가중평균으로 합산해서
    # close_position()에 넘긴다. 그러지 않으면 exit_qty만 이번 체결분이고 원가는
    # close_position()이 DB에서 다시 읽는 포지션 전체 entry_qty라 "부분 매도금액 - 전체
    # 원가"로 PnL이 왜곡된다.
    total_qty = result["filled_volume"] + pre_resolved_qty
    total_proceeds = result["filled_price"] * result["filled_volume"] + pre_resolved_proceeds
    blended_price = total_proceeds / total_qty if total_qty else result["filled_price"]
    total_fee = result["fee"] + pre_resolved_fee

    close_result = position_manager.close_position(
        position["id"], blended_price, total_qty, total_fee, close_reason,
    )
    order = db.get_order_by_id(result["order_id"])
    order.update(close_result)
    return order
```
로 교체한다.

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_order_executor.py -v`
Expected: 전부 PASS(회귀 없음 — 기본값 0.0이 기존 동작과 수학적으로 동일:
`total_qty=filled_volume`, `blended_price=filled_price`, `total_fee=fee`)

- [ ] **Step 5: 커밋**

```bash
git add trading/order_executor.py tests/test_order_executor.py
git commit -m "feat: exit()에 pre_resolved_* 파라미터 추가(잔여주문 정리분 가중평균 합산)"
```

---

### Task 4: `trading/order_executor.py` — `exit_for_risk()` 재구성(Important #1~#5, Minor #6)

**Files:**
- Modify: `trading/order_executor.py`
- Modify: `tests/test_order_executor.py`

**Interfaces:**
- Consumes: `trading.db.list_wait_orders(live_strategy_id, position_id=...)`(Task2),
  `trading.db.accumulate_stale_resolution(position_id, qty, proceeds, fee)`(Task1),
  `trading.order_executor.exit(..., pre_resolved_qty=..., pre_resolved_proceeds=...,
  pre_resolved_fee=...)`(Task3), `trading.position_manager.close_position`(기존, 시그니처
  불변).
- Produces: `trading.order_executor._resolve_stale_ask_order(stale, *, client=None) ->
  tuple[float, float, float]`(반환 타입 변경: `float` → `(qty, proceeds, fee)`).
  `trading.order_executor.exit_for_risk(...)`(반환 형태 `{"action": ..., "order_id": ...}`
  불변 — `"nothing_to_sell"` action은 더 이상 반환되지 않고 `"exited"`로 대체된다).

**이 태스크가 한 번에 묶인 이유:** `_resolve_stale_ask_order()`의 반환 타입을 바꾸는
순간 `exit_for_risk()`의 호출부도 같이 바뀌어야 하므로(그렇지 않으면 기존
`exit_for_risk` 테스트가 즉시 깨짐), 이 둘을 분리하면 중간 상태에서 테스트가 항상
실패한다. 하나의 태스크로 묶어 매 커밋마다 테스트가 통과하는 상태를 유지한다.

- [ ] **Step 1: 실패하는 테스트 작성 + 기존 테스트 1개 갱신**

`tests/test_order_executor.py`의 `test_exit_for_risk_reduces_sell_volume_by_partially_filled_stale_order`
함수를 아래로 교체한다(기존 시나리오는 그대로 두고, 마지막에 PnL 검증을 추가 — 이 시나리오는
그대로 실행돼서 최종 시장가 매도까지 이어지므로, 그 매도 체결 응답을 실제로 요청된
수량(0.006)에 맞춰 현실적인 값으로 바꾼다):
```python
async def test_exit_for_risk_reduces_sell_volume_by_partially_filled_stale_order(
    monkeypatch, tmp_path,
):
    """6라운드 C2 — 취소한 잔여 매도가 이미 0.004를 팔아치웠다면 실제 코인 잔고는
    0.006뿐이다. 그런데도 청산이 원래 entry_qty(0.01) 전량을 팔려 하면 업비트가
    insufficient_funds_ask로 거부하고, 그 예외가 tick 핸들러에 삼켜져 손절이 매 쿨다운
    주기마다 똑같이 실패한다. 실제로 거래소에 나간 volume 인자를 검증한다.

    ⑤-4c 백로그 수정(Important #1) — 잔여주문 정리분(0.004, 20만원, 수수료 50원)과 이번
    시장가 체결분(0.006, 31만2천원, 수수료 30원)이 가중평균으로 합산돼 최종 realized_pnl에
    반영되는지도 함께 검증한다(기존엔 이번 체결분만 반영돼 PnL이 왜곡됐었다)."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)
    position_manager.open_position(strategy["id"], "KRW-BTC", 49_000_000.0, 0.01)
    position = position_manager.get_open_position(strategy["id"])
    stale_order_id = _stale_ask_row(dbm, strategy, position, upbit_uuid="stale-ask-uuid")

    cancelled = {"done": False}
    created = {}

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        if uuid == "stale-ask-uuid":
            return _order_state("cancel" if cancelled["done"] else "wait", 0.004)
        return {"uuid": "uuid-final-exit", "state": "done", "executed_volume": "0.006",
                "remaining_volume": "0", "paid_fee": "30.0", "trades": [{"funds": "312000.0"}]}

    async def fake_cancel_order(*, uuid=None, identifier=None, client=None):
        cancelled["done"] = True
        return {"uuid": uuid, "state": "cancel"}

    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        created["volume"] = volume
        return {"uuid": "uuid-final-exit", "state": "wait"}

    monkeypatch.setattr(upbit_client, "cancel_order", fake_cancel_order)
    monkeypatch.setattr(upbit_client, "create_order", fake_create_order)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    result = await order_executor.exit_for_risk(strategy, position, 50_000_000.0, "stop_loss_pct")

    assert created["volume"] == "0.006"  # 0.01 - 이미 팔린 0.004
    assert result["action"] == "exited"
    assert dbm.get_order_by_id(stale_order_id)["status"] == "cancel"
    # total_qty=0.01, total_proceeds=200,000+312,000=512,000, blended_price=51,200,000,
    # total_fee=50+30=80, realized_pnl=512,000-490,000-80=21,920.0
    position_row = dbm.get_position(position["id"])
    assert position_row["status"] == "closed"
    assert position_row["realized_pnl"] == pytest.approx(21_920.0)
    assert position_row["exit_qty"] == pytest.approx(0.01)
```

`test_exit_for_risk_places_no_order_when_stale_order_filled_during_cancel_race` 함수 전체를
아래로 교체한다(이름도 바꾼다 — 더 이상 "주문을 안 낸다"가 아니라 "즉시 포지션을 닫는다"):
```python
async def test_exit_for_risk_closes_position_immediately_when_stale_resolution_covers_full_quantity(
    monkeypatch, tmp_path,
):
    """⑤-4c 백로그 수정(Important #2) — DB를 읽은 시점과 취소 사이에 잔여 매도가 전량
    체결되면 업비트는 취소를 거부한다(_run_limit_timeout이 이미 겪은 경쟁조건). 정리한
    수량이 포지션 전량을 커버하면 새 주문을 내지 않고 그 자리에서 바로 포지션을
    종료해야 한다(이전엔 "nothing_to_sell"로 아무것도 안 해 reconciler가 이걸 "설명 안
    됨"으로 오분류하고 전략을 자동 정지시켰다)."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)
    position_manager.open_position(strategy["id"], "KRW-BTC", 49_000_000.0, 0.01)
    position = position_manager.get_open_position(strategy["id"])
    stale_order_id = _stale_ask_row(dbm, strategy, position, upbit_uuid="stale-ask-uuid")
    recorded = {"count": 0}
    monkeypatch.setattr(
        risk_manager, "record_trade_result",
        lambda *a: recorded.__setitem__("count", recorded["count"] + 1),
    )

    create_calls = {"n": 0}
    seen = {"n": 0}

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        if uuid == "stale-ask-uuid":
            seen["n"] += 1
            if seen["n"] == 1:  # 취소 시도 전: 아직 부분체결
                return _order_state("wait", 0.004)
            return _order_state("done", 0.01, funds="500000.0")  # 그 사이 전량 체결
        return _exit_fill_response()

    async def fake_cancel_order(*, uuid=None, identifier=None, client=None):
        raise _http_error(400)

    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        create_calls["n"] += 1
        return _exit_fill_response()

    monkeypatch.setattr(upbit_client, "cancel_order", fake_cancel_order)
    monkeypatch.setattr(upbit_client, "create_order", fake_create_order)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    result = await order_executor.exit_for_risk(strategy, position, 50_000_000.0, "stop_loss_pct")

    assert create_calls["n"] == 0  # 팔 게 남아있지 않으므로 새 주문 자체를 내지 않는다
    assert result["action"] == "exited"
    assert result["order_id"] is None
    stale_row = dbm.get_order_by_id(stale_order_id)
    assert stale_row["status"] == "done"
    assert stale_row["filled_volume"] == pytest.approx(0.01)
    # 포지션이 즉시 종료됐는지 확인 — 이게 이번 수정의 핵심.
    assert position_manager.get_open_position(strategy["id"]) is None
    position_row = dbm.get_position(position["id"])
    assert position_row["status"] == "closed"
    assert position_row["close_reason"] == "stop_loss_pct"
    # exit_price=500,000/0.01=50,000,000, realized_pnl=50,000,000*0.01-49,000,000*0.01-fee(50)=9,950.0
    assert position_row["realized_pnl"] == pytest.approx(9_950.0)
    assert recorded["count"] == 1  # check_circuit_breaker 판정을 daemon이 정상적으로 이어갈 수 있게
```

파일 끝(`test_exit_for_risk_does_not_cancel_bid_side_wait_orders` 함수 다음)에 추가:
```python
async def test_exit_for_risk_ignores_wait_orders_belonging_to_a_different_position(
    monkeypatch, tmp_path,
):
    """⑤-4c 백로그 수정(Important #5) — 이전에 종료된 포지션이 남긴 잔여 ask 주문이
    현재 포지션의 정리 대상에 섞이면, 그만큼 과다하게 차감돼 손절 자체가 안 나갈 수
    있다. list_wait_orders를 position_id로 좁혀 이 혼선을 없앤다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)
    position_manager.open_position(strategy["id"], "KRW-BTC", 49_000_000.0, 0.01)
    old_position = position_manager.get_open_position(strategy["id"])
    # 이전 포지션이 남긴 잔여 ask 주문(예: 아직 reconciler가 못 치운 것) — 실제로는
    # 이미 closed된 포지션에 딸린 행이지만, 이 테스트는 "다른 position_id에 딸린 wait
    # 행이 이번 청산 계산에 섞이면 안 된다"는 계약만 검증하면 되므로 포지션을 굳이
    # closed로 전환하지 않는다.
    _stale_ask_row(dbm, strategy, old_position, upbit_uuid="old-position-stale-uuid")
    position_manager.close_position(old_position["id"], 49_000_000.0, 0.01, 0.0, "signal")

    position_manager.open_position(strategy["id"], "KRW-BTC", 49_000_000.0, 0.01)
    position = position_manager.get_open_position(strategy["id"])

    cancel_calls = {"n": 0}
    created = {}

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        # old-position-stale-uuid가 조회되면 이 테스트는 실패해야 한다(스코핑이 안 된
        # 것이므로) — assert로 명시한다.
        assert uuid != "old-position-stale-uuid"
        return _exit_fill_response()

    async def fake_cancel_order(*, uuid=None, identifier=None, client=None):
        cancel_calls["n"] += 1
        return {"uuid": uuid, "state": "cancel"}

    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        created["volume"] = volume
        return _exit_fill_response()

    monkeypatch.setattr(upbit_client, "cancel_order", fake_cancel_order)
    monkeypatch.setattr(upbit_client, "create_order", fake_create_order)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    result = await order_executor.exit_for_risk(strategy, position, 50_000_000.0, "stop_loss_pct")

    assert cancel_calls["n"] == 0  # 다른 포지션의 잔여 주문은 취소 대상에 아예 안 들어옴
    assert created["volume"] == "0.01"  # 현재 포지션 entry_qty 전량 — 과다 차감 없음
    assert result["action"] == "exited"


async def test_exit_for_risk_persists_stale_resolution_before_a_later_order_raises(
    monkeypatch, tmp_path,
):
    """⑤-4c 백로그 수정(Important #3) — 잔여 주문 2건 중 처리 순서상 뒤쪽에서 예외가 나도,
    앞서 처리된 건의 수량/대금/수수료는 이미 positions 테이블에 누적돼 있어야 한다(그래야
    다음 tick이 그 정보를 이어받아 과다매도를 안 한다, Important #4). list_wait_orders는
    삽입 순서대로 반환되므로(ORDER BY 없는 단순 rowid 스캔) stale_a를 먼저 삽입해 먼저
    처리되게 한다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)
    position_manager.open_position(strategy["id"], "KRW-BTC", 49_000_000.0, 0.01)
    position = position_manager.get_open_position(strategy["id"])
    stale_a_id = _stale_ask_row(dbm, strategy, position, upbit_uuid="stale-a-uuid")
    _stale_ask_row(dbm, strategy, position, upbit_uuid="stale-b-uuid")

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        if uuid == "stale-a-uuid":
            return _order_state("done", 0.003, funds="150000.0")
        if uuid == "stale-b-uuid":
            raise _http_error(500)
        return _exit_fill_response()

    async def fake_cancel_order(*, uuid=None, identifier=None, client=None):
        raise AssertionError("이미 done인 stale-a는 취소를 시도하면 안 된다")

    monkeypatch.setattr(upbit_client, "cancel_order", fake_cancel_order)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    with pytest.raises(httpx.HTTPStatusError):
        await order_executor.exit_for_risk(strategy, position, 50_000_000.0, "stop_loss_pct")

    # stale-a의 처리분은 예외와 무관하게 이미 DB에 영구 기록돼 있어야 한다.
    position_row = dbm.get_position(position["id"])
    assert position_row["stale_resolved_qty"] == pytest.approx(0.003)
    assert position_row["stale_resolved_proceeds"] == pytest.approx(150_000.0)
    assert position_row["stale_resolved_fee"] == pytest.approx(50.0)
    assert dbm.get_order_by_id(stale_a_id)["status"] == "done"


async def test_exit_for_risk_carries_over_prior_tick_stale_resolution_when_no_new_wait_orders(
    monkeypatch, tmp_path,
):
    """⑤-4c 백로그 수정(Important #4) — 이전 tick에 이미 누적된 stale_resolved_qty가
    있는데 이번 tick엔 정리할 wait 행이 하나도 없으면(이전 tick에서 이미 전부 terminal
    상태로 종결됐으므로), 이번 tick의 매도수량 계산은 그 누적치를 반드시 반영해야 한다
    — 안 그러면 원래 entry_qty 전량으로 재매도를 시도해 이미 판 코인을 또 팔려 한다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)
    position_manager.open_position(strategy["id"], "KRW-BTC", 49_000_000.0, 0.01)
    position_before = position_manager.get_open_position(strategy["id"])
    # 이전 tick이 이미 처리해 영구 기록해둔 것처럼 미리 누적시켜 둔다 — 이번 tick엔
    # 대응하는 wait 행이 하나도 없다(이미 다 terminal 상태로 종결됐다고 가정).
    dbm.accumulate_stale_resolution(position_before["id"], 0.004, 200_000.0, 50.0)
    position = position_manager.get_open_position(strategy["id"])  # fresh 재조회(⑤-4c 결정8)

    created = {}

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        return {"state": "done", "executed_volume": "0.006", "remaining_volume": "0",
                "paid_fee": "30.0", "trades": [{"funds": "312000.0"}]}

    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        created["volume"] = volume
        return {"uuid": "uuid-final", "state": "wait"}

    monkeypatch.setattr(upbit_client, "create_order", fake_create_order)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    result = await order_executor.exit_for_risk(strategy, position, 50_000_000.0, "stop_loss_pct")

    assert created["volume"] == "0.006"  # 0.01 - 이전 tick 누적분(0.004), entry_qty 전량 아님
    assert result["action"] == "exited"
    position_row = dbm.get_position(position["id"])
    # total_qty=0.01, total_proceeds=200,000+312,000=512,000, realized_pnl=512,000-490,000-80=21,920.0
    assert position_row["realized_pnl"] == pytest.approx(21_920.0)


async def test_resolve_stale_ask_order_marks_row_failed_on_orphan_4xx(monkeypatch, tmp_path):
    """⑤-4c 백로그 수정(Minor #6) — identifier 조회가 4xx(거래소에 접수된 적 없음)를
    받으면 그 행을 'failed'로 마킹해야 한다. 안 그러면 다음 tick마다 같은 GET을
    무한 재시도한다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)
    position_manager.open_position(strategy["id"], "KRW-BTC", 49_000_000.0, 0.01)
    position = position_manager.get_open_position(strategy["id"])
    stale_order_id = _stale_ask_row(dbm, strategy, position)  # upbit_uuid=None

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        if identifier == stale_order_id:
            raise _http_error(404)
        return _exit_fill_response()

    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        return _exit_fill_response()

    monkeypatch.setattr(upbit_client, "create_order", fake_create_order)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    await order_executor.exit_for_risk(strategy, position, 50_000_000.0, "stop_loss_pct")

    assert dbm.get_order_by_id(stale_order_id)["status"] == "failed"
    # terminal 마킹됐으므로 다음 조회에서 더 이상 wait 행으로 안 잡힌다.
    assert dbm.list_wait_orders(strategy["id"], position_id=position["id"]) == []
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_order_executor.py -v -k exit_for_risk`
Expected: 다수 FAIL — 기존 `test_exit_for_risk_reduces_sell_volume_by_partially_filled_stale_order`는
PnL 관련 assert에서 실패, `test_exit_for_risk_closes_position_immediately_when_stale_resolution_covers_full_quantity`
등 신규 테스트는 `TypeError`(반환 타입이 아직 튜플이 아님) 또는 assert 실패

- [ ] **Step 3: `trading/order_executor.py` 수정**

`_resolve_stale_ask_order()`의 docstring 마지막 문단(600번째 줄 근처, "(수량 0) 정리한
주문이..."로 시작하는 문단) 다음에 새 문단을 추가한다:
```python
    (⑤-4c 백로그 수정) 이 함수는 이제 수량뿐 아니라 대금/수수료도 함께 반환한다
    (`(qty, proceeds, fee)`) — 호출자(exit_for_risk)가 그 값을
    db.accumulate_stale_resolution()으로 즉시 영구 기록하고, 최종 청산 시 가중평균
    원가에 반영해야 하기 때문이다(Important #1/#3/#4). identifier 조회가 4xx를 받는
    고아 행은 이제 status='failed'로 terminal 마킹한다(Minor #6) — 안 그러면 다음
    tick마다 같은 GET을 무한 재시도한다."""
```

`_resolve_stale_ask_order()` 함수 정의 위에 헬퍼를 추가한다(`_mark_stale_order_resolved`
함수 바로 아래, `_resolve_stale_ask_order` 정의 바로 위):
```python
def _stale_proceeds(fill: dict) -> tuple[float, float, float]:
    qty = fill["executed_volume"]
    proceeds = fill["filled_price"] * qty if fill["filled_price"] is not None else 0.0
    return qty, proceeds, fill["fee"]
```

`_resolve_stale_ask_order()` 함수 시그니처를:
```python
async def _resolve_stale_ask_order(
    stale: dict, *, client: httpx.AsyncClient | None = None,
) -> float:
```
에서:
```python
async def _resolve_stale_ask_order(
    stale: dict, *, client: httpx.AsyncClient | None = None,
) -> tuple[float, float, float]:
```
로 교체한다.

함수 본문의:
```python
    upbit_uuid = stale["upbit_uuid"]
    if upbit_uuid is None:
        try:
            resp = await upbit_client.get_order(identifier=stale["id"], client=client)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500:
                raise
            logger.info(
                "잔여 매도 주문이 거래소에 접수된 적이 없다(취소 대상 아님): order_id=%s status=%s",
                stale["id"], exc.response.status_code,
            )
            return 0.0
        upbit_uuid = resp["uuid"]
        fill = _fill_from_order(resp)
    else:
        fill = await _fetch_fill(upbit_uuid, client=client)

    if fill["state"] in ("done", "cancel"):
        # 이미 거래소에서 확정된 주문 — 취소할 게 없다(무의미한 취소 요청으로 order 그룹
        # rate limit을 소모하지 않는다). 체결된 만큼만 "이미 팔림"으로 계산한다.
        _mark_stale_order_resolved(stale, upbit_uuid, fill)
        return fill["executed_volume"]

    cancel_error: httpx.HTTPStatusError | None = None
    try:
        await upbit_client.cancel_order(uuid=upbit_uuid, client=client)
    except httpx.HTTPStatusError as exc:
        cancel_error = exc

    fill = await _fetch_fill(upbit_uuid, client=client)
    if cancel_error is not None and fill["state"] == "wait":
        raise cancel_error
    _mark_stale_order_resolved(stale, upbit_uuid, fill)
    return fill["executed_volume"]
```
를:
```python
    upbit_uuid = stale["upbit_uuid"]
    if upbit_uuid is None:
        try:
            resp = await upbit_client.get_order(identifier=stale["id"], client=client)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500:
                raise
            logger.info(
                "잔여 매도 주문이 거래소에 접수된 적이 없다(취소 대상 아님, terminal 마킹): "
                "order_id=%s status=%s", stale["id"], exc.response.status_code,
            )
            db.update_order_filled(stale["id"], None, None, None, None, None, "failed")
            return 0.0, 0.0, 0.0
        upbit_uuid = resp["uuid"]
        fill = _fill_from_order(resp)
    else:
        fill = await _fetch_fill(upbit_uuid, client=client)

    if fill["state"] in ("done", "cancel"):
        # 이미 거래소에서 확정된 주문 — 취소할 게 없다(무의미한 취소 요청으로 order 그룹
        # rate limit을 소모하지 않는다). 체결된 만큼만 "이미 팔림"으로 계산한다.
        _mark_stale_order_resolved(stale, upbit_uuid, fill)
        return _stale_proceeds(fill)

    cancel_error: httpx.HTTPStatusError | None = None
    try:
        await upbit_client.cancel_order(uuid=upbit_uuid, client=client)
    except httpx.HTTPStatusError as exc:
        cancel_error = exc

    fill = await _fetch_fill(upbit_uuid, client=client)
    if cancel_error is not None and fill["state"] == "wait":
        raise cancel_error
    _mark_stale_order_resolved(stale, upbit_uuid, fill)
    return _stale_proceeds(fill)
```
로 교체한다.

`exit_for_risk()` 함수 전체(docstring 포함, `async def exit_for_risk(` 부터 함수 끝까지)를
찾아서, docstring 마지막 문단 뒤에 새 문단을 추가한다(6라운드 문단 바로 다음):
```python
    (⑤-4c 백로그 수정, 7~8라운드 대응) 6라운드까지는 "이번 tick에 처리한 만큼만" 계산해
    exit()를 호출했다 — 그 정보가 이 함수 호출 하나의 지역 변수에만 머물러서, (a) 정리한
    주문이 포지션 전량을 커버해도 아무 조치 없이 "nothing_to_sell"만 반환해 포지션이
    방치되고 reconciler가 이걸 "설명 안 됨"으로 오분류해 전략을 자동 정지시켰다
    (Important #2), (b) 잔여 주문이 여럿일 때 뒤쪽 처리 중 예외가 나면 앞쪽 처리분이
    사라져 다음 tick이 과다매도를 시도했다(Important #3/#4), (c) 부분 정리 후 남은
    수량만 파는 exit() 호출이 성공해도 close_position()이 DB의 원래 entry_qty 전체를
    원가로 계산해 PnL이 왜곡됐다(Important #1), (d) 잔여 주문 조회가 전략 전체 기준이라
    이전에 종료된 포지션의 잔여분이 섞였다(Important #5).

    지금은: list_wait_orders를 이 포지션(position_id)으로 좁히고(Important #5),
    지역 누적변수(total_resolved_qty/proceeds/fee)를 position의 기존 누적치로
    초기화한 뒤 각 정리분을 db.accumulate_stale_resolution()으로 그 자리에서 영구
    기록하는 동시에 지역 변수에도 더한다(Important #3 — 예외가 나도 이미 커밋된
    앞쪽 처리분은 살아남는다; Important #4 — 다음 tick은 position의 누적치를 통해
    이번 tick의 결과를 자동으로 이어받는다, ⑤-4c 결정8의 매 tick fresh 포지션 재조회
    덕분). 정리만으로 포지션 전량(또는 최소주문금액 미만만 남기고)이 소진됐으면 새
    주문을 내지 않고 그 자리에서 바로 position_manager.close_position()을 호출해
    포지션을 종료한다(Important #2). 남은 수량이 있으면 exit()에 pre_resolved_*로
    누적치를 넘겨 최종 체결분과 가중평균으로 합산하게 한다(Important #1)."""
```

`exit_for_risk()`의 본문(docstring 다음)을:
```python
    resolved_volume = 0.0
    if not dry_run:
        # position_id로 거르지 않는다 — 이 전략에 열려있는 ask 주문은 어느 것이든 지금
        # 팔려는 같은 코인 잔고를 두고 충돌하므로 전부 정리 대상이고, 체결량도 전부
        # 합산한다. 혹시 이전 포지션이 남긴 체결까지 합산돼 과다 차감되더라도 방향이
        # 안전한 쪽이다("덜 팔아 먼지가 남음"은 reconciler가 self-heal하지만, "더 팔려다
        # insufficient_funds_ask로 거부됨"은 손절 자체가 실패한다).
        for stale in db.list_wait_orders(strategy["id"]):
            if stale["side"] != "ask":
                continue
            resolved_volume += await _resolve_stale_ask_order(stale, client=client)

    if resolved_volume > 0:
        sell_volume = _floor_volume(position["entry_qty"] - resolved_volume)
        if sell_volume <= 0 or sell_volume * expected_price < _MIN_ORDER_AMOUNT_KRW:
            logger.warning(
                "잔여 매도 주문이 이미 체결돼 청산할 수량이 남지 않았다(주문 생략, 포지션 "
                "부기는 reconciler가 맞춘다): strategy_id=%s entry_qty=%s 기체결=%s",
                strategy["id"], position["entry_qty"], resolved_volume,
            )
            return {"action": "nothing_to_sell", "order_id": None}
        position = {**position, "entry_qty": sell_volume}

    forced_risk_config = json.loads(strategy["risk_config_json"])
    forced_risk_config["order_execution_mode"] = "market"
    # market 모드는 max_slippage_pct 등 다른 risk_config 필드를 요구하지 않는다
    # (_validate_mode 참고 — market_capped만 요구) — 그 필드를 한 번도 설정한 적 없는
    # 전략을 강제로 market에 태워도 안전하다.
    forced_strategy = {**strategy, "risk_config_json": json.dumps(forced_risk_config)}

    order = await exit(
        forced_strategy, position, expected_price, client=client, dry_run=dry_run, close_reason=reason,
    )
    if order["status"] == "done":
        risk_manager.record_trade_result(strategy["id"], order["realized_pnl"], order["capital_after"])
        return {"action": "exited", "order_id": order["id"]}
    if order["status"] == "cancel":
        return {"action": "slippage_exceeded", "order_id": order["id"]}
    return {"action": "pending", "order_id": order["id"]}
```
에서:
```python
    total_resolved_qty = position["stale_resolved_qty"]
    total_resolved_proceeds = position["stale_resolved_proceeds"]
    total_resolved_fee = position["stale_resolved_fee"]

    if not dry_run:
        # position_id로 좁힌다(Important #5) — 이전에 종료된 다른 포지션이 남긴 잔여
        # 주문이 이번 계산에 섞이면 안 된다. 이 포지션에 걸려있는 ask 주문은 지금
        # 팔려는 같은 코인 잔고를 두고 충돌하므로 전부 정리 대상이다.
        for stale in db.list_wait_orders(strategy["id"], position_id=position["id"]):
            if stale["side"] != "ask":
                continue
            qty, proceeds, fee = await _resolve_stale_ask_order(stale, client=client)
            if qty > 0:
                # 즉시 영구 기록한다(Important #3) — 이 for문의 다음 반복이 예외를
                # 던져도 이미 커밋된 이 분량은 살아남고, 다음 tick은 fresh position을
                # 통해 이 값을 자동으로 이어받는다(Important #4).
                db.accumulate_stale_resolution(position["id"], qty, proceeds, fee)
            total_resolved_qty += qty
            total_resolved_proceeds += proceeds
            total_resolved_fee += fee

    sellable_qty = _floor_volume(position["entry_qty"] - total_resolved_qty)
    if sellable_qty <= 0 or sellable_qty * expected_price < _MIN_ORDER_AMOUNT_KRW:
        # 잔여 주문 정리만으로 포지션이 사실상 전부 소진됐다(Important #2) — 새 주문 없이
        # 그 자리에서 바로 종료한다. 방치하면 reconciler가 이 포지션을 "설명 안 됨"으로
        # 오분류해 전략을 자동 정지시킨다.
        blended_price = (
            total_resolved_proceeds / total_resolved_qty if total_resolved_qty else expected_price
        )
        close_result = position_manager.close_position(
            position["id"], blended_price, total_resolved_qty, total_resolved_fee, reason,
        )
        risk_manager.record_trade_result(
            strategy["id"], close_result["realized_pnl"], close_result["capital_after"],
        )
        return {"action": "exited", "order_id": None}

    forced_risk_config = json.loads(strategy["risk_config_json"])
    forced_risk_config["order_execution_mode"] = "market"
    # market 모드는 max_slippage_pct 등 다른 risk_config 필드를 요구하지 않는다
    # (_validate_mode 참고 — market_capped만 요구) — 그 필드를 한 번도 설정한 적 없는
    # 전략을 강제로 market에 태워도 안전하다.
    forced_strategy = {**strategy, "risk_config_json": json.dumps(forced_risk_config)}
    sell_position = {**position, "entry_qty": sellable_qty}

    order = await exit(
        forced_strategy, sell_position, expected_price, client=client, dry_run=dry_run,
        close_reason=reason, pre_resolved_qty=total_resolved_qty,
        pre_resolved_proceeds=total_resolved_proceeds, pre_resolved_fee=total_resolved_fee,
    )
    if order["status"] == "done":
        risk_manager.record_trade_result(strategy["id"], order["realized_pnl"], order["capital_after"])
        return {"action": "exited", "order_id": order["id"]}
    if order["status"] == "cancel":
        return {"action": "slippage_exceeded", "order_id": order["id"]}
    return {"action": "pending", "order_id": order["id"]}
```
로 교체한다.

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_order_executor.py -v`
Expected: 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add trading/order_executor.py tests/test_order_executor.py
git commit -m "fix: exit_for_risk가 잔여주문 정리분을 영구 기록·position 스코핑·가중평균 합산하도록 재구성"
```

---

### Task 5: `trading/order_executor.py` — `enter()`/`exit()` 예외 시 주문 행 실패 마킹(Minor #7)

**Files:**
- Modify: `trading/order_executor.py`
- Modify: `tests/test_order_executor.py`

**Interfaces:**
- Produces: `enter()`/`exit()`의 기존 동작(정상 흐름) 불변 — 예외 발생 시 `orders` 행이
  `status='wait'`로 영원히 남는 대신 `status='failed'`로 마킹된 뒤 예외가 그대로
  다시 던져진다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_order_executor.py` 파일 끝에 추가:
```python
async def test_exit_marks_order_failed_when_create_order_raises_auth_error(monkeypatch, tmp_path):
    """⑤-4c 백로그 수정(Minor #7) — 401/403 같은 인증오류로 주문 생성 자체가 실패하면
    방금 만든 orders 행이 status='wait'로 영원히 남는다(다음 GET 재시도 대상도 아니고
    영영 고아로 남음). 예외가 나면 그 행을 'failed'로 마킹한 뒤 예외를 다시 던져야 한다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)
    position_manager.open_position(strategy["id"], "KRW-BTC", 49_000_000.0, 0.01)
    position = position_manager.get_open_position(strategy["id"])

    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        raise _http_error(401)

    monkeypatch.setattr(upbit_client, "create_order", fake_create_order)

    with pytest.raises(httpx.HTTPStatusError):
        await order_executor.exit(strategy, position, 50_000_000.0)

    orders = dbm.list_wait_orders(strategy["id"])
    assert orders == []  # 더 이상 'wait'로 남아있지 않다(failed로 마킹됐다)


async def test_enter_marks_order_failed_when_create_order_raises_auth_error(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)

    async def fake_create_order(market, side, ord_type, *, volume=None, price=None,
                                 time_in_force=None, identifier=None, client=None):
        raise _http_error(403)

    monkeypatch.setattr(upbit_client, "create_order", fake_create_order)

    with pytest.raises(httpx.HTTPStatusError):
        await order_executor.enter(strategy, 500_000.0, 50_000_000.0)

    orders = dbm.list_wait_orders(strategy["id"])
    assert orders == []
    assert position_manager.get_open_position(strategy["id"]) is None
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_order_executor.py -v -k marks_order_failed`
Expected: FAIL — `orders`가 비어있지 않음(`status='wait'`인 채로 남아있음)

- [ ] **Step 3: `trading/order_executor.py` 수정**

`enter()`의 실행모드 분기를:
```python
    if dry_run:
        db.update_order_filled(order_id, None, price, volume, 0.0, 0.0, "done")
        result = {"order_id": order_id, "status": "done", "filled_price": price,
                   "filled_volume": volume, "fee": 0.0}
    elif mode == "market":
        result = await _run_market(order_id, market, "bid", capital, volume, expected_price, client=client)
    elif mode == "limit":
        result = await _run_limit(order_id, market, "bid", price, volume, client=client)
    elif mode == "limit_timeout":
        timeout_sec = risk_config.get("order_timeout_sec", 10)
        result = await _run_limit_timeout(
            order_id, strategy["id"], None, market, "bid", price, volume, expected_price,
            timeout_sec, client=client,
        )
    elif mode == "market_capped":
        result = await _run_market_capped(
            order_id, market, "bid", expected_price, volume, risk_config["max_slippage_pct"],
            capital=capital, client=client,
        )
    else:  # _validate_mode()가 이미 걸러 도달 불가 — 모드 추가 시 분기 누락 방지용 방어코드
        raise ValueError(f"지원하지 않는 order_execution_mode: {mode}")
```
에서:
```python
    try:
        if dry_run:
            db.update_order_filled(order_id, None, price, volume, 0.0, 0.0, "done")
            result = {"order_id": order_id, "status": "done", "filled_price": price,
                       "filled_volume": volume, "fee": 0.0}
        elif mode == "market":
            result = await _run_market(order_id, market, "bid", capital, volume, expected_price, client=client)
        elif mode == "limit":
            result = await _run_limit(order_id, market, "bid", price, volume, client=client)
        elif mode == "limit_timeout":
            timeout_sec = risk_config.get("order_timeout_sec", 10)
            result = await _run_limit_timeout(
                order_id, strategy["id"], None, market, "bid", price, volume, expected_price,
                timeout_sec, client=client,
            )
        elif mode == "market_capped":
            result = await _run_market_capped(
                order_id, market, "bid", expected_price, volume, risk_config["max_slippage_pct"],
                capital=capital, client=client,
            )
        else:  # _validate_mode()가 이미 걸러 도달 불가 — 모드 추가 시 분기 누락 방지용 방어코드
            raise ValueError(f"지원하지 않는 order_execution_mode: {mode}")
    except Exception:
        # ⑤-4c 백로그 수정(Minor #7) — 인증오류(401/403) 등으로 여기서 실패하면 방금 만든
        # order_id 행이 status='wait'로 영원히 남는다(GET 재시도 대상도 아니고 영영 고아로
        # 남음). terminal 마킹만 하고 예외는 그대로 다시 던진다(호출자의 기존 처리 유지).
        db.update_order_filled(order_id, None, None, None, None, None, "failed")
        raise
```
로 교체한다.

`exit()`의 실행모드 분기도 동일한 패턴으로 교체한다. 원본:
```python
    if dry_run:
        db.update_order_filled(order_id, None, price, volume, 0.0, 0.0, "done")
        result = {"order_id": order_id, "status": "done", "filled_price": price,
                   "filled_volume": volume, "fee": 0.0}
    elif mode == "market":
        result = await _run_market(order_id, market, "ask", None, volume, expected_price, client=client)
    elif mode == "limit":
        result = await _run_limit(order_id, market, "ask", price, volume, client=client)
    elif mode == "limit_timeout":
        timeout_sec = risk_config.get("order_timeout_sec", 10)
        result = await _run_limit_timeout(
            order_id, strategy["id"], position["id"], market, "ask", price, volume, expected_price,
            timeout_sec, client=client,
        )
    elif mode == "market_capped":
        result = await _run_market_capped(
            order_id, market, "ask", expected_price, volume, risk_config["max_slippage_pct"],
            capital=None, client=client,  # 매도는 보유수량 전량이라 capital 기반 재계산 불필요
        )
    else:  # _validate_mode()가 이미 걸러 도달 불가 — 모드 추가 시 분기 누락 방지용 방어코드
        raise ValueError(f"지원하지 않는 order_execution_mode: {mode}")
```
로 감싸서:
```python
    try:
        if dry_run:
            db.update_order_filled(order_id, None, price, volume, 0.0, 0.0, "done")
            result = {"order_id": order_id, "status": "done", "filled_price": price,
                       "filled_volume": volume, "fee": 0.0}
        elif mode == "market":
            result = await _run_market(order_id, market, "ask", None, volume, expected_price, client=client)
        elif mode == "limit":
            result = await _run_limit(order_id, market, "ask", price, volume, client=client)
        elif mode == "limit_timeout":
            timeout_sec = risk_config.get("order_timeout_sec", 10)
            result = await _run_limit_timeout(
                order_id, strategy["id"], position["id"], market, "ask", price, volume, expected_price,
                timeout_sec, client=client,
            )
        elif mode == "market_capped":
            result = await _run_market_capped(
                order_id, market, "ask", expected_price, volume, risk_config["max_slippage_pct"],
                capital=None, client=client,  # 매도는 보유수량 전량이라 capital 기반 재계산 불필요
            )
        else:  # _validate_mode()가 이미 걸러 도달 불가 — 모드 추가 시 분기 누락 방지용 방어코드
            raise ValueError(f"지원하지 않는 order_execution_mode: {mode}")
    except Exception:
        # ⑤-4c 백로그 수정(Minor #7) — enter()와 동일한 이유로 terminal 마킹.
        db.update_order_filled(order_id, None, None, None, None, None, "failed")
        raise
```
로 교체한다.

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_order_executor.py -v`
Expected: 전부 PASS(회귀 없음 — 정상 흐름은 try 블록을 그대로 통과)

- [ ] **Step 5: 커밋**

```bash
git add trading/order_executor.py tests/test_order_executor.py
git commit -m "fix: enter()/exit()가 주문실행 실패 시 orders 행을 failed로 마킹하도록 수정"
```

---

### Task 6: 문서 정정 + Minor #8 주석 + 전체 회귀

**Files:**
- Modify: `trading/daemon.py`(docstring만)
- Modify: `trading/order_executor.py`(주석만)

**Interfaces:** 없음(코드 동작 변경 없음).

- [ ] **Step 1: `trading/daemon.py` docstring 정정**

`_run_risk_exit_loop()`의 docstring 안, 6라운드 문단의:
```
    또 정리한 주문이 이미 포지션 수량을 다 팔아버린
    경우엔 action=="nothing_to_sell"로 돌아오는데, "exited"가 아니므로 아래 분기가
    자연히 서킷브레이커 판정/쿨다운 리셋을 건너뛴다(포지션 부기는 reconciler 몫).
    이 가드를 지우고도
```
를:
```
    또 정리한 주문이 이미 포지션 수량을 다 팔아버린
    경우엔(⑤-4c 백로그 수정 이후) exit_for_risk()가 그 자리에서 바로 포지션을 닫고
    action=="exited"로 돌아온다 — 아래 분기가 정상적으로 서킷브레이커 판정/쿨다운
    리셋을 수행한다(예전엔 action=="nothing_to_sell"로 아무 것도 안 하고 포지션을
    방치해 reconciler가 "설명 안 됨"으로 오분류했었다 — 잔여주문 정리분을
    positions 테이블에 영구 기록하는 구조로 바뀌면서 해소됨, 상세는
    docs/superpowers/specs_v1/2026-08-09-live-trading-risk-exit-backlog-fix-design.md).
    이 가드를 지우고도
```
로 교체한다.

- [ ] **Step 2: `trading/order_executor.py`에 Minor #8 주석 추가**

`_resolve_stale_ask_order()`의 docstring 맨 끝(Task4에서 추가한 "(⑤-4c 백로그 수정)"
문단 다음)에 문단을 하나 더 추가한다:
```python
    (Minor #8, 미수정) cancel_order()가 성공 응답을 줬는데 그 직후 재조회에서 거래소가
    여전히 wait로 보고하는 극단적인 경쟁조건은 mock으로 재현 가능한 버그가 아니라 실제
    업비트 거래소 동작에 대한 불확실성이다 — 코드로 고치지 않고
    [[upbit-v1-live-trading-roadmap-sequencing]]의 소액 실전 테스트 단계에서 실제로
    발생하는지 관찰 대상으로 남긴다."""
```

- [ ] **Step 3: 전체 테스트 스위트 실행(회귀 확인)**

Run: `python -m pytest -q`
Expected: 전부 PASS(기존 스위트 + 이 플랜의 신규 테스트 전부 포함, 회귀 없음)

- [ ] **Step 4: 커밋**

```bash
git add trading/daemon.py trading/order_executor.py
git commit -m "docs: nothing_to_sell 관련 서술 정정 + Minor #8 미수정 사유 주석 추가"
```

---

## Self-Review

**스펙 커버리지:**
- 결정1(positions에 누적 컬럼 3개) → Task1.
- 결정2(position_id 스코핑, Important #5) → Task2(db 계층) + Task4(exit_for_risk 호출부).
- 결정3(완전소진 시 즉시 close, Important #2) → Task4.
- 결정4(가중평균 close 전달, Important #1) → Task3(exit() 파라미터) + Task4(exit_for_risk가
  넘기는 부분).
- 결정5(Minor #6, 4xx terminal 마킹) → Task4.
- 결정6(Minor #7, 인증오류 terminal 마킹) → Task5.
- 결정7(Minor #8, 코드 미수정 + 관찰 대상 문서화) → Task6.
- Important #3/#4(누적 즉시 기록 + 다음 tick 이어받기) → Task4의 지역 누적변수 +
  즉시 `accumulate_stale_resolution` 호출로 함께 해소.

**플레이스홀더 스캔:** 없음 — 모든 스텝에 완전한 코드가 있다.

**타입 일관성:** `_resolve_stale_ask_order()`의 반환 타입이 `float`에서
`tuple[float, float, float]`로 바뀌는 지점(Task4)이 유일한 breaking change인데, 같은
태스크 안에서 유일한 호출부(`exit_for_risk()`)도 함께 바뀌므로 태스크 경계에서 항상
타입이 맞는다. `exit()`의 `pre_resolved_*` 3개 파라미터 이름이 Task3(정의)과
Task4(`exit_for_risk`의 호출부)에서 동일하게 `pre_resolved_qty`/`pre_resolved_proceeds`/
`pre_resolved_fee`로 일치함을 확인했다. `db.accumulate_stale_resolution(position_id, qty,
proceeds, fee)`의 인자 순서가 Task1(정의)과 Task4(호출부) 양쪽에서 동일하다.

**기존 테스트 영향 재확인:** `test_exit_for_risk_reduces_sell_volume_by_partially_filled_stale_order`
(갱신), `test_exit_for_risk_places_no_order_when_stale_order_filled_during_cancel_race`
(이름 변경 + 갱신) 외의 기존 `exit_for_risk`/`exit`/`enter` 테스트는 전부 `resolved_volume`이
0인 경로(잔여 주문이 아예 없거나 전량 미체결)만 타므로 `total_resolved_qty=0` →
`sellable_qty=entry_qty`(기존과 동일) → `pre_resolved_*=0`(Task3의 항등식) 경로를 타서
회귀 없이 그대로 통과해야 한다.
