import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import type { TrendSegment } from '@/lib/types/eda';

function formatShortDate(iso: string): string {
  return iso.slice(5).replace('-', '/');
}

function formatReturn(pct: number): string {
  const sign = pct > 0 ? '+' : '';
  return `${sign}${pct.toFixed(1)}%`;
}

const TREND_TEXT_CLASS: Record<TrendSegment['trend'], string> = {
  up: 'text-[color:var(--price-up)]',
  down: 'text-[color:var(--price-down)]',
  sideways: 'text-muted-foreground',
};

export default function TrendSegmentTable({ segments }: { segments: TrendSegment[] }) {
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
            <TableHead className="text-right">등락률</TableHead>
            <TableHead>패턴</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {segments.map((seg) => (
            <TableRow key={`${seg.start_date}-${seg.end_date}`}>
              <TableCell className="whitespace-nowrap">
                {formatShortDate(seg.start_date)} ~ {formatShortDate(seg.end_date)}
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
