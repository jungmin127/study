# 라이브 트레이딩 서브플랜⑤-1 — DB CRUD + position_manager + risk_manager Design Spec

## 배경 및 목표

`docs/superpowers/specs/2026-08-04-live-trading-foundation-design.md`(이하 "기반 스펙")의
1단계 로드맵 중 "5. 트레이딩 엔진 코어"는 `signal_engine.py`/`order_executor.py`/
`position_manager.py`/`risk_manager.py`/`reconciler.py`/`daemon.py` 6개 모듈을 아우르는
가장 큰 서브플랜이다. 확인 결과 `trading/db.py`(서브플랜①)에는 7개 테이블 스키마와
`_connect()`만 있고 실제 읽기/쓰기 함수가 하나도 없다 — 이 스펙은 그 CRUD 레이어와, 외부
네트워크 I/O 없이 순수 DB/로직만으로 완결되는 두 모듈(`position_manager.py`/
`risk_manager.py`)을 다룬다. 서브플랜⑤ 전체를 하나로 묶기엔 너무 크다는 걸 사용자와
합의해 4단계로 쪼갰다(①DB+자금관리+리스크관리 ②신호평가 ③주문실행 ④reconciler+daemon
통합) — 이 문서는 그 첫 단계다.

## 범위

**이 스펙에서 확정하는 것:**
- `trading/db.py`에 추가할 CRUD 함수(현재 이 서브플랜이 실제로 쓰는 것만 — YAGNI. `signals`/
  `orders`/`manual_intervention_events` 테이블의 CRUD는 각각 신호평가·주문실행·
  reconciler 서브플랜에서 추가한다)
- `trading/position_manager.py`의 정확한 함수 시그니처와 동작
- `trading/risk_manager.py`의 정확한 함수 시그니처와 동작(서킷브레이커 + 일별 성과 집계)
- 두 모듈의 에러 처리, 테스트 전략

**이 스펙에서 다루지 않는 것(후속 서브플랜에서):**
- 실제 계좌 잔고 조회(업비트 API 호출) — 이 서브플랜의 함수들은 잔고를 파라미터로
  받는다(아래 "핵심 결정" 참고). 실제 조회는 승인 API(⑥ UX)나 daemon의 몫.
- `position_manager`/`risk_manager`를 실제로 호출해 엮는 로직(포지션 진입/청산 트리거,
  주문 체결 후 처리) — 그건 `order_executor.py`(⑤-3)의 몫. 이 서브플랜은 두 모듈을
  독립적으로 호출 가능한 형태로 완성하는 데까지만.
- 신호평가(`signal_engine.py`, ⑤-2), 주문실행(`order_executor.py`, ⑤-3), 수동개입 감지 +
  daemon 메인루프(⑤-4).

## 핵심 결정

### 결정 1 — `position_manager.py`는 계좌 잔고를 직접 조회하지 않는 순수 함수로 유지한다

`calculate_initial_capital()`이 `position_sizing_mode='percent'`일 때 계좌 잔고가 필요하지만,
이 함수가 직접 `trading.upbit_client.get_accounts()`를 호출하게 만들면 이 모듈이 네트워크
의존을 갖게 돼 단위테스트가 mock 없이는 불가능해진다. 대신 `available_balance: float`을
**호출자가 이미 조회한 값**으로 파라미터로 받는다 — 실제 조회(및 스펙 결정7의 "이미 running
중인 다른 전략들의 current_capital 합 + 신규 전략 최초 자금 ≤ 실제 가용 KRW 잔고" 검증)는
승인 API(⑥)의 몫이다. 이렇게 하면 `position_manager.py`는 `trading/db.py` 외에 아무것도
import하지 않는 순수 모듈로 남는다.

### 결정 2 — DB CRUD는 `trading/db.py`에 둔다(스키마와 분리하지 않음)

기반 스펙의 모듈구조 절이 `trading/db.py`를 "trading.db 스키마 정의/접근(engine/cache.py와
같은 패턴)"이라고 이미 명시했다 — `engine/cache.py`가 스키마와 CRUD를 한 파일에 같이 두는
패턴을 그대로 따른다. `position_manager.py`/`risk_manager.py`는 비즈니스 로직(자금 계산,
서킷브레이커 판정)만 담당하고, 실제 SQL은 `trading/db.py`의 CRUD 함수를 호출해서 수행한다.
`daily_performance`의 "누적" 로직(거래 건수·승패 카운트·realized_pnl 합산)은 산술이므로
`risk_manager.py`가 계산하고, `trading/db.py`의 upsert 함수는 최종값을 그대로 저장하는
기계적인 역할만 한다(db.py를 "똑똑하게" 만들지 않는다).

