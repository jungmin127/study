# 라이브 전략 매수/매도 설정 조회 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 라이브 전략 카드에 정보(물음표) 버튼을 추가해, 클릭 시 해당 전략의 현재 매수 조건/매도 조건/리스크 관리 설정을 읽기 전용 모달로 보여준다.

**Architecture:** 백엔드는 이미 DB에 저장돼 있지만 API 응답에서 빠져 있던 `buy_conditions_json`/`sell_conditions_json`/`risk_config_json`을 파싱해 응답에 추가한다. 프론트는 새 API 호출 없이 기존 목록 응답에 실린 값을 그대로 모달에 렌더링한다.

**Tech Stack:** FastAPI (Python), Next.js/React (TypeScript), base-ui 기반 shadcn 스타일 Dialog 컴포넌트.

## Global Constraints

- 스펙 문서: `docs/superpowers/specs_v1/2026-08-17-live-strategy-condition-info-design.md`
- 조회 전용 — 편집 기능 없음, 변경 이력 없음(변경 이력은 별도 스펙 `2026-08-17-live-strategy-capital-adjustment-design.md`의 몫)
- 조건 요약 텍스트는 반드시 기존 `summarizeGroup()`(frontend/lib/condition-summary.ts)을 재사용한다 — 새 렌더러를 만들지 않는다.

---

### Task 1: 백엔드 — 라이브 전략 응답에 조건/리스크 설정 추가

**Files:**
- Modify: `backend/main.py:1167-1179` (`_live_strategy_response` 함수)
- Test: `tests/test_backend.py`

**Interfaces:**
- Consumes: `strategy` dict의 `buy_conditions_json`/`sell_conditions_json`/`risk_config_json` 컬럼 (모두 JSON 문자열, `trading/db.py::get_live_strategy()`가 `SELECT *`로 그대로 반환).
- Produces: 목록(`GET /api/v1/live-strategies`)·단건 조회 응답 dict에 `buy_conditions`(dict), `sell_conditions`(dict), `risk_config`(dict) 키 추가. 이 함수는 이미 `json` 모듈이 import돼 있는 `backend/main.py` 안에 있다(다른 곳에서 `json.dumps` 사용 중).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_backend.py`에서 `test_create_live_strategy_creates_draft` 근처(458행 뒤)에 아래 테스트를 추가한다:

```python
def test_list_live_strategies_includes_buy_sell_conditions_and_risk_config(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])

    client.post("/api/v1/live-strategies", json=_live_strategy_request())

    resp = client.get("/api/v1/live-strategies")

    assert resp.status_code == 200
    body = resp.json()[0]
    assert body["buy_conditions"] == _VALID_BUY
    assert body["sell_conditions"] == _VALID_SELL
    assert body["risk_config"]["position_sizing_mode"] == "fixed"
    assert body["risk_config"]["position_sizing_value"] == 100000
    assert body["risk_config"]["daily_loss_limit_pct"] == -5.0
```

- [ ] **Step 2: 테스트 실행 → 실패 확인**

Run: `python -m pytest tests/test_backend.py::test_list_live_strategies_includes_buy_sell_conditions_and_risk_config -v`
Expected: FAIL — `KeyError: 'buy_conditions'` (assert 단계에서 KeyError 또는 AssertionError)

- [ ] **Step 3: `_live_strategy_response` 구현**

`backend/main.py`의 `_live_strategy_response` 함수(1167행)를 다음과 같이 수정한다:

```python
def _live_strategy_response(strategy: dict, position: dict | None, current_price: float | None) -> dict:
    return {
        "id": strategy["id"],
        "market": strategy["market"],
        "timeframe": strategy["timeframe"],
        "status": strategy["status"],
        "current_capital": strategy["current_capital"],
        "created_at": strategy["created_at"],
        "approved_at": strategy["approved_at"],
        "started_at": strategy["started_at"],
        "stopped_at": strategy["stopped_at"],
        "open_position": _open_position_summary(position, current_price) if position else None,
        "buy_conditions": json.loads(strategy["buy_conditions_json"]),
        "sell_conditions": json.loads(strategy["sell_conditions_json"]),
        "risk_config": json.loads(strategy["risk_config_json"]),
    }
