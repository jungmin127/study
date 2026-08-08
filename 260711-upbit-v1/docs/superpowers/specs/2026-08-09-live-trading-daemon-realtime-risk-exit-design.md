# 라이브 트레이딩 서브플랜⑤-4c — 실시간 손절/익절(ticker 기반) Design Spec

## 배경 및 목표

`docs/superpowers/specs/2026-08-04-live-trading-foundation-design.md`(이하 "기반 스펙")가
결정한 "리스크 청산(손절/익절)은 캔들 마감을 기다리지 않는다 — `STOP_LOSS_PCT`/
`TAKE_PROFIT_PCT`는 실시간 체결(ticker) 스트림마다 별도로 평가한다"를 구현한다.
`docs/superpowers/specs/2026-08-08-live-trading-daemon-core.md`(⑤-4b, 이하 "daemon-core
스펙")가 결정1에서 이 부분을 의도적으로 범위 밖에 두고 "태스크셋 매니저/전략별 루프
구조에 새 백그라운드 요소를 얹기만 하면 된다"고 예고한 그 조각이다.

⑤-4b가 이미 만든 것: 전략마다 `asyncio.Task` 하나(`_run_strategy_loop`)를 두고, 그
안에서 캔들 기반 신호처리(`evaluate_signals`+`handle_signal_result`)와 reconciler
2종(`check_manual_intervention`+`sync_pending_limit_orders`)을 **같은 코루틴 안에서
순차 실행**해 동시성 충돌을 원천 차단했다(daemon-core 스펙 결정3). 이 스펙이 추가하는
ticker 기반 실시간 청산은 이 불변조건을 깨지 않으면서(같은 전략에 대해 `order_executor.
exit()`를 동시에 두 곳에서 부르면 안 됨) 새로운 비동기 이벤트 소스(WS ticker)를 얹어야
한다.

## 범위

**이 스펙에서 확정하는 것:**
- `sell_conditions_json` 트리에서 `STOP_LOSS_PCT`/`TAKE_PROFIT_PCT` 블록만 독립적으로
  뽑아 평가하는 방식(다른 지표와의 AND/OR 결합 무시)
- ticker WebSocket 구독을 daemon.py의 태스크셋 관리 구조에 통합하는 방식
- `_run_strategy_loop`(캔들 기반)와 새 ticker 루프가 같은 전략에 대해 주문 실행을
  직렬화하는 방식
- ticker 트리거로 청산할 때의 실행 경로(`order_executor` 신규 진입점) + 감사 추적
  (`close_reason`)
- `trading/daemon.py`가 `engine/`을 여전히 import하지 않도록 유지하는 방식

**이 스펙에서 다루지 않는 것:**
- `HOLDING_PERIOD_BARS` — 봉 개수 기반이라 캔들 주기 그대로 유지(ticker와 무관, 기존
  `_run_strategy_loop` 경로가 이미 처리).
- 승인/제어 API, "라이브 전략 관리" 프론트엔드 — ⑥(UX).
- 텔레그램 알림 — 2단계.

## 핵심 결정

### 결정 1 — `STOP_LOSS_PCT`/`TAKE_PROFIT_PCT`는 다른 조건과의 AND/OR 결합을 무시하고
독립 안전망으로 평가한다(사용자 확정)

`sell_conditions_json`은 하나의 트리라 `RSI>70 OR STOP_LOSS_PCT<=-5`처럼 손절/익절이
다른 기술적 지표와 섞일 수 있다. 하지만 ticker 스트림은 가격만 주고 RSI 같은 지표값은
주지 않으므로, 트리 전체를 ticker마다 재평가하려면 직전 캔들 시점의 지표값을 캐싱해야
하고 그 값과 실시간 가격이 시차 혼합되는 문제가 생긴다.

**결정:** `STOP_LOSS_PCT`/`TAKE_PROFIT_PCT` 블록만 트리에서 뽑아, 원래 트리 안의
AND/OR 결합과 무관하게 "이 값이 나오면 무조건 즉시 청산"하는 독립 안전망으로 취급한다.
사용자가 `RSI>70 AND STOP_LOSS_PCT<=-5`처럼 결합해도, ticker 경로에서는 `STOP_LOSS_PCT`
단독 위반만으로 즉시 청산한다(RSI 조건은 무시). 캔들 기반 경로(`_run_strategy_loop`)는
이 스펙으로 전혀 바뀌지 않으므로, 봉 마감 시점에는 여전히 트리 전체(AND 포함)가 정확히
평가된다 — 두 경로가 "더 보수적인 쪽(ticker)이 안전망, 더 정확한 쪽(캔들)이 정식 판정"
관계로 공존한다.

### 결정 2 — 추출/평가 로직은 `trading/signal_engine.py`에 둔다, `daemon.py`가 아니다

`daemon.py`는 `engine/`을 전혀 import하지 않기로 확정돼 있다(daemon-core 스펙, 기반
스펙 결정1 "backtrader를 라이브 엔진에서 완전 배제"). `STOP_LOSS_PCT`/`TAKE_PROFIT_PCT`
블록 추출에 필요한 `collect_blocks()`/`apply_operator()`는 `engine/condition_tree.py`에
있다. `trading/signal_engine.py`는 이미 이 모듈에서 `collect_blocks`를 포함해 여러
함수를 import하고 있으므로(⑤-2), 새 함수 두 개를 여기 추가하고 `daemon.py`는
`trading.signal_engine`을 통해서만 간접 사용한다 — 기존 import 경계를 그대로 지킨다.

```python
# trading/signal_engine.py
from engine.condition_tree import (
    POSITION_RELATIVE_INDICATORS, apply_operator, collect_blocks,
    eval_group_values, indicator_key, max_required_period, required_aux_markets,
)  # apply_operator만 새로 추가

_TICKER_RISK_INDICATORS = {"STOP_LOSS_PCT", "TAKE_PROFIT_PCT"}


def has_risk_exit_conditions(sell_conditions: dict) -> bool:
    """sell_conditions_json에 STOP_LOSS_PCT/TAKE_PROFIT_PCT 블록이 하나라도 있는지
    확인한다(⑤-4c: 없는 전략은 ticker WS 연결 자체를 안 열기 위한 최적화용)."""
    return any(b["indicator"] in _TICKER_RISK_INDICATORS for b in collect_blocks(sell_conditions))


def matched_risk_exit_indicator(sell_conditions: dict, position_return_pct: float) -> str | None:
    """STOP_LOSS_PCT/TAKE_PROFIT_PCT를 독립 안전망으로 평가(결정1). 위반된 블록의
    indicator 이름(첫 번째로 찾은 것)을 반환, 없으면 None. daemon.py가 반환값을
    close_reason 기록에 그대로 쓴다."""
    for block in collect_blocks(sell_conditions):
        if block["indicator"] in _TICKER_RISK_INDICATORS:
            if apply_operator(position_return_pct, block["operator"], float(block["threshold"])):
                return block["indicator"]
    return None
```

### 결정 3 — ticker 구독은 전략당 개별 WS 연결(사용자 확정)

`upbit_ws.stream_ticker(markets)`는 구독 마켓 목록을 연결 시점에 고정한다(동적 추가/
제거 불가). 모든 활성 전략의 마켓을 하나의 공유 연결로 묶으면, 마켓 집합이 바뀔 때마다
`_task_set_manager_loop`의 20초 주기에 맞춰 재연결하는 로직이 추가로 필요하고, 한 전략의
tick을 다른 전략 처리와 분리하는 라우팅 레이어도 필요해진다.

**결정:** `_task_set_manager_loop`가 `_run_strategy_loop`와 똑같은 생명주기로 전략당
`_run_risk_exit_loop` 태스크도 `create_task`/`cancel`한다. 각 연결은 해당 전략의 마켓
하나만 구독해 재구독 로직이 전혀 필요 없다(`stream_ticker`가 markets 고정을 전제로
제약받는 문제 자체가 없어짐). 코인당 최대 1개 전략 동시실행(기반 스펙 결정6)이 이미
확정돼 있어 연결 수는 활성 전략 수만큼만 늘어나고, 부담이 작다.

### 결정 4 — 전략당 `asyncio.Lock`을 `_run_strategy_loop`와 `_run_risk_exit_loop`가
공유해 주문 실행을 직렬화한다(사용자 확정)

`_run_risk_exit_loop`가 청산을 트리거하면 `order_executor.exit()`를 호출하는데, 같은
전략에 대해 `_run_strategy_loop`의 신호처리(`enter()`/`exit()`)나 reconcile
(`check_manual_intervention()`)이 동시에 돌면 daemon-core 스펙 결정3이 막아둔 바로 그
레이스가 재발한다(⑤-4a 최종리뷰가 daemon.py에 남긴 전제조건 — "reconciler를 enter()/
exit()와 동시에 돌리지 말 것" — 는 ticker 태스크에도 그대로 적용된다).

**결정:** `_task_set_manager_loop`가 전략당 `asyncio.Lock()`을 하나 만들어
`_run_strategy_loop`와 `_run_risk_exit_loop` 양쪽에 넘긴다. `_run_strategy_loop`는
기존 신호처리 try 블록과 reconcile try 블록 각각을 `async with lock:`으로 감싼다(둘 다
전체가 아니라 개별 블록 — 두 블록은 원래도 같은 코루틴 안에서 순차 실행이라 서로
겹칠 수 없고, ticker 태스크만 막으면 됨). `_run_risk_exit_loop`는 청산 트리거 시
`async with lock:`을 잡은 뒤 포지션을 다시 확인하고 `exit()`를 부른다. lock을 못 잡는
동안 ticker 태스크는 그냥 대기하고(동시성 자체를 막지 않음, 순차 실행만 보장), 캔들
루프가 60초씩 쉬는 동안 손절 트리거가 지연되는 게 아니라 "지금 진행 중인 한 틱(보통
수백ms~수초)"만큼만 늦게 반응한다.

### 결정 5 — ticker 트리거 청산은 `order_executor.exit_for_risk()`(신규)를 쓴다,
`handle_signal_result()`를 억지로 재사용하지 않는다

`handle_signal_result()`는 `signal_result["sell_signal_id"]`(캔들 사이클마다
`signals` 테이블에 미리 기록된 행)를 전제로 `db.update_signal_result()`를 호출한다.
ticker 트리거는 캔들 사이클 밖에서 발생하는 이벤트라 대응되는 `signals` 행이 없다 —
억지로 끼워맞추면 존재하지 않는 signal_id를 참조하거나 가짜 신호 행을 만들어야 한다.

**결정:** `trading/order_executor.py`에 전용 진입점을 추가한다.

```python
async def exit_for_risk(
    strategy: dict, position: dict, expected_price: float, reason: str,
    *, client: httpx.AsyncClient | None = None, dry_run: bool = False,
) -> dict:
    """ticker 트리거 손절/익절 전용 진입점(⑤-4c). handle_signal_result()와 달리
    signals 테이블과 무관 — 대응되는 candle 사이클 신호 행이 없다. 성공 시
    record_trade_result()까지 호출(handle_signal_result의 매도 성공 분기와 동일한
    부기 의무 — daemon.py의 check_circuit_breaker() 호출 전제 조건, 결정7 재사용)."""
    order = await exit(strategy, position, expected_price, client=client, dry_run=dry_run, close_reason=reason)
    if order["status"] == "done":
        risk_manager.record_trade_result(strategy["id"], order["realized_pnl"], order["capital_after"])
        return {"action": "exited", "order_id": order["id"]}
    if order["status"] == "cancel":
        return {"action": "slippage_exceeded", "order_id": order["id"]}
    return {"action": "pending", "order_id": order["id"]}
```

`daemon.py`의 `_run_risk_exit_loop`는 `action == "exited"`일 때 daemon-core 스펙
결정7과 동일한 지점에서 `risk_manager.check_circuit_breaker()`를 호출한다 — "포지션이
청산될 때마다"라는 기반 스펙 문구를 트리거 경로와 무관하게 일관되게 지킨다.

### 결정 6 — `order_executor.exit()`에 `close_reason` 파라미터를 추가한다(감사 추적)

`exit()`는 지금 `close_reason="signal"`을 하드코딩해 `position_manager.close_position()`
에 넘긴다. ticker 트리거 청산과 캔들 신호 청산을 나중에 구분할 방법이 없으면 "왜
청산됐는지" 감사가 불가능해진다.

**결정:** `exit()`에 `close_reason: str = "signal"`(기본값 유지, 하위호환) 파라미터를
추가해 그대로 `close_position()`에 전달한다. `exit_for_risk()`는 결정2의
`matched_risk_exit_indicator()` 반환값(`"STOP_LOSS_PCT"`/`"TAKE_PROFIT_PCT"`)을 소문자로
바꿔 `reason`으로 넘긴다 — `positions.close_reason`에 `"stop_loss_pct"`/
`"take_profit_pct"`가 그대로 남아 `"signal"`과 구분된다. `db.close_position_row()`의
`close_reason` 컬럼은 순수 TEXT라 스키마 변경이 필요 없다.

### 결정 7 — 위험조건이 없는 전략은 WS 연결을 아예 열지 않는다, 그로 인한 20초 주기
no-op 태스크 재생성은 감내한다(트레이드오프 명시)

`_run_risk_exit_loop`는 시작하자마자 `has_risk_exit_conditions()`가 `False`면 WS 연결
없이 즉시 반환한다 — `STOP_LOSS_PCT`/`TAKE_PROFIT_PCT`를 아예 안 쓰는 전략까지 연결을
열 이유가 없다. 하지만 이 전략의 `status`는 여전히 `running`/`paused`이므로
`_task_set_manager_loop`의 다음 20초 스캔에서 `tasks[strategy_id].done() == True`를
보고 다시 `create_task()`한다 — 20초마다 코루틴을 만들고 즉시 버리는 무한 반복이 생긴다.

**결정:** 이 반복을 감내한다. 매번 하는 일은 `db.get_live_strategy()` 1회 + JSON
파싱 + 트리 순회뿐이라 실제 부하는 무시할 수준이고, 이걸 막으려면
`_task_set_manager_loop` 자체가 매 스캔마다 각 전략의 `sell_conditions_json`을 들여다
봐야 해서(현재는 `id`만 조회) 태스크셋 매니저의 책임이 커진다. 단순함을 우선한다(⑤-4b
결정8과 같은 원칙 — 과설계 방지).

### 결정 8 — 포지션은 매 트리거 직전에 다시 조회한다(⑤-4a 전제 재확인)

`_run_risk_exit_loop`는 lock을 잡은 뒤 `position_manager.get_open_position(strategy_id)`
를 다시 호출해 포지션이 그 사이 청산되지 않았는지 확인하고, 청산 직전에는
`db.get_live_strategy(strategy_id)`로 전략도 다시 읽는다 — ⑤-4a 최종리뷰가 daemon.py에
남긴 전제조건("항상 갓 읽은 dict를 넘길 것, 절대 옛 dict를 재사용하지 말 것")을 그대로
따른다. lock 획득까지 대기하는 동안(다른 태스크가 먼저 청산했을 수 있음) 포지션이 이미
없어졌으면 조용히 스킵한다.

## `trading/daemon.py` 변경분

```python
async def _run_risk_exit_loop(strategy_id: str, lock: asyncio.Lock) -> None:
    """전략 하나의 ticker 기반 실시간 손절/익절 전용 태스크(⑤-4c). sell_conditions_json에
    STOP_LOSS_PCT/TAKE_PROFIT_PCT가 없으면 WS 연결 없이 즉시 반환(결정7). 있으면 해당
    마켓의 ticker를 구독해(결정3) 매 tick마다 position_return_pct를 계산하고 독립
    안전망으로 평가(결정1) — 위반 시 lock을 잡고(결정4) exit_for_risk() 호출(결정5)."""


async def _task_set_manager_loop() -> None:
    """기존 로직(⑤-4b)에 더해: 전략당 asyncio.Lock을 만들어 _run_strategy_loop와
    _run_risk_exit_loop 양쪽에 넘기고(결정4), _run_risk_exit_loop도 같은 생명주기로
    create_task/cancel한다(결정3)."""


async def _run_strategy_loop(strategy_id: str, lock: asyncio.Lock) -> None:
    """⑤-4b와 동일한 구조. 신호처리 try 블록과 reconcile try 블록 각각을
    async with lock:으로 감싸는 것만 추가(결정4)."""
```

## 다른 모듈에 추가할 것

```python
# trading/signal_engine.py (결정2)
def has_risk_exit_conditions(sell_conditions: dict) -> bool: ...
def matched_risk_exit_indicator(sell_conditions: dict, position_return_pct: float) -> str | None: ...

# trading/order_executor.py
async def exit(..., close_reason: str = "signal") -> dict: ...  # 파라미터만 추가(결정6)
async def exit_for_risk(strategy, position, expected_price, reason, *, client=None, dry_run=False) -> dict: ...  # 신규(결정5)
```

## 에러 처리

- `_run_risk_exit_loop` 본문(포지션 조회~exit_for_risk 호출)의 예외는 `_run_strategy_loop`
  와 동일하게 `try/except Exception` + 로그만 남기고 다음 tick에 재시도한다 — 한 tick의
  실패가 WS 연결이나 태스크 전체를 죽이면 안 된다(⑤-4b 결정8과 동일 원칙).
- `stream_ticker()` 자체의 재연결/백오프는 `trading/upbit_ws.py`가 이미 처리한다(⑤
  Upbit 연동 서브플랜에서 구현·검증 완료) — `_run_risk_exit_loop`는 그 위에서 도는
  `async for`만 작성하면 된다. 재연결 사이의 tick 유실은 `upbit_ws.py` 자체 문서화된
  기존 트레이드오프(캔들 기반 신호는 이 스트림과 무관하므로 영향 없음)를 그대로
  상속한다.
- `task.cancel()`이 `_run_risk_exit_loop`에 전파되는 지점은 `async for`가 다음 WS
  메시지를 기다리는 `await`이므로 `_run_strategy_loop`처럼 별도 상태 자가진단 없이도
  20초 이내 정상 종료된다.

## 테스트 전략

- `signal_engine.has_risk_exit_conditions()`/`matched_risk_exit_indicator()`: 순수
  함수 — STOP_LOSS_PCT만 있는 트리, TAKE_PROFIT_PCT만 있는 트리, 둘 다 있는 트리, 다른
  지표와 AND/OR로 섞인 트리, 아예 없는 트리를 골든테스트로 검증. 특히 "RSI>70 AND
  STOP_LOSS_PCT<=-5"에서 RSI 조건과 무관하게 STOP_LOSS_PCT 단독으로 매치되는지(결정1의
  핵심 계약) 명시적으로 확인.
