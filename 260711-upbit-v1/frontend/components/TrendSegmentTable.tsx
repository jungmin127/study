'use client';

import { useMemo, useState } from 'react';
import { ArrowDown, ArrowUp, ArrowUpDown } from 'lucide-react';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import type { TrendSegment } from '@/lib/types/eda';

function formatReturn(pct: number): string {
  const sign = pct > 0 ? '+' : '';
  return `${sign}${pct.toFixed(1)}%`;
}

const TREND_TEXT_CLASS: Record<TrendSegment['trend'], string> = {
  up: 'text-[color:var(--price-up)]',
  down: 'text-[color:var(--price-down)]',
  sideways: 'text-muted-foreground',
};

// TrendPatternLegend.tsx의 3x3 격자와 동일한 순서(상승→하락→횡보)로 패턴을 줄세운다.
const TREND_RANK: Record<TrendSegment['trend'], number> = { up: 0, down: 1, sideways: 2 };

function patternRank(seg: TrendSegment): number {
  return TREND_RANK[seg.first_half_trend] * 3 + TREND_RANK[seg.second_half_trend];
}

type SortKey = 'pattern' | 'return_pct';
type SortDir = 'asc' | 'desc';

export default function TrendSegmentTable({ segments }: { segments: TrendSegment[] }) {
  const [sortKey, setSortKey] = useState<SortKey | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>('desc');

  const sorted = useMemo(() => {
    if (!sortKey) return segments;
    const factor = sortDir === 'asc' ? 1 : -1;
    return [...segments].sort((a, b) => {
      const av = sortKey === 'pattern' ? patternRank(a) : a.return_pct;
      const bv = sortKey === 'pattern' ? patternRank(b) : b.return_pct;
      return (av - bv) * factor;
    });
  }, [segments, sortKey, sortDir]);

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

  if (segments.length === 0) {
    return <p className="text-muted-foreground">구간 데이터가 없습니다.</p>;
  }

  return (
    <div className="max-h-96 overflow-auto rounded-md border [&>[data-slot=table-container]]:overflow-visible">
      <Table>
        <TableHeader className="sticky top-0 z-10 bg-background">
          <TableRow>
            <TableHead>기간</TableHead>
            <TableHead className="text-right">일수</TableHead>
            <TableHead className="text-right">
              <button
                type="button"
                className="flex w-full items-center justify-end gap-1 hover:text-foreground"
                onClick={() => toggleSort('return_pct')}
              >
                등락률 <SortIcon sortKeyOf="return_pct" />
              </button>
            </TableHead>
            <TableHead>
              <button
                type="button"
                className="flex items-center gap-1 hover:text-foreground"
                onClick={() => toggleSort('pattern')}
              >
                패턴 <SortIcon sortKeyOf="pattern" />
              </button>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {sorted.map((seg) => (
            <TableRow key={`${seg.start_date}-${seg.end_date}`}>
              <TableCell className="whitespace-nowrap">
                {seg.start_date} ~ {seg.end_date}
              </TableCell>
              <TableCell className="text-right tabular-nums">{seg.days}일</TableCell>
              <TableCell className={`text-right tabular-nums ${TREND_TEXT_CLASS[seg.trend]}`}>
                {formatReturn(seg.return_pct)}
              </TableCell>
              <TableCell>{seg.pattern_label}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