### 결정 3 — `record_trade_result()`와 `check_circuit_breaker()`를 분리한다

포지션이 청산될 때마다 (1) 일별 성과를 기록하고 (2) 한도 초과 여부를 판정하는 두 동작이
필요하지만, 이 둘을 하나의 함수로 합치지 않는다 — "기록"은 항상 일어나야 하고 "판정"은
그 기록을 바탕으로 별도로 호출돼야 자연스럽다(호출자인 `order_executor.py`가 이 둘을
순서대로 호출하는 조합을 스스로 결정하게 둔다). `check_circuit_breaker()`가 실제로 트립을
판정하면 `circuit_breaker_state.tripped=1`과 `live_strategies.status='paused'`를 **같은
함수 안에서 원자적으로** 기록한다(기반 스펙이 "판정 및 반응"을 하나로 묶어 설명하므로).

### 결정 4 — 연속손실(consecutive_losses)은 "이번 거래가 손실이면 +1, 이익이면 0으로 리셋"

기반 스펙에 연속손실의 정확한 정의가 없어 이 스펙에서 확정한다: `realized_pnl < 0`이면
연속손실 카운트 +1, `realized_pnl >= 0`(익절 또는 손익분기)이면 0으로 리셋. 트레이딩
데이트(KST 'YYYY-MM-DD')가 바뀌면 연속손실과 트립 상태 둘 다 리셋된다(기반 스펙 명시).

## `trading/db.py`에 추가할 CRUD 함수

```python
def get_live_strategy(live_strategy_id: str) -> dict | None
def update_live_strategy_status(live_strategy_id: str, status: str) -> None
def update_live_strategy_capital(live_strategy_id: str, current_capital: float) -> None
def update_live_strategy_last_candle(live_strategy_id: str, candle_time: str) -> None

def insert_position(live_strategy_id: str, market: str, entry_price: float, entry_qty: float) -> str
def close_position_row(
    position_id: str, exit_price: float, exit_qty: float,
    realized_pnl: float, realized_pnl_pct: float, close_reason: str,
) -> None
def get_open_position(live_strategy_id: str) -> dict | None

def get_circuit_breaker_state(live_strategy_id: str) -> dict | None
def upsert_circuit_breaker_state(
    live_strategy_id: str, trading_date: str, consecutive_losses: int, tripped: int,
    tripped_reason: str | None = None, tripped_at: str | None = None, resumed_at: str | None = None,
) -> None

def get_daily_performance(live_strategy_id: str, trading_date: str) -> dict | None
def upsert_daily_performance(
    live_strategy_id: str, trading_date: str, realized_pnl: float, realized_pnl_pct: float,
    trade_count: int, win_count: int, loss_count: int,
    starting_balance: float, ending_balance: float,
) -> None
```

모든 함수는 내부에서 `trading.db._connect()`로 커넥션을 열고 닫는다(요청마다 새 커넥션,
기존 `engine/cache.py` 관례와 동일). `close_position_row`/`update_live_strategy_capital`처럼
같은 트랜잭션 안에서 여러 테이블을 갱신해야 하는 경우는 없다(포지션 청산 시
`live_strategies.current_capital` 갱신은 `position_manager.close_position()`이 두 CRUD
함수를 순서대로 호출하는 것으로 충분 — 둘 다 이 함수 하나의 흐름 안에서만 쓰이고 동시
접근 경합이 없으므로 별도 트랜잭션 묶음이 필요 없다).

## `trading/position_manager.py`

```python
def calculate_initial_capital(risk_config: dict, available_balance: float) -> float:
    """position_sizing_mode('fixed'|'percent')에 따라 최초 진입 자금을 계산하고,
    max_position_per_market 상한으로 클램프한다(결정7 — 승인 시 1회만 호출)."""

def open_position(live_strategy_id: str, market: str, entry_price: float, entry_qty: float) -> str:
    """positions 행 생성(status='open'), position_id 반환."""

def close_position(
    position_id: str, exit_price: float, exit_qty: float, fee: float, close_reason: str,
) -> dict:
    """포지션 청산. realized_pnl = exit_price*exit_qty - entry_price*entry_qty - fee,
    realized_pnl_pct = realized_pnl / (entry_price*entry_qty) * 100 계산 후 positions 행
    갱신 + live_strategies.current_capital을 (exit_price*exit_qty - fee)로 갱신(복리,
    결정7 — 수수료 차감 후 실현금액이 그대로 다음 진입 자금). 반환값
    {"realized_pnl": float, "realized_pnl_pct": float, "capital_after": float}는 호출자가
    risk_manager.record_trade_result()에 그대로 넘길 수 있는 형태."""

def get_open_position(live_strategy_id: str) -> dict | None:
    """전략의 현재 오픈 포지션(있으면), positions.status='open' 행."""
```

