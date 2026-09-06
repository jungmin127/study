# daemon 장세 자동 스왑 루프 — 설계 스펙

## 배경

[[upbit-v1-regime-strategy-pivot-adx-autoswap]]에서 확정한 4단계 피벗 계획의
마지막 단계다. 1단계(레거시 삭제), 2단계(ADX 장세판별 엔진+탭), 3단계(전략
라이브러리 UI), 그리고 로컬→AWS 라이브러리 푸시 스크립트까지 전부 완료됐다.
남은 것은 daemon이 실시간으로 코인별 현재 장세를 판정해, `regime_strategy_library`에
저장된 매핑으로 라이브 전략을 자동 교체하는 루프뿐이다 — 실거래에 직접
개입하는 이 프로젝트에서 가장 리스크가 높은 부분이라 별도 세션에서
브레인스토밍하기로 결정했었다.

이미 있어서 재사용하는 것: "전략 교체" API(`trading_db.replace_live_strategy_strategy`,
2026-08-18)가 같은 market·포지션 없음 조건에서의 제자리 교체를 이미 구현해
두었고, `regime_strategy_library`(3단계)가 코인별 하락/횡보/상승/기본 매핑을
이미 저장하고 있다. 이 스펙은 그 사이를 잇는 판정+실행 루프만 추가한다.

## 목표

`auto_swap_enabled`가 켜진 라이브 전략에 대해 daemon이 10분마다:

1. 해당 market의 1시간봉 ADX로 "현재 확정 장세"를 판정한다(최근 3봉 연속
   같은 판정이어야 확정 — 끈백질/whipsaw 방지). 판정이 불확실하면 "기본"으로
   취급한다.
2. 확정 장세가 이 전략에 마지막으로 적용된 장세(`active_regime`)와 다르면,
   `regime_strategy_library`에서 해당 (market, 장세) 매핑을 찾아
   `replace_live_strategy_strategy()`로 교체한다.
3. 오픈 포지션이 있어 교체가 거부되면 다음 틱에 자동으로 재시도한다(대기).
4. 사용자는 라이브 전략별로 자동/수동 스위치를 언제든 켜고 끌 수 있고,
   자동 스위치가 켜진 채로 기존 "전략 교체" UI로 수동 개입해도, 다음 실제
   장세변화 시점까지 automation이 되돌리지 않는다.
5. 모든 판정/시도/성공/실패/수동개입을 로그 테이블에 남겨 UI에서 확인할 수
   있다(실거래 개입이라 추적성 확보).

## 비범위

- 장세 판정 알고리즘 자체 변경 — 2단계에서 만든 `engine.regime_adx`를 그대로
  재사용한다.
- 코인별 라이브러리 매핑 편집 UI — 3단계에서 이미 구현됨(`/strategy-library`).
- 텔레그램 등 알림 — 로그 테이블 + UI 노출까지만 이번 범위.
- 여러 라이브 전략이 같은 market을 동시에 갖는 시나리오에 대한 특별 처리 —
  각 라이브 전략은 독립적으로(자기 `id` 기준) 처리되므로 자연히 지원되지만,
  일반적인 운영 관례(시장당 전략 1개)를 전제로 설계했다.

## 설계

### 1. DB 스키마 변경 (`trading/db.py`)

`live_strategies` 테이블에 컬럼 2개 추가(`_SCHEMA` 문자열, `baseline_qty`/
`deleted_at` 다음):

```sql
CREATE TABLE IF NOT EXISTS live_strategies (
    ...
    baseline_qty        REAL,
    deleted_at          TEXT,
    auto_swap_enabled   INTEGER NOT NULL DEFAULT 0,
    active_regime       TEXT
);
```

- `auto_swap_enabled`: 0(수동, 기본값)/1(자동). 기존 전략은 전부 0으로
  마이그레이션 없이 자연스럽게 초기화된다(이 프로젝트는 개발 단계 무마이그레이션
  정책 — DB 파일 재생성이 곧 마이그레이션).
