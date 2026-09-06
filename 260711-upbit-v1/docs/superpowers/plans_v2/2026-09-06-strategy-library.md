# 전략 라이브러리 UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 코인별로 하락/횡보/상승/기본 4개 전략(백테스트 결과)을 미리 매핑해두는 관리 화면(`/strategy-library`)을 만든다. 4단계(daemon 자동 스왑)가 이 매핑을 읽어 쓰게 될 기반 데이터를 저장/조회/편집한다.

**Architecture:** `trading.db`에 `regime_strategy_library` 테이블을 신설해 `live_strategies`와 동일하게 매핑 저장 시점에 조건을 스냅샷한다. 기존 "전략 교체"(`replace-strategy`) 엔드포인트의 검증 로직을 공용 헬퍼로 추출해 새 매핑 저장 엔드포인트와 함께 쓴다. 프론트는 기존 `StrategySwapDialog`(라이브 전략 교체 다이얼로그)를 공용 `BacktestPickerDialog`로 추출해 재사용하고, 20코인×4슬롯 표 + 현재장세/라이브전략 동기화 상태를 보여주는 새 탭을 만든다.

**Tech Stack:** Python/FastAPI/SQLite(백엔드), Next.js/React/TypeScript(프론트), pytest

## Global Constraints

- 대상 코인은 `engine/regime_adx_constants.MAJOR_MARKETS`(20개, 기존) 그대로 재사용 — 이 플랜에서 수정하지 않음
- 슬롯은 정확히 4가지 문자열: `"하락"`, `"횡보"`, `"상승"`, `"기본"` — `regime_strategy_library` 테이블의 `regime` 컬럼 CHECK 제약과 백엔드 `REGIME_LIBRARY_SLOTS` 튜플 양쪽에서 동일하게 유지
- 매핑은 저장 시점에 `timeframe`/`buy_conditions_json`/`sell_conditions_json`을 스냅샷한다(참조만 저장하지 않음) — `live_strategies`와 동일한 이유(백테스트 결과 삭제/수정에 영향받지 않기 위함)
- 부분 매핑 허용 — 코인당 0~4개 슬롯만 설정돼 있어도 정상
- 백테스트 결과 검증 규칙(기존 `/replace-strategy`와 동일): `strategy_name == "ConditionTreeStrategy"`, market 일치, `timeframe in VALID_TIMEFRAMES`(from `engine.metrics`), 매수/매도 조건이 `is_empty()`가 아님, `find_unknown_indicators()` 결과가 비어있음
- 설계 스펙: `docs/superpowers/specs_v2/2026-09-06-strategy-library-design.md` (이 플랜의 모든 세부사항은 이 스펙에서 파생됨)

---

### Task 1: `trading_db.regime_strategy_library` 테이블 + CRUD 함수

**Files:**
- Modify: `trading/db.py`
- Test: `tests/test_trading_db.py`

**Interfaces:**
- Consumes: 없음(순수 DB 계층)
- Produces:
  - `trading_db.upsert_regime_strategy_mapping(market: str, regime: str, source_run_id: str, timeframe: str, buy_conditions_json: str, sell_conditions_json: str) -> None` — Task 3(API)에서 사용
  - `trading_db.delete_regime_strategy_mapping(market: str, regime: str) -> bool` — Task 3에서 사용
  - `trading_db.list_regime_strategy_mappings() -> list[dict]` (각 dict는 테이블 컬럼 그대로: `market`, `regime`, `source_run_id`, `timeframe`, `buy_conditions_json`, `sell_conditions_json`, `updated_at`) — Task 3에서 사용

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_trading_db.py` 파일 끝에 추가(파일 상단은 `import trading.db as db_module`이므로 아래도 `db_module`을 그대로 쓴다):

```python
def test_upsert_regime_strategy_mapping_inserts_new_row(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "trading.db")

    db_module.upsert_regime_strategy_mapping(
        "KRW-BTC", "상승", source_run_id="run-1", timeframe="minutes60",
        buy_conditions_json="{}", sell_conditions_json="{}",
    )

    rows = db_module.list_regime_strategy_mappings()
    assert len(rows) == 1
    assert rows[0]["market"] == "KRW-BTC"
    assert rows[0]["regime"] == "상승"
    assert rows[0]["source_run_id"] == "run-1"
    assert rows[0]["timeframe"] == "minutes60"


def test_upsert_regime_strategy_mapping_overwrites_existing_slot(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "trading.db")
    db_module.upsert_regime_strategy_mapping(
        "KRW-BTC", "상승", source_run_id="run-1", timeframe="minutes60",
        buy_conditions_json="{}", sell_conditions_json="{}",
    )

    db_module.upsert_regime_strategy_mapping(
        "KRW-BTC", "상승", source_run_id="run-2", timeframe="minutes30",
        buy_conditions_json='{"a":1}', sell_conditions_json='{"b":2}',
    )

    rows = db_module.list_regime_strategy_mappings()
    assert len(rows) == 1
    assert rows[0]["source_run_id"] == "run-2"
    assert rows[0]["timeframe"] == "minutes30"
    assert rows[0]["buy_conditions_json"] == '{"a":1}'


def test_upsert_regime_strategy_mapping_keeps_slots_independent(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "trading.db")
    db_module.upsert_regime_strategy_mapping(
        "KRW-BTC", "상승", source_run_id="run-up", timeframe="minutes60",
        buy_conditions_json="{}", sell_conditions_json="{}",
    )
    db_module.upsert_regime_strategy_mapping(
        "KRW-BTC", "하락", source_run_id="run-down", timeframe="minutes60",
        buy_conditions_json="{}", sell_conditions_json="{}",
    )
    db_module.upsert_regime_strategy_mapping(
        "KRW-ETH", "상승", source_run_id="run-eth-up", timeframe="minutes60",
        buy_conditions_json="{}", sell_conditions_json="{}",
    )

    rows = db_module.list_regime_strategy_mappings()
    assert len(rows) == 3
    keys = {(r["market"], r["regime"], r["source_run_id"]) for r in rows}
    assert keys == {
        ("KRW-BTC", "상승", "run-up"),
        ("KRW-BTC", "하락", "run-down"),
        ("KRW-ETH", "상승", "run-eth-up"),
    }


