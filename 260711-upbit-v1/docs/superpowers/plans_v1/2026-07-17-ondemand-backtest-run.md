# 온디맨드 백테스트 실행 화면 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/backtests` 탭에서 사용자가 코인/봉타입/전략/기간을 선택하면 그 자리에서 새 백테스트를 실행하고 결과 상세 화면으로 이동시킨다.

**Architecture:** FastAPI에 신호 목록 조회(`GET /api/v1/eda/signals`)와 온디맨드 실행(`POST /api/v1/backtests/run`) 엔드포인트를 추가한다. 실행 엔드포인트는 기존 `get_candles()` + `run_backtest_cached()`를 그대로 호출해 `run_id`만 반환한다. 프런트엔드는 `'use client'` 폼 컴포넌트가 이 두 엔드포인트를 직접 호출하고, 성공 시 기존 `/backtests/[runId]` 상세 화면으로 이동한다(상세 화면 자체는 수정하지 않음).

**Tech Stack:** FastAPI, pydantic, pytest + `fastapi.testclient.TestClient`, Next.js App Router, React(`'use client'`), TypeScript.

## Global Constraints

- 리스크 설정은 `engine.sweep.DEFAULT_RISK_CONFIG`를 그대로 사용한다 — 폼에 노출하지 않는다.
- 온디맨드 실행 결과는 `backtest_results` 캐시에만 저장하고 `sweep_history`에는 기록하지 않는다.
- 코인 목록은 `["KRW-BTC", "KRW-ETH"]`, 봉타입 목록은 `days`(일봉)/`minutes240`(4시간봉)/`minutes60`(1시간봉)/`minutes15`(15분봉)로 프런트에 고정한다.
- 전략 체크박스 목록은 백엔드 `SIGNAL_REGISTRY`에서 동적으로 가져온다(하드코딩 금지).
- 기존 `/backtests/[runId]/page.tsx`, `EquityCurveChart`, `engine/*`, `signals.py`는 수정하지 않는다.

---

### Task 1: 백엔드 — `GET /api/v1/eda/signals`

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_backend.py`

**Interfaces:**
- Produces: `GET /api/v1/eda/signals` → `200` + JSON 배열(`list[str]`), `signals.SIGNAL_REGISTRY`의 키를 정렬한 목록.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_backend.py` 끝에 추가:

```python
def test_get_signals_returns_registered_signal_keys():
    from signals import SIGNAL_REGISTRY

    client = TestClient(app)
    resp = client.get("/api/v1/eda/signals")
    assert resp.status_code == 200
    assert resp.json() == sorted(SIGNAL_REGISTRY.keys())
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_backend.py::test_get_signals_returns_registered_signal_keys -v`
Expected: FAIL — `404 Not Found` (엔드포인트가 아직 없음)

- [ ] **Step 3: 구현 작성**

`backend/main.py` 상단 import 블록을 아래로 교체(기존 `from engine.cache import (...)` 바로 아래에 추가):

```python
from engine.cache import (
    list_combined_ranking,
    list_distinct_combos,
    list_latest_sweep_results,
    list_sweep_history,
    load_result,
)
from signals import SIGNAL_REGISTRY
```

`get_combos()` 엔드포인트 아래(51번째 줄 `@app.get("/api/v1/eda/history")` 바로 위)에 추가:

```python
@app.get("/api/v1/eda/signals")
def get_signals() -> list[str]:
    return sorted(SIGNAL_REGISTRY.keys())
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_backend.py::test_get_signals_returns_registered_signal_keys -v`
Expected: PASS

- [ ] **Step 5: 전체 백엔드 테스트 스위트 확인**

Run: `pytest tests/test_backend.py -v`
Expected: 기존 테스트 전부 PASS + 새 테스트 PASS

- [ ] **Step 6: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "feat: add GET /api/v1/eda/signals endpoint"
```

---

### Task 2: 백엔드 — `POST /api/v1/backtests/run`

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_backend.py`

