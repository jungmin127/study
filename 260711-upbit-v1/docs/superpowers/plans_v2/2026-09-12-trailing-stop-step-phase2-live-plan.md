# 계단식 트레일링 스탑 Phase 2 (실거래 연동) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 백테스트 전용이던 `TRAILING_STOP_STEP_PCT`(계단식 트레일링 스탑)를 실거래
daemon의 실시간 리스크 청산 안전망(STOP_LOSS_PCT/TAKE_PROFIT_PCT와 동일 경로)에도
연동해, tick마다 즉시 반응하도록 만든다.

**Architecture:** `engine/condition_tree.py`의 계단 공식을 순수 함수로 뽑아 백테스트/
라이브가 공유하게 하고, `trading/db.py`의 `positions` 테이블에 `peak_return_pct`
컬럼을 추가해 고점을 영속화한다. `trading/daemon.py`의 `_run_risk_exit_loop`(이미
존재하는 ticker WebSocket 구독, 신규 API 호출 없음)가 매 tick 메모리에서 고점을
갱신하고 주기적으로만(+청산 직전 강제로) DB에 flush하며, `trading/signal_engine.py`의
`matched_risk_exit_indicator()`가 이 고점 기준으로 즉시 판정한다. 봉 마감 조건트리
평가(`evaluate_signals`) 경로도 동일한 고점을 받아 정합적으로 동작하게 한다.

**Tech Stack:** Python(asyncio, sqlite3), pytest, pytest-asyncio

## Global Constraints

- 모든 python/pytest 실행은 `PYTHONPATH=. PYTHONIOENCODING=utf-8`를 앞에 붙인다
  (Windows 콘솔 인코딩/모듈 경로 요구사항, 기존 스크립트 전부 동일)
- 코드 주석/식별자는 기존 관례대로 한글 설명 + 영어 식별자 혼용
- 각 태스크 완료 후 커밋 메시지 끝에 다음 트레일러를 반드시 포함한다:
  ```
  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01FRATo6oMgBvRHHfjj8uEkX
  ```
- 이 프로젝트는 항상 `main`에서 직접 작업하고, 각 태스크는 개별 커밋만 하며,
  전체 푸시는 마지막 태스크(4번) 완료 시 수행한다(프로젝트 CLAUDE.md 규칙)
- 원본 스펙: `docs/superpowers/specs_v2/2026-09-12-trailing-stop-step-phase2-live-design.md`
  (설계 의도/결정 근거는 이 문서와 일치해야 한다)
- **`trading/daemon.py`의 `_run_risk_exit_loop`는 7라운드 리뷰를 거친 동시성
  critical 코드다.** 이 플랜의 Task 3는 기존 락 획득 시점/가드 순서/쿨다운 로직을
  **절대 재배치하지 않는다** — peak 갱신 로직만 기존 구조에 끼워 넣는다. 코드
  전체를 다시 쓰지 말고, 이 플랜이 지정한 정확한 삽입 지점만 수정할 것.
- `eval_group()`/`eval_group_values()`는 쌍둥이 함수(파일 내 기존 경고 주석과 동일
  원칙) — 계단 공식을 순수 함수로 뽑은 뒤에도 두 함수 모두 그 함수를 호출해야 한다.

---

## File Structure

- **Modify: `engine/condition_tree.py`** — 계단 공식을 `trailing_stop_step_level()`
  순수 함수로 추출(동작 불변 리팩터링), `eval_group()`/`eval_group_values()`가 이를
  호출하도록 교체
- **Modify: `trading/db.py`** — `positions` 테이블에 `peak_return_pct` 컬럼 추가
  (신규 DB용 `_SCHEMA` + 기존 DB용 마이그레이션 함수), `update_position_peak_return_pct()`
  함수 추가
- **Modify: `trading/signal_engine.py`** — `_TICKER_RISK_INDICATORS`에
  `TRAILING_STOP_STEP_PCT` 추가, `matched_risk_exit_indicator()`가 계단 공식을
  판정하도록 확장, `_position_context()`를 3-tuple로 확장, `evaluate_signals()`
  호출부 갱신
- **Modify: `trading/daemon.py`** — `_run_risk_exit_loop`에 tick마다 고점 갱신 +
  주기적 flush + 청산 직전 강제 flush 추가
- **Modify: `tests/test_condition_tree.py`** — 리팩터링 회귀 확인 + 새 순수 함수
  단위 테스트
- **Modify: `tests/test_trading_db.py`** — 신규 컬럼 마이그레이션 + 갱신 함수 테스트
- **Modify: `tests/test_signal_engine.py`** — `matched_risk_exit_indicator`/
  `_position_context` 확장 테스트
- **Modify: `tests/test_daemon.py`** — `_run_risk_exit_loop`의 실시간 트레일링 스탑
  트리거 + 주기적 flush + 청산 직전 강제 flush 테스트

---

### Task 1: `engine/condition_tree.py` — 계단 공식 공유 함수 추출

**Files:**
- Modify: `engine/condition_tree.py`
- Test: `tests/test_condition_tree.py`

**Interfaces:**
- Produces: `trailing_stop_step_level(peak_return_pct: float, step_pct: float) -> float | None`
  (step_pct<=0이면 None — Task 3이 `trading/signal_engine.py`에서 이 함수를 import해
  재사용한다)
- `eval_group()`/`eval_group_values()`의 기존 시그니처/동작은 완전히 동일하게
  유지된다(순수 리팩터링, 동작 변경 없음)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_condition_tree.py` 파일 끝에 추가:

```python


def test_trailing_stop_step_level_computes_stepped_stop_level():
    assert trailing_stop_step_level(16.0, 5.0) == 10.0
    assert trailing_stop_step_level(6.0, 5.0) == 0.0
    assert trailing_stop_step_level(3.0, 5.0) == -5.0


def test_trailing_stop_step_level_returns_none_for_invalid_step():
    assert trailing_stop_step_level(16.0, 0.0) is None
    assert trailing_stop_step_level(16.0, -5.0) is None
```

파일 상단 import에 `trailing_stop_step_level`을 추가한다:

```python
from engine.condition_tree import (
    ...,
    trailing_stop_step_level,
)
```

(이미 파일에 `eval_group`, `eval_group_values` 등을 이런 식으로 import하고 있다면
그 목록에 이름만 추가한다.)

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_condition_tree.py::test_trailing_stop_step_level_computes_stepped_stop_level tests/test_condition_tree.py::test_trailing_stop_step_level_returns_none_for_invalid_step -v`
Expected: FAIL — `ImportError: cannot import name 'trailing_stop_step_level'`

