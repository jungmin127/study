'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { ArrowDown, ArrowUp, ArrowUpDown, Copy } from 'lucide-react';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Button } from '@/components/ui/button';
import type { RegimeFactSegment } from '@/lib/types/eda';
import { formatDateTimeShort } from '@/lib/format';

const LABEL_TEXT_CLASS: Record<RegimeFactSegment['label'], string> = {
  하락: 'text-[color:var(--regime-surge-down)]',
  하락아님: 'text-muted-foreground',
};

function buildGridSearchHref(market: string, timeframe: string, seg: RegimeFactSegment): string {
  const params = new URLSearchParams({
    market,
    timeframe,
    start: seg.start.slice(0, 10),
    end: seg.end.slice(0, 10),
  });
  return `/grid-search?${params.toString()}`;
}

type SortKey = 'start' | 'bar_count';
type SortDir = 'asc' | 'desc';

export default function RegimeFactSegmentTable({
  segments, market, timeframe,
}: { segments: RegimeFactSegment[]; market: string; timeframe: string }) {
  const [sortKey, setSortKey] = useState<SortKey>('start');
  const [sortDir, setSortDir] = useState<SortDir>('asc');

  const sorted = useMemo(() => {
    const factor = sortDir === 'asc' ? 1 : -1;
    return [...segments].sort((a, b) => {
      // start(string)와 bar_count(number)는 비교 타입이 달라 분기마다 같은
      // 타입끼리 비교한다.
      if (sortKey === 'start') {
        return a.start < b.start ? -factor : a.start > b.start ? factor : 0;
      }
      return (a.bar_count - b.bar_count) * factor;
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
    return <p className="text-muted-foreground">최소 지속봉수를 넘는 구간이 없습니다.</p>;
  }

  return (
    <div className="max-h-96 overflow-auto rounded-md border [&>[data-slot=table-container]]:overflow-visible">
      <Table>
        <TableHeader className="sticky top-0 z-10 bg-background">
          <TableRow>
            <TableHead>
              <button
                type="button"
                className="flex items-center gap-1 hover:text-foreground"
                onClick={() => toggleSort('start')}
              >
                기간 <SortIcon sortKeyOf="start" />
              </button>
            </TableHead>
            <TableHead className="text-right">
              <button
                type="button"
                className="flex w-full items-center justify-end gap-1 hover:text-foreground"
                onClick={() => toggleSort('bar_count')}
              >
                지속 <SortIcon sortKeyOf="bar_count" />
              </button>
            </TableHead>
            <TableHead>라벨</TableHead>
            <TableHead />
          </TableRow>
        </TableHeader>
        <TableBody>
          {sorted.map((seg) => (
            <TableRow key={`${seg.start}-${seg.end}`}>
              <TableCell className="whitespace-nowrap">
                {formatDateTimeShort(seg.start)} ~ {formatDateTimeShort(seg.end)}
              </TableCell>
              <TableCell className="text-right tabular-nums">{seg.bar_count}봉</TableCell>
              <TableCell className={LABEL_TEXT_CLASS[seg.label]}>{seg.label}</TableCell>
              <TableCell>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  nativeButton={false}
                  role="link"
                  aria-label="그리드서치로 복사"
                  title="그리드서치로 복사"
                  render={<Link href={buildGridSearchHref(market, timeframe, seg)} />}
                >
                  <Copy className="size-3.5" />
                </Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
