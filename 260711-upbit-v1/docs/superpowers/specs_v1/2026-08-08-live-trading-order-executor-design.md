# 라이브 트레이딩 서브플랜⑤-3 — order_executor.py Design Spec

## 배경 및 목표

`docs/superpowers/specs_v1/2026-08-04-live-trading-foundation-design.md`(이하 "기반 스펙")의
1단계 로드맵 "5. 트레이딩 엔진 코어"를 4단계로 쪼갠 것(①DB+자금관리+리스크관리 ②신호평가
③주문실행 ④reconciler+daemon 통합) 중 세 번째다. 서브플랜⑤-2(`signal_engine.py`)가 매수/
매도 신호(True/False/판단불가)를 계산·기록하는 데까지 끝냈고
([[upbit-v1-live-trading-foundation]]), 이 스펙은 그 신호를 받아 실제로 업비트에 주문을
내는 `order_executor.py`를 설계한다 — 서브플랜⑤-1의 `position_manager`/`risk_manager`,
서브플랜④의 `upbit_client`(async REST)를 엮는다.

## 범위

**이 스펙에서 확정하는 것:**
- `trading/order_executor.py`의 `enter()`/`exit()`(시장가/지정가/지정가+타임아웃/
  **슬리피지상한(market_capped) 4모드**) + `handle_signal_result()`(신호평가 결과를 받아
  실제 주문 여부까지 결정하는 단일 진입점)
- 틱사이즈(호가단위) 라운딩 방식
- `risk_config_json`에 `order_execution_mode='market_capped'` 값과 `max_slippage_pct`
  필드 추가(foundation 스펙의 `risk_config_json` 스키마 확장 — 결정9)
- `trading/db.py`에 추가할 `orders` CRUD + `signals.resulting_order_id` 갱신 함수
- `trading/signal_engine.py`의 `evaluate_signals()` 반환값에 추가할 필드(이미 완료된
  ⑤-2 코드에 대한 최소 수정)
- 주문 실패 시 재시도/이중주문 방지 정책, `dry_run` 동작

**이 스펙에서 다루지 않는 것(후속 서브플랜에서):**
- `limit` 모드로 낸 뒤 방치된 미체결 주문의 장기 감시, 재시작 시 State Hydration, 외부
  수동주문 감지(Reconciler), daemon 메인루프 — 전부 ⑤-4.
- 손절/익절(`STOP_LOSS_PCT`/`TAKE_PROFIT_PCT`)의 실시간 ticker 기반 평가 — ⑤-4(기반
  스펙: "캔들 마감을 기다리지 않는다").
- `max_total_position`(계좌 전체 상한) 검증 — ⑤-1 스펙이 명시한 대로 전략 승인 시점(아직
  구현 안 됨)의 몫. 결정 6(전략 1개 = 코인 1개 = 단일 포지션)에서는 전략별 상한
  (`max_position_per_market`)만으로 이 서브플랜의 책임이 끝난다.
- 승인 API, "라이브 전략 관리" 프론트엔드 — 별도 UX 서브플랜.

## 핵심 결정

### 결정 1 — 틱사이즈 라운딩은 하드코딩 테이블을 쓴다 (기반 스펙 가정 정정)

기반 스펙과 ④(upbit_client) 플랜 문서는 "틱사이즈는 `orders/chance` API 결과로 계산한다"고
가정했으나, 이 스펙 작성 중 업비트 공식 API 문서를 재확인한 결과 `orders/chance` 응답의
`market.bid/ask.price_unit` 필드는 **deprecated**이며 참조하지 않는 게 권장된다는 걸
확인했다. 실제 원화마켓 틱사이즈는 가격 구간별 고정 테이블로 정해져 있다(2,000,000원
이상=1,000원, 1,000,000~2,000,000=1,000원, 500,000~1,000,000=500원, 100,000~500,000=100원,
50,000~100,000=50원, 10,000~50,000=10원, 5,000~10,000=5원, 1,000~5,000=1원, 100~1,000=1원,
10~100=0.1원, 1~10=0.01원, 0.1~1=0.001원, 이하 소수점 자리마다 10배씩 세분화, 출처:
docs.upbit.com 원화마켓 주문 가격 단위 문서).

