# 라이브 트레이딩 더스트(dust) 잔량 자동 흡수 설계

날짜: 2026-08-21

## 배경

`order_execution_mode='limit_timeout'`로 매도를 실행할 때, 1차 지정가 주문 체결 후 남은
잔량이 업비트 최소주문금액(5,000원) 미만이면 잔량 전환 주문 자체를 시도하지 않고 1차
체결분만으로 주문/포지션을 종료한다(`order_executor.py:_run_limit_timeout`,
`_finalize_first_leg`, 271-274행). 이는 의도된 동작이다 — 최소금액 미만 주문은 업비트가
거부하므로 시도해봐야 실패한다.

문제는 이 시점에 내부 `positions` 테이블은 포지션을 닫힌 것으로 기록하지만, 실제 지갑에는
그 잔량이 코인으로 남는다는 점이다. `daemon.py`가 20초마다 호출하는
`reconciler.check_manual_intervention()`이 이 실제 잔고와 내부 장부의 차이를 발견하면
(`_reconcile_position()`), 이를 설명할 매칭 주문이 없으므로 "설명 안 되는 잔고 변화"로
분류하고:

1. 그 잔량만큼 포지션을 다시 연다(`_self_heal_unexplained`).
2. 전략을 `status='paused', manual_pause=1`로 강제 정지시킨다.

`manual_pause=1`이라 자동재개 가드도 이 정지를 스스로 풀지 못한다. 사용자가 UI에서 수동으로
`running`으로 되돌려도, 재오픈된 이 더스트 포지션은 그대로 열려 있다. 그 결과:

- **매수 차단**: `order_executor.handle_signal_result()`는 `position is None`일 때만
  매수 신호를 처리하므로, 더스트 포지션이 열려 있는 한 매수 신호가 계속 무시된다.
- **매도 실패 반복**: 매도 신호가 뜨면 그 더스트 수량(entry_qty) 전체를 팔려고 시도하지만
  (캔들 매도 경로에는 최소주문금액 가드가 없음), 업비트가 5,000원 미만 주문을 거부해
  예외가 나고 `daemon.py`의 바깥 try/except가 로그만 남기고 다음 틱으로 넘어간다. 포지션은
  끝내 닫히지 않아 이 실패가 매 매도 신호마다 반복된다.

실제 사고: 2026-08-21 AWS 라이브에서 KRW-LINK 전략이 이 경로로 paused됐고, 사용자가
running으로 되돌렸지만 매수/매도가 여전히 막혀 있는 상태로 확인됨.

## 목표

- 이런 더스트 잔고 불일치가 앞으로 다시 발생해도, 전략이 정지되거나 매수가 영구히 막히지
  않고 자동으로 계속 정상 운영되게 한다.
- 지금 AWS에 이미 멈춰있는 KRW-LINK 전략의 더스트 포지션을 안전하게 정리한다.
- 진짜 이상(도난/오조작/원가 미추적 등)은 지금처럼 감지·정지 대상으로 남긴다 — "더스트라서
  무시"와 "진짜 몰라서 무시"를 혼동하지 않는다.

## 비목표 (Out of scope)

- 포지션이 이미 열려 있는 상태에서 발생하는 잔고 불일치(예: 부분체결 누적으로 포지션이
  더스트 수준까지 줄어든 경우)는 이번 설계 범위 밖이다. 이 경우는 여전히 기존 동작(정지 후
  수동 확인)을 따른다 — `order_executor.py`의 `exit_for_risk()` docstring에도 이미
  "reconciler가 다음 주기에 처리한다"는 관찰 대상으로 명시돼 있던 동일 계열의 한계이며,
  이번 변경이 그 한계를 새로 만드는 것이 아니라 기존 범위를 유지하는 것이다.
- UI에 포지션 수동 종료/조정 기능을 추가하지 않는다 — Part 3는 1회성 CLI 스크립트다.

## Part 1 — reconciler의 더스트 자동 흡수

파일: `trading/reconciler.py`

`_reconcile_position()`에서 `diff = actual_qty - internal_qty`를 계산한 직후,
**`position is None`이고 `diff`가 양수인 경우**에 한해 다음 판단을 먼저 수행한다(기존
`is_mixed_side`/`is_topup`/`_apply_explained_change` 분기보다 앞서 평가):

```python
_MIN_ORDER_AMOUNT_KRW = 5000  # 업비트 원화마켓 최소 주문금액. order_executor.py의 동일 상수와
                                # 같은 값이지만, 이 모듈의 기존 의존 경계(엔진/다른 trading
                                # 서브모듈 미의존 원칙)를 지키기 위해 import 대신 값을 복제한다.

if position is None and diff > _QTY_EPSILON:
    dust_value_krw = diff * avg_buy_price if avg_buy_price > 0 else None
    if dust_value_krw is not None and dust_value_krw < _MIN_ORDER_AMOUNT_KRW:
        new_baseline = baseline_qty + diff
        db.update_live_strategy_baseline_qty(strategy["id"], new_baseline)
        db.insert_manual_intervention_event(
            market,
            f"최소주문금액 미만 잔고 차이 {diff:.8f}개(≈{dust_value_krw:.0f}원) baseline에 흡수",
            "dust_absorbed",
        )
        return {"balance_mismatch": True, "action": "dust_absorbed", "paused": False}
```

판단 기준:

- **가격 기준**: `avg_buy_price`(이미 `_reconcile_position()`이 계좌 조회로 받아둔 값)를
  그대로 재사용한다 — 추가 API 호출 없음. `avg_buy_price <= 0`(원가 미추적 코인)이면
  가치를 안전하게 판단할 수 없으므로 더스트로 취급하지 않고 기존 로직(정지)으로 폴백한다.