def test_delete_regime_strategy_mapping_returns_true_when_deleted(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "trading.db")
    db_module.upsert_regime_strategy_mapping(
        "KRW-BTC", "상승", source_run_id="run-1", timeframe="minutes60",
        buy_conditions_json="{}", sell_conditions_json="{}",
    )

    deleted = db_module.delete_regime_strategy_mapping("KRW-BTC", "상승")

    assert deleted is True
    assert db_module.list_regime_strategy_mappings() == []


def test_delete_regime_strategy_mapping_returns_false_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "trading.db")

    deleted = db_module.delete_regime_strategy_mapping("KRW-BTC", "상승")

    assert deleted is False


def test_list_regime_strategy_mappings_returns_empty_list_when_none(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "trading.db")

    assert db_module.list_regime_strategy_mappings() == []
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_trading_db.py -v -k regime_strategy_library`
Expected: FAIL with `AttributeError: module 'trading.db' has no attribute 'upsert_regime_strategy_mapping'`

- [ ] **Step 3: 최소 구현 작성**

`trading/db.py`의 `TABLE_NAMES` 튜플(17~26행)에 마지막 항목으로 추가:

```python
TABLE_NAMES = (
    "live_strategies",
    "positions",
    "orders",
    "signals",
    "daily_performance",
    "circuit_breaker_state",
    "manual_intervention_events",
    "capital_adjustments",
    "regime_strategy_library",
)
```

`_SCHEMA` 문자열(30~147행) 끝, `capital_adjustments` 테이블 정의 바로 다음(147행의 닫는 `"""` 앞)에 추가:

```sql
CREATE TABLE IF NOT EXISTS regime_strategy_library (
    market                TEXT NOT NULL,
    regime                TEXT NOT NULL CHECK (regime IN ('하락', '횡보', '상승', '기본')),
    source_run_id         TEXT NOT NULL,
    timeframe             TEXT NOT NULL,
    buy_conditions_json   TEXT NOT NULL,
    sell_conditions_json  TEXT NOT NULL,
    updated_at            TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (market, regime)
);
```

`replace_live_strategy_strategy` 함수(807행 부근) 바로 다음에 신규 함수 3개를 추가:

```python
def upsert_regime_strategy_mapping(
    market: str,
    regime: str,
    source_run_id: str,
    timeframe: str,
    buy_conditions_json: str,
    sell_conditions_json: str,
) -> None:
    """market+regime 슬롯을 있으면 덮어쓰고 없으면 새로 만든다. live_strategies와
    동일하게 저장 시점에 조건을 스냅샷한다(source_run_id는 표시용 참조일 뿐)."""
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO regime_strategy_library "
            "(market, regime, source_run_id, timeframe, buy_conditions_json, sell_conditions_json, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, datetime('now')) "
            "ON CONFLICT(market, regime) DO UPDATE SET "
            "source_run_id=excluded.source_run_id, timeframe=excluded.timeframe, "
            "buy_conditions_json=excluded.buy_conditions_json, "
            "sell_conditions_json=excluded.sell_conditions_json, updated_at=excluded.updated_at",
            (market, regime, source_run_id, timeframe, buy_conditions_json, sell_conditions_json),
        )
        conn.commit()
    finally:
        conn.close()


def delete_regime_strategy_mapping(market: str, regime: str) -> bool:
    conn = _connect()
    try:
        cursor = conn.execute(
            "DELETE FROM regime_strategy_library WHERE market = ? AND regime = ?",
            (market, regime),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def list_regime_strategy_mappings() -> list[dict]:
    """설정된 슬롯만 반환한다(미설정 슬롯은 행 자체가 없음)."""
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM regime_strategy_library").fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_trading_db.py -v`
Expected: 기존 테스트 전부 PASS + 신규 6개 PASS, 회귀 없음

- [ ] **Step 5: 커밋**

```bash
git add trading/db.py tests/test_trading_db.py
git commit -m "feat: 전략 라이브러리 매핑 테이블+CRUD 함수 추가"
```

---

### Task 2: 백테스트 검증 로직 공용 헬퍼 추출 (순수 리팩터)

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_backend.py`(기존 `test_replace_live_strategy_*` 테스트로 회귀만 확인, 신규 테스트 없음)

**Interfaces:**
- Consumes: `get_run_config`, `VALID_TIMEFRAMES`, `is_empty`, `find_unknown_indicators`(모두 기존, 이미 import됨)
- Produces: `_validate_backtest_config_for_market(config: dict, market: str) -> None`(문제 있으면 `HTTPException` 발생, 정상이면 아무것도 반환하지 않음) — Task 3에서 사용

이 태스크는 동작을 바꾸지 않는 순수 리팩터다. 기존 `replace_live_strategy_endpoint`의 검증 6줄을 헬퍼 함수로 추출하고, 엔드포인트는 그 헬퍼를 호출하도록 바꾼다. 기존 `test_replace_live_strategy_*` 테스트들이 안전망이므로 신규 테스트는 작성하지 않고, 리팩터 전후로 그 테스트들이 계속 통과하는지만 확인한다.

- [ ] **Step 1: 리팩터 전 기준선 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_backend.py -v -k test_replace_live_strategy`
Expected: PASS (9 passed) — 이 결과를 기억해두고 리팩터 후에도 동일해야 한다.

- [ ] **Step 2: 헬퍼 함수 추출**

`backend/main.py`의 `replace_live_strategy_endpoint` 함수(1577행 부근) 바로 앞에 추가:

```python
def _validate_backtest_config_for_market(config: dict, market: str) -> None:
    """백테스트 결과가 실거래(라이브 전략 교체 / 전략 라이브러리 매핑)에 쓰일 수
    있는지 검증한다. config는 get_run_config()의 반환값."""
    if config["strategy_name"] != "ConditionTreeStrategy":
        raise HTTPException(status_code=400, detail="지원하지 않는 백테스트 결과입니다")
    if config["market"] != market:
        raise HTTPException(status_code=400, detail="선택한 백테스트 결과의 마켓이 일치하지 않습니다")
    if config["timeframe"] not in VALID_TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 봉데이터입니다: {config['timeframe']}")
    if is_empty(config["buy_conditions"]) or is_empty(config["sell_conditions"]):
        raise HTTPException(status_code=400, detail="매수/매도 조건이 비어 있는 백테스트 결과입니다")
    unknown = sorted(
        set(find_unknown_indicators(config["buy_conditions"]))
        | set(find_unknown_indicators(config["sell_conditions"]))
    )
    if unknown:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 지표입니다: {', '.join(unknown)}")
```

`replace_live_strategy_endpoint` 본문에서 검증 6줄(`if config["strategy_name"] != ...`부터 `unknown = ...`/`if unknown: ...`까지)을 지우고 그 자리에 한 줄로 교체:

```python
    config = get_run_config(req.source_run_id)
    if config is None:
        raise HTTPException(status_code=404, detail="해당 run_id의 백테스트 설정을 찾을 수 없습니다")
    _validate_backtest_config_for_market(config, strategy["market"])

    replaced = trading_db.replace_live_strategy_strategy(
```

(이 함수의 나머지 부분 — `trading_db.replace_live_strategy_strategy(...)` 호출과 이후 반환 — 은 그대로 둔다.)

- [ ] **Step 3: 회귀 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_backend.py -v -k test_replace_live_strategy`
Expected: PASS (9 passed) — Step 1과 동일한 결과. 하나라도 실패하면 헬퍼 추출 과정에서 로직이 바뀐 것이므로 원본과 대조해 고친다.

- [ ] **Step 4: 커밋**

```bash
git add backend/main.py
git commit -m "refactor: 라이브 전략 교체 검증 로직을 공용 헬퍼로 추출"
```

---

### Task 3: 전략 라이브러리 API 엔드포인트

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_backend.py`

**Interfaces:**
- Consumes: `trading_db.upsert_regime_strategy_mapping`, `trading_db.delete_regime_strategy_mapping`, `trading_db.list_regime_strategy_mappings`(Task 1), `_validate_backtest_config_for_market`(Task 2), `MAJOR_MARKETS`(기존, 이미 import됨), `get_run_config`(기존)
- Produces:
  - `GET /api/v1/regime-strategy-library` → `list[dict]`
  - `PUT /api/v1/regime-strategy-library/{market}/{regime}` body `{"source_run_id": str}` → `dict`
  - `DELETE /api/v1/regime-strategy-library/{market}/{regime}` → `{"deleted": bool}`
  - 이후 프론트 태스크(Task 5)가 이 3개 엔드포인트를 호출한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_backend.py` 파일 끝에 추가:

```python
def test_get_regime_strategy_library_returns_empty_list_initially(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    resp = client.get("/api/v1/regime-strategy-library")

    assert resp.status_code == 200
    assert resp.json() == []


def test_upsert_regime_strategy_mapping_saves_snapshot(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _seed_backtest_run("run-up", "KRW-BTC", "minutes30", _VALID_BUY, _VALID_SELL)

    resp = client.put(
        "/api/v1/regime-strategy-library/KRW-BTC/상승",
        json={"source_run_id": "run-up"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"market": "KRW-BTC", "regime": "상승", "source_run_id": "run-up"}

    list_resp = client.get("/api/v1/regime-strategy-library")
    rows = list_resp.json()
    assert len(rows) == 1
    assert rows[0]["market"] == "KRW-BTC"
    assert rows[0]["regime"] == "상승"
    assert rows[0]["source_run_id"] == "run-up"
    assert rows[0]["timeframe"] == "minutes30"


def test_upsert_regime_strategy_mapping_returns_404_for_missing_run(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    resp = client.put(
        "/api/v1/regime-strategy-library/KRW-BTC/상승",
        json={"source_run_id": "does-not-exist"},
    )

    assert resp.status_code == 404


def test_upsert_regime_strategy_mapping_returns_400_for_market_mismatch(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _seed_backtest_run("eth-run", "KRW-ETH", "minutes30", _VALID_BUY, _VALID_SELL)

    resp = client.put(
        "/api/v1/regime-strategy-library/KRW-BTC/상승",
        json={"source_run_id": "eth-run"},
    )

    assert resp.status_code == 400


def test_upsert_regime_strategy_mapping_returns_400_for_unsupported_market(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _seed_backtest_run("run-1", "KRW-NOTREAL", "minutes30", _VALID_BUY, _VALID_SELL)

    resp = client.put(
        "/api/v1/regime-strategy-library/KRW-NOTREAL/상승",
        json={"source_run_id": "run-1"},
    )

    assert resp.status_code == 400


def test_upsert_regime_strategy_mapping_returns_400_for_unsupported_slot(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _seed_backtest_run("run-1", "KRW-BTC", "minutes30", _VALID_BUY, _VALID_SELL)

    resp = client.put(
        "/api/v1/regime-strategy-library/KRW-BTC/폭등",
        json={"source_run_id": "run-1"},
    )

    assert resp.status_code == 400


def test_upsert_regime_strategy_mapping_overwrites_existing_slot(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _seed_backtest_run("run-1", "KRW-BTC", "minutes30", _VALID_BUY, _VALID_SELL)
    _seed_backtest_run("run-2", "KRW-BTC", "minutes60", _VALID_BUY, _VALID_SELL)
    client.put("/api/v1/regime-strategy-library/KRW-BTC/상승", json={"source_run_id": "run-1"})

    resp = client.put("/api/v1/regime-strategy-library/KRW-BTC/상승", json={"source_run_id": "run-2"})

    assert resp.status_code == 200
    rows = client.get("/api/v1/regime-strategy-library").json()
    assert len(rows) == 1
    assert rows[0]["source_run_id"] == "run-2"
    assert rows[0]["timeframe"] == "minutes60"


def test_delete_regime_strategy_mapping_removes_slot(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _seed_backtest_run("run-1", "KRW-BTC", "minutes30", _VALID_BUY, _VALID_SELL)
    client.put("/api/v1/regime-strategy-library/KRW-BTC/상승", json={"source_run_id": "run-1"})

    resp = client.delete("/api/v1/regime-strategy-library/KRW-BTC/상승")

    assert resp.status_code == 200
    assert resp.json() == {"deleted": True}
    assert client.get("/api/v1/regime-strategy-library").json() == []


def test_delete_regime_strategy_mapping_is_idempotent_when_missing(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    resp = client.delete("/api/v1/regime-strategy-library/KRW-BTC/상승")

    assert resp.status_code == 200
    assert resp.json() == {"deleted": True}
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_backend.py -v -k regime_strategy_library`
Expected: FAIL with 404 Not Found(엔드포인트가 아직 없음)

- [ ] **Step 3: 최소 구현 작성**

`backend/main.py`의 `replace_live_strategy_endpoint` 함수(Task 2에서 리팩터한 버전) 바로 다음에 추가:

```python
REGIME_LIBRARY_SLOTS = ("하락", "횡보", "상승", "기본")


class UpsertRegimeStrategyMappingRequest(BaseModel):
    source_run_id: str


@app.get("/api/v1/regime-strategy-library")
def get_regime_strategy_library_endpoint() -> list[dict]:
    return trading_db.list_regime_strategy_mappings()


@app.put("/api/v1/regime-strategy-library/{market}/{regime}")
def upsert_regime_strategy_mapping_endpoint(
    market: str, regime: str, req: UpsertRegimeStrategyMappingRequest
) -> dict:
    if market not in MAJOR_MARKETS:
        raise HTTPException(status_code=400, detail=f"{market}은(는) 지원하지 않는 마켓입니다.")
    if regime not in REGIME_LIBRARY_SLOTS:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 슬롯입니다: {regime}")

    config = get_run_config(req.source_run_id)
    if config is None:
        raise HTTPException(status_code=404, detail="해당 run_id의 백테스트 설정을 찾을 수 없습니다")
    _validate_backtest_config_for_market(config, market)

    trading_db.upsert_regime_strategy_mapping(
        market,
        regime,
        source_run_id=req.source_run_id,
        timeframe=config["timeframe"],
        buy_conditions_json=json.dumps(config["buy_conditions"]),
        sell_conditions_json=json.dumps(config["sell_conditions"]),
    )
    return {"market": market, "regime": regime, "source_run_id": req.source_run_id}


@app.delete("/api/v1/regime-strategy-library/{market}/{regime}")
def delete_regime_strategy_mapping_endpoint(market: str, regime: str) -> dict:
    if regime not in REGIME_LIBRARY_SLOTS:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 슬롯입니다: {regime}")
    trading_db.delete_regime_strategy_mapping(market, regime)
    return {"deleted": True}
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_backend.py -v -k regime_strategy_library`
Expected: PASS (9 passed)

전체 회귀 확인:
Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/ -q`
Expected: 전부 통과

- [ ] **Step 5: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "feat: 전략 라이브러리 매핑 API 엔드포인트 추가"
```

---

### Task 4: 프론트 타입 + API 함수 + 탭 등록

**Files:**
- Create: `frontend/lib/types/regimeLibrary.ts`
- Create: `frontend/lib/api/regimeLibrary.ts`
- Modify: `frontend/components/NavTabs.tsx`

**Interfaces:**
- Consumes: `apiFetch`(기존, `@/lib/api/client`)
- Produces:
  - 타입 `RegimeLibrarySlot`, `RegimeStrategyMapping` — Task 5, 6에서 사용
  - `getRegimeStrategyLibrary(): Promise<RegimeStrategyMapping[]>`, `upsertRegimeStrategyMapping(market: string, regime: RegimeLibrarySlot, sourceRunId: string): Promise<{market: string; regime: string; source_run_id: string}>`, `deleteRegimeStrategyMapping(market: string, regime: RegimeLibrarySlot): Promise<{deleted: boolean}>` — Task 6에서 사용
  - `/strategy-library` 탭 링크 — Task 6이 채울 라우트

이 태스크는 순수 타입/설정 파일이라 자동화 테스트 대신 타입체크로 검증한다(이 프로젝트의 기존 관례 — Task 5/Task 6의 `tsc --noEmit` 단계와 동일).

- [ ] **Step 1: 타입 정의**

`frontend/lib/types/regimeLibrary.ts` (신규):

```typescript
export type RegimeLibrarySlot = '하락' | '횡보' | '상승' | '기본';

export interface RegimeStrategyMapping {
  market: string;
  regime: RegimeLibrarySlot;
  source_run_id: string;
  timeframe: string;
  updated_at: string;
}
```

- [ ] **Step 2: API 함수 정의**

`frontend/lib/api/regimeLibrary.ts` (신규, `frontend/lib/api/eda.ts`의 `apiFetch` import 패턴을 그대로 따른다 — 상단에서 `import { apiFetch } from '@/lib/api/client';` 형태로 되어 있는지 `eda.ts`를 확인하고 동일하게 맞춘다):

```typescript
import { apiFetch } from '@/lib/api/client';
import type { RegimeLibrarySlot, RegimeStrategyMapping } from '@/lib/types/regimeLibrary';

export function getRegimeStrategyLibrary(): Promise<RegimeStrategyMapping[]> {
  return apiFetch<RegimeStrategyMapping[]>('/api/v1/regime-strategy-library');
}

export function upsertRegimeStrategyMapping(
  market: string,
  regime: RegimeLibrarySlot,
  sourceRunId: string,
): Promise<{ market: string; regime: string; source_run_id: string }> {
  return apiFetch(`/api/v1/regime-strategy-library/${market}/${encodeURIComponent(regime)}`, {
    method: 'PUT',
    body: JSON.stringify({ source_run_id: sourceRunId }),
  });
}

export function deleteRegimeStrategyMapping(
  market: string,
  regime: RegimeLibrarySlot,
): Promise<{ deleted: boolean }> {
  return apiFetch(`/api/v1/regime-strategy-library/${market}/${encodeURIComponent(regime)}`, {
    method: 'DELETE',
  });
}
```

- [ ] **Step 3: 탭 등록**

`frontend/components/NavTabs.tsx`의 import 목록(5행)에 `Library` 아이콘 추가:

```typescript
import { Activity, BarChart3, BookOpen, ClipboardList, FlaskConical, Grid3x3, Library, Rocket, Settings } from 'lucide-react';
```

`STEPS` 배열에서 `{ href: '/regime', title: '장세 판별', icon: Activity }` 바로 다음에 추가:

```typescript
  { href: '/regime', title: '장세 판별', icon: Activity },
  { href: '/strategy-library', title: '전략 라이브러리', icon: Library },
```

- [ ] **Step 4: 타입체크 확인**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음(Task 4가 만드는 파일들은 아직 아무 페이지도 import하지 않으므로 미사용 export일 뿐 — 에러가 아니면 통과. `/strategy-library` 링크는 Task 6에서 실제 라우트가 생기기 전까지 404이지만 타입체크와는 무관)

- [ ] **Step 5: 커밋**

```bash
git add frontend/lib/types/regimeLibrary.ts frontend/lib/api/regimeLibrary.ts frontend/components/NavTabs.tsx
git commit -m "feat: 전략 라이브러리 프론트 타입/API/탭 추가"
```

---

### Task 5: `BacktestPickerDialog` 공용 컴포넌트 추출

**Files:**
- Create: `frontend/components/BacktestPickerDialog.tsx`
- Modify: `frontend/components/LiveStrategiesPage.tsx`

**Interfaces:**
- Consumes: `getBacktestRuns`, `ApiError`(기존, `@/lib/api/eda`, `@/lib/api/client`), `BacktestRunSummary`(기존 타입)
- Produces: `BacktestPickerDialog({ market, title, excludeRunId, trigger, onSelect }): JSX.Element` — Task 6에서 사용

`LiveStrategiesPage.tsx`의 기존 `StrategySwapDialog`(대략 202~330행 부근 — 정확한 끝 줄은 파일을 열어 `</Dialog>`로 끝나는 지점까지 확인)를 그대로 옮기되, "무엇을 선택하면 어떤 함수를 호출하는지"만 `onSelect` prop으로 바깥에서 주입받도록 바꾼다. **동작을 바꾸지 않는 리팩터**이므로 기존 라이브 전략 교체 UX(문구, 후보 제외 로직, 로딩/에러 상태)는 그대로 유지한다.

- [ ] **Step 1: 현재 `StrategySwapDialog` 전체와 `DialogTrigger`의 렌더 방식을 확인**

`frontend/components/LiveStrategiesPage.tsx`에서 `function StrategySwapDialog(`(202행)부터 그 함수가 끝나는 `}`(337행)까지 전체를 읽는다. 이미 확인된 구조:
- state: `open`, `candidates`, `loading`, `loadError`, `selectedRunId`, `submitting`, `submitError`
- `loadCandidates()`: `getBacktestRuns(market)` 호출 후 `strategy.source_run_id`와 같은 `run_id`를 제외하고 `candidates`에 저장
- `handleSubmit()`: `replaceLiveStrategyStrategy(strategy.id, selectedRunId)` 호출 → `onChanged()` → 다이얼로그 닫기
- 후보 목록 렌더링: 각 후보를 라디오 버튼 + `run.title`(없으면 "(제목 없음)") + `returnRateColor(run.return_rate)`로 색칠한 수익률 + `run.description`(있으면)으로 표시, 선택된 항목은 `border-primary bg-muted/50`
- 트리거: `RefreshCw` 아이콘, `aria-label="전략 교체"`, `title="전략 교체"`
- 다이얼로그 제목: `전략 교체 — {marketName}`
- 설명 문구: "같은 코인의 다른 백테스트 결과를 선택하면 시간봉·매수/매도 조건이 그 결과로 교체됩니다. 자금관리 설정과 거래 이력은 그대로 유지됩니다."

`frontend/components/ui/dialog.tsx`도 함께 확인한다: 이 프로젝트의 `DialogTrigger`는 Radix의 `asChild`가 아니라 **Base UI**(`@base-ui/react/dialog`) 기반이라 `render` prop으로 폴리모픽 렌더링을 한다(같은 파일의 `DialogClose`가 `render={<Button variant="ghost" .../>}`로 쓰는 것이 그 예). 즉 트리거를 매개변수화하려면 `<DialogTrigger render={trigger} />` 형태를 써야 하며 `asChild`는 이 코드베이스에 존재하지 않는다.

- [ ] **Step 2: `BacktestPickerDialog.tsx` 작성**

`frontend/components/BacktestPickerDialog.tsx` (신규) — 기존 `StrategySwapDialog`와 동일한 state/로직/문구/후보 렌더링을 그대로 옮기되 다음을 매개변수화한다: (a) 후보 로딩 대상 `market`, (b) 제외할 `excludeRunId`, (c) 다이얼로그 제목 `title`, (d) 안내 문구 `description`, (e) 트리거 요소 `trigger`(`DialogTrigger`의 `render` prop으로 전달), (f) 선택 시 호출할 `onSelect(runId)`(성공하면 다이얼로그가 닫히고, 실패하면 `submitError`에 에러 메시지 표시), (g) 빈 후보/확인 버튼 문구 `emptyText`/`confirmText`(호출부마다 "교체 가능한 백테스트 결과가 없습니다"/"교체" 대 "선택할 수 있는 백테스트 결과가 없습니다"/"저장"처럼 문구가 달라질 수 있으므로):

```typescript
'use client';

import { useState } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger, DialogFooter } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { ApiError } from '@/lib/api/client';
import { getBacktestRuns } from '@/lib/api/eda';
import { returnRateColor } from '@/lib/return-rate-color';
import type { BacktestRunSummary } from '@/lib/types/eda';

export default function BacktestPickerDialog({
  market,
  title,
  description,
  excludeRunId,
  trigger,
  emptyText = '선택할 수 있는 백테스트 결과가 없습니다.',
  confirmText = '확인',
  onSelect,
}: {
  market: string;
  title: string;
  description?: string;
  excludeRunId?: string | null;
  trigger: React.ReactElement;
  emptyText?: string;
  confirmText?: string;
  onSelect: (runId: string) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [candidates, setCandidates] = useState<BacktestRunSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  function closeAndReset() {
    setOpen(false);
    setCandidates([]);
    setSelectedRunId(null);
    setLoadError(null);
    setSubmitError(null);
  }

  async function loadCandidates() {
    setLoading(true);
    setLoadError(null);
    try {
      const runs = await getBacktestRuns(market);
      setCandidates(excludeRunId ? runs.filter((r) => r.run_id !== excludeRunId) : runs);
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : '백테스트 결과를 불러오지 못했습니다.');
    } finally {
      setLoading(false);
    }
  }

  async function handleSubmit() {
    if (!selectedRunId) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      await onSelect(selectedRunId);
      closeAndReset();
    } catch (err) {
      setSubmitError(err instanceof ApiError ? err.message : '저장에 실패했습니다.');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next) {
          setOpen(true);
          loadCandidates();
        } else {
          closeAndReset();
        }
      }}
    >
      <DialogTrigger render={trigger} />
      <DialogContent className="max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3 text-sm">
          {description && <p className="text-xs text-muted-foreground">{description}</p>}
          {loading && <p className="text-muted-foreground">불러오는 중...</p>}
          {loadError && <p className="text-destructive">{loadError}</p>}
          {!loading && !loadError && candidates.length === 0 && (
            <p className="rounded-md bg-muted/50 p-3 text-muted-foreground">{emptyText}</p>
          )}
          {!loading && candidates.length > 0 && (
            <div className="space-y-2">
              {candidates.map((run) => (
                <label
                  key={run.run_id}
                  className={`flex cursor-pointer items-start gap-2 rounded-md border p-3 ${
                    selectedRunId === run.run_id ? 'border-primary bg-muted/50' : 'border-border'
                  }`}
                >
                  <input
                    type="radio"
                    name="backtest-picker-candidate"
                    className="mt-1"
                    checked={selectedRunId === run.run_id}
                    onChange={() => setSelectedRunId(run.run_id)}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate font-medium">
                        {run.title || <span className="text-muted-foreground">(제목 없음)</span>}
                      </span>
                      <span className={returnRateColor(run.return_rate)}>
                        수익률 {run.return_rate?.toFixed(2) ?? '-'}%
                      </span>
                    </div>
                    {run.description && (
                      <p className="mt-0.5 truncate text-xs text-muted-foreground">{run.description}</p>
                    )}
                  </div>
                </label>
              ))}
            </div>
          )}
          {submitError && <p className="text-destructive">{submitError}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={closeAndReset} disabled={submitting}>
            취소
          </Button>
          <Button onClick={handleSubmit} disabled={submitting || !selectedRunId}>
            {confirmText}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```

- [ ] **Step 3: `LiveStrategiesPage.tsx`를 새 컴포넌트로 교체**

`frontend/components/LiveStrategiesPage.tsx`의 `StrategySwapDialog` 함수 전체(202~337행)를 삭제한다. 이 함수를 호출하던 491행 `<StrategySwapDialog strategy={s} marketName={koreanName} onChanged={refresh} />`을 다음으로 교체:

```typescript
<BacktestPickerDialog
  market={s.market}
  title={`전략 교체 — ${koreanName}`}
  description="같은 코인의 다른 백테스트 결과를 선택하면 시간봉·매수/매도 조건이 그 결과로 교체됩니다. 자금관리 설정과 거래 이력은 그대로 유지됩니다."
  excludeRunId={s.source_run_id}
  emptyText="교체 가능한 백테스트 결과가 없습니다."
  confirmText="교체"
  trigger={
    <Button
      type="button"
      variant="outline"
      size="icon-lg"
      aria-label="전략 교체"
      title="전략 교체"
    >
      <RefreshCw />
    </Button>
  }
  onSelect={(runId) => replaceLiveStrategyStrategy(s.id, runId).then(() => refresh())}
/>
```

파일 상단 import에 `import BacktestPickerDialog from '@/components/BacktestPickerDialog';` 추가. `RefreshCw`는 이미 4행에서 import돼 있으므로 그대로 둔다. `buttonVariants`(40행)가 삭제된 `StrategySwapDialog` 외에 154행/570행에서도 여전히 쓰이므로 import는 유지한다. `returnRateColor`(44행)도 611행에서 별도로 쓰이므로 유지한다.

- [ ] **Step 4: 타입체크 + 빌드 확인**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

Run: `cd frontend && npm run build`
Expected: 빌드 성공

- [ ] **Step 5: 브라우저로 회귀 확인**

`/live-strategies` 탭에서 실행 중이거나 중지된 라이브 전략의 "전략 교체" 버튼을 눌러 다이얼로그가 뜨고, 후보 목록이 로드되고, 하나를 선택해 "확인"을 누르면 교체가 정상 반영되는지 수동 확인(Playwright MCP 또는 직접 브라우저).

- [ ] **Step 6: 커밋**

```bash
git add frontend/components/BacktestPickerDialog.tsx frontend/components/LiveStrategiesPage.tsx
git commit -m "refactor: 백테스트 선택 다이얼로그를 공용 컴포넌트로 추출"
```

---

### Task 6: `/strategy-library` 페이지 조립

**Files:**
- Create: `frontend/app/strategy-library/page.tsx`
- Create: `frontend/components/RegimeStrategyLibraryPage.tsx`

**Interfaces:**
- Consumes: `getRegimeStrategyLibrary`, `upsertRegimeStrategyMapping`, `deleteRegimeStrategyMapping`(Task 4), `BacktestPickerDialog`(Task 5), `getRegimeAdxOverview`(기존, `@/lib/api/eda`), `getLiveStrategies`(기존, `@/lib/api/liveStrategies`), `getBacktestRuns`(기존, `@/lib/api/eda`), `MAJOR_MARKETS`/`TIMEFRAME`(기존, `@/lib/constants/regime`), `getMarkets`(기존, 한글명 표시용)
- Produces: `/strategy-library` 라우트(브라우저에서 접근 가능한 최종 화면) — 이후 태스크 없음(Step 5가 이 결과물을 수동 검증)

이 컴포넌트는 순수 화면 조립이라 이 프로젝트 관례상(Task 6/7의 `RegimeAdxOverview` 등과 동일) 자동화 유닛 테스트를 추가하지 않고 타입체크 + 수동 브라우저 검증으로 확인한다.

- [ ] **Step 1: `RegimeStrategyLibraryPage.tsx` 작성**

`frontend/components/RegimeStrategyLibraryPage.tsx` (신규):

```typescript
'use client';

import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import BacktestPickerDialog from '@/components/BacktestPickerDialog';
import { ApiError } from '@/lib/api/client';
import { getBacktestRuns, getMarkets, getRegimeAdxOverview } from '@/lib/api/eda';
import { getLiveStrategies } from '@/lib/api/liveStrategies';
import {
  deleteRegimeStrategyMapping,
  getRegimeStrategyLibrary,
  upsertRegimeStrategyMapping,
} from '@/lib/api/regimeLibrary';
import { MAJOR_MARKETS, TIMEFRAME } from '@/lib/constants/regime';
import type { BacktestRunSummary, Market, RegimeAdxOverviewItem } from '@/lib/types/eda';
import type { LiveStrategy } from '@/lib/types/liveStrategies';
import type { RegimeLibrarySlot, RegimeStrategyMapping } from '@/lib/types/regimeLibrary';

const SLOTS: RegimeLibrarySlot[] = ['하락', '횡보', '상승', '기본'];

const LABEL_BG_CLASS: Record<string, string> = {
  상승: 'bg-[color:var(--regime-surge-up)]/15 border-[color:var(--regime-surge-up)]/40',
  하락: 'bg-[color:var(--regime-surge-down)]/15 border-[color:var(--regime-surge-down)]/40',
  횡보: 'bg-muted border-border',
};

function findMapping(
  mappings: RegimeStrategyMapping[], market: string, regime: RegimeLibrarySlot,
): RegimeStrategyMapping | null {
  return mappings.find((m) => m.market === market && m.regime === regime) ?? null;
}

function currentLiveStrategyFor(strategies: LiveStrategy[], market: string): LiveStrategy | null {
  const active = strategies.filter((s) => s.market === market && (s.status === 'running' || s.status === 'paused'));
  return active.find((s) => s.status === 'running') ?? active[0] ?? null;
}

function syncStatusFor(
  market: string,
  currentLabel: string | null,
  mappings: RegimeStrategyMapping[],
  liveStrategies: LiveStrategy[],
): { text: string; tone: 'ok' | 'warn' | 'muted' } {
  const live = currentLiveStrategyFor(liveStrategies, market);
  if (!live) return { text: '라이브 전략 없음', tone: 'muted' };
  const slot: RegimeLibrarySlot = currentLabel === '상승' || currentLabel === '하락' || currentLabel === '횡보' ? currentLabel : '기본';
  const mapping = findMapping(mappings, market, slot);
  if (!mapping) return { text: '매핑 없음', tone: 'muted' };
  return mapping.source_run_id === live.source_run_id
    ? { text: '동기화됨', tone: 'ok' }
    : { text: '전략 교체 필요', tone: 'warn' };
}

export default function RegimeStrategyLibraryPage() {
  const [mappings, setMappings] = useState<RegimeStrategyMapping[]>([]);
  const [overview, setOverview] = useState<RegimeAdxOverviewItem[]>([]);
  const [liveStrategies, setLiveStrategies] = useState<LiveStrategy[]>([]);
  const [backtestRuns, setBacktestRuns] = useState<BacktestRunSummary[]>([]);
  const [markets, setMarkets] = useState<Market[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  async function loadAll() {
    setLoading(true);
    setError(null);
    try {
      const [mappingsResult, overviewResult, liveResult, runsResult, marketsResult] = await Promise.all([
        getRegimeStrategyLibrary(),
        getRegimeAdxOverview(TIMEFRAME),
        getLiveStrategies(),
        getBacktestRuns(),
        getMarkets(),
      ]);
      setMappings(mappingsResult);
      setOverview(overviewResult);
      setLiveStrategies(liveResult);
      setBacktestRuns(runsResult);
      setMarkets(marketsResult);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '전략 라이브러리를 불러오지 못했습니다.');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadAll();
  }, []);

  if (loading) return <p className="text-muted-foreground">불러오는 중...</p>;
  if (error) return <p className="text-destructive">{error}</p>;

  return (
    <div className="overflow-x-auto rounded-md border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>코인</TableHead>
            <TableHead>현재장세</TableHead>
            <TableHead>라이브전략 상태</TableHead>
            {SLOTS.map((slot) => (
              <TableHead key={slot}>{slot}</TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {MAJOR_MARKETS.map((market) => {
            const koreanName = markets.find((m) => m.market === market)?.korean_name ?? market;
            const overviewItem = overview.find((o) => o.market === market);
            const currentLabel = overviewItem?.label ?? null;
            const sync = syncStatusFor(market, currentLabel, mappings, liveStrategies);

            return (
              <TableRow key={market}>
                <TableCell className="whitespace-nowrap font-medium">{koreanName}</TableCell>
                <TableCell>
                  <span className={`rounded-full border px-2 py-0.5 text-xs ${currentLabel ? LABEL_BG_CLASS[currentLabel] : 'bg-muted border-border'}`}>
                    {currentLabel ?? '미분류'}
                  </span>
                </TableCell>
                <TableCell>
                  <span className={sync.tone === 'ok' ? 'text-[color:var(--regime-surge-up)]' : sync.tone === 'warn' ? 'text-[color:var(--regime-surge-down)]' : 'text-muted-foreground'}>
                    {sync.text}
                  </span>
                </TableCell>
                {SLOTS.map((slot) => {
                  const mapping = findMapping(mappings, market, slot);
                  const run = mapping ? backtestRuns.find((r) => r.run_id === mapping.source_run_id) : null;
                  const summary = mapping ? (run ? (run.title ?? run.run_id) : '삭제된 백테스트 결과') : null;

                  return (
                    <TableCell key={slot} className="min-w-40">
                      {summary && <p className="mb-1 truncate text-xs text-muted-foreground">{summary}</p>}
                      <div className="flex gap-1">
                        <BacktestPickerDialog
                          market={market}
                          title={`${koreanName} — ${slot} 전략 설정`}
                          excludeRunId={mapping?.source_run_id}
                          trigger={
                            <Button variant="outline" size="sm">
                              {mapping ? '변경' : '설정'}
                            </Button>
                          }
                          onSelect={(runId) => upsertRegimeStrategyMapping(market, slot, runId).then(() => loadAll())}
                        />
                        {mapping && (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => deleteRegimeStrategyMapping(market, slot).then(() => loadAll())}
                          >
                            제거
                          </Button>
                        )}
                      </div>
                    </TableCell>
                  );
                })}
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
```

- [ ] **Step 2: 라우트 페이지 작성**

`frontend/app/strategy-library/page.tsx` (신규):

```typescript
import RegimeStrategyLibraryPage from '@/components/RegimeStrategyLibraryPage';

export default function Page() {
  return <RegimeStrategyLibraryPage />;
}
```

- [ ] **Step 3: 타입체크 + 빌드 확인**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

Run: `cd frontend && npm run build`
Expected: 빌드 성공

- [ ] **Step 4: 브라우저로 수동 검증**

개발 서버 실행 후 `/strategy-library`에서:
1. 20개 코인 행이 뜨고 각 행에 현재장세 뱃지, 라이브전략 상태 뱃지가 보이는지
2. 빈 슬롯에서 "설정" 클릭 → 다이얼로그가 해당 마켓의 백테스트 후보를 보여주는지 → 하나 선택 후 "확인" → 셀에 요약과 "변경"/"제거" 버튼이 나타나는지
3. "변경" → 다른 결과 선택 → 요약이 갱신되는지
4. "제거" → 슬롯이 다시 "설정" 버튼으로 돌아가는지
5. `/live-strategies`의 "전략 교체" 다이얼로그가 Task 5 리팩터 이후에도 정상 동작하는지 재확인

- [ ] **Step 5: 커밋**

```bash
git add frontend/app/strategy-library/page.tsx frontend/components/RegimeStrategyLibraryPage.tsx
git commit -m "feat: 전략 라이브러리 화면 조립(/strategy-library)"
```

---

## 완료 기준

- `/strategy-library` 탭에서 20개 코인 × 4슬롯(하락/횡보/상승/기본) 매핑 표가 뜬다
- 빈 슬롯에서 "설정" → 같은 마켓의 백테스트 결과를 골라 저장 → 셀에 반영된다
- 매핑된 슬롯에서 "변경"/"제거"가 정상 동작한다
- 각 행에 현재 ADX 장세와, 라이브 전략이 있으면 그 전략이 매핑과 동기화됐는지 뱃지로 표시된다
- `/live-strategies`의 기존 "전략 교체" 기능이 리팩터 이후에도 회귀 없이 동작한다
- 신규 백엔드 유닛 테스트 전부 통과, 기존 테스트 스위트 회귀 없음
- `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/ -q` 전체 통과, `cd frontend && npm run build` 성공
- 브라우저로 위 흐름 수동 검증
