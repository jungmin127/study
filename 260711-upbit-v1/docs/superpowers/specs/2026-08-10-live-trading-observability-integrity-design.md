# 이월된 운영 가시성/데이터 무결성 항목 — Design Spec

## 배경 및 목표

⑤-1~⑤-4c 각 서브플랜의 최종 리뷰가 "지금 당장 막을 버그는 아니지만 소액이라도 실거래
전에는 처리해야 한다"며 후속 서브플랜으로 넘긴 항목들이다([[upbit-v1-live-trading-roadmap-sequencing]]
1단계의 2번째 항목). ⑤-4c 백로그 8건 + 최종리뷰 추가분이 전부 정리된 지금
([[upbit-v1-realtime-risk-exit-postmortem]]), 소액 실전 테스트 전에 처리해야 할 마지막
관측성/무결성 항목이다.

확인된 5건:
1. `trading/upbit_ws.py`/`trading/upbit_client.py`에 로깅이 전무함 — WS 재연결, REST
   429 재시도/소진이 전부 조용히 일어난다.
2. `signals` 테이블에 UNIQUE 제약이 없다 — daemon 크래시 후 재시작 시 같은 candle을
   중복 기록할 수 있다.
3. `skip_reason`의 값 도메인이 `insert_signal()` 시점엔 `"unknown"` 하나뿐이라, 왜
   판단불가였는지(어느 지표가 비어있었는지) 로그만 봐서는 알 수 없다.
4. `circuit_breaker_state.resumed_at`(UTC)과 `tripped_at`(KST)의 타임스탬프 포맷이
   서로 다르다.
5. `trading/db.py`의 `_connect()`가 호출마다(=거의 모든 CRUD 호출마다) 전체 스키마
   `executescript`를 재실행한다.

소액이라도 실거래 중 문제가 생기면 로그 없이는 원인 파악이 안 되고, DB 무결성이 깨지면
reconciler의 자동 판단(⑤-4c에서 다듬은 self-heal 로직)이 잘못된 전제 위에서 움직이게
된다 — 그래서 소액 테스트 전에 처리한다.

## 범위

**이 스펙에서 다루는 것:** 위 5건. `trading/upbit_ws.py`, `trading/upbit_client.py`,
`trading/db.py`, `trading/signal_engine.py`, `trading/risk_manager.py`,
`engine/condition_tree.py`(헬퍼 1개 추가).

**이 스펙에서 다루지 않는 것:** `trading/daemon.py`/`trading/order_executor.py`/
`trading/position_manager.py`/`trading/reconciler.py` 로직 변경 없음(이 5건 어디에도
해당 모듈을 건드릴 이유가 없다 — 전부 하위 계층의 관측성/데이터 정합성 문제).
`circuit_breaker_state.trading_date`/`today_kst()`는 영업일 경계 목적이라 그대로 KST
유지(결정4에서 다시 언급).

## 핵심 결정

### 결정 1 — `upbit_ws.py`/`upbit_client.py`에 기존 로깅 컨벤션 적용

`trading/daemon.py`/`trading/order_executor.py`가 이미 쓰고 있는 컨벤션을 그대로
따른다: `logging.getLogger(__name__)`, `%s` 지연 포매팅, 한국어 메시지, 예외는
`logger.exception`(스택트레이스 필요) 또는 `logger.warning`/`logger.error`(예상된
실패 경로).

**`trading/upbit_ws.py`** — `stream_ticker()`의 `except (...): pass`가 재연결 사유를
통째로 삼킨다. 예외를 변수로 잡아 로그를 남기되, 매 재연결마다 스팸이 되지 않도록
최소화한다:

```python
except (websockets.exceptions.WebSocketException, OSError, json.JSONDecodeError) as exc:
    logger.warning("ticker WS 연결 끊김, %.1f초 후 재연결: %s", delay, exc)
```

백오프가 최대치(`RECONNECT_MAX_DELAY_SECONDS`)에 도달한 뒤에도 계속 실패하면(연속
장애가 길어지고 있다는 신호) 추가로 한 번 더 눈에 띄게 남긴다:

```python
if delay >= RECONNECT_MAX_DELAY_SECONDS:
    logger.error("ticker WS 재연결이 최대 백오프(%.0f초)에 도달 — 연속 장애 의심", delay)
```

**`trading/upbit_client.py`** — `_request()`의 429 처리 루프와 재시도 소진 지점에 추가:

```python
if resp.status_code == 429:
    logger.warning(
        "업비트 429 재시도 %d/%d: %s %s", attempt + 1, RETRY_ATTEMPTS, method, path,
    )
    await asyncio.sleep(RATE_LIMIT_BACKOFF_SECONDS * (attempt + 1))
    continue
resp.raise_for_status()
return resp.json()
logger.error("업비트 API 호출 실패(429 재시도 소진): %s %s", method, path)
raise UpbitRateLimitError(f"업비트 API 호출 실패 (429 재시도 소진): {method} {path}")
```

성공 요청은 로그하지 않는다(기본 30req/s·주문 8req/s 트래픽에서 매 요청 INFO 로그는
곧바로 노이즈가 된다). `UpbitCredentialsError`는 명확한 메시지를 담아 그대로 던지는
기존 동작 유지(호출부의 폭넓은 예외 처리가 이미 로그로 남긴다).

### 결정 2 — `signals` 테이블에 UNIQUE 제약 + idempotent insert

**문제:** `signal_engine.evaluate_signals()`는 새 candle을 감지하면 `db.insert_signal()`
(buy/sell 각 1행)을 먼저 호출하고, 그다음에 `db.update_live_strategy_last_candle()`로
`last_processed_candle_time`을 갱신한다. 이 둘은 별도의 커밋이다 — 그 사이에 daemon이
죽으면(또는 이번 tick의 `handle_signal_result()` 호출이 실패해도 `evaluate_signals()`
자체는 이미 완주해 있으므로) 재시작 후 같은 candle을 "아직 처리 안 한 candle"로 다시
평가해 `insert_signal()`을 또 호출한다 — 감사 로그(signals)에 같은 candle에 대한 중복
행이 쌓인다.

**변경:** `positions` 스키마 확장 때와 동일한 정책([[upbit-v1-realtime-risk-exit-postmortem]]
참고 — 아직 실거래 데이터가 없는 개발 단계라 마이그레이션 없이 `CREATE TABLE IF NOT EXISTS`에
바로 반영)으로 `signals` 테이블에 제약을 추가한다:

```sql
CREATE TABLE IF NOT EXISTS signals (
    id                      TEXT PRIMARY KEY,
    live_strategy_id        TEXT NOT NULL REFERENCES live_strategies(id),
    signal_type             TEXT NOT NULL,
    candle_time             TEXT NOT NULL,
    indicator_snapshot_json TEXT,
    resulting_order_id      TEXT REFERENCES orders(id),
    skip_reason             TEXT,
    triggered_at            TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(live_strategy_id, signal_type, candle_time)
);
```

`db.insert_signal()`을 `INSERT OR IGNORE`로 바꾸고, 충돌(이미 같은 행 존재)이면 새로
만든 uuid 대신 기존 행의 `id`를 조회해서 반환한다:

```python
def insert_signal(
    live_strategy_id: str, signal_type: str, candle_time: str,
    indicator_snapshot_json: str, skip_reason: str | None = None,
) -> str:
    signal_id = str(uuid.uuid4())
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "INSERT OR IGNORE INTO signals "
            "(id, live_strategy_id, signal_type, candle_time, indicator_snapshot_json, skip_reason) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (signal_id, live_strategy_id, signal_type, candle_time, indicator_snapshot_json, skip_reason),
        )
        if cursor.rowcount == 0:
            existing = conn.execute(
                "SELECT id FROM signals WHERE live_strategy_id = ? AND signal_type = ? AND candle_time = ?",
                (live_strategy_id, signal_type, candle_time),
            ).fetchone()
            signal_id = existing["id"]
        conn.commit()
    finally:
        conn.close()
    return signal_id
```