- `active_regime`: `NULL`/`'하락'`/`'횡보'`/`'상승'`/`'기본'`. `NULL`은 "이
  기능 도입 이후 아직 한 번도 동기화된 적 없음"을 의미하며, 자동스위치를 켠
  직후 첫 틱에서 반드시 한 번 동기화를 시도하게 만드는 트리거 역할을 한다
  (오픈 포지션이 있으면 그 상태로 대기).

신규 테이블(`regime_strategy_library` 다음에 추가):

```sql
CREATE TABLE IF NOT EXISTS regime_swap_log (
    id                TEXT PRIMARY KEY,
    live_strategy_id  TEXT NOT NULL REFERENCES live_strategies(id),
    market            TEXT NOT NULL,
    occurred_at       TEXT NOT NULL DEFAULT (datetime('now')),
    event             TEXT NOT NULL CHECK (event IN (
                          'swap_success', 'swap_skipped_open_position',
                          'swap_skipped_no_mapping', 'manual_override_ack'
                      )),
    from_regime       TEXT,
    to_regime         TEXT NOT NULL,
    detail            TEXT
);
```

신규 함수:

```python
def set_auto_swap_enabled(strategy_id: str, enabled: bool) -> bool:
    """존재하는 라이브 전략의 auto_swap_enabled를 갱신한다. 반환값은 갱신 성공
    여부(해당 id가 없으면 False)."""
    conn = _connect()
    try:
        cursor = conn.execute(
            "UPDATE live_strategies SET auto_swap_enabled = ? WHERE id = ?",
            (1 if enabled else 0, strategy_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def set_active_regime(strategy_id: str, regime: str | None) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE live_strategies SET active_regime = ? WHERE id = ?",
            (regime, strategy_id),
        )
        conn.commit()
    finally:
        conn.close()


def insert_regime_swap_log(
    live_strategy_id: str, market: str, event: str,
    from_regime: str | None, to_regime: str, detail: str | None = None,
) -> str:
    log_id = str(uuid.uuid4())
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO regime_swap_log "
            "(id, live_strategy_id, market, event, from_regime, to_regime, detail) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (log_id, live_strategy_id, market, event, from_regime, to_regime, detail),
        )
        conn.commit()
    finally:
        conn.close()
    return log_id


def list_regime_swap_log(live_strategy_id: str, limit: int = 50) -> list[dict]:
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM regime_swap_log WHERE live_strategy_id = ? "
            "ORDER BY occurred_at DESC LIMIT ?",
            (live_strategy_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
```

`list_active_strategies()`/`get_live_strategy()`는 `SELECT *`라서 새 컬럼이
자동으로 포함된다 — 수정 불필요.

### 2. 판정+실행 로직 — 신규 모듈 `trading/regime_autoswap.py`

daemon.py는 "engine/ 미의존" 원칙을 갖고 있지만(무거운 backtrader/lightgbm
회피가 취지), `engine/regime_adx.py`는 순수 pandas 구현으로 이미 가볍다는
점을 확인했다(사용자 승인 — 이 모듈은 예외로 daemon이 직접 import한다).

