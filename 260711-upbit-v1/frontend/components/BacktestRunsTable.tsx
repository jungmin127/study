'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { ArrowDown, ArrowUp, ArrowUpDown, Eye } from 'lucide-react';
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
              {/* base-ui's Button doesn't support Radix-style `asChild` composition — it
                  exposes a `render` prop instead, so the Link is swapped in as the underlying
                  element while keeping Button's variant/size styling. `nativeButton={false}`
                  is required here: base-ui's Button assumes the `render` target is a real
                  <button> by default and otherwise logs a console error since Link renders
                  an <a>. */}
              <Button
                variant="link"
                size="sm"
                className="px-0"
                nativeButton={false}
                render={<Link href={`/backtests/${run.run_id}`} />}
              >
                <Eye className="size-3.5" />
                보기
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
