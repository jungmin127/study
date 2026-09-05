# 백테스트 결과 "복사" 컬럼(설정 프리셋 이동) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/backtests`(백테스트 결과) 목록의 "상세" 옆에 "복사" 컬럼을 추가해, 클릭 시 `/`(백테스트 설정)로 이동하면서 코인/봉타입/기간/매수·매도 조건이 동일한 값으로 프리셋 채워지게 한다.

**Architecture:** 백엔드 변경 없음. `/backtests`가 이미 갖고 있는 `BacktestRunSummary`(코인/봉타입/기간/조건 전부 포함)를 URL 쿼리스트링에 실어 `/`로 넘기고, `PortSetupForm`이 `useSearchParams()`로 그 값을 읽어 초기 상태로 쓴다.

**Tech Stack:** Next.js 14 (App Router) / TypeScript / lucide-react

**참고 스펙:** `docs/superpowers/specs_v1/2026-07-26-backtest-copy-preset-design.md`

## Global Constraints

- 백엔드(`backend/`, `engine/`) 변경 없음.
- 제목/설명/운용자금은 프리셋에서 제외 — 기존 기본값(빈 제목, 기본 자금 1,000,000원) 그대로 유지.
- 클릭 시 자동 실행 없음 — 프리셋만 채우고 실행은 사용자가 직접.
- 이 프론트엔드에는 자동화된 테스트 러너가 없다. 각 태스크의 검증은 `npm run dev` 구동 후 Playwright(또는 브라우저)로 실제 렌더링/콘솔 에러 유무를 확인하는 방식으로 한다.
- `npm run build`를 `npm run dev`가 떠 있는 상태에서 실행하지 않는다 — 컴파일 확인은 `npx tsc --noEmit`으로 한다.

---

## Task 1: `BacktestRunsTable.tsx` — "복사" 컬럼 추가

**Files:**
- Modify: `frontend/components/BacktestRunsTable.tsx` (전체 교체)

**Interfaces:**
- Produces: URL 쿼리스트링 파라미터 계약 — `market`, `timeframe`, `start`(YYYY-MM-DD), `startTime`(HH:MM), `end`(YYYY-MM-DD), `endTime`(HH:MM), `buy`(JSON), `sell`(JSON). Task 2의 `PortSetupForm.tsx`가 이 정확한 파라미터 이름으로 읽는다.

- [ ] **Step 1: 전체 교체**

`frontend/components/BacktestRunsTable.tsx` 전체를 아래로 교체:

