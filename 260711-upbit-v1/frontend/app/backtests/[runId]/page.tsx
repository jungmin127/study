import { getBacktestDetail } from '@/lib/api/eda';
import PriceChart from '@/components/PriceChart';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { returnRateColor } from '@/lib/return-rate-color';
import { formatDateTime } from '@/lib/format';
import type { BacktestMetrics } from '@/lib/types/eda';

function fmtPct(value: number): string {
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`;
}

function MetricTile({ label, value, colorClass }: { label: string; value: string; colorClass?: string }) {
  return (
    <div className="rounded-md border p-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className={`mt-1 text-base font-semibold ${colorClass ?? ''}`}>{value}</p>
    </div>
  );
}

function MetricsGrid({ metrics }: { metrics: BacktestMetrics }) {
  const tiles: { label: string; value: string; colorClass?: string }[] = [
    { label: '총 수익률', value: fmtPct(metrics.total_return), colorClass: returnRateColor(metrics.total_return) },
    { label: 'CAGR', value: fmtPct(metrics.cagr), colorClass: returnRateColor(metrics.cagr) },
    { label: 'Buy&Hold', value: fmtPct(metrics.buy_and_hold_return), colorClass: returnRateColor(metrics.buy_and_hold_return) },
    { label: 'MDD', value: fmtPct(metrics.mdd), colorClass: returnRateColor(metrics.mdd) },
    { label: '샤프 비율', value: metrics.sharpe_ratio.toFixed(2) },
    { label: '소르티노', value: metrics.sortino_ratio.toFixed(2) },
    { label: '칼마 비율', value: metrics.calmar_ratio.toFixed(2) },
    { label: '총 거래', value: `${metrics.total_trades}건` },
    { label: '승률', value: `${metrics.win_rate.toFixed(1)}%` },
    { label: '손익비', value: metrics.profit_factor.toFixed(2) },
    { label: '평균 보유', value: `${metrics.avg_holding_period.toFixed(1)}일` },
    { label: '최대연속손실', value: `${metrics.max_consecutive_loss}건` },
  ];

  return (
    <div>
      <h2 className="mb-2 text-sm font-semibold">성과 지표</h2>
      <div className="grid grid-cols-6 gap-3">
        {tiles.map((tile) => (
          <MetricTile key={tile.label} label={tile.label} value={tile.value} colorClass={tile.colorClass} />
        ))}
      </div>
    </div>
  );
}

export default async function BacktestDetailPage({ params }: { params: { runId: string } }) {
  const detail = await getBacktestDetail(params.runId);

  return (
    <div>
      <h1 className="mb-1 text-lg font-semibold">백테스트 상세</h1>
      <p className="mb-4 text-sm text-muted-foreground">
        {detail.market} · {detail.timeframe} · {detail.start.slice(0, 10)} ~ {detail.end.slice(0, 10)}
      </p>

      <div className="mb-6 flex gap-6 rounded-md border p-4">
        <div>
          <p className="text-xs text-muted-foreground">총 수익률</p>
          <p className={`text-lg font-semibold ${returnRateColor(detail.metrics.total_return)}`}>
            {fmtPct(detail.metrics.total_return)}
          </p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">MDD</p>
          <p className={`text-lg font-semibold ${returnRateColor(detail.metrics.mdd)}`}>
            {fmtPct(detail.metrics.mdd)}
          </p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">총 거래</p>
          <p className="text-lg font-semibold">{detail.metrics.total_trades}건</p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">최초 투입금</p>
          <p className="text-lg font-semibold">{Math.round(detail.initial_capital).toLocaleString()}원</p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">최종 금액</p>
          <p className="text-lg font-semibold">{Math.round(detail.final_value).toLocaleString()}원</p>
        </div>
      </div>

      <div className="mb-6">
        <MetricsGrid metrics={detail.metrics} />
      </div>

      <h2 className="mb-2 font-medium">가격 차트</h2>
      <PriceChart ohlcv={detail.ohlcv} trades={detail.trades} timeframe={detail.timeframe} />

      <h2 className="mt-6 mb-2 font-medium">거래 내역 ({detail.trades.length}건)</h2>
      {detail.trades.length === 0 ? (
        <p className="text-muted-foreground">거래 내역이 없습니다.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>진입</TableHead>
              <TableHead>청산</TableHead>
              <TableHead>수익률(%)</TableHead>
              <TableHead>매수가</TableHead>
              <TableHead>매도가</TableHead>
              <TableHead>수익금</TableHead>
              <TableHead>보유기간</TableHead>
              <TableHead>상태</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {detail.trades.map((t, i) => (
              <TableRow key={i}>
                <TableCell>{formatDateTime(t.entryTime)}</TableCell>
                <TableCell>{formatDateTime(t.exitTime)}</TableCell>
                <TableCell className={returnRateColor(t.returnRate)}>{t.returnRate.toFixed(2)}</TableCell>
                <TableCell>{t.entryPrice.toLocaleString()}</TableCell>
                <TableCell>{t.exitPrice.toLocaleString()}</TableCell>
                <TableCell className={returnRateColor(t.pnl)}>{Math.round(t.pnl).toLocaleString()}</TableCell>
                <TableCell>{t.holdingPeriod}</TableCell>
                <TableCell>
                  {t.forceClosed ? (
                    <Badge
                      variant="secondary"
                      title="매도 조건을 만족하지 못해 백테스트 종료 시점 종가로 평가된 상태입니다."
                    >
                      보유중(기간종료)
                    </Badge>
                  ) : (
                    <Badge variant="outline">청산됨</Badge>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
