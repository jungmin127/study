# 라이브 트레이딩 더스트 잔량 자동 흡수 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 최소주문금액(5,000원) 미만 잔량 불일치가 발생해도 reconciler가 전략을 정지시키거나
포지션을 재오픈하지 않고 조용히 흡수하게 하며, 캔들 매도 경로에도 같은 최소금액 가드를 추가하고,
지금 AWS에 이미 멈춰있는 KRW-LINK 더스트 포지션을 안전하게 정리한다.

**Architecture:** `trading/reconciler.py`의 `_reconcile_position()`에 "포지션 없음 + 최소주문금액
미만 잔고 차이"를 감지하면 `baseline_qty`에 흡수하고 조용히 넘어가는 분기를 추가한다(Part 1).
`trading/order_executor.py`의 `handle_signal_result()` 캔들 매도 분기에 `exit_for_risk()`와
동일한 최소금액 가드를 추가한다(Part 2). `scripts/absorb_dust_position.py`는
`scripts/backfill_entry_fee.py`와 동일한 드라이런/--apply/자동백업 패턴을 따르는 1회성
정리 스크립트다(Part 3).

**Tech Stack:** Python 3.11, sqlite3, pytest(`asyncio_mode = auto` — `async def test_...`에
데코레이터 불필요), httpx(모킹 대상).

## Global Constraints

- 업비트 원화마켓 최소주문금액은 5,000원이다(`order_executor._MIN_ORDER_AMOUNT_KRW`와 동일
  값). `reconciler.py`와 `scripts/absorb_dust_position.py`는 이 모듈들의 기존 의존 경계
  원칙(각 파일 docstring에 명시된 "무엇에 의존하는지" 목록)을 지키기 위해 이 상수를 import가
  아니라 각자 자체 상수로 복제한다.
- `reconciler.py`의 기존 오차 허용치 `_QTY_EPSILON = 1e-6`을 그대로 재사용한다(새 스크립트도
  동일 값을 자체 상수로 둔다).
- 기존 코드 스타일을 그대로 따른다: 이 세 파일은 이미 "왜 이렇게 했는지"를 설명하는 긴 docstring/
  인라인 주석이 많다 — 새로 추가하는 코드도 그 스타일(비직관적인 이유를 설명하는 주석)을
  유지한다. 새 함수/분기의 동작(WHAT)을 설명하는 주석은 추가하지 않는다.
- 테스트는 각 파일의 기존 `_fresh_db(monkeypatch, tmp_path)` / `_strategy_row(dbm, ...)` /
  `_account(...)` 헬퍼를 그대로 재사용한다 — 새 헬퍼를 만들지 않는다.
- 모든 테스트는 `pytest tests/<file>.py -v`로 개별 실행해 통과를 확인한 뒤 커밋한다.

---

## 파일 구조

- Modify: `trading/reconciler.py` — Part 1 (더스트 자동 흡수 분기)
- Modify: `trading/order_executor.py` — Part 2 (캔들 매도 최소금액 가드)
- Create: `scripts/absorb_dust_position.py` — Part 3 (1회성 정리 스크립트)
- Modify: `tests/test_reconciler.py` — Part 1 테스트
- Modify: `tests/test_order_executor.py` — Part 2 테스트
- Create: `tests/test_absorb_dust_position.py` — Part 3 테스트

각 Part는 독립적으로 테스트 가능하고, 서로 다른 파일을 건드리므로 순서를 바꿔 실행해도
무방하다.

---

### Task 1: reconciler.py 더스트 자동 흡수

**Files:**
- Modify: `trading/reconciler.py:22` (상수 추가), `trading/reconciler.py:287-291` (분기 추가)
- Test: `tests/test_reconciler.py`

**Interfaces:**
- Consumes: `db.update_live_strategy_baseline_qty(live_strategy_id: str, baseline_qty: float) -> None`,
  `db.insert_manual_intervention_event(market: str, description: str, action_taken: str) -> str`
  (둘 다 `trading/db.py`에 이미 존재, 시그니처 변경 없음)