- [ ] **Step 3: `engine/condition_tree.py`에 순수 함수 추가**

`def eval_group(` 정의 바로 앞(126번째 줄 근처)에 추가:

```python
def trailing_stop_step_level(peak_return_pct: float, step_pct: float) -> float | None:
    """계단식 트레일링 스탑의 현재 손절선(%)을 계산한다. step_pct<=0이면 None(항상
    미발동을 뜻함) — 호출부가 결과를 그대로 bool 판정에 쓸 수 있도록 None 처리는
    호출부 책임으로 남긴다. 백테스트(eval_group)와 라이브(eval_group_values,
    trading/signal_engine.py의 matched_risk_exit_indicator)가 모두 이 함수를
    공유해 공식이 갈라지지 않게 한다."""
    if step_pct <= 0:
        return None
    return (math.floor(peak_return_pct / step_pct) - 1) * step_pct


def eval_group(
```

- [ ] **Step 4: `eval_group()`의 `TRAILING_STOP_STEP_PCT` 분기를 새 함수를 쓰도록 교체**

기존:

```python
            if item["indicator"] == "TRAILING_STOP_STEP_PCT":
                # threshold는 일반 지표처럼 "비교할 값"이 아니라 계단 폭(step_pct)이다. 손절선 자체가
                # 고점(position_peak_return_pct)으로부터 매번 새로 계산되는 이동값이라 operator/threshold를
                # apply_operator에 그대로 넘기는 일반 경로를 쓸 수 없다 — item["operator"]는 의도적으로
                # 무시한다(UI는 "<=" 고정으로 혼란을 막는다). 설계 근거:
                # docs/superpowers/specs_v2/2026-09-11-trailing-stop-step-design.md
                step = float(item["threshold"])
                if position_return_pct is None or position_peak_return_pct is None or step <= 0:
                    results.append(False)
                else:
                    stop_level = (math.floor(position_peak_return_pct / step) - 1) * step
                    results.append(position_return_pct <= stop_level)
                continue
```

교체 후:

```python
            if item["indicator"] == "TRAILING_STOP_STEP_PCT":
                # threshold는 일반 지표처럼 "비교할 값"이 아니라 계단 폭(step_pct)이다. 손절선 자체가
                # 고점(position_peak_return_pct)으로부터 매번 새로 계산되는 이동값이라 operator/threshold를
                # apply_operator에 그대로 넘기는 일반 경로를 쓸 수 없다 — item["operator"]는 의도적으로
                # 무시한다(UI는 "<=" 고정으로 혼란을 막는다). 설계 근거:
                # docs/superpowers/specs_v2/2026-09-11-trailing-stop-step-design.md
                step = float(item["threshold"])
                stop_level = None
                if position_return_pct is not None and position_peak_return_pct is not None:
                    stop_level = trailing_stop_step_level(position_peak_return_pct, step)
                results.append(stop_level is not None and position_return_pct <= stop_level)
                continue
```

`eval_group_values()`의 동일한 분기(주석 "eval_group()과 동일 로직 — 쌍둥이 함수,
둘 다 고칠 것." 바로 아래)도 똑같이 교체한다:

기존:

```python
            if item["indicator"] == "TRAILING_STOP_STEP_PCT":
                # eval_group()과 동일 로직 — 쌍둥이 함수, 둘 다 고칠 것.
                step = float(item["threshold"])
                if position_return_pct is None or position_peak_return_pct is None or step <= 0:
                    results.append(False)
                else:
                    stop_level = (math.floor(position_peak_return_pct / step) - 1) * step
                    results.append(position_return_pct <= stop_level)
                continue
```

교체 후:

```python
            if item["indicator"] == "TRAILING_STOP_STEP_PCT":
                # eval_group()과 동일 로직 — 쌍둥이 함수, 둘 다 고칠 것.
                step = float(item["threshold"])
                stop_level = None
                if position_return_pct is not None and position_peak_return_pct is not None:
                    stop_level = trailing_stop_step_level(position_peak_return_pct, step)
                results.append(stop_level is not None and position_return_pct <= stop_level)
                continue
```

- [ ] **Step 5: 테스트 실행해 전체 통과 확인(회귀 없음)**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_condition_tree.py -v`
Expected: 전체 PASS — 기존 `test_eval_group_values_trailing_stop_step_pct_*`,
`test_eval_group_trailing_stop_step_pct_matches_eval_group_values` 등이 리팩터링
후에도 동일하게 통과해야 한다(동작 불변 확인).

- [ ] **Step 6: 커밋**

```bash
git add engine/condition_tree.py tests/test_condition_tree.py
git commit -m "$(cat <<'EOF'
refactor: 계단식 트레일링 스탑 공식을 순수 함수로 추출

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01FRATo6oMgBvRHHfjj8uEkX
EOF
)"
```

---

### Task 2: `trading/db.py` — `positions.peak_return_pct` 컬럼 + 갱신 함수

**Files:**
- Modify: `trading/db.py`
- Test: `tests/test_trading_db.py`

**Interfaces:**
- Produces: `update_position_peak_return_pct(position_id: str, peak_return_pct: float) -> None`
  (Task 3의 `trading/daemon.py`가 이 함수를 호출한다)
- `db.get_open_position()`/`db.get_position()`이 반환하는 dict에 `peak_return_pct`
  키가 추가된다(신규 DB는 기본값 0, 기존 DB는 마이그레이션으로 0 채움) — Task 3의
  `_position_context()`가 이 값을 읽는다

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_trading_db.py` 파일에서 `test_connect_adds_entry_fee_column_to_existing_positions_table`
함수(1110번째 줄 근처) 바로 다음에 추가:

```python


def test_connect_adds_peak_return_pct_column_to_existing_positions_table(monkeypatch, tmp_path):
    """peak_return_pct도 entry_fee와 동일하게 실거래 중인 프로덕션 DB에 ALTER TABLE로
    적용해야 하는 무마이그레이션 정책의 예외다."""
    db = _fresh_db(monkeypatch, tmp_path)
    conn = sqlite3.connect(db.DB_PATH)
    conn.execute("""
        CREATE TABLE positions (
            id               TEXT PRIMARY KEY,
            live_strategy_id TEXT NOT NULL,
            market           TEXT NOT NULL,
            status           TEXT NOT NULL DEFAULT 'open',
            entry_price      REAL,
            entry_qty        REAL,
            entry_fee        REAL NOT NULL DEFAULT 0,
            entry_time       TEXT,
            exit_price       REAL,
            exit_qty         REAL,
            exit_time        TEXT,
            realized_pnl     REAL,
            realized_pnl_pct REAL,
            close_reason     TEXT,
            stale_resolved_qty      REAL NOT NULL DEFAULT 0,
            stale_resolved_proceeds REAL NOT NULL DEFAULT 0,
            stale_resolved_fee      REAL NOT NULL DEFAULT 0,
            created_at       TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("INSERT INTO positions (id, live_strategy_id, market) VALUES ('p1', 's1', 'KRW-BTC')")
    conn.commit()
    conn.close()

    db._connect()

    conn = sqlite3.connect(db.DB_PATH)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(positions)")}
        row = conn.execute("SELECT peak_return_pct FROM positions WHERE id = 'p1'").fetchone()
    finally:
        conn.close()
    assert "peak_return_pct" in columns
    assert row[0] == 0


def test_update_position_peak_return_pct_updates_open_position(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    position_id = db.insert_position(strategy_id, "KRW-BTC", 100_000_000.0, 0.01)

    db.update_position_peak_return_pct(position_id, 12.5)

    assert db.get_position(position_id)["peak_return_pct"] == 12.5


def test_update_position_peak_return_pct_ignores_closed_position(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    position_id = db.insert_position(strategy_id, "KRW-BTC", 100_000_000.0, 0.01)
    db.close_position_row(position_id, 101_000_000.0, 0.01, 9500.0, 0.95, "signal")

    db.update_position_peak_return_pct(position_id, 12.5)

    assert db.get_position(position_id)["peak_return_pct"] == 0  # 닫힌 포지션엔 쓰지 않음
```

`insert_live_strategy`는 이미 이 테스트 파일 상단에서 import돼 있다(`from
tests.trading_db_fixtures import insert_live_strategy`).

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_trading_db.py::test_connect_adds_peak_return_pct_column_to_existing_positions_table tests/test_trading_db.py::test_update_position_peak_return_pct_updates_open_position tests/test_trading_db.py::test_update_position_peak_return_pct_ignores_closed_position -v`
Expected: FAIL — `peak_return_pct` 컬럼이 없거나 `update_position_peak_return_pct`
함수가 없어 `AttributeError`/`sqlite3.OperationalError`

- [ ] **Step 3: `_SCHEMA`의 `positions` 테이블 정의에 컬럼 추가**

`trading/db.py`의 `CREATE TABLE IF NOT EXISTS positions` 정의(55번째 줄 근처)에서
`entry_fee` 줄 바로 다음에 추가:

```python
    entry_fee        REAL NOT NULL DEFAULT 0,
    peak_return_pct  REAL NOT NULL DEFAULT 0,
```

- [ ] **Step 4: 기존 DB용 마이그레이션 함수 추가**

`_ensure_live_strategies_active_regime_column` 함수(276번째 줄 근처) 바로 다음에
추가:

```python


def _ensure_positions_peak_return_pct_column(conn: sqlite3.Connection) -> None:
    """CREATE TABLE IF NOT EXISTS는 이미 존재하는 positions 테이블에 새 컬럼
    peak_return_pct를 추가하지 못한다. entry_fee와 동일한 이유로(AWS에서 실거래
    중인 프로덕션 DB라 파일을 지울 수 없음) ALTER TABLE로 직접 추가한다 — 기존
    오픈 포지션은 DEFAULT 0에서 추적을 재개한다(마이그레이션 시점 이후의 실제
    고점보다 낮게 시작할 수 있음, 알려진 한계 — 계단식 트레일링 스탑 Phase 2
    설계 스펙 참고)."""
    table_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='positions'"
    ).fetchone() is not None
    if not table_exists:
        return
    columns = {row[1] for row in conn.execute("PRAGMA table_info('positions')")}
    if "peak_return_pct" in columns:
        return
    conn.execute("ALTER TABLE positions ADD COLUMN peak_return_pct REAL NOT NULL DEFAULT 0")
    conn.commit()
```

`_connect()` 함수(293번째 줄 근처)의 마이그레이션 호출 목록 마지막에 추가:

```python
        _ensure_live_strategies_active_regime_column(conn)
        _ensure_positions_peak_return_pct_column(conn)
```

- [ ] **Step 5: 갱신 함수 추가**

`adjust_position_qty` 함수(734번째 줄 근처) 바로 다음에 추가:

```python


def update_position_peak_return_pct(position_id: str, peak_return_pct: float) -> None:
    """열려있는 포지션의 고점 수익률을 갱신한다. status='open' 가드로 이미 닫힌
    포지션에 대한 뒤늦은 쓰기를 무시한다(daemon 쿨다운/재시도 경합 시 안전)."""
    conn = _connect()
    try:
        conn.execute(
            "UPDATE positions SET peak_return_pct = ? WHERE id = ? AND status = 'open'",
            (peak_return_pct, position_id),
        )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 6: 테스트 실행해 통과 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_trading_db.py -v`
Expected: 전체 PASS(기존 테스트 회귀 없음)

- [ ] **Step 7: 커밋**

```bash
git add trading/db.py tests/test_trading_db.py
git commit -m "$(cat <<'EOF'
feat: 계단식 트레일링 스탑 Phase 2 - positions.peak_return_pct 컬럼 + 갱신 함수

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01FRATo6oMgBvRHHfjj8uEkX
EOF
)"
```

---

### Task 3: `trading/signal_engine.py` — 실시간 안전망 + 봉마감 경로 연동

**Files:**
- Modify: `trading/signal_engine.py`
- Test: `tests/test_signal_engine.py`

**Interfaces:**
- Consumes: Task 1의 `trailing_stop_step_level(peak_return_pct, step_pct) -> float | None`,
  Task 2의 `positions.peak_return_pct` 컬럼(DB 조회 결과에 이미 포함됨)
- Produces: `matched_risk_exit_indicator(sell_conditions, position_return_pct,
  position_peak_return_pct=None) -> str | None`(새 파라미터는 기본값 `None`이라
  기존 호출부와 하위호환), `_position_context(...) -> tuple[float | None, int |
  None, float | None]`(3-tuple로 확장 — Task 4의 `trading/daemon.py`가 이 확장된
  `matched_risk_exit_indicator`를 호출한다)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_signal_engine.py`에서 `test_position_context_computes_return_pct_and_holding_bars`
