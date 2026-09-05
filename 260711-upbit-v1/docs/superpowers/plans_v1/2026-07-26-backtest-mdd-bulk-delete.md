# 백테스트 결과 MDD 컬럼 + 체크박스 일괄삭제 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/backtests` 목록에 "MDD(%)" 컬럼을 추가하고, 개별 삭제 컬럼을 체크박스 기반 일괄삭제로 교체한다.

**Architecture:** 백엔드/타입 변경 없음(`max_drawdown`은 이미 존재). shadcn `Checkbox` 프리미티브를 추가한 뒤, `BacktestRunsTable.tsx`에 선택 상태(`Set<string>`)와 상단 툴바를 추가하고, 기존 `DELETE /api/v1/backtests/{run_id}`를 선택 개수만큼 병렬 호출한다. `DeleteRunButton.tsx`는 삭제.

**Tech Stack:** Next.js 14 / TypeScript / shadcn Checkbox / lucide-react

**참고 스펙:** `docs/superpowers/specs_v1/2026-07-26-backtest-mdd-bulk-delete-design.md`

## Global Constraints

- 백엔드(`backend/`, `engine/`) 변경 없음. 새 배치 DELETE API를 만들지 않고 기존 단건 API를 병렬 호출한다.
- 이 프론트엔드에는 자동화된 테스트 러너가 없다 — 검증은 `npm run dev` + Playwright로 한다.
- `npm run build`를 `npm run dev`가 떠 있는 상태에서 실행하지 않는다 — 컴파일 확인은 `npx tsc --noEmit`.
- MDD 컬럼은 `heatmap`/`ranking` 페이지가 이미 쓰는 컨벤션(부호 반전·색상 없이 `toFixed(2)`)을 그대로 따른다.
- shadcn CLI가 생성하는 `Checkbox`의 정확한 props(`checked`/`onCheckedChange`로 가정)는 이 프로젝트의 `base-ui` 기반 스타일에 따라 다를 수 있다 — Task 2는 Task 1이 실제로 생성한 파일을 먼저 확인한 뒤 맞춰 쓴다.
- 다이얼로그를 닫았다 다시 열 때 이전 에러 메시지가 남아있지 않아야 한다(이 저장소에서 이미 한 번 발견·수정된 패턴).

---

## Task 1: shadcn `Checkbox` 프리미티브 추가

**Files:**
- Create: `frontend/components/ui/checkbox.tsx` (shadcn CLI 산출물)

**Interfaces:**
- Produces: `Checkbox` 컴포넌트 — Task 2가 소비. 정확한 props는 이 태스크의 Step 2에서 확인.

- [ ] **Step 1: shadcn CLI로 추가**

```bash
cd frontend
npx shadcn@latest add checkbox
```

- [ ] **Step 2: 생성된 파일의 export/props 확인**

```bash
grep -n "^export\|checked\|onCheckedChange" frontend/components/ui/checkbox.tsx
```

`checked`/`onCheckedChange`가 실제 export된 props와 다르면, Task 2에서 그 실제 이름으로 맞춰 쓴다.

- [ ] **Step 3: 타입 체크**

```bash
cd frontend
npx tsc --noEmit
```

Expected: 에러 없음 (아직 아무 곳에서도 `Checkbox`를 쓰지 않으므로 새 파일 자체의 타입 오류만 없으면 됨)

- [ ] **Step 4: 커밋**

```bash
git add frontend/components/ui/checkbox.tsx frontend/package.json frontend/package-lock.json
git commit -m "feat: shadcn Checkbox 추가"
```

---

## Task 2: `BacktestRunsTable.tsx` — MDD 컬럼 + 체크박스 일괄삭제

**Files:**
- Modify: `frontend/components/BacktestRunsTable.tsx` (전체 교체)
- Delete: `frontend/components/DeleteRunButton.tsx`

**Interfaces:**
- Consumes: Task 1의 `Checkbox`(`@/components/ui/checkbox`), 기존 `deleteBacktestRun(runId: string): Promise<{ deleted: boolean }>` (`@/lib/api/eda`, 변경 없음).

- [ ] **Step 1: 전체 교체**

`frontend/components/BacktestRunsTable.tsx` 전체를 아래로 교체:

```tsx
'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { ArrowDown, ArrowUp, ArrowUpDown, Copy, Eye, Trash2 } from 'lucide-react';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Button, buttonVariants } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import { returnRateColor } from '@/lib/return-rate-color';
import { summarizeGroup } from '@/lib/condition-summary';
import { formatDateTime } from '@/lib/format';
import { deleteBacktestRun } from '@/lib/api/eda';
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
  const router = useRouter();
  const [sortKey, setSortKey] = useState<SortKey | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>('desc');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const [bulkError, setBulkError] = useState<string | null>(null);

  const sorted = useMemo(() => sortRuns(runs, sortKey, sortDir), [runs, sortKey, sortDir]);
  const allSelected = sorted.length > 0 && selected.size === sorted.length;

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

  function toggleOne(runId: string, checked: boolean) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (checked) next.add(runId);
      else next.delete(runId);
      return next;
    });
  }

  function toggleAll(checked: boolean) {
    setSelected(checked ? new Set(sorted.map((r) => r.run_id)) : new Set());
  }

  async function handleBulkDelete() {
    setBulkDeleting(true);
    setBulkError(null);
    const ids = Array.from(selected);
    const results = await Promise.allSettled(ids.map((id) => deleteBacktestRun(id)));
    const failedCount = results.filter((r) => r.status === 'rejected').length;
    setBulkDeleting(false);
    setConfirmOpen(false);
    setSelected(new Set());
    if (failedCount > 0) {
      setBulkError(`${failedCount}건 삭제에 실패했습니다. 잠시 후 다시 시도해 주세요.`);
    }
    router.refresh();
  }

  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          {selected.size > 0 ? `${selected.size}개 선택됨` : ''}
        </p>
        <AlertDialog
          open={confirmOpen}
          onOpenChange={(open) => {
            setConfirmOpen(open);
            if (!open) setBulkError(null);
          }}
        >
          {/* AlertDialogTrigger has no asChild in this project's base-ui-backed shadcn style;
              apply Button's own class-variance styles directly (same pattern used previously
              in DeleteRunButton.tsx) instead of composing via render={<Button/>}. */}
          <AlertDialogTrigger
            type="button"
            className={buttonVariants({ variant: 'destructive', size: 'sm' })}
            disabled={selected.size === 0}
          >
            <Trash2 className="size-3.5" />
            선택 삭제{selected.size > 0 ? ` (${selected.size})` : ''}
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>선택한 {selected.size}개의 백테스트 결과를 삭제하시겠습니까?</AlertDialogTitle>
              <AlertDialogDescription>삭제 후에는 되돌릴 수 없습니다.</AlertDialogDescription>
            </AlertDialogHeader>
            {bulkError && <p className="text-sm text-destructive">{bulkError}</p>}
            <AlertDialogFooter>
              <AlertDialogCancel>취소</AlertDialogCancel>
              <AlertDialogAction onClick={handleBulkDelete} disabled={bulkDeleting}>
                {bulkDeleting ? '삭제 중...' : '삭제'}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-8">
              <Checkbox checked={allSelected} onCheckedChange={(checked) => toggleAll(checked === true)} aria-label="전체 선택" />
            </TableHead>
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
            <TableHead className="text-right">MDD(%)</TableHead>
            <TableHead>
              <button type="button" className="flex items-center gap-1 hover:text-foreground" onClick={() => toggleSort('created_at')}>
                실행 시각 <SortIcon sortKeyOf="created_at" />
              </button>
            </TableHead>
            <TableHead>상세</TableHead>
            <TableHead>복사</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {sorted.map((run) => (
            <TableRow key={run.run_id}>
              <TableCell>
                <Checkbox
                  checked={selected.has(run.run_id)}
                  onCheckedChange={(checked) => toggleOne(run.run_id, checked === true)}
                  aria-label={`${run.title || run.run_id} 선택`}
                />
              </TableCell>
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
              <TableCell className="text-right tabular-nums">{run.max_drawdown?.toFixed(2) ?? '-'}</TableCell>
              <TableCell>{formatDateTime(run.created_at)}</TableCell>
              <TableCell>
                {/* nativeButton={false} + role="link" here and below: base-ui's Button `render`
                    prop assumes a real <button> by default and otherwise auto-applies
                    role="button" to the rendered <a>, breaking screen-reader link semantics. */}
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
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
```

`Checkbox`의 `onCheckedChange` 콜백 인자 타입이 Task 1에서 확인한 실제 export와 다르면(예: `boolean`만 오고 `'indeterminate'`가 없다면 `checked === true` 비교 대신 그냥 `checked`를 직접 써도 됨), 그 실제 타입에 맞춰 조정한다.

- [ ] **Step 2: `DeleteRunButton.tsx` 삭제**

```bash
git rm frontend/components/DeleteRunButton.tsx
```

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

Playwright로 `/backtests` 접속(백테스트 실행이 최소 2~3개는 있어야 함 — 없으면 `/`에서 몇 개 실행):
1. "수익률(%)" 옆에 "MDD(%)" 컬럼이 실제 값으로 채워져 있는지 확인
2. 개별 "삭제" 컬럼이 더 이상 없는지 확인
3. 헤더의 전체 선택 체크박스를 클릭하면 모든 행이 선택되고, 다시 클릭하면 전체 해제되는지 확인
4. 행 몇 개만 개별 체크했을 때 상단에 "N개 선택됨"과 "선택 삭제 (N)" 버튼이 정확한 개수로 표시되는지, 선택이 0개일 땐 버튼이 비활성화되는지 확인
5. "선택 삭제" 클릭 → 확인 다이얼로그가 뜨고 "삭제" 클릭 시 선택된 항목들이 실제로 사라지는지(새로고침 후 목록에서 없어짐) 확인
6. 콘솔 에러 0건 확인

- [ ] **Step 5: 커밋**

```bash
git add frontend/components/BacktestRunsTable.tsx
git rm frontend/components/DeleteRunButton.tsx 2>/dev/null || true
git commit -m "feat: 백테스트 결과에 MDD 컬럼과 체크박스 일괄삭제 추가, 개별 삭제 컬럼 제거"
```

---

## Self-Review 결과

- **스펙 커버리지**: MDD 컬럼(수익률 옆, heatmap/ranking과 동일 포맷), 체크박스+일괄삭제(전체선택/개별선택/상단 툴바/확인 다이얼로그/부분실패 처리/재오픈시 에러 초기화), 개별 삭제 컬럼·`DeleteRunButton.tsx` 제거가 모두 Task 2에 반영됨.
- **내부 정합성**: Task 1이 만드는 `Checkbox`를 Task 2가 그대로 소비하는 순서가 맞고, 두 태스크 모두 "실제 생성된 props를 확인 후 조정" 원칙을 일관되게 명시.
- **범위 확인**: 새 배치 DELETE API 없음(기존 단건 API 병렬 호출), MDD 정렬 없음을 스펙의 범위 밖에 명시했고 플랜도 이를 넘지 않음.
- **대상 파일 목록**: `frontend/components/ui/checkbox.tsx`(신규), `frontend/components/BacktestRunsTable.tsx`, `frontend/components/DeleteRunButton.tsx`(삭제).
