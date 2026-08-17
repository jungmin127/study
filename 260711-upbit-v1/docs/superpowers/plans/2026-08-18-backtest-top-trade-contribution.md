# 백테스트 최대 단일 거래 기여도(%) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 백테스트 결과에 "최대 단일 거래 기여도(%)" 지표(총 이익 중 가장 큰 단일 거래의 비중)를 계산해, 상세 페이지 지표 카드와 목록 표/모바일 카드에 표시한다.

**Architecture:** `engine/metrics.py`에 순수 함수 `top_trade_contribution_pct(trades)`를 추가하고, 상세 페이지는 기존 `calculate_metrics()`가 이미 계산해 둔 `wins`/`gross_profit`을 재사용해 반환 dict에 필드를 얹는다. 목록 페이지는 `backend/main.py::get_backtest_runs()`가 미청산 포지션 재평가용으로 이미 메모리에 올려둔 `trades`에 대해 같은 헬퍼 함수를 직접 호출한다. 프론트는 두 응답 타입(`BacktestMetrics`, `BacktestRunSummary`)에 `number | null` 필드를 추가하고 상세 지표 그리드/목록 표/모바일 카드 세 곳에 표시를 얹는다.

**Tech Stack:** Python(FastAPI, pandas) 백엔드, Next.js 14 App Router + TypeScript 프론트.

## Global Constraints

- 표준편차(σ) 등 다른 분포 지표는 이번 스펙 범위 밖 — 최대 단일 거래 기여도만 구현한다.
- 목록 표에서 이 컬럼 기준 정렬은 범위 밖 — 표시만 한다.
- 라이브 전략(매매일지) 쪽 동일 지표는 이번 스펙에 포함하지 않는다 — 백테스트 결과에만 적용.
- 분모는 총수익(`gross_profit`)으로 고정한다(총수익률이 아님) — 전략이 순손실이어도 "이긴 거래들 중 쏠림 정도"를 안정적으로 보여주기 위함.
- 이긴 거래가 하나도 없으면 `None`을 반환한다 — 0.0이 아니다.

---

## File Structure

- `engine/metrics.py` — `top_trade_contribution_pct(trades)` 헬퍼 추가, `calculate_metrics()`/`_empty_metrics()`에 필드 배선.
- `tests/test_metrics.py` — 헬퍼 단위 테스트 + `calculate_metrics()` 통합 테스트.
- `backend/main.py` — `get_backtest_runs()`(목록 엔드포인트)에 필드 추가. 상세 엔드포인트(`get_backtest_detail()`)는 `calculate_metrics()`를 그대로 호출하므로 코드 변경 불필요.
- `tests/test_backend.py` — `GET /api/v1/backtests` 응답 필드 테스트.
- `frontend/lib/types/eda.ts` — `BacktestMetrics`, `BacktestRunSummary`에 `top_trade_contribution_pct: number | null` 추가.
- `frontend/app/backtests/[runId]/page.tsx` — `MetricsGrid` 타일 배열에 항목 추가.
- `frontend/components/BacktestRunsTable.tsx` — 데스크톱 표에 "최대거래 기여도(%)" 컬럼 추가.
- `frontend/components/BacktestRunCard.tsx` — 모바일 카드에 같은 값 표시.

---

### Task 1: `engine/metrics.py` — 헬퍼 함수 + `calculate_metrics()` 배선

**Files:**
- Modify: `engine/metrics.py`
- Test: `tests/test_metrics.py`

**Interfaces:**
- Produces: `top_trade_contribution_pct(trades: list[dict]) -> float | None` (module-level, importable as `from engine.metrics import top_trade_contribution_pct`), and `calculate_metrics(...)`의 반환 dict에 새 키 `"top_trade_contribution_pct": float | None` 추가. `_empty_metrics()`도 같은 키를 `None`으로 포함.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_metrics.py` 맨 위 import에 헬퍼를 추가하고, 파일 끝에 아래 테스트들을 추가한다:

```python
from engine.metrics import calculate_metrics, top_trade_contribution_pct
```

(기존 `from engine.metrics import calculate_metrics` 줄을 이걸로 교체)

파일 끝에 추가:

```python
def test_top_trade_contribution_pct_none_when_no_wins():
    trades = [{"pnl": -10.0}, {"pnl": -5.0}]
    assert top_trade_contribution_pct(trades) is None