- Produces: `_reconcile_position()`이 새로 반환할 수 있는 액션 문자열 `"dust_absorbed"` —
  이 문자열은 이 태스크 밖에서 소비되지 않는다(로그/이벤트 기록용).

- [ ] **Step 1: 더스트 흡수 테스트 작성 (position 없음 + 최소금액 미만 → 정지 없이 흡수)**

`tests/test_reconciler.py`의 `test_reconcile_position_unexplained_open_uses_avg_buy_price`
함수 바로 앞(638번째 줄 근처)에 추가:

```python
async def test_reconcile_position_absorbs_dust_diff_into_baseline_without_pause(
    monkeypatch, tmp_path,
):
    """설계 문서 2026-08-21-live-trading-dust-position-auto-recovery — limit_timeout
    매도가 최소주문금액 미만 잔량을 의도적으로 팔지 않고 포지션을 닫으면, 그 잔량이
    실제 지갑에 그대로 남는다. 이 잔량은 어차피 거래소가 매도를 거부해 봇이 다룰 수
    없으므로, 포지션을 재오픈하고 전략을 정지시키는 대신 baseline_qty에 흡수해 조용히
    넘어가야 한다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, baseline_qty=0.0)

    async def fake_get_accounts(*, client=None):
        return [_account(0.00005, avg_buy_price="50000000")]  # 0.00005 * 5000만 = 2,500원

    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    result = await reconciler._reconcile_position(strategy, [])

    assert result == {"balance_mismatch": True, "action": "dust_absorbed", "paused": False}
    updated = dbm.get_live_strategy(strategy["id"])
    assert updated["baseline_qty"] == pytest.approx(0.00005)
    assert updated["status"] == "running"
    assert position_manager.get_open_position(strategy["id"]) is None
    conn = dbm._connect()
    try:
        rows = conn.execute(
            "SELECT action_taken, description FROM manual_intervention_events"
        ).fetchall()
    finally:
        conn.close()
    assert any(row[0] == "dust_absorbed" for row in rows)


async def test_reconcile_position_dust_diff_falls_back_when_avg_buy_price_zero(
    monkeypatch, tmp_path,
):
    """가치를 판단할 근거(avg_buy_price)가 없으면 수량이 아무리 작아도 더스트로 단정하지
    않고 기존 정지 경로를 그대로 따른다 — 원가 미추적 코인(입금/에어드롭)일 수 있어
    안전하게 사람 확인을 받는 쪽을 택한다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, baseline_qty=0.0)

    async def fake_get_accounts(*, client=None):
        return [_account(0.00005, avg_buy_price="0")]

    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    result = await reconciler._reconcile_position(strategy, [])

    assert result == {"balance_mismatch": True, "action": "unexplained", "paused": True}
    assert position_manager.get_open_position(strategy["id"]) is None
    assert dbm.get_live_strategy(strategy["id"])["status"] == "paused"


async def test_reconcile_position_open_position_dust_diff_still_pauses(monkeypatch, tmp_path):
    """더스트 흡수는 position이 None일 때만 적용한다(설계 문서 결정) — 이미 열려 있는
    포지션에서 발생한 최소금액 미만의 잔고 차이는 이번 변경 범위 밖이며, 기존 동작
    (정지 후 수량 self-heal)을 그대로 유지해야 한다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, baseline_qty=0.0)
    position_manager.open_position(strategy["id"], "KRW-BTC", 49_000_000.0, 0.01)

    async def fake_get_accounts(*, client=None):
        # 포지션 수량(0.01) + 더스트 수준 초과분(0.00005, ≈2,500원)
        return [_account(0.01005, avg_buy_price="50000000")]

    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    result = await reconciler._reconcile_position(strategy, [])

    assert result["action"] == "unexplained"
    assert result["paused"] is True
    position = position_manager.get_open_position(strategy["id"])
    assert position["entry_qty"] == pytest.approx(0.01005)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd 260711-upbit-v1 && python -m pytest tests/test_reconciler.py -k "absorbs_dust_diff or dust_diff_falls_back or open_position_dust_diff" -v`
