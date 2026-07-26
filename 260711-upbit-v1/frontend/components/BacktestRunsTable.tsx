'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { ArrowDown, ArrowUp, ArrowUpDown, Copy, Eye, Trash2 } from 'lucide-react';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Button, buttonVariants } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
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

function LastTradeStatusBadge({ status }: { status: BacktestRunSummary['last_trade_status'] }) {
  if (status === 'none') return <span className="text-muted-foreground">-</span>;
  if (status === 'open') return <Badge variant="secondary">보유중</Badge>;
  return <Badge variant="outline">청산</Badge>;
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
    setSelected(new Set());
    if (failedCount > 0) {
      setBulkError(`${failedCount}건 삭제에 실패했습니다. 잠시 후 다시 시도해 주세요.`);
    } else {
      setConfirmOpen(false);
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
            <TableHead>상태</TableHead>
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
              <TableCell>
                <LastTradeStatusBadge status={run.last_trade_status} />
              </TableCell>
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