- `order_executor.exit()`: 기존 테스트에 `close_reason` 기본값(`"signal"`)이 그대로
  유지되는지 회귀 확인 + 새 값을 넘겼을 때 `close_position()`에 그대로 전달되는지 검증.
- `order_executor.exit_for_risk()`: `exit()`를 monkeypatch해 status별(done/cancel/
  wait) 반환 매핑 검증 + done일 때만 `record_trade_result()` 호출되는지 검증.
- `daemon._run_risk_exit_loop()`: `upbit_ws.stream_ticker`를 가짜 async generator로
  monkeypatch, `signal_engine.matched_risk_exit_indicator`/`order_executor.
  exit_for_risk`/`risk_manager.check_circuit_breaker`를 monkeypatch해 (1) 위험조건
  없는 전략은 WS를 아예 안 여는지(stream_ticker 호출 자체가 없어야 함), (2) 포지션
  없을 때 tick이 조용히 스킵되는지, (3) 트리거 시 lock을 잡고 exit_for_risk가 호출되는지,
  (4) action=="exited"일 때만 check_circuit_breaker가 불리는지 검증.
- `daemon._task_set_manager_loop()`: 기존 테스트(⑤-4b)에 더해, 전략당 lock이 생성돼
  `_run_strategy_loop`/`_run_risk_exit_loop` 양쪽에 동일 객체로 전달되는지, 전략이
  대상에서 빠지면 두 태스크 다 취소되는지 검증.