Expected: `test_reconcile_position_absorbs_dust_diff_into_baseline_without_pause`가
`action == "unexplained"`(구현 전 기존 동작)로 나와 FAIL. 나머지 두 개는 기존 동작과
동일하므로 이미 PASS할 수 있음(그래도 실행해서 확인).

- [ ] **Step 3: reconciler.py에 더스트 흡수 분기 구현**

`trading/reconciler.py:22` 근처(`_QTY_EPSILON = 1e-6` 바로 아래)에 상수 추가:

```python
_QTY_EPSILON = 1e-6
# 업비트 원화마켓 최소 주문금액(2026-08 기준, docs.upbit.com/kr/docs/krw-market-info).
# order_executor.py의 동일 상수와 값은 같지만, 이 모듈은 그 파일에 의존하지 않는다는
# 원칙(모듈 docstring 참고)을 지키기 위해 값을 복제한다.
_MIN_ORDER_AMOUNT_KRW = 5000
```

`_reconcile_position()` 안의 다음 블록(현재 287-291행 부근):

```python
    diff = actual_qty - internal_qty
    if abs(diff) <= _QTY_EPSILON:
        return {"balance_mismatch": False, "action": "none", "paused": False}

    matched_orders = list(external_orders) + list(own_fills)
```

을 다음으로 교체:

```python
    diff = actual_qty - internal_qty
    if abs(diff) <= _QTY_EPSILON:
        return {"balance_mismatch": False, "action": "none", "paused": False}

    # 포지션이 없는데 실제 잔고가 최소주문금액 미만으로 남는 경우(설계 문서
    # docs/superpowers/specs/2026-08-21-live-trading-dust-position-auto-recovery-design.md
    # Part 1) — order_executor._run_limit_timeout()의 _finalize_first_leg()가 최소주문금액
    # 미만 잔량을 의도적으로 매도하지 않고 포지션만 종료했을 때 이 경로를 탄다. 그 잔량은
    # 어차피 거래소가 매도를 거부해 봇이 다룰 수 없으므로, 포지션을 재오픈하고 전략을
    # 정지시키는 대신 baseline_qty에 그대로 흡수해 조용히 넘어간다. avg_buy_price<=0
    # (원가 미추적 코인)이면 가치를 판단할 수 없으므로 안전하게 기존 로직(정지)으로
    # 폴백한다. position이 이미 열려 있는 경우는 이번 설계 범위 밖이라 건드리지 않는다.
    if position is None and diff > _QTY_EPSILON and avg_buy_price > 0:
        dust_value_krw = diff * avg_buy_price
        if dust_value_krw < _MIN_ORDER_AMOUNT_KRW:
            new_baseline = baseline_qty + diff
            db.update_live_strategy_baseline_qty(strategy["id"], new_baseline)
            db.insert_manual_intervention_event(
                market,
                f"최소주문금액 미만 잔고 차이 {diff:.8f}개(≈{dust_value_krw:.0f}원) "
                "baseline에 흡수",
                "dust_absorbed",
            )
            return {"balance_mismatch": True, "action": "dust_absorbed", "paused": False}

    matched_orders = list(external_orders) + list(own_fills)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd 260711-upbit-v1 && python -m pytest tests/test_reconciler.py -v`
Expected: 전체 PASS (기존 테스트 포함 회귀 없음).

- [ ] **Step 5: 커밋**