함수(174번째 줄 근처) 바로 다음에 추가:

```python


def test_position_context_returns_max_of_stored_and_current_return_as_peak(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm)
    position_id = open_position(strategy_id, "KRW-BTC", 100.0, 1.0)
    dbm.update_position_peak_return_pct(position_id, 20.0)  # daemon이 이전에 flush해둔 값

    # 지금 종가 기준 수익률(10%)은 저장된 고점(20%)보다 낮다 -> 저장된 값을 유지해야 함
    _, _, peak = signal_engine._position_context(
        strategy_id, 110.0, datetime.now(timezone.utc), "minutes60",
    )
    assert peak == 20.0

    # 지금 종가 기준 수익률(30%)이 저장된 고점(20%)보다 높다 -> 방금 계산한 값을 써야 함
    _, _, peak = signal_engine._position_context(
        strategy_id, 130.0, datetime.now(timezone.utc), "minutes60",
    )
    assert peak == 30.0


def test_position_context_returns_none_none_none_when_no_open_position(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm)

    result = signal_engine._position_context(
        strategy_id, 100.0, datetime.now(timezone.utc), "minutes60",
    )

    assert result == (None, None, None)
```

`tests/test_signal_engine.py`에서 `test_matched_risk_exit_indicator_ignores_holding_period_bars`
함수(586번째 줄 근처) 바로 다음에 추가:

```python


def test_matched_risk_exit_indicator_returns_trailing_stop_when_breached():
    sell = {"type": "OR", "conditions": [
        {"indicator": "TRAILING_STOP_STEP_PCT", "params": {}, "operator": "<=", "threshold": 5},
    ]}
    # peak=16%, step=5 -> stop_level=10%. 현재 수익률 9%는 그 이하이므로 매치돼야 한다.
    assert signal_engine.matched_risk_exit_indicator(
        sell, position_return_pct=9.0, position_peak_return_pct=16.0,
    ) == "TRAILING_STOP_STEP_PCT"


def test_matched_risk_exit_indicator_returns_none_when_trailing_stop_not_breached():
    sell = {"type": "OR", "conditions": [
        {"indicator": "TRAILING_STOP_STEP_PCT", "params": {}, "operator": "<=", "threshold": 5},
    ]}
    # peak=16%, step=5 -> stop_level=10%. 현재 수익률 11%는 그 위이므로 매치되지 않아야 한다.
    assert signal_engine.matched_risk_exit_indicator(
        sell, position_return_pct=11.0, position_peak_return_pct=16.0,
    ) is None


def test_matched_risk_exit_indicator_ignores_trailing_stop_without_peak():
    sell = {"type": "OR", "conditions": [
        {"indicator": "TRAILING_STOP_STEP_PCT", "params": {}, "operator": "<=", "threshold": 5},
    ]}
    # position_peak_return_pct를 안 넘기면(기본값 None) 항상 미발동이어야 한다
    # (기존 호출부와의 하위호환 확인).
    assert signal_engine.matched_risk_exit_indicator(sell, position_return_pct=-100.0) is None
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_signal_engine.py -k "trailing_stop or peak" -v`
Expected: FAIL — `_position_context`가 아직 2-tuple만 반환(3-tuple 언패킹
`ValueError`), `matched_risk_exit_indicator`가 `position_peak_return_pct` 키워드
인자를 몰라 `TypeError`

- [ ] **Step 3: import에 `trailing_stop_step_level` 추가**

`trading/signal_engine.py` 상단의 `from engine.condition_tree import (...)` 블록
(17번째 줄 근처)에 추가:

```python
from engine.condition_tree import (
    POSITION_RELATIVE_INDICATORS,
    apply_operator,
    collect_blocks,
    eval_group_values,
    find_indicators_with_missing_values,
    indicator_key,
    max_required_period,
    required_aux_markets,
    trailing_stop_step_level,
)
```

- [ ] **Step 4: `_position_context()`를 3-tuple로 확장**

기존(148번째 줄 근처):

```python
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

교체 후:

```python
def _position_context(
    live_strategy_id: str, latest_close: float, latest_candle_time, timeframe: str,
) -> tuple[float | None, int | None, float | None]:
    """오픈 포지션이 있으면 (수익률%, 보유 봉 수, 고점 수익률%)를, 없으면
    (None, None, None)을 반환한다(STOP_LOSS_PCT/TAKE_PROFIT_PCT/HOLDING_PERIOD_BARS/
    TRAILING_STOP_STEP_PCT 평가용, 설계 스펙 결정7 + Phase 2). 고점은 daemon의
    실시간 리스크 청산 루프가 주기적으로 flush해둔 `positions.peak_return_pct`와
    방금 계산한 현재 수익률 중 큰 값이다 — 마지막 flush 이후의 지연을 봉 마감
    시점에 즉석 보정한다(추가 DB 쓰기 없이 읽기만으로 보정)."""
    position = get_open_position(live_strategy_id)
    if position is None:
        return None, None, None

    entry_price = position["entry_price"]
    position_return_pct = (latest_close - entry_price) / entry_price * 100
    position_peak_return_pct = max(position["peak_return_pct"], position_return_pct)

    entry_time = _to_utc_timestamp(position["entry_time"])
    candle_time = _to_utc_timestamp(latest_candle_time)
    elapsed = candle_time - entry_time
    position_holding_bars = max(int(elapsed / timeframe_duration(timeframe)), 0)

    return position_return_pct, position_holding_bars, position_peak_return_pct
```

- [ ] **Step 5: `evaluate_signals()` 호출부 갱신**

기존(226-231번째 줄 근처):

```python
    latest_close = df["close"].iloc[-1]
    position_return_pct, position_holding_bars = _position_context(
        live_strategy_id, latest_close, latest_candle_time, timeframe,
    )

    buy_result = eval_group_values(buy_conditions, values, position_return_pct, position_holding_bars)
    sell_result = eval_group_values(sell_conditions, values, position_return_pct, position_holding_bars)
```

교체 후:

```python
    latest_close = df["close"].iloc[-1]
    position_return_pct, position_holding_bars, position_peak_return_pct = _position_context(
        live_strategy_id, latest_close, latest_candle_time, timeframe,
    )

    buy_result = eval_group_values(
        buy_conditions, values, position_return_pct, position_holding_bars, position_peak_return_pct,
    )
    sell_result = eval_group_values(
        sell_conditions, values, position_return_pct, position_holding_bars, position_peak_return_pct,
    )