def test_top_trade_contribution_pct_none_when_no_trades():
    assert top_trade_contribution_pct([]) is None


def test_top_trade_contribution_pct_even_distribution_is_low():
    trades = [{"pnl": 100.0}, {"pnl": 100.0}, {"pnl": 100.0}, {"pnl": 100.0}]
    assert top_trade_contribution_pct(trades) == pytest.approx(25.0)


def test_top_trade_contribution_pct_dominant_trade_is_high():
    trades = [{"pnl": 900.0}, {"pnl": 50.0}, {"pnl": 50.0}, {"pnl": -30.0}]
    # gross_profit = 900+50+50 = 1000, 최대 이긴 거래 900 -> 90%
    assert top_trade_contribution_pct(trades) == pytest.approx(90.0)


def test_calculate_metrics_includes_top_trade_contribution_pct():
    equity_curve = [{"timestamp": "2026-01-01T00:00:00", "value": 10000.0}]
    trades = [
        {"pnl": 900.0, "holdingPeriod": 1},
        {"pnl": 50.0, "holdingPeriod": 1},
        {"pnl": 50.0, "holdingPeriod": 1},
        {"pnl": -30.0, "holdingPeriod": 1},
    ]
    result = calculate_metrics(equity_curve, trades, 10000.0, _df([100]), "days")
    assert result["top_trade_contribution_pct"] == pytest.approx(90.0)


def test_calculate_metrics_top_trade_contribution_pct_none_without_wins():
    equity_curve = [{"timestamp": "2026-01-01T00:00:00", "value": 10000.0}]
    trades = [{"pnl": -10.0, "holdingPeriod": 1}]
    result = calculate_metrics(equity_curve, trades, 10000.0, _df([100]), "days")
    assert result["top_trade_contribution_pct"] is None
```

또한 기존 `test_empty_equity_curve_returns_zeroed_metrics` 테스트(파일 62번째 줄 부근)를 아래처럼 한 줄 추가해 수정한다:

```python
def test_empty_equity_curve_returns_zeroed_metrics():
    result = calculate_metrics([], [], 10000.0, _df([100]), "days")
    assert result["total_trades"] == 0
    assert result["total_return"] == 0.0
    assert result["max_consecutive_loss"] == 0
    assert result["top_trade_contribution_pct"] is None
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_metrics.py -v`
Expected: `top_trade_contribution_pct` import 실패로 collection error, 또는 `KeyError: 'top_trade_contribution_pct'`로 FAIL.

- [ ] **Step 3: `engine/metrics.py` 구현**

`_max_consecutive_loss()` 함수(171번째 줄 부근, `__all__` 선언 바로 위)와 `__all__` 사이에 헬퍼 함수를 추가한다:

```python
def top_trade_contribution_pct(trades: list[dict]) -> float | None:
    """총 이익(gross profit) 중 가장 큰 단일 거래의 pnl이 차지하는 비중(%).
    이긴 거래가 없으면 None. 분모를 총수익률이 아니라 gross_profit으로 잡아,
    전략이 순손실이어도 '이긴 거래들 중 쏠림 정도'를 안정적으로 보여준다."""
    wins = [float(t.get("pnl", 0.0)) for t in trades if t.get("pnl", 0.0) > 0]
    if not wins:
        return None
    gross_profit = sum(wins)
    return max(wins) / gross_profit * 100.0 if gross_profit > 0 else None
```

`__all__` 선언을 아래로 교체:

```python
__all__ = ["calculate_metrics", "bars_to_days", "top_trade_contribution_pct"]
```

`calculate_metrics()` 안의 기본값 초기화 블록(95번째 줄 부근)을 아래로 교체:

```python
    total_trades = len(trades)
    win_rate = 0.0
    profit_factor = 0.0
    avg_holding_period = 0.0
    max_consecutive_loss = 0
    top_trade_contribution_pct_value: float | None = None
```

바로 아래 `if trades:` 블록 안의 `profit_factor` 계산 줄(103번째 줄 부근) 다음에 두 줄을 추가:

```python
        win_rate = (len(wins) / total_trades * 100.0) if total_trades else 0.0
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        profit_factor = _safe_div(gross_profit, gross_loss) if gross_loss > 0 else 999.0
        if wins and gross_profit > 0:
            top_trade_contribution_pct_value = max(wins) / gross_profit * 100.0