```bash
git add trading/reconciler.py tests/test_reconciler.py
git commit -m "$(cat <<'EOF'
fix: 포지션 없는 최소주문금액 미만 잔고 차이를 정지 없이 baseline에 흡수

limit_timeout 매도가 최소주문금액 미만 잔량을 의도적으로 남기고 포지션을
닫으면, reconciler가 그 잔량을 "설명 안 됨"으로 오인해 포지션을 재오픈하고
전략을 정지시켜 이후 매수/매도가 모두 막혔다. 그 잔량은 어차피 거래소가
매도를 거부하므로, 정지 대신 baseline_qty에 흡수하고 조용히 넘어간다.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: order_executor.py 캔들 매도 경로 최소금액 가드

**Files:**
- Modify: `trading/order_executor.py:820-831`
- Test: `tests/test_order_executor.py`

**Interfaces:**
- Consumes: 기존 `_MIN_ORDER_AMOUNT_KRW = 5000`(같은 파일 52행에 이미 존재, 새로 추가 안 함)
- Produces: 없음(내부 분기 조건 확장뿐, 외부에서 참조하는 새 이름 없음)

- [ ] **Step 1: 최소금액 미만이면 캔들 매도를 건너뛰는 테스트 작성**

`tests/test_order_executor.py`의 `test_handle_signal_result_skips_sell_when_manually_paused`
함수 바로 뒤(1298번째 줄 이후, 파일 끝 이전 아무 위치)에 추가:

```python
async def test_handle_signal_result_skips_sell_when_value_below_min_order_amount(
    monkeypatch, tmp_path,
):
    """exit_for_risk()에 이미 있는 최소주문금액 가드(709-724행)를 캔들 매도 경로에도
    동일하게 적용한다 — 안 그러면 매도 신호가 뜰 때마다 업비트가 거부할 주문을 내고
    예외만 반복해서 로그를 채운다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)
    position_manager.open_position(strategy["id"], "KRW-BTC", 50_000_000.0, 0.00009)  # ≈4,500원

    async def fake_exit(*args, **kwargs):
        raise AssertionError("최소주문금액 미만 잔량인데 exit()가 호출되면 안 된다")

    monkeypatch.setattr(order_executor, "exit", fake_exit)

    result = await order_executor.handle_signal_result(
        strategy["id"], _signal_result(sell_signal=True, latest_close=50_000_000.0),
        dry_run=True,
    )

    assert result["sell_action"] is None
    assert result["sell_order_id"] is None
    position = position_manager.get_open_position(strategy["id"])
    assert position is not None
    assert position["entry_qty"] == pytest.approx(0.00009)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd 260711-upbit-v1 && python -m pytest tests/test_order_executor.py -k skips_sell_when_value_below_min_order_amount -v`
Expected: FAIL — `fake_exit`의 `AssertionError`가 그대로 터지거나(현재는 가드가 없어
`exit()`가 호출됨), `position`이 `None`(정상 청산됨)이 되어 assert 실패.

- [ ] **Step 3: handle_signal_result()에 최소금액 가드 추가**

`trading/order_executor.py`의 다음 블록(현재 820-832행):

```python
        sellable_qty = _floor_volume(position["entry_qty"] - position["stale_resolved_qty"])
        if sellable_qty <= 0:
            # exit_for_risk()가 이미 이 포지션을 전량 소진 처리했어야 하는 방어적
            # 경계 케이스다(이 함수는 daemon의 전략별 lock을 잡지 않으므로, concurrent
            # risk-exit tick의 close보다 살짝 먼저 읽은 stale position dict를 볼 수
            # 있다 — 흔하지 않지만 실주문을 내는 것보다 조용히 건너뛰는 편이 안전하다).
            logger.warning(
                "포지션의 잔여주문 정리분이 이미 전량을 커버해 캔들 매도를 건너뛴다: "
                "strategy_id=%s entry_qty=%s stale_resolved_qty=%s",
                strategy_id, position["entry_qty"], position["stale_resolved_qty"],
            )
        else:
```

을 다음으로 교체:

```python
        sellable_qty = _floor_volume(position["entry_qty"] - position["stale_resolved_qty"])
        # exit_for_risk()의 최소주문금액 가드(709-724행)와 동일한 이유로 여기도 판다
        # (설계 문서 2026-08-21-live-trading-dust-position-auto-recovery Part 2) — 남은
        # 수량이 있어도 그 가치가 업비트 최소주문금액 미만이면 주문 자체가 거부되므로
        # 시도하지 않는다. 시도했다가 매 매도신호마다 예외만 반복되던 문제를 없앤다.
        if sellable_qty <= 0 or sellable_qty * expected_price < _MIN_ORDER_AMOUNT_KRW:
            # exit_for_risk()가 이미 이 포지션을 전량 소진 처리했어야 하는 방어적
            # 경계 케이스이거나(이 함수는 daemon의 전략별 lock을 잡지 않으므로, concurrent
            # risk-exit tick의 close보다 살짝 먼저 읽은 stale position dict를 볼 수
            # 있다), 남은 수량이 최소주문금액 미만인 경우다 — 어느 쪽이든 실주문을 내는
            # 것보다 조용히 건너뛰는 편이 안전하다.
            logger.warning(
                "포지션의 매도 가능 수량이 없거나 최소주문금액 미만이라 캔들 매도를 "
                "건너뛴다: strategy_id=%s entry_qty=%s stale_resolved_qty=%s "
                "sellable_qty=%s expected_price=%s",
                strategy_id, position["entry_qty"], position["stale_resolved_qty"],
                sellable_qty, expected_price,
            )
        else:
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd 260711-upbit-v1 && python -m pytest tests/test_order_executor.py -v`
Expected: 전체 PASS (기존 테스트 포함 회귀 없음 — 특히
`test_handle_signal_result_exits_on_sell_signal_and_records_trade`,
`test_handle_signal_result_blends_prior_stale_resolution_into_sell`는 값이 최소금액
이상이라 그대로 매도가 실행돼야 함).

- [ ] **Step 5: 커밋**

```bash
git add trading/order_executor.py tests/test_order_executor.py
git commit -m "$(cat <<'EOF'
fix: 캔들 매도 경로에도 최소주문금액 미만 가드 추가

exit_for_risk()에만 있던 가드를 handle_signal_result()의 캔들 매도
분기에도 동일하게 적용한다. 남은 수량의 가치가 업비트 최소주문금액
미만이면 매도 신호가 뜰 때마다 거부당할 주문을 내고 예외를 반복
로그로 남기던 문제를 없앤다.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: 기존 더스트 포지션 정리 스크립트

**Files:**
- Create: `scripts/absorb_dust_position.py`
- Test: `tests/test_absorb_dust_position.py`

**Interfaces:**
- Consumes: `db.get_live_strategy`, `position_manager.get_open_position`,
  `db.close_position_row(position_id, exit_price, exit_qty, realized_pnl, realized_pnl_pct, close_reason) -> None`,
  `db.update_live_strategy_baseline_qty`, `db.insert_manual_intervention_event`,
  `upbit_client.get_accounts(*, client=None) -> list[dict]` (전부 기존 함수, 시그니처 변경 없음)
- Produces: `run(strategy_id: str, apply: bool, force: bool) -> None` — 코루틴, CLI가
  `asyncio.run()`으로 호출한다. 다른 태스크가 이 함수를 소비하지 않는다(1회성 스크립트).

- [ ] **Step 1: 스크립트 골격 작성 (아직 정리 로직 없음)**

`scripts/absorb_dust_position.py` 새로 생성:

```python
"""
scripts/absorb_dust_position.py