이렇게 하면 크래시 후 재시작으로 같은 candle이 재평가돼도 예외 없이 기존 signal 행을
그대로 이어받는다 — `evaluate_signals()`는 정상적으로
`update_live_strategy_last_candle()`까지 도달해 다음 tick부터 정상 진행한다. 실제
주문 중복 실행은 `order_executor.handle_signal_result()`의 기존 포지션 상태 가드(매수는
`position is None`일 때만, 매도는 `position is not None`일 때만)가 대부분 막아준다 —
이 결정은 그 위에 감사 로그 자체의 무결성을 더하는 것이다.

### 결정 3 — `skip_reason`에 미확보 지표명을 포함한다

**문제:** `engine/condition_tree.eval_group_values()`는 조건 트리의 리프 지표값이
`values`에 없거나 NaN이면 그 리프를 "unknown"으로 취급해 평가에서 제외하고, 최상위까지
전부 unknown이면 `None`을 반환한다(결정 스펙 8, 외부데이터 지연/실패 대비). 지금
`signal_engine.py`는 이 `None`을 받으면 그냥 `skip_reason="unknown"`으로만 기록한다 —
FUNDING_RATE가 지연된 건지 KOREA_PREMIUM이 실패한 건지 로그만 봐서는 알 수 없다.

**변경:** `engine/condition_tree.py`에 기존 `find_unknown_indicators()`(팩토리 미등록
지표 탐지용)와 나란히 새 헬퍼를 추가한다 — 조건 트리를 순회하며 `values`에서
`None`/NaN인 리프 지표명을 수집한다:

```python
def find_indicators_with_missing_values(group: dict, values: dict[str, float | None]) -> list[str]:
    """조건 트리의 리프 지표 중 values에 값이 없거나 NaN인 지표명을 수집한다(중복 제거,
    정렬). eval_group_values()가 왜 None을 반환했는지 운영자가 skip_reason만 보고
    파악할 수 있게 한다(HOLDING_PERIOD_BARS/POSITION_RELATIVE_INDICATORS는 values를
    안 쓰므로 대상에서 제외)."""
    missing = set()
    for block in collect_blocks(group):
        name = block["indicator"]
        if name == "HOLDING_PERIOD_BARS" or name in POSITION_RELATIVE_INDICATORS:
            continue
        key = indicator_key(name, block.get("params", {}))
        value = values.get(key)
        if value is None or value != value:
            missing.add(name)
    return sorted(missing)
```

`signal_engine.evaluate_signals()`가 buy/sell 각각의 결과를 넘겨받은 뒤:

```python
buy_missing = find_indicators_with_missing_values(buy_conditions, values)
sell_missing = find_indicators_with_missing_values(sell_conditions, values)

buy_signal_id = db.insert_signal(
    live_strategy_id, "buy", candle_time_str, snapshot_json,
    skip_reason=f"unknown:{','.join(buy_missing)}" if buy_result is None else None,
)
sell_signal_id = db.insert_signal(
    live_strategy_id, "sell", candle_time_str, snapshot_json,
    skip_reason=f"unknown:{','.join(sell_missing)}" if sell_result is None else None,
)
```

buy/sell 조건 트리가 서로 다른 지표를 쓸 수 있으므로 각각 따로 계산한다. `skip_reason`이
`update_signal_result()`를 통해 나중에 `"circuit_breaker_tripped"`/`"slippage_exceeded"`로
덮어써지는 기존 경로는 그대로 유지(이 결정은 `insert_signal()` 시점의 초기값만
구체화한다).

### 결정 4 — `circuit_breaker_state.tripped_at`을 UTC로 통일한다

**문제:** `trading/risk_manager.py`의 `check_circuit_breaker()`는 `tripped_at`을
`datetime.now(_KST).isoformat()`(예: `2026-08-10T14:30:00+09:00`)로 기록하는데,
`trading/signal_engine.py`가 재개 시 채우는 `resumed_at`은
`datetime.now(timezone.utc).isoformat()`(예: `2026-08-10T05:30:00+00:00`)이다. 둘 다
tz-aware ISO 문자열이라 파싱하면 절대 시각은 정확하지만, 원본 문자열을 그대로
읽는(로그, DB 조회 결과, 향후 ⑥ UX) 운영자 입장에서는 같은 이벤트 계열의 두 필드가
다른 오프셋을 달고 있어 헷갈린다.

