# 장세 판별 레거시(ML + 추세기반 세그먼트) 전면 삭제 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ML 장세판별 시스템(`/regime` 탭)과 추세기반 세그먼트(`/analysis` 탭의 "추세 기반" 섹션)를 백엔드/엔진/스크립트/테스트/프론트엔드/의존성/서버 파일까지 전부 삭제해, 더 이상 쓰지 않는 `lightgbm`/`hmmlearn`/`scikit-learn` 의존성과 코드를 걷어내고 저장소를 가볍게 유지한다.

**Architecture:** 이 작업은 새 기능을 만드는 게 아니라, 스펙(`docs/superpowers/specs_v1/2026-09-05-regime-legacy-removal-design.md`)에서 이미 안전성이 감사된 코드를 삭제하는 작업이다. 각 태스크가 끝날 때마다 저장소가 항상 import 가능하고 테스트가 통과하는 상태를 유지하도록, "참조를 먼저 끊고 → 파일을 삭제"하는 순서로 진행한다: (1) `backend/main.py`가 삭제 대상 모듈을 더 이상 import하지 않게 정리 → (2) `engine/cache.py`의 관련 스키마/함수 제거 → (3~5) engine/backend/scripts 파일과 그 전용 테스트를 통째로 삭제 → (6~7) 프론트엔드 컴포넌트/라우트/타입/API 함수 삭제 → (8) 의존성·데이터 정리 → (9) 문서화 → (10) 전체 검증.

**Tech Stack:** Python 3 (FastAPI, SQLite), pytest, Next.js/TypeScript(App Router) 프론트엔드, npm.

## Global Constraints

- 세그먼트 탭의 "규모"(size)/"섹터"(sector) 섹션은 완전히 무관한 기능이므로 절대 건드리지 않는다.
- `engine/segment_analysis.py`는 유지 대상 "세그먼트(규모)" 기능이 쓰고 있으므로 파일 자체는 삭제하지 않는다 — `engine/trend_segments.py`가 그 파일의 `_compute_volatility`를 가져다 쓰던 import만 trend_segments.py 삭제와 함께 자연히 없어진다.
- **설계/계획 문서는 삭제하지 않는다**: `docs/superpowers/specs_v1/2026-*-regime-ml-*`, `docs/superpowers/plans_v1/2026-*-regime-ml-*`, `2026-08-16-trend-segment-*` 등 과거 이력 문서는 그대로 남긴다.
- 세션 시작 시점부터 커밋 안 된 채 작업 디렉터리에 있던 참고 문서 3개(`docs/ML_Regime_Switching_Improvement_Plan.md` 등)는 이번 스펙과 무관하므로 건드리지 않는다.
- `trading/`, 그리드서치, 백테스트 엔진(`engine/condition_tree.py` 등 지표 레지스트리), 저널/캘린더는 이번 삭제 대상과 무관 — 손대지 않는다.
- AWS 서버의 `data/regime_ml_models/` 삭제와 `deploy/update.sh` 실행은 이 플랜의 범위 밖이다 — 코드 삭제가 로컬에서 전부 끝나고 사용자 승인을 받은 뒤 별도로 진행한다(오픈 포지션 확인 선행 필수).
- 각 태스크가 끝난 시점에 저장소는 항상 "다음 커밋 가능한" 상태여야 한다 — 삭제 대상 파일을 참조하는 곳을 먼저 정리한 뒤에 그 파일을 지운다.

---

## Task 1: `backend/main.py` 정리 — regime_ml/trend_segments/regime_fact import·헬퍼·엔드포인트 제거

**Files:**
- Modify: `backend/main.py`
- Modify: `tests/test_backend.py`

**Interfaces:**
- Consumes: 없음(이 태스크가 삭제 체인의 첫 단계).
- Produces: `backend/main.py`가 더 이상 `engine.cache`의 `create_regime_ml_job`/`finish_regime_ml_job`/`get_regime_ml_job`/`list_regime_ml_jobs`, `engine.trend_segments`, `backend.regime_ml_service`, `backend.regime_fact_service`, `backend.regime_ml_training_service`를 import하지 않는다 — 이후 태스크(2~4)가 이 모듈/함수들을 안전하게 삭제할 수 있는 전제 조건.

- [ ] **Step 1: `backend/main.py`의 `engine.cache` import에서 regime_ml 관련 4개 이름 제거**

`backend/main.py:21-43`의 import 블록에서 `create_regime_ml_job`, `finish_regime_ml_job`, `get_regime_ml_job`, `list_regime_ml_jobs` 4줄만 제거한다(나머지 이름은 그대로 유지):

```python
from engine.cache import (
    delete_backtest_run,
    delete_grid_search_job,
    finish_grid_search_job,
    get_grid_search_job,
    get_run_config,
    list_backtest_runs,
    list_combined_ranking,
    list_distinct_combos,
    list_grid_search_jobs,
    list_latest_sweep_results,
    list_segment_classification,
    list_sweep_history,
    load_result,
    remove_grid_search_result,
    run_backtest_cached,
    save_result,
    update_backtest_run_metadata,
)
```

- [ ] **Step 2: `engine.trend_segments`, `backend.regime_ml_service`, `backend.regime_fact_service`, `backend.regime_ml_training_service` import 문 삭제**

`backend/main.py:64`의 다음 줄을 삭제:

```python
from engine.trend_segments import EARLIEST_CANDLE_START, get_or_compute_trend_segments
```

`backend/main.py:76-85`의 다음 3개 import 블록을 통째로 삭제:

```python
from backend.regime_ml_service import (
    deploy_model,
    list_trained_models,
    predict_current_ml_regime,
)
from backend.regime_fact_service import compute_fact_regime_segments
from backend.regime_ml_training_service import (
    JobAlreadyRunningError as RegimeMlJobAlreadyRunningError,
    start_job as start_regime_ml_training_job,
)
```

- [ ] **Step 3: `_ml_training_ui_enabled`, `_fail_orphaned_regime_ml_jobs`, `_cleanup_orphaned_regime_ml_jobs`(startup 훅), `_regime_ml_job_response` 헬퍼 삭제**

`backend/main.py:174-179`의 다음 함수를 삭제(다른 곳에서 쓰이지 않는 것을 이미 확인함 — 삭제될 엔드포인트 3곳에서만 참조):

```python
def _ml_training_ui_enabled() -> bool:
    """ENABLE_ML_TRAINING_UI가 로컬 .env에만 true로 설정되어 있어야 한다 — AWS에
    실수로 재학습이 실행되는 사고(과거 grid search가 실제로 겪은 OOM 사고와 같은
    유형)를 막기 위한 게이트. _resolve_allowed_origin()과 같은 이유로 빈 문자열도
    미설정과 동일하게 취급한다."""
    return (os.environ.get("ENABLE_ML_TRAINING_UI") or "").strip().lower() == "true"
```

`backend/main.py:182-203`의 다음 블록을 삭제(startup 훅 `_cleanup_orphaned_regime_ml_jobs`와 `_fail_orphaned_regime_ml_jobs`, `_regime_ml_job_response` 전부 포함 — `_cleanup_orphaned_grid_search_jobs`용 `@app.on_event("startup")`는 그대로 유지):