1회성 정리 스크립트(설계 문서
docs/superpowers/specs/2026-08-21-live-trading-dust-position-auto-recovery-design.md
Part 3): reconciler가 최소주문금액 미만 잔고 불일치를 "설명 안 됨"으로 오인해 재오픈한
더스트 포지션을 안전하게 종결하고 baseline_qty에 흡수한다. Part 1(reconciler.py 수정)이
배포된 뒤 앞으로 재발하는 건은 자동으로 처리되므로, 이 스크립트는 그 수정 이전에 이미
생겨버린 건을 1회 정리하는 용도다.

position_manager.close_position()을 쓰면 실제 매도가 없었는데도 current_capital이
exit_price*exit_qty만큼 부풀려진다 — 그래서 db.close_position_row()를 직접 호출해
realized_pnl=0으로 종결하고 current_capital은 건드리지 않는다.

실행 전 trading.db를 자동 백업한다(--apply일 때만, scripts/backfill_entry_fee.py와 동일
패턴). 기본은 드라이런(무엇을 바꿀지만 출력).

사용법:
    python scripts/absorb_dust_position.py <live_strategy_id>              # 드라이런
    python scripts/absorb_dust_position.py <live_strategy_id> --apply      # 실제 적용
    python scripts/absorb_dust_position.py <live_strategy_id> --apply --force  # 안전장치 무시