## `trading/risk_manager.py`

```python
def today_kst() -> str:
    """KST(UTC+9) 기준 오늘 날짜, 'YYYY-MM-DD'."""

def record_trade_result(
    live_strategy_id: str, realized_pnl: float, realized_pnl_pct: float, capital_after: float,
) -> None:
    """포지션 청산마다 호출. daily_performance를 오늘 날짜 기준으로 upsert(오늘 첫 거래면
    starting_balance = capital_after - realized_pnl로 역산해 새 행 생성, 이후 거래는 기존
    행에 realized_pnl 누적·trade_count/win_count/loss_count 증가·ending_balance 갱신).
    circuit_breaker_state.consecutive_losses를 결정4대로 갱신(트레이딩 데이트가 바뀌었으면
    먼저 0으로 리셋 후 처리)."""

def check_circuit_breaker(live_strategy_id: str, risk_config: dict) -> bool:
    """오늘의 daily_performance.realized_pnl_pct와 circuit_breaker_state.consecutive_losses를
    risk_config['daily_loss_limit_pct']/risk_config['consecutive_loss_limit']와 비교.
    이미 tripped=1이면 즉시 True. 새로 한도를 넘었으면 circuit_breaker_state.tripped=1 +
    tripped_reason('daily_loss_limit'|'consecutive_loss_limit') + tripped_at 기록 +
    live_strategies.status='paused'로 갱신 후 True. 한도 이내면 False."""
```

## 에러 처리

- `close_position`/`record_trade_result`를 존재하지 않는 `position_id`/`live_strategy_id`로
  호출하면 `ValueError`(방어적 — 정상 흐름에서는 항상 존재하는 id로만 호출되지만, 버그로
  잘못된 id가 들어오면 조용히 아무 일도 안 하는 대신 즉시 실패해야 한다).
- `calculate_initial_capital`이 지원하지 않는 `position_sizing_mode` 값을 받으면 `ValueError`.
- DB 관련 예외(락 타임아웃 등)는 그대로 전파한다 — 이 서브플랜에서 별도 재시도 로직을
  추가하지 않는다(그 정책은 daemon의 장애복구 절, ⑤-4에서 결정).

## 테스트 전략

`tests/test_trading_db.py`(서브플랜①에서 이미 존재, 스키마 테스트)와 같은 패턴 —
`monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "trading.db")`로 임시 SQLite 파일에
실제로 쓰고 읽어서 검증한다. 네트워크 mock이 전혀 필요 없다(이 서브플랜 전체가 순수
DB/로직이므로). `position_manager`/`risk_manager` 테스트는 먼저 `insert_position`/
`get_live_strategy` 등으로 전제 상태를 만든 뒤 대상 함수를 호출하고 DB를 직접 조회해
결과를 검증하는 통합테스트 스타일(단위 mock보다 실제 SQLite 왕복이 이 레이어의 정확성을
더 잘 보증한다 — `engine/cache.py` 테스트 관례와 동일).

## 자기 검토(스펙 완성도)

- 플레이스홀더 없음 — 모든 함수의 정확한 시그니처와 동작을 명시했다.
- 4개 핵심 결정이 서로 상충하지 않는지 확인: 결정1(순수함수, 잔고 파라미터로 받음) ↔
  결정2(CRUD는 db.py) ↔ 함수 시그니처 절이 셋 다 일관됨 — `position_manager.py`/
  `risk_manager.py` 어디에도 `upbit_client`/`httpx` import가 없다.
- 스코프가 크다는 걸 인지하고 서브플랜⑤ 전체를 4단계로 쪼갠 이유와 각 단계의 경계를
  명시했다 — "다루지 않는 것" 절에서 후속 서브플랜(신호평가/주문실행/reconciler+daemon)의
  경계를 명확히 남겼다.
- 결정4(연속손실 정의)처럼 기반 스펙에 없던 세부사항은 이 스펙에서 새로 확정하고 근거를
  남겼다 — 나중에 "왜 이렇게 정의했지"라는 혼란이 없도록.
