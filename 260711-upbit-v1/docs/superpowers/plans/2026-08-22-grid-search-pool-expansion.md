# 그리드서치 지표 풀 확장 + 체이닝 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 로컬 그리드서치를 오실레이터 전용에서 7개 지표 카테고리 중 선택 가능하게 확장하고, 이전 결과를 베이스로 AND/OR 결합해 재귀적으로 체이닝하는 2차 이상 grid search를 추가한다.

**Architecture:** `scripts/grid_search.py`의 `OSCILLATOR_SPECS` 하드코딩을 카테고리별 스펙 레지스트리(`INDICATOR_POOL_SPECS`)로 일반화하고, `build_condition_grid()`가 선택된 풀만 순회하도록 확장한다. 체이닝은 베이스 run의 조건을 `engine.cache.get_run_config()`로 읽어와 새 후보 지표 1개씩을 AND/OR로 감싸는 방식으로 구현하며, 매번 새 후보는 1개 지표 풀에서만 뽑으므로 조합 수가 통제된다. `backend/grid_search_service.py`가 서브프로세스 CLI 인자로 풀 선택/체이닝 정보를 전달하고 `grid_search_jobs` 테이블에 기록한다. 프론트는 폼에 지표 풀 선택 섹션을, 이력 표에 체이닝 트리거 버튼과 부모-자식 트리 표시를 추가한다.

**Tech Stack:** Python 3.11 / FastAPI / backtrader / SQLite(engine.cache) / Next.js App Router / TypeScript / shadcn-ui.

**참고 스펙:** `docs/superpowers/specs/2026-08-22-grid-search-pool-expansion-design.md`

## Global Constraints

- 지표 풀 미지정(`indicator_pool=None`) 시 기존 오실레이터 전용 동작과 **완전히 동일**해야 한다 — 기존 테스트(`tests/test_grid_search.py::test_build_condition_grid_combo_counts`, buy=138/sell=150)가 수정 없이 그대로 통과해야 한다.
- "손익"(STOP_LOSS_PCT/TAKE_PROFIT_PCT/HOLDING_PERIOD_BARS, `SELL_ONLY`)은 사용자가 끌 수 있는 카테고리 토글에 포함하지 않는다 — 포지션 청산 메커니즘이라 항상 무조건 매도 조건 풀에 포함된다(기존과 동일).
- 새 지표 임계값 그리드는 `KRW-ETH`/`KRW-XRP`, 1시간봉, `2026-01-01~2026-08-20` 구간에서 실제 계산한 백분위수(p10/p20/p30/p70/p80/p90)를 반올림해 정했다(`FEAR_GREED_CMC`는 표본 구간이 공포 국면에 치우쳐 있어 문헌상 관례값 20/25/30, 65/70/75로 대체). 아래 각 스펙 값은 이 산출 근거를 그대로 반영한 것이므로 임의로 바꾸지 않는다.
- 체이닝 시 저장되는 백테스트 run의 `strategy_params.buy_conditions`/`sell_conditions`에는 항상 베이스+새 조건이 합쳐진 완전한 트리를 저장한다(새 조건만 저장하면 재체이닝 시 베이스 정보가 유실된다).

---

## Task 1: DB 스키마 — grid_search_jobs에 체이닝 컬럼 추가

**Files:**
- Modify: `engine/cache.py:78-96` (스키마), `engine/cache.py:173-189` (`_connect`), `engine/cache.py:698-713` (`create_grid_search_job`), `engine/cache.py:787-837` (`_row_to_grid_search_job_dict`/`get_grid_search_job`/`list_grid_search_jobs`)
- Test: `tests/test_cache.py` (기존 파일이 없으면 새로 생성)

**Interfaces:**
- Produces: `create_grid_search_job(job_id, market, timeframe, capital, start, end, top_n, indicator_pool_json=None, base_run_id=None, combinator=None) -> None`. `get_grid_search_job()`/`list_grid_search_jobs()`가 반환하는 dict에 `indicator_pool`(파싱된 dict|None), `base_run_id`(str|None), `combinator`(str|None) 키 추가.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_cache.py` 파일이 없다면 새로 만든다:

```python
import json

from engine.cache import (
    create_grid_search_job,
    get_grid_search_job,
)


def test_create_grid_search_job_persists_chaining_fields():
    job_id = "test-chain-job-1"
    create_grid_search_job(
        job_id=job_id, market="KRW-ETH", timeframe="minutes60", capital=1_000_000,
        start="2026-01-01", end="2026-02-01", top_n=10,
        indicator_pool_json=json.dumps({"categories": ["추세"], "excluded_indicators": []}),
        base_run_id="base-run-abc",
        combinator="OR",
    )
    job = get_grid_search_job(job_id)
    assert job is not None
    assert job["indicator_pool"] == {"categories": ["추세"], "excluded_indicators": []}
    assert job["base_run_id"] == "base-run-abc"
    assert job["combinator"] == "OR"


def test_create_grid_search_job_without_chaining_fields_defaults_to_none():
    job_id = "test-chain-job-2"
    create_grid_search_job(
        job_id=job_id, market="KRW-ETH", timeframe="minutes60", capital=1_000_000,
        start="2026-01-01", end="2026-02-01", top_n=10,
    )
    job = get_grid_search_job(job_id)
    assert job is not None
    assert job["indicator_pool"] is None
    assert job["base_run_id"] is None
    assert job["combinator"] is None
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_cache.py -v`
Expected: FAIL — `create_grid_search_job() got an unexpected keyword argument 'indicator_pool_json'`

- [ ] **Step 3: 스키마에 컬럼 추가**

`engine/cache.py:79-96`의 `grid_search_jobs` `CREATE TABLE` 문 뒤(테이블 정의 자체는 그대로 두고 — 새 DB 파일은 여기 추가해도 되지만 기존 DB 파일은 `CREATE TABLE IF NOT EXISTS`가 컬럼을 추가해주지 않으므로 `_connect()`의 마이그레이션 블록에 추가한다), `engine/cache.py:184-188`을 다음과 같이 수정:

```python
    try:
        conn.execute("ALTER TABLE backtest_results ADD COLUMN candle_count INTEGER")
    except sqlite3.OperationalError:
        pass
    for column in ("indicator_pool", "base_run_id", "combinator"):
        try:
            conn.execute(f"ALTER TABLE grid_search_jobs ADD COLUMN {column} TEXT")
        except sqlite3.OperationalError:
            pass
    return conn
```

새 DB 파일에서도 `CREATE TABLE IF NOT EXISTS`가 먼저 컬럼 없는 테이블을 만들고 바로 이 `ALTER TABLE`이 추가하므로(같은 `_connect()` 호출 안에서 순차 실행), 신규/기존 DB 모두 동일하게 동작한다.

- [ ] **Step 4: create_grid_search_job/조회 함수 수정**

`engine/cache.py:698-713`:

```python
def create_grid_search_job(
    job_id: str, market: str, timeframe: str, capital: float,
    start: str, end: str, top_n: int,
    indicator_pool_json: str | None = None,
    base_run_id: str | None = None,
    combinator: str | None = None,
) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO grid_search_jobs "
            "(id, market, timeframe, capital, start, end, top_n, status, done_combos, started_at, "
            " indicator_pool, base_run_id, combinator) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'running', 0, datetime('now'), ?, ?, ?)",
            (job_id, market, timeframe, capital, start, end, top_n,
             indicator_pool_json, base_run_id, combinator),
        )
        conn.commit()
    finally:
        conn.close()
```

`engine/cache.py:787-837`의 `_row_to_grid_search_job_dict`/`get_grid_search_job`/`list_grid_search_jobs`를 새 컬럼을 포함하도록 수정:

```python
def _row_to_grid_search_job_dict(row: tuple) -> dict:
    (job_id, market, timeframe, capital, start, end, top_n, status,
     total_combos, done_combos, started_at, finished_at, elapsed_sec,
     error_message, result_json, indicator_pool_json, base_run_id, combinator) = row
    return {
        "id": job_id,
        "market": market,
        "timeframe": timeframe,
        "capital": capital,
        "start": start,
        "end": end,
        "top_n": top_n,
        "status": status,
        "total_combos": total_combos,
        "done_combos": done_combos,
        "started_at": started_at,
        "finished_at": finished_at,
        "elapsed_sec": elapsed_sec,
        "error_message": error_message,
        "result_json": json.loads(result_json) if result_json else None,
        "indicator_pool": json.loads(indicator_pool_json) if indicator_pool_json else None,
        "base_run_id": base_run_id,
        "combinator": combinator,
    }


def get_grid_search_job(job_id: str) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT id, market, timeframe, capital, start, end, top_n, status, "
            "       total_combos, done_combos, started_at, finished_at, elapsed_sec, "
            "       error_message, result_json, indicator_pool, base_run_id, combinator "
            "FROM grid_search_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    finally:
        conn.close()
    return _row_to_grid_search_job_dict(row) if row else None


def list_grid_search_jobs() -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, market, timeframe, capital, start, end, top_n, status, "
            "       total_combos, done_combos, started_at, finished_at, elapsed_sec, "
            "       error_message, result_json, indicator_pool, base_run_id, combinator "
            "FROM grid_search_jobs ORDER BY started_at DESC, rowid DESC"
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_grid_search_job_dict(r) for r in rows]
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_cache.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: 기존 grid search 테스트 회귀 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_grid_search_service.py -v`
Expected: PASS (기존 테스트 전부 그대로 통과 — `create_grid_search_job` 호출부는 새 키워드 인자가 전부 기본값 `None`이라 호출 시그니처를 안 바꿔도 동작)

- [ ] **Step 7: 커밋**

```bash
git add engine/cache.py tests/test_cache.py
git commit -m "feat: grid_search_jobs에 indicator_pool/base_run_id/combinator 컬럼 추가"
```

---

## Task 2: scripts/grid_search.py — 지표 풀 레지스트리 일반화

**Files:**
- Modify: `scripts/grid_search.py:41-102`
- Test: `tests/test_grid_search.py`

**Interfaces:**
- Consumes: 없음(순수 데이터/함수 변경).
- Produces: `INDICATOR_POOL_SPECS: dict[str, dict[str, dict]]` (카테고리명 → 지표스펙), `build_condition_grid(pool: dict | None = None) -> tuple[list[dict], list[dict]]`. `pool`은 `{"categories": list[str], "excluded_indicators": list[str]}` 형태이며 `None`이면 기존 오실레이터 전용 동작.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_grid_search.py` 끝에 추가:

```python
def test_build_condition_grid_default_pool_unchanged():
    buy_conditions, sell_conditions = build_condition_grid()
    assert len(buy_conditions) == 138
    assert len(sell_conditions) == 150


def test_build_condition_grid_with_trend_pool_only():
    buy_conditions, sell_conditions = build_condition_grid({"categories": ["추세"], "excluded_indicators": []})
    buy_indicators = {b["indicator"] for b in buy_conditions}
    assert buy_indicators == {"SMA_PCT", "EMA_PCT", "WMA_PCT", "MOMENTUM_PCT"}
    # 손익(SELL_ONLY)은 풀 선택과 무관하게 항상 매도 조건에 포함된다
    sell_indicators = {s["indicator"] for s in sell_conditions}
    assert {"STOP_LOSS_PCT", "TAKE_PROFIT_PCT", "HOLDING_PERIOD_BARS"} <= sell_indicators
    assert "RSI" not in buy_indicators


def test_build_condition_grid_excludes_individual_indicators():
    buy_conditions, _ = build_condition_grid({"categories": ["추세"], "excluded_indicators": ["MOMENTUM_PCT"]})
    buy_indicators = {b["indicator"] for b in buy_conditions}
    assert buy_indicators == {"SMA_PCT", "EMA_PCT", "WMA_PCT"}


def test_build_condition_grid_market_sentiment_pool_has_no_param_indicators():
    buy_conditions, _ = build_condition_grid({"categories": ["시장 심리"], "excluded_indicators": []})
    fear_greed_blocks = [b for b in buy_conditions if b["indicator"] == "FEAR_GREED_CMC"]
    assert fear_greed_blocks
    assert all(b["params"] == {} for b in fear_greed_blocks)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_grid_search.py -v -k "pool or excludes"`
Expected: FAIL — `TypeError: build_condition_grid() takes 0 positional arguments but 1 was given`

- [ ] **Step 3: INDICATOR_POOL_SPECS 추가 + build_condition_grid 일반화**

`scripts/grid_search.py:41-102`을 다음으로 교체(기존 `OSCILLATOR_SPECS`/`SELL_ONLY`는 그대로 두고, 새 카테고리 스펙 딕셔너리 4개와 `INDICATOR_POOL_SPECS`, 일반화된 `build_condition_grid`를 추가):

```python
OSCILLATOR_SPECS: dict[str, dict] = {
    "RSI": {"param_grid": _period_grid(), "low": [20, 30, 40], "high": [60, 70, 80], "bidirectional": False},
    "STOCH_K": {"param_grid": _period_grid("k_period"), "low": [10, 20, 30], "high": [70, 80, 90], "bidirectional": False},
    "STOCH_D": {"param_grid": _period_grid("k_period"), "low": [10, 20, 30], "high": [70, 80, 90], "bidirectional": False},
    "CCI": {"param_grid": _period_grid(), "low": [-140, -100, -60], "high": [60, 100, 140], "bidirectional": False},
    "WILLIAMS_R": {"param_grid": _period_grid(), "low": [-90, -80, -70], "high": [-30, -20, -10], "bidirectional": False},
    "BB_PERCENT_B": {"param_grid": _period_grid(), "low": [0.0, 0.1, 0.2], "high": [0.8, 0.9, 1.0], "bidirectional": False},
    "MACD_PPO": {
        "param_grid": [
            {"fast": f, "slow": s, "signal": sig} for f in [12, 16] for s in [26, 32] for sig in [9, 12]
        ],
        "low": [-3, -2, -1],
        "high": [1, 2, 3],
        "bidirectional": False,
    },
    "MACD_PPO_signal": {
        "param_grid": [
            {"fast": f, "slow": s, "signal": sig} for f in [12, 16] for s in [26, 32] for sig in [9, 12]
        ],
        "low": [-3, -2, -1],
        "high": [1, 2, 3],
        "bidirectional": False,
    },
    "ATR_PCT": {"param_grid": _period_grid(), "low": [0.5, 1, 2, 3, 5, 8], "high": [], "bidirectional": True},
}

# 아래 4개 카테고리의 low/high 값은 KRW-ETH/KRW-XRP, 1시간봉, 2026-01-01~2026-08-20 구간에서
# 실측한 값 분포의 백분위수(p10/p20/p30/p70/p80/p90)를 반올림해 정했다(스펙 문서 참고).
# FEAR_GREED_CMC만 표본 구간이 공포 국면에 쏠려 있어 문헌상 관례값(극단적 공포<25, 극단적 탐욕>75)으로 대체했다.
TREND_SPECS: dict[str, dict] = {
    "SMA_PCT": {"param_grid": _period_grid(), "low": [-1.0, -0.6, -0.3], "high": [0.3, 0.5, 0.9], "bidirectional": False},
    "EMA_PCT": {"param_grid": _period_grid(), "low": [-0.9, -0.5, -0.3], "high": [0.25, 0.45, 0.8], "bidirectional": False},
    "WMA_PCT": {"param_grid": _period_grid(), "low": [-0.8, -0.45, -0.25], "high": [0.2, 0.4, 0.7], "bidirectional": False},
    "MOMENTUM_PCT": {
        "param_grid": [{"period": p} for p in (5, 10, 20)],
        "low": [-1.1, -0.6, -0.35], "high": [0.3, 0.6, 1.0], "bidirectional": False,
    },
}

PRICE_LEVEL_SPECS: dict[str, dict] = {
    "FIB_382_PCT": {"param_grid": _period_grid(), "low": [-1.8, -1.2, -0.8], "high": [0.15, 0.35, 0.7], "bidirectional": False},
    "FIB_500_PCT": {"param_grid": _period_grid(), "low": [-1.3, -0.8, -0.5], "high": [0.4, 0.7, 1.15], "bidirectional": False},
    "FIB_618_PCT": {"param_grid": _period_grid(), "low": [-0.85, -0.45, -0.2], "high": [0.7, 1.0, 1.6], "bidirectional": False},
    "PIVOT_P_PCT": {"param_grid": [{}], "low": [-0.5, -0.3, -0.15], "high": [0.15, 0.3, 0.5], "bidirectional": False},
    "PIVOT_R1_PCT": {"param_grid": [{}], "low": [-1.0, -0.65, -0.5], "high": [-0.1, 0.0, 0.17], "bidirectional": False},
    "PIVOT_S1_PCT": {"param_grid": [{}], "low": [-0.15, 0.0, 0.1], "high": [0.45, 0.65, 0.95], "bidirectional": False},
    "VPVR_POC_PCT": {
        "param_grid": [{"period": p} for p in (30, 50, 70)],
        "low": [-2.3, -1.3, -0.6], "high": [0.5, 1.0, 2.0], "bidirectional": False,
    },
    "VPVR_VAH_PCT": {
        "param_grid": [{"period": p} for p in (30, 50, 70)],
        "low": [-4.0, -2.7, -2.0], "high": [-0.3, 0.0, 0.5], "bidirectional": False,
    },
    "VPVR_VAL_PCT": {
        "param_grid": [{"period": p} for p in (30, 50, 70)],
        "low": [-0.6, 0.0, 0.35], "high": [1.7, 2.4, 3.7], "bidirectional": False,
    },
}

VOLUME_SPECS: dict[str, dict] = {
    "OBV_ROC": {"param_grid": _period_grid(), "low": [-45, -35, -23], "high": [12, 22, 35], "bidirectional": False},
    "VOLUME_PCT": {"param_grid": _period_grid(), "low": [-65, -53, -43], "high": [12, 40, 95], "bidirectional": False},
    "VPIN": {"param_grid": _period_grid(), "low": [0.4, 0.45, 0.5, 0.55], "high": [], "bidirectional": True},
}

TRADE_VALUE_SPECS: dict[str, dict] = {
    "TRADE_VALUE_PCT": {"param_grid": _period_grid(), "low": [-65, -53, -43], "high": [12, 40, 95], "bidirectional": False},
}

MARKET_SENTIMENT_SPECS: dict[str, dict] = {
    "MARKET_TREND_PCT": {"param_grid": _period_grid(), "low": [-0.7, -0.43, -0.24], "high": [0.22, 0.38, 0.69], "bidirectional": False},
    "BTC_CORRELATION": {"param_grid": _period_grid(), "low": [0.6, 0.71, 0.77], "high": [0.88, 0.91, 0.93], "bidirectional": False},
    "USDT_CORRELATION": {"param_grid": _period_grid(), "low": [-0.64, -0.54, -0.44], "high": [-0.1, 0.0, 0.17], "bidirectional": False},
    "FEAR_GREED_CMC": {"param_grid": [{}], "low": [20, 25, 30], "high": [65, 70, 75], "bidirectional": False},
    "KOREA_PREMIUM": {"param_grid": [{}], "low": [-0.09, -0.06, -0.03], "high": [0.05, 0.07, 0.1], "bidirectional": False},
    "FUNDING_RATE": {"param_grid": [{}], "low": [-0.008, -0.004, -0.0025], "high": [0.004, 0.0054, 0.008], "bidirectional": False},
}

# 카테고리명은 backend/main.py의 INDICATOR_CATALOG가 쓰는 표기와 동일하게 맞춘다(프론트 폼의
# 카테고리 체크박스 라벨이 그대로 이 dict의 키가 된다). "손익"은 여기 없다 — 포지션 청산
# 메커니즘이라 풀 선택과 무관하게 항상 SELL_ONLY로 매도 조건에 포함된다.
INDICATOR_POOL_SPECS: dict[str, dict[str, dict]] = {
    "오실레이터": OSCILLATOR_SPECS,
    "추세": TREND_SPECS,
    "가격대": PRICE_LEVEL_SPECS,
    "거래량": VOLUME_SPECS,
    "거래대금": TRADE_VALUE_SPECS,
    "시장 심리": MARKET_SENTIMENT_SPECS,
}

SELL_ONLY: dict[str, tuple[str, list[int]]] = {
    "STOP_LOSS_PCT": ("<=", [-3, -5, -7, -10]),
    "TAKE_PROFIT_PCT": (">=", [5, 10, 15, 20]),
    "HOLDING_PERIOD_BARS": (">=", [5, 10, 20, 40]),
}


def _selected_specs(pool: dict | None) -> dict[str, dict]:
    """pool 인자를 실제로 순회할 {지표명: 스펙} dict로 해석한다.

    pool이 None이면 기존 동작(오실레이터 전용)과 완전히 동일하게 OSCILLATOR_SPECS만
    반환한다 — build_condition_grid()를 인자 없이 호출하는 기존 호출부/테스트가
    바뀌지 않아야 하기 때문이다."""
    if pool is None:
        return OSCILLATOR_SPECS
    categories = pool.get("categories") or ["오실레이터"]
    excluded = set(pool.get("excluded_indicators") or [])
    return {
        indicator: spec
        for category in categories
        for indicator, spec in INDICATOR_POOL_SPECS.get(category, {}).items()
        if indicator not in excluded
    }


def build_condition_grid(pool: dict | None = None) -> tuple[list[dict], list[dict]]:
    """선택된 지표 풀의 매수/매도 ConditionBlock 그리드를 생성한다.

    Args:
        pool: {"categories": list[str], "excluded_indicators": list[str]} 또는 None.
            None이면 오실레이터 9종만 순회한다(기존 동작과 동일).

    Returns:
        (buy_conditions, sell_conditions) — 각각 ConditionBlock 딕셔너리 리스트
        ({"indicator": str, "params": dict, "operator": str, "threshold": float}).
        손익(SELL_ONLY) 3종은 풀 선택과 무관하게 항상 매도 조건에 포함된다.
    """
    buy_conditions: list[dict] = []
    sell_conditions: list[dict] = []

    for indicator, spec in _selected_specs(pool).items():
        for params in spec["param_grid"]:
            if spec["bidirectional"]:
                for t in spec["low"]:
                    buy_conditions.append({"indicator": indicator, "params": params, "operator": "<", "threshold": t})
                    buy_conditions.append({"indicator": indicator, "params": params, "operator": ">", "threshold": t})
                    sell_conditions.append({"indicator": indicator, "params": params, "operator": "<", "threshold": t})
                    sell_conditions.append({"indicator": indicator, "params": params, "operator": ">", "threshold": t})
            else:
                for t in spec["low"]:
                    buy_conditions.append({"indicator": indicator, "params": params, "operator": "<", "threshold": t})
                for t in spec["high"]:
                    sell_conditions.append({"indicator": indicator, "params": params, "operator": ">", "threshold": t})

    for indicator, (operator, thresholds) in SELL_ONLY.items():
        for t in thresholds:
            sell_conditions.append({"indicator": indicator, "params": {}, "operator": operator, "threshold": t})

    return buy_conditions, sell_conditions
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_grid_search.py -v`
Expected: PASS (기존 테스트 포함 전체 통과)

