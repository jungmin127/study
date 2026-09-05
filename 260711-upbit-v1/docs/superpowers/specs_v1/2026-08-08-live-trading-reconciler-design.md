# 라이브 트레이딩 서브플랜⑤-4a — reconciler.py Design Spec

## 배경 및 목표

`docs/superpowers/specs_v1/2026-08-04-live-trading-foundation-design.md`(이하 "기반 스펙")의
1단계 로드맵 "5. 트레이딩 엔진 코어"를 여러 서브플랜으로 쪼갠 것(①DB+자금관리+리스크관리
②신호평가 ③주문실행 ④reconciler+daemon 통합) 중 네 번째 단계의 첫 조각이다.
서브플랜⑤-3(`order_executor.py`)까지 끝나 신호평가→주문실행까지의 "정상 경로"는
완성됐다([[upbit-v1-live-trading-foundation]]). 기반 스펙 결정5는 모의투자 없이 처음부터
실주문을 쓰기로 했으므로, State Hydration/Reconciler(수동개입 감지)는 "나중에 추가할
인프라"가 아니라 1단계 필수 안전장치다 — 이 스펙은 그중 Reconciler를 설계한다.

⑤-4는 원래 reconciler+daemon을 한 서브플랜으로 묶어 부르던 이름이지만, daemon.py(메인
루프·asyncio 태스크 오케스트레이션)는 이 스펙이 만드는 reconciler.py의 두 함수를
"언제 호출할지"만 결정하는 얇은 조정자라 별도 스펙으로 뒤에 분리한다(사용자 확정 —
reconciler.py부터 순차 진행). 이 문서를 ⑤-4a, 다음 daemon.py 스펙을 ⑤-4b로 부른다.

## 범위

**이 스펙에서 확정하는 것:**
- `trading/reconciler.py`의 공개 함수 2개(`hydrate_state`/`check_manual_intervention`)와
  공유 내부 파이프라인
- 외부(수동) 주문 감지 방식, 잔고-포지션 대조 방식, self-heal(자동 보정) 규칙
- 체결가 추적 정밀도(설명 가능한 불일치 vs 설명 불가 불일치)와 각각의 처리 정책
- `trading/db.py`에 추가할 함수(외부주문 기록, 수동개입 이벤트 기록, 미체결 주문 조회,
  포지션 수량 보정) + `live_strategies.baseline_qty` 스키마 추가(전략 시작 전 보유
  코인 격리, 결정9)

**이 스펙에서 다루지 않는 것(⑤-4b daemon.py 및 후속 서브플랜에서):**
- `hydrate_state`/`check_manual_intervention`을 언제(재시작 시 1회, 몇 초 주기) 호출할지의
  스케줄링 자체 — reconciler.py는 자기 자신을 위한 타이머/루프를 갖지 않는다.
- 손절/익절(`STOP_LOSS_PCT`/`TAKE_PROFIT_PCT`)의 실시간 ticker 기반 평가.
- `limit`(타임아웃 없음) 모드로 낸 주문의 장기 미체결 감시("주문상태 감시" 백그라운드
  루프) — 이건 daemon.py가 별도로 돌리는 루프이고, hydrate_state는 그 주문들의 "재시작
  시점 스냅샷 동기화"만 담당한다(둘 다 미체결→체결/취소를 감지하지만, hydrate_state는
  1회성 재시작 복구, daemon의 감시 루프는 지속적 폴링이라는 점이 다르다).
- NTP 오차 점검, Rate Limit 큐 — 기반 스펙 "인프라 세부사항" 절, 별도 관심사.

## 핵심 결정

### 결정 1 — 공유 핵심 파이프라인 + 얇은 진입점 2개

`hydrate_state(strategy)`(데몬 시작 시 전략당 1회)와 `check_manual_intervention(strategy)`
(러닝 중 15~30초 주기)는 겉보기엔 다른 상황이지만 "내부 DB 상태 vs 실제 거래소 상태를
대조하고 불일치를 처리한다"는 핵심 로직은 동일하다. 공유 파이프라인
`_run_reconcile_pipeline(strategy)`(외부주문 감지 → 잔고 대조)를 두 진입점이 그대로
호출한다. 차이는 `hydrate_state`가 그 앞에 "내부 `wait` 상태 limit 주문 동기화" 단계를
하나 더 거친다는 것뿐(결정6).