**Interfaces:**
- Consumes: `engine.cache.run_backtest_cached(df, strategy_cls, risk_config, market, timeframe, start, end, strategy_params)` (기존), `engine.sweep.DEFAULT_RISK_CONFIG`(기존), `engine.strategies.SignalStrategy`(기존), `signals.SIGNAL_REGISTRY`(기존, Task 1에서 이미 import), `upbit_data_service.get_candles(market, timeframe, start, end)`(기존).
- Produces: `POST /api/v1/backtests/run` — 요청 바디 `{market, timeframe, start, end, signal_keys}` → 성공 시 `200` + `{"run_id": str}`, 실패 시 `400`/`500` + `{"detail": str}`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_backend.py` 상단 import 블록에 추가:

```python
import pandas as pd

from tests.signal_fixtures import make_oscillating_df
```

파일 끝에 추가:

```python
def _patch_get_candles(monkeypatch, df: pd.DataFrame | None = None):
    monkeypatch.setattr(
        backend_module, "get_candles",
        lambda market, timeframe, start, end: df if df is not None else make_oscillating_df(),
    )


def test_run_backtest_returns_run_id_and_is_retrievable(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch)

    resp = client.post(
        "/api/v1/backtests/run",
        json={
            "market": "KRW-BTC",
            "timeframe": "days",
            "start": "2026-01-01",
            "end": "2026-03-01",
            "signal_keys": ["macd_cross"],
        },
    )
    assert resp.status_code == 200
    run_id = resp.json()["run_id"]
    assert run_id

    detail_resp = client.get(f"/api/v1/backtests/{run_id}")
    assert detail_resp.status_code == 200
    assert "final_value" in detail_resp.json()


def test_run_backtest_rejects_empty_signal_keys(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch)

    resp = client.post(
        "/api/v1/backtests/run",
        json={
            "market": "KRW-BTC",
            "timeframe": "days",
            "start": "2026-01-01",
            "end": "2026-03-01",
            "signal_keys": [],
        },
    )
    assert resp.status_code == 400


def test_run_backtest_rejects_unknown_signal_key(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch)

    resp = client.post(
        "/api/v1/backtests/run",
        json={
            "market": "KRW-BTC",
            "timeframe": "days",
            "start": "2026-01-01",
            "end": "2026-03-01",
            "signal_keys": ["no_such_signal"],
        },
    )
    assert resp.status_code == 400


def test_run_backtest_rejects_reversed_date_range(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch)

    resp = client.post(
        "/api/v1/backtests/run",
        json={
            "market": "KRW-BTC",
            "timeframe": "days",
            "start": "2026-03-01",
            "end": "2026-01-01",
            "signal_keys": ["macd_cross"],
        },
    )
    assert resp.status_code == 400