```python
def _fail_orphaned_regime_ml_jobs() -> None:
    """백엔드가 재시작되면 서브프로세스 stdout 리더 스레드도 함께 사라진다 —
    재기동 시 남아 있는 running 행은 추적 불가능한 고아이므로 실패로 정리한다."""
    for job in list_regime_ml_jobs():
        if job["status"] == "running":
            finish_regime_ml_job(
                job["id"], status="failed",
                error_message="백엔드가 재시작되어 진행률 추적이 끊겼습니다. 모델 목록에서 결과를 확인하세요.",
            )


@app.on_event("startup")
def _cleanup_orphaned_regime_ml_jobs() -> None:
    _fail_orphaned_regime_ml_jobs()


def _regime_ml_job_response(job: dict) -> dict:
    return {
        **job,
        "started_at": _to_utc_iso(job["started_at"]),
        "finished_at": _to_utc_iso(job["finished_at"]) if job["finished_at"] else None,
    }
```

- [ ] **Step 4: `_trend_segment_ohlcv` 헬퍼와 9개 라우트 삭제**

`backend/main.py`에서 `/api/v1/analysis/segments/size` 라우트(유지) 바로 다음, `/api/v1/indicators/catalog` 라우트(유지) 바로 앞에 있는 다음 블록 전체를 삭제한다(`_trend_segment_ohlcv` 헬퍼 + trend-segments 2개 라우트 + regime/fact-segments + regime/ml-* 6개 라우트 = 총 9개 라우트, 그리고 `DeployRegimeMlModelRequest` 모델. Step 3에서 이미 삭제한 `@app.on_event("startup")` 훅 1개를 더하면 이 태스크에서 없어지는 `@app.` 데코레이터는 총 10개):

```python
def _trend_segment_ohlcv(market: str) -> list[dict]:
    df = get_candles(market, "days", EARLIEST_CANDLE_START, datetime.now(timezone.utc))
    return [
        {
            "time": _to_utc_iso(row.candle_time.isoformat()),
            "open": float(row.open), "high": float(row.high),
            "low": float(row.low), "close": float(row.close),
        }
        for row in df.itertuples()
    ]


@app.get("/api/v1/analysis/trend-segments/{market}")
def get_trend_segments_endpoint(market: str) -> dict:
    result = get_or_compute_trend_segments(market)
    return {**result, "ohlcv": _trend_segment_ohlcv(market)}


@app.post("/api/v1/analysis/trend-segments/{market}/refresh")
def refresh_trend_segments_endpoint(market: str) -> dict:
    result = get_or_compute_trend_segments(market, force_refresh=True)
    return {**result, "ohlcv": _trend_segment_ohlcv(market)}


@app.get("/api/v1/regime/fact-segments")
def get_regime_fact_segments_endpoint(
    market: str = Query(...),
    timeframe: str = Query(...),
) -> dict:
    return compute_fact_regime_segments(market, timeframe)


@app.get("/api/v1/regime/ml-current-prediction")
def get_regime_ml_current_prediction_endpoint(
    market: str = Query(...),
    timeframe: str = Query(...),
) -> dict:
    try:
        return predict_current_ml_regime(market, timeframe)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/v1/regime/ml-train-enabled")
def get_regime_ml_train_enabled_endpoint() -> dict:
    return {"enabled": _ml_training_ui_enabled()}


@app.post("/api/v1/regime/ml-train")
def start_regime_ml_train_job_endpoint() -> dict:
    if not _ml_training_ui_enabled():
        raise HTTPException(status_code=403, detail="이 환경에서는 ML 재학습이 비활성화되어 있습니다.")
    try:
        job_id = start_regime_ml_training_job()
    except RegimeMlJobAlreadyRunningError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    job = get_regime_ml_job(job_id)
    assert job is not None
    return _regime_ml_job_response(job)


@app.get("/api/v1/regime/ml-train/jobs")
def list_regime_ml_train_jobs_endpoint() -> list[dict]:
    return [_regime_ml_job_response(j) for j in list_regime_ml_jobs()]


@app.get("/api/v1/regime/ml-models")
def list_regime_ml_models_endpoint() -> list[dict]:
    return list_trained_models()


class DeployRegimeMlModelRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    model_timestamp: str = Field(pattern=r"^regime_ml_\d{8}T\d{6}Z$")


@app.post("/api/v1/regime/ml-deploy")
def deploy_regime_ml_model_endpoint(req: DeployRegimeMlModelRequest) -> dict:
    if not _ml_training_ui_enabled():
        raise HTTPException(status_code=403, detail="이 환경에서는 ML 모델 배포가 비활성화되어 있습니다.")
    try:
        deploy_model(req.model_timestamp)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"deployed": True, "model_timestamp": req.model_timestamp}
```

삭제 후 `_trend_segment_ohlcv`가 쓰던 `get_candles`/`EARLIEST_CANDLE_START`/`datetime`/`timezone` 등은 파일 상단에서 다른 용도로도 쓰이므로 import 문은 그대로 둔다(Step 2에서 `EARLIEST_CANDLE_START` import만 이미 제거됨).

`ConfigDict`와 `Field`는 삭제되는 `DeployRegimeMlModelRequest`에서만 쓰이고 파일 내 다른 곳(`grep -n "ConfigDict\|Field("`)에서는 쓰이지 않으므로, `backend/main.py:19`의 pydantic import도 함께 정리한다(`BaseModel`은 다른 request 모델들이 계속 쓰므로 유지):

```python
from pydantic import BaseModel
```

- [ ] **Step 5: `tests/test_backend.py`에서 대응 테스트 삭제**

파일 상단 import에서 `tests/test_backend.py:12`의 다음 줄 삭제:

```python
import engine.trend_segments as trend_segments_module
```

`tests/test_backend.py:2610-2650`(공백 줄 2651 포함, 바로 다음 `test_get_backtest_runs_filters_by_market` 앞까지)의 다음 두 테스트를 삭제:

```python
def test_get_trend_segments_endpoint_returns_segments_and_ohlcv(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    closes = [100, 130, 90, 140]
    dates = pd.date_range("2026-01-01", periods=len(closes), freq="D", tz="UTC")
    df = pd.DataFrame({
        "candle_time": dates, "open": closes, "high": closes, "low": closes, "close": closes,
    })
    monkeypatch.setattr(trend_segments_module, "get_candles", lambda market, tf, start, end: df)
    monkeypatch.setattr(backend_module, "get_candles", lambda market, tf, start, end: df)
    monkeypatch.setattr(trend_segments_module, "_compute_volatility", lambda market: 0.02)

    resp = client.get("/api/v1/analysis/trend-segments/KRW-BTC")

    assert resp.status_code == 200
    body = resp.json()
    assert body["market"] == "KRW-BTC"
    assert len(body["segments"]) >= 1
    assert len(body["ohlcv"]) == 4
    assert body["ohlcv"][0]["close"] == 100


def test_refresh_trend_segments_endpoint_forces_recompute(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    closes = [100, 130, 90, 140]
    dates = pd.date_range("2026-01-01", periods=len(closes), freq="D", tz="UTC")
    df = pd.DataFrame({
        "candle_time": dates, "open": closes, "high": closes, "low": closes, "close": closes,
    })
    monkeypatch.setattr(trend_segments_module, "get_candles", lambda market, tf, start, end: df)
    monkeypatch.setattr(backend_module, "get_candles", lambda market, tf, start, end: df)
    monkeypatch.setattr(trend_segments_module, "_compute_volatility", lambda market: 0.02)

    resp = client.post("/api/v1/analysis/trend-segments/KRW-BTC/refresh")

    assert resp.status_code == 200
    body = resp.json()
    assert body["market"] == "KRW-BTC"
    assert len(body["segments"]) >= 1
```