### 결정 2 — 재시작 중 발견된 수동개입도 러닝 중과 동일하게 `manual_intervention_policy` 적용

데몬이 꺼져있던 동안(다운타임) 발생한 수동개입이든 러닝 중 발생한 것이든, "엔진이 모르는
거래가 있었다"는 사실 자체는 동일하므로 같은 정책(`all_stop`/`acknowledge_and_continue`)을
적용한다(사용자 확정). `all_stop`이면 `hydrate_state` 단계에서 이미 전략이 `paused`로
남아 `running`으로 전환되지 않는다. `acknowledge_and_continue`면 이벤트만 기록하고 정상
시작한다.

### 결정 3 — 불일치는 self-heal(자동 보정)한다

내부 `positions`/`live_strategies.current_capital`을 실제 거래소 상태에 맞게 자동
보정한다(사용자 확정 — 기록만 남기고 방치하면 다음 `exit()` 호출이 stale한 수량으로 주문을
내서 거래소가 "잔고부족"으로 거부할 위험이 있다). self-heal 없이 기록만 남기는 대안은
안전하지 않다고 판단해 기각했다.

### 결정 4 — 체결가는 매칭되는 외부주문을 찾아 정밀하게 계산한다

잔고 불일치를 발견했을 때 `realized_pnl`/`current_capital` 계산에 쓸 체결가를 어디까지
추적할지 두 방안을 검토했다:
- **(채택) 정밀**: `_detect_external_orders`가 같은 실행에서 찾아낸 외부주문의 실제
  체결상세(`get_order()`의 `trades[]`)를 그대로 써서 정확한 평균 체결가로 계산.
- (기각) 근사: 수량만 보정하고 가격은 감지 시점의 현재 시세로 추정. 구현은 더 간단하지만
  외부거래로 발생한 포지션의 `realized_pnl`이 부정확해지고, 매매일지/성과분석(기반 스펙
  3단계)의 신뢰도를 떨어뜨린다.

사용자가 정밀 방안을 선택했다(외부주문과 잔고 변화가 실제로 대응되는 한 정확한 계산이
가능하므로, 구현 비용 대비 가치가 크다고 판단).

**정밀 계산의 전제:** 잔고 변화가 이번 실행에서 찾아낸 외부주문(들)만으로 오차범위
(`_QTY_EPSILON = 1e-6`, 업비트 최소주문 단위보다 훨씬 촘촘한 값) 안에서 완전히 설명될
때만 이 경로를 쓴다. 부분적으로만 설명되거나 아예 설명이 안 되면 전체를 결정5("설명 안
되는 불일치")로 처리한다 — "일부는 정밀, 일부는 근사"처럼 섞지 않는다(구현 복잡도 대비
실익이 없고, 어차피 결정5 경로도 수량 self-heal은 그대로 하므로 안전성 손실이 없다).

### 결정 5 — 설명 안 되는 잔고 불일치는 정책과 무관하게 강제 `all_stop`

매칭되는 외부주문을 못 찾은 잔고 변화(코인 입출금 등 주문을 거치지 않은 변화, 또는 조회
lookback 범위 밖의 오래된 주문)는 `manual_intervention_policy` 설정과 무관하게 항상
전략을 `paused`로 전환한다. 근거 가격 없이 자동으로 계속 매매를 이어가는 게 더
위험하다고 판단했다(사용자 확정 — 결정4의 정밀 계산 원칙과 일관됨: 가격을 모르면 계속
돌리지 않는다). 수량만 실제 잔고에 맞춰 self-heal하고(주문 실패 방지, 결정3), PnL/자금은
건드리지 않은 채 "미확인 잔고 변화"로 기록해 사용자의 수동 확인을 요구한다.