```tsx
'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { ArrowDown, ArrowUp, ArrowUpDown, Copy, Eye } from 'lucide-react';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Button } from '@/components/ui/button';
import DeleteRunButton from '@/components/DeleteRunButton';
import { returnRateColor } from '@/lib/return-rate-color';
import { summarizeGroup } from '@/lib/condition-summary';
import { formatDateTime } from '@/lib/format';
import type { BacktestRunSummary } from '@/lib/types/eda';

type SortKey = 'return_rate' | 'created_at' | 'market' | 'timeframe';
type SortDir = 'asc' | 'desc';

function sortRuns(runs: BacktestRunSummary[], key: SortKey | null, dir: SortDir): BacktestRunSummary[] {
  if (!key) return runs;
  const factor = dir === 'asc' ? 1 : -1;
  return [...runs].sort((a, b) => {
    const av = a[key];
    const bv = b[key];
    if (av === null && bv === null) return 0;
    if (av === null) return 1;
    if (bv === null) return -1;
    if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * factor;
    return String(av).localeCompare(String(bv)) * factor;
  });
}

function buildCopyHref(run: BacktestRunSummary): string {
  const params = new URLSearchParams({
    market: run.market,
    timeframe: run.timeframe,
    start: run.start.slice(0, 10),
    startTime: run.start.slice(11, 16),
    end: run.end.slice(0, 10),
    endTime: run.end.slice(11, 16),
    buy: JSON.stringify(run.buy_conditions),
    sell: JSON.stringify(run.sell_conditions),
  });
  return `/?${params.toString()}`;
}

interface BacktestRunsTableProps {
  runs: BacktestRunSummary[];
}

export default function BacktestRunsTable({ runs }: BacktestRunsTableProps) {
  const [sortKey, setSortKey] = useState<SortKey | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>('desc');

  const sorted = useMemo(() => sortRuns(runs, sortKey, sortDir), [runs, sortKey, sortDir]);

  function toggleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === 'desc' ? 'asc' : 'desc'));
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
  }

  function SortIcon({ sortKeyOf }: { sortKeyOf: SortKey }) {
    if (sortKey !== sortKeyOf) return <ArrowUpDown className="size-3.5" />;
    return sortDir === 'desc' ? <ArrowDown className="size-3.5" /> : <ArrowUp className="size-3.5" />;
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>제목</TableHead>
          <TableHead>
            <button type="button" className="flex items-center gap-1 hover:text-foreground" onClick={() => toggleSort('market')}>
              코인 <SortIcon sortKeyOf="market" />
            </button>
          </TableHead>
          <TableHead>
            <button type="button" className="flex items-center gap-1 hover:text-foreground" onClick={() => toggleSort('timeframe')}>
              봉타입 <SortIcon sortKeyOf="timeframe" />
            </button>
          </TableHead>
          <TableHead>기간</TableHead>
          <TableHead>매수전략</TableHead>
          <TableHead>매도전략</TableHead>
          <TableHead>
            <button type="button" className="flex items-center gap-1 hover:text-foreground" onClick={() => toggleSort('return_rate')}>
              수익률(%) <SortIcon sortKeyOf="return_rate" />
            </button>
          </TableHead>
          <TableHead>
            <button type="button" className="flex items-center gap-1 hover:text-foreground" onClick={() => toggleSort('created_at')}>
              실행 시각 <SortIcon sortKeyOf="created_at" />
            </button>
          </TableHead>
          <TableHead>상세</TableHead>
          <TableHead>복사</TableHead>
          <TableHead>삭제</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {sorted.map((run) => (
          <TableRow key={run.run_id}>
            <TableCell>
              {run.title || <span className="text-muted-foreground">(제목 없음)</span>}
              {run.description && <p className="text-xs text-muted-foreground">{run.description}</p>}
            </TableCell>
            <TableCell>{run.market}</TableCell>
            <TableCell>{run.timeframe}</TableCell>
            <TableCell>
              {run.start.slice(0, 10)} ~ {run.end.slice(0, 10)}
            </TableCell>
            <TableCell className="max-w-[240px] whitespace-normal font-mono text-xs">
              {summarizeGroup(run.buy_conditions)}
            </TableCell>
            <TableCell className="max-w-[240px] whitespace-normal font-mono text-xs">
              {summarizeGroup(run.sell_conditions)}
            </TableCell>
            <TableCell className={returnRateColor(run.return_rate)}>
              {run.return_rate?.toFixed(2) ?? '-'}
              {run.is_live && <span className="ml-1 text-xs text-muted-foreground">(실시간)</span>}
            </TableCell>
            <TableCell>{formatDateTime(run.created_at)}</TableCell>
            <TableCell>
              <Button
                variant="link"
                size="sm"
                className="px-0"
                nativeButton={false}
                role="link"
                render={<Link href={`/backtests/${run.run_id}`} />}
              >
                <Eye className="size-3.5" />
                보기
              </Button>
            </TableCell>
            <TableCell>
              <Button
                variant="link"
                size="sm"
                className="px-0"
                nativeButton={false}
                role="link"
                render={<Link href={buildCopyHref(run)} />}
              >
                <Copy className="size-3.5" />
                복사
              </Button>
            </TableCell>
            <TableCell>
              <DeleteRunButton runId={run.run_id} />
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
```

(기존 "보기" 셀의 `Button`+`Link` 조합 패턴 — `nativeButton={false}` + `role="link"` — 을 "복사"에도 그대로 재사용한다. 이 패턴은 이전 UI 개선 작업에서 접근성 문제(잘못된 `role="button"`)를 겪고 확정한 방식이다.)

- [ ] **Step 2: 컴파일 확인**