def test_run_backtest_rejects_empty_candle_range(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    empty_df = pd.DataFrame(columns=["candle_time", "open", "high", "low", "close", "volume"])
    _patch_get_candles(monkeypatch, df=empty_df)

    resp = client.post(
        "/api/v1/backtests/run",
        json={
            "market": "KRW-BTC",
            "timeframe": "days",
            "start": "2026-01-01",
            "end": "2026-03-01",
            "signal_keys": ["macd_cross"],
        },
    )
    assert resp.status_code == 400
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_backend.py -k run_backtest -v`
Expected: FAIL — `404 Not Found` (엔드포인트가 아직 없음)

- [ ] **Step 3: 구현 작성**

`backend/main.py` import 블록을 아래 전체로 교체:

```python
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from engine.cache import (
    list_combined_ranking,
    list_distinct_combos,
    list_latest_sweep_results,
    list_sweep_history,
    load_result,
    run_backtest_cached,
)
from engine.strategies import SignalStrategy
from engine.sweep import DEFAULT_RISK_CONFIG
from signals import SIGNAL_REGISTRY
from upbit_data_service import get_candles
```

파일 맨 아래(`get_backtest_detail` 함수 뒤)에 추가:

```python
class RunBacktestRequest(BaseModel):
    market: str
    timeframe: str
    start: str
    end: str
    signal_keys: list[str]


@app.post("/api/v1/backtests/run")
def run_backtest_endpoint(req: RunBacktestRequest) -> dict:
    if not req.signal_keys:
        raise HTTPException(status_code=400, detail="전략을 최소 1개 선택해야 합니다")

    unknown_keys = [k for k in req.signal_keys if k not in SIGNAL_REGISTRY]
    if unknown_keys:
        raise HTTPException(status_code=400, detail=f"등록되지 않은 전략입니다: {unknown_keys}")

    if req.start >= req.end:
        raise HTTPException(status_code=400, detail="시작일은 종료일보다 빨라야 합니다")

    start_dt = datetime.strptime(req.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(req.end, "%Y-%m-%d").replace(
        hour=23, minute=59, second=59, tzinfo=timezone.utc
    )

    try:
        df = get_candles(req.market, req.timeframe, start_dt, end_dt)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if df.empty:
        raise HTTPException(status_code=400, detail="해당 기간에 캔들 데이터가 없습니다")

    signals = [SIGNAL_REGISTRY[k] for k in req.signal_keys]

    result = run_backtest_cached(
        df=df,
        strategy_cls=SignalStrategy,
        risk_config=DEFAULT_RISK_CONFIG,
        market=req.market,
        timeframe=req.timeframe,
        start=start_dt,
        end=end_dt,
        strategy_params={"signals": signals},
    )
    return {"run_id": result["run_id"]}
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_backend.py -v`
Expected: 전체 PASS (Task 1 테스트 포함)

- [ ] **Step 5: 수동 확인**

```bash
uvicorn backend.main:app --port 8000 &
sleep 2
curl -s -X POST http://localhost:8000/api/v1/backtests/run \
  -H "Content-Type: application/json" \
  -d '{"market":"KRW-BTC","timeframe":"days","start":"2026-04-01","end":"2026-07-01","signal_keys":["macd_cross"]}'
```
Expected: `{"run_id": "<sha256 문자열>"}` (실제 Upbit API를 호출하므로 네트워크가 필요하고 수 초 걸릴 수 있음)

- [ ] **Step 6: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "feat: add POST /api/v1/backtests/run on-demand endpoint"
```

---

### Task 3: 프런트엔드 — 타입 정의 및 API 클라이언트 함수

**Files:**
- Modify: `frontend/lib/types/eda.ts`
- Modify: `frontend/lib/api/eda.ts`

**Interfaces:**
- Consumes: `apiFetch<T>(endpoint, options)` (기존, `frontend/lib/api/client.ts`).
- Produces: `RunBacktestRequest`, `RunBacktestResponse` 타입 / `getSignals(): Promise<string[]>`, `runBacktest(req: RunBacktestRequest): Promise<RunBacktestResponse>` 함수. Task 4가 이 두 함수를 그대로 가져다 쓴다.

- [ ] **Step 1: 타입 추가**

`frontend/lib/types/eda.ts` 파일 끝에 추가:

```typescript
export interface RunBacktestRequest {
  market: string;
  timeframe: string;
  start: string;
  end: string;
  signal_keys: string[];
}

export interface RunBacktestResponse {
  run_id: string;
}
```

- [ ] **Step 2: API 함수 추가**

`frontend/lib/api/eda.ts` import 줄을 아래로 교체:

```typescript
import { apiFetch } from './client';
import type { BacktestDetail, Combo, RunBacktestRequest, RunBacktestResponse, SweepResult } from '@/lib/types/eda';
```

파일 끝에 추가:

```typescript
export function getSignals(): Promise<string[]> {
  return apiFetch<string[]>('/api/v1/eda/signals');
}

export function runBacktest(req: RunBacktestRequest): Promise<RunBacktestResponse> {
  return apiFetch<RunBacktestResponse>('/api/v1/backtests/run', {
    method: 'POST',
    body: JSON.stringify(req),
  });
}
```

- [ ] **Step 3: 타입 체크 확인**

Run (in `frontend/`): `npx tsc --noEmit`
Expected: 에러 없음 (아직 아무 컴포넌트도 이 함수들을 쓰지 않으므로 미사용 경고는 발생하지 않음)

- [ ] **Step 4: 커밋**

```bash
git add frontend/lib/types/eda.ts frontend/lib/api/eda.ts
git commit -m "feat: add types and API client functions for on-demand backtest run"
```

---

### Task 4: 프런트엔드 — `BacktestRunForm` 컴포넌트

**Files:**
- Create: `frontend/components/BacktestRunForm.tsx`

**Interfaces:**
- Consumes: `getSignals()`, `runBacktest()` (Task 3), `ApiError` (기존, `frontend/lib/api/client.ts`), `Button` (기존, `frontend/components/ui/button.tsx`).
- Produces: `export default function BacktestRunForm()` — Task 5의 `/backtests` 페이지가 그대로 렌더링한다.

- [ ] **Step 1: 컴포넌트 작성**

`frontend/components/BacktestRunForm.tsx` 새로 생성:

```tsx
'use client';

import { useEffect, useState, type FormEvent } from 'react';
import { useRouter } from 'next/navigation';
import { Button } from '@/components/ui/button';
import { getSignals, runBacktest } from '@/lib/api/eda';
import { ApiError } from '@/lib/api/client';

const MARKETS = ['KRW-BTC', 'KRW-ETH'];

const TIMEFRAMES = [
  { value: 'days', label: '일봉' },
  { value: 'minutes240', label: '4시간봉' },
  { value: 'minutes60', label: '1시간봉' },
  { value: 'minutes15', label: '15분봉' },
];

function defaultDate(daysAgo: number): string {
  const d = new Date();
  d.setDate(d.getDate() - daysAgo);
  return d.toISOString().slice(0, 10);
}

export default function BacktestRunForm() {
  const router = useRouter();
  const [signals, setSignals] = useState<string[]>([]);
  const [market, setMarket] = useState(MARKETS[0]);
  const [timeframe, setTimeframe] = useState(TIMEFRAMES[0].value);
  const [selectedSignals, setSelectedSignals] = useState<string[]>([]);
  const [start, setStart] = useState(defaultDate(90));
  const [end, setEnd] = useState(defaultDate(0));
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getSignals().then(setSignals);
  }, []);

  function toggleSignal(key: string) {
    setSelectedSignals((prev) =>
      prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]
    );
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);

    if (selectedSignals.length === 0) {
      setError('전략을 최소 1개 선택해야 합니다.');
      return;
    }
    if (start >= end) {
      setError('시작일은 종료일보다 빨라야 합니다.');
      return;
    }

    setPending(true);
    try {
      const { run_id } = await runBacktest({
        market,
        timeframe,
        start,
        end,
        signal_keys: selectedSignals,
      });
      router.push(`/backtests/${run_id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '백테스트 실행 중 오류가 발생했습니다.');
      setPending(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="max-w-md space-y-4">
      <div>
        <label className="block text-sm font-medium mb-1">코인</label>
        <select
          className="rounded border px-2 py-1 text-sm"
          value={market}
          onChange={(e) => setMarket(e.target.value)}
        >
          {MARKETS.map((m) => (
            <option key={m} value={m}>{m}</option>
          ))}
        </select>
      </div>

      <div>
        <label className="block text-sm font-medium mb-1">봉타입</label>
        <select
          className="rounded border px-2 py-1 text-sm"
          value={timeframe}
          onChange={(e) => setTimeframe(e.target.value)}
        >
          {TIMEFRAMES.map((tf) => (
            <option key={tf.value} value={tf.value}>{tf.label}</option>
          ))}
        </select>
      </div>

      <div>
        <label className="block text-sm font-medium mb-1">전략</label>
        <div className="flex flex-col gap-1">
          {signals.map((key) => (
            <label key={key} className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={selectedSignals.includes(key)}
                onChange={() => toggleSignal(key)}
              />
              {key}
            </label>
          ))}
        </div>
      </div>

      <div>
        <label className="block text-sm font-medium mb-1">기간</label>
        <div className="flex items-center gap-2">
          <input
            type="date"
            className="rounded border px-2 py-1 text-sm"
            value={start}
            onChange={(e) => setStart(e.target.value)}
          />
          <span>~</span>
          <input
            type="date"
            className="rounded border px-2 py-1 text-sm"
            value={end}
            onChange={(e) => setEnd(e.target.value)}
          />
        </div>
      </div>

      <Button type="submit" disabled={pending}>
        {pending ? '실행 중...' : '실행'}
      </Button>

      <p className="text-xs text-muted-foreground">
        기간이 길고 봉타입이 짧을수록 최초 조회 시 시간이 걸릴 수 있습니다.
      </p>

      {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}
    </form>
  );
}
```

- [ ] **Step 2: 타입 체크 확인**

Run (in `frontend/`): `npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 3: 커밋**

```bash
git add frontend/components/BacktestRunForm.tsx
git commit -m "feat: add BacktestRunForm component"
```

---

### Task 5: 프런트엔드 — `/backtests` 페이지 교체 및 수동 통합 확인

**Files:**
- Modify: `frontend/app/backtests/page.tsx`

**Interfaces:**
- Consumes: `BacktestRunForm` (Task 4).

- [ ] **Step 1: 페이지 교체**

`frontend/app/backtests/page.tsx` 전체를 아래로 교체:

```tsx
import BacktestRunForm from '@/components/BacktestRunForm';

export default function BacktestsIndexPage() {
  return (
    <div>
      <h1 className="text-lg font-semibold mb-4">백테스트 실행</h1>
      <BacktestRunForm />
    </div>
  );
}
```

- [ ] **Step 2: 빌드 확인**

Run (in `frontend/`): `npm run build`
Expected: 빌드 성공 (타입/린트 에러 없음)

- [ ] **Step 3: 수동 통합 확인**

```bash
uvicorn backend.main:app --port 8000 &
cd frontend && npm run dev &
```

브라우저로 `http://localhost:3000/backtests` 접속 후:
1. 전략 체크박스가 `SIGNAL_REGISTRY` 4개 키(macd_cross, rsi_zone, sma_cross, bollinger_band)로 채워지는지 확인
2. 전략 미선택 상태로 "실행" 클릭 → "전략을 최소 1개 선택해야 합니다." 인라인 에러가 뜨고 API 호출이 발생하지 않는지 확인(네트워크 탭)
3. 전략 1개(예: sma_cross) 선택, 코인 KRW-BTC, 봉타입 일봉, 기간 최근 90일로 설정 후 "실행" 클릭 → 버튼이 "실행 중..."으로 바뀌고, 완료 후 `/backtests/{run_id}`로 이동해 자산곡선과 거래내역이 표시되는지 확인
4. 같은 조건으로 `/backtests`에서 다시 실행 → 캐시 hit으로 훨씬 빠르게 같은 `run_id`로 이동하는지 확인

Expected: 위 4가지 모두 통과

- [ ] **Step 4: 커밋**

```bash
git add frontend/app/backtests/page.tsx
git commit -m "feat: replace backtests index placeholder with on-demand run form"
```

## Self-Review 결과

- **스펙 커버리지**: 스펙의 인터페이스(`RunBacktestRequest`/`RunBacktestResponse`, 두 엔드포인트), 화면 레이아웃(코인/봉타입/전략 체크박스/기간/실행 버튼/안내 문구/에러 표시), 데이터 흐름(마운트 시 신호 조회 → 검증 → 실행 → 이동), 에러 처리 표(전략 미선택/기간 역전/미등록 키/빈 캔들/Upbit 실패/지연 안내) 전부 Task 1~5에서 다룸.
- **테스트 커버리지**: 스펙의 테스트 계획 5가지 백엔드 케이스(정상 실행+조회, 빈 signal_keys, 미등록 key, 기간 역전, 신호 목록 조회) 모두 Task 1~2에 포함. 스펙이 요구한 "해당 기간에 캔들 데이터 없음" 케이스는 Task 2의 `test_run_backtest_rejects_empty_candle_range`로 커버.
- **타입 일관성**: `RunBacktestRequest`/`RunBacktestResponse`(Task 3 정의) 필드명이 백엔드 pydantic 모델(Task 2) 및 `BacktestRunForm`(Task 4)의 `runBacktest()` 호출 인자와 동일함을 확인(market/timeframe/start/end/signal_keys).
- **범위 확인**: 리스크 설정 폼 노출, 전체 마켓 동적 조회, `sweep_history` 반영, 실행 이력 목록은 스펙에서 범위 밖으로 명시된 대로 어떤 Task에도 포함하지 않음.