**결정:** `order_executor.py` 안에 이 구간 테이블을 상수로 하드코딩하고
`round_to_tick(price: float) -> float`로 노출한다. API 의존이 없어 결정론적이고
테스트하기 쉽다. **단점(문서화):** 업비트가 이 표를 바꾸면(과거 2023/2024년에 실제로
변경한 이력이 있음) 코드도 수동으로 갱신해야 한다 — 이 스펙과 구현 코드 양쪽에 출처
링크를 남겨 향후 재확인이 쉽게 한다.

### 결정 2 — `order_executor.py`는 async, 내부에서 sync 모듈을 직접 호출한다

`trading.upbit_client`는 전부 `async def`(httpx.AsyncClient 기반)인 반면
`trading.signal_engine`/`trading.position_manager`/`trading.risk_manager`는 전부 sync다.
`order_executor.py`의 공개 함수(`enter`/`exit`/`handle_signal_result`)는 `async def`로
선언해 `upbit_client`를 자연스럽게 `await`하고, 그 안에서 `position_manager`/
`risk_manager`(빠른 SQLite 호출)는 동기 함수로 그냥 직접 호출한다. 향후 ⑤-4의
`daemon.py`가 어차피 asyncio 기반(전략별 asyncio 태스크, 기반 스펙 결정3)이라 이 경계가
자연스럽다. `limit_timeout` 모드의 N초 대기도 `await asyncio.sleep()`으로 같은 함수
안에서 처리할 수 있다(결정4 참고).

### 결정 3 — `handle_signal_result()`가 "신호 → 실제 주문" 연결 로직까지 담당한다

기반 스펙의 메인 루프 의사코드와 이 플랜 자체의 "다음 서브플랜" 절이 이미 이 방향을
가리키고 있었다(⑤-3이 "evaluate_signals() 결과와 position_manager/upbit_client를 엮는다"고
명시). `order_executor.py`에 다음 흐름을 가진 `handle_signal_result(strategy_id,
signal_result) -> dict`를 둔다:

```
if signal_result["paused"]: 아무것도 하지 않는다 (⑤-2가 이미 status='paused' 처리함)
if signal_result["buy_signal"] is True and 포지션 없음:
    if risk_manager.is_circuit_tripped_today(strategy_id):
        db.update_signal_result(signal_result["buy_signal_id"], None, "circuit_breaker_tripped")
    else:
        capital = min(strategy["current_capital"], risk_config["max_position_per_market"])  # 결정6
        order = await enter(strategy, capital, expected_price=signal_result["latest_close"])
        db.update_signal_result(signal_result["buy_signal_id"], order["id"], None)
if signal_result["sell_signal"] is True and 포지션 있음:
    order = await exit(strategy, position, expected_price=signal_result["latest_close"])
    db.update_signal_result(signal_result["sell_signal_id"], order["id"], None)
    risk_manager.record_trade_result(strategy_id, order["realized_pnl"], order["capital_after"])
```

매도(청산)는 서킷브레이커로 막지 않는다 — 손실 중이어도 청산은 항상 허용해야 자금이
묶이지 않는다(서킷브레이커는 "새 진입"만 막는 안전장치라는 게 기반 스펙의 취지).

이 함수가 ⑤-4 `daemon.py`가 매 캔들마다 호출할 유일한 진입점이 된다:
`result = signal_engine.evaluate_signals(strategy_id); await
order_executor.handle_signal_result(strategy_id, result)`.