### 결정 6 — `hydrate_state`의 내부 `wait` limit 주문 동기화는 catch-up이지 수동개입이 아니다

`hydrate_state`는 파이프라인 실행 전에 내부 `orders(status='wait', order_type='limit')`
행을 전부 `get_order()`로 재조회해 오프라인 동안 체결/취소된 결과를 조용히 반영한다. 이건
"우리가 낸 주문"이 뒤늦게 결과를 알려준 것일 뿐 외부 개입이 아니므로
`manual_intervention_events`에 기록하지 않는다. `limit_timeout`/`market`/`market_capped`
모드 주문은 `order_executor.py`가 이미 블로킹으로 최종 상태까지 확정한 뒤 반환하므로
(⑤-3 결정4) 여기 남을 수 없다 — 이 동기화 대상은 사용자가 명시적으로 선택한 plain
`limit` 모드뿐이다.

### 결정 7 — 여러 외부주문이 한 번에 발견되면 방향별 가중평균으로 합산한다

`_detect_external_orders`가 한 번에 매수/매도 외부주문을 여러 개 찾을 수 있다(예: 사용자가
앱에서 분할매도). `_reconcile_position`은 side별로 체결금액(`filled_price ×
filled_volume`)과 수량을 합산해 가중평균 체결가를 계산한다 — ⑤-3
`order_executor._run_limit_timeout()`이 잔량 시장가 전환분과 지정가 체결분을 합산할 때
쓴 것과 동일한 패턴이다.

### 결정 8 — API 실패는 조용히 스킵하고 다음 주기에 재시도한다

Reconciler는 감시자이지 트레이더가 아니다 — 이번 실행에서 업비트 API 호출이 실패해도
매매를 막지 않고 예외를 흡수한 뒤 다음 주기를 기다린다(`check_manual_intervention`이
15~30초 뒤 다시 호출되는 게 daemon의 몫이므로 reconciler는 재시도 루프를 만들지 않는다 —
⑤-3 결정5가 "우리가 낸 주문"의 이중주문 방지를 위해 재시도하는 것과는 성격이 다르다).
연속 실패 횟수는 반환값에 실어 daemon이 로그/알림 여부를 판단하게 한다.

### 결정 9 — 전략 시작 전부터 보유하던 코인은 `baseline_qty`로 격리한다

기반 스펙 결정6("코인당 전략 1개")은 "그 코인의 실제 잔고 = 그 전략이 만든 포지션"을
암묵적으로 가정하지만, 사용자가 전략을 승인하기 전부터 그 코인을 이미 보유하고 있으면
(사용자 확인: 현재 실제로 BTC를 보유 중) 이 가정이 깨진다. `hydrate_state()`가 첫 호출
때 "내부엔 포지션이 없는데 잔고엔 코인이 있다"를 외부 매수로 오인해 매칭되는 주문을
못 찾고 결정5에 따라 시작하자마자 강제 `paused`로 빠지는 문제가 있었다(사용자가
브레인스토밍 중 발견).

**결정:** `live_strategies`에 `baseline_qty REAL` 컬럼을 추가한다(기본값 NULL).
`hydrate_state()`는 `strategy['baseline_qty']`가 NULL이면 이번이 그 전략의 첫 호출이라고
판단해, 그 시점의 실제 코인 잔고를 그대로 `baseline_qty`로 저장하고 **이번 호출은
불일치 검사를 건너뛴다**(비교 기준을 막 세운 시점이라 비교할 게 없음). 이후
`_reconcile_position()`은 항상 raw 잔고가 아니라 `실제잔고 - baseline_qty`를 "그 전략이
직접 만든 포지션 몫"으로 계산해서 내부 `positions`와 비교한다.