```python
"""
trading/regime_autoswap.py

daemon 장세 자동 스왑 루프(4단계)의 판정+실행 로직. daemon.py는 10분마다
process_autoswap_tick()만 호출하는 얇은 래퍼다. backend/main.py의 수동
"전략 교체" 엔드포인트도 determine_target_regime()을 재사용해 auto_swap_enabled인
전략의 active_regime을 수동 교체 시점에 stamp한다(설계 스펙 "수동 개입 연동"
절 참고 — 자동 스위치를 켜둔 채 수동 개입해도 automation이 다음 실제
장세변화 전까지 되돌리지 않게 하기 위함).

engine.regime_adx(순수 pandas, backtrader 미사용)를 직접 import한다 —
daemon.py의 "engine/ 미의존" 원칙은 무거운 backtrader/lightgbm 의존성 회피가
취지이므로 이 가벼운 모듈은 예외로 둔다(설계 문서 결정, 사용자 승인).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import trading.db as db
from engine.regime_adx import classify_regime, compute_adx_di
from upbit_data_service import get_candles

logger = logging.getLogger(__name__)

REGIME_TIMEFRAME = "minutes60"
CONFIRM_BAR_COUNT = 3
FALLBACK_REGIME = "기본"
# ADX(14) 워밍업(약 28봉)에 여유를 둔 값 — backend/regime_adx_service.py의
# OVERVIEW_LOOKBACK_BARS와 동일한 근거(수렴 확보).
LOOKBACK_HOURS = 200


def determine_target_regime(market: str) -> str:
    """market의 1시간봉 기준 현재 확정 장세를 반환한다. 최근 CONFIRM_BAR_COUNT개
    봉의 라벨이 전부 같고 None이 아니면 그 라벨을, 아니면(끈백질 방지 조건
    미충족/데이터부족/미분류) FALLBACK_REGIME("기본")을 반환한다. 항상 4개
    라벨 중 하나를 반환한다(None을 반환하지 않음 — 호출부가 라이브러리의
    "기본" 매핑으로 항상 폴백할 수 있게 하기 위함)."""
    now = datetime.now(timezone.utc)
    df = get_candles(market, REGIME_TIMEFRAME, now - timedelta(hours=LOOKBACK_HOURS), now)
    if len(df) < CONFIRM_BAR_COUNT:
        return FALLBACK_REGIME

    adx_di = compute_adx_di(df)
    recent_labels = [
        classify_regime(row.adx, row.plus_di, row.minus_di)
        for row in adx_di.tail(CONFIRM_BAR_COUNT).itertuples()
    ]
    first = recent_labels[0]
    if first is not None and all(label == first for label in recent_labels):
        return first
    return FALLBACK_REGIME


def process_autoswap_tick() -> None:
    """auto_swap_enabled=1인 모든 활성(running/paused) 라이브 전략을 순회하며
    장세변화를 감지하고 필요하면 교체한다. 전략 단위로 예외를 흡수해 한
    전략의 실패가 나머지 전략 처리를 막지 않게 한다(daemon.py의 기존
    '예외는 로그만 남기고 다음 틱 재시도' 원칙과 동일)."""
    for strategy in db.list_active_strategies():
        if not strategy["auto_swap_enabled"]:
            continue
        try:
            _process_one(strategy)
        except Exception:
            logger.exception("자동스왑 처리 중 예외 발생: strategy_id=%s", strategy["id"])


def _process_one(strategy: dict) -> None:
    strategy_id = strategy["id"]
    market = strategy["market"]
    active_regime = strategy["active_regime"]

    target_regime = determine_target_regime(market)
    if target_regime == active_regime:
        return  # 이미 동기화됨(자동으로 맞췄든 사용자가 수동으로 맞췄든 무관)

    mapping = next(
        (m for m in db.list_regime_strategy_mappings()
         if m["market"] == market and m["regime"] == target_regime),
        None,
    )
    if mapping is None:
        db.insert_regime_swap_log(
            strategy_id, market, "swap_skipped_no_mapping",
            active_regime, target_regime,
            detail=f"{market}/{target_regime} 슬롯이 라이브러리에 없음",
        )
        return

    replaced = db.replace_live_strategy_strategy(
        strategy_id,
        source_run_id=mapping["source_run_id"],
        timeframe=mapping["timeframe"],
        buy_conditions_json=mapping["buy_conditions_json"],
        sell_conditions_json=mapping["sell_conditions_json"],
    )
    if not replaced:
        db.insert_regime_swap_log(
            strategy_id, market, "swap_skipped_open_position",
            active_regime, target_regime,
            detail="오픈 포지션(또는 체결 대기중 매수 주문)이 있어 교체 보류 — 다음 틱 재시도",
        )
        return

    db.set_active_regime(strategy_id, target_regime)
    db.insert_regime_swap_log(
        strategy_id, market, "swap_success",
        active_regime, target_regime,
        detail=f"source_run_id={mapping['source_run_id']}",
    )
```

