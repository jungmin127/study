import { getBacktestDetail } from '@/lib/api/eda';
import EquityCurveChart from '@/components/EquityCurveChart';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';

export default async function BacktestDetailPage({ params }: { params: { runId: string } }) {
  const detail = await getBacktestDetail(params.runId);

  return (
    <div>
      <h1 className="text-lg font-semibold mb-2">백테스트 상세</h1>
      <p className="text-sm text-muted-foreground mb-4">
        최종 자산: {detail.final_value.toFixed(0)} · Sharpe: {detail.sharpe?.toFixed(2) ?? '-'} · MDD: {detail.max_drawdown?.toFixed(2) ?? '-'}%
      </p>

      <h2 className="font-medium mb-2">자산 곡선</h2>
      <EquityCurveChart equityCurve={detail.equity_curve} />

      <h2 className="font-medium mt-6 mb-2">거래 내역 ({detail.trades.length}건)</h2>
      {detail.trades.length === 0 ? (
        <p className="text-muted-foreground">거래 내역이 없습니다.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>진입</TableHead>
              <TableHead>청산</TableHead>
              <TableHead>수익률(%)</TableHead>
              <TableHead>보유기간</TableHead>
              <TableHead>강제청산</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {detail.trades.map((t, i) => (
              <TableRow key={i}>
                <TableCell>{t.entryTime}</TableCell>
                <TableCell>{t.exitTime}</TableCell>
                <TableCell className={t.returnRate >= 0 ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'}>
                  {t.returnRate.toFixed(2)}
                </TableCell>
                <TableCell>{t.holdingPeriod}</TableCell>
                <TableCell>{t.forceClosed ? 'Y' : ''}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