"""
from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import trading.db as db
import trading.position_manager as position_manager
import trading.upbit_client as upbit_client

# 업비트 원화마켓 최소 주문금액. order_executor.py/reconciler.py의 동일 상수와 값은 같지만,
# 이 스크립트도 다른 trading 서브모듈에 의존하지 않는다는 원칙을 지키기 위해 값을 복제한다.
_MIN_ORDER_AMOUNT_KRW = 5000
# trading/reconciler.py와 동일한 오차 허용치.
_QTY_EPSILON = 1e-6


def _backup_db() -> Path:
    """raw shutil.copy2로는 WAL 모드에서 아직 체크포인트되지 않은 거래가 -wal 사이드카
    파일에만 있어 백업에서 누락될 수 있다. sqlite3의 온라인 백업 API를 쓰면 journal 모드와
    무관하게 항상 일관된 완전한 스냅샷을 얻는다(scripts/backfill_entry_fee.py와 동일)."""
    backup_path = db.DB_PATH.with_name(
        f"{db.DB_PATH.name}.bak-{datetime.now().strftime('%Y%m%dT%H%M%S')}"
    )
    src = sqlite3.connect(db.DB_PATH)
    try:
        dst = sqlite3.connect(backup_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return backup_path


async def _get_actual_balance(market: str) -> float:
    currency = market.split("-", 1)[1]
    accounts = await upbit_client.get_accounts()
    for account in accounts:
        if account["currency"] == currency:
            return float(account["balance"]) + float(account["locked"])
    return 0.0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("strategy_id", help="정리할 live_strategy_id")
    parser.add_argument("--apply", action="store_true", help="실제로 DB를 변경한다(기본은 드라이런)")
    parser.add_argument(
        "--force", action="store_true",
        help="포지션 가치가 최소주문금액 이상이어도 강제로 진행한다",
    )
    args = parser.parse_args()
```

- [ ] **Step 2: 정상 케이스 테스트 작성 (포지션 종결 + baseline 흡수 + capital 불변)**

`tests/test_absorb_dust_position.py` 새로 생성:

```python
import json

import pytest

import trading.db as db
import trading.position_manager as position_manager
import trading.upbit_client as upbit_client
from tests.trading_db_fixtures import insert_live_strategy
from scripts import absorb_dust_position as adp


def _fresh_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "trading.db")
    return db


def _strategy_row(dbm, *, baseline_qty=0.0, current_capital=1_000_000.0):
    strategy_id = insert_live_strategy(
        dbm, market="KRW-BTC", current_capital=current_capital,
        risk_config_json=json.dumps({"order_execution_mode": "market"}),
    )
    dbm.update_live_strategy_baseline_qty(strategy_id, baseline_qty)
    return dbm.get_live_strategy(strategy_id)


def _account(balance, avg_buy_price="0"):
    return {"currency": "BTC", "balance": str(balance), "locked": "0",
            "avg_buy_price": avg_buy_price}


async def test_run_closes_dust_position_and_absorbs_baseline_without_touching_capital(
    monkeypatch, tmp_path,
):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, baseline_qty=0.0, current_capital=1_000_000.0)
    position_manager.open_position(strategy["id"], "KRW-BTC", 50_000_000.0, 0.00009)  # ≈4,500원

    async def fake_get_accounts(*, client=None):
        return [_account(0.00009)]

    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    await adp.run(strategy["id"], apply=True, force=False)

    assert position_manager.get_open_position(strategy["id"]) is None
    updated = dbm.get_live_strategy(strategy["id"])
    assert updated["baseline_qty"] == pytest.approx(0.00009)
    assert updated["current_capital"] == pytest.approx(1_000_000.0)  # 자본 불변
    conn = dbm._connect()
    try:
        rows = conn.execute(
            "SELECT action_taken FROM manual_intervention_events"
        ).fetchall()
    finally:
        conn.close()
    assert any(row[0] == "dust_absorbed_manual_cleanup" for row in rows)


async def test_run_dry_run_does_not_modify_anything(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)
    position_manager.open_position(strategy["id"], "KRW-BTC", 50_000_000.0, 0.00009)

    async def fake_get_accounts(*, client=None):
        return [_account(0.00009)]

    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    await adp.run(strategy["id"], apply=False, force=False)

    assert position_manager.get_open_position(strategy["id"]) is not None
    assert dbm.get_live_strategy(strategy["id"])["baseline_qty"] == pytest.approx(0.0)


async def test_run_aborts_when_actual_balance_does_not_match_position(monkeypatch, tmp_path):
    """포지션 수량과 실제 잔고가 크게 다르면(더 큰 진짜 포지션과 혼동될 위험) 자동
    처리를 거부하고 사람이 직접 확인하게 한다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)
    position_manager.open_position(strategy["id"], "KRW-BTC", 50_000_000.0, 0.00009)

    async def fake_get_accounts(*, client=None):
        return [_account(0.05)]  # 포지션(0.00009)과 실제 잔고(0.05)가 크게 다름

    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    await adp.run(strategy["id"], apply=True, force=False)

    assert position_manager.get_open_position(strategy["id"]) is not None
    assert dbm.get_live_strategy(strategy["id"])["baseline_qty"] == pytest.approx(0.0)