```

반환 dict(117번째 줄 부근)의 `"buy_and_hold_return"` 줄 다음에 새 키를 추가:

```python
        "buy_and_hold_return": round(buy_and_hold_return, 4),
        "top_trade_contribution_pct": (
            round(top_trade_contribution_pct_value, 4)
            if top_trade_contribution_pct_value is not None
            else None
        ),
        "total_trades": total_trades,
```

`_empty_metrics()`(133번째 줄 부근)에 키 추가:

```python
def _empty_metrics() -> dict:
    return {
        "total_return": 0.0, "cagr": 0.0, "mdd": 0.0,
        "sharpe_ratio": 0.0, "sortino_ratio": 0.0, "calmar_ratio": 0.0,
        "win_rate": 0.0, "profit_factor": 0.0, "avg_holding_period": 0.0,
        "max_consecutive_loss": 0, "buy_and_hold_return": 0.0,
        "top_trade_contribution_pct": None, "total_trades": 0,
    }
```

(로컬 변수명을 `top_trade_contribution_pct_value`로 지어 모듈 레벨 함수 `top_trade_contribution_pct`와 이름이 겹치지 않게 한다.)

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_metrics.py -v`
Expected: 전체 PASS.

- [ ] **Step 5: 커밋**

```bash
git add engine/metrics.py tests/test_metrics.py
git commit -m "feat: 백테스트 지표에 최대 단일 거래 기여도(%) 계산 추가"
```

---

### Task 2: `backend/main.py` — 목록 엔드포인트에 필드 추가

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_backend.py`

**Interfaces:**
- Consumes: Task 1의 `top_trade_contribution_pct(trades: list[dict]) -> float | None` (from `engine.metrics`).
- Produces: `GET /api/v1/backtests` 응답의 각 항목에 `"top_trade_contribution_pct": float | None` 필드.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_backend.py`의 `test_get_backtests_includes_strategy_condition_summaries` 테스트 바로 다음에 추가:

```python
def test_get_backtests_includes_top_trade_contribution_pct(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    save_result(
        run_id="r1", strategy_name="ConditionTreeStrategy",
        strategy_params={"buy_conditions": {}, "sell_conditions": {}},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={
            "final_value": 10920.0, "sharpe": None, "max_drawdown": None, "equity_curve": [],
            "trades": [
                {
                    "entryTime": "2026-01-01T00:00:00", "exitTime": "2026-01-02T00:00:00",
                    "entryPrice": 100.0, "exitPrice": 190.0, "returnRate": 90.0,
                    "holdingPeriod": 1, "pnl": 900.0, "forceClosed": False, "size": 100.0,
                },
                {
                    "entryTime": "2026-01-03T00:00:00", "exitTime": "2026-01-04T00:00:00",
                    "entryPrice": 100.0, "exitPrice": 105.0, "returnRate": 5.0,
                    "holdingPeriod": 1, "pnl": 50.0, "forceClosed": False, "size": 100.0,
                },
                {
                    "entryTime": "2026-01-05T00:00:00", "exitTime": "2026-01-06T00:00:00",
                    "entryPrice": 100.0, "exitPrice": 95.0, "returnRate": -5.0,
                    "holdingPeriod": 1, "pnl": -30.0, "forceClosed": False, "size": 100.0,
                },
            ],
        },
    )

    resp = client.get("/api/v1/backtests")
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["top_trade_contribution_pct"] == pytest.approx(900 / 950 * 100.0)


def test_get_backtests_top_trade_contribution_pct_none_without_wins(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    save_result(
        run_id="r1", strategy_name="ConditionTreeStrategy",
        strategy_params={"buy_conditions": {}, "sell_conditions": {}},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={"final_value": 10000.0, "sharpe": None, "max_drawdown": None, "equity_curve": [], "trades": []},
    )

    resp = client.get("/api/v1/backtests")
    body = resp.json()
    assert body[0]["top_trade_contribution_pct"] is None
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_backend.py -k top_trade_contribution -v`
Expected: `KeyError: 'top_trade_contribution_pct'`로 FAIL.

