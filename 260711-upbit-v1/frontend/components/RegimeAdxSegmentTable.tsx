'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { ArrowDown, ArrowUp, ArrowUpDown, Copy } from 'lucide-react';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import type { RegimeAdxSegment } from '@/lib/types/eda';
import { formatDateTimeShort } from '@/lib/format';

const LABEL_TEXT_CLASS: Record<RegimeAdxSegment['label'], string> = {
  상승: 'text-[color:var(--regime-surge-up)]',
  하락: 'text-[color:var(--regime-surge-down)]',
  횡보: 'text-[color:var(--marker-boundary)]',
};

// formatDateTimeShort(lib/format.ts)와 동일한 KST(Asia/Seoul) 변환 방식을
// 재사용한다 — 표에 보이는 날짜(KST)와 그리드서치 링크에 실리는 날짜(과거
// UTC slice(0, 10))가 어긋나던 버그 수정.
const KST_DATE_FORMATTER = new Intl.DateTimeFormat('en-US', {
  timeZone: 'Asia/Seoul',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
});

function toKstDateString(iso: string): string {
  const parts = KST_DATE_FORMATTER.formatToParts(new Date(iso));
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? '';
  return `${get('year')}-${get('month')}-${get('day')}`;
}

function addOneDay(dateStr: string): string {
  const [year, month, day] = dateStr.split('-').map(Number);
  const nextDay = new Date(Date.UTC(year, month - 1, day + 1));
  return nextDay.toISOString().slice(0, 10);
}

function buildGridSearchHref(market: string, timeframe: string, seg: RegimeAdxSegment): string {
  const startDate = toKstDateString(seg.start);
  let endDate = toKstDateString(seg.end);
  // 최소 지속봉수(24봉) 구간이 KST 기준으로 하루를 넘지 않으면(예: 자정
  // 근처 시작) start==end가 될 수 있다. 그리드서치 백엔드는
  // start>=end를 거부하므로, 하루를 인위적으로 더해 유효한 범위로 만든다.
  if (endDate === startDate) {
    endDate = addOneDay(endDate);
  }
  const params = new URLSearchParams({
    market,
    timeframe,
    start: startDate,
    end: endDate,
  });
  return `/grid-search?${params.toString()}`;
}

type SortKey = 'start' | 'bar_count';
type SortDir = 'asc' | 'desc';

function SegmentRow({
  seg, market, timeframe,
}: { seg: RegimeAdxSegment; market: string; timeframe: string }) {
  return (
    <TableRow className={seg.in_progress ? 'bg-muted/40' : undefined}>
      <TableCell className="whitespace-nowrap">
        {formatDateTimeShort(seg.start)} ~ {seg.in_progress ? '현재' : formatDateTimeShort(seg.end)}
      </TableCell>
      <TableCell className="text-right tabular-nums">{seg.bar_count}봉</TableCell>
      <TableCell>
        <span className="flex items-center gap-1.5">
          <span className={LABEL_TEXT_CLASS[seg.label]}>{seg.label}</span>
          {seg.in_progress && <Badge variant="secondary">진행중</Badge>}
        </span>
      </TableCell>
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
  );
}

export default function RegimeAdxSegmentTable({
  segments, market, timeframe,
}: { segments: RegimeAdxSegment[]; market: string; timeframe: string }) {
  const [sortKey, setSortKey] = useState<SortKey>('start');
  const [sortDir, setSortDir] = useState<SortDir>('asc');

  // 진행중(가장 최근, 아직 24봉 미만일 수 있는) 구간은 정렬 대상에서 빼고
  // 표 맨 위에 항상 고정해, 표를 보면 언제나 현재 장세를 알 수 있게 한다.
  const inProgress = segments.find((s) => s.in_progress);
  const historical = segments.filter((s) => !s.in_progress);

  const sorted = useMemo(() => {
    const factor = sortDir === 'asc' ? 1 : -1;
    return [...historical].sort((a, b) => {
      if (sortKey === 'start') {
        return a.start < b.start ? -factor : a.start > b.start ? factor : 0;
      }
      return (a.bar_count - b.bar_count) * factor;
    });
  }, [historical, sortKey, sortDir]);

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
          {inProgress && (
            <SegmentRow key={`${inProgress.start}-in-progress`} seg={inProgress} market={market} timeframe={timeframe} />
          )}
          {sorted.length === 0 ? (
            <TableRow>
              <TableCell colSpan={4} className="text-muted-foreground">
                최소 지속봉수를 넘는 과거 구간이 없습니다.
              </TableCell>
            </TableRow>
          ) : (
            sorted.map((seg) => (
              <SegmentRow key={`${seg.start}-${seg.end}`} seg={seg} market={market} timeframe={timeframe} />
            ))
          )}
        </TableBody>
      </Table>
    </div>
  );
}