**변경:** `trading/*.py` 전역의 다른 모든 타임스탬프 생성 지점(`signals.triggered_at`/
`orders.created_at` 등 SQLite `datetime('now')`, `signal_engine.py`의
`now = datetime.now(timezone.utc)`)이 이미 UTC 컨벤션이므로, `tripped_at`을 그쪽에
맞춘다:

```python
db.upsert_circuit_breaker_state(
    live_strategy_id, trading_date, consecutive_losses, 1, tripped_reason,
    datetime.now(timezone.utc).isoformat(),
)
```

`today_kst()`/`trading_date`(영업일 경계 판단용)는 이 결정과 무관하게 그대로 KST
유지한다 — "몇 시에 트립됐는지"와 "어느 영업일에 속하는지"는 서로 다른 목적이다.

### 결정 5 — `db._connect()`가 프로세스당(정확히는 `DB_PATH`당) 스키마를 1회만 실행하게 한다

**문제:** `_connect()`는 매 호출마다 `conn.executescript(_SCHEMA)`(7개 테이블 전부
`CREATE TABLE IF NOT EXISTS`)를 재실행한다. `trading/db.py`의 거의 모든 함수가
`_connect()`로 새 커넥션을 열고 닫는 패턴이라, 라이브 트레이딩 중에는 이 재실행이
매우 빈번하게(틱마다 여러 번, 여러 전략이면 배수로) 일어난다 — `CREATE TABLE IF NOT
EXISTS`는 존재 여부를 매번 `sqlite_master`에서 확인해야 하므로 순수 오버헤드다.

**발견한 함정:** 단순 전역 `bool` 플래그로 캐싱하면 테스트 스위트가 깨진다 — 거의
모든 테스트가 `monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "trading.db")`로
**매 테스트마다 새 파일 경로**를 준 뒤 바로 `_connect()`를 호출하는 패턴(`_fresh_db`
헬퍼)이라, 전역 bool은 두 번째 테스트부터 새 파일에 스키마를 안 만들게 만든다.

**변경:** `DB_PATH`별로 초기화 여부를 추적하는 모듈 전역 집합을 쓴다:

```python
_initialized_paths: set[Path] = set()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    if DB_PATH not in _initialized_paths:
        conn.executescript(_SCHEMA)
        _initialized_paths.add(DB_PATH)
    return conn
```

운영 환경(고정된 `DB_PATH` 하나)에서는 프로세스 수명 동안 사실상 1회만 스키마가
실행된다. 테스트의 새 tmp_path들은 각자 처음 `_connect()`가 호출될 때 정상적으로
스키마가 만들어져 기존 테스트 동작이 그대로 유지된다(집합이 테스트 세션 동안 계속
자라지만, 모든 키가 서로 다른 파일이라 문제 없음). `PRAGMA` 2줄은 커넥션 단위 설정이라
그대로 매 호출 유지(스키마 재실행과 무관하게 필요).

## 변경 파일

- `trading/upbit_ws.py` — 로깅 추가(결정1).
- `trading/upbit_client.py` — 로깅 추가(결정1).
- `trading/db.py` — `signals`에 UNIQUE 제약(결정2), `insert_signal()`을
  `INSERT OR IGNORE` + 기존 행 재사용으로 변경(결정2), `_connect()`를 경로별 1회
  초기화로 변경(결정5).
- `trading/signal_engine.py` — `skip_reason`에 미확보 지표명 포함(결정3).
- `engine/condition_tree.py` — `find_indicators_with_missing_values()` 신규(결정3).
- `trading/risk_manager.py` — `tripped_at`을 UTC로 변경(결정4).
- `tests/test_upbit_ws.py`(신규 또는 기존), `tests/test_upbit_client.py`,
  `tests/test_trading_db.py`, `tests/test_signal_engine.py`, `tests/test_risk_manager.py`,
  `tests/test_condition_tree.py` — 각 결정에 대응하는 테스트 추가.

## 에러 처리

- 결정1의 로깅은 기존 예외 처리 흐름을 바꾸지 않는다 — 로그만 추가하고 재연결/재시도
  로직 자체는 그대로다.