async def test_run_refuses_without_force_when_value_at_or_above_min_order_amount(
    monkeypatch, tmp_path,
):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)
    position_manager.open_position(strategy["id"], "KRW-BTC", 50_000_000.0, 0.001)  # 5만원

    async def fake_get_accounts(*, client=None):
        return [_account(0.001)]

    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    await adp.run(strategy["id"], apply=True, force=False)

    assert position_manager.get_open_position(strategy["id"]) is not None


async def test_run_with_force_closes_position_above_min_order_amount(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)
    position_manager.open_position(strategy["id"], "KRW-BTC", 50_000_000.0, 0.001)

    async def fake_get_accounts(*, client=None):
        return [_account(0.001)]

    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    await adp.run(strategy["id"], apply=True, force=True)

    assert position_manager.get_open_position(strategy["id"]) is None
```

- [ ] **Step 3: 테스트 실패 확인 (아직 `run()` 함수 없음)**

Run: `cd 260711-upbit-v1 && python -m pytest tests/test_absorb_dust_position.py -v`
Expected: FAIL — `AttributeError: module 'scripts.absorb_dust_position' has no attribute 'run'`

- [ ] **Step 4: `run()` 함수 구현**

`scripts/absorb_dust_position.py`의 `_get_actual_balance()` 함수 뒤, `if __name__ ==` 블록
앞에 추가:

```python
async def run(strategy_id: str, apply: bool, force: bool) -> None:
    strategy = db.get_live_strategy(strategy_id)
    if strategy is None:
        print(f"전략을 찾을 수 없습니다: {strategy_id}")
        return

    position = position_manager.get_open_position(strategy_id)
    if position is None:
        print(f"정리할 오픈 포지션이 없습니다: strategy_id={strategy_id}")
        return

    market = strategy["market"]
    actual_balance = await _get_actual_balance(market)
    entry_qty = position["entry_qty"]
    entry_price = position["entry_price"]

    if abs(actual_balance - entry_qty) > _QTY_EPSILON:
        print(
            f"중단: 포지션 수량({entry_qty})과 실제 잔고({actual_balance})가 오차범위를 "
            "벗어나 일치하지 않습니다 — 더 큰 포지션과 혼동될 위험이 있어 자동 처리를 "
            "거부합니다. 직접 확인하세요."
        )
        return

    value_krw = entry_qty * entry_price
    if value_krw >= _MIN_ORDER_AMOUNT_KRW and not force:
        print(
            f"중단: 이 포지션의 가치({value_krw:.0f}원)가 최소주문금액"
            f"({_MIN_ORDER_AMOUNT_KRW}원) 이상이라 더스트로 보기 어렵습니다. "
            "정말 정리하려면 --force를 붙이세요."
        )
        return

    baseline_before = strategy["baseline_qty"] or 0.0
    print(
        f"strategy_id={strategy_id} market={market} entry_qty={entry_qty} "
        f"entry_price={entry_price} value_krw={value_krw:.0f} "
        f"baseline_qty(before)={baseline_before}"
    )

    if not apply:
        print("드라이런입니다. 실제로 적용하려면 --apply를 붙여 다시 실행하세요.")
        return

    backup_path = _backup_db()
    print(f"백업 완료: {backup_path}")

    db.close_position_row(position["id"], entry_price, entry_qty, 0.0, 0.0, "dust_cleanup")
    new_baseline = baseline_before + entry_qty
    db.update_live_strategy_baseline_qty(strategy_id, new_baseline)
    db.insert_manual_intervention_event(
        market,
        f"더스트 포지션 수동 정리: entry_qty={entry_qty}(≈{value_krw:.0f}원) baseline에 흡수",
        "dust_absorbed_manual_cleanup",
    )
    print(f"완료: 포지션 종결, baseline_qty {baseline_before} -> {new_baseline}")
