# 백테스트 상세 페이지 "최신 데이터로 갱신" 버튼 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 백테스트 상세 페이지에서 버튼 클릭 한 번으로 같은 조건(시장/봉타입/시작일/조건식/리스크설정)을 유지한 채 종료일만 지금 시각으로 바꿔 재실행하고, 그 결과로 **같은 run_id를 덮어쓴다** (새 런을 만들지 않는다).

**Architecture:** `backend/main.py`의 `/api/v1/backtests/run` 안에 있던 "캔들 조회 + 필요 봉수 검증 + 보조지표(KOREA_PREMIUM/FEAR_GREED_CMC) 병합" 로직을 `_fetch_backtest_dataframe()` 헬퍼로 추출해 재사용한다. 새 엔드포인트 `POST /api/v1/backtests/{run_id}/refresh`는 `engine/cache.py`에 새로 추가하는 `get_run_config(run_id)`로 기존 런의 저장된 설정(시장/봉타입/시작일/조건식/리스크설정/제목/설명)을 읽고, `end`만 `datetime.now(timezone.utc)`로 바꿔 위 헬퍼로 데이터프레임을 만든 뒤 `engine.runner.run_backtest()`를 직접 호출한다(캐시 조회용 `run_backtest_cached()`는 쓰지 않는다 — end가 바뀌면 cache key도 바뀌어 새 run_id가 생기므로, 대신 결과를 기존 `save_result()`로 **원래 run_id에 그대로 덮어쓴다**). 프론트는 상세 페이지(`frontend/app/backtests/[runId]/page.tsx`, 서버 컴포넌트)에 클라이언트 컴포넌트 `RefreshBacktestButton`을 얹어 클릭 시 API 호출 후 `router.refresh()`로 같은 URL의 서버 컴포넌트를 다시 렌더링한다.

**Tech Stack:** FastAPI, SQLite(engine/cache.py), pytest + TestClient, Next.js 14 App Router (서버 컴포넌트 + `'use client'` 하위 컴포넌트), base-ui 기반 shadcn 스타일 `Button`.

## Global Constraints

- run_id는 절대 바뀌지 않는다 — 갱신은 기존 `backtest_runs`/`backtest_results` 행을 `INSERT OR REPLACE`로 덮어쓰는 것이지, 새 행을 추가하는 게 아니다. (이미 있는 [[upbit-v1-backtest-refresh-tracking]] 결정사항: 옵션 B)
- 새 `end`는 `datetime.now(timezone.utc)`를 그대로 쓴다 (당일 23:59:59로 반올림하지 않는다) — 상세 페이지의 기존 실시간 재평가 로직(`backend/main.py:441`)이 이미 `now = datetime.now(timezone.utc)`를 "지금"의 기준으로 쓰고 있어 일관성을 맞춘다.
- `get_run_config()`는 `ConditionTreeStrategy` 런에서만 쓰이는 걸 전제로 `params_json`에 `buy_conditions`/`sell_conditions` 키가 있다고 가정한다 — `engine/cache.py`의 기존 `list_backtest_runs()`(line 458-459)와 동일한 전제.
- 프론트 버튼은 백테스트 **상세 페이지**(`frontend/app/backtests/[runId]/page.tsx`)의 상단 날짜 줄(`{detail.market} · {detail.timeframe} · ...`) 옆에만 추가한다. 목록 페이지(`BacktestRunsTable.tsx`)에는 추가하지 않는다.

---

## Task 1: `engine/cache.py`에 `get_run_config()` 추가

**Files:**
- Modify: `engine/cache.py` (새 함수를 `load_result()` 근처에 추가)
- Test: `tests/test_cache.py`