`tests/test_backend.py:2883`부터 파일 끝(3135행, `test_deploy_regime_ml_model_rejects_malformed_timestamp`까지)의 모든 테스트를 삭제한다(`test_regime_fact_segments_returns_result`부터 파일 끝까지 통째로 — 바로 앞 `test_replace_live_strategy_returns_409_when_position_open`은 유지).

- [ ] **Step 6: 삭제 확인 및 관련 테스트 실행**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_backend.py -q`
Expected: 전부 PASS, `regime`/`trend_segment` 관련 테스트가 목록에서 사라짐(수집된 테스트 개수가 이전보다 정확히 20개 감소: trend 2개 + regime_fact/ml 18개)

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -c "import backend.main"`
Expected: 에러 없이 조용히 종료(import 시점 깨진 참조가 없다는 확인)

- [ ] **Step 7: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "refactor: main.py에서 regime_ml/trend_segments/regime_fact 참조 제거"
```

---

## Task 2: `engine/cache.py` 정리 — trend_segments/regime_ml_jobs 스키마·함수 제거

**Files:**
- Modify: `engine/cache.py`
- Modify: `tests/test_cache.py`

**Interfaces:**
- Consumes: Task 1이 끝나 `backend/main.py`가 이 함수들을 더 이상 import하지 않는 상태.
- Produces: `engine/cache.py`에 `save_trend_segments`/`list_trend_segments`/`create_regime_ml_job`/`finish_regime_ml_job`/`_row_to_regime_ml_job_dict`/`get_regime_ml_job`/`list_regime_ml_jobs`가 존재하지 않음 — Task 3이 `engine/trend_segments.py`를 삭제할 때, Task 4가 `backend/regime_ml_training_service.py` 등을 삭제할 때 참조가 남아있지 않은 상태.

- [ ] **Step 1: `_SCHEMA`에서 `trend_segments`/`regime_ml_jobs` 테이블 정의 삭제**

`engine/cache.py:98-124`의 다음 두 블록을 삭제(둘 사이 공백 줄 포함):

```python
_SCHEMA += """
CREATE TABLE IF NOT EXISTS trend_segments (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    market            TEXT NOT NULL,
    start_date        TEXT NOT NULL,
    end_date          TEXT NOT NULL,
    days              INTEGER NOT NULL,
    return_pct        REAL NOT NULL,
    trend             TEXT NOT NULL,
    first_half_trend  TEXT NOT NULL,
    second_half_trend TEXT NOT NULL,
    pattern_label     TEXT NOT NULL,
    threshold_pct     REAL NOT NULL,
    computed_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trend_segments_market ON trend_segments(market);
"""

_SCHEMA += """
CREATE TABLE IF NOT EXISTS regime_ml_jobs (
    id            TEXT PRIMARY KEY,
    status        TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    error_message TEXT
);
"""
```

- [ ] **Step 2: `save_trend_segments`/`list_trend_segments` 함수 삭제**

`engine/cache.py:672-711`의 다음 두 함수를 삭제(바로 앞 `list_segment_classification`, 바로 뒤 `create_grid_search_job`은 유지):

```python
def save_trend_segments(market: str, rows: list[dict]) -> None:
    """추세 구간 분류 결과를 market 단위로 교체 저장한다. 히스토리는 보관하지
    않고 해당 market의 최신 1회분만 유지한다."""
    conn = _connect()
    try:
        conn.execute("DELETE FROM trend_segments WHERE market = ?", (market,))
        conn.executemany(
            "INSERT INTO trend_segments "
            "(market, start_date, end_date, days, return_pct, trend, first_half_trend, "
            " second_half_trend, pattern_label, threshold_pct, computed_at) "
            "VALUES (:market, :start_date, :end_date, :days, :return_pct, :trend, "
            " :first_half_trend, :second_half_trend, :pattern_label, :threshold_pct, :computed_at)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def list_trend_segments(market: str) -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT market, start_date, end_date, days, return_pct, trend, first_half_trend, "
            "       second_half_trend, pattern_label, threshold_pct, computed_at "
            "FROM trend_segments WHERE market = ? ORDER BY start_date",
            (market,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "market": r[0], "start_date": r[1], "end_date": r[2], "days": r[3],
            "return_pct": r[4], "trend": r[5], "first_half_trend": r[6],
            "second_half_trend": r[7], "pattern_label": r[8],
            "threshold_pct": r[9], "computed_at": r[10],
        }
        for r in rows
    ]
```

- [ ] **Step 3: `create_regime_ml_job`/`finish_regime_ml_job`/`_row_to_regime_ml_job_dict`/`get_regime_ml_job`/`list_regime_ml_jobs` 삭제**

파일 끝(`engine/cache.py:862-922`, `list_grid_search_jobs` 바로 다음부터 파일 끝까지)의 다음 블록 전체를 삭제:

```python
def create_regime_ml_job(job_id: str) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO regime_ml_jobs (id, status, started_at) "
            "VALUES (?, 'running', datetime('now'))",
            (job_id,),
        )
        conn.commit()
    finally:
        conn.close()


def finish_regime_ml_job(job_id: str, status: str, error_message: str | None = None) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE regime_ml_jobs "
            "SET status = ?, finished_at = datetime('now'), error_message = ? "
            "WHERE id = ?",
            (status, error_message, job_id),
        )
        conn.commit()
    finally:
        conn.close()


def _row_to_regime_ml_job_dict(row: tuple) -> dict:
    job_id, status, started_at, finished_at, error_message = row
    return {
        "id": job_id,
        "status": status,
        "started_at": started_at,
        "finished_at": finished_at,
        "error_message": error_message,
    }


def get_regime_ml_job(job_id: str) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT id, status, started_at, finished_at, error_message "
            "FROM regime_ml_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    finally:
        conn.close()
    return _row_to_regime_ml_job_dict(row) if row else None