```

`if __name__ == "__main__":` 블록 마지막 줄(`args = parser.parse_args()`) 뒤에 추가:

```python
    asyncio.run(run(args.strategy_id, args.apply, args.force))
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd 260711-upbit-v1 && python -m pytest tests/test_absorb_dust_position.py -v`
Expected: 전체 PASS (5개 테스트).

- [ ] **Step 6: 커밋**

```bash
git add scripts/absorb_dust_position.py tests/test_absorb_dust_position.py
git commit -m "$(cat <<'EOF'
feat: 더스트 포지션 1회성 정리 스크립트 추가

reconciler가 최소주문금액 미만 잔고 불일치를 오인해 재오픈한 더스트
포지션을, 실제 잔고 대조 후 안전하게 종결하고 baseline_qty에 흡수하는
1회성 스크립트. position_manager.close_position()이 아니라
db.close_position_row()를 직접 써서 current_capital을 건드리지 않는다.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## 배포 및 실행 순서 (구현 완료 후, 이 계획 밖의 운영 작업)

이 단계들은 계획 실행자가 코드로 하는 일이 아니라, 사용자가 AWS 서버에서 직접 수행해야 하는
운영 작업이다 — 계획 완료 보고 시 함께 안내한다.

1. Task 1/2/3 커밋을 AWS 서버에 배포(`deploy/update.sh` 등 기존 배포 절차).
2. AWS 서버에서 SSH로 `python scripts/absorb_dust_position.py <KRW-LINK 전략의 strategy_id>`
   드라이런 실행 → 출력 확인.
3. 이상 없으면 `--apply`로 재실행해 기존 더스트 포지션 정리.
4. 정리 후 해당 전략이 정상적으로 매수 신호를 받는지 다음 캔들에서 확인.