**Interfaces:**
- Produces: `get_run_config(run_id: str) -> dict | None` — 반환 dict 키: `strategy_name`, `market`, `timeframe`, `start`(ISO 문자열), `risk_config`(dict), `buy_conditions`(dict), `sell_conditions`(dict), `title`(str|None), `description`(str|None). run_id가 없으면 `None`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_cache.py` 끝에 추가:

```python
def test_get_run_config_returns_stored_config(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    save_result(
        run_id="r1", strategy_name="ConditionTreeStrategy",
        strategy_params={
            "buy_conditions": {"type": "AND", "conditions": [{"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30}]},
            "sell_conditions": {"type": "AND", "conditions": [{"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 70}]},
        },
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 3, 1, tzinfo=timezone.utc),
        risk_config={"initial_capital": 1_000_000, "commission_rate": 0.0005},
        result={"final_value": 1_100_000.0, "sharpe": 1.0, "max_drawdown": 5.0, "equity_curve": [], "trades": []},
        title="추적용", description="설명",
    )

    config = get_run_config("r1")

    assert config is not None
    assert config["strategy_name"] == "ConditionTreeStrategy"
    assert config["market"] == "KRW-BTC"
    assert config["timeframe"] == "days"
    assert config["start"].startswith("2026-01-01")
    assert config["risk_config"] == {"initial_capital": 1_000_000, "commission_rate": 0.0005}
    assert config["buy_conditions"]["conditions"][0]["indicator"] == "RSI"
    assert config["sell_conditions"]["conditions"][0]["threshold"] == 70
    assert config["title"] == "추적용"
    assert config["description"] == "설명"


def test_get_run_config_returns_none_for_missing_run(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    assert get_run_config("does-not-exist") is None
```

파일 상단 import에 `get_run_config`를 추가한다 (기존 `from engine.cache import save_result, ...` 형태의 import 라인을 확인하고 맞춰 추가).

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `python -m pytest tests/test_cache.py -k get_run_config -v`
Expected: FAIL with `ImportError` or `NameError: name 'get_run_config' is not defined`

- [ ] **Step 3: `engine/cache.py`에 구현 추가**

`load_result()` 함수 바로 다음에 추가:

```python
def get_run_config(run_id: str) -> dict | None:
    """run_id로 저장된 실행 설정(시장/봉타입/시작일/조건식/리스크설정 등)을 반환한다.
    "최신 데이터로 갱신" 기능처럼 같은 조건으로 end만 바꿔 재실행할 때 쓴다."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT strategy_name, market, timeframe, start, risk_config_json, params_json, title, description "
            "FROM backtest_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return None

    strategy_name, market, timeframe, start, risk_config_json, params_json, title, description = row
    params = json.loads(params_json)
    return {
        "strategy_name": strategy_name,
        "market": market,
        "timeframe": timeframe,
        "start": start,
        "risk_config": json.loads(risk_config_json),
        "buy_conditions": params["buy_conditions"],
        "sell_conditions": params["sell_conditions"],
        "title": title,
        "description": description,
    }
```

- [ ] **Step 4: 테스트 실행해 통과 확인**

Run: `python -m pytest tests/test_cache.py -k get_run_config -v`
Expected: PASS (2 passed)

- [ ] **Step 5: 커밋**

```bash
git add engine/cache.py tests/test_cache.py
git commit -m "feat: add get_run_config for reading a stored backtest run's config"
```

---

## Task 2: `backend/main.py`에서 캔들 조회+보조지표 병합 로직을 `_fetch_backtest_dataframe()`로 추출

이 태스크는 순수 리팩터링(동작 변경 없음) — 기존 `/api/v1/backtests/run`, `/api/v1/backtests/validate` 관련 테스트가 그대로 그린이면 성공.

**Files:**
- Modify: `backend/main.py:591-682` (기존 `run_backtest_endpoint` 본문 중 캔들 조회~보조지표 병합 부분)
- Test: 기존 `tests/test_backend.py`의 `test_run_backtest_*` 테스트들을 회귀 테스트로 사용 (새 테스트 추가 없음)

**Interfaces:**
- Produces: `_fetch_backtest_dataframe(market: str, timeframe: str, start_dt: datetime, end_dt: datetime, buy_dict: dict, sell_dict: dict) -> pd.DataFrame` — 성공 시 지표 계산에 필요한 컬럼까지 병합된 DataFrame을 반환. 실패 시 기존과 동일하게 `HTTPException(400 또는 500)`을 던진다.
- Consumes: Task 1에서는 쓰지 않음 (Task 3에서 이 함수를 재사용).

- [ ] **Step 1: 리팩터링 전 기준 테스트 통과 확인 (베이스라인)**

Run: `python -m pytest tests/test_backend.py -k run_backtest -v`
Expected: 기존 테스트 전부 PASS (지금 상태에서도 이미 통과해야 함 — 리팩터링 안전망 확인용)

- [ ] **Step 2: `_fetch_backtest_dataframe()` 함수 추가**

`backend/main.py`에서 `run_backtest_endpoint` 함수 바로 위에 추가:

```python
def _fetch_backtest_dataframe(
    market: str, timeframe: str, start_dt: datetime, end_dt: datetime,
    buy_dict: dict, sell_dict: dict,
):
    try:
        df = get_candles(market, timeframe, start_dt, end_dt)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if df.empty:
        raise HTTPException(status_code=400, detail="해당 기간에 캔들 데이터가 없습니다")

    required_bars = max(max_required_period(buy_dict), max_required_period(sell_dict))
    if len(df) < required_bars:
        raise HTTPException(
            status_code=400,
            detail=(
                f"선택한 지표가 최소 {required_bars}개의 봉을 필요로 하지만, "
                f"해당 기간에는 {len(df)}개의 봉만 있습니다. 기간을 늘리거나 지표 파라미터를 줄이세요."
            ),
        )

    aux_markets = required_aux_markets(buy_dict) | required_aux_markets(sell_dict)
    for aux_market in aux_markets:
        line_name = AUX_MARKET_LINE_NAME[aux_market]
        if market == aux_market:
            df = df.assign(**{line_name: df["close"]})
            continue
        try:
            aux_df = get_candles(aux_market, timeframe, start_dt, end_dt)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        if aux_df.empty:
            raise HTTPException(
                status_code=400,
                detail=f"이 조건에 필요한 {aux_market} 캔들 데이터가 해당 기간에 없습니다",
            )
        df = df.merge(
            aux_df[["candle_time", "close"]].rename(columns={"close": line_name}),
            on="candle_time",
            how="left",
        )
        if df[line_name].isna().all():
            raise HTTPException(
                status_code=400,
                detail=f"이 조건에 필요한 {aux_market} 캔들 데이터가 해당 기간에 없습니다",
            )
        df[line_name] = df[line_name].ffill().bfill()

    used_indicators = {
        b["indicator"] for b in collect_blocks(buy_dict) + collect_blocks(sell_dict)
    }
    if "FEAR_GREED_CMC" in used_indicators:
        try:
            fng_df = get_fear_greed_cmc(start_dt, end_dt)
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        df = merge_fear_greed(df, fng_df)
        if df["fear_greed_value"].isna().any():
            raise HTTPException(
                status_code=400,
                detail="이 조건에 필요한 공포탐욕지수 데이터가 해당 기간에 없습니다 (2018-02-01 이전 구간은 지원하지 않습니다)",
            )

    if "KOREA_PREMIUM" in used_indicators:
        symbol = binance_symbol(market)
        try:
            binance_df = get_binance_close(symbol, timeframe, start_dt, end_dt)
        except BinanceSymbolNotFoundError:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{market}에 대응하는 바이낸스 심볼({symbol})이 없어 "
                    f"한국프리미엄을 계산할 수 없습니다"
                ),
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        df = df.merge(
            binance_df.rename(columns={"close": "binance_close"}), on="candle_time", how="left"
        )
        if df["binance_close"].isna().any():
            raise HTTPException(
                status_code=400, detail=f"해당 기간에 {symbol} 캔들 데이터가 없습니다"
            )
        df["korea_premium_value"] = (df["close"] / (df["binance_close"] * df["usdt_close"]) - 1) * 100

    return df
```

- [ ] **Step 3: `run_backtest_endpoint`가 새 헬퍼를 쓰도록 수정**

`run_backtest_endpoint` 안의 캔들 조회~보조지표 병합 블록(원래 `try: df = get_candles(...)` 부터 `df["korea_premium_value"] = ...` 까지)을 지우고 다음으로 교체:

```python
    buy_dict = req.buy_conditions.model_dump()
    sell_dict = req.sell_conditions.model_dump()
    df = _fetch_backtest_dataframe(req.market, req.timeframe, start_dt, end_dt, buy_dict, sell_dict)
```

(그 아래 `risk_config = {**DEFAULT_RISK_CONFIG, "initial_capital": req.initial_capital}`부터 `return {"run_id": result["run_id"]}`까지는 그대로 둔다. `used_indicators` 지역변수는 더 이상 `run_backtest_endpoint`에서 쓰이지 않으므로 같이 제거된다 — 헬퍼 안에서만 쓰인다.)

- [ ] **Step 4: 회귀 테스트 통과 확인**

Run: `python -m pytest tests/test_backend.py -k "run_backtest or validate_backtest" -v`
Expected: 리팩터링 전과 동일하게 전부 PASS (동작 변경 없음을 확인)

- [ ] **Step 5: 커밋**

```bash
git add backend/main.py
git commit -m "refactor: extract candle fetch + aux indicator merge into _fetch_backtest_dataframe"
```

---

## Task 3: `POST /api/v1/backtests/{run_id}/refresh` 엔드포인트 추가

**Files:**
- Modify: `backend/main.py` (import 목록 + 새 엔드포인트를 `delete_backtest` 근처에 추가)
- Test: `tests/test_backend.py`

**Interfaces:**
- Consumes: Task 1의 `get_run_config(run_id)`, Task 2의 `_fetch_backtest_dataframe(...)`, `engine.cache.save_result(...)` (기존 함수), `engine.runner.run_backtest(...)` (기존 함수, 시그니처: `run_backtest(df, strategy_cls, risk_config, strategy_params) -> dict`).
- Produces: `POST /api/v1/backtests/{run_id}/refresh` → 성공 시 `{"run_id": run_id}` (입력과 동일한 run_id). run_id가 없으면 404.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_backend.py` 끝에 추가 (파일 상단 import에 `from engine.runner import run_backtest`는 필요 없음 — 테스트는 엔드포인트만 호출):

```python
def test_refresh_backtest_returns_404_for_missing_run(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    resp = client.post("/api/v1/backtests/does-not-exist/refresh")
    assert resp.status_code == 404


def test_refresh_backtest_keeps_same_run_id_and_updates_end(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch)

    create_resp = client.post("/api/v1/backtests/run", json=_run_request())
    run_id = create_resp.json()["run_id"]
    original_end = client.get(f"/api/v1/backtests/{run_id}").json()["end"]

    refresh_resp = client.post(f"/api/v1/backtests/{run_id}/refresh")

    assert refresh_resp.status_code == 200
    assert refresh_resp.json() == {"run_id": run_id}

    detail_resp = client.get(f"/api/v1/backtests/{run_id}")
    assert detail_resp.status_code == 200
    assert detail_resp.json()["end"] != original_end

    list_resp = client.get("/api/v1/backtests")
    assert len(list_resp.json()) == 1, "덮어쓰기이므로 목록에 런이 늘어나면 안 됨"


def test_refresh_backtest_preserves_title_and_description(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch)

    create_resp = client.post(
        "/api/v1/backtests/run",
        json=_run_request(title="추적용", description="설명"),
    )
    run_id = create_resp.json()["run_id"]

    client.post(f"/api/v1/backtests/{run_id}/refresh")

    list_resp = client.get("/api/v1/backtests")
    run = next(r for r in list_resp.json() if r["run_id"] == run_id)
    assert run["title"] == "추적용"
    assert run["description"] == "설명"
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `python -m pytest tests/test_backend.py -k refresh_backtest -v`
Expected: FAIL — `test_refresh_backtest_returns_404_for_missing_run`이 404가 아니라 405(Method Not Allowed)로 실패

- [ ] **Step 3: 엔드포인트 구현**

`backend/main.py` 상단 import 수정: `from engine.cache import (` 블록에 `get_run_config`을 추가하고, `from engine.runner import AUX_MARKET_LINE_NAME`을 `from engine.runner import AUX_MARKET_LINE_NAME, run_backtest`로 바꾼다.

`delete_backtest` 엔드포인트 바로 다음에 추가:

```python
@app.post("/api/v1/backtests/{run_id}/refresh")
def refresh_backtest_endpoint(run_id: str) -> dict:
    config = get_run_config(run_id)
    if config is None:
        raise HTTPException(status_code=404, detail="해당 run_id의 백테스트 결과를 찾을 수 없습니다")

    start_dt = datetime.fromisoformat(config["start"])
    end_dt = datetime.now(timezone.utc)
    if end_dt <= start_dt:
        raise HTTPException(status_code=400, detail="시작일이 아직 지나지 않아 갱신할 수 없습니다")

    buy_dict = config["buy_conditions"]
    sell_dict = config["sell_conditions"]
    df = _fetch_backtest_dataframe(config["market"], config["timeframe"], start_dt, end_dt, buy_dict, sell_dict)

    result = run_backtest(
        df, ConditionTreeStrategy, config["risk_config"],
        {"buy_conditions": buy_dict, "sell_conditions": sell_dict},
    )
    save_result(
        run_id=run_id,
        strategy_name=config["strategy_name"],
        strategy_params={"buy_conditions": buy_dict, "sell_conditions": sell_dict},
        market=config["market"],
        timeframe=config["timeframe"],
        start=start_dt,
        end=end_dt,
        risk_config=config["risk_config"],
        result=result,
        title=config["title"],
        description=config["description"],
    )
    return {"run_id": run_id}
```

`save_result`는 이미 `engine.cache`에서 import되어 있는지 확인한다(기존 `from engine.cache import (` 블록에 `save_result`가 없으면 추가).

- [ ] **Step 4: 테스트 실행해 통과 확인**

Run: `python -m pytest tests/test_backend.py -k refresh_backtest -v`
Expected: PASS (3 passed)

- [ ] **Step 5: 전체 백엔드 테스트 스위트 통과 확인**

Run: `python -m pytest tests/ -v`
Expected: 전부 PASS (리팩터링과 새 엔드포인트가 기존 기능을 깨지 않았음을 확인)

- [ ] **Step 6: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "feat: add POST /api/v1/backtests/{run_id}/refresh to re-run with today's end date"
```

---

## Task 4: 프론트 API 클라이언트에 `refreshBacktestRun` 추가

**Files:**
- Modify: `frontend/lib/api/eda.ts`

**Interfaces:**
- Produces: `refreshBacktestRun(runId: string): Promise<{ run_id: string }>`

- [ ] **Step 1: `frontend/lib/api/eda.ts`에 함수 추가**

`deleteBacktestRun` 함수 바로 다음에 추가:

```typescript
export function refreshBacktestRun(runId: string): Promise<{ run_id: string }> {
  return apiFetch<{ run_id: string }>(`/api/v1/backtests/${runId}/refresh`, {
    method: 'POST',
  });
}
```

- [ ] **Step 2: 타입체크 통과 확인**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 3: 커밋**

```bash
git add frontend/lib/api/eda.ts
git commit -m "feat: add refreshBacktestRun API client function"
```

---

## Task 5: `RefreshBacktestButton` 클라이언트 컴포넌트 추가 및 상세 페이지에 배치

**Files:**
- Create: `frontend/components/RefreshBacktestButton.tsx`
- Modify: `frontend/app/backtests/[runId]/page.tsx:89-91`

**Interfaces:**
- Consumes: Task 4의 `refreshBacktestRun(runId: string): Promise<{ run_id: string }>`, `ApiError`(`frontend/lib/api/client.ts`에서 이미 export됨).
- Produces: `<RefreshBacktestButton runId={string} />` — 기본 export.

- [ ] **Step 1: `RefreshBacktestButton.tsx` 작성**

```tsx
'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { refreshBacktestRun } from '@/lib/api/eda';
import { ApiError } from '@/lib/api/client';

export default function RefreshBacktestButton({ runId }: { runId: string }) {
  const router = useRouter();
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleRefresh() {
    setRefreshing(true);
    setError(null);
    try {
      await refreshBacktestRun(runId);
      router.refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '갱신에 실패했습니다.');
    } finally {
      setRefreshing(false);
    }
  }

  return (
    <span className="inline-flex items-center gap-2">
      <Button variant="outline" size="sm" onClick={handleRefresh} disabled={refreshing}>
        <RefreshCw className={refreshing ? 'size-3.5 animate-spin' : 'size-3.5'} />
        {refreshing ? '갱신 중...' : '최신 데이터로 갱신'}
      </Button>
      {error && <span className="text-xs text-destructive">{error}</span>}
    </span>
  );
}
```

- [ ] **Step 2: 상세 페이지에 배치**

`frontend/app/backtests/[runId]/page.tsx` 상단 import에 추가:

```typescript
import RefreshBacktestButton from '@/components/RefreshBacktestButton';
```

기존:

```tsx
      <p className="mb-1 text-sm text-muted-foreground">
        {detail.market} · {detail.timeframe} · {detail.start.slice(0, 10)} ~ {detail.end.slice(0, 10)}
      </p>
```

다음으로 교체:

```tsx
      <div className="mb-1 flex flex-wrap items-center gap-3">
        <p className="text-sm text-muted-foreground">
          {detail.market} · {detail.timeframe} · {detail.start.slice(0, 10)} ~ {detail.end.slice(0, 10)}
        </p>
        <RefreshBacktestButton runId={params.runId} />
      </div>
```

- [ ] **Step 3: 타입체크 통과 확인**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 4: 개발 서버에서 수동 확인**

`http://localhost:3000/backtests/{run_id}` 접속 (실제 존재하는 run_id로) →
1. 날짜 줄 옆에 "최신 데이터로 갱신" 버튼이 보이는지 확인.
2. 클릭 시 버튼이 "갱신 중..."으로 바뀌고, 완료 후 페이지의 종료일(위 날짜 줄의 `~` 뒤 날짜)이 오늘 날짜로 바뀌는지 확인.
3. 페이지를 새로고침해도 URL의 run_id가 그대로인지 확인(새 런이 생기지 않음).

- [ ] **Step 5: 커밋**

```bash
git add frontend/components/RefreshBacktestButton.tsx frontend/app/backtests/[runId]/page.tsx
git commit -m "feat: add refresh button to backtest detail page"
```