**효과:** 승인 전부터 보유하던 코인은 baseline으로 격리돼 계속 그대로 둔다. 봇이 실제로
사고 판 만큼만 정확히 추적되고, baseline 확정 이후 그 코인 잔고가 또(baseline 캡처分을
넘어) 변하면 여전히 정상적으로 수동개입/불일치로 감지된다 — "예외를 만든다"기보다
"비교 기준점을 0이 아니라 실제 시작 시점 잔고로 잡는다"에 가깝다. 사용자가 확인한 대로
현재 코인당 전략 1개 원칙(결정6)을 지킬 계획이므로, 이 baseline은 승인 시점 딱 한 번만
의미를 가지면 충분하고 이후 재조정 UI 등은 이 스펙 범위 밖이다.

**KRW 잔고 이자와의 구분:** 업비트가 지급하는 KRW 보관이자는 `_reconcile_position()`이
비교하는 대상(그 전략의 마켓 코인 잔고)과 무관한 별개 통화(KRW)라서 애초에 이 로직에
영향을 주지 않는다 — 별도 처리가 필요 없다.

## `trading/reconciler.py`

```python
_QTY_EPSILON = 1e-6  # 실제잔고 vs 내부 positions 수량 비교 허용오차

async def hydrate_state(strategy: dict, *, client: httpx.AsyncClient | None = None) -> dict:
    """데몬 시작 시 전략 1개당 1회 호출. 내부 status='wait' limit 주문을 먼저
    동기화(결정6, catch-up)한다. 이어서 strategy['baseline_qty']가 None이면(결정9,
    이 전략의 첫 호출) 그 시점 실제 코인 잔고를 db.update_live_strategy_baseline_qty()로
    저장하고 이번 호출은 불일치 검사 없이 반환한다({"baseline_captured": True, ...}).
    이미 baseline이 있으면 _run_reconcile_pipeline()을 그대로 수행한다.
    반환: {"synced_wait_orders": int, "baseline_captured": bool,
    **_run_reconcile_pipeline()의 반환값(baseline_captured=True인 호출은 생략)}."""

async def check_manual_intervention(strategy: dict, *, client=None) -> dict:
    """러닝 중 데몬이 주기적으로 호출. _run_reconcile_pipeline()을 그대로 수행한다."""

async def _sync_pending_limit_orders(strategy: dict, *, client=None) -> int:
    """내부 orders(status='wait', order_type='limit', live_strategy_id=strategy['id'])를
    순회해 get_order()로 재조회, db.update_order_filled()로 반영. 반환값은 동기화한 행 수."""

async def _run_reconcile_pipeline(strategy: dict, *, client=None) -> dict:
    """_detect_external_orders() → _reconcile_position() 순서로 실행하고 결과를 합쳐
    반환한다. 업비트 API 실패는 여기서 흡수하고 {"error": str(exc)}를 반환한다(결정8)."""

async def _detect_external_orders(strategy: dict, *, client=None) -> list[dict]:
    """list_open_orders(market)+list_closed_orders(market, states=['done','cancel'])를
    조회, db.get_order_by_upbit_uuid()로 내부에 없는 uuid만 추려 get_order()로 체결상세
    확인 후 db.insert_external_order()로 기록. 발견된 건마다
    db.insert_manual_intervention_event() + risk_config['manual_intervention_policy']에
    따라 all_stop이면 db.update_live_strategy_status(paused). 새로 기록된 외부주문
    dict 리스트를 반환(_reconcile_position이 재사용, 결정1)."""

async def _reconcile_position(
    strategy: dict, external_orders: list[dict], *, client=None,
) -> dict:
    """get_accounts()에서 market의 코인 잔고(balance+locked)를 읽고 strategy['baseline_qty']
    (결정9)를 뺀 값을 "그 전략 자신의 몫"으로 삼아 내부 position_manager.get_open_position()의
    entry_qty(없으면 0)와 대조한다.
    |(실제잔고 - baseline_qty) - 내부수량| <= _QTY_EPSILON이면 변화 없음.
    차이가 external_orders(결정7 가중평균)로 _QTY_EPSILON 이내까지 설명되면(결정4):
      - 내부 포지션 없었는데 잔고 생김 → position_manager.open_position(가중평균가)
      - 내부 포지션 전량 사라짐 → position_manager.close_position(close_reason='manual')
        + risk_manager.record_trade_result()
      - 내부 포지션 일부만 변함(부분 체결) → db.adjust_position_qty()로 수량만 보정,
        current_capital/realized_pnl은 건드리지 않음(전량 청산 시점까지 유예, ⑤-1
        position_manager.close_position()의 "복리는 전량 청산 시에만" 원칙과 동일)
      설명된 뒤에도 risk_config['manual_intervention_policy']==all_stop이면 paused.
    설명이 안 되면(결정5): 수량만 실제 잔고로 self-heal(db.adjust_position_qty 또는
      open/close, 가격은 없으므로 realized_pnl 미계산), 정책 무관 강제 paused,
      manual_intervention_events에 '미확인 잔고 변화'로 기록.
    반환: {"balance_mismatch": bool, "action": "none"|"opened"|"closed"|"adjusted"|
    "unexplained", "paused": bool}."""
```