```

- [ ] **Step 6: `_TICKER_RISK_INDICATORS`에 추가**

기존(296번째 줄 근처):

```python
_TICKER_RISK_INDICATORS = {"STOP_LOSS_PCT", "TAKE_PROFIT_PCT"}
```

교체 후:

```python
_TICKER_RISK_INDICATORS = {"STOP_LOSS_PCT", "TAKE_PROFIT_PCT", "TRAILING_STOP_STEP_PCT"}
```

- [ ] **Step 7: `matched_risk_exit_indicator()` 확장**

기존:

```python
def matched_risk_exit_indicator(sell_conditions: dict, position_return_pct: float) -> str | None:
    """STOP_LOSS_PCT/TAKE_PROFIT_PCT를 sell_conditions_json 안의 다른 조건과의 AND/OR
    결합과 무관하게 독립 안전망으로 평가한다(⑤-4c 설계 스펙 결정1). 위반된 블록의
    indicator 이름(트리에서 먼저 발견된 것)을 반환, 없으면 None. daemon.py가 반환값을
    order_executor.exit_for_risk()의 close_reason 기록에 그대로 쓴다."""
    for block in collect_blocks(sell_conditions):
        if block["indicator"] in _TICKER_RISK_INDICATORS:
            if apply_operator(position_return_pct, block["operator"], float(block["threshold"])):
                return block["indicator"]
    return None
```

교체 후:

```python
def matched_risk_exit_indicator(
    sell_conditions: dict, position_return_pct: float,
    position_peak_return_pct: float | None = None,
) -> str | None:
    """STOP_LOSS_PCT/TAKE_PROFIT_PCT/TRAILING_STOP_STEP_PCT를 sell_conditions_json 안의
    다른 조건과의 AND/OR 결합과 무관하게 독립 안전망으로 평가한다(⑤-4c 설계 스펙
    결정1, TRAILING_STOP_STEP_PCT는 Phase 2). 위반된 블록의 indicator 이름(트리에서
    먼저 발견된 것)을 반환, 없으면 None. daemon.py가 반환값을
    order_executor.exit_for_risk()의 close_reason 기록에 그대로 쓴다.

    TRAILING_STOP_STEP_PCT는 STOP_LOSS_PCT/TAKE_PROFIT_PCT처럼 apply_operator로
    바로 비교할 수 없다(손절선 자체가 고점에서 매번 새로 계산되는 이동값 —
    engine/condition_tree.py의 eval_group()과 동일 이유). position_peak_return_pct가
    None이면(daemon이 아직 고점을 안 넘겼거나 이 호출부가 고점을 모르는 경우)
    이 지표는 항상 미발동으로 취급한다."""
    for block in collect_blocks(sell_conditions):
        if block["indicator"] == "TRAILING_STOP_STEP_PCT":
            if position_peak_return_pct is None:
                continue
            stop_level = trailing_stop_step_level(position_peak_return_pct, float(block["threshold"]))
            if stop_level is not None and position_return_pct <= stop_level:
                return block["indicator"]
            continue
        if block["indicator"] in _TICKER_RISK_INDICATORS:
            if apply_operator(position_return_pct, block["operator"], float(block["threshold"])):
                return block["indicator"]
    return None
```

- [ ] **Step 8: 테스트 실행해 통과 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_signal_engine.py -v`
Expected: 전체 PASS(기존 테스트 회귀 없음 — 특히 기존
`test_matched_risk_exit_indicator_*` 4개가 `position_peak_return_pct` 인자 없이도
그대로 통과해야 한다)

- [ ] **Step 9: 커밋**

```bash
git add trading/signal_engine.py tests/test_signal_engine.py
git commit -m "$(cat <<'EOF'
feat: 계단식 트레일링 스탑 Phase 2 - signal_engine 실시간/봉마감 경로 연동

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01FRATo6oMgBvRHHfjj8uEkX
EOF
)"
```

---

### Task 4: `trading/daemon.py` — `_run_risk_exit_loop`에 실시간 고점 추적 배선

**Files:**
- Modify: `trading/daemon.py`
- Test: `tests/test_daemon.py`

**Interfaces:**
- Consumes: Task 2의 `db.update_position_peak_return_pct(position_id, peak_return_pct)`,
  Task 3의 `signal_engine.matched_risk_exit_indicator(sell_conditions,
  position_return_pct, position_peak_return_pct=None)`
- Produces: 없음(`_run_risk_exit_loop`의 내부 동작만 바뀐다)

**중요**: 이 태스크는 `_run_risk_exit_loop` 함수 안의 **정확히 지정된 위치**에만
코드를 추가한다. 기존 락 획득 시점, 가드 순서, 쿨다운 로직은 그대로 둔다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_daemon.py`에서 `test_run_risk_exit_loop_triggers_exit_for_risk_when_stop_loss_breached`
함수(789번째 줄 근처) 바로 다음에 추가:

```python


async def test_run_risk_exit_loop_triggers_exit_for_risk_when_trailing_stop_step_breached(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(
        dbm, status="running", market="KRW-BTC",
        sell_conditions_json=json.dumps({"type": "OR", "conditions": [
            {"indicator": "TRAILING_STOP_STEP_PCT", "params": {}, "operator": "<=", "threshold": 5},
        ]}),
    )
    position_manager.open_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)
    captured = {}

    async def fake_stream_ticker(markets):
        yield {"type": "ticker", "code": "KRW-BTC", "trade_price": 58_000_000.0}  # +16%, 고점 형성
        yield {"type": "ticker", "code": "KRW-BTC", "trade_price": 54_500_000.0}  # +9%, stop_level(10%) 이하 -> 매치

    async def fake_exit_for_risk(strategy, position, price, reason, **kwargs):
        captured.update(strategy_id=strategy["id"], price=price, reason=reason)
        return {"action": "exited", "order_id": "o1"}

    monkeypatch.setattr(upbit_ws, "stream_ticker", fake_stream_ticker)
    monkeypatch.setattr(order_executor, "exit_for_risk", fake_exit_for_risk)
    monkeypatch.setattr(risk_manager, "check_circuit_breaker", lambda sid, cfg: None)

    await daemon._run_risk_exit_loop(strategy_id)

    assert captured["reason"] == "trailing_stop_step_pct"
    assert captured["price"] == 54_500_000.0


