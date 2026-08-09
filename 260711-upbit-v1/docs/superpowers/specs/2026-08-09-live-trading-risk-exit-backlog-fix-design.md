# ⑤-4c 실시간 손절/익절 백로그 8건 수정 — Design Spec

## 배경 및 목표

`docs/superpowers/specs/2026-08-09-live-trading-daemon-realtime-risk-exit-design.md`(⑤-4c)
구현 후 최종 브랜치 리뷰가 7라운드에 걸쳐 진행됐고, Critical 버그는 전부 해소됐지만
Important 5건 + Minor 3건이 백로그로 남았다([[upbit-v1-realtime-risk-exit-postmortem]]
참고). 전부 `trading/order_executor.py`의 `exit_for_risk()`/`_resolve_stale_ask_order()`
영역(잔여 미체결 매도 주문을 능동적으로 취소한 뒤 청산하는 경로)에 몰려있다.

이 문서는 그 8건을 고치기 위한 설계를 확정한다. [[upbit-v1-live-trading-roadmap-sequencing]]
이 정의한 1단계 완결 순서의 첫 항목이다.

## 근본 원인

8건 전부 하나의 구조적 결함으로 수렴한다: **잔여 주문을 정리해서 "얼마나 이미 팔렸는지"
알아낸 정보가, 그 틱(tick) 하나의 지역 변수에만 머물고 어디에도 영구 기록되지 않는다.**
그 틱이 예외로 중단되거나, 정리가 여러 틱에 걸쳐 나뉘거나, 그 정보가 최종 `close_position()`
호출에 전달되지 않으면 — 자금이 조용히 사라지거나(Important #1), 포지션이 방치되거나
(#2), 다음 틱이 이미 팔린 수량을 다시 팔려 하거나(#3, #4), 다른 포지션의 잔여분과
섞인다(#5).

**추가로 코드를 직접 읽어 확인한 것 — Important #1의 정확한 메커니즘**:
`trading/position_manager.py`의 `close_position(position_id, exit_price, exit_qty, fee,
close_reason)`은 `realized_pnl`을 계산할 때 `db.get_position(position_id)`로 **DB에
저장된 원래 포지션의 전체 `entry_qty`**를 다시 읽어와 원가(`entry_price * entry_qty`)로
쓴다. `exit_for_risk`가 잔여 주문 정리로 일부를 이미 판 뒤 나머지만 시장가로 파는
경우, `exit()`에 전달되는 `position` dict는 로컬에서 `entry_qty`를 줄인 shallow copy일
뿐이라 이 값이 `close_position()`의 원가 계산에는 반영되지 않는다 — "전체 원가 -
일부 매도금액"이 되어 PnL이 왜곡된다(포스트모템의 "+2.8%→-38.8%"가 이 계산식 때문).

## 범위

**이 스펙에서 다루는 것:** Important 5건 + Minor 3건, 전부 `trading/order_executor.py`
(+ `trading/db.py`의 지원 함수). 사용자와 합의: 8건을 한 스펙/플랜으로 같이 처리한다
(포스트모템이 권고한 대로 — 근본 원인이 같아서 따로 고치면 반복 작업이 됨).

**이 스펙에서 다루지 않는 것:** `trading/daemon.py`/`trading/signal_engine.py`/
`trading/risk_manager.py`는 변경 없음(`_run_risk_exit_loop`는 `exit_for_risk`의 반환
형태(`{"action": ..., "order_id": ...}`)를 그대로 소비하므로 이 스펙의 변경과 무관하게
동작한다). `trading/position_manager.py`의 `close_position()` 시그니처도 변경 없음
(아래 결정 참고).

## 핵심 결정

### 결정 1 — 잔여 정리 누적치를 `positions` 테이블에 영구 기록한다

`positions` 테이블에 컬럼 3개 추가(전부 `REAL NOT NULL DEFAULT 0`):
- `stale_resolved_qty` — 지금까지 잔여 주문 정리로 확인된 매도 수량 누적
- `stale_resolved_proceeds` — 그 매도로 발생한 총 대금(KRW) 누적
- `stale_resolved_fee` — 그 매도의 수수료 누적

아직 실거래 데이터가 없는 개발 단계이므로 마이그레이션 없이 `CREATE TABLE IF NOT EXISTS`
정의에 바로 추가한다(기존 로컬 `trading.db`는 재생성하면 됨).

새 함수 `db.accumulate_stale_resolution(position_id: str, qty: float, proceeds: float,
fee: float) -> None` — `UPDATE positions SET stale_resolved_qty = stale_resolved_qty + ?,
stale_resolved_proceeds = stale_resolved_proceeds + ?, stale_resolved_fee =
stale_resolved_fee + ? WHERE id = ?`. 잔여 주문 하나를 정리할 때마다 **그 자리에서 즉시**
호출한다 — 같은 틱에서 다음 주문 처리 중 예외가 나도 이미 커밋되어 있다(Important #3
해소). `_run_risk_exit_loop`가 매 틱 `position_manager.get_open_position()`으로 포지션을
새로 읽어오는 기존 계약(⑤-4c 결정8) 덕분에, 다음 틱은 이 누적값을 자동으로 이어받는다
(Important #4 해소).

### 결정 2 — 잔여 주문 조회를 전략 전체가 아니라 현재 포지션으로 좁힌다

`db.list_wait_orders(live_strategy_id, order_type=None)`에 `position_id: str | None = None`
파라미터를 추가한다. 지정되면 `AND position_id = ?` 조건을 더한다.

`exit_for_risk`는 `db.list_wait_orders(strategy["id"], position_id=position["id"])`로
호출을 바꾼다 — 이전에 종료된 포지션이 남긴 잔여 주문이 현재 포지션의 정리 대상에
섞이지 않는다(Important #5 해소). 예전 포지션이 남긴 `limit` 타입 잔여 주문은
`reconciler.sync_pending_limit_orders`가 이미 별도로 처리하고 있으므로, 이 범위 축소로
방치되는 주문이 생기지 않는다.

### 결정 3 — 이미 전량 소진됐으면 그 자리에서 포지션을 종료한다(사용자 확정)

**주의(셀프리뷰에서 발견해 명확화):** "누적치"는 이번 틱 시작 시점의 `position` dict
값이 아니라, **이번 틱에 처리한 잔여 주문 정리분까지 합친 값**이어야 한다.
`exit_for_risk`는 지역 변수 `total_resolved_qty/proceeds/fee`를 `position["stale_resolved_qty"]`
등(이전 틱까지의 누적치)으로 초기화한 뒤, 잔여 주문 정리 루프에서 각 건이 확정될 때마다
`db.accumulate_stale_resolution()`(DB 기록, 결정1)과 **동시에 이 지역 변수에도** 더한다.
루프가 끝난 뒤에는 이 지역 변수를 기준으로 판단한다 — 매 틱 재조회(DB 재조회)로도
같은 값을 얻을 수 있지만, 왕복을 줄이기 위해 지역 누적을 그대로 신뢰한다(어차피 결정1의
DB 기록이 이미 진실의 원천이고, 이 지역 변수는 그 값을 틱 안에서 미러링할 뿐이다).

`sellable_qty = position["entry_qty"] - total_resolved_qty`를 계산한다.

`sellable_qty`가 0 이하이거나 `sellable_qty * expected_price < _MIN_ORDER_AMOUNT_KRW`이면
(이미 잔여 주문 정리만으로 사실상 다 팔린 경우):
- 새 주문을 내지 않고, `total_resolved_qty/proceeds/fee`로 **바로
  `position_manager.close_position()`을 호출**해 포지션을 정상 종료 처리한다.
  `exit_price`는 `total_resolved_proceeds / total_resolved_qty`(가중평균), `exit_qty`는
  `total_resolved_qty`, `fee`는 `total_resolved_fee`.
- `risk_manager.record_trade_result()`도 호출한다(`handle_signal_result`의 매도 성공
  분기와 동일한 부기 의무).
- `{"action": "exited", "order_id": None}`을 반환한다(`order_id`는 새 주문이 없으므로
  `None` — `daemon._run_risk_exit_loop`는 `action`만 보고 `check_circuit_breaker()`를
  호출하므로 영향 없음).

이전에는 이 경우 아무것도 안 하고 포지션을 `open`으로 방치해 reconciler가 "설명 안
됨"으로 오분류하고 전략을 자동 정지시켰다(Important #2). 이 결정으로 reconciler가 이
포지션을 볼 일 자체가 없어진다.

### 결정 4 — 남은 수량이 있으면 잔여분을 최종 체결가에 가중평균으로 합산한다

`sellable_qty`가 있으면 기존처럼 `exit()`를 호출하되(`market` 강제는 유지, 4라운드
결정 그대로), 새 파라미터 3개를 추가로 넘긴다:

```python
async def exit(
    strategy: dict, position: dict, expected_price: float,
    *, client: httpx.AsyncClient | None = None, dry_run: bool = False,
    close_reason: str = "signal",
    pre_resolved_qty: float = 0.0, pre_resolved_proceeds: float = 0.0, pre_resolved_fee: float = 0.0,
) -> dict:
```

기본값 `0.0`이라 기존 호출부(`handle_signal_result`)와 기존 테스트는 전혀 안 바뀐다
(하위호환). `exit()`가 실주문 체결에 성공(`status == "done"`)했을 때 `close_position()`을
호출하는 부분을, 단순히 이번 체결분만 넘기는 대신 `pre_resolved_*`와 합산한 가중평균으로
바꾼다:

```python
total_qty = result["filled_volume"] + pre_resolved_qty
total_proceeds = result["filled_price"] * result["filled_volume"] + pre_resolved_proceeds
blended_price = total_proceeds / total_qty if total_qty else result["filled_price"]
total_fee = result["fee"] + pre_resolved_fee

close_result = position_manager.close_position(
    position["id"], blended_price, total_qty, total_fee, close_reason,
)
```

`exit_for_risk`는 결정3에서 정의한 지역 누적변수 `pre_resolved_qty=total_resolved_qty` 등을
넘긴다(이전 틱 누적치 + 이번 틱 정리분 전부 포함).
이게 "부분 매도금액 - 전체 원가"로 PnL이 왜곡되던 근본 원인(Important #1)을 없앤다 —
`close_position()` 자체의 시그니처는 바뀌지 않는다(호출자가 이미 합산한 값을 넘김).

### 결정 5 — Minor #6: 4xx 응답을 받은 잔여 주문을 terminal 상태로 마킹한다

`_resolve_stale_ask_order`가 `upbit_uuid is None`이고 identifier 조회가 4xx를 받으면
(거래소에 접수된 적 없는 주문), 지금은 `0.0`을 반환하고 그 행의 상태를 안 건드린다 —
다음 틱마다 `list_wait_orders`가 같은 행을 또 돌려줘서 같은 GET을 무한 재시도한다.

**변경:** 반환 전에 `db.update_order_filled(stale["id"], None, None, None, None, None,
"failed")`를 호출해 그 행을 terminal 상태로 남긴다. 이후 `list_wait_orders`(status='wait'
필터)가 이 행을 더 이상 반환하지 않는다.

### 결정 6 — Minor #7: 인증오류로 실패한 주문 행을 terminal 상태로 마킹한다

`enter()`/`exit()`는 `db.insert_order()`로 주문 행을 만든 직후 실행모드별 함수
(`_run_market`/`_run_limit`/`_run_limit_timeout`/`_run_market_capped`)를 호출한다. 이
호출이 401/403(인증오류, `_create_order_with_retry`가 잡지 않는 `httpx.HTTPStatusError`)로
실패하면 예외가 그대로 전파되고, 방금 만든 주문 행은 `status='wait'`로 영원히 남는다 —
ticker 경로는 재시도가 잦아 이 고아 행이 틱마다 하나씩 누적된다.

**변경:** `enter()`/`exit()`의 실행모드 분기(`if dry_run: ... elif mode == "market": ...`)
전체를 `try/except Exception`으로 감싸, 예외 시 `db.update_order_filled(order_id, None,
None, None, None, None, "failed")`로 그 행을 마킹한 뒤 예외를 다시 던진다(동작은
그대로, 실패 흔적만 남김). 이 수정은 risk-exit 전용이 아니라 `enter()`/`exit()` 공통
경로이므로 캔들 기반 매매(`handle_signal_result`)에도 동일하게 적용된다(일반적인
견고성 개선).

### 결정 7 — Minor #8: 코드로 고치지 않고 실거래 검증 항목으로 남긴다

"cancel 성공 응답을 받았는데 거래소가 여전히 wait로 보고하는" 극단 케이스는 mock으로
재현할 수 있는 버그가 아니라 실제 업비트 거래소 동작에 대한 불확실성이다. 코드 변경
없이 `_resolve_stale_ask_order()` docstring에 주석을 추가해, [[upbit-v1-live-trading-roadmap-sequencing]]의
"소액 실전 테스트" 단계에서 이 케이스가 실제로 발생하는지 관찰 대상으로 남긴다.

## 변경 파일

- `trading/db.py` — `positions` 테이블에 컬럼 3개 추가, `accumulate_stale_resolution()`
  신규, `list_wait_orders()`에 `position_id` 파라미터 추가.
- `trading/order_executor.py` — `exit()`에 `pre_resolved_*` 파라미터 3개 추가(결정4),
  `exit_for_risk()`를 결정2/3/4에 맞춰 재구성, `_resolve_stale_ask_order()`에 결정5
  추가, `enter()`/`exit()`에 결정6의 try/except 추가.
- `tests/test_db.py`, `tests/test_order_executor.py` — 각 결정에 대응하는 테스트 추가.

## 에러 처리

- 잔여 주문 정리 루프 중 예외는 지금처럼 `exit_for_risk` 밖으로 전파된다(변경 없음) —
  `_run_risk_exit_loop`가 이미 `try/except Exception` + 로그로 감싸고 있어(⑤-4c 결정,
  에러 처리 절) 다음 틱에 재시도된다. 결정1 덕분에 그 예외 이전에 이미 처리된 주문의
  누적치는 살아남는다.
- 결정6의 `try/except`는 `Exception`을 잡아 주문 행만 마킹하고 반드시 다시 던진다 —
  삼키지 않는다(호출자의 기존 에러 처리 경로를 그대로 유지).

## 테스트 전략

- `db.list_wait_orders(position_id=...)` 필터, `db.accumulate_stale_resolution()`: 각각
  단위테스트(누적 합산이 정확한지, 다른 position_id는 안 섞이는지).
- `exit()`의 `pre_resolved_*` 파라미터: 기본값(0)일 때 기존 동작과 완전히 동일한지
  회귀 확인 + 값을 넘겼을 때 가중평균 `exit_price`/합산 `exit_qty`/`fee`로
  `close_position()`이 호출되는지 검증.
- `exit_for_risk()` — 포스트모템의 실제 재현 시나리오를 그대로 회귀테스트화:
  - 잔여주문 부분체결 후 최종 시장가 매도 → `realized_pnl`이 두 체결 합산 기준으로
    정확한지(Important #1).
  - 잔여주문 정리만으로 전량 소진 → 새 주문 없이 `close_position()`이 즉시 호출되고
    `{"action": "exited", "order_id": None}`을 반환하는지(Important #2).
  - 잔여주문 2건 중 2번째 처리에서 예외 → 1번째의 누적치가 이미 DB에 반영돼 있는지
    (Important #3), 다음 틱 호출에서 그 누적치를 이어받아 정확히 처리하는지
    (Important #4).
  - 이전에 종료된 포지션이 남긴 잔여 주문이 `position_id` 필터로 걸러져 현재 포지션
    계산에 섞이지 않는지(Important #5).
- Minor #6/#7 각각: 4xx/401 실패를 유도해 해당 주문 행이 `status='failed'`로 마킹되는지
  검증.
- 전체 회귀(`python -m pytest -q`) — 기존 `exit_for_risk`/`daemon`/`enter`/`exit` 테스트가
  이 변경으로 깨지지 않는지 확인(특히 `pre_resolved_*` 기본값 경로).

## 자기 검토(스펙 완성도)

- **플레이스홀더 없음** — 7개 결정 각각 "왜"와 어느 백로그 항목을 해소하는지 명시했다.
- **내부 정합성**: 결정1(영구 기록)이 결정3(즉시 close)과 결정4(가중평균 close) 양쪽의
  전제 조건이다 — 순서가 논리적으로 맞다. 결정2(position_id 스코핑)는 결정1/3/4와
  독립적이라 어느 순서로 구현해도 무방하다.
- **범위 경계**: `daemon.py`/`signal_engine.py`는 `exit_for_risk`의 반환 형태
  (`{"action": ..., "order_id": ...}`)가 그대로 유지되므로 변경이 전파되지 않는다 —
  실제로 확인함(`_run_risk_exit_loop`는 `action` 필드만 읽는다).
- **하위호환 확인**: `exit()`의 새 파라미터 3개는 전부 기본값 0.0 — `handle_signal_result`
  의 기존 `exit()` 호출과 기존 테스트는 코드 변경 없이 그대로 통과해야 한다.
- **postmortem 대비 커버리지**: Important 5건 + Minor 3건 전부 결정 1~7에 하나씩
  대응됨을 재확인했다(빠진 항목 없음).