def list_regime_ml_jobs() -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, status, started_at, finished_at, error_message "
            "FROM regime_ml_jobs ORDER BY started_at DESC, rowid DESC"
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_regime_ml_job_dict(r) for r in rows]
```

- [ ] **Step 4: `tests/test_cache.py`에서 대응 import·테스트 삭제**

`tests/test_cache.py:12-25`의 import 블록에서 `list_trend_segments`, `save_trend_segments` 2줄만 제거(나머지는 유지):

```python
from engine.cache import (
    list_backtest_runs,
    list_combined_ranking,
    list_distinct_combos,
    list_latest_sweep_results,
    list_runs_missing_candle_count,
    list_segment_classification,
    list_sweep_history,
    save_segment_classification,
    save_sweep_result,
    set_candle_count,
)
```

`tests/test_cache.py:874-946`(바로 다음 `test_list_backtest_runs_filters_by_market` 앞까지)의 다음 3개 테스트를 삭제:

```python
def test_save_and_list_trend_segments_round_trips(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")

    rows = [
        {
            "market": "KRW-BTC",
            "start_date": "2026-01-05",
            "end_date": "2026-03-20",
            "days": 75,
            "return_pct": 42.3,
            "trend": "up",
            "first_half_trend": "up",
            "second_half_trend": "up",
            "pattern_label": "지속형 상승",
            "threshold_pct": 8.5,
            "computed_at": "2026-08-16T00:00:00+00:00",
        },
        {
            "market": "KRW-BTC",
            "start_date": "2026-03-21",
            "end_date": "2026-04-10",
            "days": 20,
            "return_pct": 8.1,
            "trend": "up",
            "first_half_trend": "up",
            "second_half_trend": "sideways",
            "pattern_label": "상승 후 둔화",
            "threshold_pct": 8.5,
            "computed_at": "2026-08-16T00:00:00+00:00",
        },
    ]

    save_trend_segments("KRW-BTC", rows)
    result = list_trend_segments("KRW-BTC")

    assert len(result) == 2
    assert result[0]["start_date"] == "2026-01-05"
    assert result[0]["pattern_label"] == "지속형 상승"
    assert result[1]["pattern_label"] == "상승 후 둔화"


def test_save_trend_segments_replaces_only_that_market(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")

    save_trend_segments("KRW-BTC", [{
        "market": "KRW-BTC", "start_date": "2026-01-01", "end_date": "2026-02-01",
        "days": 31, "return_pct": 10.0, "trend": "up", "first_half_trend": "up",
        "second_half_trend": "up", "pattern_label": "지속형 상승",
        "threshold_pct": 8.0, "computed_at": "2026-08-16T00:00:00+00:00",
    }])
    save_trend_segments("KRW-ETH", [{
        "market": "KRW-ETH", "start_date": "2026-01-01", "end_date": "2026-02-01",
        "days": 31, "return_pct": -10.0, "trend": "down", "first_half_trend": "down",
        "second_half_trend": "down", "pattern_label": "지속형 하락",
        "threshold_pct": 9.0, "computed_at": "2026-08-16T00:00:00+00:00",
    }])
    # KRW-BTC를 다시 저장하면 KRW-ETH 행은 그대로 남아 있어야 한다.
    save_trend_segments("KRW-BTC", [{
        "market": "KRW-BTC", "start_date": "2026-02-01", "end_date": "2026-03-01",
        "days": 28, "return_pct": 5.0, "trend": "sideways", "first_half_trend": "sideways",
        "second_half_trend": "sideways", "pattern_label": "지속형 횡보",
        "threshold_pct": 8.0, "computed_at": "2026-08-16T01:00:00+00:00",
    }])

    assert len(list_trend_segments("KRW-BTC")) == 1
    assert list_trend_segments("KRW-BTC")[0]["start_date"] == "2026-02-01"
    assert len(list_trend_segments("KRW-ETH")) == 1


def test_list_trend_segments_returns_empty_list_when_not_computed(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    assert list_trend_segments("KRW-XRP") == []
```

`tests/test_cache.py:1043`부터 파일 끝(1084행)까지의 다음 4개 테스트를 삭제:

```python
def test_create_and_get_regime_ml_job(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    from engine.cache import create_regime_ml_job, get_regime_ml_job

    create_regime_ml_job("job-1")
    job = get_regime_ml_job("job-1")

    assert job["id"] == "job-1"
    assert job["status"] == "running"
    assert job["finished_at"] is None
    assert job["error_message"] is None


def test_finish_regime_ml_job_updates_status_and_error(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    from engine.cache import create_regime_ml_job, finish_regime_ml_job, get_regime_ml_job

    create_regime_ml_job("job-1")
    finish_regime_ml_job("job-1", status="failed", error_message="boom")

    job = get_regime_ml_job("job-1")
    assert job["status"] == "failed"
    assert job["error_message"] == "boom"
    assert job["finished_at"] is not None


def test_get_regime_ml_job_returns_none_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    from engine.cache import get_regime_ml_job

    assert get_regime_ml_job("does-not-exist") is None


def test_list_regime_ml_jobs_orders_newest_first(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    from engine.cache import create_regime_ml_job, list_regime_ml_jobs

    create_regime_ml_job("job-1")
    create_regime_ml_job("job-2")

    jobs = list_regime_ml_jobs()
    assert [j["id"] for j in jobs] == ["job-2", "job-1"]
```

- [ ] **Step 5: 테스트 실행**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_cache.py tests/test_backend.py -q`
Expected: 전부 PASS

- [ ] **Step 6: 커밋**

```bash
git add engine/cache.py tests/test_cache.py
git commit -m "refactor: cache.py에서 trend_segments/regime_ml_jobs 스키마·함수 제거"
```

---

## Task 3: `engine/` ML·추세세그먼트 파일 12개 + 전용 테스트 12개 삭제

**Files:**
- Delete: `engine/regime_ml_features.py`, `engine/regime_ml_labels.py`, `engine/regime_ml_splits.py`, `engine/regime_ml_metrics.py`, `engine/regime_ml_data.py`, `engine/regime_ml_constants.py`, `engine/regime_ml_hmm.py`, `engine/regime_ml_cross_sectional.py`, `engine/regime_ml_calibration.py`, `engine/regime_math.py`, `engine/regime_features.py`, `engine/trend_segments.py`
- Delete: `tests/test_regime_ml_features.py`, `tests/test_regime_ml_labels.py`, `tests/test_regime_ml_splits.py`, `tests/test_regime_ml_metrics.py`, `tests/test_regime_ml_data.py`, `tests/test_regime_ml_hmm.py`, `tests/test_regime_ml_cross_sectional.py`, `tests/test_regime_ml_calibration.py`, `tests/test_regime_ml_constants_frontend_sync.py`, `tests/test_regime_math.py`, `tests/test_regime_features.py`, `tests/test_trend_segments.py`

**Interfaces:**
- Consumes: Task 1(더 이상 `backend/main.py`가 `engine.trend_segments`를 import하지 않음), Task 2(더 이상 `engine/cache.py`가 이 파일들과 무관해짐 — 애초에 관련 없었음).
- Produces: 이 12개 engine 파일이 저장소에서 사라짐 — Task 4(`backend/regime_ml_service.py` 등)와 Task 8(`requirements.txt`에서 `scikit-learn`/`lightgbm`/`hmmlearn` 제거)의 전제 조건.

- [ ] **Step 1: 삭제 전 마지막 참조 확인**

Run: `grep -rl "regime_ml_features\|regime_ml_labels\|regime_ml_splits\|regime_ml_metrics\|regime_ml_data\|regime_ml_constants\|regime_ml_hmm\|regime_ml_cross_sectional\|regime_ml_calibration\|regime_math\|regime_features\|trend_segments" --include=*.py .`
Expected: 이 12개 모듈 자신의 파일, 이들의 테스트 파일, 그리고 `backend/regime_ml_service.py`/`backend/regime_ml_training_service.py`/`backend/regime_fact_service.py`(Task 4에서 함께 삭제될 파일)만 나와야 한다 — `backend/main.py`, `engine/cache.py`는 더 이상 나오면 안 됨(Task 1·2가 제대로 끝났다는 재확인).

- [ ] **Step 2: engine 파일 12개와 전용 테스트 12개 삭제**

```bash
git rm engine/regime_ml_features.py engine/regime_ml_labels.py engine/regime_ml_splits.py engine/regime_ml_metrics.py engine/regime_ml_data.py engine/regime_ml_constants.py engine/regime_ml_hmm.py engine/regime_ml_cross_sectional.py engine/regime_ml_calibration.py engine/regime_math.py engine/regime_features.py engine/trend_segments.py
git rm tests/test_regime_ml_features.py tests/test_regime_ml_labels.py tests/test_regime_ml_splits.py tests/test_regime_ml_metrics.py tests/test_regime_ml_data.py tests/test_regime_ml_hmm.py tests/test_regime_ml_cross_sectional.py tests/test_regime_ml_calibration.py tests/test_regime_ml_constants_frontend_sync.py tests/test_regime_math.py tests/test_regime_features.py tests/test_trend_segments.py
```

- [ ] **Step 3: 전체 테스트 실행 — import 깨짐 없는지 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/ -q`
Expected: 여전히 남아있는 `backend/regime_ml_service.py` 등 3개 파일이 이번에 삭제한 모듈들을 import하다 실패할 수 있음(정상 — Task 4에서 그 파일들째 삭제할 예정). 그 3개 파일 관련 테스트(`test_regime_ml_service.py`/`test_regime_ml_training_service.py`/`test_regime_fact_service.py`)의 collection error만 나와야 하고, 그 외 다른 테스트는 전부 PASS여야 한다.

- [ ] **Step 4: 커밋**

```bash
git commit -m "refactor: ML 장세판별/추세기반 세그먼트 engine 모듈 및 전용 테스트 삭제"
```

---

## Task 4: `backend/` ML 서비스 파일 3개 + 전용 테스트 3개 삭제

**Files:**
- Delete: `backend/regime_ml_service.py`, `backend/regime_ml_training_service.py`, `backend/regime_fact_service.py`
- Delete: `tests/test_regime_ml_service.py`, `tests/test_regime_ml_training_service.py`, `tests/test_regime_fact_service.py`

**Interfaces:**
- Consumes: Task 1(`backend/main.py`가 더 이상 이 3개 파일을 import하지 않음), Task 3(이 3개 파일이 의존하던 engine 모듈들이 이미 삭제됨 — 이 태스크가 끝나야 저장소가 다시 완전히 일관된 상태가 됨).
- Produces: `backend/`에 regime_ml/regime_fact 관련 파일이 전혀 남지 않음.

- [ ] **Step 1: 삭제 전 마지막 참조 확인**

Run: `grep -rl "regime_ml_service\|regime_ml_training_service\|regime_fact_service" --include=*.py .`
Expected: 이 3개 파일 자신과 각자의 테스트 파일만 나와야 함(`backend/main.py`는 나오면 안 됨).

- [ ] **Step 2: 파일 삭제**

```bash
git rm backend/regime_ml_service.py backend/regime_ml_training_service.py backend/regime_fact_service.py
git rm tests/test_regime_ml_service.py tests/test_regime_ml_training_service.py tests/test_regime_fact_service.py
```

- [ ] **Step 3: 전체 백엔드 테스트 실행**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/ -q`
Expected: 전부 PASS(collection error 없음) — 알려진 무관 flake 1건은 있을 수 있음

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -c "import backend.main"`
Expected: 에러 없이 조용히 종료

- [ ] **Step 4: 커밋**

```bash
git commit -m "refactor: ML 장세판별 backend 서비스 및 전용 테스트 삭제"
```

---

## Task 5: 스크립트 11개 + 전용 테스트 2개 삭제

**주의(Task 3 실행 중 발견된 스펙 감사 누락 보완):** 스펙 F절이 `tests/test_scan_candle_gaps.py`를 누락했다 — `scripts/scan_candle_gaps.py`(이 태스크에서 삭제)의 전용 테스트인데도 F절 목록에 없었다. `engine.regime_ml_constants`가 이미 삭제되어(Task 3) 이 테스트는 현재 collection error 상태이므로, 이 태스크에서 `test_train_regime_ml.py`와 함께 반드시 같이 삭제한다.

**Files:**
- Delete: `scripts/train_regime_ml.py`, `scripts/train_regime_ml_meta_label.py`, `scripts/tune_regime_ml_horizon.py`, `scripts/tune_regime_ml_hyperparams.py`, `scripts/compare_regime_ml_baseline.py`, `scripts/analyze_regime_fact_performance.py`, `scripts/analyze_regime_hmm_fact_performance.py`, `scripts/validate_hmm_feature.py`, `scripts/select_barrier_k.py`, `scripts/push_regime_ml_model.sh`, `scripts/scan_candle_gaps.py`
- Delete: `tests/test_train_regime_ml.py`, `tests/test_scan_candle_gaps.py`

**Interfaces:**
- Consumes: Task 3(이 스크립트들이 import하던 `engine.regime_ml_*` 모듈이 이미 삭제됨 — `scripts/scan_candle_gaps.py`가 쓰던 `engine.regime_ml_constants.TRAINING_MARKETS`도 포함).
- Produces: `scripts/`에 ML 장세판별 관련 스크립트가 전혀 남지 않음.

- [ ] **Step 1: 삭제 전 마지막 참조 확인**

Run: `grep -rln "train_regime_ml\|tune_regime_ml\|compare_regime_ml_baseline\|analyze_regime_fact_performance\|analyze_regime_hmm_fact_performance\|validate_hmm_feature\|select_barrier_k\|push_regime_ml_model\|scan_candle_gaps" --include=*.py --include=*.sh --include=*.md . | grep -v "^docs/"`
Expected: 해당 스크립트 자기 자신과 `tests/test_train_regime_ml.py`만 나와야 함. `deploy/` 아래 스크립트가 `push_regime_ml_model.sh`를 호출하지 않는지 확인.

- [ ] **Step 2: 스크립트 및 테스트 삭제**

```bash
git rm scripts/train_regime_ml.py scripts/train_regime_ml_meta_label.py scripts/tune_regime_ml_horizon.py scripts/tune_regime_ml_hyperparams.py scripts/compare_regime_ml_baseline.py scripts/analyze_regime_fact_performance.py scripts/analyze_regime_hmm_fact_performance.py scripts/validate_hmm_feature.py scripts/select_barrier_k.py scripts/push_regime_ml_model.sh scripts/scan_candle_gaps.py
git rm tests/test_train_regime_ml.py tests/test_scan_candle_gaps.py
```

- [ ] **Step 3: 전체 테스트 실행**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/ -q`
Expected: 전부 PASS

- [ ] **Step 4: 커밋**

```bash
git commit -m "refactor: ML 장세판별 관련 스크립트 및 전용 테스트 삭제"
```

---

## Task 6: 프론트엔드 컴포넌트 9개 + `/regime` 라우트 + NavTabs + AnalysisSidebarView 정리

**Files:**
- Delete: `frontend/components/RegimeDashboard.tsx`, `frontend/components/RegimeFactSegmentView.tsx`, `frontend/components/RegimeFactSegmentTable.tsx`, `frontend/components/RegimeFactChart.tsx`, `frontend/components/RegimeMlCurrentPrediction.tsx`, `frontend/components/RegimeMlAdminPanel.tsx`, `frontend/components/TrendSegmentView.tsx`, `frontend/components/TrendSegmentTable.tsx`, `frontend/components/TrendSegmentChart.tsx`
- Delete: `frontend/app/regime/` (디렉터리 전체 — `page.tsx` 포함)
- Modify: `frontend/components/NavTabs.tsx`
- Modify: `frontend/components/AnalysisSidebarView.tsx`
- Modify: `frontend/app/analysis/page.tsx`

**Interfaces:**
- Consumes: 없음(프론트엔드는 백엔드 삭제와 독립적으로 진행 가능하지만, API 계약이 바뀌므로 Task 1 이후에 진행하는 것을 권장).
- Produces: 이 9개 컴포넌트와 `/regime` 라우트가 저장소에서 사라짐. `AnalysisSidebarView`가 더 이상 `markets` prop을 받지 않음 — Task 7이 `frontend/lib/api/eda.ts`/`frontend/lib/types/eda.ts`에서 관련 함수·타입을 지울 때 이 컴포넌트들이 이미 없으므로 안전.

- [ ] **Step 1: 9개 컴포넌트와 `/regime` 라우트 삭제**

```bash
git rm frontend/components/RegimeDashboard.tsx frontend/components/RegimeFactSegmentView.tsx frontend/components/RegimeFactSegmentTable.tsx frontend/components/RegimeFactChart.tsx frontend/components/RegimeMlCurrentPrediction.tsx frontend/components/RegimeMlAdminPanel.tsx frontend/components/TrendSegmentView.tsx frontend/components/TrendSegmentTable.tsx frontend/components/TrendSegmentChart.tsx
git rm -r frontend/app/regime
```

- [ ] **Step 2: `NavTabs.tsx`에서 "장세 판별" 탭 제거**

`frontend/components/NavTabs.tsx:5`의 import에서 `Activity` 아이콘 제거(다른 곳에서 안 쓰이므로):

```typescript
import { BarChart3, BookOpen, ClipboardList, FlaskConical, Grid3x3, Rocket, Settings } from 'lucide-react';
```

`frontend/components/NavTabs.tsx:10-19`의 `STEPS` 배열에서 `/regime` 항목 삭제:

```typescript
const STEPS = [
  { href: '/', title: '백테스트 설정', icon: Settings },
  { href: '/grid-search', title: 'Grid Search', icon: Grid3x3 },
  { href: '/backtests', title: '백테스트 결과', icon: FlaskConical },
  { href: '/live-strategies', title: '라이브 전략', icon: Rocket },
  { href: '/journal', title: '매매일지', icon: ClipboardList },
  { href: '/analysis', title: '세그먼트', icon: BarChart3 },
  { href: '/guide', title: '지표 가이드', icon: BookOpen },
];
```

- [ ] **Step 3: `AnalysisSidebarView.tsx`에서 "추세 기반" 섹션 및 `markets` prop 제거**

`frontend/components/AnalysisSidebarView.tsx` 전체를 다음으로 교체(import에서 `TrendingUp`/`TrendSegmentView`/`Market` 제거, `Section` 타입에서 `'trend'` 제거, `SECTIONS`에서 trend 항목 제거, `markets` prop 및 그 분기 제거):

```typescript
'use client';

import { useState } from 'react';
import { BarChart3, PieChart } from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import SegmentSizeTable, { type SegmentRow } from '@/components/SegmentSizeTable';

type Section = 'size' | 'sector';

const SECTIONS: { key: Section; label: string; icon: typeof BarChart3 }[] = [
  { key: 'size', label: '세그먼트(규모)', icon: BarChart3 },
  { key: 'sector', label: '세그먼트(섹터)', icon: PieChart },
];

export default function AnalysisSidebarView({
  segmentSizeRows,
}: {
  segmentSizeRows: SegmentRow[];
}) {
  const [section, setSection] = useState<Section>('size');

  return (
    <div className="flex gap-6">
      <nav className="flex w-44 shrink-0 flex-col gap-1">
        {SECTIONS.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            type="button"
            onClick={() => setSection(key)}
            className={
              section === key
                ? 'flex items-center gap-2 rounded-md bg-muted px-3 py-2 text-sm font-medium text-foreground'
                : 'flex items-center gap-2 rounded-md px-3 py-2 text-sm text-muted-foreground hover:bg-muted hover:text-foreground'
            }
          >
            <Icon className="size-4" />
            {label}
          </button>
        ))}
      </nav>

      <div className="min-w-0 flex-1">
        {section === 'size' ? (
          <SegmentSizeTable rows={segmentSizeRows} />
        ) : (
          <Card>
            <CardContent className="pt-4">
              <p className="text-muted-foreground">준비 중입니다.</p>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: `frontend/app/analysis/page.tsx`에서 `markets` prop 전달 제거**

`frontend/app/analysis/page.tsx:25`를 수정(이 페이지에서 `markets` 변수 자체는 `marketByCode` 계산에 여전히 쓰이므로 그대로 유지하고, `AnalysisSidebarView`로 넘기는 prop만 제거):

```typescript
      <AnalysisSidebarView segmentSizeRows={segmentSizeRows} />
```

- [ ] **Step 5: 프론트엔드 빌드로 확인**

Run: `cd frontend && npm run build`
Expected: 성공(삭제된 컴포넌트를 참조하는 곳이 없음 확인). 이 시점에는 `frontend/lib/api/eda.ts`/`frontend/lib/types/eda.ts`에 아직 쓰이지 않는 regime/trend_segment 관련 export가 남아있을 수 있으나, TypeScript는 미사용 export 자체를 에러로 보지 않으므로 빌드는 성공해야 한다.

- [ ] **Step 6: 커밋**

```bash
git add frontend/components/NavTabs.tsx frontend/components/AnalysisSidebarView.tsx frontend/app/analysis/page.tsx
git commit -m "refactor: 장세 판별 탭 및 추세 기반 세그먼트 프론트엔드 컴포넌트 삭제"
```

---

## Task 7: `frontend/lib/api/eda.ts`, `frontend/lib/types/eda.ts` 정리

**주의(Task 7 실행 중 발견된 스펙 감사 누락 보완):** `frontend/components/TrendPatternLegend.tsx`는 스펙 E절의 9개 컴포넌트 목록에 없었지만, `TrendDirection` 타입만 쓰는 순수 추세 패턴 범례 UI였고 Task 6 이후 아무 곳에서도 import되지 않는 고아 파일이었다(과거 `TrendSegmentTable.tsx`/`TrendSegmentView.tsx`가 참조했을 것으로 추정). Task 7 구현 중 빌드 실패로 발견되어 이 태스크 범위에 포함해 함께 삭제했다.

**Files:**
- Modify: `frontend/lib/api/eda.ts`
- Modify: `frontend/lib/types/eda.ts`

**Interfaces:**
- Consumes: Task 6(이 API 함수·타입을 쓰던 컴포넌트가 전부 삭제된 상태 — 이제 안전하게 지울 수 있음).
- Produces: `frontend/lib/api/eda.ts`/`frontend/lib/types/eda.ts`에 regime_ml/trend_segment/regime_fact 관련 함수·타입이 전혀 남지 않음.

- [ ] **Step 1: `frontend/lib/api/eda.ts`에서 타입 import 정리**

`frontend/lib/api/eda.ts:2-23`의 import 블록에서 `MlCurrentPrediction`, `RegimeFactAnalysis`, `RegimeMlJob`, `RegimeMlModelSummary`, `TrendSegmentAnalysis` 5개를 제거:

```typescript
import type {
  BacktestDetail,
  BacktestRunSummary,
  Combo,
  GridSearchEstimate,
  GridSearchIndicatorPoolCatalog,
  GridSearchJob,
  GridSearchJobRequest,
  IndicatorCatalogItem,
  IndicatorPool,
  Market,
  RunBacktestRequest,
  RunBacktestResponse,
  SegmentSizeEntry,
  SweepResult,
  ValidateBacktestResponse,
} from '@/lib/types/eda';
```

- [ ] **Step 2: `frontend/lib/api/eda.ts`에서 함수 6개 삭제**

`frontend/lib/api/eda.ts:104-112`(바로 앞 `getSegmentSizeAnalysis`, 바로 뒤 `createGridSearchJob`은 유지)의 다음 2개 함수 삭제:

```typescript
export function getTrendSegments(market: string): Promise<TrendSegmentAnalysis> {
  return apiFetch<TrendSegmentAnalysis>(`/api/v1/analysis/trend-segments/${market}`);
}

export function refreshTrendSegments(market: string): Promise<TrendSegmentAnalysis> {
  return apiFetch<TrendSegmentAnalysis>(`/api/v1/analysis/trend-segments/${market}/refresh`, {
    method: 'POST',
  });
}
```

파일 끝(`frontend/lib/api/eda.ts:158-197`, 바로 앞 `deleteGridSearchJob`은 유지)의 다음 6개 함수 삭제:

```typescript
export function getRegimeMlCurrentPrediction(params: {
  market: string;
  timeframe: string;
}): Promise<MlCurrentPrediction> {
  const query = new URLSearchParams(params);
  return apiFetch<MlCurrentPrediction>(`/api/v1/regime/ml-current-prediction?${query.toString()}`);
}

export function getRegimeFactSegments(params: {
  market: string;
  timeframe: string;
}): Promise<RegimeFactAnalysis> {
  const query = new URLSearchParams(params);
  return apiFetch<RegimeFactAnalysis>(`/api/v1/regime/fact-segments?${query.toString()}`);
}

export function getRegimeMlTrainEnabled(): Promise<{ enabled: boolean }> {
  return apiFetch<{ enabled: boolean }>('/api/v1/regime/ml-train-enabled');
}

export function startRegimeMlTrainJob(): Promise<RegimeMlJob> {
  return apiFetch<RegimeMlJob>('/api/v1/regime/ml-train', { method: 'POST' });
}

export function getRegimeMlTrainJobs(): Promise<RegimeMlJob[]> {
  return apiFetch<RegimeMlJob[]>('/api/v1/regime/ml-train/jobs');
}

export function getRegimeMlModels(): Promise<RegimeMlModelSummary[]> {
  return apiFetch<RegimeMlModelSummary[]>('/api/v1/regime/ml-models');
}

export function deployRegimeMlModel(
  modelTimestamp: string,
): Promise<{ deployed: boolean; model_timestamp: string }> {
  return apiFetch('/api/v1/regime/ml-deploy', {
    method: 'POST',
    body: JSON.stringify({ model_timestamp: modelTimestamp }),
  });
}
```

- [ ] **Step 3: `frontend/lib/types/eda.ts`에서 타입 정리**

`frontend/lib/types/eda.ts:97-194`(바로 앞 `SegmentSizeEntry` 인터페이스, 바로 뒤 `IndicatorParamDef` 인터페이스는 유지)의 다음 블록 전체를 삭제 — `TrendDirection`, `TrendSegment`, `TrendSegmentAnalysis`, `RegimeCategory`, `RegimeFactBar`, `RegimeFactSegment`, `RegimeFactAnalysis`, `MlFoldPerformance`, `ClassPrecisionRecall`, `MlPooledMetrics`, `MlModelPerformance`, `MlCurrentPrediction`, `RegimeMlJob`, `RegimeMlModelSummary`:

```typescript
export type TrendDirection = 'up' | 'down' | 'sideways';

export interface TrendSegment {
  start_date: string;
  end_date: string;
  days: number;
  return_pct: number;
  trend: TrendDirection;
  first_half_trend: TrendDirection;
  second_half_trend: TrendDirection;
  pattern_label: string;
}

export interface TrendSegmentAnalysis {
  market: string;
  threshold_pct: number;
  computed_at: string;
  segments: TrendSegment[];
  ohlcv: OhlcvPoint[];
}

export type RegimeCategory = '하락' | '하락아님';

export interface RegimeFactBar extends OhlcvPoint {
  label: RegimeCategory | null;
}

export interface RegimeFactSegment {
  start: string;
  end: string;
  label: RegimeCategory;
  bar_count: number;
}

export interface RegimeFactAnalysis {
  market: string;
  timeframe: string;
  bars: RegimeFactBar[];
  segments: RegimeFactSegment[];
}

export interface MlFoldPerformance {
  fold_index: number;
  n_train: number;
  n_test: number;
  // 레거시(5단계) 모델 전용
  correlation?: number | null;
  // 신규(분류) 모델 전용
  macro_f1?: number | null;
  weighted_kappa?: number | null;
}

export interface ClassPrecisionRecall {
  precision: number | null;
  recall: number | null;
}

export interface MlPooledMetrics {
  n: number;
  macro_f1: number | null;
  weighted_kappa: number | null;
  confusion: Record<RegimeCategory, Record<RegimeCategory, number>>;
  class_precision_recall: Record<RegimeCategory, ClassPrecisionRecall>;
}

export interface MlModelPerformance {
  folds: MlFoldPerformance[];
  // 레거시(5단계) 모델 전용
  pooled_correlation?: number | null;
  pooled_hit_rate?: Record<string, number | null>;
  // 신규(분류) 모델 전용
  pooled?: MlPooledMetrics;
  per_market?: Record<string, MlPooledMetrics>;
}

export interface MlCurrentPrediction {
  predicted_category: RegimeCategory;
  probs: Record<RegimeCategory, number>;
  bar_time: string;
  model_trained_at: string;
  model_fold_index: number;
  model_performance: MlModelPerformance | null;
}

export interface RegimeMlJob {
  id: string;
  status: 'running' | 'completed' | 'failed';
  started_at: string;
  finished_at: string | null;
  error_message: string | null;
}

export interface RegimeMlModelSummary {
  model_timestamp: string;
  trained_at: string;
  performance: MlModelPerformance | null;
  is_deployed: boolean;
}
```

- [ ] **Step 4: 프론트엔드 빌드 및 타입체크**

Run: `cd frontend && npm run build`
Expected: 성공(타입 에러 없음 — 삭제된 타입/함수를 참조하는 곳이 전혀 없다는 최종 확인)

- [ ] **Step 5: 커밋**

```bash
git add frontend/lib/api/eda.ts frontend/lib/types/eda.ts
git commit -m "refactor: eda.ts API/타입에서 regime_ml/trend_segment 관련 정의 삭제"
```

---

## Task 8: 의존성 및 로컬 데이터 정리

**Files:**
- Modify: `requirements.txt`
- Delete: `data/regime_ml_models/` (디렉터리 전체)

**Interfaces:**
- Consumes: Task 3, 4, 5(코드에서 `lightgbm`/`scikit-learn`/`hmmlearn`을 import하는 곳이 전부 삭제된 상태).
- Produces: `requirements.txt`에 이 3개 패키지가 없음. 로컬 `data/regime_ml_models/`가 사라짐.

- [ ] **Step 1: 삭제 전 마지막 참조 확인**

Run: `grep -rln "^import sklearn\|^from sklearn\|^import lightgbm\|^from lightgbm\|^import hmmlearn\|^from hmmlearn" --include=*.py .`
Expected: 아무 결과도 없어야 함(이번 세션에서 삭제한 파일들 외에는 애초에 사용처가 없었음을 재확인).

- [ ] **Step 2: `requirements.txt`에서 3줄 삭제**

`requirements.txt:12-14`의 다음 3줄 삭제:

```
lightgbm>=4.0,<4.6
scikit-learn>=1.3
hmmlearn>=0.3,<0.4
```

- [ ] **Step 3: 로컬 데이터 디렉터리 삭제**

```bash
git rm -r data/regime_ml_models
```

(`data/`가 `.gitignore` 대상이라 git에 추적되지 않는 경우 `rm -rf data/regime_ml_models`로 대체)

- [ ] **Step 4: 전체 테스트 재실행**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/ -q`
Expected: 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add requirements.txt
git commit -m "chore: lightgbm/scikit-learn/hmmlearn 의존성 및 로컬 ML 모델 데이터 삭제"
```

(`data/regime_ml_models`가 git 추적 대상이 아니었다면 `requirements.txt`만 커밋에 포함된다 — 정상)

---

## Task 9: 문서화 — `docs/regime-ml-backlog.md`에 폐기 요약 추가

**Files:**
- Modify: `docs/regime-ml-backlog.md`

**Interfaces:**
- Consumes: 이 플랜의 전체 삭제 작업이 완료된 상태(Task 1~8).
- Produces: 백로그 문서 맨 위에 이 세션의 결론(ML 장세판별 전체 폐기, 코드 삭제 완료, 다음은 ADX 기반 규칙 판별로 전면 피벗)이 기록됨.

- [ ] **Step 1: 문서 맨 위에 새 절 추가**

`docs/regime-ml-backlog.md:1`(현재 제목 `# 장세 판별 ML — 잔여 작업 백로그 (2026-09-05 갱신 11)`) 바로 다음 줄에 아래 내용을 삽입한다(기존 본문은 그대로 보존):

```markdown

## [2026-09-05] 이 방향(지도학습 ML 장세판별) 전체 폐기 — 코드 삭제 완료

피처/모델/horizon/메타레이블링/HMM 5개 방향이 전부 미래 장세 예측에
실패해, "미래를 예측하지 말고 현재 장세만 규칙기반(ADX+방향지표)으로
판별해 코인별 3개 전략(하락/횡보/상승용) 중 하나로 자동 전환"하는
방향으로 전면 피벗했다. ML 장세판별(`/regime` 탭)과 추세기반
세그먼트(`/analysis` 탭의 "추세 기반" 섹션)를 백엔드/엔진/스크립트/
테스트/프론트엔드/의존성(`lightgbm`/`scikit-learn`/`hmmlearn`)까지 전부
삭제했다. 설계 스펙:
[2026-09-05-regime-legacy-removal-design.md](superpowers/specs_v1/2026-09-05-regime-legacy-removal-design.md).

아래는 이 폐기 이전까지의 라운드 기록(이력으로 보존).
```

- [ ] **Step 2: 커밋**

```bash
git add docs/regime-ml-backlog.md
git commit -m "docs: ML 장세판별 전면 폐기 및 코드 삭제 완료 기록"
```

---

## Task 10: 최종 통합 검증

**Files:** 없음(코드 변경 없음, 검증만 수행)

**Interfaces:**
- Consumes: Task 1~9 전체 완료 상태.
- Produces: 스펙의 "검증 계획" 4개 항목 중 로컬에서 확인 가능한 3개를 통과시킨 최종 확인. AWS 배포는 범위 밖(사용자 승인 후 별도 진행).

- [ ] **Step 1: 전체 pytest 스위트 실행**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/ -q`
Expected: 전부 PASS(스펙에 명시된 "알려진 무관 flake 1건" 제외)

- [ ] **Step 2: 프론트엔드 빌드**

Run: `cd frontend && npm run build`
Expected: 성공, 타입 에러 없음

- [ ] **Step 3: uvicorn 부팅 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -c "from backend.main import app; print('OK', len(app.routes))"`
Expected: `OK <라우트 개수>` 출력, 에러 없음 — import 시점에 죽는 실수가 없다는 확인. 라우트 개수가 삭제 전(main.py에 `@app.` 데코레이터가 48개 있던 시점) 대비 10개(Task 1에서 삭제한 9개 라우트 + startup 훅 1개) 줄어들었는지(→ 38개) 눈으로 확인.

**[최종리뷰 반영] 이 태스크의 "총 12개 라우트" 표기는 오기였다(실제로는 라우트 9개 + `DeployRegimeMlModelRequest` 모델 + `_trend_segment_ohlcv` 헬퍼를 합쳐 12개 항목을 삭제한 것을 "라우트 12개"로 잘못 요약함). 실행 시점에 `@app.` 데코레이터 48→38(10개 감소, 라우트 9개 + startup 훅 1개)로 정확히 검증됨 — 위 두 단락은 이 최종리뷰 이후 바로잡은 내용이다.

- [ ] **Step 4: 삭제 대상 식별자 전역 재검색 — 잔여물 없음 확인**

Run: `grep -rln "regime_ml\|trend_segment\|RegimeCategory\|RegimeFactAnalysis\|RegimeMlJob\|MlCurrentPrediction\|MlModelPerformance" --include=*.py --include=*.ts --include=*.tsx . | grep -v "^docs/" | grep -v "^data/"`
Expected: 아무 결과도 없어야 함(설계/계획 문서, `docs/regime-ml-backlog.md` 자체는 `docs/`로 필터링되어 제외됨).

- [ ] **Step 5: 사용자에게 AWS 배포 여부 확인**

이 플랜은 여기서 종료. AWS 서버의 `data/regime_ml_models/` 삭제와 `deploy/update.sh` 실행(요구사항 목록이 줄어 배포 시간 단축)은 사용자 승인을 받은 뒤 [[upbit-v1-deploy-check-open-positions-first]] 원칙대로 오픈 포지션 확인을 선행하고 별도로 진행한다 — 이 플랜의 커밋만으로는 로컬 저장소만 정리된 상태.
