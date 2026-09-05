# 라이브 전략 카드 — 코인명 한글표기 & 직전 매수/매도 시각 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 라이브 전략 관리 카드에서 코인을 한글명으로 표기하고, 직전 매수/매도 일자·시간을 작은 글씨로 보여준다.

**Architecture:** 백엔드는 `_live_strategy_response()`에 `last_buy_at`/`last_sell_at` 필드를 추가해 이미 있는 `trading_db.list_closed_positions()`로 계산한다. 프론트는 `getMarkets()`로 마켓 한글명을 1회 가져와 맵으로 만들고, 카드의 세 곳(헤더·전략설정 다이얼로그·전략교체 다이얼로그)에서 티커 대신 한글명을 쓰며, 통계 영역 아래 축약 시각 한 줄을 추가한다.

**Tech Stack:** FastAPI + SQLite(`trading/db.py`) 백엔드, Next.js/React 프론트, pytest.

## Global Constraints

- `last_buy_at`/`last_sell_at`은 UTC ISO 문자열(오프셋 포함) 또는 `null`이어야 한다 — 기존 `_to_utc_iso()` 헬퍼로 감싼다 (스펙: "capital_adjustments의 adjusted_at과 동일하게 UTC ISO로 통일").
- 코인 한글명이 없으면(마켓 목록 fetch 실패 등) 반드시 티커(`s.market`)로 폴백한다 — 화면이 빈 값으로 깨지면 안 된다.
- 프론트엔드에는 자동 테스트 스위트가 없다(기존 관례) — 프론트 작업은 `npx tsc --noEmit`(타입 체크)과 개발 서버 수동 확인으로 검증한다.

---

### Task 1: 백엔드 — `last_buy_at` / `last_sell_at` 필드 추가

**Files:**
- Modify: `backend/main.py:1170-1196` (`_live_strategy_response`)
- Test: `tests/test_backend.py` (기존 `test_list_live_strategies_*` 그룹 옆에 추가)

**Interfaces:**
- Consumes: `trading_db.list_closed_positions(live_strategy_id: str) -> list[dict]`(이미 존재, `entry_time DESC` 정렬, 각 행에 `entry_time`/`exit_time` 포함), `_to_utc_iso(value: str) -> str`(이미 존재).
- Produces: `_live_strategy_response(...)` 반환 dict에 `last_buy_at: str | None`, `last_sell_at: str | None` 키 추가 — `list_live_strategies_endpoint`(`GET /api/v1/live-strategies`)와 `_full_live_strategy_response`(전략 생성/승인/교체 응답) 양쪽에 자동 반영됨.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_backend.py`에 `test_list_live_strategies_includes_open_position_summary` 근처(약 545번째 줄 뒤)에 아래 3개 테스트를 추가한다:

```python
def test_list_live_strategies_last_buy_at_reflects_open_position(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    create_resp = client.post("/api/v1/live-strategies", json=_live_strategy_request())
    strategy_id = create_resp.json()["id"]
    trading_db_module.insert_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)
    _patch_get_current_prices(monkeypatch, {"KRW-BTC": 55_000_000.0})

    resp = client.get("/api/v1/live-strategies")

    body = resp.json()[0]
    assert body["last_buy_at"] is not None
    assert body["last_sell_at"] is None


def test_list_live_strategies_last_buy_and_sell_reflect_closed_position(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    create_resp = client.post("/api/v1/live-strategies", json=_live_strategy_request())
    strategy_id = create_resp.json()["id"]
    position_id = trading_db_module.insert_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)
    trading_db_module.close_position_row(position_id, 51_000_000.0, 0.01, 10000.0, 2.0, "signal")

    resp = client.get("/api/v1/live-strategies")

    body = resp.json()[0]
    assert body["last_buy_at"] is not None
    assert body["last_sell_at"] is not None