**`max_position_reached` skip_reason은 이 서브플랜에서 쓰지 않는다:** 기반 스펙의 `orders`
테이블 주석에 `skip_reason` 예시로 `max_position_reached`가 언급돼 있지만,
`max_position_per_market`은 이 스펙에서 "진입 차단"이 아니라 "진입 금액 클램프"로
구현한다(결정6) — 상한을 넘는 금액이 있어도 상한만큼만 진입하지, 아예 스킵하지 않는다.
따라서 이 서브플랜이 실제로 쓰는 `skip_reason` 값은 `circuit_breaker_tripped`와
`slippage_exceeded`(결정9, `market_capped` 모드가 FOK로 전량취소됐을 때) 둘뿐이다
(`unknown`은 이미 ⑤-2가 씀).

### 결정 4 — `limit_timeout`의 N초 타이머는 `enter()`/`exit()` 내부에서 블로킹 처리한다

```python
async def enter(..., order_timeout_sec=10):
    order = await upbit_client.create_order(..., ord_type="limit", ...)
    await asyncio.sleep(order_timeout_sec)
    status = await upbit_client.get_order(uuid=order["uuid"])
    if status["state"] != "done":
        await upbit_client.cancel_order(uuid=order["uuid"])
        remaining = requested_volume - float(status["executed_volume"])
        market_order = await upbit_client.create_order(..., ord_type="market", volume=remaining)
        # replaces_order_id로 연결, 두 체결의 가중평균가 재계산
```

daemon이 어차피 전략별 asyncio 태스크로 동작하므로, 한 전략이 이 10초 남짓을 기다려도
다른 전략의 캔들 처리는 막히지 않는다. 이 함수가 반환할 때는 이미 최종 상태(체결완료 또는
실패)까지 확정돼 있으므로, ⑤-4의 "주문상태 감시" 백그라운드 루프는 **이 서브플랜이 낸
주문을 다시 다룰 필요가 없다** — 그 루프의 역할은 재시작 후 남아있는 `status='wait'` 행
복구(State Hydration)와 `limit` 모드(타임아웃 없음)로 방치된 주문 감시로 좁혀진다.

### 결정 5 — 주문 실패 시 재시도는 우리 쪽 `orders.id`를 `identifier`로 활용해 재조회 후에만 한다

`upbit_client.create_order()`는 이미 `identifier` 파라미터를 지원한다. `order_executor`는
DB에 `orders` 행을 먼저 생성해 `id`(uuid4)를 얻은 뒤, 그 값을 `identifier`로 실어
`create_order()`를 호출한다. 네트워크 에러/타임아웃으로 응답을 못 받으면(`upbit_uuid`도
모름) 바로 재시도하지 않고 `upbit_client.get_order(identifier=order_id)`로 재조회해 실제로
주문이 들어갔는지 확인한 뒤에만 1회 재시도한다(이중주문 방지, 기반 스펙 "주문 정확성"
절 요구사항). 업비트가 동일 `identifier` 중복을 거부해주므로 이중주문 자체도 2차 방어선이
된다. 재조회마저 실패하면 예외를 그대로 전파한다(추가 재시도 루프를 만들지 않음 — ⑤-1의
"DB 예외는 그대로 전파, 정책은 daemon 몫" 관례와 동일. `UpbitRateLimitError`는 이미
`upbit_client` 내부에서 3회 재시도된 뒤에도 실패한 것이므로 더 재시도하지 않는다).

### 결정 6 — `max_position_per_market`은 진입마다(최초+복리 이후 전부) 클램프한다

⑤-1의 `position_manager.calculate_initial_capital()`은 **최초 진입 1회**에만
`max_position_per_market` 클램프를 적용한다(승인 시점). 하지만 기반 스펙 결정7이 명시한
"복리로 자금이 과도하게 불어나는 걸 막는 안전 상한"이라는 취지는 2차 이후 진입에도
동일하게 적용돼야 한다. `handle_signal_result()`가 매 진입마다
`capital = min(strategy["current_capital"], risk_config["max_position_per_market"])`로
직접 클램프한다(`position_manager`를 수정하지 않음 — 그 함수는 "최초 1회"라는 이름 그대로
유지하고, 반복 클램프는 호출자인 이 서브플랜의 책임으로 둔다).