- [ ] **Step 5: 커밋**

```bash
git add scripts/grid_search.py tests/test_grid_search.py
git commit -m "feat: grid search 지표 풀을 오실레이터 외 5개 카테고리로 확장"
```

---

## Task 3: scripts/grid_search.py — 보조 데이터 연동 + CLI 풀 선택

지금 `main()`은 `get_candles()`만 호출해 df를 만든다. 시장심리 카테고리(BTC_CORRELATION/USDT_CORRELATION/MARKET_TREND_PCT/KOREA_PREMIUM/FUNDING_RATE/FEAR_GREED_CMC)를 선택하면 이 df에 `btc_close`/`usdt_close`/`fear_greed_value`/`korea_premium_value`/`funding_rate_value` 컬럼이 없어 실행이 깨진다. `backend/main.py`의 `_fetch_backtest_dataframe()`가 이미 이 보조 데이터 병합 로직을 전부 갖고 있으므로 그것을 재사용한다.

**Files:**
- Modify: `scripts/grid_search.py:334-413` (`main()`), argparse 섹션
- Test: `tests/test_grid_search.py`

**Interfaces:**
- Consumes: `backend.main._fetch_backtest_dataframe(market, timeframe, start_dt, end_dt, buy_dict, sell_dict) -> pd.DataFrame` (Task 2에서 만든 `build_condition_grid`가 반환하는 flat block 리스트를 `{"type": "AND", "conditions": [...]}`로 감싸 전달).
- Produces: CLI 인자 `--categories`(콤마 구분), `--exclude-indicators`(콤마 구분).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_grid_search.py`에 추가(CLI 파싱만 검증 — 실제 서브프로세스 실행은 `test_grid_search_service.py`가 이미 서비스 레이어에서 목으로 검증):

```python
def test_main_parses_categories_and_exclude_indicators_args(monkeypatch, capsys):
    import sys
    from scripts import grid_search

    captured_pool = {}

    def fake_build_condition_grid(pool=None):
        captured_pool["pool"] = pool
        return [], []

    monkeypatch.setattr(grid_search, "build_condition_grid", fake_build_condition_grid)
    monkeypatch.setattr(
        sys, "argv",
        [
            "grid_search.py", "--market", "KRW-ETH", "--timeframe", "minutes60",
            "--capital", "1000000", "--start", "2026-01-01", "--end", "2026-01-02",
            "--categories", "오실레이터,추세", "--exclude-indicators", "MOMENTUM_PCT",
        ],
    )
    try:
        grid_search.main()
    except SystemExit:
        pass  # 빈 조합이라 캔들 조회 이후 어딘가에서 중단돼도 괜찮다 — 파싱 결과만 검증
    assert captured_pool["pool"] == {
        "categories": ["오실레이터", "추세"],
        "excluded_indicators": ["MOMENTUM_PCT"],
    }
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_grid_search.py -v -k categories_and_exclude`
Expected: FAIL — `error: unrecognized arguments: --categories 오실레이터,추세 --exclude-indicators MOMENTUM_PCT`

- [ ] **Step 3: import 추가 + main() 재구성**

`scripts/grid_search.py` 상단 import에 추가:

```python
from fastapi import HTTPException