## `trading/db.py` 스키마 변경 + 추가 함수

`live_strategies`에 `baseline_qty REAL` 컬럼 추가(결정9, 기본값 NULL). 실제
`data/trading.db` 파일이 아직 생성된 적이 없으므로(⑤-1~⑤-3은 전부 테스트에서만
`tmp_path` DB를 썼다) 마이그레이션 대상 데이터가 없다 — `_SCHEMA`의
`CREATE TABLE live_strategies` 정의에 `baseline_qty REAL` 컬럼을 바로 추가한다(YAGNI,
이 저장소에 기존 `ALTER TABLE` 마이그레이션 관례 자체가 없다).

```python
def get_order_by_upbit_uuid(upbit_uuid: str) -> dict | None:
    """_detect_external_orders()의 중복 감지에 사용(이미 기록된 외부주문 재알림 방지)."""

def insert_external_order(
    live_strategy_id: str, position_id: str | None, market: str, side: str,
    order_type: str, upbit_uuid: str, filled_price: float | None,
    filled_volume: float | None, fee: float | None, status: str,
) -> str:
    """이미 체결/취소 결과까지 알고 있는 외부주문을 한 번에 기록한다(is_external=1).
    order_executor.insert_order()+update_order_filled() 2단계와 달리, 외부주문은
    발견 시점에 이미 최종 상태이므로 1회 INSERT로 끝난다."""

def insert_manual_intervention_event(
    market: str, description: str, action_taken: str,
) -> str:
    """manual_intervention_events 행 생성(action_taken: 'all_stop'|
    'acknowledged_and_continued')."""

def list_wait_orders(live_strategy_id: str, order_type: str | None = None) -> list[dict]:
    """orders(status='wait', live_strategy_id=..., [order_type=...])를 조회.
    _sync_pending_limit_orders()가 order_type='limit'로 호출."""

def update_live_strategy_baseline_qty(live_strategy_id: str, baseline_qty: float) -> None:
    """live_strategies.baseline_qty를 설정한다(결정9, hydrate_state의 첫 호출에서 1회만
    호출됨 — 이미 값이 있으면 호출하지 않는 게 호출자의 책임)."""

def adjust_position_qty(position_id: str, new_qty: float) -> None:
    """positions.entry_qty를 직접 보정한다(부분 외부체결 self-heal 전용 — 정상 매매
    흐름의 enter()/exit()는 이 함수를 쓰지 않는다, 항상 전량 진입/청산이므로)."""
```

## 에러 처리

- 업비트 API 호출 실패(네트워크/레이트리밋) 시 `_run_reconcile_pipeline()`이 예외를
  잡아 `{"error": str(exc)}`를 반환하고 그 실행은 아무 것도 변경하지 않는다(결정8).
- `_reconcile_position()`에서 `external_orders`가 여러 방향(매수+매도) 섞여 있는데
  둘 다로 설명 가능한 복잡한 케이스(예: 거의 동시에 부분매도+부분매수)는 이 스펙
  범위에서는 "설명 안 됨"으로 처리한다(결정5) — 방향이 섞인 자동 매칭은 오탐 위험이 커서
  1단계에서는 다루지 않고, 그런 경우가 실제로 관측되면 후속 스펙에서 다룬다.