### 결정 7 — 업비트 시장가 주문의 `ord_type` 비대칭을 그대로 반영한다

업비트 API는 시장가 매수/매도를 서로 다른 `ord_type`으로 구분한다(방금 공식 문서로
재확인): **시장가 매수**는 `ord_type="price"` + `price`(KRW 금액, `volume` 생략),
**시장가 매도**는 `ord_type="market"` + `volume`(수량, `price` 생략). 지정가는 매수/매도
둘 다 `ord_type="limit"` + `price`+`volume`. `enter()`(항상 매수)와 `exit()`(항상 매도)가
서로 다른 시장가 파라미터 조합을 쓰는 이유가 이것이다 — 실수로 뒤바뀌면 업비트가 400을
반환하므로, 이 비대칭을 스펙에 명시해 구현 실수를 예방한다.

### 결정 8 — `dry_run=True`는 요청가 즉시 전량체결·`fee=0`으로 단순화한다

`upbit_client` 호출을 전부 건너뛰고, 그 자리에서 `filled_price=requested_price`,
`filled_volume=requested_volume`, `fee=0`, `status='done'`으로 `orders` 행을 즉시 확정한다
(`slippage_pct`는 자연히 0). 목적은 "네트워크만 없는 happy path"를 빠르게 검증하는 것 —
`limit_timeout`의 취소/재주문/평균가 재계산 같은 다단계 로직은 dry_run으로 커버하지 않고
`upbit_client`를 `monkeypatch`로 mock한 별도 테스트로 검증한다(테스트 전략 절 참고).
사용자 화면에는 노출하지 않는다(기반 스펙 결정5, 유닛/통합테스트 전용).

### 결정 9 — 4번째 주문모드 `market_capped`: 슬리피지 상한 + FOK로 "보호된 시장가"를 구현한다

사용자 요구사항(브레인스토밍 중 추가): threshold가 걸려 매수/매도가 확정된 순간, 약간의
슬리피지는 감수하더라도 전량 체결되길 원하되, 그 갭이 너무 크면 아예 체결되지 않고
취소되길 원한다. 업비트 API를 재확인한 결과 두 후보가 있었다:

- `ord_type="best"`(최유리지정가): 그 순간의 최우선호가에 체결하지만, **사용자가 직접
  "얼마까지 벌어지면 취소"라는 상한을 지정할 방법이 없다** — 시장이 크게 갭이 나 있어도
  그 벌어진 최우선호가에 그냥 체결된다. 요구사항의 "너무 심하면 취소"를 만족 못 함.
- `ord_type="limit"` + `time_in_force="fok"`(지정가 + Fill-Or-Kill): 가격을
  `expected_price × (1 ± max_slippage_pct/100)`으로 지정하면, 그 가격 범위 안에서 **전량
  체결 가능하면 즉시 전량 체결, 아니면 전량 취소**(부분체결 없음) — 요구사항을 정확히
  만족한다.

**결정:** `order_execution_mode`에 4번째 값 `market_capped`를 추가한다(기존 market/limit/
limit_timeout은 그대로 유지, 기본값은 여전히 `limit_timeout` — 이 스펙에서 기본값을
바꾸지 않는다). 구현:

```python
# 매수: 상한 = expected_price * (1 + max_slippage_pct / 100)
# 매도: 하한 = expected_price * (1 - max_slippage_pct / 100)
capped_price = round_to_tick(expected_price * (1 + sign * max_slippage_pct / 100))
order = await upbit_client.create_order(
    market, side, "limit", price=str(capped_price), volume=str(volume),
    time_in_force="fok", identifier=order_id,
)
```