```bash
cd frontend
npx tsc --noEmit
```

Expected: 에러 없음

- [ ] **Step 3: 브라우저로 확인**

```bash
cd frontend && npm run dev
```

Playwright로 `/backtests` 접속(실행된 백테스트가 없으면 `/`에서 하나 먼저 실행) → "복사" 링크에 `Copy` 아이콘이 보이는지 확인, 그 `href`를 확인(스냅샷/DOM에서)해 `market`/`timeframe`/`start`/`startTime`/`end`/`endTime`/`buy`/`sell` 파라미터가 모두 포함되어 있고 `buy`/`sell`이 유효한 JSON 문자열인지 확인. (아직 Task 2를 하지 않았으므로 실제로 클릭했을 때 `/` 페이지가 프리셋을 반영하지는 않는다 — 이 단계에서는 링크 자체의 정확성만 확인한다.)

- [ ] **Step 4: 커밋**

```bash
git add frontend/components/BacktestRunsTable.tsx
git commit -m "feat: 백테스트 결과 목록에 '복사' 컬럼 추가 (설정 프리셋 링크)"
```

---

## Task 2: `PortSetupForm.tsx` + `app/page.tsx` — 프리셋 초기값 반영

**Files:**
- Modify: `frontend/components/PortSetupForm.tsx` (import 블록 + 컴포넌트 상단 일부)
- Modify: `frontend/app/page.tsx` (전체 교체 — `Suspense` 래핑)

**Interfaces:**
- Consumes: Task 1이 정의한 URL 쿼리 파라미터 계약(`market`/`timeframe`/`start`/`startTime`/`end`/`endTime`/`buy`/`sell`).

- [ ] **Step 1: `app/page.tsx`를 `Suspense`로 래핑**

`frontend/app/page.tsx` 전체를 아래로 교체:

```tsx
import { Suspense } from 'react';
import PortSetupForm from '@/components/PortSetupForm';

export default function HomePage() {
  return (
    <div>
      <h1 className="mb-4 text-lg font-semibold">포트 설정</h1>
      <Suspense fallback={null}>
        <PortSetupForm />
      </Suspense>
    </div>
  );
}
```

(`PortSetupForm`이 다음 스텝에서 `useSearchParams()`를 쓰게 되므로 Next.js App Router 요구사항에 따라 `Suspense` 경계가 필요하다.)

- [ ] **Step 2: `PortSetupForm.tsx`에 프리셋 파싱 추가**

`frontend/components/PortSetupForm.tsx`의 두 번째 줄(`import { useRouter } from 'next/navigation';`)을 아래로 교체:

```tsx
import { useRouter, useSearchParams } from 'next/navigation';
```

`formatCapital` 함수 바로 뒤(`export default function PortSetupForm()` 앞)에 추가:

```tsx
function parsePreset(searchParams: URLSearchParams) {
  function parseConditionGroup(raw: string | null): ConditionGroup {
    if (!raw) return EMPTY_CONDITION_GROUP;
    try {
      return JSON.parse(raw) as ConditionGroup;
    } catch {
      return EMPTY_CONDITION_GROUP;
    }
  }

  return {
    market: searchParams.get('market') ?? '',
    timeframe: searchParams.get('timeframe') ?? CANDLE_UNITS[0].timeframe,
    startDate: searchParams.get('start') ?? defaultDate(90),
    startTime: searchParams.get('startTime') ?? '00:00',
    endDate: searchParams.get('end') ?? defaultDate(0),
    endTime: searchParams.get('endTime') ?? '00:00',
    buyConditions: parseConditionGroup(searchParams.get('buy')),
    sellConditions: parseConditionGroup(searchParams.get('sell')),
  };
}
```

`export default function PortSetupForm() {` 바로 다음 줄(`const router = useRouter();` 다음)에 추가:

```tsx
  const searchParams = useSearchParams();
  const [preset] = useState(() => parsePreset(searchParams));
```

그 아래 기존 `useState` 선언들 중 아래 6개 줄을 각각 교체:

```tsx
  const [market, setMarket] = useState(preset.market);
```
```tsx
  const [buyConditions, setBuyConditions] = useState<ConditionGroup>(preset.buyConditions);
  const [sellConditions, setSellConditions] = useState<ConditionGroup>(preset.sellConditions);
  const [capital, setCapital] = useState('1000000');
  const [timeframe, setTimeframe] = useState(preset.timeframe);
  const [startDate, setStartDate] = useState(preset.startDate);
  const [startTime, setStartTime] = useState(preset.startTime);
  const [endDate, setEndDate] = useState(preset.endDate);
  const [endTime, setEndTime] = useState(preset.endTime);
```

(`title`/`description`/`capital`은 결정된 사항대로 프리셋에서 제외 — `capital`은 원래 리터럴 `'1000000'` 그대로 유지, 변경 없음. 기존에 마켓 목록을 불러온 뒤 `market`이 비어 있을 때만 첫 코인을 자동 선택하는 `useEffect` 로직(`setMarket((prev) => prev || sorted[0].market)`)은 코드 변경 없이 그대로 둔다 — 프리셋이 있으면 `prev`가 이미 채워져 있어 자동 선택이 개입하지 않는다.)

- [ ] **Step 3: 컴파일 확인**

```bash
cd frontend
npx tsc --noEmit
```

Expected: 에러 없음

- [ ] **Step 4: 브라우저로 확인**

```bash
cd frontend && npm run dev
```

백엔드가 안 떠 있으면 시작:
```bash
cd /c/Users/jungm/personal/study/260711-upbit-v1 && uvicorn backend.main:app --reload --port 8000
```

Playwright로:
1. `/backtests`에서 기존 실행 하나의 "복사" 링크를 클릭 → `/`로 이동하면서 URL에 쿼리스트링이 그대로 붙어 있는지 확인
2. 폼에 코인 선택이 해당 실행의 코인으로, 봉데이터가 해당 타임프레임으로, 운용기간(날짜+시간)이 해당 기간으로, 매수/매도 조건이 해당 조건식 그대로 채워져 있는지 확인(조건식 요약 문자열을 `/backtests`의 원래 행과 대조)
3. 제목/설명/운용자금은 프리셋 없이 기본값(빈 제목, 1,000,000원)인지 확인
4. 프리셋 상태에서 그대로(또는 살짝 수정 후) "백테스트 실행"을 눌러 정상적으로 실행되는지 확인
5. 쿼리스트링 없이 `/`에 직접 접속했을 때(예: 상단 탭 "백테스트 설정" 클릭)는 기존과 동일하게 빈 폼 기본값으로 뜨는지 확인(회귀 없음)
6. 콘솔 에러 0건 확인(특히 `useSearchParams`/`Suspense` 관련 경고 없는지)

- [ ] **Step 5: 커밋**

```bash
git add frontend/components/PortSetupForm.tsx frontend/app/page.tsx
git commit -m "feat: 백테스트 설정 폼이 URL 쿼리스트링으로 넘어온 프리셋을 초기값으로 반영"
```

---

## Self-Review 결과

- **스펙 커버리지**: 프리셋 대상(코인/봉타입/기간/매수매도)과 제외 대상(제목/설명/자금)이 각각 Task 1의 `buildCopyHref`와 Task 2의 `parsePreset`/`useState` 초기값에 반영됨. 자동 실행 없음(단순 프리셋 채움)도 Task 2 Step 4 검증 항목에 명시.
- **내부 정합성**: Task 1이 만드는 쿼리 파라미터 이름(`market`/`timeframe`/`start`/`startTime`/`end`/`endTime`/`buy`/`sell`)과 Task 2의 `parsePreset`이 읽는 이름이 정확히 일치.
- **범위 확인**: 자동 실행 없음, 제목/설명/자금 제외, 다른 페이지로의 확장 없음을 스펙의 범위 밖에 명시했고 이 플랜도 그 경계를 넘지 않음.
- **대상 파일 목록**: `frontend/components/BacktestRunsTable.tsx`, `frontend/app/page.tsx`, `frontend/components/PortSetupForm.tsx`.