`replace_live_strategy_strategy`가 이미 열린 포지션/체결대기 매수주문 여부를
자체적으로 확인해 `False`를 반환하므로(기존 구현 재사용), 이 모듈은 그
결과만 보고 로그를 남기면 된다 — 포지션 폴링/대기 로직을 별도로 만들
필요가 없다.

### 3. daemon.py 통합

```python
import trading.regime_autoswap as regime_autoswap

_AUTOSWAP_CHECK_INTERVAL_SEC = 600  # 10분 — 판정 기준이 1시간봉이라 더 자주 볼 필요 없음


async def _run_regime_autoswap_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(regime_autoswap.process_autoswap_tick)
        except Exception:
            logger.exception("자동스왑 틱 처리 중 예외 발생")
        await asyncio.sleep(_AUTOSWAP_CHECK_INTERVAL_SEC)


async def main() -> None:
    logging.basicConfig(...)
    await asyncio.gather(
        _task_set_manager_loop(), _run_ntp_check_loop(), _run_regime_autoswap_loop(),
    )
```

기존 `_task_set_manager_loop`/`_run_ntp_check_loop`와 완전히 독립된 루프라
서로의 실패가 전파되지 않는다(`asyncio.gather`가 이미 이 패턴).

### 4. 수동 개입 연동 (`backend/main.py`)

기존 `POST /api/v1/live-strategies/{id}/replace-strategy`(2026-08-18)가
성공한 직후, 그 전략의 `auto_swap_enabled`가 켜져 있으면 그 시점의
`determine_target_regime()`을 계산해 `active_regime`에 stamp하고
`manual_override_ack` 로그를 남긴다:

```python
@app.post("/api/v1/live-strategies/{strategy_id}/replace-strategy")
def replace_live_strategy_endpoint(strategy_id: str, req: ReplaceLiveStrategyRequest) -> dict:
    strategy = trading_db.get_live_strategy(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="해당 id의 라이브 전략을 찾을 수 없습니다")
    if strategy["status"] == "draft":
        raise HTTPException(status_code=409, detail="draft 상태의 전략은 교체할 수 없습니다")

    config = get_run_config(req.source_run_id)
    if config is None:
        raise HTTPException(status_code=404, detail="해당 run_id의 백테스트 설정을 찾을 수 없습니다")
    _validate_backtest_config_for_market(config, strategy["market"])

    replaced = trading_db.replace_live_strategy_strategy(
        strategy_id,
        source_run_id=req.source_run_id,
        timeframe=config["timeframe"],
        buy_conditions_json=json.dumps(config["buy_conditions"]),
        sell_conditions_json=json.dumps(config["sell_conditions"]),
    )
    if not replaced:
        raise HTTPException(status_code=409, detail="포지션이 열려 있어 교체할 수 없습니다")

    if strategy["auto_swap_enabled"]:
        target_regime = regime_autoswap.determine_target_regime(strategy["market"])
        trading_db.set_active_regime(strategy_id, target_regime)
        trading_db.insert_regime_swap_log(
            strategy_id, strategy["market"], "manual_override_ack",
            strategy["active_regime"], target_regime,
            detail="수동 전략 교체로 인한 자동스왑 상태 동기화",
        )
    return _full_live_strategy_response(strategy_id)
```

`strategy["active_regime"]`은 교체 *이전* 값을 가리키므로(함수 시작부에서
한 번만 조회) `from_regime`으로 정확하다.

신규 엔드포인트 2개:

```python
class SetAutoSwapRequest(BaseModel):
    enabled: bool


@app.patch("/api/v1/live-strategies/{strategy_id}/auto-swap")
def set_auto_swap_endpoint(strategy_id: str, req: SetAutoSwapRequest) -> dict:
    if trading_db.get_live_strategy(strategy_id) is None:
        raise HTTPException(status_code=404, detail="해당 id의 라이브 전략을 찾을 수 없습니다")
    trading_db.set_auto_swap_enabled(strategy_id, req.enabled)
    return _full_live_strategy_response(strategy_id)


@app.get("/api/v1/live-strategies/{strategy_id}/regime-swap-log")
def get_regime_swap_log_endpoint(strategy_id: str) -> list[dict]:
    if trading_db.get_live_strategy(strategy_id) is None:
        raise HTTPException(status_code=404, detail="해당 id의 라이브 전략을 찾을 수 없습니다")
    return trading_db.list_regime_swap_log(strategy_id)
```

`_live_strategy_response()`에 필드 2개 추가:

```python
"auto_swap_enabled": bool(strategy["auto_swap_enabled"]),
"active_regime": strategy["active_regime"],
```

### 5. 프론트엔드 (라이브 전략 관리 페이지)

`frontend/lib/types/liveStrategies.ts`의 `LiveStrategy`에 필드 추가:

```typescript
auto_swap_enabled: boolean;
active_regime: '하락' | '횡보' | '상승' | '기본' | null;
```

`frontend/lib/api/liveStrategies.ts`에 함수 2개 추가(기존
`replaceLiveStrategyStrategy`와 동일 패턴):

```typescript
export function setLiveStrategyAutoSwap(id: string, enabled: boolean): Promise<LiveStrategy> {
  return apiFetch<LiveStrategy>(`/api/v1/live-strategies/${id}/auto-swap`, {
    method: 'PATCH',
    body: JSON.stringify({ enabled }),
  });
}

export interface RegimeSwapLogEntry {
  id: string;
  market: string;
  occurred_at: string;
  event: 'swap_success' | 'swap_skipped_open_position' | 'swap_skipped_no_mapping' | 'manual_override_ack';
  from_regime: string | null;
  to_regime: string;
  detail: string | null;
}

export function getRegimeSwapLog(id: string): Promise<RegimeSwapLogEntry[]> {
  return apiFetch<RegimeSwapLogEntry[]>(`/api/v1/live-strategies/${id}/regime-swap-log`);
}
```