`max_slippage_pct`는 `risk_config_json`에 새로 추가하는 필드다(예: `0.5` = 0.5%) — 전략별로
사용자가 직접 설정한다(코인마다 변동성이 달라 고정값으로는 부적합).

**FOK가 전량취소됐을 때:** 이번 캔들에서는 그냥 미체결로 끝낸다(재시도나 순수시장가
전환을 하지 않는다) — 사용자가 명시한 의도("gap이 너무 심하면 주문을 취소") 그대로.
`orders` 행은 `status='cancel'`로 남기고(감사 추적용), `handle_signal_result()`가
`db.update_signal_result(signal_id, order["id"], "slippage_exceeded")`로 **주문id와
skip_reason을 함께** 기록한다 — "시도는 했지만 슬리피지 초과로 취소됐다"는 사실이
`resulting_order_id`를 따라가면 감사할 수 있다(완전히 스킵된 신호와 구분됨). 포지션은
생성되지 않으므로 다음 캔들에서 조건이 다시 충족되면 자연스럽게 재시도된다.

`market_capped`는 매수/매도 둘 다 `ord_type="limit"`로 대칭이다 — 결정7의 `price`/`market`
비대칭은 순수 시장가(`market`) 모드에만 해당하고, 이 모드에는 적용되지 않는다.

## `trading/order_executor.py`

```python
_TICK_TABLE: list[tuple[float, float]]  # (구간 하한, 그 구간의 tick size), 오름차순

def round_to_tick(price: float) -> float:
    """가격을 결정1의 구간별 테이블에 맞는 tick으로 반올림."""

def _floor_volume(volume: float) -> float:
    """수량을 소수 8자리로 내림(업비트 최대 정밀도, 초과주문 방지를 위해 항상 내림)."""

async def enter(
    strategy: dict, capital: float, expected_price: float,
    *, client: httpx.AsyncClient | None = None, dry_run: bool = False,
) -> dict:
    """매수 주문 실행(market/limit/limit_timeout/market_capped 4모드,
    risk_config['order_execution_mode']에 따라 분기, 결정9). orders.status가 'done'일
    때만 positions 행을 생성한다(position_manager.open_position) — market_capped가 FOK로
    전량취소되면 'cancel', plain `limit`은 주문 직후 'wait'로 즉시 반환되므로(위 결정4
    각주) 둘 다 positions를 건드리지 않는다. 이미 오픈 포지션이 있으면 ValueError(방어적
    가드)."""

async def exit(
    strategy: dict, position: dict, expected_price: float,
    *, client: httpx.AsyncClient | None = None, dry_run: bool = False,
) -> dict:
    """매도 주문 실행(position['entry_qty'] 전량, all-in/all-out, 4모드는 enter()와 동일).
    orders.status가 'done'일 때만 position_manager.close_position()을 호출한다 —
    market_capped의 'cancel'과 plain `limit`의 'wait'는 둘 다 포지션을 그대로 유지한다
    (enter()와 동일한 규칙). 'done'이면 반환 dict에 close_position()의
    {"realized_pnl":, "realized_pnl_pct":, "capital_after":}를 병합. position이 None이면
    ValueError."""

async def handle_signal_result(
    strategy_id: str, signal_result: dict, *, dry_run: bool = False,
) -> dict:
    """evaluate_signals() 반환값을 받아 서킷브레이커 확인 → enter()/exit() 호출 →
    signals.resulting_order_id/skip_reason 갱신까지 한 번에 처리(결정3). market_capped
    모드가 FOK로 전량취소되면 buy_action/sell_action이 "slippage_exceeded"가 되고
    skip_reason도 같은 값으로 기록된다(결정9). **plain `limit` 모드(타임아웃 없음)는
    주문 직후 `status='wait'`로 즉시 반환되므로(결정4 각주 — 이 모드만 "다루지 않는 것"
    절에 명시된 대로 장기 미체결 방치가 사용자의 명시적 선택)**, 이 경우
    `positions`/`risk_manager`는 건드리지 않고 buy_action/sell_action이 `"pending"`이
    된다(주문은 냈지만 체결 확인은 ⑤-4 몫 — 스킵은 아니므로 skip_reason은 남기지 않고
    `resulting_order_id`만 채운다). 반환:
    {"buy_action": "entered"|"skipped_circuit_breaker"|"slippage_exceeded"|"pending"|None,
     "sell_action": "exited"|"slippage_exceeded"|"pending"|None,
     "buy_order_id": str|None, "sell_order_id": str|None}."""
```