- `_sync_pending_limit_orders()` 중 개별 주문 조회가 실패해도 나머지 주문 동기화는
  계속 진행한다(한 건의 실패가 재시작 복구 전체를 막지 않게).

## 테스트 전략

⑤-3과 동일한 패턴 — `trading.upbit_client`의 `get_accounts`/`list_open_orders`/
`list_closed_orders`/`get_order`를 `monkeypatch`로 mock, `tmp_path` SQLite DB 픽스처
재사용.

- 외부주문 없음 → 아무 것도 기록 안 됨, `paused` 전환 없음(가장 흔한 정상 경로).
- 미체결 외부주문 발견(`state='wait'`) → `orders`(`is_external=1`)/
  `manual_intervention_events` 기록, `all_stop`이면 `paused` 전환 / `acknowledge_and_continue`면
  상태 유지 둘 다 검증.
- 체결된 외부 매도로 잔고가 내부 포지션 전량만큼 사라짐 → `close_position`+
  `record_trade_result` 호출, 그 외부주문의 실제 체결가로 `realized_pnl` 계산.
- 체결된 외부 매수로 잔고가 생겼는데 내부 포지션 없음 → `open_position` 생성(가중평균가).
- 부분 외부 매도(잔고 > 0이지만 내부 수량보다 작음) → `adjust_position_qty`만 호출,
  `current_capital` 불변 확인.
- 외부주문 여러 개(같은 방향) → 가중평균 체결가 계산 검증(결정7).
- 매칭 안 되는 잔고 불일치 → 정책 무관 강제 `paused`, 수량만 보정, `realized_pnl` 없음
  (결정5).
- `hydrate_state`: 오프라인 중 내부 `wait` limit 주문이 조용히 체결/취소된 경우 →
  `manual_intervention_events`에 기록되지 않는지 확인(결정6).
- API 실패 → 예외가 전파되지 않고 `{"error": ...}` 반환, DB 변경 없음(결정8).
- 재시작 시 발견된 수동개입도 러닝 중과 동일 정책 적용 확인(결정2) — `hydrate_state`
  케이스로 `all_stop`/`acknowledge_and_continue` 둘 다 검증.
- `baseline_qty`가 NULL인 전략의 첫 `hydrate_state` 호출 → 실제 잔고(승인 전부터 보유하던
  코인 포함)가 그대로 `baseline_qty`로 저장되고, 그 잔고가 `positions`에 아무 영향도
  주지 않으며 `paused` 전환도 없는지 확인(결정9, 사용자가 발견한 "기존 보유 BTC" 케이스
  재현). 이어지는 두 번째 호출부터 `실제잔고 - baseline_qty`로 정상 대조되는지 확인.

## 자기 검토(스펙 완성도)

- 플레이스홀더 없음 — 9개 핵심 결정 각각 "왜"와 기각한 대안을 남겼다.
- 결정1(공유 파이프라인) ↔ 결정6(hydrate_state만 추가 단계)이 서로 모순 없이
  `_run_reconcile_pipeline()` 하나로 수렴함을 함수 시그니처에서 확인했다.
- 결정3(self-heal) ↔ 결정4(정밀 계산) ↔ 결정5(설명 안 되면 강제 all_stop)이 하나의
  일관된 원칙("수량은 항상 보정하되, 가격을 모르면 자동매매를 계속하지 않는다")으로
  묶임을 명시했다.
- 스코프 경계: daemon.py의 스케줄링(언제 호출할지), 손절/익절 실시간 평가, `limit` 모드
  장기 감시 루프는 전부 ⑤-4b로 명확히 넘겼다.
- 기존 모듈과의 인터페이스 일치 확인: `position_manager.open_position`/`close_position`/
  `get_open_position`, `risk_manager.record_trade_result`, `db.update_live_strategy_status`
  전부 이미 구현된 시그니처를 그대로 재사용하며 새로 바꾸는 게 없다.