```

- [ ] **Step 4: 테스트 실행 → 통과 확인**

Run: `python -m pytest tests/test_backend.py::test_list_live_strategies_includes_buy_sell_conditions_and_risk_config -v`
Expected: PASS

- [ ] **Step 5: 기존 라이브 전략 테스트 전체 회귀 확인**

Run: `python -m pytest tests/test_backend.py -k live_strategy -v`
Expected: 전부 PASS (기존 테스트는 응답에 새 키가 추가돼도 영향받지 않음 — 기존 assertion은 특정 키만 확인함)

- [ ] **Step 6: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "feat: 라이브 전략 응답에 매수/매도 조건과 리스크 설정 포함"
```

---

### Task 2: 프론트엔드 타입 확장

**Files:**
- Modify: `frontend/lib/types/liveStrategies.ts`

**Interfaces:**
- Consumes: Task 1에서 API가 내려주는 `buy_conditions`/`sell_conditions`/`risk_config` 필드.
- Produces: `LiveStrategy` 타입에 `buy_conditions: ConditionGroup`, `sell_conditions: ConditionGroup`, `risk_config: LiveStrategyRiskConfig` 필드 — Task 3이 타입 안전하게 이 필드들을 참조한다.

- [ ] **Step 1: `LiveStrategy` 인터페이스 수정**

`frontend/lib/types/liveStrategies.ts`의 `LiveStrategy` 인터페이스(35-46행)를 다음으로 교체:

```typescript
export interface LiveStrategy {
  id: string;
  market: string;
  timeframe: string;
  status: LiveStrategyStatus;
  current_capital: number | null;
  created_at: string;
  approved_at: string | null;
  started_at: string | null;
  stopped_at: string | null;
  open_position: LiveStrategyOpenPosition | null;
  buy_conditions: ConditionGroup;
  sell_conditions: ConditionGroup;
  risk_config: LiveStrategyRiskConfig;
}
```

- [ ] **Step 2: 타입 체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음 (이 시점엔 아직 아무 컴포넌트도 새 필드를 안 쓰므로 통과해야 정상)

- [ ] **Step 3: 커밋**

```bash
git add frontend/lib/types/liveStrategies.ts
git commit -m "feat: LiveStrategy 타입에 조건/리스크 설정 필드 추가"
```

---

### Task 3: 프론트엔드 — 정보 모달 UI

**Files:**
- Modify: `frontend/components/LiveStrategiesPage.tsx`

**Interfaces:**
- Consumes: `s: LiveStrategy` (Task 2에서 확장된 타입), `summarizeGroup()`(`frontend/lib/condition-summary.ts`, 시그니처: `(group: ConditionGroup) => string`), `Dialog`/`DialogTrigger`/`DialogContent`/`DialogHeader`/`DialogTitle`(`frontend/components/ui/dialog.tsx`), `buttonVariants`(이미 import돼 있음).
- Produces: 없음 (리프 UI 변경, 다른 태스크가 의존하지 않음).

- [ ] **Step 1: import 추가**

`frontend/components/LiveStrategiesPage.tsx` 상단 import 블록을 수정한다. 기존:

```typescript
import { Check, Pause, Play, Square, Trash2, X } from 'lucide-react';
```

다음으로 교체:

```typescript
import { Check, CircleHelp, Pause, Play, Square, Trash2, X } from 'lucide-react';
```

그리고 `import { Badge } from '@/components/ui/badge';` 아래(26행 부근)에 추가:

```typescript
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { summarizeGroup } from '@/lib/condition-summary';
```

- [ ] **Step 2: 리스크 설정 라벨 상수 + 포맷터 추가**

`fmtPct` 함수(35-37행) 바로 아래에 추가:

```typescript
const RISK_CONFIG_LABELS: Record<string, string> = {
  position_sizing_mode: '포지션 사이징 방식',
  position_sizing_value: '포지션 사이징 값',
  max_position_per_market: '코인당 최대 포지션',
  order_execution_mode: '주문 방식',
  order_timeout_sec: '주문 타임아웃(초)',
  manual_intervention_policy: '수동 개입 정책',
  daily_loss_limit_pct: '일일 손실 한도(%)',
  consecutive_loss_limit: '연속 손실 한도',
};

const POSITION_SIZING_MODE_LABELS: Record<string, string> = {
  fixed: '고정 금액',
  percent: '자본 비율(%)',
};

const ORDER_EXECUTION_MODE_LABELS: Record<string, string> = {
  market: '시장가',
  limit: '지정가',
  limit_timeout: '지정가(타임아웃 시 시장가 전환)',
};

const MANUAL_INTERVENTION_POLICY_LABELS: Record<string, string> = {
  all_stop: '전체 정지',
  acknowledge_and_continue: '확인 후 계속',
};

function formatRiskConfigValue(key: string, value: number | string): string {
  if (key === 'position_sizing_mode') return POSITION_SIZING_MODE_LABELS[value as string] ?? String(value);
  if (key === 'order_execution_mode') return ORDER_EXECUTION_MODE_LABELS[value as string] ?? String(value);
  if (key === 'manual_intervention_policy') return MANUAL_INTERVENTION_POLICY_LABELS[value as string] ?? String(value);
  return String(value);
}
```

- [ ] **Step 3: 카드 헤더에 정보 버튼 + 모달 추가**

`LiveStrategiesPage.tsx`에서 카드 헤더의 `<Badge ...>` 바로 다음(104행 뒤)에 정보 버튼을 추가한다. 기존:

```tsx
                <Badge variant={s.status === 'running' ? 'default' : 'secondary'}>{s.status}</Badge>
                {s.status === 'draft' && (
```

다음으로 교체:

```tsx
                <Badge variant={s.status === 'running' ? 'default' : 'secondary'}>{s.status}</Badge>
                <Dialog>
                  <DialogTrigger
                    type="button"
                    className={buttonVariants({ variant: 'outline', size: 'icon-lg' })}
                    aria-label="전략 설정 보기"
                    title="전략 설정 보기"
                  >
                    <CircleHelp />
                  </DialogTrigger>
                  <DialogContent>
                    <DialogHeader>
                      <DialogTitle>
                        {s.market} · {formatTimeframe(s.timeframe)} 전략 설정
                      </DialogTitle>
                    </DialogHeader>
                    <div className="space-y-3 text-sm">
                      <div>
                        <p className="mb-1 font-medium text-muted-foreground">매수 조건</p>
                        <p className="rounded-md bg-muted/50 p-2 font-mono text-xs">
                          {summarizeGroup(s.buy_conditions)}
                        </p>
                      </div>
                      <div>
                        <p className="mb-1 font-medium text-muted-foreground">매도 조건</p>
                        <p className="rounded-md bg-muted/50 p-2 font-mono text-xs">
                          {summarizeGroup(s.sell_conditions)}
                        </p>
                      </div>
                      <div>
                        <p className="mb-1 font-medium text-muted-foreground">리스크 관리</p>
                        <div className="space-y-1 rounded-md bg-muted/50 p-2">
                          {Object.entries(RISK_CONFIG_LABELS).map(([key, label]) => (
                            <div key={key} className="flex justify-between gap-2">
                              <span className="text-muted-foreground">{label}</span>
                              <span className="tabular-nums">
                                {formatRiskConfigValue(key, s.risk_config[key as keyof typeof s.risk_config])}
                              </span>
                            </div>
                          ))}
                        </div>
                      </div>
                    </div>
                  </DialogContent>
                </Dialog>
                {s.status === 'draft' && (
```

- [ ] **Step 4: 타입 체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 5: 개발 서버로 수동 확인**

Run: `cd frontend && npm run dev` (이미 떠 있으면 생략)

브라우저에서 라이브 전략 관리 화면을 열고:
1. 전략 카드의 배지 옆에 물음표 아이콘 버튼이 보이는지 확인
2. 클릭 시 모달이 뜨고, 매수 조건/매도 조건이 `RSI(period=14)<60` 같은 텍스트로 보이는지 확인
3. 리스크 관리 섹션에 8개 항목이 한글 라벨과 값으로 나열되는지 확인
4. 모달을 닫고(X 버튼 또는 바깥 클릭) 카드가 정상 상태로 돌아오는지 확인

- [ ] **Step 6: 커밋**

```bash
git add frontend/components/LiveStrategiesPage.tsx
git commit -m "feat: 라이브 전략 카드에 매수/매도/리스크 설정 조회 모달 추가"
```