- **방향**: `diff > 0`(잔고 초과분)만 대상. `diff < 0`(잔고 부족)은 다른 종류의 이상이므로
  대상에서 제외하고 기존 로직을 따른다.
- **범위**: `position is None`일 때만 적용한다. 포지션이 이미 열려 있는 상태의 차이는
  건드리지 않는다(비목표 참고).
- 흡수 시 `paused: False`를 반환하므로 전략 상태는 그대로 유지되고, `_run_strategy_loop`의
  `check_circuit_breaker` 등 이후 처리도 정상 경로를 그대로 탄다.

## Part 2 — 캔들 매도 경로에 최소주문금액 가드 추가

파일: `trading/order_executor.py`, 함수: `handle_signal_result()`

현재 `exit_for_risk()`(709-724행)에는 "매도 가능 수량이 최소주문금액 미만이면 시도하지
않는다"는 가드가 있지만, 캔들 기반 매도 분기(813-854행)에는 없다. 그 결과 이런 상황에서
매 매도 신호마다 업비트에 거부당하는 주문을 내고 예외를 로그로만 흘려보낸다.

`handle_signal_result()`의 매도 분기에서 `sellable_qty` 계산 직후, 기존
`if sellable_qty <= 0:` 조건을 `if sellable_qty <= 0 or sellable_qty * expected_price <
_MIN_ORDER_AMOUNT_KRW:`로 확장한다. 이 조건에 걸리면 `exit_for_risk()`와 동일하게 경고
로그만 남기고 매도 주문을 내지 않는다(포지션은 그대로 open 유지 — Part 1이 재발을
막으므로, 이 가드는 어디까지나 예외 스팸을 없애는 방어적 일관성 조치다).

`order_executor.py`에는 이미 모듈 레벨 `_MIN_ORDER_AMOUNT_KRW = 5000` 상수가 있으므로
그대로 재사용한다(같은 파일 내부이므로 복제 불필요).

## Part 3 — 기존 KRW-LINK 더스트 포지션 정리 스크립트

파일(신규): `scripts/absorb_dust_position.py`

용법: `python scripts/absorb_dust_position.py <live_strategy_id>` (AWS 서버에서 SSH로
1회 실행)

동작:

1. `trading.db.get_live_strategy(strategy_id)`와 `trading.position_manager.get_open_position(strategy_id)`로
   대상 전략/포지션을 조회한다. 포지션이 없으면 "정리할 대상 없음"으로 종료한다.
2. `trading.upbit_client.get_accounts()`로 실제 잔고를 조회해, 포지션의 `entry_qty`와
   실제 잔고(`balance + locked`)가 오차범위(`_QTY_EPSILON`) 내로 일치하는지 확인한다.
   불일치하면(더 큰 진짜 포지션과 혼동될 위험) 안전을 위해 중단하고 사람이 직접 확인하게
   한다.
3. `entry_qty * entry_price`가 정말 5,000원 미만인지 확인한다. 아니면 `--force` 플래그
   없이는 중단한다 — 실수로 진짜 포지션을 더스트로 오인해 지우는 사고를 막기 위함이다.
4. `position_manager.close_position()`이 아니라 `db.close_position_row()`를 직접
   호출해 `realized_pnl=0, realized_pnl_pct=0`으로 포지션을 종결한다.
   `position_manager.close_position()`을 쓰면 `db.update_live_strategy_capital()`이
   함께 호출돼 `exit_price * exit_qty`만큼 없는 돈이 들어온 것처럼 `current_capital`이
   부풀려진다 — 이건 실제 매도가 아니라 장부 정리이므로 자본에 어떤 영향도 줘선 안 된다.
5. `db.update_live_strategy_baseline_qty(strategy_id, baseline_qty + entry_qty)`로
   그 수량을 baseline에 흡수한다.
6. `db.insert_manual_intervention_event(market, ..., "dust_absorbed_manual_cleanup")`로
   감사 기록을 남긴다.
7. 처리 결과(정리된 수량/가치/새 baseline)를 표준출력에 요약한다.

## 테스트 계획

- `trading/reconciler.py`의 `_reconcile_position()` 유닛 테스트: `position=None` +
  `diff`가 최소주문금액 미만 가치일 때 `dust_absorbed` 액션이 반환되고, `paused=False`이며
  baseline이 정확히 갱신되는지 확인. `avg_buy_price<=0`일 때는 기존 정지 경로로 폴백하는지
  확인. `diff<0`이거나 `position is not None`이면 이 새 분기를 타지 않는지 확인(회귀 방지).
- `order_executor.py`의 `handle_signal_result()` 유닛 테스트: `sellable_qty`가 양수이지만
  가치가 최소주문금액 미만일 때 `exit()`가 호출되지 않고 포지션이 open으로 남는지 확인.
- `scripts/absorb_dust_position.py`: 정상 케이스(포지션 종결+baseline 갱신+capital
  불변)와 안전장치 케이스(실잔고 불일치, 최소금액 초과 시 `--force` 없이 중단)를 각각
  검증하는 스모크 테스트 또는 수동 점검 절차.

## 배포 순서

1. Part 1/2를 코드로 구현하고 테스트 통과 확인.
2. AWS 서버에 배포(`deploy/update.sh` 등 기존 배포 절차 사용).
3. 배포 후 `scripts/absorb_dust_position.py`를 AWS 서버에서 실행해 기존 KRW-LINK 더스트
   포지션을 정리한다 — Part 1/2가 이미 배포된 상태에서 실행해야, 정리 직후 reconciler가
   같은 잔고를 다시 "설명 안 됨"으로 오분류하지 않는다.