def test_list_live_strategies_last_buy_and_sell_are_null_without_trades(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    client.post("/api/v1/live-strategies", json=_live_strategy_request())

    resp = client.get("/api/v1/live-strategies")

    body = resp.json()[0]
    assert body["last_buy_at"] is None
    assert body["last_sell_at"] is None
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `pytest tests/test_backend.py -k "last_buy_at or last_sell_at" -v`
Expected: FAIL — `KeyError: 'last_buy_at'` (응답에 아직 필드가 없음)

- [ ] **Step 3: 최소 구현 작성**

`backend/main.py:1170-1196`의 `_live_strategy_response`를 아래처럼 바꾼다 (기존 `return {...}` 딕셔너리 앞에 계산 로직을 추가하고, 딕셔너리에 두 키를 더한다):

```python
def _live_strategy_response(strategy: dict, position: dict | None, current_price: float | None) -> dict:
    closed_positions = trading_db.list_closed_positions(strategy["id"])
    last_closed = closed_positions[0] if closed_positions else None

    if position is not None:
        last_buy_at = _to_utc_iso(position["entry_time"])
    elif last_closed is not None:
        last_buy_at = _to_utc_iso(last_closed["entry_time"])
    else:
        last_buy_at = None
    last_sell_at = _to_utc_iso(last_closed["exit_time"]) if last_closed is not None else None

    return {
        "id": strategy["id"],
        "source_run_id": strategy["source_run_id"],
        "market": strategy["market"],
        "timeframe": strategy["timeframe"],
        "status": strategy["status"],
        "current_capital": strategy["current_capital"],
        "created_at": strategy["created_at"],
        "approved_at": strategy["approved_at"],
        "started_at": strategy["started_at"],
        "stopped_at": strategy["stopped_at"],
        "open_position": _open_position_summary(position, current_price) if position else None,
        "last_buy_at": last_buy_at,
        "last_sell_at": last_sell_at,
        "buy_conditions": json.loads(strategy["buy_conditions_json"]),
        "sell_conditions": json.loads(strategy["sell_conditions_json"]),
        "risk_config": json.loads(strategy["risk_config_json"]),
        "capital_adjustments": [
            {
                "id": adj["id"],
                "adjusted_at": _to_utc_iso(adj["adjusted_at"]),
                "previous_capital": adj["previous_capital"],
                "new_capital": adj["new_capital"],
                "delta": adj["delta"],
            }
            for adj in trading_db.list_capital_adjustments(strategy["id"])
        ],
    }
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_backend.py -k "last_buy_at or last_sell_at" -v`
Expected: PASS (3 passed)

- [ ] **Step 5: 전체 백엔드 스위트로 회귀 확인**

Run: `pytest tests/test_backend.py -v`
Expected: 기존 테스트 전부 PASS (특히 `test_list_live_strategies_*`, `test_replace_live_strategy_*` 그룹)

- [ ] **Step 6: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "feat: 라이브 전략 응답에 직전 매수/매도 시각 필드 추가"
```

---

### Task 2: 프론트엔드 — 타입 추가 + 축약 시각 포맷 헬퍼

**Files:**
- Modify: `frontend/lib/types/liveStrategies.ts:43-59` (`LiveStrategy` interface)
- Modify: `frontend/lib/format.ts:1-16`

**Interfaces:**
- Consumes: 없음 (독립적인 타입/유틸 추가).
- Produces: `LiveStrategy.last_buy_at: string | null`, `LiveStrategy.last_sell_at: string | null` — Task 3에서 사용. `formatDateTimeShort(iso: string): string`(예: `"08-17 14:23"`) — Task 3에서 사용.

- [ ] **Step 1: `LiveStrategy` 타입에 필드 추가**

`frontend/lib/types/liveStrategies.ts`의 `LiveStrategy` interface(43번째 줄)를 아래처럼 수정한다:

```typescript
export interface LiveStrategy {
  id: string;
  source_run_id: string | null;
  market: string;
  timeframe: string;
  status: LiveStrategyStatus;
  current_capital: number | null;
  created_at: string;
  approved_at: string | null;
  started_at: string | null;
  stopped_at: string | null;
  open_position: LiveStrategyOpenPosition | null;
  last_buy_at: string | null;
  last_sell_at: string | null;
  buy_conditions: ConditionGroup;
  sell_conditions: ConditionGroup;
  risk_config: LiveStrategyRiskConfig;
  capital_adjustments: CapitalAdjustment[];
}
```

- [ ] **Step 2: 축약 포맷 헬퍼 추가**

`frontend/lib/format.ts`의 `formatDateTime` 함수(1-16번째 줄) 바로 아래에 추가한다:

```typescript
export function formatDateTimeShort(iso: string): string {
  const parts = KST_FORMATTER.formatToParts(new Date(iso));
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? '';
  return `${get('month')}-${get('day')} ${get('hour')}:${get('minute')}`;
}
```

- [ ] **Step 3: 타입 체크로 검증**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음 (이 시점에는 `last_buy_at`/`last_sell_at`을 아직 아무도 안 읽으므로 기존 코드에 영향 없음)

- [ ] **Step 4: 커밋**

```bash
git add frontend/lib/types/liveStrategies.ts frontend/lib/format.ts
git commit -m "feat: LiveStrategy 타입에 매수/매도 시각 필드, 축약 시각 포맷 헬퍼 추가"
```

---

### Task 3: 프론트엔드 — 카드에 한글 코인명 & 직전 매수/매도 시각 표기

**Files:**
- Modify: `frontend/components/LiveStrategiesPage.tsx`

**Interfaces:**
- Consumes: `getMarkets(): Promise<Market[]>`(from `@/lib/api/eda`, 이미 존재, `Market.korean_name` 필드 포함), `LiveStrategy.last_buy_at`/`last_sell_at`(Task 2), `formatDateTimeShort`(Task 2).
- Produces: 없음 (최종 UI 변경, 리프 노드).

- [ ] **Step 1: import 추가**

`frontend/components/LiveStrategiesPage.tsx` 상단 import 블록을 수정한다. 16번째 줄:

```typescript
import { getBacktestRuns, getMarkets } from '@/lib/api/eda';
```

42번째 줄:

```typescript
import { formatCapital, formatDateTime, formatDateTimeShort, formatTimeframe } from '@/lib/format';
```

- [ ] **Step 2: 마켓 한글명 맵을 1회 로드하는 상태 추가**

`export default function LiveStrategiesPage()` 함수 본문(337번째 줄) 시작 부분, `const [strategies, setStrategies] = useState<LiveStrategy[]>([]);` 바로 아래에 추가한다:

```typescript
  const [marketNames, setMarketNames] = useState<Record<string, string>>({});

  useEffect(() => {
    getMarkets()
      .then((markets) => {
        setMarketNames(Object.fromEntries(markets.map((m) => [m.market, m.korean_name])));
      })
      .catch(() => {
        // 한글명 로드 실패 시 컴포넌트 전체를 막지 않고 티커로 폴백한다.
      });
  }, []);
```

이 컴포넌트는 아직 `useEffect`를 import하지 않았으므로, 3번째 줄의 import를 수정한다:

```typescript
import { useCallback, useEffect, useState } from 'react';
```

- [ ] **Step 3: 카드 렌더링에서 한글명과 직전 매수/매도 시각 사용**

`strategies.map((s) => ( ... ))` 블록(381번째 줄) 시작 부분에 `koreanName` 지역 변수를 추가하고 세 곳에서 사용한다.

`{strategies.map((s) => (` 바로 다음 줄에 추가:

```typescript
      {strategies.map((s) => {
          const koreanName = marketNames[s.market] ?? s.market;
          return (
          <Card
```

(JSX를 화살표 함수 블록 바디로 바꾸는 것이므로, 이 map의 닫는 부분 `))}`를 `)})}`로 맞춰야 한다 — Step 6에서 처리.)

카드 헤더(388-391번째 줄):

```typescript
            <div className="flex items-center justify-between gap-2 px-4">
              <span className="min-w-0 truncate text-sm font-semibold">
                {koreanName} · {formatTimeframe(s.timeframe)}
              </span>
```

전략 설정 다이얼로그 제목(405-407번째 줄):

```typescript
                      <DialogTitle>
                        {koreanName} · {formatTimeframe(s.timeframe)} 전략 설정
                      </DialogTitle>
```

- [ ] **Step 4: 전략 교체 다이얼로그 제목도 한글명 사용**

`StrategySwapDialog` 컴포넌트(202-335번째 줄)는 `strategy: LiveStrategy`만 받고 한글명 맵을 모른다. props로 `marketName`을 추가로 받는다.

`StrategySwapDialog` 함수 시그니처(202-208번째 줄)를 수정:

```typescript
function StrategySwapDialog({
  strategy,
  marketName,
  onChanged,
}: {
  strategy: LiveStrategy;
  marketName: string;
  onChanged: () => Promise<void>;
}) {
```

다이얼로그 제목(275번째 줄):

```typescript
        <DialogHeader>
          <DialogTitle>전략 교체 — {marketName}</DialogTitle>
        </DialogHeader>
```

호출부(465번째 줄 근처)를 수정:

```typescript
                {s.open_position === null && s.status !== 'draft' && (
                  <StrategySwapDialog strategy={s} marketName={koreanName} onChanged={refresh} />
                )}
```

- [ ] **Step 5: 통계 영역 아래에 직전 매수/매도 시각 한 줄 추가**

통계 영역(570-590번째 줄) 바로 다음, `{s.status === 'stopped' && s.stopped_at && (...)}`(592-594번째 줄) 앞에 추가한다:

```typescript
            {(s.last_buy_at || s.last_sell_at) && (
              <p className="px-4 text-xs text-muted-foreground">
                {s.last_buy_at && `매수 ${formatDateTimeShort(s.last_buy_at)}`}
                {s.last_buy_at && s.last_sell_at && ' · '}
                {s.last_sell_at && `매도 ${formatDateTimeShort(s.last_sell_at)}`}
              </p>
            )}
```

- [ ] **Step 6: map을 화살표 함수 블록 바디로 정리하고 닫는 괄호 맞추기**

`strategies.map((s) => { ... })` 블록 전체의 JSX 반환에 `return (`을 추가했으므로, 기존 `</Card>` 다음의 `))}`(596번째 줄)를 아래로 바꾼다:

```typescript
          </Card>
        );
      })}