- [ ] **Step 3: `backend/main.py` 구현**

58번째 줄의 import를 교체:

```python
from engine.metrics import VALID_TIMEFRAMES, calculate_metrics, top_trade_contribution_pct
```

`get_backtest_runs()`의 응답 dict 조립부(539번째 줄 부근, `"max_drawdown": r["max_drawdown"],` 다음)에 한 줄 추가:

```python
            "max_drawdown": r["max_drawdown"],
            "top_trade_contribution_pct": top_trade_contribution_pct(trades),
            "is_live": is_live,
```

(`trades`는 바로 위 537번째 줄 `trades = r["trades"]`에서 이미 지역 변수로 존재한다.)

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_backend.py -k top_trade_contribution -v`
Expected: 2개 PASS.

Run: `pytest tests/test_backend.py -v`
Expected: 전체 PASS (회귀 없음 확인).

- [ ] **Step 5: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "feat: 백테스트 목록 API에 최대 단일 거래 기여도(%) 필드 추가"
```

---

### Task 3: 상세 페이지 — 타입 + 지표 카드

**Files:**
- Modify: `frontend/lib/types/eda.ts`
- Modify: `frontend/app/backtests/[runId]/page.tsx`

**Interfaces:**
- Consumes: `GET /api/v1/backtests/{run_id}` 응답의 `metrics.top_trade_contribution_pct`(Task 1에서 `calculate_metrics()`가 이미 내보냄 — 백엔드 코드 변경 불필요).
- Produces: `BacktestMetrics.top_trade_contribution_pct: number | null` 타입, 상세 페이지 지표 그리드에 "최대거래 기여도" 타일.

- [ ] **Step 1: 타입 추가**

`frontend/lib/types/eda.ts`의 `BacktestMetrics` 인터페이스(35번째 줄)에서 `buy_and_hold_return` 다음 줄에 필드 추가:

```typescript
export interface BacktestMetrics {
  total_return: number;
  cagr: number;
  mdd: number;
  sharpe_ratio: number;
  sortino_ratio: number;
  calmar_ratio: number;
  win_rate: number;
  profit_factor: number;
  avg_holding_period: number;
  max_consecutive_loss: number;
  buy_and_hold_return: number;
  top_trade_contribution_pct: number | null;
  total_trades: number;
}
```

- [ ] **Step 2: 지표 그리드에 타일 추가**

`frontend/app/backtests/[runId]/page.tsx`의 `MetricsGrid` 함수 내 `tiles` 배열(19번째 줄부터)에서 `'최대연속손실'` 타일(66-68번째 줄) 다음에 항목 추가:

```typescript
    {
      label: '최대연속손실', value: `${metrics.max_consecutive_loss}건`,
      tooltip: '연속으로 손실이 난 거래의 최대 횟수입니다. 클수록 연속 손실 구간에서 심리적/자금 압박이 컸다는 뜻입니다.', icon: Repeat,
    },
    {
      label: '최대거래 기여도',
      value: metrics.top_trade_contribution_pct != null ? `${metrics.top_trade_contribution_pct.toFixed(1)}%` : '-',
      tooltip: '총 이익 중 가장 큰 단일 거래가 차지하는 비중입니다. 높을수록 소수 거래에 수익이 쏠렸다는 뜻입니다.',
      icon: Percent,
    },
  ];
```

(`Percent`는 파일 1번째 줄에서 이미 import되어 있으므로 추가 import 불필요.)

- [ ] **Step 3: 타입 검사로 확인**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음.

- [ ] **Step 4: 커밋**

```bash
git add frontend/lib/types/eda.ts "frontend/app/backtests/[runId]/page.tsx"
git commit -m "feat: 백테스트 상세 페이지에 최대 단일 거래 기여도 타일 추가"
```

---

### Task 4: 목록 표 + 모바일 카드 — 타입 + 표시

**Files:**
- Modify: `frontend/lib/types/eda.ts`
- Modify: `frontend/components/BacktestRunsTable.tsx`
- Modify: `frontend/components/BacktestRunCard.tsx`

**Interfaces:**
- Consumes: Task 2에서 `GET /api/v1/backtests` 응답에 추가된 `top_trade_contribution_pct: number | null`.
- Produces: `BacktestRunSummary.top_trade_contribution_pct: number | null` 타입, 데스크톱 표의 새 컬럼과 모바일 카드의 동등 표시.