- 결정2의 `INSERT OR IGNORE`는 실패를 흡수하는 게 아니라 "이미 존재함"이라는 정상
  케이스를 명시적으로 처리하는 것이다 — 그 외 DB 오류(예: FK 위반)는 여전히 그대로
  예외로 전파된다.
- 결정5의 `_initialized_paths`는 프로세스 메모리에만 있는 캐시라 재시작하면 자연히
  리셋된다 — 별도 무효화 로직 불필요.

## 테스트 전략

- **결정1**: `upbit_ws.stream_ticker()`가 예외 시 `logger.warning`을 호출하는지
  `caplog`로 검증. `upbit_client._request()`가 429 재시도마다 `logger.warning`, 소진
  시 `logger.error`를 호출하는지 검증(기존 429 재시도 테스트에 assert 추가 또는 신규
  테스트).
- **결정2**: 같은 `(live_strategy_id, signal_type, candle_time)`으로 `insert_signal()`을
  두 번 호출하면 예외 없이 첫 번째 호출의 `id`를 그대로 반환하는지, `signals` 테이블에
  행이 1개만 남는지 검증. 서로 다른 `signal_type`/`candle_time`은 정상적으로 별도 행이
  되는지도 회귀 확인.
- **결정3**: `find_indicators_with_missing_values()` 단위테스트(일부만 missing, 전부
  missing, 전부 존재, POSITION_RELATIVE_INDICATORS/HOLDING_PERIOD_BARS 제외 확인).
  `evaluate_signals()`가 특정 지표만 NaN인 상황에서 `skip_reason`에 그 지표명만
  포함하는지 통합 테스트.
- **결정4**: `check_circuit_breaker()`가 트립될 때 `tripped_at`이 `+00:00` 오프셋(UTC)로
  기록되는지 검증.
- **결정5**: 같은 `DB_PATH`로 `_connect()`를 여러 번 호출했을 때 스키마가 정상적으로
  존재하는지(멱등성 확인), `_initialized_paths`에 없는 새 경로는 최초 호출 시 스키마가
  만들어지는지 검증. **`_fresh_db`를 쓰는 기존 테스트 스위트 전체가 회귀 없이 통과하는지
  반드시 확인**(이 결정이 가장 광범위한 회귀 위험을 가진 변경).
- 전체 회귀(`python -m pytest -q`) — 특히 `tests/test_trading_db.py`(테스트마다 새
  `DB_PATH`를 쓰는 패턴이 결정5와 직접 상호작용).

## 자기 검토(스펙 완성도)

- **플레이스홀더 없음** — 5개 결정 각각 문제/변경/이유를 코드 스니펫과 함께 명시했다.
- **내부 정합성**: 결정2(UNIQUE)와 결정5(스키마 캐싱)는 둘 다 `trading/db.py`를
  건드리지만 서로 다른 함수(`insert_signal()` vs `_connect()`)라 독립적으로 구현
  가능하다. 결정3(`skip_reason`)은 결정2와 무관하게 `signal_engine.py`/
  `engine/condition_tree.py`에서만 발생한다.
- **범위 경계**: `order_executor.py`/`daemon.py`/`reconciler.py`/`position_manager.py`는
  이 5건 어디에서도 언급되지 않음을 재확인했다 — 실제로 grep으로 이 모듈들이
  `insert_signal`/`_connect`/`tripped_at`을 직접 호출하지 않는지 확인 필요(구현 단계
  플랜에서 재확인).
- **하위호환 확인**: `db.insert_signal()`의 시그니처는 변경 없음(반환 타입도 `str`
  그대로) — 호출부(`signal_engine.py`)만 넘기는 `skip_reason` 값의 내용이 더 구체화될
  뿐이다. `find_indicators_with_missing_values()`는 신규 함수라 기존 코드에 영향 없다.
- **가장 리스크가 큰 변경 식별**: 결정5(`_connect()` 캐싱)가 유일하게 "거의 모든 기존
  테스트가 의존하는 헬퍼"를 건드린다 — 구현 시 가장 먼저 손대고 전체 회귀를 가장
  자주 돌려야 한다.