```

- [ ] **Step 7: 타입 체크 및 린트로 검증**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

Run: `cd frontend && npx eslint components/LiveStrategiesPage.tsx`
Expected: 에러 없음

- [ ] **Step 8: 개발 서버에서 수동 확인**

Run: `cd frontend && npm run dev` (이미 떠 있다면 생략)

`/live-strategies` 페이지를 열어 아래를 확인한다:
- 카드 헤더에 티커 대신 한글 코인명이 보인다 (예: "도지코인 · 1시간봉").
- "전략 설정 보기" 다이얼로그 제목, "전략 교체" 다이얼로그 제목도 한글명으로 보인다.
- 거래 이력이 있는 전략 카드에는 통계 영역 아래 "매수 MM-DD HH:MM · 매도 MM-DD HH:MM" 형태의 작은 글씨 줄이 보인다.
- 거래 이력이 없는(방금 승인된) 전략 카드에는 이 줄이 아예 안 보인다.
- 마켓 목록 API가 실패하는 상황을 재현하기 어려우면(선택) 네트워크 탭에서 `/api/v1/markets` 요청을 막아보고 카드가 티커로 정상 폴백하는지 확인한다.

- [ ] **Step 9: 커밋**

```bash
git add frontend/components/LiveStrategiesPage.tsx
git commit -m "feat: 라이브 전략 카드에 한글 코인명과 직전 매수/매도 시각 표기"
```