## `trading/db.py` 추가 함수

```python
def insert_order(
    live_strategy_id: str, position_id: str | None, market: str, side: str, order_type: str,
    requested_price: float | None, requested_volume: float | None, expected_price: float | None,
    *, replaces_order_id: str | None = None,
) -> str:
    """orders 행 생성(status='wait'), id(주문 API의 identifier로 그대로 재사용) 반환."""

def update_order_filled(
    order_id: str, upbit_uuid: str | None, filled_price: float | None,
    filled_volume: float | None, fee: float | None, slippage_pct: float | None, status: str,
) -> None:
    """체결/취소/실패 결과 반영(updated_at 자동 갱신)."""

def get_order_by_id(order_id: str) -> dict | None

def update_signal_result(
    signal_id: str, resulting_order_id: str | None, skip_reason: str | None,
) -> None:
    """signals.resulting_order_id/skip_reason 갱신(⑤-2가 이미 채운 skip_reason='unknown'을
    이 함수가 덮어쓰는 경우는 없다 — paused=True인 캔들은 handle_signal_result가 아예
    건드리지 않으므로, resulting_order_id 갱신 대상은 항상 skip_reason이 비어있던 신호)."""
```

## `trading/signal_engine.py` 수정 — `evaluate_signals()` 반환값 확장

이미 완료된 ⑤-2 코드에 대한 최소 수정. `db.insert_signal()`이 반환하는 id를 지역변수에
담아두고, 반환 dict에 3개 키를 추가한다:

```python
buy_signal_id = db.insert_signal(...)   # 기존 호출, 반환값만 이제 사용
sell_signal_id = db.insert_signal(...)
...
return {
    "new_candle": True,
    "candle_time": candle_time_str,
    "buy_signal": buy_result,
    "sell_signal": sell_result,
    "buy_signal_id": buy_signal_id,      # 신규
    "sell_signal_id": sell_signal_id,    # 신규
    "latest_close": float(latest_close), # 신규 — expected_price로 사용
    "paused": paused,
    "resumed": resumed,
}
```

기존 `tests/test_signal_engine.py`의 11개 테스트는 전부 특정 키만 인덱싱해 검증하므로
(`result["new_candle"] is False` 등) 이 추가로 깨지지 않는다 — 회귀 확인은 최종 통합
테스트 단계에서 명시적으로 재확인한다.

## 에러 처리

- `enter()`를 이미 오픈 포지션이 있는 채로, 또는 `exit()`를 오픈 포지션 없이 호출하면
  `ValueError`(방어적 가드 — 정상 흐름에서는 `handle_signal_result()`가 미리 걸러준다).
- `upbit_client` 호출 실패 시 재시도 정책은 결정5(identifier 재조회 후 1회만).
- `limit_timeout`의 부분체결: `get_order()` 응답의 `executed_volume`/`remaining_volume`
  필드로 잔량을 계산하고, 시장가 재주문 체결분과 가중평균가를 재계산해 `filled_price`에
  기록한다.
- 지원하지 않는 `risk_config['order_execution_mode']` 값이면 `ValueError`.

## 테스트 전략

⑤-1/⑤-2와 같은 결의 패턴을 유지하되, 이 서브플랜은 네트워크(`upbit_client`)가 처음으로
끼어드는 계층이라 mock이 필수다.