from backend.main import _fetch_backtest_dataframe
```

`scripts/grid_search.py:334-368`(`main()`의 argparse부터 `build_condition_grid()` 호출까지)을 다음으로 교체:

```python
def main() -> None:
    parser = argparse.ArgumentParser(description="그리드서치 백테스트")
    parser.add_argument("--market", required=True, help="마켓코드 (예: KRW-ETH)")
    parser.add_argument("--timeframe", required=True, help="timeframe 코드 (예: minutes60)")
    parser.add_argument("--capital", required=True, type=float, help="운용자금(원)")
    parser.add_argument("--start", required=True, help="시작일 YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="종료일 YYYY-MM-DD")
    parser.add_argument("--top-n", type=_positive_int, default=20, help="저장할 상위 개수 (기본 20, 상한 50)")
    parser.add_argument(
        "--categories", default=None,
        help="콤마로 구분된 지표 카테고리 목록 (예: 오실레이터,추세). 미지정 시 오실레이터만.",
    )
    parser.add_argument(
        "--exclude-indicators", default=None,
        help="콤마로 구분된, 선택된 카테고리 안에서 제외할 개별 지표 키",
    )
    args = parser.parse_args()

    top_n = min(args.top_n, 50)

    pool = None
    if args.categories:
        pool = {
            "categories": [c.strip() for c in args.categories.split(",") if c.strip()],
            "excluded_indicators": (
                [i.strip() for i in args.exclude_indicators.split(",") if i.strip()]
                if args.exclude_indicators else []
            ),
        }

    start_dt = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(args.end, "%Y-%m-%d").replace(
        hour=23, minute=59, second=59, tzinfo=timezone.utc
    )

    buy_conditions, sell_conditions = build_condition_grid(pool)
    total_combos = len(buy_conditions) * len(sell_conditions)
    print(
        f"[2] 매수 조건 {len(buy_conditions)}개 x 매도 조건 {len(sell_conditions)}개 = 총 {total_combos:,}개 조합",
        flush=True,
    )

    print(f"[1] 캔들 조회: {args.market} {args.timeframe} {args.start} ~ {args.end}", flush=True)
    all_buy_group = {"type": "AND", "conditions": buy_conditions}
    all_sell_group = {"type": "AND", "conditions": sell_conditions}
    try:
        df = _fetch_backtest_dataframe(args.market, args.timeframe, start_dt, end_dt, all_buy_group, all_sell_group)
    except HTTPException as exc:
        raise SystemExit(f"캔들/보조 데이터 조회 실패: {exc.detail}") from exc
    print(f"    캔들 수: {len(df)}", flush=True)

    _check_candle_warmup(df, buy_conditions, sell_conditions)

    risk_config = {**DEFAULT_RISK_CONFIG, "initial_capital": args.capital}
```

`_check_candle_warmup`이 `max_required_period`로 이미 최소 봉 수를 검증하지만 MACD류 보정(`_macd_required_bars`)이 `_fetch_backtest_dataframe` 내부 체크에는 없으므로, 기존처럼 `_check_candle_warmup`을 그대로 남겨 이중 검증한다.

`main()`의 이어지는 부분(`t0 = time.perf_counter()`부터 끝까지)은 그대로 둔다 — `df`/`buy_conditions`/`sell_conditions`/`risk_config` 변수명이 동일하므로 수정 불필요.

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_grid_search.py -v`
Expected: PASS

- [ ] **Step 5: 기존 CLI 스모크 테스트로 회귀 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/grid_search.py --market KRW-ETH --timeframe minutes60 --capital 1000000 --start 2026-08-01 --end 2026-08-15 --top-n 3`
Expected: 기존과 동일하게 오실레이터 20,700개 조합을 계산하고 `RESULT_JSON:` 라인으로 끝남(카테고리 인자를 안 줬으므로 `pool=None` 경로).

- [ ] **Step 6: 커밋**

```bash
git add scripts/grid_search.py tests/test_grid_search.py
git commit -m "feat: grid search에 보조 데이터 연동 + 카테고리 선택 CLI 인자 추가"
```

---

## Task 4: scripts/grid_search.py — 체이닝(베이스 + AND/OR + 새 후보)

**Files:**
- Modify: `scripts/grid_search.py:147-297` (`_run_one_combo`/`_init_worker`/`_run_one_combo_worker`/`compute_grid_results`/`compute_grid_results_parallel`), `scripts/grid_search.py:334-413` (`main()` 나머지)
- Test: `tests/test_grid_search.py`

**Interfaces:**
- Consumes: `engine.cache.get_run_config(run_id) -> dict | None` (Task 3에서 import 추가됨) — `{"buy_conditions": ConditionGroup, "sell_conditions": ConditionGroup, ...}`.
- Produces: CLI 인자 `--base-run-id`, `--combinator {AND,OR}`. 저장되는 백테스트 run의 `strategy_params`에는 베이스+새 조건이 합쳐진 완전한 트리가 들어간다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_grid_search.py`에 추가:

```python
def test_wrap_condition_without_base_matches_existing_shape():
    from scripts.grid_search import _wrap_condition

    block = {"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30}
    assert _wrap_condition(block, None, "AND") == {"type": "AND", "conditions": [block]}


def test_wrap_condition_with_base_nests_group():
    from scripts.grid_search import _wrap_condition

    base = {"type": "AND", "conditions": [{"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30}]}
    candidate = {"indicator": "SMA_PCT", "params": {"period": 14}, "operator": "<", "threshold": -1.0}
    assert _wrap_condition(candidate, base, "OR") == {"type": "OR", "conditions": [base, candidate]}


def test_run_one_combo_uses_base_group_when_provided():
    from engine.sweep import DEFAULT_RISK_CONFIG
    from scripts.grid_search import _run_one_combo
    from tests.signal_fixtures import make_oscillating_df

    df = make_oscillating_df()
    buy_block = {"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 90}  # 거의 항상 참
    sell_block = {"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 10}  # 거의 항상 참
    base_buy = {"type": "AND", "conditions": [{"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 0}]}  # 항상 거짓
    result = _run_one_combo(
        df, DEFAULT_RISK_CONFIG, buy_block, sell_block,
        base_buy_group=base_buy, base_sell_group=None, combinator="AND",
    )
    # base_buy가 항상 거짓인 AND 결합이므로, 매수 조건 자체가 성립할 수 없어 거래가 0건이어야 한다
    assert result["trades"] == []
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_grid_search.py -v -k "wrap_condition or uses_base_group"`
Expected: FAIL — `ImportError: cannot import name '_wrap_condition'`

- [ ] **Step 3: import 추가 + `_wrap_condition` 추가 + 파이프라인에 base 인자 스레딩**

`scripts/grid_search.py` 상단 import에 추가:

```python
from engine.cache import get_run_config
```

`scripts/grid_search.py:147-170`(`_run_one_combo`) 앞에 헬퍼 추가하고 함수를 교체:

```python
def _wrap_condition(block: dict, base_group: dict | None, combinator: str) -> dict:
    """block(단일 ConditionBlock)을 실행 가능한 ConditionGroup으로 감싼다.

    base_group이 None이면 기존과 동일하게 {"type": "AND", "conditions": [block]}로
    감싼다. base_group이 있으면(체이닝) combinator로 베이스와 block을 함께 묶는다 —
    이렇게 만들어진 트리를 그대로 저장해야 재체이닝 시 베이스 정보가 안 사라진다."""
    if base_group is None:
        return {"type": "AND", "conditions": [block]}
    return {"type": combinator, "conditions": [base_group, block]}


def _run_one_combo(
    df, risk_config: dict, buy_block: dict, sell_block: dict,
    base_buy_group: dict | None = None, base_sell_group: dict | None = None, combinator: str = "AND",
) -> dict:
    """조합 하나(매수 블록 1개 x 매도 블록 1개)에 대해 run_backtest를 1회 호출한다.

    순차 실행(compute_grid_results)과 병렬 워커(compute_grid_results_parallel) 양쪽에서
    공유하는 단일 진입점 — 조합당 실제로 무엇을 계산하는지는 여기 한 곳에만 있다.
    base_buy_group/base_sell_group이 주어지면(체이닝) 베이스 조건과 combinator로 묶는다.
    """
    buy_group = _wrap_condition(buy_block, base_buy_group, combinator)
    sell_group = _wrap_condition(sell_block, base_sell_group, combinator)
    result = run_backtest(
        df,
        ConditionTreeStrategy,
        risk_config,
        {"buy_conditions": buy_group, "sell_conditions": sell_group},
    )
    initial_capital = float(risk_config.get("initial_capital", 10000))
    return_pct = (result["final_value"] - initial_capital) / initial_capital * 100
    return {
        "return_pct": return_pct,
        "buy_block": buy_block,
        "sell_block": sell_block,
        "trades": result["trades"],
        "final_value": result["final_value"],
    }
```

`scripts/grid_search.py:172-188`(워커 전역/초기화)을 교체:

```python
_worker_df = None
_worker_risk_config: dict | None = None
_worker_base_buy_group: dict | None = None
_worker_base_sell_group: dict | None = None
_worker_combinator: str = "AND"


def _init_worker(
    df, risk_config: dict,
    base_buy_group: dict | None = None, base_sell_group: dict | None = None, combinator: str = "AND",
) -> None:
    """Pool 워커 프로세스가 (재)시작될 때마다 호출 — df/risk_config/베이스 조건을 워커
    전역에 저장해 태스크마다 재직렬화하지 않는다."""
    global _worker_df, _worker_risk_config, _worker_base_buy_group, _worker_base_sell_group, _worker_combinator
    _worker_df = df
    _worker_risk_config = risk_config
    _worker_base_buy_group = base_buy_group
    _worker_base_sell_group = base_sell_group
    _worker_combinator = combinator


def _run_one_combo_worker(buy_block: dict, sell_block: dict) -> dict:
    """Pool.apply_async에 전달되는 워커 측 진입점. 모듈 최상위 함수여야 Windows spawn에서
    pickle 가능하다."""
    return _run_one_combo(
        _worker_df, _worker_risk_config, buy_block, sell_block,
        _worker_base_buy_group, _worker_base_sell_group, _worker_combinator,
    )
```

`scripts/grid_search.py:190-213`(`compute_grid_results`) 시그니처와 내부 호출을 교체:

```python
def compute_grid_results(
    df,
    buy_conditions: list[dict],
    sell_conditions: list[dict],
    risk_config: dict,
    base_buy_group: dict | None = None,
    base_sell_group: dict | None = None,
    combinator: str = "AND",
) -> list[dict]:
    """buy_conditions x sell_conditions 전 조합을 순차로 계산한다(테스트/소규모 실행용).

    대규모 실행(main())은 compute_grid_results_parallel을 쓴다.

    Returns:
        각 조합의 결과 딕셔너리 리스트: _run_one_combo와 동일한 shape.
    """
    results: list[dict] = []
    total = len(buy_conditions) * len(sell_conditions)

    for i, buy_block in enumerate(buy_conditions):
        for sell_block in sell_conditions:
            results.append(
                _run_one_combo(df, risk_config, buy_block, sell_block, base_buy_group, base_sell_group, combinator)
            )
        if (i + 1) % 5 == 0 or (i + 1) == len(buy_conditions):
            done = (i + 1) * len(sell_conditions)
            print(f"    매수조건 {i + 1}/{len(buy_conditions)} 완료 ({done}/{total}건)", flush=True)

    return results
```

`scripts/grid_search.py:216-245`(`compute_grid_results_parallel`)에서 정확히 두 곳만 바꾼다 — 함수의 나머지 본문(`while done_count < total:` 루프 전체, `finally: pool.terminate(); pool.join()`)은 그대로 둔다:

1. 함수 시그니처(`def compute_grid_results_parallel(` ~ `) -> list[dict]:`)에 새 키워드 인자 3개 추가:

```python
def compute_grid_results_parallel(
    df,
    buy_conditions: list[dict],
    sell_conditions: list[dict],
    risk_config: dict,
    processes: int = WORKER_COUNT,
    max_tasks_per_child: int = MAX_TASKS_PER_CHILD,
    watchdog_timeout: float = WATCHDOG_TIMEOUT_SEC,
    base_buy_group: dict | None = None,
    base_sell_group: dict | None = None,
    combinator: str = "AND",
) -> list[dict]:
```

2. `initargs=(df, risk_config)` 한 줄만 다음으로 교체:

```python
        initargs=(df, risk_config, base_buy_group, base_sell_group, combinator),
```

- [ ] **Step 4: main()에 체이닝 CLI 인자 + 베이스 로딩 + 저장 로직 추가**

`scripts/grid_search.py:334-346`(argparse 섹션)에 인자 추가:

```python
    parser.add_argument("--base-run-id", default=None, help="체이닝 베이스로 삼을 결과의 run_id")
    parser.add_argument("--combinator", choices=["AND", "OR"], default=None, help="베이스 조건과 새 후보를 결합하는 연산자")
    args = parser.parse_args()

    if args.base_run_id and not args.combinator:
        raise SystemExit("--base-run-id를 주면 --combinator(AND 또는 OR)도 함께 지정해야 합니다.")

    base_buy_group: dict | None = None
    base_sell_group: dict | None = None
    if args.base_run_id:
        base_config = get_run_config(args.base_run_id)
        if base_config is None:
            raise SystemExit(f"베이스 결과를 찾을 수 없습니다(삭제되었을 수 있습니다): {args.base_run_id}")
        base_buy_group = base_config["buy_conditions"]
        base_sell_group = base_config["sell_conditions"]
```

(이 블록은 `top_n = min(args.top_n, 50)` 앞, 기존 `--top-n` 인자 뒤에 이어 붙인다.)

`compute_grid_results_parallel(...)` 호출부(Task 3 이후 기준 `scripts/grid_search.py`의 `results = compute_grid_results_parallel(df, buy_conditions, sell_conditions, risk_config)` 줄)를 교체:

```python
    results = compute_grid_results_parallel(
        df, buy_conditions, sell_conditions, risk_config,
        base_buy_group=base_buy_group, base_sell_group=base_sell_group,
        combinator=args.combinator or "AND",
    )
```

저장 루프(`for rank, r in enumerate(top_results, start=1):` 이하)에서 `buy_group`/`sell_group` 생성 부분을 교체:

```python
    saved_summaries = []
    for rank, r in enumerate(top_results, start=1):
        buy_block, sell_block = r["buy_block"], r["sell_block"]
        buy_group = _wrap_condition(buy_block, base_buy_group, args.combinator or "AND")
        sell_group = _wrap_condition(sell_block, base_sell_group, args.combinator or "AND")
        title = (
            f"[Grid] 매수 {buy_block['indicator']}{buy_block['params']}{buy_block['operator']}{buy_block['threshold']} "
            f"/ 매도 {sell_block['indicator']}{sell_block['params']}{sell_block['operator']}{sell_block['threshold']}"
        )
        description = (
            f"grid search - {args.market}/{args.timeframe}/{args.start}~{args.end}, "
            f"수익률 {r['return_pct']:+.2f}% (상위 {rank}위)"
            f", 동일 매매를 만든 조합 {r['dup_count']}개 중 대표"
        )
        saved = run_backtest_cached(
            df=df,
            strategy_cls=ConditionTreeStrategy,
            risk_config=risk_config,
            market=args.market,
            timeframe=args.timeframe,
            start=start_dt,
            end=end_dt,
            strategy_params={"buy_conditions": buy_group, "sell_conditions": sell_group},
            title=title,
            description=description,
        )
        trades = saved["trades"]
        win_rate_pct = (
            round(sum(1 for t in trades if t["pnl"] > 0) / len(trades) * 100, 2) if trades else None
        )
        print(f"  {rank:2d}. {r['return_pct']:+.2f}%  run_id={saved['run_id'][:12]}...", flush=True)
        saved_summaries.append({
            "rank": rank,
            "run_id": saved["run_id"],
            "return_pct": round(r["return_pct"], 2),
            "title": title,
            "trade_count": len(trades),
            "candle_count": saved["candle_count"],
            "max_drawdown_pct": saved.get("max_drawdown"),
            "win_rate_pct": win_rate_pct,
        })
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_grid_search.py tests/test_grid_search_service.py -v`
Expected: PASS (전체)

- [ ] **Step 6: 커밋**

```bash
git add scripts/grid_search.py tests/test_grid_search.py
git commit -m "feat: grid search에 베이스 결과 기반 AND/OR 체이닝 추가"
```

---

## Task 5: backend/grid_search_service.py — 체이닝 파라미터 전달 + 영속화

**Files:**
- Modify: `backend/grid_search_service.py:191-220`(`start_job`)
- Test: `tests/test_grid_search_service.py`

**Interfaces:**
- Consumes: Task 1의 `create_grid_search_job(..., indicator_pool_json=, base_run_id=, combinator=)`.
- Produces: `start_job(market, timeframe, capital, start, end, top_n, indicator_pool=None, base_run_id=None, combinator=None) -> str`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_grid_search_service.py`의 기존 subprocess-mocking 패턴을 확인 후(파일 상단에서 `subprocess.Popen`을 어떻게 목킹하는지 먼저 읽고 동일 패턴을 따른다), 다음 테스트를 추가:

```python
def test_start_job_passes_pool_and_chaining_args_to_subprocess(monkeypatch):
    import backend.grid_search_service as svc

    captured_args = {}

    class FakeProc:
        stdout = iter([])
        returncode = 0

        def wait(self):
            return 0

    def fake_popen(args, **kwargs):
        captured_args["args"] = args
        return FakeProc()

    monkeypatch.setattr(svc.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(svc, "create_grid_search_job", lambda **kwargs: captured_args.update({"db_kwargs": kwargs}))

    svc.start_job(
        market="KRW-ETH", timeframe="minutes60", capital=1_000_000,
        start="2026-01-01", end="2026-02-01", top_n=10,
        indicator_pool={"categories": ["추세"], "excluded_indicators": []},
        base_run_id="base-abc",
        combinator="OR",
    )

    args = captured_args["args"]
    assert "--categories" in args
    assert args[args.index("--categories") + 1] == "추세"
    assert "--base-run-id" in args
    assert args[args.index("--base-run-id") + 1] == "base-abc"
    assert "--combinator" in args
    assert args[args.index("--combinator") + 1] == "OR"
    assert captured_args["db_kwargs"]["base_run_id"] == "base-abc"
    assert captured_args["db_kwargs"]["combinator"] == "OR"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_grid_search_service.py -v -k passes_pool_and_chaining`
Expected: FAIL — `TypeError: start_job() got an unexpected keyword argument 'indicator_pool'`

- [ ] **Step 3: start_job 확장**

`backend/grid_search_service.py:191-220`에서 세 곳만 바꾼다 — `Popen(...)` 호출의 `env=`/`creationflags=`/`stdout=`/`stderr=`/`text=`/`encoding=` kwargs와 그 뒤 `start_job`의 나머지 본문(리더 스레드 시작 등)은 그대로 둔다:

1. 함수 시그니처에 새 키워드 인자 3개 추가:

```python
def start_job(
    market: str, timeframe: str, capital: float, start: str, end: str, top_n: int,
    indicator_pool: dict | None = None,
    base_run_id: str | None = None,
    combinator: str | None = None,
) -> str:
```

2. `create_grid_search_job(...)` 호출에 새 인자 전달:

```python
        create_grid_search_job(
            job_id=job_id, market=market, timeframe=timeframe, capital=capital,
            start=start, end=end, top_n=top_n,
            indicator_pool_json=json.dumps(indicator_pool) if indicator_pool else None,
            base_run_id=base_run_id, combinator=combinator,
        )
```

3. 기존에 `Popen(...)`의 첫 인자로 인라인돼 있던 CLI 인자 리스트 리터럴(`[sys.executable, "scripts/grid_search.py", "--market", market, ...]`)을 없애고, 그 자리(`Popen(` 바로 앞)에 `cli_args` 변수로 뽑아 조건부 인자를 추가한 뒤 `Popen(cli_args, ...)`으로 참조하도록 바꾼다:

```python
        cli_args = [
            sys.executable, "scripts/grid_search.py",
            "--market", market, "--timeframe", timeframe,
            "--capital", str(capital), "--start", start, "--end", end,
            "--top-n", str(top_n),
        ]
        if indicator_pool:
            cli_args += ["--categories", ",".join(indicator_pool.get("categories") or [])]
            excluded = indicator_pool.get("excluded_indicators") or []
            if excluded:
                cli_args += ["--exclude-indicators", ",".join(excluded)]
        if base_run_id:
            cli_args += ["--base-run-id", base_run_id, "--combinator", combinator]
```

기존 `proc = subprocess.Popen([sys.executable, "scripts/grid_search.py", ...], cwd=...)`의 첫 번째 위치 인자(리스트 리터럴)만 `cli_args`로 바꾸고, `cwd=str(REPO_ROOT)`부터 이어지는 나머지 kwargs는 한 글자도 건드리지 않는다. `json` 모듈이 파일 상단에 이미 import돼 있지 않다면 `import json`을 추가한다.

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_grid_search_service.py -v`
Expected: PASS (전체)

- [ ] **Step 5: 커밋**

```bash
git add backend/grid_search_service.py tests/test_grid_search_service.py
git commit -m "feat: grid search 서비스 레이어가 지표 풀/체이닝 인자를 서브프로세스로 전달"
```

---

## Task 6: backend/main.py — API 확장 (요청 모델/검증/예상치 엔드포인트)

**Files:**
- Modify: `backend/main.py:1082-1141`(`GridSearchJobRequest`/`_validate_grid_search_request`/`create_grid_search_job_endpoint`)
- Test: `tests/test_backend.py`

**Interfaces:**
- Produces: `GET /api/v1/grid-search/estimate?categories=...&exclude_indicators=...` → `{"buy_count": int, "sell_count": int, "total_combos": int, "estimated_seconds": float}`. `POST /api/v1/grid-search/jobs`가 `indicator_pool`/`base_run_id`/`combinator` 필드를 받는다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_backend.py`의 기존 grid-search 테스트 근처(파일에서 `grid-search/jobs`를 grep해 기존 테스트 스타일을 확인한 뒤) 다음을 추가:

```python
def test_grid_search_estimate_endpoint_returns_combo_counts(client):
    resp = client.get("/api/v1/grid-search/estimate", params={"categories": "추세"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["buy_count"] == 12  # SMA_PCT/EMA_PCT/WMA_PCT/MOMENTUM_PCT * 3 param_grid values
    assert data["total_combos"] == data["buy_count"] * data["sell_count"]
    assert data["estimated_seconds"] > 0


def test_create_grid_search_job_rejects_base_run_id_without_combinator(client, monkeypatch):
    import backend.main as main_module
    monkeypatch.setattr(main_module, "start_job", lambda **kwargs: "should-not-be-called")
    resp = client.post(
        "/api/v1/grid-search/jobs",
        json={
            "market": "KRW-ETH", "timeframe": "minutes60", "capital": 1000000,
            "start": "2026-01-01", "end": "2026-02-01", "top_n": 10,
            "base_run_id": "some-run-id",
        },
    )
    assert resp.status_code == 400
    assert "combinator" in resp.json()["detail"]


def test_create_grid_search_job_rejects_deleted_base_run_id(client, monkeypatch):
    import backend.main as main_module
    monkeypatch.setattr(main_module, "get_run_config", lambda run_id: None)
    resp = client.post(
        "/api/v1/grid-search/jobs",
        json={
            "market": "KRW-ETH", "timeframe": "minutes60", "capital": 1000000,
            "start": "2026-01-01", "end": "2026-02-01", "top_n": 10,
            "base_run_id": "deleted-run-id", "combinator": "AND",
        },
    )
    assert resp.status_code == 400
    assert "베이스 결과" in resp.json()["detail"]
```

(테스트 파일에 이미 `client` fixture가 있는지 먼저 확인 — 없으면 파일 상단의 다른 엔드포인트 테스트가 쓰는 fixture/헬퍼를 그대로 재사용한다.)

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_backend.py -v -k "grid_search_estimate or rejects_base_run_id or rejects_deleted"`
Expected: FAIL — 404(엔드포인트 없음) / 검증 로직 없어 200 반환 등

- [ ] **Step 3: GridSearchJobRequest 확장 + 검증 + 엔드포인트**

`backend/main.py` 상단 import에 추가:

```python
from engine.cache import get_run_config  # 이미 다른 목적으로 import돼 있다면 생략
from scripts.grid_search import INDICATOR_POOL_SPECS, build_condition_grid
```

`backend/main.py:1082-1088`을 교체:

```python
class GridSearchJobRequest(BaseModel):
    market: str
    timeframe: str
    capital: float
    start: str
    end: str
    top_n: int = 20
    indicator_pool: dict | None = None
    base_run_id: str | None = None
    combinator: str | None = None
```

`backend/main.py:1091-1114`(`_validate_grid_search_request`)의 `return errors` 직전에 검증 추가:

```python
    if req.indicator_pool is not None:
        categories = req.indicator_pool.get("categories") or []
        if not categories:
            errors.append("지표 카테고리를 최소 1개 이상 선택하세요.")
        unknown_categories = set(categories) - set(INDICATOR_POOL_SPECS)
        if unknown_categories:
            errors.append(f"알 수 없는 지표 카테고리입니다: {', '.join(sorted(unknown_categories))}")

    if req.base_run_id is not None:
        if req.combinator not in ("AND", "OR"):
            errors.append("base_run_id를 지정하면 combinator(AND 또는 OR)도 함께 지정해야 합니다.")
        elif get_run_config(req.base_run_id) is None:
            errors.append(f"베이스 결과를 찾을 수 없습니다(삭제되었을 수 있습니다): {req.base_run_id}")

    return errors
```

`backend/main.py:1125-1141`(`create_grid_search_job_endpoint`)을 교체:

```python
@app.post("/api/v1/grid-search/jobs")
def create_grid_search_job_endpoint(req: GridSearchJobRequest) -> dict:
    errors = _validate_grid_search_request(req)
    if errors:
        raise HTTPException(status_code=400, detail=" / ".join(errors))

    try:
        job_id = start_job(
            market=req.market, timeframe=req.timeframe, capital=req.capital,
            start=req.start, end=req.end, top_n=req.top_n,
            indicator_pool=req.indicator_pool, base_run_id=req.base_run_id, combinator=req.combinator,
        )
    except JobAlreadyRunningError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    job = get_grid_search_job(job_id)
    assert job is not None
    return _grid_search_job_response(job)


# 20,700개 조합/워커4개 기준 완료된 로컬 job들의 실측 elapsed_sec에서 뽑은 처리량
# (data/backtest_results.db의 grid_search_jobs 이력, 이상치 1건 제외 중앙값 약 11.3
# combos/sec) — 워커 수/머신 성능이 바뀌면 재보정이 필요하다.
_ESTIMATED_COMBOS_PER_SEC = 11.0


@app.get("/api/v1/grid-search/estimate")
def estimate_grid_search_endpoint(categories: str = "", exclude_indicators: str = "") -> dict:
    pool = {
        "categories": [c.strip() for c in categories.split(",") if c.strip()] or ["오실레이터"],
        "excluded_indicators": [i.strip() for i in exclude_indicators.split(",") if i.strip()],
    }
    buy_conditions, sell_conditions = build_condition_grid(pool)
    total_combos = len(buy_conditions) * len(sell_conditions)
    return {
        "buy_count": len(buy_conditions),
        "sell_count": len(sell_conditions),
        "total_combos": total_combos,
        "estimated_seconds": round(total_combos / _ESTIMATED_COMBOS_PER_SEC, 1),
    }
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_backend.py -v`
Expected: PASS (전체)

- [ ] **Step 5: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "feat: grid search API에 지표 풀/체이닝 필드 + 예상 조합수 엔드포인트 추가"
```

---

## Task 7: 프론트엔드 타입 + API 클라이언트

**Files:**
- Modify: `frontend/lib/types/eda.ts:178-212`, `frontend/lib/api/eda.ts:107-112`

**Interfaces:**
- Produces: `IndicatorPool`, `getGridSearchEstimate(pool: IndicatorPool) -> Promise<GridSearchEstimate>`.

- [ ] **Step 1: 타입 확장**

`frontend/lib/types/eda.ts:178-212`을 교체:

```typescript
export interface IndicatorPool {
  categories: string[];
  excluded_indicators: string[];
}

export interface GridSearchJobRequest {
  market: string;
  timeframe: string;
  capital: number;
  start: string;
  end: string;
  top_n: number;
  indicator_pool?: IndicatorPool;
  base_run_id?: string;
  combinator?: 'AND' | 'OR';
}

export interface GridSearchSavedResult {
  rank: number;
  run_id: string;
  return_pct: number;
  title: string;
  trade_count?: number;
  candle_count?: number;
  max_drawdown_pct?: number | null;
  win_rate_pct?: number | null;
}

export interface GridSearchJob {
  id: string;
  market: string;
  timeframe: string;
  capital: number;
  start: string;
  end: string;
  top_n: number;
  status: 'running' | 'completed' | 'failed' | 'canceled';
  total_combos: number | null;
  done_combos: number;
  started_at: string;
  finished_at: string | null;
  elapsed_sec: number | null;
  error_message: string | null;
  result_json: GridSearchSavedResult[] | null;
  indicator_pool: IndicatorPool | null;
  base_run_id: string | null;
  combinator: 'AND' | 'OR' | null;
}

export interface GridSearchEstimate {
  buy_count: number;
  sell_count: number;
  total_combos: number;
  estimated_seconds: number;
}
```

- [ ] **Step 2: API 클라이언트 함수 추가**

`frontend/lib/api/eda.ts:107-112` 뒤에 추가:

```typescript
export function getGridSearchEstimate(pool: IndicatorPool): Promise<GridSearchEstimate> {
  const params = new URLSearchParams({
    categories: pool.categories.join(','),
    exclude_indicators: pool.excluded_indicators.join(','),
  });
  return apiFetch<GridSearchEstimate>(`/api/v1/grid-search/estimate?${params.toString()}`);
}
```

(파일 상단 `import type { ... } from '@/lib/types/eda'`에 `IndicatorPool`, `GridSearchEstimate`를 추가한다.)

- [ ] **Step 3: 타입체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 이 시점에는 `GridSearchForm.tsx`/`GridSearchHistory.tsx`가 아직 이전 타입을 안 쓰므로 에러 없이 통과.

- [ ] **Step 4: 커밋**

```bash
git add frontend/lib/types/eda.ts frontend/lib/api/eda.ts
git commit -m "feat: 프론트 grid search 타입에 지표 풀/체이닝 필드 추가"
```

---

## Task 8: GridSearchForm.tsx — 지표 풀 선택 UI + 예상 조합수 표시

**Files:**
- Modify: `frontend/components/GridSearchForm.tsx`

**Interfaces:**
- Consumes: `getGridSearchEstimate(pool)`.
- Produces: `onSubmit`에 넘기는 `GridSearchJobRequest.indicator_pool`이 채워짐(기본값 `{categories: ['오실레이터'], excluded_indicators: []}`).

- [ ] **Step 1: 카테고리 상수 + 상태 추가**

`frontend/components/GridSearchForm.tsx:14-17`(`TIMEFRAME_OPTIONS` 선언부) 아래에 추가:

```typescript
const POOL_CATEGORIES = ['오실레이터', '추세', '가격대', '거래량', '거래대금', '시장 심리'] as const;
const DEFAULT_CATEGORIES: string[] = ['오실레이터'];
```

`GridSearchForm` 컴포넌트 내부(`const [topN, setTopN] = useState(initial.topN);` 아래)에 상태 추가:

```typescript
  const [selectedCategories, setSelectedCategories] = useState<string[]>(DEFAULT_CATEGORIES);
  const [estimate, setEstimate] = useState<GridSearchEstimate | null>(null);
  const [estimateError, setEstimateError] = useState<string | null>(null);
```

`import`에 `GridSearchEstimate`, `getGridSearchEstimate`를 추가.

- [ ] **Step 2: 예상 조합수 조회 useEffect + 카테고리 토글 핸들러**

`useEffect(() => { getMarkets()...`) 아래에 추가:

```typescript
  useEffect(() => {
    getGridSearchEstimate({ categories: selectedCategories, excluded_indicators: [] })
      .then(setEstimate)
      .catch(() => setEstimateError('예상 조합수를 불러오지 못했습니다.'));
  }, [selectedCategories]);

  function toggleCategory(category: string, checked: boolean) {
    setSelectedCategories((prev) =>
      checked ? [...prev, category] : prev.filter((c) => c !== category)
    );
  }
```

- [ ] **Step 3: handleSubmit이 indicator_pool을 포함하도록 수정 + 빈 풀 검증**

`frontend/components/GridSearchForm.tsx:56-74`(`handleSubmit`)을 교체:

```typescript
  async function handleSubmit() {
    setValidationError(null);
    if (start >= end) {
      setValidationError('시작일은 종료일보다 빨라야 합니다.');
      return;
    }
    const topNValue = Number(topN);
    if (!Number.isInteger(topNValue) || topNValue < 1 || topNValue > 50) {
      setValidationError('상위N개는 1~50 사이의 정수여야 합니다.');
      return;
    }
    if (selectedCategories.length === 0) {
      setValidationError('지표 카테고리를 최소 1개 이상 선택하세요.');
      return;
    }

    setSubmitting(true);
    try {
      await onSubmit({
        market, timeframe, capital: Number(capital), start, end, top_n: topNValue,
        indicator_pool: { categories: selectedCategories, excluded_indicators: [] },
      });
    } finally {
      setSubmitting(false);
    }
  }
```

- [ ] **Step 4: 지표 풀 선택 섹션 렌더링 추가**

폼의 마지막 필드(상위N개 입력) 바로 아래, 제출 버튼 위에 섹션 추가:

```tsx
      <div>
        <label className="mb-1.5 block text-sm font-medium">지표 풀 선택</label>
        <div className="flex flex-wrap gap-3">
          {POOL_CATEGORIES.map((category) => (
            <label key={category} className="flex items-center gap-1.5 text-sm">
              <Checkbox
                checked={selectedCategories.includes(category)}
                onCheckedChange={(checked) => toggleCategory(category, checked === true)}
              />
              {category}
            </label>
          ))}
        </div>
        {estimateError && <p className="mt-1 text-xs text-destructive">{estimateError}</p>}
        {estimate && (
          <p className="mt-1 text-xs text-muted-foreground">
            예상 조합수 {estimate.total_combos.toLocaleString()}개, 약 {Math.round(estimate.estimated_seconds / 60)}분 소요 예상
            {estimate.total_combos > 40000 && (
              <span className="ml-1 text-amber-600">— 조합이 많아 오래 걸릴 수 있습니다. 카테고리를 나눠서 실행하는 것을 추천합니다.</span>
            )}
          </p>
        )}
      </div>
```

(`Checkbox` import는 `@/components/ui/checkbox`에서 가져온다 — `GridSearchHistory.tsx`가 이미 같은 컴포넌트를 쓰고 있으니 동일하게 import.)

- [ ] **Step 5: 브라우저 수동 확인**

Run: `cd frontend && npm run dev` 후 `/grid-search` 접속.
Expected: 지표 풀 섹션이 보이고 기본값은 "오실레이터"만 체크됨. 체크박스를 추가로 켜면 잠시 후 "예상 조합수 ...개, 약 ...분 소요 예상" 문구가 갱신됨. 모든 카테고리를 다 켜면 경고 문구(주황색)가 나타남. 모두 해제하면 제출 시 "지표 카테고리를 최소 1개 이상 선택하세요." 에러가 뜸.

- [ ] **Step 6: 커밋**

```bash
git add frontend/components/GridSearchForm.tsx
git commit -m "feat: grid search 폼에 지표 풀 선택 + 예상 조합수 표시 추가"
```

---

## Task 9: GridSearchHistory.tsx — 체이닝 트리거 버튼 + 폼

**Files:**
- Modify: `frontend/components/GridSearchHistory.tsx`, `frontend/components/GridSearchPage.tsx`

**Interfaces:**
- Consumes: `onSubmit: (request: GridSearchJobRequest) => Promise<void>` (신규 prop, `GridSearchPage`의 기존 `handleSubmit`을 그대로 전달).
- Produces: 결과 행에 "이 결과 기반으로 추가 탐색" 버튼 → 인라인 폼(AND/OR + 카테고리 체크박스) → `onSubmit`으로 체이닝 요청 제출.

- [ ] **Step 1: GridSearchHistoryProps에 onSubmit 추가 + 체이닝 폼 상태**

`frontend/components/GridSearchHistory.tsx:93-96`(`GridSearchHistoryProps`)을 교체:

```typescript
interface GridSearchHistoryProps {
  jobs: GridSearchJob[];
  onRefresh: () => void | Promise<void>;
  onSubmit: (request: GridSearchJobRequest) => Promise<void>;
}
```

컴포넌트 선언(`export default function GridSearchHistory({ jobs, onRefresh }: GridSearchHistoryProps) {`)을 `{ jobs, onRefresh, onSubmit }`로 바꾸고, 상태를 추가:

```typescript
  const [chainingTarget, setChainingTarget] = useState<{ jobId: string; result: GridSearchSavedResult } | null>(null);
  const [chainCombinator, setChainCombinator] = useState<'AND' | 'OR'>('AND');
  const [chainCategories, setChainCategories] = useState<string[]>([]);
  const [chainSubmitting, setChainSubmitting] = useState(false);
  const [chainError, setChainError] = useState<string | null>(null);
```

`import`에 `GridSearchJobRequest`를 추가(이미 `GridSearchJob`을 import 중이므로 같은 줄에 추가).

- [ ] **Step 2: 체이닝 트리거 버튼을 결과 행에 추가**

`frontend/components/GridSearchHistory.tsx:410-415`(각 결과 행의 마지막 `<Link>` 다음)에 버튼 추가:

```tsx
                                  <Link href={`/backtests/${r.run_id}`} className="underline">
                                    보기
                                  </Link>
                                  <Button
                                    variant="outline"
                                    size="sm"
                                    onClick={() => {
                                      setChainingTarget({ jobId: job.id, result: r });
                                      setChainCombinator('AND');
                                      setChainCategories([]);
                                      setChainError(null);
                                    }}
                                  >
                                    이 결과 기반으로 추가 탐색
                                  </Button>
```

- [ ] **Step 3: 체이닝 폼(다이얼로그) 렌더링 + 제출 핸들러**

파일 하단, 기존 `<AlertDialog>` 두 개 다음에 새 다이얼로그 추가:

```tsx
      <AlertDialog open={chainingTarget !== null} onOpenChange={(open) => !open && setChainingTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>추가 탐색 (2차 grid search)</AlertDialogTitle>
            <AlertDialogDescription>
              선택한 결과의 매수/매도 조건을 베이스로 고정하고, 아래에서 고른 지표 풀에서 새 후보 1개씩을 결합 방식으로 이어붙여 탐색합니다.
              AND는 베이스 조건을 좁히기만 해 항상 안전합니다(최악의 경우 거래 0건). OR은 베이스와 새 조건 중 하나만 맞아도 매매하므로,
              새 조건의 질이 낮으면 베이스의 성과가 오히려 나빠질 수 있습니다.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="space-y-3">
            <div className="flex gap-4">
              {(['AND', 'OR'] as const).map((c) => (
                <label key={c} className="flex items-center gap-1.5 text-sm">
                  <input type="radio" checked={chainCombinator === c} onChange={() => setChainCombinator(c)} />
                  {c}
                </label>
              ))}
            </div>
            <div className="flex flex-wrap gap-3">
              {['추세', '가격대', '거래량', '거래대금', '시장 심리', '오실레이터'].map((category) => (
                <label key={category} className="flex items-center gap-1.5 text-sm">
                  <Checkbox
                    checked={chainCategories.includes(category)}
                    onCheckedChange={(checked) =>
                      setChainCategories((prev) =>
                        checked === true ? [...prev, category] : prev.filter((c) => c !== category)
                      )
                    }
                  />
                  {category}
                </label>
              ))}
            </div>
            {chainError && <p className="text-sm text-destructive">{chainError}</p>}
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel>취소</AlertDialogCancel>
            <AlertDialogAction
              disabled={chainSubmitting}
              onClick={async (e) => {
                e.preventDefault();
                if (!chainingTarget) return;
                if (chainCategories.length === 0) {
                  setChainError('지표 카테고리를 최소 1개 이상 선택하세요.');
                  return;
                }
                const parentJob = jobs.find((j) => j.id === chainingTarget.jobId);
                if (!parentJob) return;
                setChainSubmitting(true);
                setChainError(null);
                try {
                  await onSubmit({
                    market: parentJob.market,
                    timeframe: parentJob.timeframe,
                    capital: parentJob.capital,
                    start: parentJob.start,
                    end: parentJob.end,
                    top_n: parentJob.top_n,
                    indicator_pool: { categories: chainCategories, excluded_indicators: [] },
                    base_run_id: chainingTarget.result.run_id,
                    combinator: chainCombinator,
                  });
                  setChainingTarget(null);
                  await onRefresh();
                } catch {
                  setChainError('체이닝 job 시작에 실패했습니다.');
                } finally {
                  setChainSubmitting(false);
                }
              }}
            >
              {chainSubmitting ? '시작하는 중...' : '탐색 시작'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
```

- [ ] **Step 4: GridSearchPage.tsx가 onSubmit을 GridSearchHistory에 전달**

`frontend/components/GridSearchPage.tsx:110`을 교체:

```tsx
      <GridSearchHistory jobs={jobs} onRefresh={refresh} onSubmit={handleSubmit} />
```

- [ ] **Step 5: 타입체크 + 브라우저 수동 확인**

Run: `cd frontend && npx tsc --noEmit`
Expected: PASS

Run: `cd frontend && npm run dev` 후 `/grid-search`에서 완료된 이력의 결과 행을 펼쳐 "이 결과 기반으로 추가 탐색" 클릭.
Expected: 다이얼로그가 뜨고, AND/OR 라디오와 카테고리 체크박스가 보임. 카테고리 없이 "탐색 시작"을 누르면 검증 에러. 카테고리를 골라 제출하면 새 job이 실행중 상태로 나타남(`GridSearchProgress`에 표시).

- [ ] **Step 6: 커밋**

```bash
git add frontend/components/GridSearchHistory.tsx frontend/components/GridSearchPage.tsx
git commit -m "feat: 그리드서치 이력에 결과 기반 체이닝 탐색 버튼/폼 추가"
```

---

## Task 10: GridSearchHistory.tsx — 부모-자식 트리 표시 + delta + 베이스 삭제 처리

**Files:**
- Modify: `frontend/components/GridSearchHistory.tsx`

**Interfaces:**
- Consumes: `job.base_run_id`(Task 7에서 타입 추가됨), 각 결과의 `max_drawdown_pct`/`win_rate_pct`(Task 4/7).
- Produces: `historyJobs`가 평평한 리스트 대신 부모-자식 트리로 렌더링되고, 체이닝된 job의 결과 행에 베이스 대비 delta가 표시됨.

- [ ] **Step 1: 트리 빌드 헬퍼 추가**

`frontend/components/GridSearchHistory.tsx`의 `sortJobs` 함수 다음에 추가:

```typescript
interface JobTreeNode {
  job: GridSearchJob;
  depth: number;
  baseResult: GridSearchSavedResult | null; // 체이닝의 경우 이 job이 seed로 쓴 결과, 최상위면 null
  baseOrphaned: boolean; // base_run_id는 있는데 그 결과를 찾을 수 없는 경우(베이스 job/결과가 삭제됨)
}

function buildJobTree(jobs: GridSearchJob[]): JobTreeNode[] {
  const byRunId = new Map<string, { job: GridSearchJob; result: GridSearchSavedResult }>();
  for (const job of jobs) {
    for (const result of job.result_json ?? []) {
      byRunId.set(result.run_id, { job, result });
    }
  }

  const childrenByJobId = new Map<string, GridSearchJob[]>();
  const roots: GridSearchJob[] = [];
  const orphaned: GridSearchJob[] = [];

  for (const job of jobs) {
    if (!job.base_run_id) {
      roots.push(job);
      continue;
    }
    const base = byRunId.get(job.base_run_id);
    if (!base) {
      orphaned.push(job); // 베이스 결과를 찾을 수 없음 — 최상위로 끌어올림
      continue;
    }
    const siblings = childrenByJobId.get(base.job.id) ?? [];
    siblings.push(job);
    childrenByJobId.set(base.job.id, siblings);
  }

  const nodes: JobTreeNode[] = [];
  function visit(job: GridSearchJob, depth: number, baseResult: GridSearchSavedResult | null) {
    nodes.push({ job, depth, baseResult, baseOrphaned: false });
    for (const result of job.result_json ?? []) {
      for (const child of childrenByJobId.get(job.id) ?? []) {
        if (child.base_run_id === result.run_id) {
          visit(child, depth + 1, result);
        }
      }
    }
  }
  for (const root of roots) visit(root, 0, null);
  for (const job of orphaned) nodes.push({ job, depth: 0, baseResult: null, baseOrphaned: true });

  return nodes;
}

function formatDelta(current: number, base: number, unit: string): string {
  const diff = current - base;
  const sign = diff >= 0 ? '+' : '';
  return `(베이스 대비 ${sign}${diff.toFixed(2)}${unit})`;
}
```

- [ ] **Step 2: sorted를 트리 기반으로 교체**

`frontend/components/GridSearchHistory.tsx:141`(`const sorted = useMemo(...)`) 바로 뒤에 `tree` useMemo를 추가한다. 정렬/필터(코인/봉타입)는 최상위(체이닝 안 된) job 기준으로만 적용되고, 체이닝된 하위 job은 그 계보의 최상위 job이 필터를 통과할 때만 함께 보인다:

```typescript
  const tree = useMemo(() => {
    const allNodes = buildJobTree(jobs);
    const visibleRootIds = new Set(sorted.map((j) => j.id));
    function isDescendantOfVisibleRoot(node: JobTreeNode): boolean {
      if (node.depth === 0) return visibleRootIds.has(node.job.id);
      // depth > 0인 노드는 트리 생성 순서상 항상 부모 바로 뒤에 온다는 보장이 없으므로,
      // base_run_id 체인을 거슬러 올라가 최상위 job이 필터를 통과하는지 확인한다.
      let current: GridSearchJob | undefined = node.job;
      while (current?.base_run_id) {
        const parentEntry = allNodes.find((n) =>
          (n.job.result_json ?? []).some((r) => r.run_id === current!.base_run_id)
        );
        if (!parentEntry) break;
        current = parentEntry.job;
      }
      return current ? visibleRootIds.has(current.id) : false;
    }
    return allNodes.filter(isDescendantOfVisibleRoot);
  }, [jobs, sorted]);
```

(이 헬퍼는 O(n²)이지만 로컬 grid search 이력 규모(수십~수백 건)에서는 문제없다.)

- [ ] **Step 3: 렌더링 루프를 tree 기반으로 교체 + delta 표시**

`frontend/components/GridSearchHistory.tsx:282`(`{sorted.map((job) => {`)를 `{tree.map(({ job, depth, baseResult, baseOrphaned }) => {`로 바꾸고, 첫 `<TableRow>`의 코인 셀에 들여쓰기 스타일 추가:

```tsx
                    <TableCell style={{ paddingLeft: `${depth * 20 + 8}px` }}>
                      {job.market.replace('KRW-', '')}
                      {baseOrphaned && (
                        <span className="ml-1 text-xs text-muted-foreground">(베이스 삭제됨)</span>
                      )}
                    </TableCell>
```

(기존 `<TableCell>{job.market.replace('KRW-', '')}</TableCell>`을 이걸로 대체.)

결과 행 렌더링(`expansion.results.map((r) => {`) 안, `<span className={returnRateColor(r.return_pct)}>{r.return_pct.toFixed(2)}%</span>` 다음에 delta 추가:

```tsx
                                  <span className={returnRateColor(r.return_pct)}>
                                    {r.return_pct.toFixed(2)}%
                                    {baseResult && (
                                      <span className="ml-1 text-xs text-muted-foreground">
                                        {formatDelta(r.return_pct, baseResult.return_pct, '%p')}
                                      </span>
                                    )}
                                  </span>
```

(MDD/거래수 delta도 같은 패턴으로 `r.max_drawdown_pct`/`r.trade_count`와 `baseResult.max_drawdown_pct`/`baseResult.trade_count`를 비교해 트레이드 카운트 옆에 붙인다 — trade_count는 `formatFrequency` 옆에 `baseResult.trade_count`가 있을 때만 `(베이스 대비 {diff}건)` 형태로 추가.)

- [ ] **Step 4: 타입체크 + 브라우저 수동 확인**

Run: `cd frontend && npx tsc --noEmit`
Expected: PASS

Run: `cd frontend && npm run dev` 후 `/grid-search`에서 Task 9로 만든 체이닝 job을 확인.
Expected: 체이닝된 job이 베이스 결과가 있던 job 아래에 들여써서 나타남. 체이닝 job을 펼치면 결과 행에 베이스 대비 수익률 delta가 "(베이스 대비 +X.XX%p)" 형태로 표시됨. 베이스가 된 job을 삭제(Trash 아이콘)한 뒤 새로고침하면, 체이닝 job이 "(베이스 삭제됨)" 표시와 함께 최상위로 올라옴.

- [ ] **Step 5: 커밋**

```bash
git add frontend/components/GridSearchHistory.tsx
git commit -m "feat: 그리드서치 이력에 체이닝 트리 표시 + 베이스 대비 delta 추가"
```

---

## Self-Review 메모

- **스펙 커버리지:** 1차 카테고리 선택(Task 2,3,8) / 2차 체이닝(Task 4,9) / 이력 트리+delta(Task 10) / 예상 조합수 안내(Task 6,8) / 베이스 삭제 처리(Task 10) 모두 태스크로 커버됨. 스펙의 "손익 카테고리는 항상 포함" 결정은 Task 2의 `INDICATOR_POOL_SPECS`에서 의도적으로 제외하고 Global Constraints에 명시함.
- **커버 안 된 스펙 조각:** 스펙 문서의 "코인 스윕 기능"은 명시적으로 스코프 아님(별도 세션).
- **타입 일관성:** `IndicatorPool`(Task 7) → `GridSearchForm`(Task 8)/`GridSearchHistory`(Task 9) 전체에서 동일한 `{categories, excluded_indicators}` 형태로 사용. `_wrap_condition`(Task 4)이 `build_condition_grid`(Task 2)가 반환하는 flat block과 `get_run_config`(Task 4)가 반환하는 이미-wrapped ConditionGroup을 정확히 구분해서 다룸.
