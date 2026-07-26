import { AlertTriangle } from 'lucide-react';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { changeColorClass, formatChangeRate, formatPrice, formatTradePrice24h } from '@/lib/market-format';
import type { SegmentSizeEntry } from '@/lib/types/eda';

export interface SegmentRow extends SegmentSizeEntry {
  price: number | null;
  change_rate: number | null;
  change_price: number | null;
  trade_price_24h: number | null;
}

const SEGMENT_ORDER: SegmentRow['segment'][] = ['large', 'mid', 'junk'];
const SEGMENT_LABELS: Record<SegmentRow['segment'], string> = {
  large: '대형주',
  mid: '중형주',
  junk: '잡주',
};

function formatVolatility(value: number | null): string {
  if (value === null) return '-';
  return `${(value * 100).toFixed(2)}%`;
}

export function groupBySegment(rows: SegmentRow[]): { segment: SegmentRow['segment']; rows: SegmentRow[] }[] {
  return SEGMENT_ORDER.map((segment) => ({
    segment,
    rows: rows.filter((r) => r.segment === segment),
  }));
}

export default function SegmentSizeTable({ rows }: { rows: SegmentRow[] }) {
  if (rows.length === 0) {
    return <p className="text-muted-foreground">배치 실행 중입니다. 잠시 후 새로고침해 주세요.</p>;
  }

  return (
    <div className="flex flex-col gap-6">
      {groupBySegment(rows).map(({ segment, rows: group }) => (
        <div key={segment}>
          <p className="mb-2 text-sm font-semibold">
            {SEGMENT_LABELS[segment]} ({group.length})
          </p>
          <div className="max-h-80 overflow-auto rounded-md border [&>[data-slot=table-container]]:overflow-visible">
            {/* Table's own wrapper div sets overflow-x-auto (table.tsx), which per the CSS
                overflow spec forces its overflow-y to become "auto" too (an axis can't stay
                "visible" once the other is non-visible). That makes the *inner* wrapper the
                nearest scrolling ancestor instead of this outer div, so `sticky top-0` on
                TableHeader binds to the inner wrapper and never actually sticks while this
                outer div scrolls. Forcing the inner wrapper back to overflow-visible removes
                it as a scroll container, so sticky correctly targets this outer div (which
                itself becomes the scrollable ancestor for both axes once overflow-auto is set).
                overflow-auto (not overflow-y-auto) is used here because this table sits in a
                `min-w-0 flex-1` content area next to a fixed-width sidebar (AnalysisSidebarView),
                so on narrow viewports it can be squeezed well below its natural width and needs
                horizontal scroll too (same reasoning as heatmap/page.tsx's fix history). */}
            <Table>
              <TableHeader className="sticky top-0 z-10 bg-background">
                <TableRow>
                  <TableHead>코인</TableHead>
                  <TableHead className="text-right">현재가</TableHead>
                  <TableHead className="text-right">전일대비등락률</TableHead>
                  <TableHead className="text-right">거래대금</TableHead>
                  <TableHead className="text-right">변동성(30일)</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {group.map((r) => (
                  <TableRow key={r.market}>
                    <TableCell>
                      {r.korean_name}
                      {r.is_caution && (
                        <span className="ml-2 inline-flex items-center gap-1 text-xs text-amber-600 dark:text-amber-400">
                          <AlertTriangle className="size-3.5" />
                          유의종목
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{formatPrice(r.price)}</TableCell>
                    <TableCell className={`text-right tabular-nums ${changeColorClass(r.change_rate)}`}>
                      {formatChangeRate(r.change_rate)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums text-muted-foreground">
                      {formatTradePrice24h(r.trade_price_24h)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums text-muted-foreground">
                      {formatVolatility(r.volatility_30d)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </div>
      ))}
    </div>
  );
}