`frontend/components/LiveStrategiesPage.tsx`의 각 전략 카드(기존 "전략
교체" 버튼이 있는 자리 근처)에 추가:

- **자동/수동 토글 스위치**: 켜면 `setLiveStrategyAutoSwap(id, true)` 호출 후
  `refresh()`. 이미 이 페이지에 있는 스위치류 UI 패턴(예: 일시정지 버튼)과
  같은 스타일로.
- **"현재 적용 장세" 배지**: `active_regime`이 `null`이면 "미동기화"(자동
  스위치를 켰지만 아직 첫 틱이 안 돈 상태), 아니면 해당 라벨을 색상 배지로
  (`/regime` 탭에서 이미 쓰는 하락/횡보/상승 색상 매핑을 재사용, "기본"은
  중립색).
- **스왑 이력 접기/펼치기**: 펼치면 `getRegimeSwapLog(id)` 호출, 최근 항목부터
  `occurred_at`/`event`/`from_regime → to_regime`/`detail`을 리스트로 표시.
  `event`별 한글 라벨 매핑(예: `swap_success` → "자동 교체 성공",
  `manual_override_ack` → "수동 개입 반영") 필요.

## 에러 처리 / 엣지 케이스

- **최초 토글 ON**: `active_regime`이 `NULL`이라 `determine_target_regime()`
  결과와 항상 다르다고 판정 → 즉시 동기화 시도(오픈 포지션 있으면 자연히
  대기 후 재시도).
- **"기본" 슬롯조차 비어있음**: `swap_skipped_no_mapping` 로그만 남기고
  `active_regime`은 갱신하지 않는다 — 다음 틱에 다시 시도(사용자가 라이브러리를
  채우면 그 다음 틱에 바로 반영됨).
- **캔들 조회 실패(네트워크 등)**: `_process_one` 밖의 `process_autoswap_tick`
  try/except가 흡수해 로그만 남기고 다음 틱 재시도(daemon.py의 기존
  "예외는 로그만, 다음 틱 재시도" 원칙과 동일).
- **daemon 재시작**: `active_regime`이 DB에 영구 저장되므로 재시작해도
  상태가 유지되어 중복 스왑이 발생하지 않는다.
- **라이브 전략이 `draft`이거나 `deleted_at`이 있는 경우**: `list_active_strategies()`가
  `status IN ('running', 'paused')`만 반환하므로 애초에 순회 대상이 아니다.

## 테스트 전략

- **`tests/test_trading_db.py`**: `set_auto_swap_enabled`/`set_active_regime`/
  `insert_regime_swap_log`/`list_regime_swap_log`(존재하지 않는 id 처리 포함,
  `list_regime_swap_log`의 `ORDER BY occurred_at DESC` 순서 확인).
- **`tests/test_regime_autoswap.py`**(신규):
  - `determine_target_regime`: `upbit_data_service.get_candles`를 monkeypatch해
    (a) 최근 3봉이 전부 같은 라벨 → 그 라벨 반환, (b) 3봉 중 하나라도 다름 →
    "기본", (c) 봉 수 부족 → "기본", (d) 최근 3봉이 전부 같지만 None → "기본"
    각각 검증.
  - `process_autoswap_tick`: `trading.db`를 실제 sqlite(tmp_path)로 붙이고
    `determine_target_regime`을 monkeypatch해 4가지 분기 검증 — ①
    `active_regime`과 동일 → 아무 것도 안 함(로그 없음), ② 매핑 없음 →
    `swap_skipped_no_mapping` 로그, ③ 오픈 포지션 있음 →
    `swap_skipped_open_position` 로그 + `active_regime` 미변경, ④ 정상 교체 →
    `live_strategies` 갱신 + `active_regime` 갱신 + `swap_success` 로그.
  - `auto_swap_enabled=0`인 전략은 `process_autoswap_tick`이 아예 건드리지
    않는지 확인.
- **`tests/test_daemon.py`**: `_run_regime_autoswap_loop`가
  `regime_autoswap.process_autoswap_tick`을 주기적으로 호출하는지(예외 발생
  시에도 루프가 죽지 않는지), `main()`의 `asyncio.gather`에 포함되는지 확인
  (기존 `_run_ntp_check_loop` 테스트 패턴 재사용).
- **`tests/test_backend.py`**: `replace-strategy` 엔드포인트가
  `auto_swap_enabled=1`인 전략에서 `active_regime`/`manual_override_ack` 로그를
  남기는지, `auto_swap_enabled=0`인 전략에서는 안 남기는지. 신규
  `PATCH .../auto-swap`, `GET .../regime-swap-log` 엔드포인트의 정상/404
  케이스.
- `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/ -q` 전체 통과.
- 프론트엔드는 webapp-testing(Playwright)으로 토글 on/off, 배지 표시, 스왑
  이력 펼치기를 브라우저에서 수동 검증(자동화 테스트는 이 프로젝트 관례상
  없음 — 기존 다른 프론트 기능들과 동일).

## 완료 기준

- 라이브 전략 카드에서 자동 스위치를 켜면, daemon이 10분 이내에 현재
  장세에 맞는 라이브러리 매핑으로 전략을 교체한다(오픈 포지션이 있으면
  닫힐 때까지 대기 후 교체).
- 자동 스위치가 켜진 채로 기존 "전략 교체" UI로 수동 교체해도, 실제 장세가
  바뀌기 전까지 daemon이 되돌리지 않는다.
- "기본" 슬롯이 매핑 없음/장세 불확실 두 경우 모두의 폴백으로 동작하고,
  사용자가 수동으로도 "기본" 슬롯을 선택할 수 있다(기존 전략 교체 UI로
  이미 가능 — 이 스펙에서 추가 작업 불필요).
- 모든 자동 판정/교체/실패/수동개입이 `regime_swap_log`에 기록되고 UI에서
  전략별로 조회할 수 있다.
- 신규 유닛 테스트 전부 통과, 기존 테스트 스위트 회귀 없음.
