import { AlertTriangle } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import type { SegmentSizeEntry } from '@/lib/types/eda';

const SEGMENT_ORDER: SegmentSizeEntry['segment'][] = ['large', 'mid', 'junk'];
const SEGMENT_LABELS: Record<SegmentSizeEntry['segment'], string> = {
  large: '대형주',
  mid: '중형주',
  junk: '잡주',
};

function formatTradeValue(value: number | null): string {
  if (value === null) return '-';
  return `${Math.round(value / 100_000_000).toLocaleString('ko-KR')}억`;
}

function formatVolatility(value: number | null): string {
  if (value === null) return '-';
  return `${(value * 100).toFixed(2)}%`;
}

export function groupBySegment(
  entries: SegmentSizeEntry[]
): { segment: SegmentSizeEntry['segment']; entries: SegmentSizeEntry[] }[] {
  return SEGMENT_ORDER.map((segment) => ({
    segment,
    entries: entries.filter((e) => e.segment === segment),
  }));
}

export default function SegmentSizeCard({ entries }: { entries: SegmentSizeEntry[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>세그먼트(규모)</CardTitle>
      </CardHeader>
      <CardContent>
        {entries.length === 0 ? (
          <p className="text-muted-foreground">배치 실행 중입니다. 잠시 후 새로고침해 주세요.</p>
        ) : (
          <div className="flex flex-col gap-4">
            {groupBySegment(entries).map(({ segment, entries: group }) => (
              <div key={segment}>
                <p className="mb-2 text-sm font-semibold">
                  {SEGMENT_LABELS[segment]} ({group.length})
                </p>
                <div className="flex flex-col gap-1">
                  {group.map((e) => (
                    <div key={e.market} className="flex items-center justify-between text-sm">
                      <span>
                        {e.korean_name}
                        {e.is_caution && (
                          <span className="ml-2 inline-flex items-center gap-1 text-xs text-amber-600 dark:text-amber-400">
                            <AlertTriangle className="size-3.5" />
                            유의종목
                          </span>
                        )}
                      </span>
                      <span className="tabular-nums text-muted-foreground">
                        거래대금 {formatTradeValue(e.trade_value_24h)} · 변동성{' '}
                        {formatVolatility(e.volatility_30d)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