- `trading/db.py`의 `orders` CRUD: 기존 `test_trading_db.py` 관례대로 `tmp_path` SQLite에
  실제로 쓰고 읽어 검증.
- `round_to_tick()`: 각 가격구간 경계값(예: 999원/1,000원, 1,999,999원/2,000,000원)을
  골든테스트로 검증.
- `enter()`/`exit()`의 `market`/`limit` 모드: `trading.upbit_client.create_order`/
  `get_order`/`cancel_order`를 `monkeypatch`로 mock(async 함수이므로 `AsyncMock`류 사용).
- `limit_timeout`: "타임아웃 → 취소 → 잔량 시장가 재주문 → 평균가 재계산" 흐름을 mock 호출
  순서/인자로 검증(`asyncio.sleep`도 monkeypatch해 테스트가 실제로 10초 기다리지 않게 함).
- `market_capped`(결정9): 두 케이스 모두 검증 — (1) FOK 성공 시 `price`/`time_in_force`
  파라미터가 슬리피지 상한대로 정확히 계산됐는지, (2) FOK가 `cancel` 상태를 반환했을 때
  `positions`/`current_capital`이 전혀 바뀌지 않고 `orders.status='cancel'`만 남는지.
- `dry_run=True` 경로: mock 없이 happy path(즉시 전량체결)만 검증.
- `handle_signal_result()`: `enter`/`exit`을 monkeypatch해 "서킷브레이커 트립 시 skip",
  "매수신호 시 enter 호출 + signals 갱신", "매도신호 시 exit + record_trade_result 호출",
  "paused=True면 아무것도 안 함", "market_capped 취소 시 skip_reason='slippage_exceeded'로
  기록(포지션 미생성)" 등을 검증(⑤-2의 `insert_live_strategy`/`make_oscillating_df` fixture
  재사용).
- 최종 통합 확인 단계에서 `evaluate_signals()` 반환값 확장이 기존 11개 테스트를 깨지
  않는지 전체 회귀(`python -m pytest -q`)로 재확인.

## 자기 검토(스펙 완성도)

- 플레이스홀더 없음 — 모든 함수의 정확한 시그니처와 동작, 그리고 9개 핵심 결정 각각의
  "왜"를 남겼다.
- 기반 스펙과의 불일치를 발견 즉시 정정하고 근거를 남겼다(결정1의 `price_unit` deprecated
  이슈, 결정3의 `max_position_reached` skip_reason 미사용 사유, 결정9의 4번째 주문모드로
  `risk_config_json` 스키마 확장) — 나중에 "왜 스펙이랑 다르지"라는 혼란을 예방.
- 9개 결정이 서로 상충하지 않는지 확인: 결정2(async 경계) ↔ 결정4(`limit_timeout` 블로킹)
  ↔ 결정5(identifier 재조회) ↔ 결정9(`market_capped`도 같은 async 함수 안에서 FOK 결과까지
  끝까지 처리) — 넷 다 "`enter()`/`exit()` 안에서 async로 끝까지 처리"라는 하나의 실행
  모델로 일관됨. 결정3(연결 로직 포함) ↔ 결정6(진입마다 클램프) ↔ 결정9(FOK 취소도
  `handle_signal_result()`가 skip_reason으로 기록)도 `handle_signal_result()`가 "신호 →
  주문 여부"의 유일한 소유자라는 점에서 일관됨.
- 스코프 경계 명시: `limit` 모드 장기 감시/State Hydration/Reconciler/손절익절 ticker
  평가는 전부 ⑤-4로 명확히 넘겼다(결정4의 "이 서브플랜이 낸 주문은 ⑤-4가 다시 다룰 필요
  없다"는 문장으로 경계가 왜 이렇게 그어졌는지도 남겼다).
- 이미 완료된 ⑤-2 코드에 대한 수정(evaluate_signals 반환값 확장)이 기존 테스트를 깨지
  않는다는 걸 근거와 함께 확인했다.