- 동시성 통합 확인: 가짜 lock(호출 순서를 기록하는 스텁)으로 `_run_strategy_loop`의
  신호처리 블록이 진행 중일 때 `_run_risk_exit_loop`의 exit_for_risk 호출이 lock 획득을
  기다리는지(실제 asyncio.Lock으로 두 코루틴을 asyncio.gather해 순서 검증) 확인 — 결정4의
  핵심 안전 계약이므로 mock만으로 끝내지 않고 진짜 Lock 객체로 레이스 재현 테스트 추가.
- 최종 통합 확인: `engine/` 미의존 확인(daemon.py AST 검사 스크립트 재사용, import 목록에
  `trading.signal_engine` 외 새 항목 없어야 함), 전체 회귀(`python -m pytest -q`).

## 자기 검토(스펙 완성도)

- 플레이스홀더 없음 — 8개 핵심 결정 각각 "왜"와 기각한 대안(공유 WS 연결, event/queue
  기반 시그널링, handle_signal_result 재사용)을 남겼다.
- **`engine/` 미의존 제약을 다시 확인하는 과정에서 발견한 문제**: 애초 구상은
  `collect_blocks`/`apply_operator`를 daemon.py가 직접 쓰는 것이었으나, daemon.py의
  자체 docstring 제약과 정면충돌해서 결정2로 재설계했다 — `trading/signal_engine.py`가
  이미 `engine.condition_tree`를 import하고 있다는 사실을 확인하고 그 경계를 그대로
  재사용했다.
- 결정3(전략당 개별 WS) ↔ 결정4(전략당 Lock)이 "전략 단위로 격리한다"는 하나의 일관된
  원칙으로 수렴함을 확인했다 — daemon-core 스펙의 "전략별 태스크 하나" 철학을 태스크
  두 개(신호처리+ticker)로 확장하면서도 유지했다.
- 스코프 경계: `HOLDING_PERIOD_BARS`는 캔들 경로 그대로, 승인/제어 UI는 ⑥ — 명확히
  넘겼다.
- 기존 모듈과의 인터페이스 일치 확인: `upbit_ws.stream_ticker`(이미 구현·검증 완료,
  markets 리스트 인자만 사용), `position_manager.get_open_position`/`db.
  get_live_strategy`(이미 구현된 시그니처 그대로), `risk_manager.record_trade_result`/
  `check_circuit_breaker`(⑤-4b가 이미 쓰는 것과 동일한 시그니처) — 전부 새로 바꾸는 게
  없다. `order_executor.exit()`의 `close_reason` 파라미터 추가만 기존 시그니처 확장(
  기본값 있어 하위호환).