async def test_run_risk_exit_loop_does_not_trigger_trailing_stop_before_first_step(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(
        dbm, status="running", market="KRW-BTC",
        sell_conditions_json=json.dumps({"type": "OR", "conditions": [
            {"indicator": "TRAILING_STOP_STEP_PCT", "params": {}, "operator": "<=", "threshold": 5},
        ]}),
    )
    position_manager.open_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)
    exit_calls = {"n": 0}

    async def fake_stream_ticker(markets):
        yield {"type": "ticker", "code": "KRW-BTC", "trade_price": 51_000_000.0}  # +2%, 아직 step 미달

    async def fake_exit_for_risk(strategy, position, price, reason, **kwargs):
        exit_calls["n"] += 1
        return {"action": "exited", "order_id": "o1"}

    monkeypatch.setattr(upbit_ws, "stream_ticker", fake_stream_ticker)
    monkeypatch.setattr(order_executor, "exit_for_risk", fake_exit_for_risk)

    await daemon._run_risk_exit_loop(strategy_id)

    assert exit_calls["n"] == 0


async def test_run_risk_exit_loop_flushes_peak_return_pct_only_after_interval_elapses(monkeypatch, tmp_path):
    """peak은 매 tick 메모리에서 갱신되지만, DB flush는 _PEAK_FLUSH_INTERVAL_SEC가
    지나야 일어난다(스로틀 — sqlite 동시쓰기 부하 완화, Phase 2 설계 스펙 결정5)."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(
        dbm, status="running", market="KRW-BTC",
        sell_conditions_json=json.dumps({"type": "OR", "conditions": [
            {"indicator": "TRAILING_STOP_STEP_PCT", "params": {}, "operator": "<=", "threshold": 5},
        ]}),
    )
    position_id = position_manager.open_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)
    clock = {"value": 0.0}
    monkeypatch.setattr(daemon.time, "monotonic", lambda: clock["value"])
    # daemon.py의 바깥 try/except가 async 제너레이터 본체에서 던져진 예외를 로그만
    # 남기고 삼켜버리므로(Important 5, stream_ticker 자체 예외 처리), 제너레이터
    # 안에서 직접 assert하면 실패가 조용히 사라져 테스트가 거짓으로 통과할 수 있다
    # — 그래서 값만 스냅샷으로 모아두고, 실제 assert는 루프가 끝난 뒤 밖에서 한다.
    snapshots = []

    async def fake_stream_ticker(markets):
        yield {"type": "ticker", "code": "KRW-BTC", "trade_price": 55_000_000.0}  # +10%
        snapshots.append(dbm.get_position(position_id)["peak_return_pct"])
        clock["value"] += daemon._PEAK_FLUSH_INTERVAL_SEC
        yield {"type": "ticker", "code": "KRW-BTC", "trade_price": 55_000_000.0}  # 여전히 +10%
        snapshots.append(dbm.get_position(position_id)["peak_return_pct"])

    monkeypatch.setattr(upbit_ws, "stream_ticker", fake_stream_ticker)

    await daemon._run_risk_exit_loop(strategy_id)

    assert snapshots == [0, 10.0]  # 첫 tick 직후엔 미경과라 flush 안 됨, 두 번째 tick에서 flush됨


async def test_run_risk_exit_loop_flushes_peak_return_pct_immediately_before_exit(monkeypatch, tmp_path):
    """청산이 트리거되면 주기적 flush 타이밍과 무관하게 그 즉시 고점을 DB에 남긴다
    (Phase 2 설계 스펙 "청산 직전 강제 flush")."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(
        dbm, status="running", market="KRW-BTC",
        sell_conditions_json=json.dumps({"type": "OR", "conditions": [
            {"indicator": "TRAILING_STOP_STEP_PCT", "params": {}, "operator": "<=", "threshold": 5},
        ]}),
    )
    position_id = position_manager.open_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)

    async def fake_stream_ticker(markets):
        yield {"type": "ticker", "code": "KRW-BTC", "trade_price": 58_000_000.0}  # +16%, 고점 형성
        yield {"type": "ticker", "code": "KRW-BTC", "trade_price": 54_500_000.0}  # +9%, 즉시 청산 트리거

    async def fake_exit_for_risk(strategy, position, price, reason, **kwargs):
        return {"action": "exited", "order_id": "o1"}

    monkeypatch.setattr(upbit_ws, "stream_ticker", fake_stream_ticker)
    monkeypatch.setattr(order_executor, "exit_for_risk", fake_exit_for_risk)
    monkeypatch.setattr(risk_manager, "check_circuit_breaker", lambda sid, cfg: None)

    await daemon._run_risk_exit_loop(strategy_id)

    # _PEAK_FLUSH_INTERVAL_SEC(30초)이 전혀 안 지났어도, 청산 직전 강제 flush로
    # 실제 청산을 유발한 고점(16%)이 DB에 남아있어야 한다.
    assert dbm.get_position(position_id)["peak_return_pct"] == 16.0


