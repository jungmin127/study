import Link from 'next/link';
import { getHeatmap } from '@/lib/api/eda';
import { Badge } from '@/components/ui/badge';
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
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>전략</TableHead>
              <TableHead>코인</TableHead>
              <TableHead>봉타입</TableHead>
              <TableHead>수익률(%)</TableHead>
              <TableHead>Sharpe</TableHead>
              <TableHead>MDD(%)</TableHead>
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
                <TableCell className={returnRateColor(row.return_rate)}>
                  {row.return_rate?.toFixed(2) ?? '-'}
                </TableCell>
                <TableCell>{row.sharpe?.toFixed(2) ?? '-'}</TableCell>
                <TableCell>{row.max_drawdown?.toFixed(2) ?? '-'}</TableCell>
                <TableCell>{row.swept_at}</TableCell>
                <TableCell>
                  <Link href={`/backtests/${row.run_id}`} className="text-blue-600 hover:underline dark:text-blue-400">
                    보기
                  </Link>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
