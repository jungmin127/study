import Link from 'next/link';
import { Eye } from 'lucide-react';
import { getHeatmap } from '@/lib/api/eda';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { returnRateColor } from '@/lib/return-rate-color';

export default async function HeatmapPage() {
  const rows = await getHeatmap();

  return (
    <div>
      <h1 className="text-lg font-semibold mb-4">전략 × 코인 × 봉타입 수익률</h1>
      {rows.length === 0 ? (
        <p className="text-muted-foreground">아직 스윕 데이터가 없습니다. run_sweep()을 먼저 실행하세요.</p>
      ) : (
        <div className="max-h-[70vh] overflow-y-auto rounded-md border [&>[data-slot=table-container]]:overflow-visible">
          {/* Table's own wrapper div sets overflow-x-auto (table.tsx), which per the CSS
              overflow spec forces its overflow-y to become "auto" too (an axis can't stay
              "visible" once the other is non-visible). That makes the *inner* wrapper the
              nearest scrolling ancestor instead of this outer div, so `sticky top-0` on
              TableHeader binds to the inner wrapper and never actually sticks while this
              outer div scrolls. Forcing the inner wrapper back to overflow-visible removes
              it as a scroll container, so sticky correctly targets this outer div (which
              itself becomes the scrollable ancestor for both axes once overflow-y-auto is set). */}
          <Table>
            <TableHeader className="sticky top-0 z-10 bg-background">
              <TableRow>
                <TableHead>전략</TableHead>
                <TableHead>코인</TableHead>
                <TableHead>봉타입</TableHead>
                <TableHead className="text-right">수익률(%)</TableHead>
                <TableHead className="text-right">Sharpe</TableHead>
                <TableHead className="text-right">MDD(%)</TableHead>
                <TableHead>스윕 시각</TableHead>
                <TableHead>상세</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={`${row.signal_set_name}-${row.market}-${row.timeframe}`}>
                  <TableCell>
                    {row.signal_set_name}
                    {row.is_combined && <Badge className="ml-2" variant="secondary">혼합</Badge>}
                  </TableCell>
                  <TableCell>{row.market}</TableCell>
                  <TableCell>{row.timeframe}</TableCell>
                  <TableCell className={`text-right tabular-nums ${returnRateColor(row.return_rate)}`}>
                    {row.return_rate?.toFixed(2) ?? '-'}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{row.sharpe?.toFixed(2) ?? '-'}</TableCell>
                  <TableCell className="text-right tabular-nums">{row.max_drawdown?.toFixed(2) ?? '-'}</TableCell>
                  <TableCell>{row.swept_at}</TableCell>
                  <TableCell>
                    {/* base-ui's Button doesn't support Radix-style `asChild` composition — it
                        exposes a `render` prop instead, so the Link is swapped in as the underlying
                        element while keeping Button's variant/size styling. `nativeButton={false}`
                        is required here: base-ui's Button assumes the `render` target is a real
                        <button> by default and otherwise logs a console error since Link renders
                        an <a>. `role="link"` overrides base-ui's default `role="button"` on the
                        rendered <a>, which would otherwise be an accessibility defect. */}
                    <Button
                      variant="link"
                      size="sm"
                      className="px-0"
                      nativeButton={false}
                      role="link"
                      render={<Link href={`/backtests/${row.run_id}`} />}
                    >
                      <Eye className="size-3.5" />
                      보기
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}