async def test_run_risk_exit_loop_resets_peak_tracking_when_position_changes(monkeypatch, tmp_path):
    """포지션이 청산 후 새로 진입하면(같은 전략, 새 position id) 로컬 고점 추적이
    새 포지션의 DB 값(0)에서 다시 시작해야 한다 — 옛 포지션의 고점을 새 포지션에
    그대로 이어받으면 안 된다(계단식 트레일링 스탑 Phase 2 설계 스펙 "에러 처리 /
    엣지 케이스 — 포지션 전환"). threshold=50으로 넉넉히 잡아 이 테스트 동안 실제
    청산은 트리거되지 않게 한다(청산 로직이 아니라 순수 peak 리셋만 검증)."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(
        dbm, status="running", market="KRW-BTC",
        sell_conditions_json=json.dumps({"type": "OR", "conditions": [
            {"indicator": "TRAILING_STOP_STEP_PCT", "params": {}, "operator": "<=", "threshold": 50},
        ]}),
    )
    first_position_id = position_manager.open_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)
    clock = {"value": 0.0}
    monkeypatch.setattr(daemon.time, "monotonic", lambda: clock["value"])
    second_position_id_holder = {}
    # daemon.py의 바깥 try/except가 async 제너레이터 본체에서 던져진 예외를 로그만
    # 남기고 삼켜버리므로(Important 5), 제너레이터 안에서 직접 assert하지 않고
    # 값만 스냅샷으로 모아 루프가 끝난 뒤 밖에서 assert한다.
    first_position_peak_after_flush = {}

    async def fake_stream_ticker(markets):
        yield {"type": "ticker", "code": "KRW-BTC", "trade_price": 58_000_000.0}  # +16%, 고점 형성
        clock["value"] += daemon._PEAK_FLUSH_INTERVAL_SEC
        yield {"type": "ticker", "code": "KRW-BTC", "trade_price": 58_000_000.0}  # flush 트리거
        first_position_peak_after_flush["value"] = dbm.get_position(first_position_id)["peak_return_pct"]
        # 첫 포지션 청산 후 새 포지션 진입(별도 id) — daemon 바깥(예: 신호 기반
        # 매도+매수)에서 벌어지는 상황을 흉내낸다.
        dbm.close_position_row(first_position_id, 58_000_000.0, 0.01, 0.0, 16.0, "signal")
        second_position_id = position_manager.open_position(strategy_id, "KRW-BTC", 60_000_000.0, 0.01)
        second_position_id_holder["id"] = second_position_id
        yield {"type": "ticker", "code": "KRW-BTC", "trade_price": 60_600_000.0}  # 새 포지션 +1%
        clock["value"] += daemon._PEAK_FLUSH_INTERVAL_SEC
        yield {"type": "ticker", "code": "KRW-BTC", "trade_price": 60_600_000.0}  # flush 트리거

    monkeypatch.setattr(upbit_ws, "stream_ticker", fake_stream_ticker)

    await daemon._run_risk_exit_loop(strategy_id)

    assert first_position_peak_after_flush["value"] == 16.0
    # 새 포지션의 고점은 옛 포지션의 16%를 이어받지 않고 1%에서 시작해야 한다.
    assert dbm.get_position(second_position_id_holder["id"])["peak_return_pct"] == 1.0
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_daemon.py -k "trailing_stop or peak" -v`
Expected: FAIL — `TRAILING_STOP_STEP_PCT`가 `_TICKER_RISK_INDICATORS`에 없거나(Task
3에서 이미 추가했으므로 이 부분은 통과) `daemon.py`가 `position_peak_return_pct`를
전혀 계산/전달하지 않아 청산이 트리거되지 않음, `daemon._PEAK_FLUSH_INTERVAL_SEC`
속성 자체가 없어 `AttributeError`

- [ ] **Step 3: 모듈 상수 추가**

`trading/daemon.py`의 `_RISK_EXIT_RETRY_COOLDOWN_SEC = 30` 정의 바로 다음에 추가:

```python
_RISK_EXIT_RETRY_COOLDOWN_SEC = 30

# 고점(peak_return_pct)은 매 tick 메모리에서 갱신하지만, DB에는 이 간격으로만
# flush한다 — tick마다 SQLite에 쓰면 daemon과의 동시쓰기 부하가 커진다(계단식
# 트레일링 스탑 Phase 2 설계 스펙 결정5). 실제 청산 트리거 직전에는 이 간격과
# 무관하게 항상 강제로 flush한다.
_PEAK_FLUSH_INTERVAL_SEC = 30
```

- [ ] **Step 4: `_run_risk_exit_loop`에 고점 추적 로컬 상태 추가**

기존(`last_risk_exit_attempt = float("-inf")` 정의, 210번째 줄 근처):

```python
    last_risk_exit_attempt = float("-inf")
    try:
        async for tick in upbit_ws.stream_ticker([market]):
```

교체 후:

```python
    last_risk_exit_attempt = float("-inf")
    tracked_position_id: str | None = None
    local_peak_return_pct: float = 0.0
    last_peak_flush = float("-inf")
    try:
        async for tick in upbit_ws.stream_ticker([market]):
```

- [ ] **Step 5: 매 tick 고점 갱신 + 스로틀 flush 추가**

기존(220-241번째 줄 근처, `position = position_manager.get_open_position(strategy_id)`
부터 `matched = signal_engine.matched_risk_exit_indicator(current_sell_conditions,
position_return_pct)`까지):

```python
                position = position_manager.get_open_position(strategy_id)
                if position is None:
                    continue
                # 라이브 전략 "전략 교체" 기능이 실행 중(running/paused)인 전략의
                # sell_conditions를 제자리에서 바꿀 수 있게 되면서, 함수 시작 시점에
                # 한 번만 읽은 sell_conditions를 계속 재사용하면 교체 이후에도 옛
                # 매도조건 기준으로 손절/익절을 계속 트리거하는 버그가 생긴다(태스크
                # 매니저는 이 태스크가 done()이거나 새로 활성화됐을 때만 재생성하므로,
                # 이미 실행 중인 태스크는 교체와 무관하게 계속 산다). 매 tick마다
                # 전략을 다시 읽어 최신 sell_conditions로 판단한다 — position도 이미
                # 매 tick 다시 읽고 있으므로(위 position_manager.get_open_position)
                # 비용 프로파일은 동일하다.
                current_strategy = db.get_live_strategy(strategy_id)
                if current_strategy is None:
                    continue
                current_sell_conditions = json.loads(current_strategy["sell_conditions_json"])
                position_return_pct = (
                    (trade_price - position["entry_price"]) / position["entry_price"] * 100
                )
                matched = signal_engine.matched_risk_exit_indicator(current_sell_conditions, position_return_pct)
```

교체 후:

```python
                position = position_manager.get_open_position(strategy_id)
                if position is None:
                    continue
                # 포지션이 바뀌었으면(신규 진입 또는 이 태스크의 첫 tick) 로컬 고점
                # 추적을 DB에 남아있던 값에서 이어서 시작한다(계단식 트레일링 스탑
                # Phase 2 설계 스펙 — daemon 재시작 시에도 마지막 flush 값부터 재개).
                if position["id"] != tracked_position_id:
                    tracked_position_id = position["id"]
                    local_peak_return_pct = position["peak_return_pct"]
                tick_return_pct = (trade_price - position["entry_price"]) / position["entry_price"] * 100
                local_peak_return_pct = max(local_peak_return_pct, tick_return_pct)
                now_for_peak_flush = time.monotonic()
                if now_for_peak_flush - last_peak_flush >= _PEAK_FLUSH_INTERVAL_SEC:
                    db.update_position_peak_return_pct(position["id"], local_peak_return_pct)
                    last_peak_flush = now_for_peak_flush
                # 라이브 전략 "전략 교체" 기능이 실행 중(running/paused)인 전략의
                # sell_conditions를 제자리에서 바꿀 수 있게 되면서, 함수 시작 시점에
                # 한 번만 읽은 sell_conditions를 계속 재사용하면 교체 이후에도 옛
                # 매도조건 기준으로 손절/익절을 계속 트리거하는 버그가 생긴다(태스크
                # 매니저는 이 태스크가 done()이거나 새로 활성화됐을 때만 재생성하므로,
                # 이미 실행 중인 태스크는 교체와 무관하게 계속 산다). 매 tick마다
                # 전략을 다시 읽어 최신 sell_conditions로 판단한다 — position도 이미
                # 매 tick 다시 읽고 있으므로(위 position_manager.get_open_position)
                # 비용 프로파일은 동일하다.
                current_strategy = db.get_live_strategy(strategy_id)
                if current_strategy is None:
                    continue
                current_sell_conditions = json.loads(current_strategy["sell_conditions_json"])
                position_return_pct = tick_return_pct
                matched = signal_engine.matched_risk_exit_indicator(
                    current_sell_conditions, position_return_pct, local_peak_return_pct,
                )
```

(`position_return_pct`는 기존과 동일한 계산을 `tick_return_pct`라는 이름으로
한 번만 수행하도록 정리했다 — 값은 기존 코드와 100% 동일하다.)

- [ ] **Step 6: lock 안 재확인 지점에도 고점 전달**

기존(280-289번째 줄 근처):

```python
                    fresh_position_return_pct = (
                        (trade_price - fresh_position["entry_price"]) / fresh_position["entry_price"] * 100
                    )
                    # 위 tick-loop 재읽기와 같은 이유로, lock 안의 최종 판단도 바깥
                    # 스코프의 stale한 sell_conditions가 아니라 방금 다시 읽은
                    # fresh_strategy 기준으로 해야 한다.
                    fresh_sell_conditions = json.loads(fresh_strategy["sell_conditions_json"])
                    fresh_matched = signal_engine.matched_risk_exit_indicator(
                        fresh_sell_conditions, fresh_position_return_pct,
                    )
```

교체 후:

```python
                    fresh_position_return_pct = (
                        (trade_price - fresh_position["entry_price"]) / fresh_position["entry_price"] * 100
                    )
                    # peak도 fresh 값 기준으로 재확인한다(3라운드 M3와 동일 원칙) —
                    # local_peak_return_pct(락 밖에서 갱신된 값)와 방금 다시 계산한
                    # fresh_position_return_pct 중 큰 값을 쓴다.
                    fresh_peak_return_pct = max(local_peak_return_pct, fresh_position_return_pct)
                    # 위 tick-loop 재읽기와 같은 이유로, lock 안의 최종 판단도 바깥
                    # 스코프의 stale한 sell_conditions가 아니라 방금 다시 읽은
                    # fresh_strategy 기준으로 해야 한다.
                    fresh_sell_conditions = json.loads(fresh_strategy["sell_conditions_json"])
                    fresh_matched = signal_engine.matched_risk_exit_indicator(
                        fresh_sell_conditions, fresh_position_return_pct, fresh_peak_return_pct,
                    )
```

- [ ] **Step 7: 청산 직전 강제 flush 추가**

기존(303-306번째 줄 근처):

```python
                    last_risk_exit_attempt = now
                    result = await order_executor.exit_for_risk(
                        fresh_strategy, fresh_position, trade_price, fresh_matched.lower(),
                    )
```

교체 후:

```python
                    last_risk_exit_attempt = now
                    # 주기적 flush 타이밍과 무관하게, 실제로 청산을 유발한 고점 값을
                    # 청산 직전에 반드시 DB에 남긴다(계단식 트레일링 스탑 Phase 2
                    # 설계 스펙 — "청산 직전 강제 flush").
                    db.update_position_peak_return_pct(fresh_position["id"], fresh_peak_return_pct)
                    local_peak_return_pct = fresh_peak_return_pct
                    last_peak_flush = now
                    result = await order_executor.exit_for_risk(
                        fresh_strategy, fresh_position, trade_price, fresh_matched.lower(),
                    )
```

- [ ] **Step 8: 테스트 실행해 통과 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_daemon.py -v`
Expected: 전체 PASS(기존 테스트 전부 회귀 없음 — 특히 STOP_LOSS_PCT/TAKE_PROFIT_PCT
관련 기존 테스트가 `local_peak_return_pct`/`tick_return_pct` 도입 후에도 동일하게
통과해야 한다)

- [ ] **Step 9: 전체 회귀 테스트 스위트 실행**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/ -q`
Expected: 전체 PASS(이 플랜의 Task 1~4에서 추가한 테스트 포함, 기존 스위트 회귀
없음). `tests/test_import_backtest_results.py::test_script_runs_as_real_subprocess_entry_point`가
실패하더라도, 이 플랜과 무관한 기존 Windows 콘솔 인코딩 이슈이니 무시한다(2026-09-12
세션에서 이미 확인됨).

- [ ] **Step 10: 커밋 + 푸시**

```bash
git add trading/daemon.py tests/test_daemon.py
git commit -m "$(cat <<'EOF'
feat: 계단식 트레일링 스탑 Phase 2 - 실시간 리스크 청산 루프에 고점 추적 배선

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01FRATo6oMgBvRHHfjj8uEkX
EOF
)"
git push
```

---

## 최종 완료 기준 (스펙 Phase 2와 동일)

- 실거래 전략에 `TRAILING_STOP_STEP_PCT` 조건이 있으면, tick마다 실시간으로
  고점이 갱신되고 계단식 손절선이 즉시 평가되어 STOP_LOSS_PCT/TAKE_PROFIT_PCT와
  동일한 반응 속도로 청산된다.
- peak 값은 tick마다가 아니라 `_PEAK_FLUSH_INTERVAL_SEC`(30초) 간격으로만, 그리고
  청산 직전에는 강제로 DB에 기록된다.
- 봉 마감 시점 조건트리 평가(`evaluate_signals`) 경로도 동일한 고점 기준으로
  정합적으로 동작한다.
- `_run_risk_exit_loop`의 기존 락/가드 구조는 변경되지 않았다.
- 계단 공식은 `engine/condition_tree.py`의 `trailing_stop_step_level()` 단일
  정의로 남아 백테스트/라이브가 공유한다.
- 신규 유닛/통합 테스트 전부 통과, 기존 테스트 스위트 회귀 없음(알려진 무관한
  실패 1건 제외).