- [ ] **Step 1: 타입 추가**

`frontend/lib/types/eda.ts`의 `BacktestRunSummary` 인터페이스(155번째 줄)에서 `max_drawdown` 다음 줄에 필드 추가:

```typescript
export interface BacktestRunSummary {
  run_id: string;
  title: string | null;
  description: string | null;
  market: string;
  timeframe: string;
  start: string;
  end: string;
  created_at: string;
  final_value: number;
  return_rate: number | null;
  sharpe: number | null;
  max_drawdown: number | null;
  top_trade_contribution_pct: number | null;
  is_live: boolean;
  last_trade_status: 'open' | 'closed' | 'none';
  buy_conditions: ConditionGroup;
  sell_conditions: ConditionGroup;
}
```

- [ ] **Step 2: 데스크톱 표에 컬럼 추가**

`frontend/components/BacktestRunsTable.tsx`의 테이블 헤더(296-309번째 줄, `MDD(%)` `TableHead`) 다음에 새 헤더 추가:

```tsx
            <TableHead className="text-right">
              <button
                type="button"
                className="flex w-full items-center justify-end gap-1 hover:text-foreground"
                onClick={() => toggleSort('max_drawdown')}
              >
                MDD(%) <SortIcon sortKeyOf="max_drawdown" />
              </button>
            </TableHead>
            <TableHead className="text-right">최대거래 기여도(%)</TableHead>
            <TableHead>상태</TableHead>
```

바디 행(361번째 줄, MDD `TableCell`) 다음에 새 셀 추가:

```tsx
              <TableCell className="text-right tabular-nums">{run.max_drawdown?.toFixed(2) ?? '-'}</TableCell>
              <TableCell className="text-right tabular-nums">
                {run.top_trade_contribution_pct != null ? `${run.top_trade_contribution_pct.toFixed(1)}%` : '-'}
              </TableCell>
              <TableCell>
                <LastTradeStatusBadge status={run.last_trade_status} />
              </TableCell>
```

`colSpan={13}`으로 되어 있는 빈 상태 행(323번째 줄)을 컬럼 하나 늘어난 만큼 `colSpan={14}`로 수정:

```tsx
              <TableCell colSpan={14} className="text-center text-muted-foreground">
                조건에 맞는 결과가 없습니다.
              </TableCell>
```

- [ ] **Step 3: 모바일 카드에 표시 추가**

`frontend/components/BacktestRunCard.tsx`의 수익률/MDD/상태 배지가 있는 flex 블록(51-58번째 줄)을 아래로 교체:

```tsx
      <div className="mb-2 flex flex-wrap items-center gap-3 text-sm">
        <span className={returnRateColor(run.return_rate)}>
          수익률 {run.return_rate?.toFixed(2) ?? '-'}%
          {run.is_live && <span className="ml-1 text-xs text-muted-foreground">(실시간)</span>}
        </span>
        <span className="text-muted-foreground">MDD {run.max_drawdown?.toFixed(2) ?? '-'}%</span>
        <span className="text-muted-foreground">
          최대거래 기여도 {run.top_trade_contribution_pct != null ? `${run.top_trade_contribution_pct.toFixed(1)}%` : '-'}
        </span>
        <LastTradeStatusBadge status={run.last_trade_status} />
      </div>
```

- [ ] **Step 4: 타입 검사로 확인**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음.

- [ ] **Step 5: 커밋**

```bash
git add frontend/lib/types/eda.ts frontend/components/BacktestRunsTable.tsx frontend/components/BacktestRunCard.tsx
git commit -m "feat: 백테스트 목록 표/모바일 카드에 최대 단일 거래 기여도 컬럼 추가"
```

---

## 최종 검증

모든 태스크 완료 후:

- [ ] `pytest tests/test_metrics.py tests/test_backend.py -v` 전체 PASS
- [ ] `cd frontend && npx tsc --noEmit` 에러 없음
- [ ] 로컬에서 `/backtests` 목록 페이지와 임의의 `/backtests/{run_id}` 상세 페이지를 브라우저로 열어 새 컬럼/타일이 정상 표시되는지 육안 확인
