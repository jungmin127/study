# 라이브 전략 시드(자본) 변경

## 배경 / 문제

라이브 전략은 승인 시점에 1회 계산된 자본(`live_strategies.current_capital`)으로 시작해, 이후 매 청산마다 `position_manager.close_position()`이 청산 금액(`exit_price*exit_qty - fee`) 전액을 그대로 `current_capital`에 덮어쓰는 복리 재투자 구조다(`trading/position_manager.py:39-65`). 사용자가 운용 중 자금을 증액하거나 감액하고 싶어도 이를 반영할 방법이 없다.

또한 자본이 중간에 바뀌면, 현재 `_strategy_metrics()`(`backend/trading_analytics_service.py:51`)가 쓰는 `cumulative_pnl / 고정 baseline` 방식은 왜곡된다 — baseline보다 큰 금액을 투입한 뒤 손익이 나면 실제 성과보다 훨씬 작거나 큰 수익률로 표시된다.

## 목표

- 포지션이 없을 때 전략의 현재 자본(시드)을 사용자가 직접 증액/감액할 수 있다.
- 자본 변경 이력을 기록하고 화면에서 확인할 수 있다.
- 자본 변경이 있어도 누적 수익률(%)이 시간가중수익률(TWR) 방식으로 정확히 계산된다.

## 비목표

- 포지션 보유 중 시드 변경 (지원하지 않음, 명시적으로 차단)
- 계좌(업비트) 실제 입출금 자동화 — 사용자가 실제로 입출금을 마친 뒤 화면에서 숫자만 맞추는 것을 전제로 한다.
- 백테스트 쪽 자본 변경 (라이브 전략에만 해당)

## 설계

### 데이터 모델: `capital_adjustments` 테이블

```sql
CREATE TABLE IF NOT EXISTS capital_adjustments (
    id                TEXT PRIMARY KEY,
    live_strategy_id  TEXT NOT NULL REFERENCES live_strategies(id),
    adjusted_at       TEXT NOT NULL,   -- datetime('now')
    previous_capital  REAL NOT NULL,
    new_capital       REAL NOT NULL,
    delta             REAL NOT NULL    -- new_capital - previous_capital
);
```

`trading/db.py`에 `insert_capital_adjustment(live_strategy_id, previous_capital, new_capital)`와 `list_capital_adjustments(live_strategy_id)`(adjusted_at 오름차순) 추가.

### 백엔드: 시드 변경 엔드포인트

`PATCH /api/v1/live-strategies/{id}/capital`, body `{ "new_capital": number }`.

검증 순서:
1. 전략 존재 + `approved_at is not None` (draft 전략은 아직 자본 개념 자체가 없음)
2. `status in ("running", "paused")` (stopped 전략은 변경 불가)
3. `trading_db.get_open_position(id) is None` — 포지션 보유 중이면 400 "포지션 보유 중에는 시드를 변경할 수 없습니다."
4. `new_capital > 0`

통과 시: `insert_capital_adjustment(id, previous_capital=strategy["current_capital"], new_capital)` 기록 → 기존 `db.update_live_strategy_capital(id, new_capital)` 호출 (close_position이 쓰는 것과 같은 함수, 재사용). 응답은 `_full_live_strategy_response(id)`.

### 누적 수익률: 시간가중수익률(TWR)로 전환

`_strategy_metrics()`가 `cumulative_pnl_pct`를 구간 연결 방식으로 계산하도록 변경한다.

```python
def _twr_pct(closed_positions: list[dict], baseline: float, adjustments: list[dict]) -> float:
    """자본 조정 시점을 경계로 거래를 구간으로 나눠 구간수익률을 복리로 연결한다.
    조정 이력이 없으면 결과는 (cumulative_pnl / baseline * 100)과 동일하다."""
    if not adjustments:
        pnl = sum(p["realized_pnl"] for p in closed_positions)
        return (pnl / baseline * 100.0) if baseline else 0.0

    positions_sorted = sorted(closed_positions, key=lambda p: p["exit_time"])
    factor = 1.0
    seg_start_capital = baseline
    cursor = 0
    for adj in adjustments:  # adjusted_at 오름차순
        seg_pnl = 0.0
        while cursor < len(positions_sorted) and positions_sorted[cursor]["exit_time"] < adj["adjusted_at"]:
            seg_pnl += positions_sorted[cursor]["realized_pnl"]
            cursor += 1
        if seg_start_capital:
            factor *= 1 + seg_pnl / seg_start_capital
        seg_start_capital = adj["new_capital"]  # 조정 직후 자본이 다음 구간의 시작 자본

    seg_pnl = sum(p["realized_pnl"] for p in positions_sorted[cursor:])
    if seg_start_capital:
        factor *= 1 + seg_pnl / seg_start_capital
    return (factor - 1) * 100.0
```

`adjustments[i]["previous_capital"]`은 별도로 쓰지 않는다 — 포지션 없을 때만 변경 가능하다는 제약 덕분에, 조정 직전 `current_capital`은 항상 "직전 구간 시작자본 + 직전 구간 pnl"과 같으므로 `new_capital`만으로 다음 구간을 이어갈 수 있다.

`cumulative_pnl`(금액, %가 아닌 절대값)은 지금처럼 `sum(realized_pnl)` 그대로 유지 — 구간 나눌 필요 없음. `_strategy_metrics()`가 반환하는 `cumulative_pnl_pct` 하나만 이 함수로 대체되고, 이를 소비하는 `get_journal_summary()`/`_market_metrics()`/`_backtest_comparison()`은 호출부 변경 없이 그대로 정확한 값을 받는다.

일별 자본 곡선(`daily_performance`)은 청산 시에만 갱신되므로(`trading/risk_manager.py:21`) 시드 변경 자체로는 건드릴 필요가 없다 — 다음 청산부터 자연히 새 자본 규모를 반영한다.

### API 응답 확장

[[2026-08-17-live-strategy-condition-info-design]]에서 만드는 `_live_strategy_response()`에 `capital_adjustments` 필드(시간순 리스트)를 추가한다.

### 프론트엔드

- `LiveStrategiesPage.tsx` 카드: `open_position === null && status in (running, paused)`일 때만 "시드 변경" 버튼 노출. 클릭 시 다이얼로그(`ui/dialog.tsx`)에 현재 자본 표시 + 새 금액 입력 필드 + 확인/취소. 확인 시 `PATCH .../capital` 호출 후 목록 새로고침.
- 1번 기능의 정보 모달에 "자본 변경 이력" 섹션 추가: `capital_adjustments`를 시간순으로 나열, "2026-08-17 14:30 · 500,000원 → 1,000,000원" 형태.
- 매매일지(`journal`)/전략 카드의 수익률 표시 컴포넌트는 수정 불필요 — 백엔드가 이미 TWR로 계산한 `cumulative_pnl_pct`를 그대로 사용.

## 테스트 계획

- `tests/test_trading_db.py`: `insert_capital_adjustment`/`list_capital_adjustments` 단위 테스트.
- `tests/test_backend.py`: `PATCH /api/v1/live-strategies/{id}/capital` — 성공 케이스, 포지션 보유 중 차단, stopped 전략 차단, new_capital<=0 차단.
- `tests/test_trading_analytics_service.py`: `_twr_pct()` — 조정 없을 때 기존 계산과 동일한지, 조정 1회/2회 있을 때 구간 연결 결과가 수기 계산과 일치하는지(브레인스토밍 중 확인한 예시: 50만→+10%→55만, 50만 증액(105만)→-5%→99.75만 이면 TWR = (1.10×0.95)-1 = +4.5%).
